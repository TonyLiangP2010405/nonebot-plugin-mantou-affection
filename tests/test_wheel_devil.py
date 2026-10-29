from __future__ import annotations

import asyncio
import random
from pathlib import Path

import pytest
from nonebot.adapters.onebot.v11 import Message

from nonebot_plugin_mantou_affection.config import Config
from nonebot_plugin_mantou_affection.service import AffectionService
from nonebot_plugin_mantou_affection.storage import AffectionStore
from nonebot_plugin_mantou_affection.wheel import (
    DEVIL_SPEC,
    DEVIL_WHEEL_OUTCOMES,
    NORMAL_SPEC,
    WheelCoordinator,
    draw_wheel_outcome,
    format_devil_wheel_rules,
)

GROUP = "94001"
TRIGGER = "94002"
OTHER = "94003"


def test_devil_weights_match_approved_table() -> None:
    weighted = [((o.kind, o.value), w) for o, w in DEVIL_WHEEL_OUTCOMES]
    assert weighted == [
        (("meme", 0), 10),
        (("taozi", 0), 25),
        (("corpus", 0), 10),
        (("affection", 4), 4),
        (("affection", 8), 2),
        (("affection", 12), 2),
        (("affection", 20), 1),
        (("affection", 40), 1),
        (("affection", -4), 13),
        (("affection", -8), 11),
        (("affection", -12), 8),
        (("affection", -20), 7),
        (("affection", -40), 4),
        (("affection", -60), 2),
    ]
    assert sum(w for _o, w in DEVIL_WHEEL_OUTCOMES) == 100


def test_devil_spec_differs_from_normal_only_where_designed() -> None:
    assert NORMAL_SPEC.auto_start is False
    assert NORMAL_SPEC.refund is True
    assert NORMAL_SPEC.max_participants == 4
    assert NORMAL_SPEC.join_cost == 2
    assert NORMAL_SPEC.taozi_cost == 10
    assert NORMAL_SPEC.meme_candidates == 1
    assert DEVIL_SPEC.auto_start is True
    assert DEVIL_SPEC.refund is False
    assert DEVIL_SPEC.max_participants is None
    assert DEVIL_SPEC.join_cost == 5
    assert DEVIL_SPEC.taozi_cost == 30
    assert DEVIL_SPEC.meme_cost == 5
    assert DEVIL_SPEC.corpus_cost == 5
    assert DEVIL_SPEC.meme_candidates == 5


def test_devil_rules_list_threshold_cost_and_every_probability() -> None:
    text = format_devil_wheel_rules(60, 200)
    for fragment in (
        "大于 200",
        "不限人数",
        "参与一次扣 5 点好感度",
        "60 秒后自动开转",
        "不退款",
        "表情 10%",
        "桃子表情包 25%",
        "30 点好感",
        "群友语料库 10%",
        "扣 5 点好感",
        "+4 4%",
        "+40 1%",
        "-4 13%",
        "-60 2%",
    ):
        assert fragment in text


def test_pick_mode_requires_more_than_threshold() -> None:
    class FixedRandom(random.Random):
        def random(self) -> float:
            return 0.0

    coordinator = WheelCoordinator(object(), Config(), rng=FixedRandom(0))
    assert coordinator.pick_mode(200) is False
    assert coordinator.pick_mode(201) is True


def test_pick_mode_respects_configured_chance() -> None:
    coordinator = WheelCoordinator(
        object(), Config(mantou_affection_wheel_devil_chance=0.0), rng=random.Random(0)
    )
    assert coordinator.pick_mode(999) is False


@pytest.mark.parametrize(("roll", "kind"), [(0, "meme"), (10, "taozi"), (35, "corpus")])
def test_devil_draw_ranges(roll, kind) -> None:
    class Fixed(random.Random):
        def randrange(self, stop: int) -> int:
            assert stop == 100
            return roll

    assert draw_wheel_outcome(Fixed(0), DEVIL_WHEEL_OUTCOMES).kind == kind


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
    def __init__(self) -> None:
        self.self_id = 999


class FakeStickers:
    def __init__(
        self,
        image: bytes = b"taozi:001",
        caption: str = "头部主播",
        error: Exception | None = None,
    ):
        self.image = image
        self.caption = caption
        self.error = error
        self.picks = 0
        self.rechecks = 0

    async def pick(self) -> tuple[bytes, str]:
        self.picks += 1
        if self.error is not None:
            raise self.error
        return self.image, self.caption

    def check_after_missing(self) -> bool:
        self.rechecks += 1
        return True


def make_coordinator(
    tmp_path: Path, rng=None, memes=None, resources=None, stickers=None
):
    config = Config()
    service = AffectionService(AffectionStore(tmp_path / "affection.json"), config)
    coordinator = WheelCoordinator(
        service,
        config,
        rng=rng or random.Random(0),
        memes=memes,
        resources=resources,
        stickers=stickers,
        poke_delay=0,
    )
    coordinator.choice_timeout = 30
    return coordinator


async def give(service: AffectionService, user_id: str, score: int) -> None:
    def bump(profile) -> None:
        profile.affection = score

    await service.store.update_profile(GROUP, user_id, "测试员", bump)


