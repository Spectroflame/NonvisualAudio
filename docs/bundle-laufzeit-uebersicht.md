# Bundle, Startzeit und Laufzeit (Übersicht)

Stand: 2026-08-12. Grundlage: eine rein lesende Messung des Ist-Zustands
auf `version-2.2` (Apple M1 Max, Projekt-venv, Python 3.12.2, numpy
2.4.6, scipy 1.17.1). Alle Zahlen in diesem Plan sind gemessen, nicht
geschätzt; wo geschätzt wird, steht es ausdrücklich dabei.

Ziel: kleineres Bundle, schnellerer Start, kürzere Analysezeit — **ohne
dass sich ein einziger berichteter Messwert ändert.**

## Zielrelease: nach 2.2

Dieser Plan wird **nicht** auf dem 2.2-Releasebranch umgesetzt. Die
Entscheidung des Nutzers vom 2026-08-12: 2.2 steht kurz vor dem Release,
und die hier beschriebenen Eingriffe — vor allem der Ersatz der
scipy-Aufrufe im Spektrum-Analysator — würden dessen Stabilisierung
gefährden. Der Plan ist bewusst als Vorarbeit für ein späteres Release
abgelegt.

Der Arbeitsbranch wird beim Start der ersten Phase festgelegt und unten
unter „Status" vermerkt. Steht dort keiner: nachfragen, nicht raten.

Die Dokumente dieses Plans sind **interne Entwicklungsunterlagen** und
gelangen nicht ins ausgelieferte Bundle. Belegt: `NonvisualAudio.spec`
sammelt in `datas` ausschließlich das gebündelte ffmpeg aus
`src/nonvisualaudio/resources/bin/`, die Datei `build/_version.txt`, die
dist-info über `copy_metadata` sowie `*.json` und `*.html` unterhalb von
`src/nonvisualaudio/resources/`. `docs/` liegt außerhalb aller dieser
Wurzeln, ist kein importierbarer Python-Code und steht auch nicht in der
`package-data`-Liste von `pyproject.toml`. Wer die Spec ändert, prüft
das erneut — einen automatisierten Wächter dagegen gibt es bisher nicht.

## Was gemessen wurde

Bundle (`release-downloads/NonvisualAudio-2.1.2-macOS.zip`, entpackt
456,7 MB in 392 Dateien, komprimiert 149 MB):

| Komponente | Größe | Anteil |
|---|---|---|
| scipy | 157,5 MB | 34 % |
| ffmpeg | 137,6 MB | 30 % |
| wxPython | 54,2 MB | 12 % |
| numpy | 49,7 MB | 11 % |
| Python-Runtime | 29,8 MB | 7 % |
| App-Launcher | 10,6 MB | 2 % |
| Rest (OpenSSL, libsndfile, PortAudio, Resources) | 17,3 MB | 4 % |

Startzeit: 819 ms reine Importe bis `nonvisualaudio.app`, davon **515 ms
allein `scipy.signal`** (das wiederum `scipy.stats` mitzieht, 220 ms, nur
wegen `find_peaks`).

Laufzeit, pro Stunde Audio (WAV, 48 kHz, stereo, Datei im Page-Cache):
22,6 s gesamt = 159× Echtzeit. Davon 15,4 s ffmpeg `ebur128` (68 %) und
7,2 s Decode plus die drei Python-Analysatoren (32 %, davon 4,5 s
Python). Innerhalb der 15,4 s kostet allein die True-Peak-Messung rund
11,4 s — `ebur128` ohne `peak=true` braucht nur 3,5 s.

## Der entscheidende Befund

**scipy ist 157 MB und 515 ms schwer und wird in genau einer Datei
benutzt:** `analysis/spectrum.py`, für genau vier Dinge — `get_window`,
`fft.rfft`, `signal.welch`, `signal.find_peaks`. Sonst kommt im ganzen
Quellbaum kein scipy vor. Der Rest der 157 MB ist Mitgeschlepptes:
65 MB OpenBLAS, ein 9 MB MILP-Solver, 8,8 MB Sparse-Matrix-Code,
zweimal die gfortran-Runtime.

Dazu kommt: `compute_spectrum`, `compute_dynamics`, `compute_stereo` und
`decode_and_measure` haben **keinen einzigen Aufrufer im
Produktionscode** — sie existieren nur noch als Testreferenz für die
Streamer. `welch` läuft an echten Nutzerdateien also nie; der einzige
verbliebene Produktionspfad wäre der Fallback in
`SpectrumStreamer.finalize` für Eingaben unter 4096 Samples, also unter
0,09 Sekunden Audio.

## Was „gleiche Ergebnisse" hier heißt

Die Anforderung lautet: nichts darf sich ändern. Das ist präzisierbar,
und die Präzisierung ist wichtig, weil sie an einer Stelle nicht
wörtlich einlösbar ist.

