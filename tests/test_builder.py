from nonvisualaudio.analysis.result import (
    AnalysisResult,
    BandEnergies,
    DynamicsMetrics,
    FileInfo,
    LoudnessMetrics,
    SpectralPeak,
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
    assert "Effectively silent (below minus 70 dB)" in freq
    assert "sub-bass" in freq
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
    assert "The whole signal sits in the midrange" in freq
    # No spread sentence in this degenerate case.
    assert "Total spread between" not in freq


def test_silent_band_threshold_excludes_just_above_floor():
    # A band at -69 dB is still audible (above the threshold), so it
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
                air_db=-69.0,  # just above the silence threshold
            ),
            peaks=(),
        )
    )
    report = build_report(near_floor)
    freq = report.split("Frequency Balance")[1].split("\n\n")[0]
    # The -69 dB band gets a normal quieter sentence, not the silent line.
    assert "Effectively silent" not in freq
    assert "air" in freq


def test_silent_band_threshold_catches_dither_floor():
    # Regression for the threshold raise from -90 to -70: a real 16-bit
    # dithered export parks unused bands around -90 relative energy,
    # which slipped just past the old threshold and produced an absurd
    # "89.6 dB quieter" sentence. Anything in the digital-floor window
    # must now collapse into the silent line instead.
    dither_floor = _make_result(
        spectrum=SpectrumMetrics(
            bands=BandEnergies(
                sub_db=-14.0,
                bass_db=-11.0,
                low_mid_db=-9.0,
                mid_db=-8.0,
                presence_db=-10.0,
                air_db=-75.0,  # below the new threshold, above the old one
            ),
            peaks=(),
        )
    )
    report = build_report(dither_floor)
    freq = report.split("Frequency Balance")[1].split("\n\n")[0]
    assert "Effectively silent (below minus 70 dB)" in freq
    # The spread sentence must use the quietest AUDIBLE band, not the
    # silent one — presence at -10 vs mid at -8, not air at -75.
    assert "Total spread between" in freq
    assert "67.0 dB" not in freq


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
    assert "sub-bass" in overall
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
        min_correlation_time_seconds=73.0,
    )
    report = build_report(_make_result(stereo=stereo))
    stereo_block = report.split("Stereo Image")[1].split("\n\n")[0]
    assert "worst block" in stereo_block.lower()
    # The position is surfaced in the same "occurs at" wording as the
    # true-peak line (1 minute 13 seconds = 73 s).
    assert "occurs at" in stereo_block.lower()
    assert "13 second" in stereo_block.lower()
    # Healthy mean + small mono drop → the hint reads as a check, not a
    # phase warning.
    assert "spot to check" in stereo_block.lower()


def test_stereo_worst_block_warns_when_overall_picture_is_poor():
    """A diverging worst block on top of a weak mean and a real mono drop
    gets the substantive hint, not the calm check wording."""
    stereo = StereoMetrics(
        is_stereo=True,
        mean_correlation=0.2,
        min_correlation=-0.6,
        mono_drop_db=-4.0,
        side_to_mid_db=-2.0,
        min_correlation_time_seconds=12.0,
    )
    report = build_report(_make_result(stereo=stereo))
    stereo_block = report.split("Stereo Image")[1].split("\n\n")[0]
    assert "worst block" in stereo_block.lower()
    assert "mono check" in stereo_block.lower()
    assert "spot to check" not in stereo_block.lower()


def test_stereo_worst_block_drops_timestamp_in_project_mode():
    """In project mode the block position maps to the concatenated
    timeline, so the value is shown without a misleading timestamp."""
    stereo = StereoMetrics(
        is_stereo=True,
        mean_correlation=0.7,
        min_correlation=-0.4,
        mono_drop_db=-0.5,
        side_to_mid_db=-9.0,
        min_correlation_time_seconds=73.0,
    )
    report = build_report(_make_result(stereo=stereo), project=True)
    stereo_block = report.split("Stereo Image")[1].split("\n\n")[0]
    assert "worst block" in stereo_block.lower()
    assert "occurs at" not in stereo_block.lower()


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


# --------------------------------------------------------------------------- #
# Material context: neutral mode (no profile selected)
# --------------------------------------------------------------------------- #

def _speech_like_bands(**overrides) -> BandEnergies:
    """Band fixture modeled on the real-world speech take that motivated
    the 2.2 rework: midrange loudest, low mids close behind, presence
    recessed, sub bass nearly absent."""
    defaults = dict(
        sub_db=-45.9,
        bass_db=-12.0,
        low_mid_db=-4.9,
        mid_db=0.0,
        presence_db=-11.3,
        air_db=-8.2,
        bass_low_db=-13.0,
        bass_high_db=-11.0,
        air_low_db=-9.0,
        air_high_db=-20.0,
    )
    defaults.update(overrides)
    return BandEnergies(**defaults)


