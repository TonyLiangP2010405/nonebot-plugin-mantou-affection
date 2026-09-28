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
from nonebot_plugin_mantou_affection.logic import UPSET_EVENT_RATIOS, snapshot_for
from nonebot_plugin_mantou_affection.models import Profile
from nonebot_plugin_mantou_affection.service import AffectionService
from nonebot_plugin_mantou_affection.storage import AffectionStore

GROUP_ID = "90001"
TRIGGER_ID = "90002"
OTHER_ID = "90003"


class FixedRng(random.Random):
    """固定 random() 返回值，用来精确控制抽题与触发判定。"""

    def __init__(self, value: float):
        super().__init__(0)
        self.value = value

    def random(self) -> float:
        return self.value


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


def _upset_events_path(bundled_texts_path: Path) -> Path:
    return bundled_texts_path.with_name("affection_events_upset.json")


def _coordinator(
    service: AffectionService,
    config: Config,
    bundled_texts_path: Path,
    *,
    rng: random.Random,
    upset_library: bool = True,
) -> EventCoordinator:
    return EventCoordinator(
        service,
        config,
        EventLibrary(_events_path(bundled_texts_path)),
        upset_library=(
            EventLibrary(_upset_events_path(bundled_texts_path)) if upset_library else None
        ),
        rng=rng,
    )


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
    rng = config_kwargs.pop("coordinator_rng", random.Random(0))
    config = Config(mantou_affection_ambient_probability=1.0, **config_kwargs)
    service = _service(tmp_path, AffectionTextLibrary(bundled_texts_path), config)
    coordinator = _coordinator(service, config, bundled_texts_path, rng=rng)
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
    assert "大家都可以回答，直接发送 1、2、3 即可（答题不用@）" in text
    assert "请在 3 秒内作答，超时好感度 -5！" in text
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


# ---------------------------------------------- 闹别扭事件 / 抽题与结算


@pytest.mark.parametrize(
    ("affection", "band", "ratio"),
    [
        (0, "neutral", 0.0),
        (10, "neutral", 0.0),
        (30, "warm", 0.1),
        (60, "close", 0.2),
        (100, "flirty", 0.35),
        (160, "intimate", 0.5),
        (999, "intimate", 0.5),
    ],
)
def test_upset_ratio_table(affection: int, band: str, ratio: float) -> None:
    assert snapshot_for(affection).band == band
    assert UPSET_EVENT_RATIOS[band] == ratio


@pytest.mark.parametrize(
    ("affection", "value", "upset"),
    [
        (0, 0.0, False),
        (30, 0.05, True),
        (30, 0.15, False),
        (60, 0.15, True),
        (60, 0.25, False),
        (100, 0.30, True),
        (100, 0.40, False),
        (160, 0.45, True),
        (160, 0.55, False),
    ],
)
def test_start_picks_library_by_affection(
    tmp_path: Path,
    bundled_texts_path: Path,
    affection: int,
    value: float,
    upset: bool,
) -> None:
    service = _service(tmp_path)
    coordinator = _coordinator(
        service, Config(), bundled_texts_path, rng=FixedRng(value)
    )

    pending = coordinator.start(GROUP_ID, TRIGGER_ID, "桃友", affection=affection)

    assert pending is not None
    assert pending.event.upset is upset


def test_start_falls_back_to_normal_library(
    tmp_path: Path, bundled_texts_path: Path
) -> None:
    service = _service(tmp_path)
    coordinator = _coordinator(
        service, Config(), bundled_texts_path, rng=FixedRng(0.0), upset_library=False
    )

    pending = coordinator.start(GROUP_ID, TRIGGER_ID, "桃友", affection=999)

    assert pending is not None
    assert pending.event.upset is False


def test_upset_correct_option_position_varies(
    tmp_path: Path, bundled_texts_path: Path
) -> None:
    service = _service(tmp_path)
    positions = set()

    for _ in range(40):
        coordinator = _coordinator(
            service, Config(), bundled_texts_path, rng=FixedRng(0.0)
        )
        pending = coordinator.start(GROUP_ID, TRIGGER_ID, "桃友", affection=999)
        assert pending is not None and pending.event.upset is True
        positions.add(
            next(
                index
                for index, option in enumerate(pending.options)
                if option.delta == 10
            )
        )

    assert positions == {0, 1, 2}


