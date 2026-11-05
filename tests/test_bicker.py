from __future__ import annotations

import asyncio
import random
from pathlib import Path

from nonebot.adapters.onebot.v11 import Message

from nonebot_plugin_mantou_affection.bicker import (
    BICKER_OPTIONS,
    BickerCoordinator,
    GroupMemoryUnavailable,
)
from nonebot_plugin_mantou_affection.config import Config
from nonebot_plugin_mantou_affection.service import AffectionService
from nonebot_plugin_mantou_affection.storage import AffectionStore

GROUP_ID = "93001"
TRIGGER_ID = "93002"
OTHER_ID = "93003"


class FakeMemory:
    def __init__(self, responses, recent=None, available=True):
        self.responses = list(responses)
        self.recent = (
            recent
            if recent is not None
            else [("甲", "我昨天十点就睡了"), ("乙", "早睡都是假的")]
        )
        self.available_flag = available
        self.calls: list[str] = []

    def available(self) -> bool:
        return self.available_flag

    async def recent_messages(self, group_id, limit=30):
        return list(self.recent)

    async def complete(self, group_id, text, *, timeout=20.0):
        self.calls.append(text)
        if not self.responses:
            raise GroupMemoryUnavailable("模型没有返回")
        return self.responses.pop(0)


def _coordinator(tmp_path: Path, memory, *, config=None, rng=None, events=None):
    config = config or Config(mantou_affection_newbie_protection_below=0)
    service = AffectionService(
        AffectionStore(tmp_path / "affection.json"),
        config,
        rng=rng or random.Random(0),
    )
    coordinator = BickerCoordinator(
        service, config, memory, events=events, rng=rng or random.Random(0)
    )
    coordinator.bet_window = 0.2
    coordinator.round_timeout = 30.0
    return coordinator, service


async def give(service: AffectionService, user_id: str, score: int) -> None:
    def bump(profile) -> None:
        profile.affection = score

    await service.store.update_profile(GROUP_ID, user_id, "测试员", bump)


async def _noop_send(message) -> None:
    return None


async def _start(coordinator, *, user_id=TRIGGER_ID, nickname="触发者"):
    sent = []

    async def send(message) -> None:
        sent.append(Message(message) if isinstance(message, str) else message)

    assert await coordinator.start(
        group_id=GROUP_ID, user_id=user_id, nickname=nickname, send=send
    )
    return coordinator.pending[GROUP_ID], sent


def test_bicker_options_are_six_numbered_stakes() -> None:
    assert BICKER_OPTIONS == {
        "1": (True, 1),
        "2": (True, 3),
        "3": (True, 5),
        "4": (False, 1),
        "5": (False, 3),
        "6": (False, 5),
    }


async def test_start_aborts_when_memory_unavailable(tmp_path: Path) -> None:
    coordinator, _ = _coordinator(tmp_path, FakeMemory([], available=False))
    assert await coordinator.start(
        group_id=GROUP_ID, user_id=TRIGGER_ID, nickname="触发者", send=_noop_send
    ) is False
    assert coordinator.pending == {}


async def test_start_aborts_when_recent_messages_empty(tmp_path: Path) -> None:
    coordinator, _ = _coordinator(tmp_path, FakeMemory(["题目"], recent=[]))
    assert await coordinator.start(
        group_id=GROUP_ID, user_id=TRIGGER_ID, nickname="触发者", send=_noop_send
    ) is False
    assert coordinator.pending == {}


async def test_start_posts_topic_and_six_options(tmp_path: Path) -> None:
    coordinator, _ = _coordinator(tmp_path, FakeMemory(["凌晨三点睡到底健不健康"]))
    pending, sent = await _start(coordinator)
    body = sent[0].extract_plain_text()
    assert "馒头斗嘴" in body
    assert "凌晨三点睡到底健不健康" in body
    assert "押群友赢 1 点" in body
    assert "押群友输 5 点" in body
    for index in range(1, 7):
        assert f"{index}." in body
    assert pending.phase == "betting"
    pending.bet_task.cancel()


async def test_bet_deducts_and_records(tmp_path: Path) -> None:
    coordinator, service = _coordinator(tmp_path, FakeMemory(["题目"]))
    pending, _sent = await _start(coordinator)
    await give(service, OTHER_ID, 10)
    assert await coordinator.answer(GROUP_ID, OTHER_ID, "甲", "2") is True
    assert pending.bets[OTHER_ID].stake == 3
    assert pending.bets[OTHER_ID].side is True
    assert (await service.profile(GROUP_ID, OTHER_ID)).affection == 7
    pending.bet_task.cancel()


async def test_trigger_cannot_bet(tmp_path: Path) -> None:
    coordinator, service = _coordinator(tmp_path, FakeMemory(["题目"]))
    pending, sent = await _start(coordinator)
    await give(service, TRIGGER_ID, 10)
    assert await coordinator.answer(GROUP_ID, TRIGGER_ID, "触发者", "1") is True
    assert TRIGGER_ID not in pending.bets
    assert "当事人" in sent[-1].extract_plain_text()
    pending.bet_task.cancel()


