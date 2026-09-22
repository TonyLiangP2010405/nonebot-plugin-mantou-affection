import pytest

from nonebot_plugin_mantou_affection.commands import _format_probability, _format_wait
from nonebot_plugin_mantou_affection.config import Config


def test_interaction_defaults_are_five_times_and_two_hours() -> None:
    config = Config()
    assert config.mantou_affection_interaction_limit == 5
    assert config.mantou_affection_interaction_cooldown == 7200


def test_interaction_cooldown_accepts_two_hours() -> None:
    config = Config(mantou_affection_interaction_cooldown=7200)
    assert config.mantou_affection_interaction_cooldown == 7200


def test_ambient_defaults_are_enabled_at_one_percent() -> None:
    config = Config()
    assert config.mantou_affection_ambient_enabled is True
    assert config.mantou_affection_ambient_probability == 0.01


def test_format_wait_seconds() -> None:
    assert _format_wait(1) == "1 秒"
    assert _format_wait(59) == "59 秒"


def test_format_wait_minutes() -> None:
    assert _format_wait(60) == "1 分钟"
    assert _format_wait(3599) == "59 分钟"


def test_format_wait_hours() -> None:
    assert _format_wait(3600) == "1 小时"
    assert _format_wait(3601) == "1 小时"
    assert _format_wait(7260) == "2 小时 1 分钟"
    assert _format_wait(7320) == "2 小时 2 分钟"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0.01, "1%"),
        (0.005, "0.5%"),
        (0.0123, "1.23%"),
        (0.0, "0%"),
        (1.0, "100%"),
    ],
)
def test_format_probability(value: float, expected: str) -> None:
    assert _format_probability(value) == expected


def test_registered_matchers_include_probability_command() -> None:
    from nonebot_plugin_mantou_affection import matchers

    (
        _profile_cmd,
        _interact_cmd,
        _ranking_cmd,
        _help_cmd,
        _adjust_cmd,
        probability_cmd,
    ) = matchers
    assert probability_cmd.priority == 10
    assert probability_cmd.block is True
    assert [handler.call.__name__ for handler in probability_cmd.handlers] == [
        "handle_probability"
    ]
