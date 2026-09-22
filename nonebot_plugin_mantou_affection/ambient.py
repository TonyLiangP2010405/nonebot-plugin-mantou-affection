from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable

from nonebot import logger, on_message
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment

from .commands import GROUP_ONLY
from .config import Config
from .events import Event, EventLibrary, EventOption
from .service import AffectionService

ANSWERS = {"1": 0, "2": 1, "3": 2}
EVENT_MESSAGE = (
    "⚡ 触发随机事件！\n"
    "{scene}\n"
    "1. {first}\n"
    "2. {second}\n"
    "3. {third}\n"
    "请在 {timeout} 秒内作答，直接发送 1、2、3 即可（答题不用@），超时好感度 -{penalty}！"
)
TIMEOUT_REPLY = "{bot}等不到你的回答，失望地走开了，好感度 -{penalty}"


class PendingEvent:
    """一次已经发出、正在等待作答的随机事件。"""

    def __init__(self, event: Event, options: tuple[EventOption, ...]):
        self.event = event
        self.options = options
        self.future: asyncio.Future[int] = asyncio.get_running_loop().create_future()
        self.task: asyncio.Task | None = None

    def resolve(self, index: int) -> bool:
        if self.future.done():
            return False
        self.future.set_result(index)
        return True


class EventCoordinator:
    """管理随机事件的触发、作答等待与结算。"""

    def __init__(
        self,
        service: AffectionService,
        config: Config,
        library: EventLibrary | None = None,
        *,
        rng: random.Random | None = None,
    ):
        self.service = service
        self.config = config
        self.library = library
        self.rng = rng or random.Random()
        self.pending: dict[tuple[str, str], PendingEvent] = {}

    def triggered(self) -> bool:
        """小动作命中后再掷一次骰子，决定是否升级为随机事件。"""

        return self.rng.random() < self.config.mantou_affection_ambient_event_ratio

    def start(self, group_id: str, user_id: str) -> PendingEvent | None:
        """抽一个事件并登记待作答状态；已有未结束事件或事件库为空时返回 None。"""

        key = (str(group_id), str(user_id))
        if key in self.pending or self.library is None:
            return None
        event = self.library.pick()
        if event is None:
            return None
        pending = PendingEvent(event, self.library.shuffled_options(event))
        self.pending[key] = pending
        return pending

    def discard(self, group_id: str, user_id: str) -> None:
        self.pending.pop((str(group_id), str(user_id)), None)

    def answer(self, group_id: str, user_id: str, text: str) -> bool:
        """收到答题消息时调用；只接受 1/2/3，其他内容忽略。"""

        pending = self.pending.get((str(group_id), str(user_id)))
        if pending is None:
            return False
        index = ANSWERS.get(text.strip())
        if index is None:
            return False
        return pending.resolve(index)

    def event_message(self, pending: PendingEvent) -> str:
        options = pending.options
        return EVENT_MESSAGE.format(
            scene=pending.event.text,
            first=options[0].text,
            second=options[1].text,
            third=options[2].text,
            timeout=self.config.mantou_affection_event_timeout,
            penalty=self.config.mantou_affection_event_timeout_penalty,
        )

    async def settle(
        self,
        pending: PendingEvent,
        *,
        group_id: str,
        user_id: str,
        nickname: str,
        timeout: float,
    ) -> str:
        """等待作答并结算好感度，返回结果文本；结算走不受每日上限约束的调整路径。"""

        try:
            try:
                choice = await asyncio.wait_for(pending.future, timeout)
            except asyncio.TimeoutError:
                choice = None
        finally:
            self.discard(group_id, user_id)

        if choice is None:
            penalty = self.config.mantou_affection_event_timeout_penalty
            delta = -penalty
            reply = TIMEOUT_REPLY.format(
                bot=self.config.mantou_affection_bot_name, penalty=penalty
            )
        else:
            option = pending.options[choice]
            delta = option.delta
            reply = self._option_reply(option)

        await self.service.adjust(str(group_id), str(user_id), nickname, delta)
        return reply

    def _option_reply(self, option: EventOption) -> str:
        if option.delta >= 2:
            verb = "开心地收下了"
        elif option.delta > 0:
            verb = "收下了"
        else:
            verb = "嫌弃地躲开了"
        return (
            f"{self.config.mantou_affection_bot_name}{verb}"
            f"「{option.text}」，好感度 {option.delta:+d}"
        )

    def schedule(
        self,
        pending: PendingEvent,
        *,
        group_id: str,
        user_id: str,
        nickname: str,
        send: Callable[[Message | str], Awaitable[object]],
        timeout: float,
    ) -> asyncio.Task:
        """后台等待作答并回复结果，异常只记日志。"""

        async def run() -> str | None:
            try:
                reply = await self.settle(
                    pending,
                    group_id=group_id,
                    user_id=user_id,
                    nickname=nickname,
                    timeout=timeout,
                )
            except Exception:
                logger.exception("[mantou-affection] 随机事件结算失败")
                return None
            try:
                await send(reply)
            except Exception:
                logger.exception("[mantou-affection] 发送随机事件结果失败")
                return None
            return reply

        pending.task = asyncio.create_task(run())
        return pending.task


def register_ambient(
    service: AffectionService,
    config: Config,
    events: EventCoordinator | None = None,
) -> tuple:
    """群友发言时按概率让馒头冒个小动作，偶尔升级为限时随机事件。"""

    coordinator = events or EventCoordinator(service, config)
    ambient = on_message(rule=GROUP_ONLY, priority=90, block=False)
    answer = on_message(rule=GROUP_ONLY, priority=5, block=False)

    @answer.handle()
    async def handle_answer(event: GroupMessageEvent) -> None:
        try:
            coordinator.answer(
                str(event.group_id),
                str(event.user_id),
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

        pending = coordinator.start(group_id, user_id) if coordinator.triggered() else None
        if pending is not None:
            try:
                await ambient.send(
                    MessageSegment.at(event.user_id)
                    + MessageSegment.text(f" {coordinator.event_message(pending)}")
                )
            except Exception:
                logger.exception("[mantou-affection] 发送随机事件失败")
                coordinator.discard(group_id, user_id)
            else:
                coordinator.schedule(
                    pending,
                    group_id=group_id,
                    user_id=user_id,
                    nickname=_sender_name(event),
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
