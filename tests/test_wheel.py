from __future__ import annotations

import asyncio
import random
from contextlib import suppress
from pathlib import Path
from types import SimpleNamespace

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
    format_meme_confirm,
    format_wheel_rules,
)
from nonebot_plugin_mantou_affection.wheel_memes import MemeAssetMissing, MemeGenerationError
from nonebot_plugin_mantou_affection.wheel_stickers import StickerAssetMissing, StickerError

GROUP = "94001"
TRIGGER = "94002"
OTHER = "94003"


class FixedRollRng(random.Random):
    def __new__(cls, *args, **kwargs):
        return super().__new__(cls)

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


class FakeMemeTemplate:
    def __init__(
        self,
        key: str = "petpet",
        keywords: tuple[str, ...] = ("摸摸头",),
        tags: tuple[str, ...] = ("可爱", "宠物"),
    ):
        self.key = key
        self.keywords = list(keywords)
        self.tags = list(tags)


class FakeMemes:
    def __init__(
        self,
        missing_users: set[str] | None = None,
        draw_error: Exception | None = None,
        generate_error: Exception | None = None,
    ):
        self.missing_users = missing_users or set()
        self.template = FakeMemeTemplate()
        self.draw_error = draw_error
        self.generate_error = generate_error
        self.draw_calls = 0
        self.generate_calls = 0
        self.generated: list[tuple[object, str, str]] = []
        self.drawn_groups: list[str] = []
        self.generated_groups: list[str] = []

    def draw(self) -> tuple[FakeMemeTemplate, str]:
        self.draw_calls += 1
        if self.draw_error is not None:
            raise self.draw_error
        return self.template, "single"

    async def draw_for_group(self, *, group_id: str, bot, user_id: str):
        self.drawn_groups.append(group_id)
        return self.draw()

    async def generate(self, *, bot, user_id: str, nickname: str, group_id=None) -> bytes:
        self.generated_groups.append(group_id)
        self.generate_calls += 1
        if self.generate_error is not None:
            raise self.generate_error
        if user_id in self.missing_users:
            raise MemeAssetMissing("petpet/base.png")
        return f"image:{user_id}".encode()

    async def generate_with(
        self, meme, kind: str, *, bot, user_id: str, nickname: str, group_id=None
    ) -> bytes:
        self.generated.append((meme, kind, user_id))
        self.generated_groups.append(group_id)
        if self.generate_error is not None:
            raise self.generate_error
        if user_id in self.missing_users:
            raise MemeAssetMissing("petpet/base.png")
        return f"image:{user_id}".encode()


class FakeResources:
    def __init__(self):
        self.calls = 0

    def check_after_missing(self) -> bool:
        self.calls += 1
        return True


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


def make_coordinator(tmp_path: Path, rng=None, memes=None, resources=None, stickers=None):
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
    def bump(profile: Profile) -> None:
        profile.affection = score

    await service.store.update_profile(GROUP, user_id, "测试员", bump)


async def start(
    coordinator: WheelCoordinator,
    bot=None,
    *,
    user_id: str = TRIGGER,
    nickname: str = "触发者",
):
    sent = []

    async def send(message) -> None:
        sent.append(Message(message) if isinstance(message, str) else message)

    bot = bot or FakeBot()
    assert await coordinator.start(
        group_id=GROUP, user_id=user_id, nickname=nickname, bot=bot, send=send
    )
    return coordinator.pending[GROUP], sent, bot


async def wait_for_choice(pending) -> None:
    assert pending.task is not None
    await asyncio.wait_for(pending.task, 2)


async def operate(coordinator: WheelCoordinator, pending, *, user_id: str = TRIGGER) -> None:
    await coordinator.answer(GROUP, user_id, "触发者", "运作")
    await coordinator.answer(GROUP, user_id, "触发者", "确认")
    await wait_for_choice(pending)


async def cancel_meme_confirm(coordinator: WheelCoordinator, user_id: str = TRIGGER) -> None:
    confirm = coordinator.meme_confirms.get((GROUP, user_id))
    if confirm is None or confirm.task is None:
        return
    confirm.task.cancel()
    with suppress(asyncio.CancelledError):
        await confirm.task


