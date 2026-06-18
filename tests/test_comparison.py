from tests.test_builder import _make_result
from nonvisualaudio.analysis.result import LoudnessMetrics
from nonvisualaudio.reporting.comparison import (
    build_genre_comparison,
    build_reference_comparison,
)
from nonvisualaudio.reporting.genre_profiles import GENRES
from nonvisualaudio.reporting.templates import Section


def _flat(section: Section) -> str:
    """Heading + body joined into one searchable string."""
    parts: list[str] = []
    if section.heading is not None:
        parts.append(section.heading)
    parts.extend(section.body)
    return "\n".join(parts)


def test_genre_comparison_flags_loud_file_vs_classical():
    loud = _make_result(
        loudness=LoudnessMetrics(
            integrated_lufs=-6.0,
            short_term_max_lufs=-3.0,
            true_peak_dbtp=-0.5,
            loudness_range_lu=4.0,
        )
    )
    text = _flat(build_genre_comparison(loud, GENRES["classical_orchestral"]))
    assert "COMPARISON TO" in text
    assert "CLASSICAL" in text
    assert "louder" in text.lower()


def test_reference_comparison_detects_level_difference():
    target = _make_result()
    quieter = _make_result(
        loudness=LoudnessMetrics(
            integrated_lufs=-30.0,
            short_term_max_lufs=-22.0,
            true_peak_dbtp=-6.0,
            loudness_range_lu=8.0,
        )
    )
    text = _flat(build_reference_comparison(target, quieter))
    assert "COMPARISON TO REFERENCE FILE" in text
    assert "louder" in text.lower()


def test_reference_comparison_uses_project_intro_when_reference_is_project():
    from nonvisualaudio.localization import load
    load("en")
    target = _make_result()
    # A "project reference" is just an AnalysisResult whose file_info
    # carries the project's display name. We tag the call with the
    # reference_is_project flag so the intro line reads "Reference
    # project: …" instead of "Reference filename: …".
    ref = _make_result()
    text = _flat(build_reference_comparison(target, ref, reference_is_project=True))
    assert "Reference project:" in text
    assert "Reference filename:" not in text


def test_reference_comparison_addresses_project_when_target_is_project():
    from nonvisualaudio.localization import load
    load("en")
    target = _make_result()
    quieter = _make_result(
        loudness=LoudnessMetrics(
            integrated_lufs=-30.0,
            short_term_max_lufs=-22.0,
            true_peak_dbtp=-6.0,
            loudness_range_lu=8.0,
        )
    )
    text = _flat(build_reference_comparison(target, quieter, project=True))
    # The "X is louder than the reference" sentence speaks of the
    # project (target) instead of "the target".
    assert "the project" in text.lower()
    assert "the target" not in text.lower()


def test_reference_comparison_recommends_lowering_louder_reference():
    from nonvisualaudio.localization import load
    load("en")
    # Reference (-15 LUFS) is louder than the target (-21.4 default), so
    # for a fair A/B test the reference must come down by the difference.
    target = _make_result()  # integrated_lufs = -21.4
    louder_ref = _make_result(
        loudness=LoudnessMetrics(
            integrated_lufs=-15.0,
            short_term_max_lufs=-10.0,
            true_peak_dbtp=-1.0,
            loudness_range_lu=6.0,
        )
    )
    text = _flat(build_reference_comparison(target, louder_ref))
    assert "lower the reference by about 6.4 dB" in text


def test_reference_comparison_recommends_lowering_louder_target():
    from nonvisualaudio.localization import load
    load("en")
    # Target (-21.4 default) is louder than the reference (-30), so the
    # louder file to attenuate is the target, not the reference.
    target = _make_result()
    quieter_ref = _make_result(
        loudness=LoudnessMetrics(
            integrated_lufs=-30.0,
            short_term_max_lufs=-22.0,
            true_peak_dbtp=-6.0,
            loudness_range_lu=8.0,
        )
    )
    text = _flat(build_reference_comparison(target, quieter_ref))
    assert "lower the target by about 8.6 dB" in text
    assert "lower the reference" not in text


def test_reference_comparison_ab_match_addresses_project_when_target_is_project():
    from nonvisualaudio.localization import load
    load("en")
    target = _make_result()
    quieter_ref = _make_result(
        loudness=LoudnessMetrics(
            integrated_lufs=-30.0,
            short_term_max_lufs=-22.0,
            true_peak_dbtp=-6.0,
            loudness_range_lu=8.0,
        )
    )
    text = _flat(build_reference_comparison(target, quieter_ref, project=True))
    assert "lower the project by about 8.6 dB" in text


def test_reference_comparison_ab_match_says_matched_when_levels_equal():
    from nonvisualaudio.localization import load
    load("en")
    target = _make_result()
    same_ref = _make_result()  # identical default loudness
    text = _flat(build_reference_comparison(target, same_ref))
    assert "no level adjustment is needed for an A/B comparison" in text


def test_reference_comparison_ab_match_omitted_for_silent_reference():
    from nonvisualaudio.localization import load
    load("en")
    # A silent reference has no defined integrated loudness (-inf), so the
    # target-minus-reference difference is non-finite. No A/B level offset
    # must be printed rather than a meaningless "lower by unknown dB".
    target = _make_result()
    silent_ref = _make_result(
        loudness=LoudnessMetrics(
            integrated_lufs=float("-inf"),
            short_term_max_lufs=float("-inf"),
            true_peak_dbtp=-120.0,
            loudness_range_lu=0.0,
        )
    )
    text = _flat(build_reference_comparison(target, silent_ref))
    assert "A/B comparison" not in text
    assert "level adjustment" not in text


def test_genre_comparison_with_null_targets_skips_loudness_sentences():
    result = _make_result()
    profile = GENRES["speech_raw_recording"]
    text = _flat(build_genre_comparison(result, profile))
    # No LUFS or LRA judgement against a number that does not exist.
    assert "LUFS" not in text
    assert "loudness range" not in text.lower()
    # The neutral no-target statement takes their place.
    assert "Raw recordings have no fixed loudness target." in text
    # Heading and notes survive, so screen-reader navigation stays intact.
    assert "COMPARISON TO" in text
    assert "Typical tonal character" in text


def test_genre_comparison_with_numeric_targets_is_unchanged():
    result = _make_result()
    text = _flat(build_genre_comparison(result, GENRES["podcast_conversation"]))
    assert "LUFS" in text
    assert "Raw recordings have no fixed loudness target." not in text
