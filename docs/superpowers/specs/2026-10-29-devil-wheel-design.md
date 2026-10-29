# 馒头小恶魔转盘设计

日期：2026-10-29

## 目标

在现有「馒头命运齿轮转盘」之外，加一张更狠的第二张表——「馒头小恶魔转盘」。转盘事件触发时按 2/3 概率走恶魔表，1/3 走普通表；恶魔表只有好感度大于 200 的触发者才抽得到。

恶魔表的内容是：表情、桃子表情包、群友语料库、好感增加、好感减少。相比普通表，它去掉了戳一戳、桃子更常见也更贵、好感增加只占 10%、好感加减的量整体放大。

## 已确认的用户规则

- **触发**：转盘事件照旧按 `MANTOU_AFFECTION_WHEEL_CHANCE` 判定；命中后再掷一次，2/3 走恶魔表、1/3 走普通表。触发者好感度 **大于 200** 才允许走恶魔表，不到 200 一律按普通表开这一轮。
- **参与费**：恶魔转盘其他群友参与一次扣 **5 点**好感（普通表仍是 2 点）。
- **人数**：恶魔转盘 **不限人数**（普通表仍是含触发者最多 5 人）。
- **流程**：恶魔转盘 **没有「运作」「不运作」「确认」**，触发后直接进入 60 秒报名，到点自动开转；**没有取消手段，也不退款**。
- **表情**：抽中后扣 **5 点**好感，随机抽 **5 个模板**，**只列名称、分类、键名，不发图**，由本人回复数字 1–5 选一个，再生成图片。
- **群友语料库**：抽中后扣 **5 点**好感，发一句随机群友说过的话。
- **桃子表情包**：抽中后花 **30 点**好感换一张，沿用现有「要 / 不要」询问流程。
- 以上所有规则（门槛、参与费、不限人数、到点自动开、不退款、各项概率、表情与语料库各扣 5、桃子 30 点）都必须在**开场说明消息**里写清楚。

## 恶魔表权重（合计 100）

| 结果 | 权重 | 明细 |
|---|---:|---|
| 表情 | 10% | 抽中扣 5 点，五选一 |
| 桃子表情包 | 25% | 换一张 30 点 |
| 群友语料库 | 10% | 抽中扣 5 点 |
| 好感增加 | 10% | +4 4%、+8 2%、+12 2%、+20 1%、+40 1% |
| 好感减少 | 45% | -4 13%、-8 11%、-12 8%、-20 7%、-40 4%、-60 2% |

平均每次约 **-5.4** 点（普通表约 +0.15）。负向权重接近一半，数值整体是普通表的 4 倍起步。

## 方案

### 一、代码组织：参数化（选定）

在 `wheel.py` 里引入 `WheelSpec`，把两张表的差异收敛成数据，同一个 `WheelCoordinator` 跑两套：

```python
@dataclass(frozen=True)
class WheelSpec:
    name: str
    outcomes: tuple[tuple[WheelOutcome, int], ...]
    join_cost: int
    max_participants: int | None   # None = 不限
    auto_start: bool               # True = 到点自动开，无运作/不运作/确认
    refund: bool                   # 未开转时是否退参与费
    meme_cost: int                 # 抽中表情扣多少，0 = 不扣
    meme_candidates: int           # 表情候选数，1 = 直接确认/生成
    corpus_cost: int               # 抽中语料库扣多少
    taozi_cost: int
```

`NORMAL_SPEC` 复刻现有行为（`auto_start=False`、`max_participants=4`、`join_cost=2`、`meme_cost=0`、`meme_candidates=1`、`corpus_cost=0`、`taozi_cost=10`、`refund=True`），`DEVIL_SPEC` 按上表配置（`auto_start=True`、`max_participants=None`、`join_cost=5`、`meme_cost=5`、`meme_candidates=5`、`corpus_cost=5`、`taozi_cost=30`、`refund=False`）。

被否掉的两个方案：新开 `wheel_devil.py` 独立类（报名、超时、pending 字典、表情/桃子确认状态都要复制一份，改一处得改两处）；直接在现有方法里堆 `if pending.devil`（流程分支散落各处，最难维护）。

### 二、语料库：打包进 wheel（选定）

1 万条语料随包发布到 PyPI/GitHub，bot 从国内镜像装包即自带，不新增下载环节、没有素材失败路径。代价是群友发言会公开——用户已确认接受。

被否掉的方案：像桃子表情包那样放独立仓库走镜像链下载（包体小一点，但仓库同样公开，还要多一套下载与失败处理）。

## 触发与流程

### 触发判定

`WheelCoordinator.pick_mode(affection) -> bool`：`affection > 200` 且 `rng.random() < mantou_affection_wheel_devil_chance`（默认 0.667）时返回 `True`。纯函数、不读写存储，便于测试。

`ambient.py` 在 `service.wheel_triggered()` 命中后读一次触发者好感度，调 `pick_mode()` 得到 `devil`，再传给 `WheelCoordinator.start(..., devil=...)`。

