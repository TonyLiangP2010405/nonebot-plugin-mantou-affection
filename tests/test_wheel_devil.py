from __future__ import annotations

import random

import pytest

from nonebot_plugin_mantou_affection.config import Config
from nonebot_plugin_mantou_affection.wheel import (
    DEVIL_SPEC,
    DEVIL_WHEEL_OUTCOMES,
    NORMAL_SPEC,
    WheelCoordinator,
    draw_wheel_outcome,
    format_devil_wheel_rules,
)


def test_devil_weights_match_approved_table() -> None:
    weighted = [((o.kind, o.value), w) for o, w in DEVIL_WHEEL_OUTCOMES]
    assert weighted == [
        (("meme", 0), 10),
        (("taozi", 0), 25),
        (("corpus", 0), 10),
        (("affection", 4), 4),
        (("affection", 8), 2),
        (("affection", 12), 2),
        (("affection", 20), 1),
        (("affection", 40), 1),
        (("affection", -4), 13),
        (("affection", -8), 11),
        (("affection", -12), 8),
        (("affection", -20), 7),
        (("affection", -40), 4),
        (("affection", -60), 2),
    ]
    assert sum(w for _o, w in DEVIL_WHEEL_OUTCOMES) == 100


def test_devil_spec_differs_from_normal_only_where_designed() -> None:
    assert NORMAL_SPEC.auto_start is False
    assert NORMAL_SPEC.refund is True
    assert NORMAL_SPEC.max_participants == 4
    assert NORMAL_SPEC.join_cost == 2
    assert NORMAL_SPEC.taozi_cost == 10
    assert NORMAL_SPEC.meme_candidates == 1
    assert DEVIL_SPEC.auto_start is True
    assert DEVIL_SPEC.refund is False
    assert DEVIL_SPEC.max_participants is None
    assert DEVIL_SPEC.join_cost == 5
    assert DEVIL_SPEC.taozi_cost == 30
    assert DEVIL_SPEC.meme_cost == 5
    assert DEVIL_SPEC.corpus_cost == 5
    assert DEVIL_SPEC.meme_candidates == 5


def test_devil_rules_list_threshold_cost_and_every_probability() -> None:
    text = format_devil_wheel_rules(60, 200)
    for fragment in (
        "大于 200",
        "不限人数",
        "参与一次扣 5 点好感度",
        "60 秒后自动开转",
        "不退款",
        "表情 10%",
        "桃子表情包 25%",
        "30 点好感",
        "群友语料库 10%",
        "扣 5 点好感",
        "+4 4%",
        "+40 1%",
        "-4 13%",
        "-60 2%",
    ):
        assert fragment in text


def test_pick_mode_requires_more_than_threshold() -> None:
    class FixedRandom(random.Random):
        def random(self) -> float:
            return 0.0

    coordinator = WheelCoordinator(object(), Config(), rng=FixedRandom(0))
    assert coordinator.pick_mode(200) is False
    assert coordinator.pick_mode(201) is True


def test_pick_mode_respects_configured_chance() -> None:
    coordinator = WheelCoordinator(
        object(), Config(mantou_affection_wheel_devil_chance=0.0), rng=random.Random(0)
    )
    assert coordinator.pick_mode(999) is False


@pytest.mark.parametrize(("roll", "kind"), [(0, "meme"), (10, "taozi"), (35, "corpus")])
def test_devil_draw_ranges(roll, kind) -> None:
    class Fixed(random.Random):
        def randrange(self, stop: int) -> int:
            assert stop == 100
            return roll

    assert draw_wheel_outcome(Fixed(0), DEVIL_WHEEL_OUTCOMES).kind == kind
