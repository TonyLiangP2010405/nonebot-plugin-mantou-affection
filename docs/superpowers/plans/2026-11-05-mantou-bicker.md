# 馒头斗嘴 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增「馒头斗嘴」群内活动：从群聊记忆出题，60 秒押注，5 回合对话后由模型判定输赢，并按押注结算好感度。

**Architecture:** 新开 `bicker.py`，用 `BickerCoordinator` 管理押注、回合、超时和结算；通过 `GroupMemoryAdapter` 软依赖 `nonebot-plugin-group-chat-memory` 的 `store.recent_messages()` 和 `chat_client.complete()`。活动接入现有群级活动席位、ambient 触发链、答案路由和 SUPERUSER 强制事件列表。

**Tech Stack:** Python 3.9+、NoneBot2、OneBot v11、pytest、ruff。

**Spec:** `docs/superpowers/specs/2026-11-05-mantou-bicker-design.md`

## Global Constraints

- Python `>=3.9,<4.0`，不新增任何依赖。
- 所有群友可见文案为中文，沿用「@ + 一个空格 + 正文」的现有风格。
- `poetry run pytest -q` 必须全绿；`poetry run ruff check .` 必须无告警。
- 群聊记忆插件是软依赖：未加载、未配置聊天模型、或模型调用失败时，活动取消并退回押注，不结算 ±3/-5。
- 押注选项固定为 6 个：`1/3/5` 押群友赢、`1/3/5` 押群友输；每人一次，当事人不能押。
- 押注窗口 60 秒，5 个回合，每回合 60 秒；每次模型调用外层超时 20 秒。
- 触发概率默认 `0.0005`；强制事件列表新增第 9 项。
- 当事人赢 +3、输（含超时）-5；押注本金不享受新手保护，当事人的 -5 走新手保护。
- 版本号从 `0.30.0` 升到 `0.31.0`。

## Preconditions

当前工作区有上一轮恶魔转盘优化留下的未提交改动（`README.md`、`wheel.py`、`wheel_memes.py`、两个 wheel 测试）。执行本计划前先和用户确认这些改动是提交、暂存还是保留；本计划的每个 commit 只 `git add` 本任务明确列出的文件，禁止 `git add -A`。

---

### Task 1: 配置、概率读取与 `/馒头斗嘴概率`

**Files:**
- Modify: `nonebot_plugin_mantou_affection/config.py`
- Modify: `nonebot_plugin_mantou_affection/service.py`
- Modify: `nonebot_plugin_mantou_affection/commands.py`
- Test: `tests/test_service.py`, `tests/test_commands.py`

**Interfaces:**
- Consumes: 现有 `AffectionService._stored_probability()`、`store.get_setting()`、`store.set_setting()`、`parse_probability()`。
- Produces:
  - `Config.mantou_affection_bicker_chance: float`（默认 `0.0005`）
  - `Config.mantou_affection_bicker_bet_window: int`（默认 `60`）
  - `Config.mantou_affection_bicker_round_timeout: int`（默认 `60`）
  - `AffectionService.bicker_triggered() -> bool`
  - `AffectionService.bicker_probability() -> float`
  - `AffectionService.bicker_probability_override() -> float | None`
  - `AffectionService.set_bicker_probability(value: float) -> None`
  - `commands.BICKER_CHANCE_USAGE: str`
  - `register_commands(...)` 返回元组新增 `bicker_cmd`，位于索引 `14`。

- [ ] **Step 1: 写失败测试**

在 `tests/test_commands.py` 的默认值测试区加：

```python
def test_bicker_defaults() -> None:
    config = Config()
    assert config.mantou_affection_bicker_chance == 0.0005
    assert config.mantou_affection_bicker_bet_window == 60
    assert config.mantou_affection_bicker_round_timeout == 60
```

在 `tests/test_service.py` 末尾加：

```python
async def test_bicker_probability_defaults_and_override(tmp_path: Path) -> None:
    service = _service(tmp_path)
    assert await service.bicker_probability() == pytest.approx(0.0005)
    await service.set_bicker_probability(0.25)
    assert await service.bicker_probability() == pytest.approx(0.25)
    assert await service.bicker_probability_override() == pytest.approx(0.25)
```

在 `tests/test_commands.py` 的概率命令测试区加：

```python
async def test_bicker_probability_command_query_and_set(tmp_path: Path, monkeypatch) -> None:
    service = AffectionService(AffectionStore(tmp_path / "affection.json"), Config())
    bicker_cmd = register_commands(service, Config())[14]
    sent = []

    async def fake_finish(message: Message | str = "", **kwargs) -> None:
        sent.append(str(message))

    monkeypatch.setattr(bicker_cmd, "finish", fake_finish)
    handler = bicker_cmd.handlers[0]
    await handler.call(Message(""))
    assert "当前馒头斗嘴概率：0.05%" in sent[-1]
    await handler.call(Message("0.2%"))
    assert "已调整为 0.2%" in sent[-1]
    assert await service.bicker_probability() == 0.002
    await handler.call(Message("not-a-probability"))
    assert "请输入 0~1" in sent[-1]
    assert await service.bicker_probability() == 0.002
```

- [ ] **Step 2: 跑测试确认失败**

Run: `poetry run pytest tests/test_commands.py::test_bicker_defaults tests/test_service.py::test_bicker_probability_defaults_and_override tests/test_commands.py::test_bicker_probability_command_query_and_set -q`

Expected: FAIL（`Config` 没有 `mantou_affection_bicker_chance`；`register_commands(...)[14]` 越界）。

- [ ] **Step 3: 实现配置与 service 方法**

`config.py` 在 `mantou_affection_group_quiz_timeout` 后加：