def test_neutral_mode_keeps_factual_lines():
    result = _make_result(
        spectrum=SpectrumMetrics(bands=_speech_like_bands(), peaks=())
    )
    report = build_report(result, material="neutral")
    assert "The loudest band in this file is the midrange" in report
    assert "dB quieter" in report
    assert "Total spread between loudest" in report


def test_neutral_mode_has_no_dramatic_verdict():
    result = _make_result(
        spectrum=SpectrumMetrics(bands=_speech_like_bands(), peaks=())
    )
    report = build_report(result, material="neutral")
    # No "clearly X-heavy ... louder than the sub-bass" judgement.
    assert "-heavy tonal balance" not in report
    assert "louder than the sub-bass" not in report
    # The cautious phrasing takes its place.
    assert "with the energy concentrated in the midrange" in report


def test_neutral_mode_adds_cautious_notes_with_material_disclaimer():
    result = _make_result(
        spectrum=SpectrumMetrics(bands=_speech_like_bands(), peaks=())
    )
    report = build_report(result, material="neutral")
    assert "The sub-bass level is very low." in report
    assert "Depending on the material, this can be intentional." in report


def test_neutral_mode_suppresses_music_band_recommendations():
    result = _make_result(
        spectrum=SpectrumMetrics(bands=_speech_like_bands(), peaks=())
    )
    report = build_report(result, material="neutral")
    assert "The very low end is almost absent" not in report
    assert "shelf around 2 dB above 8 kHz" not in report
    assert "If this is music" not in report


def test_neutral_mode_gives_no_profile_hint():
    result = _make_result(
        spectrum=SpectrumMetrics(bands=_speech_like_bands(), peaks=())
    )
    report = build_report(result, material="neutral")
    for phrase in ("profile", "Profile", "genre", "Genre"):
        assert phrase not in report


def test_neutral_mode_keeps_robust_recommendations():
    loud = LoudnessMetrics(
        integrated_lufs=-21.4,
        short_term_max_lufs=-14.2,
        true_peak_dbtp=-0.3,
        loudness_range_lu=8.7,
    )
    result = _make_result(
        loudness=loud,
        spectrum=SpectrumMetrics(
            bands=_speech_like_bands(),
            peaks=(SpectralPeak(frequency_hz=1100.0, prominence_db=6.0),),
        ),
    )
    report = build_report(result, material="neutral")
    # True-peak ceiling and narrow-resonance EQ tips are robust findings
    # and must survive in neutral mode.
    assert "minus 1 dBTP" in report
    assert "1.1 kHz" in report


def test_neutral_mode_flat_spectrum_emits_no_interpretation():
    flat = BandEnergies(
        sub_db=-10.0,
        bass_db=-10.5,
        low_mid_db=-9.5,
        mid_db=-10.0,
        presence_db=-10.2,
        air_db=-9.8,
    )
    result = _make_result(spectrum=SpectrumMetrics(bands=flat, peaks=()))
    report = build_report(result, material="neutral")
    assert "Depending on the material" not in report
    assert "flat, even tonal balance" in report


def test_music_mode_default_is_unchanged_by_material_param():
    result = _make_result(
        spectrum=SpectrumMetrics(bands=_speech_like_bands(), peaks=())
    )
    assert build_report(result) == build_report(result, material="music")


def test_neutral_loudness_drops_genre_references():
    # Integrated minus 16 LUFS lands in the "moderate" bucket and LRA 8.7
    # in the "typical" bucket — both historically cite broadcast levels
    # and music mixes, which are guesses when no profile is selected.
    loud = LoudnessMetrics(
        integrated_lufs=-16.0,
        short_term_max_lufs=-12.0,
        true_peak_dbtp=-1.5,
        loudness_range_lu=8.7,
    )
    report = build_report(_make_result(loudness=loud), material="neutral")
    for banned in ("music mix", "broadcast", "streaming", "podcast", "audiobook"):
        assert banned not in report.lower(), (
            f"{banned!r} leaked into the neutral report"
        )
    assert "That is a moderate loudness range." in report
    assert "The file sits at a moderate loudness level." in report


def test_neutral_loudness_narrow_lra_and_loud_level_stay_neutral():
    # LRA 4 → "narrow" bucket (music wording cites streaming/broadcast
    # masters); integrated minus 11 → "loud" bucket (music wording cites
    # modern streaming masters). Crest 13.2 with LRA 4 also lands in the
    # moderate dynamics bucket, whose music wording cites pop/broadcast
    # mastering — all three must come out neutral without a profile.
    loud = LoudnessMetrics(
        integrated_lufs=-11.0,
        short_term_max_lufs=-8.0,
        true_peak_dbtp=-1.5,
        loudness_range_lu=4.0,
    )
    report = build_report(_make_result(loudness=loud), material="neutral")
    assert "streaming and broadcast masters" not in report
    assert "pop or broadcast mastering" not in report
    assert "the perceived loudness varies only a little over time" in report
    assert "The file reads as loud, leaving little headroom." in report
    assert "Dynamics sit in the moderate range." in report


