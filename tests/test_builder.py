from nonvisualaudio.analysis.result import (
    AnalysisResult,
    BandEnergies,
    DynamicsMetrics,
    FileInfo,
    LoudnessMetrics,
    SpectrumMetrics,
    StereoMetrics,
)
from nonvisualaudio.reporting.builder import build_report as _build_report_doc


def build_report(*args, **kwargs) -> str:
    """Test helper: return the rendered text instead of the doc.

    These tests assert against substrings in the plain-text rendering,
    so the helper hides the structured pipeline from each test body.
    Failures of the structured layout are covered by ``test_export.py``.
    """
    return _build_report_doc(*args, **kwargs).to_text()


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
        "File Info",
        "Loudness Summary",
        "Dynamics Summary",
        "Frequency Balance",
        "Overall Assessment",
    ):
        assert section in report, f"missing section: {section}"
    # The recommendations section is now conditional: the default fixture
    # has no peaks, normal loudness, and a balanced spectrum, so nothing
    # actionable comes up. The Possible Action Options heading is then
    # suppressed and only the "all good" sentence remains in the flow.
    assert "Possible Action Options" not in report
    assert "No specific corrective actions" in report


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
    freq = report.split("Frequency Balance")[1].split("\n\n")[0]
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


def test_silent_bands_collapse_into_a_single_line():
    # A 1 kHz sine tone parks all other bands far below the noise floor.
    # The "X dB quieter" sentences would just be noise, so the silent
    # bands are summarised in one screen-reader-friendly line at the end
    # of the band listing.
    sine = _make_result(
        spectrum=SpectrumMetrics(
            bands=BandEnergies(
                sub_db=-100.0,
                bass_db=-95.0,
                low_mid_db=-12.0,
                mid_db=-0.2,
                presence_db=-100.0,
                air_db=-100.0,
            ),
            peaks=(),
        )
    )
    report = build_report(sine)
    freq = report.split("Frequency Balance")[1].split("\n\n")[0]
    # Sub bass, bass, presence, air are all silent.
    assert "Effectively silent (below -90 dB)" in freq
    assert "sub bass" in freq
    assert "bass" in freq
    assert "presence" in freq
    assert "air" in freq
    # The "120 dB quieter" wall must not be there.
    assert "100 dB quieter" not in freq
    assert "120 dB quieter" not in freq
    # Audible non-loudest band (low_mid at -12 vs mid at -0.2) is still
    # listed normally as the quietest audible band.
    assert "dB quieter" in freq


def test_sine_tone_emits_single_band_dominant_sentence():
    # When only the loudest band is audible, the "spread between
    # loudest and quietest" sentence makes no sense — replace it with
    # an explicit "the whole signal sits in X" line.
    sine = _make_result(
        spectrum=SpectrumMetrics(
            bands=BandEnergies(
                sub_db=-110.0,
                bass_db=-110.0,
                low_mid_db=-110.0,
                mid_db=-0.1,
                presence_db=-110.0,
                air_db=-110.0,
            ),
            peaks=(),
        )
    )
    report = build_report(sine)
    freq = report.split("Frequency Balance")[1].split("\n\n")[0]
    assert "The whole signal sits in midrange" in freq
    # No spread sentence in this degenerate case.
    assert "Total spread between" not in freq


def test_silent_band_threshold_excludes_just_above_floor():
    # A band at -89 dB is still audible (above the threshold), so it
    # must keep its "X dB quieter" sentence rather than disappearing
    # into the silent group.
    near_floor = _make_result(
        spectrum=SpectrumMetrics(
            bands=BandEnergies(
                sub_db=-14.0,
                bass_db=-11.0,
                low_mid_db=-9.0,
                mid_db=-8.0,
                presence_db=-10.0,
                air_db=-89.0,  # just above the silence threshold
            ),
            peaks=(),
        )
    )
    report = build_report(near_floor)
    freq = report.split("Frequency Balance")[1].split("\n\n")[0]
    # The -89 dB band gets a normal quieter sentence, not the silent line.
    assert "Effectively silent" not in freq
    assert "air" in freq


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
    overall = report.split("Overall Assessment")[1].split("Possible Action Options")[0]
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
    dyn_block = report.split("Dynamics Summary")[1].split("\n\n")[0]
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
    dyn_block = report.split("Dynamics Summary")[1].split("\n\n")[0]
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
    loud_block = report.split("Loudness Summary")[1].split("\n\n")[0]
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
    overall = report.split("Overall Assessment")[1].split("\n\n")[0]
    assert "dynamic recording" not in overall.lower()


def test_crest_factor_line_includes_explanation():
    report = build_report(_make_result())
    dyn_block = report.split("Dynamics Summary")[1].split("\n\n")[0]
    # The value is still there, plus a one-liner explaining what it is.
    assert "13.2" in dyn_block
    assert "peak" in dyn_block.lower() and "average" in dyn_block.lower()


def test_stereo_section_appears_with_heading():
    report = build_report(_make_result())
    assert "Stereo Image" in report


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
    stereo_block = report.split("Stereo Image")[1].split("\n\n")[0]
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
    stereo_block = report.split("Stereo Image")[1].split("\n\n")[0]
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
    stereo_block = report.split("Stereo Image")[1].split("\n\n")[0]
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
    stereo_block = report.split("Stereo Image")[1].split("\n\n")[0]
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
    recs_block = report.split("Possible Action Options")[1]
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
    recs_block = report.split("Possible Action Options")[1]
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
    # When the analysis turns up nothing to act on, the section header is
    # suppressed entirely — only the "all good" sentence remains in the
    # flow. Reading a "Possible Action Options" heading followed by
    # "nothing to do" was the noise the user explicitly asked us to drop.
    assert "Possible Action Options" not in report
    assert "No specific corrective actions" in report
