"""转盘随机壁纸：从米哈游官方渠道取图。

只走国内可直连的域名，不依赖 GitHub、Wallhaven 这类境内打不开的站点：

* 绝区零：米哈游 Wiki 的「壁纸合集」页面（``act-api-takumi-static.mihoyo.com``），
  原图在 ``act-upload.mihoyo.com``；
* 原神 / 崩坏：星穹铁道：米游社上持续更新的官方电脑壁纸帖（``bbs-api.miyoushe.com``），
  原图在 ``upload-bbs.miyoushe.com``。

合集索引按 TTL 缓存在本地，网络抖动或站点临时不可用时优先用过期缓存兜底。
"""

from __future__ import annotations

import json
import random
import re
import time
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from nonebot import logger
from nonebot.utils import run_sync

WIKI_ENTRY_API = "https://act-api-takumi-static.mihoyo.com/hoyowiki/{app}/wapi/entry_page"
BBS_POST_API = "https://bbs-api.miyoushe.com/post/wapi/getPostFull"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
WIKI_IMAGE_PATTERN = re.compile(
    r"https://act-upload\.mihoyo\.com/[^\s\"'\\]+?\.(?:png|jpg|jpeg|webp)"
)
BBS_IMAGE_HOSTS = ("upload-bbs.miyoushe.com",)
MIN_WIDTH = 1920
CACHE_TTL_SECONDS = 12 * 3600
FETCH_TIMEOUT = 20.0
MAX_IMAGE_BYTES = 30 * 1024 * 1024
DOWNLOAD_ATTEMPTS = 3

__all__ = [
    "COLLECTIONS",
    "Wallpaper",
    "WallpaperCollection",
    "WallpaperError",
    "WallpaperLibrary",
    "WallpaperUnavailable",
]


class WallpaperError(RuntimeError):
    """壁纸相关的可预期失败。"""


class WallpaperUnavailable(WallpaperError):
    """壁纸源暂时取不到图（网络、数据异常或缓存为空）。"""


@dataclass(frozen=True)
class WallpaperCollection:
    """一个壁纸来源。

    ``kind`` 为 ``wiki`` 时 ``ref`` 是米哈游 Wiki 的 ``entry_page_id``，
    ``app`` 是 Wiki 的 app code（如 ``zzz``）；
    ``kind`` 为 ``post`` 时 ``ref`` 是米游社帖子的 ``post_id``，``app`` 留空。
    """

    game: str
    kind: str
    ref: int
    app: str = ""


@dataclass(frozen=True)
class Wallpaper:
    """抽中的一张壁纸。"""

    game: str
    collection: str
    url: str


COLLECTIONS: tuple[WallpaperCollection, ...] = (
    # 原神 - 米游社持续更新的官方电脑壁纸帖
    WallpaperCollection("原神", "post", 70866259),
    WallpaperCollection("原神", "post", 77268932),
    WallpaperCollection("原神", "post", 76873211),
    # 崩坏星穹铁道 - 同一系列的官方电脑壁纸帖
    WallpaperCollection("崩坏：星穹铁道", "post", 74067340),
    WallpaperCollection("崩坏：星穹铁道", "post", 74067370),
    WallpaperCollection("崩坏：星穹铁道", "post", 74067382),
    WallpaperCollection("崩坏：星穹铁道", "post", 74329025),
    # 绝区零 - 官方 Wiki 的壁纸合集
    WallpaperCollection("绝区零", "wiki", 689, "zzz"),
    WallpaperCollection("绝区零", "wiki", 688, "zzz"),
    WallpaperCollection("绝区零", "wiki", 685, "zzz"),
    WallpaperCollection("绝区零", "wiki", 718, "zzz"),
    WallpaperCollection("绝区零", "wiki", 717, "zzz"),
    WallpaperCollection("绝区零", "wiki", 775, "zzz"),
    WallpaperCollection("绝区零", "wiki", 684, "zzz"),
    WallpaperCollection("绝区零", "wiki", 1127, "zzz"),
    WallpaperCollection("绝区零", "wiki", 1084, "zzz"),
    WallpaperCollection("绝区零", "wiki", 1085, "zzz"),
    WallpaperCollection("绝区零", "wiki", 1599, "zzz"),
    WallpaperCollection("绝区零", "wiki", 1864, "zzz"),
    WallpaperCollection("绝区零", "wiki", 1965, "zzz"),
    WallpaperCollection("绝区零", "wiki", 1987, "zzz"),
    WallpaperCollection("绝区零", "wiki", 1988, "zzz"),
    WallpaperCollection("绝区零", "wiki", 1989, "zzz"),
    WallpaperCollection("绝区零", "wiki", 1990, "zzz"),
)


