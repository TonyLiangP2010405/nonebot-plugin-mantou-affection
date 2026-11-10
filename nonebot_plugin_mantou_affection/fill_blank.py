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
from .logic import NEWBIE_PROTECTION_NOTE
from .service import AffectionService, normalize_nickname

if TYPE_CHECKING:
    from .ambient import EventCoordinator

BUNDLED_FILL_BLANK_PATH = (
    Path(__file__).parent / "resources" / "fill_blank_questions.json"
)

FILL_BLANK_RIGHT_REWARD = 5
FILL_BLANK_WRONG_PENALTY = -5
FILL_BLANK_CHAR_REWARD = 1
FILL_BLANK_MAX_PARTIAL_REWARD = 4
FILL_BLANK_TIMEOUT_PENALTY = -2

FILL_BLANK_HARD_RIGHT_REWARD = 8
FILL_BLANK_HARD_WRONG_PENALTY = -8
FILL_BLANK_HARD_TIMEOUT_PENALTY = -10
FILL_BLANK_HARD_MIN_LENGTH = 6
FILL_BLANK_HARD_MAX_LENGTH = 8

FILL_BLANK_ANSWER_SLACK = 2
FILL_BLANK_PLACEHOLDER = "____"

FILL_BLANK_PREFIX = "🧩 补句问答！"
FILL_BLANK_HARD_PREFIX = "🧩 补句问答·高难度！"
FILL_BLANK_QUESTION_REPLY = (
    "{prefix}\n"
    "补全下面这句话：\n"
    "{sentence}\n"
    "拼音首字母：{initials}（{length} 个字）\n"
    "大家都可以回答，直接发送答案即可（答题不用@），请在 {timeout} 秒内作答。\n"
    "{rules}"
)
FILL_BLANK_RULES = (
    "答对 +5；答错 -5；答对其中 1 个字 +1；被 @ 的触发者整局没答 -2。"
)
FILL_BLANK_HARD_RULES = (
    "高难度：答对 +8；答错 -8；被 @ 的触发者整局没答 -10（部分答对不加分）。"
)
FILL_BLANK_RESULT_REPLY = "{prefix}\n答案：{answer}（{source}）"
FILL_BLANK_EXACT_REPLY = "全对，好感度 {delta:+d}，当前 {affection}"
FILL_BLANK_PARTIAL_REPLY = "答对 {matched} 个字，好感度 {delta:+d}，当前 {affection}"
FILL_BLANK_HARD_PARTIAL_REPLY = (
    "答对 {matched} 个字但不加分，好感度 {delta:+d}，当前 {affection}"
)
FILL_BLANK_WRONG_REPLY = "没答对，好感度 {delta:+d}，当前 {affection}"
FILL_BLANK_TIMEOUT_REPLY = "整局未答，好感度 {delta:+d}，当前 {affection}"

_STRIP_PUNCTUATION = re.compile(
    r"[\s\u3000，。！？、；：,.!?;:\"'“”‘’（）()《》〈〉【】「」『』\[\]{}<>…—－_~～]+"
)
_ANSWER_PREFIX = re.compile(
    r"^(?:答案是|答案|答|我猜是|我猜|我觉得是|我觉得|应该是|就是)[:：\s]*"
)
_COMMAND_PREFIXES = ("/", "／")


@dataclass(frozen=True)
class FillBlankQuestion:
    """一道补句题：带横线的句子、拼音首字母和答案。"""

    question_id: str
    sentence: str
    initials: str
    answer: str
    source: str = "口头禅"
    hard: bool = False


