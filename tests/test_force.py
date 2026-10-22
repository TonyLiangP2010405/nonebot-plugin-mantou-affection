"""SUPERUSER 强制事件：命令校验、选择状态与七类活动分发。"""

import asyncio
import json
import random
from contextlib import suppress
from pathlib import Path
from time import time

import pytest
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment
from nonebot.adapters.onebot.v11.event import Sender
from nonebot.permission import SUPERUSER

from nonebot_plugin_mantou_affection.ambient import EventCoordinator
from nonebot_plugin_mantou_affection.bet import (
    BUNDLED_ROUNDS_PATH,
    BetCoordinator,
    BetLibrary,
)
from nonebot_plugin_mantou_affection.config import Config
from nonebot_plugin_mantou_affection.copywriting import AffectionTextLibrary
from nonebot_plugin_mantou_affection.events import EventLibrary
from nonebot_plugin_mantou_affection.findchar import (
    BUNDLED_PUZZLES_PATH,
    FindCharCoordinator,
    FindCharLibrary,
)
from nonebot_plugin_mantou_affection.force import (
    FORCE_ACTIVITY_LINES,
    FORCE_ANSWER_PRIORITY,
    FORCE_BUSY_REPLY,
    FORCE_CHOICE_TIMEOUT,
    FORCE_INVALID_REPLY,
    FORCE_RIVAL_REPLY,
    FORCE_TIMEOUT_REPLY,
    FORCE_USAGE,
    ForceCoordinator,
    register_force_commands,
)
from nonebot_plugin_mantou_affection.group_quiz import (
    BUNDLED_GROUP_QUIZ_PATH,
    GroupQuizCoordinator,
)
from nonebot_plugin_mantou_affection.pk import (
    BUNDLED_PK_PATH,
    PkCoordinator,
    PkQuestionLibrary,
)
from nonebot_plugin_mantou_affection.service import AffectionService
from nonebot_plugin_mantou_affection.storage import AffectionStore
from nonebot_plugin_mantou_affection.wheel import WheelCoordinator

GROUP_ID = "90001"
SUPER_ID = "10001"
TARGET_ID = "90002"
RIVAL_A = "90003"
RIVAL_B = "90004"
BYSTANDER = "90005"

EXPECTED_ACTIVITY_LINES = (
    "1. 馒头小动作",
    "2. 随机事件",
    "3. 找字",
    "4. 馒头博弈",
    "5. 桃子问答",
    "6. 群友问答",
    "7. 命运齿轮转盘",
)


class FixedRng(random.Random):
    """固定 random() 返回值，让概率判定在测试里完全可控。"""

    def __init__(self, value: float):
        super().__init__(0)
        self.value = value

    def random(self) -> float:
        return self.value


def _event(text: str, user_id: str = SUPER_ID, nickname: str = "管理员") -> GroupMessageEvent:
    message = Message(text)
    return GroupMessageEvent(
        time=int(time()),
        self_id=10000,
        post_type="message",
        sub_type="normal",
        user_id=int(user_id),
        message_type="group",
        message_id=1,
        message=message,
        original_message=message,
        raw_message=text,
        font=0,
        sender=Sender(user_id=int(user_id), nickname=nickname, role="owner"),
        to_me=False,
        group_id=int(GROUP_ID),
    )


def _at_message(*targets: str) -> Message:
    message = Message()
    for index, target in enumerate(targets):
        if index:
            message.append(MessageSegment.text(" "))
        message.append(MessageSegment.at(target))
    return message


def _body(message: Message) -> str:
    return "".join(segment.data["text"] for segment in message if segment.type == "text")


def _at_ids(message: Message) -> list[str]:
    return [str(segment.data["qq"]) for segment in message if segment.type == "at"]


def _events_path(bundled_texts_path: Path) -> Path:
    return bundled_texts_path.with_name("affection_events.json")


