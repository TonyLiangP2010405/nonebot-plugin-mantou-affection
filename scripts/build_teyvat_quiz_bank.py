#!/usr/bin/env python3
"""生成「提瓦特知识问答」题库。

题目来自原神官方中文数据（genshin-db 的 ChineseSimplified 数据）里的角色、
角色语音、武器故事、圣遗物故事、地区描述、成就、材料、料理、秘境与敌人条目，
再补充一小批七神、坎瑞亚、愚人众等主干剧情事实。每题三个选项、一个正确项。

用法：
    npm install genshin-db
    uv run python scripts/build_teyvat_quiz_bank.py \
        --data node_modules/genshin-db/src/min/data.min.json

数据源版本不同时题目内容会随之变化；题库生成后随插件一起发布。
"""

from __future__ import annotations

import argparse
import json
import random
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable

DEFAULT_DATA_PATH = (
    Path(__file__).resolve().parent.parent
    / "node_modules"
    / "genshin-db"
    / "src"
    / "min"
    / "data.min.json"
)
DEFAULT_OUT_PATH = (
    Path(__file__).resolve().parent.parent
    / "nonebot_plugin_mantou_affection"
    / "resources"
    / "teyvat_quiz_questions.json"
)
DEFAULT_COUNT = 5000
DEFAULT_SEED = 20261114

# 每类题目的上限: 总量不足时会把缺口按比例分给还有余量的类别。
CATEGORY_QUOTAS: dict[str, int] = {
    "char": 800,
    "voice": 1000,
    "weapon": 600,
    "artifact": 300,
    "geo": 450,
    "ach": 550,
    "mat": 500,
    "food": 400,
    "domain": 150,
    "enemy": 200,
    "lore": 50,
}

