# nonebot-plugin-mantou-affection

为群聊机器人“馒头”设计的 NoneBot2 共享好感度系统。群友通过 `/馒头互动` 以及现有插件的真实互动积累好感；每日运势、戳一戳、水群榜和桃纸助手会读取同一份关系状态，逐渐改变文案、语气与展示。

## 功能

- 好感数据按“群号 + 用户”隔离
- 主动互动、冷却时间和每日次数限制（约 1/7 概率惹馒头不高兴，好感度 -1）
- 七级好感称号和群排行榜
- 自动感知其他插件的命令、正则及完整匹配 Matcher
- 每插件独立冷却、每日联动奖励上限，避免刷分
- 互动与联动共享每日好感获取总上限，最快约一年满级；额度用满后互动不会再扣好感
- SUPERUSER 手动增减群友好感度
- 群友发言时馒头按概率冒泡的小动作（好感度 >0 才触发，默认 1%），其中约 1/3 会升级为限时随机事件
- 戳一戳当天累进不耐烦，戳太多馒头会不想理你（`poke` API 一行接入）
- 为其他插件提供好感上报与状态读取 API
- 使用 `nonebot-plugin-localstore` 和原子写入持久化数据

## 安装

### 使用 nb-cli

```bash
nb plugin install nonebot-plugin-mantou-affection
```

### 使用 pip

```bash
pip install nonebot-plugin-mantou-affection
```

### 使用 Poetry

```bash
poetry add nonebot-plugin-mantou-affection
```

本地开发阶段也可以把 `nonebot_plugin_mantou_affection` 放进机器人项目的插件目录加载。

## 配置

全部配置均有默认值，零配置即可加载。在 NoneBot 项目的 `.env` 或对应环境文件中按需修改：

```env
MANTOU_AFFECTION_BOT_NAME=馒头
MANTOU_AFFECTION_TIMEZONE=Asia/Shanghai
MANTOU_AFFECTION_TEXT_PATH=
MANTOU_AFFECTION_INTERACTION_LIMIT=5
MANTOU_AFFECTION_INTERACTION_COOLDOWN=7200
MANTOU_AFFECTION_MAX=999
MANTOU_AFFECTION_DAILY_GAIN_LIMIT=3
MANTOU_AFFECTION_RANKING_SIZE=10
MANTOU_AFFECTION_LINK_ENABLED=true
MANTOU_AFFECTION_LINK_NOTIFY=true
MANTOU_AFFECTION_LINK_DAILY_LIMIT=10
MANTOU_AFFECTION_LINK_COOLDOWN=300
MANTOU_AFFECTION_AMBIENT_ENABLED=true
MANTOU_AFFECTION_AMBIENT_PROBABILITY=0.01
MANTOU_AFFECTION_AMBIENT_EVENT_RATIO=0.333
MANTOU_AFFECTION_EVENT_TIMEOUT=20
MANTOU_AFFECTION_EVENT_TIMEOUT_PENALTY=5
MANTOU_AFFECTION_POKE_NEGATIVE_BASE=0.1
MANTOU_AFFECTION_POKE_MAX_PENALTY=5
MANTOU_AFFECTION_POKE_IGNORE_THRESHOLD=10
```

`MANTOU_AFFECTION_LINK_REWARDS` 是“插件模块名 -> 单次变化值”的 JSON 对象，支持正数和负数。默认值如下：

```json
{
  "nonebot_plugin_taozi": 2,
  "nonebot_plugin_taozi_music": 2,
  "nonebot_plugin_xianmei": 1,
  "nonebot_plugin_daily_attendance": 2,
  "nonebot_plugin_crystelf": 1,
  "nonebot_plugin_chat_learning": 1,
  "nonebot_plugin_msg_rank_card": 1,
  "nonebot_plugin_bili_dynamic": 1,
  "nonebot_plugin_miao": 1
}
```

`MANTOU_AFFECTION_DAILY_GAIN_LIMIT` 是每天通过互动和联动最多能获得的好感度总和（默认 3 点，互动与联动共享
同一份额度）。好感上限是 999，按默认值最快约 333 天满级；如果想严格满一年以上，可以设为 2（约 500 天）。
SUPERUSER 手动增减不受这个上限限制，`/馒头好感` 会显示当天已获取的点数。

额度用完之后不会再让好感倒退：`/馒头互动` 抽到 -1 时按 0 处理（`delta=0`），正向奖励也截断到 0，这时互动
仍然算一次、只是不再加分，文案会补一行「今天的好感已经拿满啦，明天再来吧。」；抽到发呆档（原始奖励就是
0）则直接用互动池里配对的那句旁白，不套用阶段文案，避免暗示加了好感。

