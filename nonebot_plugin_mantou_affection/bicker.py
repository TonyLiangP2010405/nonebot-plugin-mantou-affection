from __future__ import annotations

import asyncio
import json
import random
import re
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
BICKER_ROUNDS = 3
BICKER_MODEL_TIMEOUT = 20.0
BICKER_MAX_LINE = 60
BICKER_MAX_REPLY = 200
BICKER_MAX_STANCE = 40
BICKER_PREFIX = "🎤 馒头斗嘴！"

TOPIC_PROMPT = (
    "下面是本群最近的聊天记录：\n{recent}\n\n"
    "请根据这些内容，出一道适合群友和馒头正反辩论的题目，并给出正反两方的立场。\n"
    "要求：题目必须和群友说过的话有关；两方立场必须互相矛盾，不能是同一意思的两种说法；"
    "每条不超过 30 字；不要解释，不要加引号。\n"
    '只输出 JSON：{{"topic": "辩题", "group_side": "群友立场", '
    '"bot_side": "馒头立场"}}'
)
REPLY_PROMPT = (
    "你正在和群友打一场一对一的正反辩论，你是馒头。\n"
    "辩题：{topic}\n"
    "你的立场（馒头）：{bot_side}\n"
    "对方的立场（群友）：{group_side}\n\n"
    "已经发生的对话：\n{transcript}\n\n"
    "规则：\n"
    "1. 你的立场从头到尾不能变，不能中立、不能改口、不能替对方说话。\n"
    "2. 每一句都要先戳破对方上一句的具体说法，再用理由、例子、类比或反问把话拉回你的立场。\n"
    "3. 禁止说「你说得对」「有道理」「我同意」「我服了」「确实」这类认输或附和的话，"
    "也禁止只反问不表态。\n"
    "4. 不要重复你已经说过的理由，每一回合都要换一个新的攻击点。\n"
    "5. 可以损、可以阴阳、可以夸张，但必须是在为自己的立场辩护。\n\n"
    "只输出馒头接下来的一句回嘴，不超过 60 字，不要解释，不要加引号。"
)
REPLY_RETRY_NOTE = (
    "\n\n注意：你刚才那句是在附和对面的观点，等于放弃了自己的立场。"
    "重写一句：立场不变，直接反驳对方上一句的具体说法，不要认输，不要附和。"
)
_CONCESSION = re.compile(
    r"你说得(对|没错)|你说得有道理|你说服我了|我(同意|认输|错了)|"
    r"确实是我(错|输)|是我(错|输)了|我服了"
)
JUDGE_PROMPT = (
    "你是一个完全中立的斗嘴裁判，不代表 A 或 B。\n"
    "辩题：{topic}\n"
    "A 的立场：{a_side}\n"
    "B 的立场：{b_side}\n\n"
    "{transcript}\n\n"
    "只看辩论表现评分（0-10 分）：\n"
    "- 立场一致性：有没有始终为自己那一方辩护，有没有反过来赞同对方、改口或中立；\n"
    "- 反驳质量：有没有正面拆掉对方上一句的具体说法；\n"
    "- 论据与机智：理由、例子、类比、反问是否有说服力；\n"
    "- 切题：有没有跑题或重复自己。\n"
    "不要评判哪一方的立场更正确，也不要因为某一方更长或更像机器人就偏袒。\n"
    "reason 要指出决定胜负的具体回合和原因，说明谁在哪一句守住了或丢了立场。\n"
    '只输出 JSON：{{"a_score": 0, "b_score": 0, "winner": "A", '
    '"reason": "一句话说明胜负原因"}}\n'
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


@dataclass(frozen=True)
class BickerJudgement:
    group_won: bool
    reason: str
    group_score: int | None = None
    bot_score: int | None = None


@dataclass(frozen=True)
class BickerTopic:
    topic: str
    group_side: str
    bot_side: str


@dataclass
class PendingBicker:
    group_id: str
    trigger_id: str
    trigger_name: str
    topic: str
    send: Send
    group_side: str = ""
    bot_side: str = ""
    phase: str = "preparing"
    bets: dict[str, BickerBet] = field(default_factory=dict)
    transcript: list[tuple[str, str]] = field(default_factory=list)
    round: int = 0
    accepting: bool = True
    awaiting_reply: bool = False
    last_message_id: int | None = None
    bet_task: asyncio.Task | None = None
    round_task: asyncio.Task | None = None


def _first_line(text: str) -> str:
    for line in text.splitlines():
        cleaned = line.strip().strip("“”\"'")
        if cleaned:
            return cleaned[:BICKER_MAX_LINE]
    return ""


def _clip(text: str, limit: int) -> str:
    return text.strip().strip("“”\"'").strip()[:limit]


def _fallback_stances(topic: str) -> tuple[str, str]:
    return f"支持「{topic}」", f"反对「{topic}」"


def parse_topic(reply: str) -> BickerTopic | None:
    start = reply.find("{")
    end = reply.rfind("}")
    if start != -1 and end > start:
        try:
            payload: Any = json.loads(reply[start : end + 1])
        except (TypeError, ValueError):
            payload = None
        if isinstance(payload, dict):
            topic = _first_line(str(payload.get("topic") or ""))
            group_side = _clip(
                str(payload.get("group_side") or ""), BICKER_MAX_STANCE
            )
            bot_side = _clip(str(payload.get("bot_side") or ""), BICKER_MAX_STANCE)
            if not topic:
                return None
            if not group_side or not bot_side or group_side == bot_side:
                group_side, bot_side = _fallback_stances(topic)
            return BickerTopic(topic, group_side, bot_side)
    topic = _first_line(reply)
    if not topic:
        return None
    group_side, bot_side = _fallback_stances(topic)
    return BickerTopic(topic, group_side, bot_side)


def _concedes(line: str) -> bool:
    return bool(_CONCESSION.search(line))


def format_transcript(transcript: list[tuple[str, str]]) -> str:
    return "\n".join(f"{role}: {text}" for role, text in transcript)


def _score(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return max(0, min(10, number))


def _localize_reason(reason: str, group_is_a: bool) -> str:
    group_label = "群友" if group_is_a else "馒头"
    bot_label = "馒头" if group_is_a else "群友"
    reason = re.sub(r"(?<![A-Za-z])A(?![A-Za-z])\s*", group_label, reason)
    reason = re.sub(r"(?<![A-Za-z])B(?![A-Za-z])\s*", bot_label, reason)
    return reason[:100]


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
        self._lock = asyncio.Lock()

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
        if topic is None:
            self.pending.pop(key, None)
            return False
        pending.topic = topic.topic
        pending.group_side = topic.group_side
        pending.bot_side = topic.bot_side
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
    ) -> BickerTopic | None:
        lines = "\n".join(f"{name}: {text}" for name, text in recent[:30])
        try:
            reply = await self.memory.complete(
                group_id, TOPIC_PROMPT.format(recent=lines), timeout=self.model_timeout
            )
        except Exception:
            logger.exception("[mantou-affection] 生成斗嘴题目失败")
            return None
        return parse_topic(reply)

    def opening_message(self, pending: PendingBicker) -> Message:
        options = "\n".join(
            f"{index}. {'押群友赢' if side else '押群友输'} {stake} 点"
            for index, (side, stake) in BICKER_OPTIONS.items()
        )
        text = (
            f" {BICKER_PREFIX}\n"
            f"题目：{pending.topic}\n"
            f"群友立场：{pending.group_side}\n"
            f"馒头立场：{pending.bot_side}\n"
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
        option = BICKER_OPTIONS.get(text.strip())
        if option is None:
            return False
        async with self._lock:
            pending = self.pending.get(str(group_id))
            if pending is None or pending.phase != "betting" or not pending.accepting:
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
        pending.accepting = False
        await self._send_text(pending, self.summary_message(pending))
        await self._begin_debate(pending)

    async def _begin_debate(self, pending: PendingBicker) -> None:
        if self.pending.get(pending.group_id) is not pending:
            return
        pending.round = 0
        try:
            await pending.send(
                MessageSegment.at(pending.trigger_id)
                + MessageSegment.text(
                    f" 题目：{pending.topic}\n"
                    f"你的立场：{pending.group_side}\n"
                    f"我的立场：{pending.bot_side}\n"
                    f"来，说说你的看法。请在 {int(self.round_timeout)} 秒内回嘴。"
                )
            )
        except Exception:
            logger.exception("[mantou-affection] 发送斗嘴开场失败")
            await self._cancel(pending)
            return
        pending.phase = "debating"
        pending.accepting = True
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
        await self._finish(
            pending,
            judgement=BickerJudgement(group_won=False, reason="群友超时未回嘴"),
        )

    async def handle_debate(
        self,
        group_id: str,
        user_id: str,
        text: str,
        *,
        message_id: str | int | None = None,
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
        if message_id is not None:
            try:
                pending.last_message_id = int(message_id)
            except (TypeError, ValueError):
                pending.last_message_id = None
        pending.transcript.append(("群友", line))
        pending.round += 1
        try:
            reply = await self._generate_reply(pending)
        except Exception:
            logger.exception("[mantou-affection] 生成斗嘴回应失败")
            await self._cancel(pending)
            return True
        pending.transcript.append(("馒头", reply))
        segments: list[MessageSegment] = []
        if pending.last_message_id is not None:
            segments.append(MessageSegment.reply(pending.last_message_id))
        segments.append(MessageSegment.text(f" {reply}"))
        try:
            await pending.send(Message(segments))
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
            group_side=pending.group_side,
            bot_side=pending.bot_side,
            transcript=format_transcript(pending.transcript),
        )
        reply = await self.memory.complete(
            pending.group_id, prompt, timeout=self.model_timeout
        )
        line = _first_line(reply)
        if not line:
            raise GroupMemoryUnavailable("斗嘴回应为空")
        if not _concedes(line):
            return line
        try:
            retry = await self.memory.complete(
                pending.group_id,
                prompt + REPLY_RETRY_NOTE,
                timeout=self.model_timeout,
            )
        except Exception:
            logger.exception("[mantou-affection] 重写斗嘴回应失败")
            return line
        retry_line = _first_line(retry)
        if retry_line and not _concedes(retry_line):
            return retry_line
        return line

    async def _finish(
        self, pending: PendingBicker, *, judgement: BickerJudgement | None = None
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
        if judgement is None:
            try:
                judgement = await self._judge(pending)
            except Exception:
                logger.exception("[mantou-affection] 判定斗嘴输赢失败")
                await self._cancel(pending)
                return
        if judgement is None:
            await self._cancel(pending)
            return
        try:
            await self._settle(
                pending,
                trigger_won=judgement.group_won,
                judgement=judgement,
            )
        except Exception:
            logger.exception("[mantou-affection] 斗嘴结算失败")
            self.pending.pop(pending.group_id, None)

    async def _judge(self, pending: PendingBicker) -> BickerJudgement | None:
        group_is_a = self.rng.random() < 0.5
        a_side, b_side = self._stance_pair(pending, group_is_a)
        prompt = JUDGE_PROMPT.format(
            topic=pending.topic,
            a_side=a_side,
            b_side=b_side,
            transcript=self._anonymized_transcript(
                pending.transcript, group_is_a
            ),
        )
        reply = await self.memory.complete(
            pending.group_id, prompt, timeout=self.model_timeout
        )
        return self._parse_judgement(reply, group_is_a)

    @staticmethod
    def _stance_pair(pending: PendingBicker, group_is_a: bool) -> tuple[str, str]:
        if group_is_a:
            return pending.group_side, pending.bot_side
        return pending.bot_side, pending.group_side

    @staticmethod
    def _anonymized_transcript(
        transcript: list[tuple[str, str]], group_is_a: bool
    ) -> str:
        group_label = "A" if group_is_a else "B"
        bot_label = "B" if group_is_a else "A"
        return "\n".join(
            f"{group_label if role == '群友' else bot_label}: {text}"
            for role, text in transcript
        )

    def _parse_judgement(
        self, reply: str, group_is_a: bool
    ) -> BickerJudgement | None:
        payload: Any = None
        start = reply.find("{")
        end = reply.rfind("}")
        if start != -1 and end > start:
            try:
                payload = json.loads(reply[start : end + 1])
            except (TypeError, ValueError):
                payload = None
        if isinstance(payload, dict):
            a_score = _score(payload.get("a_score"))
            b_score = _score(payload.get("b_score"))
            group_score = a_score if group_is_a else b_score
            bot_score = b_score if group_is_a else a_score
            winner = str(payload.get("winner", "")).strip()
            reason = _localize_reason(
                str(payload.get("reason", "")).strip(), group_is_a
            )
            if winner.upper() in {"A", "B"}:
                group_won = (winner.upper() == "A") == group_is_a
                return BickerJudgement(
                    group_won,
                    reason or "裁判没有给出具体理由",
                    group_score,
                    bot_score,
                )
            if "群友" in winner:
                return BickerJudgement(
                    True,
                    reason or "裁判没有给出具体理由",
                    group_score,
                    bot_score,
                )
            if "馒头" in winner:
                return BickerJudgement(
                    False,
                    reason or "裁判没有给出具体理由",
                    group_score,
                    bot_score,
                )
            if a_score is not None and b_score is not None:
                if a_score == b_score:
                    group_won = self.rng.random() < 0.5
                    reason = (reason or "双方评分相同") + "，按平局掷骰判定"
                    return BickerJudgement(
                        group_won, reason, group_score, bot_score
                    )
                group_won = (a_score > b_score) == group_is_a
                return BickerJudgement(
                    group_won,
                    reason or "裁判没有给出具体理由",
                    group_score,
                    bot_score,
                )
        group_won_text = "群友赢" in reply
        bot_won_text = "馒头赢" in reply
        if group_won_text == bot_won_text:
            return None
        return BickerJudgement(group_won_text, "裁判没有给出具体理由")

    async def _settle(
        self,
        pending: PendingBicker,
        *,
        trigger_won: bool,
        judgement: BickerJudgement | None = None,
    ) -> None:
        pending.phase = "settled"
        pending.accepting = False
        self.pending.pop(pending.group_id, None)
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

        def add_text(line: str) -> None:
            if segments:
                segments.append(MessageSegment.text("\n"))
            segments.append(MessageSegment.text(f" {line}"))

        result = "群友赢" if trigger_won else "馒头赢"
        add(
            pending.trigger_id,
            f"斗嘴结果：{result}，好感度 {applied:+d}，"
            f"当前 {profile.affection}{note}",
        )
        if judgement is not None:
            score_text = ""
            if (
                judgement.group_score is not None
                and judgement.bot_score is not None
            ):
                score_text = (
                    f"（评分：群友 {judgement.group_score} / "
                    f"馒头 {judgement.bot_score}）"
                )
            add_text(f"裁判点评{score_text}：{judgement.reason}")
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
            winner_stake = sum(bet.actual for bet in winners)
            profits = {
                bet.user_id: pool * bet.actual // winner_stake
                for bet in winners
            }
            remainder = pool - sum(profits.values())
            for bet in sorted(
                winners, key=lambda item: (-item.actual, item.placed_at)
            )[:remainder]:
                profits[bet.user_id] += 1
            for bet in sorted(winners, key=lambda item: item.placed_at):
                profit = profits[bet.user_id]
                amount = bet.actual + profit
                _refund, refund_profile, _ = await self.service.adjust(
                    pending.group_id,
                    bet.user_id,
                    bet.nickname,
                    amount,
                    protect=False,
                )
                add(
                    bet.user_id,
                    f"押注 {bet.stake} 点押中，本金 {bet.actual} 点已退回，"
                    f"按比例分得 {profit} 点，当前 {refund_profile.affection}",
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

    async def _cancel(self, pending: PendingBicker) -> None:
        if pending.phase == "settled":
            return
        pending.phase = "settled"
        pending.accepting = False
        self.pending.pop(pending.group_id, None)
        current = asyncio.current_task()
        for task in (pending.bet_task, pending.round_task):
            if task is not None and task is not current:
                task.cancel()
        segments: list[MessageSegment] = []
        for bet in pending.bets.values():
            try:
                _refund, profile, _ = await self.service.adjust(
                    pending.group_id,
                    bet.user_id,
                    bet.nickname,
                    bet.actual,
                    protect=False,
                )
            except Exception:
                logger.exception("[mantou-affection] 斗嘴取消退款失败")
                continue
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
