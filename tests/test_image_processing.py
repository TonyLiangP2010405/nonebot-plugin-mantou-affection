from __future__ import annotations

from io import BytesIO
from pathlib import Path
from time import time

import pytest
from nonebot import get_driver
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment
from nonebot.adapters.onebot.v11.event import Sender
from PIL import Image

from nonebot_plugin_mantou_affection.commands import register_commands
from nonebot_plugin_mantou_affection.config import Config
from nonebot_plugin_mantou_affection.image_processing import (
    NO_IMAGE_REPLY,
    NO_REPLY_IMAGE_REPLY,
    REPLY_READ_FAILED,
    ImageOpError,
    ImageProcessingCoordinator,
    ImageQuotaStore,
    apply_image_request,
    parse_image_request,
)
from nonebot_plugin_mantou_affection.service import AffectionService
from nonebot_plugin_mantou_affection.storage import AffectionStore

GROUP = "91001"
USER = "91002"
BOT = "999"


def make_image(size: tuple[int, int] = (4, 2), color=(255, 0, 0, 255), mode="RGBA") -> bytes:
    image = Image.new(mode, size, color)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def make_gif(
    frame_count: int = 3,
    size: tuple[int, int] = (8, 6),
    durations: tuple[int, ...] = (100, 200, 300),
) -> bytes:
    frames: list[Image.Image] = []
    for index in range(frame_count):
        frame = Image.new("RGBA", size, (0, 0, 0, 0))
        frame.paste((255, 0, 0, 255), (index, 1, index + 3, 3))
        frames.append(frame)
    buffer = BytesIO()
    frames[0].save(
        buffer,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=list(durations[:frame_count]),
        loop=0,
        disposal=2,
        optimize=False,
    )
    return buffer.getvalue()


def open_image(data: bytes) -> Image.Image:
    return Image.open(BytesIO(data))


def open_frames(data: bytes) -> tuple[list[Image.Image], list[int]]:
    image = Image.open(BytesIO(data))
    frames: list[Image.Image] = []
    durations: list[int] = []
    for index in range(image.n_frames):
        image.seek(index)
        durations.append(int(image.info.get("duration", 0)))
        frames.append(image.convert("RGBA"))
    return frames, durations


class FakeBot:
    def __init__(self, replies: dict[int, dict] | None = None):
        self.self_id = BOT
        self.replies = replies or {}
        self.calls: list[tuple[str, dict]] = []

    async def get_msg(self, *, message_id: int) -> dict:
        return self.replies.get(message_id, {})

    async def call_api(self, name: str, **kwargs):
        self.calls.append((name, kwargs))
        return {}


def make_event(
    text: str = "",
    *,
    images: tuple[bytes, ...] = (),
    reply_id: int | None = None,
    reply: dict | None = None,
) -> GroupMessageEvent:
    message = Message()
    if reply_id is not None:
        message.append(MessageSegment("reply", {"id": str(reply_id)}))
    if text:
        message.append(MessageSegment.text(text))
    for index in range(len(images)):
        message.append(
            MessageSegment("image", {"file": f"inline{index}.png", "url": f"inline{index}"})
        )
    return GroupMessageEvent(
        time=int(time()),
        self_id=int(BOT),
        post_type="message",
        sub_type="normal",
        user_id=int(USER),
        message_type="group",
        message_id=1,
        message=message,
        original_message=message,
        raw_message=text,
        font=0,
        sender=Sender(user_id=int(USER), nickname="桃友", role="member"),
        to_me=False,
        group_id=int(GROUP),
        reply=reply,
    )


@pytest.fixture()
def quota(tmp_path: Path) -> ImageQuotaStore:
    return ImageQuotaStore(tmp_path / "image_quota.json")


@pytest.fixture()
def images() -> dict[str, bytes]:
    return {
        "inline0": make_image((4, 2), (255, 0, 0, 255)),
        "inline1": make_image((2, 4), (0, 0, 255, 255)),
    }


