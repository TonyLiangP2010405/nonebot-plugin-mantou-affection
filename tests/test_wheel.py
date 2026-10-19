import asyncio
import random
from pathlib import Path

import pytest
from nonebot.adapters.onebot.v11 import Message

from nonebot_plugin_mantou_affection.config import Config
from nonebot_plugin_mantou_affection.models import Profile
from nonebot_plugin_mantou_affection.service import AffectionService
from nonebot_plugin_mantou_affection.storage import AffectionStore
from nonebot_plugin_mantou_affection.wheel import (
    WHEEL_OUTCOMES,
    WheelCoordinator,
    draw_not_operate_delta,
    draw_wheel_outcome,
)
from nonebot_plugin_mantou_affection.wheel_memes import MemeAssetMissing

GROUP = "94001"
TRIGGER = "94002"
OTHER = "94003"


class FixedRollRng(random.Random):
    def __init__(self, roll: int, value: float = 0.0):
        super().__init__(0)
        self.roll = roll
        self.value = value

    def randrange(self, stop: int) -> int:
        assert stop == 100
        return self.roll

    def random(self) -> float:
        return self.value


class SequenceRng(FixedRollRng):
    def __init__(self, rolls: list[int], value: float = 0.0):
        super().__init__(0, value)
        self.rolls = iter(rolls)

    def randrange(self, stop: int) -> int:
        assert stop == 100
        return next(self.rolls)


class FakeBot:
    def __init__(self, fail_users: set[str] | None = None):
        self.self_id = 999
        self.fail_users = fail_users or set()
        self.calls = []

    async def call_api(self, name: str, **kwargs) -> None:
        assert name == "send_poke"
        self.calls.append(kwargs)
        if str(kwargs["user_id"]) in self.fail_users:
            raise RuntimeError("poke unsupported")


class FakeMemes:
    def __init__(self, missing_users: set[str] | None = None):
        self.missing_users = missing_users or set()

    async def generate(self, *, bot, user_id: str, nickname: str) -> bytes:
        if user_id in self.missing_users:
            raise MemeAssetMissing("petpet/base.png")
        return f"image:{user_id}".encode()


class FakeResources:
    def __init__(self):
        self.calls = 0

    def check_after_missing(self) -> bool:
        self.calls += 1
        return True


def make_coordinator(tmp_path: Path, rng=None, memes=None, resources=None):
    config = Config()
    service = AffectionService(AffectionStore(tmp_path / "affection.json"), config)
    coordinator = WheelCoordinator(
        service,
        config,
        rng=rng or random.Random(0),
        memes=memes,
        resources=resources,
        poke_delay=0,
    )
    coordinator.choice_timeout = 30
    return coordinator


async def give(service: AffectionService, user_id: str, score: int) -> None:
    def bump(profile: Profile) -> None:
        profile.affection = score

    await service.store.update_profile(GROUP, user_id, "测试员", bump)


async def start(coordinator: WheelCoordinator, bot=None):
    sent = []

    async def send(message) -> None:
        sent.append(Message(message) if isinstance(message, str) else message)

    bot = bot or FakeBot()
    assert await coordinator.start(
        group_id=GROUP, user_id=TRIGGER, nickname="触发者", bot=bot, send=send
    )
    return coordinator.pending[GROUP], sent, bot


async def wait_for_choice(pending) -> None:
    assert pending.task is not None
    await asyncio.wait_for(pending.task, 2)


def test_wheel_weights_match_approved_table() -> None:
    weighted = [((outcome.kind, outcome.value), weight) for outcome, weight in WHEEL_OUTCOMES]
    assert weighted == [
        (("meme", 0), 20),
        (("poke", 1), 12),
        (("poke", 3), 8),
        (("poke", 5), 5),
        (("poke", 10), 4),
        (("poke", 20), 1),
        (("affection", 1), 7),
        (("affection", 2), 6),
        (("affection", 3), 5),
        (("affection", 5), 4),
        (("affection", 10), 3),
        (("affection", 20), 1),
        (("affection", -1), 7),
        (("affection", -2), 6),
        (("affection", -3), 5),
        (("affection", -5), 3),
        (("affection", -10), 2),
        (("affection", -20), 1),
    ]
    assert sum(weight for _, weight in WHEEL_OUTCOMES) == 100