class Harness:
    """一套接好线的强制事件环境：协调器、命令 matcher 和捕获到的发送内容。"""

    def __init__(
        self,
        tmp_path: Path,
        bundled_texts_path: Path,
        monkeypatch,
        *,
        choice_timeout: float = FORCE_CHOICE_TIMEOUT,
        **config_kwargs,
    ):
        config_kwargs.setdefault("mantou_affection_ambient_enabled", False)
        config_kwargs.setdefault("mantou_affection_ambient_probability", 0.0)
        config_kwargs.setdefault("mantou_affection_find_char_chance", 0.0)
        config_kwargs.setdefault("mantou_affection_bet_chance", 0.0)
        config_kwargs.setdefault("mantou_affection_pk_chance", 0.0)
        config_kwargs.setdefault("mantou_affection_wheel_chance", 0.0)
        self.config = Config(**config_kwargs)
        self.service = AffectionService(
            AffectionStore(tmp_path / "affection.json"),
            self.config,
            text_library=AffectionTextLibrary(bundled_texts_path),
            rng=random.Random(0),
        )
        rng = FixedRng(0.99)
        self.events = EventCoordinator(
            self.service,
            self.config,
            EventLibrary(_events_path(bundled_texts_path)),
            rng=rng,
        )
        self.findchar = FindCharCoordinator(
            self.service,
            self.config,
            FindCharLibrary(BUNDLED_PUZZLES_PATH),
            events=self.events,
            rng=rng,
        )
        self.bet = BetCoordinator(
            self.service,
            self.config,
            BetLibrary(BUNDLED_ROUNDS_PATH),
            events=self.events,
            rng=rng,
        )
        self.pk = PkCoordinator(
            self.service,
            self.config,
            PkQuestionLibrary(BUNDLED_PK_PATH),
            events=self.events,
            rng=rng,
        )
        self.group_quiz = GroupQuizCoordinator(
            self.service,
            self.config,
            PkQuestionLibrary(BUNDLED_GROUP_QUIZ_PATH),
            events=self.events,
            rng=rng,
        )
        self.wheel = WheelCoordinator(self.service, self.config, events=self.events, rng=rng)
        self.events.attach_find_char(self.findchar)
        self.events.attach_bet(self.bet)
        self.events.attach_pk(self.pk)
        self.events.attach_group_quiz(self.group_quiz)
        self.events.attach_wheel(self.wheel)
        self.force = ForceCoordinator(
            self.service,
            self.config,
            events=self.events,
            findchar=self.findchar,
            bet=self.bet,
            pk=self.pk,
            wheel=self.wheel,
            group_quiz=self.group_quiz,
            choice_timeout=choice_timeout,
        )
        self.force_cmd, self.force_answer = register_force_commands(
            self.service,
            self.config,
            self.events,
            self.findchar,
            self.bet,
            self.pk,
            self.wheel,
            coordinator=self.force,
        )
        self.sent: list[Message] = []

        async def fake_send(message, **kwargs) -> None:
            self.sent.append(message if isinstance(message, Message) else Message(message))

        monkeypatch.setattr(self.force_cmd, "finish", fake_send)
        monkeypatch.setattr(self.force_answer, "send", fake_send)

    async def start_command(
        self, *, user_id: str = SUPER_ID, targets: tuple[str, ...] = (TARGET_ID,)
    ) -> None:
        await self.force_cmd.handlers[0].call(
            _event("/馒头强制事件", user_id), _at_message(*targets)
        )

    async def choose(self, text: str, *, user_id: str = SUPER_ID) -> None:
        await self.force_answer.handlers[0].call(_event(text, user_id))

    def note_speakers(self, *members: tuple[str, str]) -> None:
        for user_id, name in members:
            self.bet.note_message(GROUP_ID, user_id, name)

    async def cleanup(self) -> None:
        tasks = []
        for pending in (
            *self.events.pending.values(),
            *self.findchar.pending.values(),
            *self.bet.pending.values(),
            *self.pk.pending.values(),
            *self.group_quiz.pending.values(),
            *self.wheel.pending.values(),
            *self.force.pending.values(),
        ):
            task = getattr(pending, "task", None)
            if task is not None:
                tasks.append(task)
        for task in tasks:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


