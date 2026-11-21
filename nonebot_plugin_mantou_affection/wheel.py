from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from typing import TYPE_CHECKING

from nonebot import logger
from nonebot.adapters.onebot.v11 import MessageSegment

from .logic import NEWBIE_PROTECTION_NOTE, protect_deduction
from .service import normalize_nickname
from .wheel_memes import MemeAssetMissing, MemeGenerationError
from .wheel_stickers import StickerAssetMissing, StickerError, pick_local_sticker
from .wheel_wallpapers import Wallpaper, WallpaperUnavailable

if TYPE_CHECKING:
    from .ambient import EventCoordinator


@dataclass(frozen=True)
class WheelOutcome:
    kind: str
    value: int = 0


WHEEL_OUTCOMES: tuple[tuple[WheelOutcome, int], ...] = (
    (WheelOutcome("meme"), 7),
    (WheelOutcome("mantou_sticker"), 3),
    (WheelOutcome("taozi"), 1),
    (WheelOutcome("wallpaper"), 10),
    (WheelOutcome("image", 1), 2),
    (WheelOutcome("image", 2), 1),
    (WheelOutcome("poke", 1), 7),
    (WheelOutcome("poke", 3), 5),
    (WheelOutcome("poke", 5), 3),
    (WheelOutcome("poke", 10), 2),
    (WheelOutcome("poke", 20), 1),
    (WheelOutcome("panel", 1), 5),
    (WheelOutcome("panel_rank", 1), 3),
    (WheelOutcome("affection", 1), 7),
    (WheelOutcome("affection", 2), 6),
    (WheelOutcome("affection", 3), 5),
    (WheelOutcome("affection", 5), 4),
    (WheelOutcome("affection", 10), 3),
    (WheelOutcome("affection", 20), 1),
    (WheelOutcome("affection", -1), 7),
    (WheelOutcome("affection", -2), 6),
    (WheelOutcome("affection", -3), 5),
    (WheelOutcome("affection", -5), 3),
    (WheelOutcome("affection", -10), 2),
    (WheelOutcome("affection", -20), 1),
)


def draw_wheel_outcome(rng: random.Random) -> WheelOutcome:
    roll = rng.randrange(100)
    cursor = 0
    for outcome, weight in WHEEL_OUTCOMES:
        cursor += weight
        if roll < cursor:
            return outcome
    raise AssertionError("转盘权重必须合计 100")


WHEEL_MAX_PARTICIPANTS = 4
WHEEL_TOTAL_SEATS = WHEEL_MAX_PARTICIPANTS + 1
WHEEL_JOIN_COST = 2
WHEEL_TAOZI_COST = 10


def _join_weights(items: list[tuple[int, int]], prefix: str) -> str:
    return "、".join(f"{prefix}{value} {weight}%" for value, weight in items)


def format_wheel_rules(timeout: int, newbie_below: int = 50) -> str:
    meme_weight = sum(weight for outcome, weight in WHEEL_OUTCOMES if outcome.kind == "meme")
    mantou_sticker_weight = sum(
        weight for outcome, weight in WHEEL_OUTCOMES if outcome.kind == "mantou_sticker"
    )
    taozi_weight = sum(weight for outcome, weight in WHEEL_OUTCOMES if outcome.kind == "taozi")
    wallpaper_weight = sum(
        weight for outcome, weight in WHEEL_OUTCOMES if outcome.kind == "wallpaper"
    )
    images = [(o.value, w) for o, w in WHEEL_OUTCOMES if o.kind == "image"]
    pokes = [(o.value, w) for o, w in WHEEL_OUTCOMES if o.kind == "poke"]
    panels = sum(weight for outcome, weight in WHEEL_OUTCOMES if outcome.kind == "panel")
    panel_ranks = sum(weight for outcome, weight in WHEEL_OUTCOMES if outcome.kind == "panel_rank")
    gains = [(o.value, w) for o, w in WHEEL_OUTCOMES if o.kind == "affection" and o.value > 0]
    losses = [(-o.value, w) for o, w in WHEEL_OUTCOMES if o.kind == "affection" and o.value < 0]

    return (
        " 馒头命运齿轮转盘出现了！"
        f"含触发者最多 {WHEEL_TOTAL_SEATS} 人，参与需要扣除 {WHEEL_JOIN_COST} 点好感度。\n"
        f"其他群友发送“参与”即可加入，{timeout} 秒后自动开转，每人独立抽取一次。\n"
        "倒计时结束前都可以参与，开转后不再接受新的参与者。\n"
        f"好感度低于 {newbie_below} 的群友有新手保护：所有扣分只扣 1/3。\n"
        "转盘各项概率：\n"
        f"· 馒头制作随机表情 {meme_weight}%\n"
        f"· 馒头表情包 {mantou_sticker_weight}%（随机发一张）\n"
        f"· 桃子表情包 {taozi_weight}%（换一张需 {WHEEL_TAOZI_COST} 点好感）\n"
        f"· 随机壁纸 {wallpaper_weight}%（米游社二创图，原神 / 星铁 / 绝区零随机）\n"
        f"· 馒头主动戳一戳：{_join_weights(pokes, '×')}\n"
        f"· 角色面板次数 {panels}%（在喵喵插件里免费查看一次角色面板）\n"
        f"· 群面板排名次数 {panel_ranks}%（在喵喵插件里免费查看一次群面板排名）\n"
        f"· 图像处理次数：{_join_weights(images, '×')}（每张图消耗 1 次）\n"
        f"· 好感增加：{_join_weights(gains, '+')}\n"
        f"· 好感减少：{_join_weights(losses, '-')}"
    )


