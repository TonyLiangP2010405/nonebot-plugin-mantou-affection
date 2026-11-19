from __future__ import annotations

from pathlib import Path

from nonebot.adapters.onebot.v11 import Message

from nonebot_plugin_mantou_affection.commands import register_commands
from nonebot_plugin_mantou_affection.config import Config
from nonebot_plugin_mantou_affection.service import AffectionService
from nonebot_plugin_mantou_affection.storage import AffectionStore
from nonebot_plugin_mantou_affection.wheel_wallpapers import (
    Wallpaper,
    WallpaperUnavailable,
)

WALLPAPER = Wallpaper(
    "原神",
    "原神电脑官方壁纸第三弹",
    "https://upload-bbs.miyoushe.com/upload/a.png",
)


class FakeWallpapers:
    """记录传入的游戏过滤，并按配置返回图片或抛错。"""

    def __init__(
        self,
        *,
        result: tuple[Wallpaper, bytes] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.result = result or (WALLPAPER, b"fake-image")
        self.error = error
        self.games: list[str | None] = []

    async def draw(self, game: str | None = None) -> tuple[Wallpaper, bytes]:
        self.games.append(game)
        if self.error is not None:
            raise self.error
        return self.result


def _service(tmp_path: Path) -> AffectionService:
    return AffectionService(AffectionStore(tmp_path / "affection.json"), Config())


def _wallpaper_command(tmp_path: Path, wallpapers=None):
    commands = register_commands(_service(tmp_path), Config(), wallpapers=wallpapers)
    return next(
        command
        for command in commands
        if command.handlers[0].call.__name__ == "handle_wallpaper"
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


def test_wallpaper_command_requires_superuser(tmp_path: Path) -> None:
    command = _wallpaper_command(tmp_path)

    assert "Superuser" in repr(command.permission)


async def test_wallpaper_command_sends_random_image(
    tmp_path: Path, monkeypatch
) -> None:
    fake = FakeWallpapers()
    command = _wallpaper_command(tmp_path, fake)
    sent = _capture_send(command, monkeypatch)

    await command.handlers[0].call(Message(""))

    assert fake.games == [None]
    assert [segment.type for segment in sent[0]] == ["text", "image"]
    assert "随机壁纸（原神 · 原神电脑官方壁纸第三弹）" in _text(sent[0])


async def test_wallpaper_command_filters_by_game(tmp_path: Path, monkeypatch) -> None:
    fake = FakeWallpapers()
    command = _wallpaper_command(tmp_path, fake)
    _capture_send(command, monkeypatch)

    await command.handlers[0].call(Message("星铁"))

    assert fake.games == ["崩坏：星穹铁道"]


async def test_wallpaper_command_rejects_unknown_game(
    tmp_path: Path, monkeypatch
) -> None:
    fake = FakeWallpapers()
    command = _wallpaper_command(tmp_path, fake)
    finished = _capture_finish(command, monkeypatch)

    await command.handlers[0].call(Message("崩坏3"))

    assert "用法：/馒头壁纸" in finished[0]
    assert fake.games == []


async def test_wallpaper_command_reports_unavailable_source(
    tmp_path: Path, monkeypatch
) -> None:
    fake = FakeWallpapers(error=WallpaperUnavailable("网络炸了"))
    command = _wallpaper_command(tmp_path, fake)
    finished = _capture_finish(command, monkeypatch)

    await command.handlers[0].call(Message(""))

    assert "这次没取到壁纸" in finished[0]


async def test_wallpaper_command_without_library(tmp_path: Path, monkeypatch) -> None:
    command = _wallpaper_command(tmp_path)
    finished = _capture_finish(command, monkeypatch)

    await command.handlers[0].call(Message(""))

    assert "壁纸库没有接线" in finished[0]


async def test_wallpaper_command_falls_back_to_direct_url(
    tmp_path: Path, monkeypatch
) -> None:
    command = _wallpaper_command(tmp_path, FakeWallpapers())
    sent: list[Message] = []

    async def flaky_send(message: Message) -> None:
        sent.append(message)
        if len(sent) == 1:
            raise OSError("base64 太长发不出去")

    monkeypatch.setattr(command, "send", flaky_send)

    await command.handlers[0].call(Message(""))

    assert len(sent) == 2
    assert WALLPAPER.url in str(sent[1])
