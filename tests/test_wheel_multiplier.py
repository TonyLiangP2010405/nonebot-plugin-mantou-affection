from __future__ import annotations

import asyncio
import random
from contextlib import suppress
from pathlib import Path

import pytest
from nonebot.adapters.onebot.v11 import Message

from nonebot_plugin_mantou_affection.config import Config
from nonebot_plugin_mantou_affection.models import Profile
from nonebot_plugin_mantou_affection.service import AffectionService
from nonebot_plugin_mantou_affection.storage import AffectionStore
from nonebot_plugin_mantou_affection.wheel import (
    MULTIPLIER_OUTCOMES,
    WheelCoordinator,
    draw_multiplier,
    format_wheel_rules,
)

GROUP = "94201"
TRIGGER = "94202"
OTHER = "94203"


class SequenceRng(random.Random):
    def __new__(cls, *args, **kwargs):
        # Python 3.9/3.10 的 _random.Random.__new__ 会尝试哈希构造参数, 子类必须显式接管。
        return super().__new__(cls)

    def __init__(self, rolls: list[int]):
        super().__init__(0)
        self.rolls = iter(rolls)

    def randrange(self, stop: int) -> int:
        assert stop == 100
        return next(self.rolls)


class FixedRollRng(SequenceRng):
    def __init__(self, roll: int):
        super().__init__([roll])


class FakeBot:
    self_id = 999

    async def call_api(self, name: str, **kwargs) -> None:
        raise AssertionError("倍率转盘不应该调用 poke")


def _coordinator(
    tmp_path: Path,
    *,
    wheel_rolls: list[int],
    multiplier_rolls: list[int] | None = None,
    chance: float = 1.0,
    timeout: float = 0.0,
    newbie_below: int = 0,
) -> WheelCoordinator:
    config = Config(
        mantou_affection_newbie_protection_below=newbie_below,
        mantou_affection_multiplier_chance=chance,
    )
    service = AffectionService(AffectionStore(tmp_path / "affection.json"), config)
    coordinator = WheelCoordinator(
        service,
        config,
        rng=SequenceRng(wheel_rolls),
        multiplier_rng=SequenceRng(multiplier_rolls or [0, 0]),
        poke_delay=0,
    )
    coordinator.choice_timeout = 0
    coordinator.multiplier_timeout = timeout
    return coordinator


def _collector():
    sent: list[Message] = []

    async def send(message) -> None:
        sent.append(Message(message) if isinstance(message, str) else message)

    return sent, send


async def _give(service: AffectionService, user_id: str, score: int) -> None:
    def bump(profile: Profile) -> None:
        profile.affection = score

    await service.store.update_profile(GROUP, user_id, "测试员", bump)


async def _run_group_wheel(coordinator: WheelCoordinator, *, joins: int = 0):
    sent, send = _collector()
    assert await coordinator.start(
        group_id=GROUP, user_id=TRIGGER, nickname="触发者", bot=FakeBot(), send=send
    )
    pending = coordinator.pending[GROUP]
    if pending.task is not None and not pending.task.done():
        pending.task.cancel()
        with suppress(asyncio.CancelledError):
            await pending.task
    for index in range(joins):
        user_id = str(int(OTHER) + index)
        await _give(coordinator.service, user_id, 10)
        await coordinator.answer(GROUP, user_id, "路人", "参与")
    await coordinator._auto_start(pending)
    return sent


def _text(sent: list[Message]) -> str:
    return "\n".join(message.extract_plain_text() for message in sent)


def test_multiplier_table_sums_to_one_hundred() -> None:
    assert sum(weight for _, weight in MULTIPLIER_OUTCOMES) == 100
    assert [multiplier for multiplier, _ in MULTIPLIER_OUTCOMES] == [1, 2, 3, 4, 5]


@pytest.mark.parametrize(
    ("roll", "expected"),
    [(0, 1), (29, 1), (30, 2), (59, 2), (60, 3), (81, 3), (82, 4), (93, 4), (94, 5), (99, 5)],
)
def test_draw_multiplier_boundaries(roll: int, expected: int) -> None:
    assert draw_multiplier(FixedRollRng(roll)) == expected


