import asyncio
import random
from contextlib import suppress
from pathlib import Path
from time import time

import pytest
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message
from nonebot.adapters.onebot.v11.event import Sender

from nonebot_plugin_mantou_affection.ambient import EventCoordinator, register_ambient
from nonebot_plugin_mantou_affection.commands import register_commands
from nonebot_plugin_mantou_affection.config import Config
from nonebot_plugin_mantou_affection.copywriting import AffectionTextLibrary
from nonebot_plugin_mantou_affection.events import EventLibrary
from nonebot_plugin_mantou_affection.findchar import (
    CONFUSABLE_PAIRS,
    FIND_CHAR_MAX_COLS,
    FIND_CHAR_MAX_ROWS,
    FIND_CHAR_MIN_COLS,
    FIND_CHAR_MIN_ROWS,
    FIND_CHAR_PREFIX,
    OPENING_LINES,
    FindCharCoordinator,
    FindCharPuzzle,
    generate_puzzle,
    grid_text,
    is_han,
    judge,
    parse_position,
)
from nonebot_plugin_mantou_affection.logic import POKE_POSITIVE_FALLBACK
from nonebot_plugin_mantou_affection.models import Profile
from nonebot_plugin_mantou_affection.service import AffectionService
from nonebot_plugin_mantou_affection.storage import AffectionStore

GROUP_ID = "90001"
TRIGGER_ID = "90002"
OTHER_ID = "90003"
THIRD_ID = "90004"
POKE_GROUP = "90071"
PUZZLE = FindCharPuzzle(
    rows=6, cols=8, base_char="己", target_char="已", target_row=3, target_col=5
)


class FixedRng(random.Random):
    """固定 random() 返回值，用来精确控制触发判定。"""

    def __init__(self, value: float):
        super().__init__(0)
        self.value = value

    def random(self) -> float:
        return self.value


def _event(
    text: str = "晚上好", user_id: int = 90002, group_id: int = 90001, nickname: str = "桃友"
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
    config: Config | None = None,
    rng: random.Random | None = None,
    text_library: AffectionTextLibrary | None = None,
) -> AffectionService:
    return AffectionService(
        AffectionStore(tmp_path / "affection.json"),
        config or Config(),
        text_library=text_library,
        rng=rng or random.Random(0),
    )


async def _give_affection(
    service: AffectionService, score: int, user_id: str = TRIGGER_ID
) -> None:
    def bump(profile: Profile) -> None:
        profile.affection = score

    await service.store.update_profile(GROUP_ID, user_id, "桃友", bump)


def _capture_send(matcher, monkeypatch) -> list[Message]:
    sent: list[Message] = []

    async def fake_send(message: Message) -> None:
        sent.append(message)

    monkeypatch.setattr(matcher, "send", fake_send)
    return sent


async def _cancel_task(find_char: FindCharCoordinator, group_id: str) -> None:
    pending = find_char.pending.get(str(group_id))
    if pending is None or pending.task is None:
        return
    pending.task.cancel()
    with suppress(asyncio.CancelledError):
        await pending.task


def _at_ids(message: Message) -> list[str]:
    return [str(segment.data["qq"]) for segment in message if segment.type == "at"]


def _lines(message: Message) -> list[str]:
    body = "".join(segment.data["text"] for segment in message if segment.type == "text")
    return [line.strip() for line in body.split("\n") if line.strip()]


# ------------------------------------------------------------ 方阵与判题


def test_confusable_pairs_are_plausible() -> None:
    assert len(CONFUSABLE_PAIRS) >= 40
    for first, second in CONFUSABLE_PAIRS:
        assert len(first) == 1
        assert len(second) == 1
        assert first != second
        assert is_han(first)
        assert is_han(second)


def test_opening_lines_are_plentiful_and_unique() -> None:
    assert 20 <= len(OPENING_LINES) <= 30
    assert len(set(OPENING_LINES)) == len(OPENING_LINES)
    assert all(line.strip() for line in OPENING_LINES)


def test_find_char_defaults() -> None:
    config = Config()
    assert config.mantou_affection_find_char_chance == 0.01
    assert config.mantou_affection_poke_find_char_chance == 0.01
    assert config.mantou_affection_find_char_timeout == 30


