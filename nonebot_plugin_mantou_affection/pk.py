from __future__ import annotations

import asyncio
import json
import random
import time
from collections.abc import Awaitable, Callable, Sequence
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

PK_SIDE_A = "A"
PK_SIDE_B = "B"
PK_QUESTIONS_PER_SIDE = 3
PK_TOTAL_QUESTIONS = PK_QUESTIONS_PER_SIDE * 2
PK_SELECT_TIMEOUT = 60.0
PK_QUESTION_TIMEOUT = 60.0
PK_ANSWER_CHOICES = {"1": 0, "2": 1, "3": 2}

PK_PREFIX = "🍑 桃子问答 PK！"
PK_SELECT_PROMPT = "{prefix}\n想和谁 PK？请在 {timeout} 秒内回复编号：\n{candidates}"
PK_SELECT_TIMEOUT_REPLY = "{bot}等不到你的选择，这局先不开了。"
PK_BUSY_REPLY = "群里正有一场活动在进行，等它结束了再来吧。"
PK_NOT_IN_RANKING_REPLY = "你还不在{bot}的好感榜上，先去 /馒头互动 攒点好感再来吧。"
PK_COOLDOWN_WAIT_REPLY = "刚开过一局，{wait} 后才能再发起问答 PK。"
PK_COOLDOWN_REPLY = "又能发起问答 PK 啦，发送 /馒头问答PK 就能开一局。"
PK_EMPTY_BANK_REPLY = "题库已经出完了，请 SUPERUSER 往 resources/pk_questions.json 里补题。"
PK_LOW_BANK_REPLY = "题库只剩 {remaining} 题，凑不齐一局，请 SUPERUSER 补题。"
PK_NO_RIVAL_REPLY = "最近 {within} 条群消息里没有别的榜上群友说话，这次先不开啦。"
PK_ORDER_REPLY = "{bot}把签筒摇了摇：先让 {first} 答题，后让 {second} 答题。"
PK_QUESTION_REPLY = (
    "第 {index} 题：{text}\n"
    "1. {first}\n"
    "2. {second}\n"
    "3. {third}\n"
    "{timeout} 秒内作答，直接发 1、2、3 即可（答题不用@）"
)
PK_QUESTION_RESULT_REPLY = "第 {index} 题答案：{number}. {option}"
PK_ANSWER_RIGHT_REPLY = "答对了"
PK_ANSWER_WRONG_REPLY = "答错了"
PK_WRONG_SIDE_REPLY = "你已经参与了 {joined} 的队伍，不能再参与 {other} 的队伍了"
PK_SCORE_REPLY = "{side} 方答对 {hits} 题"
PK_WIN_REPLY = "这一边赢下了这局 PK！"
PK_DRAW_REPLY = "两边答对数一样，这局平局，谁都不用拿出好感度。"
PK_LOSE_REPLY = "输掉了 PK，拿出 {pool} 点好感度当奖池，好感度 -{pool}，当前 {affection}"
PK_CAPTAIN_WIN_REPLY = "获胜队长分得 {amount} 点好感度，当前 {affection}"
PK_CAPTAIN_EMPTY_REPLY = "获胜队长这次没有奖池可分，当前 {affection}"
PK_MATE_WIN_REPLY = "答对 {hits} 题，和队长一起分得 {amount} 点好感度，当前 {affection}"
PK_MATE_EMPTY_REPLY = "答对 {hits} 题，这次没有奖池可分，当前 {affection}"
PK_MATE_SKIP_REPLY = "答对 {hits} 题，没达到队长的手气，这次没分到好感度，当前 {affection}"
PK_LOSE_SIDE_REPLY = "答对 {hits} 题，队伍输了，好感度没有变化，当前 {affection}"
PK_LOW_STOCK_DM = (
    "馒头问答题库只剩 {remaining}/{total} 题啦，记得往 resources/pk_questions.json 里补题。"
)


