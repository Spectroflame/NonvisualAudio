from nonvisualaudio.reporting.templates import (
    fmt_duration,
    fmt_hz,
    fmt_peak_time,
    fmt_signed,
    heading,
    paragraph,
)


def test_fmt_signed_negative_reads_as_minus():
    assert fmt_signed(-21.4) == "minus 21.4"


def test_fmt_signed_positive_has_no_minus():
    assert fmt_signed(3.2) == "3.2"


def test_fmt_signed_zero():
    assert fmt_signed(0.0) == "0.0"


def test_fmt_hz_switches_to_khz_above_1000():
    assert fmt_hz(480.0) == "480 Hz"
    assert fmt_hz(2500.0) == "2.5 kHz"


def test_fmt_duration_minutes_and_seconds():
    assert fmt_duration(754.0) == "12 minutes 34 seconds"
    assert fmt_duration(42.0) == "42 seconds"


def test_fmt_peak_time_keeps_sub_second_precision():
    # The bug we're fixing: a 0.4 s peak was rounded to "0 seconds",
    # which read as "the peak is at the very start" to the user.
    assert fmt_peak_time(0.4) == "0.4 seconds"
    assert fmt_peak_time(0.0) == "0.0 seconds"


def test_fmt_peak_time_drops_decimals_from_one_second_up():
    # At minute-level granularity tenths are noise — fall back to the
    # whole-second formatting fmt_duration already uses for durations.
    assert fmt_peak_time(133.7) == "2 minutes 14 seconds"
    assert fmt_peak_time(1.0) == "1 seconds"
    assert fmt_peak_time(42.4) == "42 seconds"


def test_heading_is_uppercase_no_markdown():
    h = heading("Loudness Summary")
    assert h == "LOUDNESS SUMMARY"
    assert "#" not in h and "*" not in h


def test_paragraph_adds_terminal_period():
    p = paragraph("hello world", "second sentence")
    assert p == "hello world. second sentence."
