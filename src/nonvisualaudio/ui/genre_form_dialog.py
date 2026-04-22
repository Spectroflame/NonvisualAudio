"""Form dialog to add or edit a single genre profile.

Keeps typing manual — every field is a plain ``wx.TextCtrl`` or
``wx.ComboBox``. No spin controls: a blind user reaches for the
number keys long before the screen-reader-awkward up/down arrows of
``SpinCtrlDouble``, and the extra visual polish is not worth losing
keystroke efficiency.

Validation runs on OK. A single bad field keeps the dialog open and
shows a screen-reader-friendly error via ``wx.MessageDialog``.
"""

from __future__ import annotations

import math
import re
from typing import Any

import wx

from nonvisualaudio.localization import t
from nonvisualaudio.reporting.genre_profiles import localised_field
from nonvisualaudio.ui import a11y, theme


_KEY_RE = re.compile(r"^[a-z0-9_]+$")
_SLUG_RE = re.compile(r"[^a-z0-9]+")

# Transliterate common German-language characters before stripping, so
# "Hörspiel" becomes "hoerspiel" rather than "h_rspiel". Other accented
# letters (é, à, ñ, etc.) fall through and the non-ASCII character gets
# replaced by an underscore, which is still a valid (if less pretty)
# identifier.
_TRANSLIT = str.maketrans(
    {
        "ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss",
        "Ä": "ae", "Ö": "oe", "Ü": "ue",
    }
)


def slugify(text: str) -> str:
    """Turn a display name into a lowercase underscored key."""
    translated = text.translate(_TRANSLIT).strip().lower()
    slug = _SLUG_RE.sub("_", translated).strip("_")
    return slug


