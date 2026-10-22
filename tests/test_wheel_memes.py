import asyncio
import random
import zipfile
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest

from nonebot_plugin_mantou_affection.wheel_memes import (
    DEFAULT_BLOCKED_MEME_TERMS,
    EXTRA_MEME_REPOS,
    ExtraMemeRepo,
    MemeAssetMissing,
    MemeGenerationError,
    MemeGeneratorAdapter,
    MemeRepoManager,
    MemeResourceManager,
    parse_blocked_terms,
    repo_download_urls,
)


class FakeParams:
    def __init__(self, minimum: int, maximum: int, min_texts: int = 0, max_texts: int = 0):
        self.min_images = minimum
        self.max_images = maximum
        self.min_texts = min_texts
        self.max_texts = max_texts


class FakeMeme:
    def __init__(
        self,
        key: str,
        minimum: int,
        maximum: int,
        min_texts: int = 0,
        max_texts: int = 0,
        keywords=(),
    ):
        self.key = key
        self.keywords = list(keywords)
        self.params_type = FakeParams(minimum, maximum, min_texts, max_texts)
        self.result: object = BytesIO(b"generated")
        self.error: Exception | None = None
        self.calls = []

    def __call__(self, *, images=(), texts=(), args=None):
        self.calls.append((list(images), list(texts), args))
        if self.error is not None:
            raise self.error
        return self.result


class FakeBackend:
    MemeParamsType = FakeParams

    def __init__(self, memes=()):
        self.memes = list(memes)
        self.loaded_dirs = []

    def get_memes(self):
        return self.memes

    def load_memes(self, directory):
        self.loaded_dirs.append(Path(directory))


class FakeRepos:
    def __init__(self):
        self.kickoffs = 0

    def kickoff(self) -> None:
        self.kickoffs += 1


async def avatars(user_id: str) -> bytes:
    return f"avatar:{user_id}".encode()


def adapter_for(meme: FakeMeme, **kwargs) -> MemeGeneratorAdapter:
    return MemeGeneratorAdapter(
        backend=FakeBackend([meme]), rng=random.Random(0), avatar_fetcher=avatars, **kwargs
    )


async def test_text_meme_uses_group_nickname() -> None:
    meme = FakeMeme("text", 0, 0, min_texts=1, max_texts=1)
    result = await adapter_for(meme).generate(
        bot=SimpleNamespace(self_id=999), user_id="123", nickname="群昵称"
    )
    assert result == b"generated"
    images, texts, args = meme.calls[0]
    assert images == []
    assert texts == ["群昵称"]
    assert args == {}


async def test_single_meme_uses_user_avatar() -> None:
    meme = FakeMeme("single", 1, 1)
    await adapter_for(meme).generate(
        bot=SimpleNamespace(self_id=999), user_id="123", nickname="群昵称"
    )
    images, texts, _ = meme.calls[0]
    assert images == [b"avatar:123"]
    assert texts == []


async def test_double_meme_uses_bot_then_user_avatar() -> None:
    meme = FakeMeme("double", 2, 2)
    await adapter_for(meme).generate(
        bot=SimpleNamespace(self_id=999), user_id="123", nickname="群昵称"
    )
    images, _, _ = meme.calls[0]
    assert images == [b"avatar:999", b"avatar:123"]


async def test_incompatible_templates_are_not_candidates() -> None:
    backend = FakeBackend([
        FakeMeme("three", 3, 3),
        FakeMeme("two-texts", 0, 0, min_texts=2, max_texts=2),
    ])
    adapter = MemeGeneratorAdapter(backend=backend, avatar_fetcher=avatars)
    with pytest.raises(MemeGenerationError, match="没有兼容"):
        await adapter.generate(
            bot=SimpleNamespace(self_id=999), user_id="123", nickname="群昵称"
        )


async def test_blocked_meme_by_key_is_filtered() -> None:
    meme = FakeMeme("kurogames_jinhsi_eat", 1, 1, keywords=["吃"])
    adapter = adapter_for(meme)
    with pytest.raises(MemeGenerationError, match="没有兼容"):
        await adapter.generate(
            bot=SimpleNamespace(self_id=999), user_id="123", nickname="群昵称"
        )
    assert meme.calls == []


