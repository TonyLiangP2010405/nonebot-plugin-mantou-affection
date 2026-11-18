from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
import urllib.request
from contextlib import suppress
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

from nonebot import logger
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageSegment
from nonebot.utils import run_sync
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_IMAGES = 9
MAX_INPUT_SIDE = 2000
MAX_OUTPUT_SIDE = 4000
MAX_OUTPUT_PIXELS = 40_000_000
MAX_ANIMATED_FRAMES = 200
MAX_ANIMATED_PIXELS = 25_000_000
GIF_DEFAULT_FRAME_MS = 100
GIF_MIN_FRAME_MS = 20
GIF_MAX_FRAME_MS = 60_000

CONCAT_OPS = frozenset({"hconcat", "vconcat"})

OP_ALIASES: dict[str, str] = {
    "水平翻转": "hflip",
    "左翻": "hflip",
    "右翻": "hflip",
    "竖直翻转": "vflip",
    "上翻": "vflip",
    "下翻": "vflip",
    "旋转": "rotate",
    "缩放": "scale",
    "裁剪": "crop",
    "灰度图": "grayscale",
    "黑白": "grayscale",
    "反相": "invert",
    "反色": "invert",
    "横向拼接": "hconcat",
    "纵向拼接": "vconcat",
    "gif变速": "gif_speed",
    "变速": "gif_speed",
}

OP_LABELS: dict[str, str] = {
    "hflip": "水平翻转",
    "vflip": "竖直翻转",
    "rotate": "旋转",
    "scale": "缩放",
    "crop": "裁剪",
    "grayscale": "灰度图",
    "invert": "反相",
    "hconcat": "横向拼接",
    "vconcat": "纵向拼接",
    "gif_speed": "GIF 变速",
}

IMAGE_USAGE = (
    "🎨 馒头图像处理（每张图消耗 1 次图像处理次数）\n"
    "用法：回复一张图片或自定义表情，发送 /馒头图像处理 <操作> [参数]\n"
    "可用操作：\n"
    "· 水平翻转 / 左翻 / 右翻\n"
    "· 竖直翻转 / 上翻 / 下翻\n"
    "· 旋转 <角度>（默认 90，逆时针）\n"
    "· 缩放 <100x100 | 100x | 50%>\n"
    "· 裁剪 <100x100 | 2:1 | 0,0,100,100>\n"
    "· 灰度图 / 黑白\n"
    "· 反相 / 反色\n"
    "· 横向拼接 / 纵向拼接（至少两张图）\n"
    "· GIF 变速 <0.5x | 2倍 | 50% | 10fps | 0.05s>\n"
    "动图会保留全部帧和节奏，处理完还是动图；拼接时静态图会一直显示。\n"
    "图像处理次数来自命运齿轮转盘，抽到就会记到账上；SUPERUSER 使用不消耗次数。"
)

NO_IMAGE_REPLY = (
    "没有找到可处理的图片：请回复一张图片或自定义表情，"
    "或把图片和 /馒头图像处理 一起发出来。"
)
NO_REPLY_IMAGE_REPLY = (
    "回复的消息里没有找到可处理的图片或自定义表情。"
    "QQ 系统小黄脸表情不是图片，换一张图片/收藏表情再试。"
)
REPLY_READ_FAILED = "读取被回复的消息失败了，请重新回复图片再试。"
INSUFFICIENT_TEMPLATE = (
    "图像处理次数不够啦：这次要 {cost} 次，当前只有 {balance} 次。"
    "去群里等命运齿轮转盘抽到图像处理次数吧。"
)
FAILED_REPLY = "图像处理失败了，次数已经退回，请稍后再试。"
TOO_MANY_REPLY = f"一次最多处理 {MAX_IMAGES} 张图片，请少发几张。"

