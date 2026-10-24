from __future__ import annotations

import asyncio
import importlib
import random
import re
import shutil
import threading
import time
import urllib.request
import zipfile
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

from nonebot import logger
from nonebot.utils import run_sync


class MemeGenerationError(RuntimeError):
    pass


class MemeAssetMissing(MemeGenerationError):
    pass


DEFAULT_BLOCKED_MEME_TERMS: tuple[str, ...] = (
    # 库洛游戏: 公司、旗下游戏与模板统一前缀
    "库洛", "kuro", "kurogames", "战双", "帕弥什", "松伦",
    # 鸣潮: 游戏名与玩家称呼
    "鸣潮", "呜潮", "鸣批", "潮批", "wuwa", "wuthering",
    # 鸣潮: 角色与昵称
    "阿布", "今汐", "汐汐", "长离", "离妃", "守岸人", "岸宝", "椿", "大傻椿", "傻椿", "红椿",
    "珂莱塔", "卡提西亚", "卡提希娅", "卡提", "洛可可", "菲比", "布兰特", "赞妮", "坎特蕾拉",
    "夏空", "露帕", "弗洛洛", "奥古斯塔", "尤诺", "优诺", "嘉贝丽娜", "嘉贝莉娜", "仇远",
    "千咲", "秧秧", "炽霞", "白芷", "安可", "维里奈", "小维", "鉴心", "凌阳", "雪豹",
    "卡卡罗", "忌炎", "相里要", "灯灯", "釉瑚", "漂泊者", "阿漂", "散华", "丹瑾", "桃祈",
    "渊武", "莫特斐", "秋水", "折枝", "吟霖",
    # 鸣潮: 怪物、阵营与梗
    "无冠者", "哀声鸷", "无妄者", "赫卡忒", "咕咕河豚", "叮咚咚", "飞廉之猩",
    "今州", "瑝珑", "黑海岸", "残星会", "拉古那", "黎那汐塔", "岁主", "声骸",
    # 鸣潮: 表情模板英文键名(keywords 可能是通用词, 只能靠 key 拦)
    "camellya", "carlotta", "cartethyia", "changli", "chisa", "chouyuan", "iuno",
    "jinhsi", "jiyan", "lingyang", "lupa", "mortefi", "phoebe", "phrolova", "roccia",
    "rover", "shorekeeper", "verina", "yangyang", "zani", "zhezhi", "galbrena", "abby",
)

_TERM_SPLIT = re.compile(r"[,，、;；\s]+")


def parse_blocked_terms(extra: str | None) -> tuple[str, ...]:
    """内置屏蔽词 + 配置里追加的词，去空白去重。"""

    terms: list[str] = list(DEFAULT_BLOCKED_MEME_TERMS)
    if extra:
        for term in _TERM_SPLIT.split(extra):
            term = term.strip()
            if term and term not in terms:
                terms.append(term)
    return tuple(terms)