def test_wheel_draw_uses_independent_integer_ranges() -> None:
    assert draw_wheel_outcome(FixedRollRng(0)).kind == "meme"
    assert draw_wheel_outcome(FixedRollRng(19)).kind == "meme"
    assert draw_wheel_outcome(FixedRollRng(20)).value == 1
    assert draw_wheel_outcome(FixedRollRng(49)).value == 20
    assert draw_wheel_outcome(FixedRollRng(50)).value == 1
    assert draw_wheel_outcome(FixedRollRng(75)).value == 20
    assert draw_wheel_outcome(FixedRollRng(76)).value == -1
    assert draw_wheel_outcome(FixedRollRng(99)).value == -20


def test_not_operate_delta_is_one_third_positive() -> None:
    assert draw_not_operate_delta(FixedRollRng(0, 0.2)) == 2
    assert draw_not_operate_delta(FixedRollRng(0, 0.9)) == -2


async def test_wheel_answer_ignores_unrelated_text(tmp_path: Path) -> None:
    coordinator = make_coordinator(tmp_path)
    assert await coordinator.answer(GROUP, OTHER, "路人", "普通聊天") is False
    assert await coordinator.answer(GROUP, OTHER, "路人", "1") is False


async def test_wheel_join_rolls_back_partial_fee(tmp_path: Path) -> None:
    coordinator = make_coordinator(tmp_path)
    await give(coordinator.service, OTHER, 1)
    pending, sent, _ = await start(coordinator)

    assert await coordinator.answer(GROUP, OTHER, "路人", "参与") is True
    assert pending.participants == []
    assert (await coordinator.service.profile(GROUP, OTHER)).affection == 1
    assert "需要 2 点" in sent[-1].extract_plain_text()
    pending.task.cancel()


async def test_wheel_timeout_refunds_once(tmp_path: Path) -> None:
    coordinator = make_coordinator(tmp_path)
    coordinator.choice_timeout = 0.01
    await give(coordinator.service, OTHER, 10)
    pending, _, _ = await start(coordinator)
    await coordinator.answer(GROUP, OTHER, "路人", "参与")
    assert (await coordinator.service.profile(GROUP, OTHER)).affection == 8

    await asyncio.wait_for(pending.task, 2)
    assert (await coordinator.service.profile(GROUP, OTHER)).affection == 10
    assert GROUP not in coordinator.pending


async def test_wheel_not_operate_only_changes_trigger(tmp_path: Path) -> None:
    coordinator = make_coordinator(tmp_path, rng=FixedRollRng(0, 0.9))
    await give(coordinator.service, TRIGGER, 10)
    await give(coordinator.service, OTHER, 10)
    pending, _, _ = await start(coordinator)
    await coordinator.answer(GROUP, OTHER, "路人", "参与")

    assert await coordinator.answer(GROUP, TRIGGER, "触发者", "不运作") is True
    await wait_for_choice(pending)
    assert (await coordinator.service.profile(GROUP, TRIGGER)).affection == 8
    assert (await coordinator.service.profile(GROUP, OTHER)).affection == 10


async def test_wheel_choice_and_timeout_settle_once(tmp_path: Path) -> None:
    coordinator = make_coordinator(tmp_path, rng=SequenceRng([50, 50]))
    coordinator.choice_timeout = 0.01
    await give(coordinator.service, TRIGGER, 10)
    await give(coordinator.service, OTHER, 10)
    pending, _, _ = await start(coordinator)
    await coordinator.answer(GROUP, OTHER, "路人", "参与")
    timeout_task = pending.task

    await coordinator.answer(GROUP, TRIGGER, "触发者", "运作")
    await wait_for_choice(pending)
    await asyncio.sleep(0.02)
    assert (await coordinator.service.profile(GROUP, TRIGGER)).affection == 11
    assert (await coordinator.service.profile(GROUP, OTHER)).affection == 9
    assert timeout_task.cancelled() or timeout_task.done()


