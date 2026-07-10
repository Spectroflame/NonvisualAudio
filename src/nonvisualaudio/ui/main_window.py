"""Main application window."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import wx

from nonvisualaudio import preferences
from nonvisualaudio.analysis.memory import (
    MemoryEstimate,
    collect_run_estimate,
    format_bytes,
)
from nonvisualaudio.errors import UserFacingError
from nonvisualaudio.localization import t
from nonvisualaudio.reporting import genre_profiles
from nonvisualaudio.reporting.builder import SECTION_ORDER, ReportSections
from nonvisualaudio.reporting.templates import ReportDoc
from nonvisualaudio.ui import a11y
from nonvisualaudio.ui import macos_a11y
from nonvisualaudio.ui import theme
from nonvisualaudio.ui.about_dialog import show_about
from nonvisualaudio.ui.clipboard_paths import read_clipboard_paths
from nonvisualaudio.ui.click_sound import ClickTicker
from nonvisualaudio.ui.diagnostics_dialog import show_diagnostics
from nonvisualaudio.ui.drop import common_folder_name, expand_audio_paths
from nonvisualaudio.ui.error_dialog import show_error
from nonvisualaudio.ui.eta import EtaEstimator
from nonvisualaudio.ui.genre_dialog import GenreDialog
from nonvisualaudio.ui.genre_editor_dialog import GenreEditorDialog
from nonvisualaudio.ui.project_prompt import should_offer_project_mode
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
        # The frame is sized from its content at the end of __init__
        # (see the SetClientSize / SetMinSize block) rather than from a
        # fixed guess: a hard-coded height could be smaller than what the
        # controls actually need — especially with longer German labels —
        # and would clip the bottom-most control (the Analyze button).
        super().__init__(
            parent=None,
            title=t("ui.main.title"),
        )
        self.SetName(t("ui.main.title"))

        self._target_paths: list[str] = []
        # Reference can be a single file (the historical case) or a
        # multi-file/folder selection that we treat as a "reference
        # project". An empty list means no reference comparison.
        self._reference_paths: list[str] = []
        self._click_ticker = ClickTicker(self)
        self._worker = None  # keep a reference to the running worker
        # Cancellation bookkeeping. ``_run_token`` is bumped every time an
        # analysis starts or is cancelled; worker callbacks capture the
        # token they were started with and are dropped if it no longer
        # matches, so a late callback from an abandoned run can never
        # paint results or errors over a fresh run. ``_analysis_running``
        # gates the cancel action; ``_cancel_dialog_open`` blocks a second
        # confirmation prompt; ``_pending_result`` carries a result that
        # arrived while the confirmation prompt was open.
        self._run_token: int = 0
        self._analysis_running: bool = False
        self._cancel_dialog_open: bool = False
        self._pending_result: tuple | None = None
        # Remaining-time estimate for the progress display; started on
        # Analyze, reset when the analysis stops. See ui.eta.EtaEstimator.
        self._eta = EtaEstimator()
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
        # One-time "use project mode?" nudge: set after the prompt was
        # answered (either way) or after the user switched project mode
        # on manually; cleared together with the target list so a fresh
        # batch of files can trigger the question again.
        self._project_prompt_dismissed: bool = False

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
        # One button toggles between starting and cancelling: it reads
        # "Analyze" while idle and "Cancel" while a run is in progress, so
        # there is only ever one primary action. The click is routed
        # through a state-aware dispatcher; the visible label and the
        # screen-reader name/hint are swapped by _set_analyze_button_state.
        self.analyze_btn = wx.Button(panel, label=t("ui.btn.analyze"))
        a11y.set_a11y(self.analyze_btn, t("ui.label.analyze"), t("ui.hint.analyze"))
        self.analyze_btn.Bind(wx.EVT_BUTTON, self._on_analyze_or_cancel)
        self.analyze_btn.Disable()
        self.analyze_btn.SetDefault()
        root.Add(self.analyze_btn, flag=wx.LEFT | wx.RIGHT, border=10)

        # ----- Progress row: gauge + current-stage label, side by side -----
        # The gauge owns the percent (as its value, which the screen
        # reader announces as the control's state) and, during a run,
        # the remaining-time estimate folded into its name — so
        # "progress + runtime" reads as a single element. The label
        # beside it carries only what the analysis is currently doing.
        self.progress = wx.Gauge(panel, range=100, style=wx.GA_HORIZONTAL)
        a11y.set_a11y(
            self.progress,
            t("ui.label.progress_bar"),
            t("ui.hint.progress_bar"),
        )
        self.progress.Hide()

        # Read-only TextCtrl rather than StaticText so it sits in the
        # tab order: during analysis the user can Tab past the
        # (disabled) Analyze button and land on this control to hear the
        # current stage ("Dekodiere Audio"). A StaticText would be
        # skipped by the screen reader's focus traversal.
        self.progress_label = wx.TextCtrl(panel, style=wx.TE_READONLY)
        a11y.set_a11y(
            self.progress_label,
            t("ui.label.progress_stage"),
            t("ui.hint.progress_stage"),
        )
        self.progress_label.Hide()

        progress_row = wx.BoxSizer(wx.HORIZONTAL)
        progress_row.Add(
            self.progress,
            proportion=1,
            flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT,
            border=10,
        )
        progress_row.Add(self.progress_label, proportion=1, flag=wx.EXPAND)
        root.Add(progress_row, flag=wx.EXPAND | wx.ALL, border=10)

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
        # Dedicated command id for the Escape→cancel accelerator. It must
        # NOT be the Analyze button's id: while idle that button starts an
        # analysis, and Escape must never do that — it only ever cancels.
        self._cancel_cmd_id = wx.NewIdRef()
        accel = wx.AcceleratorTable(
            [
                wx.AcceleratorEntry(wx.ACCEL_CMD, ord("O"), self._get_id(self.open_btn)),
                wx.AcceleratorEntry(wx.ACCEL_CMD, ord("R"), self._get_id(self.analyze_btn)),
                # Escape raises the same "really cancel?" prompt as the
                # button; the handler is a no-op when nothing is running.
                wx.AcceleratorEntry(
                    wx.ACCEL_NORMAL,
                    wx.WXK_ESCAPE,
                    int(self._cancel_cmd_id),
                ),
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
        # Escape dispatches an EVT_MENU on the dedicated id → _on_cancel.
        self.Bind(wx.EVT_MENU, self._on_cancel, id=int(self._cancel_cmd_id))

        # Drag-and-drop: both the outer panel (big hit area for sighted
        # users) and the targets_view (the visual home of the file list)
        # accept audio file drops.
        drop_target_panel = _AudioDropTarget(self._add_paths)
        panel.SetDropTarget(drop_target_panel)
        drop_target_list = _AudioDropTarget(self._add_paths)
        self.targets_view.SetDropTarget(drop_target_list)

        # Intercept close to cancel a running analysis cleanly.
        self.Bind(wx.EVT_CLOSE, self._on_close)

        self.open_btn.SetFocus()

        # Paint the window with the chosen theme. Done at the end of
        # __init__ so every widget already exists.
        theme.apply(self)

        # Size the frame to its content instead of a fixed guess, so the
        # Analyze button (the bottom-most idle control) is always fully
        # visible on first open, in both UI languages.
        #
        # The progress gauge and stage label are hidden while idle, so a
        # plain content measurement would not reserve room for them — and
        # starting an analysis reveals them, which would then push the
        # lower controls off the bottom. To avoid that, show them only for
        # the measurement, take the sizer's minimum, then hide them again:
        # the reserved height stays, so revealing them later never causes a
        # new overflow. Width keeps a 760 px floor for a comfortable
        # sighted layout but grows if longer labels need more.
        self.progress.Show()
        self.progress_label.Show()
        content = root.CalcMin()
        self.progress.Hide()
        self.progress_label.Hide()
        self.SetClientSize(wx.Size(max(760, content.x), content.y))
        # Floor the window at its content size so the primary action can
        # never be shrunk out of view.
        self.SetMinSize(self.GetSize())
        self.Layout()
        self.Centre()

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

        # Edit menu: paste-from-clipboard plus the genre editor. The
        # paste item carries the Cmd/Ctrl+V accelerator that previously
        # rode on an EVT_CHAR_HOOK at frame level. Menu accelerators on
        # macOS route through the menu bar before any focused widget
        # can claim them, which fixed the "press Cmd+V twice" bug — the
        # char hook missed the first press whenever a native control
        # (TextCtrl, ListBox) had focus and consumed the key locally.
        edit_menu = wx.Menu()
        paste_item = edit_menu.Append(
            wx.ID_ANY,
            t("ui.menu.paste"),
            t("ui.menu.paste.hint"),
        )
        edit_menu.AppendSeparator()
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
        self.Bind(wx.EVT_MENU, self._on_paste_menu, paste_item)
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
        previous_count = len(self._target_paths)
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
        self._maybe_offer_project_mode(previous_count)

    # ------------------------------------------------------------------ #
    # Clipboard helpers
    # ------------------------------------------------------------------ #

    def _paste_destination_is_reference(self) -> bool:
        """Return True when the user is working in the reference area.

        Focus on the reference TextCtrl, the "Choose reference" button,
        or the "Clear reference" button all count as "in the reference
        area" for paste-routing purposes. Everything else (target list,
        target buttons, the panel itself, anything unfocused) routes to
        targets — the intuition is "paste lands where I am working".
        """
        focused = wx.Window.FindFocus()
        if focused is None:
            return False
        return focused in (
            self.reference_label,
            self.reference_btn,
            self.clear_reference_btn,
        )

    def _on_paste_menu(self, event: wx.CommandEvent) -> None:
        """Handle the Edit → Paste-Audio-Files menu item (Cmd/Ctrl+V).

        Switched from EVT_CHAR_HOOK to a menu accelerator because the
        char-hook variant missed the first press on macOS whenever a
        native control had focus and consumed Cmd+V locally — the user
        then had to press it again. Menu accelerators bypass widget
        event-routing entirely on macOS, so one press always fires.
        """
        paths = read_clipboard_paths()
        if not paths:
            log.info("paste menu: clipboard had no usable paths")
            return
        log.info("paste menu: %d raw clipboard path(s)", len(paths))
        if self._paste_destination_is_reference():
            self._set_reference_paths(paths)
        else:
            self._add_paths(paths)

    def _maybe_offer_clipboard_import(self) -> None:
        """Offer to import any audio files found on the clipboard.

        Fires once after window show. Silent if the clipboard does not
        contain recognisable audio paths so the dialog does not nag on
        every launch.
        """
        if self._startup_scan_done:
            return
        self._startup_scan_done = True
        raw = read_clipboard_paths()
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
        # A fresh filling of the list may ask the project-mode question
        # again — "analyze separately" only applied to the cleared batch.
        self._project_prompt_dismissed = False
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
        if self._project_mode:
            # Switching project mode on manually answers the question the
            # multi-file prompt would ask, so never bring it up for this
            # filling of the list — even if the mode is toggled off again.
            self._project_prompt_dismissed = True
        log.info("project mode toggled: %s (not persisted)", self._project_mode)

    def _maybe_offer_project_mode(self, previous_count: int) -> None:
        """Offer project mode once when an add crosses the threshold.

        Called after every successful target add with the list size from
        before the add. Fires only while project mode is off and only on
        the operation that pushes the list past the threshold; answering
        either way (or enabling the mode manually) keeps the prompt quiet
        until the target list is cleared and refilled.
        """
        count = len(self._target_paths)
        if not should_offer_project_mode(
            previous_count,
            count,
            self._project_mode,
            self._project_prompt_dismissed,
        ):
            return
        self._project_prompt_dismissed = True
        log.info("project prompt shown (%d targets)", count)
        if self._ask_use_project_mode(count):
            self._project_mode = True
            self.project_check.SetValue(True)
            log.info("project prompt: user chose project mode")
        else:
            log.info("project prompt: user chose separate analysis")

    def _ask_use_project_mode(self, count: int) -> bool:
        """Show the modal project-mode question; True means "as project".

        Deliberately no Cancel option: the files are already in the list
        at this point, the answer only decides the project-mode toggle.
        """
        dlg = wx.MessageDialog(
            self,
            t("ui.project_prompt.body", count=count),
            t("ui.project_prompt.title"),
            style=wx.YES_NO | wx.YES_DEFAULT | wx.ICON_QUESTION,
        )
        try:
            dlg.SetYesNoLabels(
                t("ui.project_prompt.btn.project"),
                t("ui.project_prompt.btn.separate"),
            )
            return dlg.ShowModal() == wx.ID_YES
        finally:
            dlg.Destroy()

    def _derive_project_name(self) -> str | None:
        """Pick a sensible project name from the input list.

        Uses the common parent folder if every input lives below the
        same directory; otherwise returns ``None`` and the pipeline
        falls back to a generic localised label.
        """
        return common_folder_name(self._target_paths)

    def _derive_reference_name(self) -> str | None:
        """Same heuristic as :meth:`_derive_project_name` but for the
        reference project.
        """
        if len(self._reference_paths) <= 1:
            return None
        return common_folder_name(self._reference_paths)

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
        # While a run is in progress the button is the always-enabled
        # Cancel action; target-list changes must not disable it.
        if self._analysis_running:
            return
        has_targets = bool(self._target_paths)
        self.analyze_btn.Enable(has_targets)
        self.clear_targets_btn.Enable(has_targets)

    def _set_analyze_button_state(self, running: bool) -> None:
        """Toggle the single action button between Analyze and Cancel.

        Updates the visible label *and* the screen-reader name/hint so the
        announcement matches what the button now does.
        """
        if running:
            self.analyze_btn.SetLabel(t("ui.btn.cancel"))
            a11y.set_a11y(
                self.analyze_btn, t("ui.label.cancel"), t("ui.hint.cancel")
            )
        else:
            self.analyze_btn.SetLabel(t("ui.btn.analyze"))
            a11y.set_a11y(
                self.analyze_btn, t("ui.label.analyze"), t("ui.hint.analyze")
            )

    def _on_analyze_or_cancel(self, event: wx.Event) -> None:
        """Route the one action button to start or cancel by current state."""
        if self._analysis_running:
            self._on_cancel(event)
        else:
            self._on_analyze(event)

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

        # New run: bump the token so any straggler callback from a
        # previous (e.g. just-cancelled) run is ignored from here on.
        self._run_token += 1
        token = self._run_token
        self._analysis_running = True
        self._pending_result = None
        self.open_btn.Disable()
        # Flip the (still-enabled) Analyze button into its Cancel role. The
        # button keeps the focus it already holds from the click / keyboard
        # activation, so the abort action stays right under the user without
        # us forcing focus: an explicit SetFocus() here would make VoiceOver
        # re-announce "Cancel, button" on every start, which reads as a delay
        # before the analysis. Escape still cancels via its frame-level
        # accelerator regardless of where focus sits.
        self._set_analyze_button_state(running=True)
        self.SetStatusText(t("status.running"))
        self.progress.SetValue(0)
        # Reset the gauge name in case a previous run left a remaining-time
        # estimate folded into it; the first progress tick fills it back in.
        a11y.update_name(self.progress, t("ui.label.progress_bar"))
        self.progress.Show()
        self.progress_label.ChangeValue(t("ui.progress.starting"))
        self.progress_label.Show()
        self.Layout()
        self._eta.start()
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
            lambda report, had_failures, _tok=token: self._on_analysis_done(
                _tok, report, had_failures
            ),
            lambda err, _tok=token: self._on_analysis_failed(_tok, err),
            lambda percent, stage, _tok=token: self._on_analysis_progress(
                _tok, percent, stage
            ),
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
        """Worst-case RAM estimate for the run that's about to start.

        The heavy lifting lives in :func:`collect_run_estimate`; this
        wrapper only supplies the current window state and the localised
        fallback labels for the combined passes.
        """
        return collect_run_estimate(
            self._target_paths,
            self._reference_paths,
            project_mode=self._project_mode,
            project_label=self._derive_project_name()
            or t("project.default_name"),
            reference_label=self._derive_reference_name()
            or t("project.reference_default_name"),
        )

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

    def _on_analysis_progress(self, token: int, percent: int, stage: str) -> None:
        # Drop progress from an abandoned run (cancelled or superseded).
        if token != self._run_token:
            return
        # One element for "progress + runtime": the percent stays the
        # gauge's value (which the screen reader announces as its state)
        # while the remaining-time estimate rides in the gauge's name.
        # The neighbouring label then carries only the current stage, so
        # no widget announces the percent twice — the double reading the
        # split design used to produce confused screen-reader users.
        self.progress.SetValue(max(0, min(100, int(percent))))
        eta = self._eta.label_for(int(percent))
        if eta is None:
            gauge_name = t("ui.label.progress_bar")
            status = t("ui.progress.status", stage=stage)
        else:
            gauge_name = t("ui.progress.gauge.with_eta", eta=eta)
            status = t("ui.progress.status.with_eta", stage=stage, eta=eta)
        a11y.update_name(self.progress, gauge_name)
        self.progress_label.ChangeValue(stage)
        a11y.update_help(self.progress_label, t("ui.progress.hint", stage=stage))
        self.SetStatusText(status)

    def _stop_running_ui(self) -> None:
        self._analysis_running = False
        self._click_ticker.stop()
        # Restore the action button to its "Analyze" role before
        # _update_analyze_state re-enables it according to the target list.
        self._set_analyze_button_state(running=False)
        self.progress.Hide()
        self.progress.SetValue(0)
        # Drop any remaining-time estimate from the gauge name so a later
        # run never briefly shows a stale ETA before its first tick.
        a11y.update_name(self.progress, t("ui.label.progress_bar"))
        self.progress_label.ChangeValue("")
        self.progress_label.Hide()
        self._eta.reset()
        self.Layout()
        self.open_btn.Enable()
        self._update_analyze_state()

    def _on_analysis_done(
        self, token: int, report: ReportDoc, had_failures: bool
    ) -> None:
        # Stale callback from an abandoned run: ignore it completely so it
        # cannot raise a results window over a fresh run or the idle UI.
        if token != self._run_token:
            log.info("dropping stale 'done' callback from an abandoned run")
            return
        # The result landed while the "really cancel?" prompt is open. Stash
        # it and let _on_cancel deliver it once the prompt closes, so we
        # never stack a results window on top of the confirmation dialog.
        if self._cancel_dialog_open:
            self._pending_result = ("done", report, had_failures)
            return
        log.info(
            "analysis done, %d section(s) delivered to UI, failures=%s",
            len(report.sections),
            had_failures,
        )
        self._worker = None
        self._stop_running_ui()
        self.SetStatusText(
            t("status.partial") if had_failures else t("status.done")
        )
        self._show_results(report)

    def _on_analysis_failed(self, token: int, err: UserFacingError) -> None:
        if token != self._run_token:
            log.info("dropping stale 'error' callback from an abandoned run")
            return
        if self._cancel_dialog_open:
            self._pending_result = ("error", err)
            return
        log.error("analysis failed: %s", err)
        self._worker = None
        self._stop_running_ui()
        self.SetStatusText(t("status.error"))
        show_error(self, err)
        # Return focus to the analyze button for a quick retry, or to the
        # open button if no targets are left.
        if self.analyze_btn.IsEnabled():
            self.analyze_btn.SetFocus()
        else:
            self.open_btn.SetFocus()

    def _on_cancel(self, event: wx.Event) -> None:
        """Confirm, then abort the running analysis. Shared by button + Escape.

        Escape fires this even when nothing is running, so the no-op
        guard comes first. A second invocation while the confirmation
        prompt is open is ignored, so repeated presses cannot stack
        dialogs.
        """
        if not self._analysis_running or self._worker is None:
            return
        if self._cancel_dialog_open:
            return
        self._cancel_dialog_open = True
        try:
            answer = wx.MessageBox(
                t("ui.cancel_confirm.body"),
                t("ui.cancel_confirm.title"),
                style=wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION,
                parent=self,
            )
        finally:
            self._cancel_dialog_open = False

        # The run finished while the prompt was open: there is nothing left
        # to abort, so deliver the result that arrived in the meantime.
        if self._pending_result is not None:
            self._deliver_pending_result()
            return

        if answer != wx.YES:
            # "No / keep running": the button stays in its Cancel role and
            # the analysis carries on. Put focus back on it.
            log.info("cancel declined; analysis continues")
            if self._analysis_running:
                self.analyze_btn.SetFocus()
            return

        # Confirmed. Bump the token first so any callback the worker has
        # already queued is dropped, then signal the worker (which kills
        # any running ffmpeg) and return the UI to a clean idle state.
        log.info("cancel confirmed by user")
        self._run_token += 1
        worker = self._worker
        self._worker = None
        self._stop_running_ui()
        if worker is not None:
            worker.cancel()
        self.SetStatusText(t("status.cancelled"))
        if self.analyze_btn.IsEnabled():
            self.analyze_btn.SetFocus()
        else:
            self.open_btn.SetFocus()

    def _deliver_pending_result(self) -> None:
        """Deliver a result that arrived while the cancel prompt was open."""
        pending = self._pending_result
        self._pending_result = None
        self._worker = None
        if pending is None:
            return
        if pending[0] == "done":
            _kind, report, had_failures = pending
            self._stop_running_ui()
            self.SetStatusText(
                t("status.partial") if had_failures else t("status.done")
            )
            self._show_results(report)
        else:
            _kind, err = pending
            self._stop_running_ui()
            self.SetStatusText(t("status.error"))
            show_error(self, err)
            if self.analyze_btn.IsEnabled():
                self.analyze_btn.SetFocus()
            else:
                self.open_btn.SetFocus()

    def _show_results(self, report: ReportDoc) -> None:
        dlg = ResultsDialog(self, report=report)
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
        # process, but a still-running ffmpeg child would otherwise linger
        # until the OS reaps it — so signal cancel to kill it promptly.
        if self._worker is not None:
            try:
                self._worker.cancel()
            except Exception as exc:  # noqa: BLE001 — never block shutdown
                log.debug("worker cancel raised during shutdown: %s", exc)
        # Just stop the click to avoid a trailing tick after the window
        # disappears.
        try:
            self._click_ticker.stop()
        except Exception as exc:  # noqa: BLE001 — never block shutdown on audio cleanup
            log.debug("click ticker stop raised during shutdown: %s", exc)
        self.Destroy()