async def test_force_command_lists_activities_and_waits_for_choice(
    tmp_path: Path, bundled_texts_path: Path, monkeypatch
) -> None:
    harness = Harness(tmp_path, bundled_texts_path, monkeypatch)

    await harness.start_command()

    assert FORCE_ACTIVITY_LINES == EXPECTED_ACTIVITY_LINES
    assert FORCE_CHOICE_TIMEOUT == 20.0
    assert harness.force.choice_timeout == 20.0
    assert len(harness.sent) == 1
    body = _body(harness.sent[0])
    for line in EXPECTED_ACTIVITY_LINES:
        assert line in body
    assert "20 秒内" in body
    pending = harness.force.pending[GROUP_ID]
    assert pending.user_id == SUPER_ID
    assert pending.target_id == TARGET_ID
    await harness.cleanup()


async def test_force_command_requires_exactly_one_at(
    tmp_path: Path, bundled_texts_path: Path, monkeypatch
) -> None:
    harness = Harness(tmp_path, bundled_texts_path, monkeypatch)

    await harness.start_command(targets=())
    assert FORCE_USAGE in _body(harness.sent[-1])
    assert harness.force.pending == {}

    await harness.start_command(targets=(TARGET_ID, RIVAL_A))
    assert FORCE_USAGE in _body(harness.sent[-1])
    assert harness.force.pending == {}

    await harness.start_command(targets=("all",))
    assert FORCE_USAGE in _body(harness.sent[-1])
    assert harness.force.pending == {}


def test_force_matchers_are_registered(
    tmp_path: Path, bundled_texts_path: Path, monkeypatch
) -> None:
    harness = Harness(tmp_path, bundled_texts_path, monkeypatch)

    assert harness.force_cmd.priority == 10
    assert harness.force_cmd.block is True
    assert [handler.call.__name__ for handler in harness.force_cmd.handlers] == ["handle_force"]
    assert harness.force_answer.priority == FORCE_ANSWER_PRIORITY
    assert harness.force_answer.block is False

    from nonebot_plugin_mantou_affection.ambient import register_ambient

    _ambient, ambient_answer = register_ambient(
        harness.service,
        harness.config,
        harness.events,
        harness.findchar,
        harness.bet,
        harness.pk,
        harness.wheel,
    )
    assert harness.force_answer.priority > ambient_answer.priority

    assert [handler.call.__name__ for handler in harness.force_answer.handlers] == [
        "handle_force_choice"
    ]


async def test_force_command_permission_is_superuser_only(
    tmp_path: Path, bundled_texts_path: Path, monkeypatch
) -> None:
    harness = Harness(tmp_path, bundled_texts_path, monkeypatch)

    assert harness.force_cmd.permission.checkers == SUPERUSER.checkers


async def test_force_command_is_documented_in_help_and_metadata(
    tmp_path: Path, bundled_texts_path: Path, monkeypatch
) -> None:
    import nonebot

    from nonebot_plugin_mantou_affection.commands import register_commands

    harness = Harness(tmp_path, bundled_texts_path, monkeypatch)
    help_cmd = register_commands(harness.service, harness.config)[3]
    sent: list[str] = []

    async def fake_finish(message="", **kwargs) -> None:
        sent.append(str(message))

    monkeypatch.setattr(help_cmd, "finish", fake_finish)
    await help_cmd.handlers[0].call()

    assert "/馒头强制事件 @群友" in sent[-1]
    plugin = nonebot.get_plugin_by_module_name("nonebot_plugin_mantou_affection")
    assert plugin is not None and plugin.metadata is not None
    assert "/馒头强制事件 @群友" in plugin.metadata.usage


async def test_force_answer_rule_only_matches_pending_choice(
    tmp_path: Path, bundled_texts_path: Path, monkeypatch
) -> None:
    harness = Harness(tmp_path, bundled_texts_path, monkeypatch)
    event = _event("1")
    bot = object()

    assert await harness.force_answer.rule(bot, event, {}) is False
    await harness.start_command()
    assert await harness.force_answer.rule(bot, event, {}) is True
    assert await harness.force_answer.rule(bot, _event("1", user_id=RIVAL_A), {}) is True
    await harness.cleanup()


