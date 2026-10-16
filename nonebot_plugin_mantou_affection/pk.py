from __future__ import annotations

import asyncio
import json
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from nonebot import get_bot, get_driver, logger
from nonebot.adapters.onebot.v11 import Message, MessageSegment

from .config import Config
from .service import AffectionService, normalize_nickname

if TYPE_CHECKING:
    from .ambient import EventCoordinator

BUNDLED_PK_PATH = Path(__file__).parent / "resources" / "pk_questions.json"

PK_QUESTION_TIMEOUT = 30.0
PK_ANSWER_CHOICES = {"1": 0, "2": 1, "3": 2}
PK_RIGHT_REWARD = 3
PK_WRONG_PENALTY = -5
PK_TIMEOUT_PENALTY = -10

PK_PREFIX = "🍑 桃子问答！"
PK_QUESTION_REPLY = (
    "{prefix}\n{text}\n"
    "1. {first}\n"
    "2. {second}\n"
    "3. {third}\n"
    "被 @ 的群友必须作答，其他群友也可以回答；直接发送 1、2、3 即可（答题不用@）。\n"
    "答对 +3，答错 -5；被 @ 的群友超时未答 -10。请在 {timeout} 秒内作答。"
)
PK_EMPTY_BANK_REPLY = "题库已经出完了，请 SUPERUSER 往 resources/pk_questions.json 里补题。"
PK_RESULT_REPLY = "{prefix}\n答案：{number}. {option}"
PK_ANSWER_RIGHT_REPLY = "答对了"
PK_ANSWER_WRONG_REPLY = "答错了"
PK_ANSWER_REPLY = "{result}，好感度 {delta:+d}，当前 {affection}"
PK_TIMEOUT_REPLY = "超时未答，好感度 {delta:+d}，当前 {affection}"
PK_LOW_STOCK_DM = (
    "馒头问答题库只剩 {remaining}/{total} 题啦，记得往 resources/pk_questions.json 里补题。"
)


@dataclass(frozen=True)
class PkQuestion:
    """一道问答题：题干、三个选项和正确选项下标（0 起）。"""

    text: str
    options: tuple[str, str, str]
    answer: int


class PkQuestionLibrary:
    """加载问答题库，非法条目跳过并记录警告。"""

    def __init__(self, path: Path):
        self.path = path
        self._questions = self._load_file(path)

    @property
    def questions(self) -> tuple[PkQuestion, ...]:
        return self._questions

    @staticmethod
    def _load_file(path: Path) -> tuple[PkQuestion, ...]:
        try:
            with path.open("r", encoding="utf-8") as file:
                return PkQuestionLibrary._normalize(json.load(file))
        except (OSError, json.JSONDecodeError, ValueError) as error:
            logger.warning(f"[mantou-affection] 加载问答题库失败，暂时关闭随机问答: {error}")
            return ()

    @classmethod
    def _normalize(cls, data: Any) -> tuple[PkQuestion, ...]:
        raw_questions = data.get("questions") if isinstance(data, dict) else data
        if not isinstance(raw_questions, list):
            raise ValueError("问答题库根节点必须是数组（或带 questions 数组的对象）")

        questions: list[PkQuestion] = []
        for index, raw in enumerate(raw_questions):
            reason = cls._invalid_reason(raw)
            if reason is not None:
                logger.warning(f"[mantou-affection] 跳过第 {index + 1} 道问答题：{reason}")
                continue
            questions.append(
                PkQuestion(
                    text=str(raw["text"]).strip(),
                    options=tuple(str(option).strip() for option in raw["options"]),
                    answer=int(raw["answer"]),
                )
            )
        return tuple(questions)

    @staticmethod
    def _invalid_reason(raw: Any) -> str | None:
        """校验一道题的字段，合法时返回 None，否则返回跳过原因。"""

        if not isinstance(raw, dict):
            return "不是对象"
        text = raw.get("text")
        if not isinstance(text, str) or not text.strip():
            return "text 必须是题干"
        options = raw.get("options")
        if not isinstance(options, list) or len(options) != 3:
            return "options 必须正好 3 个选项"
        if not all(isinstance(option, str) and option.strip() for option in options):
            return "options 里必须都是文案"
        answer = raw.get("answer")
        if isinstance(answer, bool) or not isinstance(answer, int):
            return "answer 必须是 0~2 的整数"
        if not 0 <= answer <= 2:
            return "answer 必须在 0~2 之间"
        return None

    def pick(self, used: set[int], count: int, rng: random.Random) -> list[int] | None:
        """从未用过的题里随机抽 count 道，返回题号；不够时返回 None。"""

        available = [index for index in range(len(self._questions)) if index not in used]
        if len(available) < count:
            return None
        return rng.sample(available, count)


