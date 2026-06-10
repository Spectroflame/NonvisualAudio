"""Blind-freundlicher Selbsttest fuer den Protokollbetrachter.

Misst die echte Dialog-Geometrie (kein Screenshot) fuer Deutsch UND
Englisch und prueft die Grundfunktion: Dialog oeffnet, Text lesbar,
Inhalt byte-identisch zum Geladenen, Refresh liest Neues, alle Buttons
sichtbar. Zusaetzlich der Diagnosedialog, dessen Buttonzeile durch den
neuen vierten Button breiter geworden ist.

Das Severity-Highlighting ist rein dekorativ: Wenn GetStyle auf der
Plattform nicht unterstuetzt wird, gibt es nur einen HINWEIS und kein
DURCHGEFALLEN. FAIL gibt es nur, wenn Styles lesbar sind, aber falsche
Farben gesetzt wurden, der Textinhalt veraendert wurde oder im
High-Contrast-Theme Severity-Farben auftauchen.
"""

import tempfile
from pathlib import Path

import wx

from nonvisualaudio import diagnostics, localization
from nonvisualaudio.ui import theme
from nonvisualaudio.ui.diagnostics_dialog import DiagnosticsDialog
from nonvisualaudio.ui.log_viewer_dialog import _HIGHLIGHT_COLOURS, LogViewerDialog

app = wx.App()
failures = 0
notes = 0

SAMPLE_LOG = (
    "2026-06-10 12:00:00 INFO  nonvisualaudio: app started\n"
    "2026-06-10 12:00:01 WARNING nonvisualaudio.audio: level clipped\n"
    "2026-06-10 12:00:02 ERROR nonvisualaudio: analysis failed\n"
    "Traceback (most recent call last):\n"
    "  ValueError: kaputt\n"
)

# Eine private Logdatei, damit der Test deterministisch ist und die
# echte Sitzungsdatei unberuehrt bleibt.
_tmpdir = tempfile.TemporaryDirectory()
_log_dir = Path(_tmpdir.name)
diagnostics.user_log_dir = lambda: _log_dir  # nur fuer diesen Selbsttest
_log_path = _log_dir / "nonvisualaudio.log"
_log_path.write_text(SAMPLE_LOG, encoding="utf-8")


def check_buttons_visible(dlg, buttons, dialog_name):
    """Alle Buttons vollstaendig sichtbar + Buttonzeile passt in die Breite."""
    global failures
    client = dlg.GetClientSize()
    print(f"Dialog-Innenflaeche: {client.width} x {client.height} Pixel.")
    for name, btn in buttons:
        r = btn.GetRect()
        overflow_right = r.GetRight() - client.width
        overflow_bottom = r.GetBottom() - client.height
        if overflow_right <= 0 and overflow_bottom <= 0 and r.x >= 0:
            print(
                f"BESTANDEN: Button '{btn.GetLabel()}' ({name}) vollstaendig "
                f"sichtbar, rechte Kante {r.GetRight()} von {client.width} Pixeln."
            )
        else:
            failures += 1
            print(
                f"DURCHGEFALLEN: Button '{btn.GetLabel()}' ({name}) ragt "
                f"{max(overflow_right, 0)} Pixel rechts und "
                f"{max(overflow_bottom, 0)} Pixel unten ueber den Rand."
            )
    row_best = buttons[0][1].GetContainingSizer().GetMinSize()
    available = client.width - 24
    if row_best.width <= available:
        print(
            f"BESTANDEN: Button-Reihe im {dialog_name} braucht "
            f"{row_best.width} Pixel, {available} Pixel stehen zur Verfuegung."
        )
    else:
        failures += 1
        print(
            f"DURCHGEFALLEN: Button-Reihe im {dialog_name} braucht "
            f"{row_best.width} Pixel, aber nur {available} Pixel sind da — "
            f"{row_best.width - available} Pixel fehlen."
        )


def get_style_colour(text_ctrl, pos):
    """Textfarbe an Position auslesen; None, wenn die Plattform es nicht kann."""
    attr = wx.TextAttr()
    try:
        supported = text_ctrl.GetStyle(pos, attr)
    except Exception:
        return None
    if not supported or not attr.HasTextColour():
        return None
    c = attr.GetTextColour()
    return (c.Red(), c.Green(), c.Blue())


