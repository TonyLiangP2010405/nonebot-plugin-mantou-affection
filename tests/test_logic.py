import random
from datetime import date
from typing import Callable

import pytest

from nonebot_plugin_mantou_affection.logic import (
    INTERACTIONS,
    adjust_affection,
    apply_link_reward,
    level_for,
    perform_interaction,
    progress_text,
    snapshot_for,
)
from nonebot_plugin_mantou_affection.models import InteractionResult, LinkRewardResult, Profile


class FixedChoice(random.Random):
    """让 choice 固定返回指定 reward 的那一档，避免依赖随机种子。"""

    def __init__(self, reward: int):
        super().__init__(0)
        self.reward = reward

    def choice(self, seq):
        return next(item for item in seq if item[1] == self.reward)


IDLE_TEXT = next(text for text, reward in INTERACTIONS if reward == 0)
NEGATIVE_COPY = "馒头被你闹得躲进蒸笼深处，只留给你一个背影。"
GAIN_HINT = "今天的好感已经拿满啦，明天再来吧。"


def _run_interaction(
    profile: Profile,
    *,
    rng: random.Random,
    text_picker: Callable[[int, int], str | None] | None = None,
    now_timestamp: float = 100.0,
    daily_limit: int = 5,
    daily_gain_limit: int = 999,
) -> InteractionResult:
    return perform_interaction(
        profile,
        bot_name="馒头",
        today=date(2026, 9, 21),
        now_timestamp=now_timestamp,
        daily_limit=daily_limit,
        daily_gain_limit=daily_gain_limit,
        cooldown_seconds=0,
        affection_max=999,
        rng=rng,
        text_picker=text_picker,
    )


def _run_link_reward(
    profile: Profile,
    *,
    source: str = "nonebot_plugin_taozi",
    reward: int = 2,
    today: date = date(2026, 9, 21),
    now_timestamp: float = 100.0,
    daily_limit: int = 10,
    daily_gain_limit: int = 999,
) -> LinkRewardResult:
    return apply_link_reward(
        profile,
        source=source,
        reward=reward,
        today=today,
        now_timestamp=now_timestamp,
        daily_limit=daily_limit,
        daily_gain_limit=daily_gain_limit,
        cooldown_seconds=0,
        affection_max=999,
    )


@pytest.mark.parametrize(
    ("score", "number", "title"),
    [
        (0, 1, "初次见面"),
        (9, 1, "初次见面"),
        (10, 2, "有点眼熟"),
        (249, 6, "特别偏爱"),
        (250, 7, "心照不宣"),
    ],
)
def test_level_boundaries(score: int, number: int, title: str) -> None:
    level = level_for(score)
    assert level.number == number
    assert level.title == title


def test_progress_text_at_max_level() -> None:
    assert progress_text(999) == "已达到最高称号"


@pytest.mark.parametrize(
    ("score", "band"),
    [(0, "neutral"), (30, "warm"), (60, "close"), (100, "flirty"), (160, "intimate")],
)
def test_snapshot_bands(score: int, band: str) -> None:
    snapshot = snapshot_for(score)
    assert snapshot.affection == score
    assert snapshot.band == band


def test_interaction_cooldown_and_daily_limit() -> None:
    profile = Profile("1")
    first = perform_interaction(
        profile,
        bot_name="馒头",
        today=date(2026, 9, 21),
        now_timestamp=100.0,
        daily_limit=1,
        daily_gain_limit=999,
        cooldown_seconds=60,
        affection_max=999,
        rng=random.Random(1),
    )
    second = perform_interaction(
        profile,
        bot_name="馒头",
        today=date(2026, 9, 21),
        now_timestamp=101.0,
        daily_limit=1,
        daily_gain_limit=999,
        cooldown_seconds=60,
        affection_max=999,
        rng=random.Random(1),
    )
    assert first.accepted is True
    assert second.accepted is False
    assert second.reason == "daily_limit"