```python
    mantou_affection_bicker_chance: float = Field(default=0.0005, ge=0.0, le=1.0)
    mantou_affection_bicker_bet_window: int = Field(default=60, ge=5, le=300)
    mantou_affection_bicker_round_timeout: int = Field(default=60, ge=5, le=300)
```

`service.py` 在 `GROUP_QUIZ_PROBABILITY_SETTING` 旁加 `BICKER_PROBABILITY_SETTING = "bicker_probability"`，在 `set_group_quiz_probability` 后加：

```python
    async def bicker_triggered(self) -> bool:
        """馒头斗嘴不看好感度，只按独立概率判定。"""

        chance = await self.bicker_probability()
        return self.rng.random() < chance

    async def bicker_probability(self) -> float:
        return await self._stored_probability(
            BICKER_PROBABILITY_SETTING,
            self.config.mantou_affection_bicker_chance,
        )

    async def bicker_probability_override(self) -> float | None:
        stored: Any = await self.store.get_setting(BICKER_PROBABILITY_SETTING, None)
        if isinstance(stored, bool) or not isinstance(stored, (int, float)):
            return None
        return min(1.0, max(0.0, float(stored)))

    async def set_bicker_probability(self, value: float) -> None:
        await self.store.set_setting(BICKER_PROBABILITY_SETTING, float(value))
```

- [ ] **Step 4: 实现命令**

`commands.py` 顶部加：

```python
BICKER_CHANCE_USAGE = (
    "请输入 0~1 的小数（如 0.0005）或百分数（如 0.05%），0 即关闭馒头斗嘴"
)
```

在 `group_quiz_cmd` 旁注册：

```python
    bicker_cmd = on_command(
        "馒头斗嘴概率",
        aliases={"馒头斗嘴触发概率"},
        permission=SUPERUSER,
        priority=10,
        block=True,
    )
```

在 `handle_group_quiz_probability` 后加一个 handler，复制该函数的查询/写入结构，把 `group_quiz` 换成 `bicker`、默认值换成 `config.mantou_affection_bicker_chance`、提示换成 `BICKER_CHANCE_USAGE`，成功文案为 `馒头斗嘴概率已调整为 {_format_probability(value)}`。

在 `return (...)` 元组末尾追加 `bicker_cmd`。

在 `/馒头好感帮助` 文案里加一行：

```
"/馒头斗嘴概率 0.05% —— SUPERUSER 查看或调整馒头斗嘴概率\n"
```

- [ ] **Step 5: 跑测试确认通过**

Run: `poetry run pytest tests/test_commands.py tests/test_service.py -q`

Expected: PASS。

- [ ] **Step 6: Commit**

```bash
git add nonebot_plugin_mantou_affection/config.py nonebot_plugin_mantou_affection/service.py \
  nonebot_plugin_mantou_affection/commands.py tests/test_service.py tests/test_commands.py
git commit -m "feat: add bicker config, probability and command"
```

---

### Task 2: `bicker.py` — 记忆适配、出题、押注

**Files:**
- Create: `nonebot_plugin_mantou_affection/bicker.py`
- Create: `tests/test_bicker.py`

**Interfaces:**
- Consumes: `AffectionService.profile()`、`AffectionService.adjust()`、`Config.mantou_affection_bicker_*`、`EventCoordinator.busy()`（Task 4 才接）。
- Produces:
  - `BICKER_STAKES: tuple[int, ...]`
  - `BICKER_OPTIONS: dict[str, tuple[bool, int]]`（`True` = 押群友赢）
  - `BICKER_ROUNDS = 5`、`BICKER_MODEL_TIMEOUT = 20.0`、`BICKER_MAX_LINE = 60`、`BICKER_MAX_REPLY = 200`
  - `GroupMemoryUnavailable(RuntimeError)`
  - `GroupMemoryAdapter(plugin_getter=None)`，方法 `available() -> bool`、`recent_messages(group_id, limit=30) -> list[tuple[str, str]]`、`complete(group_id, text, *, timeout=BICKER_MODEL_TIMEOUT) -> str`
  - `BickerBet`、`PendingBicker`
  - `BickerCoordinator(service, config, memory=None, *, events=None, rng=None, model_timeout=BICKER_MODEL_TIMEOUT)`
  - `BickerCoordinator.available() -> bool`（直接返回 `memory.available()`）
  - `BickerCoordinator.busy()`、`start()`、`answer()`、`opening_message()`、`summary_message()`、`discard()`、`is_debater_turn()`

- [ ] **Step 1: 写失败测试**

`tests/test_bicker.py` 的测试骨架：

```python
from __future__ import annotations

import asyncio
import random
from pathlib import Path

from nonebot.adapters.onebot.v11 import Message

from nonebot_plugin_mantou_affection.bicker import (
    BICKER_OPTIONS,
    GroupMemoryUnavailable,
    BickerCoordinator,
)
from nonebot_plugin_mantou_affection.config import Config
from nonebot_plugin_mantou_affection.service import AffectionService
from nonebot_plugin_mantou_affection.storage import AffectionStore

GROUP_ID = "93001"
TRIGGER_ID = "93002"
OTHER_ID = "93003"


class FakeMemory:
    def __init__(self, responses, recent=None, available=True):
        self.responses = list(responses)
        self.recent = (
            recent
            if recent is not None
            else [("甲", "我昨天十点就睡了"), ("乙", "早睡都是假的")]
        )
        self.available_flag = available
        self.calls: list[str] = []

    def available(self) -> bool:
        return self.available_flag

    async def recent_messages(self, group_id, limit=30):
        return list(self.recent)

    async def complete(self, group_id, text, *, timeout=20.0):
        self.calls.append(text)
        if not self.responses:
            raise GroupMemoryUnavailable("模型没有返回")
        return self.responses.pop(0)


def _coordinator(tmp_path: Path, memory, *, config=None, rng=None, events=None):
    config = config or Config(mantou_affection_newbie_protection_below=0)
    service = AffectionService(
        AffectionStore(tmp_path / "affection.json"),
        config,
        rng=rng or random.Random(0),
    )
    coordinator = BickerCoordinator(
        service, config, memory, events=events, rng=rng or random.Random(0)
    )
    coordinator.bet_window = 0.2
    coordinator.round_timeout = 30.0
    return coordinator, service


async def give(service: AffectionService, user_id: str, score: int) -> None:
    def bump(profile) -> None:
        profile.affection = score

    await service.store.update_profile(GROUP_ID, user_id, "测试员", bump)


async def _noop_send(message) -> None:
    return None


async def _start(coordinator, *, user_id=TRIGGER_ID, nickname="触发者"):
    sent = []

    async def send(message) -> None:
        sent.append(Message(message) if isinstance(message, str) else message)

    assert await coordinator.start(
        group_id=GROUP_ID, user_id=user_id, nickname=nickname, send=send
    )
    return coordinator.pending[GROUP_ID], sent
```

