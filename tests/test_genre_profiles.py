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
    assert len(genre_profiles.GENRES) == 43
    assert len(genre_profiles.CATEGORY_ORDER) == 14


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
    assert len(genre_profiles.GENRES) == 43
    assert "pop_modern" in genre_profiles.GENRES


def test_schema_broken_user_profile_is_skipped_not_fatal(
    isolated_user_dir: Path, caplog: pytest.LogCaptureFixture
):
    # Valid JSON, but the override profile is missing the required
    # numeric fields (target_lufs / lra_*). This used to raise KeyError
    # straight out of the import-time reload() and prevent the app from
    # starting. It must now be skipped with a warning, leaving the
    # bundled defaults fully usable.
    (isolated_user_dir / "genres.json").write_text(
        json.dumps(
            {"profiles": [{"key": "x", "display_name": "X", "category_key": "c"}]}
        ),
        encoding="utf-8",
    )
    with caplog.at_level("WARNING", logger="nonvisualaudio.genre_profiles"):
        genre_profiles.reload()  # must not raise
    # Broken entry skipped...
    assert "x" not in genre_profiles.GENRES
    # ...bundle defaults intact...
    assert len(genre_profiles.GENRES) == 43
    assert "pop_modern" in genre_profiles.GENRES
    # ...and the skip was logged.
    assert any(
        "malformed genre profile" in rec.getMessage() for rec in caplog.records
    )


