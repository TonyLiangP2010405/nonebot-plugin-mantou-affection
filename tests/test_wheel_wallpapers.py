from __future__ import annotations

import json
import random
import urllib.parse
from pathlib import Path

import pytest

from nonebot_plugin_mantou_affection import wheel_wallpapers
from nonebot_plugin_mantou_affection.wheel_wallpapers import (
    Wallpaper,
    WallpaperCollection,
    WallpaperLibrary,
    WallpaperUnavailable,
    match_wallpaper_game,
)

WIKI_URL = (
    "https://act-api-takumi-static.mihoyo.com/hoyowiki/zzz/wapi/entry_page"
    "?entry_page_id=689"
)
POST_URL = "https://bbs-api.miyoushe.com/post/wapi/getPostFull?post_id=70866259"
IMG_A = "https://act-upload.mihoyo.com/nap/2026/01/30/a.png"
IMG_B = "https://act-upload.mihoyo.com/nap/2026/01/30/b.jpg"
BBS_A = "https://upload-bbs.miyoushe.com/upload/2025/11/24/a.png"
BBS_B = "https://upload-bbs.miyoushe.com/upload/2025/11/24/b.png"

WIKI_PAYLOAD = {
    "retcode": 0,
    "data": {
        "page": {
            "name": "阵营壁纸合集",
            "icon_url": "https://act-upload.mihoyo.com/nap/icon.png",
            "modules": [
                {
                    "name": "",
                    "components": [
                        {
                            "data": {
                                "list": [
                                    {"img": IMG_A},
                                    {"img": IMG_A},
                                    {"img": IMG_B},
                                ]
                            }
                        }
                    ],
                }
            ],
        }
    },
}

POST_PAYLOAD = {
    "retcode": 0,
    "data": {
        "post": {
            "post": {"subject": "原神电脑官方壁纸第三弹"},
            "image_list": [
                {"url": BBS_A, "width": 3840, "height": 2160},
                {"url": "https://upload-bbs.miyoushe.com/small.png", "width": 800},
                {"url": "https://example.com/evil.png", "width": 3840},
                {"url": BBS_A, "width": 3840},
                {"url": BBS_B, "width": "4096"},
            ],
        }
    },
}


class FakeResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self, size: int | None = None) -> bytes:
        if size is None or size < 0:
            return self._payload
        return self._payload[:size]

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *exc) -> bool:
        return False


class FakeOpener:
    def __init__(self, routes: dict[str, bytes] | None = None):
        self.routes = dict(routes or {})
        self.fail: set[str] = set()
        self.calls: list[str] = []

    def __call__(self, request, timeout=None) -> FakeResponse:
        url = getattr(request, "full_url", request)
        self.calls.append(url)
        if url in self.fail or url not in self.routes:
            raise OSError(f"boom: {url}")
        return FakeResponse(self.routes[url])


def wiki_routes(name: str = "阵营壁纸合集") -> dict[str, bytes]:
    payload = json.loads(json.dumps(WIKI_PAYLOAD))
    payload["data"]["page"]["name"] = name
    return {WIKI_URL: json.dumps(payload).encode("utf-8")}


def make_library(tmp_path: Path, opener: FakeOpener, **kwargs) -> WallpaperLibrary:
    collections = kwargs.pop(
        "collections", (WallpaperCollection("绝区零", "wiki", 689, "zzz"),)
    )
    kwargs.setdefault("rng", random.Random(0))
    return WallpaperLibrary(
        cache_dir=tmp_path / "wallpapers",
        opener=opener,
        collections=collections,
        **kwargs,
    )


def test_wiki_urls_skip_page_icon_and_dedupe() -> None:
    page = WIKI_PAYLOAD["data"]["page"]
    urls = wheel_wallpapers._wiki_image_urls(page["modules"])
    assert urls == [IMG_A, IMG_B]
    assert "https://act-upload.mihoyo.com/nap/icon.png" not in urls


