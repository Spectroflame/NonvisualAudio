# Phase 5: `welch` ersetzen und die scipy-Abhängigkeit entfernen

Diese Datei ist selbständig umsetzbar. Ein frischer Agent braucht keine
weiteren Erklärungen des Nutzers — alles Nötige steht hier oder an den
genannten Stellen im Repo.

**Diese Phase braucht vor Beginn die ausdrückliche Freigabe des
Nutzers.** Sie ist die gefährlichste des ganzen Plans, siehe „Warum
diese Phase gefährlich ist".

## Kontext für einen frischen Agenten

- Projekt: NonvisualAudio, Audio-Analyse-App für einen blinden Nutzer.
- **Zielrelease: nach 2.2.** Dieser Plan wird ausdrücklich *nicht* auf
  dem 2.2-Releasebranch umgesetzt — er würde dessen Stabilisierung
  gefährden. Der Arbeitsbranch wird beim Start der ersten Phase
  festgelegt und in `docs/bundle-laufzeit-uebersicht.md` unter „Status"
  vermerkt. Steht dort keiner: nachfragen, nicht raten.
- Teil des Bundle-, Startzeit- und Laufzeitplans (Übersicht:
  `docs/bundle-laufzeit-uebersicht.md`), letzte von drei Phasen in
  Block C. **Der eigentliche Gewinn liegt hier: 157,5 MB Bundle und
  515 ms Startzeit.**
- Setzt die Phasen 1, 3 und 4 voraus.
- Testkommando (voll): `./.venv/bin/python -m pytest tests -q`

## Vor Beginn prüfen

1. **Freigabe des Nutzers für genau diese Phase liegt vor.**
2. Sauberer Arbeitsbaum (`git status`); letzter Commit ist der
   Rückkehrpunkt. Ist der Baum verändert: melden und nachfragen.
3. Status in `docs/bundle-laufzeit-uebersicht.md`: Phasen 1, 3 und 4
   müssen erledigt sein.
4. `compute_spectrum` und `SpectrumStreamer.finalize` in
   `src/nonvisualaudio/analysis/spectrum.py` lesen, dazu
   `tests/test_spectrum_streamer.py`.

## Warum diese Phase gefährlich ist

Zwei Dinge treffen hier zusammen, und beide einzeln wären harmlos:

**Erstens ist `welch` nicht bitgenau nachbaubar.** Eine Nachbildung
weicht um relativ 2,5e-15 ab, weil scipy in anderer Reihenfolge
normiert und über die Segmente mittelt. Vorab gemessen, nicht vermutet.

**Zweitens ist `compute_spectrum` heute die Testreferenz** für den
`SpectrumStreamer` — und `compute_spectrum` ist genau die Funktion, die
hier verändert wird. Wer nur die vorhandenen Äquivalenztests laufen
lässt, prüft nach dieser Änderung die neue Implementierung gegen sich
selbst. Die Suite kann grün bleiben, während Referenz und
Implementierung gemeinsam abgedriftet sind.

Deshalb gilt in dieser Phase: **die vorhandenen Äquivalenztests sind
kein Abnahmekriterium.** Abnahme erfolgt ausschließlich über den
Goldkorpus aus Phase 1 (eingefrorene Werte des Codes von vorher) und
über den Differentialtest gegen das gepinnte scipy.

## Befund (worum es geht)

`welch` wird an genau zwei Stellen aufgerufen:

- `compute_spectrum` — die Batch-Variante. Sie hat **keinen Aufrufer im
  Produktionscode**, nur in Tests. An echten Nutzerdateien läuft sie nie.
- `SpectrumStreamer.finalize`, Fallback für Eingaben unter 4096 Samples,
  also unter 0,09 Sekunden Audio. Das ist der einzige Produktionspfad,
  der `welch` je erreicht.

Der Reporttext rundet alle Bandenergien auf zwei Nachkommastellen. Eine
relative Abweichung von 2,5e-15 liegt dreizehn Größenordnungen darunter.
Dass sie deshalb unsichtbar bleibt, ist plausibel — **belegt ist es erst
durch den Goldkorpus**, und genau das ist die Aufgabe dieser Phase.

## Ziel dieser Phase

1. Eine eigene Welch-Implementierung in `spectrum.py`, die dieselbe
   Segmentierung, Fensterung und Normierung verwendet wie bisher.
2. scipy verschwindet aus Laufzeit und Bundle, bleibt aber
   Entwicklungs-Abhängigkeit für den Differentialtest.

## Umsetzungsskizze