def _load_backend() -> Any | None:
    try:
        backend = importlib.import_module("meme_generator")
    except (ImportError, OSError) as error:
        logger.warning(f"[mantou-affection] memes 生成器不可用: {error}")
        return None
    if not hasattr(backend, "MemeParamsType"):
        logger.warning(
            "[mantou-affection] 当前 meme-generator 版本不兼容，"
            "请安装 0.1.x（推荐 0.1.14）以支持外部表情仓库"
        )
    return backend


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
        repos: MemeRepoManager | None = None,
        blocked_terms: Iterable[str] | None = None,
    ):
        self.backend = backend if backend is not None else _load_backend()
        self.rng = rng or random.Random()
        self.avatar_fetcher = avatar_fetcher or _fetch_avatar
        self.bot_name = bot_name
        self.repos = repos
        self.blocked_terms = (
            parse_blocked_terms(None) if blocked_terms is None else tuple(blocked_terms)
        )
        self._blocked_logged = False

    def _is_blocked(self, meme: Any) -> bool:
        key = str(getattr(meme, "key", "") or "").lower()
        keywords = [str(kw).lower() for kw in (getattr(meme, "keywords", None) or ())]
        for term in self.blocked_terms:
            term = term.lower()
            if term and (term in key or any(term in kw for kw in keywords)):
                return True
        return False

    @staticmethod
    def _kind(params: Any) -> str | None:
        if params.max_images == 0 and params.min_texts <= 1 <= params.max_texts:
            return "text"
        if params.min_images == params.max_images == 1 and params.min_texts == 0:
            return "single"
        if params.min_images == params.max_images == 2 and params.min_texts == 0:
            return "double"
        return None

    def draw(self) -> tuple[Any, str]:
        """从非屏蔽候选里随机抽一个模板，返回 (meme, kind)。"""

        backend = self.backend
        if backend is None:
            raise MemeGenerationError("meme-generator 未安装")
        if not hasattr(backend, "MemeParamsType"):
            raise MemeGenerationError("meme-generator 版本不兼容，需要 0.1.x（如 0.1.14）")
        if self.repos is not None:
            self.repos.kickoff()
        try:
            available = backend.get_memes()
        except Exception as error:
            raise MemeGenerationError("读取表情列表失败") from error
        candidates = []
        blocked_count = 0
        for meme in available:
            if self._is_blocked(meme):
                blocked_count += 1
                continue
            kind = self._kind(meme.params_type)
            if kind is not None:
                candidates.append((meme, kind))
        if blocked_count and not self._blocked_logged:
            self._blocked_logged = True
            logger.info(f"[mantou-affection] 已按屏蔽词过滤 {blocked_count} 个表情模板")
        if not candidates:
            raise MemeGenerationError("没有兼容的表情模板")
        return self.rng.choice(candidates)

    async def generate_with(
        self,
        meme: Any,
        kind: str,
        *,
        bot: Any,
        user_id: str,
        nickname: str,
    ) -> bytes:
        """用指定模板生成表情。"""

        texts = [nickname] if kind == "text" else []
        images = []
        if kind != "text":
            ids = [user_id] if kind == "single" else [str(bot.self_id), user_id]
            for current_id in ids:
                try:
                    data = await self.avatar_fetcher(current_id)
                except Exception as error:
                    raise MemeGenerationError("头像下载失败") from error
                images.append(data)
        try:
            result = await run_sync(meme)(images=images, texts=texts, args={})
        except FileNotFoundError as error:
            raise MemeAssetMissing(str(error)) from error
        except MemeGenerationError:
            raise
        except Exception as error:
            raise MemeGenerationError("表情生成失败") from error
        if isinstance(result, bytes):
            return result
        getvalue = getattr(result, "getvalue", None)
        if callable(getvalue):
            return getvalue()
        raise MemeGenerationError(f"表情结果格式异常: {type(result).__name__}")

    async def generate(self, *, bot: Any, user_id: str, nickname: str) -> bytes:
        meme, kind = self.draw()
        return await self.generate_with(
            meme, kind, bot=bot, user_id=user_id, nickname=nickname
        )


def _load_resource_check() -> Callable[[], None] | None:
    try:
        module = importlib.import_module("meme_generator.download")
        coroutine = module.check_resources
    except (ImportError, AttributeError, OSError) as error:
        logger.warning(f"[mantou-affection] memes 素材检查不可用: {error}")
        return None

    async def runner() -> None:
        try:
            await coroutine()
        except Exception:
            logger.exception("[mantou-affection] memes 素材检查失败")

    def schedule() -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            threading.Thread(target=lambda: asyncio.run(runner()), daemon=True).start()
        else:
            task = loop.create_task(runner())
            task.add_done_callback(lambda _task: None)

    return schedule


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


@dataclass(frozen=True)
class ExtraMemeRepo:
    name: str
    slug: str
    subdir: str


EXTRA_MEME_REPOS: tuple[ExtraMemeRepo, ...] = (
    ExtraMemeRepo("mantou-memes", "TonyLiangP2010405/mantou-memes", "memes"),
    ExtraMemeRepo("meme-generator-contrib", "MemeCrafters/meme-generator-contrib", "memes"),
    ExtraMemeRepo("meme_emoji", "anyliew/meme_emoji", "emoji"),
    ExtraMemeRepo("tudou-meme", "LRZ9712/tudou-meme", "meme"),
)


def repo_download_urls(slug: str) -> list[str]:
    archive = f"https://github.com/{slug}/archive/refs/heads/main.zip"
    return [
        f"https://codeload.github.com/{slug}/zip/refs/heads/main",
        f"https://ghproxy.net/{archive}",
        f"https://gh-proxy.com/{archive}",
        f"https://ghfast.top/{archive}",
    ]


