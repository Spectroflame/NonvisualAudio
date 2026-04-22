"""Main application window."""

from __future__ import annotations

import logging
from pathlib import Path

import wx

from nonvisualaudio import preferences
from nonvisualaudio.errors import UserFacingError
from nonvisualaudio.localization import t
from nonvisualaudio.reporting.genre_profiles import GENRES
from nonvisualaudio.ui import a11y
from nonvisualaudio.ui import theme
from nonvisualaudio.ui.about_dialog import show_about
from nonvisualaudio.ui.click_sound import ClickTicker
from nonvisualaudio.ui.drop import expand_audio_paths, parse_paste_text
from nonvisualaudio.ui.error_dialog import show_error
from nonvisualaudio.ui.genre_dialog import GenreDialog
from nonvisualaudio.ui.genre_editor_dialog import GenreEditorDialog
from nonvisualaudio.ui.results_dialog import ResultsDialog
from nonvisualaudio.ui.worker import start_analysis


class _AudioDropTarget(wx.FileDropTarget):
    """Wire wx.FileDropTarget's callback back into ``MainWindow._add_paths``."""

    def __init__(self, on_drop):
        super().__init__()
        self._on_drop = on_drop

    def OnDropFiles(self, x: int, y: int, filenames: list[str]) -> bool:  # noqa: N802
        self._on_drop(list(filenames))
        return True

log = logging.getLogger("nonvisualaudio.ui")


