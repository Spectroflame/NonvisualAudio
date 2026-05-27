"""Tests for plain-text → HTML / Markdown export."""

from __future__ import annotations

from nonvisualaudio.reporting.export import (
    _is_heading,
    to_html,
    to_markdown,
    to_plain_text,
)


SAMPLE = """FILE INFO
Filename: sample.wav.
Duration: 2 minutes.
Sample rate: 48000 Hz.

LOUDNESS SUMMARY
Integrated loudness: minus 21.4 LUFS.
True peak: minus 1.1 dBTP.
The file reads as moderate.

RECOMMENDATIONS
No specific corrective actions are obvious.
"""


def test_heading_detection_accepts_all_caps_with_alpha():
    assert _is_heading("FILE INFO")
    assert _is_heading("STEREO IMAGE")


def test_heading_detection_rejects_body_text():
    assert not _is_heading("Filename: sample.wav.")
    assert not _is_heading("Integrated loudness: minus 21.4 LUFS.")
    assert not _is_heading("The file reads as moderate.")


def test_heading_detection_rejects_empty_or_numeric():
    assert not _is_heading("")
    assert not _is_heading("48000")
    assert not _is_heading("   ")


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
    # Stay in lock-step with the HTML export — no stray h2 escapes.
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


def test_markdown_handles_report_without_heading():
    md = to_markdown("Just a single line.\n")
    assert "Just a single line." in md
    # No phantom heading.
    assert "##" not in md


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
    text = (
        "FILE INFO\nFirst.\n\n"
        "LOUDNESS SUMMARY\nA.\n\n"
        "FILE INFO\nSecond.\n\n"
        "LOUDNESS SUMMARY\nB.\n"
    )
    out = to_html(text)
    assert '<h3 id="file-info">File Info</h3>' in out
    assert '<h3 id="file-info-2">File Info</h3>' in out
    assert '<h3 id="loudness-summary">Loudness Summary</h3>' in out
    assert '<h3 id="loudness-summary-2">Loudness Summary</h3>' in out


def test_html_anchors_handle_german_umlauts():
    # The German catalogue's heading "ÜBERBLICK" must produce a
    # readable, ASCII-only anchor — the transliteration in
    # _slugify_for_anchor turns Ü into "ue".
    out = to_html("ÜBERBLICK\nBeispiel.\n")
    assert 'id="ueberblick"' in out


def test_html_body_lines_become_paragraphs():
    out = to_html(SAMPLE)
    assert "<p>Filename: sample.wav.</p>" in out
    assert "<p>The file reads as moderate.</p>" in out


def test_html_escapes_special_characters():
    text = "ODD INPUT\nFile <weird & cool>.mp3 \"quoted\".\n"
    out = to_html(text)
    assert "<p>File &lt;weird &amp; cool&gt;.mp3 &quot;quoted&quot;.</p>" in out


def test_html_carries_provided_title_and_language():
    out = to_html("FILE INFO\nLine.\n", title="My Report", lang="de")
    assert "<title>My Report</title>" in out
    assert '<html lang="de">' in out


# ---------------------------------------------------------------------------
# Plain text passthrough
# ---------------------------------------------------------------------------


def test_plain_text_normalises_windows_line_endings():
    text = "LINE ONE\r\nline two\r\n"
    assert to_plain_text(text) == "LINE ONE\nline two\n"


def test_plain_text_normalises_old_mac_line_endings():
    text = "LINE ONE\rline two\r"
    assert to_plain_text(text) == "LINE ONE\nline two\n"


def test_plain_text_passes_unix_through_unchanged():
    text = "LINE ONE\nline two\n"
    assert to_plain_text(text) == text


# ---------------------------------------------------------------------------
# Heading hierarchy (level 1 / 2 / 3)
# ---------------------------------------------------------------------------


HIERARCHY_SAMPLE = """Analysis: demo.wav
==================

File Info
---------
Filename: demo.wav.

LOUDNESS SUMMARY
Integrated loudness: minus 21.4 LUFS.
"""


def test_html_emits_h1_for_level_1_underlined_heading():
    out = to_html(HIERARCHY_SAMPLE)
    # Level 1 → <h1>. Case is preserved (no .title() for level 1 / 2),
    # so the filename's lower-case extension stays intact in the HTML.
    assert '<h1 id="analysis-demo-wav">Analysis: demo.wav</h1>' in out


def test_html_emits_h2_for_level_2_dash_underlined_heading():
    out = to_html(HIERARCHY_SAMPLE)
    assert '<h2 id="file-info">File Info</h2>' in out


def test_html_keeps_h3_for_legacy_all_caps_heading():
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


def test_html_handles_short_underline_below_heading_text():
    # A canonical RST underline matches the heading length exactly, but
    # we accept underlines that are at least as long. Anything shorter
    # falls through to body text.
    out = to_html("Heading\n=====\nLine.\n")
    # Three "=" is below the heading length (7), so this is NOT an
    # underline → no <h1>. The "Heading" line becomes a <p>.
    assert "<h1" not in out
    assert "<p>Heading</p>" in out
