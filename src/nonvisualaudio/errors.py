"""Structured, user-facing errors.

The UI layer shows these verbatim to the user, so the wording is the
product. The three fields separate a short headline from an explanation
and a concrete next step, which lets a screen reader announce them as
distinct sentences without the user having to parse a wall of text.

Non-user-facing exceptions (programming errors, Qt/wx internals, etc.)
stay as plain Python exceptions. Only things the user can meaningfully
react to are wrapped in ``UserFacingError``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UserFacingError(Exception):
    """An error that is safe and useful to show to the end user."""

    title: str
    body: str
    hint: str = ""

    def __str__(self) -> str:  # used for logging and fallback display
        parts = [self.title, self.body]
        if self.hint:
            parts.append(self.hint)
        return " — ".join(p for p in parts if p)

    def as_message(self) -> str:
        """Return a multi-paragraph plain-text message for display."""
        parts = [self.title, self.body]
        if self.hint:
            parts.append(self.hint)
        return "\n\n".join(p for p in parts if p)


class MissingFFmpegError(UserFacingError):
    """Raised when the bundled and system ffmpeg are both absent."""


class AudioDecodeError(UserFacingError):
    """Raised when an input file cannot be decoded."""


class LoudnessMeasurementError(UserFacingError):
    """Raised when ffmpeg's ebur128 filter produces unparseable output."""
