from __future__ import annotations

import random
from types import SimpleNamespace

from nonebot_plugin_mantou_affection.config import Config
from nonebot_plugin_mantou_affection.event_wheel import (
    EVENT_WHEEL_AMBIENT,
    EVENT_WHEEL_BET,
    EVENT_WHEEL_EVENT,
    EVENT_WHEEL_FINDCHAR,
    EVENT_WHEEL_GROUP_QUIZ,
    EVENT_WHEEL_LABELS,
    EVENT_WHEEL_PK,
    EVENT_WHEEL_SLOTS,
    EVENT_WHEEL_WHEEL,
    EVENT_WHEEL_WHOAMI,
    EventWheelCoordinator,
)
from nonebot_plugin_mantou_affection.force import FORCE_ACTIVITY_LINES

GROUP_ID = "97001"
USER_ID = "97002"

AMBIENT_COPY = "馒头看了看你。"


class FixedSlotRng(random.Random):
    """让事件转盘固定转到指定格子。"""

    def __init__(self, slot: str | None) -> None:
        super().__init__(0)
        self.slot = slot

    def choice(self, seq):  # type: ignore[override]
        items = list(seq)
        if self.slot is None:
            return items[0]
        return self.slot if self.slot in items else items[0]


def _library(**attrs) -> SimpleNamespace:
    return SimpleNamespace(**attrs)


def _quiz(*, ready: bool = True, started: bool = True) -> SimpleNamespace:
    async def start(**kwargs) -> bool:
        return started

    return SimpleNamespace(
        library=_library(questions=("q",)) if ready else None,
        start=start,
    )


def _wheel() -> SimpleNamespace:
    async def start(**kwargs) -> bool:
        return True

    return SimpleNamespace(start=start)


def _events(*, ready: bool = True, started: bool = True) -> SimpleNamespace:
    def start(group_id: str, user_id: str, nickname: str):
        if not started:
            return None
        return SimpleNamespace()

    def event_message(pending) -> str:
        return "⚡ 触发随机事件！"

    def schedule(pending, **kwargs) -> None:
        return None

    return SimpleNamespace(
        library=_library(events=("e",)) if ready else None,
        start=start,
        event_message=event_message,
        schedule=schedule,
    )


def _findchar(*, ready: bool = True, started: bool = True) -> SimpleNamespace:
    def start(group_id: str, user_id: str, nickname: str):
        if not started:
            return None
        return SimpleNamespace()

    def message(pending) -> str:
        return "🔍 找字小游戏！"

    def schedule(pending, **kwargs) -> None:
        return None

    return SimpleNamespace(
        library=_library(puzzles=("p",)) if ready else None,
        start=start,
        message=message,
        schedule=schedule,
    )


def _bet(*, candidates: int = 3, started: bool = True) -> SimpleNamespace:
    def recent_candidates(group_id: str, *, minutes: int):
        return [(f"90{i}", f"甲{i}") for i in range(candidates)]

    async def maybe_start(**kwargs) -> bool:
        return started

    return SimpleNamespace(
        library=_library(rounds=("r",)),
        recent_candidates=recent_candidates,
        maybe_start=maybe_start,
    )


class _FakeService:
    """只提供事件转盘真正用到的两个能力。"""

    def __init__(self, *, affection: int = 10, ambient_text: str | None = AMBIENT_COPY):
        self.affection = affection
        self.text_library = object()
        self.ambient_text = ambient_text

    async def profile(self, group_id: str, user_id: str) -> SimpleNamespace:
        return SimpleNamespace(affection=self.affection)

    async def ambient_reaction(self, group_id: str, user_id: str) -> str | None:
        return self.ambient_text


