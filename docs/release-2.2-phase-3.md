# Phase 3: Atomarer Schreibhelfer plus `preferences.py`

Diese Datei ist selbständig umsetzbar. Ein frischer Agent braucht keine
weiteren Erklärungen des Nutzers — alles Nötige steht hier oder an den
genannten Stellen im Repo.

## Kontext für einen frischen Agenten

- Projekt: NonvisualAudio, Audio-Analyse-App für einen blinden Nutzer.
  Arbeitsbranch: `version-2.2`.
- Diese Phase ist Teil des Stabilisierungsplans vor dem 2.2-Release
  (Übersicht: `docs/release-2.2-stabilisierung.md`). Sie ist die erste
  von zwei Phasen zu Befund 3 des Code-Reviews vom 15.07.2026
  (`CODE_REVIEW.md` im Repo-Wurzelverzeichnis, unversioniert).
- Unabhängig von den Phasen 1 und 2 (Block A) — die müssen nicht
  erledigt sein. Phase 4 baut auf dieser auf.
- Testkommando (voll): `./.venv/bin/python -m pytest tests -q`

## Vor Beginn prüfen

1. Sauberer Arbeitsbaum (`git status`); letzter Commit ist der
   Rückkehrpunkt. Ist der Baum verändert: melden und nachfragen.
2. In `docs/release-2.2-stabilisierung.md` im Abschnitt „Status"
   nachsehen, ob diese Phase nicht schon erledigt ist.
3. `src/nonvisualaudio/preferences.py` und `src/nonvisualaudio/paths.py`
   lesen (Schreibpfad, Rückgabewert-Semantik von `save()`), bevor Code
   entsteht.

## Befund (worum es geht)

`preferences.py` und `reporting/genre_profiles.py` öffnen die
Zieldatei direkt mit Modus `w`. Absturz, Stromverlust oder volle
Platte während des Schreibens hinterlassen eine leere oder halbe
JSON-Datei; beim nächsten Start wirken Einstellungen bzw. eigene
Genre-Profile verloren. Priorität: mittel.

## Ziel dieser Phase

Ein gemeinsamer Helfer für atomares JSON-Schreiben, zuerst nur auf die
Einstellungen (`preferences.py`) angewendet. `genre_profiles.py` folgt
bewusst erst in Phase 4.

## Umsetzungsskizze

- Helfer (Vorschlag: `nonvisualaudio/persistence.py` oder direkt in
  `paths.py`, falls das besser zur Struktur passt): in eine temporäre
  Datei im Zielverzeichnis schreiben, flushen, `os.fsync()`, dann
  `os.replace()` auf den Zielpfad; Temp-Datei bei jedem Fehler
  aufräumen.
- `preferences.py` stellt auf den Helfer um; Rückgabewert-Semantik
  von `save()` bleibt unverändert (kein UI-Umbau in dieser Phase —
  sichtbare Fehlermeldungen sind bewusst die optionale Phase 5 nach
  dem Release).

## Tests

- Fehler mitten im Schreiben (gemocktes `json.dump`/`write` wirft):
  bestehende Datei bleibt byteidentisch erhalten, keine Temp-Leiche.
- Erfolgsfall: Datei vollständig ersetzt, Inhalt korrekt.
- Grenzfall: Zielverzeichnis existiert noch nicht (Erststart).

## Fertig-Kriterien und Abschluss

1. Neue Tests grün, volle Suite grün
   (`./.venv/bin/python -m pytest tests -q`, Exit-Code 0, Zahlen im
   Abschlussbericht nennen). Keine Verhaltensänderung im Erfolgsfall.
2. Unabhängiger Review-Subagent (PASS/FAIL) vor dem Commit.
3. Ein thematisch geschlossener Commit mit Warum-Begründung.
4. In `docs/release-2.2-stabilisierung.md` den Status dieser Phase auf
   erledigt setzen (mit Commit-Hash) und diese Statusänderung mit
   committen.
