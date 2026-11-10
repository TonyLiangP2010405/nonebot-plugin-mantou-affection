from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from pathlib import Path

from nonebot.adapters.onebot.v11 import Message

from nonebot_plugin_mantou_affection.ambient import EventCoordinator
from nonebot_plugin_mantou_affection.commands import register_commands
from nonebot_plugin_mantou_affection.config import Config
from nonebot_plugin_mantou_affection.fill_blank import (
    BUNDLED_FILL_BLANK_PATH,
    FILL_BLANK_PREFIX,
    FillBlankCoordinator,
    FillBlankLibrary,
    looks_like_answer,
    matched_characters,
    normalize_answer,
    score_answer,
)
from nonebot_plugin_mantou_affection.service import AffectionService
from nonebot_plugin_mantou_affection.storage import AffectionStore

GROUP_ID = "96001"
TARGET = "96002"
OTHER = "96003"
OTHER_B = "96004"

QUESTIONS = [
    {
        "id": "dachuhuo",
        "sentence": "看到稀有物时，桃纸会喊「____」！",
        "initials": "dch",
        "answer": "大出货",
        "source": "口头禅",
    },
    {
        "id": "yilimi",
        "sentence": "最遥远的距离，是我和岛之间那____。",
        "initials": "ylm",
        "answer": "一厘米",
        "source": "口头禅",
    },
]


def _library(tmp_path: Path, questions: list[dict] | None = None) -> FillBlankLibrary:
    path = tmp_path / "fill_blank_questions.json"
    payload = questions if questions is not None else QUESTIONS
    path.write_text(
        json.dumps({"questions": payload}, ensure_ascii=False), encoding="utf-8"
    )
    return FillBlankLibrary(path)


def _config(**overrides) -> Config:
    values = {
        "mantou_affection_newbie_protection_below": 0,
        "mantou_affection_newbie_gift_below": 0,
        "mantou_affection_fill_blank_timeout": 60,
    }
    values.update(overrides)
    return Config(**values)


def _service(tmp_path: Path, config: Config) -> AffectionService:
    return AffectionService(AffectionStore(tmp_path / "affection.json"), config)


def _coordinator(
    tmp_path: Path,
    *,
    config: Config | None = None,
    questions: list[dict] | None = None,
    events: EventCoordinator | None = None,
) -> FillBlankCoordinator:
    config = config or _config()
    coordinator = FillBlankCoordinator(
        _service(tmp_path, config),
        config,
        _library(tmp_path, questions),
        events=events,
    )
    coordinator.question_timeout = 0.02
    return coordinator


async def _give_affection(
    service: AffectionService, score: int, user_id: str
) -> None:
    def bump(profile) -> None:
        profile.affection = score

    await service.store.update_profile(GROUP_ID, user_id, "群友", bump)


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


async def _start(
    coordinator: FillBlankCoordinator,
    sent: list[Message],
    *,
    user_id: str = TARGET,
) -> bool:
    async def fake_send(message) -> None:
        sent.append(Message(message) if isinstance(message, str) else message)

    return await coordinator.start(
        group_id=GROUP_ID, user_id=user_id, nickname="群友", send=fake_send
    )


async def _submit(
    coordinator: FillBlankCoordinator,
    user_id: str,
    text: str,
    *,
    nickname: str = "路人",
) -> bool:
    return await coordinator.answer(GROUP_ID, user_id, nickname, text)


async def _cancel_task(coordinator: FillBlankCoordinator) -> None:
    pending = coordinator.pending.get(GROUP_ID)
    if pending is None or pending.task is None:
        return
    await asyncio.sleep(0)
    pending.task.cancel()
    with suppress(asyncio.CancelledError):
        await pending.task


# ---------------------------------------------------------------- 题库与打分


def test_bundled_fill_blank_library_is_valid() -> None:
    library = FillBlankLibrary(BUNDLED_FILL_BLANK_PATH)
    raw = json.loads(BUNDLED_FILL_BLANK_PATH.read_text(encoding="utf-8"))["questions"]

    assert library.questions
    assert len(library.questions) == len(raw)
    assert len(library.questions) >= 40
    ids = [question.question_id for question in library.questions]
    assert len(ids) == len(set(ids))
    for question in library.questions:
        assert question.sentence.count("____") == 1
        assert question.initials.isascii() and question.initials.isalpha()
        assert len(question.initials) == len(question.answer)
        assert question.answer
        assert question.source


