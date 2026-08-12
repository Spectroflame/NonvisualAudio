# Vor dem 2.2-Release: offene Punkte

Diese Datei ist selbständig umsetzbar. Ein frischer Agent braucht keine
weiteren Erklärungen des Nutzers — alles Nötige steht hier oder an den
genannten Stellen im Repo.

Grundlage: eine rein lesende Release-Prüfung vom 2026-08-12 auf
`version-2.2`. **Ergebnis: 2.2 ist funktional release-fähig.** Die
Testsuite ist grün, es gibt keinen bekannten Fehler im Analyse- oder
UI-Verhalten. Die hier gelisteten Punkte betreffen Prozess und
Dokumentation, nicht die Funktion.

## Kontext für einen frischen Agenten

- Projekt: NonvisualAudio, Audio-Analyse-App für einen blinden Nutzer.
  Arbeitsbranch: `version-2.2` (das ist hier der richtige Branch — im
  Unterschied zum Bundle-/Laufzeitplan, der bewusst erst nach 2.2 kommt).
- Testkommando (voll): `./.venv/bin/python -m pytest tests -q`
- Ausgangsstand der Prüfung: 569 Tests grün, Exit-Code 0, in 60,96 s.

## Grundregel für diese Aufgabe

**Nichts erfinden.** Diese Liste ist das Ergebnis einer bereits
durchgeführten Prüfung. Wer hier arbeitet, arbeitet die Liste ab und
sucht nicht nach zusätzlichen Gründen, Code anzufassen. Fällt beim
Abarbeiten etwas wirklich Neues auf: melden, nicht eigenmächtig
mitreparieren.

Der Abschnitt „Was ausdrücklich in Ordnung ist" weiter unten steht
genau dafür da — er hält fest, was bereits geprüft wurde, damit niemand
dieselbe Suche noch einmal führt oder dort Probleme konstruiert.

## Vor Beginn prüfen

1. Sauberer Arbeitsbaum (`git status`); letzter Commit ist der
   Rückkehrpunkt. Ist der Baum verändert: melden und nachfragen.
   Dauerhaft untracked und **nicht** Teil dieser Aufgabe:
   `.venv-broken-backup/`, `CODE_REVIEW.md`, `doku/`,
   `release-downloads/`.
2. Testsuite einmal laufen lassen, damit der Ausgangsstand belegt ist.
3. Diese Datei von oben nach unten lesen, bevor Code entsteht.

## Prio 1 — vor dem Release beheben

### 1.1 Die CI führt die Testsuite nicht aus

**Befund.** `.github/workflows/build.yml` hat vier Jobs (`macos`,
`windows`, `linux`, `release`) und ruft an keiner Stelle `pytest` auf.
Die Pipeline kann ein Release bauen und veröffentlichen, ohne dass eine
der 569 Tests gelaufen ist.

**Beleg.** `grep -n -i "test\|pytest" .github/workflows/build.yml` —
alle Treffer durchsehen. Es sind ausschließlich der
`test_marker`-Eingabeparameter und Shell-Prüfungen der Form
`test -f "$ZIP"` bzw. `test -x "$HELPER"` in der Paketverifikation. Kein
einziger pytest-Aufruf.

**Warum das hier besonders wiegt.** Automatisiertes Prüfen ist für
diesen Nutzer die Absicherung, die visuelle Kontrolle ersetzt. Ein
roter Stand würde heute unbemerkt durchgehen.

**Was zu tun ist.** Einen Testschritt in die Plattform-Jobs aufnehmen,
nach der Installation der Abhängigkeiten und vor dem PyInstaller-Lauf.
Zu klären und bewusst zu entscheiden, nicht nebenbei festzulegen:

- In welchen Jobs? Alle drei Plattformen wäre gründlich, kostet aber
  Laufzeit; nur Linux wäre schnell, prüft aber nicht die
  plattformspezifischen Pfade.
- GUI-Tests: `CODE_REVIEW.md` Befund 6 hält fest, dass der
  GUI-Testaufbau nicht headless-robust ist. Auf einem CI-Runner ohne
  Display kann das fehlschlagen. **Nicht** durch Überspringen oder
  Deaktivieren von Tests lösen — wenn Teile nicht headless laufen, das
  melden und einen Vorschlag machen, statt die Suite zu beschneiden.

