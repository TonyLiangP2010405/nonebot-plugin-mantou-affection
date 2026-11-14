#!/usr/bin/env python3
"""生成「绝区零知识问答」题库。

数据来自 Biligame 绝区零 WIKI 的代理人、音擎、驱动盘、邦布和敌人页面。
脚本会优先读取本地缓存；缓存缺失时自动抓取并写入 --cache。

用法:
    uv run python scripts/build_zzz_quiz_bank.py --cache /tmp/zzz_wiki.json
"""

from __future__ import annotations

import argparse
import json
import random
import re
import time
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any

from quiz_bank_utils import (
    add_candidate,
    drop_ambiguous,
    format_questions,
    select_items,
    shorten,
    strip_wiki,
    write_bank,
)

DEFAULT_CACHE_PATH = Path("/tmp/zzz_wiki.json")
DEFAULT_OUT_PATH = (
    Path(__file__).resolve().parent.parent
    / "nonebot_plugin_mantou_affection"
    / "resources"
    / "zzz_quiz_questions.json"
)
DEFAULT_COUNT = 5000
DEFAULT_SEED = 20261114
QUOTAS = {
    "agent": 3700,
    "engine": 700,
    "disc": 250,
    "bangboo": 280,
    "enemy": 20,
    "lore": 50,
}
WIKI_API = "https://wiki.biligame.com/zzz/api.php"
WIKI_UA = "Mozilla/5.0"
CATEGORY_TEMPLATES = {
    "角色": "角色图鉴",
    "音擎": "武器图鉴",
    "驱动盘": "驱动盘",
    "邦布": "邦布图鉴",
    "敌人": "敌人",
}

