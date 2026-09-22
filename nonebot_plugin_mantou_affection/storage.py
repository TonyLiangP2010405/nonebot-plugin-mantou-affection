from __future__ import annotations

import asyncio
import json
import os
import tempfile
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

    def _load_sync(self) -> dict[str, Any]:
        if self._data is not None:
            return self._data
        if not self.path.is_file():
            self._data = {"version": 1, "groups": {}, "settings": {}}
            return self._data
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        groups = payload.get("groups") if isinstance(payload, dict) else None
        settings = payload.get("settings") if isinstance(payload, dict) else None
        self._data = {
            "version": 1,
            "groups": groups if isinstance(groups, dict) else {},
            "settings": settings if isinstance(settings, dict) else {},
        }
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
