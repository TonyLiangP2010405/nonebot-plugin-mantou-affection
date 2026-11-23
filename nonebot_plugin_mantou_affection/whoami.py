from __future__ import annotations

import asyncio
import json
import random
import re
import unicodedata
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from nonebot import logger
from nonebot.adapters.onebot.v11 import Message, MessageSegment

from .config import Config
from .logic import NEWBIE_PROTECTION_NOTE
from .service import AffectionService, normalize_nickname

if TYPE_CHECKING:
    from .ambient import EventCoordinator

BUNDLED_WHOAMI_PATH = Path(__file__).parent / "resources" / "whoami_puzzles.json"

WHOAMI_CHARACTER = "character"
WHOAMI_SCENE = "scene"
WHOAMI_CATEGORIES = (WHOAMI_CHARACTER, WHOAMI_SCENE)

# 越早的线索答对奖励越高: 第 1 条 +5、第 2 条 +4、第 3 条 +3。
WHOAMI_REWARDS: dict[int, int] = {1: 5, 2: 4, 3: 3}
WHOAMI_WRONG_DELTA = -2
WHOAMI_TIMEOUT_DELTA = -1
WHOAMI_MAX_GUESS_LENGTH = 32

WHOAMI_CHARACTER_PREFIX = "🕵️ 我是谁？"
WHOAMI_SCENE_PREFIX = "🗺️ 这是哪里？"
WHOAMI_START_REPLY = (
    "{prefix}\n"
    "{intro}\n"
    "线索 1/{total}：{hint}\n"
    "直接发{noun}作答（不用 @），每人只能猜一次，猜错 {wrong:+d}。\n"
    "第 1 条线索答对 {first:+d}，第 2 条 {second:+d}，第 3 条 {third:+d}；"
    "{timeout} 秒后揭晓答案。"
)
WHOAMI_HINT_REPLY = "{prefix}\n线索 {level}/{total}：{hint}\n（还剩 {remaining} 秒）"
WHOAMI_RESULT_REPLY = "{prefix}\n答案：{answer}（{game}）"
WHOAMI_CORRECT_REPLY = "在第 {level} 条线索时答对，好感度 {delta:+d}，当前 {affection}"
WHOAMI_WRONG_REPLY = "猜错了，好感度 {delta:+d}，当前 {affection}"
WHOAMI_TIMEOUT_REPLY = "整局没答，好感度 {delta:+d}，当前 {affection}"
WHOAMI_BUSY_REPLY = "群里还有一场活动没结束，等它结算完再来。"
WHOAMI_UNAVAILABLE_REPLY = "题库还没准备好，暂时玩不了。"
WHOAMI_LIMIT_REPLY = "这个时段的活动额度用完了，等额度恢复再来。"

CHARACTER_INTROS: tuple[str, ...] = (
    "{bot}把眼睛蒙上，清了清嗓子。",
    "{bot}披上一件看不见的斗篷，开始扮演一个人。",
    "{bot}从蒸笼里探出头，说要让你们猜一个人。",
    "{bot}翻了翻自己的小本子，挑中了一个名字。",
)
SCENE_INTROS: tuple[str, ...] = (
    "{bot}摊开一张地图，用爪子在某个地方按了一下。",
    "{bot}闭上眼睛，说它现在正站在一个地方。",
    "{bot}从抽屉里翻出一张风景照，只肯一点点给你们看。",
    "{bot}把地图铺在蒸笼边上，先盖住了一大半。",
)

_GUESS_PREFIXES = ("我猜是", "我猜", "应该是", "就是", "是")
_GUESS_SUFFIXES = ("吧", "吗", "呢", "啊", "呀", "哦", "噢", "啦")
_GUESS_NOISE = re.compile(
    r"[\s,，。.!！?？、:：;；'\"“”‘’《》〈〉()（）\[\]【】{}~～\-—_·・•「」]+"
)


@dataclass(frozen=True)
class WhoAmIEntry:
    """一条线索题：答案、出处游戏、类别和三段逐渐明确的提示。"""

    answer: str
    game: str
    category: str
    aliases: tuple[str, ...]
    hints: tuple[str, ...]

    @property
    def prefix(self) -> str:
        if self.category == WHOAMI_CHARACTER:
            return WHOAMI_CHARACTER_PREFIX
        return WHOAMI_SCENE_PREFIX

    @property
    def noun(self) -> str:
        return "名字" if self.category == WHOAMI_CHARACTER else "地名"


