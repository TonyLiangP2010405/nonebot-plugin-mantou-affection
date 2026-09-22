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
from nonebot_plugin_mantou_affection.models import InteractionResult, Profile


def _run_interaction(
    profile: Profile,
    *,
    rng: random.Random,
    text_picker: Callable[[int], str | None] | None = None,
    now_timestamp: float = 100.0,
    daily_limit: int = 5,
) -> InteractionResult:
    return perform_interaction(
        profile,
        bot_name="馒头",
        today=date(2026, 9, 21),
        now_timestamp=now_timestamp,
        daily_limit=daily_limit,
        cooldown_seconds=0,
        affection_max=999,
        rng=rng,
        text_picker=text_picker,
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
        cooldown_seconds=60,
        affection_max=999,
        rng=random.Random(1),
    )
    assert result.accepted is False
    assert result.reason == "cooldown"
    assert result.wait_seconds == 40


def test_interaction_uses_text_picker_with_updated_affection() -> None:
    profile = Profile("1", affection=29)
    seen: list[int] = []

    def picker(affection: int) -> str | None:
        seen.append(affection)
        return "{bot}记住了这次互动。"

    result = _run_interaction(profile, rng=random.Random(1), text_picker=picker)
    assert result.accepted is True
    assert seen == [result.affection]
    assert result.affection == 30
    assert result.text == "馒头记住了这次互动。"
    assert "{bot}" not in result.text


def test_interaction_falls_back_to_builtin_copy() -> None:
    builtin = {text.format(bot="馒头") for text, _ in INTERACTIONS}
    plain = _run_interaction(Profile("1"), rng=random.Random(2))
    missing = _run_interaction(
        Profile("2"), rng=random.Random(2), text_picker=lambda affection: None
    )
    empty = _run_interaction(Profile("3"), rng=random.Random(2), text_picker=lambda affection: "")
    assert plain.text in builtin
    assert missing.text == plain.text
    assert empty.text == plain.text


def test_interaction_rewards_keep_builtin_distribution() -> None:
    rng = random.Random(7)
    reference = random.Random(7)
    profile = Profile("1")
    rounds = len(INTERACTIONS)
    deltas: list[int] = []
    for step in range(rounds):
        result = _run_interaction(
            profile,
            rng=rng,
            now_timestamp=100.0 + step,
            daily_limit=rounds,
            text_picker=lambda affection: "馒头记住了这次互动。",
        )
        deltas.append(result.delta)
    assert deltas == [reference.choice(INTERACTIONS)[1] for _ in range(rounds)]
    assert profile.affection == sum(deltas)


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
        cooldown_seconds=0,
        affection_max=999,
    )
    assert result.delta == -3
    assert result.affection == 2
    assert result.linked_points == 3
