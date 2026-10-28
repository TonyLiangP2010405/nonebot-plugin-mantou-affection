from __future__ import annotations

import io
import json
import random
import zipfile
from pathlib import Path

import pytest

from nonebot_plugin_mantou_affection.wheel_stickers import (
    StickerAssetMissing,
    StickerError,
    TaoziSticker,
    TaoziStickerLibrary,
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


class FakeResponse:
    def __init__(self, data: bytes):
        self.data = data

    def read(self) -> bytes:
        return self.data

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *exc) -> bool:
        return False


def repo_zip(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in entries.items():
            archive.writestr(f"taozi-stickers-main/{name}", data)
    return buffer.getvalue()


def catalog_payload() -> bytes:
    return json.dumps(
        {
            "target": 500,
            "completed": 1,
            "items": [{"id": 1, "caption": "头部主播", "path": "stickers/001.png"}],
        },
        ensure_ascii=False,
    ).encode("utf-8")


class FakeOpener:
    def __init__(self, responses: list[bytes | Exception]):
        self.responses = list(responses)
        self.urls: list[str] = []

    def __call__(self, url: str, timeout: float = 60):
        self.urls.append(url)
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return FakeResponse(result)


def make_library(tmp_path: Path, opener, **kwargs) -> TaoziStickerLibrary:
    return TaoziStickerLibrary(
        base_dir=tmp_path / "taozi_stickers",
        opener=opener,
        rng=kwargs.pop("rng", random.Random(0)),
        **kwargs,
    )


async def test_ensure_loaded_extracts_only_catalog_and_stickers(tmp_path: Path) -> None:
    archive = repo_zip(
        {
            "catalog.json": catalog_payload(),
            "stickers/001.png": b"png",
            "001-头部主播.png": b"preview",
            "index.html": b"<html>",
            "plan.json": b"{}",
        }
    )
    library = make_library(tmp_path, FakeOpener([archive]))

    await library.ensure_loaded()

    base = tmp_path / "taozi_stickers"
    assert (base / "catalog.json").is_file()
    assert (base / "stickers" / "001.png").read_bytes() == b"png"
    assert not (base / "001-头部主播.png").exists()
    assert not (base / "index.html").exists()
    assert not (base / "plan.json").exists()


async def test_ensure_loaded_falls_back_to_next_mirror(tmp_path: Path) -> None:
    archive = repo_zip({"catalog.json": catalog_payload(), "stickers/001.png": b"png"})
    opener = FakeOpener([OSError("blocked"), archive])
    library = make_library(tmp_path, opener)

    await library.ensure_loaded()

    assert len(opener.urls) == 2
    assert opener.urls[0].startswith("https://codeload.github.com/")
    assert opener.urls[1] == (
        "https://ghproxy.net/https://github.com/TonyLiangP2010405/taozi-stickers"
        "/archive/refs/heads/main.zip"
    )


async def test_ensure_loaded_reuses_ready_copy(tmp_path: Path) -> None:
    base = tmp_path / "taozi_stickers"
    write_catalog(base, [{"id": 1, "caption": "头部主播", "path": "stickers/001.png"}])
    write_sticker(base, "stickers/001.png")
    opener = FakeOpener([])
    library = make_library(tmp_path, opener)

    await library.ensure_loaded()

    assert opener.urls == []


async def test_ensure_loaded_redownloads_broken_copy(tmp_path: Path) -> None:
    base = tmp_path / "taozi_stickers"
    write_catalog(base, [{"id": 1, "caption": "头部主播", "path": "stickers/001.png"}])
    archive = repo_zip({"catalog.json": catalog_payload(), "stickers/001.png": b"png"})
    library = make_library(tmp_path, FakeOpener([archive]))

    await library.ensure_loaded()

    assert (base / "stickers" / "001.png").is_file()


async def test_ensure_loaded_raises_when_every_mirror_fails(tmp_path: Path) -> None:
    library = make_library(
        tmp_path,
        FakeOpener([OSError("a"), OSError("b"), OSError("c"), OSError("d")]),
    )

    with pytest.raises(StickerError):
        await library.ensure_loaded()


async def test_ensure_loaded_raises_when_archive_has_no_catalog(
    tmp_path: Path,
) -> None:
    library = make_library(
        tmp_path, FakeOpener([repo_zip({"stickers/001.png": b"png"})])
    )

    with pytest.raises(StickerError):
        await library.ensure_loaded()


async def test_pick_returns_caption_and_bytes(tmp_path: Path) -> None:
    base = tmp_path / "taozi_stickers"
    write_catalog(
        base,
        [
            {"id": 1, "caption": "头部主播", "path": "stickers/001.png"},
            {"id": 2, "caption": "五冠王", "path": "stickers/002.png"},
        ],
    )
    write_sticker(base, "stickers/001.png", b"one")
    write_sticker(base, "stickers/002.png", b"two")
    library = make_library(tmp_path, FakeOpener([]), rng=random.Random(0))

    image, caption = await library.pick()

    assert (image, caption) in {(b"one", "头部主播"), (b"two", "五冠王")}


async def test_pick_raises_asset_missing_when_not_ready(tmp_path: Path) -> None:
    library = make_library(tmp_path, FakeOpener([]))

    with pytest.raises(StickerAssetMissing):
        await library.pick()


def test_check_after_missing_is_throttled_by_clock(tmp_path: Path) -> None:
    now = [100.0]
    calls = []
    library = TaoziStickerLibrary(
        base_dir=tmp_path / "taozi_stickers",
        clock=lambda: now[0],
        retry_seconds=600,
    )
    library.kickoff = lambda: calls.append(now[0])

    assert library.check_after_missing() is True
    now[0] += 100
    assert library.check_after_missing() is False
    now[0] += 600
    assert library.check_after_missing() is True
    assert calls == [100.0, 800.0]
