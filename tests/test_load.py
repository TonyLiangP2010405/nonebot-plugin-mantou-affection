import nonebot


def test_wheel_is_wired_without_new_public_affection_api() -> None:
    import nonebot_plugin_mantou_affection as plugin

    assert plugin.event_coordinator.wheel is plugin.wheel_coordinator
    assert plugin.wheel_coordinator.memes is plugin.meme_generator_adapter
    assert plugin.wheel_coordinator.resources is plugin.meme_resources


async def test_meme_startup_hook_delegates_to_resource_manager(monkeypatch) -> None:
    import nonebot_plugin_mantou_affection as plugin

    calls = []
    monkeypatch.setattr(plugin, "get_plugin", lambda _name: None)
    monkeypatch.setattr(
        plugin.meme_resources,
        "check_on_startup",
        lambda **kwargs: calls.append(kwargs),
    )
    await plugin._check_meme_resources_on_startup()
    assert calls == [{"handled_by_memes_plugin": False}]


async def test_meme_startup_hook_coerces_string_false_config(monkeypatch) -> None:
    from types import SimpleNamespace

    import nonebot_plugin_mantou_affection as plugin

    calls = []
    monkeypatch.setattr(plugin, "get_plugin", lambda _name: object())
    monkeypatch.setattr(
        plugin,
        "driver",
        SimpleNamespace(config=SimpleNamespace(memes_check_resources_on_startup="false")),
    )
    monkeypatch.setattr(
        plugin.meme_resources,
        "check_on_startup",
        lambda **kwargs: calls.append(kwargs),
    )
    await plugin._check_meme_resources_on_startup()
    assert calls == [{"handled_by_memes_plugin": False}]


def test_plugin_load() -> None:
    plugin = nonebot.get_plugin_by_module_name("nonebot_plugin_mantou_affection")
    assert plugin is not None
    assert plugin.metadata is not None
    assert plugin.metadata.name == "馒头好感度"
    assert plugin.metadata.type == "application"
    assert plugin.metadata.supported_adapters == {"~onebot.v11"}


def test_public_api_is_exported() -> None:
    from nonebot_plugin_mantou_affection import (
        PokeResult,
        add_affection,
        change_affection,
        get_affection,
        get_affection_response,
        get_affection_snapshot,
        poke,
    )

    assert callable(add_affection)
    assert callable(change_affection)
    assert callable(get_affection)
    assert callable(get_affection_response)
    assert callable(get_affection_snapshot)
    assert callable(poke)
    assert PokeResult.__dataclass_fields__.keys() == {"delta", "text"}


async def test_public_read_api_returns_shared_snapshot() -> None:
    from nonebot_plugin_mantou_affection import (
        add_affection,
        get_affection,
        get_affection_response,
        get_affection_snapshot,
    )
    from nonebot_plugin_mantou_affection.config import Config

    await add_affection("read-api-group", "read-api-user", 10, source="test:read-api")
    daily_gain_limit = Config().mantou_affection_daily_gain_limit
    assert await get_affection("read-api-group", "read-api-user") == daily_gain_limit
    snapshot = await get_affection_snapshot("read-api-group", "read-api-user")
    assert snapshot.affection == daily_gain_limit
    assert snapshot.title == "初次见面"
    assert snapshot.band == "neutral"
    response = await get_affection_response(
        "crystelf.poke",
        "read-api-group",
        "read-api-user",
        seed="stable",
    )
    assert response.affection == daily_gain_limit
    assert response.text
