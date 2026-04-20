"""Main application window."""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger("nonvisualaudio.ui")

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from nonvisualaudio.reporting.genre_profiles import GENRES
from nonvisualaudio.ui import a11y
from nonvisualaudio.ui.click_sound import ClickTicker
from nonvisualaudio.ui.genre_dialog import GenreDialog
from nonvisualaudio.ui.results_dialog import ResultsDialog
from nonvisualaudio.ui.worker import start_analysis

SUPPORTED_FILTER = (
    "Audio files (*.wav *.aiff *.aif *.mp3 *.m4a *.aac *.flac *.ogg *.opus);;"
    "All files (*)"
)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("NonvisualAudio")
        self.resize(760, 560)

        self._target_paths: list[str] = []
        self._reference_path: str | None = None
        self._thread = None  # keep a reference
        self._click_ticker = ClickTicker(self)
        self._results_dialog: ResultsDialog | None = None

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setSpacing(12)

        intro = QLabel(
            "Pick one or more audio files to analyze. Optionally choose "
            "one or more genre references, a reference file, or both. "
            "Press Analyze to run. The report opens in a separate window."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        # ----- Target file picker (multiple files) -----
        target_row = QHBoxLayout()
        self.open_btn = QPushButton("Add Audio Files...")
        a11y.set_a11y(self.open_btn, a11y.LABEL_OPEN_FILE, a11y.HINT_OPEN_FILE)
        self.open_btn.clicked.connect(self._on_add_targets)
        self.clear_targets_btn = QPushButton("Clear Files")
        self.clear_targets_btn.setAccessibleName("Clear selected audio files")
        self.clear_targets_btn.setAccessibleDescription(
            "Remove all files from the list so you can start fresh."
        )
        self.clear_targets_btn.clicked.connect(self._on_clear_targets)
        self.clear_targets_btn.setEnabled(False)
        target_row.addWidget(self.open_btn)
        target_row.addWidget(self.clear_targets_btn)
        target_row.addStretch(1)
        root.addLayout(target_row)

        # A read-only text area is used instead of a QListWidget because
        # QListWidget is not reliably navigable with VoiceOver on macOS:
        # the cursor jumps around and items often are not announced. A
        # plain text view lets any screen reader read the file names
        # line by line with the usual reading keys.
        self.targets_view = QPlainTextEdit()
        self.targets_view.setReadOnly(True)
        self.targets_view.setAccessibleName("Selected audio files")
        self.targets_view.setAccessibleDescription(
            "Read-only list of audio files that will be analyzed. "
            "Each file produces its own section in the report. "
            "Use Clear Files to start over."
        )
        self.targets_view.setMinimumHeight(90)
        self.targets_view.setMaximumHeight(140)
        self._refresh_targets_view()
        root.addWidget(self.targets_view)

        # ----- Genre picker (opens a dialog) -----
        genre_row = QHBoxLayout()
        self.genre_btn = QPushButton("Choose Genres...")
        a11y.set_a11y(self.genre_btn, a11y.LABEL_GENRE_PICKER, a11y.HINT_GENRE_PICKER)
        self.genre_btn.clicked.connect(self._on_choose_genre)
        self.genre_value_label = QLabel()
        self.genre_value_label.setAccessibleName("Selected genres")
        self._selected_genre_keys: list[str] = []
        self._update_genre_label()
        genre_row.addWidget(self.genre_btn)
        genre_row.addWidget(self.genre_value_label, stretch=1)
        root.addLayout(genre_row)

        # ----- Reference file picker (optional) -----
        ref_row = QHBoxLayout()
        self.reference_btn = QPushButton("Choose Reference File...")
        a11y.set_a11y(self.reference_btn, a11y.LABEL_REFERENCE_FILE, a11y.HINT_REFERENCE_FILE)
        self.reference_btn.clicked.connect(self._on_open_reference)
        self.reference_label = QLabel("No reference file selected.")
        self.reference_label.setAccessibleName("Selected reference file")
        self.clear_reference_btn = QPushButton("Clear Reference")
        self.clear_reference_btn.setAccessibleName("Clear reference file")
        self.clear_reference_btn.setAccessibleDescription(
            "Remove the currently selected reference file so the report does not include a reference comparison."
        )
        self.clear_reference_btn.clicked.connect(self._on_clear_reference)
        self.clear_reference_btn.setEnabled(False)
        ref_row.addWidget(self.reference_btn)
        ref_row.addWidget(self.reference_label, stretch=1)
        ref_row.addWidget(self.clear_reference_btn)
        root.addLayout(ref_row)

        # ----- Analyze button -----
        self.analyze_btn = QPushButton("Analyze")
        a11y.set_a11y(self.analyze_btn, a11y.LABEL_ANALYZE, a11y.HINT_ANALYZE)
        self.analyze_btn.setEnabled(False)
        self.analyze_btn.clicked.connect(self._on_analyze)
        root.addWidget(self.analyze_btn)

        # ----- Progress bar -----
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setAccessibleName("Analysis progress")
        self.progress_bar.setAccessibleDescription(
            "Progress of the running analysis, from zero to one hundred percent."
        )
        self.progress_bar.setVisible(False)
        root.addWidget(self.progress_bar)

        # Stretch fills the bottom so everything sits at the top.
        root.addStretch(1)

        # ----- Status bar -----
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status.showMessage(a11y.STATUS_IDLE)

        # ----- Keyboard shortcuts -----
        QShortcut(QKeySequence.StandardKey.Open, self, self._on_add_targets)
        QShortcut(QKeySequence("Ctrl+R"), self, self._on_analyze)

        self.open_btn.setFocus()

    # ------------------------------------------------------------------ #
    # Target files
    # ------------------------------------------------------------------ #

    def _on_add_targets(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Add audio files", "", SUPPORTED_FILTER
        )
        if not paths:
            log.debug("add targets: cancelled")
            return
        added = 0
        for path in paths:
            if path in self._target_paths:
                continue
            self._target_paths.append(path)
            added += 1
        log.info("added %d target(s); total now %d", added, len(self._target_paths))
        self._refresh_targets_view()
        self._update_analyze_state()

    def _on_clear_targets(self) -> None:
        self._target_paths.clear()
        log.info("targets cleared")
        self._refresh_targets_view()
        self._update_analyze_state()

    def _refresh_targets_view(self) -> None:
        if not self._target_paths:
            self.targets_view.setPlainText(
                "No audio files selected. Click Add Audio Files to choose one or more."
            )
            self.targets_view.setAccessibleDescription(
                "No audio files selected. Click Add Audio Files to choose one or more."
            )
            return
        lines = [
            f"{i + 1}. {Path(p).name}"
            for i, p in enumerate(self._target_paths)
        ]
        count = len(lines)
        header = (
            f"{count} audio file selected:"
            if count == 1
            else f"{count} audio files selected:"
        )
        text = header + "\n" + "\n".join(lines)
        self.targets_view.setPlainText(text)
        self.targets_view.setAccessibleDescription(text)

    # ------------------------------------------------------------------ #
    # Genres
    # ------------------------------------------------------------------ #

    def _update_genre_label(self) -> None:
        names = [
            GENRES[k].display_name
            for k in self._selected_genre_keys
            if k in GENRES
        ]
        if not names:
            self.genre_value_label.setText("Standalone (no genre selected)")
            self.genre_value_label.setAccessibleDescription(
                "Standalone analysis. No genre references selected. "
                "Open the Choose Genres dialog to add one or more."
            )
            return
        if len(names) == 1:
            text = names[0]
        else:
            text = f"{names[0]} and {len(names) - 1} more"
        self.genre_value_label.setText(text)
        self.genre_value_label.setAccessibleDescription(
            f"Selected genres: {', '.join(names)}."
        )

    def _on_choose_genre(self) -> None:
        dialog = GenreDialog(self, selected_keys=self._selected_genre_keys)
        if dialog.exec() != GenreDialog.DialogCode.Accepted:
            return
        self._selected_genre_keys = dialog.selected_keys()
        self._update_genre_label()
        self._update_analyze_state()
        log.info("genres selected: %s", self._selected_genre_keys)

    # ------------------------------------------------------------------ #
    # Reference file
    # ------------------------------------------------------------------ #

    def _on_open_reference(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open reference audio file", "", SUPPORTED_FILTER
        )
        if not path:
            return
        log.info("reference selected: %s", path)
        self._reference_path = path
        self.reference_label.setText(Path(path).name)
        self.reference_label.setAccessibleDescription(
            f"Reference file: {Path(path).name}"
        )
        self.clear_reference_btn.setEnabled(True)

    def _on_clear_reference(self) -> None:
        self._reference_path = None
        self.reference_label.setText("No reference file selected.")
        self.reference_label.setAccessibleDescription(
            "No reference file selected."
        )
        self.clear_reference_btn.setEnabled(False)
        log.info("reference cleared")

    # ------------------------------------------------------------------ #
    # Analyze flow
    # ------------------------------------------------------------------ #

    def _update_analyze_state(self) -> None:
        has_targets = bool(self._target_paths)
        self.analyze_btn.setEnabled(has_targets)
        self.clear_targets_btn.setEnabled(has_targets)

    def _on_analyze(self) -> None:
        if not self._target_paths:
            return
        self.analyze_btn.setEnabled(False)
        self.open_btn.setEnabled(False)
        self.status.showMessage(a11y.STATUS_RUNNING)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("Starting... %p%")
        self.progress_bar.setVisible(True)
        self._click_ticker.start()
        genre_keys = self._selected_genre_keys or None
        log.info(
            "analyze clicked: %d file(s) genres=%s reference=%s",
            len(self._target_paths),
            genre_keys,
            self._reference_path,
        )
        self._thread = start_analysis(
            self,
            self._target_paths,
            genre_keys,
            self._reference_path,
            self._on_analysis_done,
            self._on_analysis_failed,
            self._on_analysis_progress,
        )

    def _on_analysis_progress(self, percent: int, stage: str) -> None:
        self.progress_bar.setValue(percent)
        self.progress_bar.setFormat(f"{stage}... %p%")
        self.progress_bar.setAccessibleDescription(
            f"Analysis progress: {stage}, {percent} percent."
        )
        self.status.showMessage(f"{stage}... {percent}%")

    def _stop_running_ui(self) -> None:
        self._click_ticker.stop()
        self.progress_bar.setVisible(False)
        self.progress_bar.setValue(0)
        self.open_btn.setEnabled(True)
        self._update_analyze_state()

    def _on_analysis_done(self, report: str) -> None:
        log.info("analysis done, %d chars delivered to UI", len(report))
        self._stop_running_ui()
        self.status.showMessage(a11y.STATUS_DONE)
        self._show_results(report)

    def _on_analysis_failed(self, message: str) -> None:
        log.error(
            "analysis failed: %s",
            message.splitlines()[0] if message else "(no message)",
        )
        self._stop_running_ui()
        self.status.showMessage(a11y.STATUS_ERROR)
        QMessageBox.critical(self, "Analysis failed", message)

    def _show_results(self, report: str) -> None:
        # Close any previous results window so we don't pile them up.
        if self._results_dialog is not None:
            self._results_dialog.close()
            self._results_dialog = None
        dialog = ResultsDialog(self, report_text=report)
        self._results_dialog = dialog
        # exec() runs a nested event loop and returns when the dialog is
        # closed. Modal show + exec is the only combination that reliably
        # gives the focus (and the VoiceOver cursor) to the report text
        # on macOS.
        dialog.exec()
        self._results_dialog = None
        log.info("results window closed, focus returning to main window")
        self.activateWindow()
        self.raise_()
        # Focus the analyze button so a screen reader announces the main
        # window is active and the user is positioned to run another
        # analysis immediately.
        (self.analyze_btn if self.analyze_btn.isEnabled() else self.open_btn).setFocus(
            Qt.FocusReason.OtherFocusReason
        )
