import random
from types import SimpleNamespace

import pytest

from nonebot_plugin_mantou_affection.wheel_memes import (
    MemeAssetMissing,
    MemeGenerationError,
    MemeGeneratorAdapter,
)


class FakeAssetMissing:
    def __init__(self, path: str = "petpet/base.png"):
        self.path = path


class FakeImage:
    def __init__(self, name: str, data: bytes):
        self.name = name
        self.data = data


class FakeMeme:
    def __init__(
        self, key: str, minimum: int, maximum: int, min_texts: int = 0, max_texts: int = 0
    ):
        self.key = key
        self.info = SimpleNamespace(
            params=SimpleNamespace(
                min_images=minimum,
                max_images=maximum,
                min_texts=min_texts,
                max_texts=max_texts,
            )
        )
        self.result = b"generated"
        self.calls = []

    def generate(self, images, texts, options):
        self.calls.append((images, texts, options))
        return self.result


class FakeBackend:
    Image = FakeImage
    ImageAssetMissing = FakeAssetMissing

    def __init__(self, memes):
        self.memes = memes

    def get_memes(self):
        return self.memes


async def avatars(user_id: str) -> bytes:
    return f"avatar:{user_id}".encode()


def adapter_for(meme: FakeMeme) -> MemeGeneratorAdapter:
    return MemeGeneratorAdapter(
        backend=FakeBackend([meme]), rng=random.Random(0), avatar_fetcher=avatars
    )


async def test_text_meme_uses_group_nickname() -> None:
    meme = FakeMeme("text", 0, 0, min_texts=1, max_texts=1)
    result = await adapter_for(meme).generate(
        bot=SimpleNamespace(self_id=999), user_id="123", nickname="群昵称"
    )
    assert result == b"generated"
    images, texts, options = meme.calls[0]
    assert images == []
    assert texts == ["群昵称"]
    assert options == {}


async def test_single_meme_uses_user_avatar() -> None:
    meme = FakeMeme("single", 1, 1)
    await adapter_for(meme).generate(
        bot=SimpleNamespace(self_id=999), user_id="123", nickname="群昵称"
    )
    images, texts, _ = meme.calls[0]
    assert [(image.name, image.data) for image in images] == [("群昵称", b"avatar:123")]
    assert texts == []


async def test_double_meme_uses_bot_then_user_avatar() -> None:
    meme = FakeMeme("double", 2, 2)
    await adapter_for(meme).generate(
        bot=SimpleNamespace(self_id=999), user_id="123", nickname="群昵称"
    )
    images, _, _ = meme.calls[0]
    assert [(image.name, image.data) for image in images] == [
        ("馒头", b"avatar:999"),
        ("群昵称", b"avatar:123"),
    ]


async def test_incompatible_templates_are_not_candidates() -> None:
    backend = FakeBackend([
        FakeMeme("three", 3, 3),
        FakeMeme("two-texts", 0, 0, min_texts=2, max_texts=2),
    ])
    adapter = MemeGeneratorAdapter(backend=backend, avatar_fetcher=avatars)
    with pytest.raises(MemeGenerationError, match="没有兼容"):
        await adapter.generate(
            bot=SimpleNamespace(self_id=999), user_id="123", nickname="群昵称"
        )


async def test_asset_missing_has_distinct_exception() -> None:
    meme = FakeMeme("single", 1, 1)
    meme.result = FakeAssetMissing()
    with pytest.raises(MemeAssetMissing):
        await adapter_for(meme).generate(
            bot=SimpleNamespace(self_id=999), user_id="123", nickname="群昵称"
        )


async def test_avatar_failure_is_wrapped() -> None:
    async def broken(_user_id: str) -> bytes:
        raise OSError("offline")

    meme = FakeMeme("single", 1, 1)
    adapter = MemeGeneratorAdapter(
        backend=FakeBackend([meme]), avatar_fetcher=broken
    )
    with pytest.raises(MemeGenerationError, match="头像"):
        await adapter.generate(
            bot=SimpleNamespace(self_id=999), user_id="123", nickname="群昵称"
        )
