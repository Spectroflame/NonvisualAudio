"""Unit tests for the remaining-time (ETA) estimation helpers.

The formatting buckets and the EMA smoothing used to live inside the
main window and were only exercised indirectly through the wx-driven
progress-display tests. Now that they are a pure module, this file
pins down the bucket boundaries and the estimator's warm-up and
smoothing behaviour without a wx app.
"""

from __future__ import annotations

import time

from nonvisualaudio.localization import t
from nonvisualaudio.ui.eta import EtaEstimator, format_eta


# --------------------------------------------------------------------------- #
# format_eta buckets
# --------------------------------------------------------------------------- #


def test_under_30_seconds_is_one_stable_phrase() -> None:
    assert format_eta(0.0) == t("ui.progress.eta.under_30s")
    assert format_eta(29.9) == t("ui.progress.eta.under_30s")


def test_negative_input_is_clamped_to_under_30s() -> None:
    assert format_eta(-5.0) == t("ui.progress.eta.under_30s")


def test_sub_minute_rounds_to_ten_second_steps() -> None:
    assert format_eta(44.0) == t("ui.progress.eta.seconds", seconds=40)
    assert format_eta(46.0) == t("ui.progress.eta.seconds", seconds=50)


def test_minutes_bucket_rounds_to_whole_minutes() -> None:
    assert format_eta(60.0) == t("ui.progress.eta.minutes.one", minutes=1)
    assert format_eta(150.0) == t("ui.progress.eta.minutes", minutes=2)
    assert format_eta(599.0) == t("ui.progress.eta.minutes", minutes=10)


def test_over_ten_minutes_rounds_to_five_minute_steps() -> None:
    assert format_eta(600.0) == t("ui.progress.eta.minutes", minutes=10)
    assert format_eta(1000.0) == t("ui.progress.eta.minutes", minutes=15)
    # Never rounds below the bucket's own floor of 5 minutes.
    assert format_eta(601.0) == t("ui.progress.eta.minutes", minutes=10)


def test_hours_bucket_with_and_without_minutes() -> None:
    assert format_eta(3600.0) == t("ui.progress.eta.hours_only.one", hours=1)
    assert format_eta(3600.0 + 600.0) == t(
        "ui.progress.eta.hours.one", hours=1, minutes=10
    )
    assert format_eta(2 * 3600.0) == t("ui.progress.eta.hours_only", hours=2)


def test_hours_minute_overflow_rolls_into_next_hour() -> None:
    # 1 h 58 min rounds the minutes to 60, which must become "2 hours",
    # never "1 hour 60 minutes".
    assert format_eta(3600.0 + 58 * 60.0) == t(
        "ui.progress.eta.hours_only", hours=2
    )


# --------------------------------------------------------------------------- #
# EtaEstimator
# --------------------------------------------------------------------------- #


def test_no_label_before_start() -> None:
    est = EtaEstimator()
    assert est.label_for(50) is None


def test_no_label_below_five_percent() -> None:
    est = EtaEstimator()
    est.started_at = time.monotonic() - 10.0
    assert est.label_for(4) is None
    assert est.label_for(5) is not None


def test_no_label_in_first_half_second() -> None:
    est = EtaEstimator()
    est.started_at = time.monotonic()  # just started
    assert est.label_for(50) is None


def test_first_tick_seeds_estimate_second_tick_smooths() -> None:
    est = EtaEstimator()
    # 10 s elapsed at 50 % projects 10 s remaining.
    est.started_at = time.monotonic() - 10.0
    est.label_for(50)
    first = est.eta_seconds
    assert first is not None
    assert abs(first - 10.0) < 0.5

    # Same elapsed time at 90 % projects ~1.1 s raw — the EMA must land
    # between the previous estimate and the new raw value.
    est.label_for(90)
    second = est.eta_seconds
    assert second is not None
    assert second < first
    assert second > 10.0 / 9.0 - 0.5


def test_start_and_reset_clear_state() -> None:
    est = EtaEstimator()
    est.started_at = time.monotonic() - 10.0
    est.label_for(50)
    assert est.eta_seconds is not None

    est.start()
    assert est.started_at is not None
    assert est.eta_seconds is None

    est.reset()
    assert est.started_at is None
    assert est.eta_seconds is None
