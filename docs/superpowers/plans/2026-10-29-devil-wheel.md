# 馒头小恶魔转盘 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有命运齿轮转盘之外加一张「小恶魔转盘」：2/3 概率触发、触发者需 >200 好感、参与费 5 点、不限人数、60 秒自动开转，表内新增群友语料库与表情五选一。

**Architecture:** 用 `WheelSpec` 把两张表的差异（权重、参与费、人数上限、是否自动开、是否退款、表情/语料库费用与候选数）收敛成数据，同一个 `WheelCoordinator` 跑两套流程；语料库 1 万条随包发布，由 `GroupCorpus` 惰性加载。

**Tech Stack:** Python 3.9+、NoneBot2、pytest、poetry

**Spec:** `docs/superpowers/specs/2026-10-29-devil-wheel-design.md`

## Global Constraints

- Python `>=3.9,<4.0`，不新增任何依赖。
- 所有群友可见文案为中文，沿用「@ + 一个空格 + 正文」的现有风格。
- `poetry run pytest -q` 必须全绿；`poetry run ruff check .` 必须无告警。
- 恶魔表权重合计必须为 100，且与设计文档逐项一致。
- 普通转盘（权重、参与费 2、人数上限 4、运作/不运作/确认、退款）行为不得改变，`tests/test_wheel.py` 全部保持通过。
- 恶魔转盘没有「运作」「不运作」「确认」，到点必开、不退款。

## Review Focus

- **60 秒到点时有人正等着五选一**：转盘结算必须立刻继续，不能等任何人回复；五选一的状态活在转盘 pending 之外。
- **恶魔转盘参与者很多（10 人以上）**：逐人结算必须串行完成且互不阻塞，单人发送失败只记日志、不影响后面的人。
- **五选一挂起期间同群开新一轮转盘、或本人发「参与」**：不能被五选一吞掉，也不能互相覆盖状态。
- **语料库 JSON 出现空串或非字符串条目**：`GroupCorpus.pick()` 不能崩，按素材未就绪处理。
- **好感 0 的人抽中扣 5 点的语料库或表情**：扣到 0 为止，不抛错、不拒绝。

---

### Task 1: 群友语料库数据与加载器

**Files:**
- Create: `scripts/build_group_corpus.py`
- Create: `nonebot_plugin_mantou_affection/wheel_corpus.py`
- Create: `nonebot_plugin_mantou_affection/resources/group_corpus.json`（由脚本生成）
- Test: `tests/test_wheel_corpus.py`

**Interfaces:**
- Produces: `BUNDLED_CORPUS_PATH: Path`、`class GroupCorpus(path: Path | None, *, rng: random.Random | None = None)`、`GroupCorpus.pick() -> str`、`GroupCorpus.line_count -> int`、`CorpusError(RuntimeError)`、`CorpusAssetMissing(CorpusError)`。
- 数据格式：`{"lines": ["…", "…"]}`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_wheel_corpus.py
import json
import random
from pathlib import Path

import pytest

from nonebot_plugin_mantou_affection.wheel_corpus import (
    BUNDLED_CORPUS_PATH,
    CorpusAssetMissing,
    GroupCorpus,
)


def _write(tmp_path: Path, payload) -> Path:
    path = tmp_path / "corpus.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_group_corpus_pick_returns_one_of_the_lines(tmp_path: Path) -> None:
    corpus = GroupCorpus(_write(tmp_path, {"lines": ["甲", "乙丙"]}), rng=random.Random(0))
    assert corpus.pick() in {"甲", "乙丙"}
    assert corpus.line_count == 2


@pytest.mark.parametrize(
    "payload",
    [{"lines": []}, {"lines": ["", "  "]}, {"lines": [1, None]}, {}, {"lines": "not-a-list"}],
)
def test_group_corpus_rejects_unusable_payload(tmp_path: Path, payload) -> None:
    corpus = GroupCorpus(_write(tmp_path, payload))
    with pytest.raises(CorpusAssetMissing):
        corpus.pick()


def test_group_corpus_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(CorpusAssetMissing):
        GroupCorpus(tmp_path / "nope.json").pick()


def test_bundled_corpus_has_ten_thousand_clean_lines() -> None:
    lines = json.loads(BUNDLED_CORPUS_PATH.read_text(encoding="utf-8"))["lines"]
    assert len(lines) == 10_000
    assert len(set(lines)) == 10_000
    assert all(isinstance(line, str) for line in lines)
    assert all(2 <= len(line.strip()) <= 60 for line in lines)
    assert all("http" not in line and "www." not in line for line in lines)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `poetry run pytest tests/test_wheel_corpus.py -q`
Expected: FAIL，`ModuleNotFoundError: nonebot_plugin_mantou_affection.wheel_corpus`

- [ ] **Step 3: 实现 `wheel_corpus.py`**

`GroupCorpus` 在第一次 `pick()` 时读文件并缓存；`path` 为 `None`、文件缺失、JSON 解析失败、`lines` 不是 list、过滤后为空，都抛 `CorpusAssetMissing`。过滤规则：只保留 `str`，`strip()` 后非空。`BUNDLED_CORPUS_PATH = Path(__file__).parent / "resources" / "group_corpus.json"`。

- [ ] **Step 4: 跑测试确认除内置语料外全过**

Run: `poetry run pytest tests/test_wheel_corpus.py -q`
Expected: 前三个测试 PASS，`test_bundled_corpus_has_ten_thousand_clean_lines` FAIL（文件不存在）