def make_coordinator(
    quota: ImageQuotaStore, images: dict[str, bytes]
) -> ImageProcessingCoordinator:
    return ImageProcessingCoordinator(
        quota,
        Config(),
        fetcher=lambda url: images[url],
    )


@pytest.mark.parametrize(
    ("raw", "op"),
    [
        ("水平翻转", "hflip"),
        ("左翻", "hflip"),
        ("右翻", "hflip"),
        ("竖直翻转", "vflip"),
        ("上翻", "vflip"),
        ("下翻", "vflip"),
        ("灰度图", "grayscale"),
        ("黑白", "grayscale"),
        ("反相", "invert"),
        ("反色", "invert"),
        ("横向拼接", "hconcat"),
        ("纵向拼接", "vconcat"),
    ],
)
def test_parse_simple_operations(raw: str, op: str) -> None:
    request = parse_image_request(raw)
    assert request is not None
    assert request.op == op


def test_parse_empty_returns_none() -> None:
    assert parse_image_request("") is None
    assert parse_image_request("   ") is None


def test_parse_rotate_defaults_and_angles() -> None:
    request = parse_image_request("旋转")
    assert request is not None and request.angle == 90.0
    request = parse_image_request("旋转 -45")
    assert request is not None and request.angle == -45.0


@pytest.mark.parametrize(
    "raw", ["旋转 拉满", "旋转 361", "翻转 多余", "水平翻转 多余"]
)
def test_parse_rejects_bad_operations(raw: str) -> None:
    with pytest.raises(ImageOpError):
        parse_image_request(raw)


def test_parse_scale_variants() -> None:
    size = parse_image_request("缩放 100x100")
    assert size is not None and (size.width, size.height) == (100, 100)
    width_only = parse_image_request("缩放 100x")
    assert width_only is not None and (width_only.width, width_only.height) == (100, None)
    height_only = parse_image_request("缩放 x100")
    assert height_only is not None and (height_only.width, height_only.height) == (None, 100)
    percent = parse_image_request("缩放 50%")
    assert percent is not None and percent.percent == 50.0


@pytest.mark.parametrize("raw", ["缩放", "缩放 abc", "缩放 0x100", "缩放 2000%"])
def test_parse_rejects_bad_scale(raw: str) -> None:
    with pytest.raises(ImageOpError):
        parse_image_request(raw)


def test_parse_crop_variants() -> None:
    box = parse_image_request("裁剪 0,0,100,100")
    assert box is not None and box.crop_box == (0, 0, 100, 100)
    size = parse_image_request("裁剪 100x100")
    assert size is not None and size.crop_size == (100, 100)
    ratio = parse_image_request("裁剪 2:1")
    assert ratio is not None and ratio.crop_ratio == (2, 1)


@pytest.mark.parametrize("raw", ["裁剪", "裁剪 abc", "裁剪 100x0", "裁剪 5,5,4,4"])
def test_parse_rejects_bad_crop(raw: str) -> None:
    with pytest.raises(ImageOpError):
        parse_image_request(raw)


def test_hflip_mirrors_pixels() -> None:
    image = Image.new("RGBA", (2, 1))
    image.putpixel((0, 0), (255, 0, 0, 255))
    image.putpixel((1, 0), (0, 0, 255, 255))
    buffer = BytesIO()
    image.save(buffer, format="PNG")

    result = open_image(apply_image_request(parse_image_request("水平翻转"), [buffer.getvalue()]))
    assert result.getpixel((0, 0)) == (0, 0, 255, 255)
    assert result.getpixel((1, 0)) == (255, 0, 0, 255)


def test_rotate_swaps_dimensions() -> None:
    result = open_image(apply_image_request(parse_image_request("旋转 90"), [make_image((4, 2))]))
    assert result.size == (2, 4)


