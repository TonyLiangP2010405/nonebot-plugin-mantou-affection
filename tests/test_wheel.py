from __future__ import annotations

import asyncio
import base64
import random
from contextlib import suppress
from pathlib import Path

import pytest
from nonebot.adapters.onebot.v11 import Message

from nonebot_plugin_mantou_affection.config import Config
from nonebot_plugin_mantou_affection.image_processing import ImageQuotaStore
from nonebot_plugin_mantou_affection.models import Profile
from nonebot_plugin_mantou_affection.panel_quota import PanelQuotaStore, PanelRankQuotaStore
from nonebot_plugin_mantou_affection.service import AffectionService
from nonebot_plugin_mantou_affection.storage import AffectionStore
from nonebot_plugin_mantou_affection.wheel import (
    WHEEL_OUTCOMES,
    WheelCoordinator,
    WheelOutcome,
    draw_wheel_outcome,
    format_wheel_rules,
)
from nonebot_plugin_mantou_affection.wheel_memes import MemeAssetMissing, MemeGenerationError
from nonebot_plugin_mantou_affection.wheel_stickers import StickerAssetMissing, StickerError
from nonebot_plugin_mantou_affection.wheel_wallpapers import (
    Wallpaper,
    WallpaperUnavailable,
)

GROUP = "94001"
TRIGGER = "94002"
OTHER = "94003"


class FixedRollRng(random.Random):
    def __new__(cls, *args, **kwargs):
        return super().__new__(cls)

    def __init__(self, roll: int):
        super().__init__(0)
        self.roll = roll

    def randrange(self, stop: int) -> int:
        assert stop == 100
        return self.roll


class SequenceRng(FixedRollRng):
    def __init__(self, rolls: list[int]):
        super().__init__(0)
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
    def __init__(
        self,
        missing_users: set[str] | None = None,
        generate_error: Exception | None = None,
    ):
        self.missing_users = missing_users or set()
        self.generate_error = generate_error
        self.calls: list[tuple[str | None, str]] = []

    async def generate(self, *, bot, user_id: str, nickname: str, group_id=None) -> bytes:
        self.calls.append((group_id, user_id))
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


class FakeWallpapers:
    def __init__(
        self,
        image: bytes = b"wallpaper:001",
        game: str = "原神",
        collection: str = "米游社「原神同人图」",
        error: Exception | None = None,
        fanart_error: Exception | None = None,
    ):
        self.image = image
        self.game = game
        self.collection = collection
        self.error = error
        self.fanart_error = fanart_error
        self.draws = 0
        self.calls: list[tuple[str | None, bool]] = []

    async def draw(self, game=None, *, fanart: bool = False):
        self.draws += 1
        self.calls.append((game, fanart))
        if self.error is not None:
            raise self.error
        if fanart and self.fanart_error is not None:
            raise self.fanart_error
        source = "fanart" if fanart else "official"
        return (
            Wallpaper(
                self.game, self.collection, "https://example.com/a.png", source
            ),
            self.image,
        )


