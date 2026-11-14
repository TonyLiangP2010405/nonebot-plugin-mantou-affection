from __future__ import annotations

import math
import random
import time
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
from .storage import AffectionStore

AMBIENT_PROBABILITY_SETTING = "ambient_probability"
FIND_CHAR_GROUP_CHANCE_SETTING = "find_char_group_chance"
FIND_CHAR_POKE_CHANCE_SETTING = "find_char_poke_chance"
BET_PROBABILITY_SETTING = "bet_probability"
PK_PROBABILITY_SETTING = "pk_probability"
GROUP_QUIZ_PROBABILITY_SETTING = "group_quiz_probability"
TEYVAT_QUIZ_PROBABILITY_SETTING = "teyvat_quiz_probability"
FILL_BLANK_PROBABILITY_SETTING = "fill_blank_probability"
WHEEL_PROBABILITY_SETTING = "wheel_probability"
MEME_CONFIRM_WHITELIST_SETTING = "meme_confirm_whitelist"
NEWBIE_GIFT_REPLY = " 扣分安慰：送你一张桃子表情包「{caption}」"
AMBIENT_SCENE = "mantou.ambient"


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


PROBABILITY_FIELDS: tuple[ProbabilityField, ...] = (
    ProbabilityField(
        AMBIENT_PROBABILITY_SETTING,
        "馒头小动作",
        "mantou_affection_ambient_probability",
    ),
    ProbabilityField(
        FIND_CHAR_GROUP_CHANCE_SETTING,
        "找字（群消息）",
        "mantou_affection_find_char_chance",
    ),
    ProbabilityField(
        FIND_CHAR_POKE_CHANCE_SETTING,
        "找字（戳一戳）",
        "mantou_affection_poke_find_char_chance",
    ),
    ProbabilityField(BET_PROBABILITY_SETTING, "馒头博弈", "mantou_affection_bet_chance"),
    ProbabilityField(PK_PROBABILITY_SETTING, "桃子问答", "mantou_affection_pk_chance"),
    ProbabilityField(
        GROUP_QUIZ_PROBABILITY_SETTING,
        "群友问答",
        "mantou_affection_group_quiz_chance",
    ),
    ProbabilityField(
        TEYVAT_QUIZ_PROBABILITY_SETTING,
        "提瓦特问答",
        "mantou_affection_teyvat_chance",
    ),
    ProbabilityField(
        FILL_BLANK_PROBABILITY_SETTING,
        "补句问答",
        "mantou_affection_fill_blank_chance",
    ),
    ProbabilityField(WHEEL_PROBABILITY_SETTING, "命运齿轮", "mantou_affection_wheel_chance"),
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

    async def ambient_probability(self) -> float:
        return await self._stored_probability(
            AMBIENT_PROBABILITY_SETTING,
            self.config.mantou_affection_ambient_probability,
        )

    async def set_ambient_probability(self, value: float) -> None:
        await self.store.set_setting(AMBIENT_PROBABILITY_SETTING, float(value))

    async def find_char_group_chance(self) -> float:
        """群消息路径的找字小游戏概率，运行时用 /馒头找字概率 覆盖过就优先用它。"""

        return await self._stored_probability(
            FIND_CHAR_GROUP_CHANCE_SETTING,
            self.config.mantou_affection_find_char_chance,
        )

    async def find_char_poke_chance(self, default: float | None = None) -> float:
        """戳一戳路径的找字小游戏概率，default 由调用方传入当前配置里的默认值。"""

        fallback = (
            self.config.mantou_affection_poke_find_char_chance if default is None else default
        )
        return await self._stored_probability(FIND_CHAR_POKE_CHANCE_SETTING, fallback)

    async def find_char_chances(self) -> tuple[float, float]:
        """返回 (群消息概率, 戳一戳概率)。"""

        return await self.find_char_group_chance(), await self.find_char_poke_chance()

    async def set_find_char_chances(
        self,
        *,
        group: float | None = None,
        poke: float | None = None,
    ) -> None:
        """只写入传进来的那一项，另一项保持原值。"""

        if group is not None:
            await self.store.set_setting(FIND_CHAR_GROUP_CHANCE_SETTING, float(group))
        if poke is not None:
            await self.store.set_setting(FIND_CHAR_POKE_CHANCE_SETTING, float(poke))

    async def find_char_triggered(self, group_id: str, user_id: str) -> bool:
        """群消息路径的找字判定：好感度 > 0 且掷中概率才返回 True。"""

        profile = await self.store.get_profile(group_id, user_id)
        if profile.affection <= 0:
            return False
        chance = await self.find_char_group_chance()
        return self.rng.random() < chance

    async def bet_triggered(self, group_id: str, user_id: str) -> bool:
        """群消息路径的博弈判定：好感度超过门槛且掷中概率才返回 True。"""

        profile = await self.store.get_profile(group_id, user_id)
        if profile.affection <= self.config.mantou_affection_bet_min_affection:
            return False
        chance = await self.bet_probability()
        return self.rng.random() < chance

    async def bet_probability(self) -> float:
        """馒头博弈的触发概率，运行时用 /馒头博弈概率 覆盖过就优先用它。"""

        return await self._stored_probability(
            BET_PROBABILITY_SETTING, self.config.mantou_affection_bet_chance
        )

    async def bet_probability_override(self) -> float | None:
        """读取运行时覆盖的博弈概率，没覆盖过返回 None。"""

        stored: Any = await self.store.get_setting(BET_PROBABILITY_SETTING, None)
        if isinstance(stored, bool) or not isinstance(stored, (int, float)):
            return None
        return min(1.0, max(0.0, float(stored)))

    async def set_bet_probability(self, value: float) -> None:
        await self.store.set_setting(BET_PROBABILITY_SETTING, float(value))

    async def pk_triggered(self) -> bool:
        """随机问答不看榜单或好感度，只按独立概率判定。"""

        chance = await self.pk_probability()
        return self.rng.random() < chance

    async def pk_probability(self) -> float:
        """随机问答的触发概率，运行时用 /馒头问答概率 覆盖过就优先用它。"""

        return await self._stored_probability(
            PK_PROBABILITY_SETTING, self.config.mantou_affection_pk_chance
        )

    async def pk_probability_override(self) -> float | None:
        """读取运行时覆盖的问答概率，没覆盖过返回 None。"""

        stored: Any = await self.store.get_setting(PK_PROBABILITY_SETTING, None)
        if isinstance(stored, bool) or not isinstance(stored, (int, float)):
            return None
        return min(1.0, max(0.0, float(stored)))

    async def set_pk_probability(self, value: float) -> None:
        await self.store.set_setting(PK_PROBABILITY_SETTING, float(value))

    async def group_quiz_triggered(self) -> bool:
        """群友问答不看榜单或好感度，只按独立概率判定。"""

        chance = await self.group_quiz_probability()
        return self.rng.random() < chance

    async def group_quiz_probability(self) -> float:
        """群友问答的触发概率，运行时用 /馒头群友问答概率 覆盖过就优先用它。"""

        return await self._stored_probability(
            GROUP_QUIZ_PROBABILITY_SETTING,
            self.config.mantou_affection_group_quiz_chance,
        )

    async def group_quiz_probability_override(self) -> float | None:
        """读取运行时覆盖的群友问答概率，没覆盖过返回 None。"""

        stored: Any = await self.store.get_setting(GROUP_QUIZ_PROBABILITY_SETTING, None)
        if isinstance(stored, bool) or not isinstance(stored, (int, float)):
            return None
        return min(1.0, max(0.0, float(stored)))

    async def set_group_quiz_probability(self, value: float) -> None:
        await self.store.set_setting(GROUP_QUIZ_PROBABILITY_SETTING, float(value))

    async def teyvat_quiz_triggered(self) -> bool:
        """提瓦特问答不看好感度，只按独立概率判定。"""

        chance = await self.teyvat_quiz_probability()
        return self.rng.random() < chance

    async def teyvat_quiz_probability(self) -> float:
        """提瓦特问答的触发概率，运行时用 /馒头提瓦特概率 覆盖过就优先用它。"""

        return await self._stored_probability(
            TEYVAT_QUIZ_PROBABILITY_SETTING,
            self.config.mantou_affection_teyvat_chance,
        )

    async def teyvat_quiz_probability_override(self) -> float | None:
        """读取运行时覆盖的提瓦特问答概率，没覆盖过返回 None。"""

        stored: Any = await self.store.get_setting(TEYVAT_QUIZ_PROBABILITY_SETTING, None)
        if isinstance(stored, bool) or not isinstance(stored, (int, float)):
            return None
        return min(1.0, max(0.0, float(stored)))

    async def set_teyvat_quiz_probability(self, value: float) -> None:
        await self.store.set_setting(TEYVAT_QUIZ_PROBABILITY_SETTING, float(value))

    async def fill_blank_triggered(self) -> bool:
        """补句问答不看榜单或好感度，只按独立概率判定。"""

        chance = await self.fill_blank_probability()
        return self.rng.random() < chance

    async def fill_blank_probability(self) -> float:
        """补句问答的触发概率，运行时用 /馒头补句概率 覆盖过就优先用它。"""

        return await self._stored_probability(
            FILL_BLANK_PROBABILITY_SETTING,
            self.config.mantou_affection_fill_blank_chance,
        )

    async def fill_blank_probability_override(self) -> float | None:
        """读取运行时覆盖的补句问答概率，没覆盖过返回 None。"""

        stored: Any = await self.store.get_setting(FILL_BLANK_PROBABILITY_SETTING, None)
        if isinstance(stored, bool) or not isinstance(stored, (int, float)):
            return None
        return min(1.0, max(0.0, float(stored)))

    async def set_fill_blank_probability(self, value: float) -> None:
        await self.store.set_setting(FILL_BLANK_PROBABILITY_SETTING, float(value))

    async def wheel_triggered(self, group_id: str, user_id: str) -> bool:
        profile = await self.store.get_profile(group_id, user_id)
        if profile.affection <= 0:
            return False
        chance = await self.wheel_probability()
        if chance <= 0:
            return False
        return self.rng.random() < chance

    async def wheel_probability(self) -> float:
        return await self._stored_probability(
            WHEEL_PROBABILITY_SETTING, self.config.mantou_affection_wheel_chance
        )

    async def wheel_probability_override(self) -> float | None:
        stored: Any = await self.store.get_setting(WHEEL_PROBABILITY_SETTING, None)
        if isinstance(stored, bool) or not isinstance(stored, (int, float)):
            return None
        return min(1.0, max(0.0, float(stored)))

    async def set_wheel_probability(self, value: float) -> None:
        await self.store.set_setting(WHEEL_PROBABILITY_SETTING, float(value))

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
        """清除全部运行时概率覆盖，返回实际删除的条数。"""

        return await self.store.delete_settings(
            field.key for field in PROBABILITY_FIELDS
        )

    async def meme_confirm_whitelist(self) -> set[str]:
        """读取表情免确认白名单（全局，不分群）。"""

        stored: Any = await self.store.get_setting(MEME_CONFIRM_WHITELIST_SETTING, [])
        if not isinstance(stored, list):
            return set()
        return {
            str(value)
            for value in stored
            if isinstance(value, (str, int)) and not isinstance(value, bool)
        }

    async def toggle_meme_confirm_whitelist(self, user_id: str) -> bool:
        """切换某人的表情免确认状态，返回切换后是否在白名单。"""

        user = str(user_id)
        whitelist = await self.meme_confirm_whitelist()
        if user in whitelist:
            whitelist.discard(user)
            enabled = False
        else:
            whitelist.add(user)
            enabled = True
        await self.store.set_setting(
            MEME_CONFIRM_WHITELIST_SETTING, sorted(whitelist)
        )
        return enabled

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
        if not self.config.mantou_affection_ambient_enabled or self.text_library is None:
            return None
        profile = await self.store.get_profile(group_id, user_id)
        if profile.affection <= 0:
            return None
        probability = await self.ambient_probability()
        if self.rng.random() >= probability:
            return None
        return self.text_library.pick(AMBIENT_SCENE, snapshot_for(profile.affection))

    async def forced_ambient_reaction(self, group_id: str, user_id: str) -> str | None:
        """强制小动作：绕过开关、概率和好感门槛，直接按好感阶段抽一句旁白。"""

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
