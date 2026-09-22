import asyncio
import json
import random
from contextlib import suppress
from pathlib import Path
from time import time

import pytest
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message
from nonebot.adapters.onebot.v11.event import Sender

from nonebot_plugin_mantou_affection.ambient import (
    EventCoordinator,
    PendingEvent,
    register_ambient,
)
from nonebot_plugin_mantou_affection.config import Config
from nonebot_plugin_mantou_affection.copywriting import AffectionTextLibrary
from nonebot_plugin_mantou_affection.events import EventLibrary
from nonebot_plugin_mantou_affection.models import Profile
from nonebot_plugin_mantou_affection.service import AffectionService
from nonebot_plugin_mantou_affection.storage import AffectionStore

GROUP_ID = "90001"
TRIGGER_ID = "90002"
OTHER_ID = "90003"


def _event(
    text: str = "晚上好",
    user_id: int = 90002,
    group_id: int = 90001,
    nickname: str = "桃友",
) -> GroupMessageEvent:
    message = Message(text)
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
        raw_message=text,
        font=0,
        sender=Sender(user_id=user_id, nickname=nickname, role="member"),
        to_me=False,
        group_id=group_id,
    )


def _service(
    tmp_path: Path,
    text_library: AffectionTextLibrary | None = None,
    config: Config | None = None,
) -> AffectionService:
    return AffectionService(
        AffectionStore(tmp_path / "affection.json"),
        config or Config(),
        text_library=text_library,
        rng=random.Random(0),
    )


def _events_path(bundled_texts_path: Path) -> Path:
    return bundled_texts_path.with_name("affection_events.json")


async def _give_affection(service: AffectionService, score: int, user_id: str = TRIGGER_ID) -> None:
    def bump(profile: Profile) -> None:
        profile.affection = score

    await service.store.update_profile(GROUP_ID, user_id, "桃友", bump)


def _capture_send(matcher, monkeypatch) -> list[Message]:
    sent: list[Message] = []

    async def fake_send(message: Message) -> None:
        sent.append(message)

    monkeypatch.setattr(matcher, "send", fake_send)
    return sent


def _setup(
    bundled_texts_path: Path,
    tmp_path: Path,
    monkeypatch,
    **config_kwargs,
) -> tuple:
    config = Config(mantou_affection_ambient_probability=1.0, **config_kwargs)
    service = _service(tmp_path, AffectionTextLibrary(bundled_texts_path), config)
    coordinator = EventCoordinator(
        service, config, EventLibrary(_events_path(bundled_texts_path)), rng=random.Random(0)
    )
    ambient, answer = register_ambient(service, config, coordinator)
    sent = _capture_send(ambient, monkeypatch)
    return coordinator, sent, ambient, answer


async def _start_event(coordinator: EventCoordinator) -> PendingEvent:
    pending = coordinator.start(GROUP_ID, TRIGGER_ID, "桃友")
    assert pending is not None
    return pending


async def _settle(coordinator: EventCoordinator, timeout: float = 0.01) -> list[Message]:
    pending = coordinator.pending[GROUP_ID]
    sent: list[Message] = []

    async def fake_send(message: Message) -> None:
        sent.append(message)

    task = coordinator.schedule(pending, group_id=GROUP_ID, send=fake_send, timeout=timeout)
    await asyncio.wait_for(task, 2)
    return sent


async def _cancel_task(coordinator: EventCoordinator) -> None:
    pending = coordinator.pending.get(GROUP_ID)
    if pending is None or pending.task is None:
        return
    pending.task.cancel()
    with suppress(asyncio.CancelledError):
        await pending.task


def _at_ids(message: Message) -> list[str]:
    return [str(segment.data["qq"]) for segment in message if segment.type == "at"]


def _body(message: Message) -> str:
    return "".join(segment.data["text"] for segment in message if segment.type == "text")


def _lines(message: Message) -> list[str]:
    return [line for line in _body(message).split("\n") if line.strip()]