async def test_blocked_meme_by_keyword_is_filtered() -> None:
    meme = FakeMeme("some_steamed_buns", 1, 1, keywords=["今汐小笼包"])
    adapter = adapter_for(meme)
    with pytest.raises(MemeGenerationError, match="没有兼容"):
        await adapter.generate(
            bot=SimpleNamespace(self_id=999), user_id="123", nickname="群昵称"
        )
    assert meme.calls == []


async def test_blocked_meme_matching_is_case_insensitive() -> None:
    meme = FakeMeme("KuroGames_CAMELLYA_holdsign", 1, 1, keywords=["举牌"])
    adapter = adapter_for(meme)
    with pytest.raises(MemeGenerationError, match="没有兼容"):
        await adapter.generate(
            bot=SimpleNamespace(self_id=999), user_id="123", nickname="群昵称"
        )
    assert meme.calls == []


async def test_unblocked_meme_survives_default_blocklist() -> None:
    meme = FakeMeme("petpet", 1, 1, keywords=["摸", "摸摸头"])
    result = await adapter_for(meme).generate(
        bot=SimpleNamespace(self_id=999), user_id="123", nickname="群昵称"
    )
    assert result == b"generated"
    assert len(meme.calls) == 1


async def test_blocked_meme_falls_back_to_other_candidates() -> None:
    blocked = FakeMeme("kurogames_camellya", 1, 1, keywords=["椿"])
    normal = FakeMeme("petpet", 1, 1, keywords=["摸"])
    adapter = MemeGeneratorAdapter(
        backend=FakeBackend([blocked, normal]),
        rng=random.Random(0),
        avatar_fetcher=avatars,
    )
    result = await adapter.generate(
        bot=SimpleNamespace(self_id=999), user_id="123", nickname="群昵称"
    )
    assert result == b"generated"
    assert blocked.calls == []
    assert len(normal.calls) == 1


def test_draw_returns_unblocked_template_and_kind() -> None:
    blocked = FakeMeme("kurogames_camellya", 1, 1, keywords=["椿"])
    normal = FakeMeme("petpet", 1, 1, keywords=["摸"])
    adapter = MemeGeneratorAdapter(
        backend=FakeBackend([blocked, normal]),
        rng=random.Random(0),
        avatar_fetcher=avatars,
    )

    meme, kind = adapter.draw()

    assert meme is normal
    assert kind == "single"
    assert blocked.calls == []
    assert normal.calls == []


def test_draw_reports_text_kind() -> None:
    meme = FakeMeme("text", 0, 0, min_texts=1, max_texts=1)
    drawn, kind = adapter_for(meme).draw()

    assert drawn is meme
    assert kind == "text"


def test_draw_without_compatible_templates_raises() -> None:
    backend = FakeBackend([FakeMeme("three", 3, 3)])
    adapter = MemeGeneratorAdapter(backend=backend, avatar_fetcher=avatars)

    with pytest.raises(MemeGenerationError, match="没有兼容"):
        adapter.draw()


async def test_generate_with_uses_only_given_template() -> None:
    first = FakeMeme("first", 1, 1)
    second = FakeMeme("second", 1, 1)
    adapter = MemeGeneratorAdapter(
        backend=FakeBackend([first, second]), avatar_fetcher=avatars
    )

    result = await adapter.generate_with(
        second, "single", bot=SimpleNamespace(self_id=999), user_id="123", nickname="群昵称"
    )

    assert result == b"generated"
    assert first.calls == []
    assert len(second.calls) == 1
    images, texts, args = second.calls[0]
    assert images == [b"avatar:123"]
    assert texts == []
    assert args == {}


async def test_generate_composes_draw_and_generate_with(monkeypatch) -> None:
    first = FakeMeme("first", 1, 1)
    second = FakeMeme("second", 1, 1)
    adapter = MemeGeneratorAdapter(
        backend=FakeBackend([first, second]), avatar_fetcher=avatars
    )

    def pick_second():
        return second, "single"

    monkeypatch.setattr(adapter, "draw", pick_second)

    result = await adapter.generate(
        bot=SimpleNamespace(self_id=999), user_id="123", nickname="群昵称"
    )

    assert result == b"generated"
    assert first.calls == []
    assert len(second.calls) == 1


async def test_extra_blocked_terms_take_effect() -> None:
    meme = FakeMeme("rosetta", 1, 1, keywords=["罗塞塔"])
    adapter = adapter_for(meme, blocked_terms=["罗塞塔"])
    with pytest.raises(MemeGenerationError, match="没有兼容"):
        await adapter.generate(
            bot=SimpleNamespace(self_id=999), user_id="123", nickname="群昵称"
        )
    assert meme.calls == []


