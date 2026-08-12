# Phase 7: Loudness- und Analysedurchlauf nebenläufig

Diese Datei ist selbständig umsetzbar. Ein frischer Agent braucht keine
weiteren Erklärungen des Nutzers — alles Nötige steht hier oder an den
genannten Stellen im Repo.

**Diese Phase braucht vor Beginn die ausdrückliche Freigabe des
Nutzers.** Nebenläufigkeit berührt Abbruchlogik, Fehlerbehandlung und
Fortschrittsanzeige — alles Dinge, deren Fehlverhalten ein blinder
Nutzer nicht nebenbei bemerkt.

## Kontext für einen frischen Agenten

- Projekt: NonvisualAudio, Audio-Analyse-App für einen blinden Nutzer.
- **Zielrelease: nach 2.2.** Dieser Plan wird ausdrücklich *nicht* auf
  dem 2.2-Releasebranch umgesetzt — er würde dessen Stabilisierung
  gefährden. Der Arbeitsbranch wird beim Start der ersten Phase
  festgelegt und in `docs/bundle-laufzeit-uebersicht.md` unter „Status"
  vermerkt. Steht dort keiner: nachfragen, nicht raten.
- Teil des Bundle-, Startzeit- und Laufzeitplans (Übersicht:
  `docs/bundle-laufzeit-uebersicht.md`), Block D. Als einzige Phase
  verbessert sie die **Analysezeit**, nicht Bundle oder Start.
- Setzt Phase 1 voraus. **Nach Phase 6 durchführen**, wenn beide gemacht
  werden — sonst wird gegen ein ffmpeg gemessen, das später ausgetauscht
  wird. Wird Phase 6 verworfen oder verschoben, kann diese Phase
  eigenständig laufen; die Messung ist dann nach jedem späteren
  ffmpeg-Wechsel zu wiederholen.
- Testkommando (voll): `./.venv/bin/python -m pytest tests -q`

## Vor Beginn prüfen

1. **Freigabe des Nutzers für genau diese Phase liegt vor.**
2. Sauberer Arbeitsbaum (`git status`); letzter Commit ist der
   Rückkehrpunkt.
3. Status in `docs/bundle-laufzeit-uebersicht.md`: Phase 1 muss erledigt
   sein; Stand von Phase 6 prüfen.
4. Lesen: `_try_streaming_soundfile` in
   `src/nonvisualaudio/audio/decoder.py` (der Block-Leseschleife folgt
   dort der `measure_loudness`-Aufruf), `analysis/pipeline.py`
   (Fortschrittsabbildung `_on_decode_progress`), `cancellation.py` und
   `ui/worker.py`.

## Befund (worum es geht)

Auf dem soundfile-Pfad (WAV, AIFF, FLAC, OGG — und MP3, das libsndfile
inzwischen ebenfalls liest) laufen zwei vollständige Durchläufe über
dieselbe Datei **nacheinander**:

1. `sf.blocks(...)` dekodiert und füttert die drei Streamer.
2. Danach startet `measure_loudness` einen eigenen ffmpeg-Prozess, der
   die Datei ein zweites Mal liest und `ebur128` rechnet.

Gemessen an einer Stunde Audio: 7,2 s für Durchlauf 1, 15,4 s für
Durchlauf 2, zusammen 22,6 s. Durchlauf 2 ist ein eigener Prozess — der
GIL blockiert ihn nicht. Ein Prototyp, der beide überlappt, brauchte
16,10 s statt 22,12 s (**1,37×**) und lieferte in allen vier Metriken
identische Ergebnisse.

Der kombinierte ffmpeg-Pfad (M4A, WMA und alles, was libsndfile nicht
liest) liest die Datei ohnehin nur einmal und ist von dieser Phase
**nicht** betroffen.

## Ziel dieser Phase

Auf dem soundfile-Pfad die beiden Durchläufe nebenläufig ausführen. Die
Messwerte bleiben identisch — es ändert sich nur, wann welche Arbeit
läuft.

## Umsetzungsskizze

- In `_try_streaming_soundfile` den `measure_loudness`-Aufruf vor der
  Block-Leseschleife in einem eigenen Thread starten und nach der
  Schleife einsammeln. ffmpeg ist ein Subprozess; der Thread wartet
  überwiegend auf I/O.
