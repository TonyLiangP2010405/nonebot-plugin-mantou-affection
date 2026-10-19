from __future__ import annotations

import asyncio
import importlib
import random
import time
import urllib.request
from collections.abc import Awaitable, Callable
from typing import Any

from nonebot import logger
from nonebot.utils import run_sync


class MemeGenerationError(RuntimeError):
    pass


class MemeAssetMissing(MemeGenerationError):
    pass


def _load_backend() -> Any | None:
    try:
        return importlib.import_module("meme_generator")
    except (ImportError, OSError) as error:
        logger.warning(f"[mantou-affection] memes 生成器不可用: {error}")
        return None


def _download_avatar(user_id: str) -> bytes:
    url = f"https://q1.qlogo.cn/g?b=qq&nk={user_id}&s=640"
    with urllib.request.urlopen(url, timeout=15) as response:
        return response.read()


async def _fetch_avatar(user_id: str) -> bytes:
    return await asyncio.to_thread(_download_avatar, user_id)


class MemeGeneratorAdapter:
    def __init__(
        self,
        *,
        backend: Any | None = None,
        rng: random.Random | None = None,
        avatar_fetcher: Callable[[str], Awaitable[bytes]] | None = None,
        bot_name: str = "馒头",
    ):
        self.backend = backend if backend is not None else _load_backend()
        self.rng = rng or random.Random()
        self.avatar_fetcher = avatar_fetcher or _fetch_avatar
        self.bot_name = bot_name

    @staticmethod
    def _kind(params: Any) -> str | None:
        if params.max_images == 0 and params.min_texts <= 1 <= params.max_texts:
            return "text"
        if params.min_images == params.max_images == 1 and params.min_texts == 0:
            return "single"
        if params.min_images == params.max_images == 2 and params.min_texts == 0:
            return "double"
        return None

    async def generate(self, *, bot: Any, user_id: str, nickname: str) -> bytes:
        backend = self.backend
        if backend is None:
            raise MemeGenerationError("meme-generator 未安装")
        try:
            available = backend.get_memes()
        except Exception as error:
            raise MemeGenerationError("读取表情列表失败") from error
        candidates = []
        for meme in available:
            kind = self._kind(meme.info.params)
            if kind is not None:
                candidates.append((meme, kind))
        if not candidates:
            raise MemeGenerationError("没有兼容的表情模板")
        meme, kind = self.rng.choice(candidates)
        texts = [nickname] if kind == "text" else []
        images = []
        if kind != "text":
            ids = [user_id] if kind == "single" else [str(bot.self_id), user_id]
            for index, current_id in enumerate(ids):
                try:
                    data = await self.avatar_fetcher(current_id)
                except Exception as error:
                    raise MemeGenerationError("头像下载失败") from error
                name = nickname if kind == "single" or index == 1 else self.bot_name
                images.append(backend.Image(name, data))
        try:
            result = await run_sync(meme.generate)(images, texts, {})
        except Exception as error:
            raise MemeGenerationError("表情生成失败") from error
        if isinstance(result, bytes):
            return result
        asset_error = getattr(backend, "ImageAssetMissing", None)
        if asset_error is not None and isinstance(result, asset_error):
            raise MemeAssetMissing(str(getattr(result, "path", "未知素材")))
        detail = getattr(result, "error", None) or getattr(result, "feedback", None)
        raise MemeGenerationError(str(detail or type(result).__name__))


def _load_resource_check() -> Callable[[], None] | None:
    try:
        module = importlib.import_module("meme_generator.resources")
        return module.check_resources_in_background
    except (ImportError, AttributeError, OSError) as error:
        logger.warning(f"[mantou-affection] memes 素材检查不可用: {error}")
        return None


class MemeResourceManager:
    def __init__(
        self,
        *,
        check: Callable[[], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        retry_seconds: float = 600.0,
    ):
        self.check = check if check is not None else _load_resource_check()
        self.clock = clock
        self.retry_seconds = retry_seconds
        self._startup_checked = False
        self._last_check: float | None = None

    def _start_check(self) -> bool:
        if self.check is None:
            return False
        try:
            self.check()
        except Exception:
            logger.exception("[mantou-affection] 发起 memes 素材后台检查失败")
            return False
        self._last_check = self.clock()
        return True

    def check_on_startup(self, *, handled_by_memes_plugin: bool) -> bool:
        if self._startup_checked:
            return False
        self._startup_checked = True
        if handled_by_memes_plugin:
            self._last_check = self.clock()
            return False
        return self._start_check()

    def check_after_missing(self) -> bool:
        now = self.clock()
        if self._last_check is not None and now - self._last_check < self.retry_seconds:
            return False
        return self._start_check()