def test_neutral_stereo_width_verdict_names_no_genre():
    stereo = StereoMetrics(
        is_stereo=True,
        mean_correlation=0.7,
        min_correlation=0.68,
        mono_drop_db=-0.2,
        side_to_mid_db=-6.0,
    )
    report = build_report(_make_result(stereo=stereo), material="neutral")
    assert "stereo music and broadcast material" not in report
    assert "neither notably narrow nor notably wide" in report


def test_music_material_keeps_genre_wording():
    # With a music profile the historic reference points must survive:
    # LRA 8.7 → "music mixes"; minus 16 LUFS → streaming normalization range.
    loud = LoudnessMetrics(
        integrated_lufs=-16.0,
        short_term_max_lufs=-12.0,
        true_peak_dbtp=-1.5,
        loudness_range_lu=8.7,
    )
    report = build_report(_make_result(loudness=loud), material="music")
    assert "That sits in the typical range for music mixes." in report
    assert "streaming platforms aim for with loudness normalization" in report


def test_spoken_word_profile_uses_speech_wording_not_music():
    # Mastered spoken-word (audio drama, audiobook, podcast) carries
    # material="music" but tonality="speech". The genre-referencing
    # verdicts must swap music references for spoken-word ones — the
    # production is finished, so it is NOT routed to the neutral siblings.
    loud = LoudnessMetrics(
        integrated_lufs=-11.0,
        short_term_max_lufs=-8.0,
        true_peak_dbtp=-1.5,
        loudness_range_lu=4.0,
    )
    stereo = StereoMetrics(
        is_stereo=True,
        mean_correlation=0.7,
        min_correlation=0.68,
        mono_drop_db=-0.2,
        side_to_mid_db=-6.0,
    )
    report = build_report(
        _make_result(loudness=loud, stereo=stereo),
        material="music",
        tonality="speech",
    )
    # No music reference points.
    for banned in (
        "streaming masters",
        "streaming and broadcast masters",
        "pop or broadcast mastering",
        "music mix",
        "stereo music and broadcast material",
    ):
        assert banned not in report.lower(), f"{banned!r} leaked into speech report"
    # Speech reference points instead.
    assert "spoken-word production" in report
    assert "radio drama and broadcast material" in report
    # And it is NOT the bare neutral fallback.
    assert "neither notably narrow nor notably wide" not in report


def test_raw_speech_stays_neutral_not_finished_production():
    # A raw recording (material="speech") must avoid both music AND
    # "finished production" claims — it is judged as source material, so
    # it routes to the neutral siblings, not the .speech ones.
    from nonvisualaudio import localization

    localization.load("de")
    try:
        loud = LoudnessMetrics(
            integrated_lufs=-11.0,
            short_term_max_lufs=-8.0,
            true_peak_dbtp=-1.5,
            loudness_range_lu=4.0,
        )
        report = build_report(
            _make_result(loudness=loud), material="speech", tonality="speech"
        )
        for banned in ("Master", "Mix", "Sendefassung", "Streaming-Fassung"):
            assert banned not in report, f"{banned!r} leaked into raw-speech report"
        assert "Die Datei wirkt laut und lässt wenig Headroom." in report
    finally:
        localization.load("en")


def test_neutral_mode_german_report_has_no_musik_mix():
    # The reported bug: a no-profile run printed "im üblichen Bereich für
    # Musik-Mixe" for a radio-drama project. The German catalogue must
    # render the neutral siblings instead — and keep the music wording
    # when a music profile is selected.
    from nonvisualaudio import localization

    localization.load("de")
    try:
        loud = LoudnessMetrics(
            integrated_lufs=-16.0,
            short_term_max_lufs=-12.0,
            true_peak_dbtp=-1.5,
            loudness_range_lu=8.7,
        )
        result = _make_result(loudness=loud)
        neutral = build_report(result, material="neutral")
        assert "Musik-Mix" not in neutral
        assert "Rundfunk" not in neutral
        assert "Das ist ein moderater Lautheitsbereich." in neutral
        assert "Die Datei liegt auf einem moderaten Lautheitsniveau." in neutral
        music = build_report(result, material="music")
        assert "im üblichen Bereich für Musik-Mixe" in music
    finally:
        localization.load("en")


# --------------------------------------------------------------------------- #
# Material context: speech mode
# --------------------------------------------------------------------------- #

def test_speech_mode_announces_speech_reading():
    result = _make_result(
        spectrum=SpectrumMetrics(bands=_speech_like_bands(), peaks=())
    )
    report = build_report(result, material="speech")
    assert "Reading this as a speech recording" in report
    # Deliberately cautious wording: the midrange concentration is
    # described, not judged ("carries the body" was too assertive).
    assert "the energy concentrates in the midrange" in report
    assert "This shapes the character of the voice." in report


def test_speech_mode_does_not_flag_low_sub_bass():
    result = _make_result(
        spectrum=SpectrumMetrics(bands=_speech_like_bands(), peaks=())
    )
    report = build_report(result, material="speech")
    # Low sub bass is normal for speech: no music recommendation, no
    # "very low" finding, no dramatic verdict naming the sub bass.
    assert "The very low end is almost absent" not in report
    assert "The sub-bass level is very low." not in report
    assert "louder than the sub-bass" not in report


