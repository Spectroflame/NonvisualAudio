# Stabilisierungsplan vor dem 2.2-Release (Übersicht)

Stand: 2026-08-05. Grundlage: EBU-R128-Korrektheitsprüfung vom selben
Tag (alle Messungen normkonform, FTPK-Zeitstempel-Fix in `c9c73a4`
erledigt) und der kritische Code-Review vom 15.07.2026
(`CODE_REVIEW.md`, unversioniert im Repo-Wurzelverzeichnis).

Zwei Befunde sollen vor dem Release behoben werden, aufgeteilt in vier
kleine Phasen. Jede Phase steht vollständig und selbständig umsetzbar
in einer eigenen Datei — eine Arbeitssitzung beginnt mit „Lies
`docs/release-2.2-phase-N.md` und setze um", weiterer Kontext ist
nicht nötig. Jede Phase schließt mit eigenem Commit, grüner Testsuite
und unabhängigem Review ab.

Reihenfolge: Phase 1 vor 2, Phase 3 vor 4. Die Blöcke A (Phasen 1–2)
und B (Phasen 3–4) sind voneinander unabhängig.

## Status

Wird von der jeweiligen Sitzung nach Abschluss aktualisiert
(erledigt + Commit-Hash). Frische Agenten prüfen hier die
Voraussetzungen ihrer Phase.

- Phase 1 (Watchdog + `run_split_streams`): erledigt (`05a2d24`)
- Phase 2 (Watchdog für `run_split_streams_streaming`): offen
- Phase 3 (atomarer Schreibhelfer + `preferences.py`): offen
- Phase 4 (atomare Writes für `genre_profiles.py`): offen

## Die Phasen

- Block A — wirksamer Wall-Clock-Timeout für die ffmpeg-Runner
  (Befund 1, Priorität hoch):
  - `docs/release-2.2-phase-1.md`: Deadline-Watchdog in
    `ffmpeg_runner.py`, angewendet auf `run_split_streams`.
  - `docs/release-2.2-phase-2.md`: derselbe Watchdog für
    `run_split_streams_streaming`, plus Deadline-Prüfung in der
    Chunk-Leseschleife.
- Block B — atomare Persistenz (Befund 3, Priorität mittel):
  - `docs/release-2.2-phase-3.md`: atomarer JSON-Schreibhelfer
    (temp + fsync + `os.replace`), angewendet auf `preferences.py`.
  - `docs/release-2.2-phase-4.md`: Umstellung von
    `save_user_overrides()` in `reporting/genre_profiles.py`.

## Optional nach dem Release (2.2.x, kein Blocker)

- Phase 5: Speicherfehler bis zur UI durchreichen (Befund 4) — eine
  nicht-blockierende, screenreader-freundliche Fehlermeldung, wenn
  Einstellungen oder Profile nicht gespeichert werden konnten.
- DynamicsStreamer-Carry auf O(n) umbauen (Befund 2) — praktisch
  vor allem Testlaufzeit (der Chunkgröße-1-Test kostet ~47 s).
- stderr-Aufbewahrung im Runner begrenzen (Befund 5).
- Constraints-Snapshot der Dependencies für reproduzierbare
  Release-Builds (Befund 7).
