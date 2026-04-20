"""Main application window."""

from __future__ import annotations

import logging
from pathlib import Path

import wx

from nonvisualaudio.errors import UserFacingError
from nonvisualaudio.reporting.genre_profiles import GENRES
from nonvisualaudio.ui import a11y
from nonvisualaudio.ui.click_sound import ClickTicker
from nonvisualaudio.ui.error_dialog import show_error
from nonvisualaudio.ui.genre_dialog import GenreDialog
from nonvisualaudio.ui.results_dialog import ResultsDialog
from nonvisualaudio.ui.worker import start_analysis

log = logging.getLogger("nonvisualaudio.ui")

SUPPORTED_WILDCARD = (
    "Audio files (*.wav;*.aiff;*.aif;*.mp3;*.m4a;*.aac;*.flac;*.ogg;*.opus)"
    "|*.wav;*.aiff;*.aif;*.mp3;*.m4a;*.aac;*.flac;*.ogg;*.opus"
    "|All files (*.*)|*.*"
)


class MainWindow(wx.Frame):
    def __init__(self) -> None:
        super().__init__(
            parent=None,
            title="NonvisualAudio",
            size=wx.Size(760, 600),
        )
        self.SetName("NonvisualAudio main window")

        self._target_paths: list[str] = []
        self._reference_path: str | None = None
        self._click_ticker = ClickTicker(self)
        self._worker = None  # keep a reference to the running worker

        panel = wx.Panel(self)
        root = wx.BoxSizer(wx.VERTICAL)

        intro = wx.StaticText(
            panel,
            label=(
                "Pick one or more audio files to analyze. Optionally choose\n"
                "one or more genre references, a reference file, or both.\n"
                'Press "Analyze" to run. The report opens in a separate window.'
            ),
        )
        root.Add(intro, flag=wx.ALL, border=10)

        # ----- Target file picker -----
        target_row = wx.BoxSizer(wx.HORIZONTAL)
        self.open_btn = wx.Button(panel, label="Add Audio Files...")
        a11y.set_a11y(self.open_btn, a11y.LABEL_OPEN_FILE, a11y.HINT_OPEN_FILE)
        self.open_btn.Bind(wx.EVT_BUTTON, self._on_add_targets)
        self.clear_targets_btn = wx.Button(panel, label="Clear Files")
        a11y.set_a11y(
            self.clear_targets_btn,
            "Clear selected audio files",
            "Remove all files from the list so you can start fresh.",
        )
        self.clear_targets_btn.Bind(wx.EVT_BUTTON, self._on_clear_targets)
        self.clear_targets_btn.Disable()
        target_row.Add(self.open_btn)
        target_row.Add(self.clear_targets_btn, flag=wx.LEFT, border=8)
        root.Add(target_row, flag=wx.LEFT | wx.RIGHT, border=10)

        # A read-only multi-line text control is used instead of a list box
        # because list boxes on macOS announce items inconsistently with
        # VoiceOver — arrow navigation sometimes skips entries or replays
        # the same item. A plain text view lets any screen reader read the
        # file names line by line with the usual reading keys.
        self.targets_view = wx.TextCtrl(
            panel,
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_DONTWRAP,
        )
        a11y.set_a11y(
            self.targets_view,
            "Selected audio files",
            (
                "Read-only list of audio files that will be analyzed. "
                "Each file produces its own section in the report. "
                "Use Clear Files to start over."
            ),
        )
        self.targets_view.SetMinSize(wx.Size(-1, 110))
        root.Add(
            self.targets_view,
            flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP,
            border=10,
        )
        self._refresh_targets_view()

        # ----- Genre picker -----
        genre_row = wx.BoxSizer(wx.HORIZONTAL)
        self.genre_btn = wx.Button(panel, label="Choose Genres...")
        a11y.set_a11y(self.genre_btn, a11y.LABEL_GENRE_PICKER, a11y.HINT_GENRE_PICKER)
        self.genre_btn.Bind(wx.EVT_BUTTON, self._on_choose_genre)
        self.genre_value_label = wx.StaticText(panel, label="")
        self.genre_value_label.SetName("Selected genres")
        self._selected_genre_keys: list[str] = []
        self._update_genre_label()
        genre_row.Add(self.genre_btn)
        genre_row.Add(self.genre_value_label, proportion=1, flag=wx.LEFT | wx.ALIGN_CENTER_VERTICAL, border=10)
        root.Add(genre_row, flag=wx.EXPAND | wx.ALL, border=10)

        # ----- Reference file picker -----
        ref_row = wx.BoxSizer(wx.HORIZONTAL)
        self.reference_btn = wx.Button(panel, label="Choose Reference File...")
        a11y.set_a11y(self.reference_btn, a11y.LABEL_REFERENCE_FILE, a11y.HINT_REFERENCE_FILE)
        self.reference_btn.Bind(wx.EVT_BUTTON, self._on_open_reference)
        self.reference_label = wx.StaticText(panel, label="No reference file selected.")
        self.reference_label.SetName("Selected reference file")
        self.clear_reference_btn = wx.Button(panel, label="Clear Reference")
        a11y.set_a11y(
            self.clear_reference_btn,
            "Clear reference file",
            "Remove the currently selected reference file so the report does not include a reference comparison.",
        )
        self.clear_reference_btn.Bind(wx.EVT_BUTTON, self._on_clear_reference)
        self.clear_reference_btn.Disable()
        ref_row.Add(self.reference_btn)
        ref_row.Add(self.reference_label, proportion=1, flag=wx.LEFT | wx.ALIGN_CENTER_VERTICAL, border=10)
        ref_row.Add(self.clear_reference_btn, flag=wx.LEFT, border=8)
        root.Add(ref_row, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=10)

        # ----- Analyze button -----
        self.analyze_btn = wx.Button(panel, label="Analyze")
        a11y.set_a11y(self.analyze_btn, a11y.LABEL_ANALYZE, a11y.HINT_ANALYZE)
        self.analyze_btn.Bind(wx.EVT_BUTTON, self._on_analyze)
        self.analyze_btn.Disable()
        root.Add(self.analyze_btn, flag=wx.LEFT | wx.RIGHT, border=10)

        # ----- Progress bar -----
        self.progress = wx.Gauge(panel, range=100, style=wx.GA_HORIZONTAL)
        a11y.set_a11y(
            self.progress,
            "Analysis progress",
            "Progress of the running analysis, from zero to one hundred percent.",
        )
        self.progress.Hide()
        root.Add(self.progress, flag=wx.EXPAND | wx.ALL, border=10)

        # Progress label (visible text that screen readers can also read).
        self.progress_label = wx.StaticText(panel, label="")
        self.progress_label.SetName("Current analysis stage")
        self.progress_label.Hide()
        root.Add(self.progress_label, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=10)

        panel.SetSizer(root)

        # Menu bar — on macOS, wx relocates wx.ID_EXIT / wx.ID_ABOUT into
        # the automatic application menu and wires Cmd+Q to the Quit item.
        # Without an EXIT menu item, Cmd+Q is swallowed by the OS but has
        # no bound handler, so the window never closes.
        self._build_menu_bar()

        # Status bar at the bottom for quick idle/running/done messages.
        self.CreateStatusBar()
        self.SetStatusText(a11y.STATUS_IDLE)

        # Keyboard shortcuts: Ctrl/Cmd+O to add files, Ctrl/Cmd+R to run.
        accel = wx.AcceleratorTable(
            [
                wx.AcceleratorEntry(wx.ACCEL_CMD, ord("O"), self._get_id(self.open_btn)),
                wx.AcceleratorEntry(wx.ACCEL_CMD, ord("R"), self._get_id(self.analyze_btn)),
            ]
        )
        self.SetAcceleratorTable(accel)

        # Intercept close to cancel a running analysis cleanly.
        self.Bind(wx.EVT_CLOSE, self._on_close)

        self.open_btn.SetFocus()

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _get_id(widget: wx.Window) -> int:
        return widget.GetId()

    def _build_menu_bar(self) -> None:
        menubar = wx.MenuBar()

        file_menu = wx.Menu()
        # Ctrl+Q on Linux/Windows, Cmd+Q on macOS (wx maps ACCEL_CMD
        # to the native modifier).
        quit_item = file_menu.Append(
            wx.ID_EXIT,
            "&Quit\tCtrl+Q",
            "Close NonvisualAudio.",
        )
        menubar.Append(file_menu, "&File")

        self.SetMenuBar(menubar)
        self.Bind(wx.EVT_MENU, self._on_quit_menu, quit_item)

    def _on_quit_menu(self, event: wx.CommandEvent) -> None:
        # Route through Close() so the EVT_CLOSE handler still runs and
        # gets a chance to stop the click ticker and any running work.
        self.Close()

    # ------------------------------------------------------------------ #
    # Target files
    # ------------------------------------------------------------------ #

    def _on_add_targets(self, event: wx.Event) -> None:
        with wx.FileDialog(
            self,
            message="Add audio files",
            wildcard=SUPPORTED_WILDCARD,
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST | wx.FD_MULTIPLE,
        ) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                log.debug("add targets: cancelled")
                return
            paths = list(dlg.GetPaths())
        if not paths:
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

    def _on_clear_targets(self, event: wx.Event) -> None:
        self._target_paths.clear()
        log.info("targets cleared")
        self._refresh_targets_view()
        self._update_analyze_state()

    def _refresh_targets_view(self) -> None:
        if not self._target_paths:
            placeholder = (
                "No audio files selected. Click Add Audio Files to choose one or more."
            )
            self.targets_view.ChangeValue(placeholder)
            self.targets_view.SetHelpText(placeholder)
            return
        lines = [f"{i + 1}. {Path(p).name}" for i, p in enumerate(self._target_paths)]
        count = len(lines)
        header = (
            f"{count} audio file selected:"
            if count == 1
            else f"{count} audio files selected:"
        )
        text = header + "\n" + "\n".join(lines)
        self.targets_view.ChangeValue(text)
        self.targets_view.SetHelpText(text)

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
            self.genre_value_label.SetLabel("Standalone (no genre selected)")
            self.genre_value_label.SetHelpText(
                "Standalone analysis. No genre references selected. "
                "Open the Choose Genres dialog to add one or more."
            )
            return
        if len(names) == 1:
            text = names[0]
        else:
            text = f"{names[0]} and {len(names) - 1} more"
        self.genre_value_label.SetLabel(text)
        self.genre_value_label.SetHelpText(f"Selected genres: {', '.join(names)}.")

    def _on_choose_genre(self, event: wx.Event) -> None:
        dlg = GenreDialog(self, selected_keys=self._selected_genre_keys)
        try:
            if dlg.ShowModal() != wx.ID_OK:
                return
            self._selected_genre_keys = dlg.selected_keys()
        finally:
            dlg.Destroy()
        self._update_genre_label()
        self._update_analyze_state()
        log.info("genres selected: %s", self._selected_genre_keys)

    # ------------------------------------------------------------------ #
    # Reference file
    # ------------------------------------------------------------------ #

    def _on_open_reference(self, event: wx.Event) -> None:
        with wx.FileDialog(
            self,
            message="Open reference audio file",
            wildcard=SUPPORTED_WILDCARD,
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
        ) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            path = dlg.GetPath()
        log.info("reference selected: %s", path)
        self._reference_path = path
        self.reference_label.SetLabel(Path(path).name)
        self.reference_label.SetHelpText(f"Reference file: {Path(path).name}")
        self.clear_reference_btn.Enable()

    def _on_clear_reference(self, event: wx.Event) -> None:
        self._reference_path = None
        self.reference_label.SetLabel("No reference file selected.")
        self.reference_label.SetHelpText("No reference file selected.")
        self.clear_reference_btn.Disable()
        log.info("reference cleared")

    # ------------------------------------------------------------------ #
    # Analyze flow
    # ------------------------------------------------------------------ #

    def _update_analyze_state(self) -> None:
        has_targets = bool(self._target_paths)
        self.analyze_btn.Enable(has_targets)
        self.clear_targets_btn.Enable(has_targets)

    def _on_analyze(self, event: wx.Event) -> None:
        if not self._target_paths:
            return
        self.analyze_btn.Disable()
        self.open_btn.Disable()
        self.SetStatusText(a11y.STATUS_RUNNING)
        self.progress.SetValue(0)
        self.progress.Show()
        self.progress_label.SetLabel("Starting...")
        self.progress_label.Show()
        self.Layout()
        self._click_ticker.start()
        genre_keys = self._selected_genre_keys or None
        log.info(
            "analyze clicked: %d file(s) genres=%s reference=%s",
            len(self._target_paths),
            genre_keys,
            self._reference_path,
        )
        self._worker = start_analysis(
            self._target_paths,
            genre_keys,
            self._reference_path,
            self._on_analysis_done,
            self._on_analysis_failed,
            self._on_analysis_progress,
        )

    def _on_analysis_progress(self, percent: int, stage: str) -> None:
        self.progress.SetValue(max(0, min(100, int(percent))))
        self.progress_label.SetLabel(f"{stage} — {percent}%")
        self.progress_label.SetHelpText(
            f"Analysis progress: {stage}, {percent} percent."
        )
        self.SetStatusText(f"{stage}... {percent}%")

    def _stop_running_ui(self) -> None:
        self._click_ticker.stop()
        self.progress.Hide()
        self.progress.SetValue(0)
        self.progress_label.Hide()
        self.Layout()
        self.open_btn.Enable()
        self._update_analyze_state()

    def _on_analysis_done(self, report: str, had_failures: bool) -> None:
        log.info(
            "analysis done, %d chars delivered to UI, failures=%s",
            len(report),
            had_failures,
        )
        self._stop_running_ui()
        self.SetStatusText(a11y.STATUS_PARTIAL if had_failures else a11y.STATUS_DONE)
        self._show_results(report)

    def _on_analysis_failed(self, err: UserFacingError) -> None:
        log.error("analysis failed: %s", err)
        self._stop_running_ui()
        self.SetStatusText(a11y.STATUS_ERROR)
        show_error(self, err)
        # Return focus to the analyze button for a quick retry, or to the
        # open button if no targets are left.
        if self.analyze_btn.IsEnabled():
            self.analyze_btn.SetFocus()
        else:
            self.open_btn.SetFocus()

    def _show_results(self, report: str) -> None:
        dlg = ResultsDialog(self, report_text=report)
        try:
            dlg.ShowModal()
        finally:
            dlg.Destroy()
        log.info("results window closed, focus returning to main window")
        self.Raise()
        if self.analyze_btn.IsEnabled():
            self.analyze_btn.SetFocus()
        else:
            self.open_btn.SetFocus()

    # ------------------------------------------------------------------ #
    # Close handling
    # ------------------------------------------------------------------ #

    def _on_close(self, event: wx.CloseEvent) -> None:
        # The analysis worker is a daemon thread and will die with the
        # process; nothing to join here. Just stop the click to avoid a
        # trailing tick after the window disappears.
        try:
            self._click_ticker.stop()
        except Exception:  # noqa: BLE001 — never block shutdown on audio cleanup
            pass
        self.Destroy()
