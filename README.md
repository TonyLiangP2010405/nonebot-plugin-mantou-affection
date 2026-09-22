# nonebot-plugin-mantou-affection

为群聊机器人“馒头”设计的 NoneBot2 共享好感度系统。群友通过 `/馒头互动` 以及现有插件的真实互动积累好感；每日运势、戳一戳、水群榜和桃纸助手会读取同一份关系状态，逐渐改变文案、语气与展示。

## 功能

- 好感数据按“群号 + 用户”隔离
- 主动互动、冷却时间和每日次数限制
- 七级好感称号和群排行榜
- 自动感知其他插件的命令、正则及完整匹配 Matcher
- 每插件独立冷却、每日联动奖励上限，避免刷分
- SUPERUSER 手动增减群友好感度
- 群友发言时馒头按概率冒泡的小动作（好感度 >0 才触发，默认 1%）
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
MANTOU_AFFECTION_RANKING_SIZE=10
MANTOU_AFFECTION_LINK_ENABLED=true
MANTOU_AFFECTION_LINK_DAILY_LIMIT=10
MANTOU_AFFECTION_LINK_COOLDOWN=300
MANTOU_AFFECTION_AMBIENT_ENABLED=true
MANTOU_AFFECTION_AMBIENT_PROBABILITY=0.01
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

`MANTOU_AFFECTION_AMBIENT_ENABLED` 控制群友发言时馒头是否可能冒泡，`MANTOU_AFFECTION_AMBIENT_PROBABILITY`
是单次触发的概率（0～1，默认 1%）。SUPERUSER 也可以用 `/馒头反应概率` 在运行时查看或覆盖，覆盖值写进
数据文件，重启后依然生效。群友发言时，只要他对馒头的好感度大于 0，馒头就有这个概率 @他冒出一句小动作
旁白；旁白不引用发言内容，已被命令接管的发言也不会触发，不会影响正常对话。

### 大型文案库

内置文案位于 `nonebot_plugin_mantou_affection/resources/affection_texts.json`，按“插件场景 + 好感阶段”组织。
`mantou.interact`、`daily_attendance.fortune`、`crystelf.poke`、`msg_rank_card.rank`、`taozi.fortune` 和
`taozi.lexicon` 各 5 个阶段 × 1000 条；`mantou.ambient` 是馒头在群友发言时按概率冒泡的
小动作旁白，每个阶段 2000 条；全库合计 40000 条。
每个场景分别配置 `neutral`、`warm`、`close`、`flirty`、`intimate` 数组。

`/馒头互动` 的回复取自 `mantou.interact` 场景：好感度奖励仍按原有分布随机（0～3 点），回复文案则随互动后的
好感阶段变化，从初见时的礼貌疏远逐渐变成亲密；文案中的 `{bot}` 会被替换为配置的机器人名字。
`msg_rank_card.rank` 场景为水群榜卡片上每位群友名字旁的极短好感短评（4～16 字），随好感阶段变化。
`mantou.ambient` 场景只描写馒头自己的动作神态（4～30 字，不需要 `{bot}` 占位符），不回应群友说了什么，
语气同样随好感阶段从礼貌走向亲密。

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
| `/馒头互动` | 群员 | 群聊 | 和馒头互动并获得 0～3 点好感（每日 5 次，间隔至少 2 小时） |
| `/馒头好感` | 群员 | 群聊 | 查看好感度、关系称号和联动额度 |
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

则按配置向发起者静默增减好感度。普通 `on_message` 监听器不会自动变化，避免聊天学习、消息统计等插件在每条群消息上刷分。每日联动上限按变化值的绝对值计算。

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

`add_affection` 与 `change_affection` 都会遵守每日联动上限和同一 `source` 的冷却，并返回实际变化值。`add_affection` 只接受正数，`change_affection` 接受正负数；两个读取 API 不会改变数据。

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