@dataclass(frozen=True)
class PkQuestion:
    """一道 PK 题：题干、三个选项和正确选项下标（0 起）。"""

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
            logger.warning(f"[mantou-affection] 加载问答题库失败，暂时关闭问答 PK: {error}")
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


@dataclass(frozen=True)
class PkStart:
    """一次发起请求的结果：message 是要回给发起者的文案。"""

    ok: bool
    message: str


class PkAnswer:
    """一次作答：某人选了第几个选项。"""

    def __init__(self, user_id: str, nickname: str, index: int):
        self.user_id = user_id
        self.nickname = nickname
        self.index = index


class PkRound:
    """当前正在作答的一道题。"""

    def __init__(self, side: str, index: int, question: PkQuestion):
        self.side = side
        self.index = index
        self.question = question
        self.answers: dict[str, PkAnswer] = {}

    def record(self, user_id: str, nickname: str, index: int) -> bool:
        """记录作答，每人每题只算第一次。"""

        if user_id in self.answers:
            return False
        self.answers[user_id] = PkAnswer(user_id, nickname, index)
        return True


class PendingPk:
    """一局进行中的问答 PK：选人阶段或答题阶段。"""

    def __init__(
        self,
        group_id: str,
        initiator: tuple[str, str],
        candidates: Sequence[tuple[str, str]],
    ):
        self.group_id = group_id
        self.initiator = initiator
        self.candidates = tuple(candidates)
        self.phase = "select"
        self.selection: asyncio.Future[int] = asyncio.get_running_loop().create_future()
        self.send: Callable[[Message | str], Awaitable[object]] | None = None
        self.captains: dict[str, tuple[str, str]] = {}
        self.round: PkRound | None = None
        self.side_of: dict[str, str] = {}
        self.names: dict[str, str] = {}
        self.hits: dict[str, int] = {}
        self.task: asyncio.Task | None = None

    def add_hits(self, user_id: str, nickname: str, hits: int) -> None:
        self.names.setdefault(user_id, nickname)
        self.hits[user_id] = self.hits.get(user_id, 0) + hits

    def side_totals(self) -> dict[str, int]:
        """每边所有人的答对数之和。"""

        totals = {PK_SIDE_A: 0, PK_SIDE_B: 0}
        for user_id, hits in self.hits.items():
            side = self.side_of.get(user_id)
            if side in totals:
                totals[side] += hits
        return totals


