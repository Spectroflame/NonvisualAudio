"""Modal genre picker dialog.

Uses QCheckBox for each profile so the user can pick any number of
genres and have the file compared against all of them at once. Checkboxes
are reliably accessible to VoiceOver, NVDA and Orca in Qt 6.
"""

from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from nonvisualaudio.reporting.genre_profiles import GENRES, grouped_genres


class GenreDialog(QDialog):
    """Modal dialog that lets the user pick one or more genre profiles."""

    def __init__(
        self,
        parent=None,
        selected_keys: Iterable[str] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Choose genre references")
        self.setModal(True)
        self.setAccessibleName("Choose genre references")
        self.setAccessibleDescription(
            "A list of genre profiles grouped by category. "
            "Tab between profiles and press Space to toggle each one. "
            "Any number of profiles can be selected at once."
        )
        self.resize(500, 520)

        self._checks: dict[str, QCheckBox] = {}

        root = QVBoxLayout(self)

        intro = QLabel(
            "Pick one or more genres whose typical loudness and dynamics "
            "you want the file compared against. You can select as many as "
            "you like — the report will include a comparison for each."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        # Quick actions.
        actions_row = QVBoxLayout()
        self._select_none_btn = QPushButton("Clear all")
        self._select_none_btn.setAccessibleName("Clear all genre selections")
        self._select_none_btn.clicked.connect(self._clear_all)
        actions_row.addWidget(self._select_none_btn)
        root.addLayout(actions_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(0, 0, 0, 0)
        inner_layout.setSpacing(6)

        pre_selected: set[str] = set(selected_keys or ())

        first_check: QCheckBox | None = None
        for category, profiles in grouped_genres():
            cat_label = QLabel(category)
            font = cat_label.font()
            font.setBold(True)
            cat_label.setFont(font)
            inner_layout.addWidget(cat_label)
            for p in profiles:
                check = QCheckBox(p.display_name)
                check.setAccessibleName(p.display_name)
                check.setAccessibleDescription(
                    f"{category} genre reference. {p.notes}"
                )
                check.setProperty("genre_key", p.key)
                if p.key in pre_selected:
                    check.setChecked(True)
                self._checks[p.key] = check
                inner_layout.addWidget(check)
                if first_check is None:
                    first_check = check
        inner_layout.addStretch(1)
        scroll.setWidget(inner)
        root.addWidget(scroll, stretch=1)

        if first_check is not None:
            first_check.setFocus(Qt.FocusReason.OtherFocusReason)

        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        root.addWidget(button_box)

    def _clear_all(self) -> None:
        for check in self._checks.values():
            check.setChecked(False)

    def selected_keys(self) -> list[str]:
        """Return selected profile keys in display order."""
        keys: list[str] = []
        for key, check in self._checks.items():
            if check.isChecked():
                keys.append(key)
        return keys

    def selected_display_names(self) -> list[str]:
        names: list[str] = []
        for key in self.selected_keys():
            profile = GENRES.get(key)
            if profile is not None:
                names.append(profile.display_name)
        return names
