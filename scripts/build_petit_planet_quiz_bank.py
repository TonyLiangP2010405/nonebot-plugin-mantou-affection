#!/usr/bin/env python3
"""生成「星布谷地知识问答」题库。

数据来自 Petit Planet DB（petitplanetdb.com）的简体中文资料页：邻居、鱼类、昆虫、
海岸生物、食谱、作物、家具和服装。资料页会把每一条记录的字段渲染成
``<dt>/<dd>`` 卡片，脚本直接解析这些卡片，因此不需要逐条抓详情页。

资料站记录的是测试版本（0.95 Final Beta Test）的社区数据，未核验的官方译名保留
原文，所以家具、鱼类等条目名称仍是英文。

用法:
    uv run python scripts/build_petit_planet_quiz_bank.py --cache /tmp/pp_db
"""

from __future__ import annotations

import argparse
import random
import re
import time
import urllib.request
from collections import Counter, defaultdict
from html import unescape
from pathlib import Path
from typing import Any

from quiz_bank_utils import (
    Candidate,
    add_candidate,
    drop_ambiguous,
    format_questions,
    select_items,
    write_bank,
)

DEFAULT_CACHE_DIR = Path("/tmp/pp_db")
DEFAULT_OUT_PATH = (
    Path(__file__).resolve().parent.parent
    / "nonebot_plugin_mantou_affection"
    / "resources"
    / "petit_planet_quiz_questions.json"
)
DEFAULT_COUNT = 5000
DEFAULT_SEED = 20261115
BASE_URL = "https://petitplanetdb.com/zh-cn"
WIKI_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

# 每个资料页的实体名与可用字段。字段值是 (问题里的说法, 是否适合反向出题)。
SECTION_FIELDS: dict[str, dict[str, tuple[str, bool]]] = {
    "neighbors": {
        "身份": ("身份", True),
        "星系": ("所在星系", True),
        "生日": ("生日", False),
    },
    "fish": {
        "栖息地": ("栖息地", True),
        "区域": ("出现区域", False),
        "时间": ("出没时间", True),
        "品质": ("品质", True),
        "记录最高售价（Dough）": ("最高售价", False),
    },
    "bugs": {
        "栖息地": ("栖息地", True),
        "区域": ("出现区域", False),
        "时间": ("出没时间", True),
        "品质": ("品质", True),
        "记录最高售价（Dough）": ("最高售价", False),
    },
    "shore-dwellers": {
        "栖息地": ("栖息地", True),
        "区域": ("出现区域", False),
        "时间": ("出没时间", True),
        "品质": ("品质", True),
        "记录最高售价（Dough）": ("最高售价", False),
    },
    "recipes": {
        "分类": ("烹饪方式与菜系", True),
        "体力恢复": ("体力恢复量", False),
        "记录配料": ("配料", False),
    },
    "crops": {},
    "furniture": {
        "分类": ("分类", True),
        "类型": ("类型", True),
        "品质": ("品质", True),
        "材料": ("材料", True),
        "颜色": ("颜色", True),
        "售价": ("售价（Dough）", False),
    },
    "clothing": {
        "分类": ("分类", True),
        "类型": ("类型", True),
        "品质": ("品质", True),
        "风格": ("风格", True),
    },
}

# 邻居的官方中文名 / 资料页保留的是英文罗马音。
NEIGHBOR_NAMES = {
    "Yunguo": "云果",
    "Isaki": "伊佐奇",
    "Medowlyn": "绵朵莉",
    "Frostia": "幻雪",
    "Rebella": "热贝尔",
    "Dorjelang": "多杰朗",
    "Msafiri": "萨飞里",
    "Harpeno": "哈佩诺",
    "Manna": "蛮蛮",
    "Tika": "缇卡",
    "Heyu": "鹤玉",
    "Nerina": "纳蕾娜",
    "Mobai": "莫白",
    "Elsasani": "艾莎莎尼",
    "Glenn": "格连",
    "Mors": "墨尔斯",
}

# 资料页上的实体叫法。
SECTION_LABELS = {
    "neighbors": "邻居",
    "fish": "鱼类",
    "bugs": "昆虫",
    "shore-dwellers": "海岸生物",
    "recipes": "菜肴",
    "crops": "作物",
    "furniture": "家具",
    "clothing": "服装",
}

# 这些取值不构成有效答案 / 直接跳过。
WEAK_VALUES = {
    "",
    "-",
    "无",
    "其他",
    "未知",
    "来源未注明版本",
    "暂无",
    "未记录",
}

# 数值型字段在问题里需要额外单位说明。
NUMBER_FIELDS = {
    "售价",
    "记录最高售价（Dough）",
    "体力恢复",
}