# 主干剧情补充: 七神、坎瑞亚、愚人众与各国大事件。只收录稳定设定。
LORE_FACTS: list[tuple[str, str, list[str]]] = [
    ("提瓦特七神中，蒙德的风神是？", "巴巴托斯", ["摩拉克斯", "巴尔泽布"]),
    ("提瓦特七神中，璃月的岩神是？", "摩拉克斯", ["巴巴托斯", "布耶尔"]),
    ("提瓦特七神中，稻妻的雷神是？", "巴尔泽布", ["巴尔", "芙卡洛斯"]),
    ("提瓦特七神中，须弥的草神是？", "布耶尔", ["芙卡洛斯", "玛薇卡"]),
    ("提瓦特七神中，枫丹的水神是？", "芙卡洛斯", ["布耶尔", "巴尔泽布"]),
    ("提瓦特七神中，纳塔的火神是？", "玛薇卡", ["摩拉克斯", "巴巴托斯"]),
    ("至冬国的统治者通常被称为什么？", "冰之女皇", ["火神", "水神"]),
    ("七神持有的、能与天空岛相连的核心信物是？", "神之心", ["神之眼", "命之座"]),
    ("普通提瓦特人获得元素力的外置器官是？", "神之眼", ["神之心", "邪眼"]),
    ("愚人众用来强制驱动元素力的装置是？", "邪眼", ["神之眼", "神之心"]),
    ("坎瑞亚灾变大约发生在主线开始的多少年前？", "500 年前", ["1000 年前", "100 年前"]),
    ("坎瑞亚是一个以什么技术闻名的地下古国？", "机械与炼金", ["元素魔法", "航海贸易"]),
    (
        "深渊教团的核心目标通常被描述为？",
        "推翻天空岛与七神秩序",
        ["守护七国和平", "复兴坎瑞亚商业"],
    ),
    ("旅行者在提瓦特寻找的血亲，后来成为了深渊教团的？", "公主／王子", ["大祭司", "执行官"]),
    ("愚人众执行官「女士」的本名是？", "罗莎琳", ["哥伦比娅", "桑多涅"]),
    ("愚人众执行官「公子」的本名是？", "达达利亚", ["潘塔罗涅", "多托雷"]),
    ("愚人众执行官「散兵」的本名是？", "斯卡拉姆齐", ["卡皮塔诺", "皮耶罗"]),
    ("愚人众执行官「博士」的本名是？", "多托雷", ["达达利亚", "阿蕾奇诺"]),
    ("愚人众执行官「仆人」的本名是？", "阿蕾奇诺", ["罗莎琳", "哥伦比娅"]),
    ("愚人众执行官「队长」的本名是？", "卡皮塔诺", ["皮耶罗", "普契涅拉"]),
    ("愚人众执行官「少女」的本名是？", "哥伦比娅", ["桑多涅", "阿蕾奇诺"]),
    ("愚人众执行官「丑角」的本名是？", "皮耶罗", ["卡皮塔诺", "多托雷"]),
    ("愚人众执行官「富人」的本名是？", "潘塔罗涅", ["达达利亚", "普契涅拉"]),
    ("愚人众执行官「公鸡」的本名是？", "普契涅拉", ["皮耶罗", "潘塔罗涅"]),
    ("愚人众执行官「木偶」的本名是？", "桑多涅", ["哥伦比娅", "罗莎琳"]),
    ("蒙德的风魔龙原名是？", "特瓦林", ["杜林", "乌尔法"]),
    ("蒙德的风魔龙是被谁唤醒并操控的？", "深渊教团", ["愚人众", "盗宝团"]),
    ("璃月港上空、凝光建造的浮空宫殿叫什么？", "群玉阁", ["天守阁", "望舒客栈"]),
    ("璃月七星中负责掌管权力的代表人物是？", "凝光", ["刻晴", "北斗"]),
    ("璃月的仙人中，被称为「留云借风真君」的是？", "闲云", ["甘雨", "申鹤"]),
    ("璃月港外海中的魔神残魂封印与哪座建筑有关？", "群玉阁", ["北斗的船", "望舒客栈"]),
    ("稻妻幕府颁布的、收缴神之眼的法令叫？", "眼狩令", ["锁国令", "奉行令"]),
    ("稻妻长期封锁与外界的往来，这项政策叫？", "锁国令", ["眼狩令", "天领令"]),
    ("稻妻鸣神大社的宫司是？", "八重神子", ["神里绫华", "珊瑚宫心海"]),
    ("稻妻反抗军「珊瑚宫」的领袖是？", "珊瑚宫心海", ["五郎", "九条裟罗"]),
    ("稻妻踏鞴砂灾难与哪座冶炼设施有关？", "御影炉心", ["天守阁", "鸣神大社"]),
    ("渊下宫所在的白夜国崇拜的神明是？", "奥罗巴斯", ["巴尔泽布", "若陀龙王"]),
    ("鹤观岛上被村民祭祀、后来发怒的巨鸟是？", "雷鸟", ["特瓦林", "杜林"]),
    ("清籁岛上由雷元素凝聚成的强大存在是？", "雷音权现", ["雷丘丘王", "无相之雷"]),
    ("须弥教令院中负责管理知识的核心系统叫？", "虚空终端", ["地脉终端", "神谕系统"]),
    ("须弥沙漠中被称为「赤王」的魔神是？", "阿赫玛尔", ["布耶尔", "娜布·玛莉卡塔"]),
    ("须弥传说中与赤王、大慈树王并列的花神是？", "娜布·玛莉卡塔", ["纳西妲", "妮露"]),
    ("须弥雨林中的森林精灵叫什么？", "兰那罗", ["嘟嘟可", "蕈兽"]),
    ("须弥的前任草神「大慈树王」与现任草神的关系是？", "前任与继任", ["姐妹", "母女"]),
    ("枫丹的最高审判官是？", "那维莱特", ["芙宁娜", "克洛琳德"]),
    ("枫丹预言中，枫丹人最终会遭遇什么？", "被海水溶解", ["被雷暴吞噬", "被沙漠掩埋"]),
    ("枫丹的「水仙十字结社」核心人物之一是？", "雷内", ["娜维娅", "芙宁娜"]),
    ("枫丹的原始胎海之水与什么现象密切相关？", "枫丹人的溶解", ["坎瑞亚灾变", "魔神战争"]),
    ("纳塔的六大部族共同推举的领袖被称为？", "火神", ["大酋长", "祭司王"]),
    ("纳塔的「回声之子」部族主要使用哪种龙伙伴？", "嵴锋龙", ["匿叶龙", "鳍游龙"]),
    ("纳塔的「燃素」主要用于什么？", "驱动纳塔的火焰与战斗", ["制造神之眼", "开启传送锚点"]),
    ("愚人众执行官一共有多少位？", "十一位", ["七位", "十三位"]),
    ("提瓦特七国中，以「契约」为信条的是？", "璃月", ["蒙德", "稻妻"]),
]

