"""Screen-reader-friendly text formatting helpers.

Rules for output:
- No Markdown characters (no asterisks, hashes, backticks, underscores).
- No ASCII separator lines (``===``, ``---``, ``***`` and friends): a
  screen reader reads them character by character, which is pure noise.
  Heading hierarchy is carried by the structured :class:`Section` /
  :class:`ReportDoc` types below instead, so the plain-text output never
  needs a visual underline.
- Level-3 section headings in ALL CAPS, on their own line; the
  HTML/Markdown exporter title-cases them back. Levels 1 and 2 keep
  their natural Mixed-Case (filename, project name, …) because the
  exporters carry their level via the structured pipeline.
- Numbers written as "minus 21.4" instead of "-21.4" so screen readers speak
  them naturally. Positive numbers are unchanged.
- Decimal separator follows the active language: "." on English,
  "," on German (``minus 21,4``).
- One fact per sentence where possible; sentences end with a period.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

from nonvisualaudio.localization import decimal_sep, t


def _localise_decimal(text: str) -> str:
    """Swap ``.`` for the active-locale decimal separator.

    Safe on strings that contain no dot — the replacement is a no-op.
    """
    sep = decimal_sep()
    if sep == ".":
        return text
    return text.replace(".", sep)


def fmt_signed(value: float, digits: int = 1) -> str:
    """Format a signed float as e.g. 'minus 21.4' or '3.2'."""
    if not math.isfinite(value):
        return t("templates.unknown")
    rounded = round(value, digits)
    magnitude = _localise_decimal(f"{abs(rounded):.{digits}f}")
    if rounded < 0:
        return f"{t('templates.minus')} {magnitude}"
    return magnitude


def fmt_decimal(value: float, digits: int = 1) -> str:
    """Format a non-negative float using the locale decimal separator.

    Preserves the sign — use ``fmt_signed`` when the value must be
    spelled out as "minus X" instead of "-X".
    """
    if not math.isfinite(value):
        return t("templates.unknown")
    return _localise_decimal(f"{value:.{digits}f}")


def fmt_hz(hz: float) -> str:
    if hz >= 1000.0:
        return f"{_localise_decimal(f'{hz / 1000.0:.1f}')} {t('templates.khz')}"
    return f"{int(round(hz))} {t('templates.hz')}"


def _unit_label(count: int, plural_key: str, singular_key: str) -> str:
    """Pick the singular form when ``count`` is exactly one.

    German and English both fall back to plural for 0 and any value
    greater than 1, so we only special-case ``count == 1``. Anything
    fractional (``0.4`` seconds, ``1.5`` minutes) is always plural —
    the caller passes the unit count as an int, so we never see those
    here.
    """
    return t(singular_key) if count == 1 else t(plural_key)


def fmt_duration(seconds: float) -> str:
    if not math.isfinite(seconds) or seconds < 0:
        return t("templates.unknown_duration")
    total = int(round(seconds))
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    hours_label = _unit_label(hours, "templates.hours", "templates.hour")
    minutes_label = _unit_label(minutes, "templates.minutes", "templates.minute")
    seconds_label = _unit_label(secs, "templates.seconds", "templates.second")
    if hours:
        return (
            f"{hours} {hours_label} "
            f"{minutes} {minutes_label} "
            f"{secs} {seconds_label}"
        )
    if minutes:
        return f"{minutes} {minutes_label} {secs} {seconds_label}"
    return f"{secs} {seconds_label}"


def fmt_peak_time(seconds: float) -> str:
    """Format a peak position, with sub-second precision only when useful.

    For peaks under one second we keep one decimal place so a 0.4 s
    spike reads as "0.4 seconds" rather than the old "0 seconds" —
    that was the bug: a sub-second peak at the very start of the file
    looked rounded down to zero. From one second upward we fall back
    to :func:`fmt_duration`'s whole-second formatting, because at
    minute-level granularity tenths just add noise (a peak at "2
    minutes 13.7 seconds" doesn't help the user any more than "2
    minutes 13 seconds").
    """
    if not math.isfinite(seconds) or seconds < 0:
        return t("templates.unknown_duration")
    if seconds < 1.0:
        secs_str = _localise_decimal(f"{round(seconds, 1):.1f}")
        return f"{secs_str} {t('templates.seconds')}"
    return fmt_duration(seconds)


def heading_text(title: str, level: int = 3) -> str:
    """Return the heading line as a plain string at the given level.

    Levels 1 and 2 keep the original Mixed-Case (filename, project name)
    because the HTML/Markdown exporters know the level via the
    structured :class:`Section` pipeline and don't need a visual
    underline in the plain-text form. Level 3 stays ALL-CAPS, matching
    the long-standing in-app convention for per-section blocks; the
    HTML/Markdown exporters then title-case it back into ``Loudness``
    etc. on the way out.
    """
    if level not in (1, 2, 3):
        raise ValueError(f"heading level must be 1, 2, or 3, not {level}")
    if level == 3:
        return title.upper()
    return title


def paragraph(*sentences: str) -> str:
    """Join non-empty sentences into one paragraph, each ending with a period."""
    cleaned: list[str] = []
    for s in sentences:
        if not s:
            continue
        s = s.strip()
        if not s:
            continue
        if not s.endswith((".", "!", "?")):
            s = s + "."
        cleaned.append(s)
    return " ".join(cleaned)


@dataclass(frozen=True)
class Section:
    """One heading-bearing block in a report.

    ``level`` is 1, 2, or 3 and maps directly onto ``<h1>``/``<h2>``/
    ``<h3>`` in the HTML export and ``#``/``##``/``###`` in Markdown.
    ``heading`` is the plain heading line as :func:`heading_text` would
    render it (Mixed-Case for level 1/2, ALL-CAPS for level 3); the
    HTML/Markdown exporters title-case ALL-CAPS headings back to
    "Loudness" etc. on the way out. ``heading`` may be ``None`` for a
    rare preamble block that carries body lines without a heading of
    its own.

    ``body`` is the per-line body text in the order it should appear.
    Empty strings are allowed and render as paragraph breaks.
    """

    level: int
    heading: str | None
    body: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.level not in (1, 2, 3):
            raise ValueError(f"Section level must be 1, 2, or 3, not {self.level}")


@dataclass(frozen=True)
class ReportDoc:
    """A structured report ready to be rendered to text/HTML/Markdown.

    The structured form is the single source of truth: the plain-text
    output is one rendering of the doc, the HTML and Markdown exports
    are two more. None of them carry ASCII underline markers — the
    heading hierarchy lives in the section data, not in the text.
    """

    sections: tuple[Section, ...] = ()

    def to_text(self) -> str:
        """Render to a screen-reader-friendly plain-text string.

        Each section becomes a heading line (if any) followed by its
        body lines; sections are separated by a single blank line. No
        underlines, no separator characters — the screen reader's
        line-by-line navigation is enough structure.
        """
        if not self.sections:
            return ""
        blocks: list[str] = []
        for sec in self.sections:
            lines: list[str] = []
            if sec.heading is not None:
                lines.append(sec.heading)
            lines.extend(sec.body)
            # Trim trailing blank lines so the between-section spacing
            # stays exactly one blank line regardless of how the section
            # was assembled.
            while lines and lines[-1] == "":
                lines.pop()
            if lines:
                blocks.append("\n".join(lines))
        return "\n\n".join(blocks) + "\n"


def make_section(
    title: str,
    body: Iterable[str],
    *,
    level: int = 3,
) -> Section:
    """Convenience constructor: build a :class:`Section` from a title and body lines.

    Empty body lines are kept so callers can emit paragraph breaks
    inside a section; only trailing blanks would matter for layout, and
    :meth:`ReportDoc.to_text` strips those at render time.
    """
    body_tuple = tuple(body)
    return Section(level=level, heading=heading_text(title, level=level), body=body_tuple)
