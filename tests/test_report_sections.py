"""Tests for the report-section selector."""

from nonvisualaudio.analysis.result import (
    AnalysisResult,
    BandEnergies,
    DynamicsMetrics,
    FileInfo,
    LoudnessMetrics,
    SpectrumMetrics,
)
from nonvisualaudio.reporting.builder import (
    SECTION_ORDER,
    ReportSections,
    build_report as _build_report_doc,
)


def build_report(*args, **kwargs) -> str:
    """Test helper: return the rendered text instead of the doc."""
    return _build_report_doc(*args, **kwargs).to_text()


def _make_result() -> AnalysisResult:
    return AnalysisResult(
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


def test_default_sections_match_legacy_layout():
    # build_report() with no sections argument behaves like before.
    legacy = build_report(_make_result())
    explicit = build_report(_make_result(), sections=ReportSections.all())
    assert legacy == explicit


def test_only_loudness_keeps_just_that_section():
    sections = ReportSections.from_keys(["loudness"])
    text = build_report(_make_result(), sections=sections)
    assert "Loudness Summary" in text
    for skipped in (
        "File Info",
        "Dynamics Summary",
        "Frequency Balance",
        "Overall Assessment",
        "Possible Action Options",
    ):
        assert skipped not in text, f"{skipped!r} should be hidden"


def test_only_frequency_keeps_just_that_section():
    sections = ReportSections.from_keys(["frequency"])
    text = build_report(_make_result(), sections=sections)
    assert "Frequency Balance" in text
    assert "Loudness Summary" not in text
    assert "Dynamics Summary" not in text
    assert "Possible Action Options" not in text


def test_comparison_flag_drops_extras_when_off():
    from nonvisualaudio.reporting.templates import Section

    sections = ReportSections.from_keys(["loudness"])
    extras = [Section(level=2, heading="GENRE COMPARISON", body=("Line.",))]
    text = build_report(_make_result(), extra_sections=extras, sections=sections)
    assert "GENRE COMPARISON" not in text


def test_comparison_flag_keeps_extras_when_on():
    from nonvisualaudio.reporting.templates import Section

    sections = ReportSections.from_keys(["loudness", "comparison"])
    extras = [Section(level=2, heading="GENRE COMPARISON", body=("Line.",))]
    text = build_report(_make_result(), extra_sections=extras, sections=sections)
    assert "GENRE COMPARISON" in text


def test_unknown_keys_are_ignored():
    # Forward-compatible: a stale preferences file with a removed key
    # must not crash the dialog or emit empty headings.
    sections = ReportSections.from_keys(["loudness", "unknown_section"])
    text = build_report(_make_result(), sections=sections)
    assert "Loudness Summary" in text


def test_to_keys_round_trips_through_from_keys():
    selected = ReportSections.from_keys(["loudness", "frequency"])
    assert set(selected.to_keys()) == {"loudness", "frequency"}
    again = ReportSections.from_keys(selected.to_keys())
    assert again == selected


def test_section_order_lists_each_field_exactly_once():
    fields = set(ReportSections().as_dict().keys())
    assert set(SECTION_ORDER) == fields
    assert len(SECTION_ORDER) == len(fields)


def test_none_sections_renders_nothing_but_a_trailing_newline():
    text = build_report(_make_result(), sections=ReportSections.none())
    # No headings at all.
    for upper in (
        "File Info",
        "Loudness Summary",
        "Dynamics Summary",
        "Frequency Balance",
        "Overall Assessment",
        "Possible Action Options",
    ):
        assert upper not in text