- Eine lokale Welch-Funktion: Segmente bei 0, hop, 2·hop, …, Fenster aus
  Phase 3, `np.fft.rfft`, Einseitenkorrektur (alle Bins außer DC und
  Nyquist verdoppeln), Division durch `fs · Σ(w²)`, Mittelung über die
  Segmentzahl. Das ist dieselbe Rechnung, die `SpectrumStreamer` bereits
  ausführt — die Gemeinsamkeit darf herausgezogen werden, **muss** aber
  so, dass die Akkumulationsreihenfolge des Streamers sich nicht ändert.
  Der Streamer ist der Produktionspfad; seine Roh-PSD muss bitgenau
  bleiben.
- `compute_spectrum` und der `finalize`-Fallback rufen die lokale
  Funktion. Signatur, Rückgabetyp und Fehlermeldungen bleiben exakt
  gleich.
- `set_workers(-1)`-Kontexte entfallen mit dem letzten scipy-Import.
- Abhängigkeit entfernen:
  - `pyproject.toml`: `scipy` aus `dependencies` streichen und in
    `optional-dependencies.dev` aufnehmen (der Differentialtest braucht
    es weiter).
  - `NonvisualAudio.spec`: prüfen, ob scipy dort auftaucht
    (`hiddenimports`, `excludes`); scipy vorsorglich in `excludes`
    aufnehmen, damit PyInstaller es nicht über einen Umweg wieder
    einsammelt.
  - `src/nonvisualaudio/ui/about_dialog.py` Zeile 36 nennt scipy im
    Nutzertext („scipy.signal, numpy, soundfile — analysis primitives").
    Dieser Text muss mit — sonst behauptet die App etwas Falsches über
    sich selbst.
  - `README.md` und `docs/architektur.md` auf scipy-Nennungen prüfen.

## Tests

- **Goldkorpus aus Phase 1: `--check` ohne Abweichung.** Das ist das
  einzige harte Abnahmekriterium für die Zahlen. Gültige Regeln laut
  Phase 1: gerundete Metriken und Reporttext exakt gleich; Roh-PSD im
  `welch`-Pfad relative Abweichung ≤ 1e-12.
- **Roh-PSD des `SpectrumStreamer` bleibt bitgenau** — der
  Produktionspfad darf sich nicht einmal im letzten Bit bewegen.
- **Differentialtest gegen gepinntes scipy** (übersprungen wenn scipy
  fehlt): lokale Welch-Funktion gegen `scipy.signal.welch` über den
  Korpus, relative Abweichung ≤ 1e-12.
- Der Sub-4096-Fallback wird ausdrücklich mit Eingaben von 1, 2, 4095
  und 4096 Samples geprüft, dazu leer und Stille.
- **Nachweis, dass scipy wirklich weg ist:**
  - `import nonvisualaudio.app` lässt `scipy` nicht in `sys.modules`
    auftauchen (Test aus Phase 2 deckt das ab, hier verschärfen: auch
    nach einem vollständigen Analyselauf).
  - Ein Lauf der vollen Suite in einer venv **ohne** scipy: alle Tests
    grün oder sauber übersprungen, kein Fehler.
  - Nach einem Testbuild: scipy taucht im Bundle nicht mehr auf, und die
    Bundle-Größe ist gemessen kleiner. Ohne Testbuild gilt die
    Bundle-Einsparung als **ungeprüft** und ist so zu berichten.
- Volle Suite grün.

## Fertig-Kriterien und Abschluss

1. Alle oben genannten Tests grün; Goldkorpus-`--check` ohne Abweichung
   (`./.venv/bin/python -m pytest tests -q`, Exit-Code 0, Zahlen im
   Abschlussbericht nennen).
2. Gemessene Zahlen im Abschlussbericht: Bundle-Größe vorher/nachher,
   Importzeit vorher/nachher, reale Start­zeit vorher/nachher (kalt und
   warm, je dreimal, Median). Nicht gemessene Punkte ausdrücklich als
   ungeprüft benennen.
3. Unabhängiger Review-Subagent (PASS/FAIL) vor dem Commit. Der Review
   prüft ausdrücklich den Zirkelschluss: **Wurde die Abnahme wirklich
   gegen den eingefrorenen Goldkorpus gefahren und nicht gegen die
   mitveränderte Batch-Funktion?**
4. Ein thematisch geschlossener Commit mit Warum-Begründung.
5. Status in `docs/bundle-laufzeit-uebersicht.md` auf erledigt setzen
   (mit Commit-Hash) und mit committen.