@dataclass
class WhoAmIGuess:
    """一次作答：某人第几条线索时猜了什么，猜对没有。"""

    user_id: str
    nickname: str
    correct: bool
    level: int


@dataclass
class PendingWhoAmI:
    """一局进行中的「我是谁」，所有人都可以猜，每人只算第一次。"""

    group_id: str
    entry: WhoAmIEntry
    trigger_id: str
    trigger_name: str
    level: int = 1
    accepting: bool = True
    answers: dict[str, WhoAmIGuess] = field(default_factory=dict)
    task: asyncio.Task | None = None

    def record(self, user_id: str, nickname: str, correct: bool, level: int) -> bool:
        """记录作答，每人只算第一次；已作答或窗口关闭时返回 False。"""

        if not self.accepting or user_id in self.answers:
            return False
        self.answers[user_id] = WhoAmIGuess(user_id, nickname, correct, level)
        return True


def normalize_guess(text: str) -> str:
    """把作答统一成全角转半角、去掉空白和标点的小写形式。"""

    return _GUESS_NOISE.sub("", unicodedata.normalize("NFKC", text).strip().lower())


def guess_candidates(text: str) -> tuple[str, ...]:
    """列出一次发言可能想表达的答案，兼容「我猜是胡桃吧」这类口语。"""

    base = normalize_guess(text)
    if not base or len(base) > WHOAMI_MAX_GUESS_LENGTH:
        return ()
    candidates: list[str] = [base]

    body = base
    for prefix in _GUESS_PREFIXES:
        if base.startswith(prefix) and len(base) > len(prefix):
            body = base[len(prefix) :]
            candidates.append(body)
            break

    for suffix in _GUESS_SUFFIXES:
        if body.endswith(suffix) and len(body) > len(suffix):
            candidates.append(body[: -len(suffix)])
            break
    for suffix in _GUESS_SUFFIXES:
        if base.endswith(suffix) and len(base) > len(suffix):
            candidates.append(base[: -len(suffix)])
            break

    unique: list[str] = []
    for candidate in candidates:
        if candidate and candidate not in unique:
            unique.append(candidate)
    return tuple(unique)


def _parse_entry(raw: Any) -> WhoAmIEntry | None:
    if not isinstance(raw, dict):
        return None
    answer = str(raw.get("answer") or "").strip()
    game = str(raw.get("game") or "").strip()
    category = str(raw.get("category") or "").strip()
    hints = raw.get("hints")
    aliases = raw.get("aliases") or []
    if not answer or not game or category not in WHOAMI_CATEGORIES:
        logger.warning(f"[mantou-affection] 我是谁题目缺少答案/出处/类别，已跳过：{raw!r}")
        return None
    if not isinstance(hints, list) or len(hints) < 3:
        logger.warning(f"[mantou-affection] 我是谁题目线索不足 3 条，已跳过：{answer}")
        return None
    cleaned_hints = tuple(str(hint).strip() for hint in hints[:3])
    if any(not hint or "\n" in hint for hint in cleaned_hints):
        logger.warning(f"[mantou-affection] 我是谁题目线索为空或含换行，已跳过：{answer}")
        return None
    if not isinstance(aliases, list):
        aliases = []
    cleaned_aliases: list[str] = []
    for alias in aliases:
        text = str(alias).strip()
        if text and text != answer and text not in cleaned_aliases:
            cleaned_aliases.append(text)
    return WhoAmIEntry(
        answer=answer,
        game=game,
        category=category,
        aliases=tuple(cleaned_aliases),
        hints=cleaned_hints,
    )


