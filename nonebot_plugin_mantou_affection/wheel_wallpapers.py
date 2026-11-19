"""随机壁纸：官方图源与二创图源。

只走国内可直连的域名，不依赖 GitHub、Wallhaven 这类境内打不开的站点：

* 绝区零（官方）：米哈游 Wiki 的「壁纸合集」页面（``act-api-takumi-static.mihoyo.com``），
  原图在 ``act-upload.mihoyo.com``；
* 原神 / 崩坏：星穹铁道（官方）：米游社上持续更新的官方电脑壁纸帖（``bbs-api.miyoushe.com``），
  原图在 ``upload-bbs.miyoushe.com``；
* 二创：米游社的搜索接口（``bbs-api.miyoushe.com``），取玩家发在「同人图 / 壁纸」
  版区的帖子，原图同样在 ``upload-bbs.miyoushe.com``；尺寸用图床的
  ``x-oss-process=image/info`` 接口先确认，避免下完才发现是小图。

合集索引按 TTL 缓存在本地，网络抖动或站点临时不可用时优先用过期缓存兜底。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import re
import time
import urllib.parse
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
BBS_SEARCH_API = "https://bbs-api.miyoushe.com/post/wapi/searchPosts"
BBS_IMAGE_INFO = "?x-oss-process=image/info"
SEARCH_POSTS = 20
MIN_WIDTH = 1920
FANART_MIN_SIDE = 1080
FANART_MAX_BYTES = 10 * 1024 * 1024
FANART_INFO_CHECKS = 5
CACHE_TTL_SECONDS = 12 * 3600
FETCH_TIMEOUT = 20.0
MAX_IMAGE_BYTES = 30 * 1024 * 1024
DOWNLOAD_ATTEMPTS = 3

__all__ = [
    "COLLECTIONS",
    "FANART_COLLECTIONS",
    "WALLPAPER_GAME_ALIASES",
    "Wallpaper",
    "WallpaperCollection",
    "WallpaperError",
    "WallpaperLibrary",
    "WallpaperUnavailable",
    "match_wallpaper_game",
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
    ``kind`` 为 ``post`` 时 ``ref`` 是米游社帖子的 ``post_id``，``app`` 留空；
    ``kind`` 为 ``search`` 时用 ``query`` 去米游社搜二创图，``ref`` 留 0。
    """

    game: str
    kind: str
    ref: int
    app: str = ""
    query: str = ""


@dataclass(frozen=True)
class Wallpaper:
    """抽中的一张壁纸。"""

    game: str
    collection: str
    url: str
    source: str = "official"


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

# 二创图源: 米游社搜索, 内容都是玩家自己发的同人/二创图, 不是官方素材。
FANART_COLLECTIONS: tuple[WallpaperCollection, ...] = (
    WallpaperCollection("原神", "search", 0, query="原神壁纸"),
    WallpaperCollection("原神", "search", 0, query="原神同人图"),
    WallpaperCollection("崩坏：星穹铁道", "search", 0, query="星穹铁道壁纸"),
    WallpaperCollection("崩坏：星穹铁道", "search", 0, query="星铁同人图"),
    WallpaperCollection("绝区零", "search", 0, query="绝区零壁纸"),
    WallpaperCollection("绝区零", "search", 0, query="绝区零同人图"),
)

# 用户输入的游戏名 -> 合集里的标准名, 统一小写后匹配。
WALLPAPER_GAME_ALIASES: dict[str, str] = {
    "原神": "原神",
    "genshin": "原神",
    "ys": "原神",
    "崩坏：星穹铁道": "崩坏：星穹铁道",
    "崩坏星穹铁道": "崩坏：星穹铁道",
    "星穹铁道": "崩坏：星穹铁道",
    "星铁": "崩坏：星穹铁道",
    "崩铁": "崩坏：星穹铁道",
    "hsr": "崩坏：星穹铁道",
    "绝区零": "绝区零",
    "绝区": "绝区零",
    "zzz": "绝区零",
}


def match_wallpaper_game(query: str) -> str | None:
    """把用户输入的游戏名换成合集里的标准名，认不出来返回 None。"""

    return WALLPAPER_GAME_ALIASES.get(query.strip().lower())


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


def _search_image_urls(posts: Any) -> list[str]:
    """从米游社搜索结果里收集帖子图片，保持顺序并去重。"""

    found: list[str] = []
    seen: set[str] = set()
    for entry in posts or ():
        if not isinstance(entry, dict):
            continue
        post = entry.get("post")
        if not isinstance(post, dict):
            continue
        images = post.get("images")
        if not isinstance(images, list):
            continue
        for url in images:
            if not _is_bbs_image(url) or url in seen:
                continue
            seen.add(url)
            found.append(url)
    return found


