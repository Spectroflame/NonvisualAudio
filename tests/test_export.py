"""Tests for ReportDoc → plain-text / HTML / Markdown export."""

from __future__ import annotations

from nonvisualaudio.reporting.export import to_html, to_markdown, to_plain_text
from nonvisualaudio.reporting.templates import ReportDoc, Section


# The export pipeline consumes structured ReportDoc input, so the fixture
# below mirrors what the real builders emit: three level-3 sections with
# ALL-CAPS headings (the historical in-app convention for body sections).
SAMPLE = ReportDoc(
    sections=(
        Section(
            level=3,
            heading="FILE INFO",
            body=(
                "Filename: sample.wav.",
                "Duration: 2 minutes.",
                "Sample rate: 48000 Hz.",
            ),
        ),
        Section(
            level=3,
            heading="LOUDNESS SUMMARY",
            body=(
                "Integrated loudness: minus 21.4 LUFS.",
                "True peak: minus 1.1 dBTP.",
                "The file reads as moderate.",
            ),
        ),
        Section(
            level=3,
            heading="RECOMMENDATIONS",
            body=("No specific corrective actions are obvious.",),
        ),
    )
)


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------


def test_markdown_uses_h3_for_sections():
    md = to_markdown(SAMPLE)
    assert "### File Info" in md
    assert "### Loudness Summary" in md
    assert "### Recommendations" in md
    # Headings collapse from ALL CAPS to title case.
    assert "### FILE INFO" not in md
    # No stray h2 escapes when the doc only carries level-3 sections.
    assert "\n## " not in md


def test_markdown_preserves_every_body_line():
    md = to_markdown(SAMPLE)
    for needle in (
        "Filename: sample.wav.",
        "Integrated loudness: minus 21.4 LUFS.",
        "True peak: minus 1.1 dBTP.",
        "No specific corrective actions are obvious.",
    ):
        assert needle in md, f"missing body line: {needle!r}"


def test_markdown_keeps_line_breaks_via_trailing_spaces():
    # GitHub-flavoured Markdown collapses adjacent lines into one
    # paragraph; the export adds two trailing spaces so each line
    # survives as its own visual row.
    md = to_markdown(SAMPLE)
    assert "Filename: sample.wav.  \n" in md


def test_markdown_handles_doc_without_heading():
    doc = ReportDoc(
        sections=(Section(level=3, heading=None, body=("Just a single line.",)),)
    )
    md = to_markdown(doc)
    assert "Just a single line." in md
    # Headingless sections must not emit a phantom #.
    assert "##" not in md
    assert "###" not in md


def test_markdown_omits_no_ascii_separators():
    # Screen-reader-hostile underlines (===, ---) must never appear in
    # any exported format.
    md = to_markdown(SAMPLE)
    for line in md.splitlines():
        stripped = line.strip()
        assert stripped != "=" * len(stripped) or not stripped
        assert stripped != "-" * len(stripped) or not stripped


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------


def test_html_is_valid_html5_document():
    out = to_html(SAMPLE)
    assert out.startswith("<!DOCTYPE html>")
    assert "<html" in out and "</html>" in out
    assert '<meta charset="utf-8">' in out
    assert "</body>" in out


def test_html_wraps_sections_in_h3_with_anchor():
    out = to_html(SAMPLE)
    assert '<h3 id="file-info">File Info</h3>' in out
    assert '<h3 id="loudness-summary">Loudness Summary</h3>' in out
    assert '<h3 id="recommendations">Recommendations</h3>' in out


def test_html_keeps_anchors_unique_across_duplicate_headings():
    # Multi-file reports list "FILE INFO" once per analysed track. We
    # don't want every anchor to point at the first one, so collisions
    # get a counted suffix in document order.
    doc = ReportDoc(
        sections=(
            Section(level=3, heading="FILE INFO", body=("First.",)),
            Section(level=3, heading="LOUDNESS SUMMARY", body=("A.",)),
            Section(level=3, heading="FILE INFO", body=("Second.",)),
            Section(level=3, heading="LOUDNESS SUMMARY", body=("B.",)),
        )
    )
    out = to_html(doc)
    assert '<h3 id="file-info">File Info</h3>' in out
    assert '<h3 id="file-info-2">File Info</h3>' in out
    assert '<h3 id="loudness-summary">Loudness Summary</h3>' in out
    assert '<h3 id="loudness-summary-2">Loudness Summary</h3>' in out


def test_html_anchors_handle_german_umlauts():
    # The German catalogue's heading "ÜBERBLICK" must produce a
    # readable, ASCII-only anchor — the transliteration in
    # _slugify_for_anchor turns Ü into "ue".
    doc = ReportDoc(
        sections=(Section(level=3, heading="ÜBERBLICK", body=("Beispiel.",)),)
    )
    out = to_html(doc)
    assert 'id="ueberblick"' in out