async def test_wheel_operation_draws_each_person_independently(tmp_path: Path) -> None:
    coordinator = make_coordinator(tmp_path, rng=SequenceRng([50, 75]))
    await give(coordinator.service, TRIGGER, 10)
    await give(coordinator.service, OTHER, 10)
    pending, sent, _ = await start(coordinator)
    await coordinator.answer(GROUP, OTHER, "路人", "参与")

    await coordinator.answer(GROUP, TRIGGER, "触发者", "运作")
    await wait_for_choice(pending)
    assert (await coordinator.service.profile(GROUP, TRIGGER)).affection == 11
    assert (await coordinator.service.profile(GROUP, OTHER)).affection == 28
    assert any("净变化 +18" in message.extract_plain_text() for message in sent)


async def test_wheel_poke_failure_refunds_without_calling_incoming_poke(
    tmp_path: Path, monkeypatch
) -> None:
    coordinator = make_coordinator(tmp_path, rng=SequenceRng([50, 20]))

    async def forbidden_poke(*args, **kwargs):
        raise AssertionError("不得调用入向 poke")

    monkeypatch.setattr(coordinator.service, "poke", forbidden_poke)
    await give(coordinator.service, TRIGGER, 10)
    await give(coordinator.service, OTHER, 10)
    bot = FakeBot(fail_users={OTHER})
    pending, _, _ = await start(coordinator, bot=bot)
    await coordinator.answer(GROUP, OTHER, "路人", "参与")
    await coordinator.answer(GROUP, TRIGGER, "触发者", "运作")
    await wait_for_choice(pending)

    assert bot.calls == [{"group_id": int(GROUP), "user_id": int(OTHER)}]
    assert (await coordinator.service.profile(GROUP, OTHER)).affection == 10


async def test_wheel_meme_missing_refunds_and_requests_throttled_recheck(tmp_path: Path) -> None:
    memes = FakeMemes(missing_users={OTHER})
    resources = FakeResources()
    coordinator = make_coordinator(
        tmp_path, rng=SequenceRng([0, 0]), memes=memes, resources=resources
    )
    await give(coordinator.service, OTHER, 10)
    pending, sent, _ = await start(coordinator)
    await coordinator.answer(GROUP, OTHER, "路人", "参与")
    await coordinator.answer(GROUP, TRIGGER, "触发者", "运作")
    await wait_for_choice(pending)

    assert resources.calls == 1
    assert (await coordinator.service.profile(GROUP, OTHER)).affection == 10
    assert any(segment.type == "image" for message in sent for segment in message)


async def test_wheel_join_cap_duplicate_and_other_cannot_choose(tmp_path: Path) -> None:
    coordinator = make_coordinator(tmp_path)
    user_ids = [str(94010 + index) for index in range(6)]
    for user_id in user_ids:
        await give(coordinator.service, user_id, 10)
    pending, _, _ = await start(coordinator)

    for user_id in user_ids[:5]:
        await coordinator.answer(GROUP, user_id, "路人", "参与")
    await coordinator.answer(GROUP, user_ids[0], "路人", "参与")
    await coordinator.answer(GROUP, user_ids[5], "路人", "参与")

    assert len(pending.participants) == 5
    assert (await coordinator.service.profile(GROUP, user_ids[0])).affection == 8
    assert (await coordinator.service.profile(GROUP, user_ids[5])).affection == 10
    assert await coordinator.answer(GROUP, user_ids[0], "路人", "运作") is True
    assert pending.accepting is True
    pending.task.cancel()