class WhoAmILibrary:
    """加载并校验「我是谁」题库，同时提供作答用的别名索引。"""

    def __init__(self, path: Path | None = None):
        self.path = path
        self.entries: tuple[WhoAmIEntry, ...] = ()
        self._index: dict[str, WhoAmIEntry] = {}
        if path is not None:
            self._load(path)

    def __len__(self) -> int:
        return len(self.entries)

    def _load(self, path: Path) -> None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            logger.warning(f"[mantou-affection] 我是谁题库不存在：{path}")
            return
        except (OSError, json.JSONDecodeError):
            logger.exception(f"[mantou-affection] 读取我是谁题库失败：{path}")
            return

        raw_entries = payload.get("entries") if isinstance(payload, dict) else payload
        if not isinstance(raw_entries, list):
            logger.warning(f"[mantou-affection] 我是谁题库格式不对：{path}")
            return

        entries: list[WhoAmIEntry] = []
        index: dict[str, WhoAmIEntry] = {}
        seen_answers: set[str] = set()
        for raw in raw_entries:
            entry = _parse_entry(raw)
            if entry is None:
                continue
            key = normalize_guess(entry.answer)
            if key in seen_answers:
                logger.warning(f"[mantou-affection] 我是谁题目答案重复，已跳过：{entry.answer}")
                continue
            seen_answers.add(key)
            entries.append(entry)
            for alias in (entry.answer, *entry.aliases):
                alias_key = normalize_guess(alias)
                if not alias_key:
                    continue
                owner = index.get(alias_key)
                if owner is not None and owner.answer != entry.answer:
                    logger.warning(
                        f"[mantou-affection] 我是谁别名「{alias}」重复，"
                        f"保留给「{owner.answer}」，忽略「{entry.answer}」"
                    )
                    continue
                index[alias_key] = entry

        self.entries = tuple(entries)
        self._index = index

    def pick(self, rng: random.Random) -> WhoAmIEntry | None:
        """随机抽一道题，允许重复抽到同一题。"""

        if not self.entries:
            return None
        return rng.choice(self.entries)

    def resolve(self, text: str) -> WhoAmIEntry | None:
        """把一句发言解析成题库里的某个答案；认不出来返回 None。"""

        if not self._index:
            return None
        for candidate in guess_candidates(text):
            entry = self._index.get(candidate)
            if entry is not None:
                return entry
        return None


