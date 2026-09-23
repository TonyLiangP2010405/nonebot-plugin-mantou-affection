import asyncio
import json
import random
from contextlib import suppress
from datetime import date
from pathlib import Path
from time import time

import pytest
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message
from nonebot.adapters.onebot.v11.event import Sender

from nonebot_plugin_mantou_affection.ambient import EventCoordinator, register_ambient
from nonebot_plugin_mantou_affection.config import Config
from nonebot_plugin_mantou_affection.copywriting import AffectionTextLibrary
from nonebot_plugin_mantou_affection.events import EventLibrary
from nonebot_plugin_mantou_affection.logic import (
    POKE_POSITIVE_FALLBACK,
    POKE_POSITIVE_SCENE,
    perform_poke,
    snapshot_for,
)
from nonebot_plugin_mantou_affection.models import PokeResult, Profile
from nonebot_plugin_mantou_affection.service import AffectionService
from nonebot_plugin_mantou_affection.storage import AffectionStore

TODAY = date(2026, 9, 23)
TODAY_TEXT = TODAY.isoformat()
GROUP_ID = "90001"
USER_ID = "90002"
OTHER_ID = "90003"
POKE_GROUP = 90051
GAIN_HINT = "今天的好感已经拿满啦，明天再来吧。"
BAND_TEXTS = {
    "neutral": "被戳到的{bot}往旁边挪了挪。",
    "warm": "{bot}小声说别戳啦。",
    "close": "{bot}鼓着脸把凳子挪远了。",
    "flirty": "{bot}闷闷地问你是不是只喜欢戳它。",
    "intimate": "{bot}轻轻握住你的手让你停下。",
}


class FixedRng(random.Random):
    """固定 random() 返回值，用来精确控制事件触发判定。"""

    def __init__(self, value: float):
        super().__init__(0)
        self.value = value

    def random(self) -> float:
        return self.value


async def _noop_send(message) -> None:
    return None


