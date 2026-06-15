#!/usr/bin/env python3
"""Verify the report uses profile-appropriate language (deterministic).

The user is blind and cannot eyeball the rendered report, so this script
asserts the wording rules programmatically and prints a screen-reader
friendly PASS/FAIL summary with the exact offending lines.

Rules checked (German report, the shipping locale):

- A **music** profile (pop, rock, hip-hop, …) may use music wording
  ("Musik-Mix", "Streaming-Master", "Mastering", "Song", …).
- A **mastered spoken-word** profile (Hörspiel — Streaming/Rundfunk,
  Hörbuch, Podcast) must NOT use music wording. It is a finished
  spoken-word production, so speech wording (Sendefassung,
  Streaming-Fassung, Sprachproduktion, Hörspiel-/Sendematerial) is used.
- A **raw speech** profile ("Rohe Sprachaufnahme") must additionally
  avoid any "finished master / commercial mix" claim — it is judged as
  source material, not as a master.

Run:  python scripts/verify_report_profile_language.py
Exit code is non-zero if any rule fails.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from nonvisualaudio.analysis.result import (  # noqa: E402
    AnalysisResult,
    BandEnergies,
    DynamicsMetrics,
    FileInfo,
    LoudnessMetrics,
    SpectrumMetrics,
    StereoMetrics,
)
from nonvisualaudio.localization import load as load_lang  # noqa: E402
from nonvisualaudio.reporting import genre_profiles as gp  # noqa: E402
from nonvisualaudio.reporting.builder import build_report  # noqa: E402
from nonvisualaudio.reporting.comparison import build_genre_comparison  # noqa: E402

# Music wording that must never appear under a speech/Hörspiel profile.
MUSIC_TERMS = (
    "Musik-Mix",
    "Musik-Mixe",
    "Streaming-Master",
    "Rundfunk-Master",
    "Rundfunk-Mastern",
    "Rundfunk-Mastering",
    "Pop- oder Rundfunk",
    "Stereo-Musik",
    "Song",
)
# A bare "Musik" anywhere is also a music assumption for speech profiles.
MUSIC_BARE = "Musik"
# For raw recordings, even the "finished product" framing is wrong.
RAW_FORBIDDEN = (
    "fertiges Master",
    "kommerzieller Mix",
    "kommerziellen Mix",
    "Streaming-Master",
)


def _make_result() -> AnalysisResult:
    """A result tuned to fire every genre-referencing verdict at once:
    loud + narrow LRA + moderate dynamics + low headroom + a diverging
    stereo block. That maximises the chance of catching a music-term
    leak in any section."""
    return AnalysisResult(
        file_info=FileInfo(
            filename="probe.wav",
            duration_seconds=754.0,
            sample_rate=48000,
            channels=2,
            bit_depth=24,
        ),
        loudness=LoudnessMetrics(
            integrated_lufs=-11.0,
            short_term_max_lufs=-9.0,
            true_peak_dbtp=-0.3,
            loudness_range_lu=3.0,
        ),
        dynamics=DynamicsMetrics(
            peak_db=-0.2, rms_db=-9.0, crest_factor_db=7.0, dr_score=7.0
        ),
        spectrum=SpectrumMetrics(
            bands=BandEnergies(
                sub_db=-14.0,
                bass_db=-11.0,
                low_mid_db=-9.0,
                mid_db=-8.0,
                presence_db=-10.0,
                air_db=-18.0,
            ),
            peaks=(),
        ),
        stereo=StereoMetrics(
            is_stereo=True,
            mean_correlation=0.4,
            min_correlation=0.35,
            mono_drop_db=-1.0,
            side_to_mid_db=-6.0,
            min_correlation_time_seconds=10.0,
        ),
    )


# (profile key, human label, is_music_profile, is_raw)
SCENARIOS = [
    ("pop_modern", "Pop (Musik)", True, False),
    ("rock_metal", "Metal (Musik)", True, False),
    ("audio_drama_modern", "Hörspiel — modernes Streaming", False, False),
    ("audio_drama_classic", "Hörspiel — Rundfunk", False, False),
    ("spoken_audiobook", "Hörbuch", False, False),
    ("podcast_news", "Podcast", False, False),
    ("speech_raw_recording", "Rohe Sprachaufnahme", False, True),
    (None, "Kein Profil (neutral)", False, False),
]


def _profile_for(key: str):
    for prof in gp.list_genres():
        if prof.key == key:
            return prof
    raise KeyError(key)


def _offending_lines(text: str, terms) -> list[str]:
    hits = []
    for line in text.splitlines():
        for term in terms:
            if term in line:
                hits.append(f"      [{term}] {line.strip()}")
                break
    return hits


def main() -> int:
    load_lang("de")
    gp.reload()
    result = _make_result()
    failures = 0

    print("Profil-Sprache im Report — Verifikation (Deutsch)\n")
    for key, label, is_music, is_raw in SCENARIOS:
        keys = [key] if key else None
        material = gp.material_context_for(keys)
        tonality = gp.tonality_context_for(keys)
        text = build_report(
            result, material=material, tonality=tonality
        ).to_text()
        # Append the genre-comparison block too — that is where the
        # raw-recording loudness-target wording lives.
        if key:
            comp = build_genre_comparison(result, _profile_for(key))
            text += "\n" + "\n".join(comp.body)

        problems: list[str] = []
        if not is_music:
            terms = list(MUSIC_TERMS) + [MUSIC_BARE]
            problems += _offending_lines(text, terms)
        if is_raw:
            problems += _offending_lines(text, RAW_FORBIDDEN)

        status = "FAIL" if problems else "PASS"
        if problems:
            failures += 1
        print(
            f"  {status}  {label}  "
            f"(material={material}, tonality={tonality})"
        )
        for p in problems:
            print(p)

    print()
    if failures:
        print(f"ERGEBNIS: FAIL — {failures} Profil(e) mit Musik-Wording.")
        return 1
    print("ERGEBNIS: PASS — alle Profile verwenden passende Sprache.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