_SIZE_RE = re.compile(r"^(\d{1,4})?[*xX,，](\d{1,4})?$")
_PERCENT_RE = re.compile(r"^(\d{1,3})%$")
_CROP_BOX_RE = re.compile(
    r"^(\d{1,4})\s*[,，]\s*(\d{1,4})\s*[,，]\s*(\d{1,4})\s*[,，]\s*(\d{1,4})$"
)
_CROP_SIZE_RE = re.compile(r"^(\d{1,4})\s*[*xX,，]\s*(\d{1,4})$")
_CROP_RATIO_RE = re.compile(r"^(\d{1,2})\s*[:：比]\s*(\d{1,2})$")
_NUMBER_RE = re.compile(r"^-?\d{1,3}(?:\.\d{1,2})?$")
_SPEED_MULTIPLIER_RE = re.compile(
    r"^(\d{1,4}(?:\.\d{1,3})?)\s*(?:x|倍速?)$", re.IGNORECASE
)
_SPEED_PERCENT_RE = re.compile(r"^(\d{1,4}(?:\.\d{1,3})?)\s*%$")
_SPEED_FPS_RE = re.compile(r"^(\d{1,4}(?:\.\d{1,3})?)\s*fps$", re.IGNORECASE)
_SPEED_TIME_RE = re.compile(r"^(\d{1,4}(?:\.\d{1,3})?)\s*(ms|s)$", re.IGNORECASE)


class ImageOpError(ValueError):
    """可以直接展示给群友的图像处理错误。"""


@dataclass(frozen=True)
class ImageRequest:
    op: str
    angle: float = 90.0
    width: int | None = None
    height: int | None = None
    percent: float | None = None
    crop_box: tuple[int, int, int, int] | None = None
    crop_size: tuple[int, int] | None = None
    crop_ratio: tuple[int, int] | None = None
    speed: float | None = None
    frame_duration_ms: float | None = None

    @property
    def label(self) -> str:
        return OP_LABELS.get(self.op, self.op)

    @property
    def uses_many_images(self) -> bool:
        return self.op in CONCAT_OPS


def parse_image_request(raw: str) -> ImageRequest | None:
    """解析命令参数；空参数返回 None，未知操作抛 ImageOpError。"""

    text = raw.replace("\u3000", " ").strip()
    if not text:
        return None
    head, _, tail = text.partition(" ")
    op = OP_ALIASES.get(head) or OP_ALIASES.get(head.lower())
    if op is None:
        raise ImageOpError(f"不认识「{head}」这个操作，可以看看下面的用法。")
    arg = tail.strip()

    if op in {"hflip", "vflip", "grayscale", "invert", "hconcat", "vconcat"}:
        if arg:
            raise ImageOpError(f"{OP_LABELS[op]}不需要参数，直接回复图片就行。")
        return ImageRequest(op)

    if op == "gif_speed":
        if not arg:
            raise ImageOpError(
                "GIF 变速还需要一个参数，例如 gif变速 0.5x、gif变速 10fps。"
            )
        return _parse_gif_speed(arg)

    if not arg:
        if op == "rotate":
            return ImageRequest(op)
        raise ImageOpError(f"{OP_LABELS[op]}还需要一个参数，例如 {_example_for(op)}。")

    if op == "rotate":
        if not _NUMBER_RE.fullmatch(arg):
            raise ImageOpError("旋转角度要写成数字，例如：旋转 90、旋转 -45。")
        angle = float(arg)
        if abs(angle) > 360:
            raise ImageOpError("旋转角度请控制在 -360 到 360 之间。")
        return ImageRequest(op, angle=angle)

    if op == "scale":
        percent = _PERCENT_RE.fullmatch(arg)
        if percent:
            value = int(percent.group(1))
            if not 1 <= value <= 1000:
                raise ImageOpError("缩放百分比请控制在 1%~1000% 之间。")
            return ImageRequest(op, percent=float(value))
        size = _SIZE_RE.fullmatch(arg)
        if not size or not any(size.groups()):
            raise ImageOpError("缩放参数格式不对，例如：100x100、100x、50%。")
        width, height = _sides(size)
        return ImageRequest(op, width=width, height=height)

    box = _CROP_BOX_RE.fullmatch(arg)
    if box:
        left, top, right, bottom = (int(value) for value in box.groups())
        if right <= left or bottom <= top:
            raise ImageOpError("裁剪范围要满足右 > 左、下 > 上。")
        return ImageRequest(op, crop_box=(left, top, right, bottom))
    size = _CROP_SIZE_RE.fullmatch(arg)
    if size:
        width, height = int(size.group(1)), int(size.group(2))
        if width < 1 or height < 1:
            raise ImageOpError("裁剪尺寸必须大于 0。")
        return ImageRequest(op, crop_size=(width, height))
    ratio = _CROP_RATIO_RE.fullmatch(arg)
    if ratio:
        width, height = int(ratio.group(1)), int(ratio.group(2))
        if width < 1 or height < 1:
            raise ImageOpError("裁剪比例必须大于 0。")
        return ImageRequest(op, crop_ratio=(width, height))
    raise ImageOpError("裁剪参数格式不对，例如：100x100、2:1、0,0,100,100。")


