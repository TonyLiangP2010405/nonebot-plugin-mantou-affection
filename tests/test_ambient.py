import asyncio
import json
import random
from pathlib import Path
from time import time

import pytest
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message
from nonebot.adapters.onebot.v11.event import Sender

from nonebot_plugin_mantou_affection.ambient import EventCoordinator, register_ambient
from nonebot_plugin_mantou_affection.config import Config
from nonebot_plugin_mantou_affection.copywriting import AffectionTextLibrary
from nonebot_plugin_mantou_affection.events import EventLibrary
from nonebot_plugin_mantou_affection.models import Profile
from nonebot_plugin_mantou_affection.service import AffectionService
from nonebot_plugin_mantou_affection.storage import AffectionStore

GROUP_ID = "90001"
USER_ID = "90002"
KEY = (GROUP_ID, USER_ID)


def _event(
    text: str = "晚上好", group_id: int = 90001, user_id: int = 90002
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
        sender=Sender(user_id=user_id, nickname="桃友", role="member"),
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


async def _give_affection(service: AffectionService, score: int) -> None:
    def bump(profile: Profile) -> None:
        profile.affection = score

    await service.store.update_profile(GROUP_ID, USER_ID, "桃友", bump)


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


async def _finish_event(coordinator: EventCoordinator, answer: str = "1") -> None:
    pending = coordinator.pending[KEY]
    assert coordinator.answer(GROUP_ID, USER_ID, answer) is True
    assert pending.task is not None
    await asyncio.wait_for(pending.task, 2)


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
    assert message[0].data["qq"] == USER_ID
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
    assert [segment.type for segment in sent[0]] == ["at", "text"]
    assert sent[0][0].data.get("qq") == str(_event().user_id)
    text = str(sent[0])
    assert "⚡ 触发随机事件！" in text
    assert "请在 3 秒内作答，直接发送 1、2、3 即可（答题不用@），超时好感度 -5！" in text
    pending = coordinator.pending[KEY]
    library_options = {
        frozenset(option.text for option in event.options)
        for event in EventLibrary(_events_path(bundled_texts_path)).events
    }
    assert frozenset(option.text for option in pending.options) in library_options
    await _finish_event(coordinator)


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
    await ambient.handlers[0].call(_event())
    assert KEY in coordinator.pending

    sent.clear()
    await ambient.handlers[0].call(_event())

    assert len(sent) == 1
    assert [segment.type for segment in sent[0]] == ["at", "text"]
    assert KEY in coordinator.pending
    await _finish_event(coordinator)


async def test_answer_matcher_ignores_invalid_input(
    bundled_texts_path: Path, tmp_path: Path, monkeypatch
) -> None:
    coordinator, _sent, ambient, answer = _setup(
        bundled_texts_path,
        tmp_path,
        monkeypatch,
        mantou_affection_ambient_event_ratio=1.0,
        mantou_affection_event_timeout=3,
    )
    await _give_affection(coordinator.service, 10)
    await ambient.handlers[0].call(_event())
    pending = coordinator.pending[KEY]

    for text in ("", "4", "一", "1 2", "晚上好"):
        await answer.handlers[0].call(_event(text))
        assert pending.future.done() is False

    await _finish_event(coordinator, "2")
    assert pending.future.done() is True


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

    await service.store.update_profile(GROUP_ID, USER_ID, "桃友", limited)

    coordinator = EventCoordinator(
        service, config, EventLibrary(_events_path(bundled_texts_path)), rng=random.Random(0)
    )
    pending = coordinator.start(GROUP_ID, USER_ID)
    assert pending is not None
    index = next(
        position for position, option in enumerate(pending.options) if option.delta == delta
    )
    assert coordinator.answer(GROUP_ID, USER_ID, str(index + 1)) is True

    reply = await coordinator.settle(
        pending, group_id=GROUP_ID, user_id=USER_ID, nickname="桃友", timeout=1
    )

    assert reply == expected.format(option=pending.options[index].text)
    profile = await service.profile(GROUP_ID, USER_ID)
    assert profile.affection == max(0, 10 + delta)
    assert profile.gain_points == 3
    assert coordinator.pending == {}


async def test_event_timeout_applies_penalty(
    bundled_texts_path: Path, tmp_path: Path
) -> None:
    config = Config()
    service = _service(tmp_path, config=config)
    await _give_affection(service, 10)
    coordinator = EventCoordinator(
        service, config, EventLibrary(_events_path(bundled_texts_path)), rng=random.Random(0)
    )
    pending = coordinator.start(GROUP_ID, USER_ID)
    assert pending is not None

    reply = await coordinator.settle(
        pending, group_id=GROUP_ID, user_id=USER_ID, nickname="桃友", timeout=0.05
    )

    assert reply == "馒头等不到你的回答，失望地走开了，好感度 -5"
    assert (await service.profile(GROUP_ID, USER_ID)).affection == 5
    assert coordinator.pending == {}


async def test_start_without_library_returns_none(tmp_path: Path) -> None:
    coordinator = EventCoordinator(_service(tmp_path), Config(), None, rng=random.Random(0))
    assert coordinator.start(GROUP_ID, USER_ID) is None