`MANTOU_AFFECTION_AMBIENT_ENABLED` 控制群友发言时馒头是否可能冒泡，`MANTOU_AFFECTION_AMBIENT_PROBABILITY`
是单次触发的概率（0～1，默认 1%）。SUPERUSER 也可以用 `/馒头反应概率` 在运行时查看或覆盖，覆盖值写进
数据文件，重启后依然生效。群友发言时，只要他对馒头的好感度大于 0，馒头就有这个概率 @他冒出一句小动作
旁白；旁白不引用发言内容，已被命令接管的发言也不会触发，不会影响正常对话。

`MANTOU_AFFECTION_AMBIENT_EVENT_RATIO` 是小动作命中后升级为随机事件的比例（默认 0.333，也就是约 1/3）。
`MANTOU_AFFECTION_EVENT_TIMEOUT` 是答题时限（秒，默认 20），`MANTOU_AFFECTION_EVENT_TIMEOUT_PENALTY`
是超时扣除的好感度点数（默认 5）。

`MANTOU_AFFECTION_POKE_NEGATIVE_BASE` 是戳一戳不耐烦概率的累进基数（默认 0.1，即当天第 1 戳 10%、第 2 戳
20%，第 10 戳起必定不耐烦）。`MANTOU_AFFECTION_POKE_MAX_PENALTY` 是单次不耐烦最多扣多少点（默认 5，扣分
随当天次数累进，第 5 戳及以后封顶 -5）。`MANTOU_AFFECTION_POKE_IGNORE_THRESHOLD` 是「不想理你」的当天
次数阈值（默认 10，达到后不再判定概率，直接固定文案）。

### 随机事件

小动作触发时有约 1/3 概率变成一次限时随机事件，馒头会在群里抛出一个三选一的场景，并 @触发它的群友：

```
@群友 ⚡ 触发随机事件！
馒头的小本子从桌上滑下去，页角折了一道印子，它蹲在地上看了很久。
1. 把本子捡起来递回去，说下次放稳一点
2. 先蹲下来把折角一页页抚平，再问它今天记了些什么
3. 说一本本子而已，回头给你买个更贵的
请在 20 秒内作答，直接发送 1、2、3 即可（答题不用@），超时好感度 -5！
```

**群里任何人都可以作答**，直接发 `1`、`2` 或 `3` 即可（不用 @机器人），每人只算第一次；发别的内容不算
作答，会一直等到窗口结束。同一个群同时只有一个事件，上一次还没结束就不会再触发新的（这时新的小动作会
回退成普通旁白）。

三个选项的顺序每次都会重新打乱，看起来都很合理，但只有最懂馒头心思的那个是最好的：最优 +2、普通 +1、
最差 -2，各自的加减记在**答题者自己**的好感度上。窗口结束时发一条合并消息，按答题先后每人一行；触发者
必须作答，否则扣超时 -5（点数可在配置里调整）：

```
@甲 馒头开心地收下了「先蹲下来把折角一页页抚平，再问它今天记了些什么」，好感度 +2，当前 12
@乙 馒头嫌弃地躲开了「说一本本子而已，回头给你买个更贵的」，好感度 -2，当前 3
@触发者 馒头等不到你的回答，失望地走开了，好感度 -5，当前 5
```

事件带来的好感变化走管理员调整同一条路径，不受每日获取总上限和联动冷却限制。另外，`/馒头互动` 现在也有
约 1/7 的概率惹馒头不高兴，好感度 -1（好感度为 0 时不会变成负数），这时回复会换成一套「小情绪」文案。

### 大型文案库

内置文案位于 `nonebot_plugin_mantou_affection/resources/affection_texts.json`，按“插件场景 + 好感阶段”组织。
`mantou.interact`、`daily_attendance.fortune`、`crystelf.poke`、`msg_rank_card.rank`、`taozi.fortune` 和
`taozi.lexicon` 各 5 个阶段 × 1000 条；`mantou.ambient` 是馒头在群友发言时按概率冒泡的
小动作旁白，每个阶段 2000 条；这些大场景合计 40000 条。
每个场景分别配置 `neutral`、`warm`、`close`、`flirty`、`intimate` 数组。

`/馒头互动` 的回复取自 `mantou.interact` 场景：好感度奖励仍按原有分布随机（-1～3 点），回复文案则随互动后的
好感阶段变化，从初见时的礼貌疏远逐渐变成亲密；文案中的 `{bot}` 会被替换为配置的机器人名字。抽到发呆档
（奖励 0）时会直接用互动池里配对的那句旁白，不走阶段文案。
`msg_rank_card.rank` 场景为水群榜卡片上每位群友名字旁的极短好感短评（4～16 字），随好感阶段变化。
`mantou.ambient` 场景只描写馒头自己的动作神态（4～30 字，不需要 `{bot}` 占位符），不回应群友说了什么，
语气同样随好感阶段从礼貌走向亲密。