class FillBlankLibrary:
    """加载补句题库，非法条目跳过并记录警告。"""

    def __init__(self, path: Path):
        self.path = path
        self._questions = self._load_file(path)

    @property
    def questions(self) -> tuple[FillBlankQuestion, ...]:
        return self._questions

    @property
    def normal_questions(self) -> tuple[FillBlankQuestion, ...]:
        return tuple(question for question in self._questions if not question.hard)

    @property
    def hard_questions(self) -> tuple[FillBlankQuestion, ...]:
        return tuple(question for question in self._questions if question.hard)

    @staticmethod
    def _load_file(path: Path) -> tuple[FillBlankQuestion, ...]:
        try:
            with path.open("r", encoding="utf-8") as file:
                return FillBlankLibrary._normalize(json.load(file))
        except (OSError, json.JSONDecodeError, ValueError) as error:
            logger.warning(
                f"[mantou-affection] 加载补句题库失败，暂时关闭补句问答: {error}"
            )
            return ()

    @staticmethod
    def _normalize_item(raw: Any) -> FillBlankQuestion | None:
        if not isinstance(raw, dict):
            return None
        question_id = str(raw.get("id", "")).strip()
        sentence = str(raw.get("sentence", "")).strip()
        initials = str(raw.get("initials", "")).strip().lower()
        answer = str(raw.get("answer", "")).strip()
        source = str(raw.get("source", "")).strip() or "口头禅"
        difficulty = str(raw.get("difficulty", "normal")).strip().lower()
        if difficulty not in {"normal", "hard"}:
            return None
        hard = difficulty == "hard"
        if not question_id or not sentence or not initials or not answer:
            return None
        if sentence.count(FILL_BLANK_PLACEHOLDER) != 1:
            return None
        if not initials.isascii() or not initials.isalpha():
            return None
        if len(initials) != len(answer):
            return None
        if hard and not FILL_BLANK_HARD_MIN_LENGTH <= len(answer) <= FILL_BLANK_HARD_MAX_LENGTH:
            return None
        return FillBlankQuestion(
            question_id, sentence, initials, answer, source, hard
        )

    @classmethod
    def _normalize(cls, data: Any) -> tuple[FillBlankQuestion, ...]:
        raw_questions = data.get("questions") if isinstance(data, dict) else None
        if not isinstance(raw_questions, list):
            raise ValueError("题库根节点必须是含 questions 数组的 JSON 对象")

        questions: list[FillBlankQuestion] = []
        seen_ids: set[str] = set()
        for index, raw in enumerate(raw_questions):
            question = cls._normalize_item(raw)
            if question is None:
                logger.warning(
                    f"[mantou-affection] 跳过第 {index + 1} 条补句题：格式不合法"
                )
                continue
            if question.question_id in seen_ids:
                logger.warning(
                    f"[mantou-affection] 跳过第 {index + 1} 条补句题："
                    f"id {question.question_id} 重复"
                )
                continue
            seen_ids.add(question.question_id)
            questions.append(question)
        return tuple(questions)


def normalize_answer(text: str) -> str:
    """去掉空白、标点和常见的「答：」前缀，只留下真正的答案文本。"""

    value = _STRIP_PUNCTUATION.sub("", str(text))
    value = _ANSWER_PREFIX.sub("", value)
    return value.strip()


def matched_characters(guess: str, answer: str) -> int:
    """最长公共子序列长度：答对几个字，顺序对得上就算。"""

    if not guess or not answer:
        return 0
    previous = [0] * (len(answer) + 1)
    for char in guess:
        current = [0]
        for index, target in enumerate(answer, start=1):
            if char == target:
                current.append(previous[index - 1] + 1)
            else:
                current.append(max(previous[index], current[-1]))
        previous = current
    return previous[-1]


def score_answer(
    guess: str, answer: str, *, hard: bool = False
) -> tuple[int, int]:
    """返回 (好感度变化, 答对字数)。

    普通题全对 +5、全错 -5；部分答对按最长公共子序列每字 +1，最多 +4。
    高难度题全对 +8；只要不是完全答对就 -8，部分答对不加分。
    """

    if guess == answer:
        reward = (
            FILL_BLANK_HARD_RIGHT_REWARD if hard else FILL_BLANK_RIGHT_REWARD
        )
        return reward, len(answer)
    matched = matched_characters(guess, answer)
    if hard:
        return FILL_BLANK_HARD_WRONG_PENALTY, matched
    if matched > 0:
        reward = min(matched * FILL_BLANK_CHAR_REWARD, FILL_BLANK_MAX_PARTIAL_REWARD)
        return reward, matched
    return FILL_BLANK_WRONG_PENALTY, 0


