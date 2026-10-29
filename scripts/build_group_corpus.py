#!/usr/bin/env python3
"""从 chat-learning 的 learning.db 生成群友语料库。"""

from __future__ import annotations

import argparse
import json
import random
import re
import sqlite3
from pathlib import Path

MIN_LENGTH = 2
MAX_LENGTH = 60
SYMBOL_ONLY = re.compile(r"^[\W_]+$")
DIGIT_ONLY = re.compile(r"^\d+$")
DEFAULT_SEED = 20261029
DEFAULT_COUNT = 10000


def _text_of(chain_json: str) -> str | None:
    """把消息链还原成纯文本；含任何非文本消息段时返回 None。"""

    try:
        chain = json.loads(chain_json)
    except ValueError:
        return None
    if not isinstance(chain, list) or not chain:
        return None
    parts: list[str] = []
    for segment in chain:
        if not isinstance(segment, dict) or segment.get("type") != "text":
            return None
        data = segment.get("data")
        if not isinstance(data, dict):
            return None
        parts.append(str(data.get("text") or ""))
    return "".join(parts).strip()


def _usable(text: str | None) -> bool:
    if text is None:
        return False
    if not MIN_LENGTH <= len(text) <= MAX_LENGTH:
        return False
    if SYMBOL_ONLY.match(text) or DIGIT_ONLY.match(text):
        return False
    return "http" not in text and "www." not in text


def collect_lines(db_path: Path) -> list[str]:
    connection = sqlite3.connect(str(db_path))
    try:
        rows = connection.execute(
            "select chain_json from questions union all select chain_json from answers"
        )
        lines: set[str] = set()
        for (raw,) in rows:
            text = _text_of(str(raw))
            if _usable(text):
                lines.add(text)
        return sorted(lines)
    finally:
        connection.close()


def build(db_path: Path, count: int, seed: int) -> list[str]:
    lines = collect_lines(db_path)
    if count >= len(lines):
        return lines
    return sorted(random.Random(seed).sample(lines, count))


def main() -> None:
    parser = argparse.ArgumentParser(description="生成群友语料库")
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    lines = build(args.db, args.count, args.seed)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps({"lines": lines}, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(f"wrote {len(lines)} lines to {args.out}")


if __name__ == "__main__":
    main()