def test_wheel_weights_match_approved_table() -> None:
    weighted = [((outcome.kind, outcome.value), weight) for outcome, weight in WHEEL_OUTCOMES]
    assert weighted == [
        (("meme", 0), 19),
        (("taozi", 0), 1),
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
    assert draw_wheel_outcome(FixedRollRng(18)).kind == "meme"
    assert draw_wheel_outcome(FixedRollRng(19)).kind == "taozi"
    assert draw_wheel_outcome(FixedRollRng(20)).value == 1
    assert draw_wheel_outcome(FixedRollRng(49)).value == 20
    assert draw_wheel_outcome(FixedRollRng(50)).value == 1
    assert draw_wheel_outcome(FixedRollRng(75)).value == 20
    assert draw_wheel_outcome(FixedRollRng(76)).value == -1
    assert draw_wheel_outcome(FixedRollRng(99)).value == -20


def test_not_operate_delta_is_one_third_positive() -> None:
    assert draw_not_operate_delta(FixedRollRng(0, 0.2)) == 2
    assert draw_not_operate_delta(FixedRollRng(0, 0.9)) == -2


def test_rules_message_lists_every_probability() -> None:
    rules = format_wheel_rules(60)
    assert "参与需要扣除 2 点好感度" in rules
    assert "参与费会全额退回" in rules
    assert "含触发者最多 5 人" in rules
    assert "60 秒" in rules
    assert "不运作：1/3 概率好感 +2，2/3 概率好感 -2" in rules
    assert "回复“确认”才会开转" in rules
    assert "确认后立即截止" in rules
    assert "馒头制作随机表情 19%" in rules
    assert "桃子表情包 1%（换一张需 10 点好感）" in rules
    assert "×1 12%" in rules and "×20 1%" in rules
    assert "+1 7%" in rules and "+20 1%" in rules
    assert "-1 7%" in rules and "-20 1%" in rules


async def test_start_message_shows_probability_table(tmp_path: Path) -> None:
    coordinator = make_coordinator(tmp_path)
    _, sent, _ = await start(coordinator)
    body = sent[0].extract_plain_text()
    assert "转盘各项概率" in body
    assert "馒头制作随机表情 19%" in body
    assert "参与需要扣除 2 点好感度" in body
    coordinator.pending[GROUP].task.cancel()


async def test_wheel_answer_ignores_unrelated_text(tmp_path: Path) -> None:
    coordinator = make_coordinator(tmp_path)
    assert await coordinator.answer(GROUP, OTHER, "路人", "普通聊天") is False
    assert await coordinator.answer(GROUP, OTHER, "路人", "1") is False


async def test_wheel_join_charges_two_points(tmp_path: Path) -> None:
    coordinator = make_coordinator(tmp_path)
    await give(coordinator.service, OTHER, 10)
    pending, sent, _ = await start(coordinator)

    assert await coordinator.answer(GROUP, OTHER, "路人", "参与") is True
    assert len(pending.participants) == 1
    assert (await coordinator.service.profile(GROUP, OTHER)).affection == 8
    body = sent[-1].extract_plain_text()
    assert "参与成功" in body
    assert "已扣除 2 点好感度，当前 8" in body
    pending.task.cancel()


async def test_wheel_join_requires_enough_affection(tmp_path: Path) -> None:
    coordinator = make_coordinator(tmp_path)
    await give(coordinator.service, OTHER, 1)
    pending, sent, _ = await start(coordinator)

    assert await coordinator.answer(GROUP, OTHER, "路人", "参与") is True
    assert pending.participants == []
    assert (await coordinator.service.profile(GROUP, OTHER)).affection == 1
    assert "好感度不足 2 点" in sent[-1].extract_plain_text()
    pending.task.cancel()


async def test_wheel_join_cap_duplicate_and_trigger_notices(tmp_path: Path) -> None:
    coordinator = make_coordinator(tmp_path)
    user_ids = [str(94010 + index) for index in range(6)]
    pending, sent, _ = await start(coordinator)

    for user_id in user_ids[:4]:
        await give(coordinator.service, user_id, 10)
        await coordinator.answer(GROUP, user_id, "路人", "参与")
    await coordinator.answer(GROUP, user_ids[0], "路人", "参与")
    await coordinator.answer(GROUP, user_ids[4], "路人", "参与")
    await coordinator.answer(GROUP, TRIGGER, "触发者", "参与")

    assert len(pending.participants) == 4
    scores = [
        (await coordinator.service.profile(GROUP, user_id)).affection
        for user_id in user_ids[:4]
    ]
    assert scores == [8, 8, 8, 8]
    texts = [message.extract_plain_text() for message in sent]
    assert any("你已经参与本轮转盘了" in text for text in texts)
    assert any("名额已满（含触发者最多 5 人）" in text for text in texts)
    assert any("触发者已自动加入" in text for text in texts)
    assert await coordinator.answer(GROUP, user_ids[0], "路人", "运作") is True
    assert pending.accepting is True
    pending.task.cancel()


async def test_wheel_operate_asks_for_second_confirmation(tmp_path: Path) -> None:
    coordinator = make_coordinator(tmp_path, rng=SequenceRng([50, 50]))
    await give(coordinator.service, TRIGGER, 10)
    await give(coordinator.service, OTHER, 10)
    pending, sent, _ = await start(coordinator)

    assert await coordinator.answer(GROUP, TRIGGER, "触发者", "运作") is True
    assert pending.awaiting_confirm is True
    assert pending.accepting is True
    body = sent[-1].extract_plain_text()
    assert "其他群友就无法再参与" in body
    assert "倒计时" in body
    assert "要确认吗" in body

    await coordinator.answer(GROUP, OTHER, "路人", "参与")
    assert len(pending.participants) == 1

    await coordinator.answer(GROUP, TRIGGER, "触发者", "确认")
    await wait_for_choice(pending)
    assert (await coordinator.service.profile(GROUP, TRIGGER)).affection == 11
    assert (await coordinator.service.profile(GROUP, OTHER)).affection == 9


async def test_wheel_confirm_closes_joins(tmp_path: Path) -> None:
    coordinator = make_coordinator(tmp_path, rng=SequenceRng([50, 50]))
    await give(coordinator.service, OTHER, 10)
    pending, _, _ = await start(coordinator)
    await coordinator.answer(GROUP, TRIGGER, "触发者", "运作")
    await coordinator.answer(GROUP, TRIGGER, "触发者", "确认")

    await coordinator.answer(GROUP, OTHER, "路人", "参与")
    assert pending.participants == []
    assert (await coordinator.service.profile(GROUP, OTHER)).affection == 10
    await wait_for_choice(pending)


async def test_wheel_timeout_while_awaiting_confirm_cancels(tmp_path: Path) -> None:
    coordinator = make_coordinator(tmp_path)
    coordinator.choice_timeout = 0.01
    await give(coordinator.service, TRIGGER, 10)
    pending, sent, _ = await start(coordinator)
    await coordinator.answer(GROUP, TRIGGER, "触发者", "运作")
    assert pending.awaiting_confirm is True

    await asyncio.wait_for(pending.task, 2)
    assert (await coordinator.service.profile(GROUP, TRIGGER)).affection == 10
    assert GROUP not in coordinator.pending
    assert any("超时未确认" in message.extract_plain_text() for message in sent)


async def test_wheel_not_operate_after_operate_prompt(tmp_path: Path) -> None:
    coordinator = make_coordinator(tmp_path, rng=FixedRollRng(0, 0.2))
    await give(coordinator.service, TRIGGER, 10)
    pending, _, _ = await start(coordinator)
    await coordinator.answer(GROUP, TRIGGER, "触发者", "运作")
    assert pending.awaiting_confirm is True

    await coordinator.answer(GROUP, TRIGGER, "触发者", "不运作")
    await wait_for_choice(pending)
    assert (await coordinator.service.profile(GROUP, TRIGGER)).affection == 12


async def test_wheel_confirm_without_operate_and_other_confirm_prompts(
    tmp_path: Path,
) -> None:
    coordinator = make_coordinator(tmp_path)
    pending, sent, _ = await start(coordinator)

    assert await coordinator.answer(GROUP, TRIGGER, "触发者", "确认") is True
    assert "请先发送“运作”或“不运作”" in sent[-1].extract_plain_text()

    await coordinator.answer(GROUP, TRIGGER, "触发者", "运作")
    assert await coordinator.answer(GROUP, OTHER, "路人", "确认") is True
    assert "只有被 @ 的触发者" in sent[-1].extract_plain_text()
    assert pending.awaiting_confirm is True
    assert pending.accepting is True
    pending.task.cancel()


async def test_wheel_timeout_cancels_without_affection_change(tmp_path: Path) -> None:
    coordinator = make_coordinator(tmp_path)
    coordinator.choice_timeout = 0.01
    await give(coordinator.service, OTHER, 10)
    pending, sent, _ = await start(coordinator)
    await coordinator.answer(GROUP, OTHER, "路人", "参与")

    await asyncio.wait_for(pending.task, 2)
    assert (await coordinator.service.profile(GROUP, OTHER)).affection == 10
    assert GROUP not in coordinator.pending
    texts = [message.extract_plain_text() for message in sent]
    assert any("超时取消" in text for text in texts)
    assert any("参与费 +2 已退回，当前 10" in text for text in texts)


async def test_wheel_not_operate_only_changes_trigger(tmp_path: Path) -> None:
    coordinator = make_coordinator(tmp_path, rng=FixedRollRng(0, 0.9))
    await give(coordinator.service, TRIGGER, 10)
    await give(coordinator.service, OTHER, 10)
    pending, sent, _ = await start(coordinator)
    await coordinator.answer(GROUP, OTHER, "路人", "参与")

    assert await coordinator.answer(GROUP, TRIGGER, "触发者", "不运作") is True
    await wait_for_choice(pending)
    assert (await coordinator.service.profile(GROUP, TRIGGER)).affection == 8
    assert (await coordinator.service.profile(GROUP, OTHER)).affection == 10
    assert any(
        "参与费 +2 已退回，当前 10" in message.extract_plain_text() for message in sent
    )


async def test_wheel_choice_and_timeout_settle_once(tmp_path: Path) -> None:
    coordinator = make_coordinator(tmp_path, rng=SequenceRng([50, 50]))
    coordinator.choice_timeout = 0.01
    await give(coordinator.service, TRIGGER, 10)
    await give(coordinator.service, OTHER, 10)
    pending, _, _ = await start(coordinator)
    await coordinator.answer(GROUP, OTHER, "路人", "参与")
    timeout_task = pending.task

    await coordinator.answer(GROUP, TRIGGER, "触发者", "运作")
    await coordinator.answer(GROUP, TRIGGER, "触发者", "确认")
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
    await coordinator.answer(GROUP, TRIGGER, "触发者", "确认")
    await wait_for_choice(pending)
    assert (await coordinator.service.profile(GROUP, TRIGGER)).affection == 11
    assert (await coordinator.service.profile(GROUP, OTHER)).affection == 28
    assert any("转盘好感 +20，当前 28" in message.extract_plain_text() for message in sent)


async def test_wheel_poke_failure_reports_without_calling_incoming_poke(
    tmp_path: Path, monkeypatch
) -> None:
    coordinator = make_coordinator(tmp_path, rng=SequenceRng([50, 20]))

    async def forbidden_poke(*args, **kwargs):
        raise AssertionError("不得调用入向 poke")

    monkeypatch.setattr(coordinator.service, "poke", forbidden_poke)
    await give(coordinator.service, TRIGGER, 10)
    await give(coordinator.service, OTHER, 10)
    bot = FakeBot(fail_users={OTHER})
    pending, sent, _ = await start(coordinator, bot=bot)
    await coordinator.answer(GROUP, OTHER, "路人", "参与")
    await coordinator.answer(GROUP, TRIGGER, "触发者", "运作")
    await coordinator.answer(GROUP, TRIGGER, "触发者", "确认")
    await wait_for_choice(pending)

    assert bot.calls == [{"group_id": int(GROUP), "user_id": int(OTHER)}]
    assert (await coordinator.service.profile(GROUP, OTHER)).affection == 8
    failure = "\n".join(message.extract_plain_text() for message in sent)
    assert "馒头主动戳你失败" in failure
    assert "不用你回戳" in failure


async def test_wheel_meme_missing_requests_throttled_recheck(tmp_path: Path) -> None:
    memes = FakeMemes(missing_users={OTHER})
    resources = FakeResources()
    coordinator = make_coordinator(
        tmp_path, rng=SequenceRng([0, 0]), memes=memes, resources=resources
    )
    await coordinator.service.toggle_meme_confirm_whitelist(TRIGGER)
    await coordinator.service.toggle_meme_confirm_whitelist(OTHER)
    await give(coordinator.service, OTHER, 10)
    pending, sent, _ = await start(coordinator)
    await coordinator.answer(GROUP, OTHER, "路人", "参与")
    await coordinator.answer(GROUP, TRIGGER, "触发者", "运作")
    await coordinator.answer(GROUP, TRIGGER, "触发者", "确认")
    await wait_for_choice(pending)

    assert resources.calls == 1
    assert (await coordinator.service.profile(GROUP, OTHER)).affection == 8
    assert any(segment.type == "image" for message in sent for segment in message)


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
    await coordinator.answer(GROUP, TRIGGER, "触发者", "确认")
    await wait_for_choice(pending)
    assert (await coordinator.service.profile(GROUP, TRIGGER)).affection == 100 + delta


@pytest.mark.parametrize(("roll", "count"), [(20, 1), (32, 3), (40, 5), (45, 10), (49, 20)])
async def test_wheel_poke_counts(tmp_path: Path, roll: int, count: int) -> None:
    coordinator = make_coordinator(tmp_path, rng=SequenceRng([roll]))
    pending, _, bot = await start(coordinator)
    await coordinator.answer(GROUP, TRIGGER, "触发者", "运作")
    await coordinator.answer(GROUP, TRIGGER, "触发者", "确认")
    await wait_for_choice(pending)
    assert len(bot.calls) == count
    assert all(call["user_id"] == int(TRIGGER) for call in bot.calls)


async def test_wheel_poke_message_clearly_says_mantou_pokes_you(tmp_path: Path) -> None:
    coordinator = make_coordinator(tmp_path, rng=SequenceRng([20]))
    pending, sent, _ = await start(coordinator)
    await coordinator.answer(GROUP, TRIGGER, "触发者", "运作")
    await coordinator.answer(GROUP, TRIGGER, "触发者", "确认")
    await wait_for_choice(pending)

    body = sent[-1].extract_plain_text()
    assert "馒头主动戳了你 1 次" in body
    assert "是馒头戳你，不用你回戳" in body


async def test_wheel_uses_actual_applied_delta_at_bounds(tmp_path: Path) -> None:
    coordinator = make_coordinator(tmp_path, rng=SequenceRng([75, 99]))
    await give(coordinator.service, TRIGGER, 999)
    await give(coordinator.service, OTHER, 4)
    pending, sent, _ = await start(coordinator)
    await coordinator.answer(GROUP, OTHER, "路人", "参与")
    await coordinator.answer(GROUP, TRIGGER, "触发者", "运作")
    await coordinator.answer(GROUP, TRIGGER, "触发者", "确认")
    await wait_for_choice(pending)

    assert (await coordinator.service.profile(GROUP, TRIGGER)).affection == 999
    assert (await coordinator.service.profile(GROUP, OTHER)).affection == 0
    assert any("转盘好感 -2，当前 0" in message.extract_plain_text() for message in sent)


async def test_wheel_meme_unavailable_does_not_redraw(tmp_path: Path) -> None:
    coordinator = make_coordinator(tmp_path, rng=SequenceRng([50, 0]), memes=None)
    await give(coordinator.service, OTHER, 10)
    pending, sent, _ = await start(coordinator)
    await coordinator.answer(GROUP, OTHER, "路人", "参与")
    await coordinator.answer(GROUP, TRIGGER, "触发者", "运作")
    await coordinator.answer(GROUP, TRIGGER, "触发者", "确认")
    await wait_for_choice(pending)

    assert (await coordinator.service.profile(GROUP, OTHER)).affection == 8
    assert any("本次表情生成失败" in message.extract_plain_text() for message in sent)


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
    assert pending.awaiting_confirm is True
    release_opening.set()

    try:
        assert await start_task
        assert pending.task is not None
        await coordinator.answer(GROUP, TRIGGER, "触发者", "确认")
        await wait_for_choice(pending)
        assert (await coordinator.service.profile(GROUP, TRIGGER)).affection == 11
    finally:
        task = pending.task
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_wheel_operation_continues_after_participant_adjust_failure(
    tmp_path: Path, monkeypatch
) -> None:
    coordinator = make_coordinator(tmp_path, rng=SequenceRng([50, 50]))
    await give(coordinator.service, TRIGGER, 10)
    await give(coordinator.service, OTHER, 10)
    pending, sent, _ = await start(coordinator)
    await coordinator.answer(GROUP, OTHER, "路人", "参与")

    original_adjust = coordinator.service.adjust

    async def flaky_adjust(group_id, user_id, nickname, delta):
        if user_id == TRIGGER:
            raise RuntimeError("settle failed")
        return await original_adjust(group_id, user_id, nickname, delta)

    monkeypatch.setattr(coordinator.service, "adjust", flaky_adjust)

    await coordinator.answer(GROUP, TRIGGER, "触发者", "运作")
    await coordinator.answer(GROUP, TRIGGER, "触发者", "确认")
    await wait_for_choice(pending)
    assert (await coordinator.service.profile(GROUP, TRIGGER)).affection == 10
    assert (await coordinator.service.profile(GROUP, OTHER)).affection == 9
    assert GROUP not in coordinator.pending
    assert any("转盘好感 +1" in message.extract_plain_text() for message in sent)


def sent_text(sent: list[Message]) -> str:
    return "\n".join(message.extract_plain_text() for message in sent)


def test_meme_confirm_format_falls_back_to_key_and_uncategorized() -> None:
    meme = SimpleNamespace(key="plain_key", keywords=[], tags=[])

    body = format_meme_confirm(meme, 30)

    assert "「plain_key」" in body
    assert "种类：无分类" in body
    assert "键名：plain_key" in body
    assert "表情内容可能引起不适，请谨慎确认" in body
    assert "请在 30 秒内回复「确认」或「不确认」" in body
    assert "50% 概率好感 +2" in body
    assert "50% 概率好感 -2" in body


async def test_wheel_meme_confirm_message_lists_template_and_warning(
    tmp_path: Path,
) -> None:
    memes = FakeMemes()
    coordinator = make_coordinator(tmp_path, rng=SequenceRng([0]), memes=memes)
    pending, sent, _ = await start(coordinator)

    await operate(coordinator, pending)
    await asyncio.sleep(0)

    assert GROUP not in coordinator.pending
    assert (GROUP, TRIGGER) in coordinator.meme_confirms
    body = sent_text(sent)
    assert "「摸摸头」" in body
    assert "种类：可爱、宠物" in body
    assert "键名：petpet" in body
    assert "表情内容可能引起不适，请谨慎确认" in body
    assert "请在 30 秒内回复「确认」或「不确认」" in body
    assert "50% 概率好感 +2" in body
    assert "50% 概率好感 -2" in body
    assert "30 秒" in body
    assert not any(segment.type == "image" for message in sent for segment in message)
    assert memes.draw_calls == 1
    assert memes.generate_calls == 0
    assert memes.generated == []
    await cancel_meme_confirm(coordinator)


async def test_wheel_meme_whitelisted_user_gets_image_without_confirm(
    tmp_path: Path,
) -> None:
    memes = FakeMemes()
    coordinator = make_coordinator(tmp_path, rng=SequenceRng([0]), memes=memes)
    await coordinator.service.toggle_meme_confirm_whitelist(TRIGGER)
    pending, sent, _ = await start(coordinator)

    await operate(coordinator, pending)

    assert coordinator.meme_confirms == {}
    assert any(segment.type == "image" for message in sent for segment in message)
    assert "馒头为你做了一张随机表情" in sent_text(sent)
    assert memes.generate_calls == 1
    assert memes.draw_calls == 0
    assert memes.generated_groups == [GROUP]


async def test_wheel_meme_confirm_generates_with_drawn_template(tmp_path: Path) -> None:
    memes = FakeMemes()
    coordinator = make_coordinator(tmp_path, rng=SequenceRng([0]), memes=memes)
    await give(coordinator.service, TRIGGER, 10)
    pending, sent, _ = await start(coordinator)
    await operate(coordinator, pending)
    confirm = coordinator.meme_confirms[(GROUP, TRIGGER)]

    assert await coordinator.answer(GROUP, TRIGGER, "触发者", "确认") is True
    if confirm.task is not None:
        with suppress(asyncio.CancelledError):
            await confirm.task

    assert memes.generated == [(confirm.meme, "single", TRIGGER)]
    assert memes.drawn_groups == [GROUP]
    assert memes.generated_groups == [GROUP]
    assert (GROUP, TRIGGER) not in coordinator.meme_confirms
    assert (await coordinator.service.profile(GROUP, TRIGGER)).affection == 10
    assert any(segment.type == "image" for message in sent for segment in message)
    assert "已确认，馒头为你做了一张表情" in sent_text(sent)


@pytest.mark.parametrize(
    ("value", "delta"),
    [(0.2, 2), (0.8, -2)],
)
async def test_wheel_meme_decline_rolls_affection(
    tmp_path: Path, value: float, delta: int
) -> None:
    memes = FakeMemes()
    coordinator = make_coordinator(
        tmp_path, rng=SequenceRng([0], value=value), memes=memes
    )
    await give(coordinator.service, TRIGGER, 10)
    pending, sent, _ = await start(coordinator)
    await operate(coordinator, pending)

    assert await coordinator.answer(GROUP, TRIGGER, "触发者", "不确认") is True

    assert (await coordinator.service.profile(GROUP, TRIGGER)).affection == 10 + delta
    body = sent_text(sent)
    assert f"好感 {delta:+d}" in body
    assert f"当前 {10 + delta}" in body
    assert "已放弃制作表情" in body
    assert not any(segment.type == "image" for message in sent for segment in message)
    assert coordinator.meme_confirms == {}


async def test_wheel_meme_confirm_timeout_rolls_affection(tmp_path: Path) -> None:
    memes = FakeMemes()
    coordinator = make_coordinator(
        tmp_path, rng=SequenceRng([0], value=0.2), memes=memes
    )
    coordinator.meme_confirm_timeout = 0.02
    await give(coordinator.service, TRIGGER, 10)
    pending, sent, _ = await start(coordinator)
    await operate(coordinator, pending)
    confirm = coordinator.meme_confirms[(GROUP, TRIGGER)]
    assert confirm.task is not None

    await asyncio.wait_for(confirm.task, 2)

    assert (await coordinator.service.profile(GROUP, TRIGGER)).affection == 12
    assert coordinator.meme_confirms == {}
    body = sent_text(sent)
    assert "表情确认超时" in body
    assert "好感 +2，当前 12" in body
    assert not any(segment.type == "image" for message in sent for segment in message)


async def test_wheel_meme_confirms_run_in_parallel(tmp_path: Path) -> None:
    memes = FakeMemes()
    coordinator = make_coordinator(
        tmp_path, rng=SequenceRng([0, 0], value=0.2), memes=memes
    )
    await give(coordinator.service, TRIGGER, 10)
    await give(coordinator.service, OTHER, 10)
    pending, sent, _ = await start(coordinator)
    await coordinator.answer(GROUP, OTHER, "路人", "参与")

    await operate(coordinator, pending)
    await asyncio.sleep(0)

    assert GROUP not in coordinator.pending
    assert set(coordinator.meme_confirms) == {(GROUP, TRIGGER), (GROUP, OTHER)}
    assert await coordinator.answer(GROUP, TRIGGER, "触发者", "确认") is True
    assert await coordinator.answer(GROUP, OTHER, "路人", "不确认") is True

    assert (await coordinator.service.profile(GROUP, TRIGGER)).affection == 10
    assert (await coordinator.service.profile(GROUP, OTHER)).affection == 10
    body = sent_text(sent)
    assert "已确认，馒头为你做了一张表情" in body
    assert "已放弃制作表情，好感 +2，当前 10" in body
    assert coordinator.meme_confirms == {}
    assert any(segment.type == "image" for message in sent for segment in message)


async def test_wheel_meme_confirm_replaced_by_new_draw(tmp_path: Path) -> None:
    memes = FakeMemes()
    coordinator = make_coordinator(
        tmp_path, rng=SequenceRng([0, 50, 0]), memes=memes
    )
    coordinator.meme_confirm_timeout = 5
    await give(coordinator.service, TRIGGER, 10)
    await give(coordinator.service, OTHER, 10)
    pending, _, _ = await start(coordinator)
    await operate(coordinator, pending)
    old = coordinator.meme_confirms[(GROUP, TRIGGER)]
    assert old.task is not None

    pending2, _, _ = await start(coordinator, user_id=OTHER, nickname="路人")
    await coordinator.answer(GROUP, TRIGGER, "触发者", "参与")
    await operate(coordinator, pending2, user_id=OTHER)

    new = coordinator.meme_confirms[(GROUP, TRIGGER)]
    assert new is not old
    assert old.accepting is False
    with suppress(asyncio.CancelledError):
        await old.task
    assert old.task.done()
    assert (await coordinator.service.profile(GROUP, TRIGGER)).affection == 8
    assert (await coordinator.service.profile(GROUP, OTHER)).affection == 11
    await cancel_meme_confirm(coordinator)


async def test_wheel_meme_confirm_does_not_block_new_wheel(tmp_path: Path) -> None:
    memes = FakeMemes()
    coordinator = make_coordinator(tmp_path, rng=SequenceRng([0, 50]), memes=memes)
    coordinator.meme_confirm_timeout = 5
    await give(coordinator.service, TRIGGER, 10)
    await give(coordinator.service, OTHER, 10)
    pending, _, _ = await start(coordinator)
    await operate(coordinator, pending)
    assert (GROUP, TRIGGER) in coordinator.meme_confirms

    pending2, _, _ = await start(coordinator, user_id=OTHER, nickname="路人")
    await operate(coordinator, pending2, user_id=OTHER)

    assert GROUP not in coordinator.pending
    assert (GROUP, TRIGGER) in coordinator.meme_confirms
    assert (await coordinator.service.profile(GROUP, OTHER)).affection == 11
    assert (await coordinator.service.profile(GROUP, TRIGGER)).affection == 10
    await cancel_meme_confirm(coordinator)


async def test_wheel_meme_confirmed_asset_missing_reports_without_redraw(
    tmp_path: Path,
) -> None:
    memes = FakeMemes(missing_users={TRIGGER})
    resources = FakeResources()
    coordinator = make_coordinator(
        tmp_path, rng=SequenceRng([0]), memes=memes, resources=resources
    )
    pending, sent, _ = await start(coordinator)
    await operate(coordinator, pending)

    assert await coordinator.answer(GROUP, TRIGGER, "触发者", "确认") is True

    assert resources.calls == 1
    assert "表情素材尚未下载完成" in sent_text(sent)
    assert not any(segment.type == "image" for message in sent for segment in message)
    assert coordinator.meme_confirms == {}
    assert coordinator.meme_confirms.get((GROUP, TRIGGER)) is None


async def test_wheel_meme_confirmed_generation_failure_reports(tmp_path: Path) -> None:
    memes = FakeMemes(generate_error=MemeGenerationError("boom"))
    coordinator = make_coordinator(tmp_path, rng=SequenceRng([0]), memes=memes)
    pending, sent, _ = await start(coordinator)
    await operate(coordinator, pending)

    assert await coordinator.answer(GROUP, TRIGGER, "触发者", "确认") is True

    assert "本次表情生成失败，不重抽" in sent_text(sent)
    assert not any(segment.type == "image" for message in sent for segment in message)
    assert coordinator.meme_confirms == {}


async def test_wheel_taozi_asks_before_charging(tmp_path: Path) -> None:
    stickers = FakeStickers()
    coordinator = make_coordinator(tmp_path, rng=SequenceRng([19]), stickers=stickers)
    await give(coordinator.service, TRIGGER, 20)
    pending, sent, _ = await start(coordinator)

    await operate(coordinator, pending)

    assert (GROUP, TRIGGER) in coordinator.sticker_confirms
    assert (await coordinator.service.profile(GROUP, TRIGGER)).affection == 20
    body = sent_text(sent)
    assert "「桃子表情包」" in body
    assert "花 10 点好感" in body
    assert "当前 20" in body
    assert "回复「要」或「不要」" in body
    assert "30 秒" in body
    assert not any(segment.type == "image" for message in sent for segment in message)
    assert stickers.picks == 0
    assert coordinator.sticker_confirm_timeout == 30


async def test_wheel_taozi_confirm_charges_and_sends_image(tmp_path: Path) -> None:
    stickers = FakeStickers()
    coordinator = make_coordinator(tmp_path, rng=SequenceRng([19]), stickers=stickers)
    await give(coordinator.service, TRIGGER, 20)
    pending, sent, _ = await start(coordinator)
    await operate(coordinator, pending)

    assert await coordinator.answer(GROUP, TRIGGER, "触发者", "要") is True

    assert (await coordinator.service.profile(GROUP, TRIGGER)).affection == 10
    assert (GROUP, TRIGGER) not in coordinator.sticker_confirms
    assert stickers.picks == 1
    assert any(segment.type == "image" for message in sent for segment in message)
    body = sent_text(sent)
    assert "已扣除 10 点好感" in body
    assert "「头部主播」" in body


async def test_wheel_taozi_confirm_at_exactly_ten_points_reaches_zero(
    tmp_path: Path,
) -> None:
    coordinator = make_coordinator(
        tmp_path, rng=SequenceRng([19]), stickers=FakeStickers()
    )
    await give(coordinator.service, TRIGGER, 10)
    pending, sent, _ = await start(coordinator)
    await operate(coordinator, pending)

    assert await coordinator.answer(GROUP, TRIGGER, "触发者", "要") is True

    assert (await coordinator.service.profile(GROUP, TRIGGER)).affection == 0
    assert any(segment.type == "image" for message in sent for segment in message)


async def test_wheel_taozi_decline_keeps_affection(tmp_path: Path) -> None:
    coordinator = make_coordinator(
        tmp_path, rng=SequenceRng([19]), stickers=FakeStickers()
    )
    await give(coordinator.service, TRIGGER, 20)
    pending, sent, _ = await start(coordinator)
    await operate(coordinator, pending)

    assert await coordinator.answer(GROUP, TRIGGER, "触发者", "不要") is True

    assert (await coordinator.service.profile(GROUP, TRIGGER)).affection == 20
    assert (GROUP, TRIGGER) not in coordinator.sticker_confirms
    assert "已放弃兑换桃子的表情包" in sent_text(sent)
    assert not any(segment.type == "image" for message in sent for segment in message)


async def test_wheel_taozi_timeout_keeps_affection(tmp_path: Path) -> None:
    coordinator = make_coordinator(
        tmp_path, rng=SequenceRng([19]), stickers=FakeStickers()
    )
    coordinator.sticker_confirm_timeout = 0.02
    await give(coordinator.service, TRIGGER, 20)
    pending, sent, _ = await start(coordinator)
    await operate(coordinator, pending)
    confirm = coordinator.sticker_confirms[(GROUP, TRIGGER)]
    assert confirm.task is not None

    await asyncio.wait_for(confirm.task, 2)

    assert (await coordinator.service.profile(GROUP, TRIGGER)).affection == 20
    assert coordinator.sticker_confirms == {}
    assert "桃子表情包确认超时" in sent_text(sent)


async def test_wheel_taozi_insufficient_at_prompt_does_not_ask(tmp_path: Path) -> None:
    coordinator = make_coordinator(
        tmp_path, rng=SequenceRng([19]), stickers=FakeStickers()
    )
    await give(coordinator.service, TRIGGER, 5)
    pending, sent, _ = await start(coordinator)

    await operate(coordinator, pending)

    assert coordinator.sticker_confirms == {}
    assert (await coordinator.service.profile(GROUP, TRIGGER)).affection == 5
    assert "好感不足 10 点" in sent_text(sent)


async def test_wheel_taozi_insufficient_after_prompt_does_not_charge(
    tmp_path: Path,
) -> None:
    stickers = FakeStickers()
    coordinator = make_coordinator(tmp_path, rng=SequenceRng([19]), stickers=stickers)
    await give(coordinator.service, TRIGGER, 20)
    pending, sent, _ = await start(coordinator)
    await operate(coordinator, pending)
    await give(coordinator.service, TRIGGER, 3)

    assert await coordinator.answer(GROUP, TRIGGER, "触发者", "要") is True

    assert (await coordinator.service.profile(GROUP, TRIGGER)).affection == 3
    assert "好感不足 10 点" in sent_text(sent)
    assert not any(segment.type == "image" for message in sent for segment in message)
    assert coordinator.sticker_confirms == {}


async def test_wheel_taozi_missing_assets_does_not_charge(tmp_path: Path) -> None:
    stickers = FakeStickers(error=StickerAssetMissing("catalog.json"))
    coordinator = make_coordinator(tmp_path, rng=SequenceRng([19]), stickers=stickers)
    await give(coordinator.service, TRIGGER, 20)
    pending, sent, _ = await start(coordinator)
    await operate(coordinator, pending)

    assert await coordinator.answer(GROUP, TRIGGER, "触发者", "要") is True

    assert (await coordinator.service.profile(GROUP, TRIGGER)).affection == 20
    assert "桃子表情包素材还没准备好" in sent_text(sent)
    assert stickers.rechecks == 1
    assert not any(segment.type == "image" for message in sent for segment in message)


async def test_wheel_taozi_pick_failure_does_not_charge(tmp_path: Path) -> None:
    stickers = FakeStickers(error=StickerError("boom"))
    coordinator = make_coordinator(tmp_path, rng=SequenceRng([19]), stickers=stickers)
    await give(coordinator.service, TRIGGER, 20)
    pending, sent, _ = await start(coordinator)
    await operate(coordinator, pending)

    assert await coordinator.answer(GROUP, TRIGGER, "触发者", "要") is True

    assert (await coordinator.service.profile(GROUP, TRIGGER)).affection == 20
    assert "本次桃子表情包发送失败，不重抽" in sent_text(sent)
    assert not any(segment.type == "image" for message in sent for segment in message)


async def test_wheel_taozi_confirm_replaced_by_new_draw(tmp_path: Path) -> None:
    coordinator = make_coordinator(
        tmp_path, rng=SequenceRng([19, 19, 19]), stickers=FakeStickers()
    )
    coordinator.sticker_confirm_timeout = 5
    await give(coordinator.service, TRIGGER, 100)
    await give(coordinator.service, OTHER, 100)
    pending, _, _ = await start(coordinator)
    await operate(coordinator, pending)
    old = coordinator.sticker_confirms[(GROUP, TRIGGER)]
    assert old.task is not None

    pending2, _, _ = await start(coordinator, user_id=OTHER, nickname="路人")
    await coordinator.answer(GROUP, TRIGGER, "触发者", "参与")
    await operate(coordinator, pending2, user_id=OTHER)

    new = coordinator.sticker_confirms[(GROUP, TRIGGER)]
    assert new is not old
    assert old.accepting is False
    with suppress(asyncio.CancelledError):
        await old.task
    assert (await coordinator.service.profile(GROUP, TRIGGER)).affection == 98
    new.task.cancel()


async def test_wheel_taozi_and_meme_confirms_coexist(tmp_path: Path) -> None:
    memes = FakeMemes()
    stickers = FakeStickers()
    coordinator = make_coordinator(
        tmp_path, rng=SequenceRng([0, 50, 19]), memes=memes, stickers=stickers
    )
    await give(coordinator.service, TRIGGER, 20)
    await give(coordinator.service, OTHER, 20)
    pending, _, _ = await start(coordinator)
    await operate(coordinator, pending)
    assert (GROUP, TRIGGER) in coordinator.meme_confirms

    pending2, _, _ = await start(coordinator, user_id=OTHER, nickname="路人")
    await coordinator.answer(GROUP, TRIGGER, "触发者", "参与")
    await operate(coordinator, pending2, user_id=OTHER)
    assert (GROUP, TRIGGER) in coordinator.sticker_confirms

    assert await coordinator.answer(GROUP, TRIGGER, "触发者", "要") is True

    assert (await coordinator.service.profile(GROUP, TRIGGER)).affection == 8
    assert (GROUP, TRIGGER) in coordinator.meme_confirms
    assert (GROUP, TRIGGER) not in coordinator.sticker_confirms
    await cancel_meme_confirm(coordinator)
