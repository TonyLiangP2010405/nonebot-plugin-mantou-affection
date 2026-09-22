from __future__ import annotations

from nonebot import logger
from nonebot.adapters import Event
from nonebot.adapters.onebot.v11 import GroupMessageEvent
from nonebot.consts import (
    ENDSWITH_KEY,
    FULLMATCH_KEY,
    KEYWORD_KEY,
    PREFIX_KEY,
    REGEX_MATCHED,
    STARTSWITH_KEY,
)
from nonebot.matcher import Matcher
from nonebot.message import run_postprocessor

from .config import Config
from .service import AffectionService, normalize_nickname

SELF_PLUGIN = "nonebot_plugin_mantou_affection"
MEANINGFUL_STATE_KEYS = {
    PREFIX_KEY,
    REGEX_MATCHED,
    FULLMATCH_KEY,
    STARTSWITH_KEY,
    ENDSWITH_KEY,
    KEYWORD_KEY,
}


def is_meaningful_trigger(state: dict) -> bool:
    """只奖励有明确命令/正则/完整匹配的 Matcher，忽略普通消息监听器。"""

    return any(key in state for key in MEANINGFUL_STATE_KEYS)


def matcher_source(matcher: Matcher) -> str:
    plugin_name = matcher.plugin_name or ""
    if plugin_name:
        return plugin_name
    module_name = matcher.module_name or ""
    return module_name.split(".", 1)[0]


def register_plugin_linkage(service: AffectionService, config: Config):
    @run_postprocessor
    async def reward_after_plugin(
        matcher: Matcher,
        event: Event,
        exception=None,
    ) -> None:
        if exception is not None or not config.mantou_affection_link_enabled:
            return
        if not isinstance(event, GroupMessageEvent):
            return
        source = matcher_source(matcher)
        if not source or source == SELF_PLUGIN:
            return
        reward = config.mantou_affection_link_rewards.get(source, 0)
        if reward == 0 or not is_meaningful_trigger(matcher.state):
            return
        nickname = event.sender.card or event.sender.nickname or str(event.user_id)
        try:
            result = await service.reward_external(
                str(event.group_id),
                str(event.user_id),
                normalize_nickname(nickname, str(event.user_id)),
                source,
                reward,
            )
        except Exception:
            logger.exception(f"[mantou-affection] 处理插件联动失败: {source}")
            return
        if result.accepted and result.delta:
            logger.debug(
                f"[mantou-affection] {source} 联动奖励: "
                f"group={event.group_id} user={event.user_id} delta={result.delta}"
            )

    return reward_after_plugin