WHEEL_MEME_ASSET_MISSING_REPLY = " 表情素材尚未下载完成，本次不重抽"
WHEEL_MEME_FAILED_REPLY = " 本次表情生成失败，不重抽"

WHEEL_JOIN_COST_REPLY = (
    " 参与成功，已扣除 {cost} 点好感度{note}，当前 {affection}，"
    "已加入本轮转盘，倒计时结束就开转。"
)
WHEEL_JOIN_POOR_REPLY = " 好感度不足 {cost} 点，暂时无法参与本轮转盘（当前 {affection}）。"

WHEEL_TAOZI_PROMPT_REPLY = (
    " 你抽中了「桃子表情包」！花 {cost} 点好感{note}可以换一张随机桃子表情包图片，"
    "当前 {affection}。要换吗？请在 {timeout} 秒内回复「要」或「不要」。"
)
WHEEL_TAOZI_CONFIRMED_REPLY = " 已扣除 {cost} 点好感{note}，桃子的表情包「{caption}」来啦："
WHEEL_TAOZI_DECLINED_REPLY = " 已放弃兑换桃子的表情包，好感不变"
WHEEL_TAOZI_TIMEOUT_REPLY = " 桃子表情包确认超时，本次没有兑换，好感不变"
WHEEL_TAOZI_POOR_REPLY = " 好感不足 {cost} 点，本次没有兑换，当前 {affection}"
WHEEL_TAOZI_MISSING_REPLY = " 桃子表情包素材还没准备好，本次不重抽"
WHEEL_TAOZI_FAILED_REPLY = " 本次桃子表情包发送失败，不重抽"

WHEEL_FANART_REPLY = " 🎨 抽到随机二创图（{label}）："
WHEEL_FANART_FALLBACK_REPLY = " 🖼️ 二创图源没取到，改发官方壁纸（{label}）："
WHEEL_WALLPAPER_FAILED_REPLY = " 这次没取到壁纸，本次不重抽"


def format_sticker_prompt(
    affection: int, timeout: int, cost: int = WHEEL_TAOZI_COST, note: str = ""
) -> str:
    return WHEEL_TAOZI_PROMPT_REPLY.format(
        cost=cost, affection=affection, timeout=timeout, note=note
    )


@dataclass
class WheelParticipant:
    user_id: str
    nickname: str


class PendingWheel:
    def __init__(self, group_id: str, user_id: str, nickname: str, *, bot, send):
        self.group_id = group_id
        self.trigger = WheelParticipant(user_id, nickname)
        self.participants: list[WheelParticipant] = []
        self.bot = bot
        self.send = send
        self.accepting = True
        self.task: asyncio.Task | None = None


class PendingStickerConfirm:
    def __init__(
        self,
        group_id: str,
        user_id: str,
        nickname: str,
        *,
        bot,
        send,
        cost: int = WHEEL_TAOZI_COST,
        note: str = "",
    ):
        self.group_id = group_id
        self.user_id = user_id
        self.nickname = nickname
        self.bot = bot
        self.send = send
        self.cost = cost
        self.note = note
        self.accepting = True
        self.task: asyncio.Task | None = None


