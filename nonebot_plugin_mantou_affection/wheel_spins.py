"""转盘「再来一次」次数：按群按人记账，并用 /馒头再来一次 自己单独转一轮。"""
from __future__ import annotations

from nonebot import logger, on_command
from nonebot.adapters import Event
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent
from nonebot.rule import Rule

from .image_processing import ImageQuotaStore

SPIN_OUTCOME_NAMES: dict[int, str] = {
    1: "再来一次",
    2: "再来两次",
    3: "再来三次",
}
REROLL_COMMAND = "馒头再来一次"
REROLL_ALIASES = frozenset({"馒头自己转", "馒头单飞"})
REROLL_NONE_REPLY = (
    "你还没有转盘次数。先在群里抽到「再来一次」吧，中奖后按提示再发这个命令。"
)
REROLL_BUSY_REPLY = "本群还有活动没结束，等它结束再单独转吧。"
REROLL_UNAVAILABLE_REPLY = "转盘次数账本没打开，这次单独转不了。"
REROLL_FAILED_REPLY = "这次单独转盘没能开起来，次数没有扣除。"
SOLO_JOIN_REPLY = "本轮是「再来一次」的个人转盘，只有中奖的群友能转，其他人不能参与。"


class WheelSpinQuotaStore(ImageQuotaStore):
    """按群、按群友记录转盘「再来一次」次数（与其它次数账本分开落盘）。"""


async def _group_only(event: Event) -> bool:
    return isinstance(event, GroupMessageEvent)


GROUP_ONLY = Rule(_group_only)


def register_spin_commands(coordinator) -> tuple:
    """注册 /馒头再来一次：消耗 1 次记录，立刻自己单独开转一轮。"""

    reroll_cmd = on_command(
        REROLL_COMMAND,
        aliases=set(REROLL_ALIASES),
        rule=GROUP_ONLY,
        priority=10,
        block=True,
    )

    @reroll_cmd.handle()
    async def handle_reroll(bot: Bot, event: GroupMessageEvent) -> None:
        nickname = event.sender.card or event.sender.nickname or str(event.user_id)
        try:
            status = await coordinator.spin_personal(
                group_id=str(event.group_id),
                user_id=str(event.user_id),
                nickname=nickname,
                bot=bot,
                send=reroll_cmd.send,
            )
        except Exception:
            logger.exception("[mantou-affection] 个人转盘开转失败")
            await reroll_cmd.finish(REROLL_FAILED_REPLY)
            return
        if status == "ok":
            return
        if status == "empty":
            await reroll_cmd.finish(REROLL_NONE_REPLY)
        elif status == "busy":
            await reroll_cmd.finish(REROLL_BUSY_REPLY)
        elif status == "unavailable":
            await reroll_cmd.finish(REROLL_UNAVAILABLE_REPLY)
        else:
            await reroll_cmd.finish(REROLL_FAILED_REPLY)

    return (reroll_cmd,)
