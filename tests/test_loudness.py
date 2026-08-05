import pytest

from nonvisualaudio.analysis import loudness
from nonvisualaudio.analysis.loudness import _parse
from nonvisualaudio.audio.ffmpeg_runner import FFmpegError
from nonvisualaudio.errors import LoudnessMeasurementError


def test_measure_loudness_preserves_timeout_error_translation(monkeypatch) -> None:
    monkeypatch.setattr(loudness, "find_ffmpeg", lambda: "ffmpeg")

    def _timeout(*_args, **_kwargs):
        raise FFmpegError("timeout:1200.0")

    monkeypatch.setattr(loudness, "run_split_streams", _timeout)

    with pytest.raises(LoudnessMeasurementError) as exc_info:
        loudness.measure_loudness("example.wav")

    error = exc_info.value
    assert error.title == "Loudness scan of example.wav took too long"
    assert error.body == (
        "The audio engine did not finish the EBU R128 loudness scan within "
        "the allowed time. The file may be extremely long or stored on a slow "
        "drive."
    )
    assert error.hint == (
        "Copy the file to a local drive or trim it to a shorter segment."
    )


SAMPLE_PROGRESS = """\
[Parsed_ebur128_0 @ 0x123] t: 0.1   TARGET:-23 LUFS    M:-120.7 S:-120.7     I: -70.0 LUFS       LRA:   0.0 LU  FTPK: -38.1 dBFS  TPK: -38.1 dBFS
[Parsed_ebur128_0 @ 0x123] t: 3.0   TARGET:-23 LUFS    M: -14.2 S: -16.8     I: -18.1 LUFS       LRA:   2.3 LU  FTPK: -2.0 dBFS   TPK: -2.0 dBFS
[Parsed_ebur128_0 @ 0x123] t: 6.0   TARGET:-23 LUFS    M: -12.0 S: -13.4     I: -17.0 LUFS       LRA:   3.1 LU  FTPK: -1.5 dBFS   TPK: -1.5 dBFS
[Parsed_ebur128_0 @ 0x123] t: 9.0   TARGET:-23 LUFS    M: -15.0 S: -15.5     I: -17.5 LUFS       LRA:   3.0 LU  FTPK: -1.5 dBFS   TPK: -1.5 dBFS
[Parsed_ebur128_0 @ 0x123] Summary:

  Integrated loudness:
    I:         -17.5 LUFS
    Threshold: -27.5 LUFS

  Loudness range:
    LRA:         3.0 LU
    Threshold: -37.5 LUFS
    LRA low:   -20.0 LUFS
    LRA high:  -17.0 LUFS

  True peak:
    Peak:       -1.5 dBFS
"""


def test_short_term_max_is_parsed_independently_of_integrated():
    m = _parse(SAMPLE_PROGRESS, "fake.wav")
    assert m.integrated_lufs == -17.5
    # Loudest short-term in the progress is -13.4, not the integrated -17.5.
    assert m.short_term_max_lufs == -13.4
    assert m.true_peak_dbtp == -1.5
    assert m.loudness_range_lu == 3.0


def test_short_term_regex_handles_missing_space_after_colon():
    # ffmpeg can emit "S:-120.7" (no space) at the very start before any audio
    # has been processed. Make sure we still find real values afterwards.
    progress = (
        "[Parsed_ebur128_0 @ 0x1] t: 0.1 M:-120.7 S:-120.7 I: -70.0 LUFS LRA: 0.0 LU\n"
        "[Parsed_ebur128_0 @ 0x1] t: 2.0 M: -10.0 S: -11.0 I: -12.0 LUFS LRA: 1.0 LU\n"
        "[Parsed_ebur128_0 @ 0x1] Summary:\n"
        "  Integrated loudness:\n"
        "    I:         -12.0 LUFS\n"
        "    Threshold: -22.0 LUFS\n"
        "  Loudness range:\n"
        "    LRA:         1.0 LU\n"
        "    Threshold: -32.0 LUFS\n"
        "    LRA low:   -13.0 LUFS\n"
        "    LRA high:  -12.0 LUFS\n"
        "  True peak:\n"
        "    Peak:       -1.0 dBFS\n"
    )
    m = _parse(progress, "fake.wav")
    assert m.short_term_max_lufs == -11.0