class WhoAmICoordinator:
    """管理「我是谁」的开局、逐条线索、作答与结算，同群同时只有一个活动。"""

    def __init__(
        self,
        service: AffectionService,
        config: Config,
        library: WhoAmILibrary | None = None,
        *,
        events: EventCoordinator | None = None,
        rng: random.Random | None = None,
    ):
        self.service = service
        self.config = config
        self.library = library
        self.events = events
        self.rng = rng or random.Random()
        self.pending: dict[str, PendingWhoAmI] = {}
        self._start_lock = asyncio.Lock()
        self.timeout = float(getattr(config, "mantou_affection_whoami_timeout", 75))

    @property
    def total_entries(self) -> int:
        return len(self.library) if self.library is not None else 0

    def busy(self, group_id: str) -> bool:
        """本群是否已经有待结算的活动。"""

        key = str(group_id)
        if key in self.pending:
            return True
        events = self.events
        return events is not None and events.busy(key)

    def discard(self, group_id: str, pending: PendingWhoAmI | None = None) -> None:
        key = str(group_id)
        if pending is None or self.pending.get(key) is pending:
            self.pending.pop(key, None)

    async def start(
        self,
        *,
        group_id: str,
        user_id: str,
        nickname: str,
        send: Callable[[Message | str], Awaitable[object]],
    ) -> bool:
        """开一局「我是谁」；成功发出第一条线索返回 True，群忙或题库为空返回 False。"""

        group = str(group_id)
        user = str(user_id)
        name = normalize_nickname(nickname, user)
        async with self._start_lock:
            if self.busy(group):
                return False
            entry = self.library.pick(self.rng) if self.library is not None else None
            if entry is None:
                return False
            pending = PendingWhoAmI(group, entry, user, name)
            self.pending[group] = pending

        try:
            await send(
                MessageSegment.at(user)
                + MessageSegment.text(" " + self.message(pending))
            )
        except Exception:
            self.discard(group, pending)
            raise
        self.schedule(pending, send=send)
        return True

    def message(self, pending: PendingWhoAmI) -> str:
        """开局消息：只放第一条线索，后两条留给定时揭晓。"""

        entry = pending.entry
        intros = CHARACTER_INTROS if entry.category == WHOAMI_CHARACTER else SCENE_INTROS
        return WHOAMI_START_REPLY.format(
            prefix=entry.prefix,
            intro=self.rng.choice(intros).format(
                bot=self.config.mantou_affection_bot_name
            ),
            hint=entry.hints[0],
            total=len(entry.hints),
            noun=entry.noun,
            wrong=WHOAMI_WRONG_DELTA,
            first=WHOAMI_REWARDS[1],
            second=WHOAMI_REWARDS[2],
            third=WHOAMI_REWARDS[3],
            timeout=int(self.timeout),
        )

    def hint_message(self, pending: PendingWhoAmI, remaining: int) -> str:
        """追加线索时发出的消息，不点名、不打断作答。"""

        entry = pending.entry
        return WHOAMI_HINT_REPLY.format(
            prefix=entry.prefix,
            level=pending.level,
            total=len(entry.hints),
            hint=entry.hints[pending.level - 1],
            remaining=max(0, remaining),
        )

    def schedule(
        self,
        pending: PendingWhoAmI,
        *,
        send: Callable[[Message | str], Awaitable[object]],
    ) -> asyncio.Task:
        """后台逐条放出线索，等满窗口后统一结算；异常只记日志。"""

        async def run() -> None:
            try:
                await self._reveal_and_settle(pending, send=send)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("[mantou-affection] 我是谁流程失败")
            finally:
                self.discard(pending.group_id, pending)

        pending.task = asyncio.create_task(run())
        return pending.task

    async def _reveal_and_settle(
        self,
        pending: PendingWhoAmI,
        *,
        send: Callable[[Message | str], Awaitable[object]],
    ) -> None:
        total = len(pending.entry.hints)
        interval = max(0.01, self.timeout / total)
        elapsed = interval
        for level in range(2, total + 1):
            await asyncio.sleep(interval)
            if not pending.accepting:
                return
            pending.level = level
            remaining = int(max(0.0, self.timeout - elapsed))
            try:
                await send(self.hint_message(pending, remaining))
            except Exception:
                logger.exception("[mantou-affection] 发送我是谁线索失败")
            elapsed += interval
        await asyncio.sleep(max(0.0, self.timeout - elapsed))
        if not pending.accepting:
            return
        pending.accepting = False
        await self.settle(pending, send=send)

    async def settle(
        self,
        pending: PendingWhoAmI,
        *,
        send: Callable[[Message | str], Awaitable[object]] | None = None,
    ) -> Message:
        """公布答案并逐人结算，返回合并了好感变化的消息。"""

        pending.accepting = False
        entry = pending.entry
        segments: list[MessageSegment] = [
            MessageSegment.text(
                WHOAMI_RESULT_REPLY.format(
                    prefix=entry.prefix, answer=entry.answer, game=entry.game
                )
            )
        ]

        for guess in pending.answers.values():
            delta = (
                WHOAMI_REWARDS.get(guess.level, WHOAMI_REWARDS[3])
                if guess.correct
                else WHOAMI_WRONG_DELTA
            )
            applied, profile, protected = await self.service.adjust(
                pending.group_id, guess.user_id, guess.nickname, delta
            )
            if applied < 0 and send is not None:
                await self.service.send_gift(
                    send, pending.group_id, guess.user_id, guess.nickname
                )
            template = WHOAMI_CORRECT_REPLY if guess.correct else WHOAMI_WRONG_REPLY
            self._add_line(
                segments,
                guess.user_id,
                template.format(
                    level=guess.level, delta=applied, affection=profile.affection
                )
                + (NEWBIE_PROTECTION_NOTE if protected else ""),
            )

        if pending.trigger_id not in pending.answers:
            applied, profile, protected = await self.service.adjust(
                pending.group_id,
                pending.trigger_id,
                pending.trigger_name,
                WHOAMI_TIMEOUT_DELTA,
            )
            if applied < 0 and send is not None:
                await self.service.send_gift(
                    send, pending.group_id, pending.trigger_id, pending.trigger_name
                )
            self._add_line(
                segments,
                pending.trigger_id,
                WHOAMI_TIMEOUT_REPLY.format(
                    delta=applied, affection=profile.affection
                )
                + (NEWBIE_PROTECTION_NOTE if protected else ""),
            )

        message = Message(segments)
        if send is not None:
            await send(message)
        return message

    @staticmethod
    def _add_line(segments: list[MessageSegment], user_id: str, line: str) -> None:
        segments.append(MessageSegment.text("\n"))
        segments.append(MessageSegment.at(user_id))
        segments.append(MessageSegment.text(f" {line}"))

    def answer(self, group_id: str, user_id: str, nickname: str, text: str) -> bool:
        """收到群消息时调用：只认题库里出现过的答案，闲聊不会被当成猜错。"""

        pending = self.pending.get(str(group_id))
        if pending is None or not pending.accepting:
            return False
        if self.library is None:
            return False
        entry = self.library.resolve(text)
        if entry is None:
            return False
        correct = entry.answer == pending.entry.answer
        return pending.record(
            str(user_id),
            normalize_nickname(nickname, str(user_id)),
            correct,
            pending.level,
        )
