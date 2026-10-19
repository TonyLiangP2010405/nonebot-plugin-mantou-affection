from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Union

from nonebot import get_bot, logger, on_command, on_message
from nonebot.adapters import Event
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.rule import Rule

from .ambient import EventCoordinator
from .bet import BET_RIVALS, BetCoordinator
from .commands import GROUP_ONLY
from .config import Config
from .findchar import FindCharCoordinator
from .pk import PkCoordinator
from .service import AffectionService, normalize_nickname
from .wheel import WheelCoordinator

FORCE_COMMAND = "馒头强制事件"
# 必须高于 ambient answer 的 priority=5 以避免选择数字被刚开启的活动当成答案。
FORCE_ANSWER_PRIORITY = 6
FORCE_CHOICE_TIMEOUT = 20.0

FORCE_ACTIVITY_AMBIENT = "ambient"
FORCE_ACTIVITY_EVENT = "event"
FORCE_ACTIVITY_FINDCHAR = "findchar"
FORCE_ACTIVITY_BET = "bet"
FORCE_ACTIVITY_PK = "pk"
FORCE_ACTIVITY_WHEEL = "wheel"

FORCE_CHOICES: dict[str, str] = {
    "1": FORCE_ACTIVITY_AMBIENT,
    "2": FORCE_ACTIVITY_EVENT,
    "3": FORCE_ACTIVITY_FINDCHAR,
    "4": FORCE_ACTIVITY_BET,
    "5": FORCE_ACTIVITY_PK,
    "6": FORCE_ACTIVITY_WHEEL,
}
FORCE_ACTIVITY_LINES: tuple[str, ...] = (
    "1. 馒头小动作",
    "2. 随机事件",
    "3. 找字",
    "4. 馒头博弈",
    "5. 桃子问答",
    "6. 命运齿轮转盘",
)
FORCE_PROMPT = (
    "🎛️ 馒头强制事件（目标：{target}）\n"
    + "\n".join(FORCE_ACTIVITY_LINES)
    + "\n请在 {timeout} 秒内直接回复数字选择（只有发起命令的 SUPERUSER 有效）。"
)
FORCE_USAGE = "用法：/馒头强制事件 @群友（只能 @ 一位群友）"
FORCE_BUSY_REPLY = "本群还有活动没结束，等它结束再开新一轮。"
FORCE_TIMEOUT_REPLY = "馒头强制事件超时取消，好感度没有变化。"
FORCE_INVALID_REPLY = "这不是有效的活动编号，馒头强制事件已取消，好感度没有变化。"
FORCE_RIVAL_REPLY = "最近发言的群友不足两位，凑不齐对手，馒头博弈开不了局。"
FORCE_FAIL_REPLY = "馒头没能把这场活动摆出来，已经取消，好感度没有变化。"

Send = Callable[[Union[Message, str]], Awaitable[object]]


class PendingForce:
    """一次已经发出、正在等待发起者回复编号的强制事件。"""

    def __init__(self, group_id: str, user_id: str, target_id: str, target_name: str):
        self.group_id = group_id
        self.user_id = user_id
        self.target_id = target_id
        self.target_name = target_name
        self.task: asyncio.Task | None = None