class MemeRepoManager:
    """外部表情仓库的自检测与自下载，下载完成后通过 load_memes 挂载。"""

    def __init__(
        self,
        *,
        backend: Any | None = None,
        base_dir: Path | None = None,
        repos: Iterable[ExtraMemeRepo] = EXTRA_MEME_REPOS,
        enabled: bool = True,
        opener: Callable[..., Any] | None = None,
    ):
        self.backend = backend if backend is not None else _load_backend()
        self.base_dir = Path(base_dir) if base_dir is not None else None
        self.repos = tuple(repos)
        self.enabled = enabled
        self.opener = opener or urllib.request.urlopen
        self._started = False
        self._loaded = False
        self._task: asyncio.Task | None = None

    def repo_dir(self, repo: ExtraMemeRepo) -> Path:
        if self.base_dir is None:
            raise MemeGenerationError("表情仓库目录未配置")
        return self.base_dir / repo.name

    def memes_dir(self, repo: ExtraMemeRepo) -> Path:
        return self.repo_dir(repo) / repo.subdir

    def _template_count(self, repo: ExtraMemeRepo) -> int:
        directory = self.memes_dir(repo)
        if not directory.is_dir():
            return 0
        return sum(
            (child / "__init__.py").is_file()
            for child in directory.iterdir()
            if child.is_dir()
        )

    def _repo_ready(self, repo: ExtraMemeRepo) -> bool:
        return self._template_count(repo) > 0

    def _download_repo(self, repo: ExtraMemeRepo) -> None:
        data: bytes | None = None
        last_error: Exception | None = None
        for url in repo_download_urls(repo.slug):
            try:
                with self.opener(url, timeout=60) as response:
                    data = response.read()
                break
            except Exception as error:
                last_error = error
                logger.warning(f"[mantou-affection] 表情仓库 {repo.name} 下载源不可用: {error}")
        if data is None:
            raise MemeGenerationError(f"表情仓库 {repo.name} 下载失败: {last_error}")
        self._extract_repo(repo, data)

    def _extract_repo(self, repo: ExtraMemeRepo, data: bytes) -> None:
        target = self.repo_dir(repo)
        staging = target.parent / f".{target.name}.staging"
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(BytesIO(data)) as archive:
                archive.extractall(staging)
            entries = list(staging.iterdir())
            shutil.rmtree(target, ignore_errors=True)
            if len(entries) == 1 and entries[0].is_dir():
                entries[0].rename(target)
                shutil.rmtree(staging, ignore_errors=True)
            else:
                staging.rename(target)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    async def ensure_loaded(self) -> None:
        if self._loaded or not self.enabled or self.backend is None or self.base_dir is None:
            return
        self.base_dir.mkdir(parents=True, exist_ok=True)
        total = len(self.repos)
        loaded: list[str] = []
        failed: list[str] = []
        for index, repo in enumerate(self.repos, start=1):
            progress = f"[{index}/{total}]"
            try:
                template_count = self._template_count(repo)
                if template_count:
                    logger.info(
                        f"[mantou-affection] {progress} 表情仓库 {repo.name}："
                        f"检测到 {template_count} 个模板"
                    )
                else:
                    logger.info(
                        f"[mantou-affection] {progress} 表情仓库 {repo.name}："
                        "未检测到有效模板，开始下载（文件较大，请耐心等待）"
                    )
                    await run_sync(self._download_repo)(repo)
                    template_count = self._template_count(repo)
                    if template_count == 0:
                        raise MemeGenerationError(
                            f"表情仓库 {repo.name} 下载完成后没有可用模板"
                        )
                    logger.info(
                        f"[mantou-affection] {progress} 表情仓库 {repo.name}："
                        f"下载完成，检测到 {template_count} 个模板"
                    )
                await run_sync(self.backend.load_memes)(self.memes_dir(repo))
                logger.info(
                    f"[mantou-affection] {progress} 表情仓库 {repo.name} 已加载"
                )
                loaded.append(repo.name)
            except Exception:
                failed.append(repo.name)
                logger.exception(
                    f"[mantou-affection] {progress} 表情仓库 {repo.name} 加载失败"
                )
        if failed:
            logger.warning(
                f"[mantou-affection] 表情仓库检查完成：可用 {len(loaded)}/{total}，"
                f"失败：{', '.join(failed)}"
            )
        else:
            logger.info(
                f"[mantou-affection] 表情仓库检查完成：可用 {len(loaded)}/{total}"
            )
        self._loaded = True

    def kickoff(self) -> None:
        """调度一次后台同步，重复调用安全。"""

        if self._started or not self.enabled or self.backend is None or self.base_dir is None:
            return
        self._started = True
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            threading.Thread(target=lambda: asyncio.run(self.ensure_loaded()), daemon=True).start()
        else:
            self._task = loop.create_task(self.ensure_loaded())
