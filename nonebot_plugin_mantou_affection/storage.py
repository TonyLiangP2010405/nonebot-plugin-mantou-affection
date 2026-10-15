from __future__ import annotations

import asyncio
import json
import os
import tempfile
from collections.abc import Iterable
from contextlib import suppress
from pathlib import Path
from typing import Any, Callable, TypeVar

from .models import Profile, RankingEntry

T = TypeVar("T")


class AffectionStore:
    """使用异步锁和原子替换保存好感度数据。"""

    def __init__(self, path: Path):
        self.path = path
        self._lock = asyncio.Lock()
        self._data: dict[str, Any] | None = None

    @staticmethod
    def _empty_data() -> dict[str, Any]:
        return {
            "version": 1,
            "groups": {},
            "settings": {},
            "cooldowns": {},
            "pk_used_questions": [],
        }

    def _load_sync(self) -> dict[str, Any]:
        if self._data is not None:
            return self._data
        if not self.path.is_file():
            self._data = self._empty_data()
            return self._data
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        data = self._empty_data()
        if isinstance(payload, dict):
            if isinstance(payload.get("groups"), dict):
                data["groups"] = payload["groups"]
            if isinstance(payload.get("settings"), dict):
                data["settings"] = payload["settings"]
            if isinstance(payload.get("cooldowns"), dict):
                data["cooldowns"] = payload["cooldowns"]
            used = payload.get("pk_used_questions")
            if isinstance(used, list):
                data["pk_used_questions"] = [
                    int(index)
                    for index in used
                    if isinstance(index, int) and not isinstance(index, bool)
                ]
        self._data = data
        return self._data

    def _save_sync(self) -> None:
        data = self._load_sync()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            dir=str(self.path.parent), prefix=f".{self.path.name}.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump(data, file, ensure_ascii=False, indent=2, sort_keys=True)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp_name, self.path)
        except BaseException:
            with suppress(OSError):
                os.unlink(temp_name)
            raise

    def _update_sync(
        self,
        group_id: str,
        user_id: str,
        nickname: str,
        updater: Callable[[Profile], T],
    ) -> T:
        data = self._load_sync()
        groups = data["groups"]
        group = groups.setdefault(str(group_id), {})
        if not isinstance(group, dict):
            group = {}
            groups[str(group_id)] = group
        profile = Profile.from_dict(str(user_id), group.get(str(user_id)))
        if nickname:
            profile.nickname = nickname
        result = updater(profile)
        group[str(user_id)] = profile.to_dict()
        self._save_sync()
        return result

    async def update_profile(
        self,
        group_id: str,
        user_id: str,
        nickname: str,
        updater: Callable[[Profile], T],
    ) -> T:
        async with self._lock:
            return await asyncio.to_thread(
                self._update_sync,
                str(group_id),
                str(user_id),
                nickname,
                updater,
            )

    def _get_profile_sync(self, group_id: str, user_id: str) -> Profile:
        groups = self._load_sync()["groups"]
        group = groups.get(str(group_id), {})
        raw = group.get(str(user_id)) if isinstance(group, dict) else None
        return Profile.from_dict(str(user_id), raw)

    async def get_profile(self, group_id: str, user_id: str) -> Profile:
        async with self._lock:
            return await asyncio.to_thread(self._get_profile_sync, str(group_id), str(user_id))

    async def group_ids(self) -> list[str]:
        async with self._lock:
            return list(self._load_sync()["groups"].keys())

    def _ranking_sync(self, group_id: str, limit: int) -> list[RankingEntry]:
        groups = self._load_sync()["groups"]
        group = groups.get(str(group_id), {})
        if not isinstance(group, dict):
            return []
        profiles = [Profile.from_dict(user_id, raw) for user_id, raw in group.items()]
        profiles = [profile for profile in profiles if profile.affection > 0]
        profiles.sort(key=lambda item: (-item.affection, item.user_id))
        return [
            RankingEntry(rank=index, profile=profile)
            for index, profile in enumerate(profiles[:limit], start=1)
        ]

    async def ranking(self, group_id: str, limit: int) -> list[RankingEntry]:
        async with self._lock:
            return await asyncio.to_thread(self._ranking_sync, str(group_id), limit)

    def _get_setting_sync(self, key: str, default: T) -> T:
        settings = self._load_sync()["settings"]
        return settings.get(str(key), default)

    async def get_setting(self, key: str, default: T) -> T:
        async with self._lock:
            return await asyncio.to_thread(self._get_setting_sync, str(key), default)

    def _set_setting_sync(self, key: str, value: Any) -> None:
        self._load_sync()["settings"][str(key)] = value
        self._save_sync()

    async def set_setting(self, key: str, value: Any) -> None:
        async with self._lock:
            await asyncio.to_thread(self._set_setting_sync, str(key), value)

    def _cooldowns_sync(self) -> dict[str, dict[str, float]]:
        raw = self._load_sync()["cooldowns"]
        result: dict[str, dict[str, float]] = {}
        for group_id, users in raw.items():
            if not isinstance(users, dict):
                continue
            cleaned = {
                str(user_id): float(expires)
                for user_id, expires in users.items()
                if isinstance(expires, (int, float)) and not isinstance(expires, bool)
            }
            if cleaned:
                result[str(group_id)] = cleaned
        return result

    async def cooldowns(self) -> dict[str, dict[str, float]]:
        """读取 (群 -> 人 -> 到期时间戳) 的冷却记录。"""

        async with self._lock:
            return await asyncio.to_thread(self._cooldowns_sync)

    def _set_cooldown_sync(self, group_id: str, user_id: str, expires_at: float) -> None:
        data = self._load_sync()
        group = data["cooldowns"].get(str(group_id))
        if not isinstance(group, dict):
            group = {}
            data["cooldowns"][str(group_id)] = group
        group[str(user_id)] = float(expires_at)
        self._save_sync()

    async def set_cooldown(self, group_id: str, user_id: str, expires_at: float) -> None:
        async with self._lock:
            await asyncio.to_thread(
                self._set_cooldown_sync, str(group_id), str(user_id), expires_at
            )

    def _clear_cooldown_sync(self, group_id: str, user_id: str) -> None:
        data = self._load_sync()
        group = data["cooldowns"].get(str(group_id))
        if not isinstance(group, dict):
            return
        group.pop(str(user_id), None)
        if not group:
            data["cooldowns"].pop(str(group_id), None)
        self._save_sync()

    async def clear_cooldown(self, group_id: str, user_id: str) -> None:
        async with self._lock:
            await asyncio.to_thread(
                self._clear_cooldown_sync, str(group_id), str(user_id)
            )

    def _clear_cooldowns_sync(self) -> None:
        self._load_sync()["cooldowns"] = {}
        self._save_sync()

    async def clear_cooldowns(self) -> None:
        """清空所有冷却记录（重启恢复时用）。"""

        async with self._lock:
            await asyncio.to_thread(self._clear_cooldowns_sync)

    def _used_questions_sync(self) -> list[int]:
        return sorted(set(self._load_sync()["pk_used_questions"]))

    async def used_questions(self) -> list[int]:
        """读取已经出过的问答题题号。"""

        async with self._lock:
            return await asyncio.to_thread(self._used_questions_sync)

    def _add_used_questions_sync(self, indices: Iterable[int]) -> None:
        used = set(self._load_sync()["pk_used_questions"])
        used.update(int(index) for index in indices)
        self._load_sync()["pk_used_questions"] = sorted(used)
        self._save_sync()

    async def add_used_questions(self, indices: Iterable[int]) -> None:
        """把题号标记为已出（发出去就算用掉）。"""

        async with self._lock:
            await asyncio.to_thread(self._add_used_questions_sync, list(indices))

    def _reset_all_sync(self) -> int:
        data = self._load_sync()
        count = sum(len(group) for group in data["groups"].values() if isinstance(group, dict))
        data["groups"] = {}
        self._save_sync()
        return count

    async def reset_all(self) -> int:
        async with self._lock:
            return await asyncio.to_thread(self._reset_all_sync)