def make_coordinator(
    tmp_path: Path,
    rng=None,
    memes=None,
    resources=None,
    stickers=None,
    image_quota=None,
    panel_quota=None,
    panel_rank_quota=None,
    wallpapers=None,
    sticker_dir: str = "",
    meme_groups: str = GROUP,
):
    config = Config(
        mantou_affection_newbie_protection_below=0,
        mantou_affection_wheel_sticker_dir=sticker_dir,
        mantou_affection_multiplier_chance=0.0,
        mantou_affection_memes_enabled_groups=meme_groups,
    )
    service = AffectionService(AffectionStore(tmp_path / "affection.json"), config)
    coordinator = WheelCoordinator(
        service,
        config,
        rng=rng or random.Random(0),
        memes=memes,
        resources=resources,
        stickers=stickers,
        wallpapers=wallpapers,
        image_quota=image_quota or ImageQuotaStore(tmp_path / "image_quota.json"),
        panel_quota=panel_quota or PanelQuotaStore(tmp_path / "panel_quota.json"),
        panel_rank_quota=panel_rank_quota
        or PanelRankQuotaStore(tmp_path / "panel_rank_quota.json"),
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


async def operate(coordinator: WheelCoordinator, pending) -> None:
    """跳过倒计时直接自动开转：先取消计时任务，再把倒计时归零跑一遍开转。"""

    coordinator.choice_timeout = 0
    task = pending.task
    if task is not None and not task.done():
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
    await coordinator._auto_start(pending)


def test_wheel_weights_match_approved_table() -> None:
    weighted = [((outcome.kind, outcome.value), weight) for outcome, weight in WHEEL_OUTCOMES]
    assert weighted == [
        (("meme", 0), 7),
        (("mantou_sticker", 0), 3),
        (("taozi", 0), 1),
        (("wallpaper", 0), 10),
        (("image", 1), 2),
        (("image", 2), 1),
        (("poke", 1), 7),
        (("poke", 3), 5),
        (("poke", 5), 3),
        (("poke", 10), 2),
        (("poke", 20), 1),
        (("panel", 1), 5),
        (("panel_rank", 1), 3),
        (("affection", 1), 5),
        (("affection", 2), 4),
        (("affection", 3), 4),
        (("affection", 4), 3),
        (("affection", 5), 2),
        (("affection", 6), 2),
        (("affection", 8), 1),
        (("affection", 10), 1),
        (("affection", 20), 1),
        (("affection", 50), 1),
        (("affection", -1), 5),
        (("affection", -2), 4),
        (("affection", -3), 3),
        (("affection", -4), 2),
        (("affection", -5), 2),
        (("affection", -6), 1),
        (("affection", -8), 1),
        (("affection", -10), 1),
        (("affection", -20), 1),
        (("affection", -50), 1),
        (("spin", 1), 3),
        (("spin", 2), 1),
        (("spin", 3), 1),
    ]
    assert sum(weight for _, weight in WHEEL_OUTCOMES) == 100


def test_wheel_image_quota_is_rare_and_capped_at_two() -> None:
    images = [(o.value, w) for o, w in WHEEL_OUTCOMES if o.kind == "image"]
    assert [value for value, _ in images] == [1, 2]
    assert sum(weight for _, weight in images) == 3
    pokes = [w for o, w in WHEEL_OUTCOMES if o.kind == "poke"]
    assert sum(pokes) == 18
    wallpapers = [w for o, w in WHEEL_OUTCOMES if o.kind == "wallpaper"]
    assert wallpapers == [10]
    panels = [w for o, w in WHEEL_OUTCOMES if o.kind == "panel"]
    assert panels == [5]
    panel_ranks = [w for o, w in WHEEL_OUTCOMES if o.kind == "panel_rank"]
    assert panel_ranks == [3]
    assert sum(weight for _, weight in WHEEL_OUTCOMES) == 100


def test_wheel_draw_uses_independent_integer_ranges() -> None:
    assert draw_wheel_outcome(FixedRollRng(6)).kind == "meme"
    assert draw_wheel_outcome(FixedRollRng(7)).kind == "mantou_sticker"
    assert draw_wheel_outcome(FixedRollRng(9)).kind == "mantou_sticker"
    assert draw_wheel_outcome(FixedRollRng(10)).kind == "taozi"
    assert draw_wheel_outcome(FixedRollRng(11)).kind == "wallpaper"
    assert draw_wheel_outcome(FixedRollRng(20)).kind == "wallpaper"
    assert draw_wheel_outcome(FixedRollRng(21)) == WheelOutcome("image", 1)
    assert draw_wheel_outcome(FixedRollRng(22)) == WheelOutcome("image", 1)
    assert draw_wheel_outcome(FixedRollRng(23)) == WheelOutcome("image", 2)
    assert draw_wheel_outcome(FixedRollRng(24)) == WheelOutcome("poke", 1)
    assert draw_wheel_outcome(FixedRollRng(30)) == WheelOutcome("poke", 1)
    assert draw_wheel_outcome(FixedRollRng(31)) == WheelOutcome("poke", 3)
    assert draw_wheel_outcome(FixedRollRng(35)) == WheelOutcome("poke", 3)
    assert draw_wheel_outcome(FixedRollRng(36)) == WheelOutcome("poke", 5)
    assert draw_wheel_outcome(FixedRollRng(38)) == WheelOutcome("poke", 5)
    assert draw_wheel_outcome(FixedRollRng(39)) == WheelOutcome("poke", 10)
    assert draw_wheel_outcome(FixedRollRng(40)) == WheelOutcome("poke", 10)
    assert draw_wheel_outcome(FixedRollRng(41)) == WheelOutcome("poke", 20)
    assert draw_wheel_outcome(FixedRollRng(42)) == WheelOutcome("panel", 1)
    assert draw_wheel_outcome(FixedRollRng(46)) == WheelOutcome("panel", 1)
    assert draw_wheel_outcome(FixedRollRng(47)) == WheelOutcome("panel_rank", 1)
    assert draw_wheel_outcome(FixedRollRng(49)) == WheelOutcome("panel_rank", 1)
    assert draw_wheel_outcome(FixedRollRng(50)).value == 1
    assert draw_wheel_outcome(FixedRollRng(72)).value == 20
    assert draw_wheel_outcome(FixedRollRng(73)).value == 50
    assert draw_wheel_outcome(FixedRollRng(74)).value == -1
    assert draw_wheel_outcome(FixedRollRng(94)).value == -50
    assert draw_wheel_outcome(FixedRollRng(95)) == WheelOutcome("spin", 1)
    assert draw_wheel_outcome(FixedRollRng(98)) == WheelOutcome("spin", 2)
    assert draw_wheel_outcome(FixedRollRng(99)) == WheelOutcome("spin", 3)


def test_rules_message_lists_every_probability() -> None:
    rules = format_wheel_rules(60)
    assert "参与需要扣除 2 点好感度" in rules
    assert "人数不限" in rules
    assert "最多 5 人" not in rules
    assert "60 秒后自动开转" in rules
    assert "倒计时结束前都可以参与" in rules
    assert "不运作" not in rules
    assert "确认" not in rules
    assert "馒头制作随机表情 7%" in rules
    assert "馒头表情包 3%（随机发一张）" in rules
    assert "桃子表情包 1%（换一张需 10 点好感）" in rules
    assert "随机壁纸 10%" in rules
    assert "馒头主动戳一戳：×1 7%、×3 5%、×5 3%、×10 2%、×20 1%" in rules
    assert "角色面板次数 5%" in rules
    assert "群面板排名次数 3%" in rules
    assert "图像处理次数：×1 2%、×2 1%" in rules
    assert "每张图消耗 1 次" in rules
    assert "+1 5%" in rules and "+50 1%" in rules
    assert "-1 5%" in rules and "-50 1%" in rules


async def test_start_message_shows_probability_table(tmp_path: Path) -> None:
    coordinator = make_coordinator(tmp_path)
    _, sent, _ = await start(coordinator)
    body = sent[0].extract_plain_text()
    assert "转盘各项概率" in body
    assert "馒头制作随机表情 7%" in body
    assert "馒头表情包 3%" in body
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


async def test_wheel_join_has_no_participant_cap(tmp_path: Path) -> None:
    coordinator = make_coordinator(tmp_path)
    user_ids = [str(94010 + index) for index in range(6)]
    pending, sent, _ = await start(coordinator)

    for user_id in user_ids:
        await give(coordinator.service, user_id, 10)
        await coordinator.answer(GROUP, user_id, "路人", "参与")
    await coordinator.answer(GROUP, user_ids[0], "路人", "参与")
    await coordinator.answer(GROUP, TRIGGER, "触发者", "参与")

    assert len(pending.participants) == 6
    scores = [
        (await coordinator.service.profile(GROUP, user_id)).affection
        for user_id in user_ids
    ]
    assert scores == [8, 8, 8, 8, 8, 8]
    texts = [message.extract_plain_text() for message in sent]
    assert any("你已经参与本轮转盘了" in text for text in texts)
    assert not any("名额已满" in text for text in texts)
    assert any("触发者已自动加入" in text for text in texts)
    assert pending.accepting is True
    pending.task.cancel()
    with suppress(asyncio.CancelledError):
        await pending.task


async def test_wheel_operate_and_confirm_keywords_are_ignored(tmp_path: Path) -> None:
    coordinator = make_coordinator(tmp_path)
    pending, sent, _ = await start(coordinator)

    for keyword in ("运作", "不运作", "确认"):
        assert await coordinator.answer(GROUP, TRIGGER, "触发者", keyword) is False
    assert pending.accepting is True
    assert (await coordinator.service.profile(GROUP, TRIGGER)).affection == 0
    assert len(sent) == 1
    pending.task.cancel()
    with suppress(asyncio.CancelledError):
        await pending.task


async def test_wheel_auto_starts_after_timeout(tmp_path: Path) -> None:
    coordinator = make_coordinator(tmp_path, rng=SequenceRng([50, 50]))
    coordinator.choice_timeout = 0.02
    await give(coordinator.service, TRIGGER, 10)
    await give(coordinator.service, OTHER, 10)
    pending, sent, _ = await start(coordinator)
    await coordinator.answer(GROUP, OTHER, "路人", "参与")

    await wait_for_choice(pending)
    assert (await coordinator.service.profile(GROUP, TRIGGER)).affection == 11
    assert (await coordinator.service.profile(GROUP, OTHER)).affection == 9
    assert GROUP not in coordinator.pending
    body = "\n".join(message.extract_plain_text() for message in sent)
    assert "秒到，馒头命运齿轮转盘开始转动" in body
    assert "转盘好感 +1" in body


async def test_wheel_auto_start_closes_joins(tmp_path: Path) -> None:
    coordinator = make_coordinator(tmp_path, rng=SequenceRng([50]))
    coordinator.choice_timeout = 0.02
    await give(coordinator.service, OTHER, 10)
    pending, _, _ = await start(coordinator)

    await wait_for_choice(pending)
    assert await coordinator.answer(GROUP, OTHER, "路人", "参与") is False
    assert pending.participants == []
    assert (await coordinator.service.profile(GROUP, OTHER)).affection == 10


async def test_wheel_timeout_draws_trigger_without_waiting_for_a_choice(
    tmp_path: Path,
) -> None:
    coordinator = make_coordinator(tmp_path, rng=FixedRollRng(50))
    coordinator.choice_timeout = 0.01
    await give(coordinator.service, TRIGGER, 10)
    pending, sent, _ = await start(coordinator)

    await asyncio.wait_for(pending.task, 2)
    assert (await coordinator.service.profile(GROUP, TRIGGER)).affection == 11
    assert GROUP not in coordinator.pending
    texts = [message.extract_plain_text() for message in sent]
    assert any("开始转动" in text for text in texts)
    assert any("转盘好感 +1" in text for text in texts)


async def test_wheel_auto_start_settles_once(tmp_path: Path) -> None:
    coordinator = make_coordinator(tmp_path, rng=SequenceRng([50, 50]))
    coordinator.choice_timeout = 0.02
    await give(coordinator.service, TRIGGER, 10)
    await give(coordinator.service, OTHER, 10)
    pending, _, _ = await start(coordinator)
    await coordinator.answer(GROUP, OTHER, "路人", "参与")

    await wait_for_choice(pending)
    await asyncio.sleep(0.02)
    assert (await coordinator.service.profile(GROUP, TRIGGER)).affection == 11
    assert (await coordinator.service.profile(GROUP, OTHER)).affection == 9
    assert GROUP not in coordinator.pending


async def test_wheel_operation_draws_each_person_independently(tmp_path: Path) -> None:
    coordinator = make_coordinator(tmp_path, rng=SequenceRng([50, 73]))
    await give(coordinator.service, TRIGGER, 10)
    await give(coordinator.service, OTHER, 10)
    pending, sent, _ = await start(coordinator)
    await coordinator.answer(GROUP, OTHER, "路人", "参与")

    await operate(coordinator, pending)
    assert (await coordinator.service.profile(GROUP, TRIGGER)).affection == 11
    assert (await coordinator.service.profile(GROUP, OTHER)).affection == 58
    assert any("转盘好感 +50，当前 58" in message.extract_plain_text() for message in sent)


async def test_wheel_image_quota_does_not_call_poke_api(
    tmp_path: Path, monkeypatch
) -> None:
    coordinator = make_coordinator(tmp_path, rng=SequenceRng([50, 21]))

    async def forbidden_poke(*args, **kwargs):
        raise AssertionError("不得调用 poke")

    monkeypatch.setattr(coordinator.service, "poke", forbidden_poke)
    await give(coordinator.service, TRIGGER, 10)
    await give(coordinator.service, OTHER, 10)
    bot = FakeBot(fail_users={OTHER})
    pending, sent, _ = await start(coordinator, bot=bot)
    await coordinator.answer(GROUP, OTHER, "路人", "参与")
    await operate(coordinator, pending)

    assert bot.calls == []
    assert (await coordinator.service.profile(GROUP, OTHER)).affection == 8
    assert await coordinator.image_quota.get(GROUP, OTHER) == 1
    body = "\n".join(message.extract_plain_text() for message in sent)
    assert "抽到图像处理次数 ×1" in body


async def test_wheel_meme_missing_requests_throttled_recheck(tmp_path: Path) -> None:
    memes = FakeMemes(missing_users={OTHER})
    resources = FakeResources()
    coordinator = make_coordinator(
        tmp_path, rng=SequenceRng([0, 0]), memes=memes, resources=resources
    )
    await give(coordinator.service, OTHER, 10)
    pending, sent, _ = await start(coordinator)
    await coordinator.answer(GROUP, OTHER, "路人", "参与")
    await operate(coordinator, pending)

    assert resources.calls == 1
    assert (await coordinator.service.profile(GROUP, OTHER)).affection == 8
    assert any(segment.type == "image" for message in sent for segment in message)


@pytest.mark.parametrize(
    ("roll", "delta"),
    [
        (50, 1),
        (55, 2),
        (59, 3),
        (63, 4),
        (66, 5),
        (68, 6),
        (70, 8),
        (71, 10),
        (72, 20),
        (73, 50),
        (74, -1),
        (79, -2),
        (83, -3),
        (86, -4),
        (88, -5),
        (90, -6),
        (91, -8),
        (92, -10),
        (93, -20),
        (94, -50),
    ],
)
async def test_wheel_affection_outcomes(tmp_path: Path, roll: int, delta: int) -> None:
    coordinator = make_coordinator(tmp_path, rng=SequenceRng([roll]))
    await give(coordinator.service, TRIGGER, 100)
    pending, _, _ = await start(coordinator)
    await operate(coordinator, pending)
    assert (await coordinator.service.profile(GROUP, TRIGGER)).affection == 100 + delta


@pytest.mark.parametrize(("roll", "count"), [(21, 1), (23, 2)])
async def test_wheel_image_quota_counts(tmp_path: Path, roll: int, count: int) -> None:
    coordinator = make_coordinator(tmp_path, rng=SequenceRng([roll]))
    pending, _, _ = await start(coordinator)
    await operate(coordinator, pending)
    assert await coordinator.image_quota.get(GROUP, TRIGGER) == count


async def test_wheel_image_quota_message_explains_usage(tmp_path: Path) -> None:
    coordinator = make_coordinator(tmp_path, rng=SequenceRng([21]))
    pending, sent, _ = await start(coordinator)
    await operate(coordinator, pending)

    body = sent[-1].extract_plain_text()
    assert "抽到图像处理次数 ×1" in body
    assert "当前共 1 次" in body
    assert "/馒头图像处理" in body


@pytest.mark.parametrize(
    ("roll", "count"), [(24, 1), (31, 3), (36, 5), (39, 10), (41, 20)]
)
async def test_wheel_poke_counts(tmp_path: Path, roll: int, count: int) -> None:
    coordinator = make_coordinator(tmp_path, rng=SequenceRng([roll]))
    pending, _, bot = await start(coordinator)
    await operate(coordinator, pending)
    assert len(bot.calls) == count
    assert all(call["user_id"] == int(TRIGGER) for call in bot.calls)


async def test_wheel_poke_message_clearly_says_mantou_pokes_you(tmp_path: Path) -> None:
    coordinator = make_coordinator(tmp_path, rng=SequenceRng([24]))
    pending, sent, _ = await start(coordinator)
    await operate(coordinator, pending)

    body = sent[-1].extract_plain_text()
    assert "馒头主动戳了你 1 次" in body
    assert "是馒头戳你，不用你回戳" in body


async def test_wheel_poke_failure_reports_without_raising(tmp_path: Path) -> None:
    coordinator = make_coordinator(tmp_path, rng=SequenceRng([41]))
    pending, sent, _ = await start(coordinator, bot=FakeBot(fail_users={TRIGGER}))
    await operate(coordinator, pending)

    body = "\n".join(message.extract_plain_text() for message in sent)
    assert "馒头主动戳你失败" in body
    assert "不用你回戳" in body


async def test_wheel_uses_actual_applied_delta_at_bounds(tmp_path: Path) -> None:
    coordinator = make_coordinator(tmp_path, rng=SequenceRng([73, 94]))
    await give(coordinator.service, TRIGGER, 999)
    await give(coordinator.service, OTHER, 4)
    pending, sent, _ = await start(coordinator)
    await coordinator.answer(GROUP, OTHER, "路人", "参与")
    await operate(coordinator, pending)

    assert (await coordinator.service.profile(GROUP, TRIGGER)).affection == 999
    assert (await coordinator.service.profile(GROUP, OTHER)).affection == 0
    assert any("转盘好感 -2，当前 0" in message.extract_plain_text() for message in sent)


async def test_wheel_meme_unavailable_does_not_redraw(tmp_path: Path) -> None:
    coordinator = make_coordinator(tmp_path, rng=SequenceRng([50, 0]), memes=None)
    await give(coordinator.service, OTHER, 10)
    pending, sent, _ = await start(coordinator)
    await coordinator.answer(GROUP, OTHER, "路人", "参与")
    await operate(coordinator, pending)

    assert (await coordinator.service.profile(GROUP, OTHER)).affection == 8
    assert any("本次表情生成失败" in message.extract_plain_text() for message in sent)


async def test_wheel_timer_starts_after_opening_message_is_sent(tmp_path: Path) -> None:
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
    assert pending.task is None
    release_opening.set()

    try:
        assert await start_task
        assert pending.task is not None
        await operate(coordinator, pending)
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

    await operate(coordinator, pending)
    assert (await coordinator.service.profile(GROUP, TRIGGER)).affection == 10
    assert (await coordinator.service.profile(GROUP, OTHER)).affection == 9
    assert GROUP not in coordinator.pending
    assert any("转盘好感 +1" in message.extract_plain_text() for message in sent)


def sent_text(sent: list[Message]) -> str:
    return "\n".join(message.extract_plain_text() for message in sent)


@pytest.mark.parametrize("filename", ["微笑.PNG", "挥手.gif"])
async def test_wheel_sends_mantou_sticker_directly(tmp_path: Path, filename: str) -> None:
    folder = tmp_path / "馒头 表情包"
    folder.mkdir()
    (folder / filename).write_bytes(b"local-sticker")
    (folder / "说明.txt").write_bytes(b"not an image")
    nested = folder / "子目录.png"
    nested.mkdir()
    (nested / "另一张.png").write_bytes(b"nested-sticker")
    coordinator = make_coordinator(tmp_path, rng=SequenceRng([7]), sticker_dir=str(folder))
    await give(coordinator.service, TRIGGER, 10)
    pending, sent, _ = await start(coordinator)

    await operate(coordinator, pending)

    award = sent[-1]
    assert award[0].data["qq"] == TRIGGER
    assert "抽中了「馒头表情包」" in award.extract_plain_text()
    images = [segment for segment in award if segment.type == "image"]
    assert len(images) == 1
    encoded = images[0].data["file"].removeprefix("base64://")
    assert base64.b64decode(encoded) == b"local-sticker"
    assert (await coordinator.service.profile(GROUP, TRIGGER)).affection == 10
    assert not coordinator.sticker_confirms


@pytest.mark.parametrize("directory", ["", "relative-stickers", "missing", "empty"])
async def test_wheel_handles_unavailable_mantou_stickers(tmp_path: Path, directory: str) -> None:
    (tmp_path / "empty").mkdir()
    if directory in {"missing", "empty"}:
        directory = str(tmp_path / directory)
    coordinator = make_coordinator(
        tmp_path, rng=SequenceRng([9, 50]), sticker_dir=directory
    )
    await give(coordinator.service, TRIGGER, 10)
    await give(coordinator.service, OTHER, 10)
    pending, sent, _ = await start(coordinator)
    assert await coordinator.answer(GROUP, OTHER, "路人", "参与") is True

    await operate(coordinator, pending)

    assert "抽中了「馒头表情包」，素材暂不可用" in sent_text(sent)
    assert not any(segment.type == "image" for message in sent for segment in message)
    assert (await coordinator.service.profile(GROUP, TRIGGER)).affection == 10
    assert (await coordinator.service.profile(GROUP, OTHER)).affection == 9
    assert GROUP not in coordinator.pending


async def test_wheel_sends_memes_to_all_winners_without_confirmation(tmp_path: Path) -> None:
    memes = FakeMemes()
    coordinator = make_coordinator(tmp_path, rng=SequenceRng([0, 0]), memes=memes)
    await give(coordinator.service, TRIGGER, 10)
    await give(coordinator.service, OTHER, 10)
    pending, sent, _ = await start(coordinator)
    assert await coordinator.answer(GROUP, OTHER, "路人", "参与") is True

    await operate(coordinator, pending)

    assert memes.calls == [(GROUP, TRIGGER), (GROUP, OTHER)]
    awards = [message for message in sent if any(segment.type == "image" for segment in message)]
    assert [message[0].data["qq"] for message in awards] == [TRIGGER, OTHER]
    assert sent_text(awards).count("馒头为你做了一张随机表情") == 2
    assert "确认" not in sent_text(sent)
    assert GROUP not in coordinator.pending
    assert await coordinator.answer(GROUP, TRIGGER, "触发者", "确认") is False
    assert await coordinator.answer(GROUP, OTHER, "路人", "不确认") is False
    assert (await coordinator.service.profile(GROUP, TRIGGER)).affection == 10
    assert (await coordinator.service.profile(GROUP, OTHER)).affection == 8


async def test_wheel_hides_meme_reward_when_group_switch_off(tmp_path: Path) -> None:
    memes = FakeMemes()
    coordinator = make_coordinator(
        tmp_path, rng=SequenceRng([0]), memes=memes, meme_groups=""
    )
    pending, sent, _ = await start(coordinator)

    body = sent[0].extract_plain_text()
    assert "馒头制作随机表情 7%" not in body
    assert "本群未开启表情包功能" in body
    assert "馒头表情包 3.2%" in body

    await operate(coordinator, pending)
    assert memes.calls == []
    assert "随机表情" not in sent_text(sent[1:])


async def test_wheel_meme_reward_returns_after_group_switch_on(tmp_path: Path) -> None:
    memes = FakeMemes()
    coordinator = make_coordinator(
        tmp_path, rng=SequenceRng([0]), memes=memes, meme_groups=""
    )
    await coordinator.service.set_meme_group_enabled(GROUP, True)

    pending, sent, _ = await start(coordinator)
    assert "馒头制作随机表情 7%" in sent[0].extract_plain_text()

    await operate(coordinator, pending)
    assert memes.calls == [(GROUP, TRIGGER)]


async def test_wheel_meme_asset_missing_reports_without_redraw(tmp_path: Path) -> None:
    memes = FakeMemes(missing_users={TRIGGER})
    resources = FakeResources()
    coordinator = make_coordinator(
        tmp_path, rng=SequenceRng([0]), memes=memes, resources=resources
    )
    pending, sent, _ = await start(coordinator)
    await operate(coordinator, pending)

    assert memes.calls == [(GROUP, TRIGGER)]
    assert resources.calls == 1
    assert "表情素材尚未下载完成，本次不重抽" in sent_text(sent)
    assert not any(segment.type == "image" for message in sent for segment in message)


@pytest.mark.parametrize("error", [MemeGenerationError("boom"), RuntimeError("boom")])
async def test_wheel_meme_generation_failure_reports(tmp_path: Path, error: Exception) -> None:
    memes = FakeMemes(generate_error=error)
    coordinator = make_coordinator(tmp_path, rng=SequenceRng([0]), memes=memes)
    pending, sent, _ = await start(coordinator)
    await operate(coordinator, pending)

    assert memes.calls == [(GROUP, TRIGGER)]
    assert "本次表情生成失败，不重抽" in sent_text(sent)
    assert not any(segment.type == "image" for message in sent for segment in message)


async def test_wheel_taozi_asks_before_charging(tmp_path: Path) -> None:
    stickers = FakeStickers()
    coordinator = make_coordinator(tmp_path, rng=SequenceRng([10]), stickers=stickers)
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
    coordinator = make_coordinator(tmp_path, rng=SequenceRng([10]), stickers=stickers)
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
        tmp_path, rng=SequenceRng([10]), stickers=FakeStickers()
    )
    await give(coordinator.service, TRIGGER, 10)
    pending, sent, _ = await start(coordinator)
    await operate(coordinator, pending)

    assert await coordinator.answer(GROUP, TRIGGER, "触发者", "要") is True

    assert (await coordinator.service.profile(GROUP, TRIGGER)).affection == 0
    assert any(segment.type == "image" for message in sent for segment in message)


