from __future__ import annotations

import asyncio
import json
import random
from contextlib import suppress
from pathlib import Path
from time import time as now_timestamp

from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message
from nonebot.adapters.onebot.v11.event import Sender

from nonebot_plugin_mantou_affection.ambient import EventCoordinator, register_ambient
from nonebot_plugin_mantou_affection.commands import register_commands
from nonebot_plugin_mantou_affection.config import Config
from nonebot_plugin_mantou_affection.models import Profile
from nonebot_plugin_mantou_affection.pk import PkCoordinator, PkQuestionLibrary
from nonebot_plugin_mantou_affection.service import AffectionService
from nonebot_plugin_mantou_affection.storage import AffectionStore
from nonebot_plugin_mantou_affection.teyvat_quiz import (
    BUNDLED_TEYVAT_QUIZ_PATH,
    TEYVAT_QUIZ_SPEC,
    TeyvatQuizCoordinator,
)

GROUP_ID = "97001"
INITIATOR = "97002"
OTHER = "97003"
OTHER_B = "97004"

QUESTIONS = [
    {"text": "提瓦特的岩神是？", "options": ["摩拉克斯", "巴巴托斯", "巴尔泽布"], "answer": 0},
    {"text": "蒙德的风魔龙原名是？", "options": ["杜林", "特瓦林", "乌尔法"], "answer": 1},
    {
        "text": "坎瑞亚灾变大约发生在多少年前？",
        "options": ["100 年前", "1000 年前", "500 年前"],
        "answer": 2,
    },
]


class FixedRng(random.Random):
    """固定 random() 返回值，用来精确控制概率判定。"""

    def __init__(self, value: float):
        super().__init__(0)
        self.value = value

    def random(self) -> float:
        return self.value


