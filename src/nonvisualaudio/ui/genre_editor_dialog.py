"""Genre-profile editor dialog.

Shows all genre profiles in a single listbox with an origin tag
(``built-in`` / ``user`` / ``modified``) so a screen reader can read
the status of each entry up front. The buttons on the right mutate
the user-override JSON and reload the live ``GENRES`` dict; the list
rebuilds itself after every action so the displayed state always
matches what is on disk.
"""

from __future__ import annotations

import logging
from typing import Any

import wx

from nonvisualaudio.localization import t
from nonvisualaudio.reporting import genre_profiles
from nonvisualaudio.ui import a11y, theme
from nonvisualaudio.ui.genre_form_dialog import GenreFormDialog

log = logging.getLogger("nonvisualaudio.genre_editor")


class GenreEditorDialog(wx.Dialog):
    """Manage the user's genre profile overrides."""

    def __init__(self, parent: wx.Window | None) -> None:
        super().__init__(
            parent,
            title=t("ui.genre_editor.title"),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self.SetName(t("ui.genre_editor.name"))
        self.SetHelpText(t("ui.genre_editor.help"))

        # Local working copy of the user override — mutated in place,
        # written back on every action.
        self._override = genre_profiles.user_override_raw()

        root = wx.BoxSizer(wx.VERTICAL)

        intro = wx.StaticText(self, label=t("ui.genre_editor.intro"))
        root.Add(intro, flag=wx.ALL, border=10)

        # Filter row: an "origin" picker that narrows the list to all,
        # built-in only, user-added, or modified built-ins. Default
        # stays "all" so the dialog opens looking like before.
        filter_row = wx.BoxSizer(wx.HORIZONTAL)
        filter_label = wx.StaticText(
            self, label=t("ui.genre_editor.filter_label")
        )
        filter_row.Add(filter_label, flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=8)
        # Filter values map to the same origin tokens that
        # genre_profiles.profile_origin returns; "all" is a special
        # sentinel that disables filtering.
        self._filter_keys: list[str] = ["all", "built_in", "user", "modified"]
        filter_choices = [
            t("ui.genre_editor.filter.all"),
            t("ui.genre_editor.filter.built_in"),
            t("ui.genre_editor.filter.user"),
            t("ui.genre_editor.filter.modified"),
        ]
        self.filter_ctrl = wx.Choice(self, choices=filter_choices)
        self.filter_ctrl.SetSelection(0)
        a11y.set_a11y(
            self.filter_ctrl,
            t("ui.genre_editor.filter_name"),
            t("ui.genre_editor.filter_hint"),
        )
        self.filter_ctrl.Bind(wx.EVT_CHOICE, self._on_filter_changed)
        filter_row.Add(self.filter_ctrl, proportion=1, flag=wx.EXPAND)
        root.Add(filter_row, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=10)

        body = wx.BoxSizer(wx.HORIZONTAL)

        self.list_ctrl = wx.ListBox(self, style=wx.LB_SINGLE)
        a11y.set_a11y(
            self.list_ctrl,
            t("ui.genre_editor.list_name"),
            t("ui.genre_editor.list_hint"),
        )
        self.list_ctrl.SetMinSize(wx.Size(420, 320))
        self.list_ctrl.Bind(wx.EVT_LISTBOX, self._on_selection_changed)
        self.list_ctrl.Bind(wx.EVT_LISTBOX_DCLICK, self._on_edit)
        body.Add(self.list_ctrl, proportion=1, flag=wx.EXPAND | wx.RIGHT, border=10)

        # --- Button column
        button_col = wx.BoxSizer(wx.VERTICAL)
        self.add_btn = wx.Button(self, label=t("ui.btn.add_new"))
        a11y.set_a11y(
            self.add_btn,
            t("ui.genre_editor.add_name"),
            t("ui.genre_editor.add_hint"),
        )
        self.add_btn.Bind(wx.EVT_BUTTON, self._on_add)
        button_col.Add(self.add_btn, flag=wx.EXPAND | wx.BOTTOM, border=6)

        self.edit_btn = wx.Button(self, label=t("ui.btn.edit"))
        a11y.set_a11y(
            self.edit_btn,
            t("ui.genre_editor.edit_name"),
            t("ui.genre_editor.edit_hint"),
        )
        self.edit_btn.Bind(wx.EVT_BUTTON, self._on_edit)
        button_col.Add(self.edit_btn, flag=wx.EXPAND | wx.BOTTOM, border=6)

        self.delete_btn = wx.Button(self, label=t("ui.btn.delete"))
        a11y.set_a11y(
            self.delete_btn,
            t("ui.genre_editor.delete_name"),
            t("ui.genre_editor.delete_hint"),
        )
        self.delete_btn.Bind(wx.EVT_BUTTON, self._on_delete)
        button_col.Add(self.delete_btn, flag=wx.EXPAND | wx.BOTTOM, border=6)

        self.reset_btn = wx.Button(self, label=t("ui.btn.reset_builtin"))
        a11y.set_a11y(
            self.reset_btn,
            t("ui.genre_editor.reset_name"),
            t("ui.genre_editor.reset_hint"),
        )
        self.reset_btn.Bind(wx.EVT_BUTTON, self._on_reset)
        button_col.Add(self.reset_btn, flag=wx.EXPAND | wx.BOTTOM, border=6)

        body.Add(button_col, flag=wx.EXPAND)
        root.Add(body, proportion=1, flag=wx.EXPAND | wx.LEFT | wx.RIGHT, border=10)

        # Bottom close row.
        close_row = wx.BoxSizer(wx.HORIZONTAL)
        close_row.AddStretchSpacer(1)
        self.close_btn = wx.Button(self, wx.ID_CLOSE, label=t("ui.btn.close"))
        a11y.set_a11y(
            self.close_btn,
            t("ui.genre_editor.close_name"),
            t("ui.genre_editor.close_hint"),
        )
        self.close_btn.Bind(wx.EVT_BUTTON, lambda _evt: self.EndModal(wx.ID_CLOSE))
        close_row.Add(self.close_btn)
        root.Add(close_row, flag=wx.EXPAND | wx.ALL, border=10)

        self.SetSizer(root)
        self.SetInitialSize(wx.Size(680, 560))
        self.CentreOnParent()

        self.Bind(wx.EVT_CHAR_HOOK, self._on_char)

        self._rebuild_list()
        self._refresh_button_state()
        theme.apply(self)
        self.list_ctrl.SetFocus()

    # ------------------------------------------------------------------ #
    # List rendering
    # ------------------------------------------------------------------ #

    def _origin_label(self, key: str) -> str:
        origin = genre_profiles.profile_origin(key)
        if origin == "user":
            return t("ui.genre_editor.origin.user")
        if origin == "modified":
            return t("ui.genre_editor.origin.modified")
        return t("ui.genre_editor.origin.built_in")

    def _current_filter(self) -> str:
        idx = self.filter_ctrl.GetSelection()
        if 0 <= idx < len(self._filter_keys):
            return self._filter_keys[idx]
        return "all"

    def _on_filter_changed(self, event: wx.CommandEvent) -> None:
        self._rebuild_list()
        self._refresh_button_state()

    def _rebuild_list(self) -> None:
        # Use the already-localised GenreProfile objects so display and
        # sort order follow the current UI language.
        loaded = [genre_profiles.GENRES[k] for k in genre_profiles.GENRES]
        cat_order = {name: i for i, name in enumerate(genre_profiles.CATEGORY_ORDER)}
        loaded.sort(
            key=lambda p: (
                cat_order.get(p.category, 10_000),
                p.display_name.lower(),
            )
        )
        active_filter = self._current_filter()
        self._list_keys: list[str] = []
        labels: list[str] = []
        for profile in loaded:
            origin_key = genre_profiles.profile_origin(profile.key)
            if active_filter != "all" and origin_key != active_filter:
                continue
            origin = self._origin_label(profile.key)
            labels.append(
                t(
                    "ui.genre_editor.entry",
                    origin=origin,
                    category=profile.category,
                    display_name=profile.display_name or profile.key,
                )
            )
            self._list_keys.append(profile.key)
        previous = self.list_ctrl.GetSelection()
        self.list_ctrl.Set(labels)
        if labels:
            new_selection = previous if 0 <= previous < len(labels) else 0
            self.list_ctrl.SetSelection(new_selection)

    def _on_selection_changed(self, event: wx.CommandEvent) -> None:
        self._refresh_button_state()

    def _refresh_button_state(self) -> None:
        key = self._selected_key()
        has_selection = key is not None
        self.edit_btn.Enable(has_selection)
        self.delete_btn.Enable(
            has_selection and genre_profiles.profile_origin(key) == "user"
        )
        self.reset_btn.Enable(
            has_selection and genre_profiles.profile_origin(key) == "modified"
        )

    def _selected_key(self) -> str | None:
        idx = self.list_ctrl.GetSelection()
        if idx == wx.NOT_FOUND:
            return None
        if 0 <= idx < len(self._list_keys):
            return self._list_keys[idx]
        return None

    # ------------------------------------------------------------------ #
    # Actions
    # ------------------------------------------------------------------ #

    def _on_add(self, event: wx.Event) -> None:
        dlg = GenreFormDialog(
            self,
            existing_profile=None,
            available_categories=genre_profiles.raw_categories(),
        )
        try:
            if dlg.ShowModal() != wx.ID_OK:
                return
            profile, new_category = dlg.result()
        finally:
            dlg.Destroy()
        if profile is None:
            return
        if self._key_in_use(profile["key"]):
            wx.MessageBox(
                t("ui.genre_editor.duplicate_key", key=profile["key"]),
                t("ui.genre_editor.duplicate_title"),
                style=wx.OK | wx.ICON_ERROR,
                parent=self,
            )
            return
        self._apply_new_category(new_category)
        self._override["profiles"].append(profile)
        self._save_and_refresh(select_key=profile["key"])
        log.info("added user genre profile: %s", profile["key"])

    def _on_edit(self, event: wx.Event) -> None:
        key = self._selected_key()
        if key is None:
            return
        existing = genre_profiles.raw_profile(key)
        if existing is None:
            return
        dlg = GenreFormDialog(
            self,
            existing_profile=existing,
            available_categories=genre_profiles.raw_categories(),
        )
        try:
            if dlg.ShowModal() != wx.ID_OK:
                return
            profile, new_category = dlg.result()
        finally:
            dlg.Destroy()
        if profile is None:
            return
        self._apply_new_category(new_category)
        self._upsert_override_profile(profile)
        self._save_and_refresh(select_key=profile["key"])
        log.info("edited genre profile: %s", profile["key"])

    def _on_delete(self, event: wx.Event) -> None:
        key = self._selected_key()
        if key is None:
            return
        if genre_profiles.profile_origin(key) != "user":
            return
        if (
            wx.MessageBox(
                t("ui.genre_editor.delete_prompt", key=key),
                t("ui.genre_editor.delete_title"),
                style=wx.YES_NO | wx.ICON_QUESTION,
                parent=self,
            )
            != wx.YES
        ):
            return
        self._override["profiles"] = [
            p for p in self._override["profiles"] if p.get("key") != key
        ]
        self._prune_unused_categories()
        self._save_and_refresh()
        log.info("deleted user genre profile: %s", key)

    def _on_reset(self, event: wx.Event) -> None:
        key = self._selected_key()
        if key is None:
            return
        if genre_profiles.profile_origin(key) != "modified":
            return
        if (
            wx.MessageBox(
                t("ui.genre_editor.reset_prompt", key=key),
                t("ui.genre_editor.reset_title"),
                style=wx.YES_NO | wx.ICON_QUESTION,
                parent=self,
            )
            != wx.YES
        ):
            return
        self._override["profiles"] = [
            p for p in self._override["profiles"] if p.get("key") != key
        ]
        self._prune_unused_categories()
        self._save_and_refresh(select_key=key)
        log.info("reset genre profile to built-in: %s", key)

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _key_in_use(self, key: str) -> bool:
        return any(p.get("key") == key for p in genre_profiles.raw_profiles())

    def _apply_new_category(self, new_category: dict[str, Any] | None) -> None:
        if new_category is None:
            return
        # Add only if the key is not already present in the override.
        if any(
            c.get("key") == new_category["key"] for c in self._override["categories"]
        ):
            return
        # Also skip if the bundle already has this key (shouldn't happen
        # because _resolve_category guards against it, but belt-and-braces).
        if any(
            c.get("key") == new_category["key"]
            for c in genre_profiles.raw_categories()
        ):
            return
        self._override["categories"].append(new_category)

    def _upsert_override_profile(self, profile: dict[str, Any]) -> None:
        for i, existing in enumerate(self._override["profiles"]):
            if existing.get("key") == profile["key"]:
                self._override["profiles"][i] = profile
                return
        self._override["profiles"].append(profile)

    def _prune_unused_categories(self) -> None:
        """Remove user-added categories that no longer have any profile."""
        bundle_cat_keys = {c["key"] for c in genre_profiles.raw_categories()}
        used_cat_keys = {p.get("category_key") for p in self._override["profiles"]}
        # Also keep categories that every surviving bundle profile uses —
        # but since bundle categories are always present in the bundle
        # JSON, we only need to check the override's own categories.
        self._override["categories"] = [
            c
            for c in self._override["categories"]
            # Keep if it's still in use by any user override profile,
            # OR if it's overriding a bundle category (different display
            # name) that is still referenced somewhere in the merged set.
            if c.get("key") in used_cat_keys
            or c.get("key") in bundle_cat_keys
        ]

    def _save_and_refresh(self, *, select_key: str | None = None) -> None:
        genre_profiles.save_user_overrides(
            self._override["categories"],
            self._override["profiles"],
        )
        # Reload our working copy from disk so future edits start from
        # the canonical state (duplicates/pruning etc. reconciled).
        self._override = genre_profiles.user_override_raw()
        self._rebuild_list()
        if select_key is not None and select_key in self._list_keys:
            self.list_ctrl.SetSelection(self._list_keys.index(select_key))
        self._refresh_button_state()

    def _on_char(self, event: wx.KeyEvent) -> None:
        code = event.GetKeyCode()
        if code == wx.WXK_ESCAPE:
            self.EndModal(wx.ID_CLOSE)
            return
        if code == wx.WXK_RETURN and self.list_ctrl.HasFocus():
            self._on_edit(event)
            return
        event.Skip()
