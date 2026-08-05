# Stabilisierungsplan vor dem 2.2-Release

Stand: 2026-08-05. Grundlage: EBU-R128-Korrektheitsprüfung vom selben Tag
(alle Messungen normkonform, FTPK-Zeitstempel-Fix in `c9c73a4` erledigt)
und der kritische Code-Review vom 15.07.2026 (`CODE_REVIEW.md`,
unversioniert im Repo-Wurzelverzeichnis).

Zwei Befunde sollen vor dem Release behoben werden, aufgeteilt in vier
kleine Phasen. Jede Phase ist bewusst so geschnitten, dass sie in einer
eigenen Arbeitssitzung mit eigenem Commit, grüner Testsuite und
unabhängigem Review abschließbar ist. Die Phasen bauen aufeinander auf
(1 vor 2, 3 vor 4), die beiden Blöcke A und B sind voneinander
unabhängig und können in beliebiger Reihenfolge angegangen werden.

## Arbeitsregeln für jede Phase

- Vor Beginn: sauberer Arbeitsbaum, letzter Commit als Rückkehrpunkt.
- Tests zuerst dort, wo ein Fehlverhalten reproduzierbar ist
  (Regressionstest schlägt vor dem Fix fehl, danach nicht mehr).
- Nach der Änderung: volle Suite `./.venv/bin/python -m pytest tests -q`
  mit Exit-Code 0, dann unabhängiger Review-Subagent, dann Commit.
- Timeout und Nutzerabbruch müssen überall unterscheidbare Fehler
  bleiben (`FFmpegError("timeout:...")` gegenüber `CancelledError`) —
  `loudness.py` und die UI-Fehlertexte hängen an dieser Konvention.

## Block A: Wirksamer Wall-Clock-Timeout für die ffmpeg-Runner

Befund 1 aus dem Code-Review (Priorität hoch): Beide Streaming-Runner in
`src/nonvisualaudio/audio/ffmpeg_runner.py` lesen stdout blockierend bis
EOF; `proc.wait(timeout=...)` läuft erst danach. Hängt ffmpeg, beginnt
der dokumentierte Timeout nie zu laufen. Der manuelle Abbruch
funktioniert (Cancel tötet den Prozess und entblockt die Pipes), die
automatische Begrenzung nicht.

### Phase 1: Watchdog-Infrastruktur plus `run_split_streams`

Ziel: Ein wiederverwendbarer Deadline-Watchdog, der den ffmpeg-Prozess
nach Ablauf der Frist tötet, angewendet auf den einfacheren Runner
`run_split_streams` (der Pfad von `measure_loudness`).

Umsetzungsskizze:

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

Tests (neu in `tests/test_ffmpeg_runner.py` oder benachbart):

- Fake-Kindprozess (kleines Python-Skript als Subprozess), der stdout
  offen hält und nie Daten liefert: Aufruf mit kurzem Timeout muss in
  Timeout-Fehler enden statt zu hängen — mit Zeitmessung als Beleg.
- Erfolgsfall: schneller Kindprozess unterhalb des Timeouts läuft
  unverändert durch.
- Cancel während des Hängens erzeugt weiterhin `CancelledError`,
  keinen Timeout-Fehler.

Risiken: Race zwischen Timer, Cancel und regulärem Prozessende;
Plattformunterschiede beim Töten (macOS/Windows). Deshalb nur dieser
eine Runner in dieser Phase.

Fertig, wenn: neue Tests grün, volle Suite grün, Timeout-Meldung im
UI-Fehlerdialog unverändert ankommt (Stichprobe über bestehende
Loudness-Timeout-Tests).

### Phase 2: Watchdog für `run_split_streams_streaming`

Ziel: Derselbe Watchdog für den chunked Runner (kombinierter
Decode+Loudness-Pass und Projektmodus).

Umsetzungsskizze:

- Watchdog aus Phase 1 unverändert wiederverwenden; zusätzlich in der
  Chunk-Leseschleife die bereits vorhandene Cancel-Prüfung um eine
  Deadline-Prüfung ergänzen (billiger Vergleich pro Chunk), damit auch
  ein tröpfelnder, nie endender Stream die Frist respektiert — der
  Timer allein deckt nur den komplett blockierten Fall ab.
