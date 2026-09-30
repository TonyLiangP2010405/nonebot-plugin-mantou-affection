from __future__ import annotations

import asyncio
import random
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from nonebot import logger
from nonebot.adapters.onebot.v11 import Message, MessageSegment

from .config import Config
from .service import AffectionService

if TYPE_CHECKING:
    from .ambient import EventCoordinator

FIND_CHAR_MIN_ROWS = 6
FIND_CHAR_MAX_ROWS = 9
FIND_CHAR_MIN_COLS = 8
FIND_CHAR_MAX_COLS = 12

FIND_CHAR_CORRECT_DELTA = 5
FIND_CHAR_WRONG_DELTA = -3
FIND_CHAR_TIMEOUT_DELTA = -2

FIND_CHAR_PREFIX = "🔍 找字小游戏！"
FIND_CHAR_MESSAGE = (
    "{prefix}\n"
    "{opening}\n"
    "{grid}\n"
    "方阵里有一个字和大家不一样，把它找出来；\n"
    "直接发送那个字，或者发送它的位置（例如「3行5列」「第3行第5列」，"
    "行从上到下、列从左到右，都从 1 开始数）；\n"
    "大家都可以回答（答题不用@），请在 {timeout} 秒内作答；\n"
    "答对好感度 {reward}，答错好感度 {penalty}，被点名的群友超时未答好感度 {timeout_penalty}！"
)
FIND_CHAR_CORRECT_REPLY = (
    "{bot}还没回过神，你已经找到了「{char}」，好感度 {delta:+d}，当前 {affection}"
)
FIND_CHAR_WRONG_REPLY = "猜错啦，那个字还躲在方阵里，好感度 {delta:+d}，当前 {affection}"
FIND_CHAR_TIMEOUT_REPLY = (
    "{bot}把方阵收起来了也没等到你，好感度 {delta:+d}，当前 {affection}（超时未答）"
)

CONFUSABLE_PAIRS: tuple[tuple[str, str], ...] = (
    ("己", "已"),
    ("人", "入"),
    ("未", "末"),
    ("土", "士"),
    ("日", "曰"),
    ("大", "太"),
    ("王", "玉"),
    ("刀", "刁"),
    ("候", "侯"),
    ("折", "拆"),
    ("拨", "拔"),
    ("兔", "免"),
    ("呜", "鸣"),
    ("治", "冶"),
    ("盲", "肓"),
    ("干", "千"),
    ("天", "夭"),
    ("乌", "鸟"),
    ("币", "巾"),
    ("手", "毛"),
    ("寸", "才"),
    ("囚", "因"),
    ("白", "自"),
    ("目", "且"),
    ("皿", "血"),
    ("问", "间"),
    ("比", "北"),
    ("戈", "弋"),
    ("戌", "戍"),
    ("戊", "戎"),
    ("石", "右"),
    ("田", "由"),
    ("甲", "申"),
    ("户", "尸"),
    ("毫", "亳"),
    ("亨", "享"),
    ("汩", "汨"),
    ("荼", "茶"),
    ("密", "蜜"),
    ("睛", "晴"),
    ("浆", "桨"),
    ("晌", "响"),
    ("蚂", "蚁"),
    ("抵", "低"),
    ("幻", "幼"),
    ("桥", "侨"),
    ("洒", "酒"),
    ("狼", "狠"),
    ("沐", "沫"),
    ("清", "请"),
    ("峰", "锋"),
    ("检", "捡"),
    ("徒", "徙"),
    ("恳", "垦"),
    ("拄", "柱"),
    ("休", "体"),
    ("宇", "字"),
    ("名", "各"),
)

OPENING_LINES: tuple[str, ...] = (
    "{bot}把小本子摊在蒸笼边上，用爪子画了一个方阵。",
    "{bot}说今天不答题，玩个找字的游戏。",
    "{bot}从笼屉里探出头，头顶还冒着热气。",
    "{bot}把攒下来的桃气捏成一个小方阵，说要考考大家。",
    "{bot}蹲在蒸笼边，认认真真地摆了一排字。",
    "{bot}把刚蒸好的热气吹散，露出下面的字。",
    "{bot}翻到小本子的新一页，笔尖顿了顿。",
    "{bot}说这题它自己看了三遍才找出来。",
    "{bot}把方阵举到大家面前，眼睛亮亮的。",
    "{bot}往旁边挪了挪，给方阵腾出地方。",
    "{bot}小声说：别急，看仔细一点。",
    "{bot}把尾巴收好，怕扫乱了方阵。",
    "{bot}说猜对了就分一颗桃子糖。",
    "{bot}把方阵铺在蒸笼盖上，像铺开一张小毯子。",
    "{bot}今天心情不错，主动掏出了新游戏。",
    "{bot}捧着方阵，等大家凑近一点。",
    "{bot}把爪子按在方阵边上，一脸认真。",
    "{bot}说这次藏得很用心，应该不好找。",
    "{bot}把字一个一个摆整齐，还退后看了看。",
    "{bot}从袖子里抖出一张写满字的纸。",
    "{bot}说找到那个不一样的，它就把蒸笼分你一半。",
    "{bot}把方阵举高了一点，好让后面的人也看见。",
    "{bot}打了个小小的哈欠，又坐直了等答案。",
    "{bot}说闲着也是闲着，来玩一局吧。",
)

