"""Form dialog to add or edit a single genre profile.

Keeps typing manual — every field is a plain ``wx.TextCtrl``. No spin
controls: a blind user reaches for the number keys long before the
screen-reader-awkward up/down arrows of ``SpinCtrlDouble``, and the
extra visual polish is not worth losing keystroke efficiency.

Layout choice: every label sits **above** its field (single-column
vertical layout) instead of in a 2-column grid. The 2-column pattern
looks tidier on screen, but on macOS VoiceOver does not reliably
associate a left-of label with the focusable widget on its right —
the user lands on the field and only hears "edit text" until they
arrow left to find the label. A label sitting directly above the
field is announced together with the field on every supported screen
reader.

Category picker: a ``wx.Choice`` (NSPopUpButton on macOS, "popup
menu" in VoiceOver). To create a new category, the user types it
into the dedicated "or new category name" text field below the
choice — it takes priority over the dropdown selection on save.

Validation runs on OK. A single bad field keeps the dialog open and
shows a screen-reader-friendly error via ``wx.MessageDialog``.

The dialog has no network features — every value the user enters is
typed by hand. An earlier opt-in DuckDuckGo lookup was removed
because the snippet quality was too poor to be worth shipping.
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
# Optional sign + digits with an optional decimal part. Used by the
# blur-time auto-formatter to decide whether the field already
# contains a parseable number before rewriting it.
_NUMBER_RE = re.compile(r"^\s*([+-]?)\s*(\d+)(?:[.,](\d+))?\s*$")


def normalise_number_field(text: str) -> str:
    """Tidy a numeric input.

    - Strip whitespace.
    - Replace decimal commas with periods so downstream parsing is
      consistent (the form already accepts both inputs, but storing
      a uniform shape keeps the analysis layer simple).
    - Append ``.0`` to plain integers like ``-15`` or ``3`` so every
      LUFS / LRA field carries an explicit decimal — that's what the
      report builder and genre-profile dataclasses expect.
    - Leave the original text untouched if it is not a recognisable
      number (validation will then surface a clear error on save).
    """
    match = _NUMBER_RE.match(text)
    if not match:
        return text.strip()
    sign, whole, frac = match.group(1), match.group(2), match.group(3)
    if frac is None:
        return f"{sign}{whole}.0"
    return f"{sign}{whole}.{frac}"

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

        # Scrolled body so the dialog stays usable on small screens —
        # the form has eight rows once the new-category field is added.
        body = wx.ScrolledWindow(self, style=wx.VSCROLL)
        body.SetScrollRate(0, 16)
        body_sizer = wx.BoxSizer(wx.VERTICAL)

        # --- Key
        self.key_ctrl = wx.TextCtrl(
            body,
            value=existing_profile["key"] if is_edit else "",
            style=wx.TE_READONLY if is_edit else 0,
        )
        self._add_field(
            body_sizer,
            body,
            t("ui.genre_form.field.key"),
            self.key_ctrl,
            a11y_name=t("ui.genre_form.field.key.name"),
            a11y_hint=t("ui.genre_form.field.key.hint"),
        )

        # --- Category (popup menu) + optional "new category" text field
        #
        # wx.Choice maps to NSPopUpButton on macOS (VoiceOver: "popup
        # menu"), to a CB_DROPDOWNLIST-style combo on Windows, and to
        # GtkComboBox on GTK — the cross-platform "pick one of these"
        # control. Free-text creation of a new category lives in a
        # separate, clearly labelled text field below the popup so
        # screen-reader users do not have to guess that the dropdown
        # accepts typing.
        #
        # The dropdown leads with a placeholder "<please select>"
        # entry so OK on a fresh "add" dialog cannot silently bucket
        # the new genre under whatever category happened to sit at
        # index 0 of the list.
        category_names = [
            localised_field(c.get("display_name"), "en") or c.get("key", "")
            for c in self._categories
        ]
        self._placeholder_label = t("ui.genre_form.field.category.placeholder")
        self.category_ctrl = wx.Choice(
            body,
            choices=[self._placeholder_label, *category_names],
        )
        if is_edit:
            existing_cat_key = existing_profile.get("category_key", "")
            for i, c in enumerate(self._categories):
                if c["key"] == existing_cat_key:
                    # +1 to skip the placeholder row.
                    self.category_ctrl.SetSelection(i + 1)
                    break
            else:
                self.category_ctrl.SetSelection(0)
        else:
            self.category_ctrl.SetSelection(0)
        self._add_field(
            body_sizer,
            body,
            t("ui.genre_form.field.category"),
            self.category_ctrl,
            a11y_name=t("ui.genre_form.field.category.name"),
            a11y_hint=t("ui.genre_form.field.category.hint"),
        )

        self.new_category_ctrl = wx.TextCtrl(body, value="")
        self._add_field(
            body_sizer,
            body,
            t("ui.genre_form.field.new_category"),
            self.new_category_ctrl,
            a11y_name=t("ui.genre_form.field.new_category.name"),
            a11y_hint=t("ui.genre_form.field.new_category.hint"),
        )

        # --- Display name (English + German pair)
        stored_display = existing_profile.get("display_name") if is_edit else None
        self.display_name_en_ctrl = wx.TextCtrl(
            body, value=localised_field(stored_display, "en") if is_edit else ""
        )
        self._add_field(
            body_sizer,
            body,
            t("ui.genre_form.field.display_name_en"),
            self.display_name_en_ctrl,
            a11y_name=t("ui.genre_form.field.display_name_en.name"),
            a11y_hint=t("ui.genre_form.field.display_name.hint"),
        )
        self.display_name_de_ctrl = wx.TextCtrl(
            body, value=localised_field(stored_display, "de") if is_edit else ""
        )
        self._add_field(
            body_sizer,
            body,
            t("ui.genre_form.field.display_name_de"),
            self.display_name_de_ctrl,
            a11y_name=t("ui.genre_form.field.display_name_de.name"),
            a11y_hint=t("ui.genre_form.field.display_name.hint"),
        )

        # --- Target LUFS
        self.target_lufs_ctrl = wx.TextCtrl(
            body, value=_fmt_num(existing_profile.get("target_lufs")) if is_edit else "-14.0"
        )
        self._add_field(
            body_sizer,
            body,
            t("ui.genre_form.field.target_lufs"),
            self.target_lufs_ctrl,
            a11y_name=t("ui.genre_form.field.target_lufs.name"),
            a11y_hint=t("ui.genre_form.field.target_lufs.hint"),
        )
        self.target_lufs_ctrl.Bind(wx.EVT_KILL_FOCUS, self._on_number_blur)

        # --- LRA low/high
        self.lra_low_ctrl = wx.TextCtrl(
            body, value=_fmt_num(existing_profile.get("lra_low")) if is_edit else "5.0"
        )
        self._add_field(
            body_sizer,
            body,
            t("ui.genre_form.field.lra_low"),
            self.lra_low_ctrl,
            a11y_name=t("ui.genre_form.field.lra_low.name"),
            a11y_hint=t("ui.genre_form.field.lra_low.hint"),
        )
        self.lra_low_ctrl.Bind(wx.EVT_KILL_FOCUS, self._on_number_blur)

        self.lra_high_ctrl = wx.TextCtrl(
            body, value=_fmt_num(existing_profile.get("lra_high")) if is_edit else "10.0"
        )
        self._add_field(
            body_sizer,
            body,
            t("ui.genre_form.field.lra_high"),
            self.lra_high_ctrl,
            a11y_name=t("ui.genre_form.field.lra_high.name"),
            a11y_hint=t("ui.genre_form.field.lra_high.hint"),
        )
        self.lra_high_ctrl.Bind(wx.EVT_KILL_FOCUS, self._on_number_blur)

        # --- Notes (English + German pair, each multiline)
        stored_notes = existing_profile.get("notes") if is_edit else None
        self.notes_en_ctrl = wx.TextCtrl(
            body,
            value=localised_field(stored_notes, "en") if is_edit else "",
            style=wx.TE_MULTILINE,
        )
        self.notes_en_ctrl.SetMinSize(wx.Size(-1, 60))
        self._add_field(
            body_sizer,
            body,
            t("ui.genre_form.field.notes_en"),
            self.notes_en_ctrl,
            a11y_name=t("ui.genre_form.field.notes_en.name"),
            a11y_hint=t("ui.genre_form.field.notes.hint"),
        )

        self.notes_de_ctrl = wx.TextCtrl(
            body,
            value=localised_field(stored_notes, "de") if is_edit else "",
            style=wx.TE_MULTILINE,
        )
        self.notes_de_ctrl.SetMinSize(wx.Size(-1, 60))
        self._add_field(
            body_sizer,
            body,
            t("ui.genre_form.field.notes_de"),
            self.notes_de_ctrl,
            a11y_name=t("ui.genre_form.field.notes_de.name"),
            a11y_hint=t("ui.genre_form.field.notes.hint"),
        )

        body.SetSizer(body_sizer)
        body.FitInside()
        root.Add(body, proportion=1, flag=wx.EXPAND | wx.LEFT | wx.RIGHT, border=10)

        # --- OK / Cancel
        button_sizer = self.CreateStdDialogButtonSizer(wx.OK | wx.CANCEL)
        root.Add(button_sizer, flag=wx.EXPAND | wx.ALL, border=10)

        self.SetSizer(root)
        self.SetInitialSize(wx.Size(520, 560))
        self.CentreOnParent()

        self.Bind(wx.EVT_BUTTON, self._on_ok, id=wx.ID_OK)
        self.Bind(wx.EVT_CHAR_HOOK, self._on_char)

        theme.apply(self)
        # Fokus startet beim Schlüssel — das ist die erste echte
        # Eingabe im Add-Mode und im Edit-Mode der schreibgeschützte
        # Identifier, was Screenreadern beim Eintauchen sofort sagt
        # welches Profil offen ist.
        self.key_ctrl.SetFocus()

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _add_field(
        sizer: wx.BoxSizer,
        parent: wx.Window,
        label_text: str,
        widget: wx.Window,
        *,
        a11y_name: str,
        a11y_hint: str,
    ) -> None:
        """Lay out one labelled field in vertical order.

        Adds a ``wx.StaticText`` directly above the widget, sets the
        widget's accessible name+hint via :func:`a11y.set_a11y`, and
        stretches the widget to fill its row. Putting the label above
        the field is what makes VoiceOver, NVDA and Orca read the
        field's name automatically when the user lands on it — the
        previously-used 2-column grid relied on left-of association,
        which macOS VoiceOver does not honour for text fields.
        """
        label = wx.StaticText(parent, label=label_text)
        sizer.Add(label, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=4)
        a11y.set_a11y(widget, a11y_name, a11y_hint)
        sizer.Add(
            widget,
            flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM,
            border=4,
        )

    def _on_char(self, event: wx.KeyEvent) -> None:
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self.EndModal(wx.ID_CANCEL)
            return
        event.Skip()

    def _on_number_blur(self, event: wx.FocusEvent) -> None:
        """Tidy a LUFS / LRA field when the user moves focus away.

        Plain integers gain a ``.0`` suffix, decimal commas become
        periods. Anything that doesn't look like a number passes
        through untouched so validation can complain about it on OK.
        """
        widget = event.GetEventObject()
        if isinstance(widget, wx.TextCtrl):
            current = widget.GetValue()
            tidied = normalise_number_field(current)
            if tidied != current:
                widget.ChangeValue(tidied)
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

        # New-category text field wins over the popup selection: the
        # user can have a category pre-selected from edit mode and
        # still type a fresh name to switch to a new bucket.
        new_category_text = self.new_category_ctrl.GetValue().strip()
        if new_category_text:
            category_text = new_category_text
        else:
            sel = self.category_ctrl.GetSelection()
            # Index 0 is the "<please select>" placeholder — treat it
            # as "no category chosen" so we don't silently bucket the
            # new genre under whatever sat at the top of the list.
            if sel == wx.NOT_FOUND or sel == 0:
                raise _ValidationError(t("ui.genre_form.error.category_required"))
            category_text = self.category_ctrl.GetString(sel).strip()
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
