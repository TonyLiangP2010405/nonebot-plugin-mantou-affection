import json
import random
from pathlib import Path

import pytest

from nonebot_plugin_mantou_affection.wheel_corpus import (
    BUNDLED_CORPUS_PATH,
    CorpusAssetMissing,
    GroupCorpus,
)


def _write(tmp_path: Path, payload) -> Path:
    path = tmp_path / "corpus.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_group_corpus_pick_returns_one_of_the_lines(tmp_path: Path) -> None:
    corpus = GroupCorpus(_write(tmp_path, {"lines": ["甲", "乙丙"]}), rng=random.Random(0))
    assert corpus.pick() in {"甲", "乙丙"}
    assert corpus.line_count == 2


@pytest.mark.parametrize(
    "payload",
    [{"lines": []}, {"lines": ["", "  "]}, {"lines": [1, None]}, {}, {"lines": "not-a-list"}],
)
def test_group_corpus_rejects_unusable_payload(tmp_path: Path, payload) -> None:
    corpus = GroupCorpus(_write(tmp_path, payload))
    with pytest.raises(CorpusAssetMissing):
        corpus.pick()


def test_group_corpus_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(CorpusAssetMissing):
        GroupCorpus(tmp_path / "nope.json").pick()


def test_bundled_corpus_has_ten_thousand_clean_lines() -> None:
    lines = json.loads(BUNDLED_CORPUS_PATH.read_text(encoding="utf-8"))["lines"]
    assert len(lines) == 10_000
    assert len(set(lines)) == 10_000
    assert all(isinstance(line, str) for line in lines)
    assert all(2 <= len(line.strip()) <= 60 for line in lines)
    assert all("http" not in line and "www." not in line for line in lines)
