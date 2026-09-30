from __future__ import annotations

import asyncio
import json
import random
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from nonebot import logger
from nonebot.adapters.onebot.v11 import Message, MessageSegment

from .config import Config
from .service import AffectionService

if TYPE_CHECKING:
    from .ambient import EventCoordinator

BUNDLED_PUZZLES_PATH = Path(__file__).parent / "resources" / "findchar_puzzles.json"

PAIR_MODE = "pair"
TRIO_MODE = "trio"

FIND_CHAR_MIN_ROWS = 6
FIND_CHAR_MAX_ROWS = 10
FIND_CHAR_MIN_COLS = 8
FIND_CHAR_MAX_COLS = 14

FIND_CHAR_CORRECT_DELTA = 5
FIND_CHAR_WRONG_DELTA = -3
FIND_CHAR_TIMEOUT_DELTA = -2

FIND_CHAR_PREFIX = "🔍 找字小游戏！"
_MESSAGE_TAIL = (
    "大家都可以回答（答题不用@），请在 {timeout} 秒内作答；\n"
    "答对好感度 {reward}，答错好感度 {penalty}，被点名的群友超时未答好感度 {timeout_penalty}！"
)
_MESSAGE_POSITION = (
    "直接发送那个字，或者发送它的位置（例如「3行5列」「第3行第5列」，"
    "行从上到下、列从左到右，都从 1 开始数）；\n"
)
FIND_CHAR_PAIR_MESSAGE = (
    "{prefix}\n"
    "{opening}\n"
    "{grid}\n"
    "方阵里有一个字和大家不一样，把它找出来；\n"
    f"{_MESSAGE_POSITION}"
    f"{_MESSAGE_TAIL}"
)
FIND_CHAR_TRIO_MESSAGE = (
    "{prefix}\n"
    "{opening}\n"
    "{grid}\n"
    "方阵里混着「{decoy_a}」「{decoy_b}」「{target}」三个形近字，"
    "「{decoy_a}」和「{decoy_b}」有很多个，「{target}」只藏了 1 个；\n"
    "找出「{target}」——"
    f"{_MESSAGE_POSITION}"
    f"{_MESSAGE_TAIL}"
)
FIND_CHAR_CORRECT_REPLY = (
    "{bot}还没回过神，你已经找到了「{char}」，好感度 {delta:+d}，当前 {affection}"
)
FIND_CHAR_WRONG_REPLY = "猜错啦，那个字还躲在方阵里，好感度 {delta:+d}，当前 {affection}"
FIND_CHAR_TIMEOUT_REPLY = (
    "{bot}把方阵收起来了也没等到你，好感度 {delta:+d}，当前 {affection}（超时未答）"
)

