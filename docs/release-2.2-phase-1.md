# Phase 1: Watchdog-Infrastruktur plus `run_split_streams`

Diese Datei ist selbständig umsetzbar. Ein frischer Agent braucht keine
weiteren Erklärungen des Nutzers — alles Nötige steht hier oder an den
genannten Stellen im Repo.

## Kontext für einen frischen Agenten

- Projekt: NonvisualAudio, Audio-Analyse-App für einen blinden Nutzer.
  Arbeitsbranch: `version-2.2`.
- Diese Phase ist Teil des Stabilisierungsplans vor dem 2.2-Release
  (Übersicht: `docs/release-2.2-stabilisierung.md`). Sie ist die erste
  von zwei Phasen zu Befund 1 des Code-Reviews vom 15.07.2026
  (`CODE_REVIEW.md` im Repo-Wurzelverzeichnis, unversioniert — dort
  steht die ausführliche Begründung).
- Keine Vorgänger-Phase nötig; Phase 2 baut auf dieser auf.
- Testkommando (voll): `./.venv/bin/python -m pytest tests -q`

## Vor Beginn prüfen

1. Sauberer Arbeitsbaum (`git status`); letzter Commit ist der
   Rückkehrpunkt. Ist der Baum verändert: melden und nachfragen.
2. In `docs/release-2.2-stabilisierung.md` im Abschnitt „Status"
   nachsehen, ob diese Phase nicht schon erledigt ist.
3. Kurz in `src/nonvisualaudio/audio/ffmpeg_runner.py` orientieren:
   beide Runner (`run_split_streams`, `run_split_streams_streaming`)
   und die bestehende Timeout-/Cancel-Behandlung lesen, bevor Code
   entsteht.

## Befund (worum es geht)

Beide Streaming-Runner in `src/nonvisualaudio/audio/ffmpeg_runner.py`
lesen stdout blockierend bis EOF; `proc.wait(timeout=...)` läuft erst
danach. Hängt ffmpeg, beginnt der dokumentierte Timeout nie zu laufen.
Der manuelle Abbruch funktioniert (Cancel tötet den Prozess und
entblockt die Pipes), die automatische Begrenzung nicht.
Priorität: hoch.

## Ziel dieser Phase

Ein wiederverwendbarer Deadline-Watchdog, der den ffmpeg-Prozess nach
Ablauf der Frist tötet, angewendet nur auf den einfacheren Runner
`run_split_streams` (der Pfad von `measure_loudness`). Der zweite
Runner folgt bewusst erst in Phase 2.

## Umsetzungsskizze

- Kleiner Helfer in `ffmpeg_runner.py` (z. B. Klasse `_Watchdog`):
  startet bei Prozessstart einen `threading.Timer(timeout, ...)`, der
  den Prozess terminiert/tötet und sich dabei ein Flag merkt
  („durch Timeout beendet"). Beim normalen Ende wird der Timer
  abgebrochen.
- In der Exit-Code-Auswertung: Ist das Timeout-Flag gesetzt, wird
  weiterhin `FFmpegError("timeout:...")` erhoben — dieselbe Meldung,
  die bisher aus `proc.wait(timeout=...)` entstand, damit die
  bestehende Fehlerübersetzung in `loudness.py` und `decoder.py`
  unverändert greift.
- Wechselwirkung mit Cancel klären: Wenn sowohl Cancel als auch
  Watchdog zuschlagen, gewinnt Cancel (Nutzerintention vor Automatik).

Verbindliche Konvention: Timeout und Nutzerabbruch müssen überall
unterscheidbare Fehler bleiben (`FFmpegError("timeout:...")` gegenüber
`CancelledError`) — `loudness.py` und die UI-Fehlertexte hängen daran.

## Tests (neu in `tests/test_ffmpeg_runner.py` oder benachbart)

Regressionstests zuerst schreiben: Sie müssen vor dem Fix fehlschlagen
(bzw. hängen und per Test-Timeout scheitern) und danach bestehen.

- Fake-Kindprozess (kleines Python-Skript als Subprozess), der stdout
  offen hält und nie Daten liefert: Aufruf mit kurzem Timeout muss in
  Timeout-Fehler enden statt zu hängen — mit Zeitmessung als Beleg.
- Erfolgsfall: schneller Kindprozess unterhalb des Timeouts läuft
  unverändert durch.
- Cancel während des Hängens erzeugt weiterhin `CancelledError`,
  keinen Timeout-Fehler.

## Risiken

Race zwischen Timer, Cancel und regulärem Prozessende;
Plattformunterschiede beim Töten (macOS/Windows). Deshalb in dieser
Phase nur dieser eine Runner.

## Fertig-Kriterien und Abschluss

1. Neue Tests grün, volle Suite grün
   (`./.venv/bin/python -m pytest tests -q`, Exit-Code 0, Zahlen im
   Abschlussbericht nennen).
2. Timeout-Meldung kommt im UI-Fehlerdialog unverändert an
   (Stichprobe über bestehende Loudness-Timeout-Tests).
3. Unabhängiger Review-Subagent (PASS/FAIL) vor dem Commit.
4. Ein thematisch geschlossener Commit mit Warum-Begründung.
5. In `docs/release-2.2-stabilisierung.md` den Status dieser Phase auf
   erledigt setzen (mit Commit-Hash) und diese Statusänderung mit
   committen.
