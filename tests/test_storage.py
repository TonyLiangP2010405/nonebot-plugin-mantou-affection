from pathlib import Path

from nonebot_plugin_mantou_affection.models import Profile
from nonebot_plugin_mantou_affection.storage import AffectionStore


async def test_store_persists_profile(tmp_path: Path) -> None:
    path = tmp_path / "affection.json"
    store = AffectionStore(path)

    def update(profile: Profile) -> int:
        profile.affection = 42
        return profile.affection

    result = await store.update_profile("100", "200", "桃友", update)
    reloaded = await AffectionStore(path).get_profile("100", "200")
    assert result == 42
    assert reloaded.affection == 42
    assert reloaded.nickname == "桃友"


async def test_ranking_is_group_isolated_and_sorted(tmp_path: Path) -> None:
    store = AffectionStore(tmp_path / "affection.json")

    async def set_score(group: str, user: str, score: int) -> None:
        def update(profile: Profile) -> None:
            profile.affection = score

        await store.update_profile(group, user, f"用户{user}", update)

    await set_score("1", "10", 5)
    await set_score("1", "20", 9)
    await set_score("2", "30", 100)
    entries = await store.ranking("1", 10)
    assert [entry.profile.user_id for entry in entries] == ["20", "10"]
    assert [entry.rank for entry in entries] == [1, 2]


async def test_invalid_data_file_falls_back_to_empty(tmp_path: Path) -> None:
    path = tmp_path / "affection.json"
    path.write_text("not json", encoding="utf-8")
    profile = await AffectionStore(path).get_profile("1", "2")
    assert profile.affection == 0