OPENING_LINES: dict[str, tuple[str, ...]] = {
    PAIR_MODE: (
        "{bot}把小本子摊在蒸笼边上，用爪子画了一个方阵。",
        "{bot}说今天不答题，玩个找字的游戏。",
        "{bot}从笼屉里探出头，头顶还冒着热气。",
        "{bot}把攒下来的桃气捏成一个小方阵，说要考考大家。",
        "{bot}蹲在蒸笼边，认认真真地摆了一排字。",
        "{bot}把刚蒸好的热气吹散，露出下面的字。",
        "{bot}翻到小本子的新一页，笔尖顿了顿。",
        "{bot}说这一页的字它自己看了三遍才找出不同。",
        "{bot}把方阵举到大家面前，眼睛亮亮的。",
        "{bot}往旁边挪了挪，给方阵腾出地方。",
        "{bot}小声说：别急，看仔细一点。",
        "{bot}把尾巴收好，怕扫乱了方阵。",
        "{bot}说猜对了就分一颗桃子糖。",
        "{bot}把方阵铺在蒸笼盖上，像铺开一张小毯子。",
        "{bot}今天心情不错，主动掏出了新游戏。",
        "{bot}捧着方阵，等大家凑近一点。",
        "{bot}把爪子按在方阵边上，一脸认真。",
        "{bot}说这次的字长得很像，应该不好找。",
        "{bot}把字一个一个摆整齐，还退后看了看。",
        "{bot}从袖子里抖出一张写满字的纸。",
        "{bot}说找到那个不一样的，它就把蒸笼分你一半。",
        "{bot}把方阵举高了一点，好让后面的人也看见。",
        "{bot}打了个小小的哈欠，又坐直了等答案。",
        "{bot}说这一题用的是它压箱底的字对。",
    ),
    TRIO_MODE: (
        "{bot}说这次不只一个字来捣乱，让大家看仔细。",
        "{bot}把三个长得很像的字摆在一起，眯着眼检查了一遍。",
        "{bot}捧出一张混着三种写法的方阵，还挺得意。",
        "{bot}说这题它自己都差点看花眼。",
        "{bot}把方阵转了一圈，确认没把唯一的那个字藏漏。",
        "{bot}说今天升级了难度，三个字长得像三胞胎。",
        "{bot}把爪子按在方阵中间，让大家慢点看。",
        "{bot}从笼屉边探出头，说这局要找的是独一份的那个。",
        "{bot}说两个捣乱的字到处都是，别被带跑。",
        "{bot}把方阵铺开，热气在字上打了一层薄雾。",
        "{bot}说这次藏得只有 1 个，找到就算你厉害。",
        "{bot}翻出小本子里最难的一页，摆给大家看。",
        "{bot}说它给三个字都排好了队，只有一队只有一个人。",
        "{bot}把三个形近字排成一排，又打乱了顺序。",
        "{bot}说别急着发，看清楚了再答。",
        "{bot}把方阵举高，好让后排的人也看得清。",
        "{bot}说这一局要靠眼力，不靠手速。",
        "{bot}往方阵旁边放了一颗桃子糖当彩头。",
        "{bot}说它数过三遍，确定独一份的那个只放了一次。",
        "{bot}把袖子挽起来，摆字摆得很认真。",
        "{bot}说这一页的字都长得太像，它也差点认错。",
        "{bot}把方阵摊在蒸笼盖上，自己蹲在旁边等答案。",
        "{bot}说找到独一份的那个，它就少闹一次别扭。",
        "{bot}打了个哈欠，又坐直了盯着方阵。",
    ),
}

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
    """一道找字题：题型、干扰字、答案字、方阵尺寸和答案位置（都从 1 开始数）。

    mode 取 pair 时 decoys 只有一个、整块方阵都用它铺满；取 trio 时 decoys 有两个、
    按题号 seed 做确定性伪随机混排。答案字整个方阵只出现 1 次。
    """

    mode: str
    decoys: tuple[str, ...]
    target: str
    rows: int
    cols: int
    row: int
    col: int
    seed: int = 0

    @property
    def trio(self) -> bool:
        return self.mode == TRIO_MODE


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


def grid_text(puzzle: FindCharPuzzle) -> str:
    """按行从上到下、列从左到右渲染方阵，答案格放答案字、其余放干扰字。

    pair 题的干扰字只有一个；trio 题的两个干扰字按题号（seed）做确定性伪随机
    50/50 混排，所以同一道题每次渲染出来的方阵完全一样。答案字只出现在答案格。
    """

    rng = random.Random(puzzle.seed)
    lines = []
    for row in range(1, puzzle.rows + 1):
        cells = []
        for col in range(1, puzzle.cols + 1):
            decoy = puzzle.decoys[0]
            if len(puzzle.decoys) > 1 and rng.random() < 0.5:
                decoy = puzzle.decoys[1]
            if (row, col) == (puzzle.row, puzzle.col):
                decoy = puzzle.target
            cells.append(decoy)
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
    """判断一条消息是不是有效作答；返回 None 表示不是作答，安静忽略。

    发汉字时只有正好等于答案字才算对，trio 题里发成两个干扰字都算答错。
    """

    raw = text.strip()
    if not raw:
        return None
    if len(raw) == 1:
        return raw == puzzle.target if is_han(raw) else None
    position = parse_position(raw)
    if position is None:
        return None
    return position == (puzzle.row, puzzle.col)


