from __future__ import annotations

import random
from datetime import datetime
from pathlib import Path
from time import time
from zoneinfo import ZoneInfo

import pytest
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message
from nonebot.adapters.onebot.v11.event import Sender

from nonebot_plugin_mantou_affection import ambient as ambient_module
from nonebot_plugin_mantou_affection.ambient import EventCoordinator, register_ambient
from nonebot_plugin_mantou_affection.config import Config
from nonebot_plugin_mantou_affection.copywriting import AffectionTextLibrary
from nonebot_plugin_mantou_affection.event_wheel import (
    EVENT_WHEEL_AMBIENT,
    EVENT_WHEEL_FINDCHAR,
)
from nonebot_plugin_mantou_affection.events import EventLibrary
from nonebot_plugin_mantou_affection.findchar import (
    BUNDLED_PUZZLES_PATH,
    FindCharCoordinator,
    FindCharLibrary,
)
from nonebot_plugin_mantou_affection.models import Profile
from nonebot_plugin_mantou_affection.service import AffectionService, hour_in_active_window
from nonebot_plugin_mantou_affection.storage import AffectionStore

# 这个模块验证开启时段本身, 用 @pytest.mark.real_active_hours 关掉 conftest 里
# 「全天开放」的兜底; 其余模块默认不受实际时间影响。
pytestmark = pytest.mark.real_active_hours

GROUP_ID = "90001"
TRIGGER_ID = "90003"
TIMEZONE = ZoneInfo("Asia/Shanghai")


def _at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 5, 20, hour, minute, tzinfo=TIMEZONE)


def _event() -> GroupMessageEvent:
    message = Message("晚上好")
    return GroupMessageEvent(
        time=int(time()),
        self_id=10000,
        post_type="message",
        sub_type="normal",
        user_id=int(TRIGGER_ID),
        message_type="group",
        message_id=1,
        message=message,
        original_message=message,
        raw_message="晚上好",
        font=0,
        sender=Sender(user_id=int(TRIGGER_ID), nickname="桃友", role="member"),
        to_me=False,
        group_id=int(GROUP_ID),
    )


def _service(
    tmp_path: Path,
    config: Config,
    *,
    text_library: AffectionTextLibrary | None = None,
) -> AffectionService:
    return AffectionService(
        AffectionStore(tmp_path / "affection.json"),
        config,
        text_library=text_library,
        rng=random.Random(0),
    )


async def _give_affection(service: AffectionService, score: int = 10) -> None:
    def bump(profile: Profile) -> None:
        profile.affection = score

    await service.store.update_profile(GROUP_ID, TRIGGER_ID, "桃友", bump)


def _pin_time(monkeypatch, service: AffectionService, moment: datetime) -> None:
    """把服务里的「现在几点」固定下来，让时段判定脱离真实时间。"""

    monkeypatch.setattr(service, "_now", lambda: moment)


def _capture_send(matcher, monkeypatch) -> list[Message]:
    sent: list[Message] = []

    async def fake_send(message: Message) -> None:
        sent.append(message)

    monkeypatch.setattr(matcher, "send", fake_send)
    return sent


class _FixedSlotRng(random.Random):
    """让事件转盘固定转到指定格子。"""

    def __init__(self, slot: str) -> None:
        super().__init__(0)
        self.slot = slot

    def choice(self, seq):
        items = list(seq)
        return self.slot if self.slot in items else items[0]


class _FakeWheel:
    """只记录有没有被叫起来的转盘替身。"""

    def __init__(self, started: bool = True) -> None:
        self.started = started
        self.calls = 0
        self.pending: dict[str, object] = {}

    async def start(self, **kwargs) -> bool:
        self.calls += 1
        return self.started


def _quiet_config(**kwargs) -> Config:
    """默认什么都不触发的配置，用来观察「有没有说话」。"""

    kwargs.setdefault("mantou_affection_ambient_enabled", True)
    kwargs.setdefault("mantou_affection_event_wheel_chance", 0.0)
    kwargs.setdefault("mantou_affection_event_wheel_timeout", 0.01)
    kwargs.setdefault("mantou_affection_wheel_chance", 0.0)
    kwargs.setdefault("mantou_affection_newbie_protection_below", 0)
    return Config(**kwargs)