async def test_ambient_matchers_are_registered(tmp_path: Path) -> None:
    ambient, answer = register_ambient(_service(tmp_path), Config())
    assert ambient.priority == 90
    assert ambient.block is False
    assert [handler.call.__name__ for handler in ambient.handlers] == ["handle_ambient"]
    assert answer.priority == 5
    assert answer.block is False
    assert [handler.call.__name__ for handler in answer.handlers] == ["handle_answer"]


async def test_ambient_handler_mentions_sender_with_band_copy(
    bundled_texts_path: Path, tmp_path: Path, monkeypatch
) -> None:
    ambient_copy = json.loads(bundled_texts_path.read_text(encoding="utf-8"))[
        "mantou.ambient"
    ]["neutral"]
    coordinator, sent, ambient, _answer = _setup(
        bundled_texts_path, tmp_path, monkeypatch, mantou_affection_ambient_event_ratio=0.0
    )
    await _give_affection(coordinator.service, 10)

    await ambient.handlers[0].call(_event())

    assert len(sent) == 1
    message = sent[0]
    assert [segment.type for segment in message] == ["at", "text"]
    assert message[0].data["qq"] == TRIGGER_ID
    assert message.extract_plain_text().strip() in ambient_copy
    assert coordinator.pending == {}


async def test_ambient_handler_stays_silent_when_disabled(
    bundled_texts_path: Path, tmp_path: Path, monkeypatch
) -> None:
    coordinator, sent, ambient, _answer = _setup(
        bundled_texts_path,
        tmp_path,
        monkeypatch,
        mantou_affection_ambient_enabled=False,
    )
    await _give_affection(coordinator.service, 10)

    await ambient.handlers[0].call(_event())

    assert sent == []
    assert coordinator.pending == {}


async def test_ambient_handler_swallows_service_errors(
    bundled_texts_path: Path, tmp_path: Path, monkeypatch
) -> None:
    coordinator, sent, ambient, _answer = _setup(bundled_texts_path, tmp_path, monkeypatch)

    async def broken(group_id: str, user_id: str) -> str | None:
        raise RuntimeError("store is down")

    monkeypatch.setattr(coordinator.service, "ambient_reaction", broken)

    await ambient.handlers[0].call(_event())

    assert sent == []


async def test_zero_event_ratio_keeps_plain_ambient_copy(
    bundled_texts_path: Path, tmp_path: Path, monkeypatch
) -> None:
    coordinator, sent, ambient, _answer = _setup(
        bundled_texts_path, tmp_path, monkeypatch, mantou_affection_ambient_event_ratio=0.0
    )
    await _give_affection(coordinator.service, 10)

    await ambient.handlers[0].call(_event())

    assert len(sent) == 1
    assert [segment.type for segment in sent[0]] == ["at", "text"]
    assert coordinator.pending == {}


async def test_full_event_ratio_sends_event_message(
    bundled_texts_path: Path, tmp_path: Path, monkeypatch
) -> None:
    coordinator, sent, ambient, _answer = _setup(
        bundled_texts_path,
        tmp_path,
        monkeypatch,
        mantou_affection_ambient_event_ratio=1.0,
        mantou_affection_event_timeout=3,
    )
    await _give_affection(coordinator.service, 10)

    await ambient.handlers[0].call(_event())

    assert len(sent) == 1
    text = _body(sent[0]).strip()
    assert text.startswith("⚡ 触发随机事件！")
    assert "请在 3 秒内作答，直接发送 1、2、3 即可（答题不用@），超时好感度 -5！" in text
    pending = coordinator.pending[GROUP_ID]
    assert pending.trigger_id == TRIGGER_ID
    library_options = {
        frozenset(option.text for option in event.options)
        for event in EventLibrary(_events_path(bundled_texts_path)).events
    }
    assert frozenset(option.text for option in pending.options) in library_options
    await _cancel_task(coordinator)