def test_speech_mode_flags_strong_sub_bass_as_possible_rumble():
    bands = _speech_like_bands(sub_db=-5.0)
    result = _make_result(spectrum=SpectrumMetrics(bands=bands, peaks=()))
    report = build_report(result, material="speech")
    assert "rumble, handling noise or plosives" in report
    assert "high pass filter around 80 to 100 Hz" in report


def test_speech_mode_flags_boxiness_when_low_mids_dominate():
    # low_mid only 1 dB under mid → inside the "near mid" boxiness window.
    bands = _speech_like_bands(low_mid_db=-1.0)
    result = _make_result(spectrum=SpectrumMetrics(bands=bands, peaks=()))
    report = build_report(result, material="speech")
    assert "can sound muddy or boxy" in report
    assert "250 to 500 Hz region is one possible starting point" in report


def test_speech_mode_flags_sibilance_region():
    bands = _speech_like_bands(air_low_db=-4.0)
    result = _make_result(spectrum=SpectrumMetrics(bands=bands, peaks=()))
    report = build_report(result, material="speech")
    assert "can point to sharp S sounds" in report
    assert "de-esser" in report


def test_speech_mode_mentions_reduced_openness_cautiously():
    bands = _speech_like_bands(air_high_db=-40.0)
    result = _make_result(spectrum=SpectrumMetrics(bands=bands, peaks=()))
    report = build_report(result, material="speech")
    assert "10 to 20 kHz region is very restrained" in report
    assert "judge with caution" in report


def test_speech_mode_merges_recessed_presence_with_narrow_peak():
    # Presence recessed overall (-11.3 dB below mid) AND a narrow peak
    # at 2.6 kHz inside it: the report must explain both in one combined
    # sentence instead of two contradicting ones.
    result = _make_result(
        spectrum=SpectrumMetrics(
            bands=_speech_like_bands(),
            peaks=(SpectralPeak(frequency_hz=2600.0, prominence_db=5.5),),
        )
    )
    report = build_report(result, material="speech")
    assert (
        "The presence region is restrained overall, but contains one "
        "narrow standout at 2.6 kHz." in report
    )
    # The standalone "restrained presence" sentence must not also appear.
    assert "presence region (2 to 6 kHz) is restrained overall" not in report


def test_speech_mode_tolerates_missing_subband_values():
    # Legacy fixtures carry no sub-band measurements. Speech mode must
    # not crash and must simply skip the sub-band findings.
    bands = _speech_like_bands(
        bass_low_db=None, bass_high_db=None, air_low_db=None, air_high_db=None
    )
    result = _make_result(spectrum=SpectrumMetrics(bands=bands, peaks=()))
    report = build_report(result, material="speech")
    assert "Reading this as a speech recording" in report
    assert "80 to 150 Hz" not in report
    assert "6 to 10 kHz" not in report


def test_speech_mode_suppresses_music_band_recommendations():
    bands = _speech_like_bands(sub_db=-2.0)  # would trigger sub_dominant in music
    result = _make_result(spectrum=SpectrumMetrics(bands=bands, peaks=()))
    report = build_report(result, material="speech")
    assert "The sub-bass is dominant" not in report
    assert "shelf around 2 dB above 8 kHz" not in report


# --------------------------------------------------------------------------- #
# Grammatically correct band names (DE: no "im untere Mitten-Bereich")
# --------------------------------------------------------------------------- #

def _low_mid_heavy_bands() -> BandEnergies:
    """Low mids loudest by a wide margin so the concentration sentence
    and the tonal-balance phrase both have to inflect a plural German
    band name ("die Tiefmitten")."""
    return BandEnergies(
        sub_db=-40.0,
        bass_db=-18.0,
        low_mid_db=-2.0,
        mid_db=-6.0,
        presence_db=-16.0,
        air_db=-20.0,
    )


def test_german_neutral_report_inflects_band_names():
    from nonvisualaudio import localization

    localization.load("de")
    try:
        result = _make_result(
            spectrum=SpectrumMetrics(bands=_low_mid_heavy_bands(), peaks=())
        )
        report = build_report(result, material="neutral")
        # The reported wart: a glued "im untere Mitten-Bereich".
        assert "untere Mitten" not in report
        assert "Mitten-Bereich" not in report
        # Natural phrasing with the correct dative plural instead.
        assert "Die Energie konzentriert sich stark in den Tiefmitten." in report
        # Plural band names take a plural verb in the anchor sentence,
        # and the frequency range survives untouched.
        assert (
            "Das lauteste Band der Datei sind die Tiefmitten (250 bis 500 Hz)."
            in report
        )
        assert "Die Mitten (500 Hz bis 2 kHz) sind um" in report
    finally:
        localization.load("en")