- [ ] **Step 5: 写生成脚本并生成数据**

`scripts/build_group_corpus.py` 暴露 `collect_lines(db_path: Path) -> list[str]` 与 `build(db_path: Path, count: int, seed: int) -> list[str]`，`main()` 用 argparse 接收 `--db`、`--count`（默认 10000）、`--out`、`--seed`（默认 20261029）。

筛选（对应设计文档）：`chain_json` 所有消息段 `type == "text"`；拼接后 `strip()` 长度 2–60；排除 `^[\W_]+$` 与 `^\d+$`；排除含 `http` / `www.`；按文本去重；`sorted()` 后用固定种子 `random.Random(seed).sample(lines, count)`，结果再排序写入。

```bash
poetry run python scripts/build_group_corpus.py \
  --db "/Users/liangpuyue/.kimi-code/sessions/wd_nonebot-plugin-mantou-affection_b0a50cdbb432/session_0ed5506a-23b2-4e6a-9624-9636ca4daeee/attachments/f_b5aa9f3a-f4bc-4501-b4da-084acbf4c252-learning.db" \
  --count 10000 \
  --out nonebot_plugin_mantou_affection/resources/group_corpus.json
```

- [ ] **Step 6: 跑测试确认全过**

Run: `poetry run pytest tests/test_wheel_corpus.py -q`
Expected: 全部 PASS

- [ ] **Step 7: 提交**

```bash
git add scripts/build_group_corpus.py nonebot_plugin_mantou_affection/wheel_corpus.py \
  nonebot_plugin_mantou_affection/resources/group_corpus.json tests/test_wheel_corpus.py
git commit -m "feat: add group corpus resource and loader"
```

---

### Task 2: 表情候选池

**Files:**
- Modify: `nonebot_plugin_mantou_affection/wheel_memes.py:136-182`
- Test: `tests/test_wheel_memes.py`

**Interfaces:**
- Consumes: 现有 `MemeGeneratorAdapter._is_blocked()`、`_kind()`、`_ranked_ids()`。
- Produces: `MemeGeneratorAdapter._candidates(*, max_images: int) -> list[tuple[Any, str]]`、`MemeGeneratorAdapter.draw_candidates(self, count: int, *, max_images: int = 2) -> list[tuple[Any, str]]`、`MemeGeneratorAdapter.draw_candidates_for_group(self, *, group_id: str, bot: Any, user_id: str, count: int) -> list[tuple[Any, str]]`。

- [ ] **Step 1: 写失败测试**

