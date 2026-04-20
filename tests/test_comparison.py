from tests.test_builder import _make_result
from nonvisualaudio.analysis.result import LoudnessMetrics
from nonvisualaudio.reporting.comparison import (
    build_genre_comparison,
    build_reference_comparison,
)
from nonvisualaudio.reporting.genre_profiles import GENRES


def test_genre_comparison_flags_loud_file_vs_classical():
    loud = _make_result(
        loudness=LoudnessMetrics(
            integrated_lufs=-6.0,
            short_term_max_lufs=-3.0,
            true_peak_dbtp=-0.5,
            loudness_range_lu=4.0,
        )
    )
    text = build_genre_comparison(loud, GENRES["classical_orchestral"])
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
    text = build_reference_comparison(target, quieter)
    assert "COMPARISON TO REFERENCE FILE" in text
    assert "louder" in text.lower()
