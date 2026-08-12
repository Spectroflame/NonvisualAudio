# Phase 4: `find_peaks` ohne scipy

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
  `docs/bundle-laufzeit-uebersicht.md`), zweite von drei Phasen in
  Block C.
- Setzt Phase 3 voraus. Phase 5 baut auf dieser auf.
- Testkommando (voll): `./.venv/bin/python -m pytest tests -q`

## Vor Beginn prüfen

1. Sauberer Arbeitsbaum (`git status`); letzter Commit ist der
   Rückkehrpunkt. Ist der Baum verändert: melden und nachfragen.
2. Status in `docs/bundle-laufzeit-uebersicht.md`: Phase 3 muss erledigt
   sein.
3. `_find_peaks_db` in `src/nonvisualaudio/analysis/spectrum.py` lesen
   (Zeilen um 111–162) — insbesondere, dass danach noch eine eigene
   1/3-Oktav-Trennung über die scipy-Ergebnisse läuft.

## Befund (worum es geht)

`_find_peaks_db` ruft `scipy.signal.find_peaks(excess, height=…,
distance=…)` auf. Dieser eine Aufruf zieht `scipy.signal._peak_finding`
und darüber `scipy.stats` in den Prozess — allein 220 ms der gemessenen
Startzeit.

Der Aufruf ist **bitgenau nachbaubar**. Das wurde vorab geprüft, nicht
angenommen: eine numpy-Nachbildung stimmte über 300 Zufallsfälle
(einschließlich erzwungener Plateaus, wechselnder Schwellen und
Mindestabstände) in **jedem** Fall exakt mit scipy überein
(`np.array_equal` über die Indexarrays).

Die Semantik, die dabei genau getroffen werden muss:

1. **Lokale Maxima** wie `scipy.signal._peak_finding_utils._local_maxima_1d`:
   ein Plateau gleicher Werte zählt als *ein* Peak, und der gemeldete
   Index ist die **abgerundete Plateaumitte**, nicht der linke Rand.
   Die Schleife läuft von Index 1 bis `x.size - 1` (ausschließlich).
2. **Höhenfilter** danach: `x[peak] >= height`.
3. **Abstandsfilter** zuletzt, in dieser Reihenfolge — scipy sortiert
   nach Höhe aufsteigend, geht die Peaks von der **höchsten** abwärts
   durch und streicht alle noch nicht gestrichenen Nachbarn, deren
   Indexabstand **kleiner** als `distance` ist (`<`, nicht `<=`).

Die Reihenfolge Höhe-vor-Abstand ist nicht beliebig: andersherum kämen
andere Peaks durch.

## Ziel dieser Phase

`_find_peaks_db` kommt ohne scipy aus. Die zurückgegebenen Peaks sind
bitgenau dieselben. `welch` bleibt der letzte scipy-Nutzer.

## Umsetzungsskizze

- Zwei kleine, klar benannte Hilfsfunktionen in `spectrum.py`: eine für
  die lokalen Maxima mit Plateaubehandlung, eine für den Abstandsfilter.
  Beide mit Warum-Kommentar, der auf die scipy-Semantik verweist, die
  sie nachbilden — inklusive des Hinweises, dass die Plateaumitte
  abgerundet wird und der Abstandsvergleich strikt kleiner ist. Beides
  sieht nach Flüchtigkeitsfehler aus und wird sonst „korrigiert".
- `_find_peaks_db` ruft statt scipy diese Hilfsfunktionen auf. Die
  nachgelagerte 1/3-Oktav-Trennung und die `MAX_REPORTED_PEAKS`-Grenze
  bleiben unverändert.
- Reine numpy-Vektoroperationen, wo möglich; die Plateauerkennung darf
  eine Python-Schleife bleiben. Sie läuft einmal pro Analyse über 2049
  Bins, das ist gemessen vernachlässigbar (die gesamte
  `finalize`-Reduktion kostet 0,026 s).

## Tests

- **Differentialtest gegen scipy** (übersprungen wenn scipy fehlt):
  mindestens 300 Zufallsfälle mit festem Seed, dazu gezielt konstruierte
  Fälle:
  - Plateaus verschiedener Länge, gerade und ungerade
  - Peak direkt am Array-Anfang und -Ende
  - zwei Peaks **exakt** im Mindestabstand und exakt einen Index darunter
  - alle Werte gleich, alle Werte unter der Schwelle, leeres Array
  - Arrays der Länge 0, 1, 2, 3
  - `NaN`/`Inf` im Eingabearray
- Goldkorpus aus Phase 1: `--check` meldet null Abweichungen; Peaks
  werden als exakte Indexgleichheit geprüft, nicht mit Frequenztoleranz.
- Volle Suite grün.

## Fertig-Kriterien und Abschluss

1. Differentialtest grün, Goldkorpus-`--check` ohne Abweichung, volle
   Suite grün (`./.venv/bin/python -m pytest tests -q`, Exit-Code 0,
   Zahlen im Abschlussbericht nennen).
2. Im Abschlussbericht die Anzahl der geprüften Zufallsfälle und die
   Liste der konstruierten Grenzfälle nennen.
3. Prüfen und berichten, ob `scipy.stats` nach dieser Phase noch
   importiert wird (`python -X importtime`) — es sollte verschwunden
   sein.
4. Unabhängiger Review-Subagent (PASS/FAIL) vor dem Commit.
5. Ein thematisch geschlossener Commit mit Warum-Begründung.
6. Status in `docs/bundle-laufzeit-uebersicht.md` auf erledigt setzen
   (mit Commit-Hash) und mit committen.
