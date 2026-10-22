from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

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


WHEEL_MAX_PARTICIPANTS = 4
WHEEL_TOTAL_SEATS = WHEEL_MAX_PARTICIPANTS + 1


def format_wheel_rules(timeout: int) -> str:
    meme_weight = sum(weight for outcome, weight in WHEEL_OUTCOMES if outcome.kind == "meme")
    pokes = [(o.value, w) for o, w in WHEEL_OUTCOMES if o.kind == "poke"]
    gains = [(o.value, w) for o, w in WHEEL_OUTCOMES if o.kind == "affection" and o.value > 0]
    losses = [(-o.value, w) for o, w in WHEEL_OUTCOMES if o.kind == "affection" and o.value < 0]

    def join(items: list[tuple[int, int]], prefix: str) -> str:
        return "、".join(f"{prefix}{value} {weight}%" for value, weight in items)

    return (
        " 馒头命运齿轮转盘出现了！参与完全免费，"
        f"含触发者最多 {WHEEL_TOTAL_SEATS} 人。\n"
        f"触发者请在 {timeout} 秒内发送“运作”或“不运作”"
        "（不运作：1/3 概率好感 +2，2/3 概率好感 -2）；"
        "发送“运作”后还需在倒计时内回复“确认”才会开转。\n"
        "其他群友发送“参与”即可加入，触发者确认后立即截止，运作时每人独立抽取一次。\n"
        "转盘各项概率：\n"
        f"· 馒头制作随机表情 {meme_weight}%\n"
        f"· 馒头主动戳一戳：{join(pokes, '×')}\n"
        f"· 好感增加：{join(gains, '+')}\n"
        f"· 好感减少：{join(losses, '-')}"
    )


def format_wheel_confirm(remaining: int) -> str:
    return (
        " 二次确认：回复“确认”后立即开转，一旦确认其他群友就无法再参与了；"
        f"请赶在倒计时结束前（剩余约 {max(remaining, 0)} 秒）回复，"
        "超时本轮自动取消，不会运作。要确认吗？（回复 确认 / 不运作）"
    )


WHEEL_MEME_CONFIRM_REPLY = (
    " 你抽中了表情「{name}」（种类：{tags}，键名：{key}）。\n"
    "表情内容可能引起不适，请谨慎确认。\n"
    "请在 {timeout} 秒内回复「确认」或「不确认」。\n"
    "不确认或超时未回复，都按 50% 概率好感 +2、50% 概率好感 -2 处理。"
)
WHEEL_MEME_CONFIRMED_REPLY = " 已确认，馒头为你做了一张表情："
WHEEL_MEME_DECLINED_REPLY = " 已放弃制作表情，好感 {delta:+d}，当前 {affection}"
WHEEL_MEME_TIMEOUT_REPLY = " 表情确认超时，已放弃制作，好感 {delta:+d}，当前 {affection}"
WHEEL_MEME_ASSET_MISSING_REPLY = " 表情素材尚未下载完成，本次不重抽"
WHEEL_MEME_FAILED_REPLY = " 本次表情生成失败，不重抽"


def _meme_name(meme: Any) -> str:
    for keyword in getattr(meme, "keywords", None) or ():
        text = str(keyword).strip()
        if text:
            return text
    return str(getattr(meme, "key", "") or "").strip() or "未知表情"


def _meme_tags(meme: Any) -> str:
    tags = [str(tag).strip() for tag in (getattr(meme, "tags", None) or ())]
    tags = [tag for tag in tags if tag]
    return "、".join(tags) or "无分类"


def format_meme_confirm(meme: Any, timeout: int) -> str:
    return WHEEL_MEME_CONFIRM_REPLY.format(
        name=_meme_name(meme),
        tags=_meme_tags(meme),
        key=str(getattr(meme, "key", "") or "").strip() or "未知",
        timeout=timeout,
    )


@dataclass
class WheelParticipant:
    user_id: str
    nickname: str


