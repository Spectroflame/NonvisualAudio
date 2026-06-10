"""Blind-freundlicher Selbsttest: passt der umbenannte Genre-Button ins Layout?

Seit 2.2 heißt der Button „Genre / Profil wählen..." (Englisch
„Choose Genre / Profile..."). Dieses Skript misst für BEIDE Sprachen
die echte Geometrie (kein Screenshot) und sagt in Worten:

  1. Trägt der Button wirklich den erwarteten neuen Text?
  2. Ist der Button breit genug für seinen Text (keine Stauchung)?
  3. Liegt der Button vollständig im sichtbaren Fensterbereich?

Ausgabe je Check: BESTANDEN / DURCHGEFALLEN plus die konkreten
Pixelzahlen. Exit-Code 0 nur, wenn alles bestanden ist.
"""
import sys

import wx

from nonvisualaudio import localization
from nonvisualaudio.ui.main_window import MainWindow

EXPECTED_LABELS = {
    "de": "Genre / Profil wählen...",
    "en": "Choose Genre / Profile...",
}

failures = 0


def check(name: str, ok: bool, detail: str) -> None:
    global failures
    verdict = "BESTANDEN" if ok else "DURCHGEFALLEN"
    print(f"{name}: {verdict} - {detail}")
    if not ok:
        failures += 1


def measure_language(lang: str) -> None:
    localization.load(lang)
    w = MainWindow()
    w.Show()
    w.Layout()

    btn = w.genre_btn
    label = btn.GetLabel()
    expected = EXPECTED_LABELS[lang]
    size = btn.GetSize()
    best = btn.GetBestSize()
    rect = btn.GetRect()
    # Der Button liegt in einem Panel im Fenster; fuer den Vergleich mit
    # der Fenster-Innenflaeche rechnen wir seine Position auf
    # Fenster-Koordinaten um.
    top_left = btn.GetParent().ClientToScreen(rect.GetTopLeft())
    win_origin = w.ClientToScreen(wx.Point(0, 0))
    cs = w.GetClientSize()
    right_in_window = (top_left.x - win_origin.x) + rect.width
    bottom_in_window = (top_left.y - win_origin.y) + rect.height

    print("")
    print(f"SPRACHE {lang.upper()}")
    check(
        "BUTTON-TEXT",
        label == expected,
        f"Label ist '{label}', erwartet '{expected}'.",
    )
    check(
        "BUTTON-BREITE",
        size.width >= best.width,
        f"Breite {size.width} Pixel, benoetigt {best.width} Pixel.",
    )
    check(
        "BUTTON IM FENSTER",
        right_in_window <= cs.width and bottom_in_window <= cs.height,
        f"Button endet bei x={right_in_window} / y={bottom_in_window}, "
        f"Fenster-Innenflaeche {cs.width} x {cs.height} Pixel.",
    )

    w.Destroy()


def run() -> None:
    global ran, failures
    try:
        print("ERGEBNIS GENRE-BUTTON-LAYOUT-SELBSTTEST")
        # Das Anker-Fenster hat seinen Zweck (MainLoop am Leben halten)
        # erfuellt; gemessen wird an frisch erzeugten Fenstern je Sprache.
        keeper.Hide()
        for lang in ("de", "en"):
            measure_language(lang)
        print("")
        if failures == 0:
            print("GESAMT: BESTANDEN - alle Checks in beiden Sprachen ok.")
        else:
            print(f"GESAMT: DURCHGEFALLEN - {failures} Check(s) fehlgeschlagen.")
    except Exception as exc:  # noqa: BLE001 — ein Crash darf kein stilles PASS werden
        failures += 1
        print(f"GESAMT: DURCHGEFALLEN - Selbsttest abgebrochen: {exc!r}")
    finally:
        ran = True
        keeper.Destroy()
        app.ExitMainLoop()


app = wx.App()
ran = False
# wxMac beendet die MainLoop sofort wieder, wenn beim Eintritt kein
# Top-Level-Fenster existiert — der CallLater wuerde nie feuern und das
# Skript koennte still mit Exit 0 enden. Deshalb haelt ein Anker-Fenster
# die Loop am Leben, und nach der Loop prueft ``ran``, dass die Messung
# wirklich gelaufen ist. Ein stilles Schein-BESTANDEN darf es in einem
# Selbsttest fuer blinde Nutzer nicht geben.
app.SetExitOnFrameDelete(False)
keeper = wx.Frame(None, title="layout self-test")
keeper.Show()
wx.CallLater(400, run)
app.MainLoop()
if not ran:
    print("GESAMT: DURCHGEFALLEN - die Messung wurde nie ausgefuehrt.")
    sys.exit(1)
sys.exit(0 if failures == 0 else 1)