测试用例：

```python
async def test_start_aborts_when_memory_unavailable(tmp_path: Path) -> None:
    coordinator, _ = _coordinator(tmp_path, FakeMemory([], available=False))
    assert await coordinator.start(
        group_id=GROUP_ID, user_id=TRIGGER_ID, nickname="触发者", send=_noop_send
    ) is False
    assert coordinator.pending == {}


async def test_start_aborts_when_recent_messages_empty(tmp_path: Path) -> None:
    coordinator, _ = _coordinator(tmp_path, FakeMemory(["题目"], recent=[]))
    assert await coordinator.start(
        group_id=GROUP_ID, user_id=TRIGGER_ID, nickname="触发者", send=_noop_send
    ) is False
    assert coordinator.pending == {}


async def test_start_posts_topic_and_six_options(tmp_path: Path) -> None:
    coordinator, _ = _coordinator(tmp_path, FakeMemory(["凌晨三点睡到底健不健康"]))
    pending, sent = await _start(coordinator)
    body = sent[0].extract_plain_text()
    assert "馒头斗嘴" in body
    assert "凌晨三点睡到底健不健康" in body
    assert "押群友赢 1 点" in body
    assert "押群友输 5 点" in body
    for index in range(1, 7):
        assert f"{index}." in body
    assert pending.phase == "betting"
    pending.bet_task.cancel()


async def test_bet_deducts_and_records(tmp_path: Path) -> None:
    coordinator, service = _coordinator(tmp_path, FakeMemory(["题目"]))
    pending, _sent = await _start(coordinator)
    await give(service, OTHER_ID, 10)
    assert await coordinator.answer(GROUP_ID, OTHER_ID, "甲", "2") is True
    assert pending.bets[OTHER_ID].stake == 3
    assert pending.bets[OTHER_ID].side is True
    assert (await service.profile(GROUP_ID, OTHER_ID)).affection == 7
    pending.bet_task.cancel()


async def test_trigger_cannot_bet(tmp_path: Path) -> None:
    coordinator, service = _coordinator(tmp_path, FakeMemory(["题目"]))
    pending, sent = await _start(coordinator)
    await give(service, TRIGGER_ID, 10)
    assert await coordinator.answer(GROUP_ID, TRIGGER_ID, "触发者", "1") is True
    assert TRIGGER_ID not in pending.bets
    assert "当事人" in sent[-1].extract_plain_text()
    pending.bet_task.cancel()


async def test_duplicate_bet_rejected(tmp_path: Path) -> None:
    coordinator, service = _coordinator(tmp_path, FakeMemory(["题目"]))
    pending, sent = await _start(coordinator)
    await give(service, OTHER_ID, 10)
    assert await coordinator.answer(GROUP_ID, OTHER_ID, "甲", "1") is True
    assert await coordinator.answer(GROUP_ID, OTHER_ID, "甲", "3") is True
    assert pending.bets[OTHER_ID].stake == 1
    assert (await service.profile(GROUP_ID, OTHER_ID)).affection == 9
    assert "已经押过" in sent[-1].extract_plain_text()
    pending.bet_task.cancel()


async def test_insufficient_affection_then_lower_option_allowed(tmp_path: Path) -> None:
    coordinator, service = _coordinator(tmp_path, FakeMemory(["题目"]))
    pending, sent = await _start(coordinator)
    await give(service, OTHER_ID, 2)
    assert await coordinator.answer(GROUP_ID, OTHER_ID, "甲", "3") is True
    assert OTHER_ID not in pending.bets
    assert "好感不足 5 点" in sent[-1].extract_plain_text()
    assert await coordinator.answer(GROUP_ID, OTHER_ID, "甲", "1") is True
    assert pending.bets[OTHER_ID].stake == 1
    pending.bet_task.cancel()


async def test_bet_window_close_posts_summary(tmp_path: Path) -> None:
    coordinator, service = _coordinator(tmp_path, FakeMemory(["题目"]))
    pending, sent = await _start(coordinator)
    await give(service, OTHER_ID, 10)
    await coordinator.answer(GROUP_ID, OTHER_ID, "甲", "2")
    await asyncio.wait_for(pending.bet_task, 2)
    bodies = [message.extract_plain_text() for message in sent]
    assert any("押注截止" in body for body in bodies)
    assert any("押注总额 3 点" in body for body in bodies)
    assert pending.phase == "debating"


async def test_no_bets_close_posts_no_bets_message(tmp_path: Path) -> None:
    coordinator, _ = _coordinator(tmp_path, FakeMemory(["题目"]))
    pending, sent = await _start(coordinator)
    await asyncio.wait_for(pending.bet_task, 2)
    assert any("没人押注" in message.extract_plain_text() for message in sent)
    assert pending.phase == "debating"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `poetry run pytest tests/test_bicker.py -q`

Expected: FAIL（`ModuleNotFoundError: nonebot_plugin_mantou_affection.bicker`）。

- [ ] **Step 3: 实现 `bicker.py` 的 Task 2 部分**

实现常量、`GroupMemoryUnavailable`、`GroupMemoryAdapter`、`BickerBet`、`PendingBicker`、`BickerCoordinator` 的 `__init__ / busy / start / _generate_topic / opening_message / answer / _close_betting / summary_message / discard / is_debater_turn`。

关键取值：

```python
BICKER_STAKES = (1, 3, 5)
BICKER_OPTIONS = {
    "1": (True, 1), "2": (True, 3), "3": (True, 5),
    "4": (False, 1), "5": (False, 3), "6": (False, 5),
}
BICKER_ROUNDS = 5
BICKER_MODEL_TIMEOUT = 20.0
BICKER_MAX_LINE = 60
BICKER_MAX_REPLY = 200
BICKER_PREFIX = "🎤 馒头斗嘴！"
```

`BickerCoordinator.available()` 直接返回 `self.memory.available()`，供强制事件判断是否显示第 9 项。

`GroupMemoryAdapter` 的 `_module()` 用 `get_plugin("nonebot_plugin_group_chat_memory")` 和 `sys.modules` 取 `handlers`；`available()` 返回 `handlers is not None and hasattr(handlers, "store") and hasattr(handlers, "chat_client")`，并调用 `handlers.config.require_chat_config()`，异常时返回 `False`。`complete()` 构造一个带 `recent_messages=[]`、`long_term_chunks=[]` 的本地空上下文对象，调用 `handlers.chat_client.complete(context, text, session_id=f"bicker:{group_id}")`，外面套 `asyncio.wait_for(..., timeout=timeout)`。

`start()` 的顺序必须是：`busy()` → `memory.available()` → 先在 `self.pending` 放入一个 `phase="preparing"`、`topic=""` 的占位 `PendingBicker`（出题期间也占住群席位）→ `recent_messages()` → 生成题目 → 填 `topic`、`phase="betting"` → 发开场 → 起 `bet_task`。`recent_messages()` 为空、题目为空、发送失败都 `pop` 占位并返回 `False`，不留下 `pending`。`is_debater_turn()` 只认 `phase == "debating"`，所以 `preparing` 阶段不会被当成回嘴。

`_generate_topic()` 用 `_first_line(reply)` 取第一行、去掉首尾引号、截断 `BICKER_MAX_LINE`；空则返回 `None`。`answer()` 里的提示发送失败只记日志，不改变押注状态。

`_close_betting()` 睡 `self.bet_window`，检查 `pending.accepting` 和 `self.pending.get(group_id) is pending`，把 `phase` 设为 `"debating"`，发 `summary_message()`，然后调用 `await self._begin_debate(pending)`。Task 2 里 `_begin_debate()` 先写成空方法（`return None`），Task 3 再实现。

- [ ] **Step 4: 跑测试确认通过**

Run: `poetry run pytest tests/test_bicker.py -q`

Expected: PASS（Task 2 的 9 个用例）。

- [ ] **Step 5: Commit**

```bash
git add nonebot_plugin_mantou_affection/bicker.py tests/test_bicker.py
git commit -m "feat: add bicker memory adapter, topic and betting"
```

---

### Task 3: 5 回合斗嘴、判定与结算

**Files:**
- Modify: `nonebot_plugin_mantou_affection/bicker.py`
- Modify: `tests/test_bicker.py`

**Interfaces:**
- Consumes: Task 2 的 `PendingBicker`、`BickerBet`、`BICKER_ROUNDS`、`memory.complete()`。
- Produces:
  - `BickerCoordinator.handle_debate(group_id: str, user_id: str, text: str) -> bool`
  - `_begin_debate()`、`_schedule_round()`、`_expire_round()`、`_generate_reply()`、`_finish()`、`_judge()`、`_settle()`、`_cancel()`
  - `format_transcript(transcript: list[tuple[str, str]]) -> str`

- [ ] **Step 1: 写失败测试**

在 `tests/test_bicker.py` 追加：

```python
async def test_five_rounds_then_judge_and_trigger_wins(tmp_path: Path) -> None:
    memory = FakeMemory(["题目", "回1", "回2", "回3", "回4", "回5", "群友赢"])
    coordinator, service = _coordinator(tmp_path, memory)
    await give(service, TRIGGER_ID, 50)
    pending, _sent = await _start(coordinator)
    await asyncio.wait_for(pending.bet_task, 2)
    for index in range(1, 6):
        assert await coordinator.handle_debate(GROUP_ID, TRIGGER_ID, f"我说{index}") is True
    assert len(memory.calls) == 7
    assert (await service.profile(GROUP_ID, TRIGGER_ID)).affection == 53
    assert GROUP_ID not in coordinator.pending


