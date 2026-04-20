"""Screen-reader-friendly text formatting helpers.

Rules for output:
- No Markdown characters (no asterisks, hashes, backticks, underscores).
- Section headings in ALL CAPS, on their own line, preceded by a blank line.
- Numbers written as "minus 21.4" instead of "-21.4" so screen readers speak
  them naturally. Positive numbers are unchanged.
- One fact per sentence where possible; sentences end with a period.
"""

from __future__ import annotations

import math


def fmt_signed(value: float, digits: int = 1) -> str:
    """Format a signed float as e.g. 'minus 21.4' or '3.2'."""
    if not math.isfinite(value):
        return "unknown"
    rounded = round(value, digits)
    if rounded < 0:
        return f"minus {abs(rounded):.{digits}f}"
    return f"{rounded:.{digits}f}"


def fmt_hz(hz: float) -> str:
    if hz >= 1000.0:
        return f"{hz / 1000.0:.1f} kHz"
    return f"{int(round(hz))} Hz"


def fmt_duration(seconds: float) -> str:
    if not math.isfinite(seconds) or seconds < 0:
        return "unknown duration"
    total = int(round(seconds))
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours} hours {minutes} minutes {secs} seconds"
    if minutes:
        return f"{minutes} minutes {secs} seconds"
    return f"{secs} seconds"


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
