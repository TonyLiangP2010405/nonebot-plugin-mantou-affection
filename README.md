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
- 群友发言时馒头按概率冒泡的小动作（好感度 >0 才触发，默认 0.1%），其中约 1/3 会升级为限时随机事件
- 群友发言时另有 1% 概率开一局「找字小游戏」：从题库抽一道二字型或三字型找字题，30 秒内抢答
- 戳一戳稳定 +1（受每日获取总上限约束），1% 概率戳出一次扣分翻倍的答题事件、1% 概率戳出一局找字小游戏
- 「馒头博弈」数字顺序竞猜：由水群榜等插件通过 `maybe_start_bet` API 开局，三人各猜一个 1~5 的顺序，
  输家把好感度转给赢家
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
MANTOU_AFFECTION_AMBIENT_PROBABILITY=0.001
MANTOU_AFFECTION_AMBIENT_EVENT_RATIO=0.333
MANTOU_AFFECTION_EVENT_TIMEOUT=20
MANTOU_AFFECTION_EVENT_TIMEOUT_PENALTY=5
MANTOU_AFFECTION_POKE_EVENT_CHANCE=0.01
MANTOU_AFFECTION_FIND_CHAR_CHANCE=0.01
MANTOU_AFFECTION_POKE_FIND_CHAR_CHANCE=0.01
MANTOU_AFFECTION_FIND_CHAR_TIMEOUT=30
MANTOU_AFFECTION_BET_CHANCE=0.005
MANTOU_AFFECTION_BET_WINDOW=60
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
是单次触发的概率（0～1，默认 0.1%）。SUPERUSER 也可以用 `/馒头反应概率` 在运行时查看或覆盖，覆盖值写进
数据文件，重启后依然生效。群友发言时，只要他对馒头的好感度大于 0，馒头就有这个概率 @他冒出一句小动作
旁白；旁白不引用发言内容，已被命令接管的发言也不会触发，不会影响正常对话。

`MANTOU_AFFECTION_FIND_CHAR_CHANCE` 是群消息路径的找字小游戏概率（默认 0.01，即 1%），
`MANTOU_AFFECTION_POKE_FIND_CHAR_CHANCE` 是戳一戳路径的找字概率（默认 0.01），
`MANTOU_AFFECTION_FIND_CHAR_TIMEOUT` 是找字的作答时限（秒，默认 30）。找字只看自己的概率：好感度大于 0
的群友发言时先掷找字，没命中的那条消息才继续走 0.1% 小动作流程，所以两者互斥，不会在同一条消息上同时
出现。找字的两个概率可以用 `/馒头找字概率` 在运行时覆盖并持久化，和 `MANTOU_AFFECTION_AMBIENT_ENABLED`、
`MANTOU_AFFECTION_AMBIENT_PROBABILITY` 无关；把概率设成 0 就是关掉找字。

`MANTOU_AFFECTION_BET_CHANCE` 是「馒头博弈」的开局概率（默认 0.005，即 0.5%），
`MANTOU_AFFECTION_BET_WINDOW` 是博弈的作答窗口（秒，默认 60）。博弈不参与群消息与戳一戳的随机触发，只在
水群榜等插件调用 `maybe_start_bet` 时掷一次；SUPERUSER 可以用 `/馒头博弈概率` 在运行时覆盖概率并持久化。

`MANTOU_AFFECTION_AMBIENT_EVENT_RATIO` 是小动作命中后升级为随机事件的比例（默认 0.333，也就是约 1/3）。
`MANTOU_AFFECTION_EVENT_TIMEOUT` 是答题时限（秒，默认 20），`MANTOU_AFFECTION_EVENT_TIMEOUT_PENALTY`
是超时扣除的好感度点数（默认 5）。

`MANTOU_AFFECTION_POKE_EVENT_CHANCE` 是戳一戳戳出随机事件的概率（默认 0.01，即 1%）。戳出来的事件是
「扣分翻倍」版：普通题的负向选项与超时按 2 倍结算，闹别扭题按好感比例算出的扣分（普通错误 5%、陷阱与超时
10%，各有保底）也再翻一倍，正向奖励（+1 / +2 / +10）不变。

### 随机事件

小动作触发时有约 1/3 概率变成一次限时随机事件，馒头会在群里抛出一个三选一的场景，并 @触发它的群友：

