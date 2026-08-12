# Phase 1: Referenz-Harness und Goldkorpus

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
- Diese Phase ist Teil des Bundle-, Startzeit- und Laufzeitplans
  (Übersicht: `docs/bundle-laufzeit-uebersicht.md`). Sie ist die
  **Voraussetzung für alle anderen Phasen** und enthält als einzige
  keinen Produktionscode.
- Testkommando (voll): `./.venv/bin/python -m pytest tests -q`

## Vor Beginn prüfen

1. Sauberer Arbeitsbaum (`git status`); letzter Commit ist der
   Rückkehrpunkt. Ist der Baum verändert: melden und nachfragen.
2. In `docs/bundle-laufzeit-uebersicht.md` im Abschnitt „Status"
   nachsehen, ob diese Phase nicht schon erledigt ist.
3. **scipy muss installiert sein.** `./.venv/bin/python -c "import
   scipy; print(scipy.__version__)"` muss laufen. Diese Phase zeichnet
   das Verhalten des heutigen Codes auf — ohne scipy gibt es nichts
   aufzuzeichnen.
4. `src/nonvisualaudio/analysis/` und `tests/test_spectrum_streamer.py`
   lesen, bevor Code entsteht.

## Befund (worum es geht)

Die Phasen 3 bis 5 ersetzen scipy-Aufrufe im Spektrum-Analysator. Die
heutigen Äquivalenztests vergleichen die Streamer gegen die
Batch-Funktionen derselben Datei (`compute_spectrum` und Geschwister) —
und **genau diese Batch-Funktionen werden in Phase 5 selbst verändert**.

Damit kann die Testsuite grün bleiben, obwohl Referenz und
Implementierung gemeinsam abgedriftet sind. Dieser Zirkelschluss ist das
größte Risiko des ganzen Plans. Er wird hier aufgelöst, bevor irgendein
Produktionscode angefasst wird.

Zusätzlich vergleichen die heutigen Tests mit Toleranzen (`abs_tol=0.01`
bzw. `1e-6` dB, `freq_tol_hz=0.5` bzw. `0.01`). Für dieses Vorhaben
brauchen wir einen strengeren und vor allem einen *unbeweglichen*
Maßstab.

## Ziel dieser Phase

Zwei voneinander unabhängige Sicherungsnetze, die in allen Folgephasen
als Abnahmekriterium dienen:

1. **Goldkorpus** — eingefrorene, versionierte Fixture-Datei mit den
   Ergebnissen des heutigen Codes über einen breiten Eingabekorpus, in
   voller Gleitkommapräzision, plus dem vollständigen gerenderten
   Reporttext. Wird eingecheckt und danach nie wieder regeneriert.
2. **Differentialtest gegen gepinntes scipy** — ein Test, der die neue
   Implementierung zur Laufzeit direkt gegen den echten scipy-Aufruf
   hält. scipy bleibt dafür als **Entwicklungs**-Abhängigkeit erhalten,
   verschwindet aber in Phase 5 aus Laufzeit und Bundle.

Das erste Netz fängt gemeinsames Abdriften ab, das zweite fängt Fehler
in der Nachbildung ab. Keines allein genügt.

## Umsetzungsskizze

### Korpus

Deterministisch erzeugt (Seed und Generator-Version festhalten:
`np.random.default_rng(<seed>)`, numpy-Version mitschreiben), abgelegt
unter `tests/fixtures/`. Abzudecken sind mindestens:

- **Formate:** WAV, AIFF, FLAC, OGG, MP3, M4A/AAC, Opus, WMA — je
  einmal, damit beide Decoder-Pfade (libsndfile und der kombinierte
  ffmpeg-Pass) belegt sind.
- **Kanäle:** mono, stereo, ein Mehrkanal-Fall (>2), stereo gegenphasig
  (L = −R), stereo unkorreliert.
- **Sampleraten:** 8000, 44100, 48000, 96000.
- **Längen an den Bruchkanten:** exakt 4096 Samples, 4095, 4097, exakt
  ein Vielfaches von 4096, exakt ein Vielfaches der 3-Sekunden-Blöcke
  der Dynamik, eins darüber, eins darunter, und ein Fall unter 4096
  Samples (löst den `welch`-Fallback in `SpectrumStreamer.finalize`
  aus — der einzige Produktionspfad, der `welch` je erreicht).
- **Signalcharakter:** Stille, Vollpegel/Clipping, reine Sinustöne
  (erzeugen Plateaus in der PSD), Rauschen, ein Signal mit zwei Peaks
  exakt im Mindestabstand der Peak-Trennung, ein Signal mit einer
  Resonanz nur auf einem Kanal.
