from pathlib import Path
from time import time

import pytest
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message
from nonebot.adapters.onebot.v11.event import Sender

from nonebot_plugin_mantou_affection.commands import (
    _format_probability,
    _format_wait,
    _gain_text,
    register_commands,
)
from nonebot_plugin_mantou_affection.config import Config
from nonebot_plugin_mantou_affection.models import Profile
from nonebot_plugin_mantou_affection.service import AffectionService
from nonebot_plugin_mantou_affection.storage import AffectionStore


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


def test_daily_gain_default_is_three_points() -> None:
    assert Config().mantou_affection_daily_gain_limit == 3
    assert Config(mantou_affection_daily_gain_limit=2).mantou_affection_daily_gain_limit == 2


def test_random_event_defaults() -> None:
    config = Config()
    assert config.mantou_affection_ambient_event_ratio == 0.333
    assert config.mantou_affection_event_timeout == 20
    assert config.mantou_affection_event_timeout_penalty == 5


def test_link_notify_default_is_on() -> None:
    assert Config().mantou_affection_link_notify is True
    assert Config(mantou_affection_link_notify=False).mantou_affection_link_notify is False


def test_poke_defaults() -> None:
    config = Config()
    assert config.mantou_affection_poke_negative_base == 0.1
    assert config.mantou_affection_poke_max_penalty == 5
    assert config.mantou_affection_poke_ignore_threshold == 10


def test_gain_text_shows_sign() -> None:
    assert _gain_text(3) == "好感度 +3"
    assert _gain_text(1) == "好感度 +1"
    assert _gain_text(0) == "好感度没有变化"
    assert _gain_text(-1) == "好感度 -1"


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


def _event(group_id: int = 90001, user_id: int = 90002) -> GroupMessageEvent:
    message = Message("馒头好感")
    return GroupMessageEvent(
        time=int(time()),
        self_id=10000,
        post_type="message",
        sub_type="normal",
        user_id=user_id,
        message_type="group",
        message_id=1,
        message=message,
        original_message=message,
        raw_message="馒头好感",
        font=0,
        sender=Sender(user_id=user_id, nickname="桃友", role="member"),
        to_me=False,
        group_id=group_id,
    )


async def test_profile_command_reports_daily_gain(tmp_path: Path, monkeypatch) -> None:
    service = AffectionService(AffectionStore(tmp_path / "affection.json"), Config())
    today = service._now().date().isoformat()

    def bump(profile: Profile) -> None:
        profile.gain_date = today
        profile.gain_points = 2

    await service.store.update_profile("90001", "90002", "桃友", bump)

    profile_cmd = register_commands(service, Config())[0]
    sent: list[str] = []

    async def fake_finish(message: Message | str = "", **kwargs) -> None:
        sent.append(str(message))

    monkeypatch.setattr(profile_cmd, "finish", fake_finish)
    await profile_cmd.handlers[0].call(_event())

    assert len(sent) == 1
    assert "今日好感获取：2/3" in sent[0]
