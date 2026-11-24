from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from nonebot_plugin_mantou_affection.config import Config
from nonebot_plugin_mantou_affection.copywriting import AffectionTextLibrary
from nonebot_plugin_mantou_affection.logic import INTERACTIONS, snapshot_for
from nonebot_plugin_mantou_affection.models import Profile
from nonebot_plugin_mantou_affection.service import AffectionService
from nonebot_plugin_mantou_affection.storage import AffectionStore

BAND_TEXTS = {
    "neutral": "{bot}礼貌地朝你点了点头。",
    "warm": "{bot}往你这边挪近了一点点。",
    "close": "{bot}把脑袋搁在你手边。",
    "flirty": "{bot}在你名字旁边画了一颗小桃心。",
    "intimate": "{bot}把最软的那一块留给你。",
}
BAND_SCORES = ((0, "neutral"), (30, "warm"), (60, "close"), (100, "flirty"), (160, "intimate"))
NEGATIVE_TEXTS = {
    "neutral": "{bot}往蒸笼里缩了缩，假装什么都没发生。",
    "warm": "{bot}抿着嘴不说话，眼睛有点湿。",
    "close": "{bot}哼了一声，把凳子挪开了半寸。",
    "flirty": "{bot}把桃心糖放回口袋，说自己今天不吃甜的了。",
    "intimate": "{bot}安静了很久，最后只是说了句没关系。",
}
GAIN_SEED = 7
LOSS_SEED = 0
AMBIENT_TEXTS = {
    "neutral": "馒头回头看了看你。",
    "warm": "馒头往你这边挪了半步。",
    "close": "馒头把面包放到你手边。",
    "flirty": "馒头在你名字旁边画了小桃心。",
    "intimate": "馒头把脑袋搁在你手边。",
}
AMBIENT_SCORES = (
    (10, "neutral"),
    (30, "warm"),
    (60, "close"),
    (100, "flirty"),
    (160, "intimate"),
)


