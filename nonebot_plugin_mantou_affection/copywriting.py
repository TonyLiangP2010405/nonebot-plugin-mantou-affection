from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path
from typing import Any

from nonebot import logger

from .models import AffectionSnapshot


class AffectionTextLibrary:
    """按插件场景和好感阶段加载文案，支持大型外部 JSON 文案库热更新。"""

    def __init__(self, bundled_path: Path, custom_path: str = ""):
        self.bundled_path = bundled_path
        self.custom_path = Path(custom_path).expanduser() if custom_path else None
        self._custom_mtime_ns: int | None = None
        self._bundled = self._load_file(bundled_path)
        self._scenes = self._bundled

    def _bundled_copy(self) -> dict[str, dict[str, tuple[str, ...]]]:
        return {scene: dict(bands) for scene, bands in self._bundled.items()}

    @staticmethod
    def _normalize(data: Any) -> dict[str, dict[str, tuple[str, ...]]]:
        if not isinstance(data, dict):
            raise ValueError("文案库根节点必须是 JSON 对象")
        result: dict[str, dict[str, tuple[str, ...]]] = {}
        for scene, bands in data.items():
            if not isinstance(scene, str) or not isinstance(bands, dict):
                continue
            normalized_bands: dict[str, tuple[str, ...]] = {}
            for band, texts in bands.items():
                if not isinstance(band, str) or not isinstance(texts, list):
                    continue
                cleaned = tuple(
                    text.strip() for text in texts if isinstance(text, str) and text.strip()
                )
                if cleaned:
                    normalized_bands[band] = cleaned
            if normalized_bands:
                result[scene] = normalized_bands
        return result

    @classmethod
    def _load_file(cls, path: Path) -> dict[str, dict[str, tuple[str, ...]]]:
        with path.open("r", encoding="utf-8") as file:
            return cls._normalize(json.load(file))

    def _reload_custom_if_needed(self) -> None:
        if self.custom_path is None:
            return
        try:
            mtime_ns = self.custom_path.stat().st_mtime_ns
        except OSError as error:
            if self._custom_mtime_ns != -1:
                logger.warning(f"[mantou-affection] 无法读取自定义文案库: {error}")
                self._scenes = self._bundled_copy()
            self._custom_mtime_ns = -1
            return
        if mtime_ns == self._custom_mtime_ns:
            return
        try:
            custom = self._load_file(self.custom_path)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            logger.warning(f"[mantou-affection] 自定义文案库格式错误，继续使用内置文案: {error}")
            self._scenes = self._bundled_copy()
            self._custom_mtime_ns = mtime_ns
            return
        merged = self._bundled_copy()
        for scene, bands in custom.items():
            merged.setdefault(scene, {}).update(bands)
        self._scenes = merged
        self._custom_mtime_ns = mtime_ns
        logger.info(f"[mantou-affection] 已加载自定义文案库: {self.custom_path}")

    def pick(self, scene: str, snapshot: AffectionSnapshot, *, seed: str = "") -> str | None:
        self._reload_custom_if_needed()
        choices = self._scenes.get(scene, {}).get(snapshot.band, ())
        if not choices:
            return None
        if not seed:
            return random.SystemRandom().choice(choices)
        digest = hashlib.sha256(f"{scene}\0{snapshot.band}\0{seed}".encode()).digest()
        return choices[int.from_bytes(digest[:8], "big") % len(choices)]
