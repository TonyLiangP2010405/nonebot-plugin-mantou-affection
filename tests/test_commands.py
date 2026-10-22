from pathlib import Path
from time import time

import pytest
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment
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
    assert config.mantou_affection_ambient_probability == 0.0005


def test_daily_gain_default_is_three_points() -> None:
    assert Config().mantou_affection_daily_gain_limit == 3
    assert Config(mantou_affection_daily_gain_limit=2).mantou_affection_daily_gain_limit == 2


def test_random_event_defaults() -> None:
    config = Config()
    assert config.mantou_affection_ambient_event_ratio == 0.333
    assert config.mantou_affection_event_timeout == 20
    assert config.mantou_affection_event_timeout_penalty == 5
    assert config.mantou_affection_upset_event_timeout == 60


def test_link_notify_default_is_on() -> None:
    assert Config().mantou_affection_link_notify is True
    assert Config(mantou_affection_link_notify=False).mantou_affection_link_notify is False


def test_poke_event_chance_default() -> None:
    assert Config().mantou_affection_poke_event_chance == 0.0005
    assert Config(mantou_affection_poke_event_chance=0.5).mantou_affection_poke_event_chance == 0.5


def test_wheel_defaults() -> None:
    config = Config()
    assert config.mantou_affection_wheel_chance == 0.001
    assert config.mantou_affection_wheel_timeout == 60


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

    assert len(matchers) == 14
    (
        _profile_cmd,
        _interact_cmd,
        _ranking_cmd,
        _help_cmd,
        _adjust_cmd,
        _reset_cmd,
        probability_cmd,
        find_char_cmd,
        bet_cmd,
        pk_probability_cmd,
        pk_bank_cmd,
        wheel_cmd,
        group_quiz_cmd,
        meme_whitelist_cmd,
    ) = matchers
    assert probability_cmd.priority == 10
    assert probability_cmd.block is True
    assert [handler.call.__name__ for handler in probability_cmd.handlers] == [
        "handle_probability"
    ]
    assert find_char_cmd.priority == 10
    assert find_char_cmd.block is True
    assert [handler.call.__name__ for handler in find_char_cmd.handlers] == [
        "handle_find_char_chance"
    ]
    assert bet_cmd.priority == 10
    assert bet_cmd.block is True
    assert [handler.call.__name__ for handler in bet_cmd.handlers] == [
        "handle_bet_probability"
    ]
    expected_handlers = {
        pk_probability_cmd: "handle_pk_probability",
        pk_bank_cmd: "handle_pk_bank",
        wheel_cmd: "handle_wheel_probability",
        group_quiz_cmd: "handle_group_quiz_probability",
        meme_whitelist_cmd: "handle_meme_whitelist",
    }
    for matcher, handler_name in expected_handlers.items():
        assert matcher.priority == 10
        assert matcher.block is True
        assert [handler.call.__name__ for handler in matcher.handlers] == [handler_name]


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


def _at_message(*targets: str) -> Message:
    message = Message()
    for index, target in enumerate(targets):
        if index:
            message.append(MessageSegment.text(" "))
        message.append(MessageSegment.at(target))
    return message


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


async def test_reset_command_requires_confirmation(tmp_path: Path, monkeypatch) -> None:
    service = AffectionService(AffectionStore(tmp_path / "affection.json"), Config())

    def bump(profile: Profile) -> None:
        profile.affection = 10

    await service.store.update_profile("1", "10", "甲", bump)

    reset_cmd = register_commands(service, Config())[5]
    sent: list[str] = []

    async def fake_finish(message: Message | str = "", **kwargs) -> None:
        sent.append(str(message))

    monkeypatch.setattr(reset_cmd, "finish", fake_finish)
    handler = reset_cmd.handlers[0]

    await handler.call(Message(""))
    assert "不可恢复" in sent[-1]
    assert (await service.store.get_profile("1", "10")).affection == 10

    await handler.call(Message("再看看"))
    assert (await service.store.get_profile("1", "10")).affection == 10

    await handler.call(Message("确认"))
    assert "共清空 1 条" in sent[-1]
    assert (await service.store.get_profile("1", "10")).affection == 0


async def test_wheel_probability_command_query_and_set(tmp_path: Path, monkeypatch) -> None:
    service = AffectionService(AffectionStore(tmp_path / "affection.json"), Config())
    wheel_cmd = register_commands(service, Config())[11]
    sent = []

    async def fake_finish(message: Message | str = "", **kwargs) -> None:
        sent.append(str(message))

    monkeypatch.setattr(wheel_cmd, "finish", fake_finish)
    handler = wheel_cmd.handlers[0]
    await handler.call(Message(""))
    assert "当前馒头转盘概率：0.1%" in sent[-1]
    await handler.call(Message("0.2%"))
    assert "已调整为 0.2%" in sent[-1]
    assert await service.wheel_probability() == 0.002
    await handler.call(Message("not-a-probability"))
    assert "请输入 0~1" in sent[-1]
    assert await service.wheel_probability() == 0.002