def test_interaction_reports_cooldown() -> None:
    profile = Profile(
        "1",
        interaction_date="2026-09-21",
        interaction_count=1,
        last_interaction_at=100.0,
    )
    result = perform_interaction(
        profile,
        bot_name="馒头",
        today=date(2026, 9, 21),
        now_timestamp=120.0,
        daily_limit=5,
        daily_gain_limit=999,
        cooldown_seconds=60,
        affection_max=999,
        rng=random.Random(1),
    )
    assert result.accepted is False
    assert result.reason == "cooldown"
    assert result.wait_seconds == 40


def test_interaction_uses_text_picker_with_updated_affection() -> None:
    assert random.Random(1).choice(INTERACTIONS)[1] == 1
    profile = Profile("1", affection=29)
    seen: list[tuple[int, int]] = []

    def picker(affection: int, reward: int) -> str | None:
        seen.append((affection, reward))
        return "{bot}记住了这次互动。"

    result = _run_interaction(profile, rng=random.Random(1), text_picker=picker)
    assert result.accepted is True
    assert seen == [(30, 1)]
    assert result.affection == 30
    assert result.text == "馒头记住了这次互动。"
    assert "{bot}" not in result.text


def test_interaction_falls_back_to_builtin_copy() -> None:
    builtin = {text.format(bot="馒头") for text, _ in INTERACTIONS}
    plain = _run_interaction(Profile("1"), rng=random.Random(2))
    missing = _run_interaction(
        Profile("2"), rng=random.Random(2), text_picker=lambda affection, reward: None
    )
    empty = _run_interaction(
        Profile("3"), rng=random.Random(2), text_picker=lambda affection, reward: ""
    )
    assert plain.text in builtin
    assert missing.text == plain.text
    assert empty.text == plain.text


def test_interaction_pool_contains_one_negative_reward() -> None:
    rewards = [reward for _, reward in INTERACTIONS]
    assert sorted(rewards) == [-1, 0, 1, 1, 2, 2, 3]


def test_interaction_rewards_keep_builtin_distribution() -> None:
    rng = random.Random(7)
    reference = random.Random(7)
    profile = Profile("1", affection=10)
    rounds = len(INTERACTIONS)
    deltas: list[int] = []
    for step in range(rounds):
        result = _run_interaction(
            profile,
            rng=rng,
            now_timestamp=100.0 + step,
            daily_limit=rounds,
            text_picker=lambda affection, reward: "馒头记住了这次互动。",
        )
        deltas.append(result.delta)
    assert deltas == [reference.choice(INTERACTIONS)[1] for _ in range(rounds)]
    assert profile.affection == 10 + sum(deltas)


def test_interaction_negative_reward_leaves_gain_points_alone() -> None:
    seed = 0
    assert random.Random(seed).choice(INTERACTIONS)[1] == -1
    profile = Profile("1", affection=5, gain_date="2026-09-21", gain_points=2)
    result = _run_interaction(profile, rng=random.Random(seed), daily_gain_limit=3)
    assert result.accepted is True
    assert result.delta == -1
    assert result.affection == 4
    assert profile.gain_date == "2026-09-21"
    assert profile.gain_points == 2


def test_interaction_negative_reward_keeps_affection_at_zero() -> None:
    profile = Profile("1")
    result = _run_interaction(profile, rng=random.Random(0), daily_gain_limit=3)
    assert result.delta == 0
    assert result.affection == 0
    assert profile.gain_points == 0


def test_interaction_gain_is_capped_by_daily_gain_limit() -> None:
    seed = 5
    assert random.Random(seed).choice(INTERACTIONS)[1] == 3
    profile = Profile("1")
    result = _run_interaction(profile, rng=random.Random(seed), daily_gain_limit=1)
    assert result.accepted is True
    assert result.delta == 1
    assert result.affection == 1
    assert profile.gain_date == "2026-09-21"
    assert profile.gain_points == 1