# 主干剧情补充: 新艾利都、空洞、阵营与主线大事件。只收录稳定设定。
LORE_FACTS: list[tuple[str, str, list[str]]] = [
    ("《绝区零》故事发生的最后一座城市是？", "新艾利都", ["旧艾利都", "丽都"]),
    ("新艾利都周围会出现、吞噬一切的异常空间叫？", "空洞", ["零境", "虚界"]),
    ("空洞中弥漫的、会侵蚀生物的能量叫？", "以太", ["崩坏能", "源石"]),
    ("能够在空洞中导航、带领他人探索的职业叫？", "绳匠", ["向导", "空洞技师"]),
    ("绳匠使用的、可远程操控邦布进入空洞的系统叫？", "H.D.D.", ["空洞终端", "以太网络"]),
    ("法厄同兄妹在空洞中使用的邦布名字是？", "伊埃斯", ["阿全", "艾米莉安"]),
    ("人工智能「Fairy」最初是在哪里被发现的？", "空洞深处", ["市政厅", "白祇重工"]),
    ("新艾利都的官方治安机构通常被称为什么？", "治安局", ["防卫军", "空洞调查协会"]),
    ("负责处理空洞灾害的军方特殊部队「奥波勒斯小队」属于？", "防卫军", ["治安局", "狡兔屋"]),
    ("「对空洞特别行动部第六课」通常简称？", "第六课", ["特勤课", "空洞课"]),
    ("以家政服务为掩护、实际接受委托的阵营是？", "维多利亚家政", ["白祇重工", "狡兔屋"]),
    ("由妮可领导的、接受各种委托的小队是？", "狡兔屋", ["卡吕冬之子", "怪啖屋"]),
    ("以重型机械和建设为主业的阵营是？", "白祇重工", ["维多利亚家政", "卡吕冬之子"]),
    ("由凯撒领导的摩托帮阵营是？", "卡吕冬之子", ["狡兔屋", "怪啖屋"]),
    ("以美食和怪谈为主题的阵营是？", "怪啖屋", ["卡吕冬之子", "白祇重工"]),
    ("新艾利都的「绳网」主要用来做什么？", "发布委托与交流情报", ["进行空洞战斗", "生产邦布"]),
    ("邦布的主要用途是？", "协助空洞探索与战斗", ["制造以太", "管理市政"]),
    ("空洞调查协会的主要工作是？", "调查与研究空洞", ["维持治安", "经营音像店"]),
    ("「零号空洞」通常被视作？", "最危险的空洞之一", ["最大的商业区", "邦布工厂"]),
    ("新艾利都的通用货币是？", "丁尼", ["信用点", "原石"]),
    ("代理人通过什么装置使用武器战斗？", "音擎", ["驱动盘", "邦布"]),
    ("驱动盘的作用是？", "为代理人提供套装增益", ["驱动邦布", "开启空洞"]),
    ("「侵蚀」状态通常会带来什么？", "持续伤害与负面效果", ["攻击力提升", "无敌"]),
    ("「失衡」状态的含义是？", "敌人失衡值满后进入虚弱", ["角色失去意识", "空洞崩塌"]),
    ("「连携技」通常如何触发？", "敌人进入失衡后切换角色", ["普通攻击命中", "闪避成功"]),
    ("「终结技」需要积攒什么才能发动？", "喧响等级", ["失衡值", "以太能量"]),
    ("「极限闪避」成功后通常会触发？", "子弹时间与反击机会", ["全队回血", "立即结算"]),
    ("「招架支援」属于哪类支援？", "防御型支援", ["攻击型支援", "治疗型支援"]),
    ("「邦布连携技」需要什么条件？", "队伍中有两名同属性角色", ["全员满血", "空洞等级满级"]),
    ("「属性异常」累积满后会触发？", "对应属性的异常效果", ["立即死亡", "掉落装备"]),
    ("「紊乱」效果通常与什么有关？", "不同属性异常叠加", ["空洞探索时间", "音擎强化"]),
    ("「命破」是一种？", "代理人特性", ["敌人类型", "驱动盘套装"]),
    ("「锋御」是一种？", "代理人特性", ["邦布技能", "音擎副词条"]),
    ("「强攻」特性的代理人主要定位是？", "输出", ["治疗", "防护"]),
    ("「击破」特性的代理人主要定位是？", "快速打出失衡", ["持续治疗", "远程狙击"]),
    ("「异常」特性的代理人主要定位是？", "施加属性异常", ["承受伤害", "回复能量"]),
    ("「支援」特性的代理人主要定位是？", "辅助队友", ["单挑首领", "生产邦布"]),
    ("「防护」特性的代理人主要定位是？", "保护与承受伤害", ["爆发输出", "施加异常"]),
    ("「S 级」在绝区零中代表？", "最高稀有度", ["最低稀有度", "活动限定"]),
    ("「调频」在绝区零中指的是？", "抽取代理人与音擎", ["调整画质", "升级邦布"]),
    ("「音像店」主要提供什么服务？", "驱动盘相关", ["武器锻造", "任务发布"]),
    ("「绳网」上的「法厄同」指的是？", "主角兄妹的绳匠代号", ["一名邦布", "治安局探员"]),
    ("「新艾利都」的建立与哪次灾难有关？", "旧都陷落", ["魔神战争", "坎瑞亚灾变"]),
    ("空洞灾害中失踪的旧城通常被称为？", "旧艾利都", ["旧都", "失落之城"]),
    ("「以太适性」指的是？", "对以太侵蚀的适应能力", ["对空洞的导航能力", "音擎输出效率"]),
    ("「空洞许可」的作用是？", "允许进入空洞作业", ["允许驾驶摩托", "允许经营音像店"]),
    ("「邦布」的名字来源与什么有关？", "邦布自身种类", ["代理人阵营", "驱动盘套装"]),
    ("「代理人」在绝区零中指的是？", "可操作战斗角色", ["发布委托的 NPC", "空洞怪物"]),
    ("「侵蚀抗性」通常用来抵抗？", "以太侵蚀", ["物理伤害", "失衡值"]),
    ("「喧响等级」主要通过什么提升？", "战斗中的表现", ["探索地图", "购买道具"]),
]

_HTML = re.compile(r"<[^>]+>")
_SPACES = re.compile(r"\s+")
_TEMPLATE_HEAD = re.compile(r"\{\{([^{}|]+)")


_FILE_LINK = re.compile(r"\[\[(?:File|文件|Image|file):[^\]]*\]?\]?", re.IGNORECASE)
_PX_LINK = re.compile(r"\d+px\|link=[^\s|]+")
_PX = re.compile(r"\d+px")


