from __future__ import annotations

from nonebot import get_driver, logger, on_command
from nonebot.adapters import Event
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageSegment
from nonebot.params import Command, CommandArg
from nonebot.permission import SUPERUSER
from nonebot.rule import Rule

from .config import Config
from .image_processing import ImageProcessingCoordinator
from .logic import level_for, progress_text
from .parsing import (
    FIND_CHAR_CHANCE_USAGE,
    PROBABILITY_BATCH_RESET,
    PROBABILITY_BATCH_SCALE,
    parse_adjustment,
    parse_event_limit_count,
    parse_event_limit_toggle,
    parse_find_char_chances,
    parse_probability,
    parse_probability_batch,
)
from .pk import BUNDLED_PK_PATH, PkCoordinator, PkQuestionLibrary
from .service import (
    EVENT_LIMIT_MAX_PER_WINDOW,
    EVENT_LIMIT_MIN_PER_WINDOW,
    EVENT_LIMIT_WINDOW_HOURS,
    AffectionService,
    normalize_nickname,
)
from .wheel_wallpapers import (
    WallpaperLibrary,
    WallpaperUnavailable,
    match_wallpaper_game,
)

BET_CHANCE_USAGE = (
    "请输入 0~1 的小数（如 0.0005）或百分数（如 0.05%），0 即关闭馒头博弈"
)
PK_CHANCE_USAGE = "请输入 0~1 的小数（如 0.01）或百分数（如 1%），0 即关闭馒头问答"
GROUP_QUIZ_CHANCE_USAGE = (
    "请输入 0~1 的小数（如 0.0005）或百分数（如 0.05%），0 即关闭群友问答"
)
TEYVAT_CHANCE_USAGE = (
    "请输入 0~1 的小数（如 0.0005）或百分数（如 0.05%），0 即关闭提瓦特问答"
)
HSR_CHANCE_USAGE = (
    "请输入 0~1 的小数（如 0.0005）或百分数（如 0.05%），0 即关闭星穹铁道问答"
)
ZZZ_CHANCE_USAGE = (
    "请输入 0~1 的小数（如 0.0005）或百分数（如 0.05%），0 即关闭绝区零问答"
)
PETIT_PLANET_CHANCE_USAGE = (
    "请输入 0~1 的小数（如 0.0005）或百分数（如 0.05%），0 即关闭星布谷地问答"
)
FILL_BLANK_CHANCE_USAGE = (
    "请输入 0~1 的小数（如 0.0005）或百分数（如 0.05%），0 即关闭补句问答"
)
WHEEL_CHANCE_USAGE = "请输入 0~1 的小数或百分数（如 0.1%），0 即关闭馒头转盘"
WALLPAPER_USAGE = (
    "用法：/馒头壁纸 [官方/二创] [原神/星铁/绝区零]\n"
    "不带参数抽官方壁纸；加「二创」改成抽二创图，可再跟一个游戏名"
)
WALLPAPER_FANART_WORDS = frozenset({"二创", "同人", "fanart", "fan"})
WALLPAPER_OFFICIAL_WORDS = frozenset({"官方", "official"})
WALLPAPER_FAILED_REPLY = "这次没取到壁纸：图源暂时连不上，稍后再试。"
WALLPAPER_DISABLED_REPLY = "壁纸库没有接线，暂时抽不了。"
PROBABILITY_BATCH_USAGE = (
    "用法：\n"
    "/馒头概率总调 0.05% —— 全部统一设为该值\n"
    "/馒头概率总调 ×2 —— 全部按当前值缩放（支持 ×50%）\n"
    "/馒头概率总调 重置 —— 清除覆盖，恢复配置默认"
)
MEMBER_HELP_LINES: tuple[str, ...] = (
    "/馒头互动 —— 陪馒头玩，攒好感",
    "/馒头好感 —— 查看自己的好感档案",
    "/馒头好感榜 —— 查看本群排行榜",
    "/馒头图像处理 —— 处理图片并保持动图，支持 GIF 变速（回复图片后使用）",
    "/翻转 /旋转 90 /gif变速 0.5x —— 回复图片后直接用的图像处理快捷键",
    "/馒头帮助 —— 打开这份菜单",
)
ADMIN_HELP_LINES: tuple[str, ...] = (
    "/馒头好感调整 @群友 +10 —— 手动增减好感",
    "/馒头好感重置 确认 —— 清空所有群的好感数据",
    "/馒头反应概率 5% —— 查看或调整馒头小动作概率",
    "/馒头找字概率 5% —— 查看或调整找字概率"
    "（可写「群消息 5% 戳一戳 2%」）",
    "/馒头博弈概率 0.05% —— 查看或调整博弈开局概率",
    "/馒头问答概率 1% —— 查看或调整桃子问答概率",
    "/馒头问答题库 —— 查看题库余量",
    "/馒头群友问答概率 0.05% —— 查看或调整群友问答概率",
    "/馒头提瓦特概率 0.05% —— 查看或调整提瓦特问答概率",
    "/馒头星铁概率 0.05% —— 查看或调整星穹铁道问答概率",
    "/馒头绝区零概率 0.05% —— 查看或调整绝区零问答概率",
    "/馒头星布谷地概率 0.05% —— 查看或调整星布谷地问答概率",
    "/馒头补句概率 0.05% —— 查看或调整补句问答概率",
    "/馒头转盘概率 0.1% —— 查看或调整命运齿轮概率",
    "/馒头概率总调 —— 总览全部概率；带数值统一设置，"
    "×倍率缩放，重置恢复默认",
    "/馒头壁纸 [官方/二创] [原神/星铁/绝区零] —— 手动抽一张壁纸",
    "/馒头限制开关 [开/关] —— 查看或切换事件限制（默认关闭）",
    f"/馒头限制次数 5 —— 查看或调整每群每 {EVENT_LIMIT_WINDOW_HOURS} 小时"
    "最多触发几次活动（1~100）",
    "/馒头管理帮助 —— 查看这份完整命令帮助",
    "/馒头强制事件 @群友 —— 回复 1~12 强制开一场活动",
)
HELP_FOOTER = (
    "提示：概率命令不带参数就查看当前值，0 即关闭对应活动；"
    f"事件限制开启后每群每 {EVENT_LIMIT_WINDOW_HOURS} 小时最多触发限定次数的活动，"
    "达到上限后自动活动"
    "（含小动作）会暂停到额度恢复，答题、主动命令和 /馒头强制事件 不受影响；"
    "旧命令 /馒头好感帮助 仍然可用。"
)


