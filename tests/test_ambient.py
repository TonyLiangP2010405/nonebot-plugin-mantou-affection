import json
import random
from pathlib import Path
from time import time

from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message
from nonebot.adapters.onebot.v11.event import Sender

from nonebot_plugin_mantou_affection.ambient import register_ambient
from nonebot_plugin_mantou_affection.config import Config
from nonebot_plugin_mantou_affection.copywriting import AffectionTextLibrary
from nonebot_plugin_mantou_affection.models import Profile
from nonebot_plugin_mantou_affection.service import AffectionService
from nonebot_plugin_mantou_affection.storage import AffectionStore


def _event(group_id: int = 90001, user_id: int = 90002) -> GroupMessageEvent:
    message = Message("晚上好")
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
        raw_message="晚上好",
        font=0,
        sender=Sender(user_id=user_id, nickname="桃友", role="member"),
        to_me=False,
        group_id=group_id,
    )


def _service(
    tmp_path: Path,
    text_library: AffectionTextLibrary | None,
    **config_kwargs,
) -> AffectionService:
    return AffectionService(
        AffectionStore(tmp_path / "affection.json"),
        Config(**config_kwargs),
        text_library=text_library,
        rng=random.Random(0),
    )


async def _give_affection(service: AffectionService, score: int) -> None:
    def bump(profile: Profile) -> None:
        profile.affection = score

    await service.store.update_profile("90001", "90002", "桃友", bump)


def _capture_send(matcher, monkeypatch) -> list[Message]:
    sent: list[Message] = []

    async def fake_send(message: Message) -> None:
        sent.append(message)

    monkeypatch.setattr(matcher, "send", fake_send)
    return sent


async def test_ambient_matcher_is_a_low_priority_listener(tmp_path: Path) -> None:
    matcher = register_ambient(_service(tmp_path, None), Config())
    assert matcher.priority == 90
    assert matcher.block is False
    assert [handler.call.__name__ for handler in matcher.handlers] == ["handle_ambient"]


async def test_ambient_handler_mentions_sender_with_band_copy(
    bundled_texts_path: Path, tmp_path: Path, monkeypatch
) -> None:
    ambient_neutral = json.loads(bundled_texts_path.read_text(encoding="utf-8"))[
        "mantou.ambient"
    ]["neutral"]
    service = _service(
        tmp_path,
        AffectionTextLibrary(bundled_texts_path),
        mantou_affection_ambient_probability=1.0,
    )
    await _give_affection(service, 10)

    matcher = register_ambient(service, Config(mantou_affection_ambient_probability=1.0))
    sent = _capture_send(matcher, monkeypatch)
    await matcher.handlers[0].call(_event())

    assert len(sent) == 1
    message = sent[0]
    assert [segment.type for segment in message] == ["at", "text"]
    assert message[0].data["qq"] == "90002"
    assert message.extract_plain_text().strip() in ambient_neutral


async def test_ambient_handler_stays_silent_when_disabled(
    bundled_texts_path: Path, tmp_path: Path, monkeypatch
) -> None:
    service = _service(
        tmp_path,
        AffectionTextLibrary(bundled_texts_path),
        mantou_affection_ambient_enabled=False,
        mantou_affection_ambient_probability=1.0,
    )
    await _give_affection(service, 10)

    matcher = register_ambient(
        service,
        Config(
            mantou_affection_ambient_enabled=False,
            mantou_affection_ambient_probability=1.0,
        ),
    )
    sent = _capture_send(matcher, monkeypatch)
    await matcher.handlers[0].call(_event())

    assert sent == []


async def test_ambient_handler_swallows_service_errors(
    bundled_texts_path: Path, tmp_path: Path, monkeypatch
) -> None:
    service = _service(
        tmp_path,
        AffectionTextLibrary(bundled_texts_path),
        mantou_affection_ambient_probability=1.0,
    )

    async def broken(group_id: str, user_id: str) -> str | None:
        raise RuntimeError("store is down")

    monkeypatch.setattr(service, "ambient_reaction", broken)

    matcher = register_ambient(service, Config(mantou_affection_ambient_probability=1.0))
    sent = _capture_send(matcher, monkeypatch)
    await matcher.handlers[0].call(_event())

    assert sent == []