def _clean(value: Any) -> str:
    text = str(value or "")
    text = _FILE_LINK.sub("", text)
    text = _PX_LINK.sub("", text)
    text = _PX.sub("", text)
    text = strip_wiki(text)
    text = _HTML.sub("", text)
    text = text.replace("|", " ").replace("[[", "").replace("]]", "")
    text = text.replace("{{", "").replace("}}", "")
    return _SPACES.sub(" ", text).strip()


def find_templates(text: str, name: str) -> list[str]:
    """返回页面中所有指定模板的模板体（不含外层大括号）。"""

    results: list[str] = []
    needle = "{{" + name
    start = 0
    while True:
        index = text.find(needle, start)
        if index < 0:
            return results
        depth = 0
        cursor = index
        while cursor < len(text) - 1:
            if text.startswith("{{", cursor):
                depth += 1
                cursor += 2
                continue
            if text.startswith("}}", cursor):
                depth -= 1
                cursor += 2
                if depth == 0:
                    results.append(text[index + 2 : cursor - 2])
                    break
                continue
            cursor += 1
        start = cursor


GENERIC_SKILL_NAMES = {
    "普通攻击",
    "强化普攻",
    "闪避",
    "冲刺攻击",
    "闪避反击",
    "特殊技",
    "强化特殊技",
    "连携技",
    "终结技",
    "快速支援",
    "招架支援",
    "支援突击",
    "核心被动",
    "额外能力",
}


def valid_skill_name(name: str) -> bool:
    name = name.strip().strip("：: ")
    return bool(name) and name not in GENERIC_SKILL_NAMES


def parse_params(inner: str) -> dict[str, str]:
    """按顶层 | 和 = 拆分模板参数，保留嵌套模板与链接。"""

    parts: list[str] = []
    buffer: list[str] = []
    depth = 0
    brackets = 0
    index = 0
    while index < len(inner):
        if inner.startswith("{{", index):
            depth += 1
            buffer.append("{{")
            index += 2
            continue
        if inner.startswith("}}", index):
            depth -= 1
            buffer.append("}}")
            index += 2
            continue
        if inner.startswith("[[", index):
            brackets += 1
            buffer.append("[[")
            index += 2
            continue
        if inner.startswith("]]", index):
            brackets -= 1
            buffer.append("]]")
            index += 2
            continue
        char = inner[index]
        if char == "|" and depth == 0 and brackets == 0:
            parts.append("".join(buffer))
            buffer = []
            index += 1
            continue
        buffer.append(char)
        index += 1
    parts.append("".join(buffer))

    params: dict[str, str] = {}
    for part in parts:
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        params[key.strip()] = value.strip()
    return params


def template_params(text: str, name: str) -> dict[str, str]:
    blocks = find_templates(text, name)
    return parse_params(blocks[0]) if blocks else {}


def fetch_json(params: dict[str, str], tries: int = 6) -> dict[str, Any]:
    url = WIKI_API + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"User-Agent": WIKI_UA})
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception:
            time.sleep(1.5 * (attempt + 1))
    return {}


def fetch_category(category: str) -> list[str]:
    titles: list[str] = []
    cont: str | None = None
    while True:
        params = {
            "action": "query",
            "list": "categorymembers",
            "cmtitle": f"分类:{category}",
            "cmlimit": "500",
            "format": "json",
        }
        if cont:
            params["cmcontinue"] = cont
        data = fetch_json(params)
        titles.extend(
            item["title"]
            for item in data.get("query", {}).get("categorymembers", [])
            if item.get("ns") == 0
        )
        cont = data.get("continue", {}).get("cmcontinue")
        if not cont:
            return titles


def fetch_pages(cache_path: Path) -> dict[str, Any]:
    if cache_path.is_file():
        return json.loads(cache_path.read_text(encoding="utf-8"))
    categories = {name: fetch_category(name) for name in CATEGORY_TEMPLATES}
    pages: dict[str, str] = {}
    for title in sorted({title for titles in categories.values() for title in titles}):
        data = fetch_json(
            {"action": "parse", "page": title, "prop": "wikitext", "format": "json"}
        )
        text = data.get("parse", {}).get("wikitext", {}).get("*")
        if isinstance(text, str):
            pages[title] = text
    payload = {"categories": categories, "pages": pages}
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return payload