def test_upset_event_message_uses_upset_prefix(
    tmp_path: Path, bundled_texts_path: Path
) -> None:
    config = Config(mantou_affection_event_timeout=20)
    service = _service(tmp_path, config=config)
    coordinator = _coordinator(service, config, bundled_texts_path, rng=FixedRng(0.0))
    pending = coordinator.start(GROUP_ID, TRIGGER_ID, "桃友", affection=999)
    assert pending is not None

    message = coordinator.event_message(pending)

    assert message.startswith("💢 馒头闹别扭了！")
    assert pending.event.text in message
    assert "大家都可以回答，直接发送 1、2、3 即可（答题不用@）" in message
    assert "请在 20 秒内作答，超时好感度按比例大扣！" in message
    assert all(option.text in message for option in pending.options)


def test_normal_event_message_uses_plain_prefix(
    tmp_path: Path, bundled_texts_path: Path
) -> None:
    config = Config()
    service = _service(tmp_path, config=config)
    coordinator = _coordinator(service, config, bundled_texts_path, rng=FixedRng(0.99))
    pending = coordinator.start(GROUP_ID, TRIGGER_ID, "桃友", affection=0)
    assert pending is not None

    message = coordinator.event_message(pending)

    assert message.startswith("⚡ 触发随机事件！")
    assert "大家都可以回答，直接发送 1、2、3 即可（答题不用@）" in message
    assert message.count("💢") == 0


async def _answer_upset(
    coordinator: EventCoordinator, affection: int, *, delta: int = 10
) -> tuple[list[Message], PendingEvent]:
    await _give_affection(coordinator.service, affection)
    pending = coordinator.start(GROUP_ID, TRIGGER_ID, "桃友", affection=999)
    assert pending is not None
    assert pending.event.upset is True
    index = next(
        position
        for position, option in enumerate(pending.options)
        if option.delta == delta
    )
    assert coordinator.answer(GROUP_ID, TRIGGER_ID, "桃友", str(index + 1)) is True
    return await _settle(coordinator), pending


async def test_upset_best_option_rewards_ten(
    tmp_path: Path, bundled_texts_path: Path
) -> None:
    service = _service(tmp_path)
    coordinator = _coordinator(service, Config(), bundled_texts_path, rng=FixedRng(0.0))

    sent, _pending = await _answer_upset(coordinator, 30, delta=10)

    assert _at_ids(sent[0]) == [TRIGGER_ID]
    assert _lines(sent[0])[0].endswith("馒头一下子被哄好了，好感度 +10，当前 40！")
    assert (await service.profile(GROUP_ID, TRIGGER_ID)).affection == 40


async def test_upset_best_option_works_from_zero_affection(
    tmp_path: Path, bundled_texts_path: Path
) -> None:
    service = _service(tmp_path)
    coordinator = _coordinator(service, Config(), bundled_texts_path, rng=FixedRng(0.0))

    sent, _pending = await _answer_upset(coordinator, 0, delta=10)

    assert _lines(sent[0])[0].endswith("馒头一下子被哄好了，好感度 +10，当前 10！")
    assert (await service.profile(GROUP_ID, TRIGGER_ID)).affection == 10


