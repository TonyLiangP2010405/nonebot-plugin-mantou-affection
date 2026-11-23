from __future__ import annotations

import asyncio
import random
from contextlib import suppress
from pathlib import Path
from time import time

from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message
from nonebot.adapters.onebot.v11.event import Sender

from nonebot_plugin_mantou_affection.config import Config
from nonebot_plugin_mantou_affection.models import Profile
from nonebot_plugin_mantou_affection.service import AffectionService
from nonebot_plugin_mantou_affection.storage import AffectionStore
from nonebot_plugin_mantou_affection.wheel import PendingWheel, WheelCoordinator
from nonebot_plugin_mantou_affection.wheel_spins import (
    REROLL_BUSY_REPLY,
    REROLL_NONE_REPLY,
    SOLO_JOIN_REPLY,
    WheelSpinQuotaStore,
    register_spin_commands,
)

GROUP = "94101"
USER = "94102"
OTHER = "94103"


class SequenceRng(random.Random):
    def __init__(self, rolls: list[int]):
        super().__init__(0)
        self.rolls = iter(rolls)

    def randrange(self, stop: int) -> int:
        assert stop == 100
        return next(self.rolls)


class FakeBot:
    self_id = 999

    async def call_api(self, name: str, **kwargs) -> None:
        raise AssertionError("个人转盘不应该调用 poke")


def _coordinator(tmp_path: Path, rng: list[int] | None = None) -> WheelCoordinator:
    config = Config(
        mantou_affection_newbie_protection_below=0,
        mantou_affection_multiplier_chance=0.0,
    )
    service = AffectionService(AffectionStore(tmp_path / "affection.json"), config)
    coordinator = WheelCoordinator(
        service,
        config,
        rng=SequenceRng(rng or [50]),
        spin_quota=WheelSpinQuotaStore(tmp_path / "wheel_spins.json"),
        poke_delay=0,
    )
    coordinator.choice_timeout = 0
    return coordinator


def _collector():
    sent: list[Message] = []

    async def send(message) -> None:
        sent.append(Message(message) if isinstance(message, str) else message)

    return sent, send


async def _give(service: AffectionService, user_id: str, score: int) -> None:
    def bump(profile: Profile) -> None:
        profile.affection = score

    await service.store.update_profile(GROUP, user_id, "测试员", bump)


async def _run_group_wheel(coordinator: WheelCoordinator):
    sent, send = _collector()
    assert await coordinator.start(
        group_id=GROUP, user_id=USER, nickname="测试员", bot=FakeBot(), send=send
    )
    pending = coordinator.pending[GROUP]
    if pending.task is not None and not pending.task.done():
        pending.task.cancel()
        with suppress(asyncio.CancelledError):
            await pending.task
    await coordinator._auto_start(pending)
    return sent


def _text(sent: list[Message]) -> str:
    return "\n".join(message.extract_plain_text() for message in sent)


async def test_spin_outcome_grants_credit_and_hints_command(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path, [95])
    await _give(coordinator.service, USER, 10)

    sent = await _run_group_wheel(coordinator)

    body = _text(sent)
    assert "抽到「再来一次」" in body
    assert "转盘次数 +1，当前共 1 次" in body
    assert "/馒头再来一次" in body
    assert await coordinator.spin_quota.get(GROUP, USER) == 1


async def test_spin_outcome_can_grant_three_credits(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path, [99])
    await _give(coordinator.service, USER, 10)

    sent = await _run_group_wheel(coordinator)

    assert "抽到「再来三次」" in _text(sent)
    assert await coordinator.spin_quota.get(GROUP, USER) == 3


async def test_spin_outcome_stacks_with_existing_credits(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path, [98])
    await _give(coordinator.service, USER, 10)
    await coordinator.spin_quota.add(GROUP, USER, 2)

    sent = await _run_group_wheel(coordinator)

    assert "转盘次数 +2，当前共 4 次" in _text(sent)
    assert await coordinator.spin_quota.get(GROUP, USER) == 4


async def test_personal_spin_consumes_credit_and_draws(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path, [50])
    await _give(coordinator.service, USER, 10)
    await coordinator.spin_quota.add(GROUP, USER, 2)
    sent, send = _collector()

    status = await coordinator.spin_personal(
        group_id=GROUP,
        user_id=USER,
        nickname="测试员",
        bot=FakeBot(),
        send=send,
    )

    body = _text(sent)
    assert status == "ok"
    assert "本轮只有你能转" in body
    assert "剩余 1 次" in body
    assert "转盘好感 +1" in body
    assert (await coordinator.service.profile(GROUP, USER)).affection == 11
    assert await coordinator.spin_quota.get(GROUP, USER) == 1
    assert GROUP not in coordinator.pending


async def test_personal_spin_without_credit_reports_empty(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path, [50])
    await _give(coordinator.service, USER, 10)
    sent, send = _collector()

    status = await coordinator.spin_personal(
        group_id=GROUP,
        user_id=USER,
        nickname="测试员",
        bot=FakeBot(),
        send=send,
    )

    assert status == "empty"
    assert sent == []
    assert (await coordinator.service.profile(GROUP, USER)).affection == 10