- Timeout-Budgets prüfen: 1200 s für sehr lange Projekte beibehalten;
  keine Verhaltensänderung für gesunde Läufe.

Tests:

- Hängender Fake-Prozess wie in Phase 1, aber über den streaming
  Runner mit `stdout_chunk_handler`.
- Tröpfel-Prozess: liefert dauerhaft langsam Daten über die Frist
  hinaus → Timeout-Fehler statt Endlos-Lauf.
- Regression: normaler kurzer Lauf liefert PCM und stderr unverändert
  (bestehende Äquivalenztests decken das bereits, einmal gezielt
  laufen lassen).

Fertig, wenn: beide Runner nachweislich zeitbegrenzt sind und die
volle Suite grün ist.

## Block B: Atomare Persistenz

Befund 3 aus dem Code-Review (Priorität mittel): `preferences.py` und
`reporting/genre_profiles.py` öffnen die Zieldatei direkt mit Modus
`w`. Absturz, Stromverlust oder volle Platte während des Schreibens
hinterlassen eine leere oder halbe JSON-Datei; beim nächsten Start
wirken Einstellungen bzw. eigene Genre-Profile verloren.

### Phase 3: Atomarer Schreibhelfer plus `preferences.py`

Ziel: Ein gemeinsamer Helfer für atomares JSON-Schreiben, zuerst auf
die Einstellungen angewendet.

Umsetzungsskizze:

- Helfer (Vorschlag: `nonvisualaudio/persistence.py` oder direkt in
  `paths.py`, falls das besser zur Struktur passt): in eine temporäre
  Datei im Zielverzeichnis schreiben, flushen, `os.fsync()`, dann
  `os.replace()` auf den Zielpfad; Temp-Datei bei jedem Fehler
  aufräumen.
- `preferences.py` stellt auf den Helfer um; Rückgabewert-Semantik
  von `save()` bleibt unverändert (kein UI-Umbau in dieser Phase).

Tests:

- Fehler mitten im Schreiben (gemocktes `json.dump`/`write` wirft):
  bestehende Datei bleibt byteidentisch erhalten, keine Temp-Leiche.
- Erfolgsfall: Datei vollständig ersetzt, Inhalt korrekt.
- Grenzfall: Zielverzeichnis existiert noch nicht (Erststart).

Fertig, wenn: neue Tests grün, volle Suite grün, keine
Verhaltensänderung im Erfolgsfall.

### Phase 4: Atomare Writes für `genre_profiles.py`

Ziel: `save_user_overrides()` nutzt denselben Helfer.

Umsetzungsskizze:

- Umstellung auf den Helfer aus Phase 3; die bestehende
  Rückgabe (Pfad) bleibt, damit Aufrufer unverändert funktionieren.
- Prüfen, ob der Genre-Editor-Dialog nach einem Schreibfehler einen
  irreführenden Erfolgseindruck erweckt; falls ja, nur dokumentieren —
  die sichtbare UI-Fehlermeldung ist bewusst Phase 5.

Tests: analog Phase 3, zusätzlich Roundtrip Laden→Speichern→Laden mit
eigenen Profilen.

Fertig, wenn: beide Persistenzpfade atomar schreiben und die volle
Suite grün ist.

## Optional nach dem Release (2.2.x, kein Blocker)

- Phase 5: Speicherfehler bis zur UI durchreichen (Befund 4) — eine
  nicht-blockierende, screenreader-freundliche Fehlermeldung, wenn
  Einstellungen oder Profile nicht gespeichert werden konnten.
- DynamicsStreamer-Carry auf O(n) umbauen (Befund 2) — praktisch
  vor allem Testlaufzeit (der Chunkgröße-1-Test kostet ~47 s).
- stderr-Aufbewahrung im Runner begrenzen (Befund 5).
- Constraints-Snapshot der Dependencies für reproduzierbare
  Release-Builds (Befund 7).