class ZzzBank:
    def __init__(self, payload: dict[str, Any], seed: int):
        self.categories: dict[str, list[str]] = payload["categories"]
        self.pages: dict[str, str] = payload["pages"]
        self.rng = random.Random(seed)
        self.items: list = []
        self.agent_pages = self._titles("角色")
        self.agent_fields = {
            title: template_params(self.pages.get(title, ""), "角色图鉴")
            for title in self.agent_pages
        }
        self.agent_names = [
            self._name(title, self.agent_fields[title]) for title in self.agent_pages
        ]
        self.agent_names = [name for name in self.agent_names if name]
        self.skills: list[dict[str, str]] = []
        for title in self.agent_pages:
            agent_name = self._name(title, self.agent_fields[title])
            for skill in self._parse_skills(title):
                skill["agent"] = agent_name
                self.skills.append(skill)
        self.skill_groups = sorted({skill["group"] for skill in self.skills} - {""})
        self.skill_names = [skill["name"] for skill in self.skills if skill["name"]]

        self.engine_pages = self._titles("音擎")
        self.engine_fields = {
            title: template_params(self.pages.get(title, ""), "武器图鉴")
            for title in self.engine_pages
        }
        self.engine_names = [
            self._name(title, self.engine_fields[title]) for title in self.engine_pages
        ]
        self.engine_names = [name for name in self.engine_names if name]
        self.disc_pages = self._titles("驱动盘")
        self.disc_fields = {
            title: template_params(self.pages.get(title, ""), "驱动盘")
            for title in self.disc_pages
        }
        self.disc_names = [
            self._name(title, self.disc_fields[title]) for title in self.disc_pages
        ]
        self.disc_names = [name for name in self.disc_names if name]
        self.bangboo_pages = self._titles("邦布")
        self.bangboo_fields = {
            title: template_params(self.pages.get(title, ""), "邦布图鉴")
            for title in self.bangboo_pages
        }
        self.bangboo_names = [
            self._name(title, self.bangboo_fields[title]) for title in self.bangboo_pages
        ]
        self.bangboo_names = [name for name in self.bangboo_names if name]
        self.enemy_pages = self._titles("敌人")
        self.enemy_fields = {
            title: template_params(self.pages.get(title, ""), "敌人")
            for title in self.enemy_pages
        }
        self.enemy_names = [
            self._name(title, self.enemy_fields[title]) for title in self.enemy_pages
        ]
        self.enemy_names = [name for name in self.enemy_names if name]

    def _titles(self, category: str) -> list[str]:
        return list(self.categories.get(category, []))

    @staticmethod
    def _name(title: str, params: dict[str, str]) -> str:
        value = _clean(params.get("名称"))
        return value or title

    def _parse_skills(self, title: str) -> list[dict[str, str]]:
        text = self.pages.get(title, "")
        results: list[dict[str, str]] = []

        # 旧版页面: {{角色技能|普攻=...|普攻描述=...}}
        old = template_params(text, "角色技能")
        for key, value in old.items():
            if (
                key.endswith(("描述", "GIF"))
                or "属性" in key
                or "材料" in key
                or "强化" in key
                or re.search(r"\d$", key)
            ):
                continue
            name = _clean(value)
            if not valid_skill_name(name):
                continue
            description = _clean(old.get(f"{key}描述", ""))
            results.append({"group": key, "name": name, "description": description})

        # 新版页面: {{角色/技能|普攻={{角色/技能/项|名称=...|描述=...}}}}
        for block in find_templates(text, "角色/技能"):
            for group, value in parse_params(block).items():
                if (
                    group.endswith(("描述", "GIF"))
                    or "属性" in group
                    or "强化" in group
                    or group == "未加强"
                ):
                    continue
                for item in find_templates(value, "角色/技能/项"):
                    params = parse_params(item)
                    name = _clean(params.get("名称"))
                    description = _clean(params.get("描述"))
                    if not valid_skill_name(name):
                        continue
                    if re.search(r"\d$", group):
                        continue
                    results.append(
                        {"group": group, "name": name, "description": description}
                    )
        return results

    def add(
        self, category: str, group: str, text: str, correct: str, wrongs: list[str]
    ) -> None:
        if any(marker in text for marker in ("{{", "}}", "[[", "]]")):
            return
        add_candidate(self.items, category, group, text, correct, wrongs)

    def _field_values(self, label: str) -> list[str]:
        values = [
            _clean(params.get(label))
            for params in self.agent_fields.values()
            if _clean(params.get(label))
        ]
        return list(dict.fromkeys(values))

    def build_agents(self) -> None:
        labels = (
            "全名",
            "英文名称",
            "阵营",
            "种族",
            "属性",
            "特性",
            "伤害类型",
            "实装版本",
            "中文CV",
            "日文CV",
            "生日",
            "身高",
            "体型",
            "昵称/外号",
            "冲击力",
            "异常掌控",
            "异常精通",
            "能量自动回复",
            "生命值",
            "攻击力",
            "防御力",
        )
        pools = {label: self._field_values(label) for label in labels}
        talents = {
            title: template_params(self.pages.get(title, ""), "天赋")
            for title in self.agent_pages
        }
        all_talent_names = [
            _clean(params.get(f"天赋{i}"))
            for params in talents.values()
            for i in range(1, 7)
            if _clean(params.get(f"天赋{i}"))
        ]

        for title, params in self.agent_fields.items():
            name = self._name(title, params)
            if not name:
                continue
            for label in labels:
                value = _clean(params.get(label))
                if not value or value == name or value in {"是", "否", "男", "女"}:
                    continue
                self.add(
                    "agent",
                    name,
                    f"代理人「{name}」的{label}是？",
                    value,
                    [item for item in pools[label] if item != value],
                )
                self.add(
                    "agent",
                    name,
                    f"以下{label}对应哪位代理人？\n{value}",
                    name,
                    [item for item in self.agent_names if item != name],
                )

            talent_params = talents.get(title, {})
            for index in range(1, 7):
                talent_name = _clean(talent_params.get(f"天赋{index}"))
                effect = _clean(talent_params.get(f"天赋{index}效果"))
                description = _clean(talent_params.get(f"天赋{index}描述"))
                if not talent_name:
                    continue
                self.add(
                    "agent",
                    name,
                    f"天赋「{talent_name}」属于哪位代理人？",
                    name,
                    [item for item in self.agent_names if item != name],
                )
                if len(effect) >= 12 and talent_name not in effect:
                    self.add(
                        "agent",
                        name,
                        f"以下天赋效果对应哪个天赋？\n{shorten(effect, 100)}",
                        talent_name,
                        [item for item in all_talent_names if item != talent_name],
                    )
                if len(description) >= 12 and talent_name not in description:
                    self.add(
                        "agent",
                        name,
                        f"以下天赋描述对应哪个天赋？\n{shorten(description, 100)}",
                        talent_name,
                        [item for item in all_talent_names if item != talent_name],
                    )

        for skill in self.skills:
            skill_name = skill["name"]
            agent_name = skill.get("agent", "")
            if not agent_name:
                continue
            self.add(
                "agent",
                agent_name,
                f"技能「{skill_name}」属于哪位代理人？",
                agent_name,
                [item for item in self.agent_names if item != agent_name],
            )
            group = skill["group"]
            if group:
                self.add(
                    "agent",
                    agent_name,
                    f"技能「{skill_name}」属于哪类技能？",
                    group,
                    [item for item in self.skill_groups if item != group],
                )
            description = skill["description"]
            mentions_other = any(
                item != skill_name and item in description for item in self.skill_names
            )
            if (
                len(description) >= 12
                and skill_name not in description
                and not mentions_other
            ):
                self.add(
                    "agent",
                    agent_name,
                    f"以下技能描述对应哪个技能？\n{shorten(description, 100)}",
                    skill_name,
                    [item for item in self.skill_names if item != skill_name],
                )

    def build_engines(self) -> None:
        rarities = sorted(
            {_clean(params.get("稀有度")) for params in self.engine_fields.values()} - {""}
        )
        specialties = sorted(
            {_clean(params.get("武器属性")) for params in self.engine_fields.values()} - {""}
        )
        substats = sorted(
            {_clean(params.get("副词条")) for params in self.engine_fields.values()} - {""}
        )
        for title, params in self.engine_fields.items():
            name = self._name(title, params)
            if not name:
                continue
            rarity = _clean(params.get("稀有度"))
            specialty = _clean(params.get("武器属性"))
            substat = _clean(params.get("副词条"))
            related = _clean(params.get("相关角色"))
            skill_name = _clean(params.get("武器技能名称"))
            skill_effect = _clean(params.get("武器技能效果"))
            info = _clean(params.get("武器信息"))
            story = _clean(params.get("武器故事"))
            special = _clean(params.get("特殊武器故事"))
            if rarity:
                self.add(
                    "engine",
                    specialty or name,
                    f"音擎「{name}」的稀有度是？",
                    f"{rarity} 级",
                    [f"{item} 级" for item in rarities if item != rarity],
                )
            if specialty:
                self.add(
                    "engine",
                    specialty,
                    f"音擎「{name}」的武器属性是？",
                    specialty,
                    [item for item in specialties if item != specialty],
                )
            if substat:
                self.add(
                    "engine",
                    specialty or name,
                    f"音擎「{name}」的副词条是？",
                    substat,
                    [item for item in substats if item != substat],
                )
            if related:
                self.add(
                    "engine",
                    specialty or name,
                    f"音擎「{name}」的相关角色是？",
                    related,
                    [item for item in self.agent_names if item != related],
                )
            if skill_name:
                self.add(
                    "engine",
                    specialty or name,
                    f"音擎「{name}」的技能名称是？",
                    skill_name,
                    [
                        _clean(item.get("武器技能名称"))
                        for item in self.engine_fields.values()
                        if _clean(item.get("武器技能名称")) != skill_name
                    ],
                )
                self.add(
                    "engine",
                    specialty or name,
                    f"音擎技能「{skill_name}」属于哪个音擎？",
                    name,
                    [item for item in self.engine_names if item != name],
                )
            if len(skill_effect) >= 20 and name not in skill_effect:
                self.add(
                    "engine",
                    specialty or name,
                    f"以下音擎技能效果对应哪个音擎？\n{shorten(skill_effect, 100)}",
                    name,
                    [item for item in self.engine_names if item != name],
                )
            for label, text in (("武器信息", info), ("武器故事", story), ("特殊武器故事", special)):
                if len(text) >= 16 and name not in text:
                    self.add(
                        "engine",
                        specialty or name,
                        f"以下{label}对应哪个音擎？\n{shorten(text, 100)}",
                        name,
                        [item for item in self.engine_names if item != name],
                    )

    def build_discs(self) -> None:
        rarities = sorted(
            {_clean(params.get("最高稀有度")) for params in self.disc_fields.values()} - {""}
        )
        for title, params in self.disc_fields.items():
            name = self._name(title, params)
            if not name:
                continue
            effect2 = _clean(params.get("两件套效果"))
            effect4 = _clean(params.get("四件套效果"))
            description = _clean(params.get("驱动盘描述"))
            rarity = _clean(params.get("最高稀有度"))
            if effect2:
                self.add(
                    "disc",
                    name,
                    f"「{shorten(effect2, 100)}」是哪套驱动盘的两件套效果？",
                    name,
                    [item for item in self.disc_names if item != name],
                )
            if effect4:
                self.add(
                    "disc",
                    name,
                    f"「{shorten(effect4, 100)}」是哪套驱动盘的四件套效果？",
                    name,
                    [item for item in self.disc_names if item != name],
                )
            if len(description) >= 15 and name not in description:
                self.add(
                    "disc",
                    name,
                    f"以下驱动盘描述对应哪套驱动盘？\n{shorten(description, 100)}",
                    name,
                    [item for item in self.disc_names if item != name],
                )
            if rarity:
                self.add(
                    "disc",
                    name,
                    f"驱动盘「{name}」的最高稀有度是？",
                    f"{rarity} 级",
                    [f"{item} 级" for item in rarities if item != rarity],
                )
            for index in range(1, 7):
                story = _clean(params.get(f"故事{index}"))
                if len(story) >= 20 and name not in story:
                    self.add(
                        "disc",
                        name,
                        f"以下驱动盘故事对应哪套驱动盘？\n{shorten(story, 100)}",
                        name,
                        [item for item in self.disc_names if item != name],
                    )

    def build_bangboo(self) -> None:
        rarities = sorted(
            {_clean(params.get("稀有度")) for params in self.bangboo_fields.values()} - {""}
        )
        attributes = sorted(
            {_clean(params.get("伤害属性")) for params in self.bangboo_fields.values()} - {""}
        )
        for title, params in self.bangboo_fields.items():
            name = self._name(title, params)
            if not name:
                continue
            rarity = _clean(params.get("稀有度"))
            attribute = _clean(params.get("伤害属性"))
            faction = _clean(params.get("相关阵营"))
            if rarity:
                self.add(
                    "bangboo",
                    name,
                    f"邦布「{name}」的稀有度是？",
                    f"{rarity} 级",
                    [f"{item} 级" for item in rarities if item != rarity],
                )
            if attribute:
                self.add(
                    "bangboo",
                    name,
                    f"邦布「{name}」的伤害属性是？",
                    attribute,
                    [item for item in attributes if item != attribute],
                )
            if faction:
                self.add(
                    "bangboo",
                    name,
                    f"邦布「{name}」的相关阵营是？",
                    faction,
                    [item for item in self._bangboo_factions() if item != faction],
                )
            for key in ("a", "b", "c"):
                skill_name = _clean(params.get(f"技能{key}"))
                effect = _clean(params.get(f"技能{key}效果"))
                if skill_name:
                    self.add(
                        "bangboo",
                        name,
                        f"邦布「{name}」的技能 {key.upper()} 叫什么？",
                        skill_name,
                        [
                            _clean(item.get(f"技能{key}"))
                            for item in self.bangboo_fields.values()
                            if _clean(item.get(f"技能{key}")) != skill_name
                        ],
                    )
                if len(effect) >= 15 and name not in effect:
                    self.add(
                        "bangboo",
                        name,
                        f"以下邦布技能效果对应哪只邦布？\n{shorten(effect, 90)}",
                        name,
                        [item for item in self.bangboo_names if item != name],
                    )

    def _bangboo_factions(self) -> list[str]:
        return sorted(
            {_clean(params.get("相关阵营")) for params in self.bangboo_fields.values()} - {""}
        )

    def build_enemies(self) -> None:
        for title, params in self.enemy_fields.items():
            name = self._name(title, params)
            if not name:
                continue
            faction = _clean(params.get("阵营"))
            category = _clean(params.get("分类"))
            weakness = _clean(params.get("弱点"))
            introduction = _clean(params.get("介绍"))
            if faction:
                self.add(
                    "enemy",
                    name,
                    f"敌人「{name}」的阵营是？",
                    faction,
                    self._enemy_values("阵营", faction),
                )
            if category:
                self.add(
                    "enemy",
                    name,
                    f"敌人「{name}」的分类是？",
                    category,
                    self._enemy_values("分类", category),
                )
            if weakness:
                self.add(
                    "enemy",
                    name,
                    f"敌人「{name}」的弱点是？",
                    weakness,
                    self._enemy_values("弱点", weakness),
                )
            if len(introduction) >= 20 and name not in introduction:
                self.add(
                    "enemy",
                    name,
                    f"以下介绍对应哪个敌人？\n{shorten(introduction, 100)}",
                    name,
                    [item for item in self.enemy_names if item != name],
                )

    def _enemy_values(self, label: str, exclude: str) -> list[str]:
        return [
            _clean(params.get(label))
            for params in self.enemy_fields.values()
            if _clean(params.get(label)) and _clean(params.get(label)) != exclude
        ]

    def build_lore(self) -> None:
        for question, correct, wrongs in LORE_FACTS:
            self.add("lore", "lore", question, correct, wrongs)

    def build(self) -> list:
        self.build_agents()
        self.build_engines()
        self.build_discs()
        self.build_bangboo()
        self.build_enemies()
        self.build_lore()
        return self.items


def main() -> None:
    parser = argparse.ArgumentParser(description="生成绝区零知识问答题库")
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE_PATH)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_PATH)
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()

    payload = fetch_pages(args.cache)
    rng = random.Random(args.seed)
    candidates = drop_ambiguous(ZzzBank(payload, args.seed).build())
    print("可用题目:", dict(Counter(item.category for item in candidates)))
    selected, categories = select_items(candidates, QUOTAS, args.count, rng)
    questions = format_questions(selected, rng)
    print("入选题目:", dict(categories))
    write_bank(
        args.out,
        questions,
        {
            "source": "Biligame ZZZ wiki (Chinese)",
            "seed": args.seed,
            "categories": dict(categories),
        },
    )


if __name__ == "__main__":
    main()
