from __future__ import annotations

from dataclasses import dataclass

from nonebot.adapters.onebot.v11 import Message


@dataclass(frozen=True)
class Adjustment:
    user_id: str
    delta: int


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
