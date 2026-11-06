from __future__ import annotations

import asyncio
import random
from pathlib import Path

from nonebot.adapters.onebot.v11 import Message

from nonebot_plugin_mantou_affection.config import Config
from nonebot_plugin_mantou_affection.flatter import (
    BUNDLED_THEMES_PATH,
    FlatterCoordinator,
    FlatterEntry,
    FlatterThemeLibrary,
    GroupMemoryUnavailable,
    apply_flag_cap,
    parse_scores,
    rank_delta,
    split_join,
)
from nonebot_plugin_mantou_affection.service import AffectionService
from nonebot_plugin_mantou_affection.storage import AffectionStore

GROUP_ID = "93001"
ALICE = "93002"
BOB = "93003"
CAROL = "93004"
DAVE = "93005"


class FakeMemory:
    def __init__(self, responses, available=True):
        self.responses = list(responses)
        self.available_flag = available
        self.calls: list[str] = []

    def available(self) -> bool:
        return self.available_flag

    async def complete(self, group_id, text, *, timeout=25.0):
        self.calls.append(text)
        if not self.responses:
            raise GroupMemoryUnavailable("模型没有返回")
        return self.responses.pop(0)


def _coordinator(tmp_path: Path, memory, *, config=None):
    config = config or Config(mantou_affection_newbie_protection_below=0)
    service = AffectionService(
        AffectionStore(tmp_path / "affection.json"),
        config,
        rng=random.Random(0),
    )
    coordinator = FlatterCoordinator(service, config, memory)
    coordinator.window = 0.03
    return coordinator, service


async def give(service: AffectionService, user_id: str, score: int) -> None:
    def bump(profile) -> None:
        profile.affection = score

    await service.store.update_profile(GROUP_ID, user_id, "测试员", bump)


async def _start(coordinator):
    sent = []

    async def send(message) -> None:
        sent.append(Message(message) if isinstance(message, str) else message)

    assert await coordinator.start(group_id=GROUP_ID, send=send)
    return coordinator.pending[GROUP_ID], sent


async def _join(coordinator, pending, *users: tuple[str, str]) -> None:
    for user_id, name in users:
        assert await coordinator.answer(GROUP_ID, user_id, name, "参与") is True
    for _ in range(400):
        if pending.phase != "signup":
            return
        await asyncio.sleep(0.005)
    raise AssertionError("报名阶段没有结束")


def scores_json(*items: tuple[int, int, str, str]) -> str:
    parts = ", ".join(
        f'{{"id": {index}, "score": {score}, "flag": "{flag}", "reason": "{reason}"}}'
        for index, score, flag, reason in items
    )
    return '{"scores": [' + parts + "]}"


def test_theme_library_loads_bundled_pool() -> None:
    library = FlatterThemeLibrary(BUNDLED_THEMES_PATH)
    assert len(library) == 1000
    themes = library._themes
    assert len(set(themes)) == 1000
    assert all(theme.strip() for theme in themes)


def test_theme_library_falls_back_on_bad_file(tmp_path: Path) -> None:
    broken = tmp_path / "broken.json"
    broken.write_text("not json", encoding="utf-8")
    library = FlatterThemeLibrary(broken)
    assert len(library) == 12


def test_theme_library_dedupes_and_skips_bad_entries(tmp_path: Path) -> None:
    path = tmp_path / "themes.json"
    path.write_text(
        '{"themes": ["夸桃神", "夸桃神", "", 3, "  夸桃神的歌喉  "]}',
        encoding="utf-8",
    )
    library = FlatterThemeLibrary(path)
    assert len(library) == 2
    assert library._themes == ["夸桃神", "夸桃神的歌喉"]


def test_split_join_recognizes_signup_words() -> None:
    assert split_join("参与") == (True, "")
    assert split_join("  报名 ") == (True, "")
    assert split_join("参加！") == (True, "")
    assert split_join("参与，桃神今晚必开播") == (True, "桃神今晚必开播")
    assert split_join("我要参与") == (True, "")
    assert split_join("桃神真棒") == (False, "")


async def test_start_uses_injected_theme_library(tmp_path: Path) -> None:
    path = tmp_path / "themes.json"
    path.write_text('{"themes": ["只用这一条主题"]}', encoding="utf-8")
    coordinator, _ = _coordinator(tmp_path, FakeMemory([]))
    coordinator.themes = FlatterThemeLibrary(path)
    pending, sent = await _start(coordinator)

    assert "主题：只用这一条主题" in sent[0].extract_plain_text()
    pending.task.cancel()