def test_rules_mention_multiplier_only_when_enabled() -> None:
    assert "倍率转盘" not in format_wheel_rules(60)
    rules = format_wheel_rules(60, multiplier_chance=0.2, multiplier_timeout=30)
    assert "20% 概率触发倍率转盘" in rules
    assert "30 秒后开转" in rules


async def test_multiplier_disabled_settles_immediately(tmp_path: Path) -> None:
    coordinator = _coordinator(
        tmp_path, wheel_rolls=[50], multiplier_rolls=[0, 0], chance=0.0
    )
    await _give(coordinator.service, TRIGGER, 10)

    sent = await _run_group_wheel(coordinator)

    assert "倍率转盘" not in _text(sent)
    assert (await coordinator.service.profile(GROUP, TRIGGER)).affection == 11
    assert not coordinator.settlements


async def test_multiplier_x3_delays_final_settlement(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path, wheel_rolls=[50], multiplier_rolls=[0, 74])
    await _give(coordinator.service, TRIGGER, 10)

    sent = await _run_group_wheel(coordinator)

    assert "触发倍率转盘" in _text(sent)
    assert "0 秒后开转，按倍率做最终结算" in _text(sent)
    await coordinator.wait_pending_settlements()
    body = _text(sent)
    assert "倍率转盘 ×3：好感 +1 → +3" in body
    assert (await coordinator.service.profile(GROUP, TRIGGER)).affection == 13


async def test_multiplier_x1_still_reports_result(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path, wheel_rolls=[50], multiplier_rolls=[0, 0])
    await _give(coordinator.service, TRIGGER, 10)

    sent = await _run_group_wheel(coordinator)
    await coordinator.wait_pending_settlements()

    assert "倍率转盘 ×1：好感 +1 → +1" in _text(sent)
    assert (await coordinator.service.profile(GROUP, TRIGGER)).affection == 11


async def test_multiplier_announces_before_delay(tmp_path: Path) -> None:
    coordinator = _coordinator(
        tmp_path, wheel_rolls=[50], multiplier_rolls=[0, 74], timeout=30.0
    )
    await _give(coordinator.service, TRIGGER, 10)

    sent = await _run_group_wheel(coordinator)

    assert "30 秒后开转" in _text(sent)
    assert (await coordinator.service.profile(GROUP, TRIGGER)).affection == 10
    for task in list(coordinator.settlements):
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
    await asyncio.sleep(0)
    assert not coordinator.settlements


async def test_multiplier_does_not_block_other_participants(tmp_path: Path) -> None:
    # chance 0.2 下 roll 0 命中、roll 50 未命中, 倍率档位留给 30 秒后那次抽取
    coordinator = _coordinator(
        tmp_path,
        wheel_rolls=[50, 50],
        multiplier_rolls=[0, 50, 74],
        chance=0.2,
        timeout=30.0,
    )
    await _give(coordinator.service, TRIGGER, 10)

    sent = await _run_group_wheel(coordinator, joins=1)

    assert (await coordinator.service.profile(GROUP, OTHER)).affection == 9
    assert (await coordinator.service.profile(GROUP, TRIGGER)).affection == 10
    assert "转盘好感 +1，当前 9" in _text(sent)
    for task in list(coordinator.settlements):
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


async def test_multiplier_applies_newbie_protection_to_final_delta(
    tmp_path: Path,
) -> None:
    coordinator = _coordinator(
        tmp_path,
        wheel_rolls=[92],
        multiplier_rolls=[0, 94],
        newbie_below=50,
    )
    await _give(coordinator.service, TRIGGER, 40)

    sent = await _run_group_wheel(coordinator)
    await coordinator.wait_pending_settlements()

    body = _text(sent)
    assert "倍率转盘 ×5：好感 -10 → -16" in body
    assert (await coordinator.service.profile(GROUP, TRIGGER)).affection == 24


async def test_multiplier_positive_delta_clamps_at_max(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path, wheel_rolls=[73], multiplier_rolls=[0, 74])
    await _give(coordinator.service, TRIGGER, 990)

    sent = await _run_group_wheel(coordinator)
    await coordinator.wait_pending_settlements()

    assert "倍率转盘 ×3：好感 +50 → +9" in _text(sent)
    assert (await coordinator.service.profile(GROUP, TRIGGER)).affection == 999
