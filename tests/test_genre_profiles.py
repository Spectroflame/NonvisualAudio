"""Tests for the JSON-backed genre profile loader."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nonvisualaudio.reporting import genre_profiles


@pytest.fixture
def isolated_user_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Redirect the user data dir to a tmp path and reload the module.

    Cleans up after the test so later tests see the bundled defaults.
    """
    monkeypatch.setattr(genre_profiles, "user_genres_path", lambda: tmp_path / "genres.json")
    genre_profiles.reload()
    yield tmp_path
    # Undo the monkeypatch side-effect on module state.
    genre_profiles.reload()


def test_bundle_has_expected_profile_count(isolated_user_dir: Path):
    # The JSON currently ships 42 profiles across 13 categories.
    # ``isolated_user_dir`` redirects the override file to an empty
    # tmp path so any in-development overrides a developer has saved
    # locally don't inflate the count.
    assert len(genre_profiles.GENRES) == 42
    assert len(genre_profiles.CATEGORY_ORDER) == 13


def test_known_profile_loads_with_correct_numbers(isolated_user_dir: Path):
    cl = genre_profiles.GENRES["classical_orchestral"]
    assert cl.display_name == "Classical — Symphonic or Orchestral"
    assert cl.category == "Classical"
    assert cl.target_lufs == -18.0
    assert cl.lra_low == 16.0
    assert cl.lra_high == 24.0


def test_list_genres_preserves_category_order(isolated_user_dir: Path):
    seen_categories: list[str] = []
    for p in genre_profiles.list_genres():
        if not seen_categories or seen_categories[-1] != p.category:
            seen_categories.append(p.category)
    assert seen_categories == list(genre_profiles.CATEGORY_ORDER)


def test_grouped_genres_matches_category_order(isolated_user_dir: Path):
    grouped = genre_profiles.grouped_genres()
    assert [c for c, _ in grouped] == list(genre_profiles.CATEGORY_ORDER)
    # Every category must contain at least one profile.
    for _, profiles in grouped:
        assert profiles


def test_save_user_overrides_unlinks_file_when_empty(isolated_user_dir: Path):
    # Seed a non-trivial override on disk.
    genre_profiles.save_user_overrides(
        [{"key": "custom", "display_name": "Custom"}],
        [
            {
                "key": "my_genre",
                "category_key": "custom",
                "display_name": "My Genre",
                "target_lufs": -14.0,
                "lra_low": 5.0,
                "lra_high": 10.0,
                "notes": "x",
            }
        ],
    )
    override_path = isolated_user_dir / "genres.json"
    assert override_path.is_file()

    # Saving with both lists empty must unlink the file rather than
    # leaving an empty stub behind. The privacy promise is "nothing
    # on disk if you have nothing to customise".
    genre_profiles.save_user_overrides([], [])
    assert not override_path.exists()


def test_save_user_overrides_does_not_crash_when_no_file_to_remove(
    isolated_user_dir: Path,
):
    # No file present yet; calling save with empty lists is a no-op
    # and must not raise.
    genre_profiles.save_user_overrides([], [])
    assert not (isolated_user_dir / "genres.json").exists()


def test_user_override_adds_new_profile(isolated_user_dir: Path):
    (isolated_user_dir / "genres.json").write_text(
        json.dumps(
            {
                "version": 1,
                "categories": [
                    {"key": "audio_drama", "display_name": "Audio Drama"},
                ],
                "profiles": [
                    {
                        "key": "audio_drama_scifi",
                        "category_key": "audio_drama",
                        "display_name": "Audio Drama — Science Fiction",
                        "target_lufs": -18.0,
                        "lra_low": 8.0,
                        "lra_high": 14.0,
                        "notes": "cinematic sci-fi hörspiel with wide dynamics",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    genre_profiles.reload()
    assert "audio_drama_scifi" in genre_profiles.GENRES
    assert genre_profiles.GENRES["audio_drama_scifi"].category == "Audio Drama"
    assert genre_profiles.profile_origin("audio_drama_scifi") == "user"


def test_user_override_replaces_bundled_profile(isolated_user_dir: Path):
    (isolated_user_dir / "genres.json").write_text(
        json.dumps(
            {
                "version": 1,
                "categories": [],
                "profiles": [
                    {
                        "key": "pop_modern",
                        "category_key": "pop",
                        "display_name": "Pop — Modern Commercial (tweaked)",
                        "target_lufs": -8.0,
                        "lra_low": 3.5,
                        "lra_high": 5.5,
                        "notes": "slightly louder than the stock default",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    genre_profiles.reload()
    assert genre_profiles.GENRES["pop_modern"].target_lufs == -8.0
    assert genre_profiles.GENRES["pop_modern"].display_name.endswith("(tweaked)")
    assert genre_profiles.profile_origin("pop_modern") == "modified"


def test_broken_user_override_falls_back_to_bundle(isolated_user_dir: Path):
    (isolated_user_dir / "genres.json").write_text("{ not valid json", encoding="utf-8")
    # Must not raise; must load the bundle instead.
    genre_profiles.reload()
    assert len(genre_profiles.GENRES) == 42
    assert "pop_modern" in genre_profiles.GENRES


def test_missing_user_override_is_not_an_error(isolated_user_dir: Path):
    # File was never written by the fixture.
    assert not (isolated_user_dir / "genres.json").exists()
    genre_profiles.reload()
    assert len(genre_profiles.GENRES) == 42


def test_save_user_overrides_round_trip(isolated_user_dir: Path):
    # Start with bundle only.
    genre_profiles.reload()
    assert "hoerspiel_custom" not in genre_profiles.GENRES

    categories = [{"key": "audio_drama", "display_name": "Audio Drama"}]
    profiles = [
        {
            "key": "hoerspiel_custom",
            "category_key": "audio_drama",
            "display_name": "Hörspiel — Custom",
            "target_lufs": -20.0,
            "lra_low": 9.0,
            "lra_high": 16.0,
            "notes": "custom profile",
        }
    ]
    path = genre_profiles.save_user_overrides(categories, profiles)

    assert path.exists()
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["version"] == 1
    assert on_disk["profiles"][0]["key"] == "hoerspiel_custom"

    # save_user_overrides calls reload() itself — the new profile must
    # be immediately visible.
    assert "hoerspiel_custom" in genre_profiles.GENRES
    assert genre_profiles.GENRES["hoerspiel_custom"].display_name == "Hörspiel — Custom"
    assert genre_profiles.profile_origin("hoerspiel_custom") == "user"


def test_bundle_profile_returns_unmodified_data(isolated_user_dir: Path):
    # Even with an override shadowing pop_modern, bundle_profile() must
    # return the original values — that's what the editor uses to show
    # "Reset to built-in".
    (isolated_user_dir / "genres.json").write_text(
        json.dumps(
            {
                "version": 1,
                "categories": [],
                "profiles": [
                    {
                        "key": "pop_modern",
                        "category_key": "pop",
                        "display_name": "Pop — Custom",
                        "target_lufs": -5.0,
                        "lra_low": 2.0,
                        "lra_high": 4.0,
                        "notes": "custom",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    genre_profiles.reload()
    original = genre_profiles.bundle_profile("pop_modern")
    assert original is not None
    assert original["target_lufs"] == -9.0
    # After Phase B4 the bundle stores display_name as {en, de} dicts.
    assert original["display_name"]["en"] == "Pop — Modern Commercial"
