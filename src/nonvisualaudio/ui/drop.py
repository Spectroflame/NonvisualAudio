"""Path handling for drag-and-drop, clipboard paste, and startup scan.

A single source of truth for which extensions count as "audio" (so the
file-dialog wildcard, the drop target, the paste handler, and the
startup clipboard scan stay in lock-step), plus two pure helpers that
are easy to test without a running wx event loop.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable
from pathlib import Path
from urllib.parse import unquote, urlparse

log = logging.getLogger("nonvisualaudio.drop")


# Lowercase extensions, including the leading dot. WMA runs through the
# bundled ffmpeg fallback — libsndfile rejects it, and decoder.py already
# handles that path automatically.
SUPPORTED_EXTS: frozenset[str] = frozenset(
    {
        ".wav",
        ".aiff",
        ".aif",
        ".mp3",
        ".m4a",
        ".aac",
        ".flac",
        ".ogg",
        ".opus",
        ".wma",
    }
)


def _has_supported_ext(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED_EXTS


def expand_audio_paths(paths: Iterable[str]) -> list[str]:
    """Return a deduplicated list of audio-file paths.

    - Plain files with a supported extension pass through.
    - Folders are walked recursively; audio files inside are added in
      deterministic (sorted) order so two runs over the same folder
      produce the same list.
    - Unknown extensions and non-existent paths are dropped silently —
      the UI is the right place to flag "nothing importable found".
    - Duplicates are removed while preserving first-seen order.
    """
    seen: set[str] = set()
    out: list[str] = []

    def _add(candidate: Path) -> None:
        resolved = str(candidate)
        if resolved in seen:
            return
        seen.add(resolved)
        out.append(resolved)

    for raw in paths:
        if not raw:
            continue
        path = Path(raw).expanduser()
        try:
            # resolve() normalises symlinks and trailing slashes but
            # keeps the original behaviour when the path does not exist
            # (strict=False is the default).
            path = path.resolve()
        except OSError:
            continue
        if path.is_file():
            if _has_supported_ext(path):
                _add(path)
            continue
        if path.is_dir():
            # Sorted walk: os.walk alone does not order file names on
            # all platforms, and we want identical output across runs.
            for dirpath, dirnames, filenames in os.walk(path):
                dirnames.sort()
                for name in sorted(filenames):
                    child = Path(dirpath) / name
                    if _has_supported_ext(child):
                        _add(child)
            continue
        # Silently ignore everything else (sockets, devices, vanished
        # files after a stale clipboard copy, etc.).
        log.debug("expand_audio_paths: ignoring %s", raw)

    return out


def parse_paste_text(text: str) -> list[str]:
    """Turn a clipboard text blob into a list of filesystem paths.

    Handles two common cases:
    - One path per line, optionally wrapped in quotes.
    - ``file://…`` URIs, one per line — macOS Finder and some Linux
      file managers put these on the clipboard when the user presses
      Copy on a file selection.

    Paths that don't look like either are passed through verbatim so
    the caller's path-expansion stage can still try ``Path`` on them.
    """
    paths: list[str] = []
    for line in text.splitlines():
        line = line.strip().strip('"').strip("'")
        if not line:
            continue
        if line.lower().startswith("file://"):
            parsed = urlparse(line)
            decoded = unquote(parsed.path)
            # On Windows, file:///C:/foo becomes '/C:/foo'; strip the
            # leading slash before the drive letter in that case.
            if len(decoded) >= 3 and decoded[0] == "/" and decoded[2] == ":":
                decoded = decoded[1:]
            if decoded:
                paths.append(decoded)
            continue
        paths.append(line)
    return paths