async def test_wheel_taozi_decline_keeps_affection(tmp_path: Path) -> None:
    coordinator = make_coordinator(
        tmp_path, rng=SequenceRng([10]), stickers=FakeStickers()
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
        tmp_path, rng=SequenceRng([10]), stickers=FakeStickers()
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
        tmp_path, rng=SequenceRng([10]), stickers=FakeStickers()
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
    coordinator = make_coordinator(tmp_path, rng=SequenceRng([10]), stickers=stickers)
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
    coordinator = make_coordinator(tmp_path, rng=SequenceRng([10]), stickers=stickers)
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
    coordinator = make_coordinator(tmp_path, rng=SequenceRng([10]), stickers=stickers)
    await give(coordinator.service, TRIGGER, 20)
    pending, sent, _ = await start(coordinator)
    await operate(coordinator, pending)

    assert await coordinator.answer(GROUP, TRIGGER, "触发者", "要") is True

    assert (await coordinator.service.profile(GROUP, TRIGGER)).affection == 20
    assert "本次桃子表情包发送失败，不重抽" in sent_text(sent)
    assert not any(segment.type == "image" for message in sent for segment in message)


async def test_wheel_taozi_confirm_replaced_by_new_draw(tmp_path: Path) -> None:
    coordinator = make_coordinator(
        tmp_path, rng=SequenceRng([10, 10, 10]), stickers=FakeStickers()
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
    await operate(coordinator, pending2)

    new = coordinator.sticker_confirms[(GROUP, TRIGGER)]
    assert new is not old
    assert old.accepting is False
    with suppress(asyncio.CancelledError):
        await old.task
    assert (await coordinator.service.profile(GROUP, TRIGGER)).affection == 98
    new.task.cancel()


async def test_wheel_sends_meme_and_waits_for_taozi_exchange(tmp_path: Path) -> None:
    memes = FakeMemes()
    stickers = FakeStickers()
    coordinator = make_coordinator(
        tmp_path, rng=SequenceRng([0, 10]), memes=memes, stickers=stickers
    )
    await give(coordinator.service, OTHER, 20)
    pending, sent, _ = await start(coordinator)
    assert await coordinator.answer(GROUP, OTHER, "路人", "参与") is True

    await operate(coordinator, pending)

    assert memes.calls == [(GROUP, TRIGGER)]
    assert "馒头为你做了一张随机表情" in sent_text(sent)
    assert "回复「要」或「不要」" in sent_text(sent)
    assert stickers.picks == 0
    assert (await coordinator.service.profile(GROUP, OTHER)).affection == 18

    assert await coordinator.answer(GROUP, OTHER, "路人", "要") is True
    assert stickers.picks == 1
    assert (await coordinator.service.profile(GROUP, OTHER)).affection == 8
    assert not coordinator.sticker_confirms


async def test_wheel_panel_quota_counts(tmp_path: Path) -> None:
    quota = PanelQuotaStore(tmp_path / "panel_quota.json")
    coordinator = make_coordinator(tmp_path, rng=SequenceRng([45]), panel_quota=quota)
    pending, sent, _ = await start(coordinator)
    await operate(coordinator, pending)

    assert await quota.get(GROUP, TRIGGER) == 1
    body = "\n".join(message.extract_plain_text() for message in sent)
    assert "抽到角色面板次数 ×1" in body
    assert "当前共 1 次" in body
    assert "/角色名面板" in body


async def test_wheel_panel_rank_quota_counts(tmp_path: Path) -> None:
    quota = PanelRankQuotaStore(tmp_path / "panel_rank_quota.json")
    coordinator = make_coordinator(
        tmp_path, rng=SequenceRng([47]), panel_rank_quota=quota
    )
    pending, sent, _ = await start(coordinator)
    await operate(coordinator, pending)

    assert await quota.get(GROUP, TRIGGER) == 1
    body = "\n".join(message.extract_plain_text() for message in sent)
    assert "抽到群面板排名次数 ×1" in body
    assert "当前共 1 次" in body
    assert "/面板排名" in body


async def test_panel_quota_store_add_and_consume(tmp_path: Path) -> None:
    quota = PanelQuotaStore(tmp_path / "panel_quota.json")
    assert await quota.get(GROUP, TRIGGER) == 0
    assert await quota.add(GROUP, TRIGGER, 2) == 2
    assert await quota.consume(GROUP, TRIGGER, 1) == 1
    assert await quota.consume(GROUP, TRIGGER, 5) is None
    assert await quota.get(GROUP, TRIGGER) == 1


async def test_panel_rank_quota_store_is_independent(tmp_path: Path) -> None:
    panel = PanelQuotaStore(tmp_path / "panel_quota.json")
    rank = PanelRankQuotaStore(tmp_path / "panel_rank_quota.json")
    assert await rank.add(GROUP, TRIGGER, 1) == 1
    assert await panel.get(GROUP, TRIGGER) == 0
    assert await rank.consume(GROUP, TRIGGER, 1) == 0
    assert await rank.consume(GROUP, TRIGGER, 1) is None


async def test_wheel_wallpaper_sends_fanart_by_default(tmp_path: Path) -> None:
    wallpapers = FakeWallpapers()
    coordinator = make_coordinator(
        tmp_path, rng=SequenceRng([18]), wallpapers=wallpapers
    )
    pending, sent, _ = await start(coordinator)

    await operate(coordinator, pending)

    body = sent_text(sent)
    assert "抽到随机二创图" in body
    assert "原神 · 米游社「原神同人图」" in body
    assert wallpapers.calls == [(None, True)]
    images = [
        segment
        for message in sent
        for segment in message
        if segment.type == "image"
    ]
    assert images
    encoded = images[0].data["file"].removeprefix("base64://")
    assert base64.b64decode(encoded) == b"wallpaper:001"


async def test_wheel_wallpaper_failure_reports_without_redraw(tmp_path: Path) -> None:
    wallpapers = FakeWallpapers(error=WallpaperUnavailable("源站挂了"))
    coordinator = make_coordinator(
        tmp_path, rng=SequenceRng([18]), wallpapers=wallpapers
    )
    pending, sent, _ = await start(coordinator)

    await operate(coordinator, pending)

    assert "这次没取到壁纸，本次不重抽" in sent_text(sent)
    assert not any(segment.type == "image" for message in sent for segment in message)
    assert wallpapers.calls == [(None, True), (None, False)]


async def test_wheel_wallpaper_falls_back_to_official(tmp_path: Path) -> None:
    wallpapers = FakeWallpapers(
        collection="原神电脑官方壁纸第三弹",
        fanart_error=WallpaperUnavailable("二创源挂了"),
    )
    coordinator = make_coordinator(
        tmp_path, rng=SequenceRng([18]), wallpapers=wallpapers
    )
    pending, sent, _ = await start(coordinator)

    await operate(coordinator, pending)

    body = sent_text(sent)
    assert "二创图源没取到，改发官方壁纸" in body
    assert "原神 · 原神电脑官方壁纸第三弹" in body
    assert wallpapers.calls == [(None, True), (None, False)]


async def test_wheel_wallpaper_without_library_still_reports(tmp_path: Path) -> None:
    coordinator = make_coordinator(tmp_path, rng=SequenceRng([18]), wallpapers=None)
    pending, sent, _ = await start(coordinator)

    await operate(coordinator, pending)

    assert "这次没取到壁纸，本次不重抽" in sent_text(sent)


async def test_wheel_wallpaper_falls_back_to_url_when_image_fails(
    tmp_path: Path,
) -> None:
    wallpapers = FakeWallpapers()
    coordinator = make_coordinator(
        tmp_path, rng=SequenceRng([18]), wallpapers=wallpapers
    )
    pending, sent, _ = await start(coordinator)
    original = pending.send
    state = {"failed": False}

    async def flaky(message) -> None:
        if not state["failed"] and any(
            segment.type == "image"
            and segment.data["file"].startswith("base64://")
            for segment in message
        ):
            state["failed"] = True
            raise RuntimeError("payload too large")
        await original(message)

    pending.send = flaky

    await operate(coordinator, pending)

    assert state["failed"] is True
    sent_images = [
        segment.data["file"]
        for message in sent
        for segment in message
        if segment.type == "image"
    ]
    assert sent_images == ["https://example.com/a.png"]