@pytest.mark.parametrize("seed", range(20))
def test_generate_puzzle_stays_in_range_and_hides_one_char(seed: int) -> None:
    puzzle = generate_puzzle(random.Random(seed))

    assert FIND_CHAR_MIN_ROWS <= puzzle.rows <= FIND_CHAR_MAX_ROWS
    assert FIND_CHAR_MIN_COLS <= puzzle.cols <= FIND_CHAR_MAX_COLS
    assert puzzle.base_char != puzzle.target_char
    assert is_han(puzzle.base_char) and is_han(puzzle.target_char)
    assert 1 <= puzzle.target_row <= puzzle.rows
    assert 1 <= puzzle.target_col <= puzzle.cols

    lines = grid_text(puzzle).split("\n")
    assert len(lines) == puzzle.rows
    assert all(len(line.split(" ")) == puzzle.cols for line in lines)
    body = "".join(lines)
    assert body.count(puzzle.target_char) == 1
    assert body.count(puzzle.base_char) == puzzle.rows * puzzle.cols - 1


def test_grid_text_marks_only_the_target_cell() -> None:
    lines = grid_text(PUZZLE).split("\n")

    assert lines[2].split(" ")[4] == PUZZLE.target_char
    assert lines[2].split(" ")[3] == PUZZLE.base_char
    assert lines[0].split(" ")[0] == PUZZLE.base_char


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("已", True),
        (" 已 ", True),
        ("己", False),
        ("乙", False),
        ("1", None),
        ("a", None),
        ("已已", None),
        ("晚上好呀", None),
        ("", None),
        ("   ", None),
    ],
)
def test_judge_single_char_answers(text: str, expected: bool | None) -> None:
    assert judge(text, PUZZLE) is expected


@pytest.mark.parametrize(
    "text",
    [
        "3行5列",
        "第3行第5列",
        "5列3行",
        "第5列第3行",
        "3 行 5 列",
        "3行，5列",
        "３行５列",
        "三行五列",
        "第三行第五列",
        "第3行第5列，就是它",
    ],
)
def test_judge_accepts_positions(text: str) -> None:
    assert judge(text, PUZZLE) is True


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1行1列", False),
        ("第5行第3列", False),
        ("5行3列", False),
        ("第12行第8列", False),
        ("3行", None),
        ("5列", None),
        ("第21行第1列", False),
        ("今天天气不错", None),
    ],
)
def test_judge_rejects_wrong_positions(text: str, expected: bool | None) -> None:
    assert judge(text, PUZZLE) is expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("3行5列", (3, 5)),
        ("第3行第5列", (3, 5)),
        ("5列3行", (3, 5)),
        ("第5列第3行", (3, 5)),
        ("十二行八列", (12, 8)),
        ("第二十列第一行", (1, 20)),
        ("十行十列", (10, 10)),
        ("3行", None),
        ("5列", None),
        ("随便聊聊", None),
    ],
)
def test_parse_position_handles_both_orders_and_chinese_numerals(
    text: str, expected: tuple[int, int] | None
) -> None:
    assert parse_position(text) == expected


def test_message_mentions_grid_and_rules(tmp_path: Path) -> None:
    config = Config(mantou_affection_find_char_timeout=30)
    find_char = FindCharCoordinator(_service(tmp_path, config), config, rng=random.Random(7))
    pending = find_char.start(GROUP_ID, TRIGGER_ID, "桃友")
    assert pending is not None
    text = find_char.message(pending)

    assert text.startswith(FIND_CHAR_PREFIX)
    assert grid_text(pending.puzzle) in text
    assert any(line.replace("{bot}", "馒头") == pending.opening for line in OPENING_LINES)
    assert "方阵里有一个字和大家不一样" in text
    assert "3行5列" in text and "第3行第5列" in text
    assert "行从上到下、列从左到右" in text
    assert "大家都可以回答（答题不用@）" in text
    assert "请在 30 秒内作答" in text
    assert "答对好感度 +5" in text
    assert "答错好感度 -3" in text
    assert "被点名的群友超时未答好感度 -2！" in text