`start()` 的 `devil` 参数默认 `False`，既有调用方（强制事件、现有测试）行为不变。

### 开场消息

```
@群友 馒头小恶魔转盘出现了！
只有好感度大于 200 的群友才会抽到恶魔转盘。
含触发者不限人数，其他群友发送“参与”即可加入，参与一次扣 5 点好感度。
本轮不需要“运作”或“确认”，60 秒后自动开转，每人独立抽取一次；开转后不退款。
恶魔转盘各项概率：
· 馒头制作随机表情 10%（抽中扣 5 点好感，从 5 个模板里选 1 个）
· 桃子表情包 25%（换一张需 30 点好感）
· 群友语料库 10%（抽中扣 5 点好感）
· 好感增加：+4 4%、+8 2%、+12 2%、+20 1%、+40 1%
· 好感减少：-4 13%、-8 11%、-12 8%、-20 7%、-40 4%、-60 2%
```

文案由 `format_devil_wheel_rules(timeout, min_affection)` 生成：门槛数字读 `MANTOU_AFFECTION_WHEEL_DEVIL_MIN_AFFECTION`，权重读恶魔表，两者改了文案自动同步，和现有 `format_wheel_rules` 同样的做法。

### 报名与超时

- 「参与」扣 5 点，好感不足 5 点拒绝；不限人数；重复参与、触发者自己参与仍给提示。
- 触发者在 60 秒内发「运作」「不运作」「确认」：统一回一句「恶魔转盘不需要选择，60 秒后自动开转」，不做任何结算。
- 60 秒到点：`_expire` 直接调用 `_run_operation`，不取消、不退款。
- 触发者与所有参与者按加入顺序各抽一次，互不影响。

## 各项结果

### 好感变化

走现有 `service.adjust` 路径，提示实际生效的变化，受 0 和上限约束。

### 表情：五选一

1. 从可用模板里随机抽 `min(5, 可用数)` 个，互不重复。筛选沿用 `draw_for_group()` 的口径（好感榜人数决定 `max_images`），保证选中的模板一定生成得出来。
2. 抽成功后扣 5 点好感；抽模板失败（生成器未装、素材缺失）不扣好感，按现有失败文案提示，不重抽。
3. 发一条只含信息的消息，不带图片：

```
@群友 你抽中了「随机表情」，已扣除 5 点好感。回复「选1」到「选5」挑一个，30 秒内有效：
选1. 摸摸头（种类：可爱、宠物，键名：petpet）
选2. ……
```

4. **回复格式是「选1」–「选5」，不是裸数字**。原因：`ambient.py` 的作答路由里 `wheel_coordinator.answer()` 排在最前，裸 `1`–`5` 会把群友问答、随机问答、博弈和找字的答案抢走；带前缀彻底避开冲突。
5. 回复「选N」：用对应模板生成图片并发送。
6. 超时、回复其他内容、或生成失败：都不制作，提示一句，已扣的 5 点不退、不重抽。
7. 同一人已有未决选择时又抽中表情，旧选择作废，以新的为准。
8. 表情免确认白名单用户在恶魔表里**从 5 个候选里随机取一个直接生成**，不发选择提示——白名单的语义是「不要再问我」，保持这个承诺。
9. 五选一流程与普通表的「确认 / 不确认」互不影响，各有各的 pending 键，可以同时挂着。

### 群友语料库

1. 从内置语料里随机取一句。
2. 取成功后扣 5 点好感，发：

```
@群友 群友语料库：已扣 5 点好感。
馒头记得有群友说过：「xxx」
```

3. 语料文件缺失或为空：不扣好感，只提示一句「语料库还没准备好，本次不扣好感」，不重抽。

### 桃子表情包

沿用现有询问流程，只把价格换成 30 点：抽中后先查好感，够 30 才询问；回复「要」时重新核对好感、先取图再扣费；「不要」、超时、好感不足、素材缺失都不扣费。

## 群友语料库的取数与存储

来源：用户提供的 `learning.db`（`nonebot_plugin_chat_learning` 的学习库，19.5 万问题 / 27.7 万回答）。本次实现使用会话附件里的那一份（63 MB，SQLite），路径：

```
/Users/liangpuyue/.kimi-code/sessions/wd_nonebot-plugin-mantou-affection_b0a50cdbb432/session_0ed5506a-23b2-4e6a-9624-9636ca4daeee/attachments/f_b5aa9f3a-f4bc-4501-b4da-084acbf4c252-learning.db
```

交付物里必须包含用这份库真实生成的 10,000 条，不是占位或合成数据。

筛选条件：

1. `chain_json` 里所有消息段都是 `text`（含图片、表情、语音、视频、卡片、at、回复的一律不要）。
2. 拼接并 `strip()` 后长度 2–60。
3. 排除纯符号、纯数字。
4. 排除含 `http` / `www.` 的行。
5. 问题侧与回答侧都收，按文本去重。