def check_highlighting(dlg, lang):
    """Bedingter Check: nur FAIL, wenn Styles lesbar, aber falsch sind."""
    global failures, notes
    expected = _HIGHLIGHT_COLOURS[theme.resolve(theme.current())]
    value = dlg.text.GetValue()
    lines = value.split("\n")
    # Position je einer INFO-, WARNING-, ERROR- und Traceback-Zeile
    # (Zeichen-Offset der Zeilenmitte).
    probes = []
    pos = 0
    for line in lines:
        if " INFO " in line:
            probes.append(("INFO-Zeile", pos + len(line) // 2, expected["info"]))
        elif " WARNING " in line:
            probes.append(("WARNING-Zeile", pos + len(line) // 2, expected["warning"]))
        elif " ERROR " in line:
            probes.append(("ERROR-Zeile", pos + len(line) // 2, expected["error"]))
        elif line.startswith("Traceback"):
            probes.append(
                ("Traceback-Zeile (erbt ERROR)", pos + len(line) // 2, expected["error"])
            )
        pos += len(line) + 1
    style_support = False
    for name, probe_pos, want in probes:
        got = get_style_colour(dlg.text, probe_pos)
        if got is None:
            continue
        style_support = True
        if got == want:
            print(f"BESTANDEN: {name} hat die erwartete Farbe RGB {got}.")
        else:
            failures += 1
            print(
                f"DURCHGEFALLEN: {name} hat Farbe RGB {got}, "
                f"erwartet war RGB {want}."
            )
    if not style_support:
        notes += 1
        print(
            "HINWEIS: Style-Abfrage auf dieser Plattform nicht unterstuetzt — "
            "Highlighting nicht pruefbar, Grundfunktion zaehlt."
        )


for lang in ("de", "en"):
    localization.load(lang)
    print("")
    print(f"ERGEBNIS PROTOKOLLBETRACHTER SELBSTTEST, Sprache {lang.upper()}")

    dlg = LogViewerDialog(None)
    dlg.Show()
    dlg.Layout()

    # Grundfunktion: Text geladen und byte-identisch zur Datei.
    value = dlg.text.GetValue()
    if SAMPLE_LOG.rstrip("\n") in value:
        print("BESTANDEN: Logtext vollstaendig und unveraendert im Textfeld.")
    else:
        failures += 1
        print("DURCHGEFALLEN: Logtext fehlt oder wurde veraendert.")

    # Refresh: angehaengte Zeile erscheint nach _load().
    with _log_path.open("a", encoding="utf-8") as fh:
        fh.write(f"2026-06-10 12:00:09 INFO  nonvisualaudio: refresh-{lang}\n")
    dlg._load()
    if f"refresh-{lang}" in dlg.text.GetValue():
        print("BESTANDEN: Aktualisieren liest neu angehaengte Eintraege.")
    else:
        failures += 1
        print("DURCHGEFALLEN: Aktualisieren zeigt neue Eintraege nicht.")

    check_buttons_visible(
        dlg,
        [
            ("Protokoll kopieren", dlg.copy_btn),
            ("Aktualisieren", dlg.refresh_btn),
            ("Schliessen", dlg.close_btn),
        ],
        "Protokollbetrachter",
    )
    check_highlighting(dlg, lang)
    dlg.Destroy()

    # Diagnosedialog: passt die Vier-Button-Zeile noch?
    print("")
    print(f"ERGEBNIS DIAGNOSEDIALOG SELBSTTEST, Sprache {lang.upper()}")
    ddlg = DiagnosticsDialog(None)
    ddlg.Show()
    ddlg.Layout()
    check_buttons_visible(
        ddlg,
        [
            ("Diagnosebericht erzeugen", ddlg.save_btn),
            ("Protokollordner oeffnen", ddlg.folder_btn),
            ("Aktuelles Protokoll anzeigen", ddlg.viewlog_btn),
            ("Schliessen", ddlg.close_btn),
        ],
        "Diagnosedialog",
    )
    ddlg.Destroy()

# High-Contrast: keine Severity-Farben, Theme-Vordergrund bleibt.
print("")
print("ERGEBNIS HIGH-CONTRAST SELBSTTEST")
localization.load("de")
theme.set_current("high_contrast")
hdlg = LogViewerDialog(None)
hdlg.Show()
hdlg.Layout()
if SAMPLE_LOG.rstrip("\n") in hdlg.text.GetValue():
    print("BESTANDEN: Logtext im High-Contrast-Theme lesbar geladen.")
else:
    failures += 1
    print("DURCHGEFALLEN: Logtext fehlt im High-Contrast-Theme.")
severity_rgbs = {
    rgb for palette in _HIGHLIGHT_COLOURS.values() for rgb in palette.values()
}
hc_value = hdlg.text.GetValue()
hc_lines = hc_value.split("\n")
hc_pos = 0
hc_styled = False
for line in hc_lines:
    if " ERROR " in line or " INFO " in line or " WARNING " in line:
        got = get_style_colour(hdlg.text, hc_pos + len(line) // 2)
        if got is not None and got in severity_rgbs:
            failures += 1
            hc_styled = True
            print(
                f"DURCHGEFALLEN: High-Contrast-Zeile traegt Severity-Farbe "
                f"RGB {got} — die Assistenz-Palette wurde ueberschrieben."
            )
    hc_pos += len(line) + 1
if not hc_styled:
    print(
        "BESTANDEN: Im High-Contrast-Theme wurden keine Severity-Farben "
        "gesetzt; die Gelb-auf-Schwarz-Palette bleibt unangetastet."
    )
fg = hdlg.text.GetForegroundColour()
print(
    f"Zur Info: Vordergrundfarbe des Textfelds ist RGB "
    f"({fg.Red()}, {fg.Green()}, {fg.Blue()}) — erwartet (255, 255, 0)."
)
if (fg.Red(), fg.Green(), fg.Blue()) == (255, 255, 0):
    print("BESTANDEN: High-Contrast-Vordergrund (Gelb) ist erhalten.")
else:
    failures += 1
    print("DURCHGEFALLEN: High-Contrast-Vordergrund wurde veraendert.")
hdlg.Destroy()
theme.set_current("auto")

print("")
if failures == 0:
    extra = f" ({notes} Hinweis(e))" if notes else ""
    print(f"GESAMT: BESTANDEN - Protokollbetrachter und Diagnosedialog in Ordnung{extra}.")
else:
    print(f"GESAMT: DURCHGEFALLEN - {failures} Problem(e) gefunden.")
_tmpdir.cleanup()
raise SystemExit(0 if failures == 0 else 1)
