import asyncio
import json
import random
from contextlib import suppress
from pathlib import Path
from time import time

import pytest
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message
from nonebot.adapters.onebot.v11.event import Sender

from nonebot_plugin_mantou_affection.ambient import EventCoordinator, register_ambient
from nonebot_plugin_mantou_affection.bet import (
    BET_IDENTICAL_PENALTY,
    BUNDLED_ROUNDS_PATH,
    BetCoordinator,
    BetLibrary,
    parse_bet_answer,
    similarity,
)
from nonebot_plugin_mantou_affection.commands import register_commands
from nonebot_plugin_mantou_affection.config import Config
from nonebot_plugin_mantou_affection.copywriting import AffectionTextLibrary
from nonebot_plugin_mantou_affection.findchar import (
    BUNDLED_PUZZLES_PATH,
    FindCharCoordinator,
    FindCharLibrary,
)
from nonebot_plugin_mantou_affection.models import Profile
from nonebot_plugin_mantou_affection.service import AffectionService
from nonebot_plugin_mantou_affection.storage import AffectionStore

GROUP_ID = "92001"
TRIGGER_ID = "92002"
RIVAL_A = "92003"
RIVAL_B = "92004"
OTHER_ID = "92005"
OTHER_B = "92006"
TARGET = (3, 1, 5, 2, 4)
ROUND = {
    "target": [3, 1, 5, 2, 4],
    "opening": "馒头把五张纸片摊开，说今天玩点刺激的。",
    "reveal": "馒头转过身去，纸片在爪子里响了一阵。",
    "settle": "馒头把纸片翻回来，歪着头看大家的表情。",
}
CANDIDATES = [(int(TRIGGER_ID), "桃友"), (int(RIVAL_A), "甲"), (int(RIVAL_B), "乙")]


class FixedRng(random.Random):
    """固定 random() 返回值，用来精确控制触发判定。"""

    def __init__(self, value: float):
        super().__init__(0)
        self.value = value

    def random(self) -> float:
        return self.value


def _event(
    text: str, user_id: int = 92002, group_id: int = 92001, nickname: str = "桃友"
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


def _bad_round(**overrides) -> dict:
    """造一条只改个别字段的题目，用来验证坏题会被跳过。"""

    round_ = dict(ROUND)
    round_.update(overrides)
    return round_


def _library(tmp_path: Path, rounds: list[dict] | None = None) -> BetLibrary:
    path = tmp_path / "bet_rounds.json"
    path.write_text(json.dumps(rounds if rounds is not None else [ROUND]), encoding="utf-8")
    return BetLibrary(path)


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


def _bet(
    tmp_path: Path,
    config: Config | None = None,
    rng: random.Random | None = None,
    rounds: list[dict] | None = None,
    events: EventCoordinator | None = None,
) -> tuple[BetCoordinator, AffectionService]:
    config = config or Config()
    service = _service(tmp_path, config, rng=rng)
    coordinator = BetCoordinator(
        service,
        config,
        _library(tmp_path, rounds),
        events=events,
        rng=rng or random.Random(0),
    )
    return coordinator, service


async def _give_affection(
    service: AffectionService, score: int, user_id: str, group_id: str = GROUP_ID
) -> None:
    def bump(profile: Profile) -> None:
        profile.affection = score

    await service.store.update_profile(group_id, user_id, "桃友", bump)


async def _ensure_affection(
    service: AffectionService, user_id: str, score: int = 10
) -> None:
    """候选人要有非 0 好感度才会被抽成对手,开局前先垫一点。"""

    if (await service.profile(GROUP_ID, user_id)).affection == 0:
        await _give_affection(service, score, user_id)


async def _start(
    coordinator: BetCoordinator,
    *,
    sent: list[Message] | None = None,
    candidates: list | None = None,
    user_id: str = TRIGGER_ID,
) -> bool:
    async def fake_send(message: Message) -> None:
        if sent is not None:
            sent.append(message)

    for candidate in CANDIDATES if candidates is None else candidates:
        try:
            member_id, _name = candidate
        except (TypeError, ValueError):
            continue
        if str(member_id) != str(user_id):
            await _ensure_affection(coordinator.service, str(member_id))
    return await coordinator.maybe_start(
        group_id=GROUP_ID,
        user_id=user_id,
        nickname="桃友",
        candidates=CANDIDATES if candidates is None else candidates,
        send=fake_send,
    )


async def _settle(coordinator: BetCoordinator, timeout: float = 0.01) -> list[Message]:
    pending = coordinator.pending[GROUP_ID]
    sent: list[Message] = []

    async def fake_send(message: Message) -> None:
        sent.append(message)

    task = coordinator.schedule(pending, group_id=GROUP_ID, send=fake_send, timeout=timeout)
    await asyncio.wait_for(task, 2)
    return sent


async def _cancel_task(coordinator: BetCoordinator, group_id: str = GROUP_ID) -> None:
    pending = coordinator.pending.get(group_id)
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


def _result_lines(message: Message) -> dict[str, str]:
    """按行首 @ 到的人收集结算行，抽签顺序随机也能稳定断言。

    并列获胜的行里还会 @ 出其它赢家，这里把行内 @ 记成 @qq 文本，行首 @ 才是这行的主人。
    """

    lines: dict[str, str] = {}
    owner: str | None = None
    buffer: list[str] = []

    def flush() -> None:
        nonlocal owner, buffer
        if owner is not None:
            lines[owner] = "".join(buffer).strip()
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


# ------------------------------------------------------------ 作答解析


@pytest.mark.parametrize(
    "text",
    [
        "31524",
        "3 1 5 2 4",
        "3、1、5、2、4",
        "3，1，5，2，4",
        "3；1；5；2；4",
        "3|1|5|2|4",
        "3/1/5/2/4",
        "3-1-5-2-4",
        "3->1->5->2->4",
        "3→1→5→2→4",
        "3＞1＞5＞2＞4",
        " 3 1 5 2 4 ",
        "３１５２４",
        "三一五二四",
        "三 1 五 2 四",
    ],
)
def test_parse_bet_answer_accepts_permutations(text: str) -> None:
    assert parse_bet_answer(text) == TARGET


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "3152",
        "315246",
        "31544",
        "36142",
        "abc",
        "3 1 5 2 6",
        "我猜 3 1 5 2 4",
        "六 1 5 2 4",
        "3152 46",
    ],
)
def test_parse_bet_answer_ignores_other_text(text: str) -> None:
    assert parse_bet_answer(text) is None