def looks_like_answer(text: str, answer_length: int) -> bool:
    """粗筛答题窗口里的消息，避免把命令、链接和太长的闲聊当成答案。"""

    raw = str(text).strip()
    if not raw or raw.startswith(_COMMAND_PREFIXES):
        return False
    if "http://" in raw.lower() or "https://" in raw.lower():
        return False
    guess = normalize_answer(raw)
    if not guess or guess.isdigit():
        return False
    if len(guess) > answer_length + FILL_BLANK_ANSWER_SLACK:
        return False
    return any("\u4e00" <= char <= "\u9fff" for char in guess)


class FillBlankAnswer:
    """一次作答：某人填的答案。"""

    def __init__(self, user_id: str, nickname: str, guess: str):
        self.user_id = user_id
        self.nickname = nickname
        self.guess = guess


class PendingFillBlank:
    """一道进行中的补句问答，所有人都可以回答，被 @ 的触发者不答要扣分。"""

    def __init__(
        self,
        group_id: str,
        target: tuple[str, str],
        question: FillBlankQuestion,
        *,
        hard: bool = False,
    ):
        self.group_id = group_id
        self.target = target
        self.question = question
        self.hard = hard
        self.answers: dict[str, FillBlankAnswer] = {}
        self.accepting = True
        self.task: asyncio.Task | None = None

    def record(self, user_id: str, nickname: str, guess: str) -> bool:
        """记录作答，每人只算第一次。"""

        if not self.accepting or user_id in self.answers:
            return False
        self.answers[user_id] = FillBlankAnswer(user_id, nickname, guess)
        return True


