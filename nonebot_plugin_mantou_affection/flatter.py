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

FLATTER_PREFIX = "🎤 献媚桃神！"
FLATTER_MAX_LINE = 120
FLATTER_MAX_REASON = 60
FLATTER_MODEL_TIMEOUT = 25.0
FLATTER_BATCH_SIZE = 20
FLATTER_VIOLATION_DELTA = -6
FLATTER_MAX_SCORE = 100
FLATTER_LAZY_CAP = 20
FLATTER_OFFTOPIC_CAP = 30

FLATTER_THEMES = (
    "夸桃神的游戏技术",
    "夸桃神的歌喉",
    "夸桃神的作息：晚上七点准时开播",
    "用星露谷夸桃神",
    "夸桃神的直播效果",
    "夸桃神对水友的好",
    "一句话让桃神今晚开播有动力",
    "用「咕咕」夸桃神",
    "夸桃神的神级反应",
    "夸桃神带水友上分",
    "用最夸张的比喻夸桃神",
    "自由发挥：用最有诚意的一句话夸桃神",
)

FLATTER_PROMPT = (
    "你是桃神（B站主播「小小桃纸哟」）的群管，负责给群友的献媚打分。\n"
    "桃神资料：B站 40 万粉游戏主播，最爱星露谷，什么游戏都播，"
    "晚上 7 点开播，日常自称「小小桃咕咕」。\n"
    "本局主题：{theme}\n\n"
    "群友的献媚：\n{entries}\n\n"
    "按 0-100 分评分，四个维度：\n"
    "- 具体度 0-40：有没有桃神的具体梗（星露谷、七点开播、咕咕、直播名场面）；\n"
    "- 新意 0-30：新角度、比喻、谐音、反差；复读和网络套话 0 分；\n"
    "- 真诚不尬 0-20：像真心夸，不油腻、不阴阳；\n"
    "- 节目效果 0-10：群友想笑、想接梗。\n"
    "分数要拉开差距，不要都挤在中间。\n"
    "给每条一个 flag：\n"
    "- ok：正常；\n"
    "- lazy：纯表情、少于 6 字、空泛套话（最高 20 分）；\n"
    "- offtopic：完全没提桃神或主播（最高 30 分）；\n"
    "- rude：阴阳怪气、明夸暗贬、擦边、冒犯、涉及现实隐私（0 分）；\n"
    "- copy：复读别人（0 分）。\n"
    "不要因为写得短就判 rude；不确定时用 ok。\n"
    '只输出 JSON：{{"scores": [{{"id": 1, "score": 80, "flag": "ok", '
    '"reason": "一句话点评"}}]}}'
)

FLATTER_FLAGS = {"ok", "lazy", "offtopic", "rude", "copy"}
NON_TEXT = re.compile(r"[\s\W_]+")

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

    async def complete(
        self,
        group_id: str,
        text: str,
        *,
        timeout: float = FLATTER_MODEL_TIMEOUT,
    ) -> str:
        handlers = self._require()
        return await asyncio.wait_for(
            handlers.chat_client.complete(
                _EmptyContext(), text, session_id=f"flatter:{group_id}"
            ),
            timeout=timeout,
        )


@dataclass
class FlatterEntry:
    user_id: str
    nickname: str
    text: str
    submitted_at: float
    copy: bool = False


@dataclass(frozen=True)
class FlatterScore:
    user_id: str
    score: int
    flag: str
    reason: str


@dataclass
class PendingFlatter:
    group_id: str
    theme: str
    send: Send
    phase: str = "collecting"
    entries: dict[str, FlatterEntry] = field(default_factory=dict)
    accepting: bool = True
    task: asyncio.Task | None = None


def _normalize(text: str) -> str:
    return NON_TEXT.sub("", text).lower()


def _clamp_score(value: Any) -> int:
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return 0
    return max(0, min(FLATTER_MAX_SCORE, number))


def parse_scores(
    reply: str, entries: list[FlatterEntry], start_index: int = 1
) -> dict[str, FlatterScore]:
    """解析模型的打分 JSON；无法解析时返回空字典，由调用方兜底。"""

    start = reply.find("{")
    end = reply.rfind("}")
    if start == -1 or end <= start:
        return {}
    try:
        payload: Any = json.loads(reply[start : end + 1])
    except (TypeError, ValueError):
        return {}
    items = payload.get("scores") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return {}
    by_id = {start_index + offset: entry for offset, entry in enumerate(entries)}
    scores: dict[str, FlatterScore] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            key = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        entry = by_id.get(key)
        if entry is None:
            continue
        flag = str(item.get("flag") or "ok").strip().lower()
        if flag not in FLATTER_FLAGS:
            flag = "ok"
        reason = str(item.get("reason") or "").strip()[:FLATTER_MAX_REASON]
        scores[entry.user_id] = FlatterScore(
            entry.user_id,
            _clamp_score(item.get("score")),
            flag,
            reason or "桃神点了点头",
        )
    return scores


