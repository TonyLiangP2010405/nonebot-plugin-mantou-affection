from time import time
from types import SimpleNamespace

from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message
from nonebot.adapters.onebot.v11.event import Sender
from nonebot.consts import FULLMATCH_KEY, PREFIX_KEY, REGEX_MATCHED

from nonebot_plugin_mantou_affection import plugin_linkage, service
from nonebot_plugin_mantou_affection.integration import is_meaningful_trigger


def test_command_and_regex_matchers_are_meaningful() -> None:
    assert is_meaningful_trigger({PREFIX_KEY: {}})
    assert is_meaningful_trigger({REGEX_MATCHED: object()})
    assert is_meaningful_trigger({FULLMATCH_KEY: "桃系词典"})


def test_plain_message_listener_is_not_rewarded() -> None:
    assert not is_meaningful_trigger({})
    assert not is_meaningful_trigger({"unrelated": True})


async def test_taozi_matcher_awards_affection_and_obeys_cooldown() -> None:
    message = Message("桃系词典")
    event = GroupMessageEvent(
        time=int(time()),
        self_id=10000,
        post_type="message",
        sub_type="normal",
        user_id=90002,
        message_type="group",
        message_id=1,
        message=message,
        original_message=message,
        raw_message="桃系词典",
        font=0,
        sender=Sender(user_id=90002, nickname="桃友", role="member"),
        to_me=False,
        group_id=90001,
    )
    matcher = SimpleNamespace(
        plugin_name="nonebot_plugin_taozi",
        module_name="nonebot_plugin_taozi.commands.lexicon",
        state={FULLMATCH_KEY: "桃系词典"},
    )

    await plugin_linkage(matcher, event)
    first = await service.profile("90001", "90002")
    await plugin_linkage(matcher, event)
    second = await service.profile("90001", "90002")

    assert first.affection == 2
    assert first.linked_points == 2
    assert second.affection == 2
