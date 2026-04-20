"""Central place for accessibility labels and helpers.

Keeping all strings here ensures consistent wording across the UI and makes
translation or future tweaks trivial.
"""

from __future__ import annotations

from PySide6.QtWidgets import QWidget


def set_a11y(widget: QWidget, name: str, description: str = "") -> None:
    widget.setAccessibleName(name)
    if description:
        widget.setAccessibleDescription(description)


# Control labels.
LABEL_OPEN_FILE = "Open audio file"
HINT_OPEN_FILE = "Select the audio file you want to analyze."

LABEL_MODE_GROUP = "Analysis mode"
HINT_MODE_GROUP = "Choose how you want the file to be analyzed."

LABEL_MODE_STANDALONE = "Standalone analysis"
LABEL_MODE_GENRE = "Genre reference comparison"
LABEL_MODE_REFERENCE = "Custom reference file comparison"

LABEL_GENRE_PICKER = "Genre"
HINT_GENRE_PICKER = "Choose a genre to compare the file against."

LABEL_REFERENCE_FILE = "Reference audio file"
HINT_REFERENCE_FILE = "Select a second audio file to use as the comparison reference."

LABEL_ANALYZE = "Analyze"
HINT_ANALYZE = "Run the analysis and display results below."

LABEL_RESULTS = "Analysis results"
HINT_RESULTS = (
    "The full text report. Read line by line with your screen reader. "
    "Press Control C or Command C to copy."
)

LABEL_COPY = "Copy results to clipboard"

STATUS_IDLE = 'Ready! Press "Analyze" to begin.'
STATUS_RUNNING = "Analyzing. Please wait."
STATUS_DONE = "Analysis complete. Results are in the results area below."
STATUS_ERROR = "Analysis failed."
