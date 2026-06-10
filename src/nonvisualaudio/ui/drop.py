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


def _is_filesystem_metadata(name: str) -> bool:
    """True for filenames the OS writes alongside real files for metadata.

    Catches the AppleDouble ``._foo.wav`` companion files Finder leaves
    next to every actual file when copying onto a non-HFS+ disk (SMB
    share, FAT/exFAT/NTFS USB stick). Those files carry the .wav
    extension but contain extended-attribute / resource-fork payloads,
    not PCM — handing them to the decoder produces a hard crash rather
    than a clean error, because the bytes look like garbage to the
    ffmpeg / soundfile readers.

    Also strips ``.DS_Store`` and any other dotfile-style metadata so
    a dragged folder doesn't pollute the analysis list with hidden
    bookkeeping files. The convention "leading dot = hidden / not
    user-content" holds across macOS, Linux, and the way Finder writes
    onto Windows-formatted volumes.
    """
    return name.startswith(".")


def expand_audio_paths(paths: Iterable[str]) -> list[str]:
    """Return a deduplicated list of audio-file paths.

    - Plain files with a supported extension pass through.
    - Folders are walked recursively; audio files inside are added in
      deterministic (sorted) order so two runs over the same folder
      produce the same list.
    - Unknown extensions and non-existent paths are dropped silently —
      the UI is the right place to flag "nothing importable found".
    - Filesystem-metadata files (``._foo.wav`` AppleDouble companions,
      ``.DS_Store``, anything starting with a dot) are filtered out at
      every entry point — file dialog, drag-and-drop, and clipboard
      paste all route through this function, so the decoder never sees
      a resource-fork blob it would crash on.
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
        except (OSError, ValueError):
            continue
        # The stat calls below can raise on input that was never a real
        # path to begin with — the startup clipboard scan feeds whatever
        # text the user happens to have copied through here, so an
        # overlong "filename" (OSError: File name too long) or an
        # embedded null byte (ValueError) must degrade to "not a file",
        # not to a traceback.
        try:
            is_file = path.is_file()
            is_dir = False if is_file else path.is_dir()
        except (OSError, ValueError):
            log.debug("expand_audio_paths: cannot stat %.120r", raw)
            continue
        if is_file:
            if _has_supported_ext(path) and not _is_filesystem_metadata(path.name):
                _add(path)
            continue
        if is_dir:
            # Sorted walk: os.walk alone does not order file names on
            # all platforms, and we want identical output across runs.
            # Prune hidden directories (``.Spotlight-V100``, ``.Trashes``
            # and friends) so we don't recurse into them.
            for dirpath, dirnames, filenames in os.walk(path):
                dirnames[:] = sorted(
                    d for d in dirnames if not _is_filesystem_metadata(d)
                )
                for name in sorted(filenames):
                    if _is_filesystem_metadata(name):
                        log.debug(
                            "expand_audio_paths: skipping metadata file %s/%s",
                            dirpath,
                            name,
                        )
                        continue
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
