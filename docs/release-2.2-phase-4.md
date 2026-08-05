# Phase 4: Atomare Writes für `genre_profiles.py`

Diese Datei ist selbständig umsetzbar. Ein frischer Agent braucht keine
weiteren Erklärungen des Nutzers — alles Nötige steht hier oder an den
genannten Stellen im Repo.

## Kontext für einen frischen Agenten

- Projekt: NonvisualAudio, Audio-Analyse-App für einen blinden Nutzer.
  Arbeitsbranch: `version-2.2`.
- Diese Phase ist Teil des Stabilisierungsplans vor dem 2.2-Release
  (Übersicht: `docs/release-2.2-stabilisierung.md`). Sie ist die zweite
  von zwei Phasen zu Befund 3 des Code-Reviews vom 15.07.2026
  (`CODE_REVIEW.md` im Repo-Wurzelverzeichnis, unversioniert).
- Voraussetzung: Phase 3 (`docs/release-2.2-phase-3.md`) ist erledigt —
  der atomare Schreibhelfer existiert bereits und `preferences.py`
  nutzt ihn. Unabhängig von den Phasen 1 und 2 (Block A).
- Testkommando (voll): `./.venv/bin/python -m pytest tests -q`

## Vor Beginn prüfen

1. Sauberer Arbeitsbaum (`git status`); letzter Commit ist der
   Rückkehrpunkt. Ist der Baum verändert: melden und nachfragen.
2. Phase-3-Voraussetzung verifizieren: Der atomare Schreibhelfer aus
   Phase 3 muss existieren (in `src/nonvisualaudio/persistence.py`
   oder `paths.py` — nachsehen, wo Phase 3 ihn tatsächlich abgelegt
   hat) und von `preferences.py` genutzt werden; zusätzlich im
   Abschnitt „Status" von `docs/release-2.2-stabilisierung.md`
   nachsehen. Fehlt der Helfer: abbrechen und melden, nicht selbst
   nachbauen.
3. `src/nonvisualaudio/reporting/genre_profiles.py`
   (`save_user_overrides()`) und die Tests aus Phase 3 lesen, bevor
   Code entsteht.

## Befund (worum es geht)

Wie in Phase 3: Direktes Schreiben mit Modus `w` kann bei Absturz,
Stromverlust oder voller Platte eine leere oder halbe JSON-Datei
hinterlassen — hier betrifft es die eigenen Genre-Profile des
Nutzers. Priorität: mittel.

## Ziel dieser Phase

`save_user_overrides()` in `reporting/genre_profiles.py` nutzt
denselben atomaren Schreibhelfer wie `preferences.py`.

## Umsetzungsskizze

- Umstellung auf den Helfer aus Phase 3; die bestehende Rückgabe
  (Pfad) bleibt, damit Aufrufer unverändert funktionieren.
- Prüfen, ob der Genre-Editor-Dialog nach einem Schreibfehler einen
  irreführenden Erfolgseindruck erweckt; falls ja, nur im
  Abschlussbericht dokumentieren — die sichtbare UI-Fehlermeldung ist
  bewusst die optionale Phase 5 nach dem Release, kein Umbau jetzt.

## Tests

- Analog Phase 3: Fehler mitten im Schreiben lässt die bestehende
  Datei byteidentisch intakt, keine Temp-Leiche; Erfolgsfall ersetzt
  vollständig; Grenzfall fehlendes Zielverzeichnis.
- Zusätzlich: Roundtrip Laden → Speichern → Laden mit eigenen
  Profilen.

## Fertig-Kriterien und Abschluss

1. Beide Persistenzpfade (Einstellungen und Genre-Profile) schreiben
   atomar; neue Tests grün, volle Suite grün
   (`./.venv/bin/python -m pytest tests -q`, Exit-Code 0, Zahlen im
   Abschlussbericht nennen).
2. Unabhängiger Review-Subagent (PASS/FAIL) vor dem Commit.
3. Ein thematisch geschlossener Commit mit Warum-Begründung.
4. In `docs/release-2.2-stabilisierung.md` den Status dieser Phase auf
   erledigt setzen (mit Commit-Hash) und diese Statusänderung mit
   committen.
