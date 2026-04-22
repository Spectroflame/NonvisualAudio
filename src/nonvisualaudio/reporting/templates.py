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


def fmt_duration(seconds: float) -> str:
    if not math.isfinite(seconds) or seconds < 0:
        return t("templates.unknown_duration")
    total = int(round(seconds))
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    hours_label = t("templates.hours")
    minutes_label = t("templates.minutes")
    seconds_label = t("templates.seconds")
    if hours:
        return (
            f"{hours} {hours_label} "
            f"{minutes} {minutes_label} "
            f"{secs} {seconds_label}"
        )
    if minutes:
        return f"{minutes} {minutes_label} {secs} {seconds_label}"
    return f"{secs} {seconds_label}"


def heading(title: str) -> str:
    return title.upper()


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