class ForceCoordinator:
    """管理强制事件的选择状态与六类活动分发，选择窗口默认 20 秒。

    强制触发绕过概率、好感门槛和功能开关，但保留群级互斥：本群已有待结算的活动时
    命令与选择都会被拒绝。强制事件自己的选择状态不占群席位，所以命令发出去的 20 秒
    内其它活动仍可能抢先开局，分发前会再查一次。
    """

    def __init__(
        self,
        service: AffectionService,
        config: Config,
        *,
        events: EventCoordinator,
        findchar: FindCharCoordinator,
        bet: BetCoordinator,
        pk: PkCoordinator,
        wheel: WheelCoordinator,
        choice_timeout: float = FORCE_CHOICE_TIMEOUT,
    ):
        self.service = service
        self.config = config
        self.events = events
        self.findchar = findchar
        self.bet = bet
        self.pk = pk
        self.wheel = wheel
        self.choice_timeout = float(choice_timeout)
        self.pending: dict[str, PendingForce] = {}

    def pending_for(self, group_id: str) -> PendingForce | None:
        return self.pending.get(str(group_id))

    def group_busy(self, group_id: str) -> bool:
        """本群是否已经有待结算的活动：强制事件必须让位给正在进行的活动。"""

        return self.events.busy(str(group_id))

    def start(
        self,
        *,
        group_id: str,
        user_id: str,
        target_id: str,
        target_name: str,
        send: Send,
    ) -> PendingForce:
        """登记一次等待选择的强制事件，并起一个到点取消的超时任务。"""

        key = str(group_id)
        self.discard(key)
        pending = PendingForce(key, str(user_id), str(target_id), target_name)
        self.pending[key] = pending
        pending.task = asyncio.create_task(self._expire(pending, send))
        return pending

    def discard(self, group_id: str) -> None:
        pending = self.pending.pop(str(group_id), None)
        if pending is not None and pending.task is not None:
            pending.task.cancel()

    async def _expire(self, pending: PendingForce, send: Send) -> None:
        """选择窗口到点还没收到编号就取消，不改变任何好感度。"""

        try:
            await asyncio.sleep(self.choice_timeout)
        except asyncio.CancelledError:
            return
        if self.pending.get(pending.group_id) is not pending:
            return
        self.pending.pop(pending.group_id, None)
        await self._send(send, FORCE_TIMEOUT_REPLY)

    async def choose(
        self, group_id: str, user_id: str, text: str, send: Send
    ) -> bool:
        """发起者回复时调用：有效编号就分发活动，无效内容直接取消。

        返回 True 表示这条消息已经被强制事件消费；其他人的回复一律忽略，不影响选择。
        """

        pending = self.pending_for(group_id)
        if pending is None or str(user_id) != pending.user_id:
            return False
        self.discard(pending.group_id)
        choice = FORCE_CHOICES.get(text.strip())
        if choice is None:
            await self._send(send, FORCE_INVALID_REPLY)
            return True
        await self.dispatch(pending, choice, send)
        return True

    async def dispatch(self, pending: PendingForce, choice: str, send: Send) -> bool:
        """分发一类活动，成功返回 True，失败时把原因发给群里。"""

        if self.group_busy(pending.group_id):
            await self._send(send, FORCE_BUSY_REPLY)
            return False
        try:
            failure = await self._start_activity(pending, choice, send)
        except Exception:
            logger.exception("[mantou-affection] 强制事件分发失败")
            failure = FORCE_FAIL_REPLY
        if failure is None:
            return True
        await self._send(send, failure)
        return False

    async def _start_activity(
        self, pending: PendingForce, choice: str, send: Send
    ) -> str | None:
        """按编号开一场活动，成功返回 None，失败返回要发的提示文案。"""

        if choice == FORCE_ACTIVITY_AMBIENT:
            return await self._send_ambient(pending, send)
        if choice == FORCE_ACTIVITY_EVENT:
            return await self._start_event(pending, send)
        if choice == FORCE_ACTIVITY_FINDCHAR:
            return await self._start_find_char(pending, send)
        if choice == FORCE_ACTIVITY_BET:
            return await self._start_bet(pending, send)
        if choice == FORCE_ACTIVITY_PK:
            return await self._start_pk(pending, send)
        if choice == FORCE_ACTIVITY_WHEEL:
            return await self._start_wheel(pending, send)
        return FORCE_FAIL_REPLY

    async def _send_ambient(self, pending: PendingForce, send: Send) -> str | None:
        """小动作只发文案：不开事件，也不占用群席位。"""

        text = await self.service.forced_ambient_reaction(
            pending.group_id, pending.target_id
        )
        if not text:
            return FORCE_FAIL_REPLY
        await send(MessageSegment.at(pending.target_id) + MessageSegment.text(f" {text}"))
        return None

    async def _start_event(self, pending: PendingForce, send: Send) -> str | None:
        affection = (
            await self.service.profile(pending.group_id, pending.target_id)
        ).affection
        game = self.events.start(
            pending.group_id, pending.target_id, pending.target_name, affection=affection
        )
        if game is None:
            return FORCE_FAIL_REPLY
        await send(
            MessageSegment.at(pending.target_id)
            + MessageSegment.text(f" {self.events.event_message(game)}")
        )
        self.events.schedule(
            game,
            group_id=pending.group_id,
            send=send,
            timeout=self.events.timeout_for(game),
        )
        return None

    async def _start_find_char(self, pending: PendingForce, send: Send) -> str | None:
        game = self.findchar.start(
            pending.group_id, pending.target_id, pending.target_name
        )
        if game is None:
            return FORCE_FAIL_REPLY
        await send(
            MessageSegment.at(pending.target_id)
            + MessageSegment.text(f" {self.findchar.message(game)}")
        )
        self.findchar.schedule(
            game,
            group_id=pending.group_id,
            send=send,
            timeout=self.config.mantou_affection_find_char_timeout,
        )
        return None

    async def _start_bet(self, pending: PendingForce, send: Send) -> str | None:
        """强制博弈仍要从最近发言的群友里抽另外两位对手，凑不齐就不开局。"""

        candidates = self.bet.recent_candidates(
            pending.group_id, minutes=self.config.mantou_affection_bet_recent_minutes
        )
        rivals = [item for item in candidates if str(item[0]) != pending.target_id]
        if len(rivals) < BET_RIVALS:
            return FORCE_RIVAL_REPLY
        started = await self.bet.maybe_start(
            group_id=pending.group_id,
            user_id=pending.target_id,
            nickname=pending.target_name,
            candidates=candidates,
            send=send,
            force=True,
        )
        return None if started else FORCE_FAIL_REPLY

    async def _start_pk(self, pending: PendingForce, send: Send) -> str | None:
        started = await self.pk.start(
            group_id=pending.group_id,
            user_id=pending.target_id,
            nickname=pending.target_name,
            send=send,
        )
        return None if started else FORCE_FAIL_REPLY

    async def _start_wheel(self, pending: PendingForce, send: Send) -> str | None:
        started = await self.wheel.start(
            group_id=pending.group_id,
            user_id=pending.target_id,
            nickname=pending.target_name,
            bot=_current_bot(),
            send=send,
        )
        return None if started else FORCE_FAIL_REPLY

    @staticmethod
    async def _send(send: Send, text: str) -> None:
        try:
            await send(text)
        except Exception:
            logger.exception("[mantou-affection] 发送强制事件提示失败")