def test_post_urls_keep_only_big_bbs_images() -> None:
    urls = wheel_wallpapers._post_image_urls(POST_PAYLOAD["data"]["post"]["image_list"])
    assert urls == [BBS_A, BBS_B]


async def test_draw_returns_wallpaper_and_bytes(tmp_path: Path) -> None:
    opener = FakeOpener({**wiki_routes(), IMG_A: b"png-a", IMG_B: b"png-b"})
    library = make_library(tmp_path, opener)

    wallpaper, data = await library.draw()

    assert isinstance(wallpaper, Wallpaper)
    assert wallpaper.game == "绝区零"
    assert wallpaper.collection == "阵营壁纸合集"
    assert wallpaper.url in {IMG_A, IMG_B}
    assert data in {b"png-a", b"png-b"}


async def test_draw_caches_index_and_reuses_it(tmp_path: Path) -> None:
    opener = FakeOpener({**wiki_routes(), IMG_A: b"png-a", IMG_B: b"png-b"})
    library = make_library(tmp_path, opener)

    await library.draw()
    index_calls = [url for url in opener.calls if url == WIKI_URL]
    assert len(index_calls) == 1

    await library.draw()
    assert len([url for url in opener.calls if url == WIKI_URL]) == 1


async def test_stale_cache_is_refreshed(tmp_path: Path) -> None:
    now = [1000.0]
    opener = FakeOpener({**wiki_routes(), IMG_A: b"png-a", IMG_B: b"png-b"})
    library = make_library(tmp_path, opener, ttl=10, clock=lambda: now[0])

    await library.draw()
    now[0] += 11
    await library.draw()

    assert len([url for url in opener.calls if url == WIKI_URL]) == 2


async def test_refresh_failure_falls_back_to_stale_cache(tmp_path: Path) -> None:
    now = [1000.0]
    opener = FakeOpener({**wiki_routes(), IMG_A: b"png-a", IMG_B: b"png-b"})
    library = make_library(tmp_path, opener, ttl=10, clock=lambda: now[0])
    await library.draw()

    opener.fail.add(WIKI_URL)
    now[0] += 11
    wallpaper, data = await library.draw()

    assert wallpaper.url in {IMG_A, IMG_B}
    assert data in {b"png-a", b"png-b"}


async def test_draw_skips_unavailable_collection(tmp_path: Path) -> None:
    opener = FakeOpener({**wiki_routes(), IMG_A: b"png-a", IMG_B: b"png-b"})
    collections = (
        WallpaperCollection("绝区零", "wiki", 1, "zzz"),
        WallpaperCollection("绝区零", "wiki", 689, "zzz"),
    )
    library = make_library(
        tmp_path, opener, collections=collections, rng=random.Random(3)
    )

    wallpaper, _data = await library.draw()

    assert wallpaper.url in {IMG_A, IMG_B}


async def test_draw_raises_when_everything_fails(tmp_path: Path) -> None:
    opener = FakeOpener({})
    library = make_library(tmp_path, opener)

    with pytest.raises(WallpaperUnavailable):
        await library.draw()


async def test_draw_raises_when_disabled(tmp_path: Path) -> None:
    library = make_library(tmp_path, FakeOpener({}), enabled=False)

    with pytest.raises(WallpaperUnavailable):
        await library.draw()


async def test_empty_image_download_is_rejected(tmp_path: Path) -> None:
    opener = FakeOpener({**wiki_routes(), IMG_A: b"", IMG_B: b"png-b"})
    library = make_library(tmp_path, opener, rng=random.Random(0))

    _wallpaper, data = await library.draw()

    assert data == b"png-b"


async def test_oversized_download_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(wheel_wallpapers, "MAX_IMAGE_BYTES", 4)
    opener = FakeOpener({**wiki_routes(), IMG_A: b"toolarge", IMG_B: b"ok"})
    library = make_library(tmp_path, opener, rng=random.Random(0))

    _wallpaper, data = await library.draw()

    assert data == b"ok"