class PkCoordinator:
    """管理问答 PK 的发起、选人、出题与结算，同一个群同时只有一个活动。"""

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
        self.select_timeout = PK_SELECT_TIMEOUT
        self.question_timeout = PK_QUESTION_TIMEOUT
        self.message_seq: dict[str, int] = {}
        self.last_seen: dict[tuple[str, str], tuple[int, str]] = {}
        self.cooldown_tasks: dict[tuple[str, str], asyncio.Task] = {}
        self.low_stock_warned = False

    # ------------------------------------------------------------ 最近发言

    def note_message(self, group_id: str, user_id: str, nickname: str) -> None:
        """记录一条群消息：本群消息计数 +1，并更新这个人的最后发言序号。"""

        group = str(group_id)
        seq = self.message_seq.get(group, 0) + 1
        self.message_seq[group] = seq
        self.last_seen[(group, str(user_id))] = (
            seq,
            normalize_nickname(nickname, str(user_id)),
        )

    def recent_speakers(self, group_id: str, *, within: int) -> set[str]:
        """最近 within 条群消息里出现过的人。"""

        group = str(group_id)
        cutoff = self.message_seq.get(group, 0) - within
        return {
            user_id
            for (seen_group, user_id), (seq, _name) in self.last_seen.items()
            if seen_group == group and seq > cutoff
        }

    # ------------------------------------------------------------ 题库余量

    @property
    def total_questions(self) -> int:
        return len(self.library.questions) if self.library is not None else 0

    async def remaining_questions(self) -> int:
        """还没出过的题目数量。"""

        used = await self.service.store.used_questions()
        return max(0, self.total_questions - len(set(used)))

    # ------------------------------------------------------------ 发起

    def busy(self, group_id: str) -> bool:
        """本群是否已经有待结算的活动：问答 PK、博弈、随机事件或找字小游戏。"""

        key = str(group_id)
        if key in self.pending:
            return True
        events = self.events
        return events is not None and events.busy(key)

    def discard(self, group_id: str) -> None:
        self.pending.pop(str(group_id), None)

    async def start(
        self,
        *,
        group_id: str,
        user_id: str,
        nickname: str,
        send: Callable[[Message | str], Awaitable[object]],
    ) -> PkStart:
        """发起一局 PK：检查席位、榜单、冷却与题库，然后进入选人阶段。"""

        group = str(group_id)
        user = str(user_id)
        name = normalize_nickname(nickname, user)
        if self.busy(group):
            return PkStart(False, PK_BUSY_REPLY)
        entries = await self.service.ranking(group)
        if not any(entry.profile.user_id == user for entry in entries):
            return PkStart(
                False,
                PK_NOT_IN_RANKING_REPLY.format(
                    bot=self.config.mantou_affection_bot_name
                ),
            )
        cooldown = await self.cooldown_remaining(group, user)
        if cooldown > 0:
            return PkStart(
                False, PK_COOLDOWN_WAIT_REPLY.format(wait=format_wait(cooldown))
            )
        remaining = await self.remaining_questions()
        if remaining <= 0:
            return PkStart(False, PK_EMPTY_BANK_REPLY)
        if remaining < PK_TOTAL_QUESTIONS:
            return PkStart(False, PK_LOW_BANK_REPLY.format(remaining=remaining))

        within = await self.service.pk_recent_messages()
        speakers = self.recent_speakers(group, within=within)
        candidates: list[tuple[str, str]] = []
        for entry in entries:
            candidate_id = entry.profile.user_id
            if candidate_id == user or candidate_id not in speakers:
                continue
            seen = self.last_seen.get((group, candidate_id))
            candidate_name = seen[1] if seen is not None else entry.profile.nickname
            candidates.append((candidate_id, candidate_name or candidate_id))
        if not candidates:
            return PkStart(False, PK_NO_RIVAL_REPLY.format(within=within))

        pending = PendingPk(group, (user, name), candidates)
        pending.send = send
        self.pending[group] = pending
        self.schedule(pending, send=send)
        listing = "\n".join(
            f"{index}. {candidate_name}"
            for index, (_candidate_id, candidate_name) in enumerate(candidates, start=1)
        )
        return PkStart(
            True,
            PK_SELECT_PROMPT.format(
                prefix=PK_PREFIX,
                timeout=int(self.select_timeout),
                candidates=listing,
            ),
        )

    def schedule(
        self,
        pending: PendingPk,
        *,
        send: Callable[[Message | str], Awaitable[object]],
    ) -> asyncio.Task:
        """后台跑完整局 PK：选人、六道题、结算，异常只记日志。"""

        async def run() -> None:
            try:
                await self._play(pending, send=send)
            except Exception:
                logger.exception("[mantou-affection] 问答 PK 流程失败")
            finally:
                self.discard(pending.group_id)

        pending.task = asyncio.create_task(run())
        return pending.task

    async def _play(
        self,
        pending: PendingPk,
        *,
        send: Callable[[Message | str], Awaitable[object]],
    ) -> None:
        """选人 → 定先后 → 两边各三题 → 结算。"""

        try:
            index = await asyncio.wait_for(
                pending.selection, timeout=self.select_timeout
            )
        except asyncio.TimeoutError:
            await send(
                PK_SELECT_TIMEOUT_REPLY.format(
                    bot=self.config.mantou_affection_bot_name
                )
            )
            return

        rival_id, rival_name = pending.candidates[index]
        captains = [pending.initiator, (rival_id, rival_name)]
        self.rng.shuffle(captains)
        (first_id, first_name), (second_id, second_name) = captains
        pending.captains = {
            PK_SIDE_A: (first_id, first_name),
            PK_SIDE_B: (second_id, second_name),
        }
        pending.phase = "question"

        await self._begin_cooldown(pending, send=send)
        await send(
            PK_ORDER_REPLY.format(
                bot=self.config.mantou_affection_bot_name,
                first=first_name,
                second=second_name,
            )
        )

        questions = await self._draw_questions()
        if questions is None:
            await send(
                PK_LOW_BANK_REPLY.format(remaining=await self.remaining_questions())
            )
            return
        for side_index, side in enumerate((PK_SIDE_A, PK_SIDE_B)):
            for question_index in range(PK_QUESTIONS_PER_SIDE):
                question = questions[side_index * PK_QUESTIONS_PER_SIDE + question_index]
                await self._ask(pending, side, question_index + 1, question, send=send)
        await self._settle(pending, send=send)

    async def _draw_questions(self) -> list[PkQuestion] | None:
        """从未用过的题里抽六道互不相同的题，并立刻标记为已用。"""

        if self.library is None:
            return None
        used = set(await self.service.store.used_questions())
        indices = self.library.pick(used, PK_TOTAL_QUESTIONS, self.rng)
        if indices is None:
            return None
        await self.service.store.add_used_questions(indices)
        return [self.library.questions[index] for index in indices]

    async def _ask(
        self,
        pending: PendingPk,
        side: str,
        index: int,
        question: PkQuestion,
        *,
        send: Callable[[Message | str], Awaitable[object]],
    ) -> None:
        """发一道题，等满窗口后公布答案与各人正误。"""

        round_ = PkRound(side, index, question)
        pending.round = round_
        captain_id, _captain_name = pending.captains[side]
        await send(
            MessageSegment.at(captain_id)
            + MessageSegment.text(
                " "
                + PK_QUESTION_REPLY.format(
                    index=index,
                    text=question.text,
                    first=question.options[0],
                    second=question.options[1],
                    third=question.options[2],
                    timeout=int(self.question_timeout),
                )
            )
        )
        await asyncio.sleep(self.question_timeout)
        pending.round = None
        for answer in round_.answers.values():
            pending.side_of.setdefault(answer.user_id, side)
            pending.add_hits(
                answer.user_id,
                answer.nickname,
                1 if answer.index == question.answer else 0,
            )
        await send(self._result_message(round_))

    @staticmethod
    def _result_message(round_: PkRound) -> Message:
        question = round_.question
        segments = [
            MessageSegment.text(
                PK_QUESTION_RESULT_REPLY.format(
                    index=round_.index,
                    number=question.answer + 1,
                    option=question.options[question.answer],
                )
            )
        ]
        for answer in round_.answers.values():
            correct = answer.index == question.answer
            segments.append(MessageSegment.text("\n"))
            segments.append(MessageSegment.at(answer.user_id))
            segments.append(
                MessageSegment.text(
                    " " + (PK_ANSWER_RIGHT_REPLY if correct else PK_ANSWER_WRONG_REPLY)
                )
            )
        return Message(segments)

    # ------------------------------------------------------------ 作答路由

    async def answer(self, group_id: str, user_id: str, nickname: str, text: str) -> bool:
        """收到消息时调用：选人阶段只认发起者的编号，答题阶段认 1/2/3。"""

        pending = self.pending.get(str(group_id))
        if pending is None:
            return False
        raw = text.strip()
        if pending.phase == "select":
            if str(user_id) != pending.initiator[0] or not raw.isdigit():
                return False
            index = int(raw) - 1
            if not 0 <= index < len(pending.candidates):
                return False
            if not pending.selection.done():
                pending.selection.set_result(index)
            return True

        round_ = pending.round
        if round_ is None:
            return False
        index = PK_ANSWER_CHOICES.get(raw)
        if index is None:
            return False
        user = str(user_id)
        joined = pending.side_of.get(user)
        if joined is not None and joined != round_.side:
            joined_name = pending.captains[joined][1]
            other_name = pending.captains[round_.side][1]
            await self._notify(
                pending,
                MessageSegment.at(user)
                + MessageSegment.text(
                    " " + PK_WRONG_SIDE_REPLY.format(joined=joined_name, other=other_name)
                ),
            )
            return True
        if not round_.record(user, normalize_nickname(nickname, user), index):
            return True
        pending.side_of.setdefault(user, round_.side)
        return True

    async def _notify(self, pending: PendingPk, message: Message) -> None:
        send = pending.send
        if send is None:
            return
        try:
            await send(message)
        except Exception:
            logger.exception("[mantou-affection] 发送问答 PK 提醒失败")

    # ------------------------------------------------------------ 结算

    async def _settle(
        self,
        pending: PendingPk,
        *,
        send: Callable[[Message | str], Awaitable[object]],
    ) -> None:
        totals = pending.side_totals()
        segments = [MessageSegment.text(PK_PREFIX)]
        for side in (PK_SIDE_A, PK_SIDE_B):
            captain_id, _captain_name = pending.captains[side]
            segments.append(MessageSegment.text("\n"))
            segments.append(MessageSegment.at(captain_id))
            segments.append(
                MessageSegment.text(
                    " " + PK_SCORE_REPLY.format(side=side, hits=totals[side])
                )
            )

        if totals[PK_SIDE_A] == totals[PK_SIDE_B]:
            segments.append(MessageSegment.text(f"\n{PK_DRAW_REPLY}"))
            await send(Message(segments))
            await self._check_low_stock()
            return

        winner = PK_SIDE_A if totals[PK_SIDE_A] > totals[PK_SIDE_B] else PK_SIDE_B
        loser = PK_SIDE_B if winner == PK_SIDE_A else PK_SIDE_A
        winner_id, _winner_name = pending.captains[winner]
        loser_id, loser_name = pending.captains[loser]
        segments.append(MessageSegment.text("\n"))
        segments.append(MessageSegment.at(winner_id))
        segments.append(MessageSegment.text(f" {PK_WIN_REPLY}"))

        pool = await self._take_pool(pending, loser_id, loser_name, segments)
        captain_hits = pending.hits.get(winner_id, 0)
        winners = [winner_id] + [
            user_id
            for user_id in pending.hits
            if user_id != winner_id
            and pending.side_of.get(user_id) == winner
            and pending.hits[user_id] >= captain_hits
        ]
        share, remainder = divmod(pool, len(winners))
        for position, user_id in enumerate(winners):
            amount = share + (remainder if position == 0 else 0)
            _, profile = await self.service.adjust(
                pending.group_id, user_id, pending.names.get(user_id, user_id), amount
            )
            if user_id == winner_id:
                template = PK_CAPTAIN_WIN_REPLY if amount > 0 else PK_CAPTAIN_EMPTY_REPLY
                line = template.format(amount=amount, affection=profile.affection)
            else:
                template = PK_MATE_WIN_REPLY if amount > 0 else PK_MATE_EMPTY_REPLY
                line = template.format(
                    hits=pending.hits.get(user_id, 0),
                    amount=amount,
                    affection=profile.affection,
                )
            self._add_line(segments, user_id, line)

        for user_id, hits in pending.hits.items():
            if user_id in winners:
                continue
            profile = await self.service.profile(pending.group_id, user_id)
            if pending.side_of.get(user_id) == winner:
                line = PK_MATE_SKIP_REPLY.format(hits=hits, affection=profile.affection)
            else:
                line = PK_LOSE_SIDE_REPLY.format(hits=hits, affection=profile.affection)
            self._add_line(segments, user_id, line)

        await send(Message(segments))
        await self._check_low_stock()

    @staticmethod
    def _add_line(segments: list[MessageSegment], user_id: str, line: str) -> None:
        segments.append(MessageSegment.text("\n"))
        segments.append(MessageSegment.at(user_id))
        segments.append(MessageSegment.text(f" {line}"))

    async def _take_pool(
        self,
        pending: PendingPk,
        loser_id: str,
        loser_name: str,
        segments: list[MessageSegment],
    ) -> int:
        """输家队长拿出好感度的 10% 当奖池，返回实际拿到的点数。"""

        current = (await self.service.profile(pending.group_id, loser_id)).affection
        pool = max(1, current * 10 // 100) if current > 0 else 0
        _, profile = await self.service.adjust(
            pending.group_id, loser_id, loser_name, -pool
        )
        self._add_line(
            segments,
            loser_id,
            PK_LOSE_REPLY.format(pool=pool, affection=profile.affection),
        )
        return pool

    # ------------------------------------------------------------ 冷却

    async def cooldown_remaining(self, group_id: str, user_id: str) -> float:
        """返回这个人还剩多少秒冷却，没有冷却返回 0。"""

        cooldowns = await self.service.store.cooldowns()
        expires = cooldowns.get(str(group_id), {}).get(str(user_id), 0.0)
        return max(0.0, expires - time.time())

    async def _begin_cooldown(
        self,
        pending: PendingPk,
        *,
        send: Callable[[Message | str], Awaitable[object]],
    ) -> None:
        """正式开赛：给发起者记冷却，并安排到点提醒。"""

        seconds = await self.service.pk_cooldown()
        if seconds <= 0:
            return
        initiator_id, _initiator_name = pending.initiator
        expires = time.time() + seconds
        await self.service.store.set_cooldown(pending.group_id, initiator_id, expires)
        self.schedule_cooldown_reminder(
            pending.group_id, initiator_id, seconds=seconds, send=send
        )

    def schedule_cooldown_reminder(
        self,
        group_id: str,
        user_id: str,
        *,
        seconds: float,
        send: Callable[[Message | str], Awaitable[object]],
    ) -> asyncio.Task:
        """到点后在群里 @ 这个人提醒可以再发起。"""

        async def run() -> None:
            try:
                await asyncio.sleep(seconds)
                await send(
                    MessageSegment.at(user_id)
                    + MessageSegment.text(f" {PK_COOLDOWN_REPLY}")
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("[mantou-affection] 发送问答 PK 冷却提醒失败")
            finally:
                self.cooldown_tasks.pop((str(group_id), str(user_id)), None)
                await self.service.store.clear_cooldown(str(group_id), str(user_id))

        task = asyncio.create_task(run())
        self.cooldown_tasks[(str(group_id), str(user_id))] = task
        return task

    # ------------------------------------------------------------ 余量提醒

    async def _check_low_stock(
        self, *, send_private: Callable[[str, str], Awaitable[object]] | None = None
    ) -> bool:
        """PK 结束后看余量：低于阈值提醒一次 superuser，回升后重置标记。"""

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


def format_wait(seconds: float) -> str:
    """把剩余秒数说成人话。"""

    total = max(1, int(seconds + 0.999))
    if total >= 3600:
        hours, remainder = divmod(total, 3600)
        minutes = remainder // 60
        return f"{hours} 小时 {minutes} 分钟" if minutes else f"{hours} 小时"
    if total >= 60:
        return f"{total // 60} 分钟"
    return f"{total} 秒"


async def announce_finished_cooldowns(
    coordinator: PkCoordinator,
    *,
    send: Callable[[str, Message], Awaitable[object]] | None = None,
) -> int:
    """重启后处理冷却记录：还在冷却中的立刻 @ 提醒，然后全部清除。

    send 是 (group_id, message) 形式的发送函数，不传就用当前 bot 发群消息。
    返回提醒的人数。
    """

    cooldowns = await coordinator.service.store.cooldowns()
    now = time.time()
    reminded = 0
    for group_id, users in cooldowns.items():
        for user_id, expires in users.items():
            if expires <= now:
                continue
            message = MessageSegment.at(user_id) + MessageSegment.text(
                f" {PK_COOLDOWN_REPLY}"
            )
            try:
                if send is not None:
                    await send(group_id, message)
                else:
                    await get_bot().send_group_msg(group_id=int(group_id), message=message)
                reminded += 1
            except Exception:
                logger.exception("[mantou-affection] 发送问答 PK 冷却恢复提醒失败")
    await coordinator.service.store.clear_cooldowns()
    return reminded
