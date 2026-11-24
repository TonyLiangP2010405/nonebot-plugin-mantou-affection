from __future__ import annotations

import random
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Union

from nonebot import get_bot, logger
from nonebot.adapters.onebot.v11 import Message, MessageSegment

from .bet import BET_RIVALS, BetCoordinator
from .config import Config
from .fill_blank import FillBlankCoordinator
from .findchar import FindCharCoordinator
from .group_quiz import GroupQuizCoordinator
from .hsr_quiz import HsrQuizCoordinator
from .petit_planet_quiz import PetitPlanetQuizCoordinator
from .pk import PkCoordinator
from .service import AffectionService
from .teyvat_quiz import TeyvatQuizCoordinator
from .wheel import WheelCoordinator
from .whoami import WhoAmICoordinator
from .zzz_quiz import ZzzQuizCoordinator

if TYPE_CHECKING:
    from .ambient import EventCoordinator

EVENT_WHEEL_AMBIENT = "ambient"
EVENT_WHEEL_EVENT = "event"
EVENT_WHEEL_FINDCHAR = "findchar"
EVENT_WHEEL_BET = "bet"
EVENT_WHEEL_PK = "pk"
EVENT_WHEEL_GROUP_QUIZ = "group_quiz"
EVENT_WHEEL_WHEEL = "wheel"
EVENT_WHEEL_FILL_BLANK = "fill_blank"
EVENT_WHEEL_TEYVAT_QUIZ = "teyvat_quiz"
EVENT_WHEEL_HSR_QUIZ = "hsr_quiz"
EVENT_WHEEL_ZZZ_QUIZ = "zzz_quiz"
EVENT_WHEEL_PETIT_PLANET_QUIZ = "petit_planet_quiz"
EVENT_WHEEL_WHOAMI = "whoami"

EVENT_WHEEL_RESULT = "🎡 事件转盘转到了「{label}」！"
EVENT_WHEEL_FAILED = "「{label}」这次没开起来，馒头挠了挠头。"

Send = Callable[[Union[Message, str]], Awaitable[object]]


@dataclass(frozen=True)
class EventWheelSlot:
    """事件转盘的一格：存储用的键和给群友看的名字。"""

    key: str
    label: str


# 顺序与 /馒头强制事件 的 1~13 完全一致 方便对照
EVENT_WHEEL_SLOTS: tuple[EventWheelSlot, ...] = (
    EventWheelSlot(EVENT_WHEEL_AMBIENT, "馒头小动作"),
    EventWheelSlot(EVENT_WHEEL_EVENT, "随机事件"),
    EventWheelSlot(EVENT_WHEEL_FINDCHAR, "找字"),
    EventWheelSlot(EVENT_WHEEL_BET, "馒头博弈"),
    EventWheelSlot(EVENT_WHEEL_PK, "桃子问答"),
    EventWheelSlot(EVENT_WHEEL_GROUP_QUIZ, "群友问答"),
    EventWheelSlot(EVENT_WHEEL_WHEEL, "命运齿轮转盘"),
    EventWheelSlot(EVENT_WHEEL_FILL_BLANK, "补句问答"),
    EventWheelSlot(EVENT_WHEEL_TEYVAT_QUIZ, "提瓦特问答"),
    EventWheelSlot(EVENT_WHEEL_HSR_QUIZ, "星穹铁道问答"),
    EventWheelSlot(EVENT_WHEEL_ZZZ_QUIZ, "绝区零问答"),
    EventWheelSlot(EVENT_WHEEL_PETIT_PLANET_QUIZ, "星布谷地问答"),
    EventWheelSlot(EVENT_WHEEL_WHOAMI, "我是谁"),
)
EVENT_WHEEL_LABELS: dict[str, str] = {
    slot.key: slot.label for slot in EVENT_WHEEL_SLOTS
}


def _current_bot():
    """当前事件的 bot；拿不到时返回 None，转盘那一格会自己降级。"""

    try:
        return get_bot()
    except Exception:
        return None


