import pytest
from nonebot.adapters.onebot.v11 import Message, MessageSegment

from nonebot_plugin_mantou_affection.parsing import parse_adjustment


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