async def test_kuro_generic_meme_is_blocked_by_default() -> None:
    for key, keywords in (
        ("kurogames_rosetta", ["罗塞塔"]),
        ("kurogames_good_night", ["晚安"]),
        ("kurogames_brqd", ["百日签到"]),
    ):
        meme = FakeMeme(key, 1, 1, keywords=keywords)
        adapter = adapter_for(meme)
        with pytest.raises(MemeGenerationError, match="没有兼容"):
            await adapter.generate(
                bot=SimpleNamespace(self_id=999), user_id="123", nickname="群昵称"
            )
        assert meme.calls == []


def test_parse_blocked_terms_defaults_and_extra() -> None:
    terms = parse_blocked_terms(None)
    assert "鸣潮" in terms
    assert "jinhsi" in terms
    assert len(terms) == len(set(terms))
    extra = parse_blocked_terms("罗塞塔，松伦、 ;自定义词")
    assert "罗塞塔" in extra
    assert "自定义词" in extra
    # 内置词与追加词重复时不重复出现
    assert extra.count("松伦") == 1
    assert parse_blocked_terms("") == DEFAULT_BLOCKED_MEME_TERMS


async def test_incompatible_backend_version_is_rejected() -> None:
    class RustStyleBackend:
        def get_memes(self):
            return []

    adapter = MemeGeneratorAdapter(backend=RustStyleBackend(), avatar_fetcher=avatars)
    with pytest.raises(MemeGenerationError, match="版本不兼容"):
        await adapter.generate(
            bot=SimpleNamespace(self_id=999), user_id="123", nickname="群昵称"
        )


async def test_asset_missing_has_distinct_exception() -> None:
    meme = FakeMeme("single", 1, 1)
    meme.error = FileNotFoundError("petpet/base.png")
    with pytest.raises(MemeAssetMissing):
        await adapter_for(meme).generate(
            bot=SimpleNamespace(self_id=999), user_id="123", nickname="群昵称"
        )


async def test_generate_failure_is_wrapped() -> None:
    meme = FakeMeme("single", 1, 1)
    meme.error = RuntimeError("boom")
    with pytest.raises(MemeGenerationError, match="表情生成失败"):
        await adapter_for(meme).generate(
            bot=SimpleNamespace(self_id=999), user_id="123", nickname="群昵称"
        )


async def test_avatar_failure_is_wrapped() -> None:
    async def broken(_user_id: str) -> bytes:
        raise OSError("offline")

    meme = FakeMeme("single", 1, 1)
    adapter = MemeGeneratorAdapter(backend=FakeBackend([meme]), avatar_fetcher=broken)
    with pytest.raises(MemeGenerationError, match="头像"):
        await adapter.generate(
            bot=SimpleNamespace(self_id=999), user_id="123", nickname="群昵称"
        )


async def test_generate_kickstarts_repo_sync() -> None:
    repos = FakeRepos()
    meme = FakeMeme("text", 0, 0, min_texts=1, max_texts=1)
    adapter = adapter_for(meme, repos=repos)
    await adapter.generate(bot=SimpleNamespace(self_id=999), user_id="123", nickname="群昵称")
    assert repos.kickoffs == 1


def test_resource_startup_check_runs_once_and_skips_memes_owned_check() -> None:
    calls = []
    clock_values = iter([100.0, 101.0, 102.0])
    manager = MemeResourceManager(
        check=lambda: calls.append("check"), clock=lambda: next(clock_values)
    )
    assert manager.check_on_startup(handled_by_memes_plugin=False) is True
    assert manager.check_on_startup(handled_by_memes_plugin=False) is False
    assert calls == ["check"]

    skipped = MemeResourceManager(check=lambda: calls.append("duplicate"), clock=lambda: 200.0)
    assert skipped.check_on_startup(handled_by_memes_plugin=True) is False
    assert calls == ["check"]


def test_resource_missing_recheck_is_throttled() -> None:
    calls = []
    current = [1000.0]
    manager = MemeResourceManager(
        check=lambda: calls.append("check"), clock=lambda: current[0], retry_seconds=600
    )
    assert manager.check_after_missing() is True
    assert manager.check_after_missing() is False
    current[0] += 601
    assert manager.check_after_missing() is True
    assert calls == ["check", "check"]


