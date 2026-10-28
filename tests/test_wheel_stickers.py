from __future__ import annotations

import json
from pathlib import Path

import pytest

from nonebot_plugin_mantou_affection.wheel_stickers import (
    StickerAssetMissing,
    TaoziSticker,
    catalog_ready,
    load_catalog,
)


def write_catalog(base: Path, items: list[dict]) -> None:
    base.mkdir(parents=True, exist_ok=True)
    (base / "catalog.json").write_text(
        json.dumps(
            {"target": 500, "completed": len(items), "items": items},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def write_sticker(base: Path, rel: str, data: bytes = b"png") -> None:
    path = base / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def test_load_catalog_reads_id_caption_and_path(tmp_path: Path) -> None:
    write_catalog(
        tmp_path,
        [{"id": 1, "caption": "头部主播", "path": "stickers/001-头部主播.png"}],
    )
    write_sticker(tmp_path, "stickers/001-头部主播.png")

    assert load_catalog(tmp_path) == [
        TaoziSticker(1, "头部主播", "stickers/001-头部主播.png")
    ]


def test_load_catalog_skips_entries_without_path(tmp_path: Path) -> None:
    write_catalog(
        tmp_path,
        [
            {"id": 1, "caption": "空", "path": ""},
            {"id": 2, "caption": "五冠王", "path": "stickers/002.png"},
        ],
    )
    write_sticker(tmp_path, "stickers/002.png")

    assert [item.id for item in load_catalog(tmp_path)] == [2]


def test_load_catalog_missing_file_raises_asset_missing(tmp_path: Path) -> None:
    with pytest.raises(StickerAssetMissing):
        load_catalog(tmp_path)


def test_load_catalog_broken_json_raises_asset_missing(tmp_path: Path) -> None:
    write_catalog(tmp_path, [])
    (tmp_path / "catalog.json").write_text("{not json", encoding="utf-8")

    with pytest.raises(StickerAssetMissing):
        load_catalog(tmp_path)


def test_load_catalog_empty_items_raises_asset_missing(tmp_path: Path) -> None:
    write_catalog(tmp_path, [])

    with pytest.raises(StickerAssetMissing):
        load_catalog(tmp_path)


def test_catalog_ready_requires_every_listed_file(tmp_path: Path) -> None:
    write_catalog(
        tmp_path,
        [
            {"id": 1, "caption": "头部主播", "path": "stickers/001.png"},
            {"id": 2, "caption": "五冠王", "path": "stickers/002.png"},
        ],
    )
    write_sticker(tmp_path, "stickers/001.png")

    assert catalog_ready(tmp_path) is False
    write_sticker(tmp_path, "stickers/002.png")
    assert catalog_ready(tmp_path) is True
