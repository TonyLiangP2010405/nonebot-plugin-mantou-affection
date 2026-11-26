from __future__ import annotations

import asyncio
from pathlib import Path

from nonebot import get_driver, logger, on_command
from nonebot.adapters import Event
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageSegment
from nonebot.params import Command, CommandArg
from nonebot.permission import SUPERUSER
from nonebot.rule import Rule

from .config import Config
from .event_wheel import EVENT_WHEEL_SLOTS
from .image_processing import ImageProcessingCoordinator
from .logic import level_for, progress_text
from .parsing import (
    MEME_GROUP_LIST_WORDS,
    PROBABILITY_BATCH_RESET,
    PROBABILITY_BATCH_SCALE,
    parse_adjustment,
    parse_event_limit_count,
    parse_event_limit_toggle,
    parse_meme_switch_args,
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
from .wheel_stickers import StickerAssetMissing, draw_local_sticker
from .wheel_wallpapers import (
    WallpaperLibrary,
    WallpaperUnavailable,
    match_wallpaper_game,
)
from .whoami import (
    WHOAMI_BUSY_REPLY,
    WHOAMI_LIMIT_REPLY,
    WHOAMI_UNAVAILABLE_REPLY,
    WhoAmICoordinator,
)

EVENT_WHEEL_CHANCE_USAGE = (
    "请输入 0~1 的小数（如 0.015）或百分数（如 1.5%），0 即关闭事件转盘"
)
WHEEL_CHANCE_USAGE = (
    "请输入 0~1 的小数或百分数（如 0.1%），0 即关闭馒头转盘的独立触发"
)
WALLPAPER_USAGE = (
    "用法：/馒头壁纸 [官方/二创] [原神/星铁/绝区零]\n"
    "不带参数抽官方壁纸；加「二创」改成抽二创图，可再跟一个游戏名"
)
WALLPAPER_FANART_WORDS = frozenset({"二创", "同人", "fanart", "fan"})
WALLPAPER_OFFICIAL_WORDS = frozenset({"官方", "official"})
WALLPAPER_FAILED_REPLY = "这次没取到壁纸：图源暂时连不上，稍后再试。"
WALLPAPER_DISABLED_REPLY = "壁纸库没有接线，暂时抽不了。"
STICKER_TEST_UNSET_REPLY = (
    "还没配置馒头表情包目录。\n"
    "请在机器人项目的 .env 里加一行（绝对路径，用引号包住）：\n"
    'MANTOU_AFFECTION_WHEEL_STICKER_DIR="/srv/bot/表情包/馒头"\n'
    "改完重启 NoneBot 再发一次。"
)
STICKER_TEST_RELATIVE_REPLY = (
    "配置的目录不是绝对路径：{directory}\n"
    "请改成从 / 开头的绝对路径，重启后生效。"
)
STICKER_TEST_FAILED_REPLY = (
    "读取馒头表情包目录失败：{reason}\n"
    "目录：{directory}\n"
    "检查目录是否存在、里面有没有 PNG/JPG/JPEG/GIF/WebP 图片，"
    "以及 bot 进程有没有读权限。"
)
STICKER_TEST_ERROR_REPLY = "读取馒头表情包目录时出错，详情见日志。"
STICKER_TEST_OK_REPLY = (
    "🥟 馒头表情包自检通过：目录里共 {count} 张，本次抽中 {name}\n"
    "目录：{directory}"
)
STICKER_TEST_SEND_FAILED_REPLY = (
    "读到了 {name}（{size} 字节），但发送图片失败，详情见日志。"
)
MEME_SWITCH_USAGE = (
    "用法：\n"
    "/馒头表情开关 —— 查看本群状态和已开启的群\n"
    "/馒头表情开关 开 —— 开启本群随机表情（群聊）\n"
    "/馒头表情开关 关 —— 关闭本群随机表情（群聊）\n"
    "/馒头表情开关 开 123456789 234567890 —— 按群号开启，可写多个（私聊/群聊）\n"
    "/馒头表情开关 123456789 关 —— 群号写在前面也认\n"
    "/馒头表情开关 列表 —— 列出所有已开启的群"
)
MEME_SWITCH_PRIVATE_HINT = (
    "私聊里请带上群号，例如：/馒头表情开关 开 123456789"
)
MEME_SWITCH_READ_FAILED = "读取失败，请检查数据目录权限后重试。"
MEME_SWITCH_SAVE_FAILED = "保存失败，请检查数据目录权限后重试。"
PROBABILITY_BATCH_USAGE = (
    "用法：\n"
    "/馒头概率总调 1.5% —— 事件转盘和馒头转盘统一设为该值\n"
    "/馒头概率总调 ×2 —— 两项按当前值缩放（支持 ×50%）\n"
    "/馒头概率总调 重置 —— 清除覆盖，恢复配置默认"
)
MEMBER_HELP_LINES: tuple[str, ...] = (
    "/馒头互动 —— 陪馒头玩，攒好感",
    "/馒头好感 —— 查看自己的好感档案",
    "/馒头好感榜 —— 查看本群排行榜",
    "/馒头图像处理 —— 处理图片并保持动图，支持 GIF 变速（回复图片后使用）",
    "/翻转 /旋转 90 /gif变速 0.5x —— 回复图片后直接用的图像处理快捷键",
    "/馒头再来一次 —— 用抽到的转盘次数自己单独再转一轮（其他人不能参与）",
    "/馒头我是谁 —— 看灰度服饰细节猜角色，答对 +5 好感",
    "/馒头帮助 —— 打开这份菜单",
)
ADMIN_HELP_LINES: tuple[str, ...] = (
    "/馒头好感调整 @群友 +10 —— 手动增减好感",
    "/馒头好感重置 确认 —— 清空所有群的好感数据",
    "/馒头事件转盘概率 1.5% —— 查看或调整事件转盘概率"
    "（13 项活动的统一入口）",
    "/馒头转盘概率 0.1% —— 查看或调整命运齿轮的独立触发概率",
    "/馒头问答题库 —— 查看题库余量",
    "/馒头概率总调 —— 总览事件转盘和馒头转盘两个概率；带数值统一设置，"
    "×倍率缩放，重置恢复默认",
    "/馒头壁纸 [官方/二创] [原神/星铁/绝区零] —— 手动抽一张壁纸",
    "/馒头表情包 —— 从配置目录抽一张，自检表情包素材是否可用",
    "/馒头表情开关 [开/关] [群号] —— 按群开关随机表情；私聊带群号"
    "（如 开 123456789），默认关闭",
    "/馒头限制开关 [开/关] —— 查看或切换事件限制（默认关闭）",
    f"/馒头限制次数 5 —— 查看或调整每群每 {EVENT_LIMIT_WINDOW_HOURS} 小时"
    "最多触发几次活动（1~100）",
    "/馒头管理帮助 —— 查看这份完整命令帮助",
    "/馒头强制事件 @群友 —— 回复 1~13 强制开一场活动",
)
HELP_FOOTER = (
    "提示：群消息的随机活动统一由「事件转盘」触发，转到哪一项就开哪一项，"
    "命运齿轮转盘另有独立触发；概率命令不带参数就查看当前值，0 即关闭；"
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
    return f"每天 {start:02d}:00~{end:02d}:00（命运齿轮转盘的独立触发不受时段限制）"


def _event_wheel_status_line(service: AffectionService, config: Config) -> str:
    """事件转盘的状态行：开启时段 + 现在到底会不会触发。"""

    hours = _activity_hours_text(config)
    try:
        open_now = service.activity_hours_open()
    except Exception:
        logger.exception("[mantou-affection] 判定活动开启时段失败")
        open_now = True
    if open_now:
        return f"活动开启时段：{hours}（现在在时段内）"
    return f"活动开启时段：{hours}｜现在不在时段内，事件转盘不会触发"


async def _event_limit_status_line(service: AffectionService, event: Event | None) -> str:
    """事件限流状态行；私聊里没有群号就只报开关和上限。"""

    group_id = str(event.group_id) if isinstance(event, GroupMessageEvent) else ""
    try:
        state = await service.event_limit_state(group_id)
    except Exception:
        logger.exception("[mantou-affection] 读取事件限制失败")
        return ""
    if not state.enabled:
        return "事件限制：已关闭（自动活动不受次数限制）"
    window = EVENT_LIMIT_WINDOW_HOURS
    if group_id:
        return (
            f"事件限制：已开启，每群每 {window} 小时最多 {state.per_window} 次，"
            f"本群已用 {state.used} 次、剩余 {state.remaining} 次"
        )
    return f"事件限制：已开启，每群每 {window} 小时最多 {state.per_window} 次"


def _format_factor(value: float) -> str:
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return text or "0"


def register_commands(
    service: AffectionService,
    config: Config,
    pk: PkCoordinator | None = None,
    image_processing: ImageProcessingCoordinator | None = None,
    wallpapers: WallpaperLibrary | None = None,
    whoami: WhoAmICoordinator | None = None,
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
    pk_bank_cmd = on_command(
        "馒头问答题库",
        permission=SUPERUSER,
        priority=10,
        block=True,
    )
    event_wheel_cmd = on_command(
        "馒头事件转盘概率",
        aliases={"馒头事件转盘触发概率", "馒头事件概率"},
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
    whoami_cmd = on_command(
        "馒头我是谁",
        aliases={"馒头猜猜我是谁", "馒头猜角色", "馒头猜猜看"},
        rule=GROUP_ONLY,
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
    sticker_test_cmd = on_command(
        "馒头表情包",
        aliases={"馒头表情包自检"},
        permission=SUPERUSER,
        priority=10,
        block=True,
    )
    meme_switch_cmd = on_command(
        "馒头表情开关",
        aliases={"馒头随机表情开关", "馒头表情包开关"},
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
            lines.append(_event_wheel_status_line(service, config))
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

    @event_wheel_cmd.handle()
    async def handle_event_wheel_chance(
        event: Event, args: Message = CommandArg()
    ) -> None:
        raw = args.extract_plain_text().strip()
        if not raw:
            try:
                current = await service.event_wheel_probability()
                override = await service.event_wheel_probability_override()
            except Exception:
                logger.exception("[mantou-affection] 读取事件转盘概率失败")
                await event_wheel_cmd.finish("读取失败，请检查数据目录权限后重试。")
                return
            default = config.mantou_affection_event_wheel_chance
            source = (
                f"配置默认 {_format_probability(default)}"
                if override is None
                else f"运行时覆盖，配置默认 {_format_probability(default)}"
            )
            slot_lines = "\n".join(
                f"{index}. {slot.label}"
                for index, slot in enumerate(EVENT_WHEEL_SLOTS, start=1)
            )
            lines = [
                f"当前事件转盘概率：{_format_probability(current)}（{source}）",
                f"转盘里 {len(EVENT_WHEEL_SLOTS)} 项，每次只转当前能开的那几项、等概率：",
                slot_lines,
                _event_wheel_status_line(service, config),
            ]
            limit_line = await _event_limit_status_line(service, event)
            if limit_line:
                lines.append(limit_line)
            lines.append("馒头转盘另有独立触发，可用 /馒头转盘概率 查看。")
            lines.append(f"用法：{EVENT_WHEEL_CHANCE_USAGE}")
            await event_wheel_cmd.finish("\n".join(lines))
            return
        try:
            value = parse_probability(raw)
        except ValueError as error:
            await event_wheel_cmd.finish(f"{error}。{EVENT_WHEEL_CHANCE_USAGE}")
            return
        try:
            await service.set_event_wheel_probability(value)
        except Exception:
            logger.exception("[mantou-affection] 保存事件转盘概率失败")
            await event_wheel_cmd.finish("保存失败，请检查数据目录权限后重试。")
            return
        note = ""
        if value > 0 and not service.activity_hours_open():
            note = (
                "\n注意：现在不在活动开启时段内，事件转盘要等时段开启才会触发"
                "（可用 MANTOU_AFFECTION_ACTIVE_HOURS_* 调整，或先把时段限制关掉）。"
            )
        await event_wheel_cmd.finish(
            f"事件转盘概率已调整为 {_format_probability(value)}{note}"
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
                "这个概率和事件转盘互相独立：馒头转盘既可能被事件转盘转到，"
                "也会按这个概率单独触发。\n"
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

    @sticker_test_cmd.handle()
    async def handle_sticker_test() -> None:
        directory = config.mantou_affection_wheel_sticker_dir.strip()
        if not directory:
            await sticker_test_cmd.finish(STICKER_TEST_UNSET_REPLY)
            return
        if not Path(directory).is_absolute():
            await sticker_test_cmd.finish(
                STICKER_TEST_RELATIVE_REPLY.format(directory=directory)
            )
            return
        try:
            picked = await asyncio.to_thread(draw_local_sticker, directory)
        except StickerAssetMissing as error:
            logger.warning(f"[mantou-affection] 馒头表情包目录自检未通过: {error}")
            await sticker_test_cmd.finish(
                STICKER_TEST_FAILED_REPLY.format(reason=error, directory=directory)
            )
            return
        except Exception:
            logger.exception("[mantou-affection] 馒头表情包目录自检出错")
            await sticker_test_cmd.finish(STICKER_TEST_ERROR_REPLY)
            return

        caption = STICKER_TEST_OK_REPLY.format(
            count=len(picked.images), name=picked.path.name, directory=directory
        )
        try:
            await sticker_test_cmd.send(
                MessageSegment.text(caption) + MessageSegment.image(picked.data)
            )
        except Exception:
            logger.exception("[mantou-affection] 馒头表情包自检图片发送失败")
            await sticker_test_cmd.finish(
                STICKER_TEST_SEND_FAILED_REPLY.format(
                    name=picked.path.name, size=len(picked.data)
                )
            )

    @meme_switch_cmd.handle()
    async def handle_meme_switch(event: Event, args: Message = CommandArg()) -> None:
        raw = args.extract_plain_text().strip()
        group_id = str(event.group_id) if isinstance(event, GroupMessageEvent) else ""
        if not raw or raw.lower() in MEME_GROUP_LIST_WORDS:
            try:
                groups = await service.meme_enabled_groups()
            except Exception:
                logger.exception("[mantou-affection] 读取表情包群开关失败")
                await meme_switch_cmd.finish(MEME_SWITCH_READ_FAILED)
                return
            if group_id:
                state = "本群已开启" if group_id in groups else "本群未开启"
                lines = [f"🎭 表情包开关：{state}"]
            else:
                lines = ["🎭 表情包开关（私聊可带群号配置）"]
            lines.append(f"已开启的群：{len(groups)} 个")
            shown = groups[:20]
            lines.extend(f"  · {item}" for item in shown)
            if len(groups) > len(shown):
                lines.append(f"  · …等 {len(groups)} 个")
            lines.append("只有开启的群，命运齿轮才会抽到「馒头制作随机表情」。")
            if group_id:
                hint = (
                    "/馒头表情开关 关 可关闭本群"
                    if group_id in groups
                    else "/馒头表情开关 开 可开启本群"
                )
                lines.append(f"发送 {hint}。")
            lines.append(MEME_SWITCH_USAGE)
            await meme_switch_cmd.finish("\n".join(lines))
            return

        try:
            enabled, targets = parse_meme_switch_args(raw)
        except ValueError as error:
            await meme_switch_cmd.finish(f"{error}\n{MEME_SWITCH_USAGE}")
            return
        if enabled is None:
            await meme_switch_cmd.finish(MEME_SWITCH_USAGE)
            return
        if not targets:
            if not group_id:
                await meme_switch_cmd.finish(MEME_SWITCH_PRIVATE_HINT)
                return
            targets = (group_id,)
        try:
            groups = await service.set_meme_groups_enabled(targets, enabled)
        except Exception:
            logger.exception("[mantou-affection] 保存表情包群开关失败")
            await meme_switch_cmd.finish(MEME_SWITCH_SAVE_FAILED)
            return
        verb = "开启" if enabled else "关闭"
        if len(targets) == 1 and targets[0] == group_id:
            headline = f"已{verb}本群的随机表情"
        elif len(targets) == 1:
            headline = f"已{verb}群 {targets[0]} 的随机表情"
        else:
            headline = f"已{verb} {len(targets)} 个群的随机表情：" + "、".join(targets)
        detail = (
            "命运齿轮会重新抽到「馒头制作随机表情」。"
            if enabled
            else "这些群的转盘不再抽到「馒头制作随机表情」。"
        )
        await meme_switch_cmd.finish(
            f"{headline}\n{detail}\n当前共 {len(groups)} 个群开启。"
        )

    @whoami_cmd.handle()
    async def handle_whoami(event: GroupMessageEvent) -> None:
        if whoami is None:
            await whoami_cmd.finish(WHOAMI_UNAVAILABLE_REPLY)
            return
        group_id = str(event.group_id)
        try:
            allowed = await service.event_limit_allows(group_id)
        except Exception:
            logger.exception("[mantou-affection] 判定事件限制失败")
            allowed = True
        if not allowed:
            await whoami_cmd.finish(WHOAMI_LIMIT_REPLY)
            return
        nickname = event.sender.card or event.sender.nickname or ""
        started = await whoami.start(
            group_id=group_id,
            user_id=str(event.user_id),
            nickname=nickname,
            send=whoami_cmd.send,
        )
        if not started:
            await whoami_cmd.finish(
                WHOAMI_BUSY_REPLY
                if whoami.busy(group_id)
                else WHOAMI_UNAVAILABLE_REPLY
            )
            return
        try:
            await service.record_event_trigger(group_id)
        except Exception:
            logger.exception("[mantou-affection] 记录事件限制失败")

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
        event_wheel_cmd,
        wheel_cmd,
        pk_bank_cmd,
        batch_probability_cmd,
        event_limit_toggle_cmd,
        event_limit_count_cmd,
        admin_help_cmd,
        wallpaper_cmd,
        sticker_test_cmd,
        meme_switch_cmd,
        whoami_cmd,
        image_cmd,
    )