async def test_trigger_loses_and_bets_settle(tmp_path: Path) -> None:
    memory = FakeMemory(["题目", "回1", "回2", "回3", "回4", "回5", "馒头赢"])
    coordinator, service = _coordinator(tmp_path, memory)
    await give(service, TRIGGER_ID, 50)
    await give(service, OTHER_ID, 50)
    await give(service, "93004", 50)
    pending, _sent = await _start(coordinator)
    await coordinator.answer(GROUP_ID, OTHER_ID, "甲", "1")  # 押群友赢 1
    await coordinator.answer(GROUP_ID, "93004", "乙", "4")   # 押群友输 3
    await asyncio.wait_for(pending.bet_task, 2)
    for index in range(1, 6):
        await coordinator.handle_debate(GROUP_ID, TRIGGER_ID, f"我说{index}")
    assert (await service.profile(GROUP_ID, TRIGGER_ID)).affection == 45
    assert (await service.profile(GROUP_ID, OTHER_ID)).affection == 49
    assert (await service.profile(GROUP_ID, "93004")).affection == 51


async def test_round_timeout_loses_and_settles(tmp_path: Path) -> None:
    memory = FakeMemory(["题目"])
    coordinator, service = _coordinator(tmp_path, memory)
    coordinator.round_timeout = 0.01
    await give(service, TRIGGER_ID, 50)
    pending, _sent = await _start(coordinator)
    await asyncio.wait_for(pending.bet_task, 2)
    await asyncio.wait_for(pending.round_task, 2)
    assert (await service.profile(GROUP_ID, TRIGGER_ID)).affection == 45
    assert GROUP_ID not in coordinator.pending


