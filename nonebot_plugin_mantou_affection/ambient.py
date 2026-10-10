from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable

from nonebot import logger, on_message
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment

from .bet import BUNDLED_ROUNDS_PATH, BetCoordinator, BetLibrary
from .commands import GROUP_ONLY
from .config import Config
from .events import Event, EventLibrary, EventOption
from .findchar import BUNDLED_PUZZLES_PATH, FindCharCoordinator, FindCharLibrary
from .logic import (
    UPSET_EVENT_RATIOS,
    UPSET_TRAP_RATE,
    UPSET_WRONG_RATE,
    snapshot_for,
)
from .service import AffectionService

ANSWERS = {"1": 0, "2": 1, "3": 2, "4": 3, "5": 4, "6": 5}
EVENT_MESSAGE = (
    "{prefix}\n"
    "{scene}\n"
    "{options}\n"
    "大家都可以回答，直接发送 {answer_hint} 即可（答题不用@）；\n"
    "请在 {timeout} 秒内作答，被点名的群友超时未答好感度{penalty}！"
)
ANSWER_HINT_SEPARATOR = "、"
EVENT_PREFIX = "⚡ 触发随机事件！"
UPSET_PREFIX = "💢 馒头闹别扭了！"
TIMEOUT_REPLY = "{bot}等不到你的回答，失望地走开了，好感度 -{penalty}"
UPSET_BEST_REPLY = "{bot}一下子被哄好了，好感度 {delta:+d}，当前 {affection}！"
UPSET_GOOD_REPLY = "{bot}的脸色缓和了些，好感度 {delta:+d}，当前 {affection}"
UPSET_OK_REPLY = "{bot}勉强收下了这个台阶，好感度 {delta:+d}，当前 {affection}"
UPSET_MILD_REPLY = "{bot}别过脸去，好感度 {delta:+d}，当前 {affection}"
UPSET_WORST_REPLY = "{bot}听完更委屈了，好感度 {delta:+d}，当前 {affection}…"
UPSET_TIMEOUT_REPLY = "{bot}等不到你的回答，心凉了半截，好感度 {delta:+d}，当前 {affection}"
UPSET_BEST_DELTA = 10
UPSET_GOOD_DELTA = 5
UPSET_OK_DELTA = 2
UPSET_WRONG_DELTA = -5
UPSET_WORST_DELTA = -10
UPSET_TIMEOUT_HINT = "按比例大扣"
UPSET_REPLIES: dict[int, str] = {
    UPSET_BEST_DELTA: UPSET_BEST_REPLY,
    UPSET_GOOD_DELTA: UPSET_GOOD_REPLY,
    UPSET_OK_DELTA: UPSET_OK_REPLY,
    UPSET_WRONG_DELTA: UPSET_MILD_REPLY,
    UPSET_WORST_DELTA: UPSET_WORST_REPLY,
}


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
        findchar: FindCharCoordinator | None = None,
        bet: BetCoordinator | None = None,
    ):
        self.service = service
        self.config = config
        self.library = library
        self.upset_library = upset_library
        self.rng = rng or random.Random()
        self.findchar = findchar
        self.bet = bet
        self.pending: dict[str, PendingEvent] = {}

    def attach_find_char(self, findchar: FindCharCoordinator) -> None:
        """接入找字小游戏，让两种活动共用同一个群的席位。"""

        self.findchar = findchar

    def attach_bet(self, bet: BetCoordinator) -> None:
        """接入馒头博弈，让三种活动共用同一个群的席位。"""

        self.bet = bet

    def busy(self, group_id: str) -> bool:
        """本群是否已经有待结算的活动：随机事件、找字小游戏或馒头博弈。"""

        key = str(group_id)
        if key in self.pending:
            return True
        return any(
            peer is not None and key in peer.pending for peer in (self.findchar, self.bet)
        )

    def triggered(self) -> bool:
        """小动作命中后再掷一次骰子，决定是否升级为随机事件。"""

        return self.rng.random() < self.config.mantou_affection_ambient_event_ratio

    def timeout_for(self, pending: PendingEvent) -> int:
        """闹别扭题用更长的作答窗口，普通题仍用原来的窗口。"""

        if pending.event.upset:
            return self.config.mantou_affection_upset_event_timeout
        return self.config.mantou_affection_event_timeout

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
        if self.busy(key):
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
        if index is None or index >= len(pending.options):
            return False
        return pending.record(str(user_id), nickname, index)

    def event_message(self, pending: PendingEvent) -> str:
        options = pending.options
        penalty_hint = (
            UPSET_TIMEOUT_HINT
            if pending.event.upset
            else " -"
            f"{self.config.mantou_affection_event_timeout_penalty * pending.penalty_scale}"
        )
        option_lines = "\n".join(
            f"{index}. {option.text}" for index, option in enumerate(options, start=1)
        )
        answer_hint = ANSWER_HINT_SEPARATOR.join(
            str(index) for index in range(1, len(options) + 1)
        )
        return EVENT_MESSAGE.format(
            prefix=UPSET_PREFIX if pending.event.upset else EVENT_PREFIX,
            scene=pending.event.text,
            options=option_lines,
            answer_hint=answer_hint,
            timeout=self.timeout_for(pending),
            penalty=penalty_hint,
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
            current = (
                await self.service.profile(group_id, pending.trigger_id)
            ).affection
            penalty = self._upset_penalty(
                current, UPSET_TRAP_RATE, -UPSET_WORST_DELTA
            ) * pending.penalty_scale
            _, profile = await self.service.adjust(
                group_id, pending.trigger_id, pending.trigger_name, -penalty
            )
            return UPSET_TIMEOUT_REPLY.format(
                bot=bot_name, delta=-penalty, affection=profile.affection
            )

        penalty = self.config.mantou_affection_event_timeout_penalty * pending.penalty_scale
        _, profile = await self.service.adjust(
            group_id, pending.trigger_id, pending.trigger_name, -penalty
        )
        reply = TIMEOUT_REPLY.format(bot=bot_name, penalty=penalty)
        return f"{reply}，当前 {profile.affection}"

    @staticmethod
    def _upset_penalty(affection: int, rate: float, minimum: int) -> int:
        """按当前好感比例算扣分，好感低时保底固定值。"""

        return max(minimum, int(affection * rate))

    async def _upset_delta(
        self,
        group_id: str,
        pending: PendingEvent,
        answer: PendingAnswer,
        option: EventOption,
    ) -> int:
        """闹别扭题的负向选项按答题者当前好感换算成比例扣分（正解不变）。"""

        if option.delta >= 0:
            return option.delta
        rate, minimum = (
            (UPSET_WRONG_RATE, -UPSET_WRONG_DELTA)
            if option.delta == UPSET_WRONG_DELTA
            else (UPSET_TRAP_RATE, -UPSET_WORST_DELTA)
        )
        current = (await self.service.profile(group_id, answer.user_id)).affection
        return -self._upset_penalty(current, rate, minimum) * pending.penalty_scale

    async def _settle_answer(
        self,
        group_id: str,
        pending: PendingEvent,
        answer: PendingAnswer,
        option: EventOption,
    ) -> str:
        """结算一位答题者，返回这一行的完整文案。"""

        bot_name = self.config.mantou_affection_bot_name
        if pending.event.upset:
            delta = await self._upset_delta(group_id, pending, answer, option)
        else:
            delta = pending.scaled_delta(option.delta)
        _, profile = await self.service.adjust(
            group_id, answer.user_id, answer.nickname, delta
        )
        if pending.event.upset:
            template = UPSET_REPLIES.get(option.delta, UPSET_MILD_REPLY)
            return template.format(bot=bot_name, delta=delta, affection=profile.affection)
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
            timeout=self.timeout_for(pending),
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
    findchar: FindCharCoordinator | None = None,
    bet: BetCoordinator | None = None,
) -> tuple:
    """群友发言时按概率让馒头冒个小动作，偶尔升级为群里多人可答的随机事件。

    好感度 > 0 的群友发言时先按找字小游戏的概率掷一次：命中了就只发找字方阵，
    没命中才继续走原来的 0.1% 小动作流程，两者互斥，不会在同一条消息上同时触发。
    馒头博弈不参与群消息触发，只由对外 API 启动，但作答同样走这里的答案路由。
    """

    coordinator = events or EventCoordinator(service, config)
    find_char = findchar or FindCharCoordinator(
        service,
        config,
        FindCharLibrary(BUNDLED_PUZZLES_PATH),
        events=coordinator,
        rng=coordinator.rng,
    )
    bet_coordinator = bet or BetCoordinator(
        service,
        config,
        BetLibrary(BUNDLED_ROUNDS_PATH),
        events=coordinator,
        rng=coordinator.rng,
    )
    coordinator.attach_find_char(find_char)
    coordinator.attach_bet(bet_coordinator)
    ambient = on_message(rule=GROUP_ONLY, priority=90, block=False)
    answer = on_message(rule=GROUP_ONLY, priority=5, block=False)

    @answer.handle()
    async def handle_answer(event: GroupMessageEvent) -> None:
        try:
            group_id = str(event.group_id)
            user_id = str(event.user_id)
            name = _sender_name(event)
            text = event.message.extract_plain_text()
            if await bet_coordinator.answer(group_id, user_id, name, text):
                return
            if find_char.answer(group_id, user_id, name, text):
                return
            coordinator.answer(group_id, user_id, name, text)
        except Exception:
            logger.exception("[mantou-affection] 处理作答失败")

    @ambient.handle()
    async def handle_ambient(event: GroupMessageEvent) -> None:
        group_id = str(event.group_id)
        user_id = str(event.user_id)
        nickname = _sender_name(event)

        try:
            find_char_ready = await service.find_char_triggered(group_id, user_id)
        except Exception:
            logger.exception("[mantou-affection] 判定找字小游戏失败")
            find_char_ready = False
        if find_char_ready:
            pending = find_char.start(group_id, user_id, nickname)
            if pending is not None:
                try:
                    await ambient.send(
                        MessageSegment.at(event.user_id)
                        + MessageSegment.text(f" {find_char.message(pending)}")
                    )
                except Exception:
                    logger.exception("[mantou-affection] 发送找字小游戏失败")
                    find_char.discard(group_id)
                else:
                    find_char.schedule(
                        pending,
                        group_id=group_id,
                        send=ambient.send,
                        timeout=config.mantou_affection_find_char_timeout,
                    )
                    return

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
            pending = coordinator.start(group_id, user_id, nickname, affection=affection)
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
                    timeout=coordinator.timeout_for(pending),
                )
                return

        try:
            await ambient.send(MessageSegment.at(event.user_id) + MessageSegment.text(f" {text}"))
        except Exception:
            logger.exception("[mantou-affection] 发送馒头小动作失败")

    return ambient, answer


def _sender_name(event: GroupMessageEvent) -> str:
    return event.sender.card or event.sender.nickname or str(event.user_id)
