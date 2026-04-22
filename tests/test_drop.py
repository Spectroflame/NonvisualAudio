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
