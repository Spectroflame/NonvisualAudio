"""Behavioral tests for the analysis workflow and its thin wx adapter."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from nonvisualaudio.analysis.result import (
    AnalysisResult,
    BandEnergies,
    DynamicsMetrics,
    FileInfo,
    LoudnessMetrics,
    SpectrumMetrics,
)
from nonvisualaudio.analysis.memory import RamCheckCancelled
from nonvisualaudio.analysis.project import ProjectResult
from nonvisualaudio.cancellation import Cancellation, CancelledError
from nonvisualaudio.errors import MissingFFmpegError, UserFacingError
from nonvisualaudio.reporting.templates import ReportDoc, Section
from nonvisualaudio.ui import analysis_workflow


def _result(filename: str = "target.wav") -> AnalysisResult:
    return AnalysisResult(
        file_info=FileInfo(
            filename=filename,
            duration_seconds=1.0,
            sample_rate=48_000,
            channels=1,
            bit_depth=24,
        ),
        loudness=LoudnessMetrics(
            integrated_lufs=-18.0,
            short_term_max_lufs=-16.0,
            true_peak_dbtp=-1.0,
            loudness_range_lu=6.0,
        ),
        dynamics=DynamicsMetrics(
            peak_db=-1.0,
            rms_db=-13.0,
            crest_factor_db=12.0,
            dr_score=10.0,
        ),
        spectrum=SpectrumMetrics(
            bands=BandEnergies(
                sub_db=-20.0,
                bass_db=-12.0,
                low_mid_db=-10.0,
                mid_db=-8.0,
                presence_db=-11.0,
                air_db=-18.0,
            )
        ),
    )


@dataclass
class _CallbackRecorder:
    done: list[tuple[ReportDoc, bool]] = field(default_factory=list)
    errors: list[UserFacingError] = field(default_factory=list)
    progress: list[tuple[int, str]] = field(default_factory=list)

    def callbacks(self) -> analysis_workflow.AnalysisCallbacks:
        return analysis_workflow.AnalysisCallbacks(
            on_done=lambda report, partial: self.done.append((report, partial)),
            on_error=self.errors.append,
            on_progress=lambda percent, label: self.progress.append((percent, label)),
            confirm_memory=lambda _estimate: True,
        )


def test_request_copies_mutable_input_lists() -> None:
    targets = ["first.wav"]
    genres = ["pop"]
    references = ["reference.wav"]

    request = analysis_workflow.AnalysisRequest.create(
        targets,
        genres,
        references,
    )
    targets.append("late.wav")
    genres.clear()
    references.clear()

    assert request.targets == ("first.wav",)
    assert request.genre_keys == ("pop",)
    assert request.reference_paths == ("reference.wav",)


def test_empty_run_emits_one_user_error_and_no_success() -> None:
    recorder = _CallbackRecorder()
    request = analysis_workflow.AnalysisRequest.create([], None, None)

    analysis_workflow.run_analysis(request, recorder.callbacks(), Cancellation())

    assert recorder.done == []
    assert len(recorder.errors) == 1
    assert recorder.errors[0].title
    assert recorder.progress == []


def test_single_target_emits_report_and_completion_progress(monkeypatch) -> None:
    recorder = _CallbackRecorder()
    analyzed: list[tuple[str, int, int]] = []
    expected = ReportDoc(
        sections=(Section(level=1, heading="target.wav", body=()),)
    )

    def fake_analyze(path: str, **kwargs) -> AnalysisResult:
        analyzed.append(
            (path, kwargs["percent_start"], kwargs["percent_end"])
        )
        return _result()

    monkeypatch.setattr(analysis_workflow, "analyze", fake_analyze)
    monkeypatch.setattr(
        analysis_workflow,
        "_build_target_report",
        lambda *_args, **_kwargs: expected,
    )
    request = analysis_workflow.AnalysisRequest.create(
        ["/safe/location/target.wav"],
        None,
        None,
    )

    analysis_workflow.run_analysis(request, recorder.callbacks(), Cancellation())

    assert analyzed == [("/safe/location/target.wav", 0, 90)]
    assert recorder.errors == []
    assert recorder.done == [(expected, False)]
    assert recorder.progress[-1][0] == 100


def test_partial_batch_keeps_success_and_reports_skipped_file(monkeypatch) -> None:
    recorder = _CallbackRecorder()
    successful = ReportDoc(
        sections=(Section(level=2, heading="Track 2", body=("ok",)),)
    )

    def fake_analyze(path: str, **_kwargs) -> AnalysisResult:
        if path.endswith("broken.wav"):
            raise UserFacingError("Unreadable", "The file is damaged", "Replace it")
        return _result("good.wav")

    monkeypatch.setattr(analysis_workflow, "analyze", fake_analyze)
    monkeypatch.setattr(
        analysis_workflow,
        "_build_target_report",
        lambda *_args, **_kwargs: successful,
    )
    request = analysis_workflow.AnalysisRequest.create(
        ["/input/broken.wav", "/input/good.wav"],
        None,
        None,
    )

    analysis_workflow.run_analysis(request, recorder.callbacks(), Cancellation())

    assert recorder.errors == []
    assert len(recorder.done) == 1
    report, had_failures = recorder.done[0]
    assert had_failures is True
    assert any(
        section.heading == analysis_workflow.t("ui.worker.errors_heading")
        for section in report.sections
    )
    assert successful.sections[0] in report.sections


def test_cancellation_is_not_converted_to_file_failure(monkeypatch) -> None:
    recorder = _CallbackRecorder()
    monkeypatch.setattr(
        analysis_workflow,
        "analyze",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(CancelledError()),
    )
    request = analysis_workflow.AnalysisRequest.create(["target.wav"], None, None)

    with pytest.raises(CancelledError):
        analysis_workflow.run_analysis(
            request,
            recorder.callbacks(),
            Cancellation(),
        )

    assert recorder.errors == []
    assert recorder.done == []


def test_bad_reference_stops_before_target_analysis(monkeypatch) -> None:
    recorder = _CallbackRecorder()
    analyzed: list[str] = []

    def fake_analyze(path: str, **_kwargs) -> AnalysisResult:
        analyzed.append(path)
        raise UserFacingError("Bad reference", "Cannot decode", "Choose another")

    monkeypatch.setattr(analysis_workflow, "analyze", fake_analyze)
    request = analysis_workflow.AnalysisRequest.create(
        ["target.wav"],
        None,
        ["reference.wav"],
    )

    analysis_workflow.run_analysis(request, recorder.callbacks(), Cancellation())

    assert analyzed == ["reference.wav"]
    assert recorder.done == []
    assert len(recorder.errors) == 1
    assert recorder.errors[0].title == analysis_workflow.t(
        "worker.error.bad_reference.title"
    )
    assert "Choose another" in recorder.errors[0].hint


def test_project_mode_uses_project_pipeline_and_reporter(monkeypatch) -> None:
    recorder = _CallbackRecorder()
    combined = _result("Combined")
    project = ProjectResult(
        project_name="Album",
        files=(_result("track.wav"),),
        combined=combined,
    )
    expected = ReportDoc(
        sections=(Section(level=1, heading="Project: Album", body=()),)
    )
    analyzed_paths: list[list[str]] = []

    def fake_analyze_project(paths: list[str], **_kwargs) -> ProjectResult:
        analyzed_paths.append(paths)
        return project

    monkeypatch.setattr(analysis_workflow, "analyze_project", fake_analyze_project)
    monkeypatch.setattr(
        analysis_workflow,
        "build_project_report",
        lambda *_args, **_kwargs: expected,
    )
    request = analysis_workflow.AnalysisRequest.create(
        ["track.wav"],
        None,
        None,
        project_mode=True,
        project_name="Album",
    )

    analysis_workflow.run_analysis(request, recorder.callbacks(), Cancellation())

    assert analyzed_paths == [["track.wav"]]
    assert recorder.errors == []
    assert recorder.done == [(expected, False)]
    assert recorder.progress[-1][0] == 100


def test_missing_ffmpeg_is_a_run_level_error(monkeypatch) -> None:
    recorder = _CallbackRecorder()
    missing = MissingFFmpegError("Missing tool", "ffmpeg is unavailable", "Install it")
    monkeypatch.setattr(
        analysis_workflow,
        "analyze",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(missing),
    )
    request = analysis_workflow.AnalysisRequest.create(["target.wav"], None, None)

    analysis_workflow.run_analysis(request, recorder.callbacks(), Cancellation())

    assert recorder.errors == [missing]
    assert recorder.done == []


def test_ram_decline_stops_the_whole_batch(monkeypatch) -> None:
    recorder = _CallbackRecorder()
    analyzed: list[str] = []

    def fake_analyze(path: str, **_kwargs) -> AnalysisResult:
        analyzed.append(path)
        raise RamCheckCancelled()

    monkeypatch.setattr(analysis_workflow, "analyze", fake_analyze)
    request = analysis_workflow.AnalysisRequest.create(
        ["first.wav", "second.wav"],
        None,
        None,
    )

    analysis_workflow.run_analysis(request, recorder.callbacks(), Cancellation())

    assert analyzed == ["first.wav"]
    assert recorder.done == []
    assert len(recorder.errors) == 1
    assert recorder.errors[0].title == analysis_workflow.t(
        "worker.error.ram_cancelled.title"
    )


def test_all_failed_batch_emits_one_summary_error(monkeypatch) -> None:
    recorder = _CallbackRecorder()

    def fake_analyze(path: str, **_kwargs) -> AnalysisResult:
        raise UserFacingError(f"Cannot read {path}", "Damaged input", "Replace it")

    monkeypatch.setattr(analysis_workflow, "analyze", fake_analyze)
    request = analysis_workflow.AnalysisRequest.create(
        ["first.wav", "second.wav"],
        None,
        None,
    )

    analysis_workflow.run_analysis(request, recorder.callbacks(), Cancellation())

    assert recorder.done == []
    assert len(recorder.errors) == 1
    assert recorder.errors[0].title == analysis_workflow.t(
        "worker.error.all_failed.title",
        count=2,
    )
    assert "first.wav" in recorder.errors[0].hint
    assert "second.wav" in recorder.errors[0].hint


def test_project_pipeline_crash_becomes_project_error(monkeypatch) -> None:
    recorder = _CallbackRecorder()
    monkeypatch.setattr(
        analysis_workflow,
        "analyze_project",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    request = analysis_workflow.AnalysisRequest.create(
        ["track.wav"],
        None,
        None,
        project_mode=True,
    )

    analysis_workflow.run_analysis(request, recorder.callbacks(), Cancellation())

    assert recorder.done == []
    assert len(recorder.errors) == 1
    assert recorder.errors[0].title == analysis_workflow.t(
        "worker.error.project_failed.title"
    )


def test_multi_file_reference_is_marked_as_project(monkeypatch) -> None:
    request = analysis_workflow.AnalysisRequest.create(
        ["target.wav"],
        None,
        ["reference-1.wav", "reference-2.wav"],
    )
    reference_flags: list[bool] = []
    expected = Section(level=2, heading="Reference", body=())

    def fake_reference_comparison(
        _target,
        _reference,
        *,
        reference_is_project: bool,
        **_kwargs,
    ) -> Section:
        reference_flags.append(reference_is_project)
        return expected

    monkeypatch.setattr(
        analysis_workflow,
        "build_reference_comparison",
        fake_reference_comparison,
    )

    sections = analysis_workflow._comparison_sections(
        request,
        _result("target.wav"),
        _result("Reference project"),
        project=False,
        level=2,
    )

    assert sections == [expected]
    assert reference_flags == [True]