def test_german_speech_report_has_no_glued_band_compound():
    from nonvisualaudio import localization

    localization.load("de")
    try:
        result = _make_result(
            spectrum=SpectrumMetrics(bands=_low_mid_heavy_bands(), peaks=())
        )
        report = build_report(result, material="speech")
        assert "untere Mitten" not in report
        assert "Mitten-Bereich" not in report
        assert "Die Tiefmitten (250 bis 500 Hz)" in report
    finally:
        localization.load("en")


def test_english_report_avoids_awkward_region_phrases():
    result = _make_result(
        spectrum=SpectrumMetrics(bands=_low_mid_heavy_bands(), peaks=())
    )
    report = build_report(result, material="neutral")
    assert "low midrange" not in report
    assert "midrange region" not in report
    assert "range range" not in report
    # Plural verb agreement plus the untouched frequency range.
    assert "The loudest band in this file is the low mids (250 to 500 Hz)." in report
    assert "The midrange (500 Hz to 2 kHz) is" in report
    assert "The energy is strongly concentrated in the low mids." in report


def test_neutral_and_speech_reports_contain_no_markdown_symbols():
    result = _make_result(
        spectrum=SpectrumMetrics(
            bands=_speech_like_bands(),
            peaks=(SpectralPeak(frequency_hz=2600.0, prominence_db=5.5),),
        )
    )
    for material in ("neutral", "speech"):
        report = build_report(result, material=material)
        for bad in ("*", "#", "`", "_"):
            assert bad not in report, (
                f"markdown symbol {bad!r} leaked into {material} report"
            )


# --------------------------------------------------------------------------- #
# Spoken-word tonality (audio drama / audiobook / podcast profiles)
# --------------------------------------------------------------------------- #

def _audio_drama_bands(**overrides) -> BandEnergies:
    """Band fixture modeled on the modern audio-drama mix that motivated
    the spoken-tonality wording: mids loudest, bass and low mids close
    behind, presence recessed, air 12.5 dB below the mids — a perfectly
    normal shape for dialogue with music and atmosphere underneath."""
    defaults = dict(
        sub_db=-26.0,
        bass_db=-10.5,
        low_mid_db=-9.0,
        mid_db=-8.0,
        presence_db=-14.0,
        air_db=-20.5,
    )
    defaults.update(overrides)
    return BandEnergies(**defaults)


def test_spoken_tonality_speech_shape_is_not_called_mid_heavy():
    result = _make_result(
        spectrum=SpectrumMetrics(bands=_audio_drama_bands(), peaks=())
    )
    report = build_report(result, tonality="speech")
    overall = report.split("Overall Assessment")[1].split("\n\n")[0]
    # The historic music wording must not fire for the normal speech shape.
    assert "clearly" not in overall
    assert "-heavy tonal balance" not in overall
    assert "louder than" not in overall
    # The calm spoken-word reading takes its place.
    assert "warm, speech-centered tonal balance" in overall
    assert "presence and upper treble are more restrained than the mids" in overall


def test_spoken_tonality_same_bands_stay_mid_heavy_without_the_flag():
    # Contrast case: the identical measurements with a music profile keep
    # the historic clear verdict — the flag changes wording, not buckets.
    result = _make_result(
        spectrum=SpectrumMetrics(bands=_audio_drama_bands(), peaks=())
    )
    report = build_report(result)
    overall = report.split("Overall Assessment")[1].split("\n\n")[0]
    assert "clearly" in overall
    assert "louder than" in overall


def test_spoken_tonality_keeps_the_measurement_lines_untouched():
    result = _make_result(
        spectrum=SpectrumMetrics(bands=_audio_drama_bands(), peaks=())
    )
    default_report = build_report(result)
    spoken_report = build_report(result, tonality="speech")
    freq_default = default_report.split("Frequency Balance")[1].split(
        "Overall Assessment"
    )[0]
    freq_spoken = spoken_report.split("Frequency Balance")[1].split(
        "Overall Assessment"
    )[0]
    assert freq_default == freq_spoken
    assert "dB quieter" in freq_spoken


def test_spoken_tonality_notes_absent_resonances_without_exaggerating():
    clean = _make_result(
        spectrum=SpectrumMetrics(bands=_audio_drama_bands(), peaks=())
    )
    report = build_report(clean, tonality="speech")
    overall = report.split("Overall Assessment")[1].split("\n\n")[0]
    assert "no narrow resonances were found" in overall
    # One calm fragment, not a celebration: the phrase appears exactly once.
    assert report.count("no narrow resonances were found") == 1

    with_peak = _make_result(
        spectrum=SpectrumMetrics(
            bands=_audio_drama_bands(),
            peaks=(SpectralPeak(frequency_hz=420.0, prominence_db=6.0),),
        )
    )
    report = build_report(with_peak, tonality="speech")
    assert "no narrow resonances were found" not in report


