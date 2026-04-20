"""Central place for accessibility labels used across the UI.

Keeping all user-facing strings here ensures consistent wording across the
windows and makes future translation or phrasing tweaks trivial.

wxPython exposes accessibility through ``SetName`` (the short label a
screen reader announces first), ``SetHelpText`` (the longer description)
and ``SetLabel`` (the visible text). The ``set_a11y`` helper below sets
all three sensibly from a single call.
"""

from __future__ import annotations

from typing import Any


def set_a11y(widget: Any, name: str, description: str = "") -> None:
    """Set screen-reader name and (optionally) description on a wx widget.

    ``widget`` is typed loosely so this module does not force every caller
    to import wx. All wx.Window subclasses implement ``SetName`` and
    ``SetHelpText`` so duck typing is safe here.
    """
    if hasattr(widget, "SetName"):
        widget.SetName(name)
    if description and hasattr(widget, "SetHelpText"):
        widget.SetHelpText(description)


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
HINT_ANALYZE = "Run the analysis and display results in a separate window."

LABEL_RESULTS = "Analysis results"
HINT_RESULTS = (
    "The full text report. Read line by line with your screen reader. "
    "Press Control C or Command C to copy."
)

LABEL_COPY = "Copy results to clipboard"

STATUS_IDLE = 'Ready. Press "Analyze" to begin.'
STATUS_RUNNING = "Analyzing. Please wait."
STATUS_DONE = "Analysis complete. Results are in the results window."
STATUS_ERROR = "Analysis failed."
STATUS_PARTIAL = "Analysis complete. Some files could not be processed."
