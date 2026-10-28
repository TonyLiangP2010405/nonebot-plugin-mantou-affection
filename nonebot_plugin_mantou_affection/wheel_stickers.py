from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


class StickerError(RuntimeError):
    pass


class StickerAssetMissing(StickerError):
    pass


@dataclass(frozen=True)
class TaoziSticker:
    id: int
    caption: str
    path: str


CATALOG_NAME = "catalog.json"


def load_catalog(base_dir: Path) -> list[TaoziSticker]:
    """读取桃子表情包索引；缺失、损坏或为空都按素材未就绪处理。"""

    path = Path(base_dir) / CATALOG_NAME
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as error:
        raise StickerAssetMissing(f"索引缺失: {path}") from error
    try:
        payload = json.loads(raw)
    except ValueError as error:
        raise StickerAssetMissing(f"索引解析失败: {path}") from error
    if not isinstance(payload, dict):
        raise StickerAssetMissing(f"索引格式异常: {path}")
    items: list[TaoziSticker] = []
    for entry in payload.get("items") or ():
        if not isinstance(entry, dict):
            continue
        rel = str(entry.get("path") or "").strip()
        if not rel:
            continue
        try:
            sticker_id = int(entry.get("id") or 0)
        except (TypeError, ValueError):
            sticker_id = 0
        items.append(
            TaoziSticker(sticker_id, str(entry.get("caption") or "").strip(), rel)
        )
    if not items:
        raise StickerAssetMissing(f"索引为空: {path}")
    return items


def catalog_ready(base_dir: Path) -> bool:
    """索引可解析、非空，且每条列出的图片都存在才算就绪。"""

    try:
        items = load_catalog(base_dir)
    except StickerAssetMissing:
        return False
    base = Path(base_dir)
    return all((base / item.path).is_file() for item in items)