def test_fill_blank_library_skips_invalid_questions(tmp_path: Path) -> None:
    library = _library(
        tmp_path,
        [
            QUESTIONS[0],
            "不是对象",
            {**QUESTIONS[0], "id": ""},
            {**QUESTIONS[0], "id": "no-blank", "sentence": "没有横线"},
            {**QUESTIONS[0], "id": "two-blanks", "sentence": "____和____"},
            {**QUESTIONS[0], "id": "bad-initials", "initials": "dc1"},
            {**QUESTIONS[0], "id": "length-mismatch", "initials": "dc"},
            {**QUESTIONS[0], "id": "dachuhuo"},
        ],
    )

    assert [question.question_id for question in library.questions] == ["dachuhuo"]


def test_fill_blank_defaults() -> None:
    config = Config()
    assert config.mantou_affection_fill_blank_chance == 0.0005
    assert config.mantou_affection_fill_blank_timeout == 60


def test_fill_blank_timeout_comes_from_config(tmp_path: Path) -> None:
    config = _config(mantou_affection_fill_blank_timeout=15)
    coordinator = FillBlankCoordinator(
        _service(tmp_path, config), config, _library(tmp_path)
    )

    assert coordinator.question_timeout == 15


def test_normalize_answer_strips_punctuation_and_prefix() -> None:
    assert normalize_answer(" 大出货！") == "大出货"
    assert normalize_answer("答：大出货") == "大出货"
    assert normalize_answer("答案是 大出货。") == "大出货"
    assert normalize_answer("「大出货」") == "大出货"


def test_matched_characters_uses_subsequence() -> None:
    assert matched_characters("大出货", "大出货") == 3
    assert matched_characters("大出获", "大出货") == 2
    assert matched_characters("出货", "大出货") == 2
    assert matched_characters("货出", "大出货") == 1
    assert matched_characters("哈哈", "大出货") == 0


def test_score_answer_exact_partial_wrong() -> None:
    assert score_answer("大出货", "大出货") == (5, 3)
    assert score_answer("大出获", "大出货") == (2, 2)
    assert score_answer("出货", "大出货") == (2, 2)
    assert score_answer("哈哈", "大出货") == (-3, 0)


def test_looks_like_answer_filters_noise() -> None:
    assert looks_like_answer("大出获", 3) is True
    assert looks_like_answer(" 大出获！", 3) is True
    assert looks_like_answer("/馒头帮助", 3) is False
    assert looks_like_answer("这是一句特别长的闲聊内容", 3) is False
    assert looks_like_answer("123", 3) is False
    assert looks_like_answer("https://example.com", 3) is False
    assert looks_like_answer("😂😂", 3) is False


# ---------------------------------------------------------------- 开局与作答


