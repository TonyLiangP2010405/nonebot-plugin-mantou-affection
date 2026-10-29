from __future__ import annotations

import json
import random
from pathlib import Path

BUNDLED_CORPUS_PATH = Path(__file__).parent / "resources" / "group_corpus.json"


class CorpusError(RuntimeError):
    pass


class CorpusAssetMissing(CorpusError):
    pass


class GroupCorpus:
    """群友语料库：首次取句时读盘并缓存，缺失或损坏都按素材未就绪处理。"""

    def __init__(self, path: Path | None, *, rng: random.Random | None = None):
        self.path = Path(path) if path is not None else None
        self.rng = rng or random.Random()
        self._lines: list[str] | None = None

    @property
    def line_count(self) -> int:
        return len(self._load())

    def _load(self) -> list[str]:
        if self._lines is not None:
            return self._lines
        if self.path is None:
            raise CorpusAssetMissing("语料库路径未配置")
        try:
            raw = self.path.read_text(encoding="utf-8")
        except OSError as error:
            raise CorpusAssetMissing(f"语料库缺失: {self.path}") from error
        try:
            payload = json.loads(raw)
        except ValueError as error:
            raise CorpusAssetMissing(f"语料库解析失败: {self.path}") from error
        if not isinstance(payload, dict):
            raise CorpusAssetMissing(f"语料库格式异常: {self.path}")
        entries = payload.get("lines")
        if not isinstance(entries, list):
            raise CorpusAssetMissing(f"语料库缺少 lines: {self.path}")
        lines: list[str] = []
        for item in entries:
            if not isinstance(item, str):
                continue
            text = item.strip()
            if text:
                lines.append(text)
        if not lines:
            raise CorpusAssetMissing(f"语料库为空: {self.path}")
        self._lines = lines
        return lines

    def pick(self) -> str:
        return self.rng.choice(self._load())