def test_spoken_tonality_keeps_clear_wording_for_atypical_extremes():
    # Air dominating the mix is not a speech shape — the clear music
    # wording survives so a genuinely harsh master still gets named.
    bright = _make_result(
        spectrum=SpectrumMetrics(
            bands=_audio_drama_bands(
                sub_db=-40.0,
                bass_db=-20.0,
                low_mid_db=-18.0,
                mid_db=-15.0,
                presence_db=-10.0,
                air_db=-5.0,
            ),
            peaks=(),
        )
    )
    report = build_report(bright, tonality="speech")
    overall = report.split("Overall Assessment")[1].split("\n\n")[0]
    assert "clearly" in overall
    assert "no narrow resonances were found" not in overall

    # Bass sitting far above the dialogue mids reads as boom even for a
    # drama with a music bed — also kept clear.
    boomy = _make_result(
        spectrum=SpectrumMetrics(
            bands=_audio_drama_bands(bass_db=-1.0, mid_db=-12.0),
            peaks=(),
        )
    )
    report = build_report(boomy, tonality="speech")
    overall = report.split("Overall Assessment")[1].split("\n\n")[0]
    assert "clearly" in overall


def test_default_tonality_param_changes_nothing():
    result = _make_result(
        spectrum=SpectrumMetrics(bands=_audio_drama_bands(), peaks=())
    )
    assert build_report(result) == build_report(result, tonality="full_range")


def test_neutral_material_stays_cautious_regardless_of_tonality():
    # Without a profile the material context is neutral and the cautious
    # wording wins even if a tonality flag were passed.
    result = _make_result(
        spectrum=SpectrumMetrics(bands=_audio_drama_bands(), peaks=())
    )
    report = build_report(result, material="neutral", tonality="speech")
    assert "with the energy concentrated" in report
    assert "speech-centered" not in report
    assert "no narrow resonances were found" not in report


def test_german_spoken_tonality_wording():
    from nonvisualaudio import localization

    localization.load("de")
    try:
        result = _make_result(
            spectrum=SpectrumMetrics(bands=_audio_drama_bands(), peaks=())
        )
        report = build_report(result, tonality="speech")
        assert "klar mittenlastigen Klangbalance" not in report
        assert "lauter als" not in report.split("Gesamturteil")[1].split("\n\n")[0]
        assert "warmen, sprachzentrierten Klangbalance" in report
        assert "Präsenz und obere Höhen sind zurückhaltender als die Mitten" in report
        assert "es wurden keine schmalen Resonanzen gefunden" in report
    finally:
        localization.load("en")


def test_wide_dynamics_healthy_wording_is_music_only():
    # "Wide, healthy dynamic range" is a music-mastering value judgement:
    # for spoken-word delivery a wide range can hurt intelligibility, and
    # without a profile the report must not judge at all. LRA 13 with
    # crest 15 lands in the "wide" bucket in every mode.
    loud = LoudnessMetrics(
        integrated_lufs=-18.0,
        short_term_max_lufs=-10.0,
        true_peak_dbtp=-2.0,
        loudness_range_lu=13.0,
    )
    dyn = DynamicsMetrics(
        peak_db=-2.0,
        rms_db=-17.0,
        crest_factor_db=15.0,
        dr_score=14.0,
    )
    result = _make_result(loudness=loud, dynamics=dyn)
    assert "wide, healthy dynamic range" in build_report(result, material="music")
    neutral = build_report(result, material="neutral")
    assert "healthy" not in neutral
    assert "The dynamic range is wide" in neutral
    raw_speech = build_report(result, material="speech")
    assert "healthy" not in raw_speech
    spoken = build_report(result, material="music", tonality="speech")
    assert "healthy" not in spoken
    assert "worth checking quiet passages for intelligibility" in spoken


def test_limit_less_recommendation_assumes_limiter_only_for_music():
    # Integrated above minus 9 LUFS triggers the "ease off the limiting"
    # recommendation. That advice presumes a limiter exists — fine for a
    # music (or mastered spoken-word) profile, a guess for profile-free
    # runs and raw takes, which get the neutral level wording instead.
    loud = LoudnessMetrics(
        integrated_lufs=-8.0,
        short_term_max_lufs=-5.0,
        true_peak_dbtp=-2.0,
        loudness_range_lu=3.5,
    )
    result = _make_result(loudness=loud)
    assert "easing off the limiting" in build_report(result, material="music")
    neutral = build_report(result, material="neutral")
    assert "easing off the limiting" not in neutral
    assert "lowering the overall level" in neutral
    raw_speech = build_report(result, material="speech")
    assert "easing off the limiting" not in raw_speech
    assert "lowering the overall level" in raw_speech