async def test_post_collection_uses_image_list(tmp_path: Path) -> None:
    opener = FakeOpener(
        {POST_URL: json.dumps(POST_PAYLOAD).encode("utf-8"), BBS_A: b"bbs-a"}
    )
    library = make_library(
        tmp_path,
        opener,
        collections=(WallpaperCollection("原神", "post", 70866259),),
        rng=random.Random(0),
    )

    wallpaper, data = await library.draw()

    assert wallpaper.game == "原神"
    assert wallpaper.collection == "原神电脑官方壁纸第三弹"
    assert data == b"bbs-a"


def test_wiki_collection_requires_app(tmp_path: Path) -> None:
    library = make_library(
        tmp_path,
        FakeOpener({}),
        collections=(WallpaperCollection("绝区零", "wiki", 689),),
    )

    with pytest.raises(WallpaperUnavailable, match="app"):
        library._resolve(library.collections[0])


def test_unknown_collection_kind_is_rejected(tmp_path: Path) -> None:
    library = make_library(
        tmp_path,
        FakeOpener({}),
        collections=(WallpaperCollection("原神", "rss", 1),),
    )

    with pytest.raises(WallpaperUnavailable, match="未知"):
        library._resolve(library.collections[0])


async def test_draw_can_be_limited_to_one_game(tmp_path: Path) -> None:
    opener = FakeOpener(
        {
            **wiki_routes(),
            IMG_A: b"png-a",
            IMG_B: b"png-b",
            POST_URL: json.dumps(POST_PAYLOAD).encode("utf-8"),
            BBS_A: b"bbs-a",
        }
    )
    library = make_library(
        tmp_path,
        opener,
        collections=(
            WallpaperCollection("绝区零", "wiki", 689, "zzz"),
            WallpaperCollection("原神", "post", 70866259),
        ),
        rng=random.Random(0),
    )

    wallpaper, data = await library.draw("原神")

    assert wallpaper.game == "原神"
    assert data == b"bbs-a"
    assert all(WIKI_URL not in call for call in opener.calls)


async def test_draw_rejects_unknown_game(tmp_path: Path) -> None:
    library = make_library(tmp_path, FakeOpener(wiki_routes()))

    with pytest.raises(WallpaperUnavailable, match="没有"):
        await library.draw("原神")


def test_match_wallpaper_game_aliases() -> None:
    assert match_wallpaper_game("原神") == "原神"
    assert match_wallpaper_game(" GENSHIN ") == "原神"
    assert match_wallpaper_game("星铁") == "崩坏：星穹铁道"
    assert match_wallpaper_game("绝区") == "绝区零"
    assert match_wallpaper_game("崩坏3") is None
    assert match_wallpaper_game("") is None


SEARCH_API = "https://bbs-api.miyoushe.com/post/wapi/searchPosts"
INFO_SUFFIX = "?x-oss-process=image/info"
BBS_FAN_A = "https://upload-bbs.miyoushe.com/upload/2026/01/01/fan-a.jpg"
BBS_FAN_B = "https://upload-bbs.miyoushe.com/upload/2026/01/01/fan-b.jpg"


def search_url(keyword: str) -> str:
    params = urllib.parse.urlencode(
        {"keyword": keyword, "size": 20, "offset": 0}
    )
    return f"{SEARCH_API}?{params}"


def search_payload(images: list[str]) -> dict:
    return {
        "retcode": 0,
        "data": {
            "posts": [
                {"post": {"post_id": 1, "images": images}},
            ]
        },
    }


def info_payload(width: int, height: int, size: int = 4096) -> bytes:
    return json.dumps(
        {
            "ImageWidth": {"value": str(width)},
            "ImageHeight": {"value": str(height)},
            "FileSize": {"value": str(size)},
        }
    ).encode("utf-8")


