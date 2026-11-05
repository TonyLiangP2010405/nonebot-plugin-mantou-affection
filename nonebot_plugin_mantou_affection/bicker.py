from __future__ import annotations

import asyncio
import random
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from nonebot import get_plugin, logger
from nonebot.adapters.onebot.v11 import Message, MessageSegment

from .config import Config
from .logic import NEWBIE_PROTECTION_NOTE
from .service import AffectionService, normalize_nickname

BICKER_STAKES = (1, 3, 5)
BICKER_OPTIONS: dict[str, tuple[bool, int]] = {
    "1": (True, 1),
    "2": (True, 3),
    "3": (True, 5),
    "4": (False, 1),
    "5": (False, 3),
    "6": (False, 5),
}
BICKER_ROUNDS = 5
BICKER_MODEL_TIMEOUT = 20.0
BICKER_MAX_LINE = 60
BICKER_MAX_REPLY = 200
BICKER_PREFIX = "🎤 馒头斗嘴！"

TOPIC_PROMPT = (
    "下面是本群最近的聊天记录：\n{recent}\n\n"
    "请根据这些内容，出一道适合群友和馒头斗嘴的题目。\n"
    "要求：一句话，不超过 40 字，必须和群友说过的话有关，不要解释，不要加引号。"
)
REPLY_PROMPT = (
    "你正在和群友斗嘴。题目：{topic}\n"
    "已经发生的对话：\n{transcript}\n\n"
    "请只输出馒头接下来的一句回嘴，不超过 60 字，不要解释，不要加引号。"
)
JUDGE_PROMPT = (
    "下面是馒头和群友的 5 回合斗嘴记录。\n题目：{topic}\n\n{transcript}\n\n"
    "请判断谁更占上风。只回复「群友赢」或「馒头赢」，不要解释。"
)

Send = Callable[[Message | str], Awaitable[object]]


class GroupMemoryUnavailable(RuntimeError):
    """群聊记忆插件未加载、未配置模型，或模型没有返回可用内容。"""


@dataclass
class _EmptyContext:
    recent_messages: list[Any] = field(default_factory=list)
    long_term_chunks: list[Any] = field(default_factory=list)


class GroupMemoryAdapter:
    """软依赖 nonebot-plugin-group-chat-memory 的薄适配层。"""

    def __init__(self, plugin_getter: Callable[[str], Any] | None = None):
        self._plugin_getter = plugin_getter or get_plugin

    def _module(self) -> Any | None:
        plugin = self._plugin_getter("nonebot_plugin_group_chat_memory")
        module = getattr(plugin, "module", None) or sys.modules.get(
            "nonebot_plugin_group_chat_memory"
        )
        return getattr(module, "handlers", None)

    def available(self) -> bool:
        handlers = self._module()
        if handlers is None:
            return False
        if not hasattr(handlers, "store") or not hasattr(handlers, "chat_client"):
            return False
        try:
            handlers.config.require_chat_config()
        except Exception:
            return False
        return True

    def _require(self) -> Any:
        handlers = self._module()
        if handlers is None or not self.available():
            raise GroupMemoryUnavailable("群聊记忆插件不可用")
        return handlers

    async def recent_messages(
        self, group_id: str, limit: int = 30
    ) -> list[tuple[str, str]]:
        handlers = self._require()
        messages = await handlers.store.recent_messages(int(group_id), limit)
        return [(str(message.display_name), str(message.text)) for message in messages]

    async def complete(
        self,
        group_id: str,
        text: str,
        *,
        timeout: float = BICKER_MODEL_TIMEOUT,
    ) -> str:
        handlers = self._require()
        return await asyncio.wait_for(
            handlers.chat_client.complete(
                _EmptyContext(), text, session_id=f"bicker:{group_id}"
            ),
            timeout=timeout,
        )


@dataclass
class BickerBet:
    user_id: str
    nickname: str
    side: bool
    stake: int
    actual: int
    placed_at: float


@dataclass
class PendingBicker:
    group_id: str
    trigger_id: str
    trigger_name: str
    topic: str
    send: Send
    phase: str = "preparing"
    bets: dict[str, BickerBet] = field(default_factory=dict)
    transcript: list[tuple[str, str]] = field(default_factory=list)
    round: int = 0
    accepting: bool = True
    awaiting_reply: bool = False
    bet_task: asyncio.Task | None = None
    round_task: asyncio.Task | None = None


def _first_line(text: str) -> str:
    for line in text.splitlines():
        cleaned = line.strip().strip("“”\"'")
        if cleaned:
            return cleaned[:BICKER_MAX_LINE]
    return ""