def _wiki_image_urls(node: Any) -> list[str]:
    """递归收集 Wiki 页面模块里指向米哈游图床的图片，保持顺序并去重。"""

    found: list[str] = []
    seen: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, str):
            for url in WIKI_IMAGE_PATTERN.findall(value):
                if url not in seen:
                    seen.add(url)
                    found.append(url)
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                walk(item)

    walk(node)
    return found


def _is_bbs_image(url: Any) -> bool:
    return isinstance(url, str) and any(
        f"://{host}/" in url for host in BBS_IMAGE_HOSTS
    )


def _post_image_urls(image_list: Any) -> list[str]:
    """从帖子的 ``image_list`` 里挑出够大的原图，保持顺序并去重。"""

    found: list[str] = []
    seen: set[str] = set()
    for entry in image_list or ():
        if not isinstance(entry, dict):
            continue
        url = entry.get("url")
        if not _is_bbs_image(url) or url in seen:
            continue
        try:
            width = int(entry.get("width") or 0)
        except (TypeError, ValueError):
            width = 0
        if width and width < MIN_WIDTH:
            continue
        seen.add(url)
        found.append(url)
    return found


class WallpaperLibrary:
    """米哈游壁纸库：索引本地缓存，抽中时下载原图。"""

    def __init__(
        self,
        *,
        cache_dir: Path | None = None,
        enabled: bool = True,
        opener: Callable[..., Any] | None = None,
        rng: random.Random | None = None,
        collections: Iterable[WallpaperCollection] | None = None,
        ttl: float = CACHE_TTL_SECONDS,
        timeout: float = FETCH_TIMEOUT,
        clock: Callable[[], float] = time.time,
    ):
        self.cache_dir = Path(cache_dir) if cache_dir is not None else None
        self.enabled = enabled
        self.opener = opener or urllib.request.urlopen
        self.rng = rng or random.Random()
        self.collections = tuple(
            COLLECTIONS if collections is None else collections
        )
        self.ttl = max(float(ttl), 0.0)
        self.timeout = max(float(timeout), 1.0)
        self.clock = clock

    # ------------------------------------------------------------------ #
    # 索引缓存
    # ------------------------------------------------------------------ #

    def _cache_path(self, collection: WallpaperCollection) -> Path | None:
        if self.cache_dir is None:
            return None
        tag = collection.app or "bbs"
        return self.cache_dir / f"{collection.kind}-{tag}-{collection.ref}.json"

    def _read_cache(
        self, collection: WallpaperCollection
    ) -> tuple[list[str], str, float] | None:
        path = self._cache_path(collection)
        if path is None:
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(payload, dict):
            return None
        urls = [url for url in payload.get("urls") or () if isinstance(url, str)]
        if not urls:
            return None
        try:
            fetched_at = float(payload.get("fetched_at") or 0.0)
        except (TypeError, ValueError):
            fetched_at = 0.0
        return urls, str(payload.get("name") or ""), fetched_at

    def _write_cache(
        self,
        collection: WallpaperCollection,
        urls: list[str],
        name: str,
        fetched_at: float,
    ) -> None:
        path = self._cache_path(collection)
        if path is None or not urls:
            return
        payload = {
            "game": collection.game,
            "kind": collection.kind,
            "ref": collection.ref,
            "app": collection.app,
            "name": name,
            "fetched_at": fetched_at,
            "urls": urls,
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            staging = path.with_suffix(path.suffix + ".tmp")
            staging.write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
            staging.replace(path)
        except OSError as error:
            logger.warning(f"[mantou-affection] 壁纸索引写入失败 {path}: {error}")

    # ------------------------------------------------------------------ #
    # 索引抓取
    # ------------------------------------------------------------------ #

    def _load_json(self, url: str, extra_headers: dict[str, str] | None = None) -> Any:
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://www.miyoushe.com/",
        }
        if extra_headers:
            headers.update(extra_headers)
        request = urllib.request.Request(url, headers=headers)
        with self.opener(request, timeout=self.timeout) as response:
            raw = response.read()
        return json.loads(raw.decode("utf-8"))

    def _fetch_wiki(self, collection: WallpaperCollection) -> tuple[list[str], str]:
        if not collection.app:
            raise WallpaperUnavailable(f"Wiki 合集 {collection.ref} 缺少 app")
        url = (
            WIKI_ENTRY_API.format(app=collection.app)
            + f"?entry_page_id={collection.ref}"
        )
        payload = self._load_json(
            url,
            {
                "X-Rpc-Wiki_app": collection.app,
                "Referer": "https://www.miyoushe.com/",
            },
        )
        page = ((payload.get("data") or {}).get("page")) or {}
        if not page:
            raise WallpaperUnavailable(f"Wiki 合集 {collection.app}/{collection.ref} 没有数据")
        urls = _wiki_image_urls(page.get("modules") or ())
        if not urls:
            raise WallpaperUnavailable(
                f"Wiki 合集 {collection.app}/{collection.ref} 没有图片"
            )
        return urls, str(page.get("name") or "")

    def _fetch_post(self, collection: WallpaperCollection) -> tuple[list[str], str]:
        payload = self._load_json(f"{BBS_POST_API}?post_id={collection.ref}")
        post = ((payload.get("data") or {}).get("post")) or {}
        if not post:
            raise WallpaperUnavailable(f"米游社帖子 {collection.ref} 没有数据")
        urls = _post_image_urls(post.get("image_list"))
        if not urls:
            raise WallpaperUnavailable(f"米游社帖子 {collection.ref} 没有可用壁纸")
        return urls, str((post.get("post") or {}).get("subject") or "")

    def _fetch(self, collection: WallpaperCollection) -> tuple[list[str], str]:
        if collection.kind == "wiki":
            return self._fetch_wiki(collection)
        if collection.kind == "post":
            return self._fetch_post(collection)
        raise WallpaperUnavailable(f"未知的壁纸来源类型: {collection.kind}")

    def _resolve(self, collection: WallpaperCollection) -> tuple[list[str], str]:
        """返回可用图片与合集名：缓存新鲜直接用，过期就刷新，刷新失败用旧缓存兜底。"""

        cached = self._read_cache(collection)
        if cached is not None and self.clock() - cached[2] <= self.ttl:
            return cached[0], cached[1]
        try:
            urls, name = self._fetch(collection)
        except Exception as error:
            if cached is not None:
                logger.warning(
                    f"[mantou-affection] 壁纸合集刷新失败，改用缓存 "
                    f"{collection.kind}/{collection.ref}: {error}"
                )
                return cached[0], cached[1]
            raise WallpaperUnavailable(
                f"壁纸合集 {collection.kind}/{collection.ref} 不可用: {error}"
            ) from error
        self._write_cache(collection, urls, name, self.clock())
        return urls, name

    # ------------------------------------------------------------------ #
    # 对外接口
    # ------------------------------------------------------------------ #

    def _download(self, url: str) -> bytes:
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Referer": "https://www.miyoushe.com/",
            },
        )
        with self.opener(request, timeout=self.timeout) as response:
            data = response.read(MAX_IMAGE_BYTES + 1)
        if len(data) > MAX_IMAGE_BYTES:
            raise WallpaperUnavailable(f"壁纸文件过大: {url}")
        if not data:
            raise WallpaperUnavailable(f"壁纸文件为空: {url}")
        return data

    async def draw(self) -> tuple[Wallpaper, bytes]:
        """随机取一个合集的随机一张图，返回壁纸描述和图片内容。"""

        if not self.enabled:
            raise WallpaperUnavailable("壁纸功能未启用")
        if not self.collections:
            raise WallpaperUnavailable("没有配置壁纸合集")
        order = list(self.collections)
        self.rng.shuffle(order)
        last_error: Exception | None = None
        for collection in order:
            try:
                urls, name = await run_sync(self._resolve)(collection)
            except WallpaperUnavailable as error:
                last_error = error
                continue
            candidates = list(urls)
            self.rng.shuffle(candidates)
            for url in candidates[:DOWNLOAD_ATTEMPTS]:
                try:
                    data = await run_sync(self._download)(url)
                except Exception as error:
                    last_error = error
                    logger.warning(
                        f"[mantou-affection] 壁纸下载失败 {collection.ref}: {error}"
                    )
                    continue
                return Wallpaper(collection.game, name, url), data
        raise WallpaperUnavailable(f"本轮没有取到壁纸: {last_error}")

    async def pick(self) -> Wallpaper:
        """只取壁纸描述，不下载图片（给不需要图内容的调用方）。"""

        wallpaper, _data = await self.draw()
        return wallpaper
