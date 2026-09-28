from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable

from nonebot import logger, on_message
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment

from .commands import GROUP_ONLY
from .config import Config
from .events import Event, EventLibrary, EventOption
from .logic import UPSET_EVENT_RATIOS, snapshot_for
from .models import Profile
from .service import AffectionService

ANSWERS = {"1": 0, "2": 1, "3": 2}
EVENT_MESSAGE = (
    "{prefix}\n"
    "{scene}\n"
    "1. {first}\n"
    "2. {second}\n"
    "3. {third}\n"
    "大家都可以回答，直接发送 1、2、3 即可（答题不用@），"
    "请在 {timeout} 秒内作答，超时好感度 -{penalty}！"
)
EVENT_PREFIX = "⚡ 触发随机事件！"
UPSET_PREFIX = "💢 馒头闹别扭了！"
TIMEOUT_REPLY = "{bot}等不到你的回答，失望地走开了，好感度 -{penalty}"
UPSET_DOUBLE_REPLY = "{bot}一下子被哄好了，好感度翻倍，当前 {affection}！"
UPSET_HALVE_REPLY = "{bot}听完更委屈了，好感度减半，当前 {affection}…"
UPSET_DELTA_REPLY = "{bot}别过脸去，好感度 {delta:+d}，当前 {affection}"
UPSET_TIMEOUT_REPLY = "{bot}等不到你的回答，心凉了半截，好感度减半，当前 {affection}"


class PendingAnswer:
    """一次作答：某人选了第几个选项。"""

    def __init__(self, user_id: str, nickname: str, index: int):
        self.user_id = user_id
        self.nickname = nickname
        self.index = index


class PendingEvent:
    """一次已经发出、正在等待群里作答的随机事件。"""

    def __init__(
        self,
        event: Event,
        options: tuple[EventOption, ...],
        trigger_id: str,
        trigger_name: str,
        penalty_scale: int = 1,
    ):
        self.event = event
        self.options = options
        self.trigger_id = trigger_id
        self.trigger_name = trigger_name
        self.penalty_scale = penalty_scale
        self.answers: dict[str, PendingAnswer] = {}
        self.task: asyncio.Task | None = None

    def record(self, user_id: str, nickname: str, index: int) -> bool:
        """记录作答，每人只算第一次。"""

        if user_id in self.answers:
            return False
        self.answers[user_id] = PendingAnswer(user_id, nickname, index)
        return True

    def scaled_delta(self, delta: int) -> int:
        """负向变化按倍率放大，正向保持不变。"""

        return delta * self.penalty_scale if delta < 0 else delta


