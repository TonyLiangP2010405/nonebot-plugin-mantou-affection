"""Shared helpers for building three-option quiz banks."""

from __future__ import annotations

import json
import random
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Callable

_HTML = re.compile(r"<[^>]+>")
_SPACES = re.compile(r"\s+")
_TEMPLATE = re.compile(r"\{\{[^{}]*\}\}")
_MARKUP = re.compile(r"\{[^{}]*\}|<[^>]+>|\*\*")


def clean(text: object) -> str:
    value = str(text or "")
    value = value.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
    value = _HTML.sub("", value)
    value = _MARKUP.sub("", value)
    value = value.replace("\\n", "\n")
    return value


def flatten(text: object) -> str:
    return _SPACES.sub(" ", clean(text)).strip()


def shorten(text: object, limit: int = 100) -> str:
    value = flatten(text)
    if len(value) <= limit:
        return value
    cut = value[:limit]
    boundary = max(cut.rfind("。"), cut.rfind("！"), cut.rfind("？"))
    if boundary >= 30:
        return cut[: boundary + 1]
    return cut.rstrip("，、；： ") + "…"


def strip_wiki(text: object) -> str:
    """把常见的 wiki 模板和链接还原成可读文本。"""

    value = str(text or "")

    def replace(match: re.Match[str]) -> str:
        inner = match.group(0)[2:-2]
        parts = inner.split("|")
        if len(parts) >= 3 and parts[0].strip() in {"颜色", "color"}:
            return strip_wiki(parts[-1])
        if len(parts) >= 2 and parts[0].strip() in {"图标", "icon"}:
            return ""
        return ""

    previous = None
    while previous != value:
        previous = value
        value = _TEMPLATE.sub(replace, value)
    value = re.sub(r"\[\[[^|\]]*\|([^\]]*)\]\]", r"\1", value)
    value = re.sub(r"\[\[([^\]]*)\]\]", r"\1", value)
    value = value.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
    value = _HTML.sub("", value)
    value = value.replace("'''", "").replace("''", "")
    return flatten(value)


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


def add_candidate(
    items: list[Candidate],
    category: str,
    group: str,
    text: str,
    correct: str,
    wrongs: list[str],
) -> None:
    """过滤自答、测试条目和包含关系的选项后加入候选池。"""

    correct = str(correct or "").strip()
    text = str(text or "").strip()
    if not correct or not text or correct in text or "test" in text.lower():
        return
    pool: list[str] = []
    for wrong in wrongs:
        wrong = str(wrong or "").strip()
        if not wrong or wrong == correct:
            continue
        if correct in wrong or wrong in correct:
            continue
        if wrong not in pool:
            pool.append(wrong)
    if len(pool) < 2:
        return
    items.append(Candidate(category, group, text, correct, pool))


def drop_ambiguous(items: list[Candidate]) -> list[Candidate]:
    answers: dict[str, set[str]] = defaultdict(set)
    for item in items:
        answers[item.text].add(item.correct)
    ambiguous = {text for text, values in answers.items() if len(values) > 1}
    return [item for item in items if item.text not in ambiguous]


def stratified_sample(
    items: list[Candidate],
    quota: int,
    key: Callable[[Candidate], str],
    rng: random.Random,
) -> list[Candidate]:
    groups: dict[str, list[Candidate]] = defaultdict(list)
    for item in items:
        groups[key(item)].append(item)
    for group in groups.values():
        rng.shuffle(group)
    order = list(groups)
    rng.shuffle(order)
    picked: list[Candidate] = []
    while len(picked) < quota:
        progressed = False
        for name in order:
            group = groups[name]
            if not group:
                continue
            picked.append(group.pop())
            progressed = True
            if len(picked) >= quota:
                break
        if not progressed:
            break
    return picked


def select_items(
    items: list[Candidate],
    quotas: dict[str, int],
    count: int,
    rng: random.Random,
) -> tuple[list[Candidate], Counter[str]]:
    by_category: dict[str, list[Candidate]] = defaultdict(list)
    for item in items:
        by_category[item.category].append(item)

    key_functions: dict[str, Callable[[Candidate], str]] = {
        category: (lambda item: item.group) for category in quotas
    }
    picked: list[Candidate] = []
    for category, quota in quotas.items():
        picked.extend(
            stratified_sample(by_category.get(category, []), quota, key_functions[category], rng)
        )

    unique: dict[str, Candidate] = {}
    for item in picked:
        unique.setdefault(item.text, item)
    if len(unique) < count:
        for item in items:
            if item.text in unique:
                continue
            unique[item.text] = item
            if len(unique) >= count:
                break
    result = list(unique.values())
    if len(result) < count:
        raise SystemExit(f"not enough candidates: {len(result)} < {count}")
    rng.shuffle(result)
    result = result[:count]
    categories = Counter(item.category for item in result)
    return result, categories


def format_questions(
    items: list[Candidate], rng: random.Random
) -> list[dict[str, object]]:
    questions: list[dict[str, object]] = []
    for item in items:
        wrongs = list(item.wrongs)
        rng.shuffle(wrongs)
        options = [item.correct, wrongs[0], wrongs[1]]
        rng.shuffle(options)
        questions.append(
            {
                "text": item.text,
                "options": options,
                "answer": options.index(item.correct),
            }
        )
    return questions


def write_bank(
    path: Path,
    questions: list[dict[str, object]],
    meta: dict[str, object],
) -> None:
    payload = {"meta": {**meta, "count": len(questions)}, "questions": questions}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(f"wrote {len(questions)} questions to {path}")
