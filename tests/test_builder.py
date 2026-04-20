from nonvisualaudio.analysis.result import (
    AnalysisResult,
    BandEnergies,
    DynamicsMetrics,
    FileInfo,
    LoudnessMetrics,
    SpectrumMetrics,
)
from nonvisualaudio.reporting.builder import build_report


def _make_result(**overrides) -> AnalysisResult:
    defaults = dict(
        file_info=FileInfo(
            filename="sample.wav",
            duration_seconds=754.0,
            sample_rate=48000,
            channels=2,
            bit_depth=24,
        ),
        loudness=LoudnessMetrics(
            integrated_lufs=-21.4,
            short_term_max_lufs=-14.2,
            true_peak_dbtp=-1.1,
            loudness_range_lu=8.7,
        ),
        dynamics=DynamicsMetrics(
            peak_db=-0.5,
            rms_db=-13.7,
            crest_factor_db=13.2,
            dr_score=11.0,
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
    )
    defaults.update(overrides)
    return AnalysisResult(**defaults)


def test_report_has_all_required_sections():
    report = build_report(_make_result())
    for section in (
        "FILE INFO",
        "LOUDNESS SUMMARY",
        "DYNAMICS SUMMARY",
        "FREQUENCY BALANCE",
        "OVERALL ASSESSMENT",
        "RECOMMENDATIONS",
    ):
        assert section in report, f"missing section: {section}"


def test_report_contains_no_markdown_symbols():
    report = build_report(_make_result())
    for bad in ("*", "#", "`", "_"):
        assert bad not in report, f"markdown symbol {bad!r} leaked into report"


def test_report_spells_negatives_as_minus():
    report = build_report(_make_result())
    assert "minus 21.4" in report
    assert "-21.4" not in report


def test_very_loud_file_is_flagged():
    loud = LoudnessMetrics(
        integrated_lufs=-6.0,
        short_term_max_lufs=-4.0,
        true_peak_dbtp=-0.2,
        loudness_range_lu=3.0,
    )
    report = build_report(_make_result(loudness=loud))
    assert "very loud" in report.lower() or "heavily limited" in report.lower()
    assert "intersample" in report.lower()


def test_recommendations_fallback_when_nothing_to_fix():
    balanced = _make_result(
        loudness=LoudnessMetrics(
            integrated_lufs=-16.0,
            short_term_max_lufs=-12.0,
            true_peak_dbtp=-2.0,
            loudness_range_lu=8.0,
        ),
        spectrum=SpectrumMetrics(
            bands=BandEnergies(
                sub_db=-14.0,
                bass_db=-11.0,
                low_mid_db=-10.0,
                mid_db=-10.0,
                presence_db=-12.0,
                air_db=-14.0,
            ),
            peaks=(),
        ),
    )
    report = build_report(balanced)
    assert "RECOMMENDATIONS" in report
    assert "No specific corrective actions" in report
