from __future__ import annotations

import asyncio
import json
import random
import re
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from nonebot import logger
from nonebot.adapters.onebot.v11 import Message, MessageSegment

from .config import Config
from .service import AffectionService, normalize_nickname

if TYPE_CHECKING:
    from .ambient import EventCoordinator

BUNDLED_ROUNDS_PATH = Path(__file__).parent / "resources" / "bet_rounds.json"

BET_ORDER: tuple[int, ...] = (1, 2, 3, 4, 5)
BET_RIVALS = 2
BET_IDENTICAL_PENALTY = 5
BET_LOSS_RATE = 0.05
BET_TEXT_MIN = 2
BET_TEXT_MAX = 100

BET_PREFIX = "🎲 馒头博弈！"
BET_MESSAGE = (
    "{prefix}\n"
    "{opening}\n"
    "馒头摆出了五个数字：1 2 3 4 5；\n"
    "{window} 秒后馒头会把这五个数字的顺序打乱，并公布打乱后的顺序；\n"
    "被点名的三人必须在 {window} 秒内作答：直接发送你猜的顺序（五个数字，例如 3 5 1 4 2）；\n"
    "三个人的答案不能完全一样——如果三个人发的一模一样，\n"
    "三人各扣 {identical} 好感度，游戏直接结束；\n"
    "其它群友也可以回答（不用@），答案可以重复；\n"
    "结算：被点名的人里最接近打乱后顺序的获胜（并列都算赢），输的人扣自己好感度的 5% 汇成奖池；"
    "奖池平分给赢家，以及比被点名的三个人都更接近的其它群友，其余群友不加不减。"
)
BET_REVEAL_REPLY = "馒头打乱后的顺序：{order}"
BET_WIN_REPLY = "猜中 {hits} 个位置，赢得 {amount} 点好感度，当前 {affection}"
BET_WIN_EMPTY_REPLY = "猜中 {hits} 个位置，其它人没有可转让的好感度，当前 {affection}"
BET_LOSE_REPLY = "猜中 {hits} 个位置，被扣掉 {amount} 点好感度，当前 {affection}"
BET_LOSE_EMPTY_REPLY = "猜中 {hits} 个位置，好感度已经见底，没有可转让的，当前 {affection}"
BET_MISS_REPLY = "没提交答案，被扣掉 {amount} 点好感度，当前 {affection}"
BET_MISS_EMPTY_REPLY = "没提交答案，好感度已经见底，没有可转让的，当前 {affection}"
BET_OTHER_REPLY = (
    "猜中 {hits} 个位置，比被点名的人更接近，从奖池分得 {amount} 点好感度，当前 {affection}"
)
BET_OTHER_ZERO_SHARE_REPLY = (
    "猜中 {hits} 个位置，比被点名的人更接近，这次没有分到好感度，当前 {affection}"
)
BET_OTHER_POOL_EMPTY_REPLY = (
    "猜中 {hits} 个位置，比被点名的人更接近，但奖池里没有可转让的好感度，当前 {affection}"
)
BET_OTHER_SKIP_REPLY = "猜中 {hits} 个位置，没有超过被点名的人，本次不加不减"
BET_IDENTICAL_REPLY = "和另外两人发了一模一样的顺序，好感度 {delta:+d}，当前 {affection}"
BET_NOBODY_REPLY = "{bot}看了一圈，被点名的三个人一个都没提交，这局就这么散了。"

_SEPARATOR_PATTERN = re.compile(r"[\s、,，;；:：|/\\<>＞＜\-–—_.．]+|→|⇒")
_DIGIT_TABLE = str.maketrans("０１２３４５６７８９", "0123456789")
_CHINESE_DIGITS = str.maketrans({"一": "1", "二": "2", "三": "3", "四": "4", "五": "5"})


def parse_bet_answer(text: str) -> tuple[int, ...] | None:
    """把一条消息解析成 1~5 的排列，解析不出返回 None（安静忽略）。"""

    cleaned = _SEPARATOR_PATTERN.sub("", text).translate(_DIGIT_TABLE).translate(_CHINESE_DIGITS)
    if len(cleaned) != len(BET_ORDER):
        return None
    if any(char not in "12345" for char in cleaned):
        return None
    digits = tuple(int(char) for char in cleaned)
    return digits if sorted(digits) == list(BET_ORDER) else None


