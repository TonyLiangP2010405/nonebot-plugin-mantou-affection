import asyncio
import json
import random
from contextlib import suppress
from pathlib import Path
from time import time as now_timestamp

from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message
from nonebot.adapters.onebot.v11.event import Sender

from nonebot_plugin_mantou_affection.ambient import EventCoordinator, register_ambient
from nonebot_plugin_mantou_affection.bet import BetCoordinator, BetLibrary
from nonebot_plugin_mantou_affection.commands import register_commands
from nonebot_plugin_mantou_affection.config import Config
from nonebot_plugin_mantou_affection.events import EventLibrary
from nonebot_plugin_mantou_affection.findchar import FindCharCoordinator, FindCharLibrary
from nonebot_plugin_mantou_affection.models import Profile
from nonebot_plugin_mantou_affection.pk import (
    BUNDLED_PK_PATH,
    PkCoordinator,
    PkQuestionLibrary,
)
from nonebot_plugin_mantou_affection.service import AffectionService
from nonebot_plugin_mantou_affection.storage import AffectionStore

GROUP_ID = "94001"
INITIATOR = "94002"
RIVAL = "94003"
OTHER = "94004"
OTHER_B = "94005"

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
    text: str = "晚上好", user_id: int = 94002, group_id: int = 94001, nickname: str = "桃友"
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
    path = tmp_path / "pk_questions.json"
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
) -> PkCoordinator:
    config = config or Config(mantou_affection_pk_low_stock_warning=0)
    service = _service(tmp_path, config)
    coordinator = PkCoordinator(
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

    await service.store.update_profile(group_id, user_id, "桃友", bump)


def _capture() -> list[Message]:
    return []


async def _start(
    coordinator: PkCoordinator,
    sent: list[Message],
    *,
    user_id: str = INITIATOR,
) -> bool:
    async def fake_send(message) -> None:
        sent.append(Message(message) if isinstance(message, str) else message)

    return await coordinator.start(
        group_id=GROUP_ID, user_id=user_id, nickname="桃友", send=fake_send
    )


async def _submit(
    coordinator: PkCoordinator, user_id: str, *, correct: bool, nickname: str = "路人"
) -> bool:
    pending = coordinator.pending[GROUP_ID]
    answer = pending.question.answer
    index = answer if correct else (answer + 1) % 3
    return await coordinator.answer(GROUP_ID, user_id, nickname, str(index + 1))


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


async def _cancel_task(coordinator: PkCoordinator, group_id: str = GROUP_ID) -> None:
    pending = coordinator.pending.get(group_id)
    if pending is None or pending.task is None:
        return
    await asyncio.sleep(0)
    pending.task.cancel()
    with suppress(asyncio.CancelledError):
        await pending.task


# ------------------------------------------------------------ 题库加载与概率


def test_bundled_pk_library_is_valid() -> None:
    library = PkQuestionLibrary(BUNDLED_PK_PATH)
    raw = json.loads(BUNDLED_PK_PATH.read_text(encoding="utf-8"))["questions"]

    assert library.questions
    assert len(library.questions) == len(raw)
    for question in library.questions:
        assert question.text
        assert len(question.options) == 3
        assert all(option for option in question.options)
        assert 0 <= question.answer <= 2


def test_pk_library_skips_invalid_questions(tmp_path: Path) -> None:
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


def test_pk_library_accepts_bare_list_and_rejects_broken_root(tmp_path: Path) -> None:
    path = tmp_path / "bare.json"
    path.write_text(json.dumps(QUESTIONS[:3], ensure_ascii=False), encoding="utf-8")
    assert len(PkQuestionLibrary(path).questions) == 3

    path.write_text('{"questions": "不是数组"}', encoding="utf-8")
    assert PkQuestionLibrary(path).questions == ()

    path.write_text("{", encoding="utf-8")
    assert PkQuestionLibrary(path).questions == ()
    assert PkQuestionLibrary(tmp_path / "missing.json").questions == ()


def test_pk_defaults() -> None:
    config = Config()
    assert getattr(config, "mantou_affection_pk_chance", None) == 0.01
    assert config.mantou_affection_pk_low_stock_warning == 100


def test_pk_default_question_timeout_is_thirty_seconds(tmp_path: Path) -> None:
    coordinator = PkCoordinator(_service(tmp_path), Config())
    assert coordinator.question_timeout == 30


async def test_pk_concurrent_starts_only_launch_one_question(tmp_path: Path) -> None:
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
    assert len(await coordinator.service.store.used_questions()) == 1
    pending = coordinator.pending[GROUP_ID]
    assert await _submit(coordinator, INITIATOR, correct=True) is True
    await asyncio.wait_for(pending.task, 1)
    assert (await coordinator.service.profile(GROUP_ID, INITIATOR)).affection == 3


def test_pk_library_pick_skips_used(tmp_path: Path) -> None:
    library = _library(tmp_path)
    picked = library.pick({0, 1, 2}, 3, random.Random(0))
    assert picked is not None
    assert not set(picked) & {0, 1, 2}
    assert library.pick(set(range(10)), 3, random.Random(0)) is None


async def test_pk_triggered_uses_configured_probability(tmp_path: Path) -> None:
    service = _service(tmp_path)
    trigger = getattr(service, "pk_triggered", None)
    assert callable(trigger)

    service.rng = FixedRng(0.009)
    assert await trigger() is True
    service.rng = FixedRng(0.01)
    assert await trigger() is False


async def test_pk_probability_runtime_override(tmp_path: Path) -> None:
    service = _service(tmp_path)
    probability = getattr(service, "pk_probability", None)
    override = getattr(service, "pk_probability_override", None)
    setter = getattr(service, "set_pk_probability", None)
    assert callable(probability) and callable(override) and callable(setter)

    assert await probability() == 0.01
    assert await override() is None
    await setter(0.25)
    assert await probability() == 0.25
    assert await override() == 0.25


# ------------------------------------------------------------ 开局与作答


async def test_pk_start_needs_no_ranking_and_asks_one_question(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    coordinator.question_timeout = 30
    sent = _capture()

    handled = await _start(coordinator, sent)

    assert handled is True
    pending = coordinator.pending[GROUP_ID]
    assert pending.target[0] == INITIATOR
    assert len(sent) == 1
    prompt = sent[0]
    assert _at_ids(prompt) == [INITIATOR]
    body = prompt.extract_plain_text()
    assert pending.question.text in body
    for number, option in enumerate(pending.question.options, start=1):
        assert f"{number}. {option}" in body
    assert "30 秒" in body
    assert "其他群友也可以回答" in body
    assert "答对 +3" in body
    assert "答错 -5" in body
    assert "超时未答 -10" in body
    assert len(await coordinator.service.store.used_questions()) == 1
    await _cancel_task(coordinator)


async def test_pk_start_reports_empty_bank(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path, questions=QUESTIONS[:2])
    await coordinator.service.store.add_used_questions([0, 1])
    sent = _capture()

    handled = await _start(coordinator, sent)

    assert handled is True
    assert coordinator.pending == {}
    assert "题库已经出完了" in str(sent[0])


async def test_pk_start_yields_when_group_is_busy(tmp_path: Path) -> None:
    service = _service(tmp_path, Config())
    events = EventCoordinator(
        service,
        Config(),
        EventLibrary(BUNDLED_PK_PATH.with_name("affection_events.json")),
        rng=random.Random(0),
    )
    coordinator = _coordinator(tmp_path, events=events)
    events.attach_pk(coordinator)
    running = events.start(GROUP_ID, INITIATOR, "桃友")
    assert running is not None

    handled = await _start(coordinator, _capture())

    assert handled is False
    assert GROUP_ID not in coordinator.pending
    events.discard(GROUP_ID)


async def test_pk_scores_every_answerer_independently(tmp_path: Path) -> None:
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

    assert (await coordinator.service.profile(GROUP_ID, INITIATOR)).affection == 13
    assert (await coordinator.service.profile(GROUP_ID, OTHER)).affection == 15
    assert (await coordinator.service.profile(GROUP_ID, OTHER_B)).affection == 33
    results = _result_lines(sent[-1])
    assert "答对了，好感度 +3，当前 13" in results[INITIATOR]
    assert "答错了，好感度 -5，当前 15" in results[OTHER]
    assert "答对了，好感度 +3，当前 33" in results[OTHER_B]
    correct = pending.question
    assert f"答案：{correct.answer + 1}. {correct.options[correct.answer]}" in str(sent[-1])
    assert coordinator.pending == {}


async def test_pk_target_timeout_penalty_and_bystander_is_optional(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    for user_id in (INITIATOR, OTHER, OTHER_B):
        await _give_affection(coordinator.service, 20, user_id)
    sent = _capture()
    assert await _start(coordinator, sent) is True
    pending = coordinator.pending[GROUP_ID]

    assert await _submit(coordinator, OTHER, correct=True) is True
    await asyncio.wait_for(pending.task, 1)

    assert (await coordinator.service.profile(GROUP_ID, INITIATOR)).affection == 10
    assert (await coordinator.service.profile(GROUP_ID, OTHER)).affection == 23
    assert (await coordinator.service.profile(GROUP_ID, OTHER_B)).affection == 20
    results = _result_lines(sent[-1])
    assert "超时未答，好感度 -10，当前 10" in results[INITIATOR]
    assert "答对了，好感度 +3，当前 23" in results[OTHER]
    assert OTHER_B not in results


async def test_pk_wrong_target_is_not_also_marked_timeout(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    await _give_affection(coordinator.service, 20, INITIATOR)
    sent = _capture()
    assert await _start(coordinator, sent) is True
    pending = coordinator.pending[GROUP_ID]

    assert await _submit(coordinator, INITIATOR, correct=False) is True
    await asyncio.wait_for(pending.task, 1)

    assert (await coordinator.service.profile(GROUP_ID, INITIATOR)).affection == 15
    results = _result_lines(sent[-1])
    assert "答错了，好感度 -5，当前 15" in results[INITIATOR]
    assert "超时未答" not in results[INITIATOR]


async def test_pk_only_first_answer_and_valid_choices_count(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    coordinator.question_timeout = 0.1
    await _give_affection(coordinator.service, 20, INITIATOR)
    sent = _capture()
    assert await _start(coordinator, sent) is True
    pending = coordinator.pending[GROUP_ID]

    assert await coordinator.answer(GROUP_ID, INITIATOR, "桃友", " hello ") is False
    assert await coordinator.answer(GROUP_ID, INITIATOR, "桃友", "4") is False
    assert await _submit(coordinator, INITIATOR, correct=False) is True
    assert await _submit(coordinator, INITIATOR, correct=True) is True
    assert len(pending.answers) == 1
    await asyncio.wait_for(pending.task, 1)

    assert (await coordinator.service.profile(GROUP_ID, INITIATOR)).affection == 15


async def test_pk_can_start_immediately_again_without_repeating_question(
    tmp_path: Path,
) -> None:
    coordinator = _coordinator(tmp_path)
    sent = _capture()
    assert await _start(coordinator, sent) is True
    first = coordinator.pending[GROUP_ID]
    first_text = first.question.text
    await _submit(coordinator, INITIATOR, correct=True)
    await asyncio.wait_for(first.task, 1)

    assert await _start(coordinator, sent) is True
    second = coordinator.pending[GROUP_ID]
    assert second.question.text != first_text
    assert len(await coordinator.service.store.used_questions()) == 2
    await _cancel_task(coordinator)


# ------------------------------------------------------------ 题库余量


async def test_pk_low_stock_warning_and_dedupe(tmp_path: Path) -> None:
    config = Config(mantou_affection_pk_low_stock_warning=100)
    coordinator = _coordinator(tmp_path, config)
    sent_dm: list[tuple[str, str]] = []

    async def fake_dm(user_id: str, message: str) -> None:
        sent_dm.append((user_id, message))

    assert await coordinator._check_low_stock(send_private=fake_dm) is True
    assert sent_dm
    assert "只剩" in sent_dm[0][1]

    sent_dm.clear()
    assert await coordinator._check_low_stock(send_private=fake_dm) is False
    assert sent_dm == []

    coordinator.library = _library(tmp_path, QUESTIONS * 10)
    assert await coordinator._check_low_stock(send_private=fake_dm) is False
    coordinator.library = _library(tmp_path, QUESTIONS)
    assert await coordinator._check_low_stock(send_private=fake_dm) is True
    assert sent_dm


async def test_pk_low_stock_notifies_superusers(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    sent_dm: list[tuple[str, str]] = []

    async def fake_dm(user_id: str, message: str) -> None:
        sent_dm.append((user_id, message))

    count = await coordinator.notify_superusers(3, send_private=fake_dm)

    assert count == len(sent_dm)
    assert all("3/" in message for _user_id, message in sent_dm)


# ------------------------------------------------------------ 命令、席位与消息路由


def _commands(tmp_path: Path, monkeypatch) -> tuple:
    service = _service(tmp_path)
    coordinator = PkCoordinator(service, Config(), _library(tmp_path))
    commands = register_commands(service, Config(), coordinator)
    assert len(commands) == 12
    probability_cmd, bank_cmd = commands[9:11]
    sent: list[str] = []

    async def fake_finish(message: Message | str = "", **kwargs) -> None:
        sent.append(str(message))

    for command in (probability_cmd, bank_cmd):
        monkeypatch.setattr(command, "finish", fake_finish)
    return service, coordinator, probability_cmd, bank_cmd, sent


async def test_pk_probability_command_reports_and_sets(tmp_path: Path, monkeypatch) -> None:
    service, _coordinator, probability_cmd, _bank, sent = _commands(tmp_path, monkeypatch)

    await probability_cmd.handlers[0].call(Message(""))
    assert "当前馒头问答概率：1%" in sent[0]

    await probability_cmd.handlers[0].call(Message("2.5%"))
    assert "馒头问答概率已调整为 2.5%" in sent[1]
    assert await service.pk_probability() == 0.025

    await probability_cmd.handlers[0].call(Message("0"))
    assert await service.pk_probability() == 0
    await probability_cmd.handlers[0].call(Message("abc"))
    assert "0~1" in sent[-1]


async def test_pk_bank_command_reports_remaining(tmp_path: Path, monkeypatch) -> None:
    service, coordinator, _probability, bank_cmd, sent = _commands(tmp_path, monkeypatch)
    await service.store.add_used_questions([0, 1])

    await bank_cmd.handlers[0].call()

    assert f"还剩 {len(QUESTIONS) - 2}/{len(QUESTIONS)} 题" in sent[0]
    assert coordinator.total_questions == len(QUESTIONS)


async def test_pk_shares_the_group_slot(tmp_path: Path) -> None:
    service = _service(tmp_path, Config())
    events = EventCoordinator(service, Config(), None, rng=random.Random(0))
    find_char = FindCharCoordinator(
        service,
        Config(),
        FindCharLibrary(BUNDLED_PK_PATH.with_name("findchar_puzzles.json")),
        events=events,
        rng=random.Random(0),
    )
    coordinator = _coordinator(tmp_path, events=events)
    events.attach_find_char(find_char)
    events.attach_pk(coordinator)

    assert await _start(coordinator, _capture()) is True
    assert coordinator.busy(GROUP_ID) is True
    assert find_char.start(GROUP_ID, INITIATOR, "桃友") is None
    assert events.start(GROUP_ID, INITIATOR, "桃友") is None
    await _cancel_task(coordinator)
    assert coordinator.busy(GROUP_ID) is False


async def test_pk_answer_router_uses_ambient(tmp_path: Path) -> None:
    service = _service(tmp_path, Config())
    events = EventCoordinator(service, Config(), None, rng=random.Random(0))
    coordinator = _coordinator(tmp_path, events=events)
    events.attach_pk(coordinator)
    bet = BetCoordinator(
        service,
        Config(),
        BetLibrary(BUNDLED_PK_PATH.with_name("bet_rounds.json")),
        events=events,
    )
    events.attach_bet(bet)
    _ambient, answer = register_ambient(service, Config(), events, None, bet, coordinator)
    sent = _capture()
    assert await _start(coordinator, sent) is True
    pending = coordinator.pending[GROUP_ID]
    choice = pending.question.answer + 1

    await answer.handlers[0].call(_event(str(choice), user_id=int(INITIATOR)))
    await asyncio.wait_for(pending.task, 1)

    assert (await coordinator.service.profile(GROUP_ID, INITIATOR)).affection == 3


async def test_pk_ambient_trigger_needs_no_affection_or_ranking(
    tmp_path: Path, monkeypatch
) -> None:
    service = _service(tmp_path, Config())
    events = EventCoordinator(service, Config(), None, rng=random.Random(0))
    coordinator = _coordinator(tmp_path, events=events)
    coordinator.question_timeout = 30
    events.attach_pk(coordinator)
    ambient, _answer = register_ambient(service, Config(), events, None, None, coordinator)
    sent: list[Message] = []

    async def always_triggered() -> bool:
        return True

    async def fake_send(message) -> None:
        sent.append(Message(message) if isinstance(message, str) else message)

    monkeypatch.setattr(service, "pk_triggered", always_triggered, raising=False)
    monkeypatch.setattr(ambient, "send", fake_send)
    await ambient.handlers[0].call(_event("随便聊聊", user_id=int(INITIATOR)))

    assert len(sent) == 1
    assert _at_ids(sent[0]) == [INITIATOR]
    assert "桃子问答" in sent[0].extract_plain_text()
    assert "30 秒" in sent[0].extract_plain_text()
    assert (await service.profile(GROUP_ID, INITIATOR)).affection == 0
    await _cancel_task(coordinator)