`mantou.interact.negative` 是互动扣好感时专用的「小情绪」场景（10～40 字，可用 `{bot}`）：`neutral` 疏远
尴尬、`warm` 小委屈、`close` 闹别扭、`flirty` 吃醋失落、`intimate` 受伤但包容。`crystelf.poke.negative`
是同结构的被戳烦了的短情绪（8～40 字，不含占位符），供 Crystelf 戳一戳插件在戳烦时使用。这两个场景
目前每个阶段先放 4 条占位文案，之后会继续扩充。

随机事件库位于 `nonebot_plugin_mantou_affection/resources/affection_events.json`，结构为“场景 + 三个选项”：

```json
{
  "events": [
    {
      "text": "场景描述",
      "options": [
        { "text": "最优选项", "delta": 2 },
        { "text": "普通选项", "delta": 1 },
        { "text": "最差选项", "delta": -2 }
      ]
    }
  ]
}
```

每个事件必须正好 3 个选项，`delta` 必须是 `+2`、`+1`、`-2` 各一个（发送前会打乱顺序再编号）；结构不合规
的条目会被跳过并记录警告。事件库为空时随机事件自动退回普通小动作旁白。

如果想要覆盖内置文案或继续扩充，可以把同结构 JSON 放在 bot 的数据目录外，并设置：

```env
MANTOU_AFFECTION_TEXT_PATH=/absolute/path/to/mantou_affection_texts.json
```

外部文件只需写想覆盖的场景或阶段，其他部分继续使用内置文案。插件会检查文件修改时间并自动重载，
不需要改 Python 代码；格式错误时会保留内置文案并记录警告。戳一戳默认随机抽取，运势、桃签等场景
可以传入日期等 `seed`，让同一天的文案保持稳定。

例如只保留桃纸助手和每日运势联动：

```env
MANTOU_AFFECTION_LINK_REWARDS={"nonebot_plugin_taozi":2,"nonebot_plugin_daily_attendance":2}
```

## 使用方法

| 指令 | 权限 | 范围 | 说明 |
|---|---|---|---|
| `/馒头互动` | 群员 | 群聊 | 和馒头互动，好感 -1～+3 点（每日 5 次，间隔至少 2 小时；受每日获取总上限约束，额度满后只扣次数不扣好感） |
| `/馒头好感` | 群员 | 群聊 | 查看好感度、关系称号、今日获取量和联动额度 |
| `/馒头好感榜` | 群员 | 群聊 | 查看本群好感度排行榜 |
| `/馒头好感帮助` | 群员 | 群聊 | 查看菜单 |
| `/馒头好感调整 @群友 +10` | SUPERUSER | 群聊 | 手动增加或扣除好感度 |
| `/馒头反应概率 5%` | SUPERUSER | 群聊/私聊 | 查看或调整小动作触发概率（持久保存） |

## 插件联动

插件注册了 NoneBot `run_postprocessor`。其他插件的 Matcher 成功执行后，如果：

1. 来源插件出现在奖励表中；
2. 事件来自群聊；
3. Matcher 是命令、正则、完整匹配、关键词、开头或结尾匹配之一；
4. 本次执行没有抛出异常；
5. 没有触发该插件的联动冷却或每日上限；

则按配置增减发起者的好感度，并在好感真正变化时补一条轻量提示（`MANTOU_AFFECTION_LINK_NOTIFY`，默认开启，
提示形如「馒头好感度 +2，当前 12」，不 @任何人；发送失败只记日志，不影响原插件）。普通 `on_message` 监听器不会自动变化，避免聊天学习、消息统计等插件在每条群消息上刷分。每日联动上限按变化值的绝对值计算；正向奖励还要与 `/馒头互动` 共享每日获取总上限，额度用完后只做截断，不再增加好感度。

### 主动上报 API

对于自动推送、戳一戳、献媚成功等无法从 Matcher 类型准确判断的事件，其他插件可以软依赖本插件并主动上报：

