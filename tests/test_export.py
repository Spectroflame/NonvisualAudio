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


def test_markdown_uses_h2_for_sections():
    md = to_markdown(SAMPLE)
    assert "## File Info" in md
    assert "## Loudness Summary" in md
    assert "## Recommendations" in md
    # Headings collapse from ALL CAPS to title case.
    assert "## FILE INFO" not in md


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


def test_html_wraps_sections_in_h2():
    out = to_html(SAMPLE)
    assert "<h2>File Info</h2>" in out
    assert "<h2>Loudness Summary</h2>" in out
    assert "<h2>Recommendations</h2>" in out


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