# ------------------------------------------------------------ 时段判定


def test_hour_in_active_window_is_half_open() -> None:
    assert hour_in_active_window(8, 9, 16) is False
    assert hour_in_active_window(9, 9, 16) is True
    assert hour_in_active_window(15, 9, 16) is True
    assert hour_in_active_window(16, 9, 16) is False


def test_hour_in_active_window_wraps_midnight_and_all_day() -> None:
    assert hour_in_active_window(23, 22, 6) is True
    assert hour_in_active_window(5, 22, 6) is True
    assert hour_in_active_window(6, 22, 6) is False
    assert hour_in_active_window(12, 22, 6) is False
    assert hour_in_active_window(3, 9, 9) is True


def test_default_active_hours_are_nine_to_sixteen(tmp_path: Path) -> None:
    config = Config()
    assert config.mantou_affection_active_hours_enabled is True
    assert config.mantou_affection_active_hours_start == 9
    assert config.mantou_affection_active_hours_end == 16

    service = _service(tmp_path, config)
    assert service.activity_hours_open(now=_at(8, 59)) is False
    assert service.activity_hours_open(now=_at(9, 0)) is True
    assert service.activity_hours_open(now=_at(15, 59)) is True
    assert service.activity_hours_open(now=_at(16, 0)) is False


def test_active_hours_follow_config_and_can_be_disabled(tmp_path: Path) -> None:
    night_owl = _service(
        tmp_path,
        Config(
            mantou_affection_active_hours_start=20,
            mantou_affection_active_hours_end=23,
        ),
    )
    assert night_owl.activity_hours_open(now=_at(21)) is True
    assert night_owl.activity_hours_open(now=_at(3)) is False

    always = _service(
        tmp_path, Config(mantou_affection_active_hours_enabled=False)
    )
    assert always.activity_hours_open(now=_at(3)) is True


# ------------------------------------------------------------ 群消息活动


def _ambient_setup(
    bundled_texts_path: Path,
    tmp_path: Path,
    monkeypatch,
    config: Config,
    *,
    rng: random.Random | None = None,
) -> tuple:
    service = _service(
        tmp_path, config, text_library=AffectionTextLibrary(bundled_texts_path)
    )
    coordinator = EventCoordinator(service, config, rng=rng or random.Random(0))
    ambient, _answer = register_ambient(service, config, coordinator)
    sent = _capture_send(ambient, monkeypatch)
    return service, coordinator, ambient, sent


async def _settle_wheel(coordinator) -> None:
    """等事件转盘倒计时结束、活动真正开起来。"""

    assert coordinator.event_wheel is not None
    await coordinator.event_wheel.wait_settled()


async def test_ambient_handler_stays_quiet_after_hours(
    bundled_texts_path: Path, tmp_path: Path, monkeypatch
) -> None:
    service, coordinator, ambient, sent = _ambient_setup(
        bundled_texts_path, tmp_path, monkeypatch, _quiet_config()
    )
    _pin_time(monkeypatch, service, _at(3))
    await _give_affection(service)

    await ambient.handlers[0].call(_event())

    assert sent == []
    assert coordinator.pending == {}


async def test_ambient_handler_speaks_within_hours(
    bundled_texts_path: Path, tmp_path: Path, monkeypatch
) -> None:
    service, _coordinator, ambient, sent = _ambient_setup(
        bundled_texts_path,
        tmp_path,
        monkeypatch,
        _quiet_config(mantou_affection_event_wheel_chance=1.0),
        rng=_FixedSlotRng(EVENT_WHEEL_AMBIENT),
    )
    _pin_time(monkeypatch, service, _at(10))
    await _give_affection(service)

    await ambient.handlers[0].call(_event())
    await _settle_wheel(_coordinator)

    assert len(sent) == 3
    assert "触发了事件转盘" in str(sent[0])
    assert "事件转盘转到了「馒头小动作」" in str(sent[1])