def _render_help(title: str) -> str:
    """按群友/管理员两段渲染命令菜单，群友和管理员帮助共用一份文案。"""

    return "\n".join(
        [
            f"🥟 馒头好感度 · {title}",
            "",
            "【群友可用】",
            *MEMBER_HELP_LINES,
            "",
            "【管理员 · SUPERUSER】",
            *ADMIN_HELP_LINES,
            "",
            HELP_FOOTER,
        ]
    )


async def _group_only(event: Event) -> bool:
    return isinstance(event, GroupMessageEvent)


GROUP_ONLY = Rule(_group_only)


def _nickname(event: GroupMessageEvent) -> str:
    raw = event.sender.card or event.sender.nickname or str(event.user_id)
    return normalize_nickname(raw, str(event.user_id))


def _gain_text(delta: int) -> str:
    return f"好感度 {delta:+d}" if delta else "好感度没有变化"


def _format_wait(seconds: int) -> str:
    if seconds >= 3600:
        hours, remainder = divmod(seconds, 3600)
        minutes = remainder // 60
        return f"{hours} 小时 {minutes} 分钟" if minutes else f"{hours} 小时"
    if seconds >= 60:
        return f"{seconds // 60} 分钟"
    return f"{seconds} 秒"


def _format_probability(value: float) -> str:
    percent = f"{value * 100:.6f}".rstrip("0").rstrip(".")
    return f"{percent or '0'}%"


def _activity_hours_text(config: Config) -> str:
    """把随机活动的开启时段渲染成状态命令里的一行。"""

    if not config.mantou_affection_active_hours_enabled:
        return "全天（时段限制已关闭）"
    start = config.mantou_affection_active_hours_start
    end = config.mantou_affection_active_hours_end
    if start == end:
        return "全天"
    return f"每天 {start:02d}:00~{end:02d}:00（转盘不受时段限制）"


def _format_factor(value: float) -> str:
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return text or "0"