class PendingWheel:
    def __init__(self, group_id: str, user_id: str, nickname: str, *, bot, send):
        self.group_id = group_id
        self.trigger = WheelParticipant(user_id, nickname)
        self.participants: list[WheelParticipant] = []
        self.bot = bot
        self.send = send
        self.accepting = True
        self.awaiting_confirm = False
        self.started_at = time.monotonic()
        self.task: asyncio.Task | None = None


class PendingMemeConfirm:
    def __init__(
        self,
        group_id: str,
        user_id: str,
        nickname: str,
        meme: Any,
        kind: str,
        *,
        bot,
        send,
    ):
        self.group_id = group_id
        self.user_id = user_id
        self.nickname = nickname
        self.meme = meme
        self.kind = kind
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
        self.meme_confirm_timeout = float(
            config.mantou_affection_wheel_meme_confirm_timeout
        )
        self.pending: dict[str, PendingWheel] = {}
        self.meme_confirms: dict[tuple[str, str], PendingMemeConfirm] = {}
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
                + MessageSegment.text(format_wheel_rules(int(self.choice_timeout)))
            )
        except Exception:
            self.discard(pending)
            raise
        async with self._lock:
            if self.pending.get(group) is pending and pending.accepting:
                pending.task = asyncio.create_task(self._expire(pending))
        return True

    async def answer(self, group_id: str, user_id: str, nickname: str, text: str) -> bool:
        keyword = text.strip()
        user = str(user_id)
        meme_confirm = self.meme_confirms.get((str(group_id), user))
        if (
            meme_confirm is not None
            and meme_confirm.accepting
            and keyword in {"确认", "不确认"}
        ):
            await self._answer_meme_confirm(meme_confirm, accepted=keyword == "确认")
            return True

        pending = self.pending.get(str(group_id))
        if pending is None or keyword not in {"参与", "运作", "不运作", "确认"}:
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
            if pending.awaiting_confirm:
                if keyword == "运作":
                    await pending.send("正在等待你的二次确认：回复“确认”开转，或“不运作”放弃。")
                    return True
                return self._settle(pending, operate=keyword == "确认")
            if keyword == "确认":
                await pending.send("请先发送“运作”或“不运作”。")
                return True
            if keyword == "不运作":
                return self._settle(pending, operate=False)
            pending.awaiting_confirm = True
            remaining = int(self.choice_timeout - (time.monotonic() - pending.started_at))
            await pending.send(
                MessageSegment.at(pending.trigger.user_id)
                + MessageSegment.text(format_wheel_confirm(remaining))
            )
            return True

    def _settle(self, pending: PendingWheel, *, operate: bool) -> bool:
        pending.accepting = False
        if pending.task is not None:
            pending.task.cancel()
        if operate:
            pending.task = asyncio.create_task(self._run_operation(pending))
        else:
            pending.task = asyncio.create_task(self._run_not_operate(pending))
        return True

    async def _join(self, pending: PendingWheel, user_id: str, nickname: str) -> bool:
        async with self._lock:
            if not pending.accepting or self.pending.get(pending.group_id) is not pending:
                return True
            if user_id == pending.trigger.user_id:
                await pending.send("触发者已自动加入本轮转盘，无需再发送参与。")
                return True
            if any(item.user_id == user_id for item in pending.participants):
                await pending.send("你已经参与本轮转盘了。")
                return True
            if len(pending.participants) >= WHEEL_MAX_PARTICIPANTS:
                await pending.send(f"本轮转盘名额已满（含触发者最多 {WHEEL_TOTAL_SEATS} 人）。")
                return True
            name = normalize_nickname(nickname, user_id)
            pending.participants.append(WheelParticipant(user_id, name))
            await pending.send(
                MessageSegment.at(user_id)
                + MessageSegment.text(" 参与成功，已加入本轮转盘，等待触发者选择是否运作。")
            )
        return True

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
            if pending.awaiting_confirm:
                await pending.send("馒头命运齿轮转盘超时未确认，本轮取消，不会运作。")
            else:
                await pending.send("馒头命运齿轮转盘超时取消。")
        finally:
            self.discard(pending)

    async def _run_not_operate(self, pending: PendingWheel) -> None:
        try:
            trigger = pending.trigger
            delta = draw_not_operate_delta(self.rng)
            try:
                applied, profile = await self.service.adjust(
                    pending.group_id, trigger.user_id, trigger.nickname, delta
                )
                await pending.send(
                    MessageSegment.at(trigger.user_id)
                    + MessageSegment.text(
                        f" 选择不运作，好感 {applied:+d}，当前 {profile.affection}"
                    )
                )
            except Exception:
                logger.exception(
                    f"[mantou-affection] 转盘不运作结算失败 group={pending.group_id} "
                    f"user={trigger.user_id}"
                )
        finally:
            self.discard(pending)

    async def _run_operation(self, pending: PendingWheel) -> None:
        try:
            for participant in [pending.trigger, *pending.participants]:
                try:
                    outcome = draw_wheel_outcome(self.rng)
                    if outcome.kind == "affection":
                        applied, profile = await self.service.adjust(
                            pending.group_id,
                            participant.user_id,
                            participant.nickname,
                            outcome.value,
                        )
                        await pending.send(
                            MessageSegment.at(participant.user_id)
                            + MessageSegment.text(
                                f" 转盘好感 {applied:+d}，当前 {profile.affection}"
                            )
                        )
                    elif outcome.kind == "poke":
                        await self._run_poke(pending, participant, outcome.value)
                    else:
                        await self._run_meme_outcome(pending, participant)
                except Exception:
                    logger.exception(
                        f"[mantou-affection] 转盘参与者结算失败 group={pending.group_id} "
                        f"user={participant.user_id}"
                    )
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
                + MessageSegment.text(" 馒头主动戳你失败（是馒头戳你，不用你回戳）")
            )
            return
        await pending.send(
            MessageSegment.at(participant.user_id)
            + MessageSegment.text(f" 馒头主动戳了你 {count} 次（是馒头戳你，不用你回戳）")
        )

    async def _run_meme_outcome(
        self, pending: PendingWheel, participant: WheelParticipant
    ) -> None:
        try:
            whitelist = await self.service.meme_confirm_whitelist()
        except Exception:
            logger.exception("[mantou-affection] 读取表情免确认白名单失败")
            whitelist = set()
        if participant.user_id in whitelist:
            await self._run_meme(pending, participant)
            return
        try:
            if self.memes is None:
                raise MemeGenerationError("meme-generator 未安装")
            meme, kind = self.memes.draw()
        except MemeGenerationError as error:
            await self._report_meme_error(participant.user_id, pending.send, error)
            return
        await self._request_meme_confirm(pending, participant, meme, kind)

    async def _request_meme_confirm(
        self,
        pending: PendingWheel,
        participant: WheelParticipant,
        meme: Any,
        kind: str,
    ) -> None:
        key = (pending.group_id, participant.user_id)
        old = self.meme_confirms.pop(key, None)
        if old is not None:
            old.accepting = False
            if old.task is not None:
                old.task.cancel()
        confirm = PendingMemeConfirm(
            pending.group_id,
            participant.user_id,
            participant.nickname,
            meme,
            kind,
            bot=pending.bot,
            send=pending.send,
        )
        self.meme_confirms[key] = confirm
        try:
            await pending.send(
                MessageSegment.at(participant.user_id)
                + MessageSegment.text(
                    format_meme_confirm(meme, int(self.meme_confirm_timeout))
                )
            )
        except Exception:
            confirm.accepting = False
            if self.meme_confirms.get(key) is confirm:
                self.meme_confirms.pop(key, None)
            raise
        if self.meme_confirms.get(key) is confirm and confirm.accepting:
            confirm.task = asyncio.create_task(self._expire_meme_confirm(confirm))

    async def _expire_meme_confirm(self, confirm: PendingMemeConfirm) -> None:
        try:
            await asyncio.sleep(self.meme_confirm_timeout)
        except asyncio.CancelledError:
            return
        await self._settle_meme_confirm(confirm, declined=False)

    async def _answer_meme_confirm(
        self, confirm: PendingMemeConfirm, *, accepted: bool
    ) -> None:
        if (
            not confirm.accepting
            or self.meme_confirms.get((confirm.group_id, confirm.user_id)) is not confirm
        ):
            return
        if not accepted:
            await self._settle_meme_confirm(confirm, declined=True)
            return

        self._cancel_meme_confirm(confirm)
        try:
            if self.memes is None:
                raise MemeGenerationError("meme-generator 未安装")
            image = await self.memes.generate_with(
                confirm.meme,
                confirm.kind,
                bot=confirm.bot,
                user_id=confirm.user_id,
                nickname=confirm.nickname,
            )
        except MemeGenerationError as error:
            await self._report_meme_error(confirm.user_id, confirm.send, error)
            return
        except Exception:
            logger.exception("[mantou-affection] 转盘表情确认后生成失败")
            await confirm.send(
                MessageSegment.at(confirm.user_id)
                + MessageSegment.text(WHEEL_MEME_FAILED_REPLY)
            )
            return
        await confirm.send(
            MessageSegment.at(confirm.user_id)
            + MessageSegment.text(WHEEL_MEME_CONFIRMED_REPLY)
            + MessageSegment.image(image)
        )

    async def _settle_meme_confirm(
        self, confirm: PendingMemeConfirm, *, declined: bool
    ) -> None:
        if (
            not confirm.accepting
            or self.meme_confirms.get((confirm.group_id, confirm.user_id)) is not confirm
        ):
            return
        self._cancel_meme_confirm(confirm)
        delta = 2 if self.rng.random() < 0.5 else -2
        template = (
            WHEEL_MEME_DECLINED_REPLY if declined else WHEEL_MEME_TIMEOUT_REPLY
        )
        try:
            applied, profile = await self.service.adjust(
                confirm.group_id, confirm.user_id, confirm.nickname, delta
            )
            await confirm.send(
                MessageSegment.at(confirm.user_id)
                + MessageSegment.text(
                    template.format(delta=applied, affection=profile.affection)
                )
            )
        except Exception:
            logger.exception(
                f"[mantou-affection] 转盘表情确认结算失败 group={confirm.group_id} "
                f"user={confirm.user_id}"
            )

    def _cancel_meme_confirm(self, confirm: PendingMemeConfirm) -> None:
        confirm.accepting = False
        key = (confirm.group_id, confirm.user_id)
        if self.meme_confirms.get(key) is confirm:
            self.meme_confirms.pop(key, None)
        task = confirm.task
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    async def _run_meme(self, pending: PendingWheel, participant: WheelParticipant) -> None:
        try:
            if self.memes is None:
                raise MemeGenerationError("meme-generator 未安装")
            image = await self.memes.generate(
                bot=pending.bot,
                user_id=participant.user_id,
                nickname=participant.nickname,
            )
        except MemeGenerationError as error:
            await self._report_meme_error(participant.user_id, pending.send, error)
            return
        await pending.send(
            MessageSegment.at(participant.user_id)
            + MessageSegment.text(" 馒头为你做了一张随机表情：")
            + MessageSegment.image(image)
        )

    async def _report_meme_error(self, user_id: str, send, error: MemeGenerationError) -> None:
        if isinstance(error, MemeAssetMissing):
            logger.warning(f"[mantou-affection] memes 素材尚未就绪: {error}")
            if self.resources is not None:
                self.resources.check_after_missing()
            await send(
                MessageSegment.at(user_id)
                + MessageSegment.text(WHEEL_MEME_ASSET_MISSING_REPLY)
            )
            return
        logger.warning(f"[mantou-affection] memes 结果生成失败: {error}")
        await send(
            MessageSegment.at(user_id) + MessageSegment.text(WHEEL_MEME_FAILED_REPLY)
        )