Es gibt **zwei voneinander unabhängige Sicherungsnetze**, beide in
Phase 1 angelegt. Keines allein genügt:

1. **Goldkorpus** — eingefrorene, versionierte Fixtures mit den
   Ergebnissen des heutigen Codes, in voller Gleitkommapräzision, plus
   dem kompletten gerenderten Reporttext. Wird einmal aufgezeichnet und
   danach nie wieder regeneriert.
2. **Differentialtest gegen gepinntes scipy** — vergleicht jede
   Nachbildung zur Laufzeit direkt gegen den echten scipy-Aufruf. scipy
   bleibt dafür **Entwicklungs**-Abhängigkeit; aus Laufzeit und Bundle
   verschwindet es trotzdem.

Warum zwei: die heutigen Äquivalenztests prüfen die Streamer gegen die
Batch-Funktionen derselben Datei — und genau diese Batch-Funktionen
werden in Phase 5 verändert. Ohne eingefrorene Referenz könnten
Referenz und Implementierung gemeinsam abdriften, während die Suite grün
bleibt. Der Goldkorpus fängt das ab, der Differentialtest fängt Fehler
in der Nachbildung ab.

Gleichheitsregeln pro Ergebnisart (ausführlich in Phase 1): Reporttext
exakt gleich; gerundete Metriken exakt gleich; Roh-PSD bitgenau in den
Phasen 3 und 4; im `welch`-Pfad der Phase 5 relative Abweichung ≤ 1e-12
**und** gerundete Ausgabe exakt gleich.

Bitgenauigkeit auf Rohwertebene ist erreichbar für Hann-Fenster, `rfft`
und `find_peaks` — vorab empirisch nachgewiesen (siehe Phasen 3 und 4).
Für `welch` ist sie **nicht** erreichbar: die Nachbildung weicht um
relativ 2,5e-15 ab, weil scipy in anderer Reihenfolge normiert und
mittelt. Da diese Abweichung 13 Größenordnungen unter der Rundung auf
zwei Nachkommastellen liegt und `welch` nur die Testreferenz und den
Sub-0,09-Sekunden-Fallback betrifft, ist der Reporttext davon
voraussichtlich unberührt — genau das muss Phase 5 am Goldkorpus
**zeigen**, statt es zu behaupten.

Zur Einordnung: die heutigen Äquivalenztests vergleichen mit Toleranzen
(`abs_tol=0.01` bzw. `1e-6` dB, `freq_tol_hz=0.5` bzw. `0.01`), nicht
bitgenau. Der Goldkorpus ist also ein **strengerer** Maßstab als das,
was heute im Repo steht, nicht ein schwächerer.

Ebenfalls unveränderlich, auch wenn heute nur Tests sie aufrufen: die
Signaturen, Rückgabetypen und benutzersichtbaren Fehlermeldungen der
Batch-Funktionen. Sie gelten als Funktionalität.

Die Referenzumgebung (Versionen von Python, numpy, scipy, soundfile und
ffmpeg samt Buildoptionen, Plattform und Architektur) wird im
Goldkorpus mitgeschrieben — sonst ist später nicht entscheidbar, ob eine
Abweichung von der Änderung oder von der Umgebung kommt.

## Reihenfolge und Abhängigkeiten

Phase 1 ist Voraussetzung für alles Weitere und muss laufen, **solange
scipy noch installiert ist** — sonst gibt es keine Referenz mehr, gegen
die Block C prüfen kann.

- **Block A — Fundament:** Phase 1. Kein Produktionscode.
- **Block B — Sofortgewinn Startzeit:** Phase 2. Unabhängig, jederzeit
  machbar, wird durch Block C später gegenstandslos. Trotzdem sinnvoll:
  sie liefert den Startzeitgewinn schon vor der riskanteren Arbeit und
  bleibt bestehen, falls Block C abgebrochen wird.
- **Block C — scipy raus:** Phasen 3 → 4 → 5, **streng in dieser
  Reihenfolge**. Jede Phase entfernt einen Aufruf; erst Phase 5 entfernt
  die Abhängigkeit selbst.
- **Block D — ffmpeg:** Phase 6. Unabhängig von B und C.
- **Block E — Laufzeit:** Phase 7. **Nach Phase 6**, wenn beide gemacht
  werden: Phase 7 misst eine Nebenläufigkeit gegen ffmpeg, und diese
  Messung soll gegen das ausgelieferte Binary laufen, nicht gegen den
  Vollausbau. Wird Phase 6 verworfen, kann Phase 7 eigenständig laufen;
  die Messung ist dann nach jedem späteren ffmpeg-Wechsel zu wiederholen.