def test_scale_percent_and_width_only() -> None:
    half = open_image(apply_image_request(parse_image_request("缩放 50%"), [make_image((100, 40))]))
    assert half.size == (50, 20)
    width_only = open_image(
        apply_image_request(parse_image_request("缩放 50x"), [make_image((100, 40))])
    )
    assert width_only.size == (50, 20)


def test_crop_ratio_is_centered() -> None:
    result = open_image(
        apply_image_request(parse_image_request("裁剪 2:1"), [make_image((100, 100))])
    )
    assert result.size == (100, 50)


def test_grayscale_and_invert() -> None:
    gray = open_image(
        apply_image_request(parse_image_request("灰度图"), [make_image()])
    )
    assert gray.mode in {"L", "LA"}
    red = open_image(
        apply_image_request(
            parse_image_request("反相"), [make_image(color=(255, 0, 0, 255))]
        )
    )
    assert red.convert("RGB").getpixel((0, 0)) == (0, 255, 255)


def test_concat_uses_all_images() -> None:
    first = make_image((4, 2))
    second = make_image((2, 4))
    horizontal = open_image(
        apply_image_request(parse_image_request("横向拼接"), [first, second])
    )
    assert horizontal.size == (6, 4)
    vertical = open_image(apply_image_request(parse_image_request("纵向拼接"), [first, second]))
    assert vertical.size == (4, 6)


def test_concat_needs_two_images() -> None:
    with pytest.raises(ImageOpError):
        apply_image_request(parse_image_request("横向拼接"), [make_image()])


def test_broken_image_is_reported() -> None:
    with pytest.raises(ImageOpError):
        apply_image_request(parse_image_request("灰度图"), [b"not an image"])


async def test_quota_store_add_consume_and_persist(tmp_path: Path) -> None:
    quota = ImageQuotaStore(tmp_path / "image_quota.json")
    assert await quota.get(GROUP, USER) == 0
    assert await quota.add(GROUP, USER, 3) == 3
    assert await quota.consume(GROUP, USER, 2) == 1
    assert await quota.consume(GROUP, USER, 5) is None
    assert await quota.get(GROUP, USER) == 1

    reloaded = ImageQuotaStore(tmp_path / "image_quota.json")
    assert await reloaded.get(GROUP, USER) == 1


async def test_help_shows_current_balance(quota: ImageQuotaStore, images) -> None:
    coordinator = make_coordinator(quota, images)
    await quota.add(GROUP, USER, 4)

    result = await coordinator.handle(
        group_id=GROUP, user_id=USER, raw="", event=make_event(), bot=FakeBot()
    )

    assert result.image is None
    assert "图像处理次数" in result.text
    assert "当前图像处理次数：4 次" in result.text


async def test_single_operation_consumes_one(quota: ImageQuotaStore, images) -> None:
    coordinator = make_coordinator(quota, images)
    await quota.add(GROUP, USER, 2)

    result = await coordinator.handle(
        group_id=GROUP,
        user_id=USER,
        raw="水平翻转",
        event=make_event(images=(images["inline0"],)),
        bot=FakeBot(),
    )

    assert result.image is not None
    assert "消耗 1 次，剩余 1 次" in result.text
    assert await quota.get(GROUP, USER) == 1


async def test_concat_consumes_one_per_image(quota: ImageQuotaStore, images) -> None:
    coordinator = make_coordinator(quota, images)
    await quota.add(GROUP, USER, 2)

    result = await coordinator.handle(
        group_id=GROUP,
        user_id=USER,
        raw="横向拼接",
        event=make_event(images=(images["inline0"], images["inline1"])),
        bot=FakeBot(),
    )

    assert result.image is not None
    assert "消耗 2 次，剩余 0 次" in result.text
    assert await quota.get(GROUP, USER) == 0


