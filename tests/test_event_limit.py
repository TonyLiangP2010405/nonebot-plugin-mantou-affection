from __future__ import annotations

import asyncio
import random
from pathlib import Path
from time import time

import pytest
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message
from nonebot.adapters.onebot.v11.event import Sender

from nonebot_plugin_mantou_affection.ambient import EventCoordinator, register_ambient
from nonebot_plugin_mantou_affection.commands import register_commands
from nonebot_plugin_mantou_affection.config import Config
from nonebot_plugin_mantou_affection.copywriting import AffectionTextLibrary
from nonebot_plugin_mantou_affection.event_wheel import (
    EVENT_WHEEL_AMBIENT,
    EVENT_WHEEL_FINDCHAR,
)
from nonebot_plugin_mantou_affection.events import EventLibrary
from nonebot_plugin_mantou_affection.models import Profile
from nonebot_plugin_mantou_affection.parsing import (
    parse_event_limit_count,
    parse_event_limit_toggle,
)
from nonebot_plugin_mantou_affection.service import (
    EVENT_LIMIT_WINDOW_SECONDS,
    AffectionService,
)
from nonebot_plugin_mantou_affection.storage import AffectionStore

GROUP_ID = "90001"
OTHER_GROUP_ID = "90002"
TRIGGER_ID = "90003"


def _service(
    tmp_path: Path,
    config: Config | None = None,
    *,
    text_library: AffectionTextLibrary | None = None,
) -> AffectionService:
    return AffectionService(
        AffectionStore(tmp_path / "affection.json"),
        config or Config(mantou_affection_newbie_protection_below=0),
        text_library=text_library,
        rng=random.Random(0),
    )


def _event(group_id: int = 90001, user_id: int = 90003) -> GroupMessageEvent:
    message = Message("晚上好")
    return GroupMessageEvent(
        time=int(time()),
        self_id=10000,
        post_type="message",
        sub_type="normal",
        user_id=user_id,
        message_type="group",
        message_id=1,
        message=message,
        original_message=message,
        raw_message="晚上好",
        font=0,
        sender=Sender(user_id=user_id, nickname="桃友", role="member"),
        to_me=False,
        group_id=group_id,
    )


async def _give_affection(service: AffectionService, score: int = 10) -> None:
    def bump(profile: Profile) -> None:
        profile.affection = score

    await service.store.update_profile(GROUP_ID, TRIGGER_ID, "桃友", bump)


class _FixedSlotRng(random.Random):
    """让事件转盘固定转到指定格子。"""

    def __init__(self, slot: str) -> None:
        super().__init__(0)
        self.slot = slot

    def choice(self, seq):
        items = list(seq)
        return self.slot if self.slot in items else items[0]


async def _settle_wheel(coordinator: EventCoordinator) -> None:
    """等事件转盘倒计时结束、活动真正开起来。"""

    assert coordinator.event_wheel is not None
    await coordinator.event_wheel.wait_settled()


def _capture_send(matcher, monkeypatch) -> list[Message]:
    sent: list[Message] = []

    async def fake_send(message) -> None:
        sent.append(message if isinstance(message, Message) else Message(message))

    monkeypatch.setattr(matcher, "send", fake_send)
    return sent


def _find_command(commands, handler_name: str):
    return next(
        command
        for command in commands
        if command.handlers[0].call.__name__ == handler_name
    )


def test_event_limit_defaults() -> None:
    config = Config()
    assert config.mantou_affection_event_limit_enabled is False
    assert config.mantou_affection_event_limit_per_window == 5
    assert EVENT_LIMIT_WINDOW_SECONDS == 2 * 3600


def test_event_limit_accepts_legacy_config_name() -> None:
    config = Config(mantou_affection_event_limit_per_hour=8)
    assert config.mantou_affection_event_limit_per_window == 8


