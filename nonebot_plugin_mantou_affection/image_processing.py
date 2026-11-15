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
}

IMAGE_USAGE = (
    "🎨 馒头图像处理（每张图消耗 1 次图像处理次数）\n"
    "用法：回复一张图片，发送 /馒头图像处理 <操作> [参数]\n"
    "可用操作：\n"
    "· 水平翻转 / 左翻 / 右翻\n"
    "· 竖直翻转 / 上翻 / 下翻\n"
    "· 旋转 <角度>（默认 90，逆时针）\n"
    "· 缩放 <100x100 | 100x | 50%>\n"
    "· 裁剪 <100x100 | 2:1 | 0,0,100,100>\n"
    "· 灰度图 / 黑白\n"
    "· 反相 / 反色\n"
    "· 横向拼接 / 纵向拼接（至少两张图）\n"
    "图像处理次数来自命运齿轮转盘，抽到就会记到账上。"
)

NO_IMAGE_REPLY = "没有找到可处理的图片：请回复一张图片，或把图片和 /馒头图像处理 一起发出来。"
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
    op = OP_ALIASES.get(head)
    if op is None:
        raise ImageOpError(f"不认识「{head}」这个操作，可以看看下面的用法。")
    arg = tail.strip()

    if op in {"hflip", "vflip", "grayscale", "invert", "hconcat", "vconcat"}:
        if arg:
            raise ImageOpError(f"{OP_LABELS[op]}不需要参数，直接回复图片就行。")
        return ImageRequest(op)

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


def _example_for(op: str) -> str:
    return {
        "rotate": "旋转 90",
        "scale": "缩放 50%",
        "crop": "裁剪 1:1",
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


def _decode(data: bytes) -> Image.Image:
    try:
        image = Image.open(BytesIO(data))
        image.seek(0)
        image.load()
    except (UnidentifiedImageError, OSError, ValueError) as error:
        raise ImageOpError("这张图片馒头认不出来，换一张试试。") from error
    if image.mode not in {"RGB", "RGBA"}:
        image = image.convert("RGBA")
    if max(image.size) > MAX_INPUT_SIDE:
        image.thumbnail((MAX_INPUT_SIDE, MAX_INPUT_SIDE))
    return image


def _encode(image: Image.Image) -> bytes:
    buffer = BytesIO()
    try:
        image.save(buffer, format="PNG")
    except OSError as error:
        raise ImageOpError("处理结果保存失败，换一张图片试试。") from error
    return buffer.getvalue()


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


def _merge(images: list[bytes], *, horizontal: bool) -> Image.Image:
    decoded = [_decode(data) for data in images]
    if horizontal:
        width = sum(item.width for item in decoded)
        height = max(item.height for item in decoded)
    else:
        width = max(item.width for item in decoded)
        height = sum(item.height for item in decoded)
    if width * height > MAX_OUTPUT_PIXELS:
        raise ImageOpError("拼接后的图片太大了，请少放几张或先缩小。")
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    offset = 0
    for item in decoded:
        if horizontal:
            canvas.paste(item, (offset, (height - item.height) // 2))
            offset += item.width
        else:
            canvas.paste(item, ((width - item.width) // 2, offset))
            offset += item.height
    return canvas


def apply_image_request(request: ImageRequest, images: list[bytes]) -> bytes:
    """按请求处理图片；单图操作只使用第一张，拼接使用全部。"""

    if request.uses_many_images:
        if len(images) < 2:
            raise ImageOpError("拼接至少需要两张图片，请多回复几张。")
        result = _merge(images, horizontal=request.op == "hconcat")
        return _encode(result)
    if not images:
        raise ImageOpError(NO_IMAGE_REPLY)
    image = _decode(images[0])
    if request.op == "hflip":
        result = ImageOps.mirror(image)
    elif request.op == "vflip":
        result = ImageOps.flip(image)
    elif request.op == "rotate":
        result = image.rotate(
            request.angle, expand=True, resample=Image.Resampling.BICUBIC, fillcolor=(0, 0, 0, 0)
        )
    elif request.op == "scale":
        result = _scale(image, request)
    elif request.op == "crop":
        result = _crop(image, request)
    elif request.op == "grayscale":
        result = ImageOps.grayscale(image)
    elif request.op == "invert":
        result = _invert(image)
    else:  # pragma: no cover - 解析阶段已拦截未知操作
        raise ImageOpError("这个操作暂时还不支持。")
    if max(result.size) > MAX_OUTPUT_SIDE:
        raise ImageOpError(f"处理结果太大了，单边请控制在 {MAX_OUTPUT_SIDE} 像素以内。")
    return _encode(result)


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
            await self._refund(group_id, user_id, cost)
            return ImageResult(text=str(error))
        except Exception:
            logger.exception("[mantou-affection] 图像处理失败")
            await self._refund(group_id, user_id, cost)
            return ImageResult(text=FAILED_REPLY)
        return ImageResult(
            text=f"🎨 {request.label}完成，消耗 {cost} 次，剩余 {balance} 次",
            image=data,
        )

    async def _refund(self, group_id: str, user_id: str, amount: int) -> None:
        try:
            await self.quota.add(group_id, user_id, amount)
        except Exception:
            logger.exception("[mantou-affection] 退回图像处理次数失败")

    async def _collect_images(self, event: GroupMessageEvent, bot: Bot) -> list[bytes]:
        segments: list[MessageSegment] = []
        reply_id = _reply_id(event.message)
        if reply_id is not None:
            try:
                info = await bot.get_msg(message_id=int(reply_id))
            except Exception:
                logger.exception("[mantou-affection] 读取被回复的消息失败")
                info = None
            if isinstance(info, dict):
                segments.extend(_image_segments(info.get("message")))
        segments.extend(seg for seg in event.message if seg.type == "image")

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


def _reply_id(message: Message) -> int | None:
    for segment in message:
        if segment.type == "reply":
            raw = segment.data.get("id")
            if raw is None:
                continue
            try:
                return int(raw)
            except (TypeError, ValueError):
                return None
    return None


def _image_segments(value: Any) -> list[MessageSegment]:
    if isinstance(value, Message):
        return [segment for segment in value if segment.type == "image"]
    if isinstance(value, str):
        return [segment for segment in Message(value) if segment.type == "image"]
    if isinstance(value, list):
        segments: list[MessageSegment] = []
        for item in value:
            if isinstance(item, dict) and item.get("type") == "image":
                data = item.get("data")
                segments.append(
                    MessageSegment("image", data if isinstance(data, dict) else {})
                )
        return segments
    return []