async def test_no_winners_refunds_all(tmp_path: Path) -> None:
    memory = FakeMemory(["题目", "回1", "回2", "回3", "回4", "回5", "馒头赢"])
    coordinator, service = _coordinator(tmp_path, memory)
    await give(service, TRIGGER_ID, 50)
    await give(service, OTHER_ID, 50)
    await give(service, "93004", 50)
    pending, _sent = await _start(coordinator)
    await coordinator.answer(GROUP_ID, OTHER_ID, "甲", "1")
    await coordinator.answer(GROUP_ID, "93004", "乙", "2")
    await asyncio.wait_for(pending.bet_task, 2)
    for index in range(1, 6):
        await coordinator.handle_debate(GROUP_ID, TRIGGER_ID, f"我说{index}")
    assert (await service.profile(GROUP_ID, OTHER_ID)).affection == 50
    assert (await service.profile(GROUP_ID, "93004")).affection == 50
    assert (await service.profile(GROUP_ID, TRIGGER_ID)).affection == 45


async def test_remainder_goes_to_earliest_winner(tmp_path: Path) -> None:
    memory = FakeMemory(["题目", "回1", "回2", "回3", "回4", "回5", "群友赢"])
    coordinator, service = _coordinator(tmp_path, memory)
    await give(service, TRIGGER_ID, 50)
    await give(service, OTHER_ID, 50)
    await give(service, "93004", 50)
    await give(service, "93005", 50)
    pending, _sent = await _start(coordinator)
    await coordinator.answer(GROUP_ID, OTHER_ID, "甲", "1")  # 最早赢家
    await coordinator.answer(GROUP_ID, "93004", "乙", "1")   # 第二赢家
    await coordinator.answer(GROUP_ID, "93005", "丙", "4")   # 输家押 5
    await asyncio.wait_for(pending.bet_task, 2)
    for index in range(1, 6):
        await coordinator.handle_debate(GROUP_ID, TRIGGER_ID, f"我说{index}")
    assert (await service.profile(GROUP_ID, OTHER_ID)).affection == 53
    assert (await service.profile(GROUP_ID, "93004")).affection == 52
    assert (await service.profile(GROUP_ID, "93005")).affection == 45


async def test_no_bets_only_trigger_settles(tmp_path: Path) -> None:
    memory = FakeMemory(["题目", "回1", "回2", "回3", "回4", "回5", "群友赢"])
    coordinator, service = _coordinator(tmp_path, memory)
    await give(service, TRIGGER_ID, 50)
    pending, _sent = await _start(coordinator)
    await asyncio.wait_for(pending.bet_task, 2)
    for index in range(1, 6):
        await coordinator.handle_debate(GROUP_ID, TRIGGER_ID, f"我说{index}")
    assert (await service.profile(GROUP_ID, TRIGGER_ID)).affection == 53
    assert GROUP_ID not in coordinator.pending


async def test_model_failure_cancels_and_refunds(tmp_path: Path) -> None:
    memory = FakeMemory(["题目"])
    coordinator, service = _coordinator(tmp_path, memory)
    await give(service, TRIGGER_ID, 50)
    await give(service, OTHER_ID, 50)
    pending, _sent = await _start(coordinator)
    await coordinator.answer(GROUP_ID, OTHER_ID, "甲", "2")
    await asyncio.wait_for(pending.bet_task, 2)
    assert await coordinator.handle_debate(GROUP_ID, TRIGGER_ID, "我说一句") is True
    assert (await service.profile(GROUP_ID, OTHER_ID)).affection == 50
    assert (await service.profile(GROUP_ID, TRIGGER_ID)).affection == 50
    assert GROUP_ID not in coordinator.pending


async def test_judge_failure_cancels_and_refunds(tmp_path: Path) -> None:
    memory = FakeMemory(["题目", "回1", "回2", "回3", "回4", "回5", "不知道"])
    coordinator, service = _coordinator(tmp_path, memory)
    await give(service, TRIGGER_ID, 50)
    await give(service, OTHER_ID, 50)
    pending, _sent = await _start(coordinator)
    await coordinator.answer(GROUP_ID, OTHER_ID, "甲", "1")
    await asyncio.wait_for(pending.bet_task, 2)
    for index in range(1, 6):
        await coordinator.handle_debate(GROUP_ID, TRIGGER_ID, f"我说{index}")
    assert (await service.profile(GROUP_ID, OTHER_ID)).affection == 50
    assert (await service.profile(GROUP_ID, TRIGGER_ID)).affection == 50
    assert GROUP_ID not in coordinator.pending


async def test_newbie_protection_on_trigger_loss(tmp_path: Path) -> None:
    memory = FakeMemory(["题目", "回1", "回2", "回3", "回4", "回5", "馒头赢"])
    coordinator, service = _coordinator(
        tmp_path,
        memory,
        config=Config(mantou_affection_newbie_protection_below=50),
    )
    await give(service, TRIGGER_ID, 40)
    pending, sent = await _start(coordinator)
    await asyncio.wait_for(pending.bet_task, 2)
    for index in range(1, 6):
        await coordinator.handle_debate(GROUP_ID, TRIGGER_ID, f"我说{index}")
    assert (await service.profile(GROUP_ID, TRIGGER_ID)).affection == 39
    assert "新手保护" in sent[-1].extract_plain_text()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `poetry run pytest tests/test_bicker.py -q -k "five_rounds or trigger_loses or round_timeout or no_winners or remainder or no_bets_only or model_failure or judge_failure or newbie"`