```
@群友 ⚡ 触发随机事件！
馒头的小本子从桌上滑下去，页角折了一道印子，它蹲在地上看了很久。
1. 把本子捡起来递回去，说下次放稳一点
2. 先蹲下来把折角一页页抚平，再问它今天记了些什么
3. 说一本本子而已，回头给你买个更贵的
大家都可以回答，直接发送 1、2、3 即可（答题不用@）；
请在 20 秒内作答，被点名的群友超时未答好感度 -5！
```

**群里任何人都可以作答**（消息里也会写明），直接发 `1`、`2` 或 `3` 即可（不用 @机器人），每人只算第一次；
发别的内容不算作答，会一直等到窗口结束。同一个群同时只有一个活动——随机事件和「找字小游戏」共用这个席位，
上一次还没结束就不会再触发新的（这时新的小动作会回退成普通旁白）。

三个选项的顺序每次都会重新打乱，看起来都很合理，但只有最懂馒头心思的那个是最好的：最优 +2、普通 +1、
最差 -2，各自的加减记在**答题者自己**的好感度上。事件有两种来源，扣分不同：小动作升级来的按原值结算
（最差 -2、超时 -5），戳一戳戳出来的按**2 倍**结算（最差 -4、超时 -10），正向 +1 / +2 不受影响。窗口
结束时发一条合并消息，按答题先后每人一行；触发者必须作答，否则扣超时（点数可在配置里调整）：

```
@甲 馒头开心地收下了「先蹲下来把折角一页页抚平，再问它今天记了些什么」，好感度 +2，当前 12
@乙 馒头嫌弃地躲开了「说一本本子而已，回头给你买个更贵的」，好感度 -2，当前 3
@触发者 馒头等不到你的回答，失望地走开了，好感度 -5，当前 5
```

#### 闹别扭事件

好感度越高，这种题越容易冒出来：小动作升级成事件时，会按当前好感阶段抽一次题——`neutral`（Lv1-2）0%、
`warm`（Lv3）10%、`close`（Lv4）20%、`flirty`（Lv5）35%、`intimate`（Lv6-7）50% 概率换成闹别扭题库
（`resources/affection_events_upset.json`），其余情况仍是上面的普通题。**戳一戳戳出的事件同样按这个比例抽题**，
所以和馒头关系越好，戳出来的越可能是闹别扭题。

闹别扭题是**六个选项**（普通题仍是三个），消息里逐条列出来：

```
@群友 💢 馒头闹别扭了！
馒头把蒸笼盖扣上，自己缩在最里面，只露出一点点边。
1. 坐在旁边翻手机，等它自己出来
2. 隔着笼屉轻轻说一句：我等你出来，多久都等
3. 隔着笼屉喊：别闹了快出来，大家都在看着你
4. 隔着盖子问它：要不要先吃点东西再出来
5. 轻轻敲了敲盖子说：你不出来我可就走了啊
6. 算了，不理它，等它自己消气
大家都可以回答，直接发送 1、2、3、4、5、6 即可（答题不用@）；
请在 20 秒内作答，被点名的群友超时未答好感度按比例大扣！
```

六个选项里只有一个真正对路的哄法（奖励是普通题正解的两倍 **+10**，永远固定），三个是普通错误，
两个是"听着最像安慰其实踩雷"的陷阱——**扣分按答题者当前好感比例浮动，好感越高扣得越狠**，
好感低时保底就是原来的固定值（所以低好感阶段行为向后兼容）：

| 选项 / 来源 | 数量 | 小动作升级 | 戳一戳（再 ×2） |
|---|---|---|---|
| 正解（`+10`） | 1 个 | +10，固定 | +10，固定 |
| 普通错误 | 3 个 | `-max(5, 好感 × 5%)` | 再翻倍 |
| 倍减陷阱 | 2 个 | `-max(10, 好感 × 10%)` | 再翻倍 |
| 触发者超时（没答） | — | `-max(10, 好感 × 10%)` | 再翻倍 |

每个人按**自己的**当前好感结算（超时用触发者的好感），比例扣分向下取整：

```
30 好感：普通错误 -5、陷阱 -10、超时 -10（保底，与旧行为一致）
200 好感：普通错误 -10、陷阱 -20、超时 -20（戳一戳再翻倍 → 陷阱 -40）
999 好感：普通错误 -49、陷阱 -99、超时 -99
```

