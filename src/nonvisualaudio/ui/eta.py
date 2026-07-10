"""Remaining-time (ETA) estimation for the analysis progress display.

Pure logic split out of the main window so it can be unit-tested
without a running wx app: :func:`format_eta` renders a number of
seconds as a screen-reader-friendly phrase, and :class:`EtaEstimator`
turns raw progress percentages into a smoothed remaining-time label.
"""

from __future__ import annotations

import time

from nonvisualaudio.localization import t


def format_eta(seconds: float) -> str:
    """Render the remaining-time estimate for screen-reader output.

    Buckets it into "less than 30 s" / "seconds" / "minutes" /
    "hours and minutes" with screen-reader-friendly rounding (5-s
    steps under a minute, 1-min steps under ten, 5-min steps after
    that). Reading "noch ca. 1 Minute 34 Sekunden" aloud every
    few seconds is more noise than help — these buckets keep the
    screen reader's output stable.
    """
    seconds = max(0.0, seconds)
    if seconds < 30:
        return t("ui.progress.eta.under_30s")
    if seconds < 60:
        rounded = int(round(seconds / 10.0) * 10)
        return t("ui.progress.eta.seconds", seconds=rounded)
    if seconds < 600:
        minutes = max(1, int(round(seconds / 60.0)))
        key = (
            "ui.progress.eta.minutes.one"
            if minutes == 1
            else "ui.progress.eta.minutes"
        )
        return t(key, minutes=minutes)
    if seconds < 3600:
        minutes = max(5, int(round(seconds / 300.0)) * 5)
        return t("ui.progress.eta.minutes", minutes=minutes)
    hours = int(seconds // 3600)
    minutes = int(round((seconds - hours * 3600) / 300.0)) * 5
    if minutes >= 60:
        hours += 1
        minutes = 0
    if minutes == 0:
        key = (
            "ui.progress.eta.hours_only.one"
            if hours == 1
            else "ui.progress.eta.hours_only"
        )
        return t(key, hours=hours)
    key = (
        "ui.progress.eta.hours.one" if hours == 1 else "ui.progress.eta.hours"
    )
    return t(key, hours=hours, minutes=minutes)


class EtaEstimator:
    """EMA-smoothed remaining-time estimate for a running analysis.

    ``started_at`` is the monotonic clock when the run began;
    ``eta_seconds`` holds the last smoothed remaining-time estimate
    (None until the progress bar crosses 5 %, since the ratio is
    wildly unstable below that). Both reset when the analysis stops.
    """

    def __init__(self) -> None:
        self.started_at: float | None = None
        self.eta_seconds: float | None = None

    def start(self) -> None:
        """Mark the beginning of a run; clears any previous estimate."""
        self.started_at = time.monotonic()
        self.eta_seconds = None

    def reset(self) -> None:
        """Forget the running estimate (analysis finished or cancelled)."""
        self.started_at = None
        self.eta_seconds = None

    def label_for(self, percent: int) -> str | None:
        """EMA-smoothed remaining-time label, or None if not yet useful.

        Below 5 % the ratio is dominated by start-up cost (ffmpeg launch,
        first reads) and the projection is nonsense; we wait until the
        bar has actually moved. The exponential moving average tames the
        natural jitter that comes from different pipeline stages
        running at different speeds.
        """
        if self.started_at is None or percent < 5:
            return None
        elapsed = time.monotonic() - self.started_at
        if elapsed < 0.5:
            return None
        raw_eta = max(0.0, elapsed / percent * (100 - percent))
        # alpha=0.3 is a moderate smoothing — fast enough that the ETA
        # converges within a few ticks, slow enough that one outlier
        # tick (e.g. a sudden 75→80 % jump when post-decode parallel
        # work finishes) does not yank the displayed value around.
        if self.eta_seconds is None:
            self.eta_seconds = raw_eta
        else:
            self.eta_seconds = 0.7 * self.eta_seconds + 0.3 * raw_eta
        return format_eta(self.eta_seconds)
