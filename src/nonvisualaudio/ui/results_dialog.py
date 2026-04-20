"""Modal dialog that shows the analysis report.

The main window stays focused on setup (file pickers, genre choice,
analyze button). Each analysis opens this dialog so the user can read
the report, copy it, then close the dialog and start another analysis.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import (
    QAccessible,
    QAccessibleEvent,
    QGuiApplication,
    QKeySequence,
    QShortcut,
)

try:
    from PySide6.QtGui import QAccessibleAnnouncementEvent  # Qt 6.8+
except ImportError:  # pragma: no cover — graceful fallback
    QAccessibleAnnouncementEvent = None  # type: ignore[assignment]
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from nonvisualaudio.ui import a11y


class ResultsDialog(QDialog):
    """Shows a finished analysis report in its own window."""

    def __init__(self, parent=None, report_text: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle("Analysis Results — NonvisualAudio")
        # Modal — non-modal child windows do not reliably hand focus
        # to VoiceOver on macOS, so the screen reader would stay in the
        # main window and not start reading the report. A modal dialog
        # forces the activation, and the user still returns to the main
        # window naturally when it is closed.
        self.setModal(True)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.resize(760, 620)
        self.setAccessibleName("Analysis results")
        self.setAccessibleDescription(
            "The full analysis report. Press Control Shift C to copy. "
            "Press Escape to close and return to the main window."
        )

        root = QVBoxLayout(self)
        # No intro label — keeps the first focusable and first-announced
        # widget the results text, so VoiceOver starts on the report.

        self.results = QPlainTextEdit()
        self.results.setReadOnly(True)
        self.results.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        a11y.set_a11y(self.results, a11y.LABEL_RESULTS, a11y.HINT_RESULTS)
        self.results.setPlainText(report_text)
        # Put the cursor at the start immediately so line 1 is the
        # reading starting point.
        cursor = self.results.textCursor()
        cursor.movePosition(cursor.MoveOperation.Start)
        self.results.setTextCursor(cursor)
        root.addWidget(self.results, stretch=1)

        # Proxy the dialog's own focus to the results text. Any attempt
        # to give the dialog focus (Qt's default on show, macOS window
        # activation, etc.) ends up in the text instead of on a button
        # or on the window chrome.
        self.setFocusProxy(self.results)

        button_row = QHBoxLayout()
        self.copy_btn = QPushButton("Copy Report")
        a11y.set_a11y(self.copy_btn, a11y.LABEL_COPY)
        self.copy_btn.clicked.connect(self._on_copy)
        # Do NOT set setDefault on the buttons: on macOS a default push
        # button can grab the accessibility focus on window show, which
        # would pull VoiceOver away from the report text.
        self.copy_btn.setAutoDefault(False)
        button_row.addWidget(self.copy_btn)

        self.close_btn = QPushButton("Close")
        self.close_btn.setAccessibleName("Close results window")
        self.close_btn.setAccessibleDescription(
            "Close this window and return to the main NonvisualAudio window."
        )
        self.close_btn.setAutoDefault(False)
        self.close_btn.clicked.connect(self.close)
        button_row.addStretch(1)
        button_row.addWidget(self.close_btn)
        root.addLayout(button_row)

        # Shortcuts: ⌘/Ctrl+Shift+C to copy, Escape to close.
        QShortcut(QKeySequence("Ctrl+Shift+C"), self, self._on_copy)
        QShortcut(QKeySequence("Escape"), self, self.close)

    # ------------------------------------------------------------------ #
    # Focus handling
    # ------------------------------------------------------------------ #

    def _focus_into_results(self) -> None:
        """Move keyboard focus AND the screen-reader cursor into the report.

        Qt's keyboard focus and macOS VoiceOver's cursor are two
        separate things. Just calling ``setFocus`` on a widget moves
        the keyboard focus but does not always push a notification to
        the OS accessibility layer that tells VoiceOver to re-point.
        We therefore also post a ``QAccessibleEvent(Focus)`` so Qt
        bridges the change into NSAccessibility and VoiceOver jumps
        into the text.
        """
        self.results.setFocus(Qt.FocusReason.OtherFocusReason)
        try:
            event = QAccessibleEvent(self.results, QAccessible.Event.Focus)
            QAccessible.updateAccessibility(event)
        except Exception:
            # The a11y bridge is best-effort; keyboard focus alone is
            # still correct even if this call fails on a given platform.
            pass

    def _announce_report(self) -> None:
        """Ask the screen reader to actively start speaking the report.

        Focus alone is not enough on macOS — VoiceOver stops at the
        focused widget and waits for a reading command. Posting a
        ``QAccessibleAnnouncementEvent`` with the first lines of the
        report bridges to ``NSAccessibilityAnnouncementRequestedNotification``
        on macOS, which VoiceOver speaks unconditionally. That gives
        the user an immediate "the report is here and this is what it
        starts with" read-out; from there they can continue navigating
        the text with the usual reading keys.
        """
        if QAccessibleAnnouncementEvent is None:
            return
        text = self.results.toPlainText()
        if not text:
            return
        # Announce the first few lines so the user hears the file info
        # and the first loudness numbers without pressing anything.
        lines = [ln for ln in text.splitlines() if ln.strip()]
        announcement = "Analysis complete. " + " ".join(lines[:8])
        # Cap the length — macOS truncates very long announcements anyway.
        if len(announcement) > 600:
            announcement = announcement[:600].rsplit(" ", 1)[0] + "..."
        try:
            event = QAccessibleAnnouncementEvent(self.results, announcement)
            QAccessible.updateAccessibility(event)
        except Exception:
            pass

    def showEvent(self, event) -> None:  # noqa: N802 — Qt override
        super().showEvent(event)
        self._focus_into_results()
        # Also re-assert after the event loop has processed the show
        # and the OS has activated the window, in case Qt's own
        # first-focus logic runs right after our call.
        QTimer.singleShot(0, self._focus_into_results)
        QTimer.singleShot(100, self._focus_into_results)
        # Fire the announcement with a short delay so VoiceOver has
        # already processed the window activation; an announcement
        # posted too early is sometimes dropped by NSAccessibility.
        QTimer.singleShot(150, self._announce_report)

    def changeEvent(self, event) -> None:  # noqa: N802 — Qt override
        """Re-assert focus whenever the window becomes active.

        macOS fires ActivationChange after a window is put in front,
        which is the moment VoiceOver starts scanning for the focused
        element. Pushing focus at that exact moment is what finally
        lets the screen reader jump straight into the report text.
        """
        super().changeEvent(event)
        if event.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            self._focus_into_results()

    # ------------------------------------------------------------------ #
    # Copy
    # ------------------------------------------------------------------ #

    def _on_copy(self) -> None:
        text = self.results.toPlainText()
        if not text:
            return
        QGuiApplication.clipboard().setText(text)
        self.copy_btn.setAccessibleDescription("Results copied to clipboard.")
