from __future__ import annotations

import asyncio
import json
import random
from contextlib import suppress
from pathlib import Path
from time import time as now_timestamp

import pytest
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message
from nonebot.adapters.onebot.v11.event import Sender

from nonebot_plugin_mantou_affection.ambient import EventCoordinator, register_ambient
from nonebot_plugin_mantou_affection.commands import register_commands
from nonebot_plugin_mantou_affection.config import Config
from nonebot_plugin_mantou_affection.models import Profile
from nonebot_plugin_mantou_affection.service import AffectionService
from nonebot_plugin_mantou_affection.storage import AffectionStore
from nonebot_plugin_mantou_affection.whoami import (
    BUNDLED_WHOAMI_PATH,
    WHOAMI_CHARACTER,
    WHOAMI_SCENE,
    WhoAmICoordinator,
    WhoAmILibrary,
    guess_candidates,
    normalize_guess,
)

GROUP_ID = "99001"
INITIATOR = "99002"
OTHER = "99003"
OTHER_B = "99004"

ENTRIES = [
    {
        "answer": "胡桃",
        "game": "原神",
        "category": "character",
        "aliases": ["往生堂主"],
        "hints": ["第一层线索", "第二层线索", "第三层线索"],
    },
    {
        "answer": "钟离",
        "game": "原神",
        "category": "character",
        "aliases": ["岩王帝君"],
        "hints": ["甲的线索", "乙的线索", "丙的线索"],
    },
]


class PickFirst(random.Random):
    """总是抽第一道题，让用例不依赖随机结果。"""

    def __init__(self) -> None:
        super().__init__(0)

    def choice(self, seq):  # type: ignore[override]
        return seq[0]


class FixedRng(random.Random):
    def __init__(self, value: float):
        super().__init__(0)
        self.value = value

    def random(self) -> float:
        return self.value


