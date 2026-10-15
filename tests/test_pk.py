import asyncio
import json
import random
import time
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
    PK_SIDE_A,
    PK_SIDE_B,
    PendingPk,
    PkCoordinator,
    PkQuestionLibrary,
    announce_finished_cooldowns,
    format_wait,
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
    coordinator.select_timeout = 0.05
    coordinator.question_timeout = 0.01
    return coordinator


async def _give_affection(
    service: AffectionService, score: int, user_id: str, group_id: str = GROUP_ID
) -> None:
    def bump(profile: Profile) -> None:
        profile.affection = score

    await service.store.update_profile(group_id, user_id, "桃友", bump)


async def _ready(
    coordinator: PkCoordinator, *user_ids: str, score: int = 60
) -> None:
    """让这些人进入好感榜，并在最近发言表里留下记录。"""

    for user_id in user_ids:
        await _give_affection(coordinator.service, score, user_id)
        coordinator.note_message(GROUP_ID, user_id, f"路人{user_id[-1]}")


def _capture(coordinator: PkCoordinator) -> list[Message]:
    sent: list[Message] = []

    async def fake_send(message) -> None:
        sent.append(Message(message) if isinstance(message, str) else message)

    coordinator.sender = fake_send
    return sent


async def _start(
    coordinator: PkCoordinator,
    sent: list[Message],
    *,
    user_id: str = INITIATOR,
) -> object:
    async def fake_send(message) -> None:
        sent.append(Message(message) if isinstance(message, str) else message)

    return await coordinator.start(
        group_id=GROUP_ID, user_id=user_id, nickname="桃友", send=fake_send
    )


async def _wait_round(
    coordinator: PkCoordinator, pending: PendingPk, index: int, side: str | None = None
):
    """等第 index 题（可指定 A/B 边）开始作答。"""

    for _ in range(3000):
        round_ = pending.round
        if (
            round_ is not None
            and round_.index == index
            and (side is None or round_.side == side)
        ):
            return round_
        if pending.task is not None and pending.task.done():
            break
        await asyncio.sleep(0.001)
    raise AssertionError(f"等不到第 {index} 题（side={side}）")


async def _submit(
    coordinator: PkCoordinator, user_id: str, *, correct: bool, nickname: str = "路人"
) -> None:
    """在当前的题目上作答：correct 决定选对还是选错。"""

    pending = coordinator.pending[GROUP_ID]
    round_ = pending.round
    assert round_ is not None
    answer = round_.question.answer
    index = answer if correct else (answer + 1) % 3
    await coordinator.answer(GROUP_ID, user_id, nickname, str(index + 1))


def _lines(message: Message) -> list[str]:
    body = "".join(segment.data["text"] for segment in message if segment.type == "text")
    return [line.strip() for line in body.split("\n") if line.strip()]


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
    await asyncio.sleep(0)  # 先让任务跑起来,取消时 finally 才会清理席位
    pending.task.cancel()
    with suppress(asyncio.CancelledError):
        await pending.task


# ------------------------------------------------------------ 题库加载


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
    assert config.mantou_affection_pk_cooldown == 3600
    assert config.mantou_affection_pk_recent_messages == 10
    assert config.mantou_affection_pk_low_stock_warning == 100


def test_pk_library_pick_skips_used(tmp_path: Path) -> None:
    library = _library(tmp_path)
    picked = library.pick({0, 1, 2}, 3, random.Random(0))
    assert picked is not None
    assert not set(picked) & {0, 1, 2}

    assert library.pick(set(range(10)), 3, random.Random(0)) is None


# ------------------------------------------------------------ 最近发言与候选