class EventCoordinator:
    """管理随机事件的触发、作答收集与结算，同一个群同时只有一个事件。"""

    def __init__(
        self,
        service: AffectionService,
        config: Config,
        library: EventLibrary | None = None,
        *,
        upset_library: EventLibrary | None = None,
        rng: random.Random | None = None,
    ):
        self.service = service
        self.config = config
        self.library = library
        self.upset_library = upset_library
        self.rng = rng or random.Random()
        self.pending: dict[str, PendingEvent] = {}

    def triggered(self) -> bool:
        """小动作命中后再掷一次骰子，决定是否升级为随机事件。"""

        return self.rng.random() < self.config.mantou_affection_ambient_event_ratio

    def start(
        self,
        group_id: str,
        user_id: str,
        nickname: str,
        penalty_scale: int = 1,
        affection: int = 0,
    ) -> PendingEvent | None:
        """抽一个事件并登记待作答状态；本群已有未结束事件或事件库为空时返回 None。

        好感阶段越高越可能抽到闹别扭题（UPSET_EVENT_RATIOS），抽不到就退回普通题。
        """

        return self._start(
            self._roll_library(affection), group_id, user_id, nickname, penalty_scale
        )

    def _roll_library(self, affection: int) -> EventLibrary | None:
        ratio = UPSET_EVENT_RATIOS.get(snapshot_for(affection).band, 0.0)
        if self.upset_library is not None and self.rng.random() < ratio:
            return self.upset_library
        return self.library

    def _start(
        self,
        library: EventLibrary | None,
        group_id: str,
        user_id: str,
        nickname: str,
        penalty_scale: int,
    ) -> PendingEvent | None:
        key = str(group_id)
        if key in self.pending:
            return None
        event = library.pick() if library is not None else None
        if event is None and library is not self.library and self.library is not None:
            event = self.library.pick()
        if event is None:
            return None
        pending = PendingEvent(
            event,
            EventLibrary.shuffled_options(event),
            trigger_id=str(user_id),
            trigger_name=nickname,
            penalty_scale=penalty_scale,
        )
        self.pending[key] = pending
        return pending

    def discard(self, group_id: str) -> None:
        self.pending.pop(str(group_id), None)

    def answer(self, group_id: str, user_id: str, nickname: str, text: str) -> bool:
        """收到答题消息时调用；群里任何人只认第一次的 1/2/3，其他内容忽略。"""

        pending = self.pending.get(str(group_id))
        if pending is None:
            return False
        index = ANSWERS.get(text.strip())
        if index is None:
            return False
        return pending.record(str(user_id), nickname, index)

    def event_message(self, pending: PendingEvent) -> str:
        options = pending.options
        return EVENT_MESSAGE.format(
            prefix=UPSET_PREFIX if pending.event.upset else EVENT_PREFIX,
            scene=pending.event.text,
            first=options[0].text,
            second=options[1].text,
            third=options[2].text,
            timeout=self.config.mantou_affection_event_timeout,
            penalty=self.config.mantou_affection_event_timeout_penalty * pending.penalty_scale,
        )

    async def settle(
        self,
        pending: PendingEvent,
        *,
        group_id: str,
        timeout: float,
    ) -> Message:
        """等满作答窗口后统一结算，返回合并了好感变化的消息。"""

        try:
            await asyncio.sleep(timeout)
        finally:
            self.discard(group_id)

        segments: list[MessageSegment] = []

        def add_line(user_id: str, line: str) -> None:
            if segments:
                segments.append(MessageSegment.text("\n"))
            segments.append(MessageSegment.at(user_id))
            segments.append(MessageSegment.text(f" {line}"))

        for answer in pending.answers.values():
            option = pending.options[answer.index]
            add_line(
                answer.user_id,
                await self._settle_answer(group_id, pending, answer, option),
            )

        if pending.trigger_id not in pending.answers:
            add_line(pending.trigger_id, await self._settle_timeout(group_id, pending))

        return Message(segments)

    async def _settle_timeout(self, group_id: str, pending: PendingEvent) -> str:
        """结算触发者超时，返回这一行的完整文案。"""

        bot_name = self.config.mantou_affection_bot_name
        if pending.event.upset:
            profile = await self._scale_affection(
                group_id, pending.trigger_id, pending.trigger_name, 0.5
            )
            return UPSET_TIMEOUT_REPLY.format(bot=bot_name, affection=profile.affection)

        penalty = self.config.mantou_affection_event_timeout_penalty * pending.penalty_scale
        _, profile = await self.service.adjust(
            group_id, pending.trigger_id, pending.trigger_name, -penalty
        )
        reply = TIMEOUT_REPLY.format(bot=bot_name, penalty=penalty)
        return f"{reply}，当前 {profile.affection}"

    async def _scale_affection(
        self, group_id: str, user_id: str, nickname: str, scale: float
    ) -> Profile:
        """按倍率改写好感度（向下取整），返回结算后的档案。"""

        current = (await self.service.profile(group_id, user_id)).affection
        target = min(self.config.mantou_affection_max, int(current * scale))
        _, profile = await self.service.adjust(group_id, user_id, nickname, target - current)
        return profile

    async def _settle_answer(
        self,
        group_id: str,
        pending: PendingEvent,
        answer: PendingAnswer,
        option: EventOption,
    ) -> str:
        """结算一位答题者，返回这一行的完整文案。"""

        bot_name = self.config.mantou_affection_bot_name
        if option.scale is not None:
            scale = option.scale
            profile = await self._scale_affection(
                group_id, answer.user_id, answer.nickname, scale
            )
            template = UPSET_DOUBLE_REPLY if scale > 1 else UPSET_HALVE_REPLY
            return template.format(bot=bot_name, affection=profile.affection)

        delta = pending.scaled_delta(option.delta)
        _, profile = await self.service.adjust(
            group_id, answer.user_id, answer.nickname, delta
        )
        if pending.event.upset and option.delta < 0:
            return UPSET_DELTA_REPLY.format(
                bot=bot_name, delta=delta, affection=profile.affection
            )
        return f"{self._option_reply(option, delta)}，当前 {profile.affection}"

    def _option_reply(self, option: EventOption, delta: int) -> str:
        if option.delta >= 2:
            verb = "开心地收下了"
        elif option.delta > 0:
            verb = "收下了"
        else:
            verb = "嫌弃地躲开了"
        return (
            f"{self.config.mantou_affection_bot_name}{verb}"
            f"「{option.text}」，好感度 {delta:+d}"
        )

    async def start_poke_event(
        self,
        *,
        group_id: str,
        user_id: str,
        nickname: str,
        send: Callable[[Message | str], Awaitable[object]] | None,
        chance: float,
        affection: int = 0,
    ) -> str | None:
        """戳一戳时按概率发起随机事件，成功返回事件消息文本；否则返回 None。

        抽题和普通小动作一样看触发者好感阶段（UPSET_EVENT_RATIOS），戳出来的事件按
        penalty_scale=2 结算：普通题的负向选项与超时扣 2 倍，闹别扭题的 -3 选项也翻倍，
        但 scale 选项（×2 / ×0.5）与闹别扭题的超时减半都不受它影响。
        事件消息由调用方发送（作为 poke 返回的 text），send 只用于结算消息。
        """

        if send is None or self.rng.random() >= chance:
            return None
        pending = self.start(
            str(group_id), str(user_id), nickname, penalty_scale=2, affection=affection
        )
        if pending is None:
            return None
        message = self.event_message(pending)
        self.schedule(
            pending,
            group_id=str(group_id),
            send=send,
            timeout=self.config.mantou_affection_event_timeout,
        )
        return message

    def schedule(
        self,
        pending: PendingEvent,
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
                logger.exception("[mantou-affection] 随机事件结算失败")
                return None
            try:
                await send(message)
            except Exception:
                logger.exception("[mantou-affection] 发送随机事件结果失败")
                return None
            return message

        pending.task = asyncio.create_task(run())
        return pending.task


def register_ambient(
    service: AffectionService,
    config: Config,
    events: EventCoordinator | None = None,
) -> tuple:
    """群友发言时按概率让馒头冒个小动作，偶尔升级为群里多人可答的随机事件。"""

    coordinator = events or EventCoordinator(service, config)
    ambient = on_message(rule=GROUP_ONLY, priority=90, block=False)
    answer = on_message(rule=GROUP_ONLY, priority=5, block=False)

    @answer.handle()
    async def handle_answer(event: GroupMessageEvent) -> None:
        try:
            coordinator.answer(
                str(event.group_id),
                str(event.user_id),
                _sender_name(event),
                event.message.extract_plain_text(),
            )
        except Exception:
            logger.exception("[mantou-affection] 处理随机事件作答失败")

    @ambient.handle()
    async def handle_ambient(event: GroupMessageEvent) -> None:
        group_id = str(event.group_id)
        user_id = str(event.user_id)
        try:
            text = await service.ambient_reaction(group_id, user_id)
        except Exception:
            logger.exception("[mantou-affection] 抽取馒头小动作失败")
            return
        if not text:
            return

        pending = None
        if coordinator.triggered():
            try:
                affection = (await service.snapshot(group_id, user_id)).affection
            except Exception:
                logger.exception("[mantou-affection] 读取好感度失败")
                affection = 0
            pending = coordinator.start(
                group_id, user_id, _sender_name(event), affection=affection
            )
        if pending is not None:
            try:
                await ambient.send(
                    MessageSegment.at(event.user_id)
                    + MessageSegment.text(f" {coordinator.event_message(pending)}")
                )
            except Exception:
                logger.exception("[mantou-affection] 发送随机事件失败")
                coordinator.discard(group_id)
            else:
                coordinator.schedule(
                    pending,
                    group_id=group_id,
                    send=ambient.send,
                    timeout=config.mantou_affection_event_timeout,
                )
                return

        try:
            await ambient.send(MessageSegment.at(event.user_id) + MessageSegment.text(f" {text}"))
        except Exception:
            logger.exception("[mantou-affection] 发送馒头小动作失败")

    return ambient, answer


def _sender_name(event: GroupMessageEvent) -> str:
    return event.sender.card or event.sender.nickname or str(event.user_id)
