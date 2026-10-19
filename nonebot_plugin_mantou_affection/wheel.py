from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass(frozen=True)
class WheelOutcome:
    kind: str
    value: int = 0


WHEEL_OUTCOMES: tuple[tuple[WheelOutcome, int], ...] = (
    (WheelOutcome("meme"), 20),
    (WheelOutcome("poke", 1), 12),
    (WheelOutcome("poke", 3), 8),
    (WheelOutcome("poke", 5), 5),
    (WheelOutcome("poke", 10), 4),
    (WheelOutcome("poke", 20), 1),
    (WheelOutcome("affection", 1), 7),
    (WheelOutcome("affection", 2), 6),
    (WheelOutcome("affection", 3), 5),
    (WheelOutcome("affection", 5), 4),
    (WheelOutcome("affection", 10), 3),
    (WheelOutcome("affection", 20), 1),
    (WheelOutcome("affection", -1), 7),
    (WheelOutcome("affection", -2), 6),
    (WheelOutcome("affection", -3), 5),
    (WheelOutcome("affection", -5), 3),
    (WheelOutcome("affection", -10), 2),
    (WheelOutcome("affection", -20), 1),
)


def draw_wheel_outcome(rng: random.Random) -> WheelOutcome:
    roll = rng.randrange(100)
    cursor = 0
    for outcome, weight in WHEEL_OUTCOMES:
        cursor += weight
        if roll < cursor:
            return outcome
    raise AssertionError("转盘权重必须合计 100")


def draw_not_operate_delta(rng: random.Random) -> int:
    return 2 if rng.random() < 1 / 3 else -2
