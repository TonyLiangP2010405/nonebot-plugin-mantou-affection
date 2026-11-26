from __future__ import annotations

import math
import random
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from nonebot import logger
from nonebot.adapters.onebot.v11 import MessageSegment

from .config import Config
from .copywriting import AffectionTextLibrary
from .logic import (
    adjust_affection,
    apply_link_reward,
    perform_interaction,
    perform_poke,
    protect_deduction,
    snapshot_for,
)
from .models import (
    AffectionSnapshot,
    InteractionResult,
    LinkRewardResult,
    PokeResult,
    Profile,
    RankingEntry,
)
from .parsing import parse_group_ids
from .storage import AffectionStore

EVENT_WHEEL_PROBABILITY_SETTING = "event_wheel_probability"
WHEEL_PROBABILITY_SETTING = "wheel_probability"
BET_PROBABILITY_SETTING = "bet_probability"
FIND_CHAR_POKE_CHANCE_SETTING = "find_char_poke_chance"
EVENT_LIMIT_ENABLED_SETTING = "event_limit_enabled"
EVENT_LIMIT_PER_WINDOW_SETTING = "event_limit_per_window"
# 旧版本把次数存在 event_limit_per_hour 下, 升级后继续沿用这份值。
LEGACY_EVENT_LIMIT_PER_HOUR_SETTING = "event_limit_per_hour"
EVENT_LIMIT_HISTORY_SETTING = "event_limit_history"
EVENT_LIMIT_WINDOW_HOURS = 2
EVENT_LIMIT_WINDOW_SECONDS = EVENT_LIMIT_WINDOW_HOURS * 3600
EVENT_LIMIT_MIN_PER_WINDOW = 1
EVENT_LIMIT_MAX_PER_WINDOW = 100
EVENT_LIMIT_HISTORY_KEEP = 128
MEME_GROUPS_SETTING = "meme_enabled_groups"
NEWBIE_GIFT_REPLY = " 扣分安慰：送你一张桃子表情包「{caption}」"
AMBIENT_SCENE = "mantou.ambient"
# 12 个单事件概率与「小动作升级随机事件」的比例在 0.59 退场
# 群消息触发改成统一的「事件转盘」 这些键只在清理运行时覆盖时顺带删除
LEGACY_PROBABILITY_SETTINGS: tuple[str, ...] = (
    "ambient_probability",
    "ambient_event_ratio",
    "find_char_group_chance",
    "pk_probability",
    "group_quiz_probability",
    "teyvat_quiz_probability",
    "hsr_quiz_probability",
    "zzz_quiz_probability",
    "petit_planet_quiz_probability",
    "fill_blank_probability",
    "whoami_probability",
)


@dataclass(frozen=True)
class ProbabilityField:
    """一项支持运行时覆盖的触发概率：存储键、展示名和配置默认值字段。"""

    key: str
    label: str
    config_attr: str


@dataclass(frozen=True)
class ProbabilityState:
    """一项概率的当前值、配置默认值以及是否被运行时覆盖。"""

    key: str
    label: str
    value: float
    default: float
    overridden: bool


@dataclass(frozen=True)
class ProbabilityChange:
    """整体缩放时单项概率的变化前后值，用于回复里逐项展示。"""

    label: str
    before: float
    after: float


@dataclass(frozen=True)
class EventLimitState:
    """事件限流的当前状态：开关、每群每 2 小时上限、本群已用与剩余次数。"""

    enabled: bool
    per_window: int
    used: int
    remaining: int


def hour_in_active_window(hour: int, start: int, end: int) -> bool:
    """判断整点 hour 是否落在 [start, end) 里；start 大于 end 时跨零点，相等视为全天开放。"""

    if start == end:
        return True
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def _clean_event_history(raw: Any, now: float) -> dict[str, list[float]]:
    """清洗事件触发记录：丢掉过期、非数值和未来时间戳，只留统计窗口内的。"""

    if not isinstance(raw, dict):
        return {}
    cutoff = now - EVENT_LIMIT_WINDOW_SECONDS
    history: dict[str, list[float]] = {}
    for group_id, stamps in raw.items():
        if not isinstance(stamps, list):
            continue
        fresh = [
            float(stamp)
            for stamp in stamps
            if isinstance(stamp, (int, float))
            and not isinstance(stamp, bool)
            and cutoff < float(stamp) <= now
        ]
        if fresh:
            history[str(group_id)] = fresh[-EVENT_LIMIT_HISTORY_KEEP:]
    return history