class EventWheelCoordinator:
    """事件转盘：把群消息触发的 13 项活动收进一个等概率转盘。

    命中概率由 :meth:`AffectionService.event_wheel_triggered` 单独判定；转盘在
    当前能开的项目里等概率抽一格，先公告转到的名字，再直接开对应活动。某项条件
    不满足（好感门槛、题库为空、对手不够）时不会进候选池，所以转到的项目基本都能
    正常开局；真的开局失败时补一句提示，不吃群席位也不记账。
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
        group_quiz: GroupQuizCoordinator | None = None,
        teyvat_quiz: TeyvatQuizCoordinator | None = None,
        hsr_quiz: HsrQuizCoordinator | None = None,
        zzz_quiz: ZzzQuizCoordinator | None = None,
        petit_planet_quiz: PetitPlanetQuizCoordinator | None = None,
        fill_blank: FillBlankCoordinator | None = None,
        whoami: WhoAmICoordinator | None = None,
        rng: random.Random | None = None,
    ):
        self.service = service
        self.config = config
        self.events = events
        self.findchar = findchar
        self.bet = bet
        self.pk = pk
        self.wheel = wheel
        self.group_quiz = group_quiz
        self.teyvat_quiz = teyvat_quiz
        self.hsr_quiz = hsr_quiz
        self.zzz_quiz = zzz_quiz
        self.petit_planet_quiz = petit_planet_quiz
        self.fill_blank = fill_blank
        self.whoami = whoami
        self.rng = rng or random.Random()

    def labels(self) -> tuple[str, ...]:
        """按固定顺序返回 13 格的名字，供命令里展示。"""

        return tuple(slot.label for slot in EVENT_WHEEL_SLOTS)

    def busy(self, group_id: str) -> bool:
        """本群是否已有待结算的活动；有的话事件转盘直接安静，不公告也不开局。"""

        key = str(group_id)
        events_busy = getattr(self.events, "busy", None)
        if callable(events_busy) and events_busy(key):
            return True
        return any(
            key in getattr(peer, "pending", {})
            for peer in (
                self.findchar,
                self.bet,
                self.pk,
                self.wheel,
                self.group_quiz,
                self.teyvat_quiz,
                self.hsr_quiz,
                self.zzz_quiz,
                self.petit_planet_quiz,
                self.fill_blank,
                self.whoami,
            )
            if peer is not None
        )

    async def available_slots(self, group_id: str, user_id: str) -> tuple[str, ...]:
        """返回当前能开的格子；顺序与 :data:`EVENT_WHEEL_SLOTS` 一致。"""

        profile = await self.service.profile(group_id, user_id)
        affection = profile.affection
        slots: list[str] = []
        if self._ambient_ready(affection):
            slots.append(EVENT_WHEEL_AMBIENT)
        if affection > 0 and self._event_ready():
            slots.append(EVENT_WHEEL_EVENT)
        if affection > 0 and self._findchar_ready():
            slots.append(EVENT_WHEEL_FINDCHAR)
        if self._bet_ready(group_id, user_id, affection):
            slots.append(EVENT_WHEEL_BET)
        if self._questions_ready(self.pk):
            slots.append(EVENT_WHEEL_PK)
        if self._questions_ready(self.group_quiz):
            slots.append(EVENT_WHEEL_GROUP_QUIZ)
        if affection > 0 and self.wheel is not None:
            slots.append(EVENT_WHEEL_WHEEL)
        if self._questions_ready(self.fill_blank):
            slots.append(EVENT_WHEEL_FILL_BLANK)
        if self._questions_ready(self.teyvat_quiz):
            slots.append(EVENT_WHEEL_TEYVAT_QUIZ)
        if self._questions_ready(self.hsr_quiz):
            slots.append(EVENT_WHEEL_HSR_QUIZ)
        if self._questions_ready(self.zzz_quiz):
            slots.append(EVENT_WHEEL_ZZZ_QUIZ)
        if self._questions_ready(self.petit_planet_quiz):
            slots.append(EVENT_WHEEL_PETIT_PLANET_QUIZ)
        if self._whoami_ready():
            slots.append(EVENT_WHEEL_WHOAMI)
        return tuple(slots)

    async def start(
        self,
        *,
        group_id: str,
        user_id: str,
        nickname: str,
        send: Send,
    ) -> str | None:
        """转一次并直接开对应活动，返回真正开起来的格子键；一项都没开返回 None。"""

        if self.busy(group_id):
            return None
        slots = await self.available_slots(group_id, user_id)
        key = self.draw_slot(slots)
        if key is None:
            return None
        label = EVENT_WHEEL_LABELS[key]
        try:
            await send(EVENT_WHEEL_RESULT.format(label=label))
        except Exception:
            logger.exception("[mantou-affection] 发送事件转盘结果失败")
            return None
        try:
            started = await self._start_slot(
                key, group_id=group_id, user_id=user_id, nickname=nickname, send=send
            )
        except Exception:
            logger.exception("[mantou-affection] 事件转盘开局失败")
            started = False
        if started:
            return key
        try:
            await send(EVENT_WHEEL_FAILED.format(label=label))
        except Exception:
            logger.exception("[mantou-affection] 发送事件转盘失败提示失败")
        return None

    def draw_slot(self, slots: Sequence[str]) -> str | None:
        """在可用格子里等概率抽一格，没有候选返回 None。"""

        if not slots:
            return None
        return self.rng.choice(list(slots))

    async def _start_slot(
        self,
        key: str,
        *,
        group_id: str,
        user_id: str,
        nickname: str,
        send: Send,
    ) -> bool:
        """按格子键开对应活动，成功返回 True。"""

        if key == EVENT_WHEEL_AMBIENT:
            text = await self.service.ambient_reaction(group_id, user_id)
            if not text:
                return False
            await send(MessageSegment.at(user_id) + MessageSegment.text(f" {text}"))
            return True
        if key == EVENT_WHEEL_EVENT:
            return await self._start_event(group_id, user_id, nickname, send)
        if key == EVENT_WHEEL_FINDCHAR:
            return await self._start_findchar(group_id, user_id, nickname, send)
        if key == EVENT_WHEEL_BET:
            return await self._start_bet(group_id, user_id, nickname, send)
        if key == EVENT_WHEEL_PK:
            return await self.pk.start(
                group_id=group_id, user_id=user_id, nickname=nickname, send=send
            )
        if key == EVENT_WHEEL_GROUP_QUIZ:
            return await self._start_quiz(self.group_quiz, group_id, user_id, nickname, send)
        if key == EVENT_WHEEL_FILL_BLANK:
            return await self._start_quiz(self.fill_blank, group_id, user_id, nickname, send)
        if key == EVENT_WHEEL_WHEEL:
            return await self._start_wheel(group_id, user_id, nickname, send)
        if key == EVENT_WHEEL_TEYVAT_QUIZ:
            return await self._start_quiz(self.teyvat_quiz, group_id, user_id, nickname, send)
        if key == EVENT_WHEEL_HSR_QUIZ:
            return await self._start_quiz(self.hsr_quiz, group_id, user_id, nickname, send)
        if key == EVENT_WHEEL_ZZZ_QUIZ:
            return await self._start_quiz(self.zzz_quiz, group_id, user_id, nickname, send)
        if key == EVENT_WHEEL_PETIT_PLANET_QUIZ:
            return await self._start_quiz(
                self.petit_planet_quiz, group_id, user_id, nickname, send
            )
        if key == EVENT_WHEEL_WHOAMI:
            return await self._start_quiz(self.whoami, group_id, user_id, nickname, send)
        return False

    async def _start_event(
        self,
        group_id: str,
        user_id: str,
        nickname: str,
        send: Send,
    ) -> bool:
        game = self.events.start(group_id, user_id, nickname)
        if game is None:
            return False
        await send(
            MessageSegment.at(user_id)
            + MessageSegment.text(f" {self.events.event_message(game)}")
        )
        self.events.schedule(
            game,
            group_id=group_id,
            send=send,
            timeout=self.config.mantou_affection_event_timeout,
        )
        return True

    async def _start_findchar(
        self,
        group_id: str,
        user_id: str,
        nickname: str,
        send: Send,
    ) -> bool:
        game = self.findchar.start(group_id, user_id, nickname)
        if game is None:
            return False
        await send(
            MessageSegment.at(user_id)
            + MessageSegment.text(f" {self.findchar.message(game)}")
        )
        self.findchar.schedule(
            game,
            group_id=group_id,
            send=send,
            timeout=self.config.mantou_affection_find_char_timeout,
        )
        return True

    async def _start_bet(
        self,
        group_id: str,
        user_id: str,
        nickname: str,
        send: Send,
    ) -> bool:
        """转盘转到博弈时跳过概率判定，对手人数和题库等门槛照旧。"""

        candidates = self.bet.recent_candidates(
            group_id, minutes=self.config.mantou_affection_bet_recent_minutes
        )
        return await self.bet.maybe_start(
            group_id=group_id,
            user_id=user_id,
            nickname=nickname,
            candidates=candidates,
            send=send,
            force=True,
        )

    async def _start_quiz(
        self,
        coordinator: object | None,
        group_id: str,
        user_id: str,
        nickname: str,
        send: Send,
    ) -> bool:
        if coordinator is None:
            return False
        return await coordinator.start(  # type: ignore[attr-defined]
            group_id=group_id, user_id=user_id, nickname=nickname, send=send
        )

    async def _start_wheel(
        self,
        group_id: str,
        user_id: str,
        nickname: str,
        send: Send,
    ) -> bool:
        if self.wheel is None:
            return False
        return await self.wheel.start(
            group_id=group_id,
            user_id=user_id,
            nickname=nickname,
            bot=_current_bot(),
            send=send,
        )

    def _ambient_ready(self, affection: int) -> bool:
        return (
            self.config.mantou_affection_ambient_enabled
            and self.service.text_library is not None
            and affection > 0
        )

    def _event_ready(self) -> bool:
        library = self.events.library
        return library is not None and bool(library.events)

    def _findchar_ready(self) -> bool:
        library = self.findchar.library
        return library is not None and bool(library.puzzles)

    def _bet_ready(self, group_id: str, user_id: str, affection: int) -> bool:
        if self.bet is None or affection <= self.config.mantou_affection_bet_min_affection:
            return False
        library = self.bet.library
        if library is None or not library.rounds:
            return False
        rivals = [
            item
            for item in self.bet.recent_candidates(
                group_id, minutes=self.config.mantou_affection_bet_recent_minutes
            )
            if str(item[0]) != user_id
        ]
        return len(rivals) >= BET_RIVALS

    @staticmethod
    def _questions_ready(coordinator: object | None) -> bool:
        """问卷类活动（问答、补句、我是谁）题库非空才算能开。"""

        if coordinator is None:
            return False
        library = getattr(coordinator, "library", None)
        if library is None:
            return False
        questions = getattr(library, "questions", None)
        if questions is not None:
            return bool(questions)
        return len(library) > 0

    def _whoami_ready(self) -> bool:
        return self._questions_ready(self.whoami)
