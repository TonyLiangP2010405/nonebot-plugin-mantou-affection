from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from typing import TYPE_CHECKING

from nonebot import logger
from nonebot.adapters.onebot.v11 import MessageSegment

from .service import normalize_nickname
from .wheel_memes import MemeAssetMissing, MemeGenerationError

if TYPE_CHECKING:
    from .ambient import EventCoordinator


@dataclass(frozen=True)
class WheelOutcome:
    kind: str
    value: int = 0


WHEEL_OUTCOMES: tuple[tuple[WheelOutcome, int], ...] = (
    (WheelOutcome("meme"), 20),
    (WheelOutcome("poke", 1), 12),
    (WheelOutcome("poke", 3), 8),
    (WheelOutcome("poke", 5), 5),
    (WheelOutcome("poke", 10), 4),
    (WheelOutcome("poke", 20), 1),
    (WheelOutcome("affection", 1), 7),
    (WheelOutcome("affection", 2), 6),
    (WheelOutcome("affection", 3), 5),
    (WheelOutcome("affection", 5), 4),
    (WheelOutcome("affection", 10), 3),
    (WheelOutcome("affection", 20), 1),
    (WheelOutcome("affection", -1), 7),
    (WheelOutcome("affection", -2), 6),
    (WheelOutcome("affection", -3), 5),
    (WheelOutcome("affection", -5), 3),
    (WheelOutcome("affection", -10), 2),
    (WheelOutcome("affection", -20), 1),
)


def draw_wheel_outcome(rng: random.Random) -> WheelOutcome:
    roll = rng.randrange(100)
    cursor = 0
    for outcome, weight in WHEEL_OUTCOMES:
        cursor += weight
        if roll < cursor:
            return outcome
    raise AssertionError("转盘权重必须合计 100")


def draw_not_operate_delta(rng: random.Random) -> int:
    return 2 if rng.random() < 1 / 3 else -2


WHEEL_MAX_PARTICIPANTS = 5
WHEEL_JOIN_FEE = 2


@dataclass
class WheelParticipant:
    user_id: str
    nickname: str
    fee_paid: bool = False
    refunded: bool = False


class PendingWheel:
    def __init__(self, group_id: str, user_id: str, nickname: str, *, bot, send):
        self.group_id = group_id
        self.trigger = WheelParticipant(user_id, nickname)
        self.participants: list[WheelParticipant] = []
        self.bot = bot
        self.send = send
        self.accepting = True
        self.task: asyncio.Task | None = None