class FindCharLibrary:
    """加载找字题库，非法条目跳过并记录警告。"""

    def __init__(self, path: Path):
        self.path = path
        self._puzzles = self._load_file(path)

    @property
    def puzzles(self) -> tuple[FindCharPuzzle, ...]:
        return self._puzzles

    @staticmethod
    def _load_file(path: Path) -> tuple[FindCharPuzzle, ...]:
        try:
            with path.open("r", encoding="utf-8") as file:
                return FindCharLibrary._normalize(json.load(file))
        except (OSError, json.JSONDecodeError, ValueError) as error:
            logger.warning(f"[mantou-affection] 加载找字题库失败，暂时关闭找字小游戏: {error}")
            return ()

    @classmethod
    def _normalize(cls, data: Any) -> tuple[FindCharPuzzle, ...]:
        raw_puzzles = data.get("puzzles") if isinstance(data, dict) else data
        if not isinstance(raw_puzzles, list):
            raise ValueError("找字题库根节点必须是数组（或带 puzzles 数组的对象）")

        puzzles: list[FindCharPuzzle] = []
        for index, raw in enumerate(raw_puzzles):
            reason = cls._invalid_reason(raw)
            if reason is not None:
                logger.warning(f"[mantou-affection] 跳过第 {index + 1} 道找字题：{reason}")
                continue
            puzzles.append(
                FindCharPuzzle(
                    mode=str(raw["mode"]),
                    decoys=tuple(str(decoy) for decoy in raw["decoys"]),
                    target=str(raw["target"]),
                    rows=int(raw["rows"]),
                    cols=int(raw["cols"]),
                    row=int(raw["row"]),
                    col=int(raw["col"]),
                    seed=index,
                )
            )
        return tuple(puzzles)

    @staticmethod
    def _invalid_reason(raw: Any) -> str | None:
        """校验一道题的字段，合法时返回 None，否则返回跳过原因。"""

        if not isinstance(raw, dict):
            return "不是对象"
        mode = raw.get("mode")
        if mode not in (PAIR_MODE, TRIO_MODE):
            return f"mode 必须是 {PAIR_MODE} 或 {TRIO_MODE}"
        target = raw.get("target")
        if not _is_single_han(target):
            return "target 必须是单个汉字"
        decoys = raw.get("decoys")
        if not isinstance(decoys, list):
            return "decoys 必须是数组"
        expected = 1 if mode == PAIR_MODE else 2
        if len(decoys) != expected:
            return f"{mode} 题需要 {expected} 个干扰字"
        if not all(_is_single_han(decoy) for decoy in decoys):
            return "decoys 里必须都是单个汉字"
        if target in decoys or len(set(decoys)) != len(decoys):
            return "target 和干扰字之间不能重复"
        rows = raw.get("rows")
        cols = raw.get("cols")
        if not _is_int(rows) or not _is_int(cols):
            return "rows / cols 必须是整数"
        if not (
            FIND_CHAR_MIN_ROWS <= rows <= FIND_CHAR_MAX_ROWS
            and FIND_CHAR_MIN_COLS <= cols <= FIND_CHAR_MAX_COLS
        ):
            return (
                f"rows 必须在 {FIND_CHAR_MIN_ROWS}~{FIND_CHAR_MAX_ROWS}、"
                f"cols 必须在 {FIND_CHAR_MIN_COLS}~{FIND_CHAR_MAX_COLS}"
            )
        row = raw.get("row")
        col = raw.get("col")
        if not _is_int(row) or not _is_int(col):
            return "row / col 必须是整数"
        if not (1 <= row <= rows and 1 <= col <= cols):
            return "答案位置必须在方阵范围内"
        return None

    def pick(self) -> FindCharPuzzle | None:
        if not self._puzzles:
            return None
        return random.SystemRandom().choice(self._puzzles)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_single_han(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 1 and is_han(value)


class FindCharCoordinator:
    """管理找字小游戏的触发、作答收集与结算，同一个群同时只有一个活动。"""

    def __init__(
        self,
        service: AffectionService,
        config: Config,
        library: FindCharLibrary | None = None,
        *,
        events: EventCoordinator | None = None,
        rng: random.Random | None = None,
    ):
        self.service = service
        self.config = config
        self.library = library
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
        """从题库抽一道题开局并登记待作答状态。

        本群已有未结束的活动、没有传题库或题库为空时返回 None，调用方退回普通
        小动作或普通戳一戳文案。
        """

        key = str(group_id)
        if self.busy(key):
            return None
        puzzle = self.library.pick() if self.library is not None else None
        if puzzle is None:
            return None
        opening = self.rng.choice(OPENING_LINES[puzzle.mode]).replace(
            "{bot}", self.config.mantou_affection_bot_name
        )
        pending = PendingFindChar(puzzle, opening, str(user_id), nickname)
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
        puzzle = pending.puzzle
        template = FIND_CHAR_TRIO_MESSAGE if puzzle.trio else FIND_CHAR_PAIR_MESSAGE
        decoys = puzzle.decoys
        return template.format(
            prefix=FIND_CHAR_PREFIX,
            opening=pending.opening,
            grid=grid_text(puzzle),
            timeout=self.config.mantou_affection_find_char_timeout,
            reward=f"{FIND_CHAR_CORRECT_DELTA:+d}",
            penalty=f"{FIND_CHAR_WRONG_DELTA:+d}",
            timeout_penalty=f"{FIND_CHAR_TIMEOUT_DELTA:+d}",
            decoy_a=decoys[0],
            decoy_b=decoys[-1],
            target=puzzle.target,
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
                    char=pending.puzzle.target,
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
