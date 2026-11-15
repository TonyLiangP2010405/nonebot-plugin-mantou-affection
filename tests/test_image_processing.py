from __future__ import annotations

from io import BytesIO
from pathlib import Path
from time import time

import pytest
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment
from nonebot.adapters.onebot.v11.event import Sender
from PIL import Image

from nonebot_plugin_mantou_affection.commands import register_commands
from nonebot_plugin_mantou_affection.config import Config
from nonebot_plugin_mantou_affection.image_processing import (
    NO_IMAGE_REPLY,
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


def open_image(data: bytes) -> Image.Image:
    return Image.open(BytesIO(data))


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


@pytest.mark.parametrize("raw", ["旋转 拉满", "旋转 361", "翻转", "水平翻转 多余"])
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
    assert gray.mode == "L"
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
        make_event(images=(images["inline0"],)), FakeBot(), Message("灰度图")
    )
    assert "次数不够" in sent[-1].extract_plain_text()

    await quota.add(GROUP, USER, 1)
    await image_cmd.handlers[0].call(
        make_event(images=(images["inline0"],)), FakeBot(), Message("灰度图")
    )
    assert any(segment.type == "image" for segment in sent[-1])
    assert "消耗 1 次" in sent[-1].extract_plain_text()
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

    await image_cmd.handlers[0].call(make_event(), FakeBot(), Message(""))
    assert "还没把图像处理摆出来" in sent[-1]


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
