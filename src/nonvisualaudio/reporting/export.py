"""Convert the plain-text analysis report into HTML or Markdown.

The text report is the source of truth — every export format is a
mechanical transformation of it. That keeps the formats in lockstep:
fix a wording in the builder and the HTML/Markdown exports follow on
the next run with no extra work.

Heuristic for headings: a line is treated as a section heading when
*all* of its alphabetic characters are uppercase (and at least one
alphabetic character is present). The builder always renders headings
that way via :func:`nonvisualaudio.reporting.templates.heading`, so the
heuristic stays in sync with the source.
"""

from __future__ import annotations

import html
import re
from collections.abc import Iterable
from datetime import datetime


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


def _is_heading(line: str) -> bool:
    """Return True when ``line`` looks like a section heading.

    The builder emits headings as the result of ``str.upper()``, so a
    line is a heading if every alphabetic character it contains is
    uppercase. Empty lines, numeric-only lines, and ordinary sentences
    (which always carry a lowercase letter somewhere) fall through to
    body text.
    """
    has_alpha = False
    for ch in line:
        if ch.isalpha():
            has_alpha = True
            if not ch.isupper():
                return False
    return has_alpha


def _split_sections(text: str) -> Iterable[tuple[str | None, list[str]]]:
    """Yield ``(heading, lines)`` pairs for each section in the report.

    Headings are detected with :func:`_is_heading`. The pre-heading
    preamble (if any) comes back with ``heading is None``. Each
    section's ``lines`` list excludes the heading itself and the
    blank-line separators between sections.
    """
    current_heading: str | None = None
    current_lines: list[str] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        if _is_heading(line):
            if current_heading is not None or current_lines:
                # Trim trailing blank lines from the previous section
                # so the structure round-trips cleanly.
                while current_lines and current_lines[-1] == "":
                    current_lines.pop()
                yield current_heading, current_lines
            current_heading = line
            current_lines = []
            continue
        current_lines.append(line)
    while current_lines and current_lines[-1] == "":
        current_lines.pop()
    if current_heading is not None or current_lines:
        yield current_heading, current_lines


def to_markdown(report_text: str) -> str:
    """Render the plain-text report as GitHub-flavoured Markdown.

    Headings collapse from ALL-CAPS to title case so the Markdown reads
    naturally — most renderers do not auto-lower headings, and reading
    "FILE INFO" rendered as a level-2 header tends to look shouty.
    Body lines are emitted verbatim with two trailing spaces so single
    line breaks survive Markdown's paragraph collapsing.
    """
    out: list[str] = []
    for heading, lines in _split_sections(report_text):
        if out:
            out.append("")
        if heading is not None:
            # Level-3 heading to stay in lock-step with the HTML export
            # (which uses <h3 id="…">). Every common Markdown renderer
            # (GitHub, GitLab, Obsidian, pandoc) auto-generates a slug
            # anchor from the heading text, so jump-to-section links
            # work the same way they do in the HTML output without
            # needing an explicit ``{#slug}`` suffix in the source.
            out.append(f"### {heading.title()}")
            out.append("")
        for line in lines:
            if line == "":
                out.append("")
            else:
                # Trailing two spaces force a hard break in Markdown,
                # which preserves the per-line structure the screen
                # reader navigates.
                out.append(line + "  ")
    return "\n".join(out).rstrip() + "\n"


_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
body {{ font-family: -apple-system, "Segoe UI", system-ui, sans-serif;
       line-height: 1.5; max-width: 48rem; margin: 2rem auto; padding: 0 1rem; }}
h1 {{ font-size: 1.4rem; margin-bottom: 1.5rem; }}
h3 {{ font-size: 1.15rem; margin-top: 2rem; margin-bottom: 0.5rem;
      border-bottom: 1px solid #ccc; padding-bottom: 0.25rem;
      scroll-margin-top: 1rem; }}
p  {{ margin: 0.35rem 0; }}
.report-meta {{ color: #666; font-size: 0.9rem; margin-bottom: 2rem; }}
@media (prefers-color-scheme: dark) {{
  body {{ background: #1c1c1e; color: #eaeaea; }}
  h3 {{ border-color: #444; }}
  .report-meta {{ color: #999; }}
}}
</style>
</head>
<body>
<h1>{title}</h1>
<p class="report-meta">{meta}</p>
{body}
</body>
</html>
"""


def to_html(
    report_text: str, *, title: str = "Analysis Report", lang: str = "en"
) -> str:
    """Render the plain-text report as a self-contained HTML5 document.

    The HTML is intentionally semantic first, styled second: section
    headings map to ``<h3 id="…">``, each non-empty line becomes its
    own ``<p>``. Screen readers can therefore navigate by heading the
    same way they navigate the plain text report, and the per-section
    ``id`` attributes give every heading a stable URL fragment so the
    file can be opened directly at a specific section (for example
    ``…/report.html#loudness``). The embedded CSS only affects the
    visual rendering for sighted users (with a dark-mode media query so
    the page does not glare on macOS/Win11 dark themes).

    Multi-file reports can repeat the same heading several times (one
    "File Info" per analysed track). We keep every anchor unique by
    appending ``-2``, ``-3``, … to later collisions in document order
    — that way a generated link to the first section keeps working
    even after another file's section was added below it.
    """
    body_chunks: list[str] = []
    used_anchors: dict[str, int] = {}
    for heading, lines in _split_sections(report_text):
        if heading is not None:
            base_slug = _slugify_for_anchor(heading)
            count = used_anchors.get(base_slug, 0) + 1
            used_anchors[base_slug] = count
            anchor = base_slug if count == 1 else f"{base_slug}-{count}"
            body_chunks.append(
                f'<h3 id="{html.escape(anchor, quote=True)}">'
                f"{html.escape(heading.title())}</h3>"
            )
        for line in lines:
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


def to_plain_text(report_text: str) -> str:
    """Pass-through for TXT export — the report already is plain text.

    The function exists so the export dispatcher can treat all three
    formats uniformly and so a future tweak (line-ending normalisation,
    BOM handling) has one obvious home.
    """
    # Normalise line endings to the platform-default newline so opening
    # the file in Notepad on Windows shows clean line breaks.
    normalised = report_text.replace("\r\n", "\n").replace("\r", "\n")
    return normalised