def _meme_whitelist_handler(tmp_path: Path, monkeypatch) -> tuple:
    service = AffectionService(AffectionStore(tmp_path / "affection.json"), Config())
    meme_whitelist_cmd = register_commands(service, Config())[13]
    sent: list[str] = []

    async def fake_finish(message: Message | str = "", **kwargs) -> None:
        sent.append(str(message))

    monkeypatch.setattr(meme_whitelist_cmd, "finish", fake_finish)
    return service, meme_whitelist_cmd.handlers[0], sent


async def test_meme_whitelist_command_toggles_only_self(
    tmp_path: Path, monkeypatch
) -> None:
    service, handler, sent = _meme_whitelist_handler(tmp_path, monkeypatch)

    await handler.call(_event(group_id=90001, user_id=90002), Message(""))
    assert "已加入表情免确认白名单" in sent[-1]
    assert await service.meme_confirm_whitelist() == {"90002"}

    reopened = AffectionService(AffectionStore(tmp_path / "affection.json"), Config())
    assert await reopened.meme_confirm_whitelist() == {"90002"}

    # 白名单全局生效: 换个群再发一次仍然切换的是同一个人
    await handler.call(_event(group_id=90002, user_id=90002), Message(""))
    assert "已退出表情免确认白名单" in sent[-1]
    assert await service.meme_confirm_whitelist() == set()

    # 普通群友带参数(哪怕 @ 别人)只提示管理自己, 不会改动任何人的状态
    await handler.call(_event(), Message(MessageSegment.at("90099")))
    assert "只能管理自己" in sent[-1]
    assert await service.meme_confirm_whitelist() == set()

    await handler.call(_event(), Message("90099"))
    assert "只能管理自己" in sent[-1]
    assert await service.meme_confirm_whitelist() == set()

    multiple = _at_message("90098", "90099")
    await handler.call(_event(), multiple)
    assert "只能管理自己" in sent[-1]
    assert await service.meme_confirm_whitelist() == set()


async def test_meme_whitelist_superuser_toggles_target_by_at(
    tmp_path: Path, monkeypatch
) -> None:
    service, handler, sent = _meme_whitelist_handler(tmp_path, monkeypatch)
    await service.store.update_profile("90001", "90003", "桃友", lambda _profile: None)

    await handler.call(_event(user_id=10001), Message(MessageSegment.at("90003")))
    assert "已为 桃友(90003) 加入表情免确认白名单" in sent[-1]
    assert await service.meme_confirm_whitelist() == {"90003"}

    await handler.call(_event(user_id=10001), Message(MessageSegment.at("90003")))
    assert "已为 桃友(90003) 退出表情免确认白名单" in sent[-1]
    assert await service.meme_confirm_whitelist() == set()


async def test_meme_whitelist_superuser_toggles_target_by_qq(
    tmp_path: Path, monkeypatch
) -> None:
    service, handler, sent = _meme_whitelist_handler(tmp_path, monkeypatch)

    await handler.call(_event(user_id=10001), Message("90004"))
    assert "已为 90004 加入表情免确认白名单" in sent[-1]
    assert await service.meme_confirm_whitelist() == {"90004"}

    await handler.call(_event(user_id=10001), Message("90004"))
    assert "已为 90004 退出表情免确认白名单" in sent[-1]
    assert await service.meme_confirm_whitelist() == set()


async def test_meme_whitelist_superuser_rejects_multiple_targets(
    tmp_path: Path, monkeypatch
) -> None:
    service, handler, sent = _meme_whitelist_handler(tmp_path, monkeypatch)

    two_at = _at_message("90003", "90004")
    await handler.call(_event(user_id=10001), two_at)
    assert "一次只能操作一个人" in sent[-1]
    assert await service.meme_confirm_whitelist() == set()

    await handler.call(_event(user_id=10001), Message("90003 90004"))
    assert "一次只能操作一个人" in sent[-1]
    assert await service.meme_confirm_whitelist() == set()

    await handler.call(_event(user_id=10001), Message("90003，90004"))
    assert "一次只能操作一个人" in sent[-1]
    assert await service.meme_confirm_whitelist() == set()

    mixed = _at_message("90003")
    mixed.append(MessageSegment.text(" 90004"))
    await handler.call(_event(user_id=10001), mixed)
    assert "一次只能操作一个人" in sent[-1]
    assert await service.meme_confirm_whitelist() == set()

    await handler.call(_event(user_id=10001), Message("不是 QQ 号"))
    assert "只能 @ 一位群友或填写一个 QQ 号" in sent[-1]
    assert await service.meme_confirm_whitelist() == set()


async def test_meme_whitelist_superuser_without_args_toggles_self(
    tmp_path: Path, monkeypatch
) -> None:
    service, handler, sent = _meme_whitelist_handler(tmp_path, monkeypatch)

    await handler.call(_event(user_id=10001), Message(""))
    assert "已加入表情免确认白名单" in sent[-1]
    assert await service.meme_confirm_whitelist() == {"10001"}

    await handler.call(_event(user_id=10001), Message(""))
    assert "已退出表情免确认白名单" in sent[-1]
    assert await service.meme_confirm_whitelist() == set()