_PLACEHOLDER = re.compile(r"\{[^}]*\}")
_COLOR = re.compile(r"<color=#[0-9A-Fa-f]+>|</color>|\{LINK#[^}]+\}|\{/LINK>|\{/LINK\}|\*\*")


def clean(text: Any) -> str:
    value = str(text or "")
    value = _COLOR.sub("", value)
    value = value.replace("{NICKNAME}", "旅行者").replace("\\n", "\n")
    value = _PLACEHOLDER.sub("", value)
    return value


def flatten(text: Any) -> str:
    return re.sub(r"\s+", " ", clean(text)).strip()


def shorten(text: Any, limit: int = 100) -> str:
    value = flatten(text)
    if len(value) <= limit:
        return value
    cut = value[:limit]
    boundary = max(cut.rfind("。"), cut.rfind("！"), cut.rfind("？"))
    if boundary >= 30:
        return cut[: boundary + 1]
    return cut.rstrip("，、；： ") + "…"


class Candidate:
    __slots__ = ("category", "correct", "group", "text", "wrongs")

    def __init__(
        self,
        category: str,
        group: str,
        text: str,
        correct: str,
        wrongs: list[str],
    ):
        self.category = category
        self.group = group
        self.text = text.strip()
        self.correct = str(correct).strip()
        self.wrongs = wrongs