**Freigabe-Gates.** Die Phasen 5, 6 und 7 dürfen erst nach
ausdrücklicher Freigabe des Nutzers beginnen — Phase 5 wegen des
Referenz-Zirkelschlusses, Phase 6 wegen externer Build-Kette und
Lizenzfragen, Phase 7 wegen Nebenläufigkeit in der Abbruchlogik. Die
Phasen 1 bis 4 sind rein additiv bzw. nachweislich bitgenau und brauchen
kein eigenes Gate.

Vor der ersten Änderung wird ein unbeweglicher Rückkehrpunkt notiert
(Commit-Hash unter „Status"). Ein Tag nur nach ausdrücklicher Freigabe.

## Erwarteter Gewinn

| Phase | Bundle | Startzeit | Laufzeit |
|---|---|---|---|
| 2 (verzögerte Importe) | — | −515 ms Importzeit | — |
| 3–5 (scipy raus) | −157,5 MB | −515 ms Importzeit | ±0 |
| 6 (ffmpeg-Minimalbuild) | ca. −120 MB (geschätzt) | — | — |
| 7 (Durchläufe überlappen) | — | — | 1,37× |

Nach Block C und D bliebe ein Bundle von grob 180 MB entpackt statt
456,7 MB. Die Startzeitgewinne aus Phase 2 und Block C addieren sich
nicht — es ist derselbe Import.

**Wichtig zur Startzeit:** 515 ms Importzeit sind nicht automatisch
515 ms weniger Wartezeit für den Nutzer. Die Phasen 2 und 5 messen
deshalb zusätzlich die reale Zeit vom Prozessstart bis zum benutzbaren
Fenster, kalt und warm getrennt, und berichten diese Zahl — nicht die
Importzahl.

## Status

Wird von der jeweiligen Sitzung nach Abschluss aktualisiert (erledigt +
Commit-Hash). Frische Agenten prüfen hier die Voraussetzungen ihrer
Phase.

- Rückkehrpunkt vor der ersten Änderung: noch nicht notiert
- Phase 1 (Referenz-Harness und Goldkorpus): offen
- Phase 2 (scipy-Importe verzögern): offen
- Phase 3 (Hann-Fenster und rfft ohne scipy): offen
- Phase 4 (`find_peaks` ohne scipy): offen
- Phase 5 (`welch` ersetzen, scipy entfernen): offen, Freigabe nötig
- Phase 6 (ffmpeg-Minimalbuild): offen, Freigabe nötig
- Phase 7 (Loudness- und Analysedurchlauf nebenläufig): offen, Freigabe nötig

## Die Phasen

- `docs/bundle-laufzeit-phase-1.md`: Referenz-Harness und Goldkorpus.
- `docs/bundle-laufzeit-phase-2.md`: scipy-Importe in die Funktionen
  verschieben.
- `docs/bundle-laufzeit-phase-3.md`: Hann-Fenster und `rfft` ohne scipy.
- `docs/bundle-laufzeit-phase-4.md`: `find_peaks` ohne scipy.
- `docs/bundle-laufzeit-phase-5.md`: `welch` ersetzen, scipy-Abhängigkeit
  entfernen.
- `docs/bundle-laufzeit-phase-6.md`: ffmpeg-Minimalbuild.
- `docs/bundle-laufzeit-phase-7.md`: Loudness- und Analysedurchlauf
  nebenläufig.

## Ausdrücklich nicht in diesem Plan

Diese Punkte wurden gemessen und wären Gewinn, **ändern aber Messwerte
oder Verhalten**. Sie gehören nicht in einen Plan, dessen Zusage
„nichts ändert sich" lautet, und brauchen jeweils eine eigene
Entscheidung des Nutzers:

- **Dateien parallel analysieren** (Batch- und Projektmodus). Gemessen
  4,64× mit Prozessen, Ergebnisse identisch — aber Fortschrittsanzeige,
  Abbruchlogik, RAM-Bedarf und Fehlerbehandlung ändern sich spürbar.
- **Doppelscan im Projektmodus vermeiden** (heute Faktor 1,75). Die
  R128-Gatterung ist global; ein Zusammenrechnen aus Einzeldateien
  änderte die Loudness-Zahlen.
- **`ebur128` über parallele Zeitscheiben** (Rohdurchsatz 6,7× gemessen).
  Erfordert eine eigene Gatterungs-Implementierung — Messwertrisiko an
  der Stelle, die als einzige normgeprüft ist.
- **Mono-Summierung vor Dynamik und Spektrum.** Eigener Befund, eigenes
  Dokument, eigene Entscheidung: die Dynamikwerte werden bei
  gegenphasigem Material nachweislich falsch (gemessen: `peak_db`
  −120 dB, Crest 0,0, DR 0,0 bei einem Vollpegel-Signal). Das ist ein
  Korrektheits-, kein Performancethema.