async def test_personal_spin_refused_while_group_is_busy(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path, [50])
    await _give(coordinator.service, USER, 10)
    await coordinator.spin_quota.add(GROUP, USER, 1)
    _, send = _collector()
    assert await coordinator.start(
        group_id=GROUP, user_id=USER, nickname="测试员", bot=FakeBot(), send=send
    )

    status = await coordinator.spin_personal(
        group_id=GROUP,
        user_id=USER,
        nickname="测试员",
        bot=FakeBot(),
        send=send,
    )

    assert status == "busy"
    assert await coordinator.spin_quota.get(GROUP, USER) == 1
    pending = coordinator.pending[GROUP]
    if pending.task is not None:
        pending.task.cancel()
        with suppress(asyncio.CancelledError):
            await pending.task


async def test_other_members_cannot_join_personal_spin(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path, [50])
    sent, send = _collector()
    pending = PendingWheel(GROUP, USER, "测试员", bot=FakeBot(), send=send)
    pending.solo = True
    pending.accepting = False
    coordinator.pending[GROUP] = pending

    accepted = await coordinator.answer(GROUP, OTHER, "路人", "参与")

    assert accepted is True
    assert pending.participants == []
    assert SOLO_JOIN_REPLY in _text(sent)


async def test_spin_quota_store_is_per_group_and_never_negative(tmp_path: Path) -> None:
    store = WheelSpinQuotaStore(tmp_path / "wheel_spins.json")
    assert await store.add(GROUP, USER, 2) == 2
    assert await store.add("other-group", USER, 1) == 1
    assert await store.get(GROUP, USER) == 2
    assert await store.get("other-group", USER) == 1
    assert await store.consume(GROUP, USER, 3) is None
    assert await store.get(GROUP, USER) == 2
    assert await store.consume(GROUP, USER, 2) == 0
    assert await store.get(GROUP, USER) == 0


def _event(user_id: str = USER) -> GroupMessageEvent:
    message = Message("馒头再来一次")
    return GroupMessageEvent(
        time=int(time()),
        self_id=10000,
        post_type="message",
        sub_type="normal",
        user_id=int(user_id),
        message_type="group",
        message_id=1,
        message=message,
        original_message=message,
        raw_message="馒头再来一次",
        font=0,
        sender=Sender(user_id=int(user_id), nickname="测试员", role="member"),
        to_me=True,
        group_id=int(GROUP),
    )


def _command(coordinator: WheelCoordinator):
    return register_spin_commands(coordinator)[0]


def _capture(matcher, monkeypatch) -> tuple[list[Message], list[str]]:
    sent: list[Message] = []
    finished: list[str] = []

    async def fake_send(message) -> None:
        sent.append(Message(message) if isinstance(message, str) else message)

    async def fake_finish(message="", **kwargs) -> None:
        finished.append(str(message))

    monkeypatch.setattr(matcher, "send", fake_send)
    monkeypatch.setattr(matcher, "finish", fake_finish)
    return sent, finished


async def test_reroll_command_reports_missing_credits(tmp_path: Path, monkeypatch) -> None:
    coordinator = _coordinator(tmp_path, [50])
    command = _command(coordinator)
    _, finished = _capture(command, monkeypatch)

    await command.handlers[0].call(FakeBot(), _event())

    assert finished == [REROLL_NONE_REPLY]


async def test_reroll_command_uses_credit_and_reports_busy(
    tmp_path: Path, monkeypatch
) -> None:
    coordinator = _coordinator(tmp_path, [50])
    await coordinator.spin_quota.add(GROUP, USER, 1)
    _, send = _collector()
    assert await coordinator.start(
        group_id=GROUP, user_id=USER, nickname="测试员", bot=FakeBot(), send=send
    )
    command = _command(coordinator)
    _, finished = _capture(command, monkeypatch)

    await command.handlers[0].call(FakeBot(), _event())

    assert finished == [REROLL_BUSY_REPLY]
    assert await coordinator.spin_quota.get(GROUP, USER) == 1
    pending = coordinator.pending[GROUP]
    if pending.task is not None:
        pending.task.cancel()
        with suppress(asyncio.CancelledError):
            await pending.task


async def test_reroll_command_spins_solo(tmp_path: Path, monkeypatch) -> None:
    coordinator = _coordinator(tmp_path, [50])
    await _give(coordinator.service, USER, 10)
    await coordinator.spin_quota.add(GROUP, USER, 1)
    command = _command(coordinator)
    sent, finished = _capture(command, monkeypatch)

    await command.handlers[0].call(FakeBot(), _event())

    body = _text(sent)
    assert finished == []
    assert "本轮只有你能转" in body
    assert "转盘好感 +1" in body
    assert await coordinator.spin_quota.get(GROUP, USER) == 0
