import json
from pathlib import Path

import pytest

from nonebot_plugin_mantou_affection.events import Event, EventLibrary, EventOption


def _write(path: Path, payload: object) -> Path:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _event_text(index: int) -> dict:
    return {
        "text": f"场景 {index}",
        "options": [
            {"text": f"最优 {index}", "delta": 2},
            {"text": f"普通 {index}", "delta": 1},
            {"text": f"最差 {index}", "delta": -2},
        ],
    }


def test_bundled_event_library_is_valid(bundled_texts_path: Path) -> None:
    library = EventLibrary(bundled_texts_path.with_name("affection_events.json"))
    assert len(library.events) >= 6
    for event in library.events:
        assert event.text
        assert len(event.options) == 3
        assert sorted(option.delta for option in event.options) == [-2, 1, 2]
        assert all(option.text for option in event.options)


def test_pick_returns_event_from_library(tmp_path: Path) -> None:
    library = EventLibrary(_write(tmp_path / "events.json", {"events": [_event_text(1)]}))
    picked = library.pick()
    assert picked is not None
    assert picked.text == "场景 1"
    assert [option.delta for option in picked.options] == [2, 1, -2]


def test_empty_library_picks_nothing(tmp_path: Path) -> None:
    assert EventLibrary(_write(tmp_path / "events.json", {"events": []})).pick() is None


def test_missing_file_disables_events(tmp_path: Path) -> None:
    library = EventLibrary(tmp_path / "missing.json")
    assert library.events == ()
    assert library.pick() is None


@pytest.mark.parametrize(
    "payload",
    [
        {"events": "not a list"},
        [{"text": "场景", "options": []}],
    ],
)
def test_broken_root_disables_events(tmp_path: Path, payload: object) -> None:
    library = EventLibrary(_write(tmp_path / "events.json", payload))
    assert library.events == ()


@pytest.mark.parametrize(
    "broken",
    [
        {"text": "缺少选项"},
        {"text": "选项太少", "options": [{"text": "甲", "delta": 2}]},
        {
            "text": "数值重复",
            "options": [
                {"text": "甲", "delta": 2},
                {"text": "乙", "delta": 2},
                {"text": "丙", "delta": 1},
            ],
        },
        {
            "text": "数值越界",
            "options": [
                {"text": "甲", "delta": 2},
                {"text": "乙", "delta": 1},
                {"text": "丙", "delta": -5},
            ],
        },
        {
            "text": "空选项文案",
            "options": [
                {"text": "甲", "delta": 2},
                {"text": " ", "delta": 1},
                {"text": "丙", "delta": -2},
            ],
        },
        {
            "text": "",
            "options": [
                {"text": "甲", "delta": 2},
                {"text": "乙", "delta": 1},
                {"text": "丙", "delta": -2},
            ],
        },
        "不是对象",
    ],
)
def test_invalid_events_are_skipped(tmp_path: Path, broken: object) -> None:
    library = EventLibrary(
        _write(tmp_path / "events.json", {"events": [broken, _event_text(2)]})
    )
    assert [event.text for event in library.events] == ["场景 2"]


def test_non_dict_options_are_skipped(tmp_path: Path) -> None:
    payload = {
        "events": [
            {
                "text": "半残事件",
                "options": ["不是对象", {"text": "甲", "delta": 2}, {"text": "乙", "delta": 1}],
            },
            _event_text(3),
        ]
    }
    library = EventLibrary(_write(tmp_path / "events.json", payload))
    assert [event.text for event in library.events] == ["场景 3"]


def test_shuffled_options_keep_content_and_change_order(tmp_path: Path) -> None:
    event = Event(
        "场景",
        (EventOption("甲", 2), EventOption("乙", 1), EventOption("丙", -2)),
    )
    orders = {
        tuple(option.text for option in EventLibrary.shuffled_options(event))
        for _ in range(60)
    }
    assert len(orders) > 1
    for order in orders:
        assert set(order) == {"甲", "乙", "丙"}