def test_falls_back_to_integrated_when_no_progress_short_term():
    only_summary = (
        "[Parsed_ebur128_0 @ 0x1] Summary:\n"
        "  Integrated loudness:\n"
        "    I:         -23.0 LUFS\n"
        "    Threshold: -33.0 LUFS\n"
        "  Loudness range:\n"
        "    LRA:         5.0 LU\n"
        "    Threshold: -43.0 LUFS\n"
        "    LRA low:   -28.0 LUFS\n"
        "    LRA high:  -20.0 LUFS\n"
        "  True peak:\n"
        "    Peak:       -2.0 dBFS\n"
    )
    m = _parse(only_summary, "fake.wav")
    assert m.integrated_lufs == -23.0
    assert m.short_term_max_lufs == -23.0


def test_true_peak_timestamp_is_the_first_loudest_ftpk_frame():
    m = _parse(SAMPLE_PROGRESS, "fake.wav")
    # FTPK peaks at -1.5 dBFS; that value is reached first at t: 6.0
    # (t: 9.0 ties but the earlier frame must win).
    assert m.true_peak_time_seconds == 6.0


def test_true_peak_timestamp_is_none_without_progress_frames():
    only_summary = (
        "[Parsed_ebur128_0 @ 0x1] Summary:\n"
        "  Integrated loudness:\n"
        "    I:         -23.0 LUFS\n"
        "    Threshold: -33.0 LUFS\n"
        "  Loudness range:\n"
        "    LRA:         5.0 LU\n"
        "    Threshold: -43.0 LUFS\n"
        "    LRA low:   -28.0 LUFS\n"
        "    LRA high:  -20.0 LUFS\n"
        "  True peak:\n"
        "    Peak:       -2.0 dBFS\n"
    )
    m = _parse(only_summary, "fake.wav")
    assert m.true_peak_time_seconds is None


def test_true_peak_timestamp_uses_loudest_channel_not_just_the_first():
    # Stereo files print one FTPK value per channel ("FTPK: L R dBFS").
    # The overall loudest true peak here is -0.5 dBFS on the RIGHT
    # channel at t: 5.0; the left channel peaks earlier (-1.0 at t: 2.0).
    # The timestamp must follow the loudest channel, not channel 0.
    progress = (
        "[Parsed_ebur128_0 @ 0x1] t: 2.0 M:-10 S:-11 I:-12 LUFS "
        "LRA: 1.0 LU FTPK: -1.0 -20.0 dBFS TPK: -1.0 -20.0 dBFS\n"
        "[Parsed_ebur128_0 @ 0x1] t: 5.0 M:-10 S:-11 I:-12 LUFS "
        "LRA: 1.0 LU FTPK: -20.0 -0.5 dBFS TPK: -1.0 -0.5 dBFS\n"
        "[Parsed_ebur128_0 @ 0x1] Summary:\n"
        "  Integrated loudness:\n"
        "    I:         -12.0 LUFS\n"
        "    Threshold: -22.0 LUFS\n"
        "  Loudness range:\n"
        "    LRA:         1.0 LU\n"
        "    Threshold: -32.0 LUFS\n"
        "    LRA low:   -13.0 LUFS\n"
        "    LRA high:  -12.0 LUFS\n"
        "  True peak:\n"
        "    Peak:       -0.5 dBFS\n"
    )
    m = _parse(progress, "fake.wav")
    assert m.true_peak_time_seconds == 5.0


