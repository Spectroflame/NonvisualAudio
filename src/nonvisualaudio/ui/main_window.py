"""Main application window."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import wx

from nonvisualaudio import preferences
from nonvisualaudio.analysis.memory import (
    MemoryEstimate,
    build_estimate,
    estimate_file_bytes,
    estimate_project_bytes,
    format_bytes,
)
from nonvisualaudio.errors import UserFacingError
from nonvisualaudio.localization import t
from nonvisualaudio.reporting import genre_profiles
from nonvisualaudio.reporting.builder import SECTION_ORDER, ReportSections
from nonvisualaudio.ui import a11y
from nonvisualaudio.ui import macos_a11y
from nonvisualaudio.ui import theme
from nonvisualaudio.ui.about_dialog import show_about
from nonvisualaudio.ui.click_sound import ClickTicker
from nonvisualaudio.ui.diagnostics_dialog import show_diagnostics
from nonvisualaudio.ui.drop import expand_audio_paths, parse_paste_text
from nonvisualaudio.ui.error_dialog import show_error
from nonvisualaudio.ui.genre_dialog import GenreDialog
from nonvisualaudio.ui.genre_editor_dialog import GenreEditorDialog
from nonvisualaudio.ui.results_dialog import ResultsDialog
from nonvisualaudio.ui.sections_dialog import SectionsDialog, section_label
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
        # Reference can be a single file (the historical case) or a
        # multi-file/folder selection that we treat as a "reference
        # project". An empty list means no reference comparison.
        self._reference_paths: list[str] = []
        self._click_ticker = ClickTicker(self)
        self._worker = None  # keep a reference to the running worker
        # Report-section preference: default to "everything on" so a
        # fresh install behaves like the historical report.
        stored_sections = preferences.load_report_sections()
        self._section_keys: list[str] = (
            list(stored_sections) if stored_sections else list(SECTION_ORDER)
        )
        # Project mode is intentionally NOT persisted: it is a per-run
        # choice and accidentally leaving it on after a single-file
        # workflow would silently change every later analysis.
        self._project_mode: bool = False

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
        self.reference_label.SetMinSize(wx.Size(-1, 90))
        self._update_reference_label()
        root.Add(
            self.reference_label,
            flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.TOP,
            border=10,
        )

        # Drag-and-drop a folder or several audio files onto the
        # reference area to set / replace the reference selection. The
        # same _AudioDropTarget already used for the targets list works
        # here too — it just routes into a different setter.
        self.reference_label.SetDropTarget(
            _AudioDropTarget(self._set_reference_paths)
        )

        # ----- Report sections picker -----
        sections_row = wx.BoxSizer(wx.HORIZONTAL)
        self.sections_btn = wx.Button(
            panel, label=t("ui.btn.choose_sections")
        )
        a11y.set_a11y(
            self.sections_btn,
            t("ui.label.sections_picker"),
            t("ui.hint.sections_picker"),
        )
        self.sections_btn.Bind(wx.EVT_BUTTON, self._on_choose_sections)
        sections_row.Add(self.sections_btn)
        root.Add(sections_row, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=10)

        self.sections_value_label = wx.TextCtrl(
            panel,
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_DONTWRAP,
        )
        a11y.set_a11y(
            self.sections_value_label,
            t("ui.label.selected_sections"),
            t("ui.hint.selected_sections"),
        )
        self.sections_value_label.SetMinSize(wx.Size(-1, 60))
        self._update_sections_label()
        root.Add(
            self.sections_value_label,
            flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP,
            border=10,
        )

        # ----- Project-mode toggle -----
        # A regular wx.CheckBox is read aloud reliably as a check box
        # state on every supported screen reader, so we don't need a
        # composite or radio group here.
        self.project_check = wx.CheckBox(
            panel, label=t("ui.btn.project_mode")
        )
        a11y.set_a11y(
            self.project_check,
            t("ui.label.project_mode"),
            t("ui.hint.project_mode"),
        )
        self.project_check.SetValue(self._project_mode)
        self.project_check.Bind(wx.EVT_CHECKBOX, self._on_toggle_project)
        root.Add(
            self.project_check,
            flag=wx.LEFT | wx.RIGHT | wx.TOP,
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

        # Keyboard shortcuts: Ctrl/Cmd+O to add files, Ctrl/Cmd+R to run,
        # F1 (and Cmd+Shift+ß on a German Mac keyboard, which is the
        # physical Cmd+?) to open the About/Help dialog. Routing F1 to
        # ID_ABOUT means the same handler is hit no matter whether the
        # user uses the menu, F1, or the macOS Cmd+? convention.
        accel = wx.AcceleratorTable(
            [
                wx.AcceleratorEntry(wx.ACCEL_CMD, ord("O"), self._get_id(self.open_btn)),
                wx.AcceleratorEntry(wx.ACCEL_CMD, ord("R"), self._get_id(self.analyze_btn)),
                wx.AcceleratorEntry(
                    wx.ACCEL_NORMAL,
                    wx.WXK_F1,
                    self._about_menu_id,
                ),
                wx.AcceleratorEntry(
                    wx.ACCEL_CMD | wx.ACCEL_SHIFT,
                    ord("ß"),
                    self._about_menu_id,
                ),
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
        is_macos = sys.platform == "darwin"

        # File menu — only on Windows and Linux. On macOS wx.ID_EXIT is
        # auto-relocated into the application menu, so a dedicated File
        # menu would be visibly empty. Cmd+Q still works there because
        # the App menu auto-wires it; we bind ID_EXIT globally below.
        quit_item = None
        if not is_macos:
            file_menu = wx.Menu()
            quit_item = file_menu.Append(
                wx.ID_EXIT,
                t("ui.menu.quit"),
                t("ui.menu.quit.hint"),
            )
            menubar.Append(file_menu, t("ui.menu.file"))

        # Edit menu: just the genre editor. Language moved out so the
        # user finds it directly on the top-level menu bar.
        edit_menu = wx.Menu()
        genre_editor_item = edit_menu.Append(
            wx.ID_ANY,
            t("ui.menu.genre_editor"),
            t("ui.menu.genre_editor.hint"),
        )
        menubar.Append(edit_menu, t("ui.menu.edit"))

        # View menu: theme submenu (four radio items).
        view_menu = wx.Menu()
        current_theme = theme.current()
        self._theme_items: dict[str, wx.MenuItem] = {}
        for key in theme.VALID_THEMES:
            item = view_menu.AppendRadioItem(
                wx.ID_ANY,
                t(f"ui.menu.theme.{key}"),
                t(f"ui.menu.theme.{key}.hint"),
            )
            self._theme_items[key] = item
            self.Bind(
                wx.EVT_MENU,
                lambda _e, k=key: self._on_theme_chosen(k),
                item,
            )
        self._theme_items.get(current_theme, self._theme_items["auto"]).Check(True)
        menubar.Append(view_menu, t("ui.menu.view"))

        # Language menu: top-level so screen readers find it with one
        # arrow press from the menu bar, no submenu drill-down needed.
        language_menu = wx.Menu()
        current_pref = preferences.load_language()
        self._language_items: dict[str | None, wx.MenuItem] = {}
        auto_item = language_menu.AppendRadioItem(
            wx.ID_ANY,
            t("ui.menu.language.auto"),
            t("ui.menu.language.auto.hint"),
        )
        self._language_items[None] = auto_item
        en_item = language_menu.AppendRadioItem(
            wx.ID_ANY,
            t("ui.menu.language.english"),
            "",
        )
        self._language_items["en"] = en_item
        de_item = language_menu.AppendRadioItem(
            wx.ID_ANY,
            t("ui.menu.language.deutsch"),
            "",
        )
        self._language_items["de"] = de_item
        self._language_items.get(current_pref, auto_item).Check(True)
        menubar.Append(language_menu, t("ui.menu.language"))

        # Help menu: a single "About / Help" entry that points to the
        # new combined dialog (app info + README + bug-report shortcut).
        # Use wx.ID_ANY so the entry stays in the Help menu on every
        # platform — wx.ID_ABOUT would auto-relocate into the macOS App
        # menu and leave Help empty. We also bind ID_ABOUT globally so
        # the App menu's auto-generated "About" entry on macOS still
        # triggers our dialog.
        help_menu = wx.Menu()
        about_item = help_menu.Append(
            wx.ID_ANY,
            t("ui.menu.about"),
            t("ui.menu.about.hint"),
        )
        diagnostics_item = help_menu.Append(
            wx.ID_ANY,
            t("ui.menu.diagnostics"),
            t("ui.menu.diagnostics.hint"),
        )
        menubar.Append(help_menu, t("ui.menu.help"))

        self.SetMenuBar(menubar)
        # Strip the macOS auto "Search Help" field from the Help menu: it
        # captures VoiceOver focus on every menu open and hides the actual
        # items behind a "search field, interactive" announcement.
        macos_a11y.clear_help_menu_search()
        if quit_item is not None:
            self.Bind(wx.EVT_MENU, self._on_quit_menu, quit_item)
        # macOS App-menu auto-entries: wire Quit and About by ID so they
        # respond whether wx created them or the OS did.
        self.Bind(wx.EVT_MENU, self._on_quit_menu, id=wx.ID_EXIT)
        self.Bind(wx.EVT_MENU, self._on_about, id=wx.ID_ABOUT)
        self.Bind(wx.EVT_MENU, self._on_about, about_item)
        self.Bind(wx.EVT_MENU, self._on_diagnostics, diagnostics_item)
        # F1 routes through the same handler — accelerator wired below.
        self._about_menu_id = about_item.GetId()
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

    def _on_diagnostics(self, event: wx.CommandEvent) -> None:
        show_diagnostics(self)

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
            t("ui.menu.language.restart_body"),
            t("ui.menu.language.restart_title"),
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
            genre_profiles.GENRES[k].display_name
            for k in self._selected_genre_keys
            if k in genre_profiles.GENRES
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
    # Report sections
    # ------------------------------------------------------------------ #

    def _update_sections_label(self) -> None:
        if not self._section_keys:
            placeholder = t("ui.placeholder.no_sections")
            self.sections_value_label.ChangeValue(placeholder)
            a11y.update_help(self.sections_value_label, placeholder)
            return
        if set(self._section_keys) == set(SECTION_ORDER):
            text = t("ui.list.sections_all")
            self.sections_value_label.ChangeValue(text)
            a11y.update_help(self.sections_value_label, text)
            return
        names = [section_label(k) for k in SECTION_ORDER if k in self._section_keys]
        count = len(names)
        header_key = (
            "ui.list.sections_selected.one"
            if count == 1
            else "ui.list.sections_selected.other"
        )
        text = t(header_key, count=count) + "\n" + "\n".join(
            f"{i + 1}. {n}" for i, n in enumerate(names)
        )
        self.sections_value_label.ChangeValue(text)
        a11y.update_help(self.sections_value_label, text)

    def _on_choose_sections(self, event: wx.Event) -> None:
        dlg = SectionsDialog(self, selected_keys=self._section_keys)
        try:
            if dlg.ShowModal() != wx.ID_OK:
                return
            self._section_keys = dlg.selected_keys()
        finally:
            dlg.Destroy()
        # Persist so the choice survives a restart. Saving the full list
        # of keys (rather than a boolean per key) means future versions
        # can add new sections without rewriting older preference files.
        preferences.save_report_sections(self._section_keys)
        self._update_sections_label()
        log.info("report sections selected: %s", self._section_keys)

    # ------------------------------------------------------------------ #
    # Project mode
    # ------------------------------------------------------------------ #

    def _on_toggle_project(self, event: wx.Event) -> None:
        self._project_mode = bool(self.project_check.GetValue())
        log.info("project mode toggled: %s (not persisted)", self._project_mode)

    def _derive_project_name(self) -> str | None:
        """Pick a sensible project name from the input list.

        Uses the common parent folder if every input lives below the
        same directory; otherwise returns ``None`` and the pipeline
        falls back to a generic localised label.
        """
        return self._common_folder_name(self._target_paths)

    def _derive_reference_name(self) -> str | None:
        """Same heuristic as :meth:`_derive_project_name` but for the
        reference project.
        """
        if len(self._reference_paths) <= 1:
            return None
        return self._common_folder_name(self._reference_paths)

    @staticmethod
    def _common_folder_name(paths: list[str]) -> str | None:
        if not paths:
            return None
        from os.path import commonpath

        try:
            common = Path(commonpath(paths))
        except ValueError:
            return None
        if common.is_dir():
            return common.name or None
        return None

    # ------------------------------------------------------------------ #
    # Reference file
    # ------------------------------------------------------------------ #

    def _update_reference_label(self) -> None:
        n = len(self._reference_paths)
        if n == 0:
            placeholder = t("ui.placeholder.no_reference")
            self.reference_label.ChangeValue(placeholder)
            a11y.update_help(self.reference_label, placeholder)
            return
        if n == 1:
            name = Path(self._reference_paths[0]).name
            text = t("ui.list.reference_selected", name=name)
        else:
            # Multi-file reference: list each file the same way the
            # target list does, with a header that names the count so
            # the screen reader announces "5 reference files combined…"
            # before walking through the names.
            header = t("ui.list.reference_project_selected", count=n)
            lines = [
                f"{i + 1}. {Path(p).name}"
                for i, p in enumerate(self._reference_paths)
            ]
            text = header + "\n" + "\n".join(lines)
        self.reference_label.ChangeValue(text)
        a11y.update_help(self.reference_label, text)

    def _on_open_reference(self, event: wx.Event) -> None:
        with wx.FileDialog(
            self,
            message=t("ui.file_dialog.reference"),
            wildcard=t("ui.file_dialog.wildcard"),
            # FD_MULTIPLE lets the user pick a whole album or audio drama
            # as a single reference, which then gets combined with the
            # same project-mode pipeline as the target.
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST | wx.FD_MULTIPLE,
        ) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            paths = list(dlg.GetPaths())
        if not paths:
            return
        self._set_reference_paths(paths)

    def _set_reference_paths(self, paths: list[str]) -> None:
        """Replace the current reference selection with ``paths``.

        Folder paths are walked recursively just like in the target
        list — drag-and-drop a whole album folder and every audio file
        inside becomes part of the reference project.
        """
        expanded = expand_audio_paths(paths)
        if not expanded:
            log.info(
                "reference picker: no audio files found in %d input(s)",
                len(paths),
            )
            return
        self._reference_paths = expanded
        log.info(
            "reference selected: %d file(s)", len(self._reference_paths)
        )
        self._update_reference_label()
        self.clear_reference_btn.Enable()

    def _on_clear_reference(self, event: wx.Event) -> None:
        self._reference_paths = []
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
        # Project mode with a single file is almost always a slip — the
        # mode only adds value when there are multiple tracks to combine.
        # Surface the mismatch and offer the safer alternative (regular
        # single-file analysis) as the default answer; the user can still
        # opt to run the file as a one-track "project" if they really
        # want to.
        if self._project_mode and len(self._target_paths) == 1:
            answer = wx.MessageBox(
                t("ui.project_mode.single_file.body"),
                t("ui.project_mode.single_file.title"),
                style=wx.YES_NO | wx.CANCEL | wx.YES_DEFAULT
                | wx.ICON_WARNING,
                parent=self,
            )
            if answer == wx.CANCEL:
                # User wants to step back and adjust the file list first.
                return
            if answer == wx.YES:
                self._project_mode = False
                self.project_check.SetValue(False)
                log.info(
                    "project mode auto-disabled: only one file in the list"
                )
            # answer == wx.NO falls through and runs a one-track project.

        # RAM pre-check: estimate the worst-case footprint and, only if
        # it's concerning, ask the user before touching the UI or
        # starting the click ticker. On systems with plenty of RAM this
        # silently logs the numbers and falls through to the analysis.
        if not self._ram_precheck():
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
        sections = ReportSections.from_keys(self._section_keys)
        log.info(
            "analyze clicked: %d file(s) genres=%s reference=%d "
            "sections=%s project=%s",
            len(self._target_paths),
            genre_keys,
            len(self._reference_paths),
            self._section_keys,
            self._project_mode,
        )
        self._worker = start_analysis(
            self._target_paths,
            genre_keys,
            self._reference_paths or None,
            self._on_analysis_done,
            self._on_analysis_failed,
            self._on_analysis_progress,
            sections=sections,
            project_mode=self._project_mode,
            project_name=self._derive_project_name(),
            reference_name=self._derive_reference_name(),
            # The pre-check above already accepted (or quietly cleared)
            # the worst-case footprint. We still pass a callback so the
            # pipeline keeps emitting its per-file RAM-estimate logs,
            # but force-accept here to avoid a second mid-run dialog.
            on_confirm_memory=lambda _estimate: True,
        )

    def _ram_precheck(self) -> bool:
        """Estimate RAM needs up front and confirm with the user if risky.

        Returns ``True`` when the analysis may proceed (either the
        estimate was harmless or the user clicked "yes" in the
        warning dialog). Returns ``False`` when the user cancelled —
        in that case the caller must leave the UI untouched and not
        start the click ticker or worker.
        """
        estimate = self._collect_ram_estimate()
        if estimate is None:
            return True
        log.info(
            "ram pre-check: %s estimated %s (available %s, total %s)",
            estimate.label,
            format_bytes(estimate.estimated_bytes),
            format_bytes(estimate.available_bytes),
            format_bytes(estimate.total_bytes),
        )
        if not estimate.is_concerning:
            return True
        return self._on_confirm_memory(estimate)

    def _collect_ram_estimate(self) -> MemoryEstimate | None:
        """Build the worst-case RAM estimate for the run that's about to start.

        Project mode is one combined pass over all targets, so it gets
        the project-overhead formula. Otherwise we estimate each target
        individually and report the largest — that's the file that will
        either fit or trip the warning. A multi-file reference adds its
        own project-style estimate; a single-file reference is treated
        like any other file.
        """
        candidates: list[MemoryEstimate] = []
        if self._target_paths:
            if self._project_mode:
                candidates.append(
                    build_estimate(
                        label=self._derive_project_name()
                        or t("project.default_name"),
                        estimated_bytes=estimate_project_bytes(self._target_paths),
                    )
                )
            else:
                for raw in self._target_paths:
                    candidates.append(
                        build_estimate(
                            label=Path(raw).name,
                            estimated_bytes=estimate_file_bytes(raw),
                        )
                    )
        if self._reference_paths:
            if len(self._reference_paths) == 1:
                ref = self._reference_paths[0]
                candidates.append(
                    build_estimate(
                        label=Path(ref).name,
                        estimated_bytes=estimate_file_bytes(ref),
                    )
                )
            else:
                candidates.append(
                    build_estimate(
                        label=self._derive_reference_name()
                        or t("project.reference_default_name"),
                        estimated_bytes=estimate_project_bytes(
                            self._reference_paths
                        ),
                    )
                )
        if not candidates:
            return None
        return max(candidates, key=lambda e: e.estimated_bytes)

    def _on_confirm_memory(self, estimate: MemoryEstimate) -> bool:
        """Show the RAM warning and return True if the user wants to proceed.

        Called from the pre-check on the UI thread before the worker —
        and therefore before the click ticker — starts, so the dialog
        is not accompanied by the analysis-running click. Pre-formats
        the numbers so the dialog reads cleanly to screen readers —
        one short sentence per fact, not a wall of numbers.
        """
        log.warning(
            "ram guard triggered for %s: estimated %s, available %s, total %s",
            estimate.label,
            format_bytes(estimate.estimated_bytes),
            format_bytes(estimate.available_bytes),
            format_bytes(estimate.total_bytes),
        )
        if estimate.available_bytes is not None:
            availability_line = t(
                "ui.ram_warning.available",
                available=format_bytes(estimate.available_bytes),
                total=format_bytes(estimate.total_bytes),
            )
        else:
            availability_line = t("ui.ram_warning.available_unknown")
        body = "\n\n".join(
            [
                t("ui.ram_warning.lead", label=estimate.label),
                t(
                    "ui.ram_warning.estimate",
                    estimated=format_bytes(estimate.estimated_bytes),
                ),
                availability_line,
                t("ui.ram_warning.question"),
            ]
        )
        answer = wx.MessageBox(
            body,
            t("ui.ram_warning.title"),
            style=wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING,
            parent=self,
        )
        proceed = answer == wx.YES
        log.info("user %s the ram warning", "accepted" if proceed else "cancelled")
        return proceed

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
        except Exception as exc:  # noqa: BLE001 — never block shutdown on audio cleanup
            log.debug("click ticker stop raised during shutdown: %s", exc)
        self.Destroy()
