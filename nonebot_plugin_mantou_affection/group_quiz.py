from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING

from nonebot import logger
from nonebot.adapters.onebot.v11 import Message, MessageSegment

from .config import Config
from .pk import PkQuestion, PkQuestionLibrary
from .service import AffectionService, normalize_nickname

if TYPE_CHECKING:
    from .ambient import EventCoordinator

BUNDLED_GROUP_QUIZ_PATH = Path(__file__).parent / "resources" / "group_quiz_questions.json"

GROUP_QUIZ_ANSWER_CHOICES = {"1": 0, "2": 1, "3": 2}
GROUP_QUIZ_RIGHT_REWARD = 2
GROUP_QUIZ_WRONG_PENALTY = -3
GROUP_QUIZ_TIMEOUT_PENALTY = -2

GROUP_QUIZ_PREFIX = "💬 群友问答！"
GROUP_QUIZ_QUESTION_REPLY = (
    "{prefix}\n"
    "馒头翻了翻聊天记录，考考你们……\n"
    "{text}\n"
    "1. {first}\n"
    "2. {second}\n"
    "3. {third}\n"
    "大家都可以回答，直接发送 1、2、3 即可（答题不用@），请在 {timeout} 秒内作答。\n"
    "答对 +2，答错 -3；被 @ 的触发者整局没答 -2（答错算已答，不叠加）。"
)
GROUP_QUIZ_RESULT_REPLY = "{prefix}\n答案：{number}. {option}"
GROUP_QUIZ_ANSWER_RIGHT_REPLY = "答对了"
GROUP_QUIZ_ANSWER_WRONG_REPLY = "答错了"
GROUP_QUIZ_ANSWER_REPLY = "{result}，好感度 {delta:+d}，当前 {affection}"
GROUP_QUIZ_TIMEOUT_REPLY = "整局未答，好感度 {delta:+d}，当前 {affection}"


class GroupQuizAnswer:
    """一次作答：某人选了第几个选项。"""

    def __init__(self, user_id: str, nickname: str, index: int):
        self.user_id = user_id
        self.nickname = nickname
        self.index = index


class PendingGroupQuiz:
    """一道进行中的群友问答，所有人都可以回答，被 @ 的触发者不答要扣分。"""

    def __init__(self, group_id: str, target: tuple[str, str], question: PkQuestion):
        self.group_id = group_id
        self.target = target
        self.question = question
        self.answers: dict[str, GroupQuizAnswer] = {}
        self.accepting = True
        self.task: asyncio.Task | None = None

    def record(self, user_id: str, nickname: str, index: int) -> bool:
        """记录作答，每人只算第一次。"""

        if not self.accepting or user_id in self.answers:
            return False
        self.answers[user_id] = GroupQuizAnswer(user_id, nickname, index)
        return True


class GroupQuizCoordinator:
    """管理群友问答的出题、作答与结算，同一个群同时只有一个活动。"""

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
        self.pending: dict[str, PendingGroupQuiz] = {}
        self._start_lock = asyncio.Lock()
        self.question_timeout = float(config.mantou_affection_group_quiz_timeout)

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

    def discard(self, group_id: str, pending: PendingGroupQuiz | None = None) -> None:
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
            question = self._draw_question()
            if question is None:
                return False
            pending = PendingGroupQuiz(group, (user, name), question)
            self.pending[group] = pending

        try:
            await send(
                MessageSegment.at(user)
                + MessageSegment.text(
                    " "
                    + GROUP_QUIZ_QUESTION_REPLY.format(
                        prefix=GROUP_QUIZ_PREFIX,
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

    def _draw_question(self) -> PkQuestion | None:
        """随机抽一道题；群友问答不标记已出，允许重复抽到同一题。"""

        if self.library is None:
            return None
        questions = self.library.questions
        if not questions:
            return None
        return self.rng.choice(questions)

    def schedule(
        self,
        pending: PendingGroupQuiz,
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
                logger.exception("[mantou-affection] 群友问答流程失败")
            finally:
                self.discard(pending.group_id, pending)

        pending.task = asyncio.create_task(run())
        return pending.task

    async def _settle(
        self,
        pending: PendingGroupQuiz,
        *,
        send: Callable[[Message | str], Awaitable[object]],
    ) -> None:
        question = pending.question
        segments = [
            MessageSegment.text(
                GROUP_QUIZ_RESULT_REPLY.format(
                    prefix=GROUP_QUIZ_PREFIX,
                    number=question.answer + 1,
                    option=question.options[question.answer],
                )
            )
        ]
        target_id, target_name = pending.target
        for answer in pending.answers.values():
            correct = answer.index == question.answer
            delta = GROUP_QUIZ_RIGHT_REWARD if correct else GROUP_QUIZ_WRONG_PENALTY
            applied, profile = await self.service.adjust(
                pending.group_id, answer.user_id, answer.nickname, delta
            )
            result = GROUP_QUIZ_ANSWER_RIGHT_REPLY if correct else GROUP_QUIZ_ANSWER_WRONG_REPLY
            self._add_line(
                segments,
                answer.user_id,
                GROUP_QUIZ_ANSWER_REPLY.format(
                    result=result, delta=applied, affection=profile.affection
                ),
            )

        if target_id not in pending.answers:
            applied, profile = await self.service.adjust(
                pending.group_id, target_id, target_name, GROUP_QUIZ_TIMEOUT_PENALTY
            )
            self._add_line(
                segments,
                target_id,
                GROUP_QUIZ_TIMEOUT_REPLY.format(delta=applied, affection=profile.affection),
            )

        await send(Message(segments))

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
        index = GROUP_QUIZ_ANSWER_CHOICES.get(text.strip())
        if index is None:
            return False
        pending.record(str(user_id), normalize_nickname(nickname, str(user_id)), index)
        return True