```python
from nonebot import require

require("nonebot_plugin_mantou_affection")

from nonebot_plugin_mantou_affection import (
    add_affection,
    change_affection,
    get_affection,
    get_affection_response,
    get_affection_snapshot,
    poke,
)

actual = await add_affection(
    group_id=event.group_id,
    user_id=event.user_id,
    delta=2,
    nickname=event.sender.card or event.sender.nickname,
    source="nonebot_plugin_xianmei:flatter",
)

# 也可以让某个明确行为降低好感度
actual = await change_affection(
    group_id=event.group_id,
    user_id=event.user_id,
    delta=-1,
    nickname=event.sender.card or event.sender.nickname,
    source="another_plugin:bad_action",
)

# 当前数值，以及适合调整语气的 neutral/warm/close/flirty/intimate 分层
score = await get_affection(event.group_id, event.user_id)
snapshot = await get_affection_snapshot(event.group_id, event.user_id)
print(snapshot.affection, snapshot.level, snapshot.title, snapshot.band)

# 按场景抽取文案；seed 非空时同一输入稳定返回同一句
response = await get_affection_response(
    "crystelf.poke",
    event.group_id,
    event.user_id,
)
print(response.text)
```

`add_affection` 与 `change_affection` 都会遵守每日联动上限、每日获取总上限和同一 `source` 的冷却，并返回实际变化值。`add_affection` 只接受正数，`change_affection` 接受正负数，其中负向变化不受每日获取总上限限制；两个读取 API 不会改变数据。

### 戳一戳 API

戳一戳的整套判定收在本插件里，调用方一行接入即可，不用自己维护概率或计数：

```python
from nonebot_plugin_mantou_affection import PokeResult, poke

result: PokeResult = await poke(
    group_id=event.group_id,
    user_id=event.user_id,
    nickname=event.sender.card or event.sender.nickname,
)
await matcher.finish(result.text)
```

`PokeResult` 的四个字段：`delta`（本次实际好感变化，正/0/负）、`text`（回复文案，已替换 `{bot}`）、
`count`（这是今天第几次戳）、`annoyed`（本次是否不耐烦，含「不想理你」阶段）。

机制：

1. 每次戳先按当天日期重置计数再自增，`count` 即当天的第几次。
2. 当天戳满 `MANTOU_AFFECTION_POKE_IGNORE_THRESHOLD`（默认 10 次），或好感度**当天被扣到 0** 时进入无语
   阶段：`annoyed=True`，文案固定为「馒头理都不想理你。」，扣分照常累进但好感度不会低于 0。
   这里看的是「历史最高好感大于 0、当前为 0、且归零发生在今天」：从未有过好感的新群友，以及昨天归零、
   今天还是 0 的群友，都照常走下面的概率判定，能重新把好感一点点攒回来。
3. 其余情况按 `count × MANTOU_AFFECTION_POKE_NEGATIVE_BASE`（上限 100%）判定不耐烦：命中则
   `annoyed=True`，扣 `min(count, MANTOU_AFFECTION_POKE_MAX_PENALTY)` 点（第 3 戳 -3、第 5 戳起 -5），
   文案取 `crystelf.poke.negative` 场景、按扣完后的好感阶段。**历史最高好感为 0 的群友（从没攒到过好感）
   不参与这条判定**，永远走下面的正面 +1 路径，不会被嫌弃。
4. 未命中则 `annoyed=False`、`delta=+1`，这个 +1 受每日获取总上限约束（额度用完时 `delta=0` 但仍返回
   文案），入账部分计入当日额度；它不走联动冷却，也不占用 `linked_points`。
5. `text` 在 `delta != 0` 时末尾会追加一行「好感度 +1，当前 12」这样的提示；`delta=0` 时只有文案本身。
6. 文案统一做 `{bot}` → 配置机器人名替换；文案库缺失或场景为空时回退为「馒头朝你笑了笑。」（正面）或
   「馒头往旁边挪了挪。」（不耐烦），无语阶段固定文案不变。

当前工作区还完成了四类展示联动：每日运势随关系阶段改写短句；Crystelf 戳一戳会增加好感并改变回复；水群榜展示每位群友的馒头好感；桃纸助手的桃签和词典卡片会出现不同程度的“馒头私语”。所有接入均为软依赖，好感插件未加载时原插件照常运行。

当前工作区中的 `nonebot-plugin-xianmei` 已在真正发出献媚文案时上报 `+2`，`nonebot-plugin-chat-learning` 已在真正回复成功时上报 `+1`；两处均为软联动，好感度插件未加载时不会影响原插件运行。

## 称号

| 好感度 | 称号 |
|---:|---|
| 0 | 初次见面 |
| 10 | 有点眼熟 |
| 30 | 愿意靠近 |
| 60 | 悄悄在意 |
| 100 | 暧昧升温 |
| 160 | 特别偏爱 |
| 250 | 心照不宣 |

## 数据存储

数据由 `nonebot-plugin-localstore` 保存为插件数据目录中的 `affection.json`。文件采用临时文件写入后原子替换，插件不会在 Python 安装包目录内写运行数据。

## 开发与测试

```bash
poetry install
poetry run python -m compileall nonebot_plugin_mantou_affection
poetry run pytest
poetry run ruff check .
```

## 许可证

MIT