```python
def test_draw_candidates_returns_distinct_templates() -> None:
    memes = [FakeMeme(f"m{index}", 1, 1) for index in range(8)]
    adapter = MemeGeneratorAdapter(backend=FakeBackend(memes), rng=random.Random(0))
    picked = adapter.draw_candidates(5)
    assert len(picked) == 5
    assert len({id(meme) for meme, _kind in picked}) == 5


def test_draw_candidates_returns_fewer_when_pool_is_small() -> None:
    memes = [FakeMeme(f"m{index}", 1, 1) for index in range(3)]
    adapter = MemeGeneratorAdapter(backend=FakeBackend(memes), rng=random.Random(0))
    assert len(adapter.draw_candidates(5)) == 3


def test_draw_candidates_skips_blocked_and_incompatible() -> None:
    memes = [
        FakeMeme("petpet", 1, 1),
        FakeMeme("camellya", 1, 1),
        FakeMeme("needs-five", 5, 5),
    ]
    adapter = MemeGeneratorAdapter(backend=FakeBackend(memes), rng=random.Random(0))
    picked = adapter.draw_candidates(5)
    assert [meme.key for meme, _kind in picked] == ["petpet"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `poetry run pytest tests/test_wheel_memes.py -q -k draw_candidates`
Expected: FAIL，`AttributeError: 'MemeGeneratorAdapter' object has no attribute 'draw_candidates'`

- [ ] **Step 3: 重构 `draw()` 并新增候选方法**

把 `draw()` 里「遍历 `backend.get_memes()`、过滤屏蔽词、判 `_kind`、比 `min_images`」的循环抽成 `_candidates(*, max_images)`；`draw()` 改为 `self.rng.choice(self._candidates(max_images=max_images))`，错误信息与日志保持不变。`draw_candidates()` 用 `self.rng.sample(candidates, min(count, len(candidates)))`。`draw_candidates_for_group()` 复制 `draw_for_group()` 的 `_ranked_ids()` 逻辑，传 `max_images=2 + len(ids)`。

- [ ] **Step 4: 跑测试确认通过**

Run: `poetry run pytest tests/test_wheel_memes.py -q`
Expected: 全部 PASS（含既有 `draw()` 测试）

- [ ] **Step 5: 提交**

```bash
git add nonebot_plugin_mantou_affection/wheel_memes.py tests/test_wheel_memes.py
git commit -m "feat: add distinct meme candidate drawing"
```

---

### Task 3: 恶魔表、规则文案、模式判定与配置

**Files:**
- Modify: `nonebot_plugin_mantou_affection/wheel.py:20-133`
- Modify: `nonebot_plugin_mantou_affection/config.py:33-36`
- Test: `tests/test_wheel_devil.py`（新建）

**Interfaces:**
- Produces:
  - `WheelSpec(name: str, outcomes, join_cost: int, max_participants: int | None, auto_start: bool, refund: bool, meme_cost: int, meme_candidates: int, corpus_cost: int, taozi_cost: int)`
  - `NORMAL_SPEC`、`DEVIL_SPEC`、`DEVIL_WHEEL_OUTCOMES`
  - `WheelOutcome("corpus")` 这一新 kind
  - `draw_wheel_outcome(rng, outcomes=WHEEL_OUTCOMES)`（新增第二参数，默认值保持兼容）
  - `format_devil_wheel_rules(timeout: int, min_affection: int) -> str`
  - `WheelCoordinator.pick_mode(affection: int) -> bool`
  - 配置 `mantou_affection_wheel_devil_chance`（默认 `0.667`）、`mantou_affection_wheel_devil_min_affection`（默认 `200`）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_wheel_devil.py
from __future__ import annotations

import random

import pytest

from nonebot_plugin_mantou_affection.config import Config
from nonebot_plugin_mantou_affection.wheel import (
    DEVIL_SPEC,
    DEVIL_WHEEL_OUTCOMES,
    NORMAL_SPEC,
    WheelCoordinator,
    draw_wheel_outcome,
    format_devil_wheel_rules,
)


def test_devil_weights_match_approved_table() -> None:
    weighted = [((o.kind, o.value), w) for o, w in DEVIL_WHEEL_OUTCOMES]
    assert weighted == [
        (("meme", 0), 10),
        (("taozi", 0), 25),
        (("corpus", 0), 10),
        (("affection", 4), 4),
        (("affection", 8), 2),
        (("affection", 12), 2),
        (("affection", 20), 1),
        (("affection", 40), 1),
        (("affection", -4), 13),
        (("affection", -8), 11),
        (("affection", -12), 8),
        (("affection", -20), 7),
        (("affection", -40), 4),
        (("affection", -60), 2),
    ]
    assert sum(w for _o, w in DEVIL_WHEEL_OUTCOMES) == 100


def test_devil_spec_differs_from_normal_only_where_designed() -> None:
    assert NORMAL_SPEC.auto_start is False
    assert NORMAL_SPEC.refund is True
    assert NORMAL_SPEC.max_participants == 4
    assert NORMAL_SPEC.join_cost == 2
    assert NORMAL_SPEC.taozi_cost == 10
    assert NORMAL_SPEC.meme_candidates == 1
    assert DEVIL_SPEC.auto_start is True
    assert DEVIL_SPEC.refund is False
    assert DEVIL_SPEC.max_participants is None
    assert DEVIL_SPEC.join_cost == 5
    assert DEVIL_SPEC.taozi_cost == 30
    assert DEVIL_SPEC.meme_cost == 5
    assert DEVIL_SPEC.corpus_cost == 5
    assert DEVIL_SPEC.meme_candidates == 5


def test_devil_rules_list_threshold_cost_and_every_probability() -> None:
    text = format_devil_wheel_rules(60, 200)
    for fragment in (
        "大于 200",
        "不限人数",
        "参与一次扣 5 点好感度",
        "60 秒后自动开转",
        "不退款",
        "表情 10%",
        "桃子表情包 25%",
        "30 点好感",
        "群友语料库 10%",
        "扣 5 点好感",
        "+4 4%",
        "+40 1%",
        "-4 13%",
        "-60 2%",
    ):
        assert fragment in text


def test_pick_mode_requires_more_than_threshold() -> None:
    class FixedRandom(random.Random):
        def random(self) -> float:
            return 0.0

    coordinator = WheelCoordinator(object(), Config(), rng=FixedRandom(0))
    assert coordinator.pick_mode(200) is False
    assert coordinator.pick_mode(201) is True


def test_pick_mode_respects_configured_chance() -> None:
    coordinator = WheelCoordinator(
        object(), Config(mantou_affection_wheel_devil_chance=0.0), rng=random.Random(0)
    )
    assert coordinator.pick_mode(999) is False


@pytest.mark.parametrize(("roll", "kind"), [(0, "meme"), (10, "taozi"), (35, "corpus")])
def test_devil_draw_ranges(roll, kind) -> None:
    class Fixed(random.Random):
        def randrange(self, stop: int) -> int:
            assert stop == 100
            return roll

    assert draw_wheel_outcome(Fixed(0), DEVIL_WHEEL_OUTCOMES).kind == kind
```

- [ ] **Step 2: 跑测试确认失败**

Run: `poetry run pytest tests/test_wheel_devil.py -q`
Expected: FAIL，`ImportError: cannot import name 'DEVIL_SPEC'`

- [ ] **Step 3: 实现表、文案、判定与配置**

- `WheelSpec` 用 `@dataclass(frozen=True)`，字段按 Interfaces 顺序。
- `NORMAL_SPEC` 的 `outcomes=WHEEL_OUTCOMES`、`join_cost=WHEEL_JOIN_COST`、`max_participants=WHEEL_MAX_PARTICIPANTS`、`auto_start=False`、`refund=True`、`meme_cost=0`、`meme_candidates=1`、`corpus_cost=0`、`taozi_cost=WHEEL_TAOZI_COST`。
- `DEVIL_SPEC` 按上面测试的取值，`outcomes=DEVIL_WHEEL_OUTCOMES`。
- `draw_wheel_outcome(rng, outcomes=WHEEL_OUTCOMES)` 用传入的表。
- `format_devil_wheel_rules(timeout, min_affection)` 从 `DEVIL_WHEEL_OUTCOMES` 现算各类权重，格式对齐 `format_wheel_rules`。
- `pick_mode()`：`affection > config.mantou_affection_wheel_devil_min_affection` 且 `rng.random() < chance`。
- `config.py` 新增两个字段（`Field` 带 `ge`/`le` 约束，写法对齐现有项）。