def _parse_gif_speed(arg: str) -> ImageRequest:
    """把 0.5x / 2倍 / 50% / 10fps / 0.05s 解析成倍率或固定帧间隔。"""

    match = _SPEED_MULTIPLIER_RE.fullmatch(arg)
    if match:
        speed = float(match.group(1))
        if speed <= 0:
            raise ImageOpError("变速倍率必须大于 0：0.5x 放慢，2x 加快。")
        if speed > 100:
            raise ImageOpError("变速倍率最大 100x。")
        return ImageRequest("gif_speed", speed=speed)

    match = _SPEED_PERCENT_RE.fullmatch(arg)
    if match:
        percent = float(match.group(1))
        if percent <= 0:
            raise ImageOpError("变速百分比必须大于 0：50% 会把速度放慢到一半。")
        if percent > 10000:
            raise ImageOpError("变速百分比最大 10000%。")
        return ImageRequest("gif_speed", speed=percent / 100)

    match = _SPEED_FPS_RE.fullmatch(arg)
    if match:
        fps = float(match.group(1))
        if fps <= 0:
            raise ImageOpError("帧率必须大于 0，例如 gif变速 10fps。")
        return ImageRequest("gif_speed", frame_duration_ms=1000 / fps)

    match = _SPEED_TIME_RE.fullmatch(arg)
    if match:
        value = float(match.group(1))
        if value <= 0:
            raise ImageOpError("帧间隔必须大于 0，例如 gif变速 0.05s、gif变速 50ms。")
        duration = value * 1000 if match.group(2).lower() == "s" else value
        return ImageRequest("gif_speed", frame_duration_ms=duration)

    raise ImageOpError(
        "变速参数格式不对，例如：0.5x、2倍、50%、10fps、0.05s、50ms。"
    )


def _example_for(op: str) -> str:
    return {
        "rotate": "旋转 90",
        "scale": "缩放 50%",
        "crop": "裁剪 1:1",
        "gif_speed": "gif变速 0.5x",
    }.get(op, "看看帮助")


def _sides(match: re.Match[str]) -> tuple[int | None, int | None]:
    first, second = match.groups()
    width = int(first) if first else None
    height = int(second) if second else None
    if width is not None and width < 1:
        raise ImageOpError("缩放尺寸必须大于 0。")
    if height is not None and height < 1:
        raise ImageOpError("缩放尺寸必须大于 0。")
    return width, height


@dataclass
class DecodedFrames:
    frames: list[Image.Image]
    durations: list[int]
    loop: int
    animated: bool


def _normalize_duration(value: Any) -> int:
    try:
        duration = round(float(value))
    except (TypeError, ValueError):
        duration = GIF_DEFAULT_FRAME_MS
    return max(10, min(GIF_MAX_FRAME_MS, duration))


def _normalize_loop(value: Any) -> int:
    try:
        loop = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, min(loop, 65535))