def _event(
    text: str, user_id: int = 90002, group_id: int = 90001, nickname: str = "桃友"
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


def _library(tmp_path: Path) -> AffectionTextLibrary:
    path = tmp_path / "poke_texts.json"
    path.write_text(
        json.dumps(
            {POKE_POSITIVE_SCENE: {band: [text] for band, text in BAND_TEXTS.items()}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return AffectionTextLibrary(path)


def _service(
    tmp_path: Path,
    text_library: AffectionTextLibrary | None = None,
    rng: random.Random | None = None,
    **config_kwargs,
) -> AffectionService:
    return AffectionService(
        AffectionStore(tmp_path / "affection.json"),
        Config(**config_kwargs),
        text_library=text_library,
        rng=rng or FixedRng(0.9),
    )


def _events_path(bundled_texts_path: Path) -> Path:
    return bundled_texts_path.with_name("affection_events.json")


def _coordinator(
    service: AffectionService, config: Config, bundled_texts_path: Path, *, rng: random.Random
) -> EventCoordinator:
    return EventCoordinator(
        service, config, EventLibrary(_events_path(bundled_texts_path)), rng=rng
    )


def _poke(
    profile: Profile,
    *,
    affection_max: int = 999,
    daily_gain_limit: int = 3,
    text_picker=None,
) -> PokeResult:
    return perform_poke(
        profile,
        today=TODAY,
        now_timestamp=100.0,
        bot_name="馒头",
        daily_gain_limit=daily_gain_limit,
        affection_max=affection_max,
        text_picker=text_picker,
    )


def _first_line(result: PokeResult) -> str:
    return result.text.split("\n")[0]


def _lines(message: Message) -> list[str]:
    body = "".join(segment.data["text"] for segment in message if segment.type == "text")
    return [line for line in body.split("\n") if line.strip()]


def _at_ids(message: Message) -> list[str]:
    return [str(segment.data["qq"]) for segment in message if segment.type == "at"]


async def _cancel_task(coordinator: EventCoordinator, group_id: str = GROUP_ID) -> None:
    pending = coordinator.pending.get(group_id)
    if pending is None or pending.task is None:
        return
    pending.task.cancel()
    with suppress(asyncio.CancelledError):
        await pending.task


# ------------------------------------------------------------ 纯 +1 机制


def test_poke_grants_one_point_and_keeps_copy() -> None:
    profile = Profile("1", affection=30)
    seen: list[tuple[str, int]] = []

    def picker(scene: str, affection: int) -> str:
        seen.append((scene, affection))
        return BAND_TEXTS[snapshot_for(affection).band]

    result = _poke(profile, text_picker=picker)

    assert result.delta == 1
    assert profile.affection == 31
    assert seen == [(POKE_POSITIVE_SCENE, 31)]
    assert _first_line(result) == BAND_TEXTS["warm"].replace("{bot}", "馒头")
    assert result.text.split("\n")[1] == "好感度 +1，当前 31"


@pytest.mark.parametrize("score", [0, 30, 100, 160])
def test_poke_never_reduces_affection(score: int) -> None:
    profile = Profile("1", affection=score)
    result = _poke(profile, text_picker=lambda scene, affection: None)
    assert result.delta == 1
    assert profile.affection == score + 1


def test_poke_gain_counts_towards_daily_limit() -> None:
    profile = Profile("1", affection=20, gain_date=TODAY_TEXT, gain_points=1)
    result = _poke(profile)
    assert result.delta == 1
    assert profile.gain_points == 2
    assert GAIN_HINT not in result.text


def test_poke_gain_is_capped_by_daily_limit() -> None:
    profile = Profile("1", affection=20, gain_date=TODAY_TEXT, gain_points=3)
    result = _poke(profile)
    assert result.delta == 0
    assert profile.affection == 20
    assert profile.gain_points == 3
    assert result.text.endswith(GAIN_HINT)
    assert "\n好感度" not in result.text


def test_poke_resets_daily_gain_next_day() -> None:
    profile = Profile("1", affection=20, gain_date="2026-09-22", gain_points=3)
    result = _poke(profile)
    assert result.delta == 1
    assert profile.gain_points == 1
    assert GAIN_HINT not in result.text


def test_poke_gain_is_capped_by_affection_max() -> None:
    profile = Profile("1", affection=999)
    result = _poke(profile)
    assert result.delta == 0
    assert profile.affection == 999
    assert GAIN_HINT not in result.text


def test_poke_falls_back_without_library() -> None:
    profile = Profile("1", affection=50)
    result = _poke(profile)
    assert _first_line(result) == POKE_POSITIVE_FALLBACK


def test_poke_reply_falls_back_when_scene_is_empty() -> None:
    profile = Profile("1", affection=50)
    result = _poke(profile, text_picker=lambda scene, affection: None)
    assert _first_line(result) == POKE_POSITIVE_FALLBACK


async def test_service_poke_uses_library_and_normalizes_nickname(tmp_path: Path) -> None:
    service = _service(tmp_path, _library(tmp_path))

    def bump(profile: Profile) -> None:
        profile.affection = 30

    await service.store.update_profile("100", "200", "桃友", bump)
    result = await service.poke("100", "200", "  桃  \n 友 ")

    assert result.delta == 1
    assert _first_line(result) == BAND_TEXTS["warm"].replace("{bot}", "馒头")
    assert result.text.split("\n")[1] == "好感度 +1，当前 31"
    assert (await service.profile("100", "200")).nickname == "桃 友"


async def test_public_poke_api_returns_poke_result(
    bundled_texts_path: Path, monkeypatch
) -> None:
    from nonebot_plugin_mantou_affection import PokeResult as ExportedResult
    from nonebot_plugin_mantou_affection import plugin_config, poke

    assert ExportedResult is PokeResult

    texts = json.loads(bundled_texts_path.read_text(encoding="utf-8"))["crystelf.poke"]
    monkeypatch.setattr(plugin_config, "mantou_affection_poke_event_chance", 0.0)

    result = await poke("poke-api-group", "poke-api-user", nickname="桃友")

    assert isinstance(result, PokeResult)
    assert result.delta == 1
    assert _first_line(result) in texts["neutral"]
    assert result.text.split("\n")[1] == "好感度 +1，当前 1"


async def test_public_poke_api_accepts_send_without_event(monkeypatch) -> None:
    from nonebot_plugin_mantou_affection import plugin_config, poke

    sent: list[str] = []

    async def fake_send(message) -> None:
        sent.append(str(message))

    monkeypatch.setattr(plugin_config, "mantou_affection_poke_event_chance", 0.0)

    result = await poke("poke-no-event", "poke-no-event", nickname="桃友", send=fake_send)

    assert result.delta == 1
    assert sent == []


# ------------------------------------------- 戳出随机事件 / 扣分翻倍


async def test_poke_event_message_shows_double_timeout_penalty(
    tmp_path: Path, bundled_texts_path: Path
) -> None:
    config = Config(mantou_affection_event_timeout=3)
    service = _service(tmp_path, config=config)
    coordinator = _coordinator(service, config, bundled_texts_path, rng=FixedRng(0.0))

    pending = coordinator.start(GROUP_ID, USER_ID, "桃友", penalty_scale=2)
    assert pending is not None
    message = coordinator.event_message(pending)

    assert "请在 3 秒内作答" in message
    assert "超时好感度 -10！" in message
    assert coordinator.pending == {GROUP_ID: pending}


async def test_start_poke_event_requires_send(tmp_path: Path, bundled_texts_path: Path) -> None:
    config = Config()
    service = _service(tmp_path, config=config)
    coordinator = _coordinator(service, config, bundled_texts_path, rng=FixedRng(0.0))

    started = await coordinator.start_poke_event(
        group_id=GROUP_ID, user_id=USER_ID, nickname="桃友", send=None, chance=1.0
    )

    assert started is None
    assert coordinator.pending == {}


async def test_start_poke_event_respects_chance(tmp_path: Path, bundled_texts_path: Path) -> None:
    config = Config()
    service = _service(tmp_path, config=config)
    coordinator = _coordinator(service, config, bundled_texts_path, rng=FixedRng(0.9))

    started = await coordinator.start_poke_event(
        group_id=GROUP_ID, user_id=USER_ID, nickname="桃友", send=_noop_send, chance=0.01
    )

    assert started is None
    assert coordinator.pending == {}


async def test_start_poke_event_falls_back_when_group_busy(
    tmp_path: Path, bundled_texts_path: Path
) -> None:
    config = Config()
    service = _service(tmp_path, config=config)
    coordinator = _coordinator(service, config, bundled_texts_path, rng=FixedRng(0.0))
    running = coordinator.start(GROUP_ID, "90009", "路人")
    assert running is not None

    started = await coordinator.start_poke_event(
        group_id=GROUP_ID, user_id=USER_ID, nickname="桃友", send=_noop_send, chance=1.0
    )

    assert started is None
    assert coordinator.pending[GROUP_ID] is running
    await _cancel_task(coordinator)


async def test_start_poke_event_without_library_falls_back(tmp_path: Path) -> None:
    config = Config()
    service = _service(tmp_path, config=config)
    coordinator = EventCoordinator(service, config, None, rng=FixedRng(0.0))

    started = await coordinator.start_poke_event(
        group_id=GROUP_ID, user_id=USER_ID, nickname="桃友", send=_noop_send, chance=1.0
    )

    assert started is None


async def test_poke_api_returns_event_message_when_triggered(
    tmp_path: Path, bundled_texts_path: Path, monkeypatch
) -> None:
    from nonebot_plugin_mantou_affection import poke

    config = Config(mantou_affection_event_timeout=3)
    service = _service(tmp_path, config=config)
    coordinator = _coordinator(service, config, bundled_texts_path, rng=FixedRng(0.0))
    monkeypatch.setattr("nonebot_plugin_mantou_affection.event_coordinator", coordinator)
    monkeypatch.setattr("nonebot_plugin_mantou_affection.plugin_config", config)
    events: list[str] = []

    async def fake_send(message) -> None:
        events.append(str(message))

    result = await poke(POKE_GROUP, USER_ID, nickname="桃友", send=fake_send)

    assert result.delta == 1
    assert result.text.startswith("⚡ 触发随机事件！")
    assert "超时好感度 -10！" in result.text
    # 事件消息由调用方发送(poke 返回的 text), send 只用于结算消息
    assert events == []
    assert POKE_POSITIVE_FALLBACK not in result.text
    assert "好感度 +1" not in result.text
    assert coordinator.pending[str(POKE_GROUP)].penalty_scale == 2
    await _cancel_task(coordinator, str(POKE_GROUP))


@pytest.mark.parametrize(
    ("option_delta", "expected_value"),
    [(-2, -4), (1, 1), (2, 2)],
)
async def test_poke_event_settlement_doubles_only_penalties(
    tmp_path: Path, bundled_texts_path: Path, option_delta: int, expected_value: int
) -> None:
    config = Config()
    service = _service(tmp_path, config=config)

    def bump(profile: Profile) -> None:
        profile.affection = 20

    await service.store.update_profile(GROUP_ID, USER_ID, "桃友", bump)
    coordinator = _coordinator(service, config, bundled_texts_path, rng=FixedRng(0.0))
    pending = coordinator.start(GROUP_ID, USER_ID, "桃友", penalty_scale=2)
    assert pending is not None
    index = next(
        position
        for position, option in enumerate(pending.options)
        if option.delta == option_delta
    )
    assert coordinator.answer(GROUP_ID, USER_ID, "桃友", str(index + 1)) is True
    sent: list[Message] = []

    async def fake_send(message: Message) -> None:
        sent.append(message)

    task = coordinator.schedule(pending, group_id=GROUP_ID, send=fake_send, timeout=0.01)
    await asyncio.wait_for(task, 2)

    line = _lines(sent[0])[0]
    assert f"，好感度 {expected_value:+d}，当前 {20 + expected_value}" in line
    assert (await service.profile(GROUP_ID, USER_ID)).affection == 20 + expected_value


async def test_poke_event_timeout_applies_double_penalty(
    tmp_path: Path, bundled_texts_path: Path
) -> None:
    config = Config()
    service = _service(tmp_path, config=config)

    def bump(profile: Profile) -> None:
        profile.affection = 30

    await service.store.update_profile(GROUP_ID, USER_ID, "桃友", bump)
    coordinator = _coordinator(service, config, bundled_texts_path, rng=FixedRng(0.0))
    pending = coordinator.start(GROUP_ID, USER_ID, "桃友", penalty_scale=2)
    assert pending is not None
    sent: list[Message] = []

    async def fake_send(message: Message) -> None:
        sent.append(message)

    task = coordinator.schedule(pending, group_id=GROUP_ID, send=fake_send, timeout=0.01)
    await asyncio.wait_for(task, 2)

    assert _at_ids(sent[0]) == [USER_ID]
    assert _lines(sent[0])[0].endswith("好感度 -10，当前 20")
    assert (await service.profile(GROUP_ID, USER_ID)).affection == 20


async def test_poke_event_answers_use_existing_answer_matcher(
    tmp_path: Path, bundled_texts_path: Path, monkeypatch
) -> None:
    from nonebot_plugin_mantou_affection import poke

    config = Config(mantou_affection_event_timeout=3)
    service = _service(tmp_path, _library(tmp_path))
    coordinator = _coordinator(service, config, bundled_texts_path, rng=FixedRng(0.0))
    _ambient, answer = register_ambient(service, config, coordinator)
    monkeypatch.setattr("nonebot_plugin_mantou_affection.event_coordinator", coordinator)
    monkeypatch.setattr("nonebot_plugin_mantou_affection.plugin_config", config)

    async def fake_send(message) -> None:
        return None

    result = await poke(POKE_GROUP, USER_ID, nickname="桃友", send=fake_send)
    assert result.text.startswith("⚡ 触发随机事件！")
    pending = coordinator.pending[str(POKE_GROUP)]

    await answer.handlers[0].call(
        _event("2", user_id=90003, group_id=POKE_GROUP, nickname="路人")
    )
    await answer.handlers[0].call(
        _event("9", user_id=90003, group_id=POKE_GROUP, nickname="路人")
    )
    await answer.handlers[0].call(_event("3", group_id=POKE_GROUP))

    assert list(pending.answers) == [OTHER_ID, USER_ID]
    assert pending.penalty_scale == 2
    await _cancel_task(coordinator, str(POKE_GROUP))