async def test_running_event_falls_back_to_ambient_copy(
    bundled_texts_path: Path, tmp_path: Path, monkeypatch
) -> None:
    coordinator, sent, ambient, _answer = _setup(
        bundled_texts_path,
        tmp_path,
        monkeypatch,
        mantou_affection_ambient_event_ratio=1.0,
        mantou_affection_event_timeout=3,
    )
    await _give_affection(coordinator.service, 10)
    await _give_affection(coordinator.service, 10, user_id=OTHER_ID)
    await ambient.handlers[0].call(_event())
    assert GROUP_ID in coordinator.pending

    sent.clear()
    await ambient.handlers[0].call(_event(user_id=90003))

    assert len(sent) == 1
    assert [segment.type for segment in sent[0]] == ["at", "text"]
    assert GROUP_ID in coordinator.pending
    await _cancel_task(coordinator)


async def test_answer_matcher_records_first_answer_only(
    bundled_texts_path: Path, tmp_path: Path, monkeypatch
) -> None:
    coordinator, _sent, _ambient, answer = _setup(
        bundled_texts_path,
        tmp_path,
        monkeypatch,
        mantou_affection_ambient_event_ratio=1.0,
        mantou_affection_event_timeout=3,
    )
    await _give_affection(coordinator.service, 10)
    await _start_event(coordinator)

    for text in ("", "4", "一", "1 2", "晚上好"):
        await answer.handlers[0].call(_event(text))
        assert coordinator.pending[GROUP_ID].answers == {}

    await answer.handlers[0].call(_event("2", user_id=90003, nickname="路人"))
    await answer.handlers[0].call(_event("1", user_id=90003, nickname="路人"))
    await answer.handlers[0].call(_event("3"))

    answers = coordinator.pending[GROUP_ID].answers
    assert list(answers) == [OTHER_ID, TRIGGER_ID]
    assert answers[OTHER_ID].nickname == "路人"
    assert answers[OTHER_ID].index == 1
    assert answers[TRIGGER_ID].index == 2


async def test_merged_message_lists_every_answerer(
    bundled_texts_path: Path, tmp_path: Path, monkeypatch
) -> None:
    coordinator, _sent, _ambient, answer = _setup(bundled_texts_path, tmp_path, monkeypatch)
    await _give_affection(coordinator.service, 10)
    await _give_affection(coordinator.service, 5, user_id=OTHER_ID)
    pending = await _start_event(coordinator)
    best = next(index for index, option in enumerate(pending.options) if option.delta == 2)
    worst = next(index for index, option in enumerate(pending.options) if option.delta == -2)

    await answer.handlers[0].call(_event(str(best + 1)))
    await answer.handlers[0].call(_event(str(worst + 1), user_id=90003, nickname="路人"))
    sent = await _settle(coordinator)

    assert len(sent) == 1
    message = sent[0]
    assert _at_ids(message) == [TRIGGER_ID, OTHER_ID]
    lines = _lines(message)
    assert len(lines) == 2
    assert f"馒头开心地收下了「{pending.options[best].text}」，好感度 +2，当前 12" in lines[0]
    assert f"馒头嫌弃地躲开了「{pending.options[worst].text}」，好感度 -2，当前 3" in lines[1]

    assert (await coordinator.service.profile(GROUP_ID, TRIGGER_ID)).affection == 12
    assert (await coordinator.service.profile(GROUP_ID, OTHER_ID)).affection == 3
    assert coordinator.pending == {}


async def test_triggerer_answer_avoids_timeout_penalty(
    bundled_texts_path: Path, tmp_path: Path, monkeypatch
) -> None:
    coordinator, _sent, _ambient, _answer = _setup(bundled_texts_path, tmp_path, monkeypatch)
    await _give_affection(coordinator.service, 10)
    pending = await _start_event(coordinator)
    plain = next(index for index, option in enumerate(pending.options) if option.delta == 1)
    assert coordinator.answer(GROUP_ID, TRIGGER_ID, "桃友", str(plain + 1)) is True

    sent = await _settle(coordinator)

    message = sent[0]
    assert _at_ids(message) == [TRIGGER_ID]
    assert "馒头等不到你的回答" not in str(message)
    assert (await coordinator.service.profile(GROUP_ID, TRIGGER_ID)).affection == 11