- [ ] **Step 4: 跑测试确认通过**

Run: `poetry run pytest tests/test_wheel_devil.py -q`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add nonebot_plugin_mantou_affection/wheel.py nonebot_plugin_mantou_affection/config.py tests/test_wheel_devil.py
git commit -m "feat: add devil wheel table, rules text and mode roll"
```

---

### Task 4: 恶魔报名与自动开转

**Files:**
- Modify: `nonebot_plugin_mantou_affection/wheel.py:170-490`
- Test: `tests/test_wheel_devil.py`

**Interfaces:**
- Consumes: Task 3 的 `WheelSpec`、`DEVIL_SPEC`、`format_devil_wheel_rules()`。
- Produces:
  - `WheelCoordinator.start(..., devil: bool = False)`（新参数，默认 `False`）
  - `PendingWheel.spec: WheelSpec`
  - `WHEEL_DEVIL_NO_CHOICE_REPLY`、`WHEEL_DEVIL_AUTO_START_REPLY` 两条文案常量
  - `_join()` / `_expire()` / `_run_operation()` 全部改读 `pending.spec`

- [ ] **Step 1: 写失败测试**

复用 `tests/test_wheel.py` 里的 `make_coordinator` / `give` / `FakeBot` / `SequenceRng` 写法，在本文件内建一份同样的 `make_coordinator`（多加一个 `corpus=None` 透传），以及辅助函数：

```python
async def devil_start(coordinator, *, user_id: str = TRIGGER, nickname: str = "触发者"):
    sent = []

    async def send(message) -> None:
        sent.append(Message(message) if isinstance(message, str) else message)

    assert await coordinator.start(
        group_id=GROUP, user_id=user_id, nickname=nickname,
        bot=FakeBot(), send=send, devil=True,
    )
    return coordinator.pending[GROUP], sent


async def devil_auto(coordinator, *, user_id: str = TRIGGER):
    """开一轮恶魔转盘并等到点自动开转，返回 (pending, sent)。"""

    coordinator.choice_timeout = 0.01
    pending, sent = await devil_start(coordinator, user_id=user_id)
    await asyncio.wait_for(pending.task, 2)
    return pending, sent
```

后面所有需要「自动开转」的测试都用 `devil_auto()`，不要自己等 `pending.task`——`make_coordinator` 把 `choice_timeout` 设成了 30，不改成 0.01 会卡住。

```python
async def test_devil_join_charges_five_and_has_no_cap(tmp_path) -> None:
    coordinator = make_coordinator(tmp_path)
    pending, sent = await devil_start(coordinator)
    for index in range(10):
        user = f"950{index}"
        await give(coordinator.service, user, 50)
        await coordinator.answer(GROUP, user, f"群友{index}", "参与")
    assert len(pending.participants) == 10
    assert (await coordinator.service.profile(GROUP, "9500")).affection == 45
    assert all("名额已满" not in m.extract_plain_text() for m in sent)
    pending.task.cancel()


async def test_devil_join_rejects_below_five(tmp_path) -> None:
    coordinator = make_coordinator(tmp_path)
    pending, sent = await devil_start(coordinator)
    await give(coordinator.service, OTHER, 4)
    await coordinator.answer(GROUP, OTHER, "群友", "参与")
    assert "好感度不足" in sent[-1].extract_plain_text()
    assert (await coordinator.service.profile(GROUP, OTHER)).affection == 4
    pending.task.cancel()


@pytest.mark.parametrize("keyword", ["运作", "不运作", "确认"])
async def test_devil_ignores_choice_keywords(tmp_path, keyword) -> None:
    coordinator = make_coordinator(tmp_path)
    pending, sent = await devil_start(coordinator)
    await coordinator.answer(GROUP, TRIGGER, "触发者", keyword)
    assert pending.accepting is True
    assert pending.task is not None and not pending.task.done()
    assert "不需要" in sent[-1].extract_plain_text()
    pending.task.cancel()


async def test_devil_auto_runs_at_timeout_without_refund(tmp_path) -> None:
    coordinator = make_coordinator(tmp_path, rng=SequenceRng([45, 45]))
    coordinator.choice_timeout = 0.01
    await give(coordinator.service, TRIGGER, 500)
    pending, _sent = await devil_start(coordinator)
    await give(coordinator.service, OTHER, 50)
    await coordinator.answer(GROUP, OTHER, "群友", "参与")
    assert (await coordinator.service.profile(GROUP, OTHER)).affection == 45
    await asyncio.wait_for(pending.task, 2)
    assert GROUP not in coordinator.pending
    # 参与者 50 - 5 参与费 + 4 抽中 = 49：没有退款，结果确实结算了
    assert (await coordinator.service.profile(GROUP, OTHER)).affection == 49
    assert (await coordinator.service.profile(GROUP, TRIGGER)).affection == 504