async def test_ambient_handler_starts_activity_within_hours(
    bundled_texts_path: Path, tmp_path: Path, monkeypatch
) -> None:
    service, coordinator, ambient, sent = _ambient_setup(
        bundled_texts_path,
        tmp_path,
        monkeypatch,
        _quiet_config(mantou_affection_event_wheel_chance=1.0),
        rng=_FixedSlotRng(EVENT_WHEEL_FINDCHAR),
    )
    _pin_time(monkeypatch, service, _at(10, 30))
    await _give_affection(service)

    await ambient.handlers[0].call(_event())
    await _settle_wheel(coordinator)

    assert sent
    assert GROUP_ID in coordinator.findchar.pending
    coordinator.findchar.pending[GROUP_ID].task.cancel()


async def test_ambient_handler_skips_activity_after_hours(
    bundled_texts_path: Path, tmp_path: Path, monkeypatch
) -> None:
    service, coordinator, ambient, sent = _ambient_setup(
        bundled_texts_path,
        tmp_path,
        monkeypatch,
        _quiet_config(mantou_affection_event_wheel_chance=1.0),
        rng=_FixedSlotRng(EVENT_WHEEL_FINDCHAR),
    )
    _pin_time(monkeypatch, service, _at(3))
    await _give_affection(service)

    await ambient.handlers[0].call(_event())

    assert sent == []
    assert coordinator.findchar.pending == {}


async def test_wheel_still_spins_after_hours(
    bundled_texts_path: Path, tmp_path: Path, monkeypatch
) -> None:
    fake_wheel = _FakeWheel()
    monkeypatch.setattr(ambient_module, "get_bot", lambda: object())
    config = _quiet_config(mantou_affection_wheel_chance=1.0)
    service = _service(
        tmp_path, config, text_library=AffectionTextLibrary(bundled_texts_path)
    )
    coordinator = EventCoordinator(service, config)
    ambient, _answer = register_ambient(service, config, coordinator, wheel=fake_wheel)
    _capture_send(ambient, monkeypatch)
    _pin_time(monkeypatch, service, _at(3))
    await _give_affection(service)

    await ambient.handlers[0].call(_event())

    assert fake_wheel.calls == 1


# ------------------------------------------------------------ 戳一戳


async def test_poke_event_respects_active_hours(
    tmp_path: Path, bundled_texts_path: Path, monkeypatch
) -> None:
    config = Config(mantou_affection_newbie_protection_below=0)
    service = _service(tmp_path, config)
    library = EventLibrary(bundled_texts_path.with_name("affection_events.json"))
    coordinator = EventCoordinator(service, config, library, rng=random.Random(0))

    async def fake_send(message: Message) -> None:
        return None

    _pin_time(monkeypatch, service, _at(3))
    started = await coordinator.start_poke_event(
        group_id=GROUP_ID,
        user_id=TRIGGER_ID,
        nickname="桃友",
        send=fake_send,
        chance=1.0,
    )
    assert started is None
    assert coordinator.pending == {}

    _pin_time(monkeypatch, service, _at(10))
    started = await coordinator.start_poke_event(
        group_id=GROUP_ID,
        user_id=TRIGGER_ID,
        nickname="桃友",
        send=fake_send,
        chance=1.0,
    )
    assert started is not None
    coordinator.pending[GROUP_ID].task.cancel()


async def test_poke_find_char_respects_active_hours(
    tmp_path: Path, monkeypatch
) -> None:
    config = Config(mantou_affection_newbie_protection_below=0)
    service = _service(tmp_path, config)
    find_char = FindCharCoordinator(
        service,
        config,
        FindCharLibrary(BUNDLED_PUZZLES_PATH),
        rng=random.Random(0),
    )

    async def fake_send(message: Message) -> None:
        return None

    _pin_time(monkeypatch, service, _at(3))
    started = await find_char.start_poke_find_char(
        group_id=GROUP_ID,
        user_id=TRIGGER_ID,
        nickname="桃友",
        send=fake_send,
        chance=1.0,
    )
    assert started is None
    assert find_char.pending == {}

    _pin_time(monkeypatch, service, _at(10))
    started = await find_char.start_poke_find_char(
        group_id=GROUP_ID,
        user_id=TRIGGER_ID,
        nickname="桃友",
        send=fake_send,
        chance=1.0,
    )
    assert started is not None
    find_char.pending[GROUP_ID].task.cancel()
