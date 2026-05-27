"""Convert the plain-text analysis report into HTML or Markdown.

The text report is the source of truth — every export format is a
mechanical transformation of it. That keeps the formats in lockstep:
fix a wording in the builder and the HTML/Markdown exports follow on
the next run with no extra work.

Heading detection runs in two passes:

  - A line followed by an RST-style underline of ``=`` (level 1) or ``-``
    (level 2) is lifted into ``<h1>`` / ``<h2>``. The builder emits
    those underlines via :func:`reporting.templates.heading` when it is
    given an explicit ``level=1`` or ``level=2``.
  - A line whose alphabetic characters are all uppercase is treated as
    a level-3 heading — the historic ALL-CAPS convention every existing
    section emits.

Anything else is body text.
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
    """Return True when ``line`` looks like a (level-3) section heading.

    The builder emits level-3 headings as the result of ``str.upper()``,
    so a line is a heading if every alphabetic character it contains is
    uppercase. Empty lines, numeric-only lines, and ordinary sentences
    (which always carry a lowercase letter somewhere) fall through to
    body text. Level 1 and 2 headings are detected separately via the
    RST underline pattern, see :func:`_underline_level`.
    """
    has_alpha = False
    for ch in line:
        if ch.isalpha():
            has_alpha = True
            if not ch.isupper():
                return False
    return has_alpha


_UNDERLINE_LEVELS: dict[str, int] = {"=": 1, "-": 2}


def _underline_level(line: str) -> int | None:
    """If ``line`` is an RST-style heading underline, return its level.

    A heading underline is a run of three or more identical ``=`` or
    ``-`` characters with no other content. Mixed runs or shorter runs
    are not treated as underlines — that keeps plain-text bullets like
    ``- one`` from triggering false matches.
    """
    if len(line) < 3:
        return None
    first = line[0]
    if first not in _UNDERLINE_LEVELS:
        return None
    if any(ch != first for ch in line):
        return None
    return _UNDERLINE_LEVELS[first]


def _split_sections(
    text: str,
) -> Iterable[tuple[int | None, str | None, list[str]]]:
    """Yield ``(level, heading, lines)`` for each section in the report.

    ``level`` is 1, 2 or 3 for headed sections and ``None`` for any
    pre-heading preamble. Levels 1 and 2 are detected via an RST
    underline of ``=`` / ``-`` on the line immediately below the
    heading text. Level 3 is the historic ALL-CAPS-only convention.
    """
    raw_lines = text.splitlines()
    current_level: int | None = None
    current_heading: str | None = None
    current_lines: list[str] = []

    def _flush() -> tuple[int | None, str | None, list[str]] | None:
        nonlocal current_lines
        while current_lines and current_lines[-1] == "":
            current_lines.pop()
        if current_heading is not None or current_lines:
            return current_level, current_heading, current_lines
        return None

    i = 0
    while i < len(raw_lines):
        line = raw_lines[i].rstrip()
        # Underline lookahead: a non-empty line followed by an underline
        # of ``=`` (level 1) or ``-`` (level 2) is a level-1 / level-2
        # heading. The underline length must be at least as long as the
        # text — that is the canonical RST contract.
        if i + 1 < len(raw_lines) and line.strip():
            next_line = raw_lines[i + 1].rstrip()
            ul_level = _underline_level(next_line)
            if ul_level is not None and len(next_line) >= len(line):
                pending = _flush()
                if pending is not None:
                    yield pending
                current_level = ul_level
                current_heading = line
                current_lines = []
                i += 2
                continue
        if _is_heading(line):
            pending = _flush()
            if pending is not None:
                yield pending
            current_level = 3
            current_heading = line
            current_lines = []
            i += 1
            continue
        current_lines.append(line)
        i += 1

    pending = _flush()
    if pending is not None:
        yield pending


def to_markdown(report_text: str) -> str:
    """Render the plain-text report as GitHub-flavoured Markdown.

    Headings collapse from ALL-CAPS to title case so the Markdown reads
    naturally — most renderers do not auto-lower headings, and reading
    "FILE INFO" rendered as a level-2 header tends to look shouty.
    Heading levels (``#``, ``##``, ``###``) follow the level detected by
    :func:`_split_sections`, so the Markdown structure matches the HTML
    export's ``<h1>`` / ``<h2>`` / ``<h3>`` hierarchy. Body lines are
    emitted verbatim with two trailing spaces so single line breaks
    survive Markdown's paragraph collapsing.
    """
    out: list[str] = []
    for level, heading_text, lines in _split_sections(report_text):
        if out:
            out.append("")
        if heading_text is not None:
            # Every common Markdown renderer (GitHub, GitLab, Obsidian,
            # pandoc) auto-generates a slug anchor from the heading
            # text, so jump-to-section links work without needing an
            # explicit ``{#slug}`` suffix in the source. ``.title()``
            # only fires for ALL-CAPS source headings (i.e. level-3
            # blocks); level 1 and 2 keep their original mixed casing
            # so filenames stay readable.
            marker = "#" * (level or 3)
            display = heading_text.title() if heading_text.isupper() else heading_text
            out.append(f"{marker} {display}")
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
    report_text: str, *, title: str = "Analysis Report", lang: str = "en"
) -> str:
    """Render the plain-text report as a self-contained HTML5 document.

    The HTML is semantic first, styled second: each heading maps to the
    ``<h1>``, ``<h2>`` or ``<h3>`` element matching the level detected
    by :func:`_split_sections`, and every non-empty body line becomes
    its own ``<p>``. Screen readers can therefore navigate the document
    hierarchy with their heading-jump shortcuts (NVDA's H key, the
    VoiceOver heading rotor) — the new project-report layout puts the
    project title on ``<h1>``, every file or track wrapper on ``<h2>``
    and every per-file section on ``<h3>``. Each heading also receives
    a stable ``id`` slug so the file can be opened at a specific
    section (for example ``…/report.html#loudness``).

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
    for level, heading_text, lines in _split_sections(report_text):
        if heading_text is not None:
            base_slug = _slugify_for_anchor(heading_text)
            count = used_anchors.get(base_slug, 0) + 1
            used_anchors[base_slug] = count
            anchor = base_slug if count == 1 else f"{base_slug}-{count}"
            tag = f"h{level}" if level in (1, 2, 3) else "h3"
            # Title-case only ALL-CAPS source headings (level 3); level
            # 1 and 2 carry their original casing so filenames and
            # project names stay readable.
            display = (
                heading_text.title() if heading_text.isupper() else heading_text
            )
            body_chunks.append(
                f'<{tag} id="{html.escape(anchor, quote=True)}">'
                f"{html.escape(display)}</{tag}>"
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