class GenreFormDialog(wx.Dialog):
    """Dialog to create or edit one genre profile.

    ``existing_profile`` is the pre-filled raw dict (as stored in the
    JSON). Pass ``None`` to open in "add" mode. ``available_categories``
    is the full list of currently-known categories in display order.
    """

    def __init__(
        self,
        parent: wx.Window | None,
        *,
        existing_profile: dict[str, Any] | None,
        available_categories: list[dict[str, Any]],
    ) -> None:
        is_edit = existing_profile is not None
        super().__init__(
            parent,
            title=t("ui.genre_form.title_edit") if is_edit else t("ui.genre_form.title_add"),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self._is_edit = is_edit
        self._existing_key = existing_profile["key"] if is_edit else ""
        self._categories = list(available_categories)

        self._result_profile: dict[str, Any] | None = None
        self._result_new_category: dict[str, Any] | None = None

        root = wx.BoxSizer(wx.VERTICAL)

        intro = wx.StaticText(
            self,
            label=t("ui.genre_form.intro_edit") if is_edit else t("ui.genre_form.intro_add"),
        )
        root.Add(intro, flag=wx.ALL, border=10)

        grid = wx.FlexGridSizer(rows=0, cols=2, vgap=8, hgap=8)
        grid.AddGrowableCol(1, 1)

        # --- Key
        self.key_ctrl = wx.TextCtrl(
            self,
            value=existing_profile["key"] if is_edit else "",
            style=wx.TE_READONLY if is_edit else 0,
        )
        a11y.set_a11y(
            self.key_ctrl,
            t("ui.genre_form.field.key.name"),
            t("ui.genre_form.field.key.hint"),
        )
        self._add_row(grid, t("ui.genre_form.field.key"), self.key_ctrl)

        # --- Category
        # Categories now carry a {en, de} dict (or a legacy string); the
        # combobox lists the English form — that's the canonical label
        # used inside the JSON.
        category_names = [
            localised_field(c.get("display_name"), "en") or c.get("key", "")
            for c in self._categories
        ]
        self.category_ctrl = wx.ComboBox(
            self,
            choices=category_names,
            style=wx.CB_DROPDOWN,
        )
        if is_edit:
            existing_cat_key = existing_profile.get("category_key", "")
            for c in self._categories:
                if c["key"] == existing_cat_key:
                    self.category_ctrl.SetValue(
                        localised_field(c.get("display_name"), "en")
                        or c.get("key", "")
                    )
                    break
        a11y.set_a11y(
            self.category_ctrl,
            t("ui.genre_form.field.category.name"),
            t("ui.genre_form.field.category.hint"),
        )
        self._add_row(grid, t("ui.genre_form.field.category"), self.category_ctrl)

        # --- Display name (English + German pair)
        stored_display = existing_profile.get("display_name") if is_edit else None
        self.display_name_en_ctrl = wx.TextCtrl(
            self, value=localised_field(stored_display, "en") if is_edit else ""
        )
        a11y.set_a11y(
            self.display_name_en_ctrl,
            t("ui.genre_form.field.display_name_en.name"),
            t("ui.genre_form.field.display_name.hint"),
        )
        self._add_row(
            grid, t("ui.genre_form.field.display_name_en"), self.display_name_en_ctrl
        )
        self.display_name_de_ctrl = wx.TextCtrl(
            self, value=localised_field(stored_display, "de") if is_edit else ""
        )
        a11y.set_a11y(
            self.display_name_de_ctrl,
            t("ui.genre_form.field.display_name_de.name"),
            t("ui.genre_form.field.display_name.hint"),
        )
        self._add_row(
            grid, t("ui.genre_form.field.display_name_de"), self.display_name_de_ctrl
        )

        # --- Target LUFS
        self.target_lufs_ctrl = wx.TextCtrl(
            self, value=_fmt_num(existing_profile.get("target_lufs")) if is_edit else "-14.0"
        )
        a11y.set_a11y(
            self.target_lufs_ctrl,
            t("ui.genre_form.field.target_lufs.name"),
            t("ui.genre_form.field.target_lufs.hint"),
        )
        self._add_row(grid, t("ui.genre_form.field.target_lufs"), self.target_lufs_ctrl)

        # --- LRA low/high
        self.lra_low_ctrl = wx.TextCtrl(
            self, value=_fmt_num(existing_profile.get("lra_low")) if is_edit else "5.0"
        )
        a11y.set_a11y(
            self.lra_low_ctrl,
            t("ui.genre_form.field.lra_low.name"),
            t("ui.genre_form.field.lra_low.hint"),
        )
        self._add_row(grid, t("ui.genre_form.field.lra_low"), self.lra_low_ctrl)

        self.lra_high_ctrl = wx.TextCtrl(
            self, value=_fmt_num(existing_profile.get("lra_high")) if is_edit else "10.0"
        )
        a11y.set_a11y(
            self.lra_high_ctrl,
            t("ui.genre_form.field.lra_high.name"),
            t("ui.genre_form.field.lra_high.hint"),
        )
        self._add_row(grid, t("ui.genre_form.field.lra_high"), self.lra_high_ctrl)

        root.Add(grid, flag=wx.EXPAND | wx.LEFT | wx.RIGHT, border=10)

        # --- Notes (English + German pair, each multiline)
        stored_notes = existing_profile.get("notes") if is_edit else None

        notes_en_label = wx.StaticText(self, label=t("ui.genre_form.field.notes_en"))
        root.Add(notes_en_label, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=10)
        self.notes_en_ctrl = wx.TextCtrl(
            self,
            value=localised_field(stored_notes, "en") if is_edit else "",
            style=wx.TE_MULTILINE,
        )
        a11y.set_a11y(
            self.notes_en_ctrl,
            t("ui.genre_form.field.notes_en.name"),
            t("ui.genre_form.field.notes.hint"),
        )
        self.notes_en_ctrl.SetMinSize(wx.Size(-1, 60))
        root.Add(self.notes_en_ctrl, proportion=1, flag=wx.EXPAND | wx.LEFT | wx.RIGHT, border=10)

        notes_de_label = wx.StaticText(self, label=t("ui.genre_form.field.notes_de"))
        root.Add(notes_de_label, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=10)
        self.notes_de_ctrl = wx.TextCtrl(
            self,
            value=localised_field(stored_notes, "de") if is_edit else "",
            style=wx.TE_MULTILINE,
        )
        a11y.set_a11y(
            self.notes_de_ctrl,
            t("ui.genre_form.field.notes_de.name"),
            t("ui.genre_form.field.notes.hint"),
        )
        self.notes_de_ctrl.SetMinSize(wx.Size(-1, 60))
        root.Add(self.notes_de_ctrl, proportion=1, flag=wx.EXPAND | wx.ALL, border=10)

        # --- OK / Cancel
        button_sizer = self.CreateStdDialogButtonSizer(wx.OK | wx.CANCEL)
        root.Add(button_sizer, flag=wx.EXPAND | wx.ALL, border=10)

        self.SetSizer(root)
        self.SetInitialSize(wx.Size(520, 560))
        self.CentreOnParent()

        self.Bind(wx.EVT_BUTTON, self._on_ok, id=wx.ID_OK)
        self.Bind(wx.EVT_CHAR_HOOK, self._on_char)

        theme.apply(self)
        self.display_name_en_ctrl.SetFocus()

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _add_row(grid: wx.FlexGridSizer, label_text: str, widget: wx.Window) -> None:
        label = wx.StaticText(widget.GetParent(), label=label_text)
        grid.Add(label, flag=wx.ALIGN_CENTER_VERTICAL)
        grid.Add(widget, flag=wx.EXPAND)

    def _on_char(self, event: wx.KeyEvent) -> None:
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self.EndModal(wx.ID_CANCEL)
            return
        event.Skip()

    def _on_ok(self, event: wx.CommandEvent) -> None:
        try:
            profile, new_category = self._validate()
        except _ValidationError as exc:
            wx.MessageBox(
                str(exc),
                t("ui.genre_form.invalid_title"),
                style=wx.OK | wx.ICON_ERROR,
                parent=self,
            )
            return
        self._result_profile = profile
        self._result_new_category = new_category
        self.EndModal(wx.ID_OK)

    def _validate(self) -> tuple[dict[str, Any], dict[str, Any] | None]:
        display_name_en = self.display_name_en_ctrl.GetValue().strip()
        display_name_de = self.display_name_de_ctrl.GetValue().strip()
        if not display_name_en and not display_name_de:
            raise _ValidationError(t("ui.genre_form.error.display_name_required"))

        # The key is derived from whichever language the user filled —
        # prefer English because it's the canonical key source, fall
        # back to German if only that was provided.
        slug_source = display_name_en or display_name_de
        raw_key = self.key_ctrl.GetValue().strip()
        if not raw_key:
            raw_key = slugify(slug_source)
        if not _KEY_RE.match(raw_key):
            raise _ValidationError(t("ui.genre_form.error.bad_key"))

        category_text = self.category_ctrl.GetValue().strip()
        if not category_text:
            raise _ValidationError(t("ui.genre_form.error.category_required"))
        category_key, new_category = self._resolve_category(category_text)

        target_lufs = _parse_num(
            self.target_lufs_ctrl.GetValue(), t("ui.genre_form.field.target_lufs.name")
        )
        lra_low = _parse_num(
            self.lra_low_ctrl.GetValue(), t("ui.genre_form.field.lra_low.name")
        )
        lra_high = _parse_num(
            self.lra_high_ctrl.GetValue(), t("ui.genre_form.field.lra_high.name")
        )
        if lra_low > lra_high:
            raise _ValidationError(t("ui.genre_form.error.lra_order"))
        if lra_low < 0 or lra_high < 0:
            raise _ValidationError(t("ui.genre_form.error.lra_negative"))

        # Notes are optional; store what was entered. Empty strings are
        # fine — the localised_field() helper treats them as missing.
        notes_en = self.notes_en_ctrl.GetValue().strip()
        notes_de = self.notes_de_ctrl.GetValue().strip()

        profile = {
            "key": raw_key,
            "category_key": category_key,
            "display_name": {"en": display_name_en, "de": display_name_de},
            "target_lufs": target_lufs,
            "lra_low": lra_low,
            "lra_high": lra_high,
            "notes": {"en": notes_en, "de": notes_de},
        }
        return profile, new_category

    def _resolve_category(
        self, category_text: str
    ) -> tuple[str, dict[str, Any] | None]:
        """Return (category_key, new_category_dict_or_None)."""
        lower = category_text.lower()
        for c in self._categories:
            # A category's display_name may be a dict ({en, de}) or a
            # legacy bare string. Match on any non-empty localised form.
            stored = c.get("display_name")
            candidates: list[str] = []
            if isinstance(stored, str):
                candidates.append(stored)
            elif isinstance(stored, dict):
                candidates.extend(str(v) for v in stored.values() if v)
            if any(cand.lower() == lower for cand in candidates):
                return c["key"], None
        # New category — slugify and hand it back to the caller so the
        # editor can add it to the user override's categories list. The
        # user typed it in one language; store that as the canonical
        # entry (English) — they can edit the other language later.
        new_key = slugify(category_text) or "custom"
        existing_keys = {c["key"] for c in self._categories}
        candidate = new_key
        n = 2
        while candidate in existing_keys:
            candidate = f"{new_key}_{n}"
            n += 1
        return candidate, {
            "key": candidate,
            "display_name": {"en": category_text, "de": ""},
        }

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def result(self) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """Return (profile_dict, new_category_dict_or_None).

        ``profile_dict`` is ``None`` if the dialog was cancelled.
        """
        return self._result_profile, self._result_new_category


# --------------------------------------------------------------------------- #
# Number parsing — locale-permissive: accept both "." and "," as decimal.
# --------------------------------------------------------------------------- #


class _ValidationError(Exception):
    pass


def _parse_num(text: str, field: str) -> float:
    # Accept both "-14.0" and "-14,0" — a German user with a German
    # keyboard layout is likely to type a comma.
    cleaned = text.strip().replace(",", ".")
    if not cleaned:
        raise _ValidationError(t("ui.genre_form.error.field_required", field=field))
    try:
        value = float(cleaned)
    except ValueError as exc:
        raise _ValidationError(
            t("ui.genre_form.error.field_not_number", field=field)
        ) from exc
    if not math.isfinite(value):
        raise _ValidationError(t("ui.genre_form.error.field_not_finite", field=field))
    return value


def _fmt_num(value: Any) -> str:
    if value is None:
        return ""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return ""
    # Use one decimal unless the value is an integer.
    if f == int(f):
        return f"{int(f)}"
    return f"{f:.1f}"