def _library(tmp_path: Path) -> AffectionTextLibrary:
    path = tmp_path / "interact_texts.json"
    path.write_text(
        json.dumps(
            {
                "mantou.interact": {band: [text] for band, text in BAND_TEXTS.items()},
                "mantou.interact.negative": {
                    band: [text] for band, text in NEGATIVE_TEXTS.items()
                },
                "mantou.ambient": {band: [text] for band, text in AMBIENT_TEXTS.items()},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return AffectionTextLibrary(path)


def _service(
    tmp_path: Path,
    *,
    text_library: AffectionTextLibrary | None = None,
    rng: random.Random | None = None,
    **config_kwargs,
) -> AffectionService:
    return AffectionService(
        AffectionStore(tmp_path / "affection.json"),
        Config(**config_kwargs),
        text_library=text_library,
        rng=rng or random.Random(0),
    )


async def _set_affection(service: AffectionService, score: int) -> None:
    def bump(profile: Profile) -> None:
        profile.affection = score

    await service.store.update_profile("100", "200", "桃友", bump)


@pytest.mark.parametrize(("score", "band"), BAND_SCORES)
async def test_interact_text_follows_affection_band(tmp_path: Path, score: int, band: str) -> None:
    assert random.Random(GAIN_SEED).choice(INTERACTIONS)[1] > 0
    service = _service(tmp_path, text_library=_library(tmp_path), rng=random.Random(GAIN_SEED))

    def bump(profile: Profile) -> None:
        profile.affection = score

    await service.store.update_profile("100", "200", "桃友", bump)
    result = await service.interact("100", "200", "桃友")
    assert result.accepted is True
    assert result.text == BAND_TEXTS[band].replace("{bot}", "馒头")


@pytest.mark.parametrize("score", [0, 30, 60, 100, 160])
async def test_interact_negative_reward_uses_negative_scene(tmp_path: Path, score: int) -> None:
    assert random.Random(LOSS_SEED).choice(INTERACTIONS)[1] == -1
    service = _service(tmp_path, text_library=_library(tmp_path), rng=random.Random(LOSS_SEED))

    def bump(profile: Profile) -> None:
        profile.affection = score

    await service.store.update_profile("100", "200", "桃友", bump)
    result = await service.interact("100", "200", "桃友")

    assert result.accepted is True
    assert result.affection == max(0, score - 1)
    band = snapshot_for(result.affection).band
    assert result.text == NEGATIVE_TEXTS[band].replace("{bot}", "馒头")


async def test_interact_without_library_uses_builtin_copy(tmp_path: Path) -> None:
    service = _service(tmp_path)
    result = await service.interact("100", "200", "桃友")
    assert result.accepted is True
    assert result.text in {text.format(bot="馒头") for text, _ in INTERACTIONS}


@pytest.mark.parametrize(("score", "band"), AMBIENT_SCORES)
async def test_ambient_reaction_follows_affection_band(
    tmp_path: Path, score: int, band: str
) -> None:
    service = _service(
        tmp_path,
        text_library=_library(tmp_path),
    )
    await _set_affection(service, score)
    assert await service.ambient_reaction("100", "200") == AMBIENT_TEXTS[band]


async def test_ambient_reaction_is_silent_when_disabled(tmp_path: Path) -> None:
    service = _service(
        tmp_path,
        text_library=_library(tmp_path),
        mantou_affection_ambient_enabled=False,
    )
    await _set_affection(service, 100)
    assert await service.ambient_reaction("100", "200") is None


async def test_ambient_reaction_skips_zero_affection(tmp_path: Path) -> None:
    service = _service(
        tmp_path,
        text_library=_library(tmp_path),
    )
    assert await service.ambient_reaction("100", "200") is None


async def test_ambient_reaction_without_library_returns_none(tmp_path: Path) -> None:
    service = _service(tmp_path)
    await _set_affection(service, 100)
    assert await service.ambient_reaction("100", "200") is None


async def test_event_wheel_probability_prefers_stored_override(tmp_path: Path) -> None:
    service = _service(tmp_path)
    assert await service.event_wheel_probability() == 0.015

    await service.set_event_wheel_probability(0.05)
    assert await service.event_wheel_probability() == 0.05
    assert await service.event_wheel_probability_override() == 0.05

    restarted = AffectionService(
        AffectionStore(tmp_path / "affection.json"),
        Config(),
        rng=random.Random(0),
    )
    assert await restarted.event_wheel_probability() == 0.05


async def test_event_wheel_probability_ignores_broken_setting(tmp_path: Path) -> None:
    service = _service(tmp_path, mantou_affection_event_wheel_chance=0.2)
    await service.store.set_setting("event_wheel_probability", "not a number")
    assert await service.event_wheel_probability() == 0.2


async def test_event_wheel_triggered_follows_probability(tmp_path: Path) -> None:
    always = _service(tmp_path, mantou_affection_event_wheel_chance=1.0)
    assert await always.event_wheel_triggered() is True

    never = _service(tmp_path, mantou_affection_event_wheel_chance=0.0)
    assert await never.event_wheel_triggered() is False


async def test_ambient_reaction_propagates_store_errors(tmp_path: Path, monkeypatch) -> None:
    service = _service(
        tmp_path,
        text_library=_library(tmp_path),
    )

    async def broken(group_id: str, user_id: str) -> Profile:
        raise RuntimeError("store is down")

    monkeypatch.setattr(service.store, "get_profile", broken)
    with pytest.raises(RuntimeError, match="store is down"):
        await service.ambient_reaction("100", "200")


async def test_wheel_probability_prefers_stored_override(tmp_path: Path) -> None:
    service = _service(tmp_path)
    assert await service.wheel_probability() == 0.001
    assert await service.wheel_probability_override() is None

    await service.set_wheel_probability(0.05)
    assert await service.wheel_probability() == 0.05
    assert await service.wheel_probability_override() == 0.05

    restarted = _service(tmp_path)
    assert await restarted.wheel_probability() == 0.05


async def test_wheel_triggered_requires_positive_affection(tmp_path: Path) -> None:
    service = _service(tmp_path, mantou_affection_wheel_chance=1.0)
    assert await service.wheel_triggered("100", "200") is False
    await _set_affection(service, 10)
    assert await service.wheel_triggered("100", "200") is True

    disabled = _service(tmp_path, mantou_affection_wheel_chance=0.0)
    await disabled.set_wheel_probability(0.0)
    assert await disabled.wheel_triggered("100", "200") is False


def test_gained_points_today_ignores_stale_date(tmp_path: Path) -> None:
    service = _service(tmp_path)
    today = service._now().date().isoformat()
    assert service.gained_points_today(Profile("1", gain_date=today, gain_points=2)) == 2
    assert service.gained_points_today(Profile("1", gain_date="2000-01-01", gain_points=2)) == 0


async def test_daily_gain_budget_is_shared_by_interact_and_link(tmp_path: Path) -> None:
    service = _service(
        tmp_path, mantou_affection_daily_gain_limit=3, rng=random.Random(GAIN_SEED)
    )
    assert random.Random(GAIN_SEED).choice(INTERACTIONS)[1] == 2

    first = await service.interact("100", "200", "桃友")
    assert first.accepted is True
    assert first.delta == 2

    second = await service.reward_external(
        "100", "200", "桃友", "nonebot_plugin_taozi", 2
    )
    assert second.delta == 1

    third = await service.reward_external(
        "100", "200", "桃友", "nonebot_plugin_taozi_music", 2
    )
    assert third.accepted is True
    assert third.delta == 0

    profile = await service.profile("100", "200")
    assert profile.affection == 3
    assert profile.gain_points == 3
    assert service.gained_points_today(profile) == 3


async def test_probability_states_show_defaults_and_overrides(tmp_path: Path) -> None:
    service = _service(tmp_path)
    states = await service.probability_states()

    assert [state.label for state in states] == ["事件转盘", "馒头转盘"]
    assert [state.key for state in states] == [
        "event_wheel_probability",
        "wheel_probability",
    ]
    assert all(state.overridden is False for state in states)
    assert all(state.value == state.default for state in states)
    assert states[0].value == pytest.approx(0.015)
    assert states[1].value == pytest.approx(0.001)

    await service.set_event_wheel_probability(0.2)
    states = await service.probability_states()
    event_wheel = next(state for state in states if state.label == "事件转盘")
    assert event_wheel.value == pytest.approx(0.2)
    assert event_wheel.default == pytest.approx(0.015)
    assert event_wheel.overridden is True


async def test_set_all_probabilities_persists_every_field(tmp_path: Path) -> None:
    service = _service(tmp_path)
    await service.set_all_probabilities(0.01)

    states = await service.probability_states()
    assert len(states) == 2
    assert all(state.value == pytest.approx(0.01) for state in states)
    assert all(state.overridden is True for state in states)

    payload = json.loads((tmp_path / "affection.json").read_text(encoding="utf-8"))
    assert len(payload["settings"]) == 2


async def test_scale_all_probabilities_uses_effective_values(tmp_path: Path) -> None:
    service = _service(tmp_path)
    await service.set_event_wheel_probability(0.01)

    changes = await service.scale_all_probabilities(2)
    by_label = {change.label: change for change in changes}
    assert by_label["事件转盘"].before == pytest.approx(0.01)
    assert by_label["事件转盘"].after == pytest.approx(0.02)
    assert by_label["馒头转盘"].before == pytest.approx(0.001)
    assert by_label["馒头转盘"].after == pytest.approx(0.002)
    assert await service.wheel_probability() == pytest.approx(0.002)


async def test_scale_all_probabilities_clamps_at_one(tmp_path: Path) -> None:
    service = _service(tmp_path)
    await service.set_all_probabilities(0.5)

    changes = await service.scale_all_probabilities(10)
    assert all(change.after == 1.0 for change in changes)


async def test_reset_all_probabilities_cleans_legacy_event_overrides(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    await service.set_event_wheel_probability(0.05)
    await service.store.set_setting("find_char_group_chance", 0.5)
    await service.store.set_setting("whoami_probability", 0.2)

    assert await service.reset_all_probabilities() == 3
    assert await service.store.get_setting("find_char_group_chance", None) is None
    assert await service.store.get_setting("whoami_probability", None) is None


async def test_reset_all_probabilities_keeps_other_settings(tmp_path: Path) -> None:
    service = _service(tmp_path)
    await service.set_all_probabilities(0)
    await service.store.set_setting("meme_confirm_whitelist", ["10001"])

    assert await service.reset_all_probabilities() == 2
    states = await service.probability_states()
    assert all(state.overridden is False for state in states)
    assert await service.store.get_setting("meme_confirm_whitelist", []) == ["10001"]
    assert await service.reset_all_probabilities() == 0
