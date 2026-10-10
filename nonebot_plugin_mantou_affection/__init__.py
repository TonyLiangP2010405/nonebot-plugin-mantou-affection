from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from pathlib import Path

from nonebot import get_plugin_config, logger, require
from nonebot.plugin import PluginMetadata

from .config import Config

__plugin_meta__ = PluginMetadata(
    name="馒头好感度",
    description="为群聊机器人馒头提供互动、关系阶段和跨插件共享好感状态",
    usage=(
        "/馒头互动｜/馒头好感｜/馒头好感榜｜/馒头好感帮助\n"
        "SUPERUSER：/馒头好感调整 @群友 +10｜/馒头好感重置 确认｜"
        "/馒头反应概率 5%｜/馒头找字概率 5%｜/馒头博弈概率 0.05%"
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
from .bet import BUNDLED_ROUNDS_PATH, BetCoordinator, BetLibrary
from .commands import register_commands
from .copywriting import AffectionTextLibrary
from .events import EventLibrary
from .findchar import BUNDLED_PUZZLES_PATH, FindCharCoordinator, FindCharLibrary
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
upset_event_library = EventLibrary(
    Path(__file__).parent / "resources" / "affection_events_upset.json"
)
service = AffectionService(store, plugin_config, text_library=text_library)
event_coordinator = EventCoordinator(
    service, plugin_config, event_library, upset_library=upset_event_library
)
find_char_library = FindCharLibrary(BUNDLED_PUZZLES_PATH)
find_char_coordinator = FindCharCoordinator(
    service, plugin_config, find_char_library, events=event_coordinator
)
event_coordinator.attach_find_char(find_char_coordinator)
bet_library = BetLibrary(BUNDLED_ROUNDS_PATH)
bet_coordinator = BetCoordinator(
    service, plugin_config, bet_library, events=event_coordinator
)
event_coordinator.attach_bet(bet_coordinator)
matchers = register_commands(service, plugin_config)
ambient_matcher, answer_matcher = register_ambient(
    service, plugin_config, event_coordinator, find_char_coordinator, bet_coordinator
)
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


async def maybe_start_bet(
    group_id: str | int,
    user_id: str | int,
    *,
    nickname: str = "",
    candidates: Iterable[tuple[str | int, str]] | None = None,
    send: Callable[[Message | str], Awaitable[object]] | None = None,
) -> bool:
    """水群榜等插件发完卡片后调用：按概率开一局「馒头博弈」，开局成功返回 True。

    candidates 是榜单成员 [(user_id, name), ...]，会排除触发者后随机抽两位当对手；
    不足两人、本群已有其它活动、概率没掷中或没传 send 都会直接返回 False。
    send 用来发开场消息与结算消息，通常直接传调用方的 matcher.send。
    """

    if send is None or candidates is None:
        return False
    return await bet_coordinator.maybe_start(
        group_id=str(group_id),
        user_id=str(user_id),
        nickname=nickname,
        candidates=candidates,
        send=send,
    )


async def poke(
    group_id: str | int,
    user_id: str | int,
    *,
    nickname: str = "",
    send: Callable[[Message | str], Awaitable[object]] | None = None,
) -> PokeResult:
    """戳一戳馒头：好感 +1；有概率戳出一次随机事件或一局找字小游戏，此时 text 为事件消息。

    send 是可选的异步发送函数，用于把事件消息与答题结算发到群里；不传则只会正常 +1。
    """

    result = await service.poke(str(group_id), str(user_id), nickname)
    if send is None:
        return result

    try:
        affection = (await service.snapshot(str(group_id), str(user_id))).affection
    except Exception:
        logger.exception("[mantou-affection] 读取好感度失败")
        affection = 0
    event_text = await event_coordinator.start_poke_event(
        group_id=str(group_id),
        user_id=str(user_id),
        nickname=nickname,
        send=send,
        chance=plugin_config.mantou_affection_poke_event_chance,
        affection=affection,
    )
    if event_text is None:
        event_text = await find_char_coordinator.start_poke_find_char(
            group_id=str(group_id),
            user_id=str(user_id),
            nickname=nickname,
            send=send,
            chance=await service.find_char_poke_chance(
                plugin_config.mantou_affection_poke_find_char_chance
            ),
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
