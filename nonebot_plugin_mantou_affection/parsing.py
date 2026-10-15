from __future__ import annotations

from dataclasses import dataclass

from nonebot.adapters.onebot.v11 import Message


@dataclass(frozen=True)
class Adjustment:
    user_id: str
    delta: int


PROBABILITY_HINT = "请输入 0~1 的小数（如 0.05）或百分数（如 5%）"
FIND_CHAR_CHANCE_USAGE = (
    "只写一个值会同时设置群消息和戳一戳，分开设置写成 /馒头找字概率 群消息 5% 戳一戳 2%"
)
FIND_CHAR_CHANCE_HINT = f"{PROBABILITY_HINT}。{FIND_CHAR_CHANCE_USAGE}"

FIND_CHAR_CHANCE_KEYS = {
    "群消息": "group",
    "群聊": "group",
    "群": "group",
    "group": "group",
    "戳一戳": "poke",
    "戳戳": "poke",
    "戳": "poke",
    "poke": "poke",
}


@dataclass(frozen=True)
class FindCharChances:
    """找字小游戏概率的调整请求，None 表示这一项不动。"""

    group: float | None
    poke: float | None


def parse_find_char_chances(text: str) -> FindCharChances:
    """解析找字概率参数：一个数值同时设置两项，或按「群消息 / 戳一戳」分别设置。"""

    tokens = text.replace("=", " ").replace("：", " ").split()
    if not tokens:
        raise ValueError(FIND_CHAR_CHANCE_HINT)
    if len(tokens) == 1:
        value = _parse_find_char_value(tokens[0])
        return FindCharChances(value, value)

    group: float | None = None
    poke: float | None = None
    index = 0
    while index < len(tokens):
        key = FIND_CHAR_CHANCE_KEYS.get(tokens[index].lower())
        if key is None or index + 1 >= len(tokens):
            raise ValueError(FIND_CHAR_CHANCE_HINT)
        value = _parse_find_char_value(tokens[index + 1])
        if key == "group":
            group = value
        else:
            poke = value
        index += 2
    return FindCharChances(group, poke)


def _parse_find_char_value(token: str) -> float:
    """概率解析失败时换成带用法的提示，让命令回复直接说明怎么设置。"""

    try:
        return parse_probability(token)
    except ValueError as error:
        raise ValueError(FIND_CHAR_CHANCE_HINT) from error


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