def test_html_body_lines_become_paragraphs():
    out = to_html(SAMPLE)
    assert "<p>Filename: sample.wav.</p>" in out
    assert "<p>The file reads as moderate.</p>" in out


def test_html_escapes_special_characters():
    doc = ReportDoc(
        sections=(
            Section(
                level=3,
                heading="ODD INPUT",
                body=('File <weird & cool>.mp3 "quoted".',),
            ),
        )
    )
    out = to_html(doc)
    assert "<p>File &lt;weird &amp; cool&gt;.mp3 &quot;quoted&quot;.</p>" in out


def test_html_carries_provided_title_and_language():
    doc = ReportDoc(
        sections=(Section(level=3, heading="FILE INFO", body=("Line.",)),)
    )
    out = to_html(doc, title="My Report", lang="de")
    assert "<title>My Report</title>" in out
    assert '<html lang="de">' in out


# ---------------------------------------------------------------------------
# Plain text passthrough
# ---------------------------------------------------------------------------


def test_plain_text_renders_doc_to_text():
    out = to_plain_text(SAMPLE)
    # All heading lines and body lines appear verbatim.
    for needle in (
        "FILE INFO",
        "Filename: sample.wav.",
        "LOUDNESS SUMMARY",
        "Integrated loudness: minus 21.4 LUFS.",
        "RECOMMENDATIONS",
        "No specific corrective actions are obvious.",
    ):
        assert needle in out


def test_plain_text_contains_no_ascii_separator_lines():
    # The whole point of the structured pipeline: never emit a line
    # that consists solely of "=" or "-" characters.
    out = to_plain_text(SAMPLE)
    for line in out.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        assert set(stripped) != {"="}, f"= underline leaked: {line!r}"
        assert set(stripped) != {"-"}, f"- underline leaked: {line!r}"


def test_plain_text_separates_sections_with_one_blank_line():
    out = to_plain_text(SAMPLE)
    # No section block should be followed by more than one blank line.
    assert "\n\n\n" not in out


# ---------------------------------------------------------------------------
# Heading hierarchy (level 1 / 2 / 3)
# ---------------------------------------------------------------------------


HIERARCHY_SAMPLE = ReportDoc(
    sections=(
        Section(level=1, heading="Analysis: demo.wav", body=()),
        Section(level=2, heading="File Info", body=("Filename: demo.wav.",)),
        Section(
            level=3,
            heading="LOUDNESS SUMMARY",
            body=("Integrated loudness: minus 21.4 LUFS.",),
        ),
    )
)


def test_html_emits_h1_for_level_1_section():
    out = to_html(HIERARCHY_SAMPLE)
    # Level 1 → <h1>. Mixed case preserved (no .title() for level 1/2),
    # so the filename's lower-case extension stays intact.
    assert '<h1 id="analysis-demo-wav">Analysis: demo.wav</h1>' in out


def test_html_emits_h2_for_level_2_section():
    out = to_html(HIERARCHY_SAMPLE)
    assert '<h2 id="file-info">File Info</h2>' in out


def test_html_keeps_h3_for_legacy_all_caps_section():
    # Level-3 (the un-decorated ALL CAPS heading) is title-cased on the
    # way out so HTML readers do not see shouting.
    out = to_html(HIERARCHY_SAMPLE)
    assert '<h3 id="loudness-summary">Loudness Summary</h3>' in out


def test_markdown_emits_one_to_three_hashes_matching_level():
    md = to_markdown(HIERARCHY_SAMPLE)
    assert "# Analysis: demo.wav" in md
    assert "## File Info" in md
    assert "### Loudness Summary" in md


def test_html_no_outer_h1_when_body_carries_its_own():
    # The previous template injected a fixed <h1>{title}</h1> from the
    # template arg into the body. Now the body's own <h1> is the only
    # one — that keeps screen readers from announcing two competing
    # page titles. Only the <head><title> survives.
    out = to_html(HIERARCHY_SAMPLE, title="My Tab Title")
    assert "<title>My Tab Title</title>" in out
    # The fixed-template <h1> is gone — only the body's <h1> appears.
    assert out.count("<h1") == 1


def test_plain_text_keeps_no_underlines_under_mixed_case_headings():
    # Used to be the regression: level-1/level-2 headings emitted a
    # following line of "=" or "-" characters. With the structured
    # pipeline that line never exists.
    out = to_plain_text(HIERARCHY_SAMPLE)
    assert "Analysis: demo.wav" in out
    assert "File Info" in out
    # Nothing immediately after a heading is allowed to be a pure
    # separator line.
    lines = out.splitlines()
    for prev, cur in zip(lines, lines[1:]):
        if prev.strip() and cur.strip():
            assert set(cur.strip()) != {"="}
            assert set(cur.strip()) != {"-"}