@pytest.mark.parametrize(
    ("affection", "option_delta", "expected_line", "expected_affection"),
    [
        (30, -5, "馒头别过脸去，好感度 -5，当前 25", 25),
        (30, -10, "馒头听完更委屈了，好感度 -10，当前 20…", 20),
        (200, -5, "馒头别过脸去，好感度 -10，当前 190", 190),
        (200, -10, "馒头听完更委屈了，好感度 -20，当前 180…", 180),
        (999, -5, "馒头别过脸去，好感度 -49，当前 950", 950),
        (999, -10, "馒头听完更委屈了，好感度 -99，当前 900…", 900),
        (500, 10, "馒头一下子被哄好了，好感度 +10，当前 510！", 510),
    ],
)
async def test_upset_penalties_scale_with_affection(
    tmp_path: Path,
    bundled_texts_path: Path,
    affection: int,
    option_delta: int,
    expected_line: str,
    expected_affection: int,
) -> None:
    service = _service(tmp_path)
    coordinator = _coordinator(service, Config(), bundled_texts_path, rng=FixedRng(0.0))

    sent, _pending = await _answer_upset(coordinator, affection, delta=option_delta)

    assert _lines(sent[0])[0].endswith(expected_line)
    assert (await service.profile(GROUP_ID, TRIGGER_ID)).affection == expected_affection


async def test_upset_penalties_use_each_answerers_affection(
    tmp_path: Path, bundled_texts_path: Path
) -> None:
    service = _service(tmp_path)
    coordinator = _coordinator(service, Config(), bundled_texts_path, rng=FixedRng(0.0))
    await _give_affection(service, 200)
    await _give_affection(service, 50, user_id=OTHER_ID)
    pending = coordinator.start(GROUP_ID, TRIGGER_ID, "桃友", affection=999)
    assert pending is not None and pending.event.upset is True
    trap = next(
        position for position, option in enumerate(pending.options) if option.delta == -10
    )
    wrong = next(
        position for position, option in enumerate(pending.options) if option.delta == -5
    )
    assert coordinator.answer(GROUP_ID, TRIGGER_ID, "桃友", str(trap + 1)) is True
    assert coordinator.answer(GROUP_ID, OTHER_ID, "路人", str(wrong + 1)) is True

    sent = await _settle(coordinator)

    lines = _lines(sent[0])
    assert lines[0].endswith("馒头听完更委屈了，好感度 -20，当前 180…")
    assert lines[1].endswith("馒头别过脸去，好感度 -5，当前 45")
    assert (await service.profile(GROUP_ID, TRIGGER_ID)).affection == 180
    assert (await service.profile(GROUP_ID, OTHER_ID)).affection == 45


@pytest.mark.parametrize(
    ("affection", "penalty"),
    [(30, -10), (40, -10), (200, -20), (999, -99)],
)
async def test_upset_timeout_scales_with_triggerer_affection(
    tmp_path: Path,
    bundled_texts_path: Path,
    affection: int,
    penalty: int,
) -> None:
    service = _service(tmp_path)
    coordinator = _coordinator(service, Config(), bundled_texts_path, rng=FixedRng(0.0))
    await _give_affection(service, affection)
    pending = coordinator.start(GROUP_ID, TRIGGER_ID, "桃友", affection=999)
    assert pending is not None and pending.event.upset is True

    sent = await _settle(coordinator)

    assert _lines(sent[0])[0].endswith(
        f"心凉了半截，好感度 {penalty}，当前 {affection + penalty}"
    )
    assert (await service.profile(GROUP_ID, TRIGGER_ID)).affection == affection + penalty


async def test_poke_upset_penalty_scales_then_doubles(
    tmp_path: Path, bundled_texts_path: Path
) -> None:
    coordinator, service, pending = await _poke_upset_pending(
        tmp_path, bundled_texts_path, 200
    )
    index = next(
        position for position, option in enumerate(pending.options) if option.delta == -10
    )
    assert coordinator.answer(GROUP_ID, TRIGGER_ID, "桃友", str(index + 1)) is True

    sent = await _settle(coordinator)

    assert _lines(sent[0])[0].endswith("馒头听完更委屈了，好感度 -40，当前 160…")
    assert (await service.profile(GROUP_ID, TRIGGER_ID)).affection == 160


async def test_upset_worst_option_stops_at_zero_affection(
    tmp_path: Path, bundled_texts_path: Path
) -> None:
    service = _service(tmp_path)
    coordinator = _coordinator(service, Config(), bundled_texts_path, rng=FixedRng(0.0))

    sent, _pending = await _answer_upset(coordinator, 4, delta=-10)

    assert _lines(sent[0])[0].endswith("馒头听完更委屈了，好感度 -10，当前 0…")
    assert (await service.profile(GROUP_ID, TRIGGER_ID)).affection == 0