def test_very_narrow_lra_limiting_claim_is_material_aware():
    # "Typical of heavily limited material" asserts a processing chain.
    # A raw, evenly spoken take can land under 3 LU without any limiter,
    # so profile-free and raw-speech runs get the descriptive neutral
    # sibling; mastered spoken-word keeps a finished-production framing.
    loud = LoudnessMetrics(
        integrated_lufs=-16.0,
        short_term_max_lufs=-13.0,
        true_peak_dbtp=-2.0,
        loudness_range_lu=2.0,
    )
    result = _make_result(loudness=loud)
    assert "typical of heavily limited material" in build_report(
        result, material="music"
    )
    neutral = build_report(result, material="neutral")
    assert "heavily limited" not in neutral
    assert "the perceived loudness stays almost constant over time" in neutral
    raw_speech = build_report(result, material="speech")
    assert "heavily limited" not in raw_speech
    spoken = build_report(result, material="music", tonality="speech")
    assert "heavily limited material" not in spoken
    assert "typical of heavily processed spoken-word versions" in spoken


def test_true_peak_recommendation_limiter_wording_is_material_aware():
    # A true peak above minus 1 dBTP triggers the ceiling recommendation.
    # The brickwall-limiter framing presumes a mastering chain; without a
    # profile (and for a raw take) the neutral sibling talks about level
    # headroom instead.
    loud = LoudnessMetrics(
        integrated_lufs=-16.0,
        short_term_max_lufs=-12.0,
        true_peak_dbtp=-0.3,
        loudness_range_lu=8.7,
    )
    result = _make_result(loudness=loud)
    assert "brickwall limiters" in build_report(result, material="music")
    neutral = build_report(result, material="neutral")
    assert "brickwall" not in neutral
    assert "a bit more level headroom helps" in neutral
    raw_speech = build_report(result, material="speech")
    assert "brickwall" not in raw_speech
    assert "a bit more level headroom helps" in raw_speech


def test_very_loud_limiting_claim_is_material_aware():
    # Review follow-up: with the recommendations now material-aware, the
    # "most likely heavily limited" loudness verdict was the last line
    # asserting a limiter in profile-free reports — the same report would
    # claim limiting up top and stay carefully neutral further down.
    loud = LoudnessMetrics(
        integrated_lufs=-7.0,
        short_term_max_lufs=-4.0,
        true_peak_dbtp=-2.0,
        loudness_range_lu=4.0,
    )
    result = _make_result(loudness=loud)
    assert "most likely heavily limited" in build_report(result, material="music")
    neutral = build_report(result, material="neutral")
    assert "heavily limited" not in neutral
    assert "very loud, leaving practically no headroom" in neutral
    raw_speech = build_report(result, material="speech")
    assert "heavily limited" not in raw_speech
    spoken = build_report(result, material="music", tonality="speech")
    assert "heavily limited" not in spoken
    assert (
        "well above the levels usual for broadcast and streaming versions"
        in spoken
    )


def test_genre_referencing_keys_have_material_siblings_in_both_catalogs():
    # Guard for the raw-key-in-UI failure mode: every key routed through
    # _material_key must have .neutral and .speech siblings in BOTH
    # catalogues, and — where the base key has a .project variant — the
    # material siblings need their .project variants too, so project
    # mode never silently falls back to file wording for one material
    # while using project wording for another.
    import json
    from pathlib import Path

    from nonvisualaudio.reporting import builder as builder_mod

    i18n_dir = (
        Path(builder_mod.__file__).resolve().parent.parent / "resources" / "i18n"
    )
    for lang in ("de", "en"):
        catalog = json.loads((i18n_dir / f"{lang}.json").read_text("utf-8"))
        for key in builder_mod._GENRE_REFERENCING_KEYS:
            assert key in catalog, f"{lang}: base key {key} missing"
            for sibling in (f"{key}.neutral", f"{key}.speech"):
                assert sibling in catalog, f"{lang}: {sibling} missing"
            if f"{key}.project" in catalog:
                for sibling in (
                    f"{key}.neutral.project",
                    f"{key}.speech.project",
                ):
                    assert sibling in catalog, f"{lang}: {sibling} missing"


def test_spoken_word_profile_suppresses_music_band_recommendations():
    # The reported oddity: an audio-drama profile (material="music",
    # tonality="speech") got "if this is music, check the high pass on
    # the mix or master bus" for its quiet sub bass — but a rolled-off
    # low end is the expected shape for spoken word, usually put there
    # by an intentional filter. The three music-band recommendations
    # must stay for real music profiles and disappear for spoken-word.
    bands = BandEnergies(
        sub_db=-30.0,
        bass_db=-12.0,
        low_mid_db=-9.0,
        mid_db=-8.0,
        presence_db=-14.0,
        air_db=-25.0,
    )
    result = _make_result(spectrum=SpectrumMetrics(bands=bands, peaks=()))
    music = build_report(result, material="music")
    assert "mix or master bus" in music
    assert "clarity and air" in music
    spoken = build_report(result, material="music", tonality="speech")
    assert "mix or master bus" not in spoken
    assert "clarity and air" not in spoken