**Fertig-Kriterium.** Der Workflow führt die Suite aus, ein
absichtlich rot gemachter Test lässt den Job fehlschlagen (einmal
lokal oder in einem Testlauf nachgewiesen, nicht behauptet).

### 1.2 Kein Changelog-Eintrag für 2.2

**Befund.** `CHANGELOG.txt` und `CHANGELOG.en.txt` enden beide bei
2.1.1 und wurden zuletzt am 2026-06-09 geändert (Commit `442b665`).
Für 2.2 gibt es keine Release Notes.

**Beleg.** `head -3 CHANGELOG.txt` und
`git log -1 --format='%ai %h %s' -- CHANGELOG.txt`.

**Was zu tun ist.** Je einen 2.2-Abschnitt in beiden Dateien, im Stil
der vorhandenen Einträge (deutsche Datei führend, englische als
Übersetzung). Rohmaterial aus den Commits seit `v2.1.2` — die folgende
Liste ist Ausgangspunkt, **nicht** zum Abschreiben gedacht: der
Changelog spricht Nutzersprache, nicht Commit-Sprache.

Nutzersichtbar in 2.2:

- Materialmodi music / neutral / speech und das Profil „Rohe
  Sprachaufnahme" ohne LUFS-Ziel; der Auswahlknopf heißt jetzt
  „Genre / Profil wählen…".
- Deutlich überarbeitete Berichtssprache für Sprachmaterial:
  profilabhängige Lautheits- und Frequenzurteile, materialabhängige
  Verdikte für leises Material, keine Genre-Bezüge mehr ohne gewähltes
  Profil (`e7de2a5`, `0bfee97`, `97531dd`, `3626ad3`, `3a8c863`,
  `0d264ad`, `9c31d11`, `7ebc9bb`, `f71f84a`).
- Protokollbetrachter für die laufende Sitzung, erreichbar über den
  Diagnosedialog; Logordner aus dem Über-Dialog erreichbar; pro Sitzung
  ein frisches Diagnoseprotokoll (`885de02`, `e3aab3e`, `ae2f74c`).
- Nachfrage nach dem Projektmodus, wenn mehr als fünf Dateien in den
  Zielbereich gelegt werden (`aa33ae8`).
- Fortschrittsanzeige und Restzeit in einem Element zusammengefasst
  (`9359b29`).
- Hinweise zur Lautheitsangleichung im Referenzvergleichsblock
  (`6bf15b4`); verbesserte Stereo-Blockausgabe (`5db14a2`).
- True-Peak-Zeitstempel nimmt jetzt den lautesten Kanal statt Kanal 0
  (`c9c73a4`); korrigierte Normbezüge in drei Genre-Profilen
  (`0ffa683`); korrigierte Messung bei gemischten Kanalzahlen im
  Projektmodus (`758b482`).
- Unter der Haube: durchgängige Streaming-Verarbeitung, dadurch
  drastisch geringerer Speicherbedarf bei langen Dateien; wirksame
  Zeitbegrenzung für ffmpeg-Läufe (`05a2d24`, `3269798`); atomares
  Speichern von Einstellungen und Genre-Profilen (`c9bd15c`,
  `39732e8`); technische Fehlerdetails erscheinen nicht mehr in
  Nutzerdialogen (`7c4d1b0`); gehärtetes Logging (`9415e47`,
  `a26b318`, `f4c571b`).

**Fertig-Kriterium.** Beide Dateien haben einen 2.2-Abschnitt, der
Umfang und Ton der vorhandenen Einträge trifft und keine Funktion
beschreibt, die es nicht gibt.

### 1.3 Die README beschreibt den Stand von 2.1.1

**Befund.** `README.md` wurde zuletzt am 2026-06-09 geändert (Commit
`442b665`), also vor jedem 2.2-Feature. Sie ist nicht falsch, aber
unvollständig: sie kennt nur „42 genre references" und einen
„Checkbox-based genre picker", während die Oberfläche inzwischen
„Choose Genre / Profile" heißt und das Profilkonzept den primären
Arbeitsablauf verändert hat.

**Beleg.** `git log -1 --format='%ai %h %s' -- README.md`, dazu
`grep -n -i "genre" README.md` gegen
`src/nonvisualaudio/resources/i18n/en.json` (Schlüssel
`ui.hint.selected_genres`).