# ------------------------------------------------------------ 一局游戏


async def test_start_blocks_second_game_in_same_group(tmp_path: Path) -> None:
    config = Config()
    find_char = FindCharCoordinator(_service(tmp_path, config), config, rng=random.Random(1))

    first = find_char.start(GROUP_ID, TRIGGER_ID, "桃友")
    assert first is not None
    assert find_char.start(GROUP_ID, OTHER_ID, "路人") is None
    assert find_char.pending[GROUP_ID] is first
    assert find_char.busy(GROUP_ID) is True

    find_char.discard(GROUP_ID)
    assert find_char.busy(GROUP_ID) is False


async def test_answer_keeps_first_guess_only(tmp_path: Path) -> None:
    config = Config()
    find_char = FindCharCoordinator(_service(tmp_path, config), config, rng=random.Random(2))
    pending = find_char.start(GROUP_ID, TRIGGER_ID, "桃友")
    assert pending is not None

    assert find_char.answer(GROUP_ID, OTHER_ID, "路人", pending.puzzle.base_char) is True
    assert find_char.answer(GROUP_ID, OTHER_ID, "路人", pending.puzzle.target_char) is False
    assert find_char.answer(GROUP_ID, OTHER_ID, "路人", "随便聊聊") is False
    assert list(pending.answers) == [OTHER_ID]
    assert pending.answers[OTHER_ID].correct is False


async def test_settle_scores_answers_and_trigger_timeout(tmp_path: Path) -> None:
    config = Config()
    service = _service(tmp_path, config)
    await _give_affection(service, 20, TRIGGER_ID)
    await _give_affection(service, 10, OTHER_ID)
    await _give_affection(service, 10, THIRD_ID)
    find_char = FindCharCoordinator(service, config, rng=random.Random(3))
    pending = find_char.start(GROUP_ID, TRIGGER_ID, "桃友")
    assert pending is not None

    assert find_char.answer(GROUP_ID, OTHER_ID, "路人", pending.puzzle.target_char) is True
    assert find_char.answer(GROUP_ID, THIRD_ID, "路人乙", "1行1列") is True
    assert pending.answers[THIRD_ID].correct is False
    assert find_char.answer(GROUP_ID, THIRD_ID, "路人乙", "2行2列") is False
    sent: list[Message] = []

    async def fake_send(message: Message) -> None:
        sent.append(message)

    task = find_char.schedule(pending, group_id=GROUP_ID, send=fake_send, timeout=0.01)
    await asyncio.wait_for(task, 2)

    assert len(sent) == 1
    assert _at_ids(sent[0]) == [OTHER_ID, THIRD_ID, TRIGGER_ID]
    lines = _lines(sent[0])
    assert "好感度 +5，当前 15" in lines[0]
    assert pending.puzzle.target_char in lines[0]
    assert "好感度 -3，当前 7" in lines[1]
    assert "超时未答" in lines[2]
    assert "好感度 -2，当前 18" in lines[2]
    assert (await service.profile(GROUP_ID, OTHER_ID)).affection == 15
    assert (await service.profile(GROUP_ID, THIRD_ID)).affection == 7
    assert (await service.profile(GROUP_ID, TRIGGER_ID)).affection == 18
    assert find_char.pending == {}


async def test_settle_counts_wrong_answer_as_answered(tmp_path: Path) -> None:
    config = Config()
    service = _service(tmp_path, config)
    await _give_affection(service, 20, TRIGGER_ID)
    find_char = FindCharCoordinator(service, config, rng=random.Random(4))
    pending = find_char.start(GROUP_ID, TRIGGER_ID, "桃友")
    assert pending is not None

    assert find_char.answer(GROUP_ID, TRIGGER_ID, "桃友", pending.puzzle.base_char) is True
    sent: list[Message] = []

    async def fake_send(message: Message) -> None:
        sent.append(message)

    task = find_char.schedule(pending, group_id=GROUP_ID, send=fake_send, timeout=0.01)
    await asyncio.wait_for(task, 2)

    lines = _lines(sent[0])
    assert len(lines) == 1
    assert "好感度 -3，当前 17" in lines[0]
    assert "超时未答" not in lines[0]
    assert (await service.profile(GROUP_ID, TRIGGER_ID)).affection == 17