```
@甲 馒头一下子被哄好了，好感度 +10，当前 60！
@乙（200 好感）馒头听完更委屈了，好感度 -20，当前 180…
@丙（50 好感）馒头别过脸去，好感度 -5，当前 45
@触发者（没答）馒头等不到你的回答，心凉了半截，好感度 -10，当前 30
```

普通题完全不受影响（正解 +2 / 普通 +1 / 陷阱 -2，超时 -5，戳一戳路径再翻倍，事件消息里会写明"超时好感度
-5！"）；闹别扭题因为扣分是比例制、发消息时无法预知数值，提示写成了「被点名的群友超时未答好感度按比例大扣！」。

事件带来的好感变化走管理员调整同一条路径，不受每日获取总上限和联动冷却限制。另外，`/馒头互动` 现在也有
约 1/7 的概率惹馒头不高兴，好感度 -1（好感度为 0 时不会变成负数），这时回复会换成一套「小情绪」文案。

### 找字小游戏

好感度大于 0 的群友发言时，馒头会先按 `MANTOU_AFFECTION_FIND_CHAR_CHANCE`（默认 1%）掷一次找字小游戏；
戳一戳也有 `MANTOU_AFFECTION_POKE_FIND_CHAR_CHANCE`（默认 1%）的概率戳出一局。两种来源开局时都会 @触发者
（戳一戳路径和随机事件一样把事件消息交给调用方发送，不额外 @任何人），然后从题库抽一道题贴出方阵。

题目分两种题型，都从 `nonebot_plugin_mantou_affection/resources/findchar_puzzles.json` 里抽：

**二字型（pair）**：整块方阵铺满同一个字，只有一个位置藏着与它形近的另一个字，让群友找出来：

```
@群友 🔍 找字小游戏！
馒头把爪子按在方阵边上，一脸认真。
候 候 候 候 候 候 候 候 候
候 候 候 候 候 候 候 候 候
...（7 行 × 9 列，整块铺满同一个字，只有一个位置藏着形近的另一个字）
方阵里全是「候」，但还藏着 1 个和它长得很像的字；
找出它——直接发送那个字，或者发送它的位置（例如「3行5列」「第3行第5列」，行从上到下、列从左到右，都从 1 开始数）；
大家都可以回答（答题不用@），请在 30 秒内作答；
答对好感度 +2，答错好感度 -5，被点名的群友超时未答好感度 -2！
```

**三字型（trio）**：方阵里混着三个特别形近的字，两个干扰字有好多份、第三个字只藏了 1 个，消息里会点名
是哪两个干扰字，但**不会说答案长什么样**：

```
@群友 🔍 找字小游戏！
馒头说两个捣乱的字到处都是，别被带跑。
已 已 己 已 已 已 己 已
已 己 己 己 已 己 己 己
...（8 行 × 8 列，「己」「已」混排，只有一个位置是那个不一样的字）
方阵里混着大量的「己」和「已」，但还藏着另外一个和它们长得很像的字，全场只有 1 个；
找出它——直接发送那个字，或者发送它的位置（例如「3行5列」「第3行第5列」，行从上到下、列从左到右，都从 1 开始数）；
大家都可以回答（答题不用@），请在 30 秒内作答；
答对好感度 +2，答错好感度 -5，被点名的群友超时未答好感度 -2！
```

- **开局消息不剧透**：只点名干扰字（二字型点名铺满方阵的底字，三字型点名两个干扰字），答案字绝不会
  出现在消息里；开场引导语也会按当前答案过滤，连机器人名字撞上答案字时都会整句省掉。答案和它的位置在
  窗口结束的结算消息里才揭示（见下面的例子）。
- **群里任何人都可以作答**（消息里也会写明，不用 @），每人只算第一次有效作答：
  - 直接发送那个答案字（`strip` 后正好是一个汉字）就算一次作答，三字型里发成两个干扰字都算答错；
  - 发送位置也算，支持「3行5列」「第3行第5列」「5列3行」「第5列第3行」，有「行 / 列」关键字就按关键字
    定顺序，数字可以是阿拉伯数字或中文数字（一~二十），行列都从 1 开始数；
  - 其他内容（普通聊天、只写了「3行」这类缺一半的位置、非汉字的单个字符）不算作答，会被安静忽略。
- 答对 **+2**、答错 **-5**，各自记在答题者自己的好感度上；窗口结束时按作答先后每人一行。触发者必须作答，
  一次都没答过（答错也算答过）会扣 **-2**，提示会写明是超时未答：