async def test_only_initiator_can_choose(
    tmp_path: Path, bundled_texts_path: Path, monkeypatch
) -> None:
    harness = Harness(tmp_path, bundled_texts_path, monkeypatch)
    await harness.start_command()
    harness.sent.clear()

    await harness.choose("1", user_id=BYSTANDER)
    assert harness.sent == []
    assert GROUP_ID in harness.force.pending
    assert harness.events.pending == {}

    await harness.choose("1")
    assert len(harness.sent) == 1
    assert harness.force.pending == {}
    await harness.cleanup()


async def test_force_choice_timeout_cancels_without_affection_change(
    tmp_path: Path, bundled_texts_path: Path, monkeypatch
) -> None:
    harness = Harness(tmp_path, bundled_texts_path, monkeypatch, choice_timeout=0.01)
    await harness.start_command()
    task = harness.force.pending[GROUP_ID].task
    assert task is not None

    await asyncio.wait_for(task, 2)

    assert harness.force.pending == {}
    assert FORCE_TIMEOUT_REPLY in _body(harness.sent[-1])
    assert len(harness.sent) == 2
    assert (await harness.service.profile(GROUP_ID, TARGET_ID)).affection == 0
    await harness.cleanup()


@pytest.mark.parametrize("text", ["8", "0", "1 2", "随便"])
async def test_invalid_choice_cancels_without_affection_change(
    tmp_path: Path, bundled_texts_path: Path, monkeypatch, text: str
) -> None:
    harness = Harness(tmp_path, bundled_texts_path, monkeypatch)
    await harness.start_command()
    harness.sent.clear()

    await harness.choose(text)

    assert len(harness.sent) == 1
    assert FORCE_INVALID_REPLY in _body(harness.sent[-1])
    assert harness.force.pending == {}
    assert harness.events.pending == {}
    assert (await harness.service.profile(GROUP_ID, TARGET_ID)).affection == 0

    await harness.choose("1")
    assert len(harness.sent) == 1
    await harness.cleanup()


@pytest.mark.parametrize(
    ("choice", "attribute", "marker"),
    [
        ("2", "events", "⚡ 触发随机事件！"),
        ("3", "findchar", "🔍 找字小游戏！"),
        ("4", "bet", "🎲 馒头博弈！"),
        ("5", "pk", "🍑 桃子问答！"),
        ("6", "group_quiz", "💬 群友问答！"),
        ("7", "wheel", "命运齿轮转盘"),
    ],
)
async def test_force_choice_starts_activity(
    tmp_path: Path,
    bundled_texts_path: Path,
    monkeypatch,
    choice: str,
    attribute: str,
    marker: str,
) -> None:
    harness = Harness(tmp_path, bundled_texts_path, monkeypatch)
    harness.note_speakers((RIVAL_A, "甲"), (RIVAL_B, "乙"))
    await harness.start_command()
    harness.sent.clear()

    await harness.choose(choice)

    assert harness.force.pending == {}
    assert GROUP_ID in getattr(harness, attribute).pending
    assert marker in _body(harness.sent[-1])
    assert _at_ids(harness.sent[-1])[0] == TARGET_ID
    await harness.cleanup()


async def test_force_bet_choice_names_target_and_two_rivals(
    tmp_path: Path, bundled_texts_path: Path, monkeypatch
) -> None:
    harness = Harness(tmp_path, bundled_texts_path, monkeypatch)
    harness.note_speakers((RIVAL_A, "甲"), (RIVAL_B, "乙"))
    await harness.start_command()
    harness.sent.clear()

    await harness.choose("4")

    pending = harness.bet.pending[GROUP_ID]
    assert pending.trigger_id == TARGET_ID
    assert set(pending.named_ids) == {TARGET_ID, RIVAL_A, RIVAL_B}
    await harness.cleanup()


async def test_forced_bet_needs_two_rivals(
    tmp_path: Path, bundled_texts_path: Path, monkeypatch
) -> None:
    harness = Harness(tmp_path, bundled_texts_path, monkeypatch)
    harness.note_speakers((TARGET_ID, "桃友"))
    await harness.start_command()
    harness.sent.clear()

    await harness.choose("4")

    assert FORCE_RIVAL_REPLY in _body(harness.sent[-1])
    assert harness.bet.pending == {}
    assert harness.force.pending == {}
    await harness.cleanup()