def register_commands(
    service: AffectionService,
    config: Config,
    pk: PkCoordinator | None = None,
    image_processing: ImageProcessingCoordinator | None = None,
    wallpapers: WallpaperLibrary | None = None,
) -> tuple:
    profile_cmd = on_command(
        "馒头好感",
        aliases={"馒头好感度", "我的好感"},
        rule=GROUP_ONLY,
        priority=10,
        block=True,
    )
    interact_cmd = on_command(
        "馒头互动",
        aliases={"摸摸馒头", "陪馒头玩"},
        rule=GROUP_ONLY,
        priority=10,
        block=True,
    )
    ranking_cmd = on_command(
        "馒头好感榜",
        aliases={"好感榜", "馒头排行榜"},
        rule=GROUP_ONLY,
        priority=10,
        block=True,
    )
    help_cmd = on_command(
        "馒头帮助",
        aliases={"馒头好感帮助", "馒头好感菜单", "馒头命令", "馒头菜单"},
        rule=GROUP_ONLY,
        priority=10,
        block=True,
    )
    adjust_cmd = on_command(
        "馒头好感调整",
        permission=SUPERUSER,
        rule=GROUP_ONLY,
        priority=10,
        block=True,
    )
    reset_cmd = on_command(
        "馒头好感重置",
        permission=SUPERUSER,
        priority=10,
        block=True,
    )
    probability_cmd = on_command(
        "馒头反应概率",
        aliases={"馒头小动作概率"},
        permission=SUPERUSER,
        priority=10,
        block=True,
    )
    find_char_cmd = on_command(
        "馒头找字概率",
        aliases={"馒头找字触发概率"},
        permission=SUPERUSER,
        priority=10,
        block=True,
    )
    bet_cmd = on_command(
        "馒头博弈概率",
        aliases={"馒头博弈触发概率"},
        permission=SUPERUSER,
        priority=10,
        block=True,
    )
    pk_probability_cmd = on_command(
        "馒头问答概率",
        aliases={"馒头问答触发概率"},
        permission=SUPERUSER,
        priority=10,
        block=True,
    )
    pk_bank_cmd = on_command(
        "馒头问答题库",
        permission=SUPERUSER,
        priority=10,
        block=True,
    )
    wheel_cmd = on_command(
        "馒头转盘概率",
        aliases={"馒头转盘触发概率"},
        permission=SUPERUSER,
        priority=10,
        block=True,
    )
    group_quiz_cmd = on_command(
        "馒头群友问答概率",
        aliases={"馒头群友问答触发概率"},
        permission=SUPERUSER,
        priority=10,
        block=True,
    )
    teyvat_quiz_cmd = on_command(
        "馒头提瓦特概率",
        aliases={"馒头提瓦特问答概率", "馒头提瓦特触发概率"},
        permission=SUPERUSER,
        priority=10,
        block=True,
    )
    hsr_quiz_cmd = on_command(
        "馒头星铁概率",
        aliases={"馒头星穹铁道概率", "馒头星铁问答概率"},
        permission=SUPERUSER,
        priority=10,
        block=True,
    )
    zzz_quiz_cmd = on_command(
        "馒头绝区零概率",
        aliases={"馒头绝区零问答概率", "馒头ZZZ概率"},
        permission=SUPERUSER,
        priority=10,
        block=True,
    )
    petit_planet_quiz_cmd = on_command(
        "馒头星布谷地概率",
        aliases={"馒头星布谷地问答概率", "馒头谷地概率"},
        permission=SUPERUSER,
        priority=10,
        block=True,
    )
    fill_blank_cmd = on_command(
        "馒头补句概率",
        aliases={"馒头补句触发概率"},
        permission=SUPERUSER,
        priority=10,
        block=True,
    )
    batch_probability_cmd = on_command(
        "馒头概率总调",
        aliases={"馒头概率总览", "馒头总概率"},
        permission=SUPERUSER,
        priority=10,
        block=True,
    )
    event_limit_toggle_cmd = on_command(
        "馒头限制开关",
        aliases={"馒头事件限制", "馒头限流开关", "馒头限制状态"},
        permission=SUPERUSER,
        priority=10,
        block=True,
    )
    event_limit_count_cmd = on_command(
        "馒头限制次数",
        aliases={"馒头每两小时次数", "馒头限制上限", "馒头事件上限"},
        permission=SUPERUSER,
        priority=10,
        block=True,
    )
    wallpaper_cmd = on_command(
        "馒头壁纸",
        aliases={"馒头随机壁纸", "馒头抽壁纸"},
        permission=SUPERUSER,
        priority=10,
        block=True,
    )
    admin_help_cmd = on_command(
        "馒头管理帮助",
        aliases={"馒头完整帮助", "馒头管理员帮助", "馒头全部命令"},
        permission=SUPERUSER,
        priority=10,
        block=True,
    )
    image_cmd = on_command(
        "馒头图像处理",
        aliases={
            "馒头图片处理",
            "馒头修图",
            "馒头图片操作",
            "翻转",
            "水平翻转",
            "左翻",
            "右翻",
            "竖直翻转",
            "上翻",
            "下翻",
            "旋转",
            "缩放",
            "裁剪",
            "灰度",
            "灰度图",
            "黑白",
            "反相",
            "反色",
            "横向拼接",
            "纵向拼接",
            "gif变速",
            "GIF变速",
            "变速",
        },
        rule=GROUP_ONLY,
        priority=10,
        block=True,
    )
    pk_coordinator = pk or PkCoordinator(
        service, config, PkQuestionLibrary(BUNDLED_PK_PATH)
    )

    @profile_cmd.handle()
    async def handle_profile(event: GroupMessageEvent) -> None:
        try:
            profile = await service.profile(str(event.group_id), str(event.user_id))
        except Exception:
            logger.exception("[mantou-affection] 读取好感度失败")
            await profile_cmd.finish("馒头翻了翻小本本，但这一页暂时打不开，请稍后再试。")
            return
        level = level_for(profile.affection)
        name = _nickname(event)
        await profile_cmd.finish(
            f"🥟 {name} 与{config.mantou_affection_bot_name}的好感档案\n"
            f"好感度：{profile.affection}\n"
            f"称号：Lv.{level.number}「{level.title}」\n"
            f"今日插件联动：{service.linked_points_today(profile)}/"
            f"{config.mantou_affection_link_daily_limit}\n"
            f"今日好感获取：{service.gained_points_today(profile)}/"
            f"{config.mantou_affection_daily_gain_limit}\n"
            f"进度：{progress_text(profile.affection)}"
        )

    @interact_cmd.handle()
    async def handle_interact(event: GroupMessageEvent) -> None:
        try:
            result = await service.interact(
                str(event.group_id), str(event.user_id), _nickname(event)
            )
        except Exception:
            logger.exception("[mantou-affection] 互动失败")
            await interact_cmd.finish("馒头刚刚走神了，没有记下这次互动，请稍后再试。")
            return
        if result.reason == "daily_limit":
            await interact_cmd.finish(
                f"今天已经陪{config.mantou_affection_bot_name}玩得够久啦，明天再来吧。"
            )
        if result.reason == "cooldown":
            await interact_cmd.finish(
                f"让{config.mantou_affection_bot_name}喘口气吧，"
                f"{_format_wait(result.wait_seconds)}后再来互动。"
            )
        await interact_cmd.finish(
            f"{result.text}\n{_gain_text(result.delta)}，当前 {result.affection}\n"
            f"今天还可互动 {result.remaining} 次"
        )

    @ranking_cmd.handle()
    async def handle_ranking(event: GroupMessageEvent) -> None:
        try:
            entries = await service.ranking(str(event.group_id))
        except Exception:
            logger.exception("[mantou-affection] 读取排行榜失败")
            await ranking_cmd.finish("馒头暂时找不到排行榜，请稍后再试。")
            return
        if not entries:
            await ranking_cmd.finish("本群还没有好感记录，发送 /馒头互动 成为第一位吧。")
        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        lines = [f"🥟 {config.mantou_affection_bot_name}好感榜"]
        for entry in entries:
            profile = entry.profile
            name = profile.nickname or f"QQ {profile.user_id}"
            prefix = medals.get(entry.rank, f"{entry.rank}.")
            title = level_for(profile.affection).title
            lines.append(f"{prefix} {name}｜{profile.affection}｜{title}")
        await ranking_cmd.finish("\n".join(lines))

    @help_cmd.handle()
    async def handle_help() -> None:
        await help_cmd.finish(_render_help("全部命令"))

    @admin_help_cmd.handle()
    async def handle_admin_help() -> None:
        await admin_help_cmd.finish(_render_help("完整命令帮助"))

    @adjust_cmd.handle()
    async def handle_adjust(event: GroupMessageEvent, args: Message = CommandArg()) -> None:
        try:
            adjustment = parse_adjustment(args)
        except ValueError as error:
            await adjust_cmd.finish(str(error))
            return
        try:
            applied, profile, _protected = await service.adjust(
                str(event.group_id),
                adjustment.user_id,
                "",
                adjustment.delta,
                protect=False,
            )
        except Exception:
            logger.exception("[mantou-affection] 管理员调整好感度失败")
            await adjust_cmd.finish("调整失败，请检查数据目录权限后重试。")
            return
        message = MessageSegment.at(adjustment.user_id) + MessageSegment.text(
            f" 好感度已调整 {applied:+d}，当前为 {profile.affection}。"
        )
        await adjust_cmd.finish(message)

    @reset_cmd.handle()
    async def handle_reset(args: Message = CommandArg()) -> None:
        if args.extract_plain_text().strip() != "确认":
            await reset_cmd.finish(
                "⚠️ 这将清空所有群所有群友的好感度数据，且不可恢复！\n"
                "确认请发送：/馒头好感重置 确认"
            )
            return
        try:
            count = await service.store.reset_all()
        except Exception:
            logger.exception("[mantou-affection] 重置好感度失败")
            await reset_cmd.finish("重置失败，请检查数据目录权限后重试。")
            return
        await reset_cmd.finish(f"已重置全部好感度数据，共清空 {count} 条群友档案。")

    @probability_cmd.handle()
    async def handle_probability(args: Message = CommandArg()) -> None:
        raw = args.extract_plain_text().strip()
        if not raw:
            try:
                current = await service.ambient_probability()
            except Exception:
                logger.exception("[mantou-affection] 读取小动作概率失败")
                await probability_cmd.finish("读取失败，请检查数据目录权限后重试。")
                return
            await probability_cmd.finish(
                f"当前馒头小动作概率：{_format_probability(current)}"
            )
            return
        try:
            value = parse_probability(raw)
        except ValueError as error:
            await probability_cmd.finish(str(error))
            return
        try:
            await service.set_ambient_probability(value)
        except Exception:
            logger.exception("[mantou-affection] 保存小动作概率失败")
            await probability_cmd.finish("保存失败，请检查数据目录权限后重试。")
            return
        await probability_cmd.finish(
            f"馒头小动作概率已调整为 {_format_probability(value)}"
        )

    @find_char_cmd.handle()
    async def handle_find_char_chance(args: Message = CommandArg()) -> None:
        raw = args.extract_plain_text().strip()
        if not raw:
            try:
                group_chance, poke_chance = await service.find_char_chances()
            except Exception:
                logger.exception("[mantou-affection] 读取找字概率失败")
                await find_char_cmd.finish("读取失败，请检查数据目录权限后重试。")
                return
            await find_char_cmd.finish(
                "当前找字小游戏概率——\n"
                f"群消息：{_format_probability(group_chance)}\n"
                f"戳一戳：{_format_probability(poke_chance)}\n"
                f"用法：{FIND_CHAR_CHANCE_USAGE}"
            )
            return
        try:
            wanted = parse_find_char_chances(raw)
        except ValueError as error:
            await find_char_cmd.finish(str(error))
            return
        try:
            await service.set_find_char_chances(group=wanted.group, poke=wanted.poke)
        except Exception:
            logger.exception("[mantou-affection] 保存找字概率失败")
            await find_char_cmd.finish("保存失败，请检查数据目录权限后重试。")
            return
        lines = ["馒头找字概率已调整——"]
        if wanted.group is not None:
            lines.append(f"群消息：{_format_probability(wanted.group)}")
        if wanted.poke is not None:
            lines.append(f"戳一戳：{_format_probability(wanted.poke)}")
        await find_char_cmd.finish("\n".join(lines))

    @bet_cmd.handle()
    async def handle_bet_probability(args: Message = CommandArg()) -> None:
        raw = args.extract_plain_text().strip()
        if not raw:
            try:
                current = await service.bet_probability()
                override = await service.bet_probability_override()
            except Exception:
                logger.exception("[mantou-affection] 读取博弈概率失败")
                await bet_cmd.finish("读取失败，请检查数据目录权限后重试。")
                return
            default = config.mantou_affection_bet_chance
            if override is None:
                source = f"配置默认 {_format_probability(default)}"
            else:
                source = f"运行时覆盖，配置默认 {_format_probability(default)}"
            await bet_cmd.finish(
                f"当前馒头博弈概率：{_format_probability(current)}（{source}）\n"
                f"用法：{BET_CHANCE_USAGE}"
            )
            return
        try:
            value = parse_probability(raw)
        except ValueError as error:
            await bet_cmd.finish(f"{error}。{BET_CHANCE_USAGE}")
            return
        try:
            await service.set_bet_probability(value)
        except Exception:
            logger.exception("[mantou-affection] 保存博弈概率失败")
            await bet_cmd.finish("保存失败，请检查数据目录权限后重试。")
            return
        await bet_cmd.finish(f"馒头博弈概率已调整为 {_format_probability(value)}")

    @pk_probability_cmd.handle()
    async def handle_pk_probability(args: Message = CommandArg()) -> None:
        raw = args.extract_plain_text().strip()
        if not raw:
            try:
                current = await service.pk_probability()
                override = await service.pk_probability_override()
            except Exception:
                logger.exception("[mantou-affection] 读取问答概率失败")
                await pk_probability_cmd.finish("读取失败，请检查数据目录权限后重试。")
                return
            default = config.mantou_affection_pk_chance
            if override is None:
                source = f"配置默认 {_format_probability(default)}"
            else:
                source = f"运行时覆盖，配置默认 {_format_probability(default)}"
            await pk_probability_cmd.finish(
                f"当前馒头问答概率：{_format_probability(current)}（{source}）\n"
                f"用法：{PK_CHANCE_USAGE}"
            )
            return
        try:
            value = parse_probability(raw)
        except ValueError as error:
            await pk_probability_cmd.finish(f"{error}。{PK_CHANCE_USAGE}")
            return
        try:
            await service.set_pk_probability(value)
        except Exception:
            logger.exception("[mantou-affection] 保存问答概率失败")
            await pk_probability_cmd.finish("保存失败，请检查数据目录权限后重试。")
            return
        await pk_probability_cmd.finish(f"馒头问答概率已调整为 {_format_probability(value)}")

    @pk_bank_cmd.handle()
    async def handle_pk_bank() -> None:
        try:
            remaining = await pk_coordinator.remaining_questions()
        except Exception:
            logger.exception("[mantou-affection] 读取问答题库余量失败")
            await pk_bank_cmd.finish("读取失败，请检查数据目录权限后重试。")
            return
        await pk_bank_cmd.finish(
            f"问答题库：还剩 {remaining}/{pk_coordinator.total_questions} 题"
        )

    @wheel_cmd.handle()
    async def handle_wheel_probability(args: Message = CommandArg()) -> None:
        raw = args.extract_plain_text().strip()
        if not raw:
            try:
                current = await service.wheel_probability()
                override = await service.wheel_probability_override()
            except Exception:
                logger.exception("[mantou-affection] 读取转盘概率失败")
                await wheel_cmd.finish("读取失败，请检查数据目录权限后重试。")
                return
            default = config.mantou_affection_wheel_chance
            if override is None:
                source = f"配置默认 {_format_probability(default)}"
            else:
                source = f"运行时覆盖，配置默认 {_format_probability(default)}"
            await wheel_cmd.finish(
                f"当前馒头转盘概率：{_format_probability(current)}（{source}）\n"
                f"用法：{WHEEL_CHANCE_USAGE}"
            )
            return
        try:
            value = parse_probability(raw)
        except ValueError as error:
            await wheel_cmd.finish(f"{error}。{WHEEL_CHANCE_USAGE}")
            return
        try:
            await service.set_wheel_probability(value)
        except Exception:
            logger.exception("[mantou-affection] 保存转盘概率失败")
            await wheel_cmd.finish("保存失败，请检查数据目录权限后重试。")
            return
        await wheel_cmd.finish(f"馒头转盘概率已调整为 {_format_probability(value)}")

    @group_quiz_cmd.handle()
    async def handle_group_quiz_probability(args: Message = CommandArg()) -> None:
        raw = args.extract_plain_text().strip()
        if not raw:
            try:
                current = await service.group_quiz_probability()
                override = await service.group_quiz_probability_override()
            except Exception:
                logger.exception("[mantou-affection] 读取群友问答概率失败")
                await group_quiz_cmd.finish("读取失败，请检查数据目录权限后重试。")
                return
            default = config.mantou_affection_group_quiz_chance
            if override is None:
                source = f"配置默认 {_format_probability(default)}"
            else:
                source = f"运行时覆盖，配置默认 {_format_probability(default)}"
            await group_quiz_cmd.finish(
                f"当前群友问答概率：{_format_probability(current)}（{source}）\n"
                f"用法：{GROUP_QUIZ_CHANCE_USAGE}"
            )
            return
        try:
            value = parse_probability(raw)
        except ValueError as error:
            await group_quiz_cmd.finish(f"{error}。{GROUP_QUIZ_CHANCE_USAGE}")
            return
        try:
            await service.set_group_quiz_probability(value)
        except Exception:
            logger.exception("[mantou-affection] 保存群友问答概率失败")
            await group_quiz_cmd.finish("保存失败，请检查数据目录权限后重试。")
            return
        await group_quiz_cmd.finish(
            f"群友问答概率已调整为 {_format_probability(value)}"
        )

    @teyvat_quiz_cmd.handle()
    async def handle_teyvat_quiz_probability(args: Message = CommandArg()) -> None:
        raw = args.extract_plain_text().strip()
        if not raw:
            try:
                current = await service.teyvat_quiz_probability()
                override = await service.teyvat_quiz_probability_override()
            except Exception:
                logger.exception("[mantou-affection] 读取提瓦特问答概率失败")
                await teyvat_quiz_cmd.finish("读取失败，请检查数据目录权限后重试。")
                return
            default = config.mantou_affection_teyvat_chance
            if override is None:
                source = f"配置默认 {_format_probability(default)}"
            else:
                source = f"运行时覆盖，配置默认 {_format_probability(default)}"
            await teyvat_quiz_cmd.finish(
                f"当前提瓦特问答概率：{_format_probability(current)}（{source}）\n"
                f"用法：{TEYVAT_CHANCE_USAGE}"
            )
            return
        try:
            value = parse_probability(raw)
        except ValueError as error:
            await teyvat_quiz_cmd.finish(f"{error}。{TEYVAT_CHANCE_USAGE}")
            return
        try:
            await service.set_teyvat_quiz_probability(value)
        except Exception:
            logger.exception("[mantou-affection] 保存提瓦特问答概率失败")
            await teyvat_quiz_cmd.finish("保存失败，请检查数据目录权限后重试。")
            return
        await teyvat_quiz_cmd.finish(
            f"提瓦特问答概率已调整为 {_format_probability(value)}"
        )

    @hsr_quiz_cmd.handle()
    async def handle_hsr_quiz_probability(args: Message = CommandArg()) -> None:
        raw = args.extract_plain_text().strip()
        if not raw:
            try:
                current = await service.hsr_quiz_probability()
                override = await service.hsr_quiz_probability_override()
            except Exception:
                logger.exception("[mantou-affection] 读取星穹铁道问答概率失败")
                await hsr_quiz_cmd.finish("读取失败，请检查数据目录权限后重试。")
                return
            default = config.mantou_affection_hsr_chance
            source = (
                f"配置默认 {_format_probability(default)}"
                if override is None
                else f"运行时覆盖，配置默认 {_format_probability(default)}"
            )
            await hsr_quiz_cmd.finish(
                f"当前星穹铁道问答概率：{_format_probability(current)}（{source}）\n"
                f"用法：{HSR_CHANCE_USAGE}"
            )
            return
        try:
            value = parse_probability(raw)
        except ValueError as error:
            await hsr_quiz_cmd.finish(f"{error}。{HSR_CHANCE_USAGE}")
            return
        try:
            await service.set_hsr_quiz_probability(value)
        except Exception:
            logger.exception("[mantou-affection] 保存星穹铁道问答概率失败")
            await hsr_quiz_cmd.finish("保存失败，请检查数据目录权限后重试。")
            return
        await hsr_quiz_cmd.finish(
            f"星穹铁道问答概率已调整为 {_format_probability(value)}"
        )

    @zzz_quiz_cmd.handle()
    async def handle_zzz_quiz_probability(args: Message = CommandArg()) -> None:
        raw = args.extract_plain_text().strip()
        if not raw:
            try:
                current = await service.zzz_quiz_probability()
                override = await service.zzz_quiz_probability_override()
            except Exception:
                logger.exception("[mantou-affection] 读取绝区零问答概率失败")
                await zzz_quiz_cmd.finish("读取失败，请检查数据目录权限后重试。")
                return
            default = config.mantou_affection_zzz_chance
            source = (
                f"配置默认 {_format_probability(default)}"
                if override is None
                else f"运行时覆盖，配置默认 {_format_probability(default)}"
            )
            await zzz_quiz_cmd.finish(
                f"当前绝区零问答概率：{_format_probability(current)}（{source}）\n"
                f"用法：{ZZZ_CHANCE_USAGE}"
            )
            return
        try:
            value = parse_probability(raw)
        except ValueError as error:
            await zzz_quiz_cmd.finish(f"{error}。{ZZZ_CHANCE_USAGE}")
            return
        try:
            await service.set_zzz_quiz_probability(value)
        except Exception:
            logger.exception("[mantou-affection] 保存绝区零问答概率失败")
            await zzz_quiz_cmd.finish("保存失败，请检查数据目录权限后重试。")
            return
        await zzz_quiz_cmd.finish(
            f"绝区零问答概率已调整为 {_format_probability(value)}"
        )

    @petit_planet_quiz_cmd.handle()
    async def handle_petit_planet_quiz_probability(args: Message = CommandArg()) -> None:
        raw = args.extract_plain_text().strip()
        if not raw:
            try:
                current = await service.petit_planet_quiz_probability()
                override = await service.petit_planet_quiz_probability_override()
            except Exception:
                logger.exception("[mantou-affection] 读取星布谷地问答概率失败")
                await petit_planet_quiz_cmd.finish("读取失败，请检查数据目录权限后重试。")
                return
            default = config.mantou_affection_petit_planet_chance
            source = (
                f"配置默认 {_format_probability(default)}"
                if override is None
                else f"运行时覆盖，配置默认 {_format_probability(default)}"
            )
            await petit_planet_quiz_cmd.finish(
                f"当前星布谷地问答概率：{_format_probability(current)}（{source}）\n"
                f"用法：{PETIT_PLANET_CHANCE_USAGE}"
            )
            return
        try:
            value = parse_probability(raw)
        except ValueError as error:
            await petit_planet_quiz_cmd.finish(f"{error}。{PETIT_PLANET_CHANCE_USAGE}")
            return
        try:
            await service.set_petit_planet_quiz_probability(value)
        except Exception:
            logger.exception("[mantou-affection] 保存星布谷地问答概率失败")
            await petit_planet_quiz_cmd.finish("保存失败，请检查数据目录权限后重试。")
            return
        await petit_planet_quiz_cmd.finish(
            f"星布谷地问答概率已调整为 {_format_probability(value)}"
        )

    @fill_blank_cmd.handle()
    async def handle_fill_blank_probability(args: Message = CommandArg()) -> None:
        raw = args.extract_plain_text().strip()
        if not raw:
            try:
                current = await service.fill_blank_probability()
                override = await service.fill_blank_probability_override()
            except Exception:
                logger.exception("[mantou-affection] 读取补句问答概率失败")
                await fill_blank_cmd.finish("读取失败，请检查数据目录权限后重试。")
                return
            default = config.mantou_affection_fill_blank_chance
            if override is None:
                source = f"配置默认 {_format_probability(default)}"
            else:
                source = f"运行时覆盖，配置默认 {_format_probability(default)}"
            await fill_blank_cmd.finish(
                f"当前补句问答概率：{_format_probability(current)}（{source}）\n"
                f"用法：{FILL_BLANK_CHANCE_USAGE}"
            )
            return
        try:
            value = parse_probability(raw)
        except ValueError as error:
            await fill_blank_cmd.finish(f"{error}。{FILL_BLANK_CHANCE_USAGE}")
            return
        try:
            await service.set_fill_blank_probability(value)
        except Exception:
            logger.exception("[mantou-affection] 保存补句问答概率失败")
            await fill_blank_cmd.finish("保存失败，请检查数据目录权限后重试。")
            return
        await fill_blank_cmd.finish(
            f"补句问答概率已调整为 {_format_probability(value)}"
        )

    @batch_probability_cmd.handle()
    async def handle_probability_batch(args: Message = CommandArg()) -> None:
        raw = args.extract_plain_text().strip()
        try:
            states = await service.probability_states()
        except Exception:
            logger.exception("[mantou-affection] 读取概率总览失败")
            await batch_probability_cmd.finish("读取失败，请检查数据目录权限后重试。")
            return

        if not raw:
            lines = [f"🥟 馒头概率总览（共 {len(states)} 项）"]
            for state in states:
                source = (
                    f"运行时覆盖，配置默认 {_format_probability(state.default)}"
                    if state.overridden
                    else "配置默认"
                )
                lines.append(
                    f"{state.label}：{_format_probability(state.value)}（{source}）"
                )
            lines.append(PROBABILITY_BATCH_USAGE)
            await batch_probability_cmd.finish("\n".join(lines))
            return

        try:
            request = parse_probability_batch(raw)
        except ValueError as error:
            await batch_probability_cmd.finish(str(error))
            return

        if request.mode == PROBABILITY_BATCH_RESET:
            try:
                removed = await service.reset_all_probabilities()
            except Exception:
                logger.exception("[mantou-affection] 重置概率失败")
                await batch_probability_cmd.finish("保存失败，请检查数据目录权限后重试。")
                return
            if removed:
                await batch_probability_cmd.finish(
                    f"已清除 {removed} 项运行时概率覆盖，全部恢复配置默认值。"
                )
            else:
                await batch_probability_cmd.finish(
                    "当前没有运行时概率覆盖，全部已是配置默认值。"
                )
            return

        if request.mode == PROBABILITY_BATCH_SCALE:
            try:
                changes = await service.scale_all_probabilities(request.value)
            except Exception:
                logger.exception("[mantou-affection] 缩放概率失败")
                await batch_probability_cmd.finish("保存失败，请检查数据目录权限后重试。")
                return
            lines = [
                f"已按 ×{_format_factor(request.value)} 缩放 "
                f"{len(changes)} 项活动概率："
            ]
            lines.extend(
                f"{change.label}：{_format_probability(change.before)} → "
                f"{_format_probability(change.after)}"
                for change in changes
            )
            await batch_probability_cmd.finish("\n".join(lines))
            return

        try:
            await service.set_all_probabilities(request.value)
        except Exception:
            logger.exception("[mantou-affection] 批量调整概率失败")
            await batch_probability_cmd.finish("保存失败，请检查数据目录权限后重试。")
            return
        await batch_probability_cmd.finish(
            f"已把 {len(states)} 项活动概率统一设为 "
            f"{_format_probability(request.value)}"
            "（发送 /馒头概率总调 重置 可恢复默认）。"
        )

    @event_limit_toggle_cmd.handle()
    async def handle_event_limit_toggle(
        event: Event, args: Message = CommandArg()
    ) -> None:
        raw = args.extract_plain_text().strip()
        group_id = str(event.group_id) if isinstance(event, GroupMessageEvent) else ""
        if not raw:
            try:
                state = await service.event_limit_state(group_id)
            except Exception:
                logger.exception("[mantou-affection] 读取事件限制失败")
                await event_limit_toggle_cmd.finish(
                    "读取失败，请检查数据目录权限后重试。"
                )
                return
            lines = [
                f"🕒 事件限制：{'已开启' if state.enabled else '已关闭'}",
                f"每群每 {EVENT_LIMIT_WINDOW_HOURS} 小时上限：{state.per_window} 次",
                f"活动开启时段：{_activity_hours_text(config)}",
            ]
            if group_id and state.enabled:
                lines.append(
                    f"本群最近 {EVENT_LIMIT_WINDOW_HOURS} 小时内已触发 {state.used} 次，"
                    f"剩余 {state.remaining} 次"
                )
            if state.enabled:
                lines.append(
                    "发送 /馒头限制开关 关 可关闭，"
                    "/馒头限制次数 N 可调整上限"
                )
            else:
                lines.append("发送 /馒头限制开关 开 即可生效")
            await event_limit_toggle_cmd.finish("\n".join(lines))
            return

        try:
            enabled = parse_event_limit_toggle(raw)
        except ValueError as error:
            await event_limit_toggle_cmd.finish(str(error))
            return
        try:
            await service.set_event_limit_enabled(enabled)
            per_window = await service.event_limit_per_window()
        except Exception:
            logger.exception("[mantou-affection] 保存事件限制开关失败")
            await event_limit_toggle_cmd.finish("保存失败，请检查数据目录权限后重试。")
            return
        if not enabled:
            await event_limit_toggle_cmd.finish(
                "已关闭事件限制：活动触发次数不再受限制。"
            )
            return
        await event_limit_toggle_cmd.finish(
            f"已开启事件限制：每群每 {EVENT_LIMIT_WINDOW_HOURS} 小时"
            f"最多触发 {per_window} 次活动。\n"
            "可用 /馒头限制次数 N 调整上限"
            f"（{EVENT_LIMIT_MIN_PER_WINDOW}~{EVENT_LIMIT_MAX_PER_WINDOW}）。"
        )

    @event_limit_count_cmd.handle()
    async def handle_event_limit_count(args: Message = CommandArg()) -> None:
        raw = args.extract_plain_text().strip()
        if not raw:
            try:
                current = await service.event_limit_per_window()
                enabled = await service.event_limit_enabled()
            except Exception:
                logger.exception("[mantou-affection] 读取事件限制次数失败")
                await event_limit_count_cmd.finish(
                    "读取失败，请检查数据目录权限后重试。"
                )
                return
            await event_limit_count_cmd.finish(
                f"当前事件限制：每群每 {EVENT_LIMIT_WINDOW_HOURS} 小时"
                f"最多 {current} 次"
                f"（限制{'已开启' if enabled else '已关闭'}）\n"
                f"发送 /馒头限制次数 N 调整，范围 "
                f"{EVENT_LIMIT_MIN_PER_WINDOW}~{EVENT_LIMIT_MAX_PER_WINDOW}。"
            )
            return

        try:
            value = parse_event_limit_count(
                raw,
                minimum=EVENT_LIMIT_MIN_PER_WINDOW,
                maximum=EVENT_LIMIT_MAX_PER_WINDOW,
            )
        except ValueError as error:
            await event_limit_count_cmd.finish(str(error))
            return
        try:
            await service.set_event_limit_per_window(value)
            enabled = await service.event_limit_enabled()
        except Exception:
            logger.exception("[mantou-affection] 保存事件限制次数失败")
            await event_limit_count_cmd.finish("保存失败，请检查数据目录权限后重试。")
            return
        note = "" if enabled else "（限制当前已关闭，发送 /馒头限制开关 开 后生效）"
        await event_limit_count_cmd.finish(
            f"事件限制已调整为每群每 {EVENT_LIMIT_WINDOW_HOURS} 小时 {value} 次{note}"
        )

    @wallpaper_cmd.handle()
    async def handle_wallpaper(args: Message = CommandArg()) -> None:
        raw = args.extract_plain_text().strip()
        fanart = False
        game = None
        for token in raw.split():
            lowered = token.lower()
            if lowered in WALLPAPER_FANART_WORDS:
                fanart = True
                continue
            if lowered in WALLPAPER_OFFICIAL_WORDS:
                fanart = False
                continue
            matched = match_wallpaper_game(token)
            if matched is None or game is not None:
                await wallpaper_cmd.finish(WALLPAPER_USAGE)
                return
            game = matched
        if wallpapers is None:
            await wallpaper_cmd.finish(WALLPAPER_DISABLED_REPLY)
            return
        try:
            wallpaper, data = await wallpapers.draw(game, fanart=fanart)
        except WallpaperUnavailable as error:
            logger.warning(f"[mantou-affection] 手动抽取壁纸失败: {error}")
            await wallpaper_cmd.finish(WALLPAPER_FAILED_REPLY)
            return
        except Exception:
            logger.exception("[mantou-affection] 抽取壁纸出错")
            await wallpaper_cmd.finish(WALLPAPER_FAILED_REPLY)
            return

        label = (
            f"{wallpaper.game} · {wallpaper.collection}"
            if wallpaper.collection
            else wallpaper.game
        )
        title = "随机二创壁纸" if wallpaper.source == "fanart" else "随机壁纸"
        caption = MessageSegment.text(f"🖼️ {title}（{label}）：")
        try:
            await wallpaper_cmd.send(caption + MessageSegment.image(data))
            return
        except Exception:
            logger.exception("[mantou-affection] 壁纸图片发送失败，改用原图直链")
        try:
            await wallpaper_cmd.send(caption + MessageSegment.image(wallpaper.url))
            return
        except Exception:
            logger.exception("[mantou-affection] 壁纸直链发送失败")
        await wallpaper_cmd.finish(
            caption + MessageSegment.text(f"\n下载地址：{wallpaper.url}")
        )

    @image_cmd.handle()
    async def handle_image(
        event: GroupMessageEvent,
        bot: Bot,
        command: tuple[str, ...] = Command(),
        args: Message = CommandArg(),
    ) -> None:
        if image_processing is None:
            await image_cmd.finish("馒头还没把图像处理摆出来，请稍后再试。")
            return
        name = command[0] if command else "馒头图像处理"
        argument = args.extract_plain_text()
        raw = (
            argument
            if name in {"馒头图像处理", "馒头图片处理", "馒头修图", "馒头图片操作"}
            else f"{name} {argument}".strip()
        )
        result = await image_processing.handle(
            group_id=str(event.group_id),
            user_id=str(event.user_id),
            raw=raw,
            event=event,
            bot=bot,
            unlimited=event.get_user_id() in get_driver().config.superusers,
        )
        if result.image is not None:
            await image_cmd.finish(
                MessageSegment.text(result.text or "")
                + MessageSegment.image(result.image)
            )
            return
        await image_cmd.finish(result.text or "图像处理没有完成。")

    return (
        profile_cmd,
        interact_cmd,
        ranking_cmd,
        help_cmd,
        adjust_cmd,
        reset_cmd,
        probability_cmd,
        find_char_cmd,
        bet_cmd,
        pk_probability_cmd,
        pk_bank_cmd,
        wheel_cmd,
        group_quiz_cmd,
        batch_probability_cmd,
        fill_blank_cmd,
        teyvat_quiz_cmd,
        hsr_quiz_cmd,
        zzz_quiz_cmd,
        petit_planet_quiz_cmd,
        event_limit_toggle_cmd,
        event_limit_count_cmd,
        admin_help_cmd,
        wallpaper_cmd,
        image_cmd,
    )
