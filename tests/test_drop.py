"""Unit tests for the drop/paste path helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from nonvisualaudio.ui.drop import (
    SUPPORTED_EXTS,
    expand_audio_paths,
    parse_paste_text,
)


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    return path


def test_supported_exts_includes_wma():
    assert ".wma" in SUPPORTED_EXTS
    assert ".mp3" in SUPPORTED_EXTS
    assert ".wav" in SUPPORTED_EXTS


# --------------------------------------------------------------------------- #
# expand_audio_paths
# --------------------------------------------------------------------------- #


def test_expand_accepts_plain_audio_files(tmp_path: Path):
    a = _touch(tmp_path / "one.wav")
    b = _touch(tmp_path / "two.mp3")
    result = expand_audio_paths([str(a), str(b)])
    assert result == [str(a), str(b)]


def test_expand_drops_unknown_extensions(tmp_path: Path):
    audio = _touch(tmp_path / "song.flac")
    text = _touch(tmp_path / "notes.txt")
    result = expand_audio_paths([str(audio), str(text)])
    assert result == [str(audio)]


def test_expand_folder_recursively(tmp_path: Path):
    sub = tmp_path / "album"
    deep = sub / "bonus"
    a = _touch(sub / "01.wav")
    b = _touch(sub / "02.mp3")
    c = _touch(deep / "03.flac")
    junk = _touch(sub / "cover.jpg")

    result = expand_audio_paths([str(sub)])
    assert set(result) == {str(a), str(b), str(c)}
    assert str(junk) not in result


def test_expand_dedups_across_inputs(tmp_path: Path):
    a = _touch(tmp_path / "song.wav")
    sub = tmp_path / "folder"
    b = _touch(sub / "song.mp3")

    # Passing the same file twice, plus a folder that contains another
    # file — should appear once each, in first-seen order.
    result = expand_audio_paths([str(a), str(a), str(sub)])
    assert result == [str(a), str(b)]


def test_expand_ignores_missing_paths(tmp_path: Path):
    ghost = tmp_path / "does-not-exist.wav"
    real = _touch(tmp_path / "real.wav")
    result = expand_audio_paths([str(ghost), str(real)])
    assert result == [str(real)]


def test_expand_includes_wma(tmp_path: Path):
    wma = _touch(tmp_path / "legacy.wma")
    result = expand_audio_paths([str(wma)])
    assert result == [str(wma)]


def test_expand_walks_folder_in_deterministic_order(tmp_path: Path):
    # Create files in "unsorted" insertion order; result must be sorted.
    c = _touch(tmp_path / "c.wav")
    a = _touch(tmp_path / "a.wav")
    b = _touch(tmp_path / "b.wav")
    result = expand_audio_paths([str(tmp_path)])
    assert result == [str(a), str(b), str(c)]


def test_expand_ignores_empty_strings():
    assert expand_audio_paths(["", "   "]) == []


# --------------------------------------------------------------------------- #
# Filesystem-metadata filter
#
# macOS Finder writes an AppleDouble "._foo.wav" next to every actual file
# when copying onto a non-HFS+ filesystem (SMB share, FAT/exFAT/NTFS stick).
# Those files carry the .wav extension but hold extended-attribute payloads,
# so passing them to the decoder used to crash the app. Every import path
# (file dialog, drag-and-drop, clipboard paste) funnels through
# ``expand_audio_paths``, so a single filter here covers all three.
# --------------------------------------------------------------------------- #


def test_expand_drops_apple_double_companion_files(tmp_path: Path):
    real = _touch(tmp_path / "song.wav")
    junk = _touch(tmp_path / "._song.wav")
    result = expand_audio_paths([str(real), str(junk)])
    assert result == [str(real)]
    assert str(junk) not in result


def test_expand_drops_ds_store_inside_dropped_folder(tmp_path: Path):
    real = _touch(tmp_path / "song.wav")
    _touch(tmp_path / ".DS_Store")
    result = expand_audio_paths([str(tmp_path)])
    assert result == [str(real)]


def test_expand_drops_apple_double_files_from_walked_folder(tmp_path: Path):
    # A dragged-in folder that was previously copied over SMB. Every real
    # ".wav" has an "._" twin sitting next to it.
    real_a = _touch(tmp_path / "a.wav")
    real_b = _touch(tmp_path / "b.mp3")
    _touch(tmp_path / "._a.wav")
    _touch(tmp_path / "._b.mp3")
    _touch(tmp_path / ".DS_Store")

    result = expand_audio_paths([str(tmp_path)])
    assert set(result) == {str(real_a), str(real_b)}
    for name in result:
        assert "/._" not in name and not name.endswith("/.DS_Store"), (
            f"metadata file leaked into result: {name}"
        )


def test_expand_prunes_hidden_directories(tmp_path: Path):
    # Spotlight / Trashes / Apple* directories appear at the root of
    # FAT-formatted volumes the user might drag in. We must not recurse
    # into them — that path eventually hits PermissionError on a real
    # macOS install and slows the walk down to a crawl regardless.
    real = _touch(tmp_path / "song.wav")
    _touch(tmp_path / ".Spotlight-V100" / "buried.wav")
    _touch(tmp_path / ".Trashes" / "deleted.mp3")
    result = expand_audio_paths([str(tmp_path)])
    assert result == [str(real)]


def test_expand_drops_apple_double_when_named_explicitly(tmp_path: Path):
    # Even when the user (or a stale clipboard) passes the "._foo.wav"
    # file path directly, the filter still kicks in. This is the file-
    # dialog edge case: macOS Finder hides these files by default, but
    # a power user with "show hidden" turned on could still select one.
    junk = _touch(tmp_path / "._broken.wav")
    assert expand_audio_paths([str(junk)]) == []


# --------------------------------------------------------------------------- #
# parse_paste_text
# --------------------------------------------------------------------------- #


def test_parse_plain_path_lines():
    text = "/Users/me/song.wav\n/Users/me/other.mp3"
    assert parse_paste_text(text) == [
        "/Users/me/song.wav",
        "/Users/me/other.mp3",
    ]


def test_parse_strips_quotes():
    text = '"/Users/me/song with space.wav"\n\'/Users/me/another.mp3\''
    assert parse_paste_text(text) == [
        "/Users/me/song with space.wav",
        "/Users/me/another.mp3",
    ]


def test_parse_ignores_empty_lines():
    text = "\n/Users/me/song.wav\n\n\n"
    assert parse_paste_text(text) == ["/Users/me/song.wav"]


def test_parse_file_uri_unix():
    text = "file:///Users/me/song%20one.wav\nfile:///tmp/track.mp3"
    assert parse_paste_text(text) == [
        "/Users/me/song one.wav",
        "/tmp/track.mp3",
    ]


def test_parse_file_uri_windows():
    text = "file:///C:/Music/song.wav"
    assert parse_paste_text(text) == ["C:/Music/song.wav"]


def test_parse_mixed_formats():
    text = "file:///home/u/a.wav\n/home/u/b.mp3\n"
    assert parse_paste_text(text) == ["/home/u/a.wav", "/home/u/b.mp3"]


def test_expand_survives_overlong_clipboard_text(tmp_path: Path):
    # Regression: the startup clipboard scan feeds arbitrary copied
    # text through expand_audio_paths. A long prose paragraph is one
    # giant "filename" component that exceeds the OS limit (~255
    # bytes), and the is_file() stat used to escape as
    # OSError: [Errno 63] File name too long.
    overlong = "ein sehr langer kopierter Fliesstext " * 20
    assert expand_audio_paths([overlong]) == []

    # Garbage next to a real file must not take the real file down.
    real = tmp_path / "take.wav"
    real.write_bytes(b"x")
    assert expand_audio_paths([overlong, str(real)]) == [str(real)]


def test_expand_survives_null_byte_in_path():
    # An embedded null byte makes os.stat raise ValueError instead of
    # OSError; both must degrade to "not a file", not a traceback.
    assert expand_audio_paths(["bad\x00path.wav"]) == []
