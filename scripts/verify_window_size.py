"""Blind-freundlicher Selbsttest: passt der Analysieren-Button ins Fenster?

Misst die echte Fenstergeometrie (kein Screenshot) und sagt in Worten,
ob unten etwas abgeschnitten wird — im Ruhezustand UND nachdem beim
Analysestart Fortschrittsbalken + Restzeit-Label eingeblendet werden.
"""
import wx
from nonvisualaudio.ui.main_window import MainWindow

app = wx.App()
w = MainWindow()
w.Show()

def measure():
    cs = w.GetClientSize()
    btn = w.analyze_btn.GetRect()
    idle_gap = cs.height - btn.GetBottom()

    # Analysestart simulieren: Balken + Label einblenden, neu layouten.
    w.progress.Show(); w.progress_label.Show(); w.Layout()
    cs2 = w.GetClientSize()
    label = w.progress_label.GetRect()
    btn2 = w.analyze_btn.GetRect()
    run_gap = cs2.height - max(label.GetBottom(), btn2.GetBottom())
    w.progress.Hide(); w.progress_label.Hide(); w.Layout()

    print("")
    print("ERGEBNIS FENSTER-SELBSTTEST")
    print(f"Fenster-Innenhoehe: {cs.height} Pixel.")
    print(f"Analysieren-Button Unterkante: {btn.GetBottom()} Pixel.")
    print(f"Abstand bis zum unteren Rand im Ruhezustand: {idle_gap} Pixel.")
    if idle_gap >= 0:
        print("RUHEZUSTAND: BESTANDEN - der Button ist vollstaendig sichtbar.")
    else:
        print(f"RUHEZUSTAND: DURCHGEFALLEN - der Button ragt um {-idle_gap} Pixel "
              "unter den Rand (abgeschnitten).")
    print(f"Abstand bis zum unteren Rand waehrend einer Analyse: {run_gap} Pixel.")
    if run_gap >= 0:
        print("WAEHREND ANALYSE: BESTANDEN - Button und Fortschritt passen rein.")
    else:
        print(f"WAEHREND ANALYSE: DURCHGEFALLEN - es fehlen {-run_gap} Pixel.")
    print(f"Mindestgroesse gesetzt: {w.GetMinSize().width} x {w.GetMinSize().height}.")
    print("")
    w.Destroy()
    app.ExitMainLoop()

wx.CallLater(400, measure)
app.MainLoop()