def test_one_broken_profile_does_not_drop_valid_siblings(isolated_user_dir: Path):
    # A broken profile next to a well-formed one: only the broken one is
    # dropped, the valid sibling still loads.
    (isolated_user_dir / "genres.json").write_text(
        json.dumps(
            {
                "version": 1,
                "categories": [{"key": "audio_drama", "display_name": "Audio Drama"}],
                "profiles": [
                    {"key": "broken", "category_key": "audio_drama"},
                    {
                        "key": "good_one",
                        "category_key": "audio_drama",
                        "display_name": "Good One",
                        "target_lufs": -18.0,
                        "lra_low": 8.0,
                        "lra_high": 14.0,
                        "notes": "fine",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    genre_profiles.reload()  # must not raise
    assert "broken" not in genre_profiles.GENRES
    assert genre_profiles.GENRES["good_one"].target_lufs == -18.0


def test_missing_user_override_is_not_an_error(isolated_user_dir: Path):
    # File was never written by the fixture.
    assert not (isolated_user_dir / "genres.json").exists()
    genre_profiles.reload()
    assert len(genre_profiles.GENRES) == 43


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


def test_raw_speech_profile_loads_with_null_targets(isolated_user_dir: Path):
    p = genre_profiles.GENRES["speech_raw_recording"]
    assert p.display_name == "Raw speech recording"
    assert p.category == "Speech"
    assert p.material == "speech"
    assert p.target_lufs is None
    assert p.lra_low is None
    assert p.lra_high is None


def test_existing_profiles_default_to_music_material(isolated_user_dir: Path):
    assert genre_profiles.GENRES["pop_modern"].material == "music"
    # Voice-adjacent profiles keep their historic behaviour too.
    assert genre_profiles.GENRES["spoken_audiobook"].material == "music"


def test_explicit_null_target_is_valid_but_missing_key_is_malformed(
    isolated_user_dir: Path,
):
    (isolated_user_dir / "genres.json").write_text(
        json.dumps(
            {
                "version": 1,
                "categories": [{"key": "c", "display_name": "C"}],
                "profiles": [
                    {
                        "key": "null_targets_ok",
                        "category_key": "c",
                        "display_name": "Null Targets",
                        "target_lufs": None,
                        "lra_low": None,
                        "lra_high": None,
                        "notes": "raw material",
                    },
                    {
                        # target_lufs key missing entirely → malformed.
                        "key": "missing_key_bad",
                        "category_key": "c",
                        "display_name": "Missing Key",
                        "lra_low": 5.0,
                        "lra_high": 10.0,
                        "notes": "x",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    genre_profiles.reload()
    assert "null_targets_ok" in genre_profiles.GENRES
    assert genre_profiles.GENRES["null_targets_ok"].target_lufs is None
    assert "missing_key_bad" not in genre_profiles.GENRES


def test_half_null_lra_pair_is_malformed(isolated_user_dir: Path):
    (isolated_user_dir / "genres.json").write_text(
        json.dumps(
            {
                "version": 1,
                "categories": [{"key": "c", "display_name": "C"}],
                "profiles": [
                    {
                        "key": "half_lra",
                        "category_key": "c",
                        "display_name": "Half LRA",
                        "target_lufs": -18.0,
                        "lra_low": 5.0,
                        "lra_high": None,
                        "notes": "x",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    genre_profiles.reload()
    assert "half_lra" not in genre_profiles.GENRES


def test_material_context_no_selection_is_neutral(isolated_user_dir: Path):
    assert genre_profiles.material_context_for([]) == "neutral"
    assert genre_profiles.material_context_for(None) == "neutral"


def test_material_context_unresolved_keys_are_neutral(isolated_user_dir: Path):
    # Keys pointing at deleted/unknown profiles do not count as a selection.
    assert genre_profiles.material_context_for(["does_not_exist"]) == "neutral"


def test_material_context_music_genre_is_music(isolated_user_dir: Path):
    assert genre_profiles.material_context_for(["pop_modern"]) == "music"


def test_material_context_speech_profile_is_speech(isolated_user_dir: Path):
    assert genre_profiles.material_context_for(["speech_raw_recording"]) == "speech"


def test_material_context_speech_wins_in_mixed_selection(isolated_user_dir: Path):
    keys = ["pop_modern", "speech_raw_recording"]
    assert genre_profiles.material_context_for(keys) == "speech"


def test_spoken_word_profiles_declare_speech_tonality(isolated_user_dir: Path):
    # Audio drama / audiobook / podcast stay music-material (they have
    # mastering targets and keep the historic report behaviour) but
    # declare a speech tonality so the overall verdict words the band
    # shape against the expected spoken-word balance.
    for key in (
        "audio_drama_modern",
        "spoken_audiobook",
        "podcast_conversation",
    ):
        assert genre_profiles.GENRES[key].tonality == "speech", key
        assert genre_profiles.GENRES[key].material == "music", key
    assert genre_profiles.GENRES["speech_raw_recording"].tonality == "speech"
    assert genre_profiles.GENRES["pop_modern"].tonality == "full_range"


def test_tonality_context_selection_rules(isolated_user_dir: Path):
    assert genre_profiles.tonality_context_for([]) == "full_range"
    assert genre_profiles.tonality_context_for(None) == "full_range"
    assert genre_profiles.tonality_context_for(["pop_modern"]) == "full_range"
    assert genre_profiles.tonality_context_for(["audio_drama_modern"]) == "speech"
    assert genre_profiles.tonality_context_for(["speech_raw_recording"]) == "speech"
    # A speech declaration is a statement about the material and wins
    # over additionally selected music genres.
    keys = ["pop_modern", "audio_drama_modern"]
    assert genre_profiles.tonality_context_for(keys) == "speech"


def test_user_override_round_trip_preserves_material_and_nulls(
    isolated_user_dir: Path,
):
    # Simulate what the editor does when the user tweaks the built-in
    # speech profile: the raw dict (including material and null targets)
    # is written to the override and must survive reload.
    raw = genre_profiles.raw_profile("speech_raw_recording")
    assert raw is not None
    raw["notes"] = {"en": "tweaked", "de": "angepasst"}
    genre_profiles.save_user_overrides([], [raw])
    p = genre_profiles.GENRES["speech_raw_recording"]
    assert p.material == "speech"
    assert p.target_lufs is None
    assert genre_profiles.profile_origin("speech_raw_recording") == "modified"


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
