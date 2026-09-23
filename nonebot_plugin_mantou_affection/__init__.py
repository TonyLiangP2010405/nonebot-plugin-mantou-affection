from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

from nonebot import get_plugin_config, require
from nonebot.plugin import PluginMetadata

from .config import Config

__plugin_meta__ = PluginMetadata(
    name="馒头好感度",
    description="为群聊机器人馒头提供互动、关系阶段和跨插件共享好感状态",
    usage=(
        "/馒头互动｜/馒头好感｜/馒头好感榜｜/馒头好感帮助\n"
        "SUPERUSER：/馒头好感调整 @群友 +10｜/馒头反应概率 5%"
    ),
    type="application",
    homepage="https://github.com/TonyLiangP2010405/nonebot-plugin-mantou-affection",
    config=Config,
    supported_adapters={"~onebot.v11"},
)

require("nonebot_plugin_localstore")

from nonebot.adapters.onebot.v11 import Message
from nonebot_plugin_localstore import get_plugin_data_dir

from .ambient import EventCoordinator, register_ambient
from .commands import register_commands
from .copywriting import AffectionTextLibrary
from .events import EventLibrary
from .integration import register_plugin_linkage
from .models import AffectionResponse, AffectionSnapshot, PokeResult
from .service import AffectionService
from .storage import AffectionStore

plugin_config = get_plugin_config(Config)
data_file = Path(get_plugin_data_dir()) / "affection.json"
store = AffectionStore(data_file)
text_library = AffectionTextLibrary(
    Path(__file__).parent / "resources" / "affection_texts.json",
    plugin_config.mantou_affection_text_path,
)
event_library = EventLibrary(Path(__file__).parent / "resources" / "affection_events.json")
service = AffectionService(store, plugin_config, text_library=text_library)
event_coordinator = EventCoordinator(service, plugin_config, event_library)
matchers = register_commands(service, plugin_config)
ambient_matcher, answer_matcher = register_ambient(service, plugin_config, event_coordinator)
plugin_linkage = register_plugin_linkage(service, plugin_config)


async def add_affection(
    group_id: str | int,
    user_id: str | int,
    delta: int,
    *,
    nickname: str = "",
    source: str = "external",
) -> int:
    """供其他插件主动增加好感度；返回实际增加值。"""

    if delta <= 0:
        raise ValueError("联动增加值必须大于 0")
    result = await service.reward_external(
        str(group_id), str(user_id), nickname, source, delta
    )
    return result.delta


async def change_affection(
    group_id: str | int,
    user_id: str | int,
    delta: int,
    *,
    nickname: str = "",
    source: str = "external",
) -> int:
    """供其他插件按正负数改变好感度；返回实际变化值。"""

    if delta == 0:
        return 0
    result = await service.reward_external(
        str(group_id), str(user_id), nickname, source, delta
    )
    return result.delta


async def get_affection(group_id: str | int, user_id: str | int) -> int:
    """读取指定群友的当前好感度；不存在记录时返回 0。"""

    return (await service.profile(str(group_id), str(user_id))).affection


async def poke(
    group_id: str | int,
    user_id: str | int,
    *,
    nickname: str = "",
    send: Callable[[Message | str], Awaitable[object]] | None = None,
) -> PokeResult:
    """戳一戳馒头：好感 +1；有概率戳出一次随机事件，此时 text 为事件消息。

    send 是可选的异步发送函数，用于把事件消息与答题结算发到群里；不传则只会正常 +1。
    """

    result = await service.poke(str(group_id), str(user_id), nickname)
    event_text = await event_coordinator.start_poke_event(
        group_id=str(group_id),
        user_id=str(user_id),
        nickname=nickname,
        send=send,
        chance=plugin_config.mantou_affection_poke_event_chance,
    )
    if event_text is None:
        return result
    return PokeResult(delta=result.delta, text=event_text)


async def get_affection_snapshot(
    group_id: str | int,
    user_id: str | int,
) -> AffectionSnapshot:
    """读取好感度、等级、称号和语气分层，供其他插件软联动。"""

    return await service.snapshot(str(group_id), str(user_id))


async def get_affection_response(
    scene: str,
    group_id: str | int,
    user_id: str | int,
    *,
    seed: str = "",
) -> AffectionResponse:
    """按场景与当前好感阶段选择文案；seed 非空时结果稳定。"""

    snapshot = await service.snapshot(str(group_id), str(user_id))
    text = text_library.pick(scene, snapshot, seed=seed)
    return AffectionResponse(
        affection=snapshot.affection,
        level=snapshot.level,
        title=snapshot.title,
        band=snapshot.band,
        text=text,
    )
