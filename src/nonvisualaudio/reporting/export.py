"""Render a :class:`~nonvisualaudio.reporting.templates.ReportDoc` to TXT/HTML/Markdown.

The structured :class:`ReportDoc` is the single source of truth: the
plain-text TXT export is one rendering of it, and the HTML and Markdown
exports are two more. None of them carry ASCII underline markers
(``===`` / ``---``) — those would be read aloud character by character
by a screen reader, which is exactly what this app exists to avoid.

The heading hierarchy lives on each :class:`Section`'s ``level`` field
(1, 2, or 3), so the HTML export emits the matching ``<h1>``/``<h2>``/
``<h3>``, the Markdown export emits ``#``/``##``/``###``, and the
plain-text export simply lays the heading line above its body.
"""

from __future__ import annotations

import html
import re
from datetime import datetime

from nonvisualaudio.reporting.templates import ReportDoc, Section


# Transliterate the German umlauts/eszett that the localised report
# headings actually contain ("LAUTHEIT", "ÜBERBLICK", "STEREO-BILD")
# before slugifying. Other accented characters fall through to the
# regex stripping below and turn into hyphens.
_ANCHOR_TRANSLIT = str.maketrans(
    {
        "ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss",
        "Ä": "ae", "Ö": "oe", "Ü": "ue",
    }
)
_ANCHOR_NONWORD_RE = re.compile(r"[^a-z0-9]+")


def _slugify_for_anchor(text: str) -> str:
    """Turn a heading like ``"LOUDNESS"`` into a URL-fragment slug.

    Lowercase, ASCII-only, hyphen-separated; empty input collapses to
    ``"section"`` so the caller can still emit a valid ``id`` attribute.
    German headings ("LAUTHEIT", "ÜBERBLICK") are transliterated rather
    than stripped, so the German export gets readable anchors too.
    """
    base = text.translate(_ANCHOR_TRANSLIT).lower()
    slug = _ANCHOR_NONWORD_RE.sub("-", base).strip("-")
    return slug or "section"


def _display_heading(heading: str) -> str:
    """ALL-CAPS-only headings get title-cased; everything else passes through.

    Level-3 sections come in as ALL-CAPS (the long-standing in-app
    convention); HTML/Markdown viewers should not see shouting, so we
    title-case those back to "Loudness" etc. Mixed-case headings
    (filenames, project names) are left alone — capitalisation already
    reads naturally.
    """
    return heading.title() if heading.isupper() else heading


def to_markdown(doc: ReportDoc) -> str:
    """Render the report as GitHub-flavoured Markdown.

    Heading levels follow each :class:`Section`'s ``level`` field, so
    the Markdown structure matches the HTML export's ``<h1>``/``<h2>``/
    ``<h3>`` hierarchy. Body lines are emitted verbatim with two
    trailing spaces so single line breaks survive Markdown's paragraph
    collapsing.
    """
    out: list[str] = []
    for sec in doc.sections:
        if out:
            out.append("")
        if sec.heading is not None:
            # Every common Markdown renderer (GitHub, GitLab, Obsidian,
            # pandoc) auto-generates a slug anchor from the heading
            # text, so jump-to-section links work without needing an
            # explicit ``{#slug}`` suffix in the source.
            marker = "#" * sec.level
            out.append(f"{marker} {_display_heading(sec.heading)}")
            out.append("")
        for line in sec.body:
            if line == "":
                out.append("")
            else:
                # Trailing two spaces force a hard break in Markdown,
                # which preserves the per-line structure the screen
                # reader navigates.
                out.append(line + "  ")
    if not out:
        return ""
    return "\n".join(out).rstrip() + "\n"


_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
body {{ font-family: -apple-system, "Segoe UI", system-ui, sans-serif;
       line-height: 1.5; max-width: 48rem; margin: 2rem auto; padding: 0 1rem; }}
h1 {{ font-size: 1.6rem; margin-top: 0; margin-bottom: 1rem; }}
h2 {{ font-size: 1.3rem; margin-top: 2.25rem; margin-bottom: 0.75rem;
      border-bottom: 1px solid #bbb; padding-bottom: 0.25rem;
      scroll-margin-top: 1rem; }}
h3 {{ font-size: 1.1rem; margin-top: 1.75rem; margin-bottom: 0.5rem;
      scroll-margin-top: 1rem; }}
p  {{ margin: 0.35rem 0; }}
.report-meta {{ color: #666; font-size: 0.9rem; margin-bottom: 2rem; }}
@media (prefers-color-scheme: dark) {{
  body {{ background: #1c1c1e; color: #eaeaea; }}
  h2 {{ border-color: #444; }}
  .report-meta {{ color: #999; }}
}}
</style>
</head>
<body>
<p class="report-meta">{meta}</p>
{body}
</body>
</html>
"""


def to_html(
    doc: ReportDoc, *, title: str = "Analysis Report", lang: str = "en"
) -> str:
    """Render the report as a self-contained HTML5 document.

    The HTML is semantic first, styled second: each section's heading
    maps to ``<h1>``/``<h2>``/``<h3>`` according to ``Section.level``,
    and every non-empty body line becomes its own ``<p>``. Screen
    readers can therefore navigate the document hierarchy with their
    heading-jump shortcuts (NVDA's H key, the VoiceOver heading rotor).
    Each heading also receives a stable ``id`` slug so the file can be
    opened at a specific section (for example ``…/report.html#loudness``).

    The browser tab title still comes from the ``title`` parameter; the
    visible page title is whichever ``<h1>`` the report body provides.
    The embedded CSS only affects sighted users, with a dark-mode media
    query so the page does not glare on macOS / Win11 dark themes.

    Multi-file reports can repeat the same heading several times (one
    "File Info" per analysed track). We keep every anchor unique by
    appending ``-2``, ``-3``, … to later collisions in document order
    — that way a generated link to the first section keeps working
    even after another file's section was added below it.
    """
    body_chunks: list[str] = []
    used_anchors: dict[str, int] = {}
    for sec in doc.sections:
        if sec.heading is not None:
            display = _display_heading(sec.heading)
            base_slug = _slugify_for_anchor(sec.heading)
            count = used_anchors.get(base_slug, 0) + 1
            used_anchors[base_slug] = count
            anchor = base_slug if count == 1 else f"{base_slug}-{count}"
            tag = f"h{sec.level}"
            body_chunks.append(
                f'<{tag} id="{html.escape(anchor, quote=True)}">'
                f"{html.escape(display)}</{tag}>"
            )
        for line in sec.body:
            if line == "":
                continue
            body_chunks.append(f"<p>{html.escape(line)}</p>")
    meta = f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    return _HTML_TEMPLATE.format(
        lang=html.escape(lang, quote=True),
        title=html.escape(title),
        meta=html.escape(meta),
        body="\n".join(body_chunks),
    )


def to_plain_text(doc: ReportDoc) -> str:
    """Render the report as a screen-reader-friendly plain-text string.

    This is just :meth:`ReportDoc.to_text` with line-ending
    normalisation on top, so opening the exported ``.txt`` in Notepad
    on Windows shows clean line breaks regardless of the platform that
    produced it.
    """
    text = doc.to_text()
    return text.replace("\r\n", "\n").replace("\r", "\n")


def section_from_lines(level: int, heading: str | None, lines: list[str]) -> Section:
    """Tiny convenience wrapper for callers that already have a heading + body lines.

    Kept here so the export module is the one obvious place for code
    that turns "I already have a heading and some lines" into a
    :class:`Section`. Used by the worker when it wraps batches and
    error blocks into the report.
    """
    return Section(level=level, heading=heading, body=tuple(lines))