def test_rank_delta_table() -> None:
    assert rank_delta(1, 1) == 3
    assert rank_delta(1, 2) == 3
    assert rank_delta(2, 2) == -2
    assert rank_delta(1, 3) == 6
    assert rank_delta(2, 3) == 1
    assert rank_delta(3, 3) == -3
    assert rank_delta(1, 4) == 6
    assert rank_delta(4, 4) == -6
    assert rank_delta(1, 5) == 6
    assert rank_delta(3, 5) == 3
    assert rank_delta(4, 5) == -4
    assert rank_delta(5, 5) == -6
    assert rank_delta(4, 6) == -2
    assert rank_delta(4, 7) == 0
    assert rank_delta(5, 7) == -2
    assert rank_delta(4, 8) == 0
    assert rank_delta(5, 8) == 0


def test_apply_flag_cap() -> None:
    assert apply_flag_cap(90, "ok") == 90
    assert apply_flag_cap(90, "lazy") == 20
    assert apply_flag_cap(90, "offtopic") == 30
    assert apply_flag_cap(90, "rude") == 0
    assert apply_flag_cap(90, "copy") == 0


def test_parse_scores_reads_flags_and_reasons() -> None:
    entries = [
        FlatterEntry(ALICE, "甲", "桃神最棒", 0.0),
        FlatterEntry(BOB, "乙", "桃神唱歌真好听", 1.0),
    ]
    parsed = parse_scores(
        scores_json((1, 90, "ok", "很具体"), (2, 40, "lazy", "太短")),
        entries,
    )
    assert parsed[ALICE].score == 90
    assert parsed[ALICE].flag == "ok"
    assert parsed[ALICE].reason == "很具体"
    assert parsed[BOB].flag == "lazy"


def test_parse_scores_ignores_bad_payloads() -> None:
    entries = [FlatterEntry(ALICE, "甲", "文本", 0.0)]
    assert parse_scores("没有 JSON", entries) == {}
    assert parse_scores('{"scores": "nope"}', entries) == {}
    assert parse_scores('{"scores": [{"id": 9}]}', entries) == {}
    parsed = parse_scores(
        '{"scores": [{"id": 1, "score": 999, "flag": "weird", "reason": ""}]}',
        entries,
    )
    assert parsed[ALICE].score == 100
    assert parsed[ALICE].flag == "ok"
    assert parsed[ALICE].reason == "桃神点了点头"


async def test_start_aborts_when_memory_unavailable(tmp_path: Path) -> None:
    coordinator, _ = _coordinator(tmp_path, FakeMemory([], available=False))
    sent = []

    async def send(message) -> None:
        sent.append(message)

    assert await coordinator.start(group_id=GROUP_ID, send=send) is False
    assert coordinator.pending == {}
    assert sent == []


async def test_start_posts_theme_and_rules(tmp_path: Path) -> None:
    coordinator, _ = _coordinator(tmp_path, FakeMemory([]))
    pending, sent = await _start(coordinator)
    body = sent[0].extract_plain_text()
    assert "献媚桃神" in body
    assert "主题：" in body
    assert "先发「参与」报名" in body
    assert "第 1 名 +6" in body
    assert "倒数第 1 名 -6" in body
    assert pending.phase == "signup"
    pending.task.cancel()


async def test_signup_then_collecting_after_window(tmp_path: Path) -> None:
    coordinator, _ = _coordinator(tmp_path, FakeMemory([]))
    pending, sent = await _start(coordinator)

    assert await coordinator.answer(GROUP_ID, ALICE, "甲", "参与") is True
    assert ALICE in pending.registered
    assert "报名成功" in sent[-1].extract_plain_text()
    assert pending.entries == {}

    await _join(coordinator, pending, (BOB, "乙"))
    assert pending.phase == "collecting"
    assert "现在开始献媚" in sent[-1].extract_plain_text()
    pending.task.cancel()


async def test_signup_message_with_extra_text_still_registers(tmp_path: Path) -> None:
    coordinator, _ = _coordinator(tmp_path, FakeMemory([]))
    pending, sent = await _start(coordinator)

    assert await coordinator.answer(GROUP_ID, ALICE, "甲", "参与 桃神今晚必开播") is True
    assert ALICE in pending.registered
    assert pending.entries == {}
    assert "喊开始再发" in sent[-1].extract_plain_text()
    pending.task.cancel()