def test_spoken_word_profile_gets_speech_limiter_recommendations():
    # With tonality now passed into the recommendations, the .speech
    # siblings of the two limiter recommendations become live for
    # mastered spoken-word profiles instead of the generic music text.
    loud = LoudnessMetrics(
        integrated_lufs=-8.0,
        short_term_max_lufs=-5.0,
        true_peak_dbtp=-0.3,
        loudness_range_lu=3.5,
    )
    result = _make_result(loudness=loud)
    music = build_report(result, material="music")
    assert "In most brickwall limiters" in music
    spoken = build_report(result, material="music", tonality="speech")
    assert "for spoken-word masters too" in spoken
    assert "easing off the limiting on the spoken-word master" in spoken


def test_speech_air_note_survives_between_floor_and_silent_threshold():
    # Review follow-up to the threshold raise: the air-note gate must sit
    # at the digital floor (SPEECH_AIR_FLOOR_DB, minus 90), NOT at the
    # raised reporting threshold (minus 70). A heavily denoised take
    # parks its top octave around minus 75 — exactly when "very
    # restrained" is warranted — while true digital silence below the
    # floor still gets no comment.
    denoised = _make_result(
        spectrum=SpectrumMetrics(
            bands=_speech_like_bands(air_high_db=-75.0), peaks=()
        )
    )
    report = build_report(denoised, material="speech")
    assert "The 10 to 20 kHz region is very restrained" in report

    digital_silence = _make_result(
        spectrum=SpectrumMetrics(
            bands=_speech_like_bands(air_high_db=-120.0), peaks=()
        )
    )
    report = build_report(digital_silence, material="speech")
    assert "The 10 to 20 kHz region is very restrained" not in report


def test_r128_broadcast_level_not_called_quiet_for_spoken_word():
    # The reported contradiction: a radio drama correctly mastered to
    # EBU R128 (minus 23 LUFS) was judged "reads as quiet, leaving
    # plenty of headroom" while the genre comparison two sections later
    # called the same value on target. For spoken-word profiles the
    # minus 20 to minus 26 region IS the classic broadcast home and
    # must be named as such; profile-free runs stay descriptive.
    loud = LoudnessMetrics(
        integrated_lufs=-23.0,
        short_term_max_lufs=-18.0,
        true_peak_dbtp=-3.0,
        loudness_range_lu=14.0,
    )
    result = _make_result(loudness=loud)
    spoken = build_report(result, material="music", tonality="speech")
    assert "classic broadcast levels" in spoken
    assert "EBU R128" in spoken
    assert "reads as quiet" not in spoken
    neutral = build_report(result, material="neutral")
    assert "broadcast" not in neutral.lower()
    assert "The file sits at a low loudness level" in neutral
    music = build_report(result, material="music")
    assert "classical, acoustic jazz, or film mixes" in music


def test_very_quiet_bucket_below_minus_26_lufs():
    # Below minus 26 LUFS even a broadcast master reads quiet. The old
    # single bucket lumped minus 23 (on target for EBU R128) together
    # with a minus 32 under-modulated take — the split keeps the
    # broadcast wording honest.
    loud = LoudnessMetrics(
        integrated_lufs=-32.0,
        short_term_max_lufs=-25.0,
        true_peak_dbtp=-8.0,
        loudness_range_lu=10.0,
    )
    result = _make_result(loudness=loud)
    music = build_report(result, material="music")
    assert "reads as very quiet" in music
    spoken = build_report(result, material="music", tonality="speech")
    assert "clearly quieter than the classic broadcast level" in spoken
    neutral = build_report(result, material="neutral")
    assert "very low loudness level" in neutral
    assert "broadcast" not in neutral.lower()


def test_moderate_bucket_cites_streaming_normalization_not_broadcast():
    # Broadcast targets are minus 23 LUFS (EBU R128) and minus 24 LKFS
    # (ATSC A/85) — both live in the quiet bucket. Calling minus 13 to
    # minus 20 "the broadcast ballpark" mislabelled the range; the
    # honest reference for it is streaming loudness normalization.
    loud = LoudnessMetrics(
        integrated_lufs=-16.0,
        short_term_max_lufs=-12.0,
        true_peak_dbtp=-1.5,
        loudness_range_lu=8.7,
    )
    result = _make_result(loudness=loud)
    music = build_report(result, material="music")
    assert "broadcast ballpark" not in music
    assert "streaming platforms aim for with loudness normalization" in music
    spoken = build_report(result, material="music", tonality="speech")
    assert "as is usual for a broadcast version" not in spoken
    assert "podcast and streaming versions of spoken-word productions" in spoken


def test_loud_speech_bucket_does_not_call_minus_11_typical():
    # Minus 9 to minus 13 LUFS sits above every common spoken-word
    # delivery target (podcast and streaming versions mostly aim for
    # minus 14 to minus 18 LUFS); the old wording called that range
    # "typical for a streaming version" of a spoken-word production.
    loud = LoudnessMetrics(
        integrated_lufs=-11.0,
        short_term_max_lufs=-8.0,
        true_peak_dbtp=-1.5,
        loudness_range_lu=4.0,
    )
    spoken = build_report(
        _make_result(loudness=loud), material="music", tonality="speech"
    )
    assert "typical for a streaming version" not in spoken
    assert "above the levels usual for podcast and streaming versions" in spoken
