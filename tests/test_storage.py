import json
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


async def test_settings_round_trip_and_persist(tmp_path: Path) -> None:
    path = tmp_path / "affection.json"
    store = AffectionStore(path)

    assert await store.get_setting("ambient_probability", 0.01) == 0.01
    await store.set_setting("ambient_probability", 0.05)

    assert await store.get_setting("ambient_probability", 0.01) == 0.05
    reloaded = await AffectionStore(path).get_setting("ambient_probability", 0.01)
    assert reloaded == 0.05


async def test_settings_do_not_disturb_groups(tmp_path: Path) -> None:
    path = tmp_path / "affection.json"
    store = AffectionStore(path)

    def update(profile: Profile) -> None:
        profile.affection = 3

    await store.update_profile("1", "2", "桃友", update)
    await store.set_setting("ambient_probability", 0.2)

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["settings"] == {"ambient_probability": 0.2}
    assert payload["groups"]["1"]["2"]["affection"] == 3


async def test_legacy_file_without_settings_is_upgraded(tmp_path: Path) -> None:
    path = tmp_path / "affection.json"
    path.write_text(
        json.dumps({"version": 1, "groups": {"1": {"2": {"affection": 7}}}}),
        encoding="utf-8",
    )
    store = AffectionStore(path)

    assert await store.get_setting("ambient_probability", 0.01) == 0.01
    assert (await store.get_profile("1", "2")).affection == 7

    await store.set_setting("ambient_probability", 0.5)

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["settings"] == {"ambient_probability": 0.5}
    assert payload["groups"]["1"]["2"]["affection"] == 7


async def test_store_keeps_history_peak_affection(tmp_path: Path) -> None:
    path = tmp_path / "affection.json"
    store = AffectionStore(path)

    def gain(profile: Profile) -> None:
        profile.affection = 12

    await store.update_profile("1", "2", "桃友", gain)
    assert (await store.get_profile("1", "2")).peak_affection == 12

    def lose(profile: Profile) -> None:
        profile.affection = 0

    await store.update_profile("1", "2", "桃友", lose)
    profile = await store.get_profile("1", "2")
    assert profile.affection == 0
    assert profile.peak_affection == 12

    reloaded = await AffectionStore(path).get_profile("1", "2")
    assert reloaded.affection == 0
    assert reloaded.peak_affection == 12


async def test_store_reads_legacy_peak_as_current_affection(tmp_path: Path) -> None:
    path = tmp_path / "affection.json"
    path.write_text(
        json.dumps({"version": 1, "groups": {"1": {"2": {"affection": 9}}}}),
        encoding="utf-8",
    )

    assert (await AffectionStore(path).get_profile("1", "2")).peak_affection == 9