def _coordinator(
    *,
    service: _FakeService | None = None,
    config: Config | None = None,
    slot: str | None = None,
    events: SimpleNamespace | None = None,
    findchar: SimpleNamespace | None = None,
    bet: SimpleNamespace | None = None,
    pk: SimpleNamespace | None = None,
    wheel: SimpleNamespace | None = None,
    group_quiz: SimpleNamespace | None = None,
    whoami: SimpleNamespace | None = None,
) -> EventWheelCoordinator:
    return EventWheelCoordinator(
        service or _FakeService(),  # type: ignore[arg-type]
        config or Config(mantou_affection_newbie_protection_below=0),
        events=events or _events(),  # type: ignore[arg-type]
        findchar=findchar or _findchar(),  # type: ignore[arg-type]
        bet=bet or _bet(),  # type: ignore[arg-type]
        pk=pk or _quiz(),  # type: ignore[arg-type]
        wheel=wheel or _wheel(),  # type: ignore[arg-type]
        group_quiz=group_quiz,
        whoami=whoami,
        rng=FixedSlotRng(slot),
    )


async def _start(coordinator: EventWheelCoordinator) -> tuple[str | None, list]:
    sent: list = []

    async def send(message) -> None:
        sent.append(message)

    result = await coordinator.start(
        group_id=GROUP_ID, user_id=USER_ID, nickname="桃友", send=send
    )
    return result, sent


def test_event_wheel_slots_follow_force_activity_order() -> None:
    assert len(EVENT_WHEEL_SLOTS) == 13
    assert [f"{index}. {slot.label}" for index, slot in enumerate(EVENT_WHEEL_SLOTS, start=1)] == (
        list(FORCE_ACTIVITY_LINES)
    )
    assert set(EVENT_WHEEL_LABELS) == {slot.key for slot in EVENT_WHEEL_SLOTS}


async def test_available_slots_skip_affection_gated_activities() -> None:
    coordinator = _coordinator(service=_FakeService(affection=0), whoami=_quiz())

    slots = await coordinator.available_slots(GROUP_ID, USER_ID)

    assert EVENT_WHEEL_AMBIENT not in slots
    assert EVENT_WHEEL_EVENT not in slots
    assert EVENT_WHEEL_FINDCHAR not in slots
    assert EVENT_WHEEL_BET not in slots
    assert EVENT_WHEEL_WHEEL not in slots
    # 问答类不看好感度
    assert EVENT_WHEEL_PK in slots
    assert EVENT_WHEEL_WHOAMI in slots


async def test_available_slots_require_positive_affection_for_wheel() -> None:
    coordinator = _coordinator(service=_FakeService(affection=10), slot=EVENT_WHEEL_WHEEL)

    slots = await coordinator.available_slots(GROUP_ID, USER_ID)
    result, sent = await _start(coordinator)

    assert EVENT_WHEEL_WHEEL in slots
    assert result == EVENT_WHEEL_WHEEL
    assert len(sent) == 1
    assert "命运齿轮转盘" in str(sent[0])


async def test_available_slots_filter_bet_by_affection_threshold() -> None:
    low = _coordinator(service=_FakeService(affection=50))
    high = _coordinator(service=_FakeService(affection=51))

    assert EVENT_WHEEL_BET not in await low.available_slots(GROUP_ID, USER_ID)
    assert EVENT_WHEEL_BET in await high.available_slots(GROUP_ID, USER_ID)


async def test_available_slots_filter_bet_by_rivals() -> None:
    coordinator = _coordinator(service=_FakeService(affection=60), bet=_bet(candidates=1))

    assert EVENT_WHEEL_BET not in await coordinator.available_slots(GROUP_ID, USER_ID)


async def test_available_slots_hide_empty_libraries() -> None:
    coordinator = _coordinator(
        events=_events(ready=False),
        findchar=_findchar(ready=False),
        pk=_quiz(ready=False),
        whoami=_quiz(ready=False),
    )

    slots = await coordinator.available_slots(GROUP_ID, USER_ID)

    assert EVENT_WHEEL_EVENT not in slots
    assert EVENT_WHEEL_FINDCHAR not in slots
    assert EVENT_WHEEL_PK not in slots
    assert EVENT_WHEEL_WHOAMI not in slots
    assert EVENT_WHEEL_AMBIENT in slots


