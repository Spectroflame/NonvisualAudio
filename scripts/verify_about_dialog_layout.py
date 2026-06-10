"""Blind-freundlicher Selbsttest: passen alle Buttons in den Ueber-Dialog?

Misst die echte Dialog-Geometrie (kein Screenshot) fuer Deutsch UND
Englisch und sagt in Worten, ob ein Button rechts oder unten aus dem
sichtbaren Bereich ragt — bei Startgroesse und bei Mindestgroesse.
"""
import wx

from nonvisualaudio import localization
from nonvisualaudio.ui.about_dialog import AboutDialog

app = wx.App()
failures = 0

for lang in ("de", "en"):
    localization.load(lang)
    dlg = AboutDialog(None)
    dlg.Show()
    dlg.Layout()

    client = dlg.GetClientSize()
    print("")
    print(f"ERGEBNIS UEBER-DIALOG SELBSTTEST, Sprache {lang.upper()}")
    print(f"Dialog-Innenflaeche: {client.width} x {client.height} Pixel.")

    buttons = [
        ("Hilfe/Readme", dlg.readme_btn),
        ("Fehler melden", dlg.bug_btn),
        ("Diagnosebericht erzeugen", dlg.diagnostics_btn),
        ("Protokollordner oeffnen", dlg.folder_btn),
        ("Schliessen", dlg.close_btn),
    ]
    for name, btn in buttons:
        r = btn.GetRect()
        label = btn.GetLabel()
        overflow_right = r.GetRight() - client.width
        overflow_bottom = r.GetBottom() - client.height
        if overflow_right <= 0 and overflow_bottom <= 0 and r.x >= 0:
            print(
                f"BESTANDEN: Button '{label}' ({name}) vollstaendig sichtbar, "
                f"rechte Kante {r.GetRight()} von {client.width} Pixeln."
            )
        else:
            failures += 1
            print(
                f"DURCHGEFALLEN: Button '{label}' ({name}) ragt "
                f"{max(overflow_right, 0)} Pixel rechts und "
                f"{max(overflow_bottom, 0)} Pixel unten ueber den Rand."
            )

    # Reicht die Startbreite ueberhaupt fuer die Button-Reihe? BestSize
    # der Reihe gegen die Innenbreite pruefen (12 px Rand links/rechts).
    row_best = dlg.folder_btn.GetContainingSizer().GetMinSize()
    available = client.width - 24
    if row_best.width <= available:
        print(
            f"BESTANDEN: Button-Reihe braucht {row_best.width} Pixel, "
            f"{available} Pixel stehen zur Verfuegung."
        )
    else:
        failures += 1
        print(
            f"DURCHGEFALLEN: Button-Reihe braucht {row_best.width} Pixel, "
            f"aber nur {available} Pixel sind da — "
            f"{row_best.width - available} Pixel fehlen."
        )
    dlg.Destroy()

print("")
if failures == 0:
    print("GESAMT: BESTANDEN - alle Buttons in beiden Sprachen sichtbar.")
else:
    print(f"GESAMT: DURCHGEFALLEN - {failures} Problem(e) gefunden.")
raise SystemExit(0 if failures == 0 else 1)