async def test_fill_blank_start_asks_everyone(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    coordinator.question_timeout = 60
    sent: list[Message] = []

    assert await _start(coordinator, sent) is True
    pending = coordinator.pending[GROUP_ID]
    assert pending.target[0] == TARGET
    assert _at_ids(sent[0]) == [TARGET]
    body = sent[0].extract_plain_text()
    assert FILL_BLANK_PREFIX in body
    assert pending.question.sentence in body
    assert f"拼音首字母：{pending.question.initials}（{len(pending.question.answer)} 个字）" in body
    assert "大家都可以回答" in body
    assert "60 秒" in body
    assert "答对 +5" in body
    assert "答错 -3" in body
    assert "答对其中 1 个字 +1" in body
    assert "整局没答 -2" in body
    await _cancel_task(coordinator)


async def test_fill_blank_start_returns_false_when_busy_or_empty(
    tmp_path: Path,
) -> None:
    events = EventCoordinator(
        _service(tmp_path, _config()), _config(), library=None
    )
    coordinator = _coordinator(tmp_path, events=events)
    sent: list[Message] = []

    assert await _start(coordinator, sent) is True
    assert await _start(coordinator, sent) is False
    assert len(sent) == 1
    await _cancel_task(coordinator)

    empty_config = _config()
    empty = FillBlankCoordinator(
        _service(tmp_path, empty_config),
        empty_config,
        _library(tmp_path, []),
    )
    assert await _start(empty, sent) is False
    assert empty.pending == {}


async def test_fill_blank_answers_are_first_only_and_others_can_join(
    tmp_path: Path,
) -> None:
    coordinator = _coordinator(tmp_path)
    sent: list[Message] = []
    assert await _start(coordinator, sent) is True

    assert await _submit(coordinator, TARGET, "大出获") is True
    assert await _submit(coordinator, TARGET, "大出货") is False
    assert await _submit(coordinator, OTHER, "大出货") is True
    assert await _submit(coordinator, OTHER_B, "哈哈") is True

    pending = coordinator.pending[GROUP_ID]
    assert set(pending.answers) == {TARGET, OTHER, OTHER_B}
    await _cancel_task(coordinator)


async def test_fill_blank_settles_exact_partial_wrong_and_timeout(
    tmp_path: Path,
) -> None:
    coordinator = _coordinator(tmp_path, questions=[QUESTIONS[0]])
    sent: list[Message] = []
    assert await _start(coordinator, sent) is True
    pending = coordinator.pending[GROUP_ID]

    assert await _submit(coordinator, TARGET, "大出货") is True
    assert await _submit(coordinator, OTHER, "大出获") is True

    await asyncio.wait_for(pending.task, 1)
    result = sent[-1]
    lines = _result_lines(result)
    assert "答案：大出货" in result.extract_plain_text()
    assert "+5" in lines[TARGET]
    assert "全对" in lines[TARGET]
    assert "+2" in lines[OTHER]
    assert "答对 2 个字" in lines[OTHER]
    assert OTHER_B not in lines
    assert (await coordinator.service.profile(GROUP_ID, TARGET)).affection == 5
    assert (await coordinator.service.profile(GROUP_ID, OTHER)).affection == 2


async def test_fill_blank_wrong_answer_and_target_timeout_penalty(
    tmp_path: Path,
) -> None:
    coordinator = _coordinator(tmp_path, questions=[QUESTIONS[0]])
    await _give_affection(coordinator.service, 50, OTHER)
    await _give_affection(coordinator.service, 50, TARGET)
    sent: list[Message] = []
    assert await _start(coordinator, sent) is True
    pending = coordinator.pending[GROUP_ID]

    assert await _submit(coordinator, OTHER, "哈哈哈") is True
    await asyncio.wait_for(pending.task, 1)

    lines = _result_lines(sent[-1])
    assert "-3" in lines[OTHER]
    assert "没答对" in lines[OTHER]
    assert "-2" in lines[TARGET]
    assert (await coordinator.service.profile(GROUP_ID, OTHER)).affection == 47
    assert (await coordinator.service.profile(GROUP_ID, TARGET)).affection == 48


async def test_fill_blank_ignores_commands_and_long_chat(
    tmp_path: Path,
) -> None:
    coordinator = _coordinator(tmp_path)
    sent: list[Message] = []
    assert await _start(coordinator, sent) is True

    assert await _submit(coordinator, TARGET, "/馒头帮助") is False
    assert await _submit(coordinator, OTHER, "这是一句特别长的闲聊内容") is False
    assert coordinator.pending[GROUP_ID].answers == {}
    await _cancel_task(coordinator)


async def test_fill_blank_answer_after_settlement_is_ignored(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    sent: list[Message] = []
    assert await _start(coordinator, sent) is True
    pending = coordinator.pending[GROUP_ID]
    await asyncio.wait_for(pending.task, 1)

    assert await _submit(coordinator, OTHER, "大出货") is False


class FixedRng:
    """固定 random() 返回值，用来精确控制概率判定。"""

    def __init__(self, value: float):
        self.value = value

    def random(self) -> float:
        return self.value


async def test_fill_blank_triggered_uses_configured_probability(
    tmp_path: Path,
) -> None:
    service = AffectionService(AffectionStore(tmp_path / "affection.json"), Config())
    service.rng = FixedRng(0.00049)
    assert await service.fill_blank_triggered() is True
    service.rng = FixedRng(0.0005)
    assert await service.fill_blank_triggered() is False


async def test_fill_blank_probability_command_reports_and_sets(
    tmp_path: Path, monkeypatch
) -> None:
    config = _config()
    service = _service(tmp_path, config)
    commands = register_commands(service, config)
    assert len(commands) == 17
    command = commands[16]
    sent: list[str] = []

    async def fake_finish(message: Message | str = "", **kwargs) -> None:
        sent.append(str(message))

    monkeypatch.setattr(command, "finish", fake_finish)

    await command.handlers[0].call(Message(""))
    assert "当前补句问答概率：0.05%（配置默认 0.05%）" in sent[0]

    await command.handlers[0].call(Message("2.5%"))
    assert "补句问答概率已调整为 2.5%" in sent[1]
    assert await service.fill_blank_probability() == 0.025

    await command.handlers[0].call(Message(""))
    assert "运行时覆盖，配置默认 0.05%" in sent[2]

    await command.handlers[0].call(Message("abc"))
    assert "0~1" in sent[-1]
