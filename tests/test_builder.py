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


def test_frequency_section_names_the_loudest_band_as_the_anchor():
    # Sub -14, bass -11, low_mid -9, mid -8, presence -10, air -18.
    # Loudest: mid (-8). Quietest: air (-18). Spread: 10 dB.
    report = build_report(_make_result())
    freq = report.split("FREQUENCY BALANCE")[1].split("\n\n")[0]
    # The anchor sentence must name the actual loudest band.
    assert "The loudest band in this file is the midrange" in freq
    # Other bands are expressed as "N dB quieter" without any "average" jargon.
    assert "dB quieter" in freq
    # The weakest band must be flagged.
    assert "the quietest band in this file" in freq
    # Abstract "average" wording must be gone.
    for banned in (
        "spectrum average",
        "band average",
        "present and balanced",
        "very subdued",
        "prominent",
    ):
        assert banned not in freq, f"{banned!r} still in the frequency section"


def test_overall_assessment_mentions_the_actual_loudest_band_when_skewed():
    skewed = _make_result(
        spectrum=SpectrumMetrics(
            bands=BandEnergies(
                sub_db=-4.0,   # strongest
                bass_db=-6.0,
                low_mid_db=-9.0,
                mid_db=-12.0,
                presence_db=-16.0,
                air_db=-24.0,  # weakest
            ),
            peaks=(),
        )
    )
    report = build_report(skewed)
    overall = report.split("OVERALL ASSESSMENT")[1].split("RECOMMENDATIONS")[0]
    assert "generally balanced" not in overall
    assert "sub bass" in overall
    assert "air" in overall


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