Expected: FAIL（`handle_debate` 不存在，或 `_begin_debate` 不启动回合）。

- [ ] **Step 3: 实现回合、判定与结算**

按设计稿实现：

- `_begin_debate()`：设置 `round = 0`，@ 当事人发题目和第一句固定挑衅「来，说说你的看法。请在 N 秒内回嘴。」，然后 `_schedule_round()`。
- `_schedule_round()`：`pending.round_task = asyncio.create_task(self._expire_round(pending, pending.round))`。
- `_expire_round(pending, round_index)`：睡 `self.round_timeout`；检查 `accepting`、`phase == "debating"`、`round == round_index`；超时调用 `_finish(pending, trigger_won=False)`。
- `handle_debate()`：用 `is_debater_turn()` 校验；取 `text.strip()[:BICKER_MAX_REPLY]`，空文本返回 False；把 `pending.awaiting_reply = True`（`is_debater_turn()` 要求它为 False，保证同一回合只收第一条）；取消 `round_task`；追加 `("群友", line)`；`round += 1`；`_generate_reply()`；追加 `("馒头", reply)` 并发送；`round >= BICKER_ROUNDS` 时 `_finish()`，否则发下一回合提示、把 `awaiting_reply` 设回 False 并 `_schedule_round()`。模型失败走 `_cancel()`，pending 会被删除，不需要复位。
- `_generate_reply()`：把题目和 `format_transcript()` 填进 `REPLY_PROMPT`，调用 `memory.complete(..., timeout=self.model_timeout)`，取第一行、截断 `BICKER_MAX_LINE`，空则抛 `GroupMemoryUnavailable`。
- `_finish()`：若 `phase != "debating"` 直接返回；设 `phase = "judging"`、`accepting = False`、取消 `round_task`（如果它不是 `asyncio.current_task()`）；`trigger_won is None` 时调 `_judge()`；判定为 `None` 或异常时 `_cancel()`；否则 `_settle()`。
- `_judge()`：`JUDGE_PROMPT` 里放题目和完整 transcript；回复含「群友赢」返回 `True`，含「馒头赢」返回 `False`；两者都没有、或同时包含两者都返回 `None`（判定失败）。
- `_settle()`：先结算当事人（`+3` 或 `-5`，走 `service.adjust`，记录 `NEWBIE_PROTECTION_NOTE`）；再按 `side == trigger_won` 分胜负：
  - 无押注：只发当事人一行。
  - 有押注但无赢家：逐个 `service.adjust(+bet.actual, protect=False)` 退回，再发当事人一行。
  - 有赢家：`pool = sum(bet.actual for bet in losers)`；`share, remainder = divmod(pool, len(winners))`；赢家按 `placed_at` 排序，每人应退 `bet.actual + share`，最早一位再加 `remainder`；逐个 `service.adjust` 后退款，提示用实际到账值（受上限截断时按实际显示）；输家只读当前好感并提示押注已扣。
  - 所有行用 `MessageSegment.at()` + 一个空格 + 文案，最后 `await pending.send(Message(segments))`，然后从 `self.pending` 删除。
- `_cancel()`：`phase` 已是 `"settled"` 直接返回；设 `"settled"`、`accepting = False`、取消两个 task（跳过 `asyncio.current_task()`，避免在回合任务里取消自己）；逐个退回 `bet.actual`（`protect=False`）；发「斗嘴中断，押注已全部退回，好感度没有变化。」；删除 pending。

- [ ] **Step 4: 跑测试确认通过**

Run: `poetry run pytest tests/test_bicker.py -q`

Expected: PASS（Task 2 + Task 3 全部用例）。

- [ ] **Step 5: Commit**

```bash
git add nonebot_plugin_mantou_affection/bicker.py tests/test_bicker.py
git commit -m "feat: add bicker rounds, judgement and settlement"
```

---

### Task 4: 接入 ambient、答案路由与群席位

**Files:**
- Modify: `nonebot_plugin_mantou_affection/ambient.py`
- Modify: `nonebot_plugin_mantou_affection/__init__.py`
- Test: `tests/test_ambient.py`

**Interfaces:**
- Consumes: Task 2/3 的 `BickerCoordinator`、`GroupMemoryAdapter`。
- Produces:
  - `EventCoordinator.__init__(..., bicker=None)`、`EventCoordinator.attach_bicker(bicker)`；`busy()` 覆盖 `bicker.pending`。
  - `register_ambient(service, config, events=None, findchar=None, bet=None, pk=None, wheel=None, group_quiz=None, bicker=None)`（`bicker` 必须是最后一个关键字参数，保持现有位置调用兼容）。
  - `__init__.py` 组装 `BickerCoordinator` 并注入 ambient。

- [ ] **Step 1: 写失败测试**

在 `tests/test_ambient.py` 加（复用现有的 `_service`、`_capture_send`、`_event`）：