@pytest.mark.parametrize(
    ("roll", "delta"),
    [
        (50, 1),
        (57, 2),
        (63, 3),
        (68, 5),
        (72, 10),
        (75, 20),
        (76, -1),
        (83, -2),
        (89, -3),
        (94, -5),
        (97, -10),
        (99, -20),
    ],
)
async def test_wheel_affection_outcomes(tmp_path: Path, roll: int, delta: int) -> None:
    coordinator = make_coordinator(tmp_path, rng=SequenceRng([roll]))
    await give(coordinator.service, TRIGGER, 100)
    pending, _, _ = await start(coordinator)
    await coordinator.answer(GROUP, TRIGGER, "触发者", "运作")
    await wait_for_choice(pending)
    assert (await coordinator.service.profile(GROUP, TRIGGER)).affection == 100 + delta


@pytest.mark.parametrize(("roll", "count"), [(20, 1), (32, 3), (40, 5), (45, 10), (49, 20)])
async def test_wheel_poke_counts(tmp_path: Path, roll: int, count: int) -> None:
    coordinator = make_coordinator(tmp_path, rng=SequenceRng([roll]))
    pending, _, bot = await start(coordinator)
    await coordinator.answer(GROUP, TRIGGER, "触发者", "运作")
    await wait_for_choice(pending)
    assert len(bot.calls) == count
    assert all(call["user_id"] == int(TRIGGER) for call in bot.calls)


async def test_wheel_uses_actual_applied_delta_at_bounds(tmp_path: Path) -> None:
    coordinator = make_coordinator(tmp_path, rng=SequenceRng([75, 99]))
    await give(coordinator.service, TRIGGER, 999)
    await give(coordinator.service, OTHER, 2)
    pending, sent, _ = await start(coordinator)
    await coordinator.answer(GROUP, OTHER, "路人", "参与")
    await coordinator.answer(GROUP, TRIGGER, "触发者", "运作")
    await wait_for_choice(pending)

    assert (await coordinator.service.profile(GROUP, TRIGGER)).affection == 999
    assert (await coordinator.service.profile(GROUP, OTHER)).affection == 0
    assert any("净变化 -2，当前 0" in message.extract_plain_text() for message in sent)


async def test_wheel_meme_unavailable_refunds_without_redraw(tmp_path: Path) -> None:
    coordinator = make_coordinator(tmp_path, rng=SequenceRng([50, 0]), memes=None)
    await give(coordinator.service, OTHER, 10)
    pending, _, _ = await start(coordinator)
    await coordinator.answer(GROUP, OTHER, "路人", "参与")
    await coordinator.answer(GROUP, TRIGGER, "触发者", "运作")
    await wait_for_choice(pending)

    assert (await coordinator.service.profile(GROUP, OTHER)).affection == 10


async def test_wheel_choice_during_opening_send_keeps_settlement_task(tmp_path: Path) -> None:
    coordinator = make_coordinator(tmp_path, rng=SequenceRng([50]))
    await give(coordinator.service, TRIGGER, 10)
    opening_started = asyncio.Event()
    release_opening = asyncio.Event()

    async def send(message) -> None:
        if not opening_started.is_set():
            opening_started.set()
            await release_opening.wait()

    start_task = asyncio.create_task(
        coordinator.start(
            group_id=GROUP,
            user_id=TRIGGER,
            nickname="触发者",
            bot=FakeBot(),
            send=send,
        )
    )
    await opening_started.wait()
    pending = coordinator.pending[GROUP]
    await coordinator.answer(GROUP, TRIGGER, "触发者", "运作")
    settlement_task = pending.task
    release_opening.set()

    try:
        assert await start_task
        assert pending.task is settlement_task
        await wait_for_choice(pending)
        assert (await coordinator.service.profile(GROUP, TRIGGER)).affection == 11
    finally:
        for task in {pending.task, settlement_task}:
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
