"""Tests for project-mode report rendering and helpers."""

import numpy as np

from nonvisualaudio.analysis.project import (
    ProjectResult,
    _concatenate_decoded,
    _resample_mono,
)
from nonvisualaudio.analysis.result import (
    AnalysisResult,
    BandEnergies,
    DynamicsMetrics,
    FileInfo,
    LoudnessMetrics,
    SpectrumMetrics,
)
from nonvisualaudio.audio.decoder import DecodedAudio
from nonvisualaudio.reporting.builder import ReportSections
from nonvisualaudio.reporting.project_report import build_project_report


def _make_file_result(
    name: str,
    integrated: float,
    crest: float,
    bands: BandEnergies | None = None,
) -> AnalysisResult:
    return AnalysisResult(
        file_info=FileInfo(
            filename=name,
            duration_seconds=120.0,
            sample_rate=48000,
            channels=2,
            bit_depth=24,
        ),
        loudness=LoudnessMetrics(
            integrated_lufs=integrated,
            short_term_max_lufs=integrated + 4.0,
            true_peak_dbtp=-1.0,
            loudness_range_lu=6.0,
        ),
        dynamics=DynamicsMetrics(
            peak_db=-0.5,
            rms_db=-0.5 - crest,
            crest_factor_db=crest,
            dr_score=10.0,
        ),
        spectrum=SpectrumMetrics(
            bands=bands
            or BandEnergies(
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


def _make_project(files: tuple[AnalysisResult, ...]) -> ProjectResult:
    # Synthesize a "combined" result that averages the individual files
    # for the loudness fields. Real callers get this from the pipeline,
    # but for layout tests we just need consistent numbers.
    combined_i = sum(f.loudness.integrated_lufs for f in files) / len(files)
    combined_crest = sum(f.dynamics.crest_factor_db for f in files) / len(files)
    avg_bands = BandEnergies(
        sub_db=sum(f.spectrum.bands.sub_db for f in files) / len(files),
        bass_db=sum(f.spectrum.bands.bass_db for f in files) / len(files),
        low_mid_db=sum(f.spectrum.bands.low_mid_db for f in files) / len(files),
        mid_db=sum(f.spectrum.bands.mid_db for f in files) / len(files),
        presence_db=sum(f.spectrum.bands.presence_db for f in files) / len(files),
        air_db=sum(f.spectrum.bands.air_db for f in files) / len(files),
    )
    total_dur = sum(f.file_info.duration_seconds for f in files)
    combined = AnalysisResult(
        file_info=FileInfo(
            filename="My Album",
            duration_seconds=total_dur,
            sample_rate=48000,
            channels=2,
            bit_depth=None,
        ),
        loudness=LoudnessMetrics(
            integrated_lufs=combined_i,
            short_term_max_lufs=combined_i + 4.0,
            true_peak_dbtp=-1.0,
            loudness_range_lu=6.5,
        ),
        dynamics=DynamicsMetrics(
            peak_db=-0.5,
            rms_db=-0.5 - combined_crest,
            crest_factor_db=combined_crest,
            dr_score=10.0,
        ),
        spectrum=SpectrumMetrics(bands=avg_bands, peaks=()),
    )
    return ProjectResult(project_name="My Album", files=files, combined=combined)


def test_project_header_lists_every_track():
    project = _make_project(
        (
            _make_file_result("track_01.wav", -14.0, 10.0),
            _make_file_result("track_02.wav", -14.0, 10.0),
            _make_file_result("track_03.wav", -14.0, 10.0),
        )
    )
    report = build_project_report(project)
    assert "PROJECT" in report
    assert "track_01.wav" in report
    assert "track_02.wav" in report
    assert "track_03.wav" in report
    # The combined sections from the inner builder show up.
    assert "LOUDNESS SUMMARY" in report


def test_consistency_block_names_loudest_and_quietest_track():
    files = (
        _make_file_result("track_01.wav", -14.0, 10.0),
        _make_file_result("track_02.wav", -14.0, 10.0),
        _make_file_result("track_loud.wav", -8.0, 10.0),  # 6 LU louder
    )
    project = _make_project(files)
    report = build_project_report(project)
    # The loudest and quietest track must be named.
    assert "track_loud.wav" in report
    assert "track_01.wav" in report
    # 6 LU spread → the "consistent level" wording must not appear.
    assert "consistent level" not in report.lower()


def test_consistency_block_says_consistent_when_spread_is_tiny():
    files = (
        _make_file_result("a.wav", -14.0, 10.0),
        _make_file_result("b.wav", -14.1, 10.0),
    )
    project = _make_project(files)
    report = build_project_report(project)
    assert "consistent level" in report.lower()


def test_consistency_block_does_not_mention_dynamics_or_frequency():
    # User feedback: dynamics and frequency outliers added noise without
    # signal — the section must stay loudness-only.
    files = (
        _make_file_result("a.wav", -14.0, 12.0),
        _make_file_result("b.wav", -14.0, 12.0),
        _make_file_result("crushed.wav", -14.0, 5.0),
    )
    project = _make_project(files)
    consistency = build_project_report(project).split(
        "CROSS-TRACK CONSISTENCY"
    )[1]
    for term in (
        "crest factor",
        "more dynamic",
        "more compressed",
        "frequency-balance",
        "stronger than",
        "weaker than",
    ):
        assert term not in consistency.lower(), (
            f"{term!r} leaked into the slim consistency block"
        )


def test_section_filter_drops_combined_blocks_but_keeps_header():
    project = _make_project(
        (
            _make_file_result("a.wav", -14.0, 10.0),
            _make_file_result("b.wav", -14.0, 10.0),
        )
    )
    sections = ReportSections.from_keys(["file_info", "loudness"])
    report = build_project_report(project, sections=sections)
    assert "PROJECT" in report
    assert "LOUDNESS SUMMARY" in report
    assert "DYNAMICS SUMMARY" not in report
    assert "FREQUENCY BALANCE" not in report
    assert "RECOMMENDATIONS" not in report


def test_consistency_block_can_be_disabled():
    project = _make_project(
        (
            _make_file_result("a.wav", -14.0, 10.0),
            _make_file_result("b.wav", -14.0, 10.0),
        )
    )
    report = build_project_report(project, include_consistency=False)
    assert "CROSS-TRACK CONSISTENCY" not in report


def test_consistency_block_skipped_for_single_file_projects():
    project = _make_project(
        (_make_file_result("only.wav", -14.0, 10.0),)
    )
    report = build_project_report(project)
    assert "CROSS-TRACK CONSISTENCY" not in report


def test_project_mode_wording_addresses_the_project_not_the_file():
    # Project verdicts must say "the project" — saying "the file" would
    # be wrong when the analysis is the concatenation of many tracks.
    from nonvisualaudio.localization import load

    load("en")
    project = _make_project(
        (
            _make_file_result("a.wav", -14.0, 10.0),
            _make_file_result("b.wav", -14.0, 10.0),
        )
    )
    report = build_project_report(project)
    assert "the project" in report.lower()
    # The legacy file-centric phrasing should be gone from the verdict
    # surface (the FILE INFO heading is suppressed in project mode).
    assert "the file is" not in report.lower()
    assert "this file sits" not in report.lower()
    assert "the file appears balanced" not in report.lower()


def test_single_file_mode_still_uses_file_wording():
    # Regression guard: turning on project mode for project_report must
    # not bleed back into the regular single-file build_report flow.
    from nonvisualaudio.localization import load
    from nonvisualaudio.reporting.builder import build_report

    load("en")
    file_result = _make_file_result("solo.wav", -14.0, 10.0)
    text = build_report(file_result)
    # Some "the file" phrasing must survive — the moderate verdict says
    # "The file sits at..." and the recommendations fallback says "The
    # file appears balanced...".
    assert "the file" in text.lower()
    assert "the project" not in text.lower()


def test_resample_mono_keeps_length_proportional():
    src = np.linspace(-1.0, 1.0, 480, dtype=np.float32)  # 10 ms at 48k
    out = _resample_mono(src, 48000, 96000)
    assert out.dtype == np.float32
    # 2x upsample → ~2x samples (polyphase may differ by a few).
    assert abs(len(out) - 960) <= 4


def test_resample_mono_is_no_op_for_matching_rates():
    src = np.linspace(-1.0, 1.0, 100, dtype=np.float32)
    out = _resample_mono(src, 48000, 48000)
    assert np.array_equal(src, out)


def test_concatenate_decoded_uses_max_sample_rate():
    a = DecodedAudio(
        samples=np.zeros(48000, dtype=np.float32),
        sample_rate=48000,
        channels=1,
        bit_depth=16,
        duration_seconds=1.0,
        filename="a.wav",
    )
    b = DecodedAudio(
        samples=np.zeros(96000, dtype=np.float32),
        sample_rate=96000,
        channels=1,
        bit_depth=16,
        duration_seconds=1.0,
        filename="b.wav",
    )
    combined, rate = _concatenate_decoded([a, b])
    assert rate == 96000
    # 48k → 96k upsample of 48000 samples ≈ 96000 samples; plus the
    # 96000 native samples → ~192000 total.
    assert abs(len(combined) - 192000) <= 8
