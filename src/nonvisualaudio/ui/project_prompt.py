"""Decision logic for the one-time "use project mode?" prompt.

When an add operation pushes the target list past the threshold while
project mode is off, the main window offers to switch project mode on —
once per filling of the list. The decision itself is a pure function so
the trigger matrix can be unit-tested without a running wx app; the
main window owns the dialog and the dismissed-state bookkeeping.
"""

from __future__ import annotations

#: Ask only when the list grows beyond this many target files. Five
#: tracks is where "a handful of singles" plausibly turns into an
#: album/audiobook-sized batch.
PROJECT_PROMPT_THRESHOLD = 5


def should_offer_project_mode(
    previous_count: int,
    new_count: int,
    project_mode_on: bool,
    already_dismissed: bool,
    threshold: int = PROJECT_PROMPT_THRESHOLD,
) -> bool:
    """Return True when the project-mode prompt should be shown.

    The prompt fires only on the add that crosses the threshold
    (``previous_count <= threshold < new_count``). Growing an
    already-large list stays silent, so the user is never asked twice
    for the same filling of the list — and switching project mode off
    while the list is still large does not immediately re-ask either,
    because the crossing already happened.
    """
    if project_mode_on or already_dismissed:
        return False
    return previous_count <= threshold < new_count