```

- [ ] **Step 2: 跑测试确认失败**

Run: `poetry run pytest tests/test_wheel_devil.py -q -k devil_join or devil_ignores or devil_auto`
Expected: FAIL，`TypeError: start() got an unexpected keyword argument 'devil'`

- [ ] **Step 3: 实现报名与自动开转**

- `PendingWheel.__init__` 增加 `spec: WheelSpec = NORMAL_SPEC` 并保存。
- `start()` 增加 `devil: bool = False`；`spec = DEVIL_SPEC if devil else NORMAL_SPEC`；开场消息按 spec 选 `format_devil_wheel_rules(int(self.choice_timeout), config.mantou_affection_wheel_devil_min_affection)` 或 `format_wheel_rules(...)`。
- `_join()`：`spec.join_cost` 替换 `WHEEL_JOIN_COST`；人数上限判断改成 `spec.max_participants is not None and len(...) >= spec.max_participants`；好感不足文案带 `spec.join_cost`。
- `answer()`：取到 `pending` 且关键字属于 `{"参与","运作","不运作","确认"}` 后，若 `pending.spec.auto_start` 且关键字不是「参与」，回 `WHEEL_DEVIL_NO_CHOICE_REPLY` 并 `return True`。
- `_expire()`：`pending.spec.auto_start` 时先发 `WHEEL_DEVIL_AUTO_START_REPLY`，再 `await self._run_operation(pending)`，不退款。
- `_run_operation()`：`draw_wheel_outcome(self.rng, pending.spec.outcomes)`。

- [ ] **Step 4: 跑测试确认通过**

Run: `poetry run pytest tests/test_wheel_devil.py tests/test_wheel.py -q`
Expected: 全部 PASS（普通转盘不受影响）

- [ ] **Step 5: 提交**

```bash
git add nonebot_plugin_mantou_affection/wheel.py tests/test_wheel_devil.py
git commit -m "feat: add devil wheel join flow and auto start"
```

---

### Task 5: 恶魔好感结算与 30 点桃子

**Files:**
- Modify: `nonebot_plugin_mantou_affection/wheel.py:123-133, 206-215, 695-815`
- Test: `tests/test_wheel_devil.py`

**Interfaces:**
- Consumes: Task 4 的 `pending.spec`。
- Produces:
  - `format_sticker_prompt(affection: int, timeout: int, cost: int = WHEEL_TAOZI_COST) -> str`（新增第三参数，默认值保持兼容）
  - `PendingStickerConfirm.cost: int`
  - `_run_sticker_outcome()` 与 `_answer_sticker_confirm()` 全部读 `spec.taozi_cost` / `confirm.cost`

- [ ] **Step 1: 写失败测试**

```python
async def test_devil_taozi_asks_thirty_and_charges_thirty(tmp_path) -> None:
    coordinator = make_coordinator(tmp_path, rng=FixedRollRng(roll=10), stickers=FakeStickers())
    await give(coordinator.service, TRIGGER, 100)
    pending, sent = await devil_auto(coordinator)
    prompt = sent[-1].extract_plain_text()
    assert "30 点好感" in prompt
    await coordinator.answer(GROUP, TRIGGER, "触发者", "要")
    assert (await coordinator.service.profile(GROUP, TRIGGER)).affection == 70
    assert "[CQ:image" in str(sent[-1])


async def test_devil_taozi_needs_thirty_to_ask(tmp_path) -> None:
    coordinator = make_coordinator(tmp_path, rng=FixedRollRng(roll=10), stickers=FakeStickers())
    await give(coordinator.service, TRIGGER, 29)
    pending, sent = await devil_auto(coordinator)
    assert "好感不足" in sent[-1].extract_plain_text()
    assert (GROUP, TRIGGER) not in coordinator.sticker_confirms


@pytest.mark.parametrize("roll,delta", [(45, 4), (49, 8), (51, 12), (53, 20), (54, 40),
                                        (55, -4), (68, -8), (79, -12), (87, -20), (94, -40), (98, -60)])
async def test_devil_affection_outcomes(tmp_path, roll, delta) -> None:
    coordinator = make_coordinator(tmp_path, rng=FixedRollRng(roll=roll))
    await give(coordinator.service, TRIGGER, 500)
    pending, _sent = await devil_auto(coordinator)
    assert (await coordinator.service.profile(GROUP, TRIGGER)).affection == 500 + delta
```

- [ ] **Step 2: 跑测试确认失败**

Run: `poetry run pytest tests/test_wheel_devil.py -q -k devil_taozi or devil_affection`
Expected: FAIL（提示里仍是「10 点好感」，或扣 10 而不是 30）

- [ ] **Step 3: 实现按 spec 取价**

- `format_sticker_prompt` 增加 `cost` 参数并格式化进去。
- `PendingStickerConfirm.__init__` 增加 `cost: int`。
- `_run_sticker_outcome()`：`cost = pending.spec.taozi_cost`，不足判断、文案、询问都用它。
- `_request_sticker_confirm()`：把 `pending.spec.taozi_cost` 传给 `PendingStickerConfirm` 和 `format_sticker_prompt`。
- `_answer_sticker_confirm()`：不足判断、扣费、成功文案全部用 `confirm.cost`。
- `format_wheel_rules()` 里桃子那行继续用 `WHEEL_TAOZI_COST`（普通表）。

- [ ] **Step 4: 跑测试确认通过**

Run: `poetry run pytest tests/test_wheel_devil.py tests/test_wheel.py -q`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add nonebot_plugin_mantou_affection/wheel.py tests/test_wheel_devil.py
git commit -m "feat: charge thirty points for devil taozi stickers"
```