async def devil_start(coordinator, *, user_id: str = TRIGGER, nickname: str = "触发者"):
    sent = []

    async def send(message) -> None:
        sent.append(Message(message) if isinstance(message, str) else message)

    assert await coordinator.start(
        group_id=GROUP,
        user_id=user_id,
        nickname=nickname,
        bot=FakeBot(),
        send=send,
        devil=True,
    )
    return coordinator.pending[GROUP], sent


async def devil_auto(coordinator, *, user_id: str = TRIGGER):
    """开一轮恶魔转盘并等到点自动开转，返回 (pending, sent)。"""

    coordinator.choice_timeout = 0.01
    pending, sent = await devil_start(coordinator, user_id=user_id)
    await asyncio.wait_for(pending.task, 2)
    return pending, sent


async def test_devil_join_charges_five_and_has_no_cap(tmp_path: Path) -> None:
    coordinator = make_coordinator(tmp_path)
    pending, sent = await devil_start(coordinator)
    for index in range(10):
        user = f"950{index}"
        await give(coordinator.service, user, 50)
        await coordinator.answer(GROUP, user, f"群友{index}", "参与")
    assert len(pending.participants) == 10
    assert (await coordinator.service.profile(GROUP, "9500")).affection == 45
    assert all("名额已满" not in m.extract_plain_text() for m in sent)
    pending.task.cancel()


async def test_devil_join_rejects_below_five(tmp_path: Path) -> None:
    coordinator = make_coordinator(tmp_path)
    pending, sent = await devil_start(coordinator)
    await give(coordinator.service, OTHER, 4)
    await coordinator.answer(GROUP, OTHER, "群友", "参与")
    assert "好感度不足" in sent[-1].extract_plain_text()
    assert (await coordinator.service.profile(GROUP, OTHER)).affection == 4
    pending.task.cancel()


@pytest.mark.parametrize("keyword", ["运作", "不运作", "确认"])
async def test_devil_ignores_choice_keywords(tmp_path: Path, keyword: str) -> None:
    coordinator = make_coordinator(tmp_path)
    pending, sent = await devil_start(coordinator)
    await coordinator.answer(GROUP, TRIGGER, "触发者", keyword)
    assert pending.accepting is True
    assert pending.task is not None and not pending.task.done()
    assert "不需要" in sent[-1].extract_plain_text()
    pending.task.cancel()


async def test_devil_auto_runs_at_timeout_without_refund(tmp_path: Path) -> None:
    coordinator = make_coordinator(tmp_path, rng=SequenceRng([45, 45]))
    coordinator.choice_timeout = 0.01
    await give(coordinator.service, TRIGGER, 500)
    pending, _sent = await devil_start(coordinator)
    await give(coordinator.service, OTHER, 50)
    await coordinator.answer(GROUP, OTHER, "群友", "参与")
    assert (await coordinator.service.profile(GROUP, OTHER)).affection == 45
    await asyncio.wait_for(pending.task, 2)
    assert GROUP not in coordinator.pending
    # 参与者 50 - 5 参与费 + 4 抽中 = 49, 没有退款, 结果确实结算了
    assert (await coordinator.service.profile(GROUP, OTHER)).affection == 49
    assert (await coordinator.service.profile(GROUP, TRIGGER)).affection == 504


async def test_devil_taozi_asks_thirty_and_charges_thirty(tmp_path: Path) -> None:
    coordinator = make_coordinator(
        tmp_path, rng=FixedRollRng(roll=10), stickers=FakeStickers()
    )
    await give(coordinator.service, TRIGGER, 100)
    _pending, sent = await devil_auto(coordinator)
    prompt = sent[-1].extract_plain_text()
    assert "30 点好感" in prompt
    await coordinator.answer(GROUP, TRIGGER, "触发者", "要")
    assert (await coordinator.service.profile(GROUP, TRIGGER)).affection == 70
    assert "[CQ:image" in str(sent[-1])


async def test_devil_taozi_needs_thirty_to_ask(tmp_path: Path) -> None:
    coordinator = make_coordinator(
        tmp_path, rng=FixedRollRng(roll=10), stickers=FakeStickers()
    )
    await give(coordinator.service, TRIGGER, 29)
    _pending, sent = await devil_auto(coordinator)
    assert "好感不足" in sent[-1].extract_plain_text()
    assert (GROUP, TRIGGER) not in coordinator.sticker_confirms


@pytest.mark.parametrize(
    ("roll", "delta"),
    [
        (45, 4),
        (49, 8),
        (51, 12),
        (53, 20),
        (54, 40),
        (55, -4),
        (68, -8),
        (79, -12),
        (87, -20),
        (94, -40),
        (98, -60),
    ],
)
async def test_devil_affection_outcomes(tmp_path: Path, roll: int, delta: int) -> None:
    coordinator = make_coordinator(tmp_path, rng=FixedRollRng(roll=roll))
    await give(coordinator.service, TRIGGER, 500)
    await devil_auto(coordinator)
    assert (await coordinator.service.profile(GROUP, TRIGGER)).affection == 500 + delta