async def test_signup_ignores_plain_chat(tmp_path: Path) -> None:
    coordinator, _ = _coordinator(tmp_path, FakeMemory([]))
    pending, _sent = await _start(coordinator)

    assert await coordinator.answer(GROUP_ID, ALICE, "甲", "桃神今天真好看") is False
    assert pending.registered == {}
    pending.task.cancel()


async def test_unregistered_chat_is_not_an_entry(tmp_path: Path) -> None:
    coordinator, _ = _coordinator(tmp_path, FakeMemory([]))
    pending, _sent = await _start(coordinator)
    await _join(coordinator, pending, (ALICE, "甲"))

    assert await coordinator.answer(GROUP_ID, BOB, "乙", "桃神唱歌真好听") is False
    assert BOB not in pending.entries
    assert await coordinator.answer(GROUP_ID, ALICE, "甲", "桃神开播比闹钟准") is True
    assert ALICE in pending.entries
    pending.task.cancel()


async def test_answer_records_one_entry_per_user(tmp_path: Path) -> None:
    coordinator, _ = _coordinator(tmp_path, FakeMemory([]))
    pending, sent = await _start(coordinator)
    await _join(coordinator, pending, (ALICE, "甲"))

    assert await coordinator.answer(GROUP_ID, ALICE, "甲", "桃神七点开播从不迟到") is True
    assert await coordinator.answer(GROUP_ID, ALICE, "甲", "再来一句") is True
    assert pending.entries[ALICE].text == "桃神七点开播从不迟到"
    assert "已经献过媚" in sent[-1].extract_plain_text()
    pending.task.cancel()


async def test_repeated_join_word_is_not_an_entry(tmp_path: Path) -> None:
    coordinator, _ = _coordinator(tmp_path, FakeMemory([]))
    pending, sent = await _start(coordinator)
    await _join(coordinator, pending, (ALICE, "甲"))

    assert await coordinator.answer(GROUP_ID, ALICE, "甲", "参与") is True
    assert pending.entries == {}
    assert "直接发你的献媚" in sent[-1].extract_plain_text()
    pending.task.cancel()


async def test_answer_ignores_commands_and_empty_text(tmp_path: Path) -> None:
    coordinator, _ = _coordinator(tmp_path, FakeMemory([]))
    pending, _sent = await _start(coordinator)

    assert await coordinator.answer(GROUP_ID, ALICE, "甲", "/馒头好感") is False
    assert await coordinator.answer(GROUP_ID, ALICE, "甲", "   ") is False
    assert pending.registered == {}
    pending.task.cancel()


async def test_duplicate_text_marks_copy(tmp_path: Path) -> None:
    coordinator, _ = _coordinator(tmp_path, FakeMemory([]))
    pending, _sent = await _start(coordinator)
    await _join(coordinator, pending, (ALICE, "甲"), (BOB, "乙"))

    await coordinator.answer(GROUP_ID, ALICE, "甲", "桃神唱歌太好听了")
    await coordinator.answer(GROUP_ID, BOB, "乙", "桃神唱歌，太好听了！")

    assert pending.entries[ALICE].copy is False
    assert pending.entries[BOB].copy is True
    pending.task.cancel()


async def test_close_without_signups_cancels(tmp_path: Path) -> None:
    coordinator, _ = _coordinator(tmp_path, FakeMemory([]))
    pending, sent = await _start(coordinator)
    await asyncio.wait_for(pending.task, 2)

    assert "没人报名" in sent[-1].extract_plain_text()
    assert GROUP_ID not in coordinator.pending


async def test_signup_without_entry_keeps_affection(tmp_path: Path) -> None:
    judge = scores_json((1, 80, "ok", "好"))
    coordinator, service = _coordinator(tmp_path, FakeMemory([judge]))
    await give(service, ALICE, 50)
    await give(service, BOB, 50)
    pending, sent = await _start(coordinator)
    await _join(coordinator, pending, (ALICE, "甲"), (BOB, "乙"))

    await coordinator.answer(GROUP_ID, ALICE, "甲", "桃神开播比闹钟准")
    await asyncio.wait_for(pending.task, 2)

    body = sent[-1].extract_plain_text()
    assert "报名没献媚（不扣分）：乙" in body
    assert (await service.profile(GROUP_ID, ALICE)).affection == 53
    assert (await service.profile(GROUP_ID, BOB)).affection == 50