PROBABILITY_FIELDS: tuple[ProbabilityField, ...] = (
    ProbabilityField(
        EVENT_WHEEL_PROBABILITY_SETTING,
        "事件转盘",
        "mantou_affection_event_wheel_chance",
    ),
    ProbabilityField(WHEEL_PROBABILITY_SETTING, "馒头转盘", "mantou_affection_wheel_chance"),
)


def clamp_probability(value: float) -> float:
    """把概率夹到 0~1，非有限数直接拒绝。"""

    number = float(value)
    if not math.isfinite(number):
        raise ValueError("概率必须是有限数值")
    return min(1.0, max(0.0, number))


def normalize_nickname(value: str, fallback: str) -> str:
    nickname = " ".join(value.replace("\n", " ").split()).strip()
    return (nickname or fallback)[:24]


class AffectionService:
    def __init__(
        self,
        store: AffectionStore,
        config: Config,
        *,
        text_library: AffectionTextLibrary | None = None,
        rng: random.Random | None = None,
        stickers=None,
    ):
        self.store = store
        self.config = config
        self.text_library = text_library
        self.rng = rng or random.Random()
        self.stickers = stickers
        try:
            self.timezone = ZoneInfo(config.mantou_affection_timezone)
        except ZoneInfoNotFoundError:
            self.timezone = ZoneInfo("Asia/Shanghai")

    def _now(self) -> datetime:
        return datetime.now(self.timezone)

    async def profile(self, group_id: str, user_id: str) -> Profile:
        return await self.store.get_profile(group_id, user_id)

    async def snapshot(self, group_id: str, user_id: str) -> AffectionSnapshot:
        profile = await self.profile(group_id, user_id)
        return snapshot_for(profile.affection)

    async def interact(self, group_id: str, user_id: str, nickname: str) -> InteractionResult:
        now = self._now()
        nickname = normalize_nickname(nickname, user_id)
        text_picker = self._interact_text_picker()

        def update(profile: Profile) -> InteractionResult:
            return perform_interaction(
                profile,
                bot_name=self.config.mantou_affection_bot_name,
                today=now.date(),
                now_timestamp=now.timestamp(),
                daily_limit=self.config.mantou_affection_interaction_limit,
                daily_gain_limit=self.config.mantou_affection_daily_gain_limit,
                cooldown_seconds=self.config.mantou_affection_interaction_cooldown,
                affection_max=self.config.mantou_affection_max,
                rng=self.rng,
                text_picker=text_picker,
            )

        return await self.store.update_profile(group_id, user_id, nickname, update)

    def _interact_text_picker(self) -> Callable[[int, int], str | None] | None:
        library = self.text_library
        if library is None:
            return None

        def pick(affection: int, reward: int) -> str | None:
            scene = "mantou.interact.negative" if reward < 0 else "mantou.interact"
            return library.pick(scene, snapshot_for(affection))

        return pick

    def _poke_text_picker(self) -> Callable[[str, int], str | None] | None:
        library = self.text_library
        if library is None:
            return None

        def pick(scene: str, affection: int) -> str | None:
            return library.pick(scene, snapshot_for(affection))

        return pick

    async def poke(self, group_id: str, user_id: str, nickname: str = "") -> PokeResult:
        now = self._now()
        nickname = normalize_nickname(nickname, user_id)
        text_picker = self._poke_text_picker()

        def update(profile: Profile) -> PokeResult:
            return perform_poke(
                profile,
                today=now.date(),
                now_timestamp=now.timestamp(),
                bot_name=self.config.mantou_affection_bot_name,
                daily_gain_limit=self.config.mantou_affection_daily_gain_limit,
                affection_max=self.config.mantou_affection_max,
                text_picker=text_picker,
            )

        return await self.store.update_profile(group_id, user_id, nickname, update)

    async def find_char_poke_chance(self, default: float | None = None) -> float:
        """戳一戳路径的找字小游戏概率；群消息路径已经并入事件转盘。"""

        fallback = (
            self.config.mantou_affection_poke_find_char_chance if default is None else default
        )
        return await self._stored_probability(FIND_CHAR_POKE_CHANCE_SETTING, fallback)

    async def bet_probability(self) -> float:
        """外部插件调用 maybe_start_bet 时的开局概率；群消息触发统一走事件转盘。"""

        return await self._stored_probability(
            BET_PROBABILITY_SETTING, self.config.mantou_affection_bet_chance
        )

    async def event_wheel_triggered(self) -> bool:
        """群消息触发事件转盘的判定：只看事件转盘自己的概率。"""

        chance = await self.event_wheel_probability()
        if chance <= 0:
            return False
        return self.rng.random() < chance

    async def event_wheel_probability(self) -> float:
        """事件转盘的触发概率，运行时用 /馒头事件转盘概率 覆盖过就优先用它。"""

        return await self._stored_probability(
            EVENT_WHEEL_PROBABILITY_SETTING,
            self.config.mantou_affection_event_wheel_chance,
        )

    async def event_wheel_probability_override(self) -> float | None:
        """读取运行时覆盖的事件转盘概率，没覆盖过返回 None。"""

        stored: Any = await self.store.get_setting(EVENT_WHEEL_PROBABILITY_SETTING, None)
        if isinstance(stored, bool) or not isinstance(stored, (int, float)):
            return None
        return min(1.0, max(0.0, float(stored)))

    async def set_event_wheel_probability(self, value: float) -> None:
        await self.store.set_setting(EVENT_WHEEL_PROBABILITY_SETTING, float(value))

    async def wheel_triggered(self, group_id: str, user_id: str) -> bool:
        """馒头转盘的独立触发判定：好感度 > 0 且掷中自己的概率。"""

        profile = await self.store.get_profile(group_id, user_id)
        if profile.affection <= 0:
            return False
        chance = await self.wheel_probability()
        if chance <= 0:
            return False
        return self.rng.random() < chance

    async def wheel_probability(self) -> float:
        """馒头转盘的独立触发概率，运行时用 /馒头转盘概率 覆盖过就优先用它。"""

        return await self._stored_probability(
            WHEEL_PROBABILITY_SETTING, self.config.mantou_affection_wheel_chance
        )

    async def wheel_probability_override(self) -> float | None:
        """读取运行时覆盖的转盘概率，没覆盖过返回 None。"""

        stored: Any = await self.store.get_setting(WHEEL_PROBABILITY_SETTING, None)
        if isinstance(stored, bool) or not isinstance(stored, (int, float)):
            return None
        return min(1.0, max(0.0, float(stored)))

    async def set_wheel_probability(self, value: float) -> None:
        await self.store.set_setting(WHEEL_PROBABILITY_SETTING, float(value))

    def activity_hours_open(self, *, now: datetime | None = None) -> bool:
        """当前是否处于随机活动的开启时段；转盘和主动命令不受这个时段限制。"""

        if not self.config.mantou_affection_active_hours_enabled:
            return True
        moment = self._now() if now is None else now
        return hour_in_active_window(
            moment.hour,
            self.config.mantou_affection_active_hours_start,
            self.config.mantou_affection_active_hours_end,
        )

    async def event_limit_enabled(self) -> bool:
        """事件限流开关：运行时用 /馒头限制开关 覆盖过就优先用它。"""

        stored: Any = await self.store.get_setting(EVENT_LIMIT_ENABLED_SETTING, None)
        if isinstance(stored, bool):
            return stored
        return bool(self.config.mantou_affection_event_limit_enabled)

    async def set_event_limit_enabled(self, enabled: bool) -> None:
        await self.store.set_setting(EVENT_LIMIT_ENABLED_SETTING, bool(enabled))

    async def event_limit_per_window(self) -> int:
        """每群每 2 小时最多触发几次活动，运行时覆盖过就优先用它。"""

        fallback = await self._stored_int(
            LEGACY_EVENT_LIMIT_PER_HOUR_SETTING,
            self.config.mantou_affection_event_limit_per_window,
        )
        stored = await self._stored_int(EVENT_LIMIT_PER_WINDOW_SETTING, fallback)
        return min(
            EVENT_LIMIT_MAX_PER_WINDOW, max(EVENT_LIMIT_MIN_PER_WINDOW, stored)
        )

    async def set_event_limit_per_window(self, value: int) -> None:
        await self.store.set_setting(EVENT_LIMIT_PER_WINDOW_SETTING, int(value))

    async def event_limit_used(self, group_id: str, *, now: float | None = None) -> int:
        """本群当前统计窗口（2 小时）内已经触发的活动次数。"""

        moment = time.time() if now is None else now
        raw: Any = await self.store.get_setting(EVENT_LIMIT_HISTORY_SETTING, {})
        return len(_clean_event_history(raw, moment).get(str(group_id), ()))

    async def event_limit_state(
        self, group_id: str, *, now: float | None = None
    ) -> EventLimitState:
        """读取本群的事件限流状态，供状态命令展示。"""

        enabled = await self.event_limit_enabled()
        per_window = await self.event_limit_per_window()
        used = await self.event_limit_used(group_id, now=now)
        return EventLimitState(
            enabled=enabled,
            per_window=per_window,
            used=used,
            remaining=max(0, per_window - used),
        )

    async def event_limit_allows(self, group_id: str) -> bool:
        """限流关闭时永远放行；开启时本群这个窗口还有额度才放行。"""

        if not await self.event_limit_enabled():
            return True
        return await self.event_limit_used(group_id) < await self.event_limit_per_window()

    async def record_event_trigger(
        self, group_id: str, *, now: float | None = None
    ) -> int:
        """记录一次真实触发的活动，返回本群当前窗口已用次数。

        限流关闭时不记账，这样刚打开开关时本群从零开始计算额度。
        """

        if not await self.event_limit_enabled():
            return 0
        moment = time.time() if now is None else now

        def mutate(raw: Any) -> tuple[int, dict[str, list[float]]]:
            history = _clean_event_history(raw, moment)
            stamps = history.setdefault(str(group_id), [])
            stamps.append(moment)
            del stamps[:-EVENT_LIMIT_HISTORY_KEEP]
            return len(stamps), history

        return await self.store.update_setting(EVENT_LIMIT_HISTORY_SETTING, {}, mutate)

    async def meme_enabled_groups(self) -> tuple[str, ...]:
        """开启了表情包功能的群：运行时写过的列表优先，否则用配置里的初始白名单。"""

        stored: Any = await self.store.get_setting(MEME_GROUPS_SETTING, None)
        if isinstance(stored, list):
            return parse_group_ids(stored)
        return parse_group_ids(self.config.mantou_affection_memes_enabled_groups)

    async def meme_group_enabled(self, group_id: str) -> bool:
        return str(group_id) in await self.meme_enabled_groups()

    async def set_meme_groups_enabled(
        self, group_ids: Iterable[str], enabled: bool
    ) -> tuple[str, ...]:
        """一次切换多个群的开关，返回最新的开启列表。"""

        targets = {str(group) for group in group_ids}
        seed = parse_group_ids(self.config.mantou_affection_memes_enabled_groups)

        def mutate(raw: Any) -> tuple[bool, list[str]]:
            # 还没写过运行时开关时用配置白名单打底 写过就只认数据文件
            groups = set(parse_group_ids(raw)) if isinstance(raw, list) else set(seed)
            if enabled:
                groups |= targets
            else:
                groups -= targets
            return bool(groups), sorted(groups, key=int)

        await self.store.update_setting(MEME_GROUPS_SETTING, None, mutate)
        return await self.meme_enabled_groups()

    async def set_meme_group_enabled(
        self, group_id: str, enabled: bool
    ) -> tuple[bool, tuple[str, ...]]:
        """切换某个群的表情包开关，返回 (本群是否开启, 最新开启列表)。"""

        groups = await self.set_meme_groups_enabled((group_id,), enabled)
        return str(group_id) in groups, groups

    def _config_probability(self, attr: str) -> float:
        return clamp_probability(getattr(self.config, attr))

    async def probability_states(self) -> tuple[ProbabilityState, ...]:
        """读取全部活动概率，附带配置默认值以及是否被运行时覆盖。"""

        states: list[ProbabilityState] = []
        for field in PROBABILITY_FIELDS:
            default = self._config_probability(field.config_attr)
            stored = await self._stored_probability_value(field.key)
            states.append(
                ProbabilityState(
                    key=field.key,
                    label=field.label,
                    value=default if stored is None else stored,
                    default=default,
                    overridden=stored is not None,
                )
            )
        return tuple(states)

    async def set_all_probabilities(self, value: float) -> None:
        """把全部活动概率统一设为同一个值，一次写入。"""

        clamped = clamp_probability(value)
        await self.store.set_settings(
            {field.key: clamped for field in PROBABILITY_FIELDS}
        )

    async def scale_all_probabilities(
        self, factor: float
    ) -> tuple[ProbabilityChange, ...]:
        """按当前生效值整体缩放，返回每项的调整前后值。"""

        if not math.isfinite(factor) or factor < 0:
            raise ValueError("缩放倍率必须是非负数")
        states = await self.probability_states()
        updated = {
            state.key: clamp_probability(state.value * factor) for state in states
        }
        await self.store.set_settings(updated)
        return tuple(
            ProbabilityChange(
                label=state.label,
                before=state.value,
                after=updated[state.key],
            )
            for state in states
        )

    async def reset_all_probabilities(self) -> int:
        """清除全部运行时概率覆盖，返回实际删除的条数。

        顺带清掉 0.59 之前留在数据文件里的单事件概率覆盖，避免它们一直躺在那里。
        """

        return await self.store.delete_settings(
            [*(field.key for field in PROBABILITY_FIELDS), *LEGACY_PROBABILITY_SETTINGS]
        )

    async def _stored_int(self, key: str, default: int) -> int:
        """读取运行时覆盖的整数设置，缺省或数据损坏时退回配置默认值。"""

        stored: Any = await self.store.get_setting(key, None)
        if isinstance(stored, bool) or not isinstance(stored, int):
            return default
        return stored

    async def _stored_probability_value(self, key: str) -> float | None:
        """读取有效的运行时覆盖值；缺省、损坏或非有限数都返回 None。"""

        stored: Any = await self.store.get_setting(key, None)
        if isinstance(stored, bool) or not isinstance(stored, (int, float)):
            return None
        value = float(stored)
        if not math.isfinite(value):
            return None
        return min(1.0, max(0.0, value))

    async def _stored_probability(self, key: str, default: float) -> float:
        """读取运行时覆盖的概率，缺省或数据损坏时退回配置默认值。"""

        stored = await self._stored_probability_value(key)
        return default if stored is None else stored

    async def ambient_reaction(self, group_id: str, user_id: str) -> str | None:
        """馒头小动作：开关打开且好感度 > 0 时按好感阶段抽一句旁白。

        触发概率已经交给事件转盘，这里只负责「能不能出现」和「说哪一句」。
        """

        if not self.config.mantou_affection_ambient_enabled or self.text_library is None:
            return None
        profile = await self.store.get_profile(group_id, user_id)
        if profile.affection <= 0:
            return None
        return self.text_library.pick(AMBIENT_SCENE, snapshot_for(profile.affection))

    async def forced_ambient_reaction(self, group_id: str, user_id: str) -> str | None:
        """强制小动作：绕过开关和好感门槛，直接按好感阶段抽一句旁白。"""

        if self.text_library is None:
            return None
        profile = await self.store.get_profile(group_id, user_id)
        return self.text_library.pick(AMBIENT_SCENE, snapshot_for(profile.affection))

    async def ranking(self, group_id: str) -> list[RankingEntry]:
        return await self.store.ranking(group_id, self.config.mantou_affection_ranking_size)

    def linked_points_today(self, profile: Profile) -> int:
        return (
            profile.linked_points
            if profile.linked_date == self._now().date().isoformat()
            else 0
        )

    def gained_points_today(self, profile: Profile) -> int:
        return (
            profile.gain_points if profile.gain_date == self._now().date().isoformat() else 0
        )

    async def reward_external(
        self,
        group_id: str,
        user_id: str,
        nickname: str,
        source: str,
        reward: int,
    ) -> LinkRewardResult:
        now = self._now()
        nickname = normalize_nickname(nickname, user_id)

        def update(profile: Profile) -> LinkRewardResult:
            return apply_link_reward(
                profile,
                source=source,
                reward=reward,
                today=now.date(),
                now_timestamp=now.timestamp(),
                daily_limit=self.config.mantou_affection_link_daily_limit,
                daily_gain_limit=self.config.mantou_affection_daily_gain_limit,
                cooldown_seconds=self.config.mantou_affection_link_cooldown,
                affection_max=self.config.mantou_affection_max,
            )

        return await self.store.update_profile(group_id, user_id, nickname, update)

    async def spend_fixed(
        self,
        group_id: str,
        user_id: str,
        nickname: str,
        amount: int,
    ) -> tuple[bool, int]:
        """固定扣除好感度（不受插件联动每日上限/冷却/新手保护影响）。

        返回 (是否扣成功, 当前好感度)。余额不足时不做任何改动。
        """

        cost = max(0, int(amount))
        nickname = normalize_nickname(nickname, user_id)

        def update(profile: Profile) -> tuple[bool, int]:
            if cost == 0:
                return True, profile.affection
            if profile.affection < cost:
                return False, profile.affection
            adjust_affection(
                profile,
                -cost,
                self.config.mantou_affection_max,
                time.time(),
            )
            return True, profile.affection

        return await self.store.update_profile(group_id, user_id, nickname, update)

    async def send_gift(
        self, send, group_id: str, user_id: str, nickname: str
    ) -> bool:
        """扣分后给低好感群友补一张桃子表情包，每人每天最多一张。"""

        if self.stickers is None:
            return False
        threshold = self.config.mantou_affection_newbie_gift_below
        if threshold <= 0:
            return False
        today = self._now().date().isoformat()
        try:
            profile = await self.profile(group_id, user_id)
        except Exception:
            logger.exception("[mantou-affection] 读取扣分安慰状态失败")
            return False
        if profile.affection >= threshold or profile.gift_date == today:
            return False
        try:
            image, caption = await self.stickers.pick()
        except Exception:
            logger.exception("[mantou-affection] 扣分安慰取图失败")
            return False

        def claim(profile: Profile) -> bool:
            if profile.gift_date == today:
                return False
            profile.gift_date = today
            return True

        try:
            claimed = await self.store.update_profile(
                group_id, user_id, normalize_nickname(nickname, user_id), claim
            )
        except Exception:
            logger.exception("[mantou-affection] 记录扣分安慰失败")
            return False
        if not claimed:
            return False
        try:
            await send(
                MessageSegment.at(user_id)
                + MessageSegment.text(NEWBIE_GIFT_REPLY.format(caption=caption))
                + MessageSegment.image(image)
            )
        except Exception:
            logger.exception("[mantou-affection] 扣分安慰发送失败")
            return False
        return True

    async def adjust(
        self,
        group_id: str,
        user_id: str,
        nickname: str,
        delta: int,
        *,
        protect: bool = True,
    ) -> tuple[int, Profile, bool]:
        nickname = normalize_nickname(nickname, user_id)
        threshold = self.config.mantou_affection_newbie_protection_below

        def update(profile: Profile) -> tuple[int, Profile, bool]:
            value = delta
            protected = False
            if protect:
                value = protect_deduction(profile.affection, delta, threshold)
                protected = value != delta
            applied = adjust_affection(
                profile,
                value,
                self.config.mantou_affection_max,
                time.time(),
            )
            return applied, profile, protected

        return await self.store.update_profile(group_id, user_id, nickname, update)