def make_zip(files: dict[str, bytes]) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


class FakeResponse:
    def __init__(self, data: bytes):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *args) -> bool:
        return False

    def read(self) -> bytes:
        return self.data


REPO = ExtraMemeRepo("demo", "owner/demo", "emoji")
DEMO_ZIP = make_zip({"demo-main/emoji/meme1/__init__.py": b"# meme"})


def repo_manager(tmp_path: Path, backend=None, **kwargs) -> MemeRepoManager:
    return MemeRepoManager(
        backend=backend if backend is not None else FakeBackend(),
        base_dir=tmp_path / "repos",
        repos=(REPO,),
        **kwargs,
    )


async def test_ready_repo_skips_download_and_loads(tmp_path: Path) -> None:
    backend = FakeBackend()
    manager = repo_manager(tmp_path, backend, opener=lambda *a, **k: pytest.fail("不应下载"))
    ready = manager.memes_dir(REPO)
    ready.mkdir(parents=True)
    (ready / "meme1").mkdir()

    await manager.ensure_loaded()

    assert backend.loaded_dirs == [ready]


async def test_missing_repo_is_downloaded_extracted_and_loaded(tmp_path: Path) -> None:
    backend = FakeBackend()
    requested = []

    def opener(url, timeout):
        requested.append(url)
        return FakeResponse(DEMO_ZIP)

    manager = repo_manager(tmp_path, backend, opener=opener)
    await manager.ensure_loaded()

    assert requested == [repo_download_urls(REPO.slug)[0]]
    assert (manager.memes_dir(REPO) / "meme1" / "__init__.py").is_file()
    assert backend.loaded_dirs == [manager.memes_dir(REPO)]
    assert not (manager.repo_dir(REPO).parent / f".{REPO.name}.staging").exists()


async def test_download_falls_back_to_next_mirror(tmp_path: Path) -> None:
    backend = FakeBackend()
    requested = []

    def opener(url, timeout):
        requested.append(url)
        if len(requested) == 1:
            raise OSError("mirror down")
        return FakeResponse(DEMO_ZIP)

    manager = repo_manager(tmp_path, backend, opener=opener)
    await manager.ensure_loaded()

    assert requested == repo_download_urls(REPO.slug)[:2]
    assert backend.loaded_dirs == [manager.memes_dir(REPO)]


async def test_all_mirrors_failing_keeps_builtin_memes(tmp_path: Path) -> None:
    backend = FakeBackend()

    def opener(url, timeout):
        raise OSError("offline")

    manager = repo_manager(tmp_path, backend, opener=opener)
    await manager.ensure_loaded()

    assert backend.loaded_dirs == []
    assert manager._loaded is True


async def test_disabled_or_unconfigured_manager_is_noop(tmp_path: Path) -> None:
    backend = FakeBackend()
    disabled = repo_manager(tmp_path, backend, enabled=False)
    await disabled.ensure_loaded()
    no_dir = MemeRepoManager(backend=backend, base_dir=None, repos=(REPO,))
    await no_dir.ensure_loaded()
    no_backend = MemeRepoManager(backend=None, base_dir=tmp_path, repos=(REPO,))
    no_backend.backend = None
    await no_backend.ensure_loaded()
    assert backend.loaded_dirs == []


async def test_kickoff_runs_only_once(tmp_path: Path) -> None:
    backend = FakeBackend()
    manager = repo_manager(tmp_path, backend, opener=lambda url, timeout: FakeResponse(DEMO_ZIP))
    manager.kickoff()
    manager.kickoff()
    await asyncio.sleep(0.05)
    assert manager._started is True
    assert manager._loaded is True
    assert backend.loaded_dirs == [manager.memes_dir(REPO)]


def test_extra_repo_registry_matches_requested_repos() -> None:
    assert [(repo.slug, repo.subdir) for repo in EXTRA_MEME_REPOS] == [
        ("MemeCrafters/meme-generator-contrib", "memes"),
        ("anyliew/meme_emoji", "emoji"),
        ("LRZ9712/tudou-meme", "meme"),
    ]
    urls = repo_download_urls("owner/demo")
    assert urls[0] == "https://codeload.github.com/owner/demo/zip/refs/heads/main"
    assert len(urls) >= 2
