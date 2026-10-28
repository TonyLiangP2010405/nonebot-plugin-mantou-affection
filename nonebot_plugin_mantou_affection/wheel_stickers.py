from __future__ import annotations

import asyncio
import json
import random
import shutil
import threading
import time
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from nonebot import logger
from nonebot.utils import run_sync

from .wheel_memes import repo_download_urls


class StickerError(RuntimeError):
    pass


class StickerAssetMissing(StickerError):
    pass


@dataclass(frozen=True)
class TaoziSticker:
    id: int
    caption: str
    path: str


CATALOG_NAME = "catalog.json"
TAOZI_STICKERS_SLUG = "TonyLiangP2010405/taozi-stickers"
STICKERS_SUBDIR = "stickers/"
STAGING_NAME = ".taozi_stickers.staging"
ARCHIVE_NAME = ".taozi_stickers.zip"


def load_catalog(base_dir: Path) -> list[TaoziSticker]:
    """读取桃子表情包索引；缺失、损坏或为空都按素材未就绪处理。"""

    path = Path(base_dir) / CATALOG_NAME
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as error:
        raise StickerAssetMissing(f"索引缺失: {path}") from error
    try:
        payload = json.loads(raw)
    except ValueError as error:
        raise StickerAssetMissing(f"索引解析失败: {path}") from error
    if not isinstance(payload, dict):
        raise StickerAssetMissing(f"索引格式异常: {path}")
    items: list[TaoziSticker] = []
    for entry in payload.get("items") or ():
        if not isinstance(entry, dict):
            continue
        rel = str(entry.get("path") or "").strip()
        if not rel:
            continue
        try:
            sticker_id = int(entry.get("id") or 0)
        except (TypeError, ValueError):
            sticker_id = 0
        items.append(
            TaoziSticker(sticker_id, str(entry.get("caption") or "").strip(), rel)
        )
    if not items:
        raise StickerAssetMissing(f"索引为空: {path}")
    return items


def catalog_ready(base_dir: Path) -> bool:
    """索引可解析、非空，且每条列出的图片都存在才算就绪。"""

    try:
        items = load_catalog(base_dir)
    except StickerAssetMissing:
        return False
    base = Path(base_dir)
    return all((base / item.path).is_file() for item in items)


def _strip_root(name: str) -> str | None:
    parts = name.split("/")
    if len(parts) < 2:
        return None
    rel = "/".join(parts[1:]).strip("/")
    return rel or None


class TaoziStickerLibrary:
    """桃子表情包素材库：启动时后台下载整包，抽中时本地随机取图。"""

    def __init__(
        self,
        *,
        base_dir: Path | None = None,
        enabled: bool = True,
        opener: Callable[..., Any] | None = None,
        rng: random.Random | None = None,
        retry_seconds: float = 600.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.base_dir = Path(base_dir) if base_dir is not None else None
        self.enabled = enabled
        self.opener = opener or urllib.request.urlopen
        self.rng = rng or random.Random()
        self.retry_seconds = retry_seconds
        self.clock = clock
        self._started = False
        self._loaded = False
        self._task: asyncio.Task | None = None
        self._last_check: float | None = None

    def _download(self) -> Path:
        """边下边写到临时文件，并逐源校验确实是 zip；坏包换下一个源。"""

        base = self.base_dir
        if base is None:
            raise StickerError("桃子表情包目录未配置")
        base.parent.mkdir(parents=True, exist_ok=True)
        archive = base.parent / ARCHIVE_NAME
        last_error: Exception | None = None
        for url in repo_download_urls(TAOZI_STICKERS_SLUG):
            try:
                with self.opener(url, timeout=60) as response, archive.open(
                    "wb"
                ) as sink:
                    shutil.copyfileobj(response, sink)
                with zipfile.ZipFile(archive):
                    pass
                return archive
            except Exception as error:
                last_error = error
                archive.unlink(missing_ok=True)
                logger.warning(f"[mantou-affection] 桃子表情包下载源不可用: {error}")
        raise StickerError(f"桃子表情包下载失败: {last_error}")

    def _extract(self, archive: Path) -> None:
        base = self.base_dir
        if base is None:
            raise StickerError("桃子表情包目录未配置")
        staging = base.parent / STAGING_NAME
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(archive) as bundle:
                for info in bundle.infolist():
                    if info.is_dir():
                        continue
                    rel = _strip_root(info.filename)
                    if rel is None or ".." in Path(rel).parts:
                        continue
                    if rel != CATALOG_NAME and not rel.startswith(STICKERS_SUBDIR):
                        continue
                    target = staging / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with bundle.open(info) as source, target.open("wb") as sink:
                        shutil.copyfileobj(source, sink)
            shutil.rmtree(base, ignore_errors=True)
            staging.rename(base)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    def _download_and_extract(self) -> None:
        archive = self._download()
        try:
            self._extract(archive)
        finally:
            archive.unlink(missing_ok=True)

    async def ensure_loaded(self) -> None:
        if not self.enabled or self.base_dir is None or self._loaded:
            return
        self.base_dir.mkdir(parents=True, exist_ok=True)
        if catalog_ready(self.base_dir):
            self._loaded = True
            return
        logger.info(
            "[mantou-affection] 桃子表情包素材未就绪，开始后台下载"
            "（整包数百 MB，请耐心等待）"
        )
        await run_sync(self._download_and_extract)()
        if not catalog_ready(self.base_dir):
            raise StickerError("桃子表情包下载完成后仍不可用")
        self._loaded = True
        logger.info("[mantou-affection] 桃子表情包素材已就绪")

    async def pick(self) -> tuple[bytes, str]:
        if self.base_dir is None:
            raise StickerAssetMissing("桃子表情包目录未配置")
        item = self.rng.choice(load_catalog(self.base_dir))
        path = self.base_dir / item.path
        try:
            data = await asyncio.to_thread(path.read_bytes)
        except OSError as error:
            raise StickerAssetMissing(f"桃子表情包读取失败: {path}") from error
        return data, item.caption

    def kickoff(self) -> None:
        """调度一次后台同步，重复调用安全。"""

        if self._started or not self.enabled or self.base_dir is None:
            return
        self._started = True
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            threading.Thread(
                target=lambda: asyncio.run(self._safe_load()), daemon=True
            ).start()
        else:
            self._task = loop.create_task(self._safe_load())

    async def _safe_load(self) -> None:
        try:
            await self.ensure_loaded()
        except Exception:
            self._started = False
            logger.exception("[mantou-affection] 桃子表情包素材准备失败")

    def check_after_missing(self) -> bool:
        now = self.clock()
        if self._last_check is not None and now - self._last_check < self.retry_seconds:
            return False
        self._last_check = now
        self.kickoff()
        return True