async def test_upset_settlement_ignores_daily_gain_limit(
    tmp_path: Path, bundled_texts_path: Path
) -> None:
    config = Config(mantou_affection_daily_gain_limit=1)
    service = _service(tmp_path, config=config)
    coordinator = _coordinator(service, config, bundled_texts_path, rng=FixedRng(0.0))
    today = service._now().date().isoformat()

    def limited(profile: Profile) -> None:
        profile.affection = 40
        profile.gain_date = today
        profile.gain_points = 1

    await service.store.update_profile(GROUP_ID, TRIGGER_ID, "桃友", limited)
    pending = coordinator.start(GROUP_ID, TRIGGER_ID, "桃友", affection=999)
    assert pending is not None
    index = next(
        position for position, option in enumerate(pending.options) if option.delta == 10
    )
    assert coordinator.answer(GROUP_ID, TRIGGER_ID, "桃友", str(index + 1)) is True

    sent = await _settle(coordinator)

    assert _lines(sent[0])[0].endswith("好感度 +10，当前 50！")
    profile = await service.profile(GROUP_ID, TRIGGER_ID)
    assert profile.affection == 50
    assert profile.gain_points == 1


async def test_poke_event_upgrades_to_upset_with_high_affection(
    tmp_path: Path, bundled_texts_path: Path
) -> None:
    service = _service(tmp_path)
    coordinator = _coordinator(service, Config(), bundled_texts_path, rng=FixedRng(0.0))
    await _give_affection(service, 999)

    async def fake_send(message) -> None:
        return None

    started = await coordinator.start_poke_event(
        group_id=GROUP_ID,
        user_id=TRIGGER_ID,
        nickname="桃友",
        send=fake_send,
        chance=1.0,
        affection=999,
    )

    assert started is not None
    assert started.startswith("💢 馒头闹别扭了！")
    pending = coordinator.pending[GROUP_ID]
    assert pending.event.upset is True
    assert pending.penalty_scale == 2
    await _cancel_task(coordinator)


@pytest.mark.parametrize(
    ("affection", "value", "upset"),
    [
        (0, 0.0, False),
        (10, 0.0, False),
        (30, 0.05, True),
        (30, 0.55, False),
        (999, 0.45, True),
        (999, 0.55, False),
    ],
)
async def test_poke_event_routing_follows_affection(
    tmp_path: Path, bundled_texts_path: Path, affection: int, value: float, upset: bool
) -> None:
    service = _service(tmp_path)
    coordinator = _coordinator(service, Config(), bundled_texts_path, rng=FixedRng(value))

    async def fake_send(message) -> None:
        return None

    started = await coordinator.start_poke_event(
        group_id=GROUP_ID,
        user_id=TRIGGER_ID,
        nickname="桃友",
        send=fake_send,
        chance=1.0,
        affection=affection,
    )

    assert started is not None
    pending = coordinator.pending[GROUP_ID]
    assert pending.event.upset is upset
    assert pending.penalty_scale == 2
    await _cancel_task(coordinator)


async def _poke_upset_pending(
    tmp_path: Path, bundled_texts_path: Path, affection: int
) -> tuple[EventCoordinator, AffectionService, PendingEvent]:
    service = _service(tmp_path)
    coordinator = _coordinator(service, Config(), bundled_texts_path, rng=FixedRng(0.0))
    await _give_affection(service, affection)

    async def fake_send(message) -> None:
        return None

    started = await coordinator.start_poke_event(
        group_id=GROUP_ID,
        user_id=TRIGGER_ID,
        nickname="桃友",
        send=fake_send,
        chance=1.0,
        affection=999,
    )
    assert started is not None
    pending = coordinator.pending[GROUP_ID]
    assert pending.event.upset is True
    assert pending.penalty_scale == 2
    return coordinator, service, pending