FORWARD_TEMPLATES = (
    "《星布谷地》里，「{name}」的{label}是？",
    "「{name}」的{label}是什么？",
    "星布谷地资料库：「{name}」的{label}是？",
)

REVERSE_TEMPLATE = "以下哪个{entity}的{label}是「{value}」？"

MEMBER_TEMPLATE = "「{name}」属于以下哪一类？"

# 来自官方介绍和 WIKI 玩法的稳定设定 / 补足纯资料表容易缺少的系统知识。
LORE_FACTS: list[tuple[str, str, list[str]]] = [
    ("《星布谷地》的英文名是？", "Petit Planet", ["Petit World", "Star Valley"]),
    ("《星布谷地》的开发商是？", "米哈游", ["库洛游戏", "鹰角网络"]),
    ("《星布谷地》的日文名是？", "プチプラネット", ["スターグレン", "プチワールド"]),
    ("玩家在星布谷地里的身份叫？", "种星人", ["星旅者", "拓荒者"]),
    ("《星布谷地》的核心玩法围绕什么展开？", "星际田园生活", ["卡牌对战", "太空射击"]),
    ("玩家用来改造星球地貌的能量叫？", "露卡活力", ["星尘", "以太"]),
    ("星球成长的核心植物是？", "露卡树", ["星杉", "银河藤"]),
    ("种植玩法中，初始的耕地格子上限是？", "60 格", ["30 格", "100 格"]),
    ("扩大耕地上限需要推进什么？", "主线任务并升级露卡树", ["购买土地", "提升邻居好感"]),
    ("水桶单次浇灌可以覆盖几格土地？", "3 格", ["1 格", "9 格"]),
    ("稻草人能监控半径多少格内的作物？", "30 格", ["10 格", "60 格"]),
    ("作物长期缺水最可能出现什么后果？", "退化为瘦弱植株并可能生虫", ["自动成熟", "品质提升"]),
    ("催生巨大化「丰硕作物」最常用的布局是？", "3×3 九宫格同种密植", ["单行等距种植", "随机混种"]),
    ("培育「奇异作物」时，3×3 九宫格中心要种？", "目标作物", ["任意杂草", "空着不放"]),
    (
        "培育「奇异作物」时，周围 8 格需要种？",
        "至少 3 种不同的其他作物",
        ["全部同种作物", "全部空置"],
    ),
    ("最高阶的变异作物「果王」会占据几格？", "4 格", ["1 格", "9 格"]),
    ("「果王」需要挨着什么作物培育？", "成熟的高阶作物（丰硕+奇异）", ["普通幼苗", "野生杂草"]),
    (
        "花卉杂交中，只有哪种情况才能杂交或自交？",
        "同一品质的花朵",
        ["任意品质的花朵", "只能金色花朵"],
    ),
    ("花卉杂交的相邻判定范围是？", "自身周围八格", ["自身所在格", "周围二十格"]),
    ("花瓣杂交时，产生新花的必要条件之一是？", "浇水", ["施肥", "播放音乐"]),
    ("好友来浇水时，新花生成概率会？", "更高", ["更低", "没有变化"]),
    ("每日新增蓝色品质花数量上限是？", "10 朵", ["5 朵", "20 朵"]),
    ("种子最常规的获取途径是？", "格连商店刷新购买", ["野外掉落", "邻居赠送"]),
    ("好友家园的作物可以怎么处理？", "只能观赏，无法收获", ["可以随便收获", "可以杂交"]),
    ("星湾里的「碎贝壳」通常怎么获得？", "沙滩拾取", ["伐木获得", "敲矿获得"]),
    ("星湾的「碎木」通常怎么获得？", "伐木", ["钓鱼", "沙滩拾取"]),
    ("游戏里的通用货币叫？", "Dough", ["摩拉", "信用点"]),
    ("制作家具和建筑常用的基础材料是？", "木材", ["宝石", "星尘"]),
    ("料理除了恢复体力，还会用于？", "完成星订单", ["提升战力", "开启副本"]),
    (
        "「禾夏风味」「诺森风味」「塔法风味」用来区分什么？",
        "菜肴的区域风味",
        ["家具风格", "服饰品质"],
    ),
    ("星球上负责商店功能、可以交换物资的邻居是？", "格连", ["莫白", "云果"]),
    ("负责种植相关引导的邻居是？", "云果", ["哈佩诺", "缇卡"]),
    ("负责斗虫相关内容的邻居是？", "伊佐奇", ["格连", "莫白"]),
    ("负责探索引导与星海旅行的伙伴是？", "艾莎莎尼", ["格连", "云果"]),
    ("负责星球登记与取名引导的邻居是？", "莫白", ["伊佐奇", "热贝尔"]),
    ("喜欢做玩偶、能设计毛绒玩具的邻居是？", "绵朵莉", ["云果", "幻雪"]),
    ("研究星象与神秘学的邻居是？", "幻雪", ["缇卡", "热贝尔"]),
    ("擅长寻找矿石的宝石小匠是？", "热贝尔", ["多杰朗", "萨飞里"]),
    ("常出现在高原星屿、擅长制帽的邻居是？", "多杰朗", ["哈佩诺", "蛮蛮"]),
    ("塔法星球的雪豹系探险向导是？", "萨飞里", ["哈佩诺", "纳蕾娜"]),
    ("被称为「暴君」的航路巡查员是？", "哈佩诺", ["萨飞里", "缇卡"]),
    ("咏春题材的武打演员邻居是？", "蛮蛮", ["缇卡", "鹤玉"]),
    ("「星空街市」主要用于什么？", "漫步、休憩与社交", ["战斗训练", "抽卡"]),
    ("玩家可以对自己的星球做什么？", "自由搭建家园并改造地貌", ["只能观看", "只能种田"]),
    ("星布谷地的联机玩法主要面向？", "多人社交与拜访", ["竞技排名", "单人速通"]),
    (
        "游戏里的家具可以通过什么方式获得？",
        "制作、商店购买或邻居赠礼",
        ["只能靠抽卡", "只能打怪掉落"],
    ),
    ("服装在游戏里主要通过什么获得？", "商店购买与羁绊奖励", ["战斗掉落", "钓鱼获得"]),
    ("「星订单」会用作物和菜肴来换取什么？", "奖励与订单进度", ["战斗力", "抽卡次数"]),
    ("邻居羁绊等级提升后通常会解锁？", "羁绊任务与专属奖励", ["战斗技能", "地图传送"]),
    ("游戏里用来指代小动物居民的通用称呼是？", "邻居", ["干员", "代理人"]),
    ("改造星球时需要注入什么来改变地貌？", "露卡活力", ["以太", "原石"]),
    ("星布谷地的钓鱼玩法产出的是？", "鱼类", ["昆虫", "矿石"]),
    ("捕捉昆虫需要准备的工具是？", "捕虫网", ["鱼竿", "铲子"]),
    ("砍树伐木可以获得的材料是？", "木材", ["布料", "宝石"]),
    ("料理的制作需要先准备什么？", "配料与烹饪器具", ["家具", "服装"]),
    ("星布谷地中「星湾」指的是？", "玩家所在的起始星球区域", ["一座商城", "一个副本"]),
    ("游戏支持的平台包括？", "PC、iOS、Android 与 Switch 2", ["只有手机", "只有主机"]),
]