def _current_bot():
    """当前事件的 bot；拿不到时返回 None，转盘的主动戳一戳会自己降级。"""

    try:
        return get_bot()
    except Exception:
        return None


def _single_target(args: Message) -> str | None:
    """命令参数里必须正好 @ 一位群友，否则返回 None（@全体成员不算）。"""

    targets = [str(segment.data.get("qq")) for segment in args if segment.type == "at"]
    if len(targets) != 1 or not targets[0].isdigit():
        return None
    return targets[0]


def register_force_commands(
    service: AffectionService,
    config: Config,
    events: EventCoordinator,
    findchar: FindCharCoordinator,
    bet: BetCoordinator,
    pk: PkCoordinator,
    wheel: WheelCoordinator,
    *,
    coordinator: ForceCoordinator | None = None,
) -> tuple:
    """/馒头强制事件：SUPERUSER 发活动列表，发起者 20 秒内回复编号强制开一场。

    只有发起命令的 SUPERUSER 回复有效，其他群友发数字不影响选择；超时或无效编号
    直接取消且不改变好感。返回 (命令 matcher, 选择 matcher)。
    """

    force = coordinator or ForceCoordinator(
        service,
        config,
        events=events,
        findchar=findchar,
        bet=bet,
        pk=pk,
        wheel=wheel,
    )
    force_cmd = on_command(
        FORCE_COMMAND,
        permission=SUPERUSER,
        rule=GROUP_ONLY,
        priority=10,
        block=True,
    )

    async def _waiting_choice(event: Event) -> bool:
        return (
            isinstance(event, GroupMessageEvent)
            and force.pending_for(str(event.group_id)) is not None
        )

    force_answer = on_message(
        rule=GROUP_ONLY & Rule(_waiting_choice),
        priority=FORCE_ANSWER_PRIORITY,
        block=False,
    )

    @force_cmd.handle()
    async def handle_force(event: GroupMessageEvent, args: Message = CommandArg()) -> None:
        target_id = _single_target(args)
        if target_id is None:
            await force_cmd.finish(FORCE_USAGE)
            return
        group_id = str(event.group_id)
        if force.group_busy(group_id):
            await force_cmd.finish(FORCE_BUSY_REPLY)
            return
        try:
            profile = await service.profile(group_id, target_id)
        except Exception:
            logger.exception("[mantou-affection] 读取强制事件目标失败")
            await force_cmd.finish(FORCE_FAIL_REPLY)
            return
        name = normalize_nickname(profile.nickname, target_id)
        force.start(
            group_id=group_id,
            user_id=str(event.user_id),
            target_id=target_id,
            target_name=name,
            send=force_answer.send,
        )
        await force_cmd.finish(
            FORCE_PROMPT.format(target=name, timeout=int(force.choice_timeout))
        )

    @force_answer.handle()
    async def handle_force_choice(event: GroupMessageEvent) -> None:
        try:
            await force.choose(
                str(event.group_id),
                str(event.user_id),
                event.message.extract_plain_text(),
                force_answer.send,
            )
        except Exception:
            logger.exception("[mantou-affection] 处理强制事件选择失败")

    return force_cmd, force_answer
