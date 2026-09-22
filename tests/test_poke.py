import json
import random
from datetime import date
from pathlib import Path

import pytest

from nonebot_plugin_mantou_affection.config import Config
from nonebot_plugin_mantou_affection.copywriting import AffectionTextLibrary
from nonebot_plugin_mantou_affection.logic import (
    POKE_IGNORE_REPLY,
    POKE_NEGATIVE_FALLBACK,
    POKE_NEGATIVE_SCENE,
    POKE_POSITIVE_FALLBACK,
    POKE_POSITIVE_SCENE,
    perform_poke,
    snapshot_for,
)
from nonebot_plugin_mantou_affection.models import PokeResult, Profile
from nonebot_plugin_mantou_affection.service import AffectionService
from nonebot_plugin_mantou_affection.storage import AffectionStore

TODAY = date(2026, 9, 22)
BAND_TEXTS = {
    "neutral": "被戳到的{bot}往旁边挪了挪。",
    "warm": "{bot}小声说别戳啦。",
    "close": "{bot}鼓着脸把凳子挪远了。",
    "flirty": "{bot}闷闷地问你是不是只喜欢戳它。",
    "intimate": "{bot}轻轻握住你的手让你停下。",
}
NEGATIVE_TEXTS = {
    "neutral": "{bot}假装没被戳到，继续看别处。",
    "warm": "{bot}捂着被戳的地方，小声嘟囔。",
    "close": "{bot}按住你的手指，说再戳就不理你了。",
    "flirty": "{bot}酸酸地嘟囔你上次也是这样保证的。",
    "intimate": "{bot}叹了口气，说它不喜欢这样。",
}


class FixedRng(random.Random):
    """固定 random() 返回值，用来精确控制不耐烦判定的命中。"""

    def __init__(self, value: float):
        super().__init__(0)
        self.value = value

    def random(self) -> float:
        return self.value


