import pytest

from nonebot_plugin_mantou_affection.models import Profile


def test_profile_defaults_have_empty_gain_fields() -> None:
    profile = Profile.from_dict("1", {})
    assert profile.gain_date == ""
    assert profile.gain_points == 0


def test_profile_without_data_has_empty_gain_fields() -> None:
    profile = Profile.from_dict("1", None)
    assert profile.gain_date == ""
    assert profile.gain_points == 0


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ({"gain_date": "2026-09-21", "gain_points": 2}, ("2026-09-21", 2)),
        ({"gain_date": "2026-09-21"}, ("2026-09-21", 0)),
        ({"gain_points": "3"}, ("", 3)),
        ({"gain_date": "2026-09-21", "gain_points": "abc"}, ("2026-09-21", 0)),
        ({"gain_date": "2026-09-21", "gain_points": None}, ("2026-09-21", 0)),
        ({"gain_date": "2026-09-21", "gain_points": [1]}, ("2026-09-21", 0)),
        ({"gain_date": "2026-09-21", "gain_points": -5}, ("2026-09-21", 0)),
    ],
)
def test_profile_gain_fields_tolerate_dirty_data(raw: dict, expected: tuple) -> None:
    profile = Profile.from_dict("1", raw)
    assert (profile.gain_date, profile.gain_points) == expected


def test_profile_round_trip_keeps_gain_fields() -> None:
    profile = Profile("1", gain_date="2026-09-21", gain_points=2)
    data = profile.to_dict()
    assert data["gain_date"] == "2026-09-21"
    assert data["gain_points"] == 2
    assert "user_id" not in data

    restored = Profile.from_dict("1", data)
    assert restored.gain_date == "2026-09-21"
    assert restored.gain_points == 2


def test_profile_defaults_have_empty_poke_fields() -> None:
    profile = Profile.from_dict("1", {})
    assert profile.poke_date == ""
    assert profile.poke_count == 0


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ({"poke_date": "2026-09-22", "poke_count": 3}, ("2026-09-22", 3)),
        ({"poke_date": "2026-09-22"}, ("2026-09-22", 0)),
        ({"poke_count": "4"}, ("", 4)),
        ({"poke_date": "2026-09-22", "poke_count": "abc"}, ("2026-09-22", 0)),
        ({"poke_date": "2026-09-22", "poke_count": None}, ("2026-09-22", 0)),
        ({"poke_date": "2026-09-22", "poke_count": -3}, ("2026-09-22", 0)),
    ],
)
def test_profile_poke_fields_tolerate_dirty_data(raw: dict, expected: tuple) -> None:
    profile = Profile.from_dict("1", raw)
    assert (profile.poke_date, profile.poke_count) == expected


def test_profile_round_trip_keeps_poke_fields() -> None:
    profile = Profile("1", poke_date="2026-09-22", poke_count=3)
    data = profile.to_dict()
    assert data["poke_date"] == "2026-09-22"
    assert data["poke_count"] == 3

    restored = Profile.from_dict("1", data)
    assert restored.poke_date == "2026-09-22"
    assert restored.poke_count == 3


def test_profile_defaults_have_zero_peak_affection() -> None:
    assert Profile.from_dict("1", {}).peak_affection == 0
    assert Profile.from_dict("1", None).peak_affection == 0


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ({"affection": 5, "peak_affection": 9}, 9),
        ({"affection": 0, "peak_affection": 9}, 9),
        ({"affection": 5}, 5),
        ({"affection": 5, "peak_affection": "abc"}, 5),
        ({"affection": 5, "peak_affection": None}, 5),
        ({"affection": 5, "peak_affection": -3}, 5),
        ({"peak_affection": 4}, 4),
    ],
)
def test_profile_peak_affection_tolerates_legacy_data(raw: dict, expected: int) -> None:
    assert Profile.from_dict("1", raw).peak_affection == expected


def test_profile_round_trip_keeps_peak_affection() -> None:
    profile = Profile("1", affection=3, peak_affection=8)
    data = profile.to_dict()
    assert data["peak_affection"] == 8

    assert Profile.from_dict("1", data).peak_affection == 8
