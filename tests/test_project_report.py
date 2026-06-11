"""Tests for project-mode report rendering and helpers."""

from nonvisualaudio.analysis.project import ProjectResult
from nonvisualaudio.analysis.result import (
    AnalysisResult,
    BandEnergies,
    DynamicsMetrics,
    FileInfo,
    LoudnessMetrics,
    SpectrumMetrics,
)
from nonvisualaudio.reporting.builder import ReportSections
from nonvisualaudio.reporting.project_report import (
    build_project_report as _build_project_report_doc,
)


def build_project_report(*args, **kwargs) -> str:
    """Test helper: render the structured doc to plain text.

    The tests in this file all assert against substrings; the helper
    keeps them readable without having to spell ``.to_text()`` after
    every call.
    """
    return _build_project_report_doc(*args, **kwargs).to_text()


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


def test_project_true_peak_names_the_track_with_the_loudest_peak():
    # Build a project where the combined loudness has the new
    # project-mode true-peak provenance fields set (analyze_project does
    # this from the per-track maximum). The report has to surface both
    # the position and the source track.
    files = (
        _make_file_result("intro.wav", -16.0, 10.0),
        _make_file_result("chapter_01.wav", -14.0, 10.0),
        _make_file_result("chapter_02.wav", -14.0, 10.0),
    )
    project = _make_project(files)
    combined = project.combined
    project = ProjectResult(
        project_name=project.project_name,
        files=project.files,
        combined=AnalysisResult(
            file_info=combined.file_info,
            loudness=LoudnessMetrics(
                integrated_lufs=combined.loudness.integrated_lufs,
                short_term_max_lufs=combined.loudness.short_term_max_lufs,
                true_peak_dbtp=-0.4,
                loudness_range_lu=combined.loudness.loudness_range_lu,
                true_peak_time_seconds=137.0,
                true_peak_track_filename="chapter_01.wav",
            ),
            dynamics=combined.dynamics,
            spectrum=combined.spectrum,
        ),
    )
    report = build_project_report(project)
    assert "chapter_01.wav" in report
    # The H/M/S formatter spells 137 seconds as "2 minutes 17 seconds".
    assert "2 minutes 17 seconds" in report
    # The sentence has to be the project variant — "in {filename}".
    assert "in chapter_01.wav" in report


def test_project_true_peak_line_omitted_when_no_track_known():
    # Mirrors the legacy behaviour: if the pipeline could not pin the
    # loudest peak to a specific track (e.g. per-frame ffmpeg readings
    # missing) the timestamp line is skipped rather than guessing.
    files = (
        _make_file_result("a.wav", -14.0, 10.0),
        _make_file_result("b.wav", -14.0, 10.0),
    )
    project = _make_project(files)
    report = build_project_report(project)
    assert "highest peak occurs" not in report.lower()


def test_project_header_lists_every_track():
    project = _make_project(
        (
            _make_file_result("track_01.wav", -14.0, 10.0),
            _make_file_result("track_02.wav", -14.0, 10.0),
            _make_file_result("track_03.wav", -14.0, 10.0),
        )
    )
    report = build_project_report(project)
    assert "Project: My Album" in report
    assert "track_01.wav" in report
    assert "track_02.wav" in report
    assert "track_03.wav" in report
    # The combined sections from the inner builder show up.
    assert "Loudness Summary" in report


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


def test_project_header_uses_level_1_heading_with_project_name():
    """The project's overall title is the document's <h1>: the first
    Section carries the project name at ``level=1``. The legacy
    "Project name: …" body line is gone because the name now lives in
    the heading itself, and the structured pipeline replaced the old
    RST-style underline (which a screen reader read as noise).
    """
    files = (
        _make_file_result("a.wav", -14.0, 10.0),
        _make_file_result("b.wav", -14.0, 10.0),
    )
    project = _make_project(files)
    doc = _build_project_report_doc(project)
    first = doc.sections[0]
    assert first.level == 1
    assert first.heading is not None and "My Album" in first.heading
    # The plain-text rendering must not carry any ASCII underline.
    rendered = doc.to_text()
    assert "Project: My Album" in rendered
    for line in rendered.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        assert set(stripped) != {"="}
        assert set(stripped) != {"-"}
    # The redundant "Project name: …" body line is gone.
    assert "Project name:" not in rendered


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
        "Cross-Track Consistency"
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
    assert "Project: My Album" in report
    assert "Loudness Summary" in report
    assert "Dynamics Summary" not in report
    assert "Frequency Balance" not in report
    assert "Possible Action Options" not in report