def _decode_frames(data: bytes) -> DecodedFrames:
    """把静态图或动图统一解成 RGBA 帧序列，并保留帧间隔和循环次数。"""

    try:
        image = Image.open(BytesIO(data))
        image.seek(0)
        frame_count = int(getattr(image, "n_frames", 1) or 1)
        width, height = image.size
        loop = _normalize_loop(image.info.get("loop", 0))
    except (UnidentifiedImageError, OSError, ValueError) as error:
        raise ImageOpError("这张图片馒头认不出来，换一张试试。") from error
    if width < 1 or height < 1:
        raise ImageOpError("这张图片尺寸不对，换一张试试。")
    if frame_count > MAX_ANIMATED_FRAMES:
        raise ImageOpError(
            f"动图最多处理 {MAX_ANIMATED_FRAMES} 帧，这张有 {frame_count} 帧，"
            "先裁短一点再试。"
        )
    if width * height * frame_count > MAX_ANIMATED_PIXELS:
        raise ImageOpError("这张动图太大了，请先缩小或裁掉一些帧再试。")

    scale = min(1.0, MAX_INPUT_SIDE / max(width, height))
    target = (max(1, round(width * scale)), max(1, round(height * scale)))
    frames: list[Image.Image] = []
    durations: list[int] = []
    try:
        for index in range(frame_count):
            image.seek(index)
            frame = image.convert("RGBA")
            if frame.size != target:
                frame = frame.resize(target, Image.Resampling.LANCZOS)
            frames.append(frame)
            durations.append(_normalize_duration(image.info.get("duration")))
    except (OSError, ValueError, EOFError) as error:
        raise ImageOpError("这张动图有一帧读不出来，换一张试试。") from error
    return DecodedFrames(frames, durations, loop, frame_count > 1)


def _check_output_size(frames: list[Image.Image], animated: bool) -> None:
    width, height = frames[0].size
    if max(width, height) > MAX_OUTPUT_SIDE:
        raise ImageOpError(f"处理结果太大了，单边请控制在 {MAX_OUTPUT_SIDE} 像素以内。")
    if animated:
        if width * height * len(frames) > MAX_ANIMATED_PIXELS:
            raise ImageOpError("处理后的动图太大了，请少放几张或先缩小。")
    elif width * height > MAX_OUTPUT_PIXELS:
        raise ImageOpError("处理后的图片太大了，请少放几张或先缩小。")


def _encode(
    frames: list[Image.Image],
    durations: list[int],
    loop: int,
    animated: bool,
) -> bytes:
    animated = animated and len(frames) > 1
    _check_output_size(frames, animated)
    if len(durations) < len(frames):
        durations = durations + [GIF_DEFAULT_FRAME_MS] * (len(frames) - len(durations))
    buffer = BytesIO()
    try:
        if animated:
            frames[0].save(
                buffer,
                format="GIF",
                save_all=True,
                append_images=frames[1:],
                duration=durations,
                loop=loop,
                disposal=2,
                optimize=False,
            )
        else:
            frames[0].save(buffer, format="PNG")
    except OSError as error:
        raise ImageOpError("处理结果保存失败，换一张图片试试。") from error
    return buffer.getvalue()


def _check_frame_durations(durations: list[int]) -> None:
    fastest = min(durations)
    slowest = max(durations)
    if fastest < GIF_MIN_FRAME_MS:
        raise ImageOpError(
            f"变速后帧间隔只有 {fastest / 1000:.3f} 秒，"
            "最快只支持 0.02 秒（50 FPS），再快 GIF 播放会不正常。"
        )
    if slowest > GIF_MAX_FRAME_MS:
        raise ImageOpError(
            f"变速后帧间隔达到 {slowest / 1000:.1f} 秒，单帧最多 60 秒。"
        )