async def test_find_char_and_event_share_the_group_slot(
    tmp_path: Path, bundled_texts_path: Path
) -> None:
    config = Config()
    service = _service(tmp_path, config)
    events = EventLibrary(bundled_texts_path.with_name("affection_events.json"))
    coordinator = EventCoordinator(service, config, events, rng=random.Random(0))
    find_char = FindCharCoordinator(service, config, events=coordinator, rng=random.Random(0))
    coordinator.attach_find_char(find_char)

    running = coordinator.start(GROUP_ID, TRIGGER_ID, "桃友")
    assert running is not None
    assert find_char.start(GROUP_ID, TRIGGER_ID, "桃友") is None
    assert coordinator.busy(GROUP_ID) is True
    coordinator.discard(GROUP_ID)

    game = find_char.start(GROUP_ID, TRIGGER_ID, "桃友")
    assert game is not None
    assert coordinator.start(GROUP_ID, TRIGGER_ID, "桃友") is None
    assert coordinator.busy(GROUP_ID) is True
    find_char.discard(GROUP_ID)
    assert coordinator.busy(GROUP_ID) is False


# ------------------------------------------------------------ 戳一戳路径


async def test_poke_find_char_requires_send(tmp_path: Path) -> None:
    config = Config()
    find_char = FindCharCoordinator(_service(tmp_path, config), config, rng=FixedRng(0.0))

    started = await find_char.start_poke_find_char(
        group_id=GROUP_ID, user_id=TRIGGER_ID, nickname="桃友", send=None, chance=1.0
    )

    assert started is None
    assert find_char.pending == {}


async def test_poke_find_char_respects_chance(tmp_path: Path) -> None:
    config = Config()
    find_char = FindCharCoordinator(_service(tmp_path, config), config, rng=FixedRng(0.9))

    started = await find_char.start_poke_find_char(
        group_id=GROUP_ID,
        user_id=TRIGGER_ID,
        nickname="桃友",
        send=_noop_send,
        chance=0.01,
    )

    assert started is None
    assert find_char.pending == {}


async def test_poke_find_char_returns_message_and_keeps_settlement_for_send(
    tmp_path: Path,
) -> None:
    config = Config(mantou_affection_find_char_timeout=3)
    service = _service(tmp_path, config)
    await _give_affection(service, 10, TRIGGER_ID)
    find_char = FindCharCoordinator(service, config, rng=FixedRng(0.0))
    sent: list[Message] = []

    async def fake_send(message: Message) -> None:
        sent.append(message)

    text = await find_char.start_poke_find_char(
        group_id=GROUP_ID, user_id=TRIGGER_ID, nickname="桃友", send=fake_send, chance=1.0
    )

    assert text is not None
    assert text.startswith(FIND_CHAR_PREFIX)
    assert sent == []
    pending = find_char.pending[GROUP_ID]
    assert pending.trigger_id == TRIGGER_ID
    assert pending.task is not None
    await _cancel_task(find_char, GROUP_ID)


async def test_public_poke_returns_find_char_message(
    tmp_path: Path, monkeypatch
) -> None:
    from nonebot_plugin_mantou_affection import poke

    config = Config(mantou_affection_find_char_timeout=3)
    service = _service(tmp_path, config)
    find_char = FindCharCoordinator(service, config, rng=FixedRng(0.0))
    coordinator = EventCoordinator(service, config, None, rng=FixedRng(0.9))
    coordinator.attach_find_char(find_char)
    monkeypatch.setattr("nonebot_plugin_mantou_affection.find_char_coordinator", find_char)
    monkeypatch.setattr("nonebot_plugin_mantou_affection.event_coordinator", coordinator)
    monkeypatch.setattr("nonebot_plugin_mantou_affection.plugin_config", config)
    sent: list[str] = []

    async def fake_send(message) -> None:
        sent.append(str(message))

    result = await poke(POKE_GROUP, TRIGGER_ID, nickname="桃友", send=fake_send)

    assert result.delta == 1
    assert result.text.startswith(FIND_CHAR_PREFIX)
    assert POKE_POSITIVE_FALLBACK not in result.text
    assert "好感度 +1" not in result.text
    # 事件消息由调用方发送, send 只用来收结算消息
    assert sent == []
    assert find_char.pending[POKE_GROUP].trigger_id == TRIGGER_ID
    await _cancel_task(find_char, POKE_GROUP)