def test_consistency_block_can_be_disabled():
    project = _make_project(
        (
            _make_file_result("a.wav", -14.0, 10.0),
            _make_file_result("b.wav", -14.0, 10.0),
        )
    )
    report = build_project_report(project, include_consistency=False)
    assert "Cross-Track Consistency" not in report


def test_consistency_block_skipped_for_single_file_projects():
    project = _make_project(
        (_make_file_result("only.wav", -14.0, 10.0),)
    )
    report = build_project_report(project)
    assert "Cross-Track Consistency" not in report


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
    text = build_report(file_result).to_text()
    # Some "the file" phrasing must survive — the moderate verdict says
    # "The file sits at..." and the recommendations fallback says "The
    # file appears balanced...".
    assert "the file" in text.lower()
    assert "the project" not in text.lower()


def test_project_report_forwards_material_to_inner_sections():
    # A speech-leaning band balance: midrange loudest, sub bass nearly
    # absent. In speech mode the inner frequency/overall sections must
    # use the speech interpretation; in neutral mode the cautious one.
    speechy = BandEnergies(
        sub_db=-45.0,
        bass_db=-12.0,
        low_mid_db=-5.0,
        mid_db=0.0,
        presence_db=-11.0,
        air_db=-9.0,
    )
    files = (
        _make_file_result("a.wav", -20.0, 12.0, bands=speechy),
        _make_file_result("b.wav", -21.0, 12.0, bands=speechy),
    )
    project = _make_project(files)

    speech_report = build_project_report(project, material="speech")
    assert "Reading this as a speech recording" in speech_report
    assert "louder than the sub bass" not in speech_report

    neutral_report = build_project_report(project, material="neutral")
    assert "The sub bass level is very low." in neutral_report
    assert "-heavy tonal balance" not in neutral_report

    music_report = build_project_report(project)
    assert "Reading this as a speech recording" not in music_report


def test_project_neutral_loudness_drops_genre_references():
    # The combined fixture lands at minus 16 LUFS ("moderate" bucket) with
    # LRA 6.5 ("typical" bucket) — in music mode both cite broadcast and
    # music-mix reference points. Without a profile the project report
    # must describe the loudness neutrally instead.
    files = (
        _make_file_result("a.wav", -16.0, 12.0),
        _make_file_result("b.wav", -16.0, 12.0),
    )
    project = _make_project(files)
    report = build_project_report(project, material="neutral")
    for banned in ("music mix", "broadcast", "streaming", "podcast", "audiobook"):
        assert banned not in report.lower(), (
            f"{banned!r} leaked into the neutral project report"
        )
    assert "Across the project, that is a moderate loudness range." in report
    assert "Across the project, the loudness sits at a moderate level." in report


def test_project_music_material_keeps_genre_wording():
    files = (
        _make_file_result("a.wav", -16.0, 12.0),
        _make_file_result("b.wav", -16.0, 12.0),
    )
    project = _make_project(files)
    report = build_project_report(project, material="music")
    assert "Across the project, that sits in the typical range for music mixes." in report
    assert "in the broadcast ballpark" in report


def test_project_neutral_mode_german_report_has_no_musik_mix():
    # German rendering of the reported bug, project flavour: no profile
    # selected must not produce "Über das ganze Projekt im üblichen
    # Bereich für Musik-Mixe."
    from nonvisualaudio import localization

    localization.load("de")
    try:
        files = (
            _make_file_result("a.wav", -16.0, 12.0),
            _make_file_result("b.wav", -16.0, 12.0),
        )
        project = _make_project(files)
        report = build_project_report(project, material="neutral")
        assert "Musik-Mix" not in report
        assert "Rundfunk" not in report
        assert "Über das ganze Projekt ein moderater Lautheitsbereich." in report
        assert (
            "Über das ganze Projekt liegt die Lautheit in einem moderaten Bereich."
            in report
        )
    finally:
        localization.load("en")