class WheelCoordinator:
    def __init__(
        self,
        service,
        config,
        *,
        events: EventCoordinator | None = None,
        rng: random.Random | None = None,
        memes=None,
        resources=None,
        poke_delay: float = 0.2,
    ):
        self.service = service
        self.config = config
        self.events = events
        self.rng = rng or random.Random()
        self.memes = memes
        self.resources = resources
        self.poke_delay = poke_delay
        self.choice_timeout = float(config.mantou_affection_wheel_timeout)
        self.pending: dict[str, PendingWheel] = {}
        self._lock = asyncio.Lock()

    def busy(self, group_id: str) -> bool:
        key = str(group_id)
        if key in self.pending:
            return True
        return self.events is not None and self.events.busy(key)

    def discard(self, pending: PendingWheel) -> None:
        if self.pending.get(pending.group_id) is pending:
            self.pending.pop(pending.group_id, None)

    async def start(self, *, group_id: str, user_id: str, nickname: str, bot, send) -> bool:
        group = str(group_id)
        user = str(user_id)
        async with self._lock:
            if self.busy(group):
                return False
            pending = PendingWheel(
                group, user, normalize_nickname(nickname, user), bot=bot, send=send
            )
            self.pending[group] = pending
        try:
            await send(
                MessageSegment.at(user)
                + MessageSegment.text(
                    f" 馒头命运齿轮转盘出现了！请在 {int(self.choice_timeout)} 秒内"
                    "发送“运作”或“不运作”；其他群友发送“参与”可支付 2 点好感加入，最多 5 人。"
                    "运作时每个人独立抽取，触发者免费。"
                )
            )
        except Exception:
            self.discard(pending)
            raise
        async with self._lock:
            if self.pending.get(group) is pending and pending.accepting:
                pending.task = asyncio.create_task(self._expire(pending))
        return True

    async def answer(self, group_id: str, user_id: str, nickname: str, text: str) -> bool:
        pending = self.pending.get(str(group_id))
        keyword = text.strip()
        user = str(user_id)
        if pending is None or keyword not in {"参与", "运作", "不运作"}:
            return False
        if keyword == "参与":
            return await self._join(pending, user, nickname)
        if user != pending.trigger.user_id:
            if pending.accepting:
                await pending.send("只有被 @ 的触发者能选择是否运作。")
            return True
        async with self._lock:
            if self.pending.get(pending.group_id) is not pending or not pending.accepting:
                return True
            pending.accepting = False
            if pending.task is not None:
                pending.task.cancel()
            if keyword == "运作":
                pending.task = asyncio.create_task(self._run_operation(pending))
            else:
                pending.task = asyncio.create_task(self._run_not_operate(pending))
        return True

    async def _join(self, pending: PendingWheel, user_id: str, nickname: str) -> bool:
        async with self._lock:
            if not pending.accepting or self.pending.get(pending.group_id) is not pending:
                return True
            if user_id == pending.trigger.user_id:
                await pending.send("触发者免费，不需要再发送参与。")
                return True
            if any(item.user_id == user_id for item in pending.participants):
                await pending.send("你已经参与本轮转盘了。")
                return True
            if len(pending.participants) >= WHEEL_MAX_PARTICIPANTS:
                await pending.send("本轮付费参与名额已满。")
                return True
            name = normalize_nickname(nickname, user_id)
            applied, profile = await self.service.adjust(
                pending.group_id, user_id, name, -WHEEL_JOIN_FEE
            )
            if applied != -WHEEL_JOIN_FEE:
                if applied:
                    await self.service.adjust(pending.group_id, user_id, name, -applied)
                await pending.send("参与需要 2 点好感，余额不足。")
                return True
            pending.participants.append(WheelParticipant(user_id, name, fee_paid=True))
            await pending.send(
                MessageSegment.at(user_id)
                + MessageSegment.text(
                    f" 参与费 {applied:+d}，当前好感 {profile.affection}；已加入转盘。"
                )
            )
        return True

    async def _refund(self, pending: PendingWheel, participant: WheelParticipant):
        if not participant.fee_paid or participant.refunded:
            return None
        applied, profile = await self.service.adjust(
            pending.group_id, participant.user_id, participant.nickname, WHEEL_JOIN_FEE
        )
        participant.refunded = True
        await pending.send(
            MessageSegment.at(participant.user_id)
            + MessageSegment.text(f" 已退回参与费 {applied:+d}，当前好感 {profile.affection}")
        )
        return applied, profile

    async def _expire(self, pending: PendingWheel) -> None:
        try:
            await asyncio.sleep(self.choice_timeout)
        except asyncio.CancelledError:
            return
        async with self._lock:
            if self.pending.get(pending.group_id) is not pending or not pending.accepting:
                return
            pending.accepting = False
        try:
            for participant in pending.participants:
                await self._refund(pending, participant)
            await pending.send("馒头命运齿轮转盘超时取消，参与费已退回。")
        finally:
            self.discard(pending)

    async def _run_not_operate(self, pending: PendingWheel) -> None:
        try:
            trigger = pending.trigger
            delta = draw_not_operate_delta(self.rng)
            applied, profile = await self.service.adjust(
                pending.group_id, trigger.user_id, trigger.nickname, delta
            )
            await pending.send(
                MessageSegment.at(trigger.user_id)
                + MessageSegment.text(f" 选择不运作，好感 {applied:+d}，当前 {profile.affection}")
            )
            for participant in pending.participants:
                await self._refund(pending, participant)
        finally:
            self.discard(pending)

    async def _run_operation(self, pending: PendingWheel) -> None:
        try:
            for participant in [pending.trigger, *pending.participants]:
                outcome = draw_wheel_outcome(self.rng)
                if outcome.kind == "affection":
                    applied, profile = await self.service.adjust(
                        pending.group_id, participant.user_id, participant.nickname, outcome.value
                    )
                    fee = WHEEL_JOIN_FEE if participant.fee_paid else 0
                    await pending.send(
                        MessageSegment.at(participant.user_id)
                        + MessageSegment.text(
                            f" 转盘好感 {applied:+d}，本轮净变化 {applied - fee:+d}，"
                            f"当前 {profile.affection}"
                        )
                    )
                elif outcome.kind == "poke":
                    await self._run_poke(pending, participant, outcome.value)
                else:
                    await self._run_meme(pending, participant)
        finally:
            self.discard(pending)

    async def _run_poke(
        self, pending: PendingWheel, participant: WheelParticipant, count: int
    ) -> None:
        try:
            for index in range(count):
                await pending.bot.call_api(
                    "send_poke",
                    group_id=int(pending.group_id),
                    user_id=int(participant.user_id),
                )
                if index + 1 < count and self.poke_delay:
                    await asyncio.sleep(self.poke_delay)
        except Exception:
            logger.exception("[mantou-affection] 转盘主动戳一戳失败")
            await pending.send(
                MessageSegment.at(participant.user_id)
                + MessageSegment.text(" 主动戳一戳未完整执行")
            )
            await self._refund(pending, participant)
            return
        fee = "，本轮参与费 -2" if participant.fee_paid else ""
        await pending.send(
            MessageSegment.at(participant.user_id)
            + MessageSegment.text(f" 馒头戳了你 {count} 次{fee}")
        )

    async def _run_meme(self, pending: PendingWheel, participant: WheelParticipant) -> None:
        try:
            if self.memes is None:
                raise MemeGenerationError("meme-generator 未安装")
            image = await self.memes.generate(
                bot=pending.bot,
                user_id=participant.user_id,
                nickname=participant.nickname,
            )
        except MemeAssetMissing as error:
            logger.warning(f"[mantou-affection] memes 素材尚未就绪: {error}")
            if self.resources is not None:
                self.resources.check_after_missing()
            await pending.send(
                MessageSegment.at(participant.user_id)
                + MessageSegment.text(" 表情素材尚未下载完成，本次不重抽")
            )
            await self._refund(pending, participant)
            return
        except MemeGenerationError as error:
            logger.warning(f"[mantou-affection] memes 结果生成失败: {error}")
            await pending.send(
                MessageSegment.at(participant.user_id)
                + MessageSegment.text(" 本次表情生成失败，不重抽")
            )
            await self._refund(pending, participant)
            return
        fee = "，本轮参与费 -2" if participant.fee_paid else ""
        await pending.send(
            MessageSegment.at(participant.user_id)
            + MessageSegment.text(f" 馒头为你做了一张随机表情{fee}：")
            + MessageSegment.image(image)
        )
