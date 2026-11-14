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
from nonebot_plugin_mantou_affection.hsr_quiz import (
    BUNDLED_HSR_QUIZ_PATH,
    HSR_QUIZ_SPEC,
    HsrQuizCoordinator,
)
from nonebot_plugin_mantou_affection.models import Profile
from nonebot_plugin_mantou_affection.pk import PkCoordinator, PkQuestionLibrary
from nonebot_plugin_mantou_affection.service import AffectionService
from nonebot_plugin_mantou_affection.storage import AffectionStore
from nonebot_plugin_mantou_affection.zzz_quiz import (
    BUNDLED_ZZZ_QUIZ_PATH,
    ZZZ_QUIZ_SPEC,
    ZzzQuizCoordinator,
)

GROUP_ID = "98001"
INITIATOR = "98002"
OTHER = "98003"
OTHER_B = "98004"

QUESTIONS = [
    {"text": "星穹列车的领航员是？", "options": ["姬子", "瓦尔特", "三月七"], "answer": 0},
    {"text": "贝洛伯格信奉的星神是？", "options": ["纳努克", "克里珀", "岚"], "answer": 1},
    {"text": "仙舟联盟中与「巡猎」相关的星神是？", "options": ["岚", "药师", "IX"], "answer": 0},
]

CASES = [
    pytest.param(
        "hsr",
        HsrQuizCoordinator,
        HSR_QUIZ_SPEC,
        BUNDLED_HSR_QUIZ_PATH,
        17,
        "hsr_quiz_probability",
        "hsr_quiz",
        "星穹铁道问答",
        id="hsr",
    ),
    pytest.param(
        "zzz",
        ZzzQuizCoordinator,
        ZZZ_QUIZ_SPEC,
        BUNDLED_ZZZ_QUIZ_PATH,
        18,
        "zzz_quiz_probability",
        "zzz_quiz",
        "绝区零问答",
        id="zzz",
    ),
]


class FixedRng(random.Random):
    def __init__(self, value: float):
        super().__init__(0)
        self.value = value

    def random(self) -> float:
        return self.value


def _event(
    text: str = "晚上好",
    user_id: int = 98002,
    group_id: int = 98001,
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


def _library(tmp_path: Path, questions: list[dict] | None = None) -> PkQuestionLibrary:
    path = tmp_path / "quiz_questions.json"
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
    cls,
    tmp_path: Path,
    config: Config | None = None,
    *,
    timeout: float = 0.02,
):
    config = config or Config(mantou_affection_newbie_protection_below=0)
    coordinator = cls(_service(tmp_path, config), config, _library(tmp_path), rng=random.Random(0))
    coordinator.question_timeout = timeout
    return coordinator


async def _give_affection(service: AffectionService, score: int, user_id: str) -> None:
    def bump(profile: Profile) -> None:
        profile.affection = score

    await service.store.update_profile(GROUP_ID, user_id, "玩家", bump)


async def _start(coordinator, sent: list[Message]) -> bool:
    async def fake_send(message) -> None:
        sent.append(Message(message) if isinstance(message, str) else message)

    return await coordinator.start(
        group_id=GROUP_ID, user_id=INITIATOR, nickname="玩家", send=fake_send
    )


async def _submit(coordinator, user_id: str, *, correct: bool) -> bool:
    pending = coordinator.pending[GROUP_ID]
    index = pending.question.answer if correct else (pending.question.answer + 1) % 3
    return await coordinator.answer(GROUP_ID, user_id, "路人", str(index + 1))


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


@pytest.mark.parametrize("case,cls,spec,path,command_index,prob_method,attach_name,label", CASES)
def test_quiz_bank_is_valid(
    case: str, cls, spec, path: Path, command_index, prob_method, attach_name, label
) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    questions = payload["questions"]
    assert payload["meta"]["count"] == 5000
    assert len(questions) == 5000
    assert sum(payload["meta"]["categories"].values()) == 5000
    assert len({question["text"] for question in questions}) == 5000
    for question in questions:
        assert question["text"].strip()
        assert len(question["options"]) == 3
        assert len(set(question["options"])) == 3
        assert all(option.strip() for option in question["options"])
        assert question["answer"] in (0, 1, 2)
        assert question["options"][question["answer"]] not in question["text"]
        assert "test" not in question["text"].lower()
        combined = question["text"] + "".join(question["options"])
        assert "{" not in combined and "}" not in combined
        assert "<color" not in combined and "**" not in combined


