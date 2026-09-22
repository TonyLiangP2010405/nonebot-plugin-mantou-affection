from __future__ import annotations

from nonebot import logger, on_command
from nonebot.adapters import Event
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.rule import Rule

from .config import Config
from .logic import level_for, progress_text
from .parsing import parse_adjustment, parse_probability
from .service import AffectionService, normalize_nickname


async def _group_only(event: Event) -> bool:
    return isinstance(event, GroupMessageEvent)


GROUP_ONLY = Rule(_group_only)


def _nickname(event: GroupMessageEvent) -> str:
    raw = event.sender.card or event.sender.nickname or str(event.user_id)
    return normalize_nickname(raw, str(event.user_id))


def _gain_text(delta: int) -> str:
    return f"好感度 +{delta}" if delta else "好感度没有变化"


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


def register_commands(service: AffectionService, config: Config) -> tuple:
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
        "馒头好感帮助",
        aliases={"馒头好感菜单"},
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
    probability_cmd = on_command(
        "馒头反应概率",
        aliases={"馒头小动作概率"},
        permission=SUPERUSER,
        priority=10,
        block=True,
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
        await help_cmd.finish(
            "🥟 馒头好感度菜单\n"
            "/馒头互动 —— 和馒头玩一会儿\n"
            "/馒头好感 —— 查看自己的好感档案\n"
            "/馒头好感榜 —— 查看本群排行榜\n"
            "/馒头好感调整 @群友 +10 —— SUPERUSER 调整好感"
        )

    @adjust_cmd.handle()
    async def handle_adjust(event: GroupMessageEvent, args: Message = CommandArg()) -> None:
        try:
            adjustment = parse_adjustment(args)
        except ValueError as error:
            await adjust_cmd.finish(str(error))
            return
        try:
            applied, profile = await service.adjust(
                str(event.group_id), adjustment.user_id, "", adjustment.delta
            )
        except Exception:
            logger.exception("[mantou-affection] 管理员调整好感度失败")
            await adjust_cmd.finish("调整失败，请检查数据目录权限后重试。")
            return
        message = MessageSegment.at(adjustment.user_id) + MessageSegment.text(
            f" 好感度已调整 {applied:+d}，当前为 {profile.affection}。"
        )
        await adjust_cmd.finish(message)

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

    return profile_cmd, interact_cmd, ranking_cmd, help_cmd, adjust_cmd, probability_cmd