```
@甲 馒头还没回过神，你已经找到了「巳」，好感度 +2，当前 14
@乙 猜错啦，那个字还躲在方阵里，好感度 -5，当前 5
@触发者 馒头把方阵收起来了也没等到你，好感度 -2，当前 18（超时未答）
答案是「巳」，在第 4 行第 6 列。
```

#### 题库文件

题库是 `nonebot_plugin_mantou_affection/resources/findchar_puzzles.json`，一个题目数组（也兼容带 `puzzles`
数组的对象），每题长这样：

```json
[
  {"mode": "pair", "decoys": ["己"], "target": "已", "rows": 8, "cols": 10, "row": 3, "col": 7},
  {"mode": "trio", "decoys": ["己", "已"], "target": "巳", "rows": 9, "cols": 12, "row": 5, "col": 2}
]
```

- `mode` 是 `pair` 或 `trio`；`decoys` 是干扰字（pair 1 个、trio 2 个）；`target` 是只出现 1 次的答案字；
  `rows` / `cols` 是方阵尺寸（6~10 行 × 8~14 列）；`row` / `col` 是答案位置（从 1 开始，行从上到下、
  列从左到右）。
- 加载时会逐题校验（题型、干扰字个数、是否都是单个汉字、答案字不与干扰字重复、行列范围、答案位置是否
  在方阵内），**坏题跳过并记一条 warning**，其余题目照常使用；题数不限，空题库或题库读取失败时找字小游戏
  自动退回普通小动作 / 普通戳一戳。
- 二字型的所有非答案格都用那一个干扰字；三字型的两个干扰字**按题号做确定性伪随机 50/50 混排**，同一道题
  每次渲染出来的方阵完全一样，答案字始终只出现 1 次。
- 题库随插件一起打包（`resources/findchar_puzzles.json`），正式题库共 **5000 题**（二字型 2500 + 三字型 2500），
  尺寸铺满 6~10 行 × 8~14 列、答案位置覆盖每一行每一列；往数组里追加题目不需要改代码，追加后重启机器人即可生效。

和随机事件一样，同一个群同时只有一个活动（小动作升级来的答题事件和找字小游戏共用这个席位），上一个
还没结束就不会再开新的，这时新的小动作会回退成普通旁白；窗口结束前的记录不会因为有人答对而提前结束，
30 秒（`MANTOU_AFFECTION_FIND_CHAR_TIMEOUT`）到时统一结算。好感变化同样走管理员调整那条路径，不受每日
获取总上限和联动冷却限制。开场那句引导语按题型各有一套（各 24 条）随机抽取。

### 馒头博弈

「馒头博弈」是一局数字顺序竞猜：馒头摆出 `1 2 3 4 5`，一分钟后把顺序打乱并公布，谁猜得最接近谁赢，
输家要把好感度转给赢家。它**不参与群消息和戳一戳的随机触发**，只由水群榜这类插件在合适的时候调用 API 开局。

```python
from nonebot_plugin_mantou_affection import maybe_start_bet

started = await maybe_start_bet(
    event.group_id,
    event.user_id,
    nickname=event.sender.card or event.sender.nickname,
    candidates=[(member.user_id, member.name) for member in ranking],
    send=matcher.send,
)
```

- `candidates` 是榜单成员 `[(user_id, name), ...]`，插件会排除触发者后随机抽两位当对手（不足两人直接返回
  `False`）；`send` 传调用方的 `matcher.send`，用来发开场消息与结算消息，不传直接返回 `False`。
- 开局前会先占同群席位（和小动作答题、找字小游戏共用，本群已有活动时返回 `False`），再按
  `MANTOU_AFFECTION_BET_CHANCE`（默认 0.5%）掷一次；命中才发消息并开窗口，成功返回 `True`。
- 窗口长度是 `MANTOU_AFFECTION_BET_WINDOW`（默认 60 秒），`/馒头博弈概率` 可以在运行时改概率。

开局消息 @ 触发者和两位对手，只说明规则、**不透露打乱后的顺序**：