def _scale(image: Image.Image, request: ImageRequest) -> Image.Image:
    width, height = image.size
    if request.percent is not None:
        ratio = request.percent / 100
        target = (max(1, round(width * ratio)), max(1, round(height * ratio)))
    elif request.width is not None and request.height is not None:
        target = (request.width, request.height)
    elif request.width is not None:
        target = (request.width, max(1, round(height * request.width / width)))
    else:
        assert request.height is not None
        target = (max(1, round(width * request.height / height)), request.height)
    if max(target) > MAX_OUTPUT_SIDE:
        raise ImageOpError(f"目标尺寸太大了，单边请控制在 {MAX_OUTPUT_SIDE} 像素以内。")
    return image.resize(target, Image.Resampling.LANCZOS)


def _crop(image: Image.Image, request: ImageRequest) -> Image.Image:
    width, height = image.size
    if request.crop_box is not None:
        left, top, right, bottom = request.crop_box
        left, top = max(0, left), max(0, top)
        right, bottom = min(width, right), min(height, bottom)
        if right <= left or bottom <= top:
            raise ImageOpError("裁剪范围超出了图片本身。")
        return image.crop((left, top, right, bottom))

    if request.crop_size is not None:
        target_width = min(width, request.crop_size[0])
        target_height = min(height, request.crop_size[1])
    else:
        assert request.crop_ratio is not None
        ratio_width, ratio_height = request.crop_ratio
        unit = min(width / ratio_width, height / ratio_height)
        target_width = max(1, round(ratio_width * unit))
        target_height = max(1, round(ratio_height * unit))
    left = (width - target_width) // 2
    top = (height - target_height) // 2
    return image.crop((left, top, left + target_width, top + target_height))


def _invert(image: Image.Image) -> Image.Image:
    if image.mode == "RGBA":
        red, green, blue, alpha = image.split()
        inverted = ImageOps.invert(Image.merge("RGB", (red, green, blue)))
        red, green, blue = inverted.split()
        return Image.merge("RGBA", (red, green, blue, alpha))
    return ImageOps.invert(image.convert("RGB"))


def _grayscale(image: Image.Image) -> Image.Image:
    gray = ImageOps.grayscale(image)
    if image.mode == "RGBA":
        return Image.merge("LA", (gray, image.getchannel("A")))
    return gray


def _apply_single(image: Image.Image, request: ImageRequest) -> Image.Image:
    if request.op == "hflip":
        return ImageOps.mirror(image)
    if request.op == "vflip":
        return ImageOps.flip(image)
    if request.op == "rotate":
        return image.rotate(
            request.angle,
            expand=True,
            resample=Image.Resampling.BICUBIC,
            fillcolor=(0, 0, 0, 0),
        )
    if request.op == "scale":
        return _scale(image, request)
    if request.op == "crop":
        return _crop(image, request)
    if request.op == "grayscale":
        return _grayscale(image)
    if request.op == "invert":
        return _invert(image)
    # pragma: no cover - 解析阶段已拦截未知操作
    raise ImageOpError("这个操作暂时还不支持。")


def _change_speed(decoded: DecodedFrames, request: ImageRequest) -> bytes:
    if not decoded.animated:
        raise ImageOpError(
            "GIF 变速只能处理动图：这张图只有一帧，换一张 GIF 或动态 WebP 再试。"
        )
    if request.frame_duration_ms is not None:
        duration = round(request.frame_duration_ms)
        durations = [duration] * len(decoded.frames)
    else:
        assert request.speed is not None
        durations = [
            max(1, round(item / request.speed)) for item in decoded.durations
        ]
    _check_frame_durations(durations)
    return _encode(decoded.frames, durations, decoded.loop, True)


def _merged_durations(
    sequences: list[DecodedFrames], frame_count: int
) -> list[int]:
    animated = [item for item in sequences if item.animated]
    if not animated:
        return [GIF_DEFAULT_FRAME_MS] * frame_count
    durations = [
        animated[0].durations[min(index, len(animated[0].durations) - 1)]
        for index in range(frame_count)
    ]
    for item in animated[1:]:
        for index in range(frame_count):
            durations[index] = max(
                durations[index],
                item.durations[min(index, len(item.durations) - 1)],
            )
    return durations


