import asyncio
import json
import random
from contextlib import suppress
from pathlib import Path
from time import time as now_timestamp
from types import SimpleNamespace

from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message
from nonebot.adapters.onebot.v11.event import Sender

from nonebot_plugin_mantou_affection.ambient import EventCoordinator, register_ambient
from nonebot_plugin_mantou_affection.bet import BetCoordinator
from nonebot_plugin_mantou_affection.commands import register_commands
from nonebot_plugin_mantou_affection.config import Config
from nonebot_plugin_mantou_affection.findchar import FindCharCoordinator
from nonebot_plugin_mantou_affection.group_quiz import (
    BUNDLED_GROUP_QUIZ_PATH,
    GROUP_QUIZ_PREFIX,
    GroupQuizCoordinator,
)
from nonebot_plugin_mantou_affection.models import Profile
from nonebot_plugin_mantou_affection.pk import PkCoordinator, PkQuestionLibrary
from nonebot_plugin_mantou_affection.service import AffectionService
from nonebot_plugin_mantou_affection.storage import AffectionStore
from nonebot_plugin_mantou_affection.wheel import WheelCoordinator

GROUP_ID = "95001"
INITIATOR = "95002"
OTHER = "95003"
OTHER_B = "95004"

QUESTIONS = [
    {"text": f"测试题 {index}", "options": ["甲", "乙", "丙"], "answer": index % 3}
    for index in range(1, 13)
]


class FixedRng(random.Random):
    """固定 random() 返回值，用来精确控制触发判定。"""

    def __init__(self, value: float):
        super().__init__(0)
        self.value = value

    def random(self) -> float:
        return self.value


