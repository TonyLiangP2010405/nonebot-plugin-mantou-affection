from __future__ import annotations

import random
import time
from datetime import datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import Config
from .copywriting import AffectionTextLibrary
from .logic import (
    adjust_affection,
    apply_link_reward,
    perform_interaction,
    perform_poke,
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
AMBIENT_SCENE = "mantou.ambient"


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
    ):
        self.store = store
        self.config = config
        self.text_library = text_library
        self.rng = rng or random.Random()
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

        return await self.store.update_profile(
            group_id, user_id, nickname, update, now.date().isoformat()
        )

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
                negative_base=self.config.mantou_affection_poke_negative_base,
                max_penalty=self.config.mantou_affection_poke_max_penalty,
                ignore_threshold=self.config.mantou_affection_poke_ignore_threshold,
                daily_gain_limit=self.config.mantou_affection_daily_gain_limit,
                affection_max=self.config.mantou_affection_max,
                rng=self.rng,
                text_picker=text_picker,
            )

        return await self.store.update_profile(
            group_id, user_id, nickname, update, now.date().isoformat()
        )

    async def ambient_probability(self) -> float:
        stored: Any = await self.store.get_setting(AMBIENT_PROBABILITY_SETTING, None)
        if isinstance(stored, bool) or not isinstance(stored, (int, float)):
            return self.config.mantou_affection_ambient_probability
        return min(1.0, max(0.0, float(stored)))

    async def set_ambient_probability(self, value: float) -> None:
        await self.store.set_setting(AMBIENT_PROBABILITY_SETTING, float(value))

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

        return await self.store.update_profile(
            group_id, user_id, nickname, update, now.date().isoformat()
        )

    async def adjust(
        self,
        group_id: str,
        user_id: str,
        nickname: str,
        delta: int,
    ) -> tuple[int, Profile]:
        today = self._now().date().isoformat()
        nickname = normalize_nickname(nickname, user_id)

        def update(profile: Profile) -> tuple[int, Profile]:
            applied = adjust_affection(
                profile,
                delta,
                self.config.mantou_affection_max,
                time.time(),
            )
            return applied, profile

        return await self.store.update_profile(group_id, user_id, nickname, update, today)