class WallpaperLibrary:
    """壁纸库：官方合集与二创搜索都索引到本地缓存，抽中时下载原图。"""

    def __init__(
        self,
        *,
        cache_dir: Path | None = None,
        enabled: bool = True,
        opener: Callable[..., Any] | None = None,
        rng: random.Random | None = None,
        collections: Iterable[WallpaperCollection] | None = None,
        fanart_collections: Iterable[WallpaperCollection] | None = None,
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
        self.fanart_collections = tuple(
            FANART_COLLECTIONS
            if fanart_collections is None
            else fanart_collections
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
        if collection.kind == "search":
            digest = hashlib.sha1(
                collection.query.encode("utf-8")
            ).hexdigest()[:12]
            return self.cache_dir / f"{collection.kind}-{digest}.json"
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
            "query": collection.query,
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

    def _fetch_search(
        self, collection: WallpaperCollection
    ) -> tuple[list[str], str]:
        """米游社搜索：收集玩家发在版区里的二创图。"""

        if not collection.query:
            raise WallpaperUnavailable("搜索合集缺少关键词")
        params = urllib.parse.urlencode(
            {"keyword": collection.query, "size": SEARCH_POSTS, "offset": 0}
        )
        payload = self._load_json(f"{BBS_SEARCH_API}?{params}")
        posts = ((payload.get("data") or {}).get("posts")) or ()
        urls = _search_image_urls(posts)
        if not urls:
            raise WallpaperUnavailable(f"米游社「{collection.query}」没有可用壁纸")
        return urls, f"米游社「{collection.query}」"

    def _fetch(self, collection: WallpaperCollection) -> tuple[list[str], str]:
        if collection.kind == "wiki":
            return self._fetch_wiki(collection)
        if collection.kind == "post":
            return self._fetch_post(collection)
        if collection.kind == "search":
            return self._fetch_search(collection)
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

    async def draw(
        self, game: str | None = None, *, fanart: bool = False
    ) -> tuple[Wallpaper, bytes]:
        """随机取一个合集的随机一张图，返回壁纸描述和图片内容。

        ``game`` 非空时只在指定游戏的合集里抽，传标准名（``原神`` /
        ``崩坏：星穹铁道`` / ``绝区零``）；``fanart`` 为真时改抽二创图源。
        """

        if not self.enabled:
            raise WallpaperUnavailable("壁纸功能未启用")
        pool = self.fanart_collections if fanart else self.collections
        kind_label = "二创" if fanart else ""
        if not pool:
            raise WallpaperUnavailable(f"没有配置{kind_label}壁纸合集")
        order = [
            collection
            for collection in pool
            if game is None or collection.game == game
        ]
        if not order:
            raise WallpaperUnavailable(f"没有 {game} 的{kind_label}壁纸合集")
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
            if collection.kind == "search":
                candidates = await self._filter_fanart(candidates)
                if not candidates:
                    last_error = WallpaperUnavailable(
                        f"米游社「{collection.query}」没有够大的二创图"
                    )
                    continue
            for url in candidates[:DOWNLOAD_ATTEMPTS]:
                try:
                    data = await run_sync(self._download)(url)
                except Exception as error:
                    last_error = error
                    logger.warning(
                        f"[mantou-affection] 壁纸下载失败 {collection.ref}: {error}"
                    )
                    continue
                source = "fanart" if collection.kind == "search" else "official"
                return Wallpaper(collection.game, name, url, source), data
        raise WallpaperUnavailable(f"本轮没有取到壁纸: {last_error}")

    def _fanart_is_large(self, url: str) -> bool:
        """用图床的 image/info 接口确认二创原图够大，省得下完才发现是小图。

        元数据读不到时按「够大」处理：接口抽风不该把整组图都丢掉。
        """

        try:
            payload = self._load_json(f"{url}{BBS_IMAGE_INFO}")
            width = int((payload.get("ImageWidth") or {}).get("value") or 0)
            height = int((payload.get("ImageHeight") or {}).get("value") or 0)
            size = int((payload.get("FileSize") or {}).get("value") or 0)
        except Exception as error:
            logger.warning(f"[mantou-affection] 二创图信息读取失败 {url}: {error}")
            return True
        if size and size > FANART_MAX_BYTES:
            return False
        if not width or not height:
            return True
        return min(width, height) >= FANART_MIN_SIDE and max(width, height) >= MIN_WIDTH

    async def _filter_fanart(self, candidates: list[str]) -> list[str]:
        """二创图先探测前几张的尺寸，留下够大的；探测数量设上限，避免请求爆炸。

        图床每次都要重新握手，探测并发跑，不然 5 张就得等十秒。
        """

        targets = candidates[:FANART_INFO_CHECKS]
        if not targets:
            return []
        results = await asyncio.gather(
            *(run_sync(self._fanart_is_large)(url) for url in targets)
        )
        return [url for url, ok in zip(targets, results) if ok]

    async def pick(self) -> Wallpaper:
        """只取壁纸描述，不下载图片（给不需要图内容的调用方）。"""

        wallpaper, _data = await self.draw()
        return wallpaper
