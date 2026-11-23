from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from pathlib import Path

from nonebot import get_driver, get_plugin, get_plugin_config, require
from nonebot.plugin import PluginMetadata

from .config import Config

__plugin_meta__ = PluginMetadata(
    name="馒头好感度",
    description="为群聊机器人馒头提供互动、关系阶段和跨插件共享好感状态",
    usage=(
        "/馒头帮助｜/馒头互动｜/馒头好感｜/馒头好感榜｜"
        "/馒头图像处理（支持 GIF 变速、动图保帧）\n"
        "SUPERUSER：/馒头好感调整 @群友 +10｜/馒头好感重置 确认｜/馒头反应概率 5%｜"
        "/馒头找字概率 5%｜/馒头博弈概率 0.05%｜/馒头问答概率 1%｜/馒头问答题库｜"
        "/馒头群友问答概率 0.05%｜/馒头提瓦特概率 0.05%｜/馒头星铁概率 0.05%｜"
        "/馒头绝区零概率 0.05%｜/馒头星布谷地概率 0.05%｜/馒头补句概率 0.05%｜"
        "/馒头转盘概率 0.1%｜/馒头概率总调 0.05%（或 ×2 / 重置）｜"
        "/馒头限制开关 开/关｜/馒头限制次数 5｜/馒头管理帮助｜"
        "/馒头强制事件 @群友（回复 1~12 强制开一场活动）"
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
from .fill_blank import (
    BUNDLED_FILL_BLANK_PATH,
    FillBlankCoordinator,
    FillBlankLibrary,
)
from .findchar import BUNDLED_PUZZLES_PATH, FindCharCoordinator, FindCharLibrary
from .force import register_force_commands
from .group_quiz import BUNDLED_GROUP_QUIZ_PATH, GroupQuizCoordinator
from .hsr_quiz import BUNDLED_HSR_QUIZ_PATH, HsrQuizCoordinator
from .image_processing import ImageProcessingCoordinator, ImageQuotaStore
from .integration import register_plugin_linkage
from .models import AffectionResponse, AffectionSnapshot, PokeResult
from .panel_quota import PanelQuotaStore, PanelRankQuotaStore
from .petit_planet_quiz import (
    BUNDLED_PETIT_PLANET_QUIZ_PATH,
    PetitPlanetQuizCoordinator,
)
from .pk import BUNDLED_PK_PATH, PkCoordinator, PkQuestionLibrary
from .service import AffectionService
from .storage import AffectionStore
from .teyvat_quiz import BUNDLED_TEYVAT_QUIZ_PATH, TeyvatQuizCoordinator
from .wheel import WheelCoordinator
from .wheel_memes import (
    MemeGeneratorAdapter,
    MemeRepoManager,
    MemeResourceManager,
    parse_blocked_terms,
)
from .wheel_spins import WheelSpinQuotaStore, register_spin_commands
from .wheel_stickers import TaoziStickerLibrary
from .wheel_wallpapers import WallpaperLibrary
from .zzz_quiz import BUNDLED_ZZZ_QUIZ_PATH, ZzzQuizCoordinator

plugin_config = get_plugin_config(Config)
data_file = Path(get_plugin_data_dir()) / "affection.json"
store = AffectionStore(data_file)
image_quota = ImageQuotaStore(Path(get_plugin_data_dir()) / "image_quota.json")
panel_quota = PanelQuotaStore(Path(get_plugin_data_dir()) / "panel_quota.json")
panel_rank_quota = PanelRankQuotaStore(
    Path(get_plugin_data_dir()) / "panel_rank_quota.json"
)
spin_quota = WheelSpinQuotaStore(Path(get_plugin_data_dir()) / "wheel_spins.json")
text_library = AffectionTextLibrary(
    Path(__file__).parent / "resources" / "affection_texts.json",
    plugin_config.mantou_affection_text_path,
)
event_library = EventLibrary(Path(__file__).parent / "resources" / "affection_events.json")
service = AffectionService(store, plugin_config, text_library=text_library)
image_processing = ImageProcessingCoordinator(image_quota, plugin_config)
event_coordinator = EventCoordinator(service, plugin_config, event_library)
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
pk_library = PkQuestionLibrary(BUNDLED_PK_PATH)
pk_coordinator = PkCoordinator(
    service, plugin_config, pk_library, events=event_coordinator
)
event_coordinator.attach_pk(pk_coordinator)
group_quiz_library = PkQuestionLibrary(BUNDLED_GROUP_QUIZ_PATH)
group_quiz_coordinator = GroupQuizCoordinator(
    service, plugin_config, group_quiz_library, events=event_coordinator
)
event_coordinator.attach_group_quiz(group_quiz_coordinator)
teyvat_quiz_library = PkQuestionLibrary(BUNDLED_TEYVAT_QUIZ_PATH)
teyvat_quiz_coordinator = TeyvatQuizCoordinator(
    service, plugin_config, teyvat_quiz_library, events=event_coordinator
)
event_coordinator.attach_teyvat_quiz(teyvat_quiz_coordinator)
hsr_quiz_library = PkQuestionLibrary(BUNDLED_HSR_QUIZ_PATH)
hsr_quiz_coordinator = HsrQuizCoordinator(
    service, plugin_config, hsr_quiz_library, events=event_coordinator
)
event_coordinator.attach_hsr_quiz(hsr_quiz_coordinator)
zzz_quiz_library = PkQuestionLibrary(BUNDLED_ZZZ_QUIZ_PATH)
zzz_quiz_coordinator = ZzzQuizCoordinator(
    service, plugin_config, zzz_quiz_library, events=event_coordinator
)
event_coordinator.attach_zzz_quiz(zzz_quiz_coordinator)
petit_planet_quiz_library = PkQuestionLibrary(BUNDLED_PETIT_PLANET_QUIZ_PATH)
petit_planet_quiz_coordinator = PetitPlanetQuizCoordinator(
    service, plugin_config, petit_planet_quiz_library, events=event_coordinator
)
event_coordinator.attach_petit_planet_quiz(petit_planet_quiz_coordinator)
fill_blank_library = FillBlankLibrary(BUNDLED_FILL_BLANK_PATH)
fill_blank_coordinator = FillBlankCoordinator(
    service, plugin_config, fill_blank_library, events=event_coordinator
)
event_coordinator.attach_fill_blank(fill_blank_coordinator)
meme_generator_adapter = MemeGeneratorAdapter(
    bot_name=plugin_config.mantou_affection_bot_name,
    blocked_terms=parse_blocked_terms(plugin_config.mantou_affection_memes_blocked_terms),
    ranking_fetcher=service.ranking,
)
meme_repo_manager = MemeRepoManager(
    backend=meme_generator_adapter.backend,
    base_dir=Path(get_plugin_data_dir()) / "meme_repos",
    enabled=plugin_config.mantou_affection_memes_extra_repos,
)
meme_generator_adapter.repos = meme_repo_manager
meme_resources = MemeResourceManager()
taozi_stickers = TaoziStickerLibrary(
    base_dir=Path(get_plugin_data_dir()) / "taozi_stickers"
)
service.stickers = taozi_stickers
wallpapers = WallpaperLibrary(
    cache_dir=Path(get_plugin_data_dir()) / "wallpapers"
)
wheel_coordinator = WheelCoordinator(
    service,
    plugin_config,
    events=event_coordinator,
    memes=meme_generator_adapter,
    resources=meme_resources,
    stickers=taozi_stickers,
    wallpapers=wallpapers,
    image_quota=image_quota,
    panel_quota=panel_quota,
    panel_rank_quota=panel_rank_quota,
    spin_quota=spin_quota,
)
event_coordinator.attach_wheel(wheel_coordinator)
spin_matchers = register_spin_commands(wheel_coordinator)
matchers = register_commands(
    service, plugin_config, pk_coordinator, image_processing, wallpapers
)
ambient_matcher, answer_matcher = register_ambient(
    service,
    plugin_config,
    event_coordinator,
    find_char_coordinator,
    bet_coordinator,
    pk_coordinator,
    wheel_coordinator,
    group_quiz_coordinator,
    teyvat_quiz_coordinator,
    hsr_quiz_coordinator,
    zzz_quiz_coordinator,
    petit_planet_quiz_coordinator,
    fill_blank_coordinator,
)
force_matchers = register_force_commands(
    service,
    plugin_config,
    events=event_coordinator,
    findchar=find_char_coordinator,
    bet=bet_coordinator,
    pk=pk_coordinator,
    wheel=wheel_coordinator,
    group_quiz=group_quiz_coordinator,
    teyvat_quiz=teyvat_quiz_coordinator,
    hsr_quiz=hsr_quiz_coordinator,
    zzz_quiz=zzz_quiz_coordinator,
    petit_planet_quiz=petit_planet_quiz_coordinator,
    fill_blank=fill_blank_coordinator,
)
plugin_linkage = register_plugin_linkage(service, plugin_config)

driver = get_driver()

_FALSE_STARTUP_FLAG_STRINGS = {"", "0", "false", "no", "off"}


def _startup_flag_enabled(value: object) -> bool:
    """.env 来源的配置项可能是字符串，需显式解析布尔语义。"""

    if isinstance(value, str):
        return value.strip().lower() not in _FALSE_STARTUP_FLAG_STRINGS
    return bool(value)


@driver.on_startup
async def _check_meme_resources_on_startup() -> None:
    """启动时发起一次后台素材检查与外部表情仓库同步，不阻塞启动。"""

    memes_plugin = get_plugin("nonebot_plugin_memes")
    handled = memes_plugin is not None and _startup_flag_enabled(
        getattr(driver.config, "memes_check_resources_on_startup", True)
    )
    meme_resources.check_on_startup(handled_by_memes_plugin=handled)
    meme_repo_manager.kickoff()
    taozi_stickers.kickoff()


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


async def get_panel_draws(group_id: str | int, user_id: str | int) -> int:
    """读取指定群友剩余的角色面板次数。"""

    return await panel_quota.get(str(group_id), str(user_id))


async def add_panel_draws(group_id: str | int, user_id: str | int, amount: int = 1) -> int:
    """给指定群友增加角色面板次数，返回新的余额。"""

    return await panel_quota.add(str(group_id), str(user_id), amount)


async def consume_panel_draw(
    group_id: str | int, user_id: str | int, amount: int = 1
) -> int | None:
    """扣除角色面板次数；次数不足返回 None 且不改动余额。"""

    return await panel_quota.consume(str(group_id), str(user_id), amount)


async def get_panel_rank_draws(group_id: str | int, user_id: str | int) -> int:
    """读取指定群友剩余的群面板排名次数。"""

    return await panel_rank_quota.get(str(group_id), str(user_id))


async def add_panel_rank_draws(
    group_id: str | int, user_id: str | int, amount: int = 1
) -> int:
    """给指定群友增加群面板排名次数，返回新的余额。"""

    return await panel_rank_quota.add(str(group_id), str(user_id), amount)


async def consume_panel_rank_draw(
    group_id: str | int, user_id: str | int, amount: int = 1
) -> int | None:
    """扣除群面板排名次数；次数不足返回 None 且不改动余额。"""

    return await panel_rank_quota.consume(str(group_id), str(user_id), amount)


async def spend_affection(
    group_id: str | int,
    user_id: str | int,
    amount: int,
    *,
    nickname: str = "",
    source: str = "external",
) -> tuple[bool, int]:
    """固定扣除好感度，返回 (是否成功, 当前好感度)。

    与 ``change_affection`` 不同：不参与联动每日上限、冷却和新手保护，
    适合面板查看这类明码标价的消费；余额不足时不扣款。
    """

    return await service.spend_fixed(
        str(group_id), str(user_id), nickname, max(0, int(amount))
    )


async def maybe_start_bet(
    group_id: str | int,
    user_id: str | int,
    *,
    nickname: str = "",
    candidates: Iterable[tuple[str | int, str]] | None = None,
    send: Callable[[Message | str], Awaitable[object]] | None = None,
) -> bool:
    """按概率开一局「馒头博弈」，开局成功返回 True；插件内部的群消息触发也走这条路。

    candidates 是最近说过话的成员 [(user_id, name), ...]，会排除触发者和馒头好感度
    没过门槛的人，再随机抽两位当对手；不足两人、本群已有其它活动、概率没掷中或没传
    send 都会直接返回 False。send 用来发开场消息与结算消息，通常直接传 matcher.send。
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

    event_text = await event_coordinator.start_poke_event(
        group_id=str(group_id),
        user_id=str(user_id),
        nickname=nickname,
        send=send,
        chance=plugin_config.mantou_affection_poke_event_chance,
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