def similarity(digits: Sequence[int], target: Sequence[int]) -> int:
    """对应位置相同就算一个命中，返回 0~5 的相似度。"""

    return sum(1 for guess, answer in zip(digits, target) if guess == answer)


@dataclass(frozen=True)
class BetRound:
    """一道博弈题：打乱后的目标顺序，以及开场 / 揭晓 / 结算三句氛围文案。"""

    target: tuple[int, ...]
    opening: str
    reveal: str
    settle: str


class BetLibrary:
    """加载博弈题库，非法条目跳过并记录警告。"""

    def __init__(self, path: Path):
        self.path = path
        self._rounds = self._load_file(path)

    @property
    def rounds(self) -> tuple[BetRound, ...]:
        return self._rounds

    @staticmethod
    def _load_file(path: Path) -> tuple[BetRound, ...]:
        try:
            with path.open("r", encoding="utf-8") as file:
                return BetLibrary._normalize(json.load(file))
        except (OSError, json.JSONDecodeError, ValueError) as error:
            logger.warning(f"[mantou-affection] 加载博弈题库失败，暂时关闭馒头博弈: {error}")
            return ()

    @classmethod
    def _normalize(cls, data: Any) -> tuple[BetRound, ...]:
        raw_rounds = data.get("rounds") if isinstance(data, dict) else data
        if not isinstance(raw_rounds, list):
            raise ValueError("博弈题库根节点必须是数组（或带 rounds 数组的对象）")

        rounds: list[BetRound] = []
        for index, raw in enumerate(raw_rounds):
            reason = cls._invalid_reason(raw)
            if reason is not None:
                logger.warning(f"[mantou-affection] 跳过第 {index + 1} 道博弈题：{reason}")
                continue
            rounds.append(
                BetRound(
                    target=tuple(int(value) for value in raw["target"]),
                    opening=str(raw["opening"]).strip(),
                    reveal=str(raw["reveal"]).strip(),
                    settle=str(raw["settle"]).strip(),
                )
            )
        return tuple(rounds)

    @staticmethod
    def _invalid_reason(raw: Any) -> str | None:
        """校验一道题的字段，合法时返回 None，否则返回跳过原因。"""

        if not isinstance(raw, dict):
            return "不是对象"
        target = raw.get("target")
        if not isinstance(target, list) or len(target) != len(BET_ORDER):
            return f"target 必须正好有 {len(BET_ORDER)} 个数字"
        if not all(_is_order_digit(value) for value in target):
            return "target 必须是 1~5 的整数"
        if sorted(int(value) for value in target) != list(BET_ORDER):
            return "target 必须是 1~5 的排列"
        for key in ("opening", "reveal", "settle"):
            text = raw.get(key)
            if not isinstance(text, str) or not text.strip():
                return f"{key} 必须是文案"
            text = text.strip()
            if "\n" in text or "\r" in text:
                return f"{key} 不能有换行"
            if any(char.isdigit() for char in text):
                return f"{key} 不能出现数字（会剧透目标顺序）"
            if not BET_TEXT_MIN <= len(text) <= BET_TEXT_MAX:
                return f"{key} 长度必须在 {BET_TEXT_MIN}~{BET_TEXT_MAX} 字之间"
        return None

    def pick(self) -> BetRound | None:
        if not self._rounds:
            return None
        return random.SystemRandom().choice(self._rounds)


