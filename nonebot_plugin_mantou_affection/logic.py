from __future__ import annotations

import random
from datetime import date
from typing import Callable

from .models import AffectionSnapshot, InteractionResult, Level, LinkRewardResult, Profile

LEVELS: tuple[tuple[int, str], ...] = (
    (0, "初次见面"),
    (10, "有点眼熟"),
    (30, "愿意靠近"),
    (60, "悄悄在意"),
    (100, "暧昧升温"),
    (160, "特别偏爱"),
    (250, "心照不宣"),
)

INTERACTIONS: tuple[tuple[str, int], ...] = (
    ("{bot}从蒸笼边探出头，认真听你说完了今天的事。", 1),
    ("{bot}晃了晃脑袋，假装没看见，其实嘴角偷偷翘起来了。", 1),
    ("你轻轻拍了拍{bot}，收获了一只热乎乎的贴贴。", 2),
    ("{bot}把今天攒下来的桃气分给了你一点。", 2),
    ("{bot}在你的群昵称旁边画了一颗小桃心。", 3),
    ("{bot}正在发呆，没有听清，但还是礼貌地向你点了点头。", 0),
)


def level_for(affection: int) -> Level:
    score = max(0, affection)
    index = 0
    for candidate, (threshold, _) in enumerate(LEVELS):
        if score < threshold:
            break
        index = candidate
    threshold, title = LEVELS[index]
    next_threshold = LEVELS[index + 1][0] if index + 1 < len(LEVELS) else None
    return Level(index + 1, title, threshold, next_threshold)


def progress_text(affection: int) -> str:
    level = level_for(affection)
    if level.next_threshold is None:
        return "已达到最高称号"
    return f"距离下一称号还差 {level.next_threshold - affection} 点"


def snapshot_for(affection: int) -> AffectionSnapshot:
    level = level_for(affection)
    if level.number <= 2:
        band = "neutral"
    elif level.number == 3:
        band = "warm"
    elif level.number == 4:
        band = "close"
    elif level.number == 5:
        band = "flirty"
    else:
        band = "intimate"
    return AffectionSnapshot(
        affection=max(0, affection),
        level=level.number,
        title=level.title,
        band=band,
    )


def perform_interaction(
    profile: Profile,
    *,
    bot_name: str,
    today: date,
    now_timestamp: float,
    daily_limit: int,
    daily_gain_limit: int,
    cooldown_seconds: int,
    affection_max: int,
    rng: random.Random,
    text_picker: Callable[[int], str | None] | None = None,
) -> InteractionResult:
    today_text = today.isoformat()
    if profile.interaction_date != today_text:
        profile.interaction_date = today_text
        profile.interaction_count = 0
        profile.last_interaction_at = 0.0
    if profile.gain_date != today_text:
        profile.gain_date = today_text
        profile.gain_points = 0

    remaining = max(0, daily_limit - profile.interaction_count)
    if remaining == 0:
        return InteractionResult(
            False,
            "daily_limit",
            "",
            0,
            0,
            0,
            profile.affection,
            level_for(profile.affection),
        )

    elapsed = now_timestamp - profile.last_interaction_at
    if profile.last_interaction_at and elapsed < cooldown_seconds:
        wait_seconds = max(1, int(cooldown_seconds - elapsed + 0.999))
        return InteractionResult(
            False,
            "cooldown",
            "",
            0,
            remaining,
            wait_seconds,
            profile.affection,
            level_for(profile.affection),
        )

    text, reward = rng.choice(INTERACTIONS)
    reward = min(reward, max(0, daily_gain_limit - profile.gain_points))
    before = profile.affection
    profile.affection = min(affection_max, profile.affection + reward)
    profile.interaction_count += 1
    profile.last_interaction_at = now_timestamp
    profile.updated_at = now_timestamp
    profile.gain_points += profile.affection - before
    picked = text_picker(profile.affection) if text_picker is not None else None
    reply = picked.replace("{bot}", bot_name) if picked else text.format(bot=bot_name)
    return InteractionResult(
        True,
        "ok",
        reply,
        profile.affection - before,
        max(0, daily_limit - profile.interaction_count),
        0,
        profile.affection,
        level_for(profile.affection),
    )


def adjust_affection(profile: Profile, delta: int, affection_max: int, now_timestamp: float) -> int:
    before = profile.affection
    profile.affection = min(affection_max, max(0, profile.affection + delta))
    profile.updated_at = now_timestamp
    return profile.affection - before


def apply_link_reward(
    profile: Profile,
    *,
    source: str,
    reward: int,
    today: date,
    now_timestamp: float,
    daily_limit: int,
    daily_gain_limit: int,
    cooldown_seconds: int,
    affection_max: int,
) -> LinkRewardResult:
    today_text = today.isoformat()
    if profile.linked_date != today_text:
        profile.linked_date = today_text
        profile.linked_points = 0
        profile.plugin_last_awards = {}
    if profile.gain_date != today_text:
        profile.gain_date = today_text
        profile.gain_points = 0

    if daily_limit <= 0 or profile.linked_points >= daily_limit:
        return LinkRewardResult(
            False, "daily_limit", 0, profile.affection, profile.linked_points
        )

    plugin_last_awards = profile.plugin_last_awards or {}
    last_award = plugin_last_awards.get(source, 0.0)
    if last_award and now_timestamp - last_award < cooldown_seconds:
        return LinkRewardResult(False, "cooldown", 0, profile.affection, profile.linked_points)

    available = daily_limit - profile.linked_points
    requested = max(-available, min(available, reward))
    if requested >= 0:
        remaining_gain = max(0, daily_gain_limit - profile.gain_points)
        actual_reward = min(requested, affection_max - profile.affection, remaining_gain)
    else:
        actual_reward = max(requested, -profile.affection)
    plugin_last_awards[source] = now_timestamp
    profile.plugin_last_awards = plugin_last_awards
    profile.linked_points += abs(actual_reward)
    profile.affection += actual_reward
    if actual_reward > 0:
        profile.gain_points += actual_reward
    profile.updated_at = now_timestamp
    return LinkRewardResult(True, "ok", actual_reward, profile.affection, profile.linked_points)
