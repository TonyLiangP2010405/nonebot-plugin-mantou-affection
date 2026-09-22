import pytest
from nonebot.adapters.onebot.v11 import Message, MessageSegment

from nonebot_plugin_mantou_affection.parsing import parse_adjustment, parse_probability


def test_parse_adjustment() -> None:
    message = Message([MessageSegment.at("12345"), MessageSegment.text(" +10")])
    result = parse_adjustment(message)
    assert result.user_id == "12345"
    assert result.delta == 10


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (Message("+10"), "@"),
        (Message([MessageSegment.at("12345")]), "填写调整数值"),
        (
            Message([MessageSegment.at("12345"), MessageSegment.text(" abc")]),
            "必须是整数",
        ),
        (
            Message([MessageSegment.at("12345"), MessageSegment.text(" 0")]),
            "不能为 0",
        ),
    ],
)
def test_parse_adjustment_errors(message: Message, expected: str) -> None:
    with pytest.raises(ValueError, match=expected):
        parse_adjustment(message)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("0.05", 0.05),
        ("0", 0.0),
        ("1", 1.0),
        ("0.5", 0.5),
        ("5%", 0.05),
        (" 5 % ", 0.05),
        ("0.5%", 0.005),
        ("0%", 0.0),
        ("100%", 1.0),
    ],
)
def test_parse_probability_accepts_decimals_and_percents(text: str, expected: float) -> None:
    assert parse_probability(text) == pytest.approx(expected)


@pytest.mark.parametrize(
    "text",
    ["", "   ", "abc", "%", "5%%", "1.5", "-0.1", "-5%", "101%", "nan", "inf"],
)
def test_parse_probability_errors(text: str) -> None:
    with pytest.raises(ValueError, match=r"请输入 0~1 的小数（如 0.05）或百分数（如 5%）"):
        parse_probability(text)