async def test_force_ambient_choice_bypasses_switch_probability_and_threshold(
    tmp_path: Path, bundled_texts_path: Path, monkeypatch
) -> None:
    harness = Harness(tmp_path, bundled_texts_path, monkeypatch)
    ambient_copy = json.loads(bundled_texts_path.read_text(encoding="utf-8"))[
        "mantou.ambient"
    ]["neutral"]
    assert await harness.service.ambient_reaction(GROUP_ID, TARGET_ID) is None

    await harness.start_command()
    harness.sent.clear()
    await harness.choose("1")

    assert len(harness.sent) == 1
    assert _at_ids(harness.sent[0]) == [TARGET_ID]
    assert _body(harness.sent[0]).strip() in ambient_copy
    assert harness.events.pending == {}
    assert (await harness.service.profile(GROUP_ID, TARGET_ID)).affection == 0
    await harness.cleanup()


async def test_forced_ambient_reaction_ignores_switch_probability_and_threshold(
    tmp_path: Path, bundled_texts_path: Path
) -> None:
    config = Config(
        mantou_affection_ambient_enabled=False,
        mantou_affection_ambient_probability=0.0,
    )
    service = AffectionService(
        AffectionStore(tmp_path / "affection.json"),
        config,
        text_library=AffectionTextLibrary(bundled_texts_path),
        rng=FixedRng(0.999),
    )
    ambient_copy = json.loads(bundled_texts_path.read_text(encoding="utf-8"))[
        "mantou.ambient"
    ]["neutral"]

    assert await service.ambient_reaction(GROUP_ID, TARGET_ID) is None
    assert await service.forced_ambient_reaction(GROUP_ID, TARGET_ID) in ambient_copy


async def test_forced_bet_skips_probability(
    tmp_path: Path, bundled_texts_path: Path, monkeypatch
) -> None:
    harness = Harness(tmp_path, bundled_texts_path, monkeypatch)
    harness.note_speakers((RIVAL_A, "甲"), (RIVAL_B, "乙"))
    candidates = harness.bet.recent_candidates(GROUP_ID, minutes=5)
    sent: list[Message] = []

    async def fake_send(message) -> None:
        sent.append(message)

    assert await harness.bet.maybe_start(
        group_id=GROUP_ID,
        user_id=TARGET_ID,
        nickname="桃友",
        candidates=candidates,
        send=fake_send,
    ) is False
    assert harness.bet.pending == {}

    assert await harness.bet.maybe_start(
        group_id=GROUP_ID,
        user_id=TARGET_ID,
        nickname="桃友",
        candidates=candidates,
        send=fake_send,
        force=True,
    ) is True
    assert GROUP_ID in harness.bet.pending
    assert _at_ids(sent[0])[0] == TARGET_ID
    await harness.cleanup()


async def test_force_command_blocked_when_group_is_busy(
    tmp_path: Path, bundled_texts_path: Path, monkeypatch
) -> None:
    harness = Harness(tmp_path, bundled_texts_path, monkeypatch)
    assert harness.findchar.start(GROUP_ID, RIVAL_A, "路人") is not None
    harness.sent.clear()

    await harness.start_command()

    assert len(harness.sent) == 1
    assert FORCE_BUSY_REPLY in _body(harness.sent[0])
    assert harness.force.pending == {}
    harness.findchar.discard(GROUP_ID)


async def test_force_choice_blocked_when_group_becomes_busy(
    tmp_path: Path, bundled_texts_path: Path, monkeypatch
) -> None:
    harness = Harness(tmp_path, bundled_texts_path, monkeypatch)
    await harness.start_command()
    assert harness.events.start(GROUP_ID, RIVAL_A, "路人") is not None
    harness.sent.clear()

    await harness.choose("1")

    assert len(harness.sent) == 1
    assert FORCE_BUSY_REPLY in _body(harness.sent[0])
    assert harness.force.pending == {}
    await harness.cleanup()