class WheelCoordinator:
    def __init__(
        self,
        service,
        config,
        *,
        events: EventCoordinator | None = None,
        rng: random.Random | None = None,
        memes=None,
        resources=None,
        stickers=None,
        wallpapers=None,
        image_quota=None,
        panel_quota=None,
        panel_rank_quota=None,
        poke_delay: float = 0.2,
    ):
        self.service = service
        self.config = config
        self.events = events
        self.rng = rng or random.Random()
        self.memes = memes
        self.resources = resources
        self.stickers = stickers
        self.wallpapers = wallpapers
        self.image_quota = image_quota
        self.panel_quota = panel_quota
        self.panel_rank_quota = panel_rank_quota
        self.poke_delay = max(float(poke_delay), 0.0)
        self.choice_timeout = float(config.mantou_affection_wheel_timeout)
        self.sticker_confirm_timeout = float(
            config.mantou_affection_wheel_taozi_timeout
        )
        self.pending: dict[str, PendingWheel] = {}
        self.sticker_confirms: dict[tuple[str, str], PendingStickerConfirm] = {}
        self._lock = asyncio.Lock()

    def busy(self, group_id: str) -> bool:
        key = str(group_id)
        if key in self.pending:
            return True
        return self.events is not None and self.events.busy(key)

    def discard(self, pending: PendingWheel) -> None:
        if self.pending.get(pending.group_id) is pending:
            self.pending.pop(pending.group_id, None)

    async def start(self, *, group_id: str, user_id: str, nickname: str, bot, send) -> bool:
        group = str(group_id)
        user = str(user_id)
        async with self._lock:
            if self.busy(group):
                return False
            pending = PendingWheel(
                group,
                user,
                normalize_nickname(nickname, user),
                bot=bot,
                send=send,
            )
            self.pending[group] = pending
        try:
            rules = format_wheel_rules(
                int(self.choice_timeout),
                self.config.mantou_affection_newbie_protection_below,
            )
            await send(MessageSegment.at(user) + MessageSegment.text(rules))
        except Exception:
            self.discard(pending)
            raise
        async with self._lock:
            if self.pending.get(group) is pending and pending.accepting:
                pending.task = asyncio.create_task(self._auto_start(pending))
        return True

    async def answer(self, group_id: str, user_id: str, nickname: str, text: str) -> bool:
        keyword = text.strip()
        user = str(user_id)
        sticker_confirm = self.sticker_confirms.get((str(group_id), user))
        if (
            sticker_confirm is not None
            and sticker_confirm.accepting
            and keyword in {"要", "不要"}
        ):
            await self._answer_sticker_confirm(
                sticker_confirm, accepted=keyword == "要"
            )
            return True

        pending = self.pending.get(str(group_id))
        if pending is None or keyword != "参与":
            return False
        return await self._join(pending, user, nickname)

    async def _join(self, pending: PendingWheel, user_id: str, nickname: str) -> bool:
        async with self._lock:
            if not pending.accepting or self.pending.get(pending.group_id) is not pending:
                return True
            if user_id == pending.trigger.user_id:
                await pending.send("触发者已自动加入本轮转盘，无需再发送参与。")
                return True
            if any(item.user_id == user_id for item in pending.participants):
                await pending.send("你已经参与本轮转盘了。")
                return True
            if len(pending.participants) >= WHEEL_MAX_PARTICIPANTS:
                await pending.send(f"本轮转盘名额已满（含触发者最多 {WHEEL_TOTAL_SEATS} 人）。")
                return True
            name = normalize_nickname(nickname, user_id)
            cost = WHEEL_JOIN_COST
            current = (await self.service.profile(pending.group_id, user_id)).affection
            if current < cost:
                await pending.send(
                    MessageSegment.at(user_id)
                    + MessageSegment.text(
                        WHEEL_JOIN_POOR_REPLY.format(cost=cost, affection=current)
                    )
                )
                return True
            applied, profile, protected = await self.service.adjust(
                pending.group_id, user_id, name, -cost
            )
            note = NEWBIE_PROTECTION_NOTE if protected else ""
            pending.participants.append(WheelParticipant(user_id, name))
            if applied < 0:
                await self.service.send_gift(
                    pending.send, pending.group_id, user_id, name
                )
            await pending.send(
                MessageSegment.at(user_id)
                + MessageSegment.text(
                    WHEEL_JOIN_COST_REPLY.format(
                        cost=-applied, affection=profile.affection, note=note
                    )
                )
            )
        return True

    async def _auto_start(self, pending: PendingWheel) -> None:
        """倒计时结束自动开转：先截止参与，再让在场每个人各抽一次。"""

        try:
            await asyncio.sleep(self.choice_timeout)
        except asyncio.CancelledError:
            return
        async with self._lock:
            if self.pending.get(pending.group_id) is not pending or not pending.accepting:
                return
            pending.accepting = False
        try:
            await pending.send(
                f" {int(self.choice_timeout)} 秒到，馒头命运齿轮转盘开始转动，本轮截止参与。"
            )
        except Exception:
            logger.exception("[mantou-affection] 发送转盘开转提示失败")
        await self._run_operation(pending)

    async def _run_operation(self, pending: PendingWheel) -> None:
        try:
            for participant in [pending.trigger, *pending.participants]:
                try:
                    outcome = draw_wheel_outcome(self.rng)
                    if outcome.kind == "affection":
                        applied, profile, protected = await self.service.adjust(
                            pending.group_id,
                            participant.user_id,
                            participant.nickname,
                            outcome.value,
                        )
                        note = NEWBIE_PROTECTION_NOTE if protected else ""
                        await pending.send(
                            MessageSegment.at(participant.user_id)
                            + MessageSegment.text(
                                f" 转盘好感 {applied:+d}，当前 {profile.affection}{note}"
                            )
                        )
                        if applied < 0:
                            await self.service.send_gift(
                                pending.send,
                                pending.group_id,
                                participant.user_id,
                                participant.nickname,
                            )
                    elif outcome.kind == "image":
                        await self._run_image_quota(pending, participant, outcome.value)
                    elif outcome.kind == "poke":
                        await self._run_poke(pending, participant, outcome.value)
                    elif outcome.kind == "panel":
                        await self._run_panel_quota(pending, participant, outcome.value)
                    elif outcome.kind == "panel_rank":
                        await self._run_panel_rank_quota(pending, participant, outcome.value)
                    elif outcome.kind == "mantou_sticker":
                        await self._run_mantou_sticker_outcome(pending, participant)
                    elif outcome.kind == "taozi":
                        await self._run_sticker_outcome(pending, participant)
                    elif outcome.kind == "wallpaper":
                        await self._run_wallpaper_outcome(pending, participant)
                    else:
                        await self._run_meme_outcome(pending, participant)
                except Exception:
                    logger.exception(
                        f"[mantou-affection] 转盘参与者结算失败 group={pending.group_id} "
                        f"user={participant.user_id}"
                    )
        finally:
            self.discard(pending)

    async def _run_image_quota(
        self, pending: PendingWheel, participant: WheelParticipant, count: int
    ) -> None:
        """转盘抽到图像处理次数：记账后提示，用户自己用 /馒头图像处理 花掉。"""

        try:
            if self.image_quota is None:
                raise RuntimeError("图像处理次数账本未接线")
            total = await self.image_quota.add(
                pending.group_id, participant.user_id, count
            )
        except Exception:
            logger.exception("[mantou-affection] 转盘发放图像处理次数失败")
            await pending.send(
                MessageSegment.at(participant.user_id)
                + MessageSegment.text(" 馒头想给你记图像处理次数，但账本没打开，这次先欠着")
            )
            return
        await pending.send(
            MessageSegment.at(participant.user_id)
            + MessageSegment.text(
                f" 🎨 抽到图像处理次数 ×{count}，当前共 {total} 次"
                "（发送 /馒头图像处理 使用）"
            )
        )

    async def _run_panel_quota(
        self, pending: PendingWheel, participant: WheelParticipant, count: int
    ) -> None:
        """转盘抽到角色面板次数：记到面板次数账本，由喵喵插件按次消耗。"""

        try:
            if self.panel_quota is None:
                raise RuntimeError("面板次数账本未接线")
            total = await self.panel_quota.add(
                pending.group_id, participant.user_id, count
            )
        except Exception:
            logger.exception("[mantou-affection] 转盘发放面板次数失败")
            await pending.send(
                MessageSegment.at(participant.user_id)
                + MessageSegment.text(" 馒头想给你记角色面板次数，但账本没打开，这次先欠着")
            )
            return
        await pending.send(
            MessageSegment.at(participant.user_id)
            + MessageSegment.text(
                f" 🖼️ 抽到角色面板次数 ×{count}，当前共 {total} 次"
                "（在喵喵插件里发送 /角色名面板 使用）"
            )
        )

    async def _run_panel_rank_quota(
        self, pending: PendingWheel, participant: WheelParticipant, count: int
    ) -> None:
        """转盘抽到群面板排名次数：记到独立账本，由喵喵插件按次消耗。"""

        try:
            if self.panel_rank_quota is None:
                raise RuntimeError("群面板排名次数账本未接线")
            total = await self.panel_rank_quota.add(
                pending.group_id, participant.user_id, count
            )
        except Exception:
            logger.exception("[mantou-affection] 转盘发放群面板排名次数失败")
            await pending.send(
                MessageSegment.at(participant.user_id)
                + MessageSegment.text(" 馒头想给你记群面板排名次数，但账本没打开，这次先欠着")
            )
            return
        await pending.send(
            MessageSegment.at(participant.user_id)
            + MessageSegment.text(
                f" 🏆 抽到群面板排名次数 ×{count}，当前共 {total} 次"
                "（在喵喵插件里发送 /面板排名 使用）"
            )
        )

    async def _run_poke(
        self, pending: PendingWheel, participant: WheelParticipant, count: int
    ) -> None:
        try:
            for index in range(count):
                await pending.bot.call_api(
                    "send_poke",
                    group_id=int(pending.group_id),
                    user_id=int(participant.user_id),
                )
                if index + 1 < count and self.poke_delay:
                    await asyncio.sleep(self.poke_delay)
        except Exception:
            logger.exception("[mantou-affection] 转盘主动戳一戳失败")
            await pending.send(
                MessageSegment.at(participant.user_id)
                + MessageSegment.text(" 馒头主动戳你失败（是馒头戳你，不用你回戳）")
            )
            return
        await pending.send(
            MessageSegment.at(participant.user_id)
            + MessageSegment.text(f" 馒头主动戳了你 {count} 次（是馒头戳你，不用你回戳）")
        )

    async def _draw_wallpaper(self) -> tuple[Wallpaper, bytes]:
        """先抽二创图；二创源取不到时退回官方壁纸，别让这次转盘白抽。"""

        assert self.wallpapers is not None
        try:
            return await self.wallpapers.draw(fanart=True)
        except WallpaperUnavailable as error:
            logger.warning(
                f"[mantou-affection] 转盘二创图不可用，改用官方壁纸: {error}"
            )
            return await self.wallpapers.draw()

    async def _run_wallpaper_outcome(
        self, pending: PendingWheel, participant: WheelParticipant
    ) -> None:
        """转盘抽到随机壁纸：优先发米游社二创图，二创源挂了再退回官方壁纸。"""

        try:
            if self.wallpapers is None:
                raise WallpaperUnavailable("壁纸库未接线")
            wallpaper, data = await self._draw_wallpaper()
        except WallpaperUnavailable as error:
            logger.warning(f"[mantou-affection] 转盘壁纸不可用: {error}")
            await pending.send(
                MessageSegment.at(participant.user_id)
                + MessageSegment.text(WHEEL_WALLPAPER_FAILED_REPLY)
            )
            return
        except Exception:
            logger.exception("[mantou-affection] 转盘壁纸取图失败")
            await pending.send(
                MessageSegment.at(participant.user_id)
                + MessageSegment.text(WHEEL_WALLPAPER_FAILED_REPLY)
            )
            return
        label = (
            f"{wallpaper.game} · {wallpaper.collection}"
            if wallpaper.collection
            else wallpaper.game
        )
        template = (
            WHEEL_FANART_REPLY
            if wallpaper.source == "fanart"
            else WHEEL_FANART_FALLBACK_REPLY
        )
        caption = MessageSegment.at(participant.user_id) + MessageSegment.text(
            template.format(label=label)
        )
        try:
            await pending.send(caption + MessageSegment.image(data))
            return
        except Exception:
            logger.exception(
                f"[mantou-affection] 转盘壁纸图片发送失败，改用原图直链 "
                f"group={pending.group_id} user={participant.user_id}"
            )
        try:
            await pending.send(caption + MessageSegment.image(wallpaper.url))
            return
        except Exception:
            logger.exception(
                f"[mantou-affection] 转盘壁纸直链发送失败 group={pending.group_id} "
                f"user={participant.user_id}"
            )
        await pending.send(
            MessageSegment.at(participant.user_id)
            + MessageSegment.text(f" 壁纸「{label}」没发出去，原图：{wallpaper.url}")
        )

    async def _run_meme_outcome(self, pending: PendingWheel, participant: WheelParticipant) -> None:
        try:
            if self.memes is None:
                raise MemeGenerationError("meme-generator 未安装")
            image = await self.memes.generate(
                bot=pending.bot,
                user_id=participant.user_id,
                nickname=participant.nickname,
                group_id=pending.group_id,
            )
        except MemeGenerationError as error:
            await self._report_meme_error(participant.user_id, pending.send, error)
            return
        except Exception:
            logger.exception("[mantou-affection] 转盘表情生成失败")
            await pending.send(
                MessageSegment.at(participant.user_id)
                + MessageSegment.text(WHEEL_MEME_FAILED_REPLY)
            )
            return
        await pending.send(
            MessageSegment.at(participant.user_id)
            + MessageSegment.text(" 馒头为你做了一张随机表情：")
            + MessageSegment.image(image)
        )

    async def _report_meme_error(self, user_id: str, send, error: MemeGenerationError) -> None:
        if isinstance(error, MemeAssetMissing):
            logger.warning(f"[mantou-affection] memes 素材尚未就绪: {error}")
            if self.resources is not None:
                self.resources.check_after_missing()
            await send(
                MessageSegment.at(user_id)
                + MessageSegment.text(WHEEL_MEME_ASSET_MISSING_REPLY)
            )
            return
        logger.warning(f"[mantou-affection] memes 结果生成失败: {error}")
        await send(
            MessageSegment.at(user_id) + MessageSegment.text(WHEEL_MEME_FAILED_REPLY)
        )

    async def _run_mantou_sticker_outcome(
        self, pending: PendingWheel, participant: WheelParticipant
    ) -> None:
        mention = MessageSegment.at(participant.user_id)
        try:
            image = await asyncio.to_thread(
                pick_local_sticker, self.config.mantou_affection_wheel_sticker_dir
            )
        except StickerAssetMissing as error:
            logger.warning(f"[mantou-affection] 馒头表情包素材不可用: {error}")
            await pending.send(
                mention + MessageSegment.text(" 抽中了「馒头表情包」，素材暂不可用，本次不重抽")
            )
            return

        await pending.send(
            mention
            + MessageSegment.text(" 抽中了「馒头表情包」：")
            + MessageSegment.image(image)
        )

    async def _run_sticker_outcome(
        self, pending: PendingWheel, participant: WheelParticipant
    ) -> None:
        cost = WHEEL_TAOZI_COST
        current = (await self.service.profile(pending.group_id, participant.user_id)).affection
        protected_cost = -protect_deduction(
            current, -cost, self.config.mantou_affection_newbie_protection_below
        )
        note = NEWBIE_PROTECTION_NOTE if protected_cost != cost else ""
        cost = protected_cost
        if current < cost:
            await pending.send(
                MessageSegment.at(participant.user_id)
                + MessageSegment.text(
                    WHEEL_TAOZI_POOR_REPLY.format(cost=cost, affection=current)
                )
            )
            return
        await self._request_sticker_confirm(pending, participant, cost, note)

    async def _request_sticker_confirm(
        self,
        pending: PendingWheel,
        participant: WheelParticipant,
        cost: int = WHEEL_TAOZI_COST,
        note: str = "",
    ) -> None:
        key = (pending.group_id, participant.user_id)
        old = self.sticker_confirms.pop(key, None)
        if old is not None:
            old.accepting = False
            if old.task is not None:
                old.task.cancel()
        confirm = PendingStickerConfirm(
            pending.group_id,
            participant.user_id,
            participant.nickname,
            bot=pending.bot,
            send=pending.send,
            cost=cost,
            note=note,
        )
        self.sticker_confirms[key] = confirm
        current = (await self.service.profile(pending.group_id, participant.user_id)).affection
        try:
            await pending.send(
                MessageSegment.at(participant.user_id)
                + MessageSegment.text(
                    format_sticker_prompt(
                        current,
                        int(self.sticker_confirm_timeout),
                        cost,
                        note,
                    )
                )
            )
        except Exception:
            confirm.accepting = False
            if self.sticker_confirms.get(key) is confirm:
                self.sticker_confirms.pop(key, None)
            raise
        if self.sticker_confirms.get(key) is confirm and confirm.accepting:
            confirm.task = asyncio.create_task(self._expire_sticker_confirm(confirm))

    async def _expire_sticker_confirm(self, confirm: PendingStickerConfirm) -> None:
        try:
            await asyncio.sleep(self.sticker_confirm_timeout)
        except asyncio.CancelledError:
            return
        await self._settle_sticker_confirm(confirm, declined=False)

    async def _answer_sticker_confirm(
        self, confirm: PendingStickerConfirm, *, accepted: bool
    ) -> None:
        if (
            not confirm.accepting
            or self.sticker_confirms.get((confirm.group_id, confirm.user_id)) is not confirm
        ):
            return
        if not accepted:
            await self._settle_sticker_confirm(confirm, declined=True)
            return

        self._cancel_sticker_confirm(confirm)
        try:
            if self.stickers is None:
                raise StickerAssetMissing("桃子表情包素材库未启用")
            image, caption = await self.stickers.pick()
        except StickerAssetMissing as error:
            await self._report_sticker_error(confirm.user_id, confirm.send, error)
            return
        except Exception:
            logger.exception("[mantou-affection] 转盘桃子表情包取图失败")
            await confirm.send(
                MessageSegment.at(confirm.user_id)
                + MessageSegment.text(WHEEL_TAOZI_FAILED_REPLY)
            )
            return

        try:
            current = (
                await self.service.profile(confirm.group_id, confirm.user_id)
            ).affection
            if current < confirm.cost:
                await confirm.send(
                    MessageSegment.at(confirm.user_id)
                    + MessageSegment.text(
                        WHEEL_TAOZI_POOR_REPLY.format(
                            cost=confirm.cost, affection=current
                        )
                    )
                )
                return
            applied, _profile, protected = await self.service.adjust(
                confirm.group_id,
                confirm.user_id,
                confirm.nickname,
                -confirm.cost,
                protect=False,
            )
            if applied < 0:
                await self.service.send_gift(
                    confirm.send,
                    confirm.group_id,
                    confirm.user_id,
                    confirm.nickname,
                )
        except Exception:
            logger.exception(
                f"[mantou-affection] 转盘桃子表情包扣费失败 group={confirm.group_id} "
                f"user={confirm.user_id}"
            )
            await confirm.send(
                MessageSegment.at(confirm.user_id)
                + MessageSegment.text(WHEEL_TAOZI_FAILED_REPLY)
            )
            return
        await confirm.send(
            MessageSegment.at(confirm.user_id)
            + MessageSegment.text(
                WHEEL_TAOZI_CONFIRMED_REPLY.format(
                    cost=-applied,
                    caption=caption,
                    note=NEWBIE_PROTECTION_NOTE if protected else confirm.note,
                )
            )
            + MessageSegment.image(image)
        )

    async def _settle_sticker_confirm(
        self, confirm: PendingStickerConfirm, *, declined: bool
    ) -> None:
        if (
            not confirm.accepting
            or self.sticker_confirms.get((confirm.group_id, confirm.user_id)) is not confirm
        ):
            return
        self._cancel_sticker_confirm(confirm)
        template = (
            WHEEL_TAOZI_DECLINED_REPLY if declined else WHEEL_TAOZI_TIMEOUT_REPLY
        )
        try:
            await confirm.send(
                MessageSegment.at(confirm.user_id) + MessageSegment.text(template)
            )
        except Exception:
            logger.exception(
                f"[mantou-affection] 转盘桃子表情包放弃提示发送失败 "
                f"group={confirm.group_id} user={confirm.user_id}"
            )

    def _cancel_sticker_confirm(self, confirm: PendingStickerConfirm) -> None:
        confirm.accepting = False
        key = (confirm.group_id, confirm.user_id)
        if self.sticker_confirms.get(key) is confirm:
            self.sticker_confirms.pop(key, None)
        task = confirm.task
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    async def _report_sticker_error(
        self, user_id: str, send, error: StickerError
    ) -> None:
        if isinstance(error, StickerAssetMissing):
            logger.warning(f"[mantou-affection] 桃子表情包素材尚未就绪: {error}")
            if self.stickers is not None:
                self.stickers.check_after_missing()
            await send(
                MessageSegment.at(user_id)
                + MessageSegment.text(WHEEL_TAOZI_MISSING_REPLY)
            )
            return
        logger.warning(f"[mantou-affection] 桃子表情包取图失败: {error}")
        await send(
            MessageSegment.at(user_id) + MessageSegment.text(WHEEL_TAOZI_FAILED_REPLY)
        )