def format_transcript(transcript: list[tuple[str, str]]) -> str:
    return "\n".join(f"{role}: {text}" for role, text in transcript)


class BickerCoordinator:
    """管理馒头斗嘴的押注、回合和结算，同一个群同时只有一个活动。"""

    def __init__(
        self,
        service: AffectionService,
        config: Config,
        memory: Any | None = None,
        *,
        events: Any | None = None,
        rng: random.Random | None = None,
        model_timeout: float = BICKER_MODEL_TIMEOUT,
    ):
        self.service = service
        self.config = config
        self.memory = memory or GroupMemoryAdapter()
        self.events = events
        self.rng = rng or random.Random()
        self.model_timeout = model_timeout
        self.bet_window = float(config.mantou_affection_bicker_bet_window)
        self.round_timeout = float(config.mantou_affection_bicker_round_timeout)
        self.pending: dict[str, PendingBicker] = {}

    def available(self) -> bool:
        return bool(self.memory.available())

    def busy(self, group_id: str) -> bool:
        key = str(group_id)
        if key in self.pending:
            return True
        return self.events is not None and self.events.busy(key)

    async def start(
        self,
        *,
        group_id: str,
        user_id: str,
        nickname: str,
        send: Send,
    ) -> bool:
        key = str(group_id)
        if self.busy(key) or not self.available():
            return False
        pending = PendingBicker(
            group_id=key,
            trigger_id=str(user_id),
            trigger_name=normalize_nickname(nickname, str(user_id)),
            topic="",
            send=send,
        )
        self.pending[key] = pending
        try:
            recent = await self.memory.recent_messages(key, 30)
        except Exception:
            logger.exception("[mantou-affection] 读取群聊记忆失败")
            self.pending.pop(key, None)
            return False
        if not recent:
            self.pending.pop(key, None)
            return False
        topic = await self._generate_topic(key, recent)
        if not topic:
            self.pending.pop(key, None)
            return False
        pending.topic = topic
        pending.phase = "betting"
        try:
            await send(self.opening_message(pending))
        except Exception:
            logger.exception("[mantou-affection] 发送馒头斗嘴开场失败")
            self.pending.pop(key, None)
            return False
        pending.bet_task = asyncio.create_task(self._close_betting(pending))
        return True

    async def _generate_topic(
        self, group_id: str, recent: list[tuple[str, str]]
    ) -> str | None:
        lines = "\n".join(f"{name}: {text}" for name, text in recent[:30])
        try:
            reply = await self.memory.complete(
                group_id, TOPIC_PROMPT.format(recent=lines), timeout=self.model_timeout
            )
        except Exception:
            logger.exception("[mantou-affection] 生成斗嘴题目失败")
            return None
        return _first_line(reply) or None

    def opening_message(self, pending: PendingBicker) -> Message:
        options = "\n".join(
            f"{index}. {'押群友赢' if side else '押群友输'} {stake} 点"
            for index, (side, stake) in BICKER_OPTIONS.items()
        )
        text = (
            f" {BICKER_PREFIX}\n"
            f"题目：{pending.topic}\n"
            f"{int(self.bet_window)} 秒内可以押注，回复编号，每人一次：\n"
            f"{options}\n"
            "好感不足所选点数不能押；当事人不能押注；没人押注也照常斗嘴。"
        )
        return MessageSegment.at(pending.trigger_id) + MessageSegment.text(text)

    async def _send_text(
        self, pending: PendingBicker, text: str, *, at: str | None = None
    ) -> None:
        try:
            segments: list[MessageSegment] = []
            if at is not None:
                segments.append(MessageSegment.at(at))
            segments.append(MessageSegment.text(text))
            await pending.send(Message(segments))
        except Exception:
            logger.exception("[mantou-affection] 发送馒头斗嘴提示失败")

    async def answer(
        self, group_id: str, user_id: str, nickname: str, text: str
    ) -> bool:
        pending = self.pending.get(str(group_id))
        if pending is None or pending.phase != "betting" or not pending.accepting:
            return False
        option = BICKER_OPTIONS.get(text.strip())
        if option is None:
            return False
        user = str(user_id)
        if user == pending.trigger_id:
            await self._send_text(pending, " 你是当事人，不能押注。")
            return True
        if user in pending.bets:
            await self._send_text(pending, " 你已经押过了，不能改注。")
            return True
        side, stake = option
        current = (await self.service.profile(pending.group_id, user)).affection
        if current < stake:
            await self._send_text(
                pending, f" 好感不足 {stake} 点，可以改选更低的档位。"
            )
            return True
        name = normalize_nickname(nickname, user)
        applied, profile, _protected = await self.service.adjust(
            pending.group_id, user, name, -stake, protect=False
        )
        pending.bets[user] = BickerBet(
            user_id=user,
            nickname=name,
            side=side,
            stake=stake,
            actual=-applied,
            placed_at=time.monotonic(),
        )
        label = "押群友赢" if side else "押群友输"
        await self._send_text(
            pending, f" 押注成功：{label} {stake} 点，当前 {profile.affection}"
        )
        return True

    def summary_message(self, pending: PendingBicker) -> str:
        if not pending.bets:
            return " 没人押注，馒头直接开斗。"
        total = sum(bet.actual for bet in pending.bets.values())
        return f" 押注截止：{len(pending.bets)} 人参与，押注总额 {total} 点。"

    async def _close_betting(self, pending: PendingBicker) -> None:
        try:
            await asyncio.sleep(self.bet_window)
        except asyncio.CancelledError:
            return
        if (
            not pending.accepting
            or self.pending.get(pending.group_id) is not pending
        ):
            return
        pending.phase = "debating"
        await self._send_text(pending, self.summary_message(pending))
        await self._begin_debate(pending)

    async def _begin_debate(self, pending: PendingBicker) -> None:
        if (
            not pending.accepting
            or self.pending.get(pending.group_id) is not pending
        ):
            return
        pending.round = 0
        pending.phase = "debating"
        try:
            await pending.send(
                MessageSegment.at(pending.trigger_id)
                + MessageSegment.text(
                    f" 题目：{pending.topic}\n"
                    f"来，说说你的看法。请在 {int(self.round_timeout)} 秒内回嘴。"
                )
            )
        except Exception:
            logger.exception("[mantou-affection] 发送斗嘴开场失败")
            await self._cancel(pending)
            return
        self._schedule_round(pending)

    def _schedule_round(self, pending: PendingBicker) -> None:
        pending.round_task = asyncio.create_task(
            self._expire_round(pending, pending.round)
        )

    async def _expire_round(
        self, pending: PendingBicker, round_index: int
    ) -> None:
        try:
            await asyncio.sleep(self.round_timeout)
        except asyncio.CancelledError:
            return
        if (
            not pending.accepting
            or pending.phase != "debating"
            or pending.round != round_index
        ):
            return
        await self._finish(pending, trigger_won=False)

    async def handle_debate(
        self, group_id: str, user_id: str, text: str
    ) -> bool:
        if not self.is_debater_turn(group_id, user_id):
            return False
        pending = self.pending[str(group_id)]
        line = text.strip()[:BICKER_MAX_REPLY]
        if not line:
            return False
        pending.awaiting_reply = True
        if pending.round_task is not None:
            pending.round_task.cancel()
        pending.transcript.append(("群友", line))
        pending.round += 1
        try:
            reply = await self._generate_reply(pending)
        except Exception:
            logger.exception("[mantou-affection] 生成斗嘴回应失败")
            await self._cancel(pending)
            return True
        pending.transcript.append(("馒头", reply))
        try:
            await pending.send(Message(MessageSegment.text(f" {reply}")))
        except Exception:
            logger.exception("[mantou-affection] 发送斗嘴回应失败")
        if pending.round >= BICKER_ROUNDS:
            await self._finish(pending)
        else:
            await self._send_text(
                pending,
                f" 轮到你了，请在 {int(self.round_timeout)} 秒内回嘴。",
                at=pending.trigger_id,
            )
            pending.awaiting_reply = False
            self._schedule_round(pending)
        return True

    async def _generate_reply(self, pending: PendingBicker) -> str:
        prompt = REPLY_PROMPT.format(
            topic=pending.topic,
            transcript=format_transcript(pending.transcript),
        )
        reply = await self.memory.complete(
            pending.group_id, prompt, timeout=self.model_timeout
        )
        line = _first_line(reply)
        if not line:
            raise GroupMemoryUnavailable("斗嘴回应为空")
        return line

    async def _finish(
        self, pending: PendingBicker, *, trigger_won: bool | None = None
    ) -> None:
        if pending.phase != "debating" or not pending.accepting:
            return
        pending.phase = "judging"
        pending.accepting = False
        if (
            pending.round_task is not None
            and pending.round_task is not asyncio.current_task()
        ):
            pending.round_task.cancel()
        if trigger_won is None:
            try:
                trigger_won = await self._judge(pending)
            except Exception:
                logger.exception("[mantou-affection] 判定斗嘴输赢失败")
                await self._cancel(pending)
                return
        if trigger_won is None:
            await self._cancel(pending)
            return
        await self._settle(pending, trigger_won=trigger_won)

    async def _judge(self, pending: PendingBicker) -> bool | None:
        prompt = JUDGE_PROMPT.format(
            topic=pending.topic,
            transcript=format_transcript(pending.transcript),
        )
        reply = await self.memory.complete(
            pending.group_id, prompt, timeout=self.model_timeout
        )
        group_won = "群友赢" in reply
        bot_won = "馒头赢" in reply
        if group_won == bot_won:
            return None
        return group_won

    async def _settle(
        self, pending: PendingBicker, *, trigger_won: bool
    ) -> None:
        pending.phase = "settled"
        pending.accepting = False
        delta = 3 if trigger_won else -5
        applied, profile, protected = await self.service.adjust(
            pending.group_id, pending.trigger_id, pending.trigger_name, delta
        )
        note = NEWBIE_PROTECTION_NOTE if protected else ""
        segments: list[MessageSegment] = []

        def add(user_id: str, line: str) -> None:
            if segments:
                segments.append(MessageSegment.text("\n"))
            segments.append(MessageSegment.at(user_id))
            segments.append(MessageSegment.text(f" {line}"))

        result = "群友赢" if trigger_won else "馒头赢"
        add(
            pending.trigger_id,
            f"斗嘴结果：{result}，好感度 {applied:+d}，"
            f"当前 {profile.affection}{note}",
        )
        winners = [
            bet for bet in pending.bets.values() if bet.side == trigger_won
        ]
        losers = [
            bet for bet in pending.bets.values() if bet.side != trigger_won
        ]
        if pending.bets and not winners:
            for bet in pending.bets.values():
                _refund, refund_profile, _ = await self.service.adjust(
                    pending.group_id,
                    bet.user_id,
                    bet.nickname,
                    bet.actual,
                    protect=False,
                )
                add(
                    bet.user_id,
                    f"押注 {bet.stake} 点已退回，当前 {refund_profile.affection}",
                )
        elif winners:
            pool = sum(bet.actual for bet in losers)
            share, remainder = divmod(pool, len(winners))
            for index, bet in enumerate(
                sorted(winners, key=lambda item: item.placed_at)
            ):
                amount = bet.actual + share + (remainder if index == 0 else 0)
                refund, refund_profile, _ = await self.service.adjust(
                    pending.group_id,
                    bet.user_id,
                    bet.nickname,
                    amount,
                    protect=False,
                )
                add(
                    bet.user_id,
                    f"押注 {bet.stake} 点押中，到账 {refund:+d}，"
                    f"当前 {refund_profile.affection}",
                )
            for bet in losers:
                loser_profile = await self.service.profile(
                    pending.group_id, bet.user_id
                )
                add(
                    bet.user_id,
                    f"押注 {bet.stake} 点没押中，当前 {loser_profile.affection}",
                )
        try:
            await pending.send(Message(segments))
        except Exception:
            logger.exception("[mantou-affection] 发送斗嘴结算失败")
        self.pending.pop(pending.group_id, None)

    async def _cancel(self, pending: PendingBicker) -> None:
        if pending.phase == "settled":
            return
        pending.phase = "settled"
        pending.accepting = False
        current = asyncio.current_task()
        for task in (pending.bet_task, pending.round_task):
            if task is not None and task is not current:
                task.cancel()
        segments: list[MessageSegment] = []
        for bet in pending.bets.values():
            _refund, profile, _ = await self.service.adjust(
                pending.group_id,
                bet.user_id,
                bet.nickname,
                bet.actual,
                protect=False,
            )
            if segments:
                segments.append(MessageSegment.text("\n"))
            segments.append(MessageSegment.at(bet.user_id))
            segments.append(
                MessageSegment.text(
                    f" 押注 {bet.stake} 点已退回，当前 {profile.affection}"
                )
            )
        prefix = "\n" if segments else ""
        segments.append(
            MessageSegment.text(
                f"{prefix} 斗嘴中断，押注已全部退回，好感度没有变化。"
            )
        )
        try:
            await pending.send(Message(segments))
        except Exception:
            logger.exception("[mantou-affection] 发送斗嘴取消提示失败")
        self.pending.pop(pending.group_id, None)

    def discard(self, pending: PendingBicker) -> None:
        if self.pending.get(pending.group_id) is pending:
            self.pending.pop(pending.group_id, None)
        current = asyncio.current_task()
        for task in (pending.bet_task, pending.round_task):
            if task is not None and task is not current:
                task.cancel()

    def is_debater_turn(self, group_id: str, user_id: str) -> bool:
        pending = self.pending.get(str(group_id))
        return (
            pending is not None
            and pending.phase == "debating"
            and pending.accepting
            and not pending.awaiting_reply
            and str(user_id) == pending.trigger_id
            and pending.round < BICKER_ROUNDS
        )
