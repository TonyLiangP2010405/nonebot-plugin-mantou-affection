from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@dataclass
class Profile:
    user_id: str
    nickname: str = ""
    affection: int = 0
    interaction_date: str = ""
    interaction_count: int = 0
    last_interaction_at: float = 0.0
    linked_date: str = ""
    linked_points: int = 0
    gain_date: str = ""
    gain_points: int = 0
    poke_date: str = ""
    poke_count: int = 0
    peak_affection: int = 0
    zeroed_date: str = ""
    plugin_last_awards: dict[str, float] = field(default_factory=dict)
    updated_at: float = 0.0

    @classmethod
    def from_dict(cls, user_id: str, data: Any) -> Profile:
        if not isinstance(data, dict):
            return cls(user_id=str(user_id))
        affection = max(0, _safe_int(data.get("affection")))
        return cls(
            user_id=str(user_id),
            nickname=str(data.get("nickname", ""))[:64],
            affection=affection,
            interaction_date=str(data.get("interaction_date", "")),
            interaction_count=max(0, _safe_int(data.get("interaction_count"))),
            last_interaction_at=max(0.0, _safe_float(data.get("last_interaction_at"))),
            linked_date=str(data.get("linked_date", "")),
            linked_points=max(0, _safe_int(data.get("linked_points"))),
            gain_date=str(data.get("gain_date", "")),
            gain_points=max(0, _safe_int(data.get("gain_points"))),
            poke_date=str(data.get("poke_date", "")),
            poke_count=max(0, _safe_int(data.get("poke_count"))),
            peak_affection=max(affection, _safe_int(data.get("peak_affection"))),
            zeroed_date=str(data.get("zeroed_date", "")),
            plugin_last_awards={
                str(key): max(0.0, _safe_float(value))
                for key, value in (data.get("plugin_last_awards") or {}).items()
            }
            if isinstance(data.get("plugin_last_awards"), dict)
            else {},
            updated_at=max(0.0, _safe_float(data.get("updated_at"))),
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("user_id", None)
        return data


@dataclass(frozen=True)
class Level:
    number: int
    title: str
    threshold: int
    next_threshold: int | None


@dataclass(frozen=True)
class AffectionSnapshot:
    affection: int
    level: int
    title: str
    band: str


@dataclass(frozen=True)
class AffectionResponse:
    affection: int
    level: int
    title: str
    band: str
    text: str | None


@dataclass(frozen=True)
class InteractionResult:
    accepted: bool
    reason: str
    text: str
    delta: int
    remaining: int
    wait_seconds: int
    affection: int
    level: Level


@dataclass(frozen=True)
class RankingEntry:
    rank: int
    profile: Profile


@dataclass(frozen=True)
class PokeResult:
    delta: int
    text: str
    count: int
    annoyed: bool


@dataclass(frozen=True)
class LinkRewardResult:
    accepted: bool
    reason: str
    delta: int
    affection: int
    linked_points: int