_CARD = re.compile(r'<section class="record card"(.*?)</section>', re.S)
_DT_DD = re.compile(r"<dt[^>]*>(.*?)</dt><dd[^>]*>(.*?)</dd>", re.S)
_H2 = re.compile(r"<h2[^>]*>(.*?)</h2>", re.S)
_TAGS = re.compile(r"<[^>]+>")
_SPACES = re.compile(r"\s+")


def clean(value: Any) -> str:
    text = _TAGS.sub("", str(value or ""))
    return _SPACES.sub(" ", unescape(text)).strip()


def fetch_section(section: str, cache_dir: Path, refresh: bool = False) -> str:
    path = cache_dir / f"{section}.html"
    if path.is_file() and not refresh:
        return path.read_text(encoding="utf-8")
    url = f"{BASE_URL}/{section}"
    request = urllib.request.Request(url, headers={"User-Agent": WIKI_UA})
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                html = response.read().decode("utf-8", errors="replace")
            break
        except Exception as error:  # 网络错误统一重试
            last_error = error
            time.sleep(2.0 * (attempt + 1))
    else:
        raise SystemExit(f"下载 {url} 失败: {last_error}")
    cache_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    return html


def parse_records(html: str) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for body in _CARD.findall(html):
        headings = _H2.findall(body)
        if not headings:
            continue
        record = {"name": clean(headings[0])}
        for key, value in _DT_DD.findall(body):
            record[clean(key)] = clean(value)
        records.append(record)
    return records