async def test_public_poke_stays_plain_when_find_char_chance_is_zero(
    tmp_path: Path, monkeypatch
) -> None:
    from nonebot_plugin_mantou_affection import poke

    config = Config(
        mantou_affection_poke_event_chance=0.0,
        mantou_affection_poke_find_char_chance=0.0,
    )
    service = _service(tmp_path, config)
    find_char = FindCharCoordinator(service, config, rng=FixedRng(0.0))
    coordinator = EventCoordinator(service, config, None, rng=FixedRng(0.0))
    coordinator.attach_find_char(find_char)
    monkeypatch.setattr("nonebot_plugin_mantou_affection.find_char_coordinator", find_char)
    monkeypatch.setattr("nonebot_plugin_mantou_affection.event_coordinator", coordinator)
    monkeypatch.setattr("nonebot_plugin_mantou_affection.plugin_config", config)

    async def fake_send(message) -> None:
        return None

    result = await poke("90072", TRIGGER_ID, nickname="桃友", send=fake_send)

    assert result.delta == 1
    assert FIND_CHAR_PREFIX not in result.text
    assert result.text.split("\n")[1] == "好感度 +1，当前 1"
    assert find_char.pending == {}
    assert coordinator.pending == {}


async def test_plugin_wiring_runs_find_char_end_to_end(monkeypatch) -> None:
    from nonebot_plugin_mantou_affection import (
        add_affection,
        ambient_matcher,
        answer_matcher,
        find_char_coordinator,
        get_affection,
        plugin_config,
    )

    group_id = "90091"
    trigger_id = "90092"
    answer_id = "90093"
    monkeypatch.setattr(plugin_config, "mantou_affection_find_char_chance", 1.0)
    monkeypatch.setattr(plugin_config, "mantou_affection_find_char_timeout", 0.05)
    sent: list[Message] = []

    async def fake_send(message) -> None:
        sent.append(Message(message) if isinstance(message, str) else message)

    monkeypatch.setattr(ambient_matcher, "send", fake_send)
    await add_affection(group_id, trigger_id, 3, source="test:find-char-trigger")
    await add_affection(group_id, answer_id, 3, source="test:find-char-answer")

    await ambient_matcher.handlers[0].call(
        _event("晚上好", user_id=int(trigger_id), group_id=int(group_id))
    )

    assert len(sent) == 1
    assert FIND_CHAR_PREFIX in sent[0].extract_plain_text()
    pending = find_char_coordinator.pending[group_id]
    assert pending.task is not None

    await answer_matcher.handlers[0].call(
        _event(
            pending.puzzle.target_char, user_id=int(answer_id), group_id=int(group_id)
        )
    )
    await asyncio.wait_for(pending.task, 2)

    assert len(sent) == 2
    assert _at_ids(sent[1]) == [answer_id, trigger_id]
    assert "好感度 +5" in _lines(sent[1])[0]
    assert "超时未答" in _lines(sent[1])[1]
    assert await get_affection(group_id, answer_id) == 8
    assert await get_affection(group_id, trigger_id) == 1


async def _noop_send(message) -> None:
    return None


# ------------------------------------------------------------ 群消息路径


async def _ambient_setup(
    bundled_texts_path: Path,
    tmp_path: Path,
    monkeypatch,
    **config_kwargs,
) -> tuple:
    config_kwargs.setdefault("mantou_affection_ambient_probability", 1.0)
    config_kwargs.setdefault("mantou_affection_ambient_event_ratio", 0.0)
    config = Config(**config_kwargs)
    service = _service(
        tmp_path,
        config,
        rng=random.Random(0),
        text_library=AffectionTextLibrary(bundled_texts_path),
    )
    coordinator = EventCoordinator(
        service,
        config,
        EventLibrary(bundled_texts_path.with_name("affection_events.json")),
        rng=random.Random(0),
    )
    find_char = FindCharCoordinator(service, config, events=coordinator, rng=random.Random(5))
    ambient, answer = register_ambient(service, config, coordinator, find_char)
    sent = _capture_send(ambient, monkeypatch)
    return service, coordinator, find_char, ambient, answer, sent


