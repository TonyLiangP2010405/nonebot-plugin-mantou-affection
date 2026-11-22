from __future__ import annotations

import base64
from pathlib import Path

import pytest
from nonebot.adapters.onebot.v11 import Message

from nonebot_plugin_mantou_affection.commands import register_commands
from nonebot_plugin_mantou_affection.config import Config
from nonebot_plugin_mantou_affection.service import AffectionService
from nonebot_plugin_mantou_affection.storage import AffectionStore
from nonebot_plugin_mantou_affection.wheel_stickers import (
    StickerAssetMissing,
    draw_local_sticker,
    scan_local_stickers,
)


def _command(tmp_path: Path, directory: str = ""):
    service = AffectionService(AffectionStore(tmp_path / "affection.json"), Config())
    commands = register_commands(
        service, Config(mantou_affection_wheel_sticker_dir=directory)
    )
    return next(
        command
        for command in commands
        if command.handlers[0].call.__name__ == "handle_sticker_test"
    )


def _capture_send(command, monkeypatch) -> list[Message]:
    sent: list[Message] = []

    async def fake_send(message: Message) -> None:
        sent.append(message)

    monkeypatch.setattr(command, "send", fake_send)
    return sent


def _capture_finish(command, monkeypatch) -> list[str]:
    finished: list[str] = []

    async def fake_finish(message: Message | str = "", **kwargs) -> None:
        finished.append(str(message))

    monkeypatch.setattr(command, "finish", fake_finish)
    return finished


def _text(message: Message) -> str:
    return "".join(
        segment.data.get("text", "") for segment in message if segment.type == "text"
    )


def _sticker_folder(tmp_path: Path) -> Path:
    folder = tmp_path / "馒头 表情包"
    folder.mkdir()
    (folder / "微笑.PNG").write_bytes(b"first-sticker")
    (folder / "说明.txt").write_bytes(b"not an image")
    nested = folder / "子目录.png"
    nested.mkdir()
    (nested / "另一张.png").write_bytes(b"nested-sticker")
    return folder


def test_sticker_command_requires_superuser(tmp_path: Path) -> None:
    assert "Superuser" in repr(_command(tmp_path).permission)


async def test_sticker_command_sends_image_and_file_name(
    tmp_path: Path, monkeypatch
) -> None:
    command = _command(tmp_path, str(_sticker_folder(tmp_path)))
    sent = _capture_send(command, monkeypatch)

    await command.handlers[0].call()

    assert len(sent) == 1
    assert [segment.type for segment in sent[0]] == ["text", "image"]
    assert "共 1 张" in _text(sent[0])
    assert "微笑.PNG" in _text(sent[0])
    assert str(tmp_path / "馒头 表情包") in _text(sent[0])
    image = next(segment for segment in sent[0] if segment.type == "image")
    encoded = image.data["file"].removeprefix("base64://")
    assert base64.b64decode(encoded) == b"first-sticker"


async def test_sticker_command_reports_unconfigured_directory(
    tmp_path: Path, monkeypatch
) -> None:
    command = _command(tmp_path)
    finished = _capture_finish(command, monkeypatch)

    await command.handlers[0].call()

    assert "MANTOU_AFFECTION_WHEEL_STICKER_DIR" in finished[0]


async def test_sticker_command_rejects_relative_directory(
    tmp_path: Path, monkeypatch
) -> None:
    command = _command(tmp_path, "表情包/馒头")
    finished = _capture_finish(command, monkeypatch)

    await command.handlers[0].call()

    assert "不是绝对路径" in finished[0]
    assert "表情包/馒头" in finished[0]


@pytest.mark.parametrize("name", ["missing", "empty"])
async def test_sticker_command_reports_unreadable_directory(
    tmp_path: Path, monkeypatch, name: str
) -> None:
    folder = tmp_path / name
    if name == "empty":
        folder.mkdir()
    command = _command(tmp_path, str(folder))
    finished = _capture_finish(command, monkeypatch)

    await command.handlers[0].call()

    assert str(folder) in finished[0]
    assert "读取馒头表情包目录失败" in finished[0]


async def test_sticker_command_reports_missing_images(
    tmp_path: Path, monkeypatch
) -> None:
    folder = tmp_path / "只有文本"
    folder.mkdir()
    (folder / "说明.txt").write_text("not an image", encoding="utf-8")
    command = _command(tmp_path, str(folder))
    finished = _capture_finish(command, monkeypatch)

    await command.handlers[0].call()

    assert "没有可用图片" in finished[0]


async def test_sticker_command_reports_send_failure(
    tmp_path: Path, monkeypatch
) -> None:
    command = _command(tmp_path, str(_sticker_folder(tmp_path)))

    async def boom(message: Message) -> None:
        raise OSError("base64 太长发不出去")

    monkeypatch.setattr(command, "send", boom)
    finished = _capture_finish(command, monkeypatch)

    await command.handlers[0].call()

    assert "微笑.PNG" in finished[0]
    assert "发送图片失败" in finished[0]


def test_scan_local_stickers_uses_top_level_images_only(tmp_path: Path) -> None:
    folder = _sticker_folder(tmp_path)

    assert [path.name for path in scan_local_stickers(str(folder))] == ["微笑.PNG"]


def test_scan_local_stickers_requires_absolute_path(tmp_path: Path) -> None:
    with pytest.raises(StickerAssetMissing):
        scan_local_stickers("relative-dir")


def test_scan_local_stickers_rejects_empty_config() -> None:
    with pytest.raises(StickerAssetMissing):
        scan_local_stickers("")


def test_draw_local_sticker_returns_listing_and_file(tmp_path: Path) -> None:
    picked = draw_local_sticker(str(_sticker_folder(tmp_path)))

    assert picked.path.name == "微笑.PNG"
    assert picked.data == b"first-sticker"
    assert len(picked.images) == 1
    assert picked.directory.name == "馒头 表情包"
