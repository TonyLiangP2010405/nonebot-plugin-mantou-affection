from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable

from nonebot import get_bot, logger, on_message
from nonebot.adapters import Event as AdapterEvent
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment
from nonebot.message import event_preprocessor
from nonebot.typing import T_State

from .bet import BUNDLED_ROUNDS_PATH, BetCoordinator, BetLibrary
from .commands import GROUP_ONLY
from .config import Config
from .events import Event, EventLibrary, EventOption
from .fill_blank import (
    BUNDLED_FILL_BLANK_PATH,
    FillBlankCoordinator,
    FillBlankLibrary,
)
from .findchar import BUNDLED_PUZZLES_PATH, FindCharCoordinator, FindCharLibrary
from .group_quiz import BUNDLED_GROUP_QUIZ_PATH, GroupQuizCoordinator
from .logic import NEWBIE_PROTECTION_NOTE
from .pk import BUNDLED_PK_PATH, PkCoordinator, PkQuestionLibrary
from .service import AffectionService
from .teyvat_quiz import BUNDLED_TEYVAT_QUIZ_PATH, TeyvatQuizCoordinator
from .wheel import WheelCoordinator

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
TIMEOUT_REPLY = "{bot}等不到你的回答，失望地走开了，好感度 -{penalty}"


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
        rng: random.Random | None = None,
        findchar: FindCharCoordinator | None = None,
        bet: BetCoordinator | None = None,
        pk: PkCoordinator | None = None,
        wheel: WheelCoordinator | None = None,
        group_quiz: GroupQuizCoordinator | None = None,
        teyvat_quiz: TeyvatQuizCoordinator | None = None,
        fill_blank: FillBlankCoordinator | None = None,
    ):
        self.service = service
        self.config = config
        self.library = library
        self.rng = rng or random.Random()
        self.findchar = findchar
        self.bet = bet
        self.pk = pk
        self.wheel = wheel
        self.group_quiz = group_quiz
        self.teyvat_quiz = teyvat_quiz
        self.fill_blank = fill_blank
        self.pending: dict[str, PendingEvent] = {}

    def attach_find_char(self, findchar: FindCharCoordinator) -> None:
        """接入找字小游戏，让两种活动共用同一个群的席位。"""

        self.findchar = findchar

    def attach_bet(self, bet: BetCoordinator) -> None:
        """接入馒头博弈，让三种活动共用同一个群的席位。"""

        self.bet = bet

    def attach_pk(self, pk: PkCoordinator) -> None:
        """接入随机问答，让四种活动共用同一个群的席位。"""

        self.pk = pk

    def attach_wheel(self, wheel: WheelCoordinator) -> None:
        self.wheel = wheel

    def attach_group_quiz(self, group_quiz: GroupQuizCoordinator) -> None:
        """接入群友问答，让它和随机事件、找字、博弈、问答、转盘共用同一个群的席位。"""

        self.group_quiz = group_quiz

    def attach_teyvat_quiz(self, teyvat_quiz: TeyvatQuizCoordinator) -> None:
        """接入提瓦特问答，让它和其余活动共用同一个群的席位。"""

        self.teyvat_quiz = teyvat_quiz

    def attach_fill_blank(self, fill_blank: FillBlankCoordinator) -> None:
        """接入补句问答，让它和其余活动共用同一个群的席位。"""

        self.fill_blank = fill_blank

    def busy(self, group_id: str) -> bool:
        """本群是否已经有待结算的活动：随机事件、找字、博弈、问答、群友问答、提瓦特问答、补句问答或转盘。"""

        key = str(group_id)
        if key in self.pending:
            return True
        return any(
            peer is not None and key in peer.pending
            for peer in (
                self.findchar,
                self.bet,
                self.pk,
                self.group_quiz,
                self.teyvat_quiz,
                self.fill_blank,
                self.wheel,
            )
        )

    def triggered(self) -> bool:
        """小动作命中后再掷一次骰子，决定是否升级为随机事件。"""

        return self.rng.random() < self.config.mantou_affection_ambient_event_ratio

    def start(
        self,
        group_id: str,
        user_id: str,
        nickname: str,
        penalty_scale: int = 1,
    ) -> PendingEvent | None:
        """抽一个事件并登记待作答状态；本群已有未结束事件或事件库为空时返回 None。"""

        key = str(group_id)
        if self.busy(key):
            return None
        event = self.library.pick() if self.library is not None else None
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
            " -"
            f"{self.config.mantou_affection_event_timeout_penalty * pending.penalty_scale}"
        )
        option_lines = "\n".join(
            f"{index}. {option.text}" for index, option in enumerate(options, start=1)
        )
        answer_hint = ANSWER_HINT_SEPARATOR.join(
            str(index) for index in range(1, len(options) + 1)
        )
        return EVENT_MESSAGE.format(
            prefix=EVENT_PREFIX,
            scene=pending.event.text,
            options=option_lines,
            answer_hint=answer_hint,
            timeout=self.config.mantou_affection_event_timeout,
            penalty=penalty_hint,
        )

    async def settle(
        self,
        pending: PendingEvent,
        *,
        group_id: str,
        timeout: float,
        send: Callable[[Message | str], Awaitable[object]] | None = None,
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
                await self._settle_answer(
                    group_id, pending, answer, option, send=send
                ),
            )

        if pending.trigger_id not in pending.answers:
            add_line(
                pending.trigger_id,
                await self._settle_timeout(group_id, pending, send=send),
            )

        return Message(segments)

    async def _settle_timeout(
        self,
        group_id: str,
        pending: PendingEvent,
        *,
        send: Callable[[Message | str], Awaitable[object]] | None = None,
    ) -> str:
        """结算触发者超时，返回这一行的完整文案。"""

        bot_name = self.config.mantou_affection_bot_name
        penalty = self.config.mantou_affection_event_timeout_penalty * pending.penalty_scale
        applied, profile, protected = await self.service.adjust(
            group_id, pending.trigger_id, pending.trigger_name, -penalty
        )
        if applied < 0 and send is not None:
            await self.service.send_gift(
                send, group_id, pending.trigger_id, pending.trigger_name
            )
        reply = TIMEOUT_REPLY.format(bot=bot_name, penalty=-applied)
        note = NEWBIE_PROTECTION_NOTE if protected else ""
        return f"{reply}，当前 {profile.affection}{note}"

    async def _settle_answer(
        self,
        group_id: str,
        pending: PendingEvent,
        answer: PendingAnswer,
        option: EventOption,
        *,
        send: Callable[[Message | str], Awaitable[object]] | None = None,
    ) -> str:
        """结算一位答题者，返回这一行的完整文案。"""

        delta = pending.scaled_delta(option.delta)
        applied, profile, protected = await self.service.adjust(
            group_id, answer.user_id, answer.nickname, delta
        )
        if applied < 0 and send is not None:
            await self.service.send_gift(
                send, group_id, answer.user_id, answer.nickname
            )
        note = NEWBIE_PROTECTION_NOTE if protected else ""
        return f"{self._option_reply(option, applied)}，当前 {profile.affection}{note}"

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
    ) -> str | None:
        """戳一戳时按概率发起随机事件，成功返回事件消息文本；否则返回 None。

        戳出来的事件按 penalty_scale=2 结算：负向选项与超时扣 2 倍，正向奖励不变。
        事件消息由调用方发送（作为 poke 返回的 text），send 只用于结算消息。
        """

        if send is None or self.rng.random() >= chance:
            return None
        pending = self.start(
            str(group_id), str(user_id), nickname, penalty_scale=2
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
                message = await self.settle(
                    pending, group_id=group_id, timeout=timeout, send=send
                )
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
    pk: PkCoordinator | None = None,
    wheel: WheelCoordinator | None = None,
    group_quiz: GroupQuizCoordinator | None = None,
    teyvat_quiz: TeyvatQuizCoordinator | None = None,
    fill_blank: FillBlankCoordinator | None = None,
) -> tuple:
    """群友发言时按概率触发找字、博弈、随机问答、群友问答、提瓦特问答、补句问答、转盘或馒头小动作。

    找字和博弈保留各自的好感度门槛；随机问答、群友问答、提瓦特问答和补句问答不看榜单或好感度，
    分别默认每次群消息有 1%、0.05%、0.05%、0.05% 概率给当前发言者出一道题。命运齿轮转盘只看
    好感大于 0 和它自己的独立概率，不参与小动作的抽签。几种活动互斥，不会在同一条消息上同时触发；
    所有数字答案都走这里的答案路由，「参与 / 运作 / 不运作」也由同一个 answer matcher 转给转盘。
    事件预处理器只给博弈记录最近发言表。
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
    pk_coordinator = pk or PkCoordinator(
        service,
        config,
        PkQuestionLibrary(BUNDLED_PK_PATH),
        events=coordinator,
        rng=coordinator.rng,
    )
    wheel_coordinator = wheel or WheelCoordinator(
        service, config, events=coordinator, rng=coordinator.rng
    )
    group_quiz_coordinator = group_quiz or GroupQuizCoordinator(
        service,
        config,
        PkQuestionLibrary(BUNDLED_GROUP_QUIZ_PATH),
        events=coordinator,
        rng=coordinator.rng,
    )
    teyvat_quiz_coordinator = teyvat_quiz or TeyvatQuizCoordinator(
        service,
        config,
        PkQuestionLibrary(BUNDLED_TEYVAT_QUIZ_PATH),
        events=coordinator,
        rng=coordinator.rng,
    )
    fill_blank_coordinator = fill_blank or FillBlankCoordinator(
        service,
        config,
        FillBlankLibrary(BUNDLED_FILL_BLANK_PATH),
        events=coordinator,
        rng=coordinator.rng,
    )
    coordinator.attach_find_char(find_char)
    coordinator.attach_bet(bet_coordinator)
    coordinator.attach_pk(pk_coordinator)
    coordinator.attach_wheel(wheel_coordinator)
    coordinator.attach_group_quiz(group_quiz_coordinator)
    coordinator.attach_teyvat_quiz(teyvat_quiz_coordinator)
    coordinator.attach_fill_blank(fill_blank_coordinator)
    ambient = on_message(rule=GROUP_ONLY, priority=90, block=False)
    answer = on_message(rule=GROUP_ONLY, priority=5, block=False)

    @event_preprocessor
    async def record_recent_speaker(event: AdapterEvent, state: T_State) -> None:
        """事件预处理：每条群消息（含被命令接管的）都记进博弈的最近发言表。"""

        if isinstance(event, GroupMessageEvent):
            nickname = _sender_name(event)
            bet_coordinator.note_message(
                str(event.group_id), str(event.user_id), nickname
            )

    @answer.handle()
    async def handle_answer(event: GroupMessageEvent) -> None:
        try:
            group_id = str(event.group_id)
            user_id = str(event.user_id)
            name = _sender_name(event)
            text = event.message.extract_plain_text()
            if await pk_coordinator.answer(group_id, user_id, name, text):
                return
            if await group_quiz_coordinator.answer(group_id, user_id, name, text):
                return
            if await teyvat_quiz_coordinator.answer(group_id, user_id, name, text):
                return
            if await fill_blank_coordinator.answer(group_id, user_id, name, text):
                return
            if await bet_coordinator.answer(group_id, user_id, name, text):
                return
            if find_char.answer(group_id, user_id, name, text):
                return
            if coordinator.answer(group_id, user_id, name, text):
                return
            # 转盘的表情选择会在转盘结束后继续等 1~9 的回复。放到最后。
            # 避免它把正在进行的事件/问答答案当成表情选择吃掉。
            if await wheel_coordinator.answer(group_id, user_id, name, text):
                return
        except Exception:
            logger.exception("[mantou-affection] 处理作答失败")

    @ambient.handle()
    async def handle_ambient(event: GroupMessageEvent) -> None:
        group_id = str(event.group_id)
        user_id = str(event.user_id)
        nickname = _sender_name(event)
        bet_coordinator.note_message(group_id, user_id, nickname)

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
            bet_ready = await service.bet_triggered(group_id, user_id)
        except Exception:
            logger.exception("[mantou-affection] 判定馒头博弈失败")
            bet_ready = False
        if bet_ready:
            try:
                started = await bet_coordinator.maybe_start(
                    group_id=group_id,
                    user_id=user_id,
                    nickname=nickname,
                    candidates=bet_coordinator.recent_candidates(
                        group_id,
                        minutes=config.mantou_affection_bet_recent_minutes,
                    ),
                    send=ambient.send,
                )
            except Exception:
                logger.exception("[mantou-affection] 抽取馒头博弈失败")
                started = False
            if started:
                return

        try:
            pk_ready = await service.pk_triggered()
        except Exception:
            logger.exception("[mantou-affection] 判定随机问答失败")
            pk_ready = False
        if pk_ready:
            try:
                started = await pk_coordinator.start(
                    group_id=group_id,
                    user_id=user_id,
                    nickname=nickname,
                    send=ambient.send,
                )
            except Exception:
                logger.exception("[mantou-affection] 发起随机问答失败")
                started = False
            if started:
                return

        try:
            group_quiz_ready = await service.group_quiz_triggered()
        except Exception:
            logger.exception("[mantou-affection] 判定群友问答失败")
            group_quiz_ready = False
        if group_quiz_ready:
            try:
                started = await group_quiz_coordinator.start(
                    group_id=group_id,
                    user_id=user_id,
                    nickname=nickname,
                    send=ambient.send,
                )
            except Exception:
                logger.exception("[mantou-affection] 发起群友问答失败")
                started = False
            if started:
                return

        try:
            teyvat_quiz_ready = await service.teyvat_quiz_triggered()
        except Exception:
            logger.exception("[mantou-affection] 判定提瓦特问答失败")
            teyvat_quiz_ready = False
        if teyvat_quiz_ready:
            try:
                started = await teyvat_quiz_coordinator.start(
                    group_id=group_id,
                    user_id=user_id,
                    nickname=nickname,
                    send=ambient.send,
                )
            except Exception:
                logger.exception("[mantou-affection] 发起提瓦特问答失败")
                started = False
            if started:
                return

        try:
            fill_blank_ready = await service.fill_blank_triggered()
        except Exception:
            logger.exception("[mantou-affection] 判定补句问答失败")
            fill_blank_ready = False
        if fill_blank_ready:
            try:
                started = await fill_blank_coordinator.start(
                    group_id=group_id,
                    user_id=user_id,
                    nickname=nickname,
                    send=ambient.send,
                )
            except Exception:
                logger.exception("[mantou-affection] 发起补句问答失败")
                started = False
            if started:
                return

        try:
            wheel_ready = await service.wheel_triggered(group_id, user_id)
        except Exception:
            logger.exception("[mantou-affection] 判定命运齿轮转盘失败")
            wheel_ready = False
        if wheel_ready:
            try:
                started = await wheel_coordinator.start(
                    group_id=group_id,
                    user_id=user_id,
                    nickname=nickname,
                    bot=get_bot(),
                    send=ambient.send,
                )
            except Exception:
                logger.exception("[mantou-affection] 发起命运齿轮转盘失败")
                started = False
            if started:
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
            pending = coordinator.start(group_id, user_id, nickname)
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