async def test_ambient_handler_starts_find_char_game(
    bundled_texts_path: Path, tmp_path: Path, monkeypatch
) -> None:
    service, coordinator, find_char, ambient, answer, sent = await _ambient_setup(
        bundled_texts_path,
        tmp_path,
        monkeypatch,
        mantou_affection_find_char_chance=1.0,
        mantou_affection_ambient_probability=0.0,
        mantou_affection_find_char_timeout=30,
    )
    await _give_affection(service, 12)

    await ambient.handlers[0].call(_event())

    assert len(sent) == 1
    message = sent[0]
    assert [segment.type for segment in message] == ["at", "text"]
    assert _at_ids(message) == [TRIGGER_ID]
    pending = find_char.pending[GROUP_ID]
    assert pending.trigger_id == TRIGGER_ID
    body = message.extract_plain_text()
    assert FIND_CHAR_PREFIX in body
    assert grid_text(pending.puzzle) in body
    assert "请在 30 秒内作答" in body
    assert coordinator.pending == {}

    await answer.handlers[0].call(
        _event(pending.puzzle.target_char, user_id=90003, nickname="路人")
    )
    assert list(pending.answers) == [OTHER_ID]
    assert pending.answers[OTHER_ID].correct is True

    await _cancel_task(find_char, GROUP_ID)
    settled: list[Message] = []

    async def fake_send(message: Message) -> None:
        settled.append(message)

    task = find_char.schedule(pending, group_id=GROUP_ID, send=fake_send, timeout=0.01)
    await asyncio.wait_for(task, 2)

    assert "好感度 +5，当前 5" in _lines(settled[0])[0]
    assert (await service.profile(GROUP_ID, OTHER_ID)).affection == 5


async def test_ambient_handler_skips_small_action_when_find_char_hits(
    bundled_texts_path: Path, tmp_path: Path, monkeypatch
) -> None:
    service, coordinator, find_char, ambient, _answer, sent = await _ambient_setup(
        bundled_texts_path,
        tmp_path,
        monkeypatch,
        mantou_affection_find_char_chance=1.0,
    )
    await _give_affection(service, 12)

    await ambient.handlers[0].call(_event())

    assert len(sent) == 1
    assert FIND_CHAR_PREFIX in sent[0].extract_plain_text()
    assert coordinator.pending == {}
    await _cancel_task(find_char, GROUP_ID)


async def test_ambient_handler_keeps_small_action_without_find_char(
    bundled_texts_path: Path, tmp_path: Path, monkeypatch
) -> None:
    service, coordinator, find_char, ambient, _answer, sent = await _ambient_setup(
        bundled_texts_path,
        tmp_path,
        monkeypatch,
        mantou_affection_find_char_chance=0.0,
    )
    await _give_affection(service, 12)

    await ambient.handlers[0].call(_event())

    assert len(sent) == 1
    assert FIND_CHAR_PREFIX not in sent[0].extract_plain_text()
    assert find_char.pending == {}
    assert coordinator.pending == {}


async def test_ambient_handler_falls_back_when_group_is_busy(
    bundled_texts_path: Path, tmp_path: Path, monkeypatch
) -> None:
    service, coordinator, find_char, ambient, _answer, sent = await _ambient_setup(
        bundled_texts_path,
        tmp_path,
        monkeypatch,
        mantou_affection_find_char_chance=1.0,
    )
    await _give_affection(service, 12)
    running = coordinator.start(GROUP_ID, TRIGGER_ID, "桃友")
    assert running is not None

    await ambient.handlers[0].call(_event())

    assert len(sent) == 1
    assert FIND_CHAR_PREFIX not in sent[0].extract_plain_text()
    assert find_char.pending == {}
    assert coordinator.pending[GROUP_ID] is running


