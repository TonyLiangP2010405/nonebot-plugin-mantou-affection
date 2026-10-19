import random

from nonebot_plugin_mantou_affection.wheel import (
    WHEEL_OUTCOMES,
    draw_not_operate_delta,
    draw_wheel_outcome,
)


class FixedRollRng(random.Random):
    def __init__(self, roll: int, value: float = 0.0):
        super().__init__(0)
        self.roll = roll
        self.value = value

    def randrange(self, stop: int) -> int:
        assert stop == 100
        return self.roll

    def random(self) -> float:
        return self.value


def test_wheel_weights_match_approved_table() -> None:
    weighted = [((outcome.kind, outcome.value), weight) for outcome, weight in WHEEL_OUTCOMES]
    assert weighted == [
        (("meme", 0), 20),
        (("poke", 1), 12),
        (("poke", 3), 8),
        (("poke", 5), 5),
        (("poke", 10), 4),
        (("poke", 20), 1),
        (("affection", 1), 7),
        (("affection", 2), 6),
        (("affection", 3), 5),
        (("affection", 5), 4),
        (("affection", 10), 3),
        (("affection", 20), 1),
        (("affection", -1), 7),
        (("affection", -2), 6),
        (("affection", -3), 5),
        (("affection", -5), 3),
        (("affection", -10), 2),
        (("affection", -20), 1),
    ]
    assert sum(weight for _, weight in WHEEL_OUTCOMES) == 100


def test_wheel_draw_uses_independent_integer_ranges() -> None:
    assert draw_wheel_outcome(FixedRollRng(0)).kind == "meme"
    assert draw_wheel_outcome(FixedRollRng(19)).kind == "meme"
    assert draw_wheel_outcome(FixedRollRng(20)).value == 1
    assert draw_wheel_outcome(FixedRollRng(49)).value == 20
    assert draw_wheel_outcome(FixedRollRng(50)).value == 1
    assert draw_wheel_outcome(FixedRollRng(75)).value == 20
    assert draw_wheel_outcome(FixedRollRng(76)).value == -1
    assert draw_wheel_outcome(FixedRollRng(99)).value == -20


def test_not_operate_delta_is_one_third_positive() -> None:
    assert draw_not_operate_delta(FixedRollRng(0, 0.2)) == 2
    assert draw_not_operate_delta(FixedRollRng(0, 0.9)) == -2