CHINESE_NUMERALS: dict[str, int] = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
    "十一": 11,
    "十二": 12,
    "十三": 13,
    "十四": 14,
    "十五": 15,
    "十六": 16,
    "十七": 17,
    "十八": 18,
    "十九": 19,
    "二十": 20,
}

_NUMERAL_PATTERN = re.compile("|".join(sorted(CHINESE_NUMERALS, key=len, reverse=True)))
_FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")
_ROW_PATTERN = re.compile(r"(?:第)?(\d{1,3})\s*行")
_COL_PATTERN = re.compile(r"(?:第)?(\d{1,3})\s*列")


@dataclass(frozen=True)
class FindCharPuzzle:
    """一局找字方阵：行列数、填满的底字、藏起来的那个字和它的位置（都从 1 开始数）。"""

    rows: int
    cols: int
    base_char: str
    target_char: str
    target_row: int
    target_col: int


class PendingGuess:
    """一次作答：某人猜的字或位置是否命中。"""

    def __init__(self, user_id: str, nickname: str, correct: bool):
        self.user_id = user_id
        self.nickname = nickname
        self.correct = correct


class PendingFindChar:
    """一局已经发出、正在等待群里作答的找字小游戏。"""

    def __init__(
        self,
        puzzle: FindCharPuzzle,
        opening: str,
        trigger_id: str,
        trigger_name: str,
    ):
        self.puzzle = puzzle
        self.opening = opening
        self.trigger_id = trigger_id
        self.trigger_name = trigger_name
        self.answers: dict[str, PendingGuess] = {}
        self.task: asyncio.Task | None = None

    def record(self, user_id: str, nickname: str, correct: bool) -> bool:
        """记录作答，每人只算第一次。"""

        if user_id in self.answers:
            return False
        self.answers[user_id] = PendingGuess(user_id, nickname, correct)
        return True


def generate_puzzle(rng: random.Random) -> FindCharPuzzle:
    """随机行列数、随机一对形近字，随机决定谁做底字、谁藏在方阵里。"""

    rows = rng.randint(FIND_CHAR_MIN_ROWS, FIND_CHAR_MAX_ROWS)
    cols = rng.randint(FIND_CHAR_MIN_COLS, FIND_CHAR_MAX_COLS)
    first, second = rng.choice(CONFUSABLE_PAIRS)
    base_char, target_char = (first, second) if rng.random() < 0.5 else (second, first)
    return FindCharPuzzle(
        rows=rows,
        cols=cols,
        base_char=base_char,
        target_char=target_char,
        target_row=rng.randint(1, rows),
        target_col=rng.randint(1, cols),
    )


def grid_text(puzzle: FindCharPuzzle) -> str:
    """按行从上到下、列从左到右渲染方阵，只有目标位置换成藏起来的那个字。"""

    lines = []
    for row in range(1, puzzle.rows + 1):
        cells = [
            puzzle.target_char
            if (row, col) == (puzzle.target_row, puzzle.target_col)
            else puzzle.base_char
            for col in range(1, puzzle.cols + 1)
        ]
        lines.append(" ".join(cells))
    return "\n".join(lines)


def is_han(character: str) -> bool:
    """判断单个字符是不是汉字，用来把单独的汉字和普通聊天区分开。"""

    return "\u4e00" <= character <= "\u9fff" or "\u3400" <= character <= "\u4dbf"


def parse_position(text: str) -> tuple[int, int] | None:
    """解析「3行5列」这类位置，行号读「行」前的数字、列号读「列」前的数字。

    阿拉伯数字和中文数字（一~二十）都支持，两个关键字缺一个就当作没解析出位置。
    """

    normalized = text.translate(_FULLWIDTH_DIGITS)
    normalized = _NUMERAL_PATTERN.sub(
        lambda match: str(CHINESE_NUMERALS[match.group()]), normalized
    )
    row_match = _ROW_PATTERN.search(normalized)
    col_match = _COL_PATTERN.search(normalized)
    if row_match is None or col_match is None:
        return None
    return int(row_match.group(1)), int(col_match.group(1))


def judge(text: str, puzzle: FindCharPuzzle) -> bool | None:
    """判断一条消息是不是有效作答；返回 None 表示不是作答，安静忽略。"""

    raw = text.strip()
    if not raw:
        return None
    if len(raw) == 1:
        return raw == puzzle.target_char if is_han(raw) else None
    position = parse_position(raw)
    if position is None:
        return None
    return position == (puzzle.target_row, puzzle.target_col)