async def test_missing_quota_is_reported(quota: ImageQuotaStore, images) -> None:
    coordinator = make_coordinator(quota, images)

    result = await coordinator.handle(
        group_id=GROUP,
        user_id=USER,
        raw="灰度图",
        event=make_event(images=(images["inline0"],)),
        bot=FakeBot(),
    )

    assert result.image is None
    assert "次数不够" in result.text
    assert "当前只有 0 次" in result.text


async def test_no_image_is_reported(quota: ImageQuotaStore, images) -> None:
    coordinator = make_coordinator(quota, images)
    await quota.add(GROUP, USER, 1)

    result = await coordinator.handle(
        group_id=GROUP, user_id=USER, raw="灰度图", event=make_event(), bot=FakeBot()
    )

    assert result.text == NO_IMAGE_REPLY
    assert await quota.get(GROUP, USER) == 1


async def test_reply_images_are_collected(quota: ImageQuotaStore, images) -> None:
    coordinator = make_coordinator(quota, images)
    await quota.add(GROUP, USER, 1)
    bot = FakeBot(
        replies={
            42: {"message": [{"type": "image", "data": {"file": "reply.png", "url": "inline1"}}]}
        }
    )

    result = await coordinator.handle(
        group_id=GROUP,
        user_id=USER,
        raw="竖直翻转",
        event=make_event(reply_id=42),
        bot=bot,
    )

    assert result.image is not None
    assert await quota.get(GROUP, USER) == 0


async def test_reply_sticker_image_is_collected(
    quota: ImageQuotaStore, images
) -> None:
    sticker = make_gif()
    coordinator = make_coordinator(quota, {**images, "sticker": sticker})
    await quota.add(GROUP, USER, 1)
    bot = FakeBot(
        replies={
            43: {
                "message": [
                    {
                        "type": "image",
                        "data": {
                            "file": "sticker.gif",
                            "url": "sticker",
                            "sub_type": 1,
                            "summary": "[动画表情]",
                        },
                    }
                ]
            }
        }
    )

    result = await coordinator.handle(
        group_id=GROUP,
        user_id=USER,
        raw="水平翻转",
        event=make_event(reply_id=43),
        bot=bot,
    )

    assert result.image is not None
    assert Image.open(BytesIO(result.image)).is_animated
    assert await quota.get(GROUP, USER) == 0


def make_quoted_reply(text: str = "", image_url: str | None = None) -> dict:
    message: list[dict] = []
    if image_url is not None:
        message.append(
            {
                "type": "image",
                "data": {
                    "file": "sticker.gif",
                    "url": image_url,
                    "sub_type": 1,
                    "summary": "[动画表情]",
                },
            }
        )
    if text:
        message.append({"type": "text", "data": {"text": text}})
    return {
        "time": int(time()),
        "message_type": "group",
        "message_id": 88,
        "real_id": 88,
        "sender": {"user_id": 123, "nickname": "被引用的人"},
        "message": message,
    }


async def test_reply_payload_with_sticker_is_collected(
    quota: ImageQuotaStore, images
) -> None:
    sticker = make_gif()
    coordinator = make_coordinator(quota, {**images, "sticker": sticker})
    await quota.add(GROUP, USER, 1)

    result = await coordinator.handle(
        group_id=GROUP,
        user_id=USER,
        raw="水平翻转",
        event=make_event(reply=make_quoted_reply(image_url="sticker")),
        bot=FakeBot(),
    )

    assert result.image is not None
    assert Image.open(BytesIO(result.image)).is_animated
    assert await quota.get(GROUP, USER) == 0


async def test_reply_payload_without_image_is_reported(
    quota: ImageQuotaStore, images
) -> None:
    coordinator = make_coordinator(quota, images)
    await quota.add(GROUP, USER, 1)

    result = await coordinator.handle(
        group_id=GROUP,
        user_id=USER,
        raw="灰度图",
        event=make_event(reply=make_quoted_reply(text="纯文字")),
        bot=FakeBot(),
    )

    assert result.text == NO_REPLY_IMAGE_REPLY
    assert await quota.get(GROUP, USER) == 1