async def test_disabled_limit_allows_everything_and_records_nothing(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    for _ in range(20):
        await service.record_event_trigger(GROUP_ID)
    assert await service.event_limit_allows(GROUP_ID) is True
    assert await service.event_limit_used(GROUP_ID) == 0


async def test_enabled_limit_blocks_after_quota(tmp_path: Path) -> None:
    service = _service(
        tmp_path,
        Config(
            mantou_affection_event_limit_enabled=True,
            mantou_affection_event_limit_per_window=2,
        ),
    )
    now = time()
    assert await service.record_event_trigger(GROUP_ID, now=now) == 1
    assert await service.record_event_trigger(GROUP_ID, now=now) == 2
    assert await service.event_limit_used(GROUP_ID, now=now) == 2
    assert await service.event_limit_allows(GROUP_ID) is False

    state = await service.event_limit_state(GROUP_ID, now=now)
    assert state.enabled is True
    assert state.per_window == 2
    assert state.used == 2
    assert state.remaining == 0


async def test_concurrent_records_are_not_lost(tmp_path: Path) -> None:
    service = _service(
        tmp_path,
        Config(
            mantou_affection_event_limit_enabled=True,
            mantou_affection_event_limit_per_window=100,
        ),
    )
    await asyncio.gather(
        *(service.record_event_trigger(GROUP_ID) for _ in range(10))
    )
    assert await service.event_limit_used(GROUP_ID) == 10


async def test_limit_window_rolls_after_two_hours(tmp_path: Path) -> None:
    service = _service(
        tmp_path,
        Config(
            mantou_affection_event_limit_enabled=True,
            mantou_affection_event_limit_per_window=1,
        ),
    )
    now = time()
    await service.record_event_trigger(GROUP_ID, now=now)
    assert await service.event_limit_used(GROUP_ID, now=now) == 1
    assert await service.event_limit_used(
        GROUP_ID, now=now + EVENT_LIMIT_WINDOW_SECONDS - 1
    ) == 1
    assert await service.event_limit_used(
        GROUP_ID, now=now + EVENT_LIMIT_WINDOW_SECONDS + 1
    ) == 0


async def test_runtime_override_takes_precedence(tmp_path: Path) -> None:
    service = _service(tmp_path)  # 配置默认关闭
    await service.set_event_limit_enabled(True)
    await service.set_event_limit_per_window(1)
    await service.record_event_trigger(GROUP_ID)
    assert await service.event_limit_allows(GROUP_ID) is False


async def test_limit_is_tracked_per_group(tmp_path: Path) -> None:
    service = _service(
        tmp_path,
        Config(
            mantou_affection_event_limit_enabled=True,
            mantou_affection_event_limit_per_window=1,
        ),
    )
    now = time()
    await service.record_event_trigger(GROUP_ID, now=now)
    assert await service.event_limit_allows(GROUP_ID) is False
    assert await service.event_limit_allows(OTHER_GROUP_ID) is True


async def test_limit_survives_restart(tmp_path: Path) -> None:
    path = tmp_path / "affection.json"
    config = Config(
        mantou_affection_event_limit_enabled=True,
        mantou_affection_event_limit_per_window=1,
    )
    service = AffectionService(AffectionStore(path), config)
    await service.set_event_limit_enabled(True)
    await service.record_event_trigger(GROUP_ID)

    reopened = AffectionService(AffectionStore(path), config)
    assert await reopened.event_limit_enabled() is True
    assert await reopened.event_limit_used(GROUP_ID) == 1
    assert await reopened.event_limit_allows(GROUP_ID) is False


async def test_limit_ignores_broken_history(tmp_path: Path) -> None:
    service = _service(
        tmp_path,
        Config(
            mantou_affection_event_limit_enabled=True,
            mantou_affection_event_limit_per_window=1,
        ),
    )
    await service.store.set_setting("event_limit_history", "not a dict")
    assert await service.event_limit_used(GROUP_ID) == 0
    assert await service.event_limit_allows(GROUP_ID) is True


async def test_limit_per_window_override_is_clamped(tmp_path: Path) -> None:
    service = _service(tmp_path)
    await service.store.set_setting("event_limit_per_window", 9999)
    assert await service.event_limit_per_window() == 100
    await service.store.set_setting("event_limit_per_window", 0)
    assert await service.event_limit_per_window() == 1
    await service.store.set_setting("event_limit_per_window", "oops")
    assert await service.event_limit_per_window() == 5


async def test_legacy_stored_limit_is_still_read(tmp_path: Path) -> None:
    service = _service(tmp_path)
    await service.store.set_setting("event_limit_per_hour", 7)
    assert await service.event_limit_per_window() == 7

    await service.set_event_limit_per_window(9)
    assert await service.event_limit_per_window() == 9


async def test_ambient_handler_stays_quiet_when_quota_is_spent(
    bundled_texts_path: Path, tmp_path: Path, monkeypatch
) -> None:
    config = Config(
        mantou_affection_event_wheel_chance=1.0,
        mantou_affection_event_wheel_timeout=0.01,
        mantou_affection_newbie_protection_below=0,
        mantou_affection_event_limit_enabled=True,
        mantou_affection_event_limit_per_window=5,
        mantou_affection_wheel_chance=0.0,
    )
    service = _service(tmp_path, config, text_library=AffectionTextLibrary(bundled_texts_path))
    for _ in range(5):
        await service.record_event_trigger(GROUP_ID)
    coordinator = EventCoordinator(service, config)
    ambient, _answer = register_ambient(service, config, coordinator)
    sent = _capture_send(ambient, monkeypatch)
    await _give_affection(service)

    await ambient.handlers[0].call(_event())

    assert sent == []
    assert coordinator.pending == {}


async def test_ambient_handler_records_real_activity(
    bundled_texts_path: Path, tmp_path: Path, monkeypatch
) -> None:
    config = Config(
        mantou_affection_event_wheel_chance=1.0,
        mantou_affection_event_wheel_timeout=0.01,
        mantou_affection_ambient_enabled=False,
        mantou_affection_newbie_protection_below=0,
        mantou_affection_event_limit_enabled=True,
        mantou_affection_event_limit_per_window=5,
        mantou_affection_wheel_chance=0.0,
    )
    service = _service(tmp_path, config)
    coordinator = EventCoordinator(service, config, rng=_FixedSlotRng(EVENT_WHEEL_FINDCHAR))
    ambient, _answer = register_ambient(service, config, coordinator)
    sent = _capture_send(ambient, monkeypatch)
    await _give_affection(service)

    await ambient.handlers[0].call(_event())
    await _settle_wheel(coordinator)

    assert sent
    assert GROUP_ID in coordinator.findchar.pending
    assert await service.event_limit_used(GROUP_ID) == 1
    coordinator.findchar.pending[GROUP_ID].task.cancel()


async def test_plain_ambient_reaction_does_not_consume_quota(
    bundled_texts_path: Path, tmp_path: Path, monkeypatch
) -> None:
    config = Config(
        mantou_affection_event_wheel_chance=1.0,
        mantou_affection_event_wheel_timeout=0.01,
        mantou_affection_wheel_chance=0.0,
        mantou_affection_newbie_protection_below=0,
        mantou_affection_event_limit_enabled=True,
        mantou_affection_event_limit_per_window=5,
    )
    service = _service(tmp_path, config, text_library=AffectionTextLibrary(bundled_texts_path))
    coordinator = EventCoordinator(service, config, rng=_FixedSlotRng(EVENT_WHEEL_AMBIENT))
    ambient, _answer = register_ambient(service, config, coordinator)
    sent = _capture_send(ambient, monkeypatch)
    await _give_affection(service)

    await ambient.handlers[0].call(_event())
    await _settle_wheel(coordinator)

    assert len(sent) == 3
    assert "事件转盘转到了「馒头小动作」" in str(sent[1])
    assert await service.event_limit_used(GROUP_ID) == 0


async def test_poke_event_respects_limit(
    bundled_texts_path: Path, tmp_path: Path
) -> None:
    config = Config(
        mantou_affection_event_limit_enabled=True,
        mantou_affection_event_limit_per_window=1,
        mantou_affection_newbie_protection_below=0,
    )
    service = _service(tmp_path, config)
    library = EventLibrary(bundled_texts_path.with_name("affection_events.json"))
    coordinator = EventCoordinator(service, config, library, rng=random.Random(0))
    await service.record_event_trigger(GROUP_ID)

    async def fake_send(message: Message) -> None:
        return None

    started = await coordinator.start_poke_event(
        group_id=GROUP_ID,
        user_id=TRIGGER_ID,
        nickname="桃友",
        send=fake_send,
        chance=1.0,
    )
    assert started is None
    assert coordinator.pending == {}

    await service.set_event_limit_enabled(False)
    started = await coordinator.start_poke_event(
        group_id=GROUP_ID,
        user_id=TRIGGER_ID,
        nickname="桃友",
        send=fake_send,
        chance=1.0,
    )
    assert started is not None
    assert GROUP_ID in coordinator.pending
    coordinator.pending[GROUP_ID].task.cancel()


@pytest.mark.parametrize("text", ["开", "开启", "打开", "启用", "on", "TRUE", "1", "是"])
def test_parse_event_limit_toggle_on(text: str) -> None:
    assert parse_event_limit_toggle(text) is True


@pytest.mark.parametrize("text", ["关", "关闭", "关掉", "禁用", "off", "false", "0", "否"])
def test_parse_event_limit_toggle_off(text: str) -> None:
    assert parse_event_limit_toggle(text) is False


@pytest.mark.parametrize("text", ["", "也许", "2"])
def test_parse_event_limit_toggle_errors(text: str) -> None:
    with pytest.raises(ValueError, match="开"):
        parse_event_limit_toggle(text)


@pytest.mark.parametrize(("text", "expected"), [("5", 5), (" 1 ", 1), ("100", 100)])
def test_parse_event_limit_count(text: str, expected: int) -> None:
    assert parse_event_limit_count(text, minimum=1, maximum=100) == expected


@pytest.mark.parametrize("text", ["0", "101", "-1", "abc", ""])
def test_parse_event_limit_count_errors(text: str) -> None:
    with pytest.raises(ValueError, match="1~100"):
        parse_event_limit_count(text, minimum=1, maximum=100)


async def test_limit_toggle_command_reports_and_switches(
    tmp_path: Path, monkeypatch
) -> None:
    service = _service(tmp_path)
    command = _find_command(register_commands(service, Config()), "handle_event_limit_toggle")
    sent: list[str] = []

    async def fake_finish(message: Message | str = "", **kwargs) -> None:
        sent.append(str(message))

    monkeypatch.setattr(command, "finish", fake_finish)
    assert "Superuser" in repr(command.permission)

    await command.handlers[0].call(_event(), Message(""))
    assert "事件限制：已关闭" in sent[0]
    assert "每群每 2 小时上限：5 次" in sent[0]
    assert "活动开启时段：每天 09:00~16:00" in sent[0]

    await command.handlers[0].call(_event(), Message("开"))
    assert sent[1] == (
        "已开启事件限制：每群每 2 小时最多触发 5 次活动。\n"
        "可用 /馒头限制次数 N 调整上限（1~100）。"
    )
    assert await service.event_limit_enabled() is True

    await service.record_event_trigger(GROUP_ID)
    await command.handlers[0].call(_event(), Message(""))
    assert "本群最近 2 小时内已触发 1 次，剩余 4 次" in sent[-1]

    await command.handlers[0].call(_event(), Message("关"))
    assert sent[-1] == "已关闭事件限制：活动触发次数不再受限制。"
    assert await service.event_limit_enabled() is False


async def test_limit_count_command_reports_and_sets(tmp_path: Path, monkeypatch) -> None:
    service = _service(tmp_path)
    command = _find_command(register_commands(service, Config()), "handle_event_limit_count")
    sent: list[str] = []

    async def fake_finish(message: Message | str = "", **kwargs) -> None:
        sent.append(str(message))

    monkeypatch.setattr(command, "finish", fake_finish)
    assert "Superuser" in repr(command.permission)

    await command.handlers[0].call(Message(""))
    assert "当前事件限制：每群每 2 小时最多 5 次（限制已关闭）" in sent[0]

    await command.handlers[0].call(Message("8"))
    assert sent[1] == (
        "事件限制已调整为每群每 2 小时 8 次"
        "（限制当前已关闭，发送 /馒头限制开关 开 后生效）"
    )
    assert await service.event_limit_per_window() == 8

    await command.handlers[0].call(Message("0"))
    assert "请输入 1~100 之间的整数" in sent[2]

    await service.set_event_limit_enabled(True)
    await command.handlers[0].call(Message(""))
    assert "当前事件限制：每群每 2 小时最多 8 次（限制已开启）" in sent[-1]


async def test_admin_help_command_lists_every_command(
    tmp_path: Path, monkeypatch
) -> None:
    service = _service(tmp_path)
    command = _find_command(register_commands(service, Config()), "handle_admin_help")
    sent: list[str] = []

    async def fake_finish(message: Message | str = "", **kwargs) -> None:
        sent.append(str(message))

    monkeypatch.setattr(command, "finish", fake_finish)
    assert "Superuser" in repr(command.permission)

    await command.handlers[0].call()

    text = sent[-1]
    assert "完整命令帮助" in text
    listed = {
        line.split(" ", 1)[0]
        for line in text.splitlines()
        if line.startswith("/")
    }
    assert {
        "/馒头互动",
        "/馒头好感",
        "/馒头好感榜",
        "/馒头帮助",
        "/馒头好感调整",
        "/馒头概率总调",
        "/馒头限制开关",
        "/馒头限制次数",
        "/馒头管理帮助",
        "/馒头强制事件",
        "/馒头图像处理",
    }.issubset(listed)
