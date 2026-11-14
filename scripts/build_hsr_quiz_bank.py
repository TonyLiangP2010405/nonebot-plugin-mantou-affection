#!/usr/bin/env python3
"""生成「星穹铁道知识问答」题库。

数据来自社区整理的 hsr-data 包（中文数据），包含角色、光锥、遗器、物品与成就。
先运行 scripts/extract_hsr_data.mjs 导出 /tmp/hsr_cn.json，再执行本脚本。

用法:
    node scripts/extract_hsr_data.mjs --dir node_modules/hsr-data/dist --out /tmp/hsr_cn.json
    uv run python scripts/build_hsr_quiz_bank.py --data /tmp/hsr_cn.json
"""

from __future__ import annotations

import argparse
import json
import random
import re
from collections import Counter
from pathlib import Path
from typing import Any

from quiz_bank_utils import (
    add_candidate,
    drop_ambiguous,
    format_questions,
    select_items,
    shorten,
    write_bank,
)

DEFAULT_DATA_PATH = Path("/tmp/hsr_cn.json")
DEFAULT_OUT_PATH = (
    Path(__file__).resolve().parent.parent
    / "nonebot_plugin_mantou_affection"
    / "resources"
    / "hsr_quiz_questions.json"
)
DEFAULT_COUNT = 5000
DEFAULT_SEED = 20261114
QUOTAS = {
    "char": 1850,
    "lightcone": 830,
    "relic": 400,
    "item": 950,
    "achievement": 920,
    "lore": 50,
}

LORE_FACTS: list[tuple[str, str, list[str]]] = [
    ("星穹列车的列车长是？", "帕姆", ["姬子", "瓦尔特"]),
    ("星穹列车的领航员是？", "姬子", ["丹恒", "三月七"]),
    ("星穹列车的记录员是？", "丹恒", ["姬子", "帕姆"]),
    ("星穹列车的无名客所追随的命途是？", "开拓", ["毁灭", "巡猎"]),
    ("星穹列车的动力核心与哪种特殊物质有关？", "星核", ["以太", "崩坏能"]),
    ("星核猎手的首领是？", "艾利欧", ["卡芙卡", "刃"]),
    ("星核猎手「刃」的本名是？", "应星", ["丹恒", "景元"]),
    ("星核猎手「银狼」最擅长的领域是？", "骇客技术", ["剑术", "医术"]),
    ("星核猎手「流萤」驾驶的装甲名为？", "萨姆", ["史瓦罗", "可可利亚"]),
    ("贝洛伯格信奉的星神是？", "克里珀", ["纳努克", "岚"]),
    ("贝洛伯格的最高统治者称号是？", "大守护者", ["将军", "大长老"]),
    ("贝洛伯格的寒潮与哪种物质有关？", "星核", ["建木", "忆质"]),
    ("仙舟联盟主要追随哪位星神？", "岚", ["药师", "IX"]),
    ("仙舟罗浮的将军是？", "景元", ["丹恒", "刃"]),
    ("仙舟「建木」与哪位星神有关？", "药师", ["岚", "克里珀"]),
    ("药王秘传主要信奉哪位星神？", "药师", ["岚", "纳努克"]),
    ("匹诺康尼最著名的事物是？", "梦境", ["沙漠", "寒潮"]),
    ("匹诺康尼的「家族」信奉哪位星神？", "希佩", ["阿哈", "浮黎"]),
    ("翁法罗斯常被称为什么之地？", "永恒之地", ["最初的城", "梦境之城"]),
    ("翁法罗斯的黄金裔踏上什么旅途？", "逐火", ["巡海", "开拓"]),
    ("黑塔空间站的主人是？", "黑塔", ["姬子", "艾丝妲"]),
    ("星际和平公司的常用简称是？", "IPC", ["SSS", "HIA"]),
    ("巡海游侠主要践行哪条命途？", "巡猎", ["存护", "丰饶"]),
    ("仙舟「云骑军」的统帅是？", "景元", ["彦卿", "符玄"]),
    ("仙舟十王司主要负责？", "刑罚与审判", ["航海贸易", "星舰建造"]),
    ("雅利洛-VI上的主要城市是？", "贝洛伯格", ["匹诺康尼", "罗浮"]),
    ("开拓命途的星神是？", "阿基维利", ["纳努克", "岚"]),
    ("毁灭命途的星神是？", "纳努克", ["克里珀", "IX"]),
    ("智识命途的星神是？", "博识尊", ["浮黎", "希佩"]),
    ("虚无命途的星神是？", "IX", ["阿哈", "药师"]),
    ("丰饶命途的星神是？", "药师", ["岚", "克里珀"]),
    ("存护命途的星神是？", "克里珀", ["纳努克", "希佩"]),
    ("同谐命途的星神是？", "希佩", ["阿哈", "浮黎"]),
    ("欢愉命途的星神是？", "阿哈", ["希佩", "末王"]),
    ("记忆命途的星神是？", "浮黎", ["IX", "博识尊"]),
    ("终末命途的星神是？", "末王", ["阿哈", "伊德莉拉"]),
    ("纯美命途的星神是？", "伊德莉拉", ["浮黎", "纳努克"]),
    ("繁育命途的星神是？", "塔伊兹育罗斯", ["奥罗博罗斯", "龙"]),
    ("贪饕命途的星神是？", "奥罗博罗斯", ["塔伊兹育罗斯", "岚"]),
    ("不朽命途的星神是？", "龙", ["浮黎", "药师"]),
    ("星神阿基维利对应哪条命途？", "开拓", ["毁灭", "巡猎"]),
    ("星神岚对应哪条命途？", "巡猎", ["丰饶", "存护"]),
    ("星神纳努克对应哪条命途？", "毁灭", ["智识", "同谐"]),
    ("星神药师对应哪条命途？", "丰饶", ["巡猎", "虚无"]),
    ("星神克里珀对应哪条命途？", "存护", ["毁灭", "欢愉"]),
    ("星神博识尊对应哪条命途？", "智识", ["记忆", "终末"]),
    ("星神IX对应哪条命途？", "虚无", ["同谐", "丰饶"]),
    ("星神希佩对应哪条命途？", "同谐", ["欢愉", "纯美"]),
    ("星神浮黎对应哪条命途？", "记忆", ["智识", "繁育"]),
]

