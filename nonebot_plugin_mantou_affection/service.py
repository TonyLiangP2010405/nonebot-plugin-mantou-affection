from __future__ import annotations

import random
import time
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import Config
from .logic import adjust_affection, apply_link_reward, perform_interaction, snapshot_for
from .models import AffectionSnapshot, InteractionResult, LinkRewardResult, Profile, RankingEntry
from .storage import AffectionStore


def normalize_nickname(value: str, fallback: str) -> str:
    nickname = " ".join(value.replace("\n", " ").split()).strip()
    return (nickname or fallback)[:24]


class AffectionService:
    def __init__(
        self,
        store: AffectionStore,
        config: Config,
        *,
        rng: random.Random | None = None,
    ):
        self.store = store
        self.config = config
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

        def update(profile: Profile) -> InteractionResult:
            return perform_interaction(
                profile,
                bot_name=self.config.mantou_affection_bot_name,
                today=now.date(),
                now_timestamp=now.timestamp(),
                daily_limit=self.config.mantou_affection_interaction_limit,
                cooldown_seconds=self.config.mantou_affection_interaction_cooldown,
                affection_max=self.config.mantou_affection_max,
                rng=self.rng,
            )

        return await self.store.update_profile(group_id, user_id, nickname, update)

    async def ranking(self, group_id: str) -> list[RankingEntry]:
        return await self.store.ranking(group_id, self.config.mantou_affection_ranking_size)

    def linked_points_today(self, profile: Profile) -> int:
        return (
            profile.linked_points
            if profile.linked_date == self._now().date().isoformat()
            else 0
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
                cooldown_seconds=self.config.mantou_affection_link_cooldown,
                affection_max=self.config.mantou_affection_max,
            )

        return await self.store.update_profile(group_id, user_id, nickname, update)

    async def adjust(
        self,
        group_id: str,
        user_id: str,
        nickname: str,
        delta: int,
    ) -> tuple[int, Profile]:
        nickname = normalize_nickname(nickname, user_id)

        def update(profile: Profile) -> tuple[int, Profile]:
            applied = adjust_affection(
                profile,
                delta,
                self.config.mantou_affection_max,
                time.time(),
            )
            return applied, profile

        return await self.store.update_profile(group_id, user_id, nickname, update)