async def test_ranking_settles_front_up_and_back_down(tmp_path: Path) -> None:
    judge = scores_json((1, 90, "ok", "梗很密"), (2, 60, "ok", "中规中矩"), (3, 20, "ok", "太敷衍"))
    memory = FakeMemory([judge])
    coordinator, service = _coordinator(tmp_path, memory)
    await give(service, ALICE, 50)
    await give(service, BOB, 50)
    await give(service, CAROL, 50)
    pending, sent = await _start(coordinator)
    await _join(coordinator, pending, (ALICE, "甲"), (BOB, "乙"), (CAROL, "丙"))

    await coordinator.answer(GROUP_ID, ALICE, "甲", "桃神的星露谷能种出全服最好的土豆")
    await coordinator.answer(GROUP_ID, BOB, "乙", "桃神唱歌还行")
    await coordinator.answer(GROUP_ID, CAROL, "丙", "桃神最棒")
    await asyncio.wait_for(pending.task, 2)

    body = sent[-1].extract_plain_text()
    assert "第 1 名" in body and "第 3 名" in body
    assert (await service.profile(GROUP_ID, ALICE)).affection == 56
    assert (await service.profile(GROUP_ID, BOB)).affection == 51
    assert (await service.profile(GROUP_ID, CAROL)).affection == 47


async def test_tied_scores_break_by_submission_time(tmp_path: Path) -> None:
    judge = scores_json((1, 80, "ok", "好"), (2, 80, "ok", "也好"), (3, 30, "ok", "一般"))
    coordinator, service = _coordinator(tmp_path, FakeMemory([judge]))
    await give(service, ALICE, 50)
    await give(service, BOB, 50)
    await give(service, CAROL, 50)
    pending, _sent = await _start(coordinator)
    await _join(coordinator, pending, (ALICE, "甲"), (BOB, "乙"), (CAROL, "丙"))

    await coordinator.answer(GROUP_ID, ALICE, "甲", "桃神开播比闹钟准")
    await coordinator.answer(GROUP_ID, BOB, "乙", "桃神的咕咕是群里的起床号")
    await coordinator.answer(GROUP_ID, CAROL, "丙", "桃神还行吧")
    await asyncio.wait_for(pending.task, 2)

    assert (await service.profile(GROUP_ID, ALICE)).affection == 56
    assert (await service.profile(GROUP_ID, BOB)).affection == 51
    assert (await service.profile(GROUP_ID, CAROL)).affection == 47


async def test_violation_loses_six_and_is_not_ranked(tmp_path: Path) -> None:
    judge = scores_json((1, 70, "ok", "真诚"), (2, 0, "rude", "阴阳怪气"))
    coordinator, service = _coordinator(tmp_path, FakeMemory([judge]))
    await give(service, ALICE, 50)
    await give(service, BOB, 50)
    pending, sent = await _start(coordinator)
    await _join(coordinator, pending, (ALICE, "甲"), (BOB, "乙"))

    await coordinator.answer(GROUP_ID, ALICE, "甲", "桃神今天状态真好")
    await coordinator.answer(GROUP_ID, BOB, "乙", "桃神也就那样吧")
    await asyncio.wait_for(pending.task, 2)

    body = sent[-1].extract_plain_text()
    assert "违规" in body
    assert (await service.profile(GROUP_ID, ALICE)).affection == 53
    assert (await service.profile(GROUP_ID, BOB)).affection == 44


async def test_copy_is_penalized_even_if_model_scores_high(tmp_path: Path) -> None:
    judge = scores_json((1, 80, "ok", "好"), (2, 90, "ok", "抄得好"))
    coordinator, service = _coordinator(tmp_path, FakeMemory([judge]))
    await give(service, ALICE, 50)
    await give(service, BOB, 50)
    pending, _sent = await _start(coordinator)
    await _join(coordinator, pending, (ALICE, "甲"), (BOB, "乙"))

    await coordinator.answer(GROUP_ID, ALICE, "甲", "桃神是星露谷的神")
    await coordinator.answer(GROUP_ID, BOB, "乙", "桃神是星露谷的神！")
    await asyncio.wait_for(pending.task, 2)

    assert (await service.profile(GROUP_ID, ALICE)).affection == 53
    assert (await service.profile(GROUP_ID, BOB)).affection == 44