async def test_draw_slot_returns_none_without_candidates() -> None:
    coordinator = _coordinator()

    assert coordinator.draw_slot(()) is None


async def test_start_announces_then_starts_ambient_copy() -> None:
    coordinator = _coordinator(slot=EVENT_WHEEL_AMBIENT)

    result, sent = await _start(coordinator)

    assert result == EVENT_WHEEL_AMBIENT
    assert len(sent) == 2
    assert "事件转盘转到了「馒头小动作」" in str(sent[0])
    assert AMBIENT_COPY in str(sent[1])


async def test_start_returns_none_and_stays_silent_when_nothing_available() -> None:
    coordinator = _coordinator(
        service=_FakeService(ambient_text=None),
        config=Config(mantou_affection_ambient_enabled=False),
        events=_events(ready=False),
        findchar=_findchar(ready=False),
        pk=_quiz(ready=False),
        wheel=None,
        bet=_bet(candidates=0),
    )
    coordinator.wheel = None  # type: ignore[assignment]

    result, sent = await _start(coordinator)

    assert result is None
    assert sent == []


async def test_start_reports_when_announced_activity_cannot_start() -> None:
    coordinator = _coordinator(
        slot=EVENT_WHEEL_FINDCHAR,
        findchar=_findchar(started=False),
    )

    result, sent = await _start(coordinator)

    assert result is None
    assert len(sent) == 2
    assert "事件转盘转到了「找字」" in str(sent[0])
    assert "这次没开起来" in str(sent[1])


async def test_busy_group_stays_silent_and_skips_drawing() -> None:
    events = _events()
    events.busy = lambda group_id: True
    coordinator = _coordinator(slot=EVENT_WHEEL_AMBIENT, events=events)

    result, sent = await _start(coordinator)

    assert result is None
    assert sent == []


async def test_busy_group_detected_via_peer_pending() -> None:
    wheel = _wheel()
    wheel.pending = {GROUP_ID: object()}
    coordinator = _coordinator(slot=EVENT_WHEEL_AMBIENT, wheel=wheel)

    assert coordinator.busy(GROUP_ID) is True
    result, sent = await _start(coordinator)
    assert result is None
    assert sent == []


async def test_start_swallows_send_errors() -> None:
    coordinator = _coordinator(slot=EVENT_WHEEL_AMBIENT)

    async def broken_send(message) -> None:
        raise RuntimeError("send is down")

    result = await coordinator.start(
        group_id=GROUP_ID, user_id=USER_ID, nickname="桃友", send=broken_send
    )

    assert result is None


async def test_start_uses_quiz_coordinator_when_configured() -> None:
    coordinator = _coordinator(slot=EVENT_WHEEL_GROUP_QUIZ, group_quiz=_quiz())

    result, sent = await _start(coordinator)

    assert result == EVENT_WHEEL_GROUP_QUIZ
    assert len(sent) == 1
    assert "群友问答" in str(sent[0])


async def test_start_dispatches_event_and_bet() -> None:
    event_result, _ = await _start(_coordinator(slot=EVENT_WHEEL_EVENT))
    bet_result, _ = await _start(
        _coordinator(
            service=_FakeService(affection=60),
            slot=EVENT_WHEEL_BET,
            bet=_bet(candidates=3),
        )
    )

    assert event_result == EVENT_WHEEL_EVENT
    assert bet_result == EVENT_WHEEL_BET


async def test_start_falls_back_to_first_candidate_when_picked_slot_unavailable() -> None:
    """抽到不在候选池里的格子时会退回第一个候选，保证总能开到一格。"""

    coordinator = _coordinator(
        service=_FakeService(affection=0),
        slot=EVENT_WHEEL_FINDCHAR,
    )

    result, _sent = await _start(coordinator)

    assert result is not None
    assert result != EVENT_WHEEL_FINDCHAR