def test_similarity_counts_matching_positions() -> None:
    assert similarity((3, 1, 5, 2, 4), TARGET) == 5
    assert similarity((1, 1, 5, 2, 4), TARGET) == 4
    assert similarity((5, 4, 2, 1, 3), TARGET) == 0


# ------------------------------------------------------------ 题库加载


def test_bundled_rounds_are_valid() -> None:
    raw = json.loads(BUNDLED_ROUNDS_PATH.read_text(encoding="utf-8"))
    library = BetLibrary(BUNDLED_ROUNDS_PATH)

    assert isinstance(raw, list)
    assert library.rounds
    assert len(library.rounds) == len(raw)
    for round_ in library.rounds:
        assert sorted(round_.target) == [1, 2, 3, 4, 5]
        for text in (round_.opening, round_.reveal, round_.settle):
            assert text
            assert "\n" not in text
            assert not any(char.isdigit() for char in text)


def test_bet_library_skips_invalid_rounds(tmp_path: Path) -> None:
    library = _library(
        tmp_path,
        [
            ROUND,
            "不是对象",
            _bad_round(target=[1, 2, 3, 4]),
            _bad_round(target=[1, 2, 3, 4, 6]),
            {"target": [1, 2, 3, 4, 5], "opening": "甲甲甲甲", "reveal": "乙乙乙乙"},
            _bad_round(opening=""),
            _bad_round(opening="有\n换行"),
            _bad_round(opening="带数字 1 的句子"),
            _bad_round(opening="短"),
            _bad_round(opening="长" * 200),
        ],
    )

    assert [round_.target for round_ in library.rounds] == [TARGET]


def test_bet_library_accepts_object_root_and_empty(tmp_path: Path) -> None:
    path = tmp_path / "wrapped.json"
    path.write_text(json.dumps({"rounds": [ROUND]}, ensure_ascii=False), encoding="utf-8")
    assert len(BetLibrary(path).rounds) == 1

    path.write_text("[]", encoding="utf-8")
    library = BetLibrary(path)
    assert library.rounds == ()
    assert library.pick() is None


