"""Screen-reader-friendly text formatting helpers.

Rules for output:
- No Markdown characters (no asterisks, hashes, backticks, underscores).
- Section headings in ALL CAPS, on their own line, preceded by a blank line.
- Numbers written as "minus 21.4" instead of "-21.4" so screen readers speak
  them naturally. Positive numbers are unchanged.
- Decimal separator follows the active language: "." on English,
  "," on German (``minus 21,4``).
- One fact per sentence where possible; sentences end with a period.
"""

from __future__ import annotations

import math

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


def heading(title: str, level: int = 3) -> str:
    """Render a section heading at one of three levels.

    Levels 1 and 2 keep the title's own casing (so a filename in an
    ``<h1>`` reads as ``Analyse: demo.wav`` rather than the shouted
    ``ANALYSE: DEMO.WAV`` — the underline alone is enough to mark the
    line as a heading for the export pipeline). Level 3 stays
    ALL-CAPS, matching the long-standing in-app text-widget convention
    for per-section blocks; the HTML/Markdown exporters then
    title-case that ALL-CAPS form back into ``Loudness`` etc. on the
    way out.

    Levels 1 and 2 add an RST-style underline (``=`` or ``-``) on the
    following line; the HTML/Markdown exporters use that underline to
    lift the heading into ``<h1>`` / ``<h2>`` (or ``#`` / ``##``).
    """
    if level not in (1, 2, 3):
        raise ValueError(f"heading level must be 1, 2, or 3, not {level}")
    if level == 3:
        return title.upper()
    underline_char = "=" if level == 1 else "-"
    return f"{title}\n{underline_char * len(title)}"


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