async def test_lazy_entry_is_capped_and_ranked_last(tmp_path: Path) -> None:
    judge = scores_json((1, 60, "ok", "不错"), (2, 90, "lazy", "空话"))
    coordinator, service = _coordinator(tmp_path, FakeMemory([judge]))
    await give(service, ALICE, 50)
    await give(service, BOB, 50)
    pending, _sent = await _start(coordinator)
    await _join(coordinator, pending, (ALICE, "甲"), (BOB, "乙"))

    await coordinator.answer(GROUP_ID, ALICE, "甲", "桃神昨晚的星露谷直播笑死我了")
    await coordinator.answer(GROUP_ID, BOB, "乙", "桃神最棒")
    await asyncio.wait_for(pending.task, 2)

    assert (await service.profile(GROUP_ID, ALICE)).affection == 53
    assert (await service.profile(GROUP_ID, BOB)).affection == 48


async def test_model_failure_cancels_without_affection_change(tmp_path: Path) -> None:
    coordinator, service = _coordinator(tmp_path, FakeMemory([]))
    await give(service, ALICE, 50)
    pending, sent = await _start(coordinator)
    await _join(coordinator, pending, (ALICE, "甲"))

    await coordinator.answer(GROUP_ID, ALICE, "甲", "桃神今天真好看")
    await asyncio.wait_for(pending.task, 2)

    assert "桃神今天没空看" in sent[-1].extract_plain_text()
    assert (await service.profile(GROUP_ID, ALICE)).affection == 50
    assert GROUP_ID not in coordinator.pending


async def test_scoring_is_batched_over_twenty_entries(tmp_path: Path) -> None:
    first = scores_json(*[(index, 50, "ok", "还行") for index in range(1, 21)])
    second = scores_json((21, 50, "ok", "还行"))
    memory = FakeMemory([first, second])
    coordinator, service = _coordinator(tmp_path, memory)
    coordinator.window = 0.2
    await give(service, ALICE, 50)
    pending, _sent = await _start(coordinator)

    for index in range(21):
        user = f"93{index:03d}"
        await give(service, user, 50)
        assert await coordinator.answer(GROUP_ID, user, f"群友{index}", "参与") is True
    for _ in range(400):
        if pending.phase == "collecting":
            break
        await asyncio.sleep(0.005)
    assert pending.phase == "collecting"
    for index in range(21):
        user = f"93{index:03d}"
        await coordinator.answer(GROUP_ID, user, f"群友{index}", f"桃神第{index}好")
    await asyncio.wait_for(pending.task, 5)

    assert len(memory.calls) == 2
    assert "20. 桃神第19好" in memory.calls[0]
    assert "21. 桃神第20好" in memory.calls[1]
    assert (await service.profile(GROUP_ID, ALICE)).affection == 53


async def test_scoring_prompt_excludes_nicknames(tmp_path: Path) -> None:
    judge = scores_json((1, 80, "ok", "好"))
    memory = FakeMemory([judge])
    coordinator, service = _coordinator(tmp_path, memory)
    await give(service, ALICE, 50)
    pending, _sent = await _start(coordinator)
    await _join(coordinator, pending, (ALICE, "甲"))

    await coordinator.answer(GROUP_ID, ALICE, "甲", "桃神开播比闹钟准")
    await asyncio.wait_for(pending.task, 2)

    prompt = memory.calls[0]
    assert "桃神开播比闹钟准" in prompt
    assert "甲：" not in prompt


async def test_entry_after_close_is_ignored(tmp_path: Path) -> None:
    judge = scores_json((1, 80, "ok", "好"))
    coordinator, service = _coordinator(tmp_path, FakeMemory([judge]))
    await give(service, ALICE, 50)
    pending, _sent = await _start(coordinator)
    await _join(coordinator, pending, (ALICE, "甲"))
    await coordinator.answer(GROUP_ID, ALICE, "甲", "桃神开播比闹钟准")
    await asyncio.wait_for(pending.task, 2)

    assert await coordinator.answer(GROUP_ID, BOB, "乙", "迟到了") is False


def test_parse_scores_accepts_string_ids_and_float_scores() -> None:
    entries = [FlatterEntry(ALICE, "甲", "文本", 0.0)]
    parsed = parse_scores(
        '{"scores": [{"id": "1", "score": 85.5, "flag": "ok", "reason": "好"}]}',
        entries,
    )
    assert parsed[ALICE].score == 85
    assert parsed[ALICE].flag == "ok"