def test_bet_library_rejects_broken_root_and_missing_file(tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text('{"rounds": "不是数组"}', encoding="utf-8")
    assert BetLibrary(path).rounds == ()

    path.write_text("{", encoding="utf-8")
    assert BetLibrary(path).rounds == ()

    assert BetLibrary(tmp_path / "missing.json").rounds == ()


def test_bet_defaults() -> None:
    config = Config()
    assert config.mantou_affection_bet_chance == 0.005
    assert config.mantou_affection_bet_window == 60


# ------------------------------------------------------------ 开局门控


async def test_maybe_start_needs_send_and_candidates(tmp_path: Path) -> None:
    coordinator, _service = _bet(tmp_path, Config(mantou_affection_bet_chance=1.0))

    assert await coordinator.maybe_start(
        group_id=GROUP_ID, user_id=TRIGGER_ID, nickname="桃友", candidates=CANDIDATES, send=None
    ) is False
    assert coordinator.pending == {}

    assert await coordinator.maybe_start(
        group_id=GROUP_ID, user_id=TRIGGER_ID, nickname="桃友", candidates=None, send=None
    ) is False


async def test_maybe_start_respects_chance(tmp_path: Path) -> None:
    coordinator, _service = _bet(
        tmp_path, Config(mantou_affection_bet_chance=0.005), rng=FixedRng(0.9)
    )

    assert await _start(coordinator) is False
    assert coordinator.pending == {}


async def test_maybe_start_needs_two_rivals(tmp_path: Path) -> None:
    coordinator, _service = _bet(tmp_path, Config(mantou_affection_bet_chance=1.0))

    assert await _start(coordinator, candidates=[(int(TRIGGER_ID), "桃友")]) is False
    assert await _start(coordinator, candidates=[(92006, "丙")]) is False
    assert await _start(coordinator, candidates=[(92006, "丙"), (92007, "丁")]) is True
    assert set(coordinator.pending[GROUP_ID].named_ids) == {TRIGGER_ID, "92006", "92007"}
    await _cancel_task(coordinator)


async def test_maybe_start_blocked_when_group_is_busy(tmp_path: Path) -> None:
    service = _service(tmp_path, Config())
    events = EventCoordinator(service, Config(), None, rng=random.Random(0))
    find_char = FindCharCoordinator(
        service,
        Config(),
        FindCharLibrary(BUNDLED_PUZZLES_PATH),
        events=events,
        rng=random.Random(0),
    )
    coordinator, _bet_service = _bet(
        tmp_path, Config(mantou_affection_bet_chance=1.0), events=events
    )
    events.attach_find_char(find_char)
    events.attach_bet(coordinator)

    game = find_char.start(GROUP_ID, TRIGGER_ID, "桃友")
    assert game is not None
    assert coordinator.busy(GROUP_ID) is True
    assert await _start(coordinator) is False

    find_char.discard(GROUP_ID)
    assert coordinator.busy(GROUP_ID) is False


async def test_maybe_start_sends_opening_without_leaking_target(tmp_path: Path) -> None:
    coordinator, service = _bet(tmp_path, Config(mantou_affection_bet_chance=1.0))
    await _give_affection(service, 30, TRIGGER_ID)
    sent: list[Message] = []

    assert await _start(coordinator, sent=sent) is True

    assert len(sent) == 1
    message = sent[0]
    assert _at_ids(message)[0] == TRIGGER_ID
    assert set(_at_ids(message)[1:]) == {RIVAL_A, RIVAL_B}
    body = message.extract_plain_text()
    assert ROUND["opening"] in body
    assert "馒头摆出了五个数字：1 2 3 4 5" in body
    assert "60 秒后馒头会把这五个数字的顺序打乱" in body
    assert "3 5 1 4 2" in body
    assert f"三人各扣 {BET_IDENTICAL_PENALTY} 好感度" in body
    assert "其它群友也可以回答（不用@）" in body
    assert "奖池平分给赢家，以及比被点名的三个人都更接近的其它群友" in body
    # 目标顺序只在结算时出现,开场消息里不能带
    assert "3 1 5 2 4" not in body
    assert ROUND["reveal"] not in body
    assert ROUND["settle"] not in body
    assert set(coordinator.pending[GROUP_ID].named_ids) == {TRIGGER_ID, RIVAL_A, RIVAL_B}
    await _cancel_task(coordinator)


async def test_maybe_start_picks_distinct_rivals_excluding_trigger(tmp_path: Path) -> None:
    coordinator, _service = _bet(tmp_path, Config(mantou_affection_bet_chance=1.0))
    candidates = [
        (int(TRIGGER_ID), "桃友"),
        (int(RIVAL_A), "甲"),
        (int(RIVAL_A), "甲的小号"),
        (int(RIVAL_B), "乙"),
    ]

    assert await _start(coordinator, candidates=candidates) is True

    pending = coordinator.pending[GROUP_ID]
    assert pending.named_ids[0] == TRIGGER_ID
    assert len(set(pending.named_ids)) == 3
    assert TRIGGER_ID not in pending.named_ids[1:]
    assert set(pending.named_ids[1:]) <= {RIVAL_A, RIVAL_B}
    await _cancel_task(coordinator)


# ------------------------------------------------------------ 作答与三同分支


async def _start_with_pending(
    tmp_path: Path, **config_kwargs
) -> tuple[BetCoordinator, AffectionService]:
    coordinator, service = _bet(
        tmp_path, Config(mantou_affection_bet_chance=1.0, **config_kwargs)
    )
    sent: list[Message] = []
    assert await _start(coordinator, sent=sent) is True
    return coordinator, service


async def test_maybe_start_filters_candidates_without_affection(tmp_path: Path) -> None:
    """好感度为 0 的候选人不会被抽成对手,过滤后不足两人就不开局。"""

    coordinator, service = _bet(tmp_path, Config(mantou_affection_bet_chance=1.0))
    await _give_affection(service, 10, RIVAL_A)
    sent: list[Message] = []

    async def fake_send(message: Message) -> None:
        sent.append(message)

    async def start() -> bool:
        return await coordinator.maybe_start(
            group_id=GROUP_ID,
            user_id=TRIGGER_ID,
            nickname="桃友",
            candidates=[
                (int(TRIGGER_ID), "桃友"),
                (int(RIVAL_A), "甲"),
                (int(RIVAL_B), "乙"),
            ],
            send=fake_send,
        )

    assert await start() is False
    assert sent == []
    assert coordinator.pending == {}

    await _give_affection(service, 3, RIVAL_B)
    assert await start() is True
    assert set(coordinator.pending[GROUP_ID].named_ids) == {TRIGGER_ID, RIVAL_A, RIVAL_B}
    await _cancel_task(coordinator)


async def test_maybe_start_keeps_candidates_with_negative_affection(
    tmp_path: Path, monkeypatch
) -> None:
    """负数好感度也算非 0,照样能当对手。"""

    coordinator, _service = _bet(tmp_path, Config(mantou_affection_bet_chance=1.0))

    async def fake_profile(group_id: str, user_id: str) -> Profile:
        if user_id == RIVAL_A:
            return Profile(user_id, affection=-3)
        return Profile(user_id, affection=5 if user_id == RIVAL_B else 0)

    monkeypatch.setattr(coordinator.service, "profile", fake_profile)
    sent: list[Message] = []

    async def fake_send(message: Message) -> None:
        sent.append(message)

    started = await coordinator.maybe_start(
        group_id=GROUP_ID,
        user_id=TRIGGER_ID,
        nickname="桃友",
        candidates=[
            (int(TRIGGER_ID), "桃友"),
            (int(RIVAL_A), "甲"),
            (int(RIVAL_B), "乙"),
            (int(OTHER_B), "丙"),
        ],
        send=fake_send,
    )

    # RIVAL_A 是负数(保留)、RIVAL_B 有 5 点、丙 是 0(过滤),正好两位对手
    assert started is True
    assert set(coordinator.pending[GROUP_ID].named_ids) == {TRIGGER_ID, RIVAL_A, RIVAL_B}
    await _cancel_task(coordinator)


async def test_answer_keeps_first_submission_only(tmp_path: Path) -> None:
    coordinator, _service = await _start_with_pending(tmp_path)

    assert await coordinator.answer(GROUP_ID, OTHER_ID, "路人", "3 1 5 2 4") is True
    assert await coordinator.answer(GROUP_ID, OTHER_ID, "路人", "1 2 3 4 5") is True
    assert await coordinator.answer(GROUP_ID, OTHER_ID, "路人", "不是答案") is False
    pending = coordinator.pending[GROUP_ID]
    assert list(pending.answers) == [OTHER_ID]
    assert pending.answers[OTHER_ID].digits == TARGET
    assert pending.answers[OTHER_ID].hits == 5
    await _cancel_task(coordinator)


async def test_answer_ignored_when_no_game(tmp_path: Path) -> None:
    coordinator, _service = _bet(tmp_path)

    assert await coordinator.answer(GROUP_ID, OTHER_ID, "路人", "3 1 5 2 4") is False


async def test_identical_answers_end_game_immediately(tmp_path: Path) -> None:
    coordinator, service = await _start_with_pending(tmp_path)
    await _give_affection(service, 20, TRIGGER_ID)
    await _give_affection(service, 20, RIVAL_A)
    await _give_affection(service, 20, RIVAL_B)
    sent: list[Message] = []

    async def fake_send(message: Message) -> None:
        sent.append(message)

    coordinator.pending[GROUP_ID].send = fake_send
    for user_id in (TRIGGER_ID, RIVAL_A, RIVAL_B):
        assert await coordinator.answer(GROUP_ID, user_id, "桃友", "3 1 5 2 4") is True
    # 立即结束:席位释放、等待窗口的任务被取消
    assert coordinator.pending == {}

    assert len(sent) == 1
    lines = _lines(sent[0])
    assert ROUND["settle"] in lines[0]
    assert set(_at_ids(sent[0])) == {TRIGGER_ID, RIVAL_A, RIVAL_B}
    results = _result_lines(sent[0])
    for user_id in (TRIGGER_ID, RIVAL_A, RIVAL_B):
        assert results[user_id].startswith("和另外两人发了一模一样的顺序")
        assert f"好感度 -{BET_IDENTICAL_PENALTY}，当前 15" in results[user_id]
        assert (await service.profile(GROUP_ID, user_id)).affection == 15
    # 目标顺序不提前泄露
    assert "3 1 5 2 4" not in sent[0].extract_plain_text()


async def test_identical_check_waits_for_all_named(tmp_path: Path) -> None:
    coordinator, _service = await _start_with_pending(tmp_path)
    sent: list[Message] = []

    async def fake_send(message: Message) -> None:
        sent.append(message)

    coordinator.pending[GROUP_ID].send = fake_send
    await coordinator.answer(GROUP_ID, TRIGGER_ID, "桃友", "3 1 5 2 4")
    await coordinator.answer(GROUP_ID, RIVAL_A, "甲", "3 1 5 2 4")

    assert sent == []
    assert GROUP_ID in coordinator.pending
    await _cancel_task(coordinator)


async def test_different_answers_do_not_end_game_early(tmp_path: Path) -> None:
    coordinator, _service = await _start_with_pending(tmp_path)

    await coordinator.answer(GROUP_ID, TRIGGER_ID, "桃友", "3 1 5 2 4")
    await coordinator.answer(GROUP_ID, RIVAL_A, "甲", "3 1 5 2 4")
    await coordinator.answer(GROUP_ID, RIVAL_B, "乙", "3 1 5 2 5")

    assert GROUP_ID in coordinator.pending
    await _cancel_task(coordinator)


# ------------------------------------------------------------ 结算


async def test_settlement_transfers_from_losers_to_winner(tmp_path: Path) -> None:
    coordinator, service = await _start_with_pending(tmp_path)
    await _give_affection(service, 100, TRIGGER_ID)
    await _give_affection(service, 100, RIVAL_A)
    await _give_affection(service, 100, RIVAL_B)

    await coordinator.answer(GROUP_ID, RIVAL_A, "甲", "3 1 5 2 4")
    await coordinator.answer(GROUP_ID, RIVAL_B, "乙", "1 2 3 4 5")
    sent = await _settle(coordinator)

    assert len(sent) == 1
    body = sent[0].extract_plain_text()
    assert ROUND["reveal"] in body
    assert "馒头打乱后的顺序：3 1 5 2 4" in body
    assert ROUND["settle"] in body
    results = _result_lines(sent[0])
    assert _at_ids(sent[0])[0] == RIVAL_A
    assert "猜中 5 个位置，赢得 10 点好感度，当前 110" in results[RIVAL_A]
    assert "没提交答案，被扣掉 5 点好感度，当前 95" in results[TRIGGER_ID]
    assert "猜中 0 个位置，被扣掉 5 点好感度，当前 95" in results[RIVAL_B]
    assert (await service.profile(GROUP_ID, RIVAL_A)).affection == 110
    assert (await service.profile(GROUP_ID, RIVAL_B)).affection == 95
    assert (await service.profile(GROUP_ID, TRIGGER_ID)).affection == 95
    assert coordinator.pending == {}


async def test_settlement_ties_make_all_top_scorers_winners(tmp_path: Path) -> None:
    """并列最高分时两人都算赢家,奖池平分,赢家行互相 @ 出并列的人。"""

    coordinator, service = await _start_with_pending(tmp_path)
    await _give_affection(service, 40, TRIGGER_ID)
    await _give_affection(service, 40, RIVAL_A)
    await _give_affection(service, 40, RIVAL_B)

    await coordinator.answer(GROUP_ID, RIVAL_B, "乙", "3 1 5 2 4")
    await coordinator.answer(GROUP_ID, RIVAL_A, "甲", "3 1 5 2 4")
    await coordinator.answer(GROUP_ID, TRIGGER_ID, "桃友", "3 1 5 4 2")
    sent = await _settle(coordinator)

    results = _result_lines(sent[0])
    assert "并列获胜，分得 1 点好感度，当前 41" in results[RIVAL_A]
    assert "并列获胜，分得 1 点好感度，当前 41" in results[RIVAL_B]
    assert "猜中 3 个位置，被扣掉 2 点好感度，当前 38" in results[TRIGGER_ID]
    assert set(results) == {TRIGGER_ID, RIVAL_A, RIVAL_B}
    assert f"@{RIVAL_A}" in results[RIVAL_B]
    assert f"@{RIVAL_B}" in results[RIVAL_A]
    assert (await service.profile(GROUP_ID, RIVAL_A)).affection == 41
    assert (await service.profile(GROUP_ID, RIVAL_B)).affection == 41
    assert (await service.profile(GROUP_ID, TRIGGER_ID)).affection == 38


async def test_settlement_pool_remainder_goes_to_earliest_winner(tmp_path: Path) -> None:
    """奖池除不尽时余数归最早提交的赢家,总额守恒。"""

    coordinator, service = await _start_with_pending(tmp_path)
    await _give_affection(service, 30, TRIGGER_ID)
    await _give_affection(service, 40, RIVAL_A)
    await _give_affection(service, 40, RIVAL_B)

    await coordinator.answer(GROUP_ID, RIVAL_A, "甲", "3 1 5 2 4")
    await coordinator.answer(GROUP_ID, RIVAL_B, "乙", "3 1 5 2 4")
    await coordinator.answer(GROUP_ID, TRIGGER_ID, "桃友", "3 1 5 4 2")
    sent = await _settle(coordinator)

    results = _result_lines(sent[0])
    assert "并列获胜，分得 1 点好感度，当前 41" in results[RIVAL_A]
    assert "并列获胜，这次没有分到好感度，当前 40" in results[RIVAL_B]
    assert "被扣掉 1 点好感度，当前 29" in results[TRIGGER_ID]
    affections = [
        (await service.profile(GROUP_ID, user_id)).affection
        for user_id in (TRIGGER_ID, RIVAL_A, RIVAL_B)
    ]
    assert affections == [29, 41, 40]
    assert sum(affections) == 110


async def test_settlement_three_way_tie_without_losers(tmp_path: Path) -> None:
    """三人用不同排列拿到同样的最高分:三人都是赢家,没有输家可扣。"""

    coordinator, service = await _start_with_pending(tmp_path)
    await _give_affection(service, 40, TRIGGER_ID)
    await _give_affection(service, 40, RIVAL_A)
    await _give_affection(service, 40, RIVAL_B)

    await coordinator.answer(GROUP_ID, RIVAL_A, "甲", "3 1 5 4 2")
    await coordinator.answer(GROUP_ID, RIVAL_B, "乙", "1 3 5 2 4")
    await coordinator.answer(GROUP_ID, TRIGGER_ID, "桃友", "5 1 3 2 4")
    sent = await _settle(coordinator)

    results = _result_lines(sent[0])
    for user_id, partners in (
        (TRIGGER_ID, (RIVAL_A, RIVAL_B)),
        (RIVAL_A, (RIVAL_B, TRIGGER_ID)),
        (RIVAL_B, (TRIGGER_ID, RIVAL_A)),
    ):
        assert "猜中 3 个位置，与 " in results[user_id]
        assert "并列获胜，其它人没有可转让的好感度，当前 40" in results[user_id]
        for partner in partners:
            assert f"@{partner}" in results[user_id]
        assert (await service.profile(GROUP_ID, user_id)).affection == 40
    # 行首 @ 之外,每个人的赢家行还会 @ 出另外两位并列的人
    assert _at_ids(sent[0]).count(RIVAL_A) == 3
    assert _at_ids(sent[0]).count(RIVAL_B) == 3
    assert _at_ids(sent[0]).count(TRIGGER_ID) == 3


async def test_settlement_handles_empty_affection_losers(tmp_path: Path) -> None:
    coordinator, service = await _start_with_pending(tmp_path)
    await _give_affection(service, 0, TRIGGER_ID)
    await _give_affection(service, 0, RIVAL_B)
    await _give_affection(service, 10, RIVAL_A)

    await coordinator.answer(GROUP_ID, RIVAL_A, "甲", "3 1 5 2 4")
    sent = await _settle(coordinator)

    results = _result_lines(sent[0])
    assert "猜中 5 个位置，其它人没有可转让的好感度，当前 10" in results[RIVAL_A]
    assert (await service.profile(GROUP_ID, RIVAL_A)).affection == 10
    assert (await service.profile(GROUP_ID, TRIGGER_ID)).affection == 0


async def test_settlement_outsider_without_beating_named_gets_nothing(
    tmp_path: Path,
) -> None:
    coordinator, service = await _start_with_pending(tmp_path)
    await _give_affection(service, 50, TRIGGER_ID)
    await _give_affection(service, 50, RIVAL_A)
    await _give_affection(service, 50, RIVAL_B)
    await _give_affection(service, 10, OTHER_ID)

    await coordinator.answer(GROUP_ID, RIVAL_A, "甲", "3 1 5 2 4")
    await coordinator.answer(GROUP_ID, RIVAL_B, "乙", "3 1 5 2 4")
    await coordinator.answer(GROUP_ID, OTHER_ID, "路人", "3 1 5 4 2")
    sent = await _settle(coordinator)

    results = _result_lines(sent[0])
    assert "猜中 3 个位置，没有超过被点名的人，本次不加不减" in results[OTHER_ID]
    assert (await service.profile(GROUP_ID, OTHER_ID)).affection == 10


async def test_settlement_outsider_shares_pool_with_winners(tmp_path: Path) -> None:
    """围观者比被点名的人更接近时,和赢家一起分奖池,余数给最早提交的人。"""

    coordinator, service = await _start_with_pending(tmp_path)
    await _give_affection(service, 100, TRIGGER_ID)
    await _give_affection(service, 100, RIVAL_A)
    await _give_affection(service, 100, RIVAL_B)
    await _give_affection(service, 10, OTHER_ID)

    await coordinator.answer(GROUP_ID, RIVAL_A, "甲", "3 1 5 4 2")
    await coordinator.answer(GROUP_ID, RIVAL_B, "乙", "1 3 5 2 4")
    await coordinator.answer(GROUP_ID, TRIGGER_ID, "桃友", "1 2 3 4 5")
    await coordinator.answer(GROUP_ID, OTHER_ID, "路人", "3 1 5 2 4")
    sent = await _settle(coordinator)

    results = _result_lines(sent[0])
    assert "并列获胜，分得 3 点好感度，当前 103" in results[RIVAL_A]
    assert "并列获胜，分得 1 点好感度，当前 101" in results[RIVAL_B]
    assert "比被点名的人更接近，从奖池分得 1 点好感度，当前 11" in results[OTHER_ID]
    assert "被扣掉 5 点好感度，当前 95" in results[TRIGGER_ID]
    affections = [
        (await service.profile(GROUP_ID, user_id)).affection
        for user_id in (TRIGGER_ID, RIVAL_A, RIVAL_B, OTHER_ID)
    ]
    assert affections == [95, 103, 101, 11]
    assert sum(affections) == 310


async def test_settlement_remainder_goes_to_earliest_outsider(tmp_path: Path) -> None:
    """最早提交的人即使是围观群友,也能拿到除不尽的余数。"""

    coordinator, service = await _start_with_pending(tmp_path)
    await _give_affection(service, 100, TRIGGER_ID)
    await _give_affection(service, 100, RIVAL_A)
    await _give_affection(service, 100, RIVAL_B)
    await _give_affection(service, 10, OTHER_ID)

    await coordinator.answer(GROUP_ID, OTHER_ID, "路人", "3 1 5 2 4")
    await coordinator.answer(GROUP_ID, RIVAL_A, "甲", "3 1 5 4 2")
    await coordinator.answer(GROUP_ID, RIVAL_B, "乙", "1 3 5 2 4")
    await coordinator.answer(GROUP_ID, TRIGGER_ID, "桃友", "1 2 3 4 5")
    sent = await _settle(coordinator)

    results = _result_lines(sent[0])
    assert "从奖池分得 3 点好感度，当前 13" in results[OTHER_ID]
    assert "并列获胜，分得 1 点好感度，当前 101" in results[RIVAL_A]
    assert "并列获胜，分得 1 点好感度，当前 101" in results[RIVAL_B]
    assert (await service.profile(GROUP_ID, OTHER_ID)).affection == 13


async def test_settlement_multiple_outsiders_share_pool(tmp_path: Path) -> None:
    coordinator, service = await _start_with_pending(tmp_path)
    await _give_affection(service, 100, TRIGGER_ID)
    await _give_affection(service, 100, RIVAL_A)
    await _give_affection(service, 100, RIVAL_B)
    await _give_affection(service, 10, OTHER_ID)
    await _give_affection(service, 10, OTHER_B)

    await coordinator.answer(GROUP_ID, RIVAL_A, "甲", "3 1 5 4 2")
    await coordinator.answer(GROUP_ID, RIVAL_B, "乙", "1 3 5 2 4")
    await coordinator.answer(GROUP_ID, TRIGGER_ID, "桃友", "1 2 3 4 5")
    await coordinator.answer(GROUP_ID, OTHER_ID, "路人甲", "3 1 5 2 4")
    await coordinator.answer(GROUP_ID, OTHER_B, "路人乙", "3 1 5 2 4")
    sent = await _settle(coordinator)

    results = _result_lines(sent[0])
    assert "并列获胜，分得 2 点好感度，当前 102" in results[RIVAL_A]
    assert "并列获胜，分得 1 点好感度，当前 101" in results[RIVAL_B]
    assert "从奖池分得 1 点好感度，当前 11" in results[OTHER_ID]
    assert "从奖池分得 1 点好感度，当前 11" in results[OTHER_B]
    affections = [
        (await service.profile(GROUP_ID, user_id)).affection
        for user_id in (TRIGGER_ID, RIVAL_A, RIVAL_B, OTHER_ID, OTHER_B)
    ]
    assert affections == [95, 102, 101, 11, 11]


async def test_settlement_empty_pool_tells_outsider_so(tmp_path: Path) -> None:
    coordinator, service = await _start_with_pending(tmp_path)
    await _give_affection(service, 0, TRIGGER_ID)
    await _give_affection(service, 100, RIVAL_A)
    await _give_affection(service, 0, RIVAL_B)
    await _give_affection(service, 10, OTHER_ID)

    await coordinator.answer(GROUP_ID, RIVAL_A, "甲", "3 1 5 4 2")
    await coordinator.answer(GROUP_ID, OTHER_ID, "路人", "3 1 5 2 4")
    sent = await _settle(coordinator)

    results = _result_lines(sent[0])
    assert "猜中 3 个位置，其它人没有可转让的好感度，当前 100" in results[RIVAL_A]
    assert "比被点名的人更接近，但奖池里没有可转让的好感度，当前 10" in results[OTHER_ID]
    assert (await service.profile(GROUP_ID, OTHER_ID)).affection == 10


async def test_settlement_without_any_named_answer(tmp_path: Path) -> None:
    coordinator, service = await _start_with_pending(tmp_path)
    await _give_affection(service, 50, TRIGGER_ID)
    await _give_affection(service, 10, OTHER_ID)
    await coordinator.answer(GROUP_ID, OTHER_ID, "路人", "3 1 5 2 4")
    sent = await _settle(coordinator)

    body = sent[0].extract_plain_text()
    assert "馒头打乱后的顺序：3 1 5 2 4" in body
    assert "被点名的三个人一个都没提交" in body
    assert (await service.profile(GROUP_ID, TRIGGER_ID)).affection == 50
    assert (await service.profile(GROUP_ID, OTHER_ID)).affection == 10


async def test_settlement_counts_silent_named_as_losers(tmp_path: Path) -> None:
    coordinator, service = await _start_with_pending(tmp_path)
    await _give_affection(service, 20, TRIGGER_ID)
    await _give_affection(service, 20, RIVAL_A)
    await _give_affection(service, 0, RIVAL_B)
    await coordinator.answer(GROUP_ID, TRIGGER_ID, "桃友", "3 1 5 2 4")

    sent = await _settle(coordinator)

    results = _result_lines(sent[0])
    assert "猜中 5 个位置，赢得 1 点好感度，当前 21" in results[TRIGGER_ID]
    assert "没提交答案，被扣掉 1 点好感度，当前 19" in results[RIVAL_A]
    assert "没提交答案，好感度已经见底，没有可转让的，当前 0" in results[RIVAL_B]
    assert (await service.profile(GROUP_ID, TRIGGER_ID)).affection == 21
    assert (await service.profile(GROUP_ID, RIVAL_A)).affection == 19


# ------------------------------------------------------------ 概率读取与命令


async def test_bet_probability_prefers_stored_override(tmp_path: Path) -> None:
    service = _service(tmp_path)
    assert await service.bet_probability() == pytest.approx(0.005)
    assert await service.bet_probability_override() is None

    await service.set_bet_probability(0.02)
    assert await service.bet_probability() == pytest.approx(0.02)
    assert await service.bet_probability_override() == pytest.approx(0.02)

    restarted = AffectionService(
        AffectionStore(tmp_path / "affection.json"), Config(), rng=random.Random(0)
    )
    assert await restarted.bet_probability() == pytest.approx(0.02)


async def test_bet_probability_ignores_broken_setting(tmp_path: Path) -> None:
    service = _service(tmp_path, Config(mantou_affection_bet_chance=0.01))
    await service.store.set_setting("bet_probability", "not a number")

    assert await service.bet_probability() == pytest.approx(0.01)
    assert await service.bet_probability_override() is None


def _bet_command(tmp_path: Path, monkeypatch) -> tuple:
    service = _service(tmp_path)
    command = register_commands(service, Config())[8]
    sent: list[str] = []

    async def fake_finish(message: Message | str = "", **kwargs) -> None:
        sent.append(str(message))

    monkeypatch.setattr(command, "finish", fake_finish)
    return service, command, sent


async def test_bet_command_reports_default(tmp_path: Path, monkeypatch) -> None:
    _service, command, sent = _bet_command(tmp_path, monkeypatch)

    await command.handlers[0].call(Message(""))

    assert "当前馒头博弈概率：0.5%" in sent[0]
    assert "配置默认" in sent[0]
    assert "0 即关闭" in sent[0]


async def test_bet_command_reports_override_and_sets(tmp_path: Path, monkeypatch) -> None:
    service, command, sent = _bet_command(tmp_path, monkeypatch)

    await command.handlers[0].call(Message("1%"))
    assert "馒头博弈概率已调整为 1%" in sent[0]
    assert await service.bet_probability() == pytest.approx(0.01)

    await command.handlers[0].call(Message(""))
    assert "当前馒头博弈概率：1%（运行时覆盖" in sent[1]

    await command.handlers[0].call(Message("0.5%"))
    assert await service.bet_probability() == pytest.approx(0.005)


async def test_bet_command_rejects_bad_input(tmp_path: Path, monkeypatch) -> None:
    service, command, sent = _bet_command(tmp_path, monkeypatch)

    await command.handlers[0].call(Message("abc"))

    assert "0 即关闭馒头博弈" in sent[0]
    assert await service.bet_probability_override() is None


async def test_bet_command_can_disable(tmp_path: Path, monkeypatch) -> None:
    service, command, _sent = _bet_command(tmp_path, monkeypatch)

    await command.handlers[0].call(Message("0"))

    assert await service.bet_probability() == 0.0


# ------------------------------------------------------------ 端到端 wiring


async def test_plugin_wiring_runs_bet_end_to_end(monkeypatch) -> None:
    from nonebot_plugin_mantou_affection import (
        add_affection,
        answer_matcher,
        bet_coordinator,
        maybe_start_bet,
        plugin_config,
    )

    group_id = "92091"
    trigger_id = "92092"
    rival_id = "92093"
    other_id = "92094"
    monkeypatch.setattr(plugin_config, "mantou_affection_bet_chance", 1.0)
    monkeypatch.setattr(plugin_config, "mantou_affection_bet_window", 0.05)
    await add_affection(group_id, trigger_id, 3, source="test:bet-trigger")
    await add_affection(group_id, rival_id, 3, source="test:bet-rival")
    await add_affection(group_id, other_id, 3, source="test:bet-other")
    sent: list[Message] = []

    async def fake_send(message) -> None:
        sent.append(Message(message) if isinstance(message, str) else message)

    started = await maybe_start_bet(
        group_id,
        trigger_id,
        nickname="桃友",
        candidates=[(int(trigger_id), "桃友"), (int(rival_id), "甲"), (int(other_id), "乙")],
        send=fake_send,
    )

    assert started is True
    assert len(sent) == 1
    opening = sent[0]
    assert _at_ids(opening)[0] == trigger_id
    pending = bet_coordinator.pending[group_id]
    assert pending.all_named_submitted() is False

    target = pending.round.target
    guess = " ".join(str(digit) for digit in target)
    await answer_matcher.handlers[0].call(
        _event(guess, user_id=int(rival_id), group_id=int(group_id))
    )
    assert pending.answers[rival_id].hits == 5

    await asyncio.wait_for(pending.task, 3)

    assert len(sent) == 2
    settle = sent[1].extract_plain_text()
    assert f"馒头打乱后的顺序：{guess}" in settle
    assert "猜中 5 个位置，赢得" in settle
    assert bet_coordinator.pending == {}


async def test_plugin_wiring_bet_returns_false_without_send(monkeypatch) -> None:
    from nonebot_plugin_mantou_affection import maybe_start_bet

    assert (
        await maybe_start_bet(
            "92095", "92096", nickname="桃友", candidates=[(1, "甲"), (2, "乙")], send=None
        )
        is False
    )


async def test_answer_router_sends_bet_answers_to_bet(tmp_path: Path) -> None:
    config = Config(mantou_affection_bet_chance=1.0, mantou_affection_ambient_probability=0.0)
    service = _service(tmp_path, config, text_library=AffectionTextLibrary(_bet_texts(tmp_path)))
    events = EventCoordinator(service, config, None, rng=random.Random(0))
    find_char = FindCharCoordinator(
        service, config, FindCharLibrary(_find_char_bank(tmp_path)), events=events,
        rng=random.Random(0),
    )
    bet = BetCoordinator(
        service,
        config,
        _library(tmp_path),
        events=events,
        rng=FixedRng(0.0),
    )
    events.attach_find_char(find_char)
    events.attach_bet(bet)
    _ambient, answer = register_ambient(service, config, events, find_char, bet)
    sent: list[Message] = []

    async def fake_send(message: Message) -> None:
        sent.append(message)

    await _give_affection(service, 10, RIVAL_A)
    await _give_affection(service, 10, RIVAL_B)
    assert await bet.maybe_start(
        group_id=GROUP_ID,
        user_id=TRIGGER_ID,
        nickname="桃友",
        candidates=CANDIDATES,
        send=fake_send,
    ) is True

    await answer.handlers[0].call(_event("3 1 5 2 4", user_id=92005, nickname="路人"))

    pending = bet.pending[GROUP_ID]
    assert list(pending.answers) == [OTHER_ID]
    assert pending.answers[OTHER_ID].hits == 5
    # 不是排列的消息不会记进博弈,也不会走乱其它路由
    await answer.handlers[0].call(_event("abc", user_id=92005, nickname="路人"))
    assert list(pending.answers) == [OTHER_ID]
    assert find_char.pending == {}
    await _cancel_task(bet)


def _bet_texts(tmp_path: Path) -> Path:
    path = tmp_path / "texts.json"
    path.write_text(
        json.dumps(
            {
                "mantou.ambient": {
                    "neutral": ["馒头看了看群里。"],
                    "warm": ["馒头往这边挪了挪。"],
                    "close": ["馒头把脑袋搁在你手边。"],
                    "flirty": ["馒头在你名字旁边画了颗心。"],
                    "intimate": ["馒头把最软的那块留给你。"],
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def _find_char_bank(tmp_path: Path) -> Path:
    path = tmp_path / "findchar_puzzles.json"
    path.write_text(
        json.dumps(
            [{"mode": "pair", "decoys": ["己"], "target": "已", "rows": 8, "cols": 10,
              "row": 3, "col": 5}],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path