def test_true_peak_timestamp_skips_inf_channel_but_reads_the_other():
    # One silent channel reports -inf while the other carries a real
    # peak — the -inf token must be skipped without discarding the line.
    progress = (
        "[Parsed_ebur128_0 @ 0x1] t: 1.5 M:-10 S:-11 I:-12 LUFS "
        "LRA: 1.0 LU FTPK: -inf -4.0 dBFS TPK: -inf -4.0 dBFS\n"
        "[Parsed_ebur128_0 @ 0x1] Summary:\n"
        "  Integrated loudness:\n"
        "    I:         -12.0 LUFS\n"
        "    Threshold: -22.0 LUFS\n"
        "  Loudness range:\n"
        "    LRA:         1.0 LU\n"
        "    Threshold: -32.0 LUFS\n"
        "    LRA low:   -13.0 LUFS\n"
        "    LRA high:  -12.0 LUFS\n"
        "  True peak:\n"
        "    Peak:       -4.0 dBFS\n"
    )
    m = _parse(progress, "fake.wav")
    assert m.true_peak_time_seconds == 1.5


def test_true_peak_timestamp_never_reads_the_cumulative_tpk_values():
    # Guard against regex over-capture: the FTPK value run must stop at
    # "dBFS" and never continue into the cumulative "TPK:" field. The
    # TPK values here are deliberately louder than every FTPK reading —
    # if they leaked into the capture, frame t: 2.0 would win with -1.0
    # instead of frame t: 5.0 with the true loudest FTPK of -10.0.
    progress = (
        "[Parsed_ebur128_0 @ 0x1] t: 2.0 M:-10 S:-11 I:-12 LUFS "
        "LRA: 1.0 LU FTPK: -20.0 -20.0 dBFS TPK: -1.0 -1.0 dBFS\n"
        "[Parsed_ebur128_0 @ 0x1] t: 5.0 M:-10 S:-11 I:-12 LUFS "
        "LRA: 1.0 LU FTPK: -10.0 -10.0 dBFS TPK: -1.0 -1.0 dBFS\n"
        "[Parsed_ebur128_0 @ 0x1] Summary:\n"
        "  Integrated loudness:\n"
        "    I:         -12.0 LUFS\n"
        "    Threshold: -22.0 LUFS\n"
        "  Loudness range:\n"
        "    LRA:         1.0 LU\n"
        "    Threshold: -32.0 LUFS\n"
        "    LRA low:   -13.0 LUFS\n"
        "    LRA high:  -12.0 LUFS\n"
        "  True peak:\n"
        "    Peak:       -1.0 dBFS\n"
    )
    m = _parse(progress, "fake.wav")
    assert m.true_peak_time_seconds == 5.0


def test_true_peak_timestamp_ignores_inf_frames():
    # Leading silence reports FTPK: -inf — those frames must not be
    # picked, and must not crash the float() conversion.
    progress = (
        "[Parsed_ebur128_0 @ 0x1] t: 0.1 M:-120 S:-120 I:-70 LUFS "
        "LRA: 0.0 LU FTPK: -inf dBFS TPK: -inf dBFS\n"
        "[Parsed_ebur128_0 @ 0x1] t: 4.2 M:-10 S:-11 I:-12 LUFS "
        "LRA: 1.0 LU FTPK: -3.0 dBFS TPK: -3.0 dBFS\n"
        "[Parsed_ebur128_0 @ 0x1] Summary:\n"
        "  Integrated loudness:\n"
        "    I:         -12.0 LUFS\n"
        "    Threshold: -22.0 LUFS\n"
        "  Loudness range:\n"
        "    LRA:         1.0 LU\n"
        "    Threshold: -32.0 LUFS\n"
        "    LRA low:   -13.0 LUFS\n"
        "    LRA high:  -12.0 LUFS\n"
        "  True peak:\n"
        "    Peak:       -3.0 dBFS\n"
    )
    m = _parse(progress, "fake.wav")
    assert m.true_peak_time_seconds == 4.2