**Was zu tun ist.** Den Funktionsteil der README auf den 2.2-Stand
bringen: Materialmodi und Profile, Protokollbetrachter,
Projektmodus-Nachfrage. Bestehende Struktur und Ton beibehalten, keine
Umgestaltung.

**Fertig-Kriterium.** Kein Abschnitt der README beschreibt mehr einen
Zustand, den die App nicht hat; die 2.2-Funktionen sind erwähnt.

## Prio 2 — sollte, aber kein Blocker

### 2.1 In-App-Hilfe kennt zwei neuere Funktionen nicht

**Befund.** `src/nonvisualaudio/resources/help/help_de.html` und
`help_en.html` wurden zuletzt am 2026-06-10 um 12:38 aktualisiert
(Commit `d4d6a47`). Der Protokollbetrachter (`885de02`, 23:06 desselben
Tages) und die Projektmodus-Nachfrage ab fünf Dateien (`aa33ae8`) kamen
danach und stehen nicht darin.

**Einordnung.** Der Protokollbetrachter sitzt im Diagnosedialog und ist
eine Support-Funktion — vertretbar, ihn nicht in der Hilfe zu führen.
Die Fünf-Dateien-Nachfrage begegnet dem Nutzer dagegen im normalen
Arbeitsablauf, ohne dass die Hilfe sie erklärt. Wenn nur eines von
beiden gemacht wird, dann diese.

**Beleg.** `git log -1 --format='%ai %h %s' -- src/nonvisualaudio/resources/help/help_de.html`
und `grep -ci "mehr als fünf\|mehr als 5" src/nonvisualaudio/resources/help/help_de.html`.

**Achtung.** Beide Hilfedateien werden ins Bundle ausgeliefert (die
Spec sammelt `*.html` unterhalb von `src/nonvisualaudio/resources/`).
Änderungen daran sind nutzersichtbar und brauchen dieselbe Sorgfalt wie
UI-Text: klare Sprache, keine rein visuellen Hinweise.

### 2.2 Kein Linter und keine Typprüfung konfiguriert

**Befund.** Weder `pyproject.toml` noch der Workflow konfigurieren
ruff, mypy, flake8, pyright oder black. „Grüne Tests" sind damit die
einzige automatisierte Qualitätsaussage.

**Beleg.** `grep -n "ruff\|mypy\|flake8\|pyright\|black" pyproject.toml .github/workflows/*.yml`
liefert nichts.

**Was zu tun ist.** Für 2.2 nichts — das ist eine bewusste Entscheidung
des Nutzers, keine Aufgabe für diese Sitzung. Hier festgehalten, damit
es nicht in Vergessenheit gerät. **Nicht** eigenmächtig einen Linter
einführen; das würde breite Formatierungsänderungen quer durch den Baum
nach sich ziehen.

## Prio 3 — bewusst vertagt, nichts zu tun

Die Befunde 2, 4, 5, 6, 7 und 8 aus `CODE_REVIEW.md` stehen in
`docs/release-2.2-stabilisierung.md` unter „Optional nach dem Release".
Das ist eine getroffene Entscheidung und keine Lücke.

Einer davon wurde gegengeprüft, weil das Review ihn als „Hoch" führt:
**Befund 2, quadratische Laufzeit im `DynamicsStreamer` bei sehr kleinen
Chunks.** In Produktion greift er nicht — die Chunkgrößen sind rund eine
Sekunde (soundfile-Pfad) bzw. 1 MB (ffmpeg-Pipe), nie der
Ein-Sample-Worstcase. Gemessen wurde die Pipeline durchgängig bei 159×
Echtzeit mit linearer Skalierung von 10 auf 60 Minuten. Kostet
Testlaufzeit, nicht Nutzerzeit. **Kein Blocker, hier nichts zu tun.**

## Was ausdrücklich in Ordnung ist

Danach wurde gesucht und nichts gefunden. Diese Punkte sind erledigt und
brauchen keine erneute Prüfung:

- **569 Tests grün**, Exit-Code 0, in 60,96 s.
- **Versionsstand konsistent:** `pyproject.toml` sagt 2.2.0, die Spec
  liest daraus (`_project_version()`), stempelt den Wert in
  `_version.txt` und in die Info.plist, und `tests/test_packaging.py`
  prüft ihn.
- **Kein einziges TODO, FIXME, XXX oder HACK** im Quellcode.
- **Alle vier Phasen des Stabilisierungsplans erledigt**, jeweils mit
  Commit-Hash in `docs/release-2.2-stabilisierung.md`.