def _is_order_digit(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value in BET_ORDER


class BetAnswer:
    """一次作答：某人提交的排列，以及它命中了几个位置。"""

    def __init__(self, user_id: str, nickname: str, digits: tuple[int, ...], hits: int):
        self.user_id = user_id
        self.nickname = nickname
        self.digits = digits
        self.hits = hits


class PendingBet:
    """一局已经发出、正在等待作答的馒头博弈。"""

    def __init__(
        self,
        round_: BetRound,
        trigger_id: str,
        trigger_name: str,
        rivals: tuple[tuple[str, str], ...],
        send: Callable[[Message | str], Awaitable[object]],
    ):
        self.round = round_
        self.trigger_id = trigger_id
        self.trigger_name = trigger_name
        self.rivals = rivals
        self.send = send
        self.answers: dict[str, BetAnswer] = {}
        self.task: asyncio.Task | None = None

    @property
    def named(self) -> tuple[tuple[str, str], ...]:
        """被点名的三人：触发者加上抽中的两位群友。"""

        return ((self.trigger_id, self.trigger_name), *self.rivals)

    @property
    def named_ids(self) -> tuple[str, ...]:
        return tuple(user_id for user_id, _ in self.named)

    def record(self, user_id: str, nickname: str, text: str) -> bool:
        """记录作答，每人只算第一次有效提交。"""

        if user_id in self.answers:
            return False
        digits = parse_bet_answer(text)
        if digits is None:
            return False
        self.answers[user_id] = BetAnswer(
            user_id, nickname, digits, similarity(digits, self.round.target)
        )
        return True

    def all_named_submitted(self) -> bool:
        return all(user_id in self.answers for user_id in self.named_ids)

    def named_answers_identical(self) -> bool:
        return len({self.answers[user_id].digits for user_id in self.named_ids}) == 1

    def best_named_hits(self) -> int | None:
        """被点名者里已提交的最高命中数，无人提交时返回 None。"""

        hits = [
            self.answers[user_id].hits
            for user_id in self.named_ids
            if user_id in self.answers
        ]
        return max(hits) if hits else None

    def winner_ids(self) -> tuple[str, ...]:
        """达到最高命中数的被点名者都是赢家（并列时不止一人），按提交先后排序。"""

        best = self.best_named_hits()
        if best is None:
            return ()
        return tuple(
            user_id
            for user_id, answer in self.answers.items()
            if user_id in self.named_ids and answer.hits == best
        )


class BetCoordinator:
    """管理馒头博弈的触发、作答收集与结算，同一个群同时只有一个活动。"""

    def __init__(
        self,
        service: AffectionService,
        config: Config,
        library: BetLibrary | None = None,
        *,
        events: EventCoordinator | None = None,
        rng: random.Random | None = None,
    ):
        self.service = service
        self.config = config
        self.library = library
        self.events = events
        self.rng = rng or random.Random()
        self.pending: dict[str, PendingBet] = {}

    def busy(self, group_id: str) -> bool:
        """本群是否已经有待结算的活动：博弈、随机事件或找字小游戏。"""

        key = str(group_id)
        if key in self.pending:
            return True
        events = self.events
        return events is not None and events.busy(key)

    def discard(self, group_id: str) -> None:
        self.pending.pop(str(group_id), None)

    def message(self, pending: PendingBet) -> Message:
        """开场消息：@被点名的三人 + 开场语 + 规则，绝不提前透露目标顺序。"""

        segments = [MessageSegment.at(user_id) for user_id in pending.named_ids]
        segments.append(
            MessageSegment.text(
                " "
                + BET_MESSAGE.format(
                    prefix=BET_PREFIX,
                    opening=pending.round.opening,
                    window=self.config.mantou_affection_bet_window,
                    identical=BET_IDENTICAL_PENALTY,
                )
            )
        )
        return Message(segments)

    async def maybe_start(
        self,
        *,
        group_id: str,
        user_id: str,
        nickname: str,
        candidates: Iterable[Any],
        send: Callable[[Message | str], Awaitable[object]],
    ) -> bool:
        """按概率开一局博弈：本群已有活动、掷骰没中或凑不齐两人都返回 False。"""

        key = str(group_id)
        if send is None or self.busy(key):
            return False
        chance = await self.service.bet_probability()
        if self.rng.random() >= chance:
            return False
        rivals = await self._pick_rivals(candidates, str(user_id), key)
        if rivals is None:
            return False
        round_ = self.library.pick() if self.library is not None else None
        if round_ is None:
            return False
        pending = PendingBet(
            round_,
            str(user_id),
            normalize_nickname(nickname, str(user_id)),
            rivals,
            send,
        )
        self.pending[key] = pending
        try:
            await send(self.message(pending))
        except Exception:
            logger.exception("[mantou-affection] 发送馒头博弈开场消息失败")
            self.discard(key)
            return False
        self.schedule(
            pending,
            group_id=key,
            send=send,
            timeout=self.config.mantou_affection_bet_window,
        )
        return True

    async def _pick_rivals(
        self, candidates: Iterable[Any], trigger_id: str, group_id: str
    ) -> tuple[tuple[str, str], ...] | None:
        """排除触发者和好感度没超过门槛的群友后，随机抽两位互不相同的对手。"""

        members: dict[str, str] = {}
        for candidate in candidates:
            try:
                user_id, name = candidate
            except (TypeError, ValueError):
                continue
            key = str(user_id)
            if not key or key == trigger_id or key in members:
                continue
            members[key] = normalize_nickname(str(name), key)
        if len(members) < BET_RIVALS:
            return None
        threshold = self.config.mantou_affection_bet_min_affection
        eligible = [
            (user_id, name)
            for user_id, name in members.items()
            if (await self.service.profile(group_id, user_id)).affection > threshold
        ]
        if len(eligible) < BET_RIVALS:
            return None
        return tuple(self.rng.sample(eligible, BET_RIVALS))

    async def answer(self, group_id: str, user_id: str, nickname: str, text: str) -> bool:
        """收到消息时调用；解析成排列就算一次作答，重复提交忽略。

        三个被点名的群友都提交后会立刻检查答案是否完全相同，撞车就当场结束这一局。
        """

        pending = self.pending.get(str(group_id))
        if pending is None:
            return False
        if not pending.record(str(user_id), nickname, text):
            return parse_bet_answer(text) is not None
        if pending.all_named_submitted() and pending.named_answers_identical():
            await self._finish_identical(pending, group_id=str(group_id))
        return True

    async def _finish_identical(self, pending: PendingBet, *, group_id: str) -> None:
        """三人答案完全一样：各扣固定好感度、当场结束，不再等窗口也不结算。"""

        if pending.task is not None:
            pending.task.cancel()
        self.discard(group_id)
        segments: list[MessageSegment] = [MessageSegment.text(pending.round.settle)]
        for user_id, nickname in pending.named:
            _, profile = await self.service.adjust(
                group_id, user_id, nickname, -BET_IDENTICAL_PENALTY
            )
            segments.append(MessageSegment.text("\n"))
            segments.append(MessageSegment.at(user_id))
            segments.append(
                MessageSegment.text(
                    " "
                    + BET_IDENTICAL_REPLY.format(
                        delta=-BET_IDENTICAL_PENALTY, affection=profile.affection
                    )
                )
            )
        try:
            await pending.send(Message(segments))
        except Exception:
            logger.exception("[mantou-affection] 发送馒头博弈结束消息失败")

    def schedule(
        self,
        pending: PendingBet,
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
                logger.exception("[mantou-affection] 馒头博弈结算失败")
                return None
            try:
                await send(message)
            except Exception:
                logger.exception("[mantou-affection] 发送馒头博弈结果失败")
                return None
            return message

        pending.task = asyncio.create_task(run())
        return pending.task

    async def settle(self, pending: PendingBet, *, group_id: str, timeout: float) -> Message:
        """等满窗口后结算转让，返回合并了揭晓与各人结果的消息。"""

        try:
            await asyncio.sleep(timeout)
        finally:
            self.discard(group_id)
        return await self.settlement(pending, group_id=group_id)

    async def settlement(self, pending: PendingBet, *, group_id: str) -> Message:
        """按目标顺序算命中数：输家的好感度汇成奖池，平分给赢家和更接近的围观群友。"""

        order = " ".join(str(digit) for digit in pending.round.target)
        head = f"{pending.round.reveal}\n{BET_REVEAL_REPLY.format(order=order)}"
        winners = pending.winner_ids()
        if not winners:
            return Message(
                [
                    MessageSegment.text(
                        f"{head}\n{pending.round.settle}\n"
                        f"{BET_NOBODY_REPLY.format(bot=self.config.mantou_affection_bot_name)}"
                    )
                ]
            )

        loser_entries: list[tuple[str, list[MessageSegment]]] = []
        pool = 0
        for user_id in pending.named_ids:
            if user_id in winners:
                continue
            amount, line = await self._take_share(group_id, pending, user_id)
            pool += amount
            loser_entries.append((user_id, [MessageSegment.text(f" {line}")]))

        best_named = pending.best_named_hits()
        sharers: list[tuple[str, str]] = []
        skip_entries: list[tuple[str, list[MessageSegment]]] = []
        for user_id, answer in pending.answers.items():
            if user_id in winners:
                sharers.append((user_id, "winner"))
            elif user_id in pending.named_ids:
                continue
            elif best_named is not None and answer.hits > best_named:
                sharers.append((user_id, "other"))
            else:
                line = BET_OTHER_SKIP_REPLY.format(hits=answer.hits)
                skip_entries.append((user_id, [MessageSegment.text(f" {line}")]))

        share, remainder = divmod(pool, len(sharers))
        sharer_entries: list[tuple[str, list[MessageSegment]]] = []
        for index, (user_id, kind) in enumerate(sharers):
            amount = share + (remainder if index == 0 else 0)
            answer = pending.answers[user_id]
            _, profile = await self.service.adjust(
                group_id, user_id, answer.nickname, amount
            )
            if kind == "winner":
                partners = [winner_id for winner_id in winners if winner_id != user_id]
                line_segments = self._winner_line(
                    pending, user_id, partners, amount, pool, profile.affection
                )
            else:
                line_segments = [
                    MessageSegment.text(
                        f" {self._other_line(answer.hits, amount, pool, profile.affection)}"
                    )
                ]
            sharer_entries.append((user_id, line_segments))

        segments: list[MessageSegment] = [
            MessageSegment.text(f"{head}\n{pending.round.settle}")
        ]
        for user_id, line_segments in sharer_entries + loser_entries + skip_entries:
            segments.append(MessageSegment.text("\n"))
            segments.append(MessageSegment.at(user_id))
            segments.extend(line_segments)
        return Message(segments)

    @staticmethod
    def _other_line(hits: int, amount: int, pool: int, affection: int) -> str:
        """围观群友那一行：从奖池分账，分不到时照实说明原因。"""

        if amount > 0:
            return BET_OTHER_REPLY.format(hits=hits, amount=amount, affection=affection)
        if pool > 0:
            return BET_OTHER_ZERO_SHARE_REPLY.format(hits=hits, affection=affection)
        return BET_OTHER_POOL_EMPTY_REPLY.format(hits=hits, affection=affection)


    def _winner_line(
        self,
        pending: PendingBet,
        winner_id: str,
        partners: list[str],
        amount: int,
        pool: int,
        affection: int,
    ) -> list[MessageSegment]:
        """赢家那一行；并列获胜时把其它赢家也 @ 出来。"""

        answer = pending.answers[winner_id]
        if not partners:
            template = BET_WIN_REPLY if amount > 0 else BET_WIN_EMPTY_REPLY
            line = template.format(hits=answer.hits, amount=amount, affection=affection)
            return [MessageSegment.text(f" {line}")]

        segments = [MessageSegment.text(f" 猜中 {answer.hits} 个位置，与 ")]
        for index, partner in enumerate(partners):
            if index:
                segments.append(MessageSegment.text("、"))
            segments.append(MessageSegment.at(partner))
        if amount > 0:
            tail = f" 并列获胜，分得 {amount} 点好感度，当前 {affection}"
        elif pool > 0:
            tail = f" 并列获胜，这次没有分到好感度，当前 {affection}"
        else:
            tail = f" 并列获胜，其它人没有可转让的好感度，当前 {affection}"
        segments.append(MessageSegment.text(tail))
        return segments

    async def _take_share(
        self, group_id: str, pending: PendingBet, user_id: str
    ) -> tuple[int, str]:
        """扣掉一位输家的好感度，返回实际扣到的点数和这一行的文案。"""

        answer = pending.answers.get(user_id)
        nickname = answer.nickname if answer is not None else self._name_of(pending, user_id)
        current = (await self.service.profile(group_id, user_id)).affection
        amount = max(1, int(current * BET_LOSS_RATE)) if current > 0 else 0
        _, profile = await self.service.adjust(group_id, user_id, nickname, -amount)
        if answer is None:
            template = BET_MISS_REPLY if amount > 0 else BET_MISS_EMPTY_REPLY
            return amount, template.format(amount=amount, affection=profile.affection)
        template = BET_LOSE_REPLY if amount > 0 else BET_LOSE_EMPTY_REPLY
        return amount, template.format(
            hits=answer.hits, amount=amount, affection=profile.affection
        )

    @staticmethod
    def _name_of(pending: PendingBet, user_id: str) -> str:
        for named_id, name in pending.named:
            if named_id == user_id:
                return name
        return user_id