async def test_reply_mface_with_url_is_collected(
    quota: ImageQuotaStore, images
) -> None:
    coordinator = make_coordinator(quota, images)
    await quota.add(GROUP, USER, 1)
    bot = FakeBot(
        replies={
            44: {
                "message": [
                    {
                        "type": "mface",
                        "data": {"url": "inline1", "summary": "[表情]"},
                    }
                ]
            }
        }
    )

    result = await coordinator.handle(
        group_id=GROUP,
        user_id=USER,
        raw="灰度图",
        event=make_event(reply_id=44),
        bot=bot,
    )

    assert result.image is not None
    assert await quota.get(GROUP, USER) == 0


async def test_reply_without_image_is_reported(
    quota: ImageQuotaStore, images
) -> None:
    coordinator = make_coordinator(quota, images)
    await quota.add(GROUP, USER, 1)
    bot = FakeBot(
        replies={45: {"message": [{"type": "text", "data": {"text": "纯文字"}}]}}
    )

    result = await coordinator.handle(
        group_id=GROUP,
        user_id=USER,
        raw="灰度图",
        event=make_event(reply_id=45),
        bot=bot,
    )

    assert result.text == NO_REPLY_IMAGE_REPLY
    assert await quota.get(GROUP, USER) == 1


async def test_reply_read_failure_is_reported(
    quota: ImageQuotaStore, images
) -> None:
    coordinator = make_coordinator(quota, images)
    await quota.add(GROUP, USER, 1)

    class BrokenBot(FakeBot):
        async def get_msg(self, *, message_id: int) -> dict:
            raise RuntimeError("boom")

    result = await coordinator.handle(
        group_id=GROUP,
        user_id=USER,
        raw="灰度图",
        event=make_event(reply_id=46),
        bot=BrokenBot(),
    )

    assert result.text == REPLY_READ_FAILED
    assert await quota.get(GROUP, USER) == 1


async def test_failed_processing_refunds_quota(quota: ImageQuotaStore) -> None:
    coordinator = make_coordinator(quota, {"inline0": b"broken"})
    await quota.add(GROUP, USER, 1)

    result = await coordinator.handle(
        group_id=GROUP,
        user_id=USER,
        raw="灰度图",
        event=make_event(images=(b"broken",)),
        bot=FakeBot(),
    )

    assert result.image is None
    assert "认不出来" in result.text
    assert await quota.get(GROUP, USER) == 1


async def test_download_failure_is_reported(quota: ImageQuotaStore) -> None:
    def broken_fetcher(url: str) -> bytes:
        raise OSError("network down")

    coordinator = ImageProcessingCoordinator(quota, Config(), fetcher=broken_fetcher)
    await quota.add(GROUP, USER, 1)

    result = await coordinator.handle(
        group_id=GROUP,
        user_id=USER,
        raw="灰度图",
        event=make_event(images=(make_image(),)),
        bot=FakeBot(),
    )

    assert "图片下载失败" in result.text
    assert await quota.get(GROUP, USER) == 1


async def test_image_command_requires_quota(tmp_path: Path, monkeypatch) -> None:
    service = AffectionService(AffectionStore(tmp_path / "affection.json"), Config())
    quota = ImageQuotaStore(tmp_path / "image_quota.json")
    images = {"inline0": make_image()}
    coordinator = make_coordinator(quota, images)
    commands = register_commands(service, Config(), None, coordinator)
    image_cmd = commands[-1]
    sent: list[Message] = []

    async def fake_finish(message=Message(), **kwargs) -> None:
        sent.append(message if isinstance(message, Message) else Message(str(message)))

    monkeypatch.setattr(image_cmd, "finish", fake_finish)

    await image_cmd.handlers[0].call(
        make_event(images=(images["inline0"],)),
        FakeBot(),
        ("馒头图像处理",),
        Message("灰度图"),
    )
    assert "次数不够" in sent[-1].extract_plain_text()

    await quota.add(GROUP, USER, 1)
    await image_cmd.handlers[0].call(
        make_event(images=(images["inline0"],)),
        FakeBot(),
        ("馒头图像处理",),
        Message("灰度图"),
    )
    assert any(segment.type == "image" for segment in sent[-1])
    assert "消耗 1 次" in sent[-1].extract_plain_text()
    assert await quota.get(GROUP, USER) == 0


