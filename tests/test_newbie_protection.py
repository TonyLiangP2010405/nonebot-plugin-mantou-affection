from __future__ import annotations

from pathlib import Path

from nonebot_plugin_mantou_affection.config import Config
from nonebot_plugin_mantou_affection.service import AffectionService
from nonebot_plugin_mantou_affection.storage import AffectionStore

GROUP = "96001"
USER = "96002"


def make_service(tmp_path: Path, below: int = 50) -> AffectionService:
    config = Config(mantou_affection_newbie_protection_below=below)
    return AffectionService(AffectionStore(tmp_path / "affection.json"), config)


async def give(service: AffectionService, score: int) -> None:
    def bump(profile) -> None:
        profile.affection = score

    await service.store.update_profile(GROUP, USER, "测试员", bump)


async def test_adjust_protects_deduction_below_threshold(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    await give(service, 40)
    applied, profile, protected = await service.adjust(GROUP, USER, "测试员", -30)
    assert applied == -10
    assert profile.affection == 30
    assert protected is True


async def test_adjust_keeps_full_deduction_at_threshold(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    await give(service, 50)
    applied, profile, protected = await service.adjust(GROUP, USER, "测试员", -30)
    assert applied == -30
    assert profile.affection == 20
    assert protected is False


async def test_adjust_ignores_positive_delta(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    await give(service, 10)
    applied, _profile, protected = await service.adjust(GROUP, USER, "测试员", 8)
    assert applied == 8
    assert protected is False


async def test_adjust_protect_false_bypasses_protection(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    await give(service, 40)
    applied, _profile, protected = await service.adjust(
        GROUP, USER, "测试员", -30, protect=False
    )
    assert applied == -30
    assert protected is False


async def test_adjust_floors_at_zero_after_protection(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    await give(service, 2)
    applied, profile, protected = await service.adjust(GROUP, USER, "测试员", -30)
    assert applied == -2
    assert profile.affection == 0
    assert protected is True


async def test_protection_can_be_disabled_by_config(tmp_path: Path) -> None:
    service = make_service(tmp_path, below=0)
    await give(service, 40)
    applied, _profile, protected = await service.adjust(GROUP, USER, "测试员", -30)
    assert applied == -30
    assert protected is False


class FakeStickers:
    def __init__(self, error: Exception | None = None):
        self.error = error
        self.picks = 0

    async def pick(self) -> tuple[bytes, str]:
        self.picks += 1
        if self.error is not None:
            raise self.error
        return b"taozi", "头部主播"


async def _noop(message) -> None:
    return None


async def test_send_gift_once_per_day(tmp_path: Path) -> None:
    stickers = FakeStickers()
    service = make_service(tmp_path)
    service.stickers = stickers
    await give(service, 60)
    sent = []

    async def send(message) -> None:
        sent.append(message)

    assert await service.send_gift(send, GROUP, USER, "测试员") is True
    assert await service.send_gift(send, GROUP, USER, "测试员") is False
    assert stickers.picks == 1
    assert len(sent) == 1
    assert "[CQ:image" in str(sent[0])


async def test_send_gift_skips_high_affection(tmp_path: Path) -> None:
    stickers = FakeStickers()
    service = make_service(tmp_path)
    service.stickers = stickers
    await give(service, 100)
    assert await service.send_gift(_noop, GROUP, USER, "测试员") is False
    assert stickers.picks == 0


async def test_send_gift_without_sticker_library(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    await give(service, 10)
    assert await service.send_gift(_noop, GROUP, USER, "测试员") is False


async def test_send_gift_failure_keeps_daily_slot(tmp_path: Path) -> None:
    stickers = FakeStickers(error=RuntimeError("missing"))
    service = make_service(tmp_path)
    service.stickers = stickers
    await give(service, 60)
    assert await service.send_gift(_noop, GROUP, USER, "测试员") is False
    stickers.error = None
    assert await service.send_gift(_noop, GROUP, USER, "测试员") is True