class BankBuilder:
    def __init__(self, data: dict[str, Any], seed: int):
        self.zh: dict[str, dict[str, Any]] = data["data"]["ChineseSimplified"]
        self.rng = random.Random(seed)
        self.characters: dict[str, dict[str, Any]] = self.zh["characters"]
        self.character_names: dict[str, str] = {
            key: str(value.get("name", "")).strip()
            for key, value in self.characters.items()
            if str(value.get("name", "")).strip()
        }
        self.all_character_names = set(self.character_names.values())
        self.candidates: list[Candidate] = []

    # ---------------------------------------------------------------- helpers

    def _mentions_character(self, text: str) -> bool:
        if any(name in text for name in self.all_character_names):
            return True
        return "派蒙" in text

    def _add(
        self,
        category: str,
        group: str,
        text: str,
        correct: str,
        wrongs: list[str],
    ) -> None:
        correct = str(correct).strip()
        if not correct or not text.strip() or correct in text:
            # 正确项直接出现在题干里就没有可答性了。
            return
        if "test" in text.lower():
            # 数据源里混有少量测试条目。
            return
        pool: list[str] = []
        for wrong in wrongs:
            wrong = str(wrong).strip()
            if not wrong or wrong == correct:
                continue
            # 避免「雷电将军 / 将军」这类包含关系让选项产生歧义。
            if correct in wrong or wrong in correct:
                continue
            if wrong not in pool:
                pool.append(wrong)
        if len(pool) < 2:
            return
        self.candidates.append(Candidate(category, group, text, correct, pool))

    # ---------------------------------------------------------------- builders

    def build_characters(self) -> None:
        values = list(self.characters.values())
        titles = [v.get("title", "").strip() for v in values if v.get("title")]
        regions = sorted({v.get("region", "").strip() for v in values if v.get("region")})
        elements = sorted(
            {
                v.get("elementText", "").strip()
                for v in values
                if v.get("elementText") and v.get("elementText") != "无"
            }
        )
        weapons = sorted({v.get("weaponText", "").strip() for v in values if v.get("weaponText")})
        constellations = [
            v.get("constellation", "").strip() for v in values if v.get("constellation")
        ]
        birthdays = sorted(
            {v.get("birthday", "").strip() for v in values if v.get("birthday")}
        )
        affiliations = sorted(
            {v.get("affiliation", "").strip() for v in values if v.get("affiliation")}
        )
        for key, value in self.characters.items():
            name = str(value.get("name", "")).strip()
            if not name:
                continue
            title = str(value.get("title", "")).strip()
            if title and name not in title:
                self._add(
                    "char",
                    key,
                    f"角色「{name}」的称号是？",
                    title,
                    [t for t in titles if t != title],
                )
                self._add(
                    "char",
                    key,
                    f"称号「{title}」属于哪位角色？",
                    name,
                    [n for n in self.all_character_names if n != name],
                )
            element = str(value.get("elementText", "")).strip()
            if element and element != "无":
                self._add(
                    "char",
                    key,
                    f"角色「{name}」的神之眼元素是？",
                    element,
                    [e for e in elements if e != element],
                )
            weapon = str(value.get("weaponText", "")).strip()
            if weapon:
                self._add(
                    "char",
                    key,
                    f"角色「{name}」使用哪种武器？",
                    weapon,
                    [w for w in weapons if w != weapon],
                )
            region = str(value.get("region", "")).strip()
            if region:
                self._add(
                    "char",
                    key,
                    f"角色「{name}」来自哪个地区？",
                    region,
                    [r for r in regions if r != region],
                )
            constellation = str(value.get("constellation", "")).strip()
            if constellation:
                self._add(
                    "char",
                    key,
                    f"角色「{name}」的命之座是？",
                    constellation,
                    [c for c in constellations if c != constellation],
                )
            birthday = str(value.get("birthday", "")).strip()
            if birthday:
                self._add(
                    "char",
                    key,
                    f"角色「{name}」的生日是哪天？",
                    birthday,
                    [b for b in birthdays if b != birthday],
                )
            affiliation = str(value.get("affiliation", "")).strip()
            if affiliation:
                self._add(
                    "char",
                    key,
                    f"角色「{name}」的所属组织是？",
                    affiliation,
                    [a for a in affiliations if a != affiliation],
                )
            description = flatten(value.get("description"))
            if len(description) >= 10 and not self._mentions_character(description):
                self._add(
                    "char",
                    key,
                    f"以下描述对应哪位角色？\n{shorten(description, 120)}",
                    name,
                    [n for n in self.all_character_names if n != name],
                )

    def build_voiceovers(self) -> None:
        for key, value in self.zh["voiceovers"].items():
            name = self.character_names.get(key, "")
            if not name:
                continue
            for line in value.get("friendLines", []):
                parts = [part.strip() for part in clean(line.get("description")).split("\n")]
                pick = ""
                for part in parts:
                    part = re.sub(r"^(派蒙|旅行者)：", "", part).strip()
                    if (
                        14 <= len(part) <= 64
                        and "旅行者" not in part
                        and not self._mentions_character(part)
                    ):
                        pick = part
                        break
                if not pick:
                    continue
                self._add(
                    "voice",
                    key,
                    f"「{pick}」——这句台词出现在哪位角色的角色语音里？",
                    name,
                    [n for n in self.all_character_names if n != name],
                )

    def build_weapons(self) -> None:
        values = list(self.zh["weapons"].values())
        names = [v.get("name", "") for v in values if v.get("name")]
        types = sorted({v.get("weaponText", "") for v in values if v.get("weaponText")})
        stats = sorted({v.get("mainStatText", "") for v in values if v.get("mainStatText")})
        for key, value in self.zh["weapons"].items():
            name = str(value.get("name", "")).strip()
            if not name:
                continue
            description = flatten(value.get("description"))
            if len(description) >= 10 and name not in description:
                self._add(
                    "weapon",
                    key,
                    f"以下描述对应哪把武器？\n{shorten(description)}",
                    name,
                    [n for n in names if n != name],
                )
            story = flatten(value.get("story"))
            snippet = ""
            if len(story) >= 30 and name not in story:
                for sentence in re.split(r"(?<=[。！？])", story):
                    sentence = sentence.strip()
                    if 24 <= len(sentence) <= 90 and name not in sentence:
                        snippet = sentence
                        break
            if snippet:
                self._add(
                    "weapon",
                    key,
                    f"以下武器故事对应哪把武器？\n「{snippet}」",
                    name,
                    [n for n in names if n != name],
                )
            weapon_type = str(value.get("weaponText", "")).strip()
            if weapon_type:
                self._add(
                    "weapon",
                    key,
                    f"武器「{name}」的类型是？",
                    weapon_type,
                    [t for t in types if t != weapon_type],
                )
            stat = str(value.get("mainStatText", "")).strip()
            if stat:
                self._add(
                    "weapon",
                    key,
                    f"武器「{name}」的副属性是？",
                    stat,
                    [s for s in stats if s != stat],
                )

    def build_artifacts(self) -> None:
        names = [v.get("name", "") for v in self.zh["artifacts"].values() if v.get("name")]
        for key, value in self.zh["artifacts"].items():
            name = str(value.get("name", "")).strip()
            if not name:
                continue
            effect2 = flatten(value.get("effect2Pc"))
            if effect2:
                self._add(
                    "artifact",
                    key,
                    f"「{effect2}」是哪套圣遗物的2件套效果？",
                    name,
                    [n for n in names if n != name],
                )
            effect4 = flatten(value.get("effect4Pc"))
            if effect4 and len(effect4) <= 120:
                self._add(
                    "artifact",
                    key,
                    f"「{effect4}」是哪套圣遗物的4件套效果？",
                    name,
                    [n for n in names if n != name],
                )
            for slot in ("flower", "plume", "sands", "goblet", "circlet"):
                piece = value.get(slot) or {}
                piece_name = str(piece.get("name", "")).strip()
                story = flatten(piece.get("story")) or flatten(piece.get("description"))
                if not piece_name or len(story) < 20:
                    continue
                if name in story or piece_name in story:
                    continue
                self._add(
                    "artifact",
                    key,
                    f"以下圣遗物故事属于哪套圣遗物？\n「{shorten(story, 110)}」",
                    name,
                    [n for n in names if n != name],
                )

    def build_geographies(self) -> None:
        values = list(self.zh["geographies"].values())
        names = [v.get("name", "") for v in values if v.get("name")]
        regions = sorted(
            {v.get("regionName", "") for v in values if v.get("regionName")}
        )
        for _key, value in self.zh["geographies"].items():
            name = str(value.get("name", "")).strip()
            description = flatten(value.get("description"))
            if not name or len(description) < 12 or name in description:
                continue
            self._add(
                "geo",
                str(value.get("regionName", "")),
                f"以下描述对应提瓦特的哪个地点？\n{shorten(description)}",
                name,
                [n for n in names if n != name],
            )
            region = str(value.get("regionName", "")).strip()
            if region:
                self._add(
                    "geo",
                    region,
                    f"地点「{name}」位于哪个地区？",
                    region,
                    [r for r in regions if r != region],
                )

    def build_achievements(self) -> None:
        values = list(self.zh["achievements"].values())
        groups = sorted(
            {v.get("achievementGroupName", "") for v in values if v.get("achievementGroupName")}
        )
        names = [v.get("name", "") for v in values if v.get("name")]
        for _key, value in self.zh["achievements"].items():
            name = str(value.get("name", "")).strip()
            if not name:
                continue
            stage = value.get("stage1") or {}
            description = flatten(stage.get("description")) if isinstance(stage, dict) else ""
            if len(description) >= 8 and name not in description:
                self._add(
                    "ach",
                    str(value.get("achievementGroupName", "")),
                    f"以下成就描述对应哪个成就？\n{shorten(description, 90)}",
                    name,
                    [n for n in names if n != name],
                )
            group = str(value.get("achievementGroupName", "")).strip()
            if group:
                self._add(
                    "ach",
                    group,
                    f"成就「{name}」属于哪个成就组？",
                    group,
                    [g for g in groups if g != group],
                )

    def build_materials(self) -> None:
        values = list(self.zh["materials"].values())
        names = [v.get("name", "") for v in values if v.get("name")]
        types = sorted({v.get("typeText", "") for v in values if v.get("typeText")})
        for _key, value in self.zh["materials"].items():
            name = str(value.get("name", "")).strip()
            description = flatten(value.get("description"))
            if not name or len(description) < 10 or name in description:
                continue
            category = str(value.get("typeText", "")).strip()
            self._add(
                "mat",
                category,
                f"以下描述对应哪种材料？\n{shorten(description)}",
                name,
                [n for n in names if n != name],
            )
            if category:
                self._add(
                    "mat",
                    category,
                    f"材料「{name}」属于哪一类？",
                    category,
                    [t for t in types if t != category],
                )

    def build_foods(self) -> None:
        values = list(self.zh["foods"].values())
        names = [v.get("name", "") for v in values if v.get("name")]
        types = sorted({v.get("filterText", "") for v in values if v.get("filterText")})
        for _key, value in self.zh["foods"].items():
            name = str(value.get("name", "")).strip()
            if not name:
                continue
            description = flatten(value.get("description"))
            if len(description) >= 10 and name not in description:
                self._add(
                    "food",
                    str(value.get("filterText", "")),
                    f"以下描述对应哪道料理？\n{shorten(description)}",
                    name,
                    [n for n in names if n != name],
                )
            ingredients = value.get("ingredients") or []
            if isinstance(ingredients, list):
                ingredient_names = [
                    str(item.get("name", ""))
                    for item in ingredients
                    if isinstance(item, dict) and item.get("name")
                ]
                if ingredient_names:
                    self._add(
                        "food",
                        str(value.get("filterText", "")),
                        f"「{'、'.join(ingredient_names)}」是哪道料理的食材？",
                        name,
                        [n for n in names if n != name],
                    )
            food_type = str(value.get("filterText", "")).strip()
            if food_type:
                self._add(
                    "food",
                    food_type,
                    f"料理「{name}」属于哪类料理？",
                    food_type,
                    [t for t in types if t != food_type],
                )

    def build_domains(self) -> None:
        values = list(self.zh["domains"].values())
        names = [v.get("name", "") for v in values if v.get("name")]
        regions = sorted({v.get("regionName", "") for v in values if v.get("regionName")})
        for _key, value in self.zh["domains"].items():
            name = str(value.get("name", "")).strip()
            description = flatten(value.get("description"))
            if not name:
                continue
            if len(description) >= 12 and name not in description:
                self._add(
                    "domain",
                    str(value.get("regionName", "")),
                    f"以下描述对应哪个秘境？\n{shorten(description)}",
                    name,
                    [n for n in names if n != name],
                )
            region = str(value.get("regionName", "")).strip()
            if region:
                self._add(
                    "domain",
                    region,
                    f"秘境「{name}」位于哪个地区？",
                    region,
                    [r for r in regions if r != region],
                )

    def build_enemies(self) -> None:
        values = list(self.zh["enemies"].values())
        names = [v.get("name", "") for v in values if v.get("name")]
        categories = sorted(
            {v.get("categoryText", "") for v in values if v.get("categoryText")}
        )
        for _key, value in self.zh["enemies"].items():
            name = str(value.get("name", "")).strip()
            if not name:
                continue
            category = str(value.get("categoryText", "")).strip()
            description = flatten(value.get("description"))
            if len(description) >= 10 and name not in description:
                self._add(
                    "enemy",
                    category,
                    f"以下描述对应哪种敌人？\n{shorten(description)}",
                    name,
                    [n for n in names if n != name],
                )
            if category:
                self._add(
                    "enemy",
                    category,
                    f"敌人「{name}」属于哪一类？",
                    category,
                    [c for c in categories if c != category],
                )

    def build_lore(self) -> None:
        for question, correct, wrongs in LORE_FACTS:
            self._add("lore", "lore", question, correct, wrongs)

    # ---------------------------------------------------------------- output

    def build(self) -> list[Candidate]:
        for builder in (
            self.build_characters,
            self.build_voiceovers,
            self.build_weapons,
            self.build_artifacts,
            self.build_geographies,
            self.build_achievements,
            self.build_materials,
            self.build_foods,
            self.build_domains,
            self.build_enemies,
            self.build_lore,
        ):
            builder()
        return self.candidates