---

### Task 6: 恶魔表情五选一

**Files:**
- Modify: `nonebot_plugin_mantou_affection/wheel.py:183-215, 516-693`
- Test: `tests/test_wheel_devil.py`

**Interfaces:**
- Consumes: Task 2 的 `draw_candidates_for_group()`、Task 4 的 `spec`。
- Produces:
  - `PendingMemePick(group_id, user_id, nickname, candidates: list[tuple[Any, str]], *, bot, send, cost: int)`
  - `WheelCoordinator.meme_picks: dict[tuple[str, str], PendingMemePick]`
  - `format_meme_pick(candidates, timeout: int, cost: int) -> str`
  - `_run_meme_pick_outcome()`、`_request_meme_pick()`、`_answer_meme_pick()`、`_expire_meme_pick()`
  - `WHEEL_MEME_PICK_REPLY`、`WHEEL_MEME_PICK_TIMEOUT_REPLY`、`WHEEL_MEME_PICK_INVALID_REPLY`

- [ ] **Step 1: 写失败测试**

`FakeMemes` 增加 `draw_candidates_for_group()`：返回 5 个不同的 `FakeMemeTemplate`，并把这 5 个存进 `self.candidates` 供断言；`generate_with()` 已有，复用。

```python
async def test_devil_meme_lists_five_and_sends_no_image(tmp_path) -> None:
    coordinator = make_coordinator(tmp_path, rng=FixedRollRng(roll=0), memes=FakeMemes())
    await give(coordinator.service, TRIGGER, 50)
    pending, sent = await devil_auto(coordinator)
    body = sent[-1].extract_plain_text()
    assert "已扣除 5 点好感" in body
    for index in range(1, 6):
        assert f"选{index}." in body
    assert "[CQ:image" not in str(sent[-1])
    assert (await coordinator.service.profile(GROUP, TRIGGER)).affection == 45


async def test_devil_meme_pick_generates_chosen_template(tmp_path) -> None:
    memes = FakeMemes()
    coordinator = make_coordinator(tmp_path, rng=FixedRollRng(roll=0), memes=memes)
    await give(coordinator.service, TRIGGER, 50)
    pending, _sent = await devil_auto(coordinator)
    await coordinator.answer(GROUP, TRIGGER, "触发者", "选3")
    assert memes.generated[-1][0] is memes.candidates[2]
    assert (GROUP, TRIGGER) not in coordinator.meme_picks


async def test_devil_bare_digit_does_not_pick(tmp_path) -> None:
    coordinator = make_coordinator(tmp_path, rng=FixedRollRng(roll=0), memes=FakeMemes())
    await give(coordinator.service, TRIGGER, 50)
    pending, _sent = await devil_auto(coordinator)
    assert await coordinator.answer(GROUP, TRIGGER, "触发者", "3") is False
    assert (GROUP, TRIGGER) in coordinator.meme_picks


async def test_devil_meme_pick_timeout_drops_without_refund(tmp_path) -> None:
    coordinator = make_coordinator(tmp_path, rng=FixedRollRng(roll=0), memes=FakeMemes())
    coordinator.meme_confirm_timeout = 0.01
    await give(coordinator.service, TRIGGER, 50)
    pending, sent = await devil_auto(coordinator)
    pick = coordinator.meme_picks[(GROUP, TRIGGER)]
    await asyncio.wait_for(pick.task, 2)
    assert "超时" in sent[-1].extract_plain_text()
    assert (await coordinator.service.profile(GROUP, TRIGGER)).affection == 45


async def test_devil_meme_whitelisted_user_gets_image_without_prompt(tmp_path) -> None:
    memes = FakeMemes()
    coordinator = make_coordinator(tmp_path, rng=FixedRollRng(roll=0), memes=memes)
    await give(coordinator.service, TRIGGER, 50)
    await coordinator.service.add_meme_confirm(TRIGGER)   # 用既有白名单接口，名字以实际为准
    pending, sent = await devil_auto(coordinator)
    assert (GROUP, TRIGGER) not in coordinator.meme_picks
    assert "image" in str(sent[-1])


async def test_devil_meme_pick_does_not_block_settlement_or_next_wheel(tmp_path) -> None:
    coordinator = make_coordinator(tmp_path, rng=FixedRollRng(roll=0), memes=FakeMemes())
    await give(coordinator.service, TRIGGER, 50)
    pending, _sent = await devil_auto(coordinator)
    # 转盘任务已经结束，但五选一还挂着
    assert pending.task.done()
    assert (GROUP, TRIGGER) in coordinator.meme_picks
    # 五选一不占群席位：另一人可以开新一轮转盘
    await give(coordinator.service, OTHER, 300)
    _next, _sent2 = await devil_start(coordinator, user_id=OTHER)
    assert (GROUP, TRIGGER) in coordinator.meme_picks
    # 「参与」不会被五选一当成选择吞掉
    assert await coordinator.answer(GROUP, TRIGGER, "触发者", "参与") is True
    assert (GROUP, TRIGGER) in coordinator.meme_picks
    # 挂起期间仍然可以正常选择
    assert await coordinator.answer(GROUP, TRIGGER, "触发者", "选1") is True
    assert (GROUP, TRIGGER) not in coordinator.meme_picks
    _next.task.cancel()
```

白名单那一步用仓库里真实的接口名（`service.meme_confirm_whitelist()` 是读取；写入走 `/馒头表情免确认` 对应的 service 方法，实现时按实际方法名替换）。

