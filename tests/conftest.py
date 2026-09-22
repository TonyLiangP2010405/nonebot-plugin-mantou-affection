import tempfile
from pathlib import Path

import nonebot
from nonebot.adapters.onebot.v11 import Adapter


def pytest_configure() -> None:
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