class FillBlankCoordinator:
    """管理补句问答的出题、作答与结算，同一个群同时只有一个活动。"""

    def __init__(
        self,
        service: AffectionService,
        config: Config,
        library: FillBlankLibrary | None = None,
        *,
        events: EventCoordinator | None = None,
        rng: random.Random | None = None,
    ):
        self.service = service
        self.config = config
        self.library = library
        self.events = events
        self.rng = rng or random.Random()
        self.pending: dict[str, PendingFillBlank] = {}
        self._start_lock = asyncio.Lock()
        self.question_timeout = float(config.mantou_affection_fill_blank_timeout)

    @property
    def total_questions(self) -> int:
        return len(self.library.questions) if self.library is not None else 0

    def busy(self, group_id: str) -> bool:
        """本群是否已经有待结算的活动。"""

        key = str(group_id)
        if key in self.pending:
            return True
        events = self.events
        return events is not None and events.busy(key)

    def discard(self, group_id: str, pending: PendingFillBlank | None = None) -> None:
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
        """给当前发言者出一道题；成功发出题目返回 True，群忙或题库为空返回 False。"""

        group = str(group_id)
        user = str(user_id)
        name = normalize_nickname(nickname, user)
        async with self._start_lock:
            if self.busy(group):
                return False
            profile = await self.service.profile(group, user)
            hard = (
                profile.affection
                > self.config.mantou_affection_fill_blank_hard_threshold
            )
            question = self._draw_question(hard=hard)
            if question is None and hard:
                question = self._draw_question(hard=False)
                hard = False
            if question is None:
                return False
            pending = PendingFillBlank(
                group, (user, name), question, hard=hard
            )
            self.pending[group] = pending

        try:
            await send(
                MessageSegment.at(user)
                + MessageSegment.text(" " + self.question_message(pending))
            )
        except Exception:
            self.discard(group, pending)
            raise
        self.schedule(pending, send=send)
        return True

    def question_message(self, pending: PendingFillBlank) -> str:
        question = pending.question
        return FILL_BLANK_QUESTION_REPLY.format(
            prefix=(
                FILL_BLANK_HARD_PREFIX if pending.hard else FILL_BLANK_PREFIX
            ),
            sentence=question.sentence,
            initials=question.initials,
            length=len(question.answer),
            timeout=int(self.question_timeout),
            rules=(
                FILL_BLANK_HARD_RULES if pending.hard else FILL_BLANK_RULES
            ),
        )

    def _draw_question(self, *, hard: bool = False) -> FillBlankQuestion | None:
        """按难度随机抽一道题，允许重复抽到同一题。"""

        if self.library is None:
            return None
        questions = (
            self.library.hard_questions if hard else self.library.normal_questions
        )
        if not questions:
            return None
        return self.rng.choice(questions)

    def schedule(
        self,
        pending: PendingFillBlank,
        *,
        send: Callable[[Message | str], Awaitable[object]],
    ) -> asyncio.Task:
        """等待作答窗口结束并统一结算，异常只记日志。"""

        async def run() -> None:
            try:
                await asyncio.sleep(self.question_timeout)
                pending.accepting = False
                await self._settle(pending, send=send)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("[mantou-affection] 补句问答流程失败")
            finally:
                self.discard(pending.group_id, pending)

        pending.task = asyncio.create_task(run())
        return pending.task

    async def _settle(
        self,
        pending: PendingFillBlank,
        *,
        send: Callable[[Message | str], Awaitable[object]],
    ) -> None:
        question = pending.question
        segments = [
            MessageSegment.text(
                FILL_BLANK_RESULT_REPLY.format(
                    prefix=(
                        FILL_BLANK_HARD_PREFIX
                        if pending.hard
                        else FILL_BLANK_PREFIX
                    ),
                    answer=question.answer,
                    source=question.source,
                )
            )
        ]
        target_id, target_name = pending.target
        for answer in pending.answers.values():
            delta, matched = score_answer(
                answer.guess, question.answer, hard=pending.hard
            )
            applied, profile, protected = await self.service.adjust(
                pending.group_id, answer.user_id, answer.nickname, delta
            )
            if applied < 0:
                await self.service.send_gift(
                    send, pending.group_id, answer.user_id, answer.nickname
                )
            if answer.guess == question.answer:
                result = FILL_BLANK_EXACT_REPLY.format(
                    delta=applied, affection=profile.affection
                )
            elif matched > 0:
                result = (
                    FILL_BLANK_HARD_PARTIAL_REPLY
                    if pending.hard
                    else FILL_BLANK_PARTIAL_REPLY
                ).format(
                    matched=matched, delta=applied, affection=profile.affection
                )
            else:
                result = FILL_BLANK_WRONG_REPLY.format(
                    delta=applied, affection=profile.affection
                )
            self._add_line(
                segments,
                answer.user_id,
                f"猜「{answer.guess}」，{result}"
                + (NEWBIE_PROTECTION_NOTE if protected else ""),
            )

        if target_id not in pending.answers:
            timeout_penalty = (
                FILL_BLANK_HARD_TIMEOUT_PENALTY
                if pending.hard
                else FILL_BLANK_TIMEOUT_PENALTY
            )
            applied, profile, protected = await self.service.adjust(
                pending.group_id, target_id, target_name, timeout_penalty
            )
            if applied < 0:
                await self.service.send_gift(
                    send, pending.group_id, target_id, target_name
                )
            self._add_line(
                segments,
                target_id,
                FILL_BLANK_TIMEOUT_REPLY.format(
                    delta=applied, affection=profile.affection
                )
                + (NEWBIE_PROTECTION_NOTE if protected else ""),
            )

        await send(Message(segments))

    @staticmethod
    def _add_line(segments: list[MessageSegment], user_id: str, line: str) -> None:
        segments.append(MessageSegment.text("\n"))
        segments.append(MessageSegment.at(user_id))
        segments.append(MessageSegment.text(f" {line}"))

    async def answer(
        self, group_id: str, user_id: str, nickname: str, text: str
    ) -> bool:
        """收到群消息时调用：答题窗口内记录第一个像答案的中文短句。"""

        pending = self.pending.get(str(group_id))
        if pending is None or not pending.accepting:
            return False
        if not looks_like_answer(text, len(pending.question.answer)):
            return False
        guess = normalize_answer(text)
        return pending.record(
            str(user_id), normalize_nickname(nickname, str(user_id)), guess
        )