```
@群友 @甲 @乙 🎲 馒头博弈！
馒头把五张写着数字的纸片摊开，说今天玩点刺激的。
馒头摆出了五个数字：1 2 3 4 5；
60 秒后馒头会把这五个数字的顺序打乱，并公布打乱后的顺序；
被点名的三人必须在 60 秒内作答：直接发送你猜的顺序（五个数字，例如 3 5 1 4 2）；
三个人的答案不能完全一样——如果三个人发的一模一样，三人各扣 5 好感度，游戏直接结束；
其它群友也可以回答（不用@），答案可以重复；
结算：被点名的人里最接近打乱后顺序的获胜，输的人扣自己好感度的 5% 转给赢家；其它群友如果比被点名的三个人都更接近则 +1，否则不加不减。
```

**作答**：消息去掉空白和 `、，,；;|/->→＞` 这类分隔符后，剩下必须正好是 `1 2 3 4 5` 的一个排列
（也接受中文数字「一二三四五」，全角数字同样可以）；每人只算第一次有效提交，其他内容安静忽略。

**结算**：

- 相似度 = 打乱后的顺序与你的答案在**对应位置**上相同的个数（0~5）。
- 被点名的人里相似度最高的获胜；**并列时所有达到最高分的人都是赢家**（没有唯一赢家）；没提交的被点名者算输家。
- 每个输家扣 `max(1, 好感度 × 5%)`（好感度为 0 时扣 0），这些点数汇成奖池**平分给赢家们**；除不尽的部分给
  最早提交的那位赢家，总额守恒。
- 其它群友只有在相似度**严格大于**被点名者最高分时才 +1，否则不加不减（会有一条提醒）。
- 三个被点名的人一个都没提交时不转让好感度，只发一条馒头失落的收尾。

如果三个被点名的人**都提交且答案完全一样**，游戏当场结束：三人各扣 5 好感度，不再等窗口、不结算，
其它人也拿不到奖励。窗口结束时的结算消息会先揭晓打乱后的顺序，再逐条列出每个人的结果：

```
馒头转过身去，纸片在爪子里响了一阵。
馒头打乱后的顺序：3 1 5 2 4
馒头把纸片翻回来，歪着头看大家的表情。
@甲 猜中 5 个位置，赢得 5 点好感度，当前 105
@触发者 没提交答案，被扣掉 5 点好感度，当前 95
@乙 猜中 0 个位置，被扣掉 5 点好感度，当前 95
@丙 猜中 3 个位置，没有超过被点名的人，本次不加不减
```

并列获胜时两位赢家会互相 @ 出来，奖池平分（除不尽的余数归最早提交的那位）：

```
@乙 猜中 5 个位置，与 @甲 并列获胜，分得 1 点好感度，当前 41
@甲 猜中 5 个位置，与 @乙 并列获胜，分得 1 点好感度，当前 41
@触发者 猜中 3 个位置，被扣掉 2 点好感度，当前 38
```

好感度的加减同样走管理员调整那条路径，不受每日获取总上限和联动冷却限制。

#### 博弈题库

题库是 `nonebot_plugin_mantou_affection/resources/bet_rounds.json`，一个题目数组（也兼容带 `rounds`
数组的对象），每题长这样：

```json
[
  {"target": [3, 1, 5, 2, 4], "opening": "开场氛围句", "reveal": "揭晓氛围句", "settle": "结算氛围句"}
]
```

- `target` 必须是 `1~5` 的一个排列；`opening` / `reveal` / `settle` 是三句氛围文案，分别用在开场消息、
  揭晓顺序那一条消息和结算消息里，要求非空、无换行、**不含阿拉伯数字**（避免提前剧透顺序），长度 2~100 字。
- 加载时逐题校验，**坏题跳过并记一条 warning**，其余照常使用；题数不限，空题库或读取失败时 `maybe_start_bet`
  直接返回 `False`（不会报错）。
- 题库随插件一起打包（`resources/bet_rounds.json`），正式题库共 **5000 题**：120 个排列各有约 40 题，三列氛围文案各 5000 句互不重复；往数组里追加题目不需要改代码，追加后重启机器人即可生效。

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
是同结构的被戳烦了的短情绪（8～40 字，不含占位符），保留给以后的负面场景使用，当前代码不再引用它。
这两个场景目前每个阶段先放 4 条占位文案，之后会继续扩充。

随机事件库位于 `nonebot_plugin_mantou_affection/resources/affection_events.json`（普通题，3 选项）与
`affection_events_upset.json`（闹别扭题，6 选项），结构都是“场景 + 选项列表”：

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