# ------------------------------------------------------------ 概率读取


async def test_find_char_triggered_needs_positive_affection(tmp_path: Path) -> None:
    config = Config(mantou_affection_find_char_chance=1.0)
    service = _service(tmp_path, config, rng=FixedRng(0.0))

    assert await service.find_char_triggered(GROUP_ID, TRIGGER_ID) is False
    await _give_affection(service, 5)
    assert await service.find_char_triggered(GROUP_ID, TRIGGER_ID) is True


async def test_find_char_triggered_respects_chance(tmp_path: Path) -> None:
    config = Config(mantou_affection_find_char_chance=0.01)
    service = _service(tmp_path, config, rng=FixedRng(0.5))
    await _give_affection(service, 5)

    assert await service.find_char_triggered(GROUP_ID, TRIGGER_ID) is False


async def test_find_char_chances_prefer_stored_override(tmp_path: Path) -> None:
    service = _service(tmp_path)
    assert await service.find_char_chances() == pytest.approx((0.01, 0.01))

    await service.set_find_char_chances(group=0.5)
    assert await service.find_char_chances() == pytest.approx((0.5, 0.01))
    await service.set_find_char_chances(poke=0.25)
    assert await service.find_char_chances() == pytest.approx((0.5, 0.25))

    restarted = AffectionService(
        AffectionStore(tmp_path / "affection.json"),
        Config(),
        rng=random.Random(0),
    )
    assert await restarted.find_char_chances() == pytest.approx((0.5, 0.25))


async def test_find_char_poke_chance_uses_callers_default(tmp_path: Path) -> None:
    service = _service(tmp_path)
    assert await service.find_char_poke_chance(0.4) == pytest.approx(0.4)

    await service.set_find_char_chances(poke=0.05)
    assert await service.find_char_poke_chance(0.4) == pytest.approx(0.05)


async def test_find_char_chances_ignore_broken_setting(tmp_path: Path) -> None:
    service = _service(tmp_path)
    await service.store.set_setting("find_char_group_chance", "not a number")

    assert await service.find_char_group_chance() == pytest.approx(0.01)


# ------------------------------------------------------------ 概率命令


def _find_char_command(tmp_path: Path, monkeypatch) -> tuple:
    service = _service(tmp_path)
    command = register_commands(service, Config())[7]
    sent: list[str] = []

    async def fake_finish(message: Message | str = "", **kwargs) -> None:
        sent.append(str(message))

    monkeypatch.setattr(command, "finish", fake_finish)
    return service, command, sent


async def test_find_char_command_reports_both_chances(tmp_path: Path, monkeypatch) -> None:
    _service, command, sent = _find_char_command(tmp_path, monkeypatch)

    await command.handlers[0].call(Message(""))

    assert "群消息：1%" in sent[0]
    assert "戳一戳：1%" in sent[0]
    assert "/馒头找字概率 群消息 5% 戳一戳 2%" in sent[0]


async def test_find_char_command_sets_both_chances(tmp_path: Path, monkeypatch) -> None:
    service, command, sent = _find_char_command(tmp_path, monkeypatch)

    await command.handlers[0].call(Message("5%"))

    assert "群消息：5%" in sent[0]
    assert "戳一戳：5%" in sent[0]
    assert await service.find_char_chances() == pytest.approx((0.05, 0.05))


async def test_find_char_command_sets_single_chance(tmp_path: Path, monkeypatch) -> None:
    service, command, sent = _find_char_command(tmp_path, monkeypatch)

    await command.handlers[0].call(Message("群消息 20%"))

    assert "群消息：20%" in sent[0]
    assert "戳一戳" not in sent[0]
    assert await service.find_char_chances() == pytest.approx((0.2, 0.01))


async def test_find_char_command_rejects_bad_input(tmp_path: Path, monkeypatch) -> None:
    service, command, sent = _find_char_command(tmp_path, monkeypatch)

    await command.handlers[0].call(Message("群消息 abc"))

    assert "馒头找字概率" in sent[0]
    assert await service.find_char_chances() == pytest.approx((0.01, 0.01))
