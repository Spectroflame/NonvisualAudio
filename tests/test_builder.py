from nonvisualaudio.analysis.result import (
    AnalysisResult,
    BandEnergies,
    DynamicsMetrics,
    FileInfo,
    LoudnessMetrics,
    SpectrumMetrics,
    StereoMetrics,
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


def test_true_peak_timestamp_appears_when_present():
    loud = LoudnessMetrics(
        integrated_lufs=-21.4,
        short_term_max_lufs=-14.2,
        true_peak_dbtp=-1.1,
        loudness_range_lu=8.7,
        true_peak_time_seconds=125.0,
    )
    report = build_report(_make_result(loudness=loud))
    assert "The highest peak occurs at" in report


def test_true_peak_timestamp_omitted_when_unknown():
    # The default fixture leaves true_peak_time_seconds at None.
    report = build_report(_make_result())
    assert "highest peak occurs" not in report


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


def test_dynamics_verdict_flags_compressed_lively_mismatch():
    # The case the user actually hits: a heavily limited master
    # (LRA 2 LU) with snare transients pushing the crest factor over
    # 14 dB. The legacy verdict was "wide dynamic range" — a lie. Now
    # the report has to acknowledge both the flat macro level and the
    # intact transients.
    loud = LoudnessMetrics(
        integrated_lufs=-10.0,
        short_term_max_lufs=-7.0,
        true_peak_dbtp=-1.0,
        loudness_range_lu=2.0,
    )
    dyn = DynamicsMetrics(
        peak_db=-0.5,
        rms_db=-15.5,
        crest_factor_db=15.0,
        dr_score=12.0,
    )
    report = build_report(_make_result(loudness=loud, dynamics=dyn))
    dyn_block = report.split("DYNAMICS SUMMARY")[1].split("\n\n")[0]
    assert "wide" not in dyn_block.lower()
    assert "lively" in dyn_block.lower() or "transient" in dyn_block.lower()


def test_dynamics_verdict_requires_both_metrics_for_wide_verdict():
    # Wide LRA *and* high crest → "wide" is fair game.
    loud = LoudnessMetrics(
        integrated_lufs=-18.0,
        short_term_max_lufs=-12.0,
        true_peak_dbtp=-2.0,
        loudness_range_lu=14.0,
    )
    dyn = DynamicsMetrics(
        peak_db=-0.5,
        rms_db=-15.5,
        crest_factor_db=15.0,
        dr_score=14.0,
    )
    report = build_report(_make_result(loudness=loud, dynamics=dyn))
    dyn_block = report.split("DYNAMICS SUMMARY")[1].split("\n\n")[0]
    assert "wide" in dyn_block.lower()


def test_lra_verbal_verdict_appears_in_loudness_block():
    # LRA 2 LU triggers the "very narrow" verbal verdict so the reader
    # gets context, not just a number.
    loud = LoudnessMetrics(
        integrated_lufs=-10.0,
        short_term_max_lufs=-7.0,
        true_peak_dbtp=-1.0,
        loudness_range_lu=2.0,
    )
    report = build_report(_make_result(loudness=loud))
    loud_block = report.split("LOUDNESS SUMMARY")[1].split("\n\n")[0]
    assert "very narrow" in loud_block.lower()


def test_overall_no_longer_calls_compressed_file_dynamic():
    # Same trap as the dynamics verdict: with crest 15 + LRA 2, the
    # overall sentence used to say "dynamic recording" — now it must
    # back off because LRA disagrees.
    loud = LoudnessMetrics(
        integrated_lufs=-9.0,
        short_term_max_lufs=-6.0,
        true_peak_dbtp=-0.5,
        loudness_range_lu=2.0,
    )
    dyn = DynamicsMetrics(
        peak_db=-0.5,
        rms_db=-15.5,
        crest_factor_db=15.0,
        dr_score=12.0,
    )
    report = build_report(_make_result(loudness=loud, dynamics=dyn))
    overall = report.split("OVERALL ASSESSMENT")[1].split("\n\n")[0]
    assert "dynamic recording" not in overall.lower()


def test_crest_factor_line_includes_explanation():
    report = build_report(_make_result())
    dyn_block = report.split("DYNAMICS SUMMARY")[1].split("\n\n")[0]
    # The value is still there, plus a one-liner explaining what it is.
    assert "13.2" in dyn_block
    assert "peak" in dyn_block.lower() and "average" in dyn_block.lower()


def test_stereo_section_appears_with_heading():
    report = build_report(_make_result())
    assert "STEREO IMAGE" in report


def test_mono_file_marks_stereo_section_as_not_applicable():
    mono = _make_result(
        file_info=FileInfo(
            filename="solo.wav",
            duration_seconds=120.0,
            sample_rate=48000,
            channels=1,
            bit_depth=24,
        )
    )
    report = build_report(mono)
    stereo_block = report.split("STEREO IMAGE")[1].split("\n\n")[0]
    assert "mono" in stereo_block.lower()
    # No verdicts on correlation / mono drop / width when the file is mono.
    assert "correlation" not in stereo_block.lower()


def test_stereo_section_reports_correlation_value_and_natural_verdict():
    stereo = StereoMetrics(
        is_stereo=True,
        mean_correlation=0.7,
        min_correlation=0.65,
        mono_drop_db=-0.2,
        side_to_mid_db=-9.0,
    )
    report = build_report(_make_result(stereo=stereo))
    stereo_block = report.split("STEREO IMAGE")[1].split("\n\n")[0]
    assert "0.70" in stereo_block
    assert "natural" in stereo_block.lower()
    assert "excellent" in stereo_block.lower()  # mono compatibility verdict


def test_stereo_section_flags_out_of_phase_signal():
    stereo = StereoMetrics(
        is_stereo=True,
        mean_correlation=-0.8,
        min_correlation=-0.95,
        mono_drop_db=-25.0,
        side_to_mid_db=10.0,
    )
    report = build_report(_make_result(stereo=stereo))
    stereo_block = report.split("STEREO IMAGE")[1].split("\n\n")[0]
    assert "out of phase" in stereo_block.lower() or "pushing against" in stereo_block.lower()
    assert "problematic" in stereo_block.lower()


def test_stereo_section_surfaces_worst_block_when_it_diverges():
    stereo = StereoMetrics(
        is_stereo=True,
        mean_correlation=0.7,
        min_correlation=-0.4,  # one bad block hiding behind a good average
        mono_drop_db=-0.5,
        side_to_mid_db=-9.0,
    )
    report = build_report(_make_result(stereo=stereo))
    stereo_block = report.split("STEREO IMAGE")[1].split("\n\n")[0]
    assert "worst block" in stereo_block.lower()


def test_stereo_low_correlation_triggers_recommendation():
    stereo = StereoMetrics(
        is_stereo=True,
        mean_correlation=0.1,
        min_correlation=-0.2,
        mono_drop_db=-1.0,
        side_to_mid_db=-2.0,
    )
    report = build_report(_make_result(stereo=stereo))
    recs_block = report.split("RECOMMENDATIONS")[1]
    assert "goniometer" in recs_block.lower() or "mono check" in recs_block.lower()


def test_stereo_mono_drop_triggers_recommendation():
    stereo = StereoMetrics(
        is_stereo=True,
        mean_correlation=0.5,
        min_correlation=0.3,
        mono_drop_db=-6.0,
        side_to_mid_db=-2.0,
    )
    report = build_report(_make_result(stereo=stereo))
    recs_block = report.split("RECOMMENDATIONS")[1]
    assert "mono playback" in recs_block.lower() or "mono-summier" in recs_block.lower()


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