_PLACEHOLDER_PERCENT = re.compile(r"#\d+\[[^\]]*\]%")
_PLACEHOLDER = re.compile(r"#\d+\[[^\]]*\]")
_NUMBER_TAG = re.compile(r"#\d+")
_HTML = re.compile(r"<[^>]+>")
_BRACES = re.compile(r"\{[^{}]*\}")
_SPACES = re.compile(r"\s+")


def clean_text(value: Any) -> str:
    text = str(value or "")
    text = _HTML.sub("", text)
    text = _BRACES.sub("", text)
    text = _PLACEHOLDER_PERCENT.sub("一定比例", text)
    text = _PLACEHOLDER.sub("一定数值", text)
    text = _NUMBER_TAG.sub("", text)
    text = text.replace("\\n", "\n").replace("※", "，").replace("|", " ")
    return _SPACES.sub(" ", text).strip()


def path_name(value: Any) -> str:
    return str((value or {}).get("name", "")).strip() if isinstance(value, dict) else ""


def series_name(value: Any) -> str:
    return str((value or {}).get("name", "")).strip() if isinstance(value, dict) else ""


class HsrBank:
    def __init__(self, data: dict[str, list[dict[str, Any]]], seed: int):
        self.data = data
        self.rng = random.Random(seed)
        self.items: list = []
        self.characters = data.get("characters", [])
        self.lightcones = data.get("lightcones", [])
        self.relics = data.get("relics", [])
        self.items_data = data.get("items", [])
        self.achievements = data.get("achievements", [])

        self.char_names = [clean_text(c.get("name")) for c in self.characters]
        self.paths = sorted({path_name(c.get("path")) for c in self.characters} - {""})
        self.elements = sorted(
            {str((c.get("combat_type") or {}).get("name", "")).strip() for c in self.characters}
            - {""}
        )
        self.factions = sorted(
            {series_name(c.get("faction")) for c in self.characters} - {""}
        )
        self.cvs = sorted(
            {
                str((c.get("cv") or {}).get("chinese", "")).strip()
                for c in self.characters
            }
            - {""}
        )
        self.skill_types = sorted(
            {
                str(skill.get("type", "")).strip()
                for c in self.characters
                for skill in c.get("skills", [])
            }
            - {""}
        )
        self.lightcone_names = [clean_text(lc.get("name")) for lc in self.lightcones]
        self.lightcone_paths = sorted(
            {str(lc.get("pathTypeText", "")).strip() for lc in self.lightcones} - {""}
        )
        self.lightcone_rarities = sorted(
            {str(lc.get("rarity", "")).strip() for lc in self.lightcones} - {""}
        )
        self.relic_names = [clean_text(r.get("name")) for r in self.relics]
        self.item_names = [clean_text(i.get("name")) for i in self.items_data]
        self.item_types = sorted(
            {
                str((i.get("type") or {}).get("name", "")).strip()
                for i in self.items_data
                if isinstance(i.get("type"), dict)
            }
            - {""}
        )
        self.achievement_names = [clean_text(a.get("name")) for a in self.achievements]
        self.achievement_series = sorted(
            {clean_text(a.get("seriesText")) for a in self.achievements} - {""}
        )

    def add(self, category: str, group: str, text: str, correct: str, wrongs: list[str]) -> None:
        add_candidate(self.items, category, group, text, correct, wrongs)

    # ------------------------------------------------------------ characters

    def build_characters(self) -> None:
        eidolon_names = [
            clean_text(e.get("name"))
            for c in self.characters
            for e in c.get("eidolons", [])
        ]
        all_skills = [
            (c, skill)
            for c in self.characters
            for skill in c.get("skills", [])
        ]
        all_skill_names = [clean_text(skill.get("name")) for _c, skill in all_skills]
        skills_by_type: dict[str, list[str]] = {}
        for _c, skill in all_skills:
            skill_type = str(skill.get("type", "")).strip()
            skill_name = clean_text(skill.get("name"))
            if skill_type and skill_name:
                skills_by_type.setdefault(skill_type, []).append(skill_name)

        for character in self.characters:
            name = clean_text(character.get("name"))
            group = str(character.get("id", name))
            if not name:
                continue
            path = path_name(character.get("path"))
            element = str((character.get("combat_type") or {}).get("name", "")).strip()
            faction = series_name(character.get("faction"))
            cv = str((character.get("cv") or {}).get("chinese", "")).strip()
            description = clean_text(character.get("description"))
            if path:
                self.add("char", group, f"角色「{name}」的命途是？", path, self.paths)
            if element:
                self.add(
                    "char", group, f"角色「{name}」的战斗属性是？", element, self.elements
                )
            if faction:
                self.add(
                    "char", group, f"角色「{name}」所属的势力是？", faction, self.factions
                )
            if cv:
                self.add(
                    "char", group, f"角色「{name}」的中文配音是？", cv, self.cvs
                )
            if len(description) >= 10 and name not in description:
                self.add(
                    "char",
                    group,
                    f"以下角色简介对应哪位角色？\n{shorten(description, 110)}",
                    name,
                    [n for n in self.char_names if n != name],
                )

            for skill in character.get("skills", []):
                skill_name = clean_text(skill.get("name"))
                skill_type = str(skill.get("type", "")).strip()
                skill_desc = clean_text(skill.get("desc"))
                if not skill_name:
                    continue
                if skill_type:
                    self.add(
                        "char",
                        group,
                        f"技能「{skill_name}」属于什么类型？",
                        skill_type,
                        [t for t in self.skill_types if t != skill_type],
                    )
                    pool = [s for s in skills_by_type.get(skill_type, []) if s != skill_name]
                    self.add(
                        "char",
                        group,
                        f"角色「{name}」的「{skill_type}」技能是？",
                        skill_name,
                        pool,
                    )
                self.add(
                    "char",
                    group,
                    f"技能「{skill_name}」属于哪位角色？",
                    name,
                    [n for n in self.char_names if n != name],
                )
                if len(skill_desc) >= 12 and skill_name not in skill_desc:
                    self.add(
                        "char",
                        group,
                        f"以下技能描述对应哪个技能？\n{shorten(skill_desc, 100)}",
                        skill_name,
                        [n for n in all_skill_names if n != skill_name],
                    )

            for index, eidolon in enumerate(character.get("eidolons", []), start=1):
                eidolon_name = clean_text(eidolon.get("name"))
                if not eidolon_name:
                    continue
                self.add(
                    "char",
                    group,
                    f"星魂「{eidolon_name}」属于哪位角色？",
                    name,
                    [n for n in self.char_names if n != name],
                )
                self.add(
                    "char",
                    group,
                    f"角色「{name}」的第 {index} 星魂叫什么？",
                    eidolon_name,
                    [e for e in eidolon_names if e != eidolon_name],
                )

    # ------------------------------------------------------------ lightcones

    def build_lightcones(self) -> None:
        for cone in self.lightcones:
            name = clean_text(cone.get("name"))
            group = str(cone.get("id", name))
            if not name:
                continue
            path = str(cone.get("pathTypeText", "")).strip()
            rarity = str(cone.get("rarity", "")).strip()
            rarity_text = f"{rarity} 星" if rarity else ""
            desc = clean_text(cone.get("desc"))
            effect_name = clean_text(cone.get("effectName"))
            if path:
                self.add(
                    "lightcone",
                    group,
                    f"光锥「{name}」的命途是？",
                    path,
                    [p for p in self.lightcone_paths if p != path],
                )
            if rarity_text:
                self.add(
                    "lightcone",
                    group,
                    f"光锥「{name}」的稀有度是？",
                    rarity_text,
                    [f"{r} 星" for r in self.lightcone_rarities if r != rarity],
                )
            if len(desc) >= 20 and name not in desc:
                self.add(
                    "lightcone",
                    group,
                    f"以下光锥故事对应哪个光锥？\n{shorten(desc, 110)}",
                    name,
                    [n for n in self.lightcone_names if n != name],
                )
            if effect_name:
                self.add(
                    "lightcone",
                    group,
                    f"光锥「{name}」的被动技能叫什么？",
                    effect_name,
                    [
                        clean_text(c.get("effectName"))
                        for c in self.lightcones
                        if clean_text(c.get("effectName")) != effect_name
                    ],
                )
                self.add(
                    "lightcone",
                    group,
                    f"光锥技能「{effect_name}」属于哪个光锥？",
                    name,
                    [n for n in self.lightcone_names if n != name],
                )

    # ------------------------------------------------------------ relics

    def build_relics(self) -> None:
        for relic in self.relics:
            name = clean_text(relic.get("name"))
            group = str(relic.get("id", name))
            if not name:
                continue
            desc = clean_text(relic.get("desc"))
            effects = relic.get("effects") or {}
            effect2 = clean_text(effects.get("2"))
            effect4 = clean_text(effects.get("4"))
            if effect2:
                self.add(
                    "relic",
                    group,
                    f"「{shorten(effect2, 100)}」是哪个遗器套装的效果？",
                    name,
                    [n for n in self.relic_names if n != name],
                )
            if effect4:
                self.add(
                    "relic",
                    group,
                    f"「{shorten(effect4, 100)}」是哪个遗器套装的效果？",
                    name,
                    [n for n in self.relic_names if n != name],
                )
            if len(desc) >= 15 and name not in desc:
                self.add(
                    "relic",
                    group,
                    f"以下遗器描述对应哪个套装？\n{shorten(desc, 100)}",
                    name,
                    [n for n in self.relic_names if n != name],
                )
            for piece in relic.get("pieces", []):
                piece_name = clean_text(piece.get("name"))
                backstory = clean_text(piece.get("backstory")) or clean_text(piece.get("desc"))
                if not piece_name:
                    continue
                self.add(
                    "relic",
                    group,
                    f"遗器部件「{piece_name}」属于哪个套装？",
                    name,
                    [n for n in self.relic_names if n != name],
                )
                if len(backstory) >= 25 and name not in backstory and piece_name not in backstory:
                    self.add(
                        "relic",
                        group,
                        f"以下遗器故事属于哪个套装？\n{shorten(backstory, 110)}",
                        name,
                        [n for n in self.relic_names if n != name],
                    )

    # ------------------------------------------------------------ items

    def build_items(self) -> None:
        for item in self.items_data:
            name = clean_text(item.get("name"))
            group = str((item.get("type") or {}).get("name", "其他"))
            if not name:
                continue
            item_type = str((item.get("type") or {}).get("name", "")).strip()
            description = clean_text(item.get("description"))
            story = clean_text(item.get("story"))
            if len(description) >= 10 and name not in description:
                self.add(
                    "item",
                    group,
                    f"以下物品描述对应哪件物品？\n{shorten(description, 90)}",
                    name,
                    [n for n in self.item_names if n != name],
                )
            if len(story) >= 10 and name not in story:
                self.add(
                    "item",
                    group,
                    f"以下物品故事对应哪件物品？\n{shorten(story, 90)}",
                    name,
                    [n for n in self.item_names if n != name],
                )
            if item_type:
                self.add(
                    "item",
                    group,
                    f"物品「{name}」属于哪一类？",
                    item_type,
                    [t for t in self.item_types if t != item_type],
                )

    # ------------------------------------------------------------ achievements

    def build_achievements(self) -> None:
        for achievement in self.achievements:
            name = clean_text(achievement.get("name"))
            group = clean_text(achievement.get("seriesText")) or "其他"
            if not name:
                continue
            description = clean_text(achievement.get("description"))
            series = clean_text(achievement.get("seriesText"))
            if len(description) >= 8 and name not in description:
                self.add(
                    "achievement",
                    group,
                    f"以下成就描述对应哪个成就？\n{shorten(description, 80)}",
                    name,
                    [n for n in self.achievement_names if n != name],
                )
            if series:
                self.add(
                    "achievement",
                    group,
                    f"成就「{name}」属于哪个系列？",
                    series,
                    [s for s in self.achievement_series if s != series],
                )

    def build_lore(self) -> None:
        for question, correct, wrongs in LORE_FACTS:
            self.add("lore", "lore", question, correct, wrongs)

    def build(self) -> list:
        self.build_characters()
        self.build_lightcones()
        self.build_relics()
        self.build_items()
        self.build_achievements()
        self.build_lore()
        return self.items


def main() -> None:
    parser = argparse.ArgumentParser(description="生成星穹铁道知识问答题库")
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA_PATH)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_PATH)
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()

    if not args.data.is_file():
        raise SystemExit(
            f"找不到 {args.data}; 先运行 node scripts/extract_hsr_data.mjs"
        )
    data = json.loads(args.data.read_text(encoding="utf-8"))
    rng = random.Random(args.seed)
    candidates = drop_ambiguous(HsrBank(data, args.seed).build())
    print("可用题目:", dict(Counter(item.category for item in candidates)))
    selected, categories = select_items(candidates, QUOTAS, args.count, rng)
    questions = format_questions(selected, rng)
    print("入选题目:", dict(categories))
    write_bank(
        args.out,
        questions,
        {
            "source": "hsr-data ChineseSimplified + curated lore",
            "seed": args.seed,
            "categories": dict(categories),
        },
    )


if __name__ == "__main__":
    main()
