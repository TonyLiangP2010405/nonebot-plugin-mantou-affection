from __future__ import annotations

import math
from dataclasses import dataclass

from nonebot.adapters.onebot.v11 import Message


@dataclass(frozen=True)
class Adjustment:
    user_id: str
    delta: int


PROBABILITY_HINT = "请输入 0~1 的小数（如 0.05）或百分数（如 5%）"

DURATION_HINT = "请输入秒数（如 1800）或带单位的时间（如 30分钟、1小时）"
COUNT_HINT = "请输入 {minimum}~{maximum} 之间的整数"
_DURATION_UNITS: dict[str, int] = {
    "秒": 1,
    "分钟": 60,
    "分": 60,
    "小时": 3600,
    "时": 3600,
    "天": 86400,
}


def parse_duration(text: str) -> int:
    """把 "1800" / "30分钟" / "1小时" 解析成秒数。"""

    raw = text.strip()
    if not raw:
        raise ValueError(DURATION_HINT)
    unit = 1
    numeric = raw
    for suffix, seconds in _DURATION_UNITS.items():
        if raw.endswith(suffix):
            unit = seconds
            numeric = raw[: -len(suffix)].strip()
            break
    try:
        value = int(numeric)
    except ValueError as error:
        raise ValueError(DURATION_HINT) from error
    if value < 0:
        raise ValueError(DURATION_HINT)
    return value * unit


def parse_message_count(text: str, *, minimum: int = 1, maximum: int = 1000) -> int:
    """解析「最近 N 条群消息」里的 N。"""

    hint = COUNT_HINT.format(minimum=minimum, maximum=maximum)
    try:
        value = int(text.strip())
    except ValueError as error:
        raise ValueError(hint) from error
    if not minimum <= value <= maximum:
        raise ValueError(hint)
    return value


def parse_adjustment(message: Message) -> Adjustment:
    target = ""
    for segment in message:
        if segment.type != "at":
            continue
        candidate = str(segment.data.get("qq", ""))
        if candidate and candidate != "all":
            target = candidate
            break
    if not target:
        raise ValueError("请先 @要调整好感度的群友")

    value = message.extract_plain_text().strip()
    if not value:
        raise ValueError("请填写调整数值，例如：/馒头好感调整 @群友 +10")
    try:
        delta = int(value)
    except ValueError as error:
        raise ValueError("调整数值必须是整数，例如 +10 或 -5") from error
    if delta == 0 or abs(delta) > 9999:
        raise ValueError("单次调整范围为 -9999 到 9999，且不能为 0")
    return Adjustment(target, delta)


def parse_probability(text: str) -> float:
    """把 "0.05" 或 "5%" 解析成 0~1 的概率。"""

    raw = text.strip()
    if not raw:
        raise ValueError(PROBABILITY_HINT)

    percent = raw.endswith("%")
    numeric = raw[:-1].strip() if percent else raw
    try:
        value = float(numeric)
    except ValueError as error:
        raise ValueError(PROBABILITY_HINT) from error

    if percent:
        value /= 100
    if not 0.0 <= value <= 1.0:
        raise ValueError(PROBABILITY_HINT)
    return value


PROBABILITY_BATCH_SET = "set"
PROBABILITY_BATCH_SCALE = "scale"
PROBABILITY_BATCH_RESET = "reset"
MAX_PROBABILITY_SCALE = 1000.0
PROBABILITY_BATCH_HINT = (
    "请输入 0~1 的小数（如 0.0005）、百分数（如 0.05%）、"
    "倍率（如 ×2、×50%）或「重置」"
)
PROBABILITY_RESET_WORDS = {"重置", "恢复默认", "默认", "reset", "default"}
SCALE_PREFIXES = ("×", "x", "X", "*", "＊", "✕")


@dataclass(frozen=True)
class ProbabilityBatch:
    """批量概率调整请求：统一设值、按倍率缩放或恢复默认。"""

    mode: str
    value: float = 0.0


def _parse_scale_factor(text: str) -> float:
    raw = text.strip()
    if raw.endswith("倍"):
        raw = raw[:-1].strip()
    if not raw:
        raise ValueError(PROBABILITY_BATCH_HINT)

    percent = raw.endswith("%")
    numeric = raw[:-1].strip() if percent else raw
    try:
        value = float(numeric)
    except ValueError as error:
        raise ValueError(PROBABILITY_BATCH_HINT) from error

    if percent:
        value /= 100
    if not math.isfinite(value) or not 0.0 <= value <= MAX_PROBABILITY_SCALE:
        raise ValueError(PROBABILITY_BATCH_HINT)
    return value


def parse_probability_batch(text: str) -> ProbabilityBatch:
    """解析批量概率命令：数值统一设值、×/x/* 前缀缩放、指定词重置。"""

    raw = text.strip()
    if not raw:
        raise ValueError(PROBABILITY_BATCH_HINT)
    if raw.lower() in PROBABILITY_RESET_WORDS:
        return ProbabilityBatch(PROBABILITY_BATCH_RESET)
    if raw.startswith(SCALE_PREFIXES):
        return ProbabilityBatch(
            PROBABILITY_BATCH_SCALE, _parse_scale_factor(raw[1:])
        )
    return ProbabilityBatch(PROBABILITY_BATCH_SET, parse_probability(raw))


EVENT_LIMIT_TOGGLE_HINT = "请回复「开」或「关」，例如：/馒头限制开关 开"
EVENT_LIMIT_ON_WORDS = frozenset({"开", "开启", "打开", "启用", "on", "true", "1", "是"})
EVENT_LIMIT_OFF_WORDS = frozenset({"关", "关闭", "关掉", "禁用", "off", "false", "0", "否"})


def parse_event_limit_toggle(text: str) -> bool:
    """解析限流开关参数：开/关，也接受开启、关闭、on/off、1/0。"""

    raw = text.strip().lower()
    if raw in EVENT_LIMIT_ON_WORDS:
        return True
    if raw in EVENT_LIMIT_OFF_WORDS:
        return False
    raise ValueError(EVENT_LIMIT_TOGGLE_HINT)


def parse_event_limit_count(text: str, *, minimum: int, maximum: int) -> int:
    """解析每群每 2 小时的次数上限，超出范围时回复带范围的提示。"""

    hint = f"请输入 {minimum}~{maximum} 之间的整数，例如：/馒头限制次数 5"
    try:
        value = int(text.strip())
    except ValueError as error:
        raise ValueError(hint) from error
    if not minimum <= value <= maximum:
        raise ValueError(hint)
    return value
