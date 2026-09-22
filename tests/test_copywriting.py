import json
from pathlib import Path

import pytest

from nonebot_plugin_mantou_affection.copywriting import AffectionTextLibrary
from nonebot_plugin_mantou_affection.logic import snapshot_for


def _write_library(path: Path, text: str) -> None:
    path.write_text(
        json.dumps({"plugin.scene": {"flirty": [text]}}, ensure_ascii=False),
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    ("score", "band"),
    [(0, "neutral"), (30, "warm"), (60, "close"), (100, "flirty"), (160, "intimate")],
)
def test_bundled_interact_scene_covers_every_band(
    bundled_texts_path: Path, score: int, band: str
) -> None:
    library = AffectionTextLibrary(bundled_texts_path)
    snapshot = snapshot_for(score)
    assert snapshot.band == band
    assert library.pick("mantou.interact", snapshot)


@pytest.mark.parametrize(
    ("score", "band"),
    [(10, "neutral"), (30, "warm"), (60, "close"), (100, "flirty"), (160, "intimate")],
)
def test_bundled_ambient_scene_covers_every_band(
    bundled_texts_path: Path, score: int, band: str
) -> None:
    library = AffectionTextLibrary(bundled_texts_path)
    snapshot = snapshot_for(score)
    assert snapshot.band == band
    assert library.pick("mantou.ambient", snapshot)


def test_seeded_copy_is_stable(tmp_path: Path) -> None:
    bundled = tmp_path / "bundled.json"
    bundled.write_text(
        json.dumps({"plugin.scene": {"flirty": ["第一句", "第二句"]}}, ensure_ascii=False),
        encoding="utf-8",
    )
    library = AffectionTextLibrary(bundled)
    snapshot = snapshot_for(100)

    first = library.pick("plugin.scene", snapshot, seed="2026-09-22")
    second = library.pick("plugin.scene", snapshot, seed="2026-09-22")
    assert first == second


def test_custom_copy_overrides_bundled_library(tmp_path: Path) -> None:
    bundled = tmp_path / "bundled.json"
    custom = tmp_path / "custom.json"
    _write_library(bundled, "内置文案")
    _write_library(custom, "自定义文案")

    library = AffectionTextLibrary(bundled, str(custom))
    assert library.pick("plugin.scene", snapshot_for(100)) == "自定义文案"


def test_missing_band_returns_none(tmp_path: Path) -> None:
    bundled = tmp_path / "bundled.json"
    _write_library(bundled, "只属于暧昧阶段")
    library = AffectionTextLibrary(bundled)
    assert library.pick("plugin.scene", snapshot_for(0)) is None
