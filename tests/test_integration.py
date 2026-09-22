import json
import random
from pathlib import Path
from time import time
from types import SimpleNamespace

from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message
from nonebot.adapters.onebot.v11.event import Sender
from nonebot.consts import FULLMATCH_KEY, PREFIX_KEY, REGEX_MATCHED

from nonebot_plugin_mantou_affection import get_affection, plugin_config, plugin_linkage, service
from nonebot_plugin_mantou_affection.integration import is_meaningful_trigger
from nonebot_plugin_mantou_affection.logic import INTERACTIONS, snapshot_for


def test_command_and_regex_matchers_are_meaningful() -> None:
    assert is_meaningful_trigger({PREFIX_KEY: {}})
    assert is_meaningful_trigger({REGEX_MATCHED: object()})
    assert is_meaningful_trigger({FULLMATCH_KEY: "桃系词典"})


def test_plain_message_listener_is_not_rewarded() -> None:
    assert not is_meaningful_trigger({})
    assert not is_meaningful_trigger({"unrelated": True})


def test_plugin_service_is_wired_to_text_library() -> None:
    assert service.text_library is not None


async def test_interact_reply_comes_from_bundled_library(
    bundled_texts_path: Path, monkeypatch
) -> None:
    texts = json.loads(bundled_texts_path.read_text(encoding="utf-8"))["mantou.interact"]
    seed = 16
    assert random.Random(seed).choice(INTERACTIONS)[1] > 0
    monkeypatch.setattr(service, "rng", random.Random(seed))
    for index in range(3):
        result = await service.interact("91001", f"9100{index}", "桃友")
        band = snapshot_for(result.affection).band
        assert result.accepted is True
        assert result.delta > 0
        assert result.text in {text.replace("{bot}", "馒头") for text in texts[band]}


def _lexicon_event(user_id: int = 90002, group_id: int = 90001) -> GroupMessageEvent:
    message = Message("桃系词典")
    return GroupMessageEvent(
        time=int(time()),
        self_id=10000,
        post_type="message",
        sub_type="normal",
        user_id=user_id,
        message_type="group",
        message_id=1,
        message=message,
        original_message=message,
        raw_message="桃系词典",
        font=0,
        sender=Sender(user_id=user_id, nickname="桃友", role="member"),
        to_me=False,
        group_id=group_id,
    )


def _matcher(sent: list | None = None) -> SimpleNamespace:
    async def send(message, **kwargs) -> None:
        if sent is not None:
            sent.append(str(message))

    return SimpleNamespace(
        plugin_name="nonebot_plugin_taozi",
        module_name="nonebot_plugin_taozi.commands.lexicon",
        state={FULLMATCH_KEY: "桃系词典"},
        send=send,
    )


async def test_taozi_matcher_awards_affection_and_obeys_cooldown() -> None:
    event = _lexicon_event()
    matcher = _matcher()

    await plugin_linkage(matcher, event)
    first = await service.profile("90001", "90002")
    await plugin_linkage(matcher, event)
    second = await service.profile("90001", "90002")

    assert first.affection == 2
    assert first.linked_points == 2
    assert second.affection == 2


async def test_linkage_notifies_affection_change(monkeypatch) -> None:
    sent: list[str] = []
    monkeypatch.setattr(plugin_config, "mantou_affection_link_notify", True)

    await plugin_linkage(_matcher(sent), _lexicon_event(user_id=90006, group_id=90005))

    assert sent == ["馒头好感度 +2，当前 2"]


async def test_linkage_notification_can_be_disabled(monkeypatch) -> None:
    sent: list[str] = []
    monkeypatch.setattr(plugin_config, "mantou_affection_link_notify", False)

    await plugin_linkage(_matcher(sent), _lexicon_event(user_id=90008, group_id=90007))

    assert sent == []
    assert await get_affection("90007", "90008") == 2


async def test_linkage_notification_failure_is_logged(monkeypatch) -> None:
    monkeypatch.setattr(plugin_config, "mantou_affection_link_notify", True)
    matcher = _matcher()
    matcher.send = None

    await plugin_linkage(matcher, _lexicon_event(user_id=90010, group_id=90009))

    assert await get_affection("90009", "90010") == 2
