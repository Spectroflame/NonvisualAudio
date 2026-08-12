# Phase 2: scipy-Importe in die Funktionen verschieben

Diese Datei ist selbständig umsetzbar. Ein frischer Agent braucht keine
weiteren Erklärungen des Nutzers — alles Nötige steht hier oder an den
genannten Stellen im Repo.

## Kontext für einen frischen Agenten

- Projekt: NonvisualAudio, Audio-Analyse-App für einen blinden Nutzer.
- **Zielrelease: nach 2.2.** Dieser Plan wird ausdrücklich *nicht* auf
  dem 2.2-Releasebranch umgesetzt — er würde dessen Stabilisierung
  gefährden. Der Arbeitsbranch wird beim Start der ersten Phase
  festgelegt und in `docs/bundle-laufzeit-uebersicht.md` unter „Status"
  vermerkt. Steht dort keiner: nachfragen, nicht raten.
- Teil des Bundle-, Startzeit- und Laufzeitplans (Übersicht:
  `docs/bundle-laufzeit-uebersicht.md`).
- Setzt Phase 1 voraus (Goldkorpus muss existieren).
- Unabhängig von den Phasen 3 bis 7. Wird durch Phase 5 gegenstandslos,
  ist aber trotzdem sinnvoll: der Startzeitgewinn ist sofort da und
  bleibt bestehen, falls Block C nie umgesetzt wird.
- Testkommando (voll): `./.venv/bin/python -m pytest tests -q`

## Vor Beginn prüfen

1. Sauberer Arbeitsbaum (`git status`); letzter Commit ist der
   Rückkehrpunkt. Ist der Baum verändert: melden und nachfragen.
2. Status in `docs/bundle-laufzeit-uebersicht.md` prüfen: Phase 1 muss
   erledigt sein.
3. `src/nonvisualaudio/analysis/spectrum.py` lesen, insbesondere die
   Importe im Modulkopf und die bereits vorhandenen lokalen Importe in
   `SpectrumStreamer.__init__` und `feed`.

## Befund (worum es geht)

Gemessen mit `python -X importtime`: der App-Start kostet 819 ms reine
Importe. Davon entfallen **515 ms auf `scipy.signal`**, das über
`analysis/spectrum.py` in die Kette
`app → main_window → worker → analysis_workflow → pipeline → spectrum`
gezogen wird. `scipy.signal._peak_finding` zieht dabei allein `scipy.stats`
mit 220 ms nach — nur wegen `find_peaks`.

Diese 515 ms fallen bei **jedem** Start an, auch wenn der Nutzer gar
keine Analyse startet. Gebraucht wird scipy erst, wenn tatsächlich
analysiert wird.

Nebenbefund: `spectrum.py` importiert in der heißen Schleife von
`SpectrumStreamer.feed` bei jedem einzelnen Segment erneut
`from scipy import fft as scipy_fft` (Zeile 332). Das ist zwar nur ein
Wörterbuchzugriff in `sys.modules`, läuft aber bei einer Stunde Audio
rund 84 000 Mal.

## Ziel dieser Phase

Die scipy-Importe aus dem Modulkopf von `spectrum.py` in die Funktionen
verschieben, die sie wirklich brauchen. Kein Verhalten, keine Signatur
und kein Messwert ändert sich — es sind dieselben Aufrufe, nur später.

Ausdrücklich **nicht** Teil dieser Phase: scipy ersetzen oder entfernen.
Das ist Block C.

## Umsetzungsskizze

- `from scipy import fft as scipy_fft` und `from scipy import signal` aus
  dem Modulkopf entfernen.
- In `compute_spectrum`, `_find_peaks_db` und
  `SpectrumStreamer.finalize` jeweils lokal importieren.
- In `SpectrumStreamer.__init__` bleibt der lokale `get_window`-Import
  wie er ist.
- Den Import aus der `feed`-Schleife herausziehen: einmal in
  `__init__` auflösen und als Attribut halten (z. B. `self._rfft`),
  statt ihn pro Segment erneut auszuführen.
- Prüfen, ob durch das Verschieben irgendwo ein zyklischer Import
  entsteht — `spectrum.py` importiert sonst nur aus
  `analysis/result.py`, das Risiko ist gering, die Prüfung trotzdem
  machen.

## Tests

- Ein Test, der belegt, dass `nonvisualaudio.app` importiert werden
  kann, **ohne** dass `scipy` in `sys.modules` landet. Das ist die
  eigentliche Zusicherung dieser Phase und muss automatisiert
  festgehalten werden, sonst rutscht der Import beim nächsten Refactor
  wieder nach oben.
- Goldkorpus aus Phase 1: `--check` meldet null Abweichungen.
- Volle Suite grün.

## Messung (Pflicht, nicht optional)

Importzeit ist nicht dasselbe wie Startzeit. Zu messen und im
Abschlussbericht als Klartext zu berichten:

- `python -X importtime -c "import nonvisualaudio.app"` vorher und
  nachher, kumulierte Millisekunden.
- Die **reale** Zeit vom Prozessstart bis zum benutzbaren Fenster,
  vorher und nachher, je dreimal, Median. Kalt- und Warmstart getrennt
  ausweisen.
- Falls die reale Startzeit deutlich weniger gewinnt als die 515 ms
  Importzeit vermuten lassen: das so berichten, nicht die Importzahl
  als Ergebnis verkaufen.

## Fertig-Kriterien und Abschluss

1. Neuer Import-Test grün, Goldkorpus-`--check` ohne Abweichung, volle
   Suite grün (`./.venv/bin/python -m pytest tests -q`, Exit-Code 0,
   Zahlen im Abschlussbericht nennen).
2. Start- und Importzeiten vorher/nachher als Klartext berichtet.
3. Unabhängiger Review-Subagent (PASS/FAIL) vor dem Commit.
4. Ein thematisch geschlossener Commit mit Warum-Begründung.
5. Status in `docs/bundle-laufzeit-uebersicht.md` auf erledigt setzen
   (mit Commit-Hash) und mit committen.