@pytest.mark.parametrize("case,cls,spec,path,command_index,prob_method,attach_name,label", CASES)
async def test_quiz_start_settle_and_timeout(
    case, cls, spec, path, command_index, prob_method, attach_name, label, tmp_path: Path
) -> None:
    coordinator = _coordinator(cls, tmp_path, timeout=60.0)
    for user_id in (INITIATOR, OTHER, OTHER_B):
        await _give_affection(coordinator.service, 100, user_id)
    sent: list[Message] = []

    assert await _start(coordinator, sent) is True
    pending = coordinator.pending[GROUP_ID]
    body = sent[0].extract_plain_text()
    assert spec.prefix in body
    assert "请在 60 秒内作答" in body
    assert "答对 +3" in body
    assert "答错 -5" in body
    assert "整局没答 -2" in body
    coordinator.question_timeout = 0.02

    assert await _submit(coordinator, OTHER, correct=True) is True
    assert await _submit(coordinator, OTHER_B, correct=False) is True
    await asyncio.wait_for(pending.task, 1)

    lines = _result_lines(sent[-1])
    assert "答对了，好感度 +3，当前 103" in lines[OTHER]
    assert "答错了，好感度 -5，当前 95" in lines[OTHER_B]
    assert "整局未答，好感度 -2，当前 98" in lines[INITIATOR]


@pytest.mark.parametrize("case,cls,spec,path,command_index,prob_method,attach_name,label", CASES)
def test_quiz_probability_command(
    case, cls, spec, path, command_index, prob_method, attach_name, label, tmp_path: Path
) -> None:
    service = _service(tmp_path)
    commands = register_commands(service, Config(), PkCoordinator(service, Config(), None))
    assert len(commands) == 19
    command = commands[command_index]
    assert [handler.call.__name__ for handler in command.handlers] == [
        f"handle_{attach_name}_probability"
    ]


@pytest.mark.parametrize("case,cls,spec,path,command_index,prob_method,attach_name,label", CASES)
async def test_quiz_probability_command_reports_and_sets(
    case, cls, spec, path, command_index, prob_method, attach_name, label, tmp_path, monkeypatch
) -> None:
    service = _service(tmp_path)
    commands = register_commands(service, Config(), PkCoordinator(service, Config(), None))
    command = commands[command_index]
    sent: list[str] = []

    async def fake_finish(message: Message | str = "", **kwargs) -> None:
        sent.append(str(message))

    monkeypatch.setattr(command, "finish", fake_finish)
    await command.handlers[0].call(Message(""))
    assert f"当前{label}概率：0.05%" in sent[0]
    await command.handlers[0].call(Message("2.5%"))
    assert f"{label}概率已调整为 2.5%" in sent[1]
    assert await getattr(service, prob_method)() == pytest.approx(0.025)


@pytest.mark.parametrize("case,cls,spec,path,command_index,prob_method,attach_name,label", CASES)
async def test_ambient_starts_quiz(
    case, cls, spec, path, command_index, prob_method, attach_name, label, tmp_path, monkeypatch
) -> None:
    kwargs = {
        "mantou_affection_find_char_chance": 0.0,
        "mantou_affection_bet_chance": 0.0,
        "mantou_affection_pk_chance": 0.0,
        "mantou_affection_group_quiz_chance": 0.0,
        "mantou_affection_teyvat_chance": 0.0,
        "mantou_affection_hsr_chance": 0.0,
        "mantou_affection_zzz_chance": 0.0,
        "mantou_affection_fill_blank_chance": 0.0,
        "mantou_affection_wheel_chance": 0.0,
        "mantou_affection_ambient_enabled": False,
        "mantou_affection_newbie_protection_below": 0,
    }
    kwargs[f"mantou_affection_{case}_chance"] = 1.0
    config = Config(**kwargs)
    service = _service(tmp_path, config)
    events = EventCoordinator(service, config)
    coordinator = cls(service, config, _library(tmp_path), events=events, rng=FixedRng(0.99))
    ambient, _answer = register_ambient(service, config, events, **{attach_name: coordinator})
    sent: list[Message] = []

    async def fake_send(message) -> None:
        sent.append(Message(message) if isinstance(message, str) else message)

    monkeypatch.setattr(ambient, "send", fake_send)
    await ambient.handlers[0].call(_event())

    assert GROUP_ID in coordinator.pending
    assert spec.prefix in sent[-1].extract_plain_text()
    coordinator.pending[GROUP_ID].task.cancel()
    with suppress(asyncio.CancelledError):
        await coordinator.pending[GROUP_ID].task


def test_new_quizzes_are_wired() -> None:
    import nonebot_plugin_mantou_affection as plugin

    assert plugin.event_coordinator.hsr_quiz is plugin.hsr_quiz_coordinator
    assert plugin.event_coordinator.zzz_quiz is plugin.zzz_quiz_coordinator
    assert plugin.hsr_quiz_coordinator.total_questions == 5000
    assert plugin.zzz_quiz_coordinator.total_questions == 5000
