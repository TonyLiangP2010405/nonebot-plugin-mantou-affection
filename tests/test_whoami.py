from __future__ import annotations

import asyncio
import json
import random
from base64 import b64decode
from contextlib import suppress
from io import BytesIO
from pathlib import Path
from time import time as now_timestamp

from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message
from nonebot.adapters.onebot.v11.event import Sender
from PIL import Image

from nonebot_plugin_mantou_affection.ambient import EventCoordinator, register_ambient
from nonebot_plugin_mantou_affection.commands import register_commands
from nonebot_plugin_mantou_affection.config import Config
from nonebot_plugin_mantou_affection.event_wheel import EVENT_WHEEL_WHOAMI
from nonebot_plugin_mantou_affection.models import Profile
from nonebot_plugin_mantou_affection.service import AffectionService
from nonebot_plugin_mantou_affection.storage import AffectionStore
from nonebot_plugin_mantou_affection.whoami import (
    BUNDLED_WHOAMI_PATH,
    WHOAMI_CHARACTER,
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
        "image": "card.png",
    },
    {
        "answer": "钟离",
        "game": "原神",
        "category": "character",
        "aliases": ["岩王帝君"],
        "image": "card.png",
    },
]


class PickFirst(random.Random):
    """总是抽第一道题，让用例不依赖随机结果。"""

    def __init__(self) -> None:
        super().__init__(0)

    def choice(self, seq):  # type: ignore[override]
        return seq[0]


class FixedSlotRng(random.Random):
    """让事件转盘固定转到指定格子。"""

    def __init__(self, slot: str):
        super().__init__(0)
        self.slot = slot

    def choice(self, seq):  # type: ignore[override]
        items = list(seq)
        return self.slot if self.slot in items else items[0]


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
    image = Image.new("RGBA", (32, 24), (230, 80, 60, 255))
    image.paste((35, 135, 155, 255), (10, 5, 30, 20))
    image.save(tmp_path / "card.png")
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
    library = WhoAmILibrary(BUNDLED_WHOAMI_PATH)
    assert len(entries) == len(library) > 0
    games = {entry["game"] for entry in entries}
    assert games == {"原神", "星穹铁道", "绝区零"}
    assert {entry["category"] for entry in entries} == {WHOAMI_CHARACTER}

    answers = [normalize_guess(entry["answer"]) for entry in entries]
    assert all(answers)
    aliases: dict[str, str] = {}
    for entry in entries:
        assert entry["answer"].strip()
        assert entry["image"]
        assert len(entry["crop"]) == 4
        assert "hints" not in entry
        for alias in [entry["answer"], *entry["aliases"]]:
            key = normalize_guess(alias)
            assert aliases.get(key, entry["answer"]) == entry["answer"]
            aliases[key] = entry["answer"]

    # 常用简称和外号要能认出来
    # 否则群友打简称会被当成闲聊
    expected = {
        "万叶": "枫原万叶",
        "枫原": "枫原万叶",
        "绫华": "神里绫华",
        "绫人": "神里绫人",
        "一斗": "荒泷一斗",
        "心海": "珊瑚宫心海",
        "雷神": "雷电将军",
        "雷电影": "雷电将军",
        "公子": "达达利亚",
        "散兵": "流浪者",
        "仆人": "阿蕾奇诺",
        "草神": "纳西妲",
        "小吉祥草王": "纳西妲",
        "水神": "芙宁娜",
        "帝君": "钟离",
        "海森": "艾尔海森",
        "那维": "那维莱特",
        "水龙王": "那维莱特",
        "克洛": "克洛琳德",
        "椰羊": "甘雨",
        "琴团长": "琴",
        "本·比格": "本",
        "福福": "橘福福",
        "调香师": "艾梅莉埃",
        "皇女": "菲谢尔",
    }
    for text, answer in expected.items():
        entry = library.resolve(text)
        assert entry is not None and entry.answer == answer, text


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


def test_library_flags_mistyped_guesses(tmp_path: Path) -> None:
    library = _library(tmp_path)
    entry = library.resolve("胡桃")
    assert library.looks_like_mistyped(entry, "我猜是胡逃吧") is True
    assert library.looks_like_mistyped(entry, "往生堂住") is True
    assert library.looks_like_mistyped(entry, "晚上好") is False
    assert library.looks_like_mistyped(entry, "胡") is False


def test_library_skips_bad_entries(tmp_path: Path) -> None:
    (tmp_path / "broken.png").write_bytes(b"not an image")
    valid = ENTRIES[0]
    entries = [
        {**valid, "answer": "旧文字题", "image": None, "hints": ["一", "二", "三"]},
        {**valid, "answer": "图找不到", "image": "missing.png"},
        {**valid, "answer": "图损坏", "image": "broken.png"},
        {**valid, "answer": "裁错了", "crop": [0, 0, 100, 100]},
        {**valid, "answer": "不存在的类别", "category": "npc"},
        valid,
        valid,
    ]
    library = _library(tmp_path, entries)
    assert [entry.answer for entry in library.entries] == ["胡桃", "胡桃"]


def test_library_allows_several_pictures_per_character(tmp_path: Path) -> None:
    entries = [ENTRIES[0], {**ENTRIES[0]}, {**ENTRIES[0], "aliases": []}]
    library = _library(tmp_path, entries)
    assert len(library.entries) == 3
    assert library.resolve("胡桃").answer == "胡桃"