普通题必须正好 3 个选项、`delta` 为 `+2`、`+1`、`-2` 各一个；闹别扭题必须正好 6 个选项，`delta` 必须是
`+10` 一个（唯一正解）、`-5` 三个（普通错误）、`-10` 两个（倍减陷阱）。选项在发送前都会打乱顺序再编号，
答题时按编号对应的选项是否存在来判断（普通题发 4 会被忽略）；结构不合规的条目会被跳过并记录警告。
事件库为空时随机事件自动退回普通小动作旁白。

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
| `/馒头好感重置 确认` | SUPERUSER | 群聊/私聊 | 清空所有群所有群友的好感度（需二次确认） |
| `/馒头反应概率 5%` | SUPERUSER | 群聊/私聊 | 查看或调整小动作触发概率（持久保存） |
| `/馒头找字概率 5%` | SUPERUSER | 群聊/私聊 | 查看或调整找字小游戏概率；只写一个值会同时设置群消息和戳一戳，分开设置写成 `/馒头找字概率 群消息 5% 戳一戳 2%`（持久保存） |
| `/馒头博弈概率 0.5%` | SUPERUSER | 群聊/私聊 | 查看或调整馒头博弈的开局概率（默认 0.5%，`0` 即关闭；持久保存） |

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
    maybe_start_bet,
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

水群榜这类发完卡片想顺势开一局小游戏的插件，用 `maybe_start_bet`（见上面的「馒头博弈」章节）：传榜单成员
和 `matcher.send`，命中概率时它会自己发开场消息并在窗口结束后发结算消息，返回 `True`。

### 戳一戳 API

戳一戳的判定收在本插件里，调用方一行接入即可：

```python
from nonebot_plugin_mantou_affection import PokeResult, poke

result: PokeResult = await poke(
    group_id=event.group_id,
    user_id=event.user_id,
    nickname=event.sender.card or event.sender.nickname,
    send=matcher.send,  # 可选：戳出随机事件或找字小游戏时用来发送事件消息与结算
)
await matcher.finish(result.text)
```

`PokeResult` 有两个字段：`delta`（本次实际好感变化）、`text`（回复文案，`{bot}` 已替换）。

机制：

1. 每次戳稳定 **+1**，不会扣好感；这个 +1 受每日获取总上限约束，额度用完时 `delta=0`，文案会补一行
   「今天的好感已经拿满啦，明天再来吧。」
2. `text` 在 `delta != 0` 时末尾会追加一行「好感度 +1，当前 12」这样的提示；`delta=0` 时只有文案本身。
3. 有 `MANTOU_AFFECTION_POKE_EVENT_CHANCE`（默认 1%）的概率戳出一次随机事件：这时 `text` 就是事件消息
   本体（不带戳一戳文案，也不 @任何人），事件消息发到群里后任何人发 `1`/`2`/`3` 作答，窗口结束统一结算。
   抽题和普通小动作一样看触发者的好感阶段，所以亲密关系下也可能戳出**闹别扭题**（消息前缀是
   「💢 馒头闹别扭了！」）。结算的翻倍规则：普通题的负向选项 -4、超时 -10；闹别扭题的比例扣分算完后再翻倍
   （好感 200 时陷阱 -20 → -40、超时 -20 → -40），正向 +1 / +2 / +10 不变。
4. 本群已经有未结束的事件、事件库为空，或调用时没有传 `send`，都会退化成普通戳一戳文案（+1 照常入账）。
5. 随机事件没触发时，还会再按 `MANTOU_AFFECTION_POKE_FIND_CHAR_CHANCE`（默认 1%）掷一次找字小游戏：命中时
   `text` 是找字方阵本体（同样不 @任何人，结算走 `send`），方阵和作答规则见上面的「找字小游戏」。
6. 文案统一做 `{bot}` → 配置机器人名替换；文案库缺失或场景为空时回退为「馒头朝你笑了笑。」。

```
⚡ 触发随机事件！
馒头的小本子从桌上滑下去，页角折了一道印子，它蹲在地上看了很久。
1. 把本子捡起来递回去，说下次放稳一点
2. 先蹲下来把折角一页页抚平，再问它今天记了些什么
3. 说一本本子而已，回头给你买个更贵的
大家都可以回答，直接发送 1、2、3 即可（答题不用@）；
请在 20 秒内作答，被点名的群友超时未答好感度 -10！
```

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