def _event(
    text: str = "晚上好",
    user_id: int = 97002,
    group_id: int = 97001,
    nickname: str = "旅行者",
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


def _library(tmp_path: Path, questions: list[dict] | None = None) -> PkQuestionLibrary:
    path = tmp_path / "teyvat_quiz_questions.json"
    payload = questions if questions is not None else QUESTIONS
    path.write_text(json.dumps({"questions": payload}, ensure_ascii=False), encoding="utf-8")
    return PkQuestionLibrary(path)


def _service(tmp_path: Path, config: Config | None = None) -> AffectionService:
    return AffectionService(
        AffectionStore(tmp_path / "affection.json"),
        config or Config(mantou_affection_newbie_protection_below=0),
        rng=random.Random(0),
    )


def _coordinator(
    tmp_path: Path,
    config: Config | None = None,
    questions: list[dict] | None = None,
    events: EventCoordinator | None = None,
    timeout: float = 0.02,
) -> TeyvatQuizCoordinator:
    config = config or Config(mantou_affection_newbie_protection_below=0)
    coordinator = TeyvatQuizCoordinator(
        _service(tmp_path, config),
        config,
        _library(tmp_path, questions),
        events=events,
        rng=random.Random(0),
    )
    coordinator.question_timeout = timeout
    return coordinator


async def _give_affection(
    service: AffectionService, score: int, user_id: str = INITIATOR
) -> None:
    def bump(profile: Profile) -> None:
        profile.affection = score

    await service.store.update_profile(GROUP_ID, user_id, "旅行者", bump)


async def _start(coordinator: TeyvatQuizCoordinator, sent: list[Message]) -> bool:
    async def fake_send(message) -> None:
        sent.append(Message(message) if isinstance(message, str) else message)

    return await coordinator.start(
        group_id=GROUP_ID, user_id=INITIATOR, nickname="旅行者", send=fake_send
    )


async def _submit(
    coordinator: TeyvatQuizCoordinator,
    user_id: str,
    *,
    correct: bool,
    nickname: str = "路人",
) -> bool:
    pending = coordinator.pending[GROUP_ID]
    index = pending.question.answer if correct else (pending.question.answer + 1) % 3
    return await coordinator.answer(GROUP_ID, user_id, nickname, str(index + 1))


def _result_lines(message: Message) -> dict[str, str]:
    lines: dict[str, str] = {}
    owner: str | None = None
    buffer: list[str] = []

    def flush() -> None:
        nonlocal owner, buffer
        if owner is not None:
            text_line = "".join(buffer).strip()
            if text_line:
                lines[owner] = text_line
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


# ------------------------------------------------------------------ 题库


def test_teyvat_quiz_is_wired_into_plugin() -> None:
    import nonebot_plugin_mantou_affection as plugin

    assert plugin.event_coordinator.teyvat_quiz is plugin.teyvat_quiz_coordinator
    assert plugin.teyvat_quiz_coordinator.total_questions == 5000


def test_bundled_teyvat_bank_is_valid() -> None:
    payload = json.loads(BUNDLED_TEYVAT_QUIZ_PATH.read_text(encoding="utf-8"))
    questions = payload["questions"]

    assert payload["meta"]["count"] == 5000
    assert len(questions) == 5000
    assert sum(payload["meta"]["categories"].values()) == 5000
    assert payload["meta"]["categories"]["voice"] == 1000
    assert len({question["text"] for question in questions}) == 5000
    for question in questions:
        assert question["text"].strip()
        assert len(question["options"]) == 3
        assert len(set(question["options"])) == 3
        assert all(option.strip() for option in question["options"])
        assert question["answer"] in (0, 1, 2)
        # 正确答案不能直接出现在题干里, 也不应混入数据源的测试条目。
        assert question["options"][question["answer"]] not in question["text"]
        assert "test" not in question["text"].lower()
        combined = question["text"] + "".join(question["options"])
        assert "{" not in combined and "}" not in combined
        assert "<color" not in combined and "**" not in combined


# ------------------------------------------------------------ 协调器与结算


async def test_teyvat_quiz_start_and_rewards(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path, timeout=60.0)
    await _give_affection(coordinator.service, 100, INITIATOR)
    await _give_affection(coordinator.service, 100, OTHER)
    await _give_affection(coordinator.service, 100, OTHER_B)
    sent: list[Message] = []

    assert await _start(coordinator, sent) is True
    pending = coordinator.pending[GROUP_ID]
    body = sent[0].extract_plain_text()
    assert TEYVAT_QUIZ_SPEC.prefix in body
    assert "馒头翻开了提瓦特的史书" in body
    assert "请在 60 秒内作答" in body
    assert "答对 +3" in body
    assert "答错 -5" in body
    assert "整局没答 -2" in body
    assert pending.question.text in body
    coordinator.question_timeout = 0.02

    assert await _submit(coordinator, OTHER, correct=True) is True
    assert await _submit(coordinator, OTHER_B, correct=False) is True
    await asyncio.wait_for(pending.task, 1)

    lines = _result_lines(sent[-1])
    assert "答对了，好感度 +3，当前 103" in lines[OTHER]
    assert "答错了，好感度 -5，当前 95" in lines[OTHER_B]
    assert "整局未答，好感度 -2，当前 98" in lines[INITIATOR]


async def test_teyvat_quiz_timeout_uses_config_and_allows_initiator(
    tmp_path: Path,
) -> None:
    config = Config(
        mantou_affection_newbie_protection_below=0,
        mantou_affection_teyvat_timeout=15,
    )
    coordinator = _coordinator(tmp_path, config=config, timeout=15.0)
    assert coordinator.question_timeout == 15
    coordinator.question_timeout = 0.02

    await _give_affection(coordinator.service, 50, INITIATOR)
    sent: list[Message] = []
    assert await _start(coordinator, sent) is True
    pending = coordinator.pending[GROUP_ID]
    assert await _submit(coordinator, INITIATOR, correct=True) is True
    await asyncio.wait_for(pending.task, 1)
    assert (await coordinator.service.profile(GROUP_ID, INITIATOR)).affection == 53


async def test_teyvat_quiz_busy_with_other_activity(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    sent: list[Message] = []
    assert await _start(coordinator, sent) is True
    assert await _start(coordinator, sent) is False
    pending = coordinator.pending[GROUP_ID]
    pending.task.cancel()
    with suppress(asyncio.CancelledError):
        await pending.task


# ------------------------------------------------------------ 命令与触发


def test_teyvat_probability_command(tmp_path: Path) -> None:
    service = _service(tmp_path)
    commands = register_commands(service, Config(), PkCoordinator(service, Config(), None))
    assert len(commands) == 19
    command = commands[16]
    assert [handler.call.__name__ for handler in command.handlers] == [
        "handle_teyvat_quiz_probability"
    ]


async def test_teyvat_probability_command_reports_and_sets(
    tmp_path: Path, monkeypatch
) -> None:
    service = _service(tmp_path)
    commands = register_commands(service, Config(), PkCoordinator(service, Config(), None))
    command = commands[16]
    sent: list[str] = []

    async def fake_finish(message: Message | str = "", **kwargs) -> None:
        sent.append(str(message))

    monkeypatch.setattr(command, "finish", fake_finish)

    await command.handlers[0].call(Message(""))
    assert "当前提瓦特问答概率：0.05%（配置默认 0.05%）" in sent[0]

    await command.handlers[0].call(Message("2.5%"))
    assert "提瓦特问答概率已调整为 2.5%" in sent[1]
    assert await service.teyvat_quiz_probability() == 0.025


async def test_ambient_starts_teyvat_quiz(tmp_path: Path, monkeypatch) -> None:
    config = Config(
        mantou_affection_ambient_enabled=False,
        mantou_affection_find_char_chance=0.0,
        mantou_affection_bet_chance=0.0,
        mantou_affection_pk_chance=0.0,
        mantou_affection_group_quiz_chance=0.0,
        mantou_affection_teyvat_chance=1.0,
        mantou_affection_fill_blank_chance=0.0,
        mantou_affection_wheel_chance=0.0,
        mantou_affection_newbie_protection_below=0,
    )
    service = _service(tmp_path, config)
    events = EventCoordinator(service, config)
    teyvat = TeyvatQuizCoordinator(
        service, config, _library(tmp_path), events=events, rng=FixedRng(0.99)
    )
    ambient, _answer = register_ambient(
        service, config, events, teyvat_quiz=teyvat
    )
    sent: list[Message] = []

    async def fake_send(message) -> None:
        sent.append(Message(message) if isinstance(message, str) else message)

    monkeypatch.setattr(ambient, "send", fake_send)

    await ambient.handlers[0].call(_event())

    assert GROUP_ID in teyvat.pending
    assert TEYVAT_QUIZ_SPEC.prefix in sent[-1].extract_plain_text()
    teyvat.pending[GROUP_ID].task.cancel()