def test_recent_speakers_use_message_sequence(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    coordinator.note_message(GROUP_ID, RIVAL, "甲")
    for index in range(9):
        coordinator.note_message(GROUP_ID, OTHER, f"路人{index}")
    coordinator.note_message(GROUP_ID, OTHER_B, "丙")

    speakers = coordinator.recent_speakers(GROUP_ID, within=10)
    assert RIVAL not in speakers  # 11 条消息之前说的,已经出窗
    assert speakers == {OTHER, OTHER_B}
    assert coordinator.message_seq[GROUP_ID] == 11


async def test_pk_candidates_need_ranking_and_recent_speech(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    await _ready(coordinator, INITIATOR, RIVAL)
    await _give_affection(coordinator.service, 0, OTHER)
    coordinator.note_message(GROUP_ID, OTHER, "丙")

    result = await _start(coordinator, _capture(coordinator))

    assert result.ok is True
    assert "1. 路人3" in result.message
    assert "路人4" not in result.message
    await _cancel_task(coordinator)


async def test_pk_candidate_out_of_window_is_dropped(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    await _ready(coordinator, INITIATOR, RIVAL)
    coordinator.last_seen[(GROUP_ID, RIVAL)] = (1, "甲")
    for index in range(12):
        coordinator.note_message(GROUP_ID, OTHER, f"路人{index}")

    result = await _start(coordinator, _capture(coordinator))

    assert result.ok is False
    assert "没有别的榜上群友说话" in result.message


# ------------------------------------------------------------ 发起门控


async def test_pk_requires_ranking_member(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    await _ready(coordinator, RIVAL)

    result = await _start(coordinator, _capture(coordinator))

    assert result.ok is False
    assert "好感榜" in result.message
    assert coordinator.pending == {}


async def test_pk_blocked_when_group_is_busy(tmp_path: Path) -> None:
    service = _service(tmp_path, Config())
    events = EventCoordinator(
        service,
        Config(),
        EventLibrary(BUNDLED_PK_PATH.with_name("affection_events.json")),
        rng=random.Random(0),
    )
    coordinator = _coordinator(tmp_path, events=events)
    events.attach_pk(coordinator)
    await _ready(coordinator, INITIATOR, RIVAL)
    running = events.start(GROUP_ID, INITIATOR, "桃友")
    assert running is not None

    result = await _start(coordinator, _capture(coordinator))

    assert result.ok is False
    assert "群里正有一场活动" in result.message
    events.discard(GROUP_ID)


async def test_pk_blocked_without_rivals(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    await _ready(coordinator, INITIATOR)

    result = await _start(coordinator, _capture(coordinator))

    assert result.ok is False
    assert "没有别的榜上群友说话" in result.message


async def test_pk_blocked_when_bank_is_empty(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path, questions=QUESTIONS[:6])
    await _ready(coordinator, INITIATOR, RIVAL)
    await coordinator.service.store.add_used_questions(range(6))

    result = await _start(coordinator, _capture(coordinator))

    assert result.ok is False
    assert "题库已经出完了" in result.message


async def test_pk_blocked_when_bank_is_low(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path, questions=QUESTIONS[:6])
    await _ready(coordinator, INITIATOR, RIVAL)
    await coordinator.service.store.add_used_questions([0])

    result = await _start(coordinator, _capture(coordinator))

    assert result.ok is False
    assert "只剩 5 题" in result.message


# ------------------------------------------------------------ 选人阶段


async def test_pk_selection_prompt_lists_candidates(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    await _ready(coordinator, INITIATOR, RIVAL, OTHER)

    result = await _start(coordinator, _capture(coordinator))

    assert result.ok is True
    assert "想和谁 PK" in result.message
    assert "1. " in result.message and "2. " in result.message
    assert coordinator.pending[GROUP_ID].phase == "select"
    await _cancel_task(coordinator)


async def test_pk_selection_only_accepts_initiator_number(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    await _ready(coordinator, INITIATOR, RIVAL)
    sent = _capture(coordinator)
    assert (await _start(coordinator, sent)).ok is True
    pending = coordinator.pending[GROUP_ID]

    assert await coordinator.answer(GROUP_ID, RIVAL, "路人", "1") is False
    assert await coordinator.answer(GROUP_ID, INITIATOR, "桃友", "9") is False
    assert await coordinator.answer(GROUP_ID, INITIATOR, "桃友", "不是数字") is False
    assert pending.selection.done() is False

    assert await coordinator.answer(GROUP_ID, INITIATOR, "桃友", "1") is True
    assert pending.selection.done() is True
    await asyncio.wait_for(pending.task, 2)
    assert coordinator.pending == {}


async def test_pk_selection_timeout_cancels(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    await _ready(coordinator, INITIATOR, RIVAL)
    sent = _capture(coordinator)
    assert (await _start(coordinator, sent)).ok is True
    pending = coordinator.pending[GROUP_ID]

    await asyncio.wait_for(pending.task, 2)

    assert coordinator.pending == {}
    assert any("等不到你的选择" in str(message) for message in sent)
    assert await coordinator.cooldown_remaining(GROUP_ID, INITIATOR) == 0


# ------------------------------------------------------------ 答题流程


async def _begin_match(coordinator: PkCoordinator, sent: list[Message]) -> PendingPk:
    assert (await _start(coordinator, sent)).ok is True
    pending = coordinator.pending[GROUP_ID]
    assert await coordinator.answer(GROUP_ID, INITIATOR, "桃友", "1") is True
    for _ in range(3000):
        if pending.captains:
            break
        await asyncio.sleep(0.001)
    return pending


async def test_pk_match_asks_six_questions_and_announces_order(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    await _ready(coordinator, INITIATOR, RIVAL)
    sent = _capture(coordinator)
    pending = await _begin_match(coordinator, sent)

    assert pending.phase == "question"
    assert set(pending.captains) == {PK_SIDE_A, PK_SIDE_B}
    assert {pending.captains[PK_SIDE_A][0], pending.captains[PK_SIDE_B][0]} == {
        INITIATOR,
        RIVAL,
    }
    order_line = next(
        _lines(message)[0] for message in sent if "先让" in str(message)
    )
    assert "先让" in order_line and "答题" in order_line

    asked: list[str] = []
    for side in (PK_SIDE_A, PK_SIDE_B):
        for index in range(1, 4):
            round_ = await _wait_round(coordinator, pending, index, side)
            asked.append(round_.question.text)
    await asyncio.wait_for(pending.task, 2)

    assert len(asked) == 6
    assert len(set(asked)) == 6
    questions = [line for message in sent for line in _lines(message) if "题：" in line]
    assert len(questions) == 6
    assert any("1. " in str(message) and "3. " in str(message) for message in sent)
    results = [message for message in sent if "题答案：" in str(message)]
    assert len(results) == 6


async def test_pk_question_message_format(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    await _ready(coordinator, INITIATOR, RIVAL)
    sent = _capture(coordinator)
    pending = await _begin_match(coordinator, sent)
    round_ = await _wait_round(coordinator, pending, 1, PK_SIDE_A)

    question_message = next(
        message for message in sent if f"第 1 题：{round_.question.text}" in str(message)
    )
    captain_id = pending.captains[PK_SIDE_A][0]
    assert _at_ids(question_message) == [captain_id]
    body = question_message.extract_plain_text()
    for number, option in enumerate(round_.question.options, start=1):
        assert f"{number}. {option}" in body
    assert "秒内作答，直接发 1、2、3 即可（答题不用@）" in body
    await asyncio.wait_for(pending.task, 2)


async def test_pk_question_result_lists_every_answerer(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    await _ready(coordinator, INITIATOR, RIVAL, OTHER)
    sent = _capture(coordinator)
    pending = await _begin_match(coordinator, sent)
    round_ = await _wait_round(coordinator, pending, 1, PK_SIDE_A)
    await _submit(coordinator, OTHER, correct=True)
    await _submit(coordinator, OTHER_B, correct=False, nickname="路人乙")
    await asyncio.wait_for(pending.task, 2)

    results = [
        message for message in sent if "第 1 题答案：" in str(message)
    ]
    assert results
    result = results[0]
    right_option = round_.question.options[round_.question.answer]
    assert f"{round_.question.answer + 1}. {right_option}" in str(result)
    assert _at_ids(result) == [OTHER, OTHER_B]
    body = result.extract_plain_text()
    assert "答对了" in body and "答错了" in body


async def test_pk_side_lock_rejects_other_side(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    await _ready(coordinator, INITIATOR, RIVAL, OTHER)
    sent = _capture(coordinator)
    pending = await _begin_match(coordinator, sent)
    await _wait_round(coordinator, pending, 1, PK_SIDE_A)
    assert await coordinator.answer(GROUP_ID, OTHER, "路人", "1") is True
    assert pending.side_of[OTHER] == PK_SIDE_A

    # 换边后同一个人再答,会被提醒并拒绝
    await _wait_round(coordinator, pending, 1, PK_SIDE_B)
    assert await coordinator.answer(GROUP_ID, OTHER, "路人", "1") is True
    assert pending.side_of[OTHER] == PK_SIDE_A
    reminder = next(message for message in sent if "不能再参与" in str(message))
    assert "你已经参与了" in str(reminder)
    await asyncio.wait_for(pending.task, 2)


async def test_pk_same_side_later_questions_are_allowed(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    await _ready(coordinator, INITIATOR, RIVAL, OTHER)
    sent = _capture(coordinator)
    pending = await _begin_match(coordinator, sent)
    await _wait_round(coordinator, pending, 1, PK_SIDE_A)
    await _submit(coordinator, OTHER, correct=True)
    await _wait_round(coordinator, pending, 2, PK_SIDE_A)
    await _submit(coordinator, OTHER, correct=True)
    await asyncio.wait_for(pending.task, 2)

    assert not any("不能再参与" in str(message) for message in sent)
    assert "答对 2 题" in str(sent[-1])


async def test_pk_first_answer_per_question_wins(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    await _ready(coordinator, INITIATOR, RIVAL, OTHER)
    sent = _capture(coordinator)
    pending = await _begin_match(coordinator, sent)
    await _wait_round(coordinator, pending, 1, PK_SIDE_A)

    await _submit(coordinator, OTHER, correct=False)
    await _submit(coordinator, OTHER, correct=True)

    assert pending.round is not None
    assert len(pending.round.answers) == 1
    assert pending.round.answers[OTHER].index != pending.round.question.answer
    await asyncio.wait_for(pending.task, 2)


# ------------------------------------------------------------ 结算


async def _play_with(
    coordinator: PkCoordinator,
    *,
    captain_a_correct: int,
    captain_b_correct: int,
    mate_correct: int = 0,
    mate_id: str | None = None,
) -> list[Message]:
    """跑完一局：按指定答对数让两边作答，返回收到的所有消息。"""

    sent = _capture(coordinator)
    pending = await _begin_match(coordinator, sent)
    for side, correct in ((PK_SIDE_A, captain_a_correct), (PK_SIDE_B, captain_b_correct)):
        captain_id = pending.captains[side][0]
        for index in range(1, 4):
            await _wait_round(coordinator, pending, index, side)
            await _submit(coordinator, captain_id, correct=index <= correct)
            if mate_id is not None and side == PK_SIDE_A:
                await _submit(coordinator, mate_id, correct=index <= mate_correct)
    await asyncio.wait_for(pending.task, 2)
    return sent


async def test_pk_settlement_pool_and_split(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    await _ready(coordinator, INITIATOR, RIVAL, OTHER)
    await _give_affection(coordinator.service, 100, INITIATOR)
    await _give_affection(coordinator.service, 100, RIVAL)
    await _give_affection(coordinator.service, 100, OTHER)

    sent = await _play_with(
        coordinator, captain_a_correct=3, captain_b_correct=1, mate_correct=3, mate_id=OTHER
    )
    results = _result_lines(sent[-1])

    assert coordinator.pending == {}
    loser_id = next(user_id for user_id, line in results.items() if "当奖池" in line)
    assert "拿出 10 点好感度当奖池" in results[loser_id]
    assert "答对 3 题，和队长一起分得 5 点好感度" in results[OTHER]
    assert sum(1 for line in results.values() if "分得" in line) == 2


async def test_pk_settlement_draw(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    await _ready(coordinator, INITIATOR, RIVAL)
    await _give_affection(coordinator.service, 100, INITIATOR)
    await _give_affection(coordinator.service, 100, RIVAL)

    sent = await _play_with(coordinator, captain_a_correct=2, captain_b_correct=2)

    settlement = sent[-1]
    assert "平局" in str(settlement)
    assert (await coordinator.service.profile(GROUP_ID, INITIATOR)).affection == 100
    assert (await coordinator.service.profile(GROUP_ID, RIVAL)).affection == 100


async def test_pk_settlement_pool_is_ten_percent(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    await _ready(coordinator, INITIATOR, RIVAL)
    await _give_affection(coordinator.service, 100, INITIATOR)
    await _give_affection(coordinator.service, 37, RIVAL)

    sent = await _play_with(coordinator, captain_a_correct=3, captain_b_correct=0)

    results = _result_lines(sent[-1])
    loser_id = next(user_id for user_id, line in results.items() if "当奖池" in line)
    assert "拿出 3 点好感度当奖池" in results[loser_id]
    assert (await coordinator.service.profile(GROUP_ID, loser_id)).affection == 34


async def test_pk_settlement_mate_split_and_remainder(tmp_path: Path) -> None:
    """奖池由获胜队长和达标队友平分，除不尽的部分给队长。"""

    coordinator = _coordinator(tmp_path)
    await _ready(coordinator, INITIATOR, RIVAL, OTHER)
    await _give_affection(coordinator.service, 100, INITIATOR)
    await _give_affection(coordinator.service, 35, RIVAL)
    await _give_affection(coordinator.service, 10, OTHER)

    sent = await _play_with(
        coordinator, captain_a_correct=3, captain_b_correct=0, mate_correct=3, mate_id=OTHER
    )

    results = _result_lines(sent[-1])
    captain_id = next(
        user_id
        for user_id in results
        if user_id in (INITIATOR, RIVAL) and "分得" in results[user_id]
    )
    assert "获胜队长分得 2 点好感度" in results[captain_id]
    assert "和队长一起分得 1 点好感度" in results[OTHER]
    assert (await coordinator.service.profile(GROUP_ID, OTHER)).affection == 11


async def test_pk_settlement_captain_without_hits(tmp_path: Path) -> None:
    """队长答对 0 而队友有答对时，整队参与者都能分奖池。"""

    coordinator = _coordinator(tmp_path)
    await _ready(coordinator, INITIATOR, RIVAL, OTHER)
    await _give_affection(coordinator.service, 100, INITIATOR)
    await _give_affection(coordinator.service, 100, RIVAL)
    await _give_affection(coordinator.service, 10, OTHER)

    sent = await _play_with(
        coordinator, captain_a_correct=0, captain_b_correct=0, mate_correct=2, mate_id=OTHER
    )

    results = _result_lines(sent[-1])
    assert sum(1 for line in results.values() if "分得" in line) == 2
    assert "答对 2 题，和队长一起分得 5 点好感度，当前 15" in results[OTHER]
    assert not any("没达到队长的手气" in line for line in results.values())


async def test_pk_settlement_mate_below_captain(tmp_path: Path) -> None:
    """队友答对数没到队长的手气时分不到奖池。"""

    coordinator = _coordinator(tmp_path)
    await _ready(coordinator, INITIATOR, RIVAL, OTHER)
    await _give_affection(coordinator.service, 100, INITIATOR)
    await _give_affection(coordinator.service, 50, RIVAL)
    await _give_affection(coordinator.service, 10, OTHER)

    sent = await _play_with(
        coordinator, captain_a_correct=3, captain_b_correct=0, mate_correct=1, mate_id=OTHER
    )

    results = _result_lines(sent[-1])
    assert "答对 1 题，没达到队长的手气，这次没分到好感度，当前 10" in results[OTHER]
    assert "获胜队长分得 5 点好感度，当前 105" in results[INITIATOR] or any(
        "获胜队长分得 5 点好感度" in line for line in results.values()
    )


# ------------------------------------------------------------ 冷却与重启


async def test_pk_cooldown_starts_after_match(tmp_path: Path) -> None:
    config = Config(mantou_affection_pk_cooldown=3600)
    coordinator = _coordinator(tmp_path, config)
    await _ready(coordinator, INITIATOR, RIVAL)
    sent = _capture(coordinator)
    await _begin_match(coordinator, sent)
    await asyncio.wait_for(coordinator.pending[GROUP_ID].task, 2)

    remaining = await coordinator.cooldown_remaining(GROUP_ID, INITIATOR)
    assert 3500 < remaining <= 3600

    result = await _start(coordinator, sent)
    assert result.ok is False
    assert "后才能再发起问答 PK" in result.message


async def test_pk_cooldown_reminder_and_clear(tmp_path: Path) -> None:
    config = Config(mantou_affection_pk_cooldown=0)
    coordinator = _coordinator(tmp_path, config)
    assert await coordinator.service.pk_cooldown() == 0
    await _ready(coordinator, INITIATOR, RIVAL)
    sent = _capture(coordinator)
    await _begin_match(coordinator, sent)
    await asyncio.wait_for(coordinator.pending[GROUP_ID].task, 2)
    assert await coordinator.cooldown_remaining(GROUP_ID, INITIATOR) == 0

    # 手动记一条冷却,看看到点提醒
    await coordinator.service.store.set_cooldown(GROUP_ID, INITIATOR, time.time() + 0.05)

    async def fake_send(message) -> None:
        sent.append(Message(message) if isinstance(message, str) else message)

    task = coordinator.schedule_cooldown_reminder(
        GROUP_ID, INITIATOR, seconds=0.05, send=fake_send
    )
    await asyncio.wait_for(task, 2)
    assert any("又能发起问答 PK 啦" in str(message) for message in sent)
    assert await coordinator.cooldown_remaining(GROUP_ID, INITIATOR) == 0


async def test_pk_restart_cooldown_recovery(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    await coordinator.service.store.set_cooldown(GROUP_ID, INITIATOR, time.time() + 600)
    await coordinator.service.store.set_cooldown(GROUP_ID, RIVAL, time.time() - 10)
    sent: list[tuple[str, str]] = []

    async def fake_send(group_id: str, message) -> None:
        sent.append((group_id, str(message)))

    reminded = await announce_finished_cooldowns(coordinator, send=fake_send)

    # 未到期与已到期的冷却记录都补发提醒, 随后清空
    assert reminded == 2
    assert sent[0][0] == GROUP_ID
    assert "又能发起问答 PK 啦" in sent[0][1]
    assert await coordinator.service.store.cooldowns() == {}
    assert await coordinator.cooldown_remaining(GROUP_ID, INITIATOR) == 0


async def test_pk_cooldown_reminder_survives_restart(tmp_path: Path) -> None:
    """冷却记录写在数据文件里，重启后还能读出来。"""

    config = Config(mantou_affection_pk_cooldown=60)
    coordinator = _coordinator(tmp_path, config)
    await _ready(coordinator, INITIATOR, RIVAL)
    sent = _capture(coordinator)
    await _begin_match(coordinator, sent)
    await asyncio.wait_for(coordinator.pending[GROUP_ID].task, 2)
    await _cancel_task(coordinator)

    restarted = PkCoordinator(
        AffectionService(AffectionStore(tmp_path / "affection.json"), config),
        config,
        _library(tmp_path),
    )
    assert await restarted.cooldown_remaining(GROUP_ID, INITIATOR) > 0
    await restarted.service.store.clear_cooldowns()


# ------------------------------------------------------------ 题库消耗与余量


async def test_pk_questions_are_consumed(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    await _ready(coordinator, INITIATOR, RIVAL)
    assert await coordinator.remaining_questions() == len(QUESTIONS)

    sent = await _play_with(coordinator, captain_a_correct=1, captain_b_correct=1)

    used = await coordinator.service.store.used_questions()
    assert len(used) == 6
    assert len(set(used)) == 6
    assert await coordinator.remaining_questions() == len(QUESTIONS) - 6

    await coordinator.service.store.clear_cooldowns()
    sent.clear()
    assert (await _start(coordinator, sent)).ok is True
    assert await coordinator.answer(GROUP_ID, INITIATOR, "桃友", "1") is True
    for _ in range(3000):
        if len(await coordinator.service.store.used_questions()) == 12:
            break
        await asyncio.sleep(0.001)
    used_again = await coordinator.service.store.used_questions()
    assert len(used_again) == 12
    assert len(set(used_again)) == 12
    await _cancel_task(coordinator)


async def test_pk_low_stock_warning_and_dedupe(tmp_path: Path) -> None:
    config = Config(mantou_affection_pk_low_stock_warning=100)
    coordinator = _coordinator(tmp_path, config)
    sent_dm: list[tuple[str, str]] = []

    async def fake_dm(user_id: str, message: str) -> None:
        sent_dm.append((user_id, message))

    assert await coordinator._check_low_stock(send_private=fake_dm) is True
    assert sent_dm
    assert "只剩" in sent_dm[0][1]

    # 去重:再次检查不会再发
    sent_dm.clear()
    assert await coordinator._check_low_stock(send_private=fake_dm) is False
    assert sent_dm == []

    # 余量回升后重置标记,再次跌破会重新提醒
    await coordinator.service.store.clear_cooldowns()
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


# ------------------------------------------------------------ 命令


def _commands(tmp_path: Path, monkeypatch) -> tuple:
    service = _service(tmp_path)
    coordinator = PkCoordinator(service, Config(), _library(tmp_path))
    commands = register_commands(service, Config(), coordinator)
    pk_cmd, cooldown_cmd, active_cmd, bank_cmd = commands[9:]
    sent: list[str] = []

    async def fake_finish(message: Message | str = "", **kwargs) -> None:
        sent.append(str(message))

    for command in (pk_cmd, cooldown_cmd, active_cmd, bank_cmd):
        monkeypatch.setattr(command, "finish", fake_finish)
    return service, coordinator, pk_cmd, cooldown_cmd, active_cmd, bank_cmd, sent


async def test_pk_cooldown_command_reports_and_sets(tmp_path: Path, monkeypatch) -> None:
    service, _coordinator, _pk, cooldown_cmd, _active, _bank, sent = _commands(
        tmp_path, monkeypatch
    )

    await cooldown_cmd.handlers[0].call(Message(""))
    assert "当前问答 PK 冷却：1 小时" in sent[0]

    await cooldown_cmd.handlers[0].call(Message("30分钟"))
    assert "问答 PK 冷却已调整为 30 分钟" in sent[1]
    assert await service.pk_cooldown() == 1800

    await cooldown_cmd.handlers[0].call(Message("1800"))
    assert await service.pk_cooldown() == 1800

    await cooldown_cmd.handlers[0].call(Message("0"))
    assert await service.pk_cooldown() == 0

    await cooldown_cmd.handlers[0].call(Message("abc"))
    assert "请输入秒数" in sent[-1]
    await cooldown_cmd.handlers[0].call(Message("25小时"))
    assert "不能超过 24 小时" in sent[-1]


async def test_pk_active_command_reports_and_sets(tmp_path: Path, monkeypatch) -> None:
    service, _coordinator, _pk, _cooldown, active_cmd, _bank, sent = _commands(
        tmp_path, monkeypatch
    )

    await active_cmd.handlers[0].call(Message(""))
    assert "当前问答 PK 活跃消息数：10 条" in sent[0]

    await active_cmd.handlers[0].call(Message("20"))
    assert "已调整为 20 条" in sent[1]
    assert await service.pk_recent_messages() == 20

    await active_cmd.handlers[0].call(Message("0"))
    assert "1~1000" in sent[-1]


async def test_pk_bank_command_reports_remaining(tmp_path: Path, monkeypatch) -> None:
    service, coordinator, _pk, _cooldown, _active, bank_cmd, sent = _commands(
        tmp_path, monkeypatch
    )
    await service.store.add_used_questions([0, 1])

    await bank_cmd.handlers[0].call()

    assert f"还剩 {len(QUESTIONS) - 2}/{len(QUESTIONS)} 题" in sent[0]
    assert coordinator.total_questions == len(QUESTIONS)


async def test_pk_command_reports_start_result(tmp_path: Path, monkeypatch) -> None:
    _service_obj, coordinator, pk_cmd, _cooldown, _active, _bank, sent = _commands(
        tmp_path, monkeypatch
    )

    await pk_cmd.handlers[0].call(_event("馒头问答PK"))

    assert "好感榜" in sent[0]
    assert coordinator.pending == {}


def test_format_wait_units() -> None:
    assert format_wait(0.2) == "1 秒"
    assert format_wait(90) == "1 分钟"
    assert format_wait(3600) == "1 小时"
    assert format_wait(7260) == "2 小时 1 分钟"


# ------------------------------------------------------------ 席位与路由


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
    await _ready(coordinator, INITIATOR, RIVAL)

    assert (await _start(coordinator, _capture(coordinator))).ok is True

    assert coordinator.busy(GROUP_ID) is True
    assert find_char.start(GROUP_ID, INITIATOR, "桃友") is None
    assert events.start(GROUP_ID, INITIATOR, "桃友") is None
    await _cancel_task(coordinator)
    assert coordinator.busy(GROUP_ID) is False


async def test_pk_answer_router_uses_ambient(tmp_path: Path, monkeypatch) -> None:
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
    await _ready(coordinator, INITIATOR, RIVAL)
    sent = _capture(coordinator)

    async def fake_send(message) -> None:
        sent.append(Message(message) if isinstance(message, str) else message)

    result = await coordinator.start(
        group_id=GROUP_ID, user_id=INITIATOR, nickname="桃友", send=fake_send
    )
    assert result.ok is True

    # 选人阶段的编号通过 answer 路由进 PK
    await answer.handlers[0].call(_event("1", user_id=int(INITIATOR)))
    pending = coordinator.pending[GROUP_ID]
    assert pending.selection.done() is True
    await asyncio.wait_for(pending.task, 2)


async def test_pk_ambient_handler_records_messages(
    tmp_path: Path, monkeypatch
) -> None:
    service = _service(tmp_path, Config())
    events = EventCoordinator(service, Config(), None, rng=random.Random(0))
    coordinator = _coordinator(tmp_path, events=events)
    events.attach_pk(coordinator)
    ambient, _answer = register_ambient(service, Config(), events, None, None, coordinator)
    sent: list[Message] = []

    async def fake_send(message) -> None:
        sent.append(Message(message) if isinstance(message, str) else message)

    monkeypatch.setattr(ambient, "send", fake_send)
    await ambient.handlers[0].call(_event("晚上好", user_id=int(RIVAL), nickname="甲"))

    assert coordinator.message_seq[GROUP_ID] == 1
    assert (GROUP_ID, RIVAL) in coordinator.last_seen
    assert coordinator.last_seen[(GROUP_ID, RIVAL)][1] == "甲"
    assert sent == []