- [ ] **Step 2: 跑测试确认失败**

Run: `poetry run pytest tests/test_wheel_devil.py -q -k devil_meme`
Expected: FAIL，`AttributeError: 'WheelCoordinator' object has no attribute 'meme_picks'`

- [ ] **Step 3: 实现五选一**

- `_run_meme_outcome()` 开头：`if pending.spec.meme_candidates > 1: return await self._run_meme_pick_outcome(...)`。
- `_run_meme_pick_outcome()`：`candidates = await self.memes.draw_candidates_for_group(..., count=pending.spec.meme_candidates)`；失败按现有 `_report_meme_error` 处理且**不扣费**；成功后扣 `pending.spec.meme_cost`；白名单命中则 `self.rng.choice(candidates)` 直接 `_send_chosen_meme()`；否则 `_request_meme_pick()`。
- `_request_meme_pick()`：与 `_request_meme_confirm()` 同构——旧 pick 作废、登记、发 `format_meme_pick()`、挂 `_expire_meme_pick()`。
- `answer()`：在处理「要/不要」之后、`pending` 关键字之前，匹配 `re.fullmatch(r"选([1-9])", keyword)`；命中则 `_answer_meme_pick()`；越界回 `WHEEL_MEME_PICK_INVALID_REPLY`。
- `_answer_meme_pick()`：取消 pick → `generate_with(candidate)` → 发图；生成失败走现有失败文案。
- `_expire_meme_pick()`：超时文案，取消 pick。

- [ ] **Step 4: 跑测试确认通过**

Run: `poetry run pytest tests/test_wheel_devil.py tests/test_wheel.py -q`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add nonebot_plugin_mantou_affection/wheel.py tests/test_wheel_devil.py
git commit -m "feat: add five-way meme pick for devil wheel"
```

---

### Task 7: 恶魔语料库结果

**Files:**
- Modify: `nonebot_plugin_mantou_affection/wheel.py`
- Test: `tests/test_wheel_devil.py`

**Interfaces:**
- Consumes: Task 1 的 `GroupCorpus` / `CorpusError`、Task 4 的 `spec.corpus_cost`。
- Produces:
  - `WheelCoordinator.__init__(..., corpus=None)`
  - `format_corpus_line(line: str, cost: int, affection: int) -> str`
  - `WHEEL_CORPUS_MISSING_REPLY`、`WHEEL_CORPUS_FAILED_REPLY`
  - `_run_corpus_outcome()`

- [ ] **Step 1: 写失败测试**

```python
class FakeCorpus:
    def __init__(self, line: str = "今天也是摆烂的一天", error: Exception | None = None):
        self.line = line
        self.error = error

    def pick(self) -> str:
        if self.error is not None:
            raise self.error
        return self.line


async def test_devil_corpus_charges_five_and_quotes_a_line(tmp_path) -> None:
    coordinator = make_coordinator(tmp_path, rng=FixedRollRng(roll=35), corpus=FakeCorpus())
    await give(coordinator.service, TRIGGER, 50)
    pending, sent = await devil_auto(coordinator)
    body = sent[-1].extract_plain_text()
    assert "今天也是摆烂的一天" in body
    assert "5 点好感" in body
    assert (await coordinator.service.profile(GROUP, TRIGGER)).affection == 45


async def test_devil_corpus_missing_does_not_charge(tmp_path) -> None:
    coordinator = make_coordinator(
        tmp_path, rng=FixedRollRng(roll=35),
        corpus=FakeCorpus(error=CorpusAssetMissing("空")),
    )
    await give(coordinator.service, TRIGGER, 50)
    pending, sent = await devil_auto(coordinator)
    assert "语料库" in sent[-1].extract_plain_text()
    assert (await coordinator.service.profile(GROUP, TRIGGER)).affection == 50


async def test_devil_corpus_floors_affection_at_zero(tmp_path) -> None:
    coordinator = make_coordinator(tmp_path, rng=FixedRollRng(roll=35), corpus=FakeCorpus())
    await give(coordinator.service, TRIGGER, 3)
    pending, _sent = await devil_auto(coordinator)
    assert (await coordinator.service.profile(GROUP, TRIGGER)).affection == 0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `poetry run pytest tests/test_wheel_devil.py -q -k devil_corpus`
Expected: FAIL，`TypeError: __init__() got an unexpected keyword argument 'corpus'`

- [ ] **Step 3: 实现语料库结果**

- `__init__` 增加 `corpus=None` 并保存。
- `_run_operation()` 增加 `elif outcome.kind == "corpus": await self._run_corpus_outcome(...)`。
- `_run_corpus_outcome()`：`corpus is None` 或 `pick()` 抛 `CorpusError` → `WHEEL_CORPUS_MISSING_REPLY`，不扣费；取句成功后 `adjust(-spec.corpus_cost)` 并发 `format_corpus_line(line, -applied, profile.affection)`；`adjust` 抛异常 → 记日志 + `WHEEL_CORPUS_FAILED_REPLY`。

- [ ] **Step 4: 跑测试确认通过**