class MainWindow(wx.Frame):
    def __init__(self) -> None:
        super().__init__(
            parent=None,
            title=t("ui.main.title"),
            size=wx.Size(760, 600),
        )
        self.SetName(t("ui.main.title"))

        self._target_paths: list[str] = []
        self._reference_path: str | None = None
        self._click_ticker = ClickTicker(self)
        self._worker = None  # keep a reference to the running worker

        panel = wx.Panel(self)
        root = wx.BoxSizer(wx.VERTICAL)

        intro = wx.StaticText(panel, label=t("ui.main.intro"))
        root.Add(intro, flag=wx.ALL, border=10)

        # ----- Target file picker -----
        target_row = wx.BoxSizer(wx.HORIZONTAL)
        self.open_btn = wx.Button(panel, label=t("ui.btn.add_audio"))
        a11y.set_a11y(self.open_btn, t("ui.label.open_file"), t("ui.hint.open_file"))
        self.open_btn.Bind(wx.EVT_BUTTON, self._on_add_targets)
        self.clear_targets_btn = wx.Button(panel, label=t("ui.btn.clear_files"))
        a11y.set_a11y(
            self.clear_targets_btn,
            t("ui.label.clear_files"),
            t("ui.hint.clear_files"),
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
            t("ui.label.targets_view"),
            t("ui.hint.targets_view"),
        )
        self.targets_view.SetMinSize(wx.Size(-1, 110))
        root.Add(
            self.targets_view,
            flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP,
            border=10,
        )
        self._refresh_targets_view()

        # ----- Genre picker -----
        # Same pattern as targets_view: the selected-genres display is a
        # multi-line read-only TextCtrl on its own row below the button.
        # A TextCtrl is focusable on every platform, so it sits in the tab
        # order and is read aloud reliably by NVDA/JAWS/VoiceOver — where
        # a wx.StaticText is skipped on Windows and often on macOS.
        genre_row = wx.BoxSizer(wx.HORIZONTAL)
        self.genre_btn = wx.Button(panel, label=t("ui.btn.choose_genres"))
        a11y.set_a11y(
            self.genre_btn, t("ui.label.genre_picker"), t("ui.hint.genre_picker")
        )
        self.genre_btn.Bind(wx.EVT_BUTTON, self._on_choose_genre)
        genre_row.Add(self.genre_btn)
        root.Add(genre_row, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=10)

        self.genre_value_label = wx.TextCtrl(
            panel,
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_DONTWRAP,
        )
        a11y.set_a11y(
            self.genre_value_label,
            t("ui.label.selected_genres"),
            t("ui.hint.selected_genres"),
        )
        self.genre_value_label.SetMinSize(wx.Size(-1, 60))
        self._selected_genre_keys: list[str] = []
        self._update_genre_label()
        root.Add(
            self.genre_value_label,
            flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP,
            border=10,
        )

        # ----- Reference file picker -----
        ref_row = wx.BoxSizer(wx.HORIZONTAL)
        self.reference_btn = wx.Button(panel, label=t("ui.btn.choose_reference"))
        a11y.set_a11y(
            self.reference_btn,
            t("ui.label.reference_file"),
            t("ui.hint.reference_file"),
        )
        self.reference_btn.Bind(wx.EVT_BUTTON, self._on_open_reference)
        self.clear_reference_btn = wx.Button(panel, label=t("ui.btn.clear_reference"))
        a11y.set_a11y(
            self.clear_reference_btn,
            t("ui.label.clear_reference"),
            t("ui.hint.clear_reference"),
        )
        self.clear_reference_btn.Bind(wx.EVT_BUTTON, self._on_clear_reference)
        self.clear_reference_btn.Disable()
        ref_row.Add(self.reference_btn)
        ref_row.Add(self.clear_reference_btn, flag=wx.LEFT, border=8)
        root.Add(ref_row, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=10)

        # Same rationale as genre_value_label above.
        self.reference_label = wx.TextCtrl(
            panel,
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_DONTWRAP,
        )
        a11y.set_a11y(
            self.reference_label,
            t("ui.label.selected_reference"),
            t("ui.hint.selected_reference"),
        )
        self.reference_label.SetMinSize(wx.Size(-1, 60))
        self._update_reference_label()
        root.Add(
            self.reference_label,
            flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.TOP,
            border=10,
        )

        # ----- Analyze button -----
        # Mark Analyze as the default so Enter from any focused control
        # triggers analysis. wx also paints it visually highlighted on
        # most platforms, which is a useful cue for sighted collaborators.
        self.analyze_btn = wx.Button(panel, label=t("ui.btn.analyze"))
        a11y.set_a11y(self.analyze_btn, t("ui.label.analyze"), t("ui.hint.analyze"))
        self.analyze_btn.Bind(wx.EVT_BUTTON, self._on_analyze)
        self.analyze_btn.Disable()
        self.analyze_btn.SetDefault()
        root.Add(self.analyze_btn, flag=wx.LEFT | wx.RIGHT, border=10)

        # ----- Progress bar -----
        self.progress = wx.Gauge(panel, range=100, style=wx.GA_HORIZONTAL)
        a11y.set_a11y(
            self.progress,
            t("ui.label.progress_bar"),
            t("ui.hint.progress_bar"),
        )
        self.progress.Hide()
        root.Add(self.progress, flag=wx.EXPAND | wx.ALL, border=10)

        # Progress label. Read-only TextCtrl rather than StaticText so it
        # sits in the tab order: during analysis the user can Tab past the
        # (disabled) Analyze button and land on this control to hear the
        # current stage ("Measuring loudness — 40%"). A StaticText would
        # be skipped by the screen reader's focus traversal.
        self.progress_label = wx.TextCtrl(panel, style=wx.TE_READONLY)
        a11y.set_a11y(
            self.progress_label,
            t("ui.label.progress_stage"),
            t("ui.hint.progress_stage"),
        )
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
        self.SetStatusText(t("status.idle"))

        # Keyboard shortcuts: Ctrl/Cmd+O to add files, Ctrl/Cmd+R to run.
        accel = wx.AcceleratorTable(
            [
                wx.AcceleratorEntry(wx.ACCEL_CMD, ord("O"), self._get_id(self.open_btn)),
                wx.AcceleratorEntry(wx.ACCEL_CMD, ord("R"), self._get_id(self.analyze_btn)),
            ]
        )
        self.SetAcceleratorTable(accel)

        # Drag-and-drop: both the outer panel (big hit area for sighted
        # users) and the targets_view (the visual home of the file list)
        # accept audio file drops.
        drop_target_panel = _AudioDropTarget(self._add_paths)
        panel.SetDropTarget(drop_target_panel)
        drop_target_list = _AudioDropTarget(self._add_paths)
        self.targets_view.SetDropTarget(drop_target_list)

        # Global keyboard shortcut Cmd/Ctrl+V to paste copied file
        # selections. Routed via EVT_CHAR_HOOK on the frame so it fires
        # no matter which control has focus — including read-only
        # TextCtrls that would otherwise swallow paste.
        self.Bind(wx.EVT_CHAR_HOOK, self._on_char_hook)

        # Intercept close to cancel a running analysis cleanly.
        self.Bind(wx.EVT_CLOSE, self._on_close)

        self.open_btn.SetFocus()

        # Paint the window with the chosen theme. Done at the end of
        # __init__ so every widget already exists.
        theme.apply(self)

        # Startup clipboard scan. Runs once after the main event loop
        # picks up control so the dialog does not interleave with the
        # window's initial layout and focus routing.
        self._startup_scan_done = False
        wx.CallAfter(self._maybe_offer_clipboard_import)

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
            t("ui.menu.quit"),
            t("ui.menu.quit.hint"),
        )
        menubar.Append(file_menu, t("ui.menu.file"))

        edit_menu = wx.Menu()
        genre_editor_item = edit_menu.Append(
            wx.ID_ANY,
            t("ui.menu.genre_editor"),
            t("ui.menu.genre_editor.hint"),
        )
        edit_menu.AppendSeparator()
        language_submenu = wx.Menu()
        current_pref = preferences.load_language()
        self._language_items: dict[str | None, wx.MenuItem] = {}
        auto_item = language_submenu.AppendRadioItem(
            wx.ID_ANY,
            t("ui.genre_form.menu.language.auto"),
            t("ui.genre_form.menu.language.auto.hint"),
        )
        self._language_items[None] = auto_item
        en_item = language_submenu.AppendRadioItem(
            wx.ID_ANY,
            t("ui.genre_form.menu.language.english"),
            "",
        )
        self._language_items["en"] = en_item
        de_item = language_submenu.AppendRadioItem(
            wx.ID_ANY,
            t("ui.genre_form.menu.language.deutsch"),
            "",
        )
        self._language_items["de"] = de_item
        # Reflect the persisted preference (None → Auto).
        self._language_items.get(current_pref, auto_item).Check(True)
        edit_menu.AppendSubMenu(
            language_submenu,
            t("ui.genre_form.menu.language"),
            t("ui.genre_form.menu.language.hint"),
        )
        menubar.Append(edit_menu, t("ui.menu.edit"))

        view_menu = wx.Menu()
        theme_submenu = wx.Menu()
        current_theme = theme.current()
        self._theme_items: dict[str, wx.MenuItem] = {}
        for key in theme.VALID_THEMES:
            label = t(f"ui.menu.theme.{key}")
            hint = t(f"ui.menu.theme.{key}.hint")
            item = theme_submenu.AppendRadioItem(wx.ID_ANY, label, hint)
            self._theme_items[key] = item
            self.Bind(
                wx.EVT_MENU,
                lambda _e, k=key: self._on_theme_chosen(k),
                item,
            )
        self._theme_items.get(current_theme, self._theme_items["auto"]).Check(True)
        view_menu.AppendSubMenu(
            theme_submenu,
            t("ui.menu.theme"),
            t("ui.menu.theme.hint"),
        )
        menubar.Append(view_menu, t("ui.menu.view"))

        # Help menu. On macOS wx automatically relocates ID_ABOUT into the
        # application menu while keeping the Help menu present for the
        # standard "Help > About" path on Windows and Linux.
        help_menu = wx.Menu()
        about_item = help_menu.Append(
            wx.ID_ABOUT,
            t("ui.menu.about"),
            t("ui.menu.about.hint"),
        )
        menubar.Append(help_menu, t("ui.menu.help"))

        self.SetMenuBar(menubar)
        self.Bind(wx.EVT_MENU, self._on_quit_menu, quit_item)
        self.Bind(wx.EVT_MENU, self._on_about, about_item)
        self.Bind(wx.EVT_MENU, self._on_edit_genres, genre_editor_item)
        self.Bind(wx.EVT_MENU, lambda _e: self._on_language_chosen(None), auto_item)
        self.Bind(wx.EVT_MENU, lambda _e: self._on_language_chosen("en"), en_item)
        self.Bind(wx.EVT_MENU, lambda _e: self._on_language_chosen("de"), de_item)

    def _on_quit_menu(self, event: wx.CommandEvent) -> None:
        # Route through Close() so the EVT_CLOSE handler still runs and
        # gets a chance to stop the click ticker and any running work.
        self.Close()

    def _on_about(self, event: wx.CommandEvent) -> None:
        show_about(self)

    def _on_theme_chosen(self, theme_key: str) -> None:
        if theme_key == theme.current():
            return
        theme.set_current(theme_key)
        if theme_key == theme.DEFAULT_THEME:
            prefs = preferences.load()
            prefs.pop("theme", None)
            preferences.save(prefs)
        else:
            preferences.save_theme(theme_key)
        theme.apply(self)
        log.info("theme changed to %s", theme_key)

    def _on_language_chosen(self, lang: str | None) -> None:
        prefs = preferences.load()
        current = prefs.get("language")
        if lang == current:
            return
        if lang is None:
            prefs.pop("language", None)
        else:
            prefs["language"] = lang
        preferences.save(prefs)
        wx.MessageBox(
            t("ui.genre_form.menu.language.restart_body"),
            t("ui.genre_form.menu.language.restart_title"),
            style=wx.OK | wx.ICON_INFORMATION,
            parent=self,
        )
        log.info("language preference changed to %s (restart required)", lang)

    def _on_edit_genres(self, event: wx.CommandEvent) -> None:
        dlg = GenreEditorDialog(self)
        try:
            dlg.ShowModal()
        finally:
            dlg.Destroy()
        # Refresh the selected-genres label in case a genre the user
        # picked earlier was deleted or renamed while the editor was open.
        self._update_genre_label()

    # ------------------------------------------------------------------ #
    # Target files
    # ------------------------------------------------------------------ #

    def _on_add_targets(self, event: wx.Event) -> None:
        with wx.FileDialog(
            self,
            message=t("ui.file_dialog.add_audio"),
            wildcard=t("ui.file_dialog.wildcard"),
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST | wx.FD_MULTIPLE,
        ) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                log.debug("add targets: cancelled")
                return
            paths = list(dlg.GetPaths())
        if not paths:
            return
        self._add_paths(paths)

    def _add_paths(self, paths: list[str]) -> None:
        """Expand folders, dedup, and merge into the target list.

        Single entry point for file-dialog, drag-and-drop, and clipboard
        paste. ``paths`` may contain folder paths, which are walked
        recursively by :func:`expand_audio_paths`.
        """
        expanded = expand_audio_paths(paths)
        if not expanded:
            log.info("no audio files found in %d input path(s)", len(paths))
            return
        existing = set(self._target_paths)
        added = 0
        for p in expanded:
            if p in existing:
                continue
            self._target_paths.append(p)
            existing.add(p)
            added += 1
        if added == 0:
            log.info("add paths: all %d already in list", len(expanded))
            return
        log.info("added %d target(s); total now %d", added, len(self._target_paths))
        self._refresh_targets_view()
        self._update_analyze_state()

    # ------------------------------------------------------------------ #
    # Clipboard helpers
    # ------------------------------------------------------------------ #

    def _read_clipboard_paths(self) -> list[str]:
        """Return path-like strings from the system clipboard, or []."""
        paths: list[str] = []
        if not wx.TheClipboard.Open():
            return paths
        try:
            file_format = wx.DataFormat(wx.DF_FILENAME)
            if wx.TheClipboard.IsSupported(file_format):
                data = wx.FileDataObject()
                if wx.TheClipboard.GetData(data):
                    paths.extend(data.GetFilenames())
            if not paths and wx.TheClipboard.IsSupported(
                wx.DataFormat(wx.DF_UNICODETEXT)
            ):
                text_data = wx.TextDataObject()
                if wx.TheClipboard.GetData(text_data):
                    paths.extend(parse_paste_text(text_data.GetText()))
        finally:
            wx.TheClipboard.Close()
        return paths

    def _on_char_hook(self, event: wx.KeyEvent) -> None:
        # Cmd/Ctrl+V anywhere in the frame pastes from the clipboard.
        if (
            (event.CmdDown() or event.ControlDown())
            and not event.ShiftDown()
            and not event.AltDown()
            and event.GetKeyCode() == ord("V")
        ):
            paths = self._read_clipboard_paths()
            if paths:
                log.info("paste detected: %d raw clipboard path(s)", len(paths))
                self._add_paths(paths)
            return
        event.Skip()

    def _maybe_offer_clipboard_import(self) -> None:
        """Offer to import any audio files found on the clipboard.

        Fires once after window show. Silent if the clipboard does not
        contain recognisable audio paths so the dialog does not nag on
        every launch.
        """
        if self._startup_scan_done:
            return
        self._startup_scan_done = True
        raw = self._read_clipboard_paths()
        if not raw:
            return
        expanded = expand_audio_paths(raw)
        if not expanded:
            return
        count = len(expanded)
        body_key = (
            "ui.clipboard_scan.body.one"
            if count == 1
            else "ui.clipboard_scan.body.other"
        )
        answer = wx.MessageBox(
            t(body_key, count=count),
            t("ui.clipboard_scan.title"),
            style=wx.YES_NO | wx.YES_DEFAULT | wx.ICON_QUESTION,
            parent=self,
        )
        if answer == wx.YES:
            log.info("startup scan: user accepted %d file(s) from clipboard", count)
            self._add_paths(raw)

    def _on_clear_targets(self, event: wx.Event) -> None:
        self._target_paths.clear()
        log.info("targets cleared")
        self._refresh_targets_view()
        self._update_analyze_state()

    def _refresh_targets_view(self) -> None:
        if not self._target_paths:
            placeholder = t("ui.placeholder.no_files")
            self.targets_view.ChangeValue(placeholder)
            a11y.update_help(self.targets_view, placeholder)
            return
        lines = [f"{i + 1}. {Path(p).name}" for i, p in enumerate(self._target_paths)]
        count = len(lines)
        header_key = (
            "ui.list.files_selected.one" if count == 1 else "ui.list.files_selected.other"
        )
        text = t(header_key, count=count) + "\n" + "\n".join(lines)
        self.targets_view.ChangeValue(text)
        a11y.update_help(self.targets_view, text)

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
            placeholder = t("ui.placeholder.no_genres")
            self.genre_value_label.ChangeValue(placeholder)
            a11y.update_help(self.genre_value_label, placeholder)
            return
        count = len(names)
        header_key = (
            "ui.list.genres_selected.one" if count == 1 else "ui.list.genres_selected.other"
        )
        text = t(header_key, count=count) + "\n" + "\n".join(
            f"{i + 1}. {n}" for i, n in enumerate(names)
        )
        self.genre_value_label.ChangeValue(text)
        a11y.update_help(self.genre_value_label, text)

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

    def _update_reference_label(self) -> None:
        if self._reference_path is None:
            placeholder = t("ui.placeholder.no_reference")
            self.reference_label.ChangeValue(placeholder)
            a11y.update_help(self.reference_label, placeholder)
            return
        name = Path(self._reference_path).name
        text = t("ui.list.reference_selected", name=name)
        self.reference_label.ChangeValue(text)
        a11y.update_help(self.reference_label, text)

    def _on_open_reference(self, event: wx.Event) -> None:
        with wx.FileDialog(
            self,
            message=t("ui.file_dialog.reference"),
            wildcard=t("ui.file_dialog.wildcard"),
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
        ) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            path = dlg.GetPath()
        log.info("reference selected: %s", path)
        self._reference_path = path
        self._update_reference_label()
        self.clear_reference_btn.Enable()

    def _on_clear_reference(self, event: wx.Event) -> None:
        self._reference_path = None
        self._update_reference_label()
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
        self.SetStatusText(t("status.running"))
        self.progress.SetValue(0)
        self.progress.Show()
        self.progress_label.ChangeValue(t("ui.progress.starting"))
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
        self.progress_label.ChangeValue(
            t("ui.progress.line", stage=stage, percent=percent)
        )
        a11y.update_help(
            self.progress_label,
            t("ui.progress.hint", stage=stage, percent=percent),
        )
        self.SetStatusText(t("ui.progress.status", stage=stage, percent=percent))

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
        self.SetStatusText(
            t("status.partial") if had_failures else t("status.done")
        )
        self._show_results(report)

    def _on_analysis_failed(self, err: UserFacingError) -> None:
        log.error("analysis failed: %s", err)
        self._stop_running_ui()
        self.SetStatusText(t("status.error"))
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