async def test_duplicate_bet_rejected(tmp_path: Path) -> None:
    coordinator, service = _coordinator(tmp_path, FakeMemory(["题目"]))
    pending, sent = await _start(coordinator)
    await give(service, OTHER_ID, 10)
    assert await coordinator.answer(GROUP_ID, OTHER_ID, "甲", "1") is True
    assert await coordinator.answer(GROUP_ID, OTHER_ID, "甲", "3") is True
    assert pending.bets[OTHER_ID].stake == 1
    assert (await service.profile(GROUP_ID, OTHER_ID)).affection == 9
    assert "已经押过" in sent[-1].extract_plain_text()
    pending.bet_task.cancel()


async def test_insufficient_affection_then_lower_option_allowed(tmp_path: Path) -> None:
    coordinator, service = _coordinator(tmp_path, FakeMemory(["题目"]))
    pending, sent = await _start(coordinator)
    await give(service, OTHER_ID, 2)
    assert await coordinator.answer(GROUP_ID, OTHER_ID, "甲", "3") is True
    assert OTHER_ID not in pending.bets
    assert "好感不足 5 点" in sent[-1].extract_plain_text()
    assert await coordinator.answer(GROUP_ID, OTHER_ID, "甲", "1") is True
    assert pending.bets[OTHER_ID].stake == 1
    pending.bet_task.cancel()


async def test_bet_window_close_posts_summary(tmp_path: Path) -> None:
    coordinator, service = _coordinator(tmp_path, FakeMemory(["题目"]))
    pending, sent = await _start(coordinator)
    await give(service, OTHER_ID, 10)
    await coordinator.answer(GROUP_ID, OTHER_ID, "甲", "2")
    await asyncio.wait_for(pending.bet_task, 2)
    bodies = [message.extract_plain_text() for message in sent]
    assert any("押注截止" in body for body in bodies)
    assert any("押注总额 3 点" in body for body in bodies)
    assert pending.phase == "debating"


async def test_no_bets_close_posts_no_bets_message(tmp_path: Path) -> None:
    coordinator, _ = _coordinator(tmp_path, FakeMemory(["题目"]))
    pending, sent = await _start(coordinator)
    await asyncio.wait_for(pending.bet_task, 2)
    assert any("没人押注" in message.extract_plain_text() for message in sent)
    assert pending.phase == "debating"


async def test_five_rounds_then_judge_and_trigger_wins(tmp_path: Path) -> None:
    memory = FakeMemory(["题目", "回1", "回2", "回3", "回4", "回5", "群友赢"])
    coordinator, service = _coordinator(tmp_path, memory)
    await give(service, TRIGGER_ID, 50)
    pending, _sent = await _start(coordinator)
    await asyncio.wait_for(pending.bet_task, 2)
    for index in range(1, 6):
        assert await coordinator.handle_debate(GROUP_ID, TRIGGER_ID, f"我说{index}") is True
    assert len(memory.calls) == 7
    assert (await service.profile(GROUP_ID, TRIGGER_ID)).affection == 53
    assert GROUP_ID not in coordinator.pending


async def test_trigger_loses_and_bets_settle(tmp_path: Path) -> None:
    memory = FakeMemory(["题目", "回1", "回2", "回3", "回4", "回5", "馒头赢"])
    coordinator, service = _coordinator(tmp_path, memory)
    await give(service, TRIGGER_ID, 50)
    await give(service, OTHER_ID, 50)
    await give(service, "93004", 50)
    pending, _sent = await _start(coordinator)
    await coordinator.answer(GROUP_ID, OTHER_ID, "甲", "1")  # 押群友赢 1
    await coordinator.answer(GROUP_ID, "93004", "乙", "5")  # 押群友输 3
    await asyncio.wait_for(pending.bet_task, 2)
    for index in range(1, 6):
        await coordinator.handle_debate(GROUP_ID, TRIGGER_ID, f"我说{index}")
    assert (await service.profile(GROUP_ID, TRIGGER_ID)).affection == 45
    assert (await service.profile(GROUP_ID, OTHER_ID)).affection == 49
    assert (await service.profile(GROUP_ID, "93004")).affection == 51


async def test_round_timeout_loses_and_settles(tmp_path: Path) -> None:
    memory = FakeMemory(["题目"])
    coordinator, service = _coordinator(tmp_path, memory)
    coordinator.round_timeout = 0.01
    await give(service, TRIGGER_ID, 50)
    pending, _sent = await _start(coordinator)
    await asyncio.wait_for(pending.bet_task, 2)
    await asyncio.wait_for(pending.round_task, 2)
    assert (await service.profile(GROUP_ID, TRIGGER_ID)).affection == 45
    assert GROUP_ID not in coordinator.pending