async def test_parse_gif_speed_variants() -> None:
    cases = [
        ("gif变速 0.5x", 0.5, None),
        ("GIF变速 2倍", 2.0, None),
        ("GIF变速 2倍速", 2.0, None),
        ("gif变速 50%", 0.5, None),
        ("gif变速 10fps", None, 100.0),
        ("gif变速 0.05s", None, 50.0),
        ("gif变速 50ms", None, 50.0),
        ("变速 1.5x", 1.5, None),
    ]
    for raw, speed, duration in cases:
        request = parse_image_request(raw)
        assert request is not None
        assert request.op == "gif_speed"
        assert request.speed == speed
        assert request.frame_duration_ms == pytest.approx(duration)


@pytest.mark.parametrize(
    "raw",
    [
        "gif变速",
        "GIF变速 abc",
        "gif变速 0x",
        "gif变速 0%",
        "gif变速 0fps",
        "gif变速 0s",
        "变速 2.5",
    ],
)
def test_parse_rejects_bad_gif_speed(raw: str) -> None:
    with pytest.raises(ImageOpError):
        parse_image_request(raw)


def test_gif_speed_multiplies_frame_durations() -> None:
    source = make_gif()
    slower_data = apply_image_request(parse_image_request("gif变速 0.5x"), [source])
    slower = open_frames(slower_data)
    assert slower[1] == [200, 400, 600]
    assert Image.open(BytesIO(slower_data)).is_animated

    faster = open_frames(
        apply_image_request(parse_image_request("gif变速 2倍"), [source])
    )
    assert faster[1] == [50, 100, 150]


def test_gif_speed_constant_frame_duration() -> None:
    source = make_gif()
    fps = open_frames(
        apply_image_request(parse_image_request("gif变速 10fps"), [source])
    )
    assert fps[1] == [100, 100, 100]
    seconds = open_frames(
        apply_image_request(parse_image_request("gif变速 0.05s"), [source])
    )
    assert seconds[1] == [50, 50, 50]


def test_gif_speed_rejects_too_fast() -> None:
    source = make_gif(durations=(20, 30, 40))
    with pytest.raises(ImageOpError, match=r"0\.02"):
        apply_image_request(parse_image_request("gif变速 2x"), [source])


def test_gif_speed_requires_animated_image() -> None:
    with pytest.raises(ImageOpError, match="只能处理动图"):
        apply_image_request(parse_image_request("gif变速 0.5x"), [make_image()])


@pytest.mark.parametrize(
    "raw",
    [
        "水平翻转",
        "竖直翻转",
        "旋转 90",
        "缩放 50%",
        "裁剪 1:1",
        "灰度图",
        "反相",
    ],
)
def test_animated_operations_keep_animation(raw: str) -> None:
    source = make_gif()
    result = apply_image_request(parse_image_request(raw), [source])
    image = Image.open(BytesIO(result))
    assert image.is_animated
    assert image.n_frames == 3
    frames, durations = open_frames(result)
    assert len(frames) == 3
    assert durations == [100, 200, 300]


def test_concat_with_animated_image_stays_animated() -> None:
    source = make_gif()
    static = make_image((4, 2), (0, 255, 0, 255))
    result = apply_image_request(parse_image_request("横向拼接"), [source, static])
    image = Image.open(BytesIO(result))
    assert image.is_animated
    assert image.n_frames == 3
    frames, durations = open_frames(result)
    assert durations == [100, 200, 300]
    for frame in frames:
        assert frame.getpixel((9, 2)) == (0, 255, 0, 255)