class FindCharCoordinator:
    """管理找字小游戏的触发、作答收集与结算，同一个群同时只有一个活动。"""

    def __init__(
        self,
        service: AffectionService,
        config: Config,
        *,
        events: EventCoordinator | None = None,
        rng: random.Random | None = None,
    ):
        self.service = service
        self.config = config
        self.events = events
        self.rng = rng or random.Random()
        self.pending: dict[str, PendingFindChar] = {}

    def busy(self, group_id: str) -> bool:
        """本群是否已经有待结算的活动：找字小游戏或随机事件。"""

        key = str(group_id)
        if key in self.pending:
            return True
        events = self.events
        return events is not None and key in events.pending

    def start(self, group_id: str, user_id: str, nickname: str) -> PendingFindChar | None:
        """开一局找字并登记待作答状态；本群已有未结束的活动时返回 None。"""

        key = str(group_id)
        if self.busy(key):
            return None
        opening = self.rng.choice(OPENING_LINES).replace(
            "{bot}", self.config.mantou_affection_bot_name
        )
        pending = PendingFindChar(
            generate_puzzle(self.rng), opening, str(user_id), nickname
        )
        self.pending[key] = pending
        return pending

    def discard(self, group_id: str) -> None:
        self.pending.pop(str(group_id), None)

    def answer(self, group_id: str, user_id: str, nickname: str, text: str) -> bool:
        """收到消息时调用；群里任何人只认第一次的有效作答，其他内容忽略。"""

        pending = self.pending.get(str(group_id))
        if pending is None:
            return False
        correct = judge(text, pending.puzzle)
        if correct is None:
            return False
        return pending.record(str(user_id), nickname, correct)

    def message(self, pending: PendingFindChar) -> str:
        return FIND_CHAR_MESSAGE.format(
            prefix=FIND_CHAR_PREFIX,
            opening=pending.opening,
            grid=grid_text(pending.puzzle),
            timeout=self.config.mantou_affection_find_char_timeout,
            reward=f"{FIND_CHAR_CORRECT_DELTA:+d}",
            penalty=f"{FIND_CHAR_WRONG_DELTA:+d}",
            timeout_penalty=f"{FIND_CHAR_TIMEOUT_DELTA:+d}",
        )

    async def settle(
        self,
        pending: PendingFindChar,
        *,
        group_id: str,
        timeout: float,
    ) -> Message:
        """等满作答窗口后统一结算，返回合并了好感变化的消息。"""

        try:
            await asyncio.sleep(timeout)
        finally:
            self.discard(group_id)

        segments: list[MessageSegment] = []

        def add_line(user_id: str, line: str) -> None:
            if segments:
                segments.append(MessageSegment.text("\n"))
            segments.append(MessageSegment.at(user_id))
            segments.append(MessageSegment.text(f" {line}"))

        for guess in pending.answers.values():
            delta = FIND_CHAR_CORRECT_DELTA if guess.correct else FIND_CHAR_WRONG_DELTA
            _, profile = await self.service.adjust(
                group_id, guess.user_id, guess.nickname, delta
            )
            template = FIND_CHAR_CORRECT_REPLY if guess.correct else FIND_CHAR_WRONG_REPLY
            add_line(
                guess.user_id,
                template.format(
                    bot=self.config.mantou_affection_bot_name,
                    char=pending.puzzle.target_char,
                    delta=delta,
                    affection=profile.affection,
                ),
            )

        if pending.trigger_id not in pending.answers:
            _, profile = await self.service.adjust(
                group_id,
                pending.trigger_id,
                pending.trigger_name,
                FIND_CHAR_TIMEOUT_DELTA,
            )
            add_line(
                pending.trigger_id,
                FIND_CHAR_TIMEOUT_REPLY.format(
                    bot=self.config.mantou_affection_bot_name,
                    delta=FIND_CHAR_TIMEOUT_DELTA,
                    affection=profile.affection,
                ),
            )

        return Message(segments)

    def schedule(
        self,
        pending: PendingFindChar,
        *,
        group_id: str,
        send: Callable[[Message | str], Awaitable[object]],
        timeout: float,
    ) -> asyncio.Task:
        """后台等满作答窗口并回复结果，异常只记日志。"""

        async def run() -> Message | None:
            try:
                message = await self.settle(pending, group_id=group_id, timeout=timeout)
            except Exception:
                logger.exception("[mantou-affection] 找字小游戏结算失败")
                return None
            try:
                await send(message)
            except Exception:
                logger.exception("[mantou-affection] 发送找字小游戏结果失败")
                return None
            return message

        pending.task = asyncio.create_task(run())
        return pending.task

    async def start_poke_find_char(
        self,
        *,
        group_id: str,
        user_id: str,
        nickname: str,
        send: Callable[[Message | str], Awaitable[object]] | None,
        chance: float,
    ) -> str | None:
        """戳一戳时按概率发起找字小游戏，成功返回事件消息文本；否则返回 None。

        和戳一戳随机事件保持同一套契约：事件消息由调用方发送（poke 返回的 text），
        send 只用于窗口结束后的结算消息，没人传 send 时退化成普通戳一戳。
        """

        if send is None or self.rng.random() >= chance:
            return None
        pending = self.start(str(group_id), str(user_id), nickname)
        if pending is None:
            return None
        message = self.message(pending)
        self.schedule(
            pending,
            group_id=str(group_id),
            send=send,
            timeout=self.config.mantou_affection_find_char_timeout,
        )
        return message