class PetitPlanetBank:
    def __init__(self, payloads: dict[str, list[dict[str, str]]], seed: int):
        self.rng = random.Random(seed)
        self.records = payloads
        self.items: list[Candidate] = []
        self.values: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
        for section, records in payloads.items():
            for record in records:
                for field in SECTION_FIELDS[section]:
                    value = record.get(field, "")
                    if value and value not in WEAK_VALUES:
                        self.values[(section, field)].append((record["name"], value))
        self._build()

    def add(
        self, category: str, group: str, text: str, correct: str, wrongs: list[str]
    ) -> None:
        add_candidate(self.items, category, group, text, correct, wrongs)

    def _value_pool(self, section: str, field: str, correct: str) -> list[str]:
        pool: list[str] = []
        for _name, value in self.values[(section, field)]:
            if value != correct and value not in pool:
                pool.append(value)
        return pool

    def _name_pool(self, section: str, field: str, value: str, name: str) -> list[str]:
        pool: list[str] = []
        for other, other_value in self.values[(section, field)]:
            if other != name and other_value != value and other not in pool:
                pool.append(other)
        return pool

    def _forward(
        self, section: str, record: dict[str, str], field: str, label: str
    ) -> None:
        value = record.get(field, "")
        if not value or value in WEAK_VALUES:
            return
        name = record["name"]
        pool = self._value_pool(section, field, value)
        if len(pool) < 2:
            return
        template = FORWARD_TEMPLATES[
            self.rng.randrange(len(FORWARD_TEMPLATES))
        ]
        text = template.format(name=name, label=label)
        self.add(section, f"{field}|forward", text, value, pool)

    def _reverse(
        self, section: str, record: dict[str, str], field: str, label: str
    ) -> None:
        value = record.get(field, "")
        if not value or value in WEAK_VALUES:
            return
        # 同一个取值对应多个条目时 / 反向题的题干会完全一样 / 容易被判成歧义题。
        if sum(
            1 for _other, other_value in self.values[(section, field)]
            if other_value == value
        ) > 1:
            return
        name = record["name"]
        pool = self._name_pool(section, field, value, name)
        if len(pool) < 2:
            return
        text = REVERSE_TEMPLATE.format(
            entity=SECTION_LABELS[section], label=label, value=value
        )
        self.add(section, f"{field}|reverse", text, name, pool)

    def _build(self) -> None:
        for section, records in self.records.items():
            fields = SECTION_FIELDS[section]
            for record in records:
                for field, (label, allow_reverse) in fields.items():
                    self._forward(section, record, field, label)
                    if allow_reverse:
                        self._reverse(section, record, field, label)
            for record in records:
                self._membership(section, record)
        self._add_lore()

    def _membership(self, section: str, record: dict[str, str]) -> None:
        correct = SECTION_LABELS[section]
        wrongs = [value for value in SECTION_LABELS.values() if value != correct]
        self.add(
            section,
            "member",
            MEMBER_TEMPLATE.format(name=record["name"]),
            correct,
            wrongs,
        )

    def _add_lore(self) -> None:
        for text, correct, wrongs in LORE_FACTS:
            self.add("lore", "lore", text, correct, wrongs)


QUOTAS = {
    "furniture": 2250,
    "clothing": 950,
    "fish": 760,
    "recipes": 380,
    "bugs": 380,
    "shore-dwellers": 250,
    "neighbors": 80,
    "crops": 20,
    "lore": 50,
}


def main() -> None:
    parser = argparse.ArgumentParser(description="生成星布谷地知识问答题库")
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_PATH)
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--refresh", action="store_true", help="忽略缓存重新抓取")
    args = parser.parse_args()

    payloads: dict[str, list[dict[str, str]]] = {}
    for section in SECTION_FIELDS:
        html = fetch_section(section, args.cache, refresh=args.refresh)
        records = parse_records(html)
        if section == "neighbors":
            for record in records:
                record["name"] = NEIGHBOR_NAMES.get(record["name"], record["name"])
        payloads[section] = records
        print(f"{section}: {len(payloads[section])} 条记录")

    bank = PetitPlanetBank(payloads, args.seed)
    candidates = drop_ambiguous(bank.items)
    print(f"候选问题 {len(bank.items)} 条，去歧义后 {len(candidates)} 条")

    quotas = dict(QUOTAS)
    total = sum(quotas.values())
    if args.count != total:
        quotas = {
            name: max(1, round(quota * args.count / total))
            for name, quota in quotas.items()
        }
    picked, categories = select_items(candidates, quotas, args.count, bank.rng)
    questions = format_questions(picked, bank.rng)

    write_bank(
        args.out,
        questions,
        {
            "source": "Petit Planet DB (petitplanetdb.com, zh-cn, 0.95 Final Beta Test)",
            "seed": args.seed,
            "categories": dict(sorted(Counter(categories).items())),
        },
    )


if __name__ == "__main__":
    main()