def apply_flag_cap(score: int, flag: str) -> int:
    if flag in {"rude", "copy"}:
        return 0
    if flag == "lazy":
        return min(score, FLATTER_LAZY_CAP)
    if flag == "offtopic":
        return min(score, FLATTER_OFFTOPIC_CAP)
    return score


def rank_delta(rank: int, total: int) -> int:
    """按名次结算：前面加分，后面扣分；人数少时用更温和的表。"""

    if total <= 1:
        return 3
    if total == 2:
        return 3 if rank == 1 else -2
    if total == 3:
        return {1: 6, 2: 1, 3: -3}.get(rank, 0)
    if rank == 1:
        return 6
    if rank == 2:
        return 4
    if rank == 3:
        return 3
    if rank == total:
        return -6
    if rank == total - 1:
        return -4
    if rank == total - 2:
        return -2
    return 0


def format_entries(entries: list[FlatterEntry], start_index: int = 1) -> str:
    return "\n".join(
        f"{start_index + offset}. {entry.text}"
        for offset, entry in enumerate(entries)
    )


class FlatterCoordinator:
    """管理献媚桃神的收稿、打分和结算，同一个群同时只有一个活动。"""

    def __init__(
        self,
        service: AffectionService,
        config: Config,
        memory: Any | None = None,
        *,
        events: Any | None = None,
        rng: random.Random | None = None,
        model_timeout: float = FLATTER_MODEL_TIMEOUT,
    ):
        self.service = service
        self.config = config
        self.memory = memory or GroupMemoryAdapter()
        self.events = events
        self.rng = rng or random.Random()
        self.model_timeout = model_timeout
        self.window = float(config.mantou_affection_bicker_bet_window)
        self.pending: dict[str, PendingFlatter] = {}
        self._lock = asyncio.Lock()

    def available(self) -> bool:
        return bool(self.memory.available())

    def busy(self, group_id: str) -> bool:
        key = str(group_id)
        if key in self.pending:
            return True
        return self.events is not None and self.events.busy(key)

    async def start(self, *, group_id: str, send: Send) -> bool:
        key = str(group_id)
        if self.busy(key) or not self.available():
            return False
        pending = PendingFlatter(
            group_id=key,
            theme=self.rng.choice(FLATTER_THEMES),
            send=send,
        )
        self.pending[key] = pending
        try:
            await send(self.opening_message(pending))
        except Exception:
            logger.exception("[mantou-affection] 发送献媚桃神开场失败")
            self.pending.pop(key, None)
            return False
        pending.task = asyncio.create_task(self._close(pending))
        return True

    def opening_message(self, pending: PendingFlatter) -> Message:
        text = (
            f" {FLATTER_PREFIX}\n"
            f"主题：{pending.theme}\n"
            f"{int(self.window)} 秒内直接发一句话献媚，每人一条。\n"
            "按桃神打分排名，同分按提交时间先后排，不并列：第 1 名 +6，第 2 名 +4，第 3 名 +3；"
            "中间 0；倒数第 1 名 -6，倒数第 2 名 -4，倒数第 3 名 -2。\n"
            "空话/纯表情最多 20 分，跑题最多 30 分，"
            "阴阳怪气、擦边、复读别人直接 -6。\n"
            "人数少时用温和表：1 人 +3；2 人 +3 / -2；3 人 +6 / +1 / -3。"
        )
        return Message(MessageSegment.text(text))

    async def _send_text(self, pending: PendingFlatter, text: str) -> None:
        try:
            await pending.send(Message(MessageSegment.text(text)))
        except Exception:
            logger.exception("[mantou-affection] 发送献媚桃神提示失败")

    async def answer(
        self, group_id: str, user_id: str, nickname: str, text: str
    ) -> bool:
        async with self._lock:
            pending = self.pending.get(str(group_id))
            if (
                pending is None
                or pending.phase != "collecting"
                or not pending.accepting
            ):
                return False
            line = text.strip()
            if not line or line.startswith(("/", "／")):
                return False
            user = str(user_id)
            if user in pending.entries:
                await self._send_text(
                    pending, " 你已经献过媚了，等截止后一起打分。"
                )
                return True
            name = normalize_nickname(nickname, user)
            normalized = _normalize(line)
            entry = FlatterEntry(
                user_id=user,
                nickname=name,
                text=line[:FLATTER_MAX_LINE],
                submitted_at=time.monotonic(),
                copy=any(
                    _normalize(item.text) == normalized
                    for item in pending.entries.values()
                ),
            )
            pending.entries[user] = entry
            await self._send_text(
                pending,
                f" 献媚已收下（第 {len(pending.entries)} 位），截止后统一打分。",
            )
            return True

    async def _close(self, pending: PendingFlatter) -> None:
        try:
            await asyncio.sleep(self.window)
        except asyncio.CancelledError:
            return
        if (
            not pending.accepting
            or pending.phase != "collecting"
            or self.pending.get(pending.group_id) is not pending
        ):
            return
        pending.accepting = False
        if not pending.entries:
            await self._send_text(pending, " 没人献媚，桃神走了，本轮取消。")
            self.pending.pop(pending.group_id, None)
            return
        await self._send_text(
            pending,
            f" 献媚截止：{len(pending.entries)} 人参与，桃神正在打分…",
        )
        pending.phase = "judging"
        try:
            scores = await self._score(pending)
        except Exception:
            logger.exception("[mantou-affection] 献媚桃神打分失败")
            await self._send_text(
                pending, " 桃神今天没空看，本轮取消，好感度没有变化。"
            )
            self.pending.pop(pending.group_id, None)
            return
        await self._settle(pending, scores)

    async def _score(self, pending: PendingFlatter) -> dict[str, FlatterScore]:
        entries = list(pending.entries.values())
        scores: dict[str, FlatterScore] = {}
        for start in range(0, len(entries), FLATTER_BATCH_SIZE):
            batch = entries[start : start + FLATTER_BATCH_SIZE]
            prompt = FLATTER_PROMPT.format(
                theme=pending.theme,
                entries=format_entries(batch, start + 1),
            )
            reply = await self.memory.complete(
                pending.group_id, prompt, timeout=self.model_timeout
            )
            scores.update(parse_scores(reply, batch, start + 1))
        return scores

    async def _settle(
        self, pending: PendingFlatter, scores: dict[str, FlatterScore]
    ) -> None:
        pending.phase = "settled"
        self.pending.pop(pending.group_id, None)
        entries = sorted(pending.entries.values(), key=lambda item: item.submitted_at)
        resolved: list[tuple[FlatterEntry, FlatterScore]] = []
        for entry in entries:
            score = scores.get(entry.user_id)
            if score is None:
                score = FlatterScore(entry.user_id, 0, "lazy", "桃神没看懂这句")
            flag = "copy" if entry.copy else score.flag
            value = apply_flag_cap(score.score, flag)
            resolved.append(
                (entry, FlatterScore(entry.user_id, value, flag, score.reason))
            )

        ranked = [
            item for item in resolved if item[1].flag not in {"rude", "copy"}
        ]
        ranked.sort(key=lambda item: (-item[1].score, item[0].submitted_at))
        ranks = list(range(1, len(ranked) + 1))

        segments: list[MessageSegment] = [
            MessageSegment.text(
                f" {FLATTER_PREFIX}结果\n主题：{pending.theme}"
            )
        ]
        for (entry, score), rank in zip(ranked, ranks):
            applied, profile, protected = await self.service.adjust(
                pending.group_id,
                entry.user_id,
                entry.nickname,
                rank_delta(rank, len(ranked)),
            )
            note = NEWBIE_PROTECTION_NOTE if protected else ""
            segments.append(MessageSegment.text("\n"))
            segments.append(MessageSegment.at(entry.user_id))
            segments.append(
                MessageSegment.text(
                    f" 第 {rank} 名 · {score.score} 分 {applied:+d}："
                    f"{score.reason}，当前 {profile.affection}{note}"
                )
            )
        for entry, score in resolved:
            if score.flag not in {"rude", "copy"}:
                continue
            applied, profile, protected = await self.service.adjust(
                pending.group_id,
                entry.user_id,
                entry.nickname,
                FLATTER_VIOLATION_DELTA,
            )
            note = NEWBIE_PROTECTION_NOTE if protected else ""
            segments.append(MessageSegment.text("\n"))
            segments.append(MessageSegment.at(entry.user_id))
            segments.append(
                MessageSegment.text(
                    f" {score.score} 分 {applied:+d}（违规）："
                    f"{score.reason}，当前 {profile.affection}{note}"
                )
            )
        try:
            await pending.send(Message(segments))
        except Exception:
            logger.exception("[mantou-affection] 发送献媚桃神结算失败")

    def discard(self, pending: PendingFlatter) -> None:
        if self.pending.get(pending.group_id) is pending:
            self.pending.pop(pending.group_id, None)
        current = asyncio.current_task()
        if pending.task is not None and pending.task is not current:
            pending.task.cancel()