- **Die Hilfe deckt Materialmodi und Projektmodus ab.** Eine erste
  Suche danach sah nach einer Lücke aus, war aber ein
  Terminologie-Artefakt — die deutsche Hilfe schreibt „Projekt", nicht
  „Projektmodus".
- **Der Bundle-Inhalt ist geprüft:** `NonvisualAudio.spec` sammelt in
  `datas` ausschließlich das gebündelte ffmpeg, `build/_version.txt`,
  die dist-info über `copy_metadata` sowie `*.json` und `*.html`
  unterhalb von `src/nonvisualaudio/resources/`. Interne Unterlagen —
  alles unter `docs/` einschließlich dieser Datei sowie `CODE_REVIEW.md`
  im Wurzelverzeichnis — können das Bundle nicht erreichen.

## Nicht geprüft (offen, kein Auftrag dieser Sitzung)

Ehrlich als ungeprüft zu behandeln, nicht als bestanden:

- Ein echter Release-Build auf allen drei Plattformen.
- Die GUI-Verifikationsskripte in `scripts/`
  (`verify_window_size.py`, `verify_about_dialog_layout.py`,
  `verify_genre_button_layout.py`, `verify_log_viewer_layout.py`). Die
  brauchen eine laufende Oberfläche. Sie wären der richtige letzte
  Schritt vor dem Release — durch den Nutzer oder in einer Sitzung mit
  Display.
- `scripts/verify_loudness_against_ebu_r128.py` seit dem 2026-08-05
  nicht erneut gelaufen.

## Reihenfolge

1. Prio 1.2 (Changelog) — reine Textarbeit, kein Risiko, größter
   sichtbarer Nutzen.
2. Prio 1.3 (README) — ebenfalls Text.
3. Prio 1.1 (CI-Testschritt) — der einzige Punkt mit echtem
   Klärungsbedarf (headless-Frage). Zuletzt, damit die Textarbeit nicht
   daran hängt.
4. Prio 2.1 (Hilfe) — wenn Zeit ist; mindestens die
   Fünf-Dateien-Nachfrage.

Jeder Punkt schließt mit eigenem, thematisch geschlossenem Commit ab.
Nach jedem Punkt die volle Suite laufen lassen, nicht erst am Ende.
Vor dem letzten Commit ein unabhängiger Review-Subagent (PASS/FAIL).

## Abschluss: Push

Wenn die Punkte aus Prio 1 erledigt sind, die Suite grün ist und der
Review PASS gemeldet hat:

**`version-2.2` pushen.** Vorher ansagen, welcher Branch gepusht wird.

Der Push löst **keine** Automation aus: `.github/workflows/build.yml`
reagiert nur auf `workflow_dispatch` und auf Pushes von Tags nach dem
Muster `v*`, nicht auf Branch-Pushes. Zum Prüfen:
`sed -n '1,15p' .github/workflows/build.yml`.

Zum Zeitpunkt dieser Aufzeichnung lagen 20 Commits ungepusht vor
`origin/version-2.2` — der Push ist also überfällig und zugleich die
Sicherung der gesamten 2.2-Arbeit.

## Ausdrücklich NICHT tun

Der Nutzer stößt Workflow und Release selbst an. Ohne seine
ausdrückliche Freigabe für genau diesen Schritt gilt:

- **Keinen Tag erstellen oder pushen** (`v2.2.0` oder ähnlich).
- **Den Workflow nicht manuell auslösen** (kein `workflow_dispatch`,
  kein `gh workflow run`).
- **Kein GitHub-Release anlegen**, keine Artefakte hochladen.
- **`main` nicht anfassen**, nicht mergen, kein Force-Push.
- Keine Version in `pyproject.toml` ändern; 2.2.0 steht bereits richtig.

## Abschlussbericht der Sitzung

Wie üblich screenreader-freundlich: Ergebnis (PASS / FAIL / PARTIAL),
geänderte Dateien, Tests und Checks mit Kommando und Exit-Code, wie sich
die Änderung rückgängig machen lässt, bekannte Restrisiken, ausdrücklich
nicht geprüfte Punkte, nächster empfohlener Schritt. Bestätigen, dass
gepusht wurde und dass **kein** Tag, Workflow-Lauf oder Release
angestoßen wurde.
