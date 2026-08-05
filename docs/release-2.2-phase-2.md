# Phase 2: Watchdog für `run_split_streams_streaming`

Diese Datei ist selbständig umsetzbar. Ein frischer Agent braucht keine
weiteren Erklärungen des Nutzers — alles Nötige steht hier oder an den
genannten Stellen im Repo.

## Kontext für einen frischen Agenten

- Projekt: NonvisualAudio, Audio-Analyse-App für einen blinden Nutzer.
  Arbeitsbranch: `version-2.2`.
- Diese Phase ist Teil des Stabilisierungsplans vor dem 2.2-Release
  (Übersicht: `docs/release-2.2-stabilisierung.md`). Sie ist die zweite
  von zwei Phasen zu Befund 1 des Code-Reviews vom 15.07.2026
  (`CODE_REVIEW.md` im Repo-Wurzelverzeichnis, unversioniert).
- Voraussetzung: Phase 1 (`docs/release-2.2-phase-1.md`) ist erledigt —
  der Deadline-Watchdog existiert bereits in
  `src/nonvisualaudio/audio/ffmpeg_runner.py` und sichert
  `run_split_streams` ab.
- Testkommando (voll): `./.venv/bin/python -m pytest tests -q`

## Vor Beginn prüfen

1. Sauberer Arbeitsbaum (`git status`); letzter Commit ist der
   Rückkehrpunkt. Ist der Baum verändert: melden und nachfragen.
2. Phase-1-Voraussetzung verifizieren: In
   `src/nonvisualaudio/audio/ffmpeg_runner.py` muss der Watchdog aus
   Phase 1 vorhanden sein und `run_split_streams` absichern; zusätzlich
   im Abschnitt „Status" von `docs/release-2.2-stabilisierung.md`
   nachsehen. Fehlt der Watchdog: abbrechen und melden, nicht selbst
   nachbauen.
3. Den Watchdog-Code und seine Tests aus Phase 1 lesen, bevor Code
   entsteht — der Helfer soll unverändert wiederverwendet werden.

## Befund (worum es geht)

Wie in Phase 1: Die Streaming-Runner lesen stdout blockierend bis EOF,
`proc.wait(timeout=...)` läuft erst danach — der dokumentierte Timeout
beginnt bei hängendem ffmpeg nie zu laufen. Phase 1 hat das für
`run_split_streams` behoben; der chunked Runner
`run_split_streams_streaming` (kombinierter Decode+Loudness-Pass und
Projektmodus) ist noch ungeschützt. Priorität: hoch.

## Ziel dieser Phase

Derselbe Watchdog für `run_split_streams_streaming`.

## Umsetzungsskizze

- Watchdog aus Phase 1 unverändert wiederverwenden; zusätzlich in der
  Chunk-Leseschleife die bereits vorhandene Cancel-Prüfung um eine
  Deadline-Prüfung ergänzen (billiger Vergleich pro Chunk), damit auch
  ein tröpfelnder, nie endender Stream die Frist respektiert — der
  Timer allein deckt nur den komplett blockierten Fall ab.
- Timeout-Budgets prüfen: 1200 s für sehr lange Projekte beibehalten;
  keine Verhaltensänderung für gesunde Läufe.

Verbindliche Konvention: Timeout und Nutzerabbruch müssen überall
unterscheidbare Fehler bleiben (`FFmpegError("timeout:...")` gegenüber
`CancelledError`) — `loudness.py` und die UI-Fehlertexte hängen daran.
Wenn sowohl Cancel als auch Watchdog zuschlagen, gewinnt Cancel.

## Tests

Regressionstests zuerst schreiben: Sie müssen vor dem Fix fehlschlagen
(bzw. hängen und per Test-Timeout scheitern) und danach bestehen.

- Hängender Fake-Prozess wie in Phase 1, aber über den streaming
  Runner mit `stdout_chunk_handler`.
- Tröpfel-Prozess: liefert dauerhaft langsam Daten über die Frist
  hinaus → Timeout-Fehler statt Endlos-Lauf.
- Regression: normaler kurzer Lauf liefert PCM und stderr unverändert
  (bestehende Äquivalenztests decken das bereits, einmal gezielt
  laufen lassen).

## Fertig-Kriterien und Abschluss

1. Beide Runner sind nachweislich zeitbegrenzt; neue Tests grün, volle
   Suite grün (`./.venv/bin/python -m pytest tests -q`, Exit-Code 0,
   Zahlen im Abschlussbericht nennen).
2. Unabhängiger Review-Subagent (PASS/FAIL) vor dem Commit.
3. Ein thematisch geschlossener Commit mit Warum-Begründung.
4. In `docs/release-2.2-stabilisierung.md` den Status dieser Phase auf
   erledigt setzen (mit Commit-Hash) und diese Statusänderung mit
   committen.