Run: `poetry run pytest tests/test_wheel_devil.py tests/test_wheel.py -q`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add nonebot_plugin_mantou_affection/wheel.py tests/test_wheel_devil.py
git commit -m "feat: add group corpus outcome to devil wheel"
```

---

### Task 8: 装配——ambient 判定、插件组装、强制事件

**Files:**
- Modify: `nonebot_plugin_mantou_affection/ambient.py:620-650`
- Modify: `nonebot_plugin_mantou_affection/__init__.py:100-112`
- Modify: `nonebot_plugin_mantou_affection/force.py:24-60, 195-210, 298-310`
- Test: `tests/test_ambient.py`、`tests/test_force.py`

**Interfaces:**
- Consumes: Task 3 的 `pick_mode()`、Task 7 的 `corpus=` 参数。
- Produces: `FORCE_ACTIVITY_DEVIL = "devil"`、强制事件第 8 项「小恶魔转盘」；`register_ambient` 在触发转盘前读好感并调 `pick_mode()`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_ambient.py
async def test_ambient_starts_devil_wheel_above_threshold(tmp_path: Path, monkeypatch) -> None:
    config = Config(
        mantou_affection_find_char_chance=0.0,
        mantou_affection_bet_chance=0.0,
        mantou_affection_pk_chance=0.0,
        mantou_affection_ambient_enabled=False,
        mantou_affection_wheel_chance=1.0,
        mantou_affection_wheel_devil_chance=1.0,
    )
    service = _service(tmp_path, config=config)
    events = EventCoordinator(service, config)
    wheel = WheelCoordinator(service, config, events=events, rng=random.Random(0))
    ambient, _ = register_ambient(service, config, events, wheel=wheel)
    sent = _capture_send(ambient, monkeypatch)
    monkeypatch.setattr(
        "nonebot_plugin_mantou_affection.ambient.get_bot", lambda: SimpleNamespace(self_id=999)
    )
    await _give_affection(service, 201)

    await ambient.handlers[0].call(_event())

    assert "小恶魔转盘" in sent[-1].extract_plain_text()
    wheel.pending[GROUP_ID].task.cancel()
```

```python
# tests/test_force.py 参数表新增一行
("8", "wheel", "小恶魔转盘"),
```

```python
# tests/test_force.py 新增
from nonebot_plugin_mantou_affection.wheel import DEVIL_SPEC


async def test_force_devil_choice_starts_devil_wheel(
    tmp_path: Path, bundled_texts_path: Path, monkeypatch
) -> None:
    harness = Harness(tmp_path, bundled_texts_path, monkeypatch)
    await harness.start_command()
    harness.sent.clear()

    await harness.choose("8")

    assert harness.wheel.pending[GROUP_ID].spec is DEVIL_SPEC
    await harness.cleanup()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `poetry run pytest tests/test_ambient.py::test_ambient_starts_devil_wheel_above_threshold tests/test_force.py -q -k devil`
Expected: FAIL（开场文案仍是普通转盘）

- [ ] **Step 3: 接线**

- `ambient.py`：`wheel_ready` 命中后读一次好感（异常记日志按 0），`devil = wheel_coordinator.pick_mode(affection)`，传进 `start(..., devil=devil)`。
- `__init__.py`：`from .wheel_corpus import GroupCorpus`，建 `group_corpus = GroupCorpus(BUNDLED_CORPUS_PATH)`，`WheelCoordinator(..., corpus=group_corpus)`。
- `force.py`：新增常量与第 8 项；`_start_wheel(pending, send, *, devil: bool = False)`；分发里 `FORCE_ACTIVITY_DEVIL` 传 `devil=True`。

- [ ] **Step 4: 跑测试确认通过**

Run: `poetry run pytest tests/test_ambient.py tests/test_force.py tests/test_wheel_devil.py -q`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add nonebot_plugin_mantou_affection/ambient.py nonebot_plugin_mantou_affection/__init__.py \
  nonebot_plugin_mantou_affection/force.py tests/test_ambient.py tests/test_force.py
git commit -m "feat: wire devil wheel into ambient, plugin and force command"
```

---

### Task 9: README 与版本

**Files:**
- Modify: `README.md`（转盘章节、强制事件列表、配置说明、用法速查）
- Modify: `pyproject.toml:3`

**Interfaces:**
- Consumes: 前八个任务的全部行为。
- Produces: 版本 `0.28.0`。

- [ ] **Step 1: 更新 README**

在「馒头命运齿轮转盘」章节后新增「#### 馒头小恶魔转盘」，写清：2/3 概率、>200 门槛、参与费 5、不限人数、60 秒自动开转、不退款、完整权重表、表情五选一（回复「选1」–「选5」）、语料库扣 5 点并说明数据来自群聊学习库且已随包公开、桃子 30 点。同步更新：
- 顶部玩法列表加一行；
- 强制事件列表加「8. 小恶魔转盘」；
- 配置表加 `MANTOU_AFFECTION_WHEEL_DEVIL_CHANCE`、`MANTOU_AFFECTION_WHEEL_DEVIL_MIN_AFFECTION`；
- 用法速查表加 `/馒头强制事件` 第 8 项说明。

- [ ] **Step 2: 升版本**

`pyproject.toml` 的 `version = "0.28.0"`。

- [ ] **Step 3: 全量验证**

Run: `poetry run pytest -q && poetry run ruff check .`
Expected: 全绿、无告警

- [ ] **Step 4: 构建校验**

Run: `poetry build && poetry run twine check dist/*`
Expected: 构建成功，`twine check` PASSED

- [ ] **Step 5: 提交**

```bash
git add README.md pyproject.toml
git commit -m "docs: document devil wheel and bump version to 0.28.0"
```