def _event(
    text: str = "晚上好", user_id: int = 95002, group_id: int = 95001, nickname: str = "群友"
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
    path = tmp_path / "group_quiz_questions.json"
    payload = questions if questions is not None else QUESTIONS
    path.write_text(json.dumps({"questions": payload}, ensure_ascii=False), encoding="utf-8")
    return PkQuestionLibrary(path)


def _service(tmp_path: Path, config: Config | None = None) -> AffectionService:
    return AffectionService(
        AffectionStore(tmp_path / "affection.json"), config or Config(), rng=random.Random(0)
    )


def _coordinator(
    tmp_path: Path,
    config: Config | None = None,
    questions: list[dict] | None = None,
    events: EventCoordinator | None = None,
    rng: random.Random | None = None,
) -> GroupQuizCoordinator:
    config = config or Config()
    service = _service(tmp_path, config)
    coordinator = GroupQuizCoordinator(
        service,
        config,
        _library(tmp_path, questions),
        events=events,
        rng=rng or random.Random(0),
    )
    coordinator.question_timeout = 0.02
    return coordinator


async def _give_affection(
    service: AffectionService, score: int, user_id: str, group_id: str = GROUP_ID
) -> None:
    def bump(profile: Profile) -> None:
        profile.affection = score

    await service.store.update_profile(group_id, user_id, "群友", bump)


async def _start(
    coordinator: GroupQuizCoordinator,
    sent: list[Message],
    *,
    user_id: str = INITIATOR,
) -> bool:
    async def fake_send(message) -> None:
        sent.append(Message(message) if isinstance(message, str) else message)

    return await coordinator.start(
        group_id=GROUP_ID, user_id=user_id, nickname="群友", send=fake_send
    )


async def _submit(
    coordinator: GroupQuizCoordinator, user_id: str, *, correct: bool, nickname: str = "路人"
) -> bool:
    pending = coordinator.pending[GROUP_ID]
    answer = pending.question.answer
    index = answer if correct else (answer + 1) % 3
    return await coordinator.answer(GROUP_ID, user_id, nickname, str(index + 1))


def _capture() -> list[Message]:
    return []


def _at_ids(message: Message) -> list[str]:
    return [str(segment.data["qq"]) for segment in message if segment.type == "at"]


def _result_lines(message: Message) -> dict[str, str]:
    """按行首 @ 到的人收集结算行。"""

    lines: dict[str, str] = {}
    owner: str | None = None
    buffer: list[str] = []

    def flush() -> None:
        nonlocal owner, buffer
        if owner is not None:
            text_line = "".join(buffer).strip()
            if text_line:
                if owner in lines:
                    lines[owner] = f"{lines[owner]} {text_line}".strip()
                else:
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


async def _cancel_task(
    coordinator: WheelCoordinator | PkCoordinator | GroupQuizCoordinator,
    group_id: str = GROUP_ID,
) -> None:
    pending = coordinator.pending.get(group_id)
    if pending is None or pending.task is None:
        return
    await asyncio.sleep(0)
    pending.task.cancel()
    with suppress(asyncio.CancelledError):
        await pending.task


# ------------------------------------------------------------ 题库加载与概率


def test_bundled_group_quiz_library_is_valid() -> None:
    library = PkQuestionLibrary(BUNDLED_GROUP_QUIZ_PATH)
    raw = json.loads(BUNDLED_GROUP_QUIZ_PATH.read_text(encoding="utf-8"))["questions"]

    assert library.questions
    assert len(library.questions) == len(raw) == 10000
    texts = [question.text for question in library.questions]
    assert len(set(texts)) == len(texts)
    for question in library.questions:
        assert question.text
        assert len(question.options) == 3
        assert all(option for option in question.options)
        assert 0 <= question.answer <= 2


def test_group_quiz_library_skips_invalid_questions(tmp_path: Path) -> None:
    library = _library(
        tmp_path,
        [
            QUESTIONS[0],
            "不是对象",
            {"text": "", "options": ["甲", "乙", "丙"], "answer": 0},
            {"text": "少选项", "options": ["甲", "乙"], "answer": 0},
            {"text": "空选项", "options": ["甲", " ", "丙"], "answer": 0},
            {"text": "答案越界", "options": ["甲", "乙", "丙"], "answer": 3},
            {"text": "答案不是整数", "options": ["甲", "乙", "丙"], "answer": "0"},
            {"text": "布尔答案", "options": ["甲", "乙", "丙"], "answer": True},
            {"text": "缺答案", "options": ["甲", "乙", "丙"]},
        ],
    )

    assert [question.text for question in library.questions] == ["测试题 1"]


def test_group_quiz_defaults() -> None:
    config = Config()
    assert config.mantou_affection_group_quiz_chance == 0.0005
    assert config.mantou_affection_group_quiz_timeout == 60


def test_group_quiz_timeout_comes_from_config(tmp_path: Path) -> None:
    config = Config(mantou_affection_group_quiz_timeout=15)
    coordinator = GroupQuizCoordinator(_service(tmp_path, config), config)

    assert coordinator.question_timeout == 15


async def test_group_quiz_concurrent_starts_only_launch_one_question(
    tmp_path: Path,
) -> None:
    coordinator = _coordinator(tmp_path)
    coordinator.question_timeout = 0.05
    sent = _capture()

    results = await asyncio.gather(
        _start(coordinator, sent),
        _start(coordinator, sent),
    )

    assert results.count(True) == 1
    assert results.count(False) == 1
    assert len(sent) == 1
    pending = coordinator.pending[GROUP_ID]
    assert await _submit(coordinator, INITIATOR, correct=True) is True
    await asyncio.wait_for(pending.task, 1)
    assert (await coordinator.service.profile(GROUP_ID, INITIATOR)).affection == 2


async def test_group_quiz_triggered_uses_configured_probability(tmp_path: Path) -> None:
    service = _service(tmp_path)

    service.rng = FixedRng(0.00049)
    assert await service.group_quiz_triggered() is True
    service.rng = FixedRng(0.0005)
    assert await service.group_quiz_triggered() is False


async def test_group_quiz_probability_runtime_override_and_persistence(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    assert await service.group_quiz_probability() == 0.0005
    assert await service.group_quiz_probability_override() is None

    await service.set_group_quiz_probability(0.25)
    assert await service.group_quiz_probability() == 0.25
    assert await service.group_quiz_probability_override() == 0.25

    reopened = _service(tmp_path)
    assert await reopened.group_quiz_probability() == 0.25
    assert await reopened.group_quiz_probability_override() == 0.25


# ------------------------------------------------------------ 开局与作答


async def test_group_quiz_start_asks_everyone(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    coordinator.question_timeout = 60
    sent = _capture()

    handled = await _start(coordinator, sent)

    assert handled is True
    pending = coordinator.pending[GROUP_ID]
    assert pending.target[0] == INITIATOR
    assert len(sent) == 1
    prompt = sent[0]
    assert _at_ids(prompt) == [INITIATOR]
    body = prompt.extract_plain_text()
    assert "馒头翻了翻聊天记录，考考你们……" in body
    assert pending.question.text in body
    for number, option in enumerate(pending.question.options, start=1):
        assert f"{number}. {option}" in body
    assert "大家都可以回答" in body
    assert "直接发送 1、2、3 即可（答题不用@）" in body
    assert "60 秒" in body
    assert "答对 +2" in body
    assert "答错 -3" in body
    assert "整局没答 -2" in body
    assert await coordinator.service.store.used_questions() == []
    await _cancel_task(coordinator)


async def test_group_quiz_start_returns_false_with_empty_library(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path, questions=[])
    sent = _capture()

    handled = await _start(coordinator, sent)

    assert handled is False
    assert coordinator.pending == {}
    assert sent == []


async def test_group_quiz_start_yields_when_group_is_busy(tmp_path: Path) -> None:
    service = _service(tmp_path, Config())
    events = EventCoordinator(service, Config(), None, rng=random.Random(0))
    coordinator = _coordinator(tmp_path, events=events)
    events.attach_group_quiz(coordinator)
    events.pending[GROUP_ID] = object()

    handled = await _start(coordinator, _capture())

    assert handled is False
    assert GROUP_ID not in coordinator.pending
    events.discard(GROUP_ID)


async def test_group_quiz_scores_every_answerer_independently(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    await _give_affection(coordinator.service, 10, INITIATOR)
    await _give_affection(coordinator.service, 20, OTHER)
    await _give_affection(coordinator.service, 30, OTHER_B)
    sent = _capture()
    assert await _start(coordinator, sent) is True
    pending = coordinator.pending[GROUP_ID]

    assert await _submit(coordinator, INITIATOR, correct=True) is True
    assert await _submit(coordinator, OTHER, correct=False) is True
    assert await _submit(coordinator, OTHER_B, correct=True) is True
    await asyncio.wait_for(pending.task, 1)

    assert (await coordinator.service.profile(GROUP_ID, INITIATOR)).affection == 12
    assert (await coordinator.service.profile(GROUP_ID, OTHER)).affection == 17
    assert (await coordinator.service.profile(GROUP_ID, OTHER_B)).affection == 32
    results = _result_lines(sent[-1])
    assert "答对了，好感度 +2，当前 12" in results[INITIATOR]
    assert "答错了，好感度 -3，当前 17" in results[OTHER]
    assert "答对了，好感度 +2，当前 32" in results[OTHER_B]
    correct = pending.question
    assert f"答案：{correct.answer + 1}. {correct.options[correct.answer]}" in str(sent[-1])
    assert coordinator.pending == {}


async def test_group_quiz_target_timeout_penalty_and_bystander_is_optional(
    tmp_path: Path,
) -> None:
    coordinator = _coordinator(tmp_path)
    for user_id in (INITIATOR, OTHER, OTHER_B):
        await _give_affection(coordinator.service, 20, user_id)
    sent = _capture()
    assert await _start(coordinator, sent) is True
    pending = coordinator.pending[GROUP_ID]

    assert await _submit(coordinator, OTHER, correct=True) is True
    await asyncio.wait_for(pending.task, 1)

    assert (await coordinator.service.profile(GROUP_ID, INITIATOR)).affection == 18
    assert (await coordinator.service.profile(GROUP_ID, OTHER)).affection == 22
    assert (await coordinator.service.profile(GROUP_ID, OTHER_B)).affection == 20
    results = _result_lines(sent[-1])
    assert "整局未答，好感度 -2，当前 18" in results[INITIATOR]
    assert "答对了，好感度 +2，当前 22" in results[OTHER]
    assert OTHER_B not in results


async def test_group_quiz_wrong_target_is_not_also_marked_timeout(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    await _give_affection(coordinator.service, 20, INITIATOR)
    sent = _capture()
    assert await _start(coordinator, sent) is True
    pending = coordinator.pending[GROUP_ID]

    assert await _submit(coordinator, INITIATOR, correct=False) is True
    await asyncio.wait_for(pending.task, 1)

    assert (await coordinator.service.profile(GROUP_ID, INITIATOR)).affection == 17
    results = _result_lines(sent[-1])
    assert "答错了，好感度 -3，当前 17" in results[INITIATOR]
    assert "整局未答" not in results[INITIATOR]


async def test_group_quiz_only_first_answer_and_valid_choices_count(
    tmp_path: Path,
) -> None:
    coordinator = _coordinator(tmp_path)
    coordinator.question_timeout = 0.1
    await _give_affection(coordinator.service, 20, INITIATOR)
    sent = _capture()
    assert await _start(coordinator, sent) is True
    pending = coordinator.pending[GROUP_ID]

    assert await coordinator.answer(GROUP_ID, INITIATOR, "群友", " hello ") is False
    assert await coordinator.answer(GROUP_ID, INITIATOR, "群友", "4") is False
    assert await _submit(coordinator, INITIATOR, correct=False) is True
    assert await _submit(coordinator, INITIATOR, correct=True) is True
    assert len(pending.answers) == 1
    await asyncio.wait_for(pending.task, 1)

    assert (await coordinator.service.profile(GROUP_ID, INITIATOR)).affection == 17


async def test_group_quiz_can_start_immediately_again(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    sent = _capture()
    assert await _start(coordinator, sent) is True
    first = coordinator.pending[GROUP_ID]
    await _submit(coordinator, INITIATOR, correct=True)
    await asyncio.wait_for(first.task, 1)

    assert await _start(coordinator, sent) is True
    second = coordinator.pending[GROUP_ID]
    assert second is not first
    await _cancel_task(coordinator)


# ------------------------------------------------------------ 命令、席位与消息路由


def _commands(tmp_path: Path, monkeypatch) -> tuple:
    service = _service(tmp_path)
    pk_coordinator = PkCoordinator(service, Config(), _library(tmp_path))
    commands = register_commands(service, Config(), pk_coordinator)
    assert len(commands) == 14
    group_quiz_cmd = commands[12]
    sent: list[str] = []

    async def fake_finish(message: Message | str = "", **kwargs) -> None:
        sent.append(str(message))

    monkeypatch.setattr(group_quiz_cmd, "finish", fake_finish)
    return service, group_quiz_cmd, sent


async def test_group_quiz_probability_command_reports_and_sets(
    tmp_path: Path, monkeypatch
) -> None:
    service, group_quiz_cmd, sent = _commands(tmp_path, monkeypatch)

    await group_quiz_cmd.handlers[0].call(Message(""))
    assert "当前群友问答概率：0.05%（配置默认 0.05%）" in sent[0]

    await group_quiz_cmd.handlers[0].call(Message("2.5%"))
    assert "群友问答概率已调整为 2.5%" in sent[1]
    assert await service.group_quiz_probability() == 0.025

    await group_quiz_cmd.handlers[0].call(Message(""))
    assert "运行时覆盖，配置默认 0.05%" in sent[2]

    reopened = _service(tmp_path)
    assert await reopened.group_quiz_probability() == 0.025

    await group_quiz_cmd.handlers[0].call(Message("0"))
    assert await service.group_quiz_probability() == 0
    await group_quiz_cmd.handlers[0].call(Message("abc"))
    assert "0~1" in sent[-1]


async def test_group_quiz_shares_the_group_slot(tmp_path: Path) -> None:
    service = _service(tmp_path, Config())
    events = EventCoordinator(service, Config(), None, rng=random.Random(0))
    coordinator = _coordinator(tmp_path, events=events)
    events.attach_group_quiz(coordinator)

    assert await _start(coordinator, _capture()) is True
    assert coordinator.busy(GROUP_ID) is True
    assert events.busy(GROUP_ID) is True
    await _cancel_task(coordinator)
    assert coordinator.busy(GROUP_ID) is False
    assert events.busy(GROUP_ID) is False


def _ambient_setup(
    tmp_path: Path,
    config: Config,
    monkeypatch,
    *,
    group_quiz_questions: list[dict] | None = None,
) -> tuple:
    service = _service(tmp_path, config)
    events = EventCoordinator(service, config, None, rng=random.Random(0))
    find_char = FindCharCoordinator(service, config, None, events=events)
    bet = BetCoordinator(service, config, None, events=events)
    pk = PkCoordinator(service, config, _library(tmp_path), events=events)
    group_quiz = GroupQuizCoordinator(
        service, config, _library(tmp_path, group_quiz_questions), events=events
    )
    group_quiz.question_timeout = 60
    wheel = WheelCoordinator(service, config, events=events)
    ambient, answer = register_ambient(
        service, config, events, find_char, bet, pk, wheel, group_quiz
    )
    sent: list[Message] = []

    async def fake_send(message) -> None:
        sent.append(Message(message) if isinstance(message, str) else message)

    monkeypatch.setattr(ambient, "send", fake_send)
    return service, events, pk, group_quiz, wheel, ambient, answer, sent


async def test_group_quiz_answer_router_uses_ambient(tmp_path: Path, monkeypatch) -> None:
    config = Config(mantou_affection_ambient_enabled=False)
    service, _events, _pk, group_quiz, _wheel, _ambient, answer, _sent = _ambient_setup(
        tmp_path, config, monkeypatch
    )
    group_quiz.question_timeout = 0.02
    sent = _capture()
    assert await _start(group_quiz, sent) is True
    pending = group_quiz.pending[GROUP_ID]
    choice = pending.question.answer + 1

    await answer.handlers[0].call(_event(str(choice), user_id=int(INITIATOR)))
    await asyncio.wait_for(pending.task, 1)

    assert (await service.profile(GROUP_ID, INITIATOR)).affection == 2


async def test_group_quiz_ambient_trigger_comes_after_pk(tmp_path: Path, monkeypatch) -> None:
    config = Config(
        mantou_affection_find_char_chance=0.0,
        mantou_affection_bet_chance=0.0,
        mantou_affection_pk_chance=0.0,
        mantou_affection_wheel_chance=0.0,
        mantou_affection_ambient_enabled=False,
    )
    service, _events, pk, group_quiz, _wheel, ambient, _answer, sent = _ambient_setup(
        tmp_path, config, monkeypatch
    )
    pk.question_timeout = 30

    async def pk_triggered() -> bool:
        return True

    async def group_quiz_triggered() -> bool:
        return True

    monkeypatch.setattr(service, "pk_triggered", pk_triggered)
    monkeypatch.setattr(service, "group_quiz_triggered", group_quiz_triggered)
    await ambient.handlers[0].call(_event("随便聊聊", user_id=int(INITIATOR)))

    assert len(sent) == 1
    assert "桃子问答" in sent[0].extract_plain_text()
    assert GROUP_ID in pk.pending
    assert group_quiz.pending == {}
    await _cancel_task(pk)


async def test_group_quiz_ambient_trigger_when_pk_misses(tmp_path: Path, monkeypatch) -> None:
    config = Config(
        mantou_affection_find_char_chance=0.0,
        mantou_affection_bet_chance=0.0,
        mantou_affection_pk_chance=0.0,
        mantou_affection_wheel_chance=0.0,
        mantou_affection_ambient_enabled=False,
    )
    service, _events, _pk, group_quiz, _wheel, ambient, _answer, sent = _ambient_setup(
        tmp_path, config, monkeypatch
    )

    async def pk_triggered() -> bool:
        return False

    async def group_quiz_triggered() -> bool:
        return True

    monkeypatch.setattr(service, "pk_triggered", pk_triggered)
    monkeypatch.setattr(service, "group_quiz_triggered", group_quiz_triggered)
    await ambient.handlers[0].call(_event("随便聊聊", user_id=int(INITIATOR)))

    assert len(sent) == 1
    assert _at_ids(sent[0]) == [INITIATOR]
    body = sent[0].extract_plain_text()
    assert GROUP_QUIZ_PREFIX in body
    assert "60 秒" in body
    assert GROUP_ID in group_quiz.pending
    await _cancel_task(group_quiz)


async def test_group_quiz_empty_library_lets_next_activity_start(
    tmp_path: Path, monkeypatch
) -> None:
    config = Config(
        mantou_affection_find_char_chance=0.0,
        mantou_affection_bet_chance=0.0,
        mantou_affection_pk_chance=0.0,
        mantou_affection_wheel_chance=1.0,
        mantou_affection_ambient_enabled=False,
    )
    service, _events, _pk, group_quiz, wheel, ambient, _answer, sent = _ambient_setup(
        tmp_path, config, monkeypatch, group_quiz_questions=[]
    )
    await _give_affection(service, 10, INITIATOR)

    async def group_quiz_triggered() -> bool:
        return True

    monkeypatch.setattr(service, "group_quiz_triggered", group_quiz_triggered)
    monkeypatch.setattr(
        "nonebot_plugin_mantou_affection.ambient.get_bot",
        lambda: SimpleNamespace(self_id=999),
    )
    await ambient.handlers[0].call(_event("随便聊聊", user_id=int(INITIATOR)))

    assert group_quiz.pending == {}
    assert GROUP_ID in wheel.pending
    assert "命运齿轮转盘" in sent[-1].extract_plain_text()
    await _cancel_task(wheel)