```python
async def test_ambient_starts_bicker_when_probability_hits(
    tmp_path: Path, monkeypatch
) -> None:
    config = Config(
        mantou_affection_find_char_chance=0.0,
        mantou_affection_bet_chance=0.0,
        mantou_affection_pk_chance=0.0,
        mantou_affection_group_quiz_chance=0.0,
        mantou_affection_wheel_chance=0.0,
        mantou_affection_ambient_enabled=False,
        mantou_affection_bicker_chance=1.0,
        mantou_affection_newbie_protection_below=0,
    )
    service = _service(tmp_path, config=config)
    events = EventCoordinator(service, config)
    memory = FakeMemory(["凌晨三点睡到底健不健康"])
    bicker = BickerCoordinator(service, config, memory, events=events)
    ambient, _ = register_ambient(service, config, events, bicker=bicker)
    sent = _capture_send(ambient, monkeypatch)

    await ambient.handlers[0].call(_event())

    assert "馒头斗嘴" in sent[-1].extract_plain_text()
    assert GROUP_ID in bicker.pending
    bicker.pending[GROUP_ID].bet_task.cancel()


async def test_ambient_routes_debate_line_before_other_activities(
    tmp_path: Path, monkeypatch
) -> None:
    config = Config(
        mantou_affection_find_char_chance=1.0,
        mantou_affection_bet_chance=0.0,
        mantou_affection_pk_chance=0.0,
        mantou_affection_group_quiz_chance=0.0,
        mantou_affection_wheel_chance=0.0,
        mantou_affection_ambient_enabled=False,
        mantou_affection_newbie_protection_below=0,
    )
    service = _service(tmp_path, config=config)
    events = EventCoordinator(service, config)
    memory = FakeMemory(["题目", "馒头的回应"])
    bicker = BickerCoordinator(service, config, memory, events=events)
    bicker.bet_window = 0.01
    ambient, _ = register_ambient(service, config, events, bicker=bicker)
    sent = _capture_send(ambient, monkeypatch)

    await bicker.start(
        group_id=GROUP_ID, user_id=TRIGGER_ID, nickname="桃友", send=ambient.send
    )
    pending = bicker.pending[GROUP_ID]
    await asyncio.wait_for(pending.bet_task, 2)
    sent.clear()

    await ambient.handlers[0].call(_event(text="我偏要凌晨三点睡", user_id=TRIGGER_ID))

    assert pending.transcript == [
        ("群友", "我偏要凌晨三点睡"),
        ("馒头", "馒头的回应"),
    ]
    assert "馒头的回应" in sent[0].extract_plain_text()
```

在 `tests/test_ambient.py` 顶部导入 `FakeMemory`、`BickerCoordinator`（`FakeMemory` 可从 `tests.test_bicker` 导入，或在本文件复制一个最小版本；计划推荐复制，避免测试模块互相依赖）。

- [ ] **Step 2: 跑测试确认失败**

Run: `poetry run pytest tests/test_ambient.py -q -k bicker`

Expected: FAIL（`register_ambient` 不接受 `bicker`；`EventCoordinator` 没有 `bicker`）。

- [ ] **Step 3: 接线**

`ambient.py`：

- `EventCoordinator.__init__` 增加 `bicker=None` 并保存；加 `attach_bicker()`；`busy()` 的 `any(...)` 元组加入 `self.bicker`。
- `register_ambient` 签名末尾加 `bicker=None`；若不为 `None`，`coordinator.attach_bicker(bicker)`。
- `handle_ambient` 在 `bet_coordinator.note_message(...)` 之后、找字判定之前加：

```python
        if bicker is not None and bicker.is_debater_turn(group_id, user_id):
            try:
                await bicker.handle_debate(
                    group_id, user_id, event.message.extract_plain_text()
                )
            except Exception:
                logger.exception("[mantou-affection] 处理斗嘴回嘴失败")
            return
```

- 在转盘判定之后、小动作之前加斗嘴触发：

```python
        try:
            bicker_ready = await service.bicker_triggered()
        except Exception:
            logger.exception("[mantou-affection] 判定馒头斗嘴失败")
            bicker_ready = False
        if bicker is not None and bicker_ready:
            try:
                started = await bicker.start(
                    group_id=group_id,
                    user_id=user_id,
                    nickname=nickname,
                    send=ambient.send,
                )
            except Exception:
                logger.exception("[mantou-affection] 发起馒头斗嘴失败")
                started = False
            if started:
                return
```

- `handle_answer` 在转盘之后加：

```python
            if bicker is not None and await bicker.answer(
                group_id, user_id, name, text
            ):
                return
```

`__init__.py`：

```python
from .bicker import BickerCoordinator, GroupMemoryAdapter
...
bicker_coordinator = BickerCoordinator(
    service,
    plugin_config,
    GroupMemoryAdapter(),
    events=event_coordinator,
)
event_coordinator.attach_bicker(bicker_coordinator)
```

并把 `bicker=bicker_coordinator` 传给 `register_ambient(...)`。

- [ ] **Step 4: 跑测试确认通过**

Run: `poetry run pytest tests/test_ambient.py tests/test_bicker.py -q`

Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add nonebot_plugin_mantou_affection/ambient.py nonebot_plugin_mantou_affection/__init__.py \
  tests/test_ambient.py
git commit -m "feat: wire bicker into ambient and group activity slot"
```

---

### Task 5: 强制事件第 9 项、帮助文案、README 与版本

**Files:**
- Modify: `nonebot_plugin_mantou_affection/force.py`
- Modify: `nonebot_plugin_mantou_affection/__init__.py`
- Modify: `nonebot_plugin_mantou_affection/commands.py`
- Modify: `README.md`
- Modify: `pyproject.toml`
- Test: `tests/test_force.py`

**Interfaces:**
- Consumes: Task 4 的 `bicker_coordinator`。
- Produces:
  - `FORCE_ACTIVITY_BICKER = "bicker"`、`FORCE_CHOICES["9"]`、`FORCE_ACTIVITY_LINES` 第 9 项 `"9. 馒头斗嘴"`、`FORCE_BICKER_UNAVAILABLE`
  - `ForceCoordinator(..., bicker=None)`、`ForceCoordinator.activity_lines() -> tuple[str, ...]`、`ForceCoordinator.prompt(target: str) -> str`
  - `register_force_commands(..., bicker=None)`

- [ ] **Step 1: 写失败测试**

`tests/test_force.py`：

- `EXPECTED_ACTIVITY_LINES` 追加 `"9. 馒头斗嘴"`。
- `Harness.__init__` 增加 `FakeMemory` 和 `self.bicker`，`self.events.attach_bicker(self.bicker)`，`register_force_commands(..., bicker=self.bicker)`。
- `cleanup()` 的 pending 元组加入 `*self.bicker.pending.values()`，并对每个 pending 取消 `bet_task`、`round_task`。
- `test_invalid_choice_cancels_without_affection_change` 的参数 `"9"` 改成 `"10"`（9 现在是合法选项）。
- 分发参数表加 `("9", "bicker", "馒头斗嘴")`。
- 新增：

```python
async def test_force_bicker_choice_starts_bicker(
    tmp_path: Path, bundled_texts_path: Path, monkeypatch
) -> None:
    harness = Harness(tmp_path, bundled_texts_path, monkeypatch)
    await harness.start_command()
    harness.sent.clear()

    await harness.choose("9")

    assert GROUP_ID in harness.bicker.pending
    assert "馒头斗嘴" in _body(harness.sent[-1])
    harness.bicker.pending[GROUP_ID].bet_task.cancel()
    await harness.cleanup()


