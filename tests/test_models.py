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


def test_profile_ignores_removed_poke_fields() -> None:
    profile = Profile.from_dict(
        "1",
        {
            "affection": 5,
            "poke_date": "2026-09-22",
            "poke_count": 9,
            "peak_affection": 30,
            "zeroed_date": "2026-09-22",
        },
    )

    assert profile.affection == 5
    data = profile.to_dict()
    assert "poke_date" not in data
    assert "poke_count" not in data
    assert "peak_affection" not in data
    assert "zeroed_date" not in data