def _merge(images: list[bytes], *, horizontal: bool) -> bytes:
    sequences = [_decode_frames(data) for data in images]
    frame_count = max(len(item.frames) for item in sequences)
    first_frames = [item.frames[0] for item in sequences]
    if horizontal:
        width = sum(item.width for item in first_frames)
        height = max(item.height for item in first_frames)
    else:
        width = max(item.width for item in first_frames)
        height = sum(item.height for item in first_frames)
    if width * height * frame_count > MAX_ANIMATED_PIXELS:
        raise ImageOpError("拼接后的动图太大了，请少放几张、裁短帧数或先缩小。")

    canvases: list[Image.Image] = []
    for index in range(frame_count):
        canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        offset = 0
        for item in sequences:
            frame = item.frames[min(index, len(item.frames) - 1)]
            if horizontal:
                canvas.paste(frame, (offset, (height - frame.height) // 2))
                offset += frame.width
            else:
                canvas.paste(frame, ((width - frame.width) // 2, offset))
                offset += frame.height
        canvases.append(canvas)

    loop = next((item.loop for item in sequences if item.animated), 0)
    durations = _merged_durations(sequences, frame_count)
    return _encode(canvases, durations, loop, frame_count > 1)


def apply_image_request(request: ImageRequest, images: list[bytes]) -> bytes:
    """按请求处理图片；单图操作只使用第一张，拼接使用全部。"""

    if request.uses_many_images:
        if len(images) < 2:
            raise ImageOpError("拼接至少需要两张图片，请多回复几张。")
        return _merge(images, horizontal=request.op == "hconcat")
    if not images:
        raise ImageOpError(NO_IMAGE_REPLY)
    decoded = _decode_frames(images[0])
    if request.op == "gif_speed":
        return _change_speed(decoded, request)
    frames = [_apply_single(frame, request) for frame in decoded.frames]
    return _encode(frames, decoded.durations, decoded.loop, decoded.animated)


class ImageQuotaStore:
    """按群、按群友记录图像处理次数，使用异步锁和原子替换写盘。"""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = asyncio.Lock()
        self._data: dict[str, Any] | None = None

    @staticmethod
    def _empty_data() -> dict[str, Any]:
        return {"version": 1, "groups": {}}

    def _load_sync(self) -> dict[str, Any]:
        if self._data is not None:
            return self._data
        if not self.path.is_file():
            self._data = self._empty_data()
            return self._data
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        data = self._empty_data()
        if isinstance(payload, dict) and isinstance(payload.get("groups"), dict):
            groups: dict[str, dict[str, int]] = {}
            for group_id, members in payload["groups"].items():
                if not isinstance(group_id, str) or not isinstance(members, dict):
                    continue
                cleaned = {}
                for user_id, value in members.items():
                    if not isinstance(user_id, str):
                        continue
                    if isinstance(value, bool) or not isinstance(value, int):
                        continue
                    cleaned[user_id] = max(0, value)
                groups[group_id] = cleaned
            data["groups"] = groups
        self._data = data
        return self._data

    def _save_sync(self) -> None:
        data = self._load_sync()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            dir=str(self.path.parent), prefix=f".{self.path.name}.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump(data, file, ensure_ascii=False, indent=2, sort_keys=True)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp_name, self.path)
        except BaseException:
            with suppress(OSError):
                os.unlink(temp_name)
            raise

    def _balance_sync(self, group_id: str, user_id: str) -> int:
        data = self._load_sync()
        members = data["groups"].get(str(group_id)) or {}
        return int(members.get(str(user_id), 0))

    def _write_sync(self, group_id: str, user_id: str, value: int) -> int:
        data = self._load_sync()
        group = data["groups"].setdefault(str(group_id), {})
        if value > 0:
            group[str(user_id)] = value
        else:
            group.pop(str(user_id), None)
            if not group:
                data["groups"].pop(str(group_id), None)
        self._save_sync()
        return value

    async def get(self, group_id: str, user_id: str) -> int:
        async with self._lock:
            return self._balance_sync(group_id, user_id)

    async def add(self, group_id: str, user_id: str, amount: int) -> int:
        async with self._lock:
            current = self._balance_sync(group_id, user_id)
            return self._write_sync(group_id, user_id, current + max(0, int(amount)))

    async def consume(self, group_id: str, user_id: str, amount: int) -> int | None:
        """扣掉 amount 次并返回余额；次数不够时返回 None 且不改动数据。"""

        async with self._lock:
            current = self._balance_sync(group_id, user_id)
            cost = max(0, int(amount))
            if current < cost:
                return None
            return self._write_sync(group_id, user_id, current - cost)


def _download_image(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=20) as response:
        data = response.read()
    if not data:
        raise ImageOpError("这张图片是空的，换一张试试。")
    return data


@dataclass(frozen=True)
class ImageResult:
    text: str | None = None
    image: bytes | None = None


class ImageProcessingCoordinator:
    """把 /馒头图像处理 的解析、取图、扣次数和出图串起来。"""

    def __init__(
        self,
        quota: ImageQuotaStore,
        config: Any,
        *,
        fetcher: Any | None = None,
        max_images: int = MAX_IMAGES,
    ):
        self.quota = quota
        self.config = config
        self.fetcher = fetcher or _download_image
        self.max_images = int(max_images)

    async def help_text(self, group_id: str, user_id: str) -> str:
        try:
            balance = await self.quota.get(group_id, user_id)
        except Exception:
            logger.exception("[mantou-affection] 读取图像处理次数失败")
            balance = 0
        return f"{IMAGE_USAGE}\n当前图像处理次数：{balance} 次"

    async def handle(
        self,
        *,
        group_id: str,
        user_id: str,
        raw: str,
        event: GroupMessageEvent,
        bot: Bot,
        unlimited: bool = False,
    ) -> ImageResult:
        try:
            request = parse_image_request(raw)
        except ImageOpError as error:
            return ImageResult(
                text=f"{error}\n\n{await self.help_text(group_id, user_id)}"
            )
        if request is None:
            return ImageResult(text=await self.help_text(group_id, user_id))

        try:
            images = await self._collect_images(event, bot)
        except ImageOpError as error:
            return ImageResult(text=str(error))
        if not images:
            return ImageResult(text=NO_IMAGE_REPLY)
        if len(images) > self.max_images:
            return ImageResult(text=TOO_MANY_REPLY)

        if request.uses_many_images:
            if len(images) < 2:
                return ImageResult(text="拼接至少需要两张图片，请回复多张图片再试。")
            used = images[: self.max_images]
        else:
            used = images[:1]
        cost = len(used)

        balance: int | None = None
        if not unlimited:
            try:
                balance = await self.quota.consume(group_id, user_id, cost)
            except Exception:
                logger.exception("[mantou-affection] 扣除图像处理次数失败")
                return ImageResult(text="图像处理次数暂时记不上账，请稍后再试。")
            if balance is None:
                current = await self.quota.get(group_id, user_id)
                return ImageResult(
                    text=INSUFFICIENT_TEMPLATE.format(cost=cost, balance=current)
                )

        try:
            data = await run_sync(apply_image_request)(request, used)
        except ImageOpError as error:
            if not unlimited:
                await self._refund(group_id, user_id, cost)
            return ImageResult(text=str(error))
        except Exception:
            logger.exception("[mantou-affection] 图像处理失败")
            if not unlimited:
                await self._refund(group_id, user_id, cost)
            return ImageResult(text=FAILED_REPLY)
        if unlimited:
            text = f"🎨 {request.label}完成（SUPERUSER 不消耗次数）"
        else:
            text = f"🎨 {request.label}完成，消耗 {cost} 次，剩余 {balance} 次"
        return ImageResult(text=text, image=data)

    async def _refund(self, group_id: str, user_id: str, amount: int) -> None:
        try:
            await self.quota.add(group_id, user_id, amount)
        except Exception:
            logger.exception("[mantou-affection] 退回图像处理次数失败")

    async def _collect_images(self, event: GroupMessageEvent, bot: Bot) -> list[bytes]:
        quoted = getattr(event, "reply", None)
        reply_segments = (
            _image_segments(getattr(quoted, "message", None)) if quoted is not None else []
        )
        reply_id = _event_reply_id(event) or _reply_id_from_model(quoted)
        info: Any = None
        if not reply_segments and reply_id is not None:
            try:
                info = await bot.get_msg(message_id=int(reply_id))
            except Exception:
                logger.exception("[mantou-affection] 读取被回复的消息失败")
                info = None
            if isinstance(info, dict):
                reply_segments = _image_segments(info.get("message"))
        segments = reply_segments + _image_segments(event.message)
        has_reply = quoted is not None or reply_id is not None
        if not segments and has_reply:
            raise ImageOpError(
                REPLY_READ_FAILED
                if quoted is None and not isinstance(info, dict)
                else NO_REPLY_IMAGE_REPLY
            )

        seen: set[str] = set()
        unique: list[MessageSegment] = []
        for segment in segments:
            marker = str(segment.data.get("file") or segment.data.get("url") or "")
            if marker and marker in seen:
                continue
            if marker:
                seen.add(marker)
            unique.append(segment)

        if len(unique) > self.max_images:
            raise ImageOpError(TOO_MANY_REPLY)
        images: list[bytes] = []
        for segment in unique:
            images.append(await self._segment_bytes(segment, bot))
        return images

    async def _segment_bytes(self, segment: MessageSegment, bot: Bot) -> bytes:
        url = segment.data.get("url")
        file = segment.data.get("file")
        if not url and file:
            try:
                info = await bot.call_api("get_image", file=file)
            except Exception:
                info = None
            if isinstance(info, dict):
                url = info.get("url")
            if not url and Path(str(file)).is_file():
                return Path(str(file)).read_bytes()
        if not url:
            raise ImageOpError("有一张图片拿不到地址，换一张再试试。")
        try:
            return await run_sync(self.fetcher)(str(url))
        except ImageOpError:
            raise
        except Exception as error:
            raise ImageOpError("图片下载失败了，换一张再试试。") from error


_REPLY_CQ_RE = re.compile(r"\[CQ:reply,id=([^,\]]+)")


def _reply_id(message: Message) -> int | None:
    for segment in message:
        if segment.type == "reply":
            return _as_int(segment.data.get("id"))
    return None


def _event_reply_id(event: GroupMessageEvent) -> int | None:
    for message in (event.message, getattr(event, "original_message", None)):
        if isinstance(message, Message):
            reply_id = _reply_id(message)
            if reply_id is not None:
                return reply_id
    raw = str(getattr(event, "raw_message", "") or "")
    match = _REPLY_CQ_RE.search(raw)
    return _as_int(match.group(1)) if match else None


def _reply_id_from_model(reply: Any) -> int | None:
    if reply is None:
        return None
    return _as_int(getattr(reply, "message_id", None))


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _image_segments(value: Any) -> list[MessageSegment]:
    if isinstance(value, Message):
        candidates = list(value)
    elif isinstance(value, str):
        candidates = list(Message(value))
    elif isinstance(value, list):
        candidates = [
            MessageSegment(
                str(item.get("type") or ""),
                item.get("data") if isinstance(item.get("data"), dict) else {},
            )
            for item in value
            if isinstance(item, dict)
        ]
    else:
        return []
    images: list[MessageSegment] = []
    for segment in candidates:
        image = _as_image_segment(segment)
        if image is not None:
            images.append(image)
    return images


def _as_image_segment(segment: MessageSegment) -> MessageSegment | None:
    """图片直接用；商城/收藏表情（mface）只有能拿到地址时才当成图片。"""

    if segment.type == "image":
        return segment
    if segment.type == "mface":
        url = segment.data.get("url")
        file = segment.data.get("file")
        if url or file:
            return MessageSegment("image", {"url": url or "", "file": file or ""})
    return None