async def test_triggerer_timeout_adds_penalty_line(
    bundled_texts_path: Path, tmp_path: Path, monkeypatch
) -> None:
    coordinator, _sent, _ambient, _answer = _setup(bundled_texts_path, tmp_path, monkeypatch)
    await _give_affection(coordinator.service, 10)
    await _give_affection(coordinator.service, 5, user_id=OTHER_ID)
    pending = await _start_event(coordinator)
    plain = next(index for index, option in enumerate(pending.options) if option.delta == 1)
    assert coordinator.answer(GROUP_ID, OTHER_ID, "路人", str(plain + 1)) is True

    sent = await _settle(coordinator)

    message = sent[0]
    assert _at_ids(message) == [OTHER_ID, TRIGGER_ID]
    lines = _lines(message)
    assert f"馒头收下了「{pending.options[plain].text}」，好感度 +1，当前 6" in lines[0]
    assert lines[1].endswith("馒头等不到你的回答，失望地走开了，好感度 -5，当前 5")
    assert (await coordinator.service.profile(GROUP_ID, TRIGGER_ID)).affection == 5
    assert (await coordinator.service.profile(GROUP_ID, OTHER_ID)).affection == 6


@pytest.mark.parametrize(
    ("delta", "expected"),
    [
        (2, "馒头开心地收下了「{option}」，好感度 +2"),
        (1, "馒头收下了「{option}」，好感度 +1"),
        (-2, "馒头嫌弃地躲开了「{option}」，好感度 -2"),
    ],
)
async def test_event_settlement_uses_unlimited_adjust_path(
    bundled_texts_path: Path, tmp_path: Path, delta: int, expected: str
) -> None:
    config = Config(mantou_affection_daily_gain_limit=3)
    service = _service(tmp_path, config=config)
    today = service._now().date().isoformat()

    def limited(profile: Profile) -> None:
        profile.affection = 10
        profile.gain_date = today
        profile.gain_points = 3

    await service.store.update_profile(GROUP_ID, TRIGGER_ID, "桃友", limited)
    coordinator = EventCoordinator(
        service, config, EventLibrary(_events_path(bundled_texts_path)), rng=random.Random(0)
    )
    pending = await _start_event(coordinator)
    index = next(
        position for position, option in enumerate(pending.options) if option.delta == delta
    )
    assert coordinator.answer(GROUP_ID, TRIGGER_ID, "桃友", str(index + 1)) is True

    sent = await _settle(coordinator)

    assert expected.format(option=pending.options[index].text) in _lines(sent[0])[0]
    assert _lines(sent[0])[0].endswith(f"，当前 {max(0, 10 + delta)}")
    profile = await service.profile(GROUP_ID, TRIGGER_ID)
    assert profile.affection == max(0, 10 + delta)
    assert profile.gain_points == 3


async def test_event_timeout_applies_penalty(
    bundled_texts_path: Path, tmp_path: Path
) -> None:
    config = Config()
    service = _service(tmp_path, config=config)
    await _give_affection(service, 10)
    coordinator = EventCoordinator(
        service, config, EventLibrary(_events_path(bundled_texts_path)), rng=random.Random(0)
    )
    pending = await _start_event(coordinator)
    sent: list[Message] = []

    async def fake_send(message: Message) -> None:
        sent.append(message)

    task = coordinator.schedule(
        pending, group_id=GROUP_ID, send=fake_send, timeout=0.01
    )
    await asyncio.wait_for(task, 2)

    assert _at_ids(sent[0]) == [TRIGGER_ID]
    assert "馒头等不到你的回答，失望地走开了，好感度 -5，当前 5" in _lines(sent[0])[0]
    assert (await service.profile(GROUP_ID, TRIGGER_ID)).affection == 5
    assert coordinator.pending == {}


async def test_start_without_library_returns_none(tmp_path: Path) -> None:
    coordinator = EventCoordinator(_service(tmp_path), Config(), None, rng=random.Random(0))
    assert coordinator.start(GROUP_ID, TRIGGER_ID, "桃友") is None