def test_force_prompt_hides_bicker_when_unavailable(
    tmp_path: Path, bundled_texts_path: Path, monkeypatch
) -> None:
    harness = Harness(tmp_path, bundled_texts_path, monkeypatch)
    harness.bicker.memory.available_flag = False

    assert "9. 馒头斗嘴" not in harness.force.prompt("桃友")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `poetry run pytest tests/test_force.py -q -k "activity or bicker or invalid_choice"`

Expected: FAIL（`EXPECTED_ACTIVITY_LINES` 仍是 8；`"9"` 被判为无效；`Harness` 没有 `bicker`）。

- [ ] **Step 3: 实现强制事件与文案**

`force.py`：

- 常量：

```python
FORCE_ACTIVITY_BICKER = "bicker"
FORCE_BICKER_UNAVAILABLE = "群聊记忆插件不可用，馒头斗嘴开不了。"
```

- `FORCE_CHOICES` 加 `"9": FORCE_ACTIVITY_BICKER`。
- `FORCE_ACTIVITY_LINES` 加 `"9. 馒头斗嘴"`。
- `ForceCoordinator.__init__` 加 `bicker=None`；`_start_activity` 加分支；`_start_bicker()` 调用 `self.bicker.start(...)`，`None` 或失败返回 `FORCE_BICKER_UNAVAILABLE`。
- `FORCE_PROMPT` 改成带 `{lines}` 的模板；`activity_lines()` 在 `bicker is None or not bicker.available()` 时过滤掉 `9. 馒头斗嘴`；`prompt(target)` 用过滤后的行拼提示；`handle_force` 改用 `force.prompt(name)`。
- `register_force_commands` 加 `bicker=None` 并传给 `ForceCoordinator`。

`__init__.py`：把 `bicker=bicker_coordinator` 传给 `register_force_commands(...)`；`__plugin_meta__.usage` 的「回复 1~7」改成「回复 1~9」，并加 `/馒头斗嘴概率`。

`commands.py`：帮助文案的「回复 1~7」改成「回复 1~9」。

`README.md`：

- 顶部功能列表加一条「馒头斗嘴」。
- 配置块加 `MANTOU_AFFECTION_BICKER_CHANCE=0.0005`、`MANTOU_AFFECTION_BICKER_BET_WINDOW=60`、`MANTOU_AFFECTION_BICKER_ROUND_TIMEOUT=60`。
- 新增 `### 馒头斗嘴` 章节，写清：触发概率、@ 触发者、群聊记忆出题、60 秒押注 6 个选项、5 回合每回合 60 秒、超时判输、模型判定、押注结算（退本金 + 平分、余数给最早赢家、赢方没人押全退）、当事人 ±3/-5、记忆插件不可用时关闭。
- 命令表加 `/馒头斗嘴概率 0.05%`。
- 强制事件列表加 `9. 馒头斗嘴`，并把「1~8」改成「1~9」。

`pyproject.toml`：`version = "0.31.0"`。

- [ ] **Step 4: 全量验证**

Run: `poetry run pytest -q && poetry run ruff check .`

Expected: 全部 PASS，ruff 无告警。

- [ ] **Step 5: Commit**

```bash
git add nonebot_plugin_mantou_affection/force.py nonebot_plugin_mantou_affection/__init__.py \
  nonebot_plugin_mantou_affection/commands.py README.md pyproject.toml tests/test_force.py
git commit -m "feat: add bicker to force events and document it"
```

---

## Review Focus

1. **群聊记忆插件已加载但聊天模型未配置**：`start()` 必须在发出任何群消息前返回 `False`，不能留下 `pending`。测试：`test_start_aborts_when_memory_unavailable`。
2. **群聊最近消息为空**：不能凭空生成题目，直接不触发。测试：`test_start_aborts_when_recent_messages_empty`。
3. **模型调用超时或失败**：取消活动、押注全额退回、当事人好感不变，不能把群席位卡死。测试：`test_model_failure_cancels_and_refunds`、`test_judge_failure_cancels_and_refunds`。
4. **回合超时与回嘴同时发生**：只能结算一次，不能既判超时又继续回合。测试：`test_round_timeout_loses_and_settles`。
5. **押注边界**：赢方没人押全退、没有输家时只退本金、除不尽的余数给最早赢家、当事人 -5 受新手保护。测试：`test_no_winners_refunds_all`、`test_remainder_goes_to_earliest_winner`、`test_newbie_protection_on_trigger_loss`。
6. **押注交互**：当事人不能押、重复押注不重复扣、好感不足可改选低档。测试：`test_trigger_cannot_bet`、`test_duplicate_bet_rejected`、`test_insufficient_affection_then_lower_option_allowed`。