结果：162,368 条候选，用固定种子随机抽 10,000 条，排序后写入 `nonebot_plugin_mantou_affection/resources/group_corpus.json`，格式 `{"lines": ["…", "…"]}`，约 245 KB。

同时提交 `scripts/build_group_corpus.py`，把上面的筛选和抽样固化下来；以后要扩到 2 万、5 万条，换掉 `--count` 重跑即可（脚本需要 `learning.db` 路径作为参数，不依赖仓库内文件）。

运行期：`GroupCorpus` 类在导入时惰性加载 JSON，`pick(rng)` 返回一句；加载失败抛 `CorpusAssetMissing`。

## 代码改动清单

- `wheel.py`
  - 新增 `WheelSpec`、`NORMAL_SPEC`、`DEVIL_SPEC`、`DEVIL_WHEEL_OUTCOMES`。
  - `draw_wheel_outcome(rng, outcomes=WHEEL_OUTCOMES)` 增加表参数（默认值保持兼容）。
  - 新增 `format_devil_wheel_rules()`、`format_meme_pick()`、`format_corpus_line()` 等文案函数。
  - `PendingWheel` 增加 `spec`；新增 `PendingMemePick`。
  - `start()` 增加 `devil` 参数；`answer()` 增加恶魔表分支；`_join` / `_expire` / `_run_operation` / `_run_meme_outcome` / `_run_sticker_outcome` 按 `spec` 取值。
  - 新增 `pick_mode()`、`_run_corpus_outcome()`、`_request_meme_pick()`、`_answer_meme_pick()`、`_expire_meme_pick()`。
- `wheel_corpus.py`（新文件）：`GroupCorpus`、`CorpusAssetMissing`、`CorpusError`。
- `wheel_memes.py`：把 `draw()` 的候选筛选抽成 `_candidates(max_images)`，新增 `draw_candidates(count)` 返回互不重复的 `(meme, kind)` 列表（`draw()` 改为从同一列表里取一个，行为不变）；`draw_for_group()` 保持不变。
- `resources/group_corpus.json`（新文件）：1 万条语料。
- `config.py`：新增 `mantou_affection_wheel_devil_chance`（默认 0.667，0–1）与 `mantou_affection_wheel_devil_min_affection`（默认 200，0–999999）。
- `ambient.py`：触发判定后读好感、调 `pick_mode()`、传 `devil`。
- `__init__.py`：组装 `GroupCorpus` 并注入 `WheelCoordinator`。
- `force.py`：强制事件新增第 8 项「恶魔转盘」，直接以 `devil=True` 开局（SUPERUSER 用，绕过 200 门槛和 2/3 掷骰）。
- `README.md`：恶魔表、玩法、门槛、参与费、语料库来源与公开说明、配置项。
- `pyproject.toml`：版本升到 `0.28.0`。

## 测试

新增 `tests/test_wheel_devil.py`：

- 恶魔表权重合计 100，且与本文档一致。
- `pick_mode()`：200 及以下返回 `False`；201 以上按 2/3 掷骰。
- 开场文案包含门槛、参与费、不限人数、自动开转、不退款、表情/语料库各扣 5、桃子 30 点。
- 参与扣 5 点、好感不足拒绝、不限人数（6 人以上仍可加入）。
- 「运作」「不运作」「确认」被忽略且不结算。
- 到点自动开转，触发者与参与者各抽一次；没有退款。
- 表情：抽 5 个候选、只发信息不发图、扣 5 点、回复「选N」生成、超时/非法输入不制作、白名单直接生成。
- 表情：「选1」不会和群友问答/随机问答/博弈/找字的裸数字答案互相抢；裸 `1` 不触发五选一。
- 语料库：扣 5 点并发出一句；语料缺失时不扣好感。测试用 `tmp_path` 里的小 fixture，不读真实 1 万条。
- 桃子：30 点，不足 30 不询问。
- 普通转盘行为不变（既有 `tests/test_wheel.py` 全部保持通过）。
- `draw_candidates()` 返回互不重复的候选，数量受可用模板数限制。

## 非目标

- 不改普通转盘的权重、参与费、人数上限、运作/确认流程。
- 不做语料库的下载、增量更新或在线审核；语料一次性打包，更新靠发新版本。
- 不新增恶魔表的独立概率命令，触发概率仍由 `/馒头转盘概率` 统一管理。
- 不做恶魔表的单独开关命令（`MANTOU_AFFECTION_WHEEL_DEVIL_CHANCE=0` 即关闭）。

## 留给实现的决定

- 表情五选一的超时沿用 `MANTOU_AFFECTION_WHEEL_MEME_CONFIRM_TIMEOUT`（默认 30 秒），不新增配置。
- 恶魔表沿用 `MANTOU_AFFECTION_WHEEL_TIMEOUT`（默认 60 秒）作为报名窗口。
- 扣费时机：表情在成功抽出 5 个候选后扣，语料库在成功取句后扣——功能本身失败时不扣好感。
- 好感不足 5 点时照常扣到 0（沿用 `service.adjust` 的下限语义），不拒绝。
