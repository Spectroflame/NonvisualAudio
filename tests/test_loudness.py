from nonvisualaudio.analysis.loudness import _parse


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