- **Chunk-Kadenzen:** derselbe Inhalt über verschiedene Feed-Chunkgrößen
  (1, 999, 48000, 65536 Samples) durch die Streamer, weil
  Blockgrenzen die Akkumulationsreihenfolge verändern.

Echte Audiodateien dürfen ergänzt werden, wenn sie klein genug fürs Repo
sind; sie ersetzen die synthetischen Grenzfälle aber nicht.

### Aufzeichnung

Ein Skript `scripts/capture_analysis_baseline.py` mit `--record` und
`--check`, das den Korpus durch die **produktiven** Einstiegspunkte
schickt — `analyze_streaming`, `analyze`, `analyze_project` — und nicht
nur durch Hilfsfunktionen. Aufgezeichnet werden:

- jedes Feld jeder Metrik-Dataclass in voller Präzision
  (`float.hex()` oder `repr`, nicht formatiert),
- Datentyp, Arrayform und Reihenfolge, wo Arrays im Spiel sind,
- der vollständige gerenderte Reporttext je Korpuseintrag,
- die Referenzumgebung: Python-, numpy-, scipy-, soundfile-Version,
  ffmpeg-Version und -Buildoptionen (`ffmpeg -version`), Plattform und
  Architektur.

Ergebnis: `tests/fixtures/analysis_baseline.json`, eingecheckt.

### Gleichheitsregeln

Pro Ergebnisart eine ausdrückliche Regel — „volle Präzision" allein
definiert nichts:

| Ergebnis | Regel |
|---|---|
| Reporttext | exakte Zeichenkettengleichheit |
| Alle gerundeten Metrikfelder (`peak_db`, `bands.*`, Peak-Frequenzen …) | exakte Gleichheit der gerundeten Werte |
| Roh-PSD und Zwischenwerte in Phase 3 und 4 | bitgenau (`np.array_equal`) |
| Roh-PSD im `welch`-Pfad (nur Phase 5) | relative Abweichung ≤ 1e-12, **und** gerundete Ausgabe exakt gleich |
| Peak-Indexlisten | exakte Gleichheit der Indexarrays |
| Fehlerfälle | gleiche Ausnahmeklasse und gleicher benutzersichtbarer Text |

Zusätzlich zu prüfen: leere Eingabe, `NaN`/`Inf` im Puffer, Eingaben
kürzer als ein Analysefenster.

### Differentialtest

`tests/test_scipy_equivalence.py`, markiert so, dass er übersprungen
wird, wenn scipy fehlt (nach Phase 5 ist scipy für Endnutzer weg, für
Entwickler nicht). Er vergleicht die jeweils neue Nachbildung direkt
gegen den echten scipy-Aufruf, mit denselben Gleichheitsregeln.

### Rückkehrpunkt

Vor der ersten Änderung in Phase 2 einen unbeweglichen Rückkehrpunkt
anlegen: den aktuellen Commit-Hash in
`docs/bundle-laufzeit-uebersicht.md` unter „Status" notieren. Ein Tag
nur nach ausdrücklicher Freigabe des Nutzers.

## Tests

- `--record` erzeugt die Fixture, `--check` gegen den unveränderten Code
  meldet null Abweichungen (Selbsttest des Harnesses).
- Ein absichtlich verfälschter Wert wird von `--check` erkannt und
  screenreader-freundlich als Klartext gemeldet (welches Feld, welcher
  Korpuseintrag, Soll und Ist) — ein Harness, der nichts findet, ist
  wertlos.
- Der Korpus deckt jeden oben genannten Punkt ab; fehlende Punkte werden
  im Abschlussbericht ausdrücklich als Lücke benannt statt stillschweigend
  weggelassen.
- Volle Suite bleibt grün.

## Fertig-Kriterien und Abschluss

1. `tests/fixtures/analysis_baseline.json` eingecheckt, mit
   Umgebungsangaben im Kopf.
2. `--check` läuft grün gegen den unveränderten Code; Exit-Code und
   Anzahl geprüfter Korpuseinträge im Abschlussbericht nennen.
3. Volle Suite grün (`./.venv/bin/python -m pytest tests -q`, Exit-Code
   0, Zahlen nennen).
4. Unabhängiger Review-Subagent (PASS/FAIL) vor dem Commit. Der Review
   prüft ausdrücklich, ob der Korpus die Grenzfälle wirklich abdeckt —
   ein zu schmaler Korpus macht alle Folgephasen wertlos.
5. Ein thematisch geschlossener Commit mit Warum-Begründung.
6. In `docs/bundle-laufzeit-uebersicht.md` den Status dieser Phase auf
   erledigt setzen (mit Commit-Hash), den Rückkehrpunkt-Hash notieren
   und diese Statusänderung mit committen.