def test_interaction_without_gain_budget_still_counts() -> None:
    profile = Profile("1", affection=5, gain_date="2026-09-21", gain_points=3)
    result = _run_interaction(profile, rng=random.Random(5), daily_gain_limit=3)
    assert result.accepted is True
    assert result.reason == "ok"
    assert result.delta == 0
    assert result.affection == 5
    assert result.remaining == 4
    assert profile.interaction_count == 1
    assert profile.gain_points == 3


def test_interaction_gain_budget_resets_next_day() -> None:
    profile = Profile("1", gain_date="2026-09-20", gain_points=3)
    result = _run_interaction(profile, rng=random.Random(5), daily_gain_limit=3)
    assert profile.gain_date == "2026-09-21"
    assert profile.gain_points == 3
    assert result.delta == 3


def test_adjustment_is_clamped() -> None:
    profile = Profile("1", affection=5)
    assert adjust_affection(profile, -10, 999, 1.0) == -5
    assert profile.affection == 0
    assert adjust_affection(profile, 2000, 999, 2.0) == 999
    assert profile.affection == 999


def test_link_reward_cooldown_limit_and_daily_reset() -> None:
    profile = Profile("1")
    first = apply_link_reward(
        profile,
        source="nonebot_plugin_taozi",
        reward=2,
        today=date(2026, 9, 21),
        now_timestamp=100.0,
        daily_limit=3,
        daily_gain_limit=999,
        cooldown_seconds=60,
        affection_max=999,
    )
    cooldown = apply_link_reward(
        profile,
        source="nonebot_plugin_taozi",
        reward=2,
        today=date(2026, 9, 21),
        now_timestamp=120.0,
        daily_limit=3,
        daily_gain_limit=999,
        cooldown_seconds=60,
        affection_max=999,
    )
    capped = apply_link_reward(
        profile,
        source="nonebot_plugin_miao",
        reward=2,
        today=date(2026, 9, 21),
        now_timestamp=121.0,
        daily_limit=3,
        daily_gain_limit=999,
        cooldown_seconds=60,
        affection_max=999,
    )
    next_day = apply_link_reward(
        profile,
        source="nonebot_plugin_taozi",
        reward=2,
        today=date(2026, 9, 22),
        now_timestamp=200.0,
        daily_limit=3,
        daily_gain_limit=999,
        cooldown_seconds=60,
        affection_max=999,
    )
    assert first.delta == 2
    assert cooldown.reason == "cooldown"
    assert capped.delta == 1
    assert capped.linked_points == 3
    assert next_day.delta == 2
    assert next_day.linked_points == 2


def test_link_reward_can_reduce_affection_and_counts_absolute_budget() -> None:
    profile = Profile("1", affection=5)
    result = apply_link_reward(
        profile,
        source="some_plugin:bad_action",
        reward=-3,
        today=date(2026, 9, 21),
        now_timestamp=100.0,
        daily_limit=10,
        daily_gain_limit=999,
        cooldown_seconds=0,
        affection_max=999,
    )
    assert result.delta == -3
    assert result.affection == 2
    assert result.linked_points == 3


def test_link_reward_positive_gain_is_capped_by_daily_gain_limit() -> None:
    profile = Profile("1")
    result = _run_link_reward(profile, reward=2, daily_gain_limit=1)
    assert result.accepted is True
    assert result.delta == 1
    assert result.affection == 1
    assert profile.gain_date == "2026-09-21"
    assert profile.gain_points == 1
    assert profile.linked_points == 1


def test_link_reward_applies_link_and_gain_budgets_together() -> None:
    by_link = Profile("1")
    link_capped = _run_link_reward(by_link, reward=2, daily_limit=1, daily_gain_limit=999)
    by_gain = Profile("2")
    gain_capped = _run_link_reward(by_gain, reward=2, daily_limit=999, daily_gain_limit=1)

    assert link_capped.delta == 1
    assert by_link.linked_points == 1
    assert by_link.gain_points == 1
    assert gain_capped.delta == 1
    assert by_gain.linked_points == 1
    assert by_gain.gain_points == 1


