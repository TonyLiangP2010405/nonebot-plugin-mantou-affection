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