def drop_ambiguous(candidates: list[Candidate]) -> list[Candidate]:
    """同一题干对应多个不同正确答案时全部丢弃，避免无解题目。"""

    answers: dict[str, set[str]] = defaultdict(set)
    for candidate in candidates:
        answers[candidate.text].add(candidate.correct)
    ambiguous = {text for text, values in answers.items() if len(values) > 1}
    return [candidate for candidate in candidates if candidate.text not in ambiguous]


def stratified_sample(
    candidates: list[Candidate],
    quota: int,
    key: Callable[[Candidate], str],
    rng: random.Random,
) -> list[Candidate]:
    """先按分组轮转取样，保证同一角色/地区/分类不会垄断题量。"""

    groups: dict[str, list[Candidate]] = defaultdict(list)
    for candidate in candidates:
        groups[key(candidate)].append(candidate)
    for items in groups.values():
        rng.shuffle(items)
    order = list(groups)
    rng.shuffle(order)
    picked: list[Candidate] = []
    while len(picked) < quota:
        progressed = False
        for group in order:
            items = groups[group]
            if not items:
                continue
            picked.append(items.pop())
            progressed = True
            if len(picked) >= quota:
                break
        if not progressed:
            break
    return picked


def select_questions(
    candidates: list[Candidate], count: int, rng: random.Random
) -> list[Candidate]:
    by_category: dict[str, list[Candidate]] = defaultdict(list)
    for candidate in candidates:
        by_category[candidate.category].append(candidate)

    key_functions: dict[str, Callable[[Candidate], str]] = {
        "char": lambda c: c.group,
        "voice": lambda c: c.group,
        "weapon": lambda c: c.correct,
        "artifact": lambda c: c.group,
        "geo": lambda c: c.group,
        "ach": lambda c: c.group,
        "mat": lambda c: c.group,
        "food": lambda c: c.group,
        "domain": lambda c: c.group,
        "enemy": lambda c: c.group,
        "lore": lambda c: c.group,
    }

    picked: list[Candidate] = []
    leftover: dict[str, list[Candidate]] = {}
    for category, quota in CATEGORY_QUOTAS.items():
        available = by_category.get(category, [])
        chosen = stratified_sample(
            available, quota, key_functions[category], rng
        )
        picked.extend(chosen)
        used = {id(candidate) for candidate in chosen}
        leftover[category] = [
            candidate for candidate in available if id(candidate) not in used
        ]

    # 配额没填满时, 从还有余量的类别里按轮转补齐。
    shortfall = count - len(picked)
    while shortfall > 0:
        progressed = False
        for category in CATEGORY_QUOTAS:
            pool = leftover.get(category, [])
            if not pool:
                continue
            key = key_functions[category]
            groups: dict[str, list[Candidate]] = defaultdict(list)
            for candidate in pool:
                groups[key(candidate)].append(candidate)
            for items in groups.values():
                rng.shuffle(items)
            order = list(groups)
            rng.shuffle(order)
            for group in order:
                if not groups[group]:
                    continue
                picked.append(groups[group].pop())
                shortfall -= 1
                progressed = True
                if shortfall <= 0:
                    break
            if shortfall <= 0:
                break
        if not progressed:
            break

    if len(picked) < count:
        raise SystemExit(
            f"可用题目不足：只凑到 {len(picked)} 道，需要 {count} 道；请检查数据源或降低题量"
        )

    unique: dict[str, Candidate] = {}
    for candidate in picked:
        unique.setdefault(candidate.text, candidate)
    if len(unique) < count:
        # 不同类别可能产出完全相同的题干(正确项也相同), 去重后用剩余题目补齐。
        for candidate in candidates:
            if candidate.text in unique:
                continue
            unique[candidate.text] = candidate
            if len(unique) >= count:
                break
    result = list(unique.values())
    if len(result) < count:
        raise SystemExit(f"去重后不足 {count} 道（实际 {len(result)} 道）")
    rng.shuffle(result)
    return result[:count]