class PkAnswer:
    """一次作答：某人选了第几个选项。"""

    def __init__(self, user_id: str, nickname: str, index: int):
        self.user_id = user_id
        self.nickname = nickname
        self.index = index


class PendingPk:
    """一道进行中的随机问答，被点名者必须作答，其他群友可选答。"""

    def __init__(self, group_id: str, target: tuple[str, str], question: PkQuestion):
        self.group_id = group_id
        self.target = target
        self.question = question
        self.answers: dict[str, PkAnswer] = {}
        self.accepting = True
        self.task: asyncio.Task | None = None

    def record(self, user_id: str, nickname: str, index: int) -> bool:
        """记录作答，每人只算第一次。"""

        if not self.accepting or user_id in self.answers:
            return False
        self.answers[user_id] = PkAnswer(user_id, nickname, index)
        return True


class PkCoordinator:
    """管理随机问答的出题、作答与结算，同一个群同时只有一个活动。"""

    def __init__(
        self,
        service: AffectionService,
        config: Config,
        library: PkQuestionLibrary | None = None,
        *,
        events: EventCoordinator | None = None,
        rng: random.Random | None = None,
    ):
        self.service = service
        self.config = config
        self.library = library
        self.events = events
        self.rng = rng or random.Random()
        self.pending: dict[str, PendingPk] = {}
        self._start_lock = asyncio.Lock()
        self.question_timeout = PK_QUESTION_TIMEOUT
        self.low_stock_warned = False

    @property
    def total_questions(self) -> int:
        return len(self.library.questions) if self.library is not None else 0

    async def remaining_questions(self) -> int:
        """还没出过的题目数量。"""

        used = await self.service.store.used_questions()
        return max(0, self.total_questions - len(set(used)))

    def busy(self, group_id: str) -> bool:
        """本群是否已经有待结算的活动。"""

        key = str(group_id)
        if key in self.pending:
            return True
        events = self.events
        return events is not None and events.busy(key)

    def discard(self, group_id: str, pending: PendingPk | None = None) -> None:
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
        """给当前发言者出一道题；概率命中后的本次处理完成时返回 True。"""

        group = str(group_id)
        user = str(user_id)
        name = normalize_nickname(nickname, user)
        async with self._start_lock:
            if self.busy(group):
                return False
            question = await self._draw_question()
            if question is not None:
                pending = PendingPk(group, (user, name), question)
                self.pending[group] = pending

        if question is None:
            await send(PK_EMPTY_BANK_REPLY)
            await self._check_low_stock()
            return True

        try:
            await send(
                MessageSegment.at(user)
                + MessageSegment.text(
                    " "
                    + PK_QUESTION_REPLY.format(
                        prefix=PK_PREFIX,
                        text=question.text,
                        first=question.options[0],
                        second=question.options[1],
                        third=question.options[2],
                        timeout=int(self.question_timeout),
                    )
                )
            )
        except Exception:
            self.discard(group, pending)
            raise
        self.schedule(pending, send=send)
        return True

    async def _draw_question(self) -> PkQuestion | None:
        """从未用过的题里抽一道，并立刻标记为已用。"""

        if self.library is None:
            return None
        used = set(await self.service.store.used_questions())
        indices = self.library.pick(used, 1, self.rng)
        if indices is None:
            return None
        await self.service.store.add_used_questions(indices)
        return self.library.questions[indices[0]]

    def schedule(
        self,
        pending: PendingPk,
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
                logger.exception("[mantou-affection] 随机问答流程失败")
            finally:
                self.discard(pending.group_id, pending)

        pending.task = asyncio.create_task(run())
        return pending.task

    async def _settle(
        self,
        pending: PendingPk,
        *,
        send: Callable[[Message | str], Awaitable[object]],
    ) -> None:
        question = pending.question
        segments = [
            MessageSegment.text(
                PK_RESULT_REPLY.format(
                    prefix=PK_PREFIX,
                    number=question.answer + 1,
                    option=question.options[question.answer],
                )
            )
        ]
        target_id, target_name = pending.target
        for answer in pending.answers.values():
            correct = answer.index == question.answer
            delta = PK_RIGHT_REWARD if correct else PK_WRONG_PENALTY
            applied, profile = await self.service.adjust(
                pending.group_id, answer.user_id, answer.nickname, delta
            )
            result = PK_ANSWER_RIGHT_REPLY if correct else PK_ANSWER_WRONG_REPLY
            self._add_line(
                segments,
                answer.user_id,
                PK_ANSWER_REPLY.format(
                    result=result, delta=applied, affection=profile.affection
                ),
            )

        if target_id not in pending.answers:
            applied, profile = await self.service.adjust(
                pending.group_id, target_id, target_name, PK_TIMEOUT_PENALTY
            )
            self._add_line(
                segments,
                target_id,
                PK_TIMEOUT_REPLY.format(delta=applied, affection=profile.affection),
            )

        await send(Message(segments))
        await self._check_low_stock()

    @staticmethod
    def _add_line(segments: list[MessageSegment], user_id: str, line: str) -> None:
        segments.append(MessageSegment.text("\n"))
        segments.append(MessageSegment.at(user_id))
        segments.append(MessageSegment.text(f" {line}"))

    async def answer(self, group_id: str, user_id: str, nickname: str, text: str) -> bool:
        """收到群消息时调用：答题窗口内认 1/2/3，每人只算第一次。"""

        pending = self.pending.get(str(group_id))
        if pending is None or not pending.accepting:
            return False
        index = PK_ANSWER_CHOICES.get(text.strip())
        if index is None:
            return False
        pending.record(str(user_id), normalize_nickname(nickname, str(user_id)), index)
        return True

    async def _check_low_stock(
        self, *, send_private: Callable[[str, str], Awaitable[object]] | None = None
    ) -> bool:
        """问答结束后看余量：低于阈值提醒一次 superuser，回升后重置标记。"""

        threshold = self.config.mantou_affection_pk_low_stock_warning
        remaining = await self.remaining_questions()
        if remaining >= threshold:
            self.low_stock_warned = False
            return False
        if self.low_stock_warned:
            return False
        self.low_stock_warned = True
        logger.warning(
            f"[mantou-affection] 问答题库余量不足: 剩余 {remaining}/{self.total_questions}"
        )
        await self.notify_superusers(remaining, send_private=send_private)
        return True

    async def notify_superusers(
        self,
        remaining: int,
        *,
        send_private: Callable[[str, str], Awaitable[object]] | None = None,
    ) -> int:
        """私聊提醒所有 superuser，失败只记日志；返回成功条数。"""

        message = PK_LOW_STOCK_DM.format(remaining=remaining, total=self.total_questions)
        sent = 0
        for user_id in get_driver().config.superusers:
            try:
                if send_private is not None:
                    await send_private(str(user_id), message)
                else:
                    await get_bot().send_private_msg(user_id=int(user_id), message=message)
                sent += 1
            except Exception:
                logger.exception(f"[mantou-affection] 提醒 superuser {user_id} 题库余量失败")
        return sent
