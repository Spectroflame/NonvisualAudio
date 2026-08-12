# Phase 3: Hann-Fenster und `rfft` ohne scipy

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
  `docs/bundle-laufzeit-uebersicht.md`), erste von drei Phasen in
  Block C („scipy raus").
- Setzt Phase 1 voraus. Phase 4 baut auf dieser auf, Phase 5 auf Phase 4.
  **Reihenfolge einhalten.**
- Testkommando (voll): `./.venv/bin/python -m pytest tests -q`

## Vor Beginn prüfen

1. Sauberer Arbeitsbaum (`git status`); letzter Commit ist der
   Rückkehrpunkt. Ist der Baum verändert: melden und nachfragen.
2. Status in `docs/bundle-laufzeit-uebersicht.md`: Phase 1 muss erledigt
   sein.
3. `src/nonvisualaudio/analysis/spectrum.py` lesen, insbesondere
   `SpectrumStreamer.__init__` (Fensterkonstruktion und `_win_norm`) und
   `feed` (die `rfft`-Aufrufe).

## Befund (worum es geht)

`spectrum.py` benutzt von scipy vier Dinge. Zwei davon lassen sich
**bitgenau** durch numpy ersetzen — das wurde vorab empirisch geprüft
und nicht angenommen:

**`get_window("hann", N)`.** Die naheliegende Formel
`0.5 - 0.5*cos(2*pi*n/N)` ist *nicht* bitgleich (Abweichung 2,22e-16),
und `np.hanning` ist das falsche Fenster (symmetrisch statt periodisch,
Abweichung 5,9e-04). scipy rechnet in `_general_cosine_impl` so:

```python
fac = np.linspace(-np.pi, np.pi, N + 1)   # N+1 wegen fftbins=True
w = np.zeros(N + 1)
for k, a in enumerate([0.5, 0.5]):
    w += a * np.cos(k * fac)
w = w[:-1]
```

Diese Nachbildung ist gegen `scipy.signal.get_window("hann", N)` für
N = 256, 1024, 4096 und 8192 **bitgleich** geprüft (`np.array_equal`,
maxdiff 0.0).

**`scipy.fft.rfft`.** Ist für float64-Eingaben bitgleich mit
`np.fft.rfft` — in fünf Zufallsversuchen mit N = 4096 exakt identisch.
Beide benutzen pocketfft.

Der `scipy_fft.set_workers(-1)`-Kontext entfällt damit. In
`SpectrumStreamer.feed` umschließt er ohnehin nur eine einzelne
4096-Punkt-Transformation, wo Parallelisierung über Segmente nichts
bringt. In `compute_spectrum` und dem `finalize`-Fallback umschließt er
`welch` — das bleibt in dieser Phase unangetastet.

## Ziel dieser Phase

`get_window` und `scipy.fft.rfft` in `spectrum.py` durch numpy ersetzen.
`welch` und `find_peaks` bleiben vorerst. Die scipy-Abhängigkeit bleibt
bestehen — sie fällt erst in Phase 5.

## Umsetzungsskizze

- Eine kleine, klar benannte Hilfsfunktion für das periodische
  Hann-Fenster in `spectrum.py`, mit einem Kommentar, der das **Warum**
  festhält: warum nicht `np.hanning`, warum nicht die kurze Formel,
  woher die `linspace`-Konstruktion stammt. Ohne diesen Kommentar
  „vereinfacht" der nächste Leser die Zeile und bricht die Bitgleichheit.
- `SpectrumStreamer.__init__`: `get_window`-Import und -Aufruf durch die
  Hilfsfunktion ersetzen. `_win_norm` bleibt unverändert.
- `feed`: `scipy_fft.rfft` durch `np.fft.rfft` ersetzen (in Verbindung
  mit Phase 2 als vorab aufgelöstes Attribut).
- `compute_spectrum` und `SpectrumStreamer.finalize`: den
  `set_workers(-1)`-Kontext um die `welch`-Aufrufe **stehen lassen**,
  solange `welch` noch scipy ist.
- Signaturen, Rückgabetypen und Fehlermeldungen aller öffentlichen
  Funktionen bleiben unverändert — auch die der Batch-Funktionen, die
  heute nur Tests aufrufen. Sie gelten als Funktionalität.

## Tests

- **Bitgleichheitstest gegen scipy** (`tests/test_scipy_equivalence.py`
  aus Phase 1, übersprungen wenn scipy fehlt): die Fenster-Hilfsfunktion
  ist für N = 256, 1024, 4096, 8192 bitgleich mit
  `scipy.signal.get_window("hann", N)`; `np.fft.rfft` ist bitgleich mit
  `scipy.fft.rfft` über mehrere Zufallseingaben mit festem Seed.
- Goldkorpus aus Phase 1: `--check` meldet null Abweichungen. Für diese
  Phase gilt die strengste Regel — die Roh-PSD muss **bitgenau** gleich
  bleiben, nicht nur die gerundete Ausgabe.
- Grenzfälle: N kleiner als das Fenster, leere Eingabe, Eingabe mit
  `NaN`/`Inf`, Samplerate 0 oder negativ.
- Volle Suite grün.

## Fertig-Kriterien und Abschluss

1. Bitgleichheitstests grün, Goldkorpus-`--check` ohne Abweichung, volle
   Suite grün (`./.venv/bin/python -m pytest tests -q`, Exit-Code 0,
   Zahlen im Abschlussbericht nennen).
2. Im Abschlussbericht ausdrücklich festhalten, dass die Roh-PSD
   bitgenau unverändert ist — mit dem Kommando, das es belegt.
3. Unabhängiger Review-Subagent (PASS/FAIL) vor dem Commit. Der Review
   prüft besonders, ob der Warum-Kommentar am Fenster ausreicht, damit
   niemand die Konstruktion später „aufräumt".
4. Ein thematisch geschlossener Commit mit Warum-Begründung.
5. Status in `docs/bundle-laufzeit-uebersicht.md` auf erledigt setzen
   (mit Commit-Hash) und mit committen.