def format_questions(candidates: list[Candidate], rng: random.Random) -> list[dict]:
    questions: list[dict] = []
    for candidate in candidates:
        wrongs = list(candidate.wrongs)
        rng.shuffle(wrongs)
        options = [candidate.correct, wrongs[0], wrongs[1]]
        rng.shuffle(options)
        questions.append(
            {
                "text": candidate.text,
                "options": options,
                "answer": options.index(candidate.correct),
            }
        )
    return questions


def main() -> None:
    parser = argparse.ArgumentParser(description="生成提瓦特知识问答题库")
    parser.add_argument(
        "--data",
        type=Path,
        default=DEFAULT_DATA_PATH,
        help="genshin-db 的 data.min.json 路径",
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_PATH)
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()

    if not args.data.is_file():
        raise SystemExit(
            f"找不到数据源 {args.data}；先执行 npm install genshin-db，"
            "或用 --data 指定 data.min.json"
        )
    with args.data.open("r", encoding="utf-8") as file:
        data = json.load(file)

    rng = random.Random(args.seed)
    builder = BankBuilder(data, args.seed)
    candidates = drop_ambiguous(builder.build())
    counts = Counter(candidate.category for candidate in candidates)
    print("可用题目：", dict(counts), "合计", len(candidates))

    selected = select_questions(candidates, args.count, rng)
    questions = format_questions(selected, rng)
    selected_counts = Counter(candidate.category for candidate in selected)
    print("入选题目：", dict(selected_counts))

    payload = {
        "meta": {
            "source": "genshin-db ChineseSimplified data",
            "count": len(questions),
            "seed": args.seed,
            "categories": dict(selected_counts),
        },
        "questions": questions,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(f"wrote {len(questions)} questions to {args.out}")


if __name__ == "__main__":
    main()