def test_search_images_are_filtered() -> None:
    posts = [
        {
            "post": {
                "images": [
                    BBS_FAN_A,
                    BBS_FAN_A,
                    "https://img.example.com/a.jpg",
                    BBS_FAN_B,
                ]
            }
        },
        {"post": {}},
        {"msg": "没有图片"},
        "oops",
    ]

    urls = wheel_wallpapers._search_image_urls(posts)

    assert urls == [BBS_FAN_A, BBS_FAN_B]


async def test_draw_fanart_uses_search_and_skips_small_images(
    tmp_path: Path,
) -> None:
    keyword = "原神壁纸"
    opener = FakeOpener(
        {
            search_url(keyword): json.dumps(
                search_payload([BBS_FAN_A, BBS_FAN_B])
            ).encode("utf-8"),
            BBS_FAN_A + INFO_SUFFIX: info_payload(828, 1792),
            BBS_FAN_B + INFO_SUFFIX: info_payload(3024, 4032),
            BBS_FAN_B: b"fan-art",
        }
    )
    library = make_library(
        tmp_path,
        opener,
        collections=(),
        fanart_collections=(
            WallpaperCollection("原神", "search", 0, query=keyword),
        ),
        rng=random.Random(0),
    )

    wallpaper, data = await library.draw(fanart=True)

    assert wallpaper.source == "fanart"
    assert wallpaper.game == "原神"
    assert wallpaper.collection == "米游社「原神壁纸」"
    assert data == b"fan-art"
    assert BBS_FAN_A not in opener.calls


async def test_draw_fanart_keeps_portrait_art_but_drops_screenshots(
    tmp_path: Path,
) -> None:
    keyword = "绝区零同人图"
    art = "https://upload-bbs.miyoushe.com/upload/2026/01/01/art.jpg"
    shot = "https://upload-bbs.miyoushe.com/upload/2026/01/01/shot.jpg"
    opener = FakeOpener(
        {
            search_url(keyword): json.dumps(
                search_payload([shot, art])
            ).encode("utf-8"),
            shot + INFO_SUFFIX: info_payload(1280, 645),
            art + INFO_SUFFIX: info_payload(1200, 1600),
            art: b"portrait-art",
        }
    )
    library = make_library(
        tmp_path,
        opener,
        collections=(),
        fanart_collections=(
            WallpaperCollection("绝区零", "search", 0, query=keyword),
        ),
        rng=random.Random(0),
    )

    wallpaper, data = await library.draw(fanart=True)

    assert wallpaper.source == "fanart"
    assert data == b"portrait-art"
    assert shot not in opener.calls


async def test_draw_fanart_keeps_images_when_info_is_missing(
    tmp_path: Path,
) -> None:
    keyword = "绝区零壁纸"
    opener = FakeOpener(
        {
            search_url(keyword): json.dumps(
                search_payload([BBS_FAN_A])
            ).encode("utf-8"),
            BBS_FAN_A: b"fan-art",
        }
    )
    library = make_library(
        tmp_path,
        opener,
        collections=(),
        fanart_collections=(
            WallpaperCollection("绝区零", "search", 0, query=keyword),
        ),
        rng=random.Random(0),
    )

    wallpaper, data = await library.draw(fanart=True)

    assert wallpaper.source == "fanart"
    assert data == b"fan-art"


async def test_draw_fanart_rejects_unknown_game(tmp_path: Path) -> None:
    library = make_library(
        tmp_path,
        FakeOpener({}),
        collections=(),
        fanart_collections=(
            WallpaperCollection("原神", "search", 0, query="原神壁纸"),
        ),
    )

    with pytest.raises(WallpaperUnavailable, match="二创"):
        await library.draw("绝区零", fanart=True)


def test_search_cache_path_depends_on_keyword(tmp_path: Path) -> None:
    library = make_library(tmp_path, FakeOpener({}), collections=())

    first = library._cache_path(
        WallpaperCollection("原神", "search", 0, query="原神壁纸")
    )
    second = library._cache_path(
        WallpaperCollection("原神", "search", 0, query="绝区零壁纸")
    )

    assert first != second
    assert first.parent == tmp_path / "wallpapers"