@pytest.mark.parametrize(
    ("delta", "expected_line", "expected_affection"),
    [
        (10, "馒头一下子被哄好了，好感度 +10，当前 40！", 40),
        (-5, "馒头别过脸去，好感度 -10，当前 20", 20),
        (-10, "馒头听完更委屈了，好感度 -20，当前 10…", 10),
    ],
)
async def test_poke_upset_options_scale_penalties_only(
    tmp_path: Path,
    bundled_texts_path: Path,
    delta: int,
    expected_line: str,
    expected_affection: int,
) -> None:
    coordinator, service, pending = await _poke_upset_pending(
        tmp_path, bundled_texts_path, 30
    )
    index = next(
        position
        for position, option in enumerate(pending.options)
        if option.delta == delta
    )
    assert coordinator.answer(GROUP_ID, TRIGGER_ID, "桃友", str(index + 1)) is True

    sent = await _settle(coordinator)

    assert _lines(sent[0])[0].endswith(expected_line)
    assert (await service.profile(GROUP_ID, TRIGGER_ID)).affection == expected_affection


async def test_upset_timeout_deducts_ten(
    tmp_path: Path, bundled_texts_path: Path
) -> None:
    service = _service(tmp_path)
    coordinator = _coordinator(service, Config(), bundled_texts_path, rng=FixedRng(0.0))
    await _give_affection(service, 40)
    pending = coordinator.start(GROUP_ID, TRIGGER_ID, "桃友", affection=999)
    assert pending is not None and pending.event.upset is True
    assert pending.penalty_scale == 1

    sent = await _settle(coordinator)

    assert _at_ids(sent[0]) == [TRIGGER_ID]
    assert _lines(sent[0])[0].endswith(
        "馒头等不到你的回答，心凉了半截，好感度 -10，当前 30"
    )
    assert (await service.profile(GROUP_ID, TRIGGER_ID)).affection == 30


async def test_poke_upset_timeout_deducts_twenty(
    tmp_path: Path, bundled_texts_path: Path
) -> None:
    coordinator, service, _pending = await _poke_upset_pending(
        tmp_path, bundled_texts_path, 40
    )

    sent = await _settle(coordinator)

    assert _lines(sent[0])[0].endswith(
        "馒头等不到你的回答，心凉了半截，好感度 -20，当前 20"
    )
    assert (await service.profile(GROUP_ID, TRIGGER_ID)).affection == 20


async def test_upset_timeout_stops_at_zero_affection(
    tmp_path: Path, bundled_texts_path: Path
) -> None:
    coordinator, service, _pending = await _poke_upset_pending(
        tmp_path, bundled_texts_path, 6
    )

    sent = await _settle(coordinator)

    assert _lines(sent[0])[0].endswith("心凉了半截，好感度 -20，当前 0")
    assert (await service.profile(GROUP_ID, TRIGGER_ID)).affection == 0


async def test_normal_event_timeout_keeps_fixed_penalty(
    tmp_path: Path, bundled_texts_path: Path
) -> None:
    service = _service(tmp_path)
    coordinator = _coordinator(service, Config(), bundled_texts_path, rng=FixedRng(0.99))
    await _give_affection(service, 40)
    pending = coordinator.start(GROUP_ID, TRIGGER_ID, "桃友", affection=0)
    assert pending is not None and pending.event.upset is False

    sent = await _settle(coordinator)

    assert _lines(sent[0])[0].endswith("失望地走开了，好感度 -5，当前 35")
    assert (await service.profile(GROUP_ID, TRIGGER_ID)).affection == 35


async def test_poke_upset_event_message_shows_relative_hint(
    tmp_path: Path, bundled_texts_path: Path
) -> None:
    coordinator, _service_obj, pending = await _poke_upset_pending(
        tmp_path, bundled_texts_path, 30
    )

    message = coordinator.event_message(pending)

    assert message.startswith("💢 馒头闹别扭了！")
    assert "超时好感度按比例大扣！" in message
    assert "超时好感度 -" not in message