def _event(
    text: str = "晚上好",
    user_id: int = 99002,
    group_id: int = 99001,
    nickname: str = "玩家",
) -> GroupMessageEvent:
    message = Message(text)
    return GroupMessageEvent(
        time=int(now_timestamp()),
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


def _library(tmp_path: Path, entries: list[dict] | None = None) -> WhoAmILibrary:
    path = tmp_path / "whoami_puzzles.json"
    payload = {"entries": entries if entries is not None else ENTRIES}
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return WhoAmILibrary(path)


def _service(tmp_path: Path, config: Config | None = None) -> AffectionService:
    return AffectionService(
        AffectionStore(tmp_path / "affection.json"),
        config or Config(mantou_affection_newbie_protection_below=0),
        rng=random.Random(0),
    )


def _coordinator(
    tmp_path: Path,
    config: Config | None = None,
    *,
    timeout: float = 0.06,
    entries: list[dict] | None = None,
) -> WhoAmICoordinator:
    config = config or Config(mantou_affection_newbie_protection_below=0)
    coordinator = WhoAmICoordinator(
        _service(tmp_path, config),
        config,
        _library(tmp_path, entries),
        rng=PickFirst(),
    )
    coordinator.timeout = timeout
    return coordinator


async def _give_affection(service: AffectionService, score: int, user_id: str) -> None:
    def bump(profile: Profile) -> None:
        profile.affection = score

    await service.store.update_profile(GROUP_ID, user_id, "玩家", bump)


async def _start(coordinator: WhoAmICoordinator, sent: list[Message]) -> bool:
    async def fake_send(message) -> None:
        sent.append(Message(message) if isinstance(message, str) else message)

    return await coordinator.start(
        group_id=GROUP_ID, user_id=INITIATOR, nickname="玩家", send=fake_send
    )


def _result_lines(message: Message) -> dict[str, str]:
    lines: dict[str, str] = {}
    owner: str | None = None
    buffer: list[str] = []

    def flush() -> None:
        nonlocal owner, buffer
        if owner is not None:
            value = "".join(buffer).strip()
            if value:
                lines[owner] = value
        owner, buffer = None, []

    for segment in message:
        if segment.type == "at":
            if owner is None:
                owner = str(segment.data["qq"])
            else:
                buffer.append(f"@{segment.data['qq']}")
            continue
        for index, part in enumerate(str(segment.data.get("text", "")).split("\n")):
            if index:
                flush()
            buffer.append(part)
    flush()
    return lines


async def _stop(coordinator: WhoAmICoordinator) -> None:
    pending = coordinator.pending.get(GROUP_ID)
    if pending is None or pending.task is None:
        return
    pending.task.cancel()
    with suppress(asyncio.CancelledError):
        await pending.task


def test_bundled_bank_is_valid() -> None:
    payload = json.loads(BUNDLED_WHOAMI_PATH.read_text(encoding="utf-8"))
    entries = payload["entries"]
    assert len(entries) >= 60
    games = {entry["game"] for entry in entries}
    assert {"原神", "星穹铁道", "绝区零", "崩坏3"} <= games
    assert {entry["category"] for entry in entries} == {WHOAMI_CHARACTER, WHOAMI_SCENE}

    answers = [normalize_guess(entry["answer"]) for entry in entries]
    assert len(answers) == len(set(answers))
    aliases: dict[str, str] = {}
    for entry in entries:
        assert entry["answer"].strip()
        assert entry["hints"] and len(entry["hints"]) == 3
        assert all(hint.strip() and "\n" not in hint for hint in entry["hints"])
        answer = normalize_guess(entry["answer"])
        assert answer not in normalize_guess(entry["hints"][0])
        assert answer not in normalize_guess(entry["hints"][1])
        for alias in [entry["answer"], *entry["aliases"]]:
            key = normalize_guess(alias)
            assert aliases.get(key, entry["answer"]) == entry["answer"]
            aliases[key] = entry["answer"]


def test_normalize_guess_handles_noise_and_particles() -> None:
    assert normalize_guess(" 胡 桃。 ") == "胡桃"
    assert normalize_guess("Ｒａｎｄｏｍ　Ｐｌａｙ") == "randomplay"
    assert "胡桃" in guess_candidates("我猜是胡桃吧")
    assert "胡桃" in guess_candidates("胡桃啊")
    assert guess_candidates("") == ()
    assert guess_candidates("胡桃" * 30) == ()


def test_library_resolves_aliases_and_ignores_chatter(tmp_path: Path) -> None:
    library = _library(tmp_path)
    assert library.resolve("往生堂主").answer == "胡桃"
    assert library.resolve("岩王帝君").answer == "钟离"
    assert library.resolve("今天吃点什么") is None


def test_library_skips_bad_entries(tmp_path: Path) -> None:
    entries = [
        {"answer": "坏题", "game": "原神", "category": "character", "hints": ["只有一条"]},
        {
            "answer": "胡桃",
            "game": "原神",
            "category": "character",
            "aliases": [],
            "hints": ["一", "二", "三"],
        },
        {
            "answer": "胡桃",
            "game": "原神",
            "category": "character",
            "aliases": [],
            "hints": ["四", "五", "六"],
        },
        {
            "answer": "不存在的类别",
            "game": "原神",
            "category": "npc",
            "aliases": [],
            "hints": ["一", "二", "三"],
        },
    ]
    library = _library(tmp_path, entries)
    assert [entry.answer for entry in library.entries] == ["胡桃"]


async def test_start_sends_first_hint_and_reveals_the_rest(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path, timeout=0.06)
    sent: list[Message] = []
    assert await _start(coordinator, sent) is True
    pending = coordinator.pending[GROUP_ID]

    body = sent[0].extract_plain_text()
    assert "🕵️ 我是谁？" in body
    assert "线索 1/3：第一层线索" in body
    assert "第二层线索" not in body
    assert "每人只能猜一次" in body
    assert "第 1 条线索答对 +5" in body

    await asyncio.wait_for(pending.task, 1)
    hints = [message.extract_plain_text() for message in sent[1:3]]
    assert "线索 2/3：第二层线索" in hints[0]
    assert "线索 3/3：第三层线索" in hints[1]
    assert "答案：胡桃（原神）" in sent[-1].extract_plain_text()


async def test_scene_round_uses_place_wording(tmp_path: Path) -> None:
    entries = [
        {
            "answer": "蒙德城",
            "game": "原神",
            "category": "scene",
            "aliases": ["蒙德"],
            "hints": ["一", "二", "三"],
        }
    ]
    coordinator = _coordinator(tmp_path, timeout=60, entries=entries)
    sent: list[Message] = []
    assert await _start(coordinator, sent) is True
    body = sent[0].extract_plain_text()
    assert "🗺️ 这是哪里？" in body
    assert "直接发地名作答" in body
    await _stop(coordinator)


async def test_answer_rules(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path, timeout=60)
    sent: list[Message] = []
    assert await _start(coordinator, sent) is True
    pending = coordinator.pending[GROUP_ID]

    assert coordinator.answer(GROUP_ID, OTHER, "路人", "晚上好") is False
    assert coordinator.answer(GROUP_ID, OTHER, "路人", "我猜是胡桃吧") is True
    assert pending.answers[OTHER].correct is True
    assert pending.answers[OTHER].level == 1
    assert coordinator.answer(GROUP_ID, OTHER, "路人", "钟离") is False

    assert coordinator.answer(GROUP_ID, OTHER_B, "路人乙", "钟离") is True
    assert pending.answers[OTHER_B].correct is False

    pending.accepting = False
    assert coordinator.answer(GROUP_ID, "99005", "路人丙", "胡桃") is False
    await _stop(coordinator)


async def test_settle_scores_by_hint_level(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path, timeout=0.06)
    for user_id in (INITIATOR, OTHER, OTHER_B):
        await _give_affection(coordinator.service, 100, user_id)
    sent: list[Message] = []

    assert await _start(coordinator, sent) is True
    pending = coordinator.pending[GROUP_ID]
    assert coordinator.answer(GROUP_ID, OTHER, "路人", "胡桃") is True
    assert coordinator.answer(GROUP_ID, OTHER_B, "路人乙", "钟离") is True
    await asyncio.wait_for(pending.task, 1)

    lines = _result_lines(sent[-1])
    assert "在第 1 条线索时答对，好感度 +5，当前 105" in lines[OTHER]
    assert "猜错了，好感度 -2，当前 98" in lines[OTHER_B]
    assert "整局没答，好感度 -1，当前 99" in lines[INITIATOR]


async def test_busy_group_refuses_second_round(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path, timeout=60)
    sent: list[Message] = []
    assert await _start(coordinator, sent) is True
    assert await _start(coordinator, sent) is False
    await _stop(coordinator)


async def test_whoami_command_starts_round(tmp_path: Path, monkeypatch) -> None:
    coordinator = _coordinator(tmp_path, timeout=60)
    commands = register_commands(
        coordinator.service, Config(), whoami=coordinator
    )
    assert len(commands) == 27
    command = commands[24]
    assert [handler.call.__name__ for handler in command.handlers] == ["handle_whoami"]
    sent: list[Message] = []

    async def fake_send(message) -> None:
        sent.append(Message(message) if isinstance(message, str) else message)

    monkeypatch.setattr(command, "send", fake_send)
    await command.handlers[0].call(_event())

    assert GROUP_ID in coordinator.pending
    assert "线索 1/3" in sent[-1].extract_plain_text()
    await _stop(coordinator)


async def test_whoami_command_reports_busy(tmp_path: Path, monkeypatch) -> None:
    coordinator = _coordinator(tmp_path, timeout=60)
    sent: list[Message] = []
    assert await _start(coordinator, sent) is True
    commands = register_commands(
        coordinator.service, Config(), whoami=coordinator
    )
    command = commands[24]
    finished: list[str] = []

    async def fake_finish(message: Message | str = "", **kwargs) -> None:
        finished.append(str(message))

    monkeypatch.setattr(command, "finish", fake_finish)
    await command.handlers[0].call(_event())
    assert finished and "还有一场活动没结束" in finished[0]
    await _stop(coordinator)


async def test_whoami_probability_command(tmp_path: Path, monkeypatch) -> None:
    coordinator = _coordinator(tmp_path, timeout=60)
    commands = register_commands(
        coordinator.service, Config(), whoami=coordinator
    )
    command = commands[25]
    assert [handler.call.__name__ for handler in command.handlers] == [
        "handle_whoami_probability"
    ]
    assert "Superuser" in repr(command.permission)
    sent: list[str] = []

    async def fake_finish(message: Message | str = "", **kwargs) -> None:
        sent.append(str(message))

    monkeypatch.setattr(command, "finish", fake_finish)
    await command.handlers[0].call(Message(""))
    assert "当前我是谁概率：0.05%" in sent[0]
    await command.handlers[0].call(Message("2.5%"))
    assert "我是谁概率已调整为 2.5%" in sent[1]
    assert await coordinator.service.whoami_probability() == pytest.approx(0.025)


async def test_ambient_starts_whoami(tmp_path: Path, monkeypatch) -> None:
    kwargs = {
        "mantou_affection_find_char_chance": 0.0,
        "mantou_affection_bet_chance": 0.0,
        "mantou_affection_pk_chance": 0.0,
        "mantou_affection_group_quiz_chance": 0.0,
        "mantou_affection_teyvat_chance": 0.0,
        "mantou_affection_hsr_chance": 0.0,
        "mantou_affection_zzz_chance": 0.0,
        "mantou_affection_petit_planet_chance": 0.0,
        "mantou_affection_fill_blank_chance": 0.0,
        "mantou_affection_wheel_chance": 0.0,
        "mantou_affection_ambient_enabled": False,
        "mantou_affection_newbie_protection_below": 0,
        "mantou_affection_whoami_chance": 1.0,
    }
    config = Config(**kwargs)
    service = _service(tmp_path, config)
    events = EventCoordinator(service, config)
    coordinator = WhoAmICoordinator(
        service,
        config,
        _library(tmp_path),
        events=events,
        rng=PickFirst(),
    )
    coordinator.timeout = 60
    ambient, _answer = register_ambient(service, config, events, whoami=coordinator)
    sent: list[Message] = []

    async def fake_send(message) -> None:
        sent.append(Message(message) if isinstance(message, str) else message)

    monkeypatch.setattr(ambient, "send", fake_send)
    await ambient.handlers[0].call(_event())

    assert GROUP_ID in coordinator.pending
    assert "我是谁" in sent[-1].extract_plain_text()
    await _stop(coordinator)


def test_whoami_is_wired() -> None:
    import nonebot_plugin_mantou_affection as plugin

    assert plugin.event_coordinator.whoami is plugin.whoami_coordinator
    assert plugin.whoami_coordinator.total_entries >= 60