- Fehler aus dem Loudness-Thread müssen **beim Einsammeln erneut
  geworfen** werden, mit derselben Ausnahmeklasse und demselben
  benutzersichtbaren Text wie heute. Ein Thread, der still stirbt, ist
  genau die Art Fehler, die hier nicht passieren darf.
- Schlägt der Dekodier-Durchlauf fehl, muss der Loudness-Prozess sauber
  beendet werden, bevor die Ausnahme hochgereicht wird — sonst bleibt
  ein ffmpeg zurück.
- Abbruch: `Cancellation` bindet heute den laufenden ffmpeg-Prozess
  (`bind_process`/`clear_process`). Mit zwei gleichzeitigen Aktivitäten
  muss geprüft werden, ob diese Bindung noch trägt oder ob sie mehrere
  Prozesse verwalten können muss. **Das ist der heikelste Punkt der
  Phase** und vor dem Umbau zu klären, nicht danach.
- Fortschritt: heute belegen „decoding" und „loudness" je eine Hälfte
  des Balkens, weil sie nacheinander laufen. Nebenläufig kommen beide
  Ströme gleichzeitig. Die Abbildung muss so umgebaut werden, dass der
  Balken **monoton** bleibt — er darf nie zurückspringen. Ein
  Vorschlag: das Minimum beider Fortschritte als Gesamtfortschritt, oder
  nur den langsameren Strom anzeigen. Die Restzeitschätzung in
  `ui/eta.py` hängt daran und ist mitzuprüfen.
- Die Sprachausgabe des Fortschritts darf nicht häufiger werden als
  heute — doppelt so viele Aktualisierungen wären für einen
  Screenreader-Nutzer eine Verschlechterung, kein Gewinn.

## Tests

- Goldkorpus aus Phase 1: `--check` ohne Abweichung, über alle Formate.
- **Abbruch** mitten im Lauf: an mehreren Zeitpunkten (früh, nach dem
  ersten Block, kurz vor Ende), jeweils mit der Zusicherung, dass kein
  ffmpeg-Prozess zurückbleibt und der Nutzer eine saubere
  Abbruchmeldung bekommt, keine Fehlermeldung.
- **Fehler im Loudness-Thread** (gemocktes ffmpeg mit Exit-Code ungleich
  0, Timeout, fehlendes Binary): gleiche Ausnahmeklasse und gleicher
  Text wie heute.
- **Fehler im Dekodier-Durchlauf** bei laufendem Loudness-Thread: kein
  verwaister Prozess, kein Deadlock.
- **Fortschritt ist monoton** — ein Test, der alle gemeldeten Prozente
  sammelt und prüft, dass die Folge nie fällt.
- Ressourcen: der Test misst RAM- und Prozessanzahl-Spitze; die Zusage
  „ein paar MB unabhängig von der Länge" aus dem Streaming-Umbau muss
  weiter gelten.
- Wiederholungslauf: die Abbruch- und Fehlertests mindestens
  20-mal hintereinander grün, damit nichtdeterministische Fehler nicht
  durchrutschen.
- Volle Suite grün.

## Messung (Pflicht)

Vorher/nachher an mindestens einer 60-Minuten-Datei je Pfad
(soundfile-Pfad und kombinierter ffmpeg-Pfad), dreimal, Median, auf
derselben Maschine. Der kombinierte Pfad darf sich **nicht**
verschlechtern. Ergebnis als Klartext mit absoluten Sekunden und
Faktor.

## Fertig-Kriterien und Abschluss

1. Alle Tests grün, Goldkorpus-`--check` ohne Abweichung, volle Suite
   grün (Exit-Code 0, Zahlen im Abschlussbericht nennen).
2. Gemessener Laufzeitgewinn als Klartext, plus der Nachweis, dass der
   kombinierte Pfad unverändert schnell ist.
3. Unabhängiger Review-Subagent (PASS/FAIL) vor dem Commit. Der Review
   prüft besonders Abbruch, verwaiste Prozesse, Fehlerweitergabe aus dem
   Thread und Monotonie des Fortschritts.
4. Ein thematisch geschlossener Commit mit Warum-Begründung.
5. Status in `docs/bundle-laufzeit-uebersicht.md` auf erledigt setzen
   (mit Commit-Hash) und mit committen.

## Rückfallebene

Zeigt sich beim Umbau, dass die Abbruchlogik mehrere Prozesse nicht
sauber verwalten kann, ist der Abbruch dieser Phase die richtige
Entscheidung. 1,37× sind kein Grund, die Zuverlässigkeit des Abbruchs
zu riskieren.