async def test_superuser_skips_image_quota(quota: ImageQuotaStore, images) -> None:
    coordinator = make_coordinator(quota, images)

    result = await coordinator.handle(
        group_id=GROUP,
        user_id=USER,
        raw="水平翻转",
        event=make_event(images=(images["inline0"],)),
        bot=FakeBot(),
        unlimited=True,
    )

    assert result.image is not None
    assert "SUPERUSER" in result.text
    assert await quota.get(GROUP, USER) == 0


async def test_image_command_superuser_skips_quota(tmp_path: Path, monkeypatch) -> None:
    service = AffectionService(AffectionStore(tmp_path / "affection.json"), Config())
    quota = ImageQuotaStore(tmp_path / "image_quota.json")
    images = {"inline0": make_image()}
    coordinator = make_coordinator(quota, images)
    commands = register_commands(service, Config(), None, coordinator)
    image_cmd = commands[-1]
    sent: list[Message] = []

    async def fake_finish(message=Message(), **kwargs) -> None:
        sent.append(message if isinstance(message, Message) else Message(str(message)))

    monkeypatch.setattr(image_cmd, "finish", fake_finish)
    monkeypatch.setattr(get_driver().config, "superusers", {USER})

    await image_cmd.handlers[0].call(
        make_event(images=(images["inline0"],)),
        FakeBot(),
        ("馒头图像处理",),
        Message("灰度图"),
    )

    assert any(segment.type == "image" for segment in sent[-1])
    assert "SUPERUSER" in sent[-1].extract_plain_text()
    assert await quota.get(GROUP, USER) == 0


async def test_image_command_without_coordinator_reports_unavailable(
    tmp_path: Path, monkeypatch
) -> None:
    service = AffectionService(AffectionStore(tmp_path / "affection.json"), Config())
    image_cmd = register_commands(service, Config())[-1]
    sent: list[str] = []

    async def fake_finish(message="", **kwargs) -> None:
        sent.append(str(message))

    monkeypatch.setattr(image_cmd, "finish", fake_finish)

    await image_cmd.handlers[0].call(
        make_event(), FakeBot(), ("馒头图像处理",), Message("")
    )
    assert "还没把图像处理摆出来" in sent[-1]


async def test_image_shortcut_alias_uses_operation(tmp_path: Path, monkeypatch) -> None:
    service = AffectionService(AffectionStore(tmp_path / "affection.json"), Config())
    quota = ImageQuotaStore(tmp_path / "image_quota.json")
    images = {"inline0": make_image()}
    coordinator = make_coordinator(quota, images)
    image_cmd = register_commands(service, Config(), None, coordinator)[-1]
    sent: list[Message] = []

    async def fake_finish(message=Message(), **kwargs) -> None:
        sent.append(message if isinstance(message, Message) else Message(str(message)))

    monkeypatch.setattr(image_cmd, "finish", fake_finish)
    await quota.add(GROUP, USER, 1)

    await image_cmd.handlers[0].call(
        make_event(images=(images["inline0"],)),
        FakeBot(),
        ("翻转",),
        Message(""),
    )

    assert any(segment.type == "image" for segment in sent[-1])
    assert "水平翻转完成" in sent[-1].extract_plain_text()


async def test_too_many_images_rejected(quota: ImageQuotaStore, images) -> None:
    coordinator = make_coordinator(quota, images)
    await quota.add(GROUP, USER, 20)
    many = tuple(images["inline0"] for _ in range(10))

    result = await coordinator.handle(
        group_id=GROUP,
        user_id=USER,
        raw="灰度图",
        event=make_event(images=many),
        bot=FakeBot(),
    )

    assert result.image is None
    assert "一次最多处理 9 张图片" in result.text
    assert await quota.get(GROUP, USER) == 20
