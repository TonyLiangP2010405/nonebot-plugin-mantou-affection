from __future__ import annotations

from nonebot import logger, on_message
from nonebot.adapters.onebot.v11 import GroupMessageEvent, MessageSegment

from .commands import GROUP_ONLY
from .config import Config
from .service import AffectionService


def register_ambient(service: AffectionService, config: Config):
    """群友发言时按概率让馒头冒出一个与发言内容无关的小动作。"""

    ambient = on_message(rule=GROUP_ONLY, priority=90, block=False)

    @ambient.handle()
    async def handle_ambient(event: GroupMessageEvent) -> None:
        try:
            text = await service.ambient_reaction(str(event.group_id), str(event.user_id))
        except Exception:
            logger.exception("[mantou-affection] 抽取馒头小动作失败")
            return
        if not text:
            return
        try:
            await ambient.send(
                MessageSegment.at(event.user_id) + MessageSegment.text(f" {text}")
            )
        except Exception:
            logger.exception("[mantou-affection] 发送馒头小动作失败")

    return ambient
