from __future__ import annotations

from pathlib import Path
from time import time

import pytest
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message
from nonebot.adapters.onebot.v11.event import Sender
from nonebot.exception import FinishedException

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


def test_event_wheel_defaults() -> None:
    config = Config()
    assert config.mantou_affection_ambient_enabled is True
    assert config.mantou_affection_event_wheel_chance == 0.015


def test_daily_gain_default_is_three_points() -> None:
    assert Config().mantou_affection_daily_gain_limit == 3
    assert Config(mantou_affection_daily_gain_limit=2).mantou_affection_daily_gain_limit == 2


def test_random_event_defaults() -> None:
    config = Config()
    assert config.mantou_affection_event_timeout == 20
    assert config.mantou_affection_event_timeout_penalty == 5


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


def test_registered_matchers_include_event_wheel_command() -> None:
    from nonebot_plugin_mantou_affection import matchers

    assert len(matchers) == 17
    (
        _profile_cmd,
        _interact_cmd,
        _ranking_cmd,
        _help_cmd,
        _adjust_cmd,
        _reset_cmd,
        event_wheel_cmd,
        wheel_cmd,
        pk_bank_cmd,
        batch_probability_cmd,
        event_limit_toggle_cmd,
        event_limit_count_cmd,
        admin_help_cmd,
        wallpaper_cmd,
        sticker_test_cmd,
        whoami_cmd,
        _image_cmd,
    ) = matchers
    expected_handlers = {
        event_wheel_cmd: "handle_event_wheel_chance",
        wheel_cmd: "handle_wheel_probability",
        pk_bank_cmd: "handle_pk_bank",
        batch_probability_cmd: "handle_probability_batch",
        event_limit_toggle_cmd: "handle_event_limit_toggle",
        event_limit_count_cmd: "handle_event_limit_count",
        admin_help_cmd: "handle_admin_help",
        wallpaper_cmd: "handle_wallpaper",
        sticker_test_cmd: "handle_sticker_test",
    }
    for matcher, handler_name in expected_handlers.items():
        assert matcher.priority == 10
        assert matcher.block is True
        assert [handler.call.__name__ for handler in matcher.handlers] == [handler_name]
        assert "Superuser" in repr(matcher.permission)

    assert whoami_cmd.priority == 10
    assert whoami_cmd.block is True
    assert [handler.call.__name__ for handler in whoami_cmd.handlers] == [
        "handle_whoami"
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


async def test_event_wheel_probability_command_query_and_set(
    tmp_path: Path, monkeypatch
) -> None:
    service = AffectionService(AffectionStore(tmp_path / "affection.json"), Config())
    command = register_commands(service, Config())[6]
    sent: list[str] = []

    async def fake_finish(message: Message | str = "", **kwargs) -> None:
        sent.append(str(message))

    monkeypatch.setattr(command, "finish", fake_finish)
    handler = command.handlers[0]

    await handler.call(Message(""))
    assert "当前事件转盘概率：1.5%" in sent[-1]
    assert "馒头小动作" in sent[-1]
    assert "我是谁" in sent[-1]

    await handler.call(Message("0.5%"))
    assert "事件转盘概率已调整为 0.5%" in sent[-1]
    assert await service.event_wheel_probability() == pytest.approx(0.005)

    await handler.call(Message(""))
    assert "运行时覆盖，配置默认 1.5%" in sent[-1]

    await handler.call(Message("not-a-probability"))
    assert "请输入 0~1" in sent[-1]
    assert await service.event_wheel_probability() == pytest.approx(0.005)


async def test_wheel_probability_command_query_and_set(tmp_path: Path, monkeypatch) -> None:
    service = AffectionService(AffectionStore(tmp_path / "affection.json"), Config())
    wheel_cmd = register_commands(service, Config())[7]
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


async def test_batch_probability_command_query_set_scale_and_reset(
    tmp_path: Path, monkeypatch
) -> None:
    service = AffectionService(AffectionStore(tmp_path / "affection.json"), Config())
    batch_cmd = register_commands(service, Config())[9]
    sent: list[str] = []

    async def fake_finish(message: Message | str = "", **kwargs) -> None:
        sent.append(str(message))

    monkeypatch.setattr(batch_cmd, "finish", fake_finish)
    handler = batch_cmd.handlers[0]

    await handler.call(Message(""))
    assert "共 2 项" in sent[-1]
    assert "事件转盘：1.5%（配置默认）" in sent[-1]
    assert "馒头转盘：0.1%（配置默认）" in sent[-1]
    assert "×2" in sent[-1]

    await handler.call(Message("0.01"))
    assert "统一设为 1%" in sent[-1]
    assert await service.event_wheel_probability() == pytest.approx(0.01)
    assert await service.wheel_probability() == pytest.approx(0.01)

    await handler.call(Message("×2"))
    assert "已按 ×2 缩放 2 项活动概率" in sent[-1]
    assert "事件转盘：1% → 2%" in sent[-1]
    assert await service.wheel_probability() == pytest.approx(0.02)

    await handler.call(Message(""))
    assert "运行时覆盖，配置默认 1.5%" in sent[-1]

    await handler.call(Message("重置"))
    assert "已清除 2 项运行时概率覆盖" in sent[-1]
    assert await service.event_wheel_probability() == pytest.approx(
        Config().mantou_affection_event_wheel_chance
    )

    await handler.call(Message("重置"))
    assert "当前没有运行时概率覆盖" in sent[-1]

    await handler.call(Message("abc"))
    assert "请输入 0~1" in sent[-1]

    await handler.call(Message("×-1"))
    assert "请输入 0~1" in sent[-1]


async def test_batch_probability_command_scales_percent_factor(
    tmp_path: Path, monkeypatch
) -> None:
    service = AffectionService(AffectionStore(tmp_path / "affection.json"), Config())
    batch_cmd = register_commands(service, Config())[9]
    sent: list[str] = []

    async def fake_finish(message: Message | str = "", **kwargs) -> None:
        sent.append(str(message))

    monkeypatch.setattr(batch_cmd, "finish", fake_finish)

    await batch_cmd.handlers[0].call(Message("×50%"))
    assert "已按 ×0.5 缩放 2 项活动概率" in sent[-1]
    assert await service.event_wheel_probability() == pytest.approx(0.0075)


async def test_help_lists_every_command(tmp_path: Path, monkeypatch) -> None:
    service = AffectionService(AffectionStore(tmp_path / "affection.json"), Config())
    help_cmd = register_commands(service, Config())[3]
    sent: list[str] = []

    async def fake_finish(message: Message | str = "", **kwargs) -> None:
        sent.append(str(message))

    monkeypatch.setattr(help_cmd, "finish", fake_finish)
    await help_cmd.handlers[0].call()

    text = sent[-1]
    assert "全部命令" in text
    listed = {
        line.split(" ", 1)[0]
        for line in text.splitlines()
        if line.startswith("/")
    }
    assert listed == {
        "/馒头互动",
        "/馒头好感",
        "/馒头好感榜",
        "/馒头帮助",
        "/馒头好感调整",
        "/馒头好感重置",
        "/馒头事件转盘概率",
        "/馒头转盘概率",
        "/馒头问答题库",
        "/馒头概率总调",
        "/馒头壁纸",
        "/馒头表情包",
        "/馒头限制开关",
        "/馒头限制次数",
        "/馒头管理帮助",
        "/馒头强制事件",
        "/馒头图像处理",
        "/翻转",
        "/馒头再来一次",
        "/馒头我是谁",
    }


@pytest.mark.parametrize("argument", ["0.01", "×2", "重置"])
async def test_batch_probability_finish_is_not_caught(
    tmp_path: Path, monkeypatch, argument: str
) -> None:
    service = AffectionService(AffectionStore(tmp_path / "affection.json"), Config())
    batch_cmd = register_commands(service, Config())[9]
    sent: list[str] = []

    async def fake_finish(message: Message | str = "", **kwargs) -> None:
        sent.append(str(message))
        raise FinishedException

    monkeypatch.setattr(batch_cmd, "finish", fake_finish)

    with pytest.raises(FinishedException):
        await batch_cmd.handlers[0].call(Message(argument))

    assert len(sent) == 1
    assert "保存失败" not in sent[0]