async def test_start_sends_one_cropped_picture_before_settlement(tmp_path: Path) -> None:
    entries = [{**ENTRIES[0], "crop": [10, 5, 30, 20]}]
    coordinator = _coordinator(tmp_path, timeout=0.06, entries=entries)
    sent: list[Message] = []
    assert await _start(coordinator, sent) is True
    pending = coordinator.pending[GROUP_ID]

    body = sent[0].extract_plain_text()
    assert "🕵️ 我是谁？" in body
    assert "胡桃" not in body
    assert "线索" not in body
    assert "每人只能猜一次" in body
    assert "打错一个字也算猜错" in body
    assert "答对 +5" in body
    assert [segment.type for segment in sent[0]] == ["at", "text", "image"]
    encoded = sent[0][-1].data["file"]
    assert encoded.startswith("base64://")
    with Image.open(BytesIO(b64decode(encoded.removeprefix("base64://")))) as image:
        assert image.size == (320, 240)
        assert image.mode == "L"
        assert image.getextrema() == (107, 107)

    await asyncio.wait_for(pending.task, 1)
    assert len(sent) == 2
    assert "答案：胡桃（原神）" in sent[-1].extract_plain_text()


async def test_scene_round_uses_place_wording(tmp_path: Path) -> None:
    entries = [
        {
            "answer": "蒙德城",
            "game": "原神",
            "category": "scene",
            "aliases": ["蒙德"],
            "image": "card.png",
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
    assert coordinator.answer(GROUP_ID, OTHER, "路人", "钟离") is False

    assert coordinator.answer(GROUP_ID, OTHER_B, "路人乙", "钟离") is True
    assert pending.answers[OTHER_B].correct is False

    pending.accepting = False
    assert coordinator.answer(GROUP_ID, "99005", "路人丙", "胡桃") is False
    await _stop(coordinator)


async def test_typo_guess_counts_as_wrong(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path, timeout=60)
    sent: list[Message] = []
    assert await _start(coordinator, sent) is True
    pending = coordinator.pending[GROUP_ID]

    assert coordinator.answer(GROUP_ID, OTHER, "路人", "胡逃") is True
    assert pending.answers[OTHER].correct is False
    assert coordinator.answer(GROUP_ID, OTHER, "路人", "胡桃") is False
    assert coordinator.answer(GROUP_ID, OTHER_B, "路人乙", "晚上好") is False
    await _stop(coordinator)


async def test_settle_typo_round_scores(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path, timeout=0.06)
    await _give_affection(coordinator.service, 100, OTHER)
    sent: list[Message] = []
    assert await _start(coordinator, sent) is True
    pending = coordinator.pending[GROUP_ID]
    assert coordinator.answer(GROUP_ID, OTHER, "路人", "我猜是胡逃吧") is True
    await asyncio.wait_for(pending.task, 1)

    lines = _result_lines(sent[-1])
    assert "猜错了，好感度 -5，当前 95" in lines[OTHER]


async def test_settle_picture_round_scores(tmp_path: Path) -> None:
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
    assert "答对了，好感度 +5，当前 105" in lines[OTHER]
    assert "猜错了，好感度 -5，当前 95" in lines[OTHER_B]
    assert "整局没答，好感度 -8，当前 92" in lines[INITIATOR]


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
    assert len(commands) == 17
    command = commands[15]
    assert [handler.call.__name__ for handler in command.handlers] == ["handle_whoami"]
    sent: list[Message] = []

    async def fake_send(message) -> None:
        sent.append(Message(message) if isinstance(message, str) else message)

    monkeypatch.setattr(command, "send", fake_send)
    await command.handlers[0].call(_event())

    assert GROUP_ID in coordinator.pending
    assert len(sent[-1]["image"]) == 1
    await _stop(coordinator)


async def test_whoami_command_reports_busy(tmp_path: Path, monkeypatch) -> None:
    coordinator = _coordinator(tmp_path, timeout=60)
    sent: list[Message] = []
    assert await _start(coordinator, sent) is True
    commands = register_commands(
        coordinator.service, Config(), whoami=coordinator
    )
    command = commands[15]
    finished: list[str] = []

    async def fake_finish(message: Message | str = "", **kwargs) -> None:
        finished.append(str(message))

    monkeypatch.setattr(command, "finish", fake_finish)
    await command.handlers[0].call(_event())
    assert finished and "还有一场活动没结束" in finished[0]
    await _stop(coordinator)


async def test_event_wheel_lands_on_whoami(tmp_path: Path, monkeypatch) -> None:
    config = Config(
        mantou_affection_event_wheel_chance=1.0,
        mantou_affection_event_wheel_timeout=0.01,
        mantou_affection_wheel_chance=0.0,
        mantou_affection_ambient_enabled=False,
        mantou_affection_newbie_protection_below=0,
    )
    service = _service(tmp_path, config)
    events = EventCoordinator(service, config, rng=FixedSlotRng(EVENT_WHEEL_WHOAMI))
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
    assert events.event_wheel is not None
    await events.event_wheel.wait_settled()

    assert GROUP_ID in coordinator.pending
    assert len(sent) == 3
    assert "触发了事件转盘" in sent[0].extract_plain_text()
    assert "事件转盘转到了「我是谁」" in sent[1].extract_plain_text()
    assert "我是谁" in sent[2].extract_plain_text()
    assert len(sent[2]["image"]) == 1
    await _stop(coordinator)


def test_whoami_is_wired() -> None:
    import nonebot_plugin_mantou_affection as plugin

    assert plugin.event_coordinator.whoami is plugin.whoami_coordinator
    assert plugin.whoami_coordinator.total_entries == len(WhoAmILibrary(BUNDLED_WHOAMI_PATH))
