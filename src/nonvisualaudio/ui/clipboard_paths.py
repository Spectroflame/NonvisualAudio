"""Read path-like entries from the system clipboard.

Split out of the main window so clipboard access lives in one place:
the paste menu handler and the startup clipboard scan both go through
:func:`read_clipboard_paths`. The function talks to ``wx.TheClipboard``
directly and therefore needs a running wx app.
"""

from __future__ import annotations

import wx

from nonvisualaudio.ui.drop import parse_paste_text


def read_clipboard_paths() -> list[str]:
    """Return path-like strings from the system clipboard, or [].

    Prefers a native file-list clipboard format (Finder/Explorer copy);
    falls back to parsing plain text line by line. The returned strings
    are raw candidates — callers still expand folders and filter for
    supported audio extensions.
    """
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