async def test_no_winners_refunds_all(tmp_path: Path) -> None:
    memory = FakeMemory(["题目", "回1", "回2", "回3", "回4", "回5", "馒头赢"])
    coordinator, service = _coordinator(tmp_path, memory)
    await give(service, TRIGGER_ID, 50)
    await give(service, OTHER_ID, 50)
    await give(service, "93004", 50)
    pending, _sent = await _start(coordinator)
    await coordinator.answer(GROUP_ID, OTHER_ID, "甲", "1")
    await coordinator.answer(GROUP_ID, "93004", "乙", "2")
    await asyncio.wait_for(pending.bet_task, 2)
    for index in range(1, 6):
        await coordinator.handle_debate(GROUP_ID, TRIGGER_ID, f"我说{index}")
    assert (await service.profile(GROUP_ID, OTHER_ID)).affection == 50
    assert (await service.profile(GROUP_ID, "93004")).affection == 50
    assert (await service.profile(GROUP_ID, TRIGGER_ID)).affection == 45


async def test_remainder_goes_to_earliest_winner(tmp_path: Path) -> None:
    memory = FakeMemory(["题目", "回1", "回2", "回3", "回4", "回5", "群友赢"])
    coordinator, service = _coordinator(tmp_path, memory)
    await give(service, TRIGGER_ID, 50)
    await give(service, OTHER_ID, 50)
    await give(service, "93004", 50)
    await give(service, "93005", 50)
    pending, _sent = await _start(coordinator)
    await coordinator.answer(GROUP_ID, OTHER_ID, "甲", "1")  # 最早赢家
    await coordinator.answer(GROUP_ID, "93004", "乙", "1")  # 第二赢家
    await coordinator.answer(GROUP_ID, "93005", "丙", "6")  # 输家押 5
    await asyncio.wait_for(pending.bet_task, 2)
    for index in range(1, 6):
        await coordinator.handle_debate(GROUP_ID, TRIGGER_ID, f"我说{index}")
    assert (await service.profile(GROUP_ID, OTHER_ID)).affection == 53
    assert (await service.profile(GROUP_ID, "93004")).affection == 52
    assert (await service.profile(GROUP_ID, "93005")).affection == 45


async def test_no_bets_only_trigger_settles(tmp_path: Path) -> None:
    memory = FakeMemory(["题目", "回1", "回2", "回3", "回4", "回5", "群友赢"])
    coordinator, service = _coordinator(tmp_path, memory)
    await give(service, TRIGGER_ID, 50)
    pending, _sent = await _start(coordinator)
    await asyncio.wait_for(pending.bet_task, 2)
    for index in range(1, 6):
        await coordinator.handle_debate(GROUP_ID, TRIGGER_ID, f"我说{index}")
    assert (await service.profile(GROUP_ID, TRIGGER_ID)).affection == 53
    assert GROUP_ID not in coordinator.pending


async def test_model_failure_cancels_and_refunds(tmp_path: Path) -> None:
    memory = FakeMemory(["题目"])
    coordinator, service = _coordinator(tmp_path, memory)
    await give(service, TRIGGER_ID, 50)
    await give(service, OTHER_ID, 50)
    pending, _sent = await _start(coordinator)
    await coordinator.answer(GROUP_ID, OTHER_ID, "甲", "2")
    await asyncio.wait_for(pending.bet_task, 2)
    assert await coordinator.handle_debate(GROUP_ID, TRIGGER_ID, "我说一句") is True
    assert (await service.profile(GROUP_ID, OTHER_ID)).affection == 50
    assert (await service.profile(GROUP_ID, TRIGGER_ID)).affection == 50
    assert GROUP_ID not in coordinator.pending


async def test_judge_failure_cancels_and_refunds(tmp_path: Path) -> None:
    memory = FakeMemory(["题目", "回1", "回2", "回3", "回4", "回5", "不知道"])
    coordinator, service = _coordinator(tmp_path, memory)
    await give(service, TRIGGER_ID, 50)
    await give(service, OTHER_ID, 50)
    pending, _sent = await _start(coordinator)
    await coordinator.answer(GROUP_ID, OTHER_ID, "甲", "1")
    await asyncio.wait_for(pending.bet_task, 2)
    for index in range(1, 6):
        await coordinator.handle_debate(GROUP_ID, TRIGGER_ID, f"我说{index}")
    assert (await service.profile(GROUP_ID, OTHER_ID)).affection == 50
    assert (await service.profile(GROUP_ID, TRIGGER_ID)).affection == 50
    assert GROUP_ID not in coordinator.pending


async def test_newbie_protection_on_trigger_loss(tmp_path: Path) -> None:
    memory = FakeMemory(["题目", "回1", "回2", "回3", "回4", "回5", "馒头赢"])
    coordinator, service = _coordinator(
        tmp_path,
        memory,
        config=Config(mantou_affection_newbie_protection_below=50),
    )
    await give(service, TRIGGER_ID, 40)
    pending, sent = await _start(coordinator)
    await asyncio.wait_for(pending.bet_task, 2)
    for index in range(1, 6):
        await coordinator.handle_debate(GROUP_ID, TRIGGER_ID, f"我说{index}")
    assert (await service.profile(GROUP_ID, TRIGGER_ID)).affection == 39
    assert "新手保护" in sent[-1].extract_plain_text()
