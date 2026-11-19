import tempfile
from pathlib import Path

import nonebot
import pytest
from nonebot.adapters.onebot.v11 import Adapter


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "real_active_hours: 用真实的开启时段判定，不套用测试默认的「全天开放」",
    )
    localstore_root = Path(tempfile.mkdtemp(prefix="mantou-affection-test-"))
    nonebot.init(
        driver="~none",
        superusers={"10001"},
        localstore_data_dir=localstore_root / "data",
        localstore_cache_dir=localstore_root / "cache",
        localstore_config_dir=localstore_root / "config",
    )
    nonebot.get_driver().register_adapter(Adapter)
    plugin = nonebot.load_plugin("nonebot_plugin_mantou_affection")
    if plugin is None:
        raise RuntimeError("nonebot_plugin_mantou_affection 加载失败")
    # 测试会话会跑一次启动钩子, 关掉素材库, 避免每次跑测试都去下载数百 MB。
    import nonebot_plugin_mantou_affection as mantou_affection

    mantou_affection.taozi_stickers.enabled = False


@pytest.fixture(autouse=True)
def _always_open_active_hours(request: pytest.FixtureRequest, monkeypatch) -> None:
    """默认把随机活动的开启时段当成全天，免得用例结果随跑测试的实际时间变化。

    验证时段限制本身的用例用 @pytest.mark.real_active_hours 关掉这个兜底。
    """

    if request.node.get_closest_marker("real_active_hours") is not None:
        return
    from nonebot_plugin_mantou_affection.service import AffectionService

    monkeypatch.setattr(
        AffectionService,
        "activity_hours_open",
        lambda self, *, now=None: True,
    )


@pytest.fixture()
def bundled_texts_path() -> Path:
    return (
        Path(__file__).resolve().parents[1]
        / "nonebot_plugin_mantou_affection"
        / "resources"
        / "affection_texts.json"
    )