def test_link_reward_negative_change_ignores_daily_gain_limit() -> None:
    profile = Profile("1", affection=5, gain_date="2026-09-21", gain_points=3)
    result = _run_link_reward(
        profile, source="some_plugin:bad_action", reward=-3, daily_gain_limit=3
    )
    assert result.delta == -3
    assert result.affection == 2
    assert profile.gain_points == 3


def test_link_reward_gain_budget_resets_next_day() -> None:
    profile = Profile(
        "1",
        gain_date="2026-09-20",
        gain_points=3,
        linked_date="2026-09-20",
        linked_points=3,
    )
    result = _run_link_reward(
        profile, today=date(2026, 9, 22), now_timestamp=200.0, daily_gain_limit=3
    )
    assert profile.gain_date == "2026-09-22"
    assert profile.gain_points == 2
    assert profile.linked_points == 2
    assert result.delta == 2


def test_interaction_pool_has_single_idle_entry() -> None:
    assert [text for text, reward in INTERACTIONS if reward == 0] == [IDLE_TEXT]


def test_interaction_idle_draw_uses_pooled_copy() -> None:
    profile = Profile("1", affection=5)
    seen: list[tuple[int, int]] = []

    def picker(affection: int, reward: int) -> str | None:
        seen.append((affection, reward))
        return "不该出现的阶段文案"

    result = _run_interaction(profile, rng=FixedChoice(0), text_picker=picker)

    assert result.accepted is True
    assert result.delta == 0
    assert result.affection == 5
    assert seen == []
    assert result.text == IDLE_TEXT.format(bot="馒头")
    assert "{bot}" not in result.text


def test_interaction_gain_hint_when_positive_draw_is_capped() -> None:
    profile = Profile("1", affection=5, gain_date="2026-09-21", gain_points=3)
    seen: list[tuple[int, int]] = []

    def picker(affection: int, reward: int) -> str:
        seen.append((affection, reward))
        return "馒头把今天攒下来的桃气分给了你一点。"

    result = _run_interaction(
        profile, rng=FixedChoice(3), text_picker=picker, daily_gain_limit=3
    )

    assert result.delta == 0
    assert result.affection == 5
    assert profile.gain_points == 3
    assert seen == [(5, 0)]
    assert result.text == f"馒头把今天攒下来的桃气分给了你一点。\n{GAIN_HINT}"


def test_interaction_negative_draw_is_immune_when_gain_limit_used_up() -> None:
    profile = Profile("1", affection=5, gain_date="2026-09-21", gain_points=3)
    seen: list[tuple[int, int]] = []

    def picker(affection: int, reward: int) -> str:
        seen.append((affection, reward))
        return "馒头从蒸笼边探出头，认真听你说完了今天的事。"

    result = _run_interaction(
        profile, rng=FixedChoice(-1), text_picker=picker, daily_gain_limit=3
    )

    assert result.delta == 0
    assert result.affection == 5
    assert profile.gain_points == 3
    assert seen == [(5, 0)]
    assert result.text == f"馒头从蒸笼边探出头，认真听你说完了今天的事。\n{GAIN_HINT}"


def test_interaction_negative_draw_still_applies_with_gain_room() -> None:
    profile = Profile("1", affection=5, gain_date="2026-09-21", gain_points=2)
    seen: list[tuple[int, int]] = []

    def picker(affection: int, reward: int) -> str | None:
        seen.append((affection, reward))
        return NEGATIVE_COPY

    result = _run_interaction(
        profile, rng=FixedChoice(-1), text_picker=picker, daily_gain_limit=3
    )

    assert result.delta == -1
    assert result.affection == 4
    assert profile.gain_points == 2
    assert seen == [(4, -1)]
    assert result.text == NEGATIVE_COPY
    assert GAIN_HINT not in result.text


def test_interaction_idle_draw_ignores_gain_budget() -> None:
    profile = Profile("1", affection=5, gain_date="2026-09-21", gain_points=3)
    result = _run_interaction(profile, rng=FixedChoice(0), daily_gain_limit=3)

    assert result.delta == 0
    assert result.text == IDLE_TEXT.format(bot="馒头")
    assert GAIN_HINT not in result.text