def _library(tmp_path: Path) -> AffectionTextLibrary:
    path = tmp_path / "poke_texts.json"
    path.write_text(
        json.dumps(
            {
                POKE_POSITIVE_SCENE: {band: [text] for band, text in BAND_TEXTS.items()},
                POKE_NEGATIVE_SCENE: {band: [text] for band, text in NEGATIVE_TEXTS.items()},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return AffectionTextLibrary(path)


def _service(
    tmp_path: Path,
    text_library: AffectionTextLibrary | None = None,
    **config_kwargs,
) -> AffectionService:
    return AffectionService(
        AffectionStore(tmp_path / "affection.json"),
        Config(**config_kwargs),
        text_library=text_library,
        rng=FixedRng(0.9),
    )


def _poke(
    profile: Profile,
    *,
    rng: random.Random,
    today: date = TODAY,
    affection_max: int = 999,
    daily_gain_limit: int = 3,
    negative_base: float = 0.1,
    max_penalty: int = 5,
    ignore_threshold: int = 10,
    text_picker=None,
) -> PokeResult:
    return perform_poke(
        profile,
        today=today,
        now_timestamp=100.0,
        bot_name="馒头",
        negative_base=negative_base,
        max_penalty=max_penalty,
        ignore_threshold=ignore_threshold,
        daily_gain_limit=daily_gain_limit,
        affection_max=affection_max,
        rng=rng,
        text_picker=text_picker,
    )


def _profile(**kwargs) -> Profile:
    return Profile("1", poke_date=TODAY.isoformat(), **kwargs)


def test_ignore_reply_is_the_fixed_contract_text() -> None:
    assert POKE_IGNORE_REPLY == "馒头理都不想理你。"


@pytest.mark.parametrize(
    ("count_before", "value", "annoyed"),
    [
        (0, 0.05, True),
        (0, 0.15, False),
        (2, 0.25, True),
        (2, 0.35, False),
        (8, 0.85, True),
        (8, 0.95, False),
    ],
)
def test_poke_negative_probability_grows_with_daily_count(
    count_before: int, value: float, annoyed: bool
) -> None:
    profile = _profile(affection=100, poke_count=count_before)
    result = _poke(profile, rng=FixedRng(value))
    assert result.count == count_before + 1
    assert result.annoyed is annoyed


@pytest.mark.parametrize(
    ("count_before", "expected_penalty"),
    [(0, -1), (2, -3), (4, -5), (7, -5)],
)
def test_poke_penalty_grows_with_daily_count(count_before: int, expected_penalty: int) -> None:
    profile = _profile(affection=100, poke_count=count_before)
    result = _poke(profile, rng=FixedRng(0.0))
    assert result.annoyed is True
    assert result.delta == expected_penalty
    assert profile.affection == 100 + expected_penalty


def test_poke_penalty_stops_at_zero_affection() -> None:
    profile = _profile(affection=2, poke_count=4)
    result = _poke(profile, rng=FixedRng(0.0))
    assert result.annoyed is True
    assert result.delta == -2
    assert profile.affection == 0


@pytest.mark.parametrize("count_before", [9, 12])
def test_poke_ignore_stage_uses_fixed_reply(count_before: int) -> None:
    profile = _profile(affection=100, poke_count=count_before)
    result = _poke(profile, rng=FixedRng(0.0), text_picker=lambda scene, score: "不该用的文案")
    assert result.annoyed is True
    assert result.text == POKE_IGNORE_REPLY
    assert result.delta == -5
    assert profile.affection == 95


@pytest.mark.parametrize("count_before", [0, 3])
def test_poke_ignore_stage_after_affection_wiped(count_before: int) -> None:
    profile = _profile(affection=0, peak_affection=9, poke_count=count_before)
    result = _poke(profile, rng=FixedRng(0.9))
    assert result.annoyed is True
    assert result.delta == 0
    assert profile.affection == 0
    assert result.text == POKE_IGNORE_REPLY


@pytest.mark.parametrize("count_before", [0, 5, 8])
def test_new_user_below_threshold_uses_roll_path(count_before: int) -> None:
    profile = _profile(affection=0, peak_affection=0, poke_count=count_before)
    result = _poke(profile, rng=FixedRng(0.9))
    assert result.count == count_before + 1
    assert result.annoyed is False
    assert result.delta == 1
    assert profile.affection == 1
    assert result.text != POKE_IGNORE_REPLY


def test_new_user_annoyed_roll_keeps_zero_affection() -> None:
    profile = _profile(affection=0, peak_affection=0)
    scenes: list[str] = []

    def picker(scene: str, affection: int) -> str:
        scenes.append(scene)
        return NEGATIVE_TEXTS[snapshot_for(affection).band]

    result = _poke(profile, rng=FixedRng(0.0), text_picker=picker)
    assert result.annoyed is True
    assert result.delta == 0
    assert profile.affection == 0
    assert scenes == [POKE_NEGATIVE_SCENE]
    assert result.text != POKE_IGNORE_REPLY


def test_poke_positive_gain_counts_towards_daily_limit() -> None:
    profile = _profile(affection=20, gain_date=TODAY.isoformat(), gain_points=1)
    result = _poke(profile, rng=FixedRng(0.9))
    assert result.annoyed is False
    assert result.delta == 1
    assert profile.gain_points == 2


def test_poke_positive_gain_is_capped_by_daily_gain_limit() -> None:
    profile = _profile(affection=10, gain_date=TODAY.isoformat(), gain_points=3)
    result = _poke(profile, rng=FixedRng(0.9))
    assert result.annoyed is False
    assert result.delta == 0
    assert result.text
    assert profile.affection == 10
    assert profile.gain_points == 3


def test_poke_positive_copy_follows_affection_band() -> None:
    profile = _profile(affection=99)
    seen: list[tuple[str, int]] = []

    def picker(scene: str, affection: int) -> str:
        seen.append((scene, affection))
        return BAND_TEXTS[snapshot_for(affection).band]

    result = _poke(profile, rng=FixedRng(0.9), text_picker=picker)
    assert seen == [(POKE_POSITIVE_SCENE, 100)]
    assert result.text == BAND_TEXTS["flirty"].replace("{bot}", "馒头")
    assert "{bot}" not in result.text


def test_poke_negative_copy_follows_affection_after_penalty() -> None:
    profile = _profile(affection=101, poke_count=2)
    seen: list[tuple[str, int]] = []

    def picker(scene: str, affection: int) -> str:
        seen.append((scene, affection))
        return NEGATIVE_TEXTS[snapshot_for(affection).band]

    result = _poke(profile, rng=FixedRng(0.0), text_picker=picker)
    assert result.delta == -3
    assert seen == [(POKE_NEGATIVE_SCENE, 98)]
    assert result.text == NEGATIVE_TEXTS["close"].replace("{bot}", "馒头")


@pytest.mark.parametrize(
    ("fixed_value", "expected"),
    [(0.9, POKE_POSITIVE_FALLBACK), (0.0, POKE_NEGATIVE_FALLBACK)],
)
def test_poke_falls_back_without_library(fixed_value: float, expected: str) -> None:
    profile = _profile(affection=50)
    result = _poke(profile, rng=FixedRng(fixed_value))
    assert result.text == expected


def test_poke_reply_falls_back_when_scene_is_empty() -> None:
    profile = _profile(affection=50)
    result = _poke(profile, rng=FixedRng(0.9), text_picker=lambda scene, score: None)
    assert result.text == POKE_POSITIVE_FALLBACK


def test_poke_resets_counter_next_day() -> None:
    profile = Profile("1", affection=50, poke_date="2026-09-21", poke_count=9)
    result = _poke(profile, rng=FixedRng(0.9))
    assert result.count == 1
    assert result.annoyed is False
    assert profile.poke_date == TODAY.isoformat()
    assert profile.poke_count == 1


def test_poke_resets_daily_gain_next_day() -> None:
    profile = Profile(
        "1",
        affection=50,
        poke_date="2026-09-21",
        poke_count=3,
        gain_date="2026-09-21",
        gain_points=3,
    )
    result = _poke(profile, rng=FixedRng(0.9))
    assert result.delta == 1
    assert profile.gain_points == 1


async def test_service_poke_uses_library_and_normalizes_nickname(tmp_path: Path) -> None:
    service = _service(tmp_path, _library(tmp_path))

    def bump(profile: Profile) -> None:
        profile.affection = 30

    await service.store.update_profile("100", "200", "桃友", bump)
    result = await service.poke("100", "200", "  桃  \n 友 ")

    assert result.count == 1
    assert result.delta == 1
    assert result.annoyed is False
    assert result.text == BAND_TEXTS["warm"].replace("{bot}", "馒头")
    profile = await service.profile("100", "200")
    assert profile.nickname == "桃 友"


async def test_service_poke_reaches_ignore_stage(tmp_path: Path) -> None:
    service = _service(tmp_path, _library(tmp_path), mantou_affection_daily_gain_limit=999)

    def bump(profile: Profile) -> None:
        profile.affection = 60

    await service.store.update_profile("100", "200", "桃友", bump)

    for expected_count in range(1, 10):
        result = await service.poke("100", "200", "桃友")
        assert result.count == expected_count
        assert result.annoyed is False
        assert result.delta == 1

    result = await service.poke("100", "200", "桃友")
    assert result.count == 10
    assert result.annoyed is True
    assert result.text == POKE_IGNORE_REPLY
    assert result.delta == -5
    assert (await service.profile("100", "200")).affection == 60 + 9 - 5


async def test_new_user_is_poked_normally(tmp_path: Path) -> None:
    service = _service(tmp_path, _library(tmp_path), mantou_affection_daily_gain_limit=10)

    deltas = []
    for expected_count in (1, 2, 3):
        result = await service.poke("100", "200", "桃友")
        assert result.count == expected_count
        assert result.annoyed is False
        assert result.text != POKE_IGNORE_REPLY
        deltas.append(result.delta)

    assert deltas == [1, 1, 1]
    profile = await service.profile("100", "200")
    assert profile.affection == 3
    assert profile.peak_affection == 3


async def test_peak_affection_tracks_every_gain_and_survives_reset(tmp_path: Path) -> None:
    service = _service(tmp_path, _library(tmp_path), mantou_affection_daily_gain_limit=10)

    await service.adjust("100", "200", "桃友", 4)
    assert (await service.profile("100", "200")).peak_affection == 4

    await service.reward_external("100", "200", "桃友", "nonebot_plugin_taozi", 2)
    profile = await service.profile("100", "200")
    assert profile.affection == 6
    assert profile.peak_affection == 6

    assert (await service.poke("100", "200", "桃友")).delta == 1
    profile = await service.profile("100", "200")
    assert profile.affection == 7
    assert profile.peak_affection == 7

    await service.adjust("100", "200", "桃友", -20)
    profile = await service.profile("100", "200")
    assert profile.affection == 0
    assert profile.peak_affection == 7

    result = await service.poke("100", "200", "桃友")
    assert result.annoyed is True
    assert result.delta == 0
    assert result.text == POKE_IGNORE_REPLY


async def test_public_poke_api_returns_poke_result(
    bundled_texts_path: Path, monkeypatch
) -> None:
    from nonebot_plugin_mantou_affection import PokeResult as ExportedResult
    from nonebot_plugin_mantou_affection import poke, service

    assert ExportedResult is PokeResult

    texts = json.loads(bundled_texts_path.read_text(encoding="utf-8"))["crystelf.poke"]
    monkeypatch.setattr(service, "rng", FixedRng(0.9))

    result = await poke("poke-api-group", "poke-api-user", nickname="桃友")

    assert isinstance(result, PokeResult)
    assert result.count == 1
    assert result.annoyed is False
    assert result.delta == 1
    assert result.text in texts["neutral"]
