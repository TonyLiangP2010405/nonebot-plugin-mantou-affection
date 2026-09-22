from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nonebot import logger

EVENT_DELTAS = (-2, 1, 2)
OPTION_COUNT = 3


@dataclass(frozen=True)
class EventOption:
    text: str
    delta: int


@dataclass(frozen=True)
class Event:
    text: str
    options: tuple[EventOption, ...]


class EventLibrary:
    """加载随机事件库，非法条目跳过并记录警告。"""

    def __init__(self, path: Path):
        self.path = path
        self._events = self._load_file(path)

    @property
    def events(self) -> tuple[Event, ...]:
        return self._events

    @staticmethod
    def _load_file(path: Path) -> tuple[Event, ...]:
        try:
            with path.open("r", encoding="utf-8") as file:
                return EventLibrary._normalize(json.load(file))
        except (OSError, json.JSONDecodeError, ValueError) as error:
            logger.warning(f"[mantou-affection] 加载随机事件库失败，暂时关闭随机事件: {error}")
            return ()

    @staticmethod
    def _normalize_option(raw: Any) -> EventOption | None:
        if not isinstance(raw, dict):
            return None
        text = str(raw.get("text", "")).strip()
        delta = raw.get("delta")
        if not text or isinstance(delta, bool) or not isinstance(delta, int):
            return None
        if delta not in EVENT_DELTAS:
            return None
        return EventOption(text, delta)

    @classmethod
    def _normalize(cls, data: Any) -> tuple[Event, ...]:
        raw_events = data.get("events") if isinstance(data, dict) else None
        if not isinstance(raw_events, list):
            raise ValueError("事件库根节点必须是含 events 数组的 JSON 对象")

        events: list[Event] = []
        for index, raw in enumerate(raw_events):
            if not isinstance(raw, dict):
                logger.warning(f"[mantou-affection] 跳过第 {index + 1} 条随机事件：不是对象")
                continue
            text = str(raw.get("text", "")).strip()
            raw_options = raw.get("options")
            options = (
                [cls._normalize_option(option) for option in raw_options]
                if isinstance(raw_options, list)
                else []
            )
            valid = [option for option in options if option is not None]
            if not text or len(valid) != OPTION_COUNT:
                logger.warning(
                    f"[mantou-affection] 跳过第 {index + 1} 条随机事件：缺少场景或选项"
                )
                continue
            if sorted(option.delta for option in valid) != sorted(EVENT_DELTAS):
                logger.warning(
                    f"[mantou-affection] 跳过第 {index + 1} 条随机事件："
                    f"选项数值必须是 {EVENT_DELTAS}"
                )
                continue
            events.append(Event(text, tuple(valid)))
        return tuple(events)

    def pick(self) -> Event | None:
        if not self._events:
            return None
        return random.SystemRandom().choice(self._events)

    @staticmethod
    def shuffled_options(event: Event) -> tuple[EventOption, ...]:
        return tuple(random.SystemRandom().sample(event.options, len(event.options)))
