# MainWindow-Zerlegung – Stufe 1: Accessibility-Verhalten fixieren

Diese Datei ist die selbständige Arbeitsanweisung für Stufe 1. Ein frischer
Agent soll zusätzlich nur die Übersicht
[`main-window-refactoring.md`](main-window-refactoring.md) lesen. In dieser
Stufe wird ausschließlich ein Sicherheitsnetz geschaffen; Produktivcode bleibt
unverändert.

## Kontext

- Projekt: NonvisualAudio, eine wxPython-Audioanalyse für blinde und
  sehbehinderte Audio-Fachleute.
- Geplanter Ausgangspunkt: Branch `version-2.2`, Commit `7c4d1b0`.
- `MainWindow` liegt in `src/nonvisualaudio/ui/main_window.py` und vereint
  Widget-Aufbau, Accessibility, Eingabeauswahl, Menüs und Laufsteuerung.
- Bereits vorhandene direkte Fenstertests:
  `tests/test_project_prompt.py` und `tests/test_progress_display.py`.
- Bereits vorhandene programmgesteuerte Layoutprüfungen:
  `scripts/verify_window_size.py` und
  `scripts/verify_genre_button_layout.py`.
- Letzte bekannte Baseline vom 2026-08-06:
  `.venv/bin/pytest -q`, Exit-Code 0, 569 bestanden in 57,66 Sekunden.
  Diese Zahl ist nur historischer Kontext und muss zu Sitzungsbeginn neu
  ermittelt werden.

## Vor Beginn prüfen

1. Globale/projektlokale Anweisungen und eine eventuell vorhandene
   `AGENT_HANDOFF.md` vollständig lesen.
2. `git status --short`, Branch und Commit prüfen. Bei fremden Änderungen nicht
   beginnen, bevor der Nutzer den Umgang damit bestätigt hat.
3. Vollständige Baseline ausführen:

   ```text
   .venv/bin/pytest -q
   ```

4. Diese Dateien vollständig lesen:
   `src/nonvisualaudio/ui/main_window.py`,
   `src/nonvisualaudio/ui/a11y.py`,
   `tests/test_project_prompt.py`,
   `tests/test_progress_display.py` und `tests/test_theme.py`.
5. In der installierten wxPython-Version die tatsächlich verfügbaren
   Abfragemethoden und Stilkonstanten prüfen. Nicht aus dem Gedächtnis
   programmieren.

## Ziel

Eine neue Datei `tests/test_main_window_accessibility.py` charakterisiert mit
einer echten `wx.App` und einem echten `MainWindow` die beobachtbaren Verträge,
die Stufe 2 nicht verändern darf.

Die Tests sollen eine spätere interne Extraktion erlauben, aber versehentliche
Änderungen an Screenreader-Ausgabe, Startzustand, Widget-Rollen und sichtbarer
Struktur erkennen.

## Genaue Testabdeckung

### 1. Widget-Rollen und Stil

Mindestens prüfen:

- Ziel-, Genre-, Referenz- und Abschnittsanzeige bleiben fokussierbare,
  schreibgeschützte `wx.TextCtrl`-Elemente;
- die mehrzeiligen Anzeigen behalten `wx.TE_MULTILINE`, `wx.TE_READONLY` und
  `wx.TE_DONTWRAP`;
- der Fortschrittstext bleibt ein schreibgeschützter `wx.TextCtrl`;
- Projektmodus bleibt eine native `wx.CheckBox`;
- Analyse bleibt ein nativer `wx.Button` und der Default-Button.

Stilflags nur so prüfen, wie es die installierte wxPython-Version zuverlässig
unterstützt. Plattforminterne, nicht dokumentierte Werte nicht festnageln.

### 2. Zugängliche Namen und Hilfetexte

Für beide UI-Sprachen Deutsch und Englisch prüfen:

- `MainWindow.GetName()` entspricht dem lokalisierten Fenstertitel;
- alle interaktiven Hauptcontrols besitzen den vorgesehenen kurzen Namen;
- der längere Hilfetext bleibt vom Namen getrennt;
- Tooltip/Hilfetext der dynamischen Nur-Lese-Anzeigen entspricht ihrem aktuellen
  Inhalt;
- keine rohe Übersetzungs-ID wird ausgegeben.

Die Sprachumschaltung in Tests immer zurücksetzen, damit keine Reihenfolge-
Abhängigkeit zur übrigen Suite entsteht. Genreprofile gegebenenfalls so neu
laden, wie es der echte App-Start tut.

### 3. Startzustand

Mindestens prüfen:

- Öffnen ist aktiviert;
- Ziel- und Referenz-Löschen sind deaktiviert;
- Analysieren ist ohne Zieldatei deaktiviert;
- Fortschrittsbalken und Fortschrittstext sind verborgen;
- Projektmodus ist aus;
- die vier Nur-Lese-Anzeigen enthalten ihre lokalisierten Leerzustände;
- der initiale Fokus liegt auf dem Öffnen-Button, sofern dies auf der
  Testplattform nach Anzeigen und Verarbeiten der nötigen wx-Ereignisse
  zuverlässig messbar ist.

Falls Fokus oder Default-Button auf einer Plattform nicht stabil automatisiert
abfragbar sind, nicht mit einem schwächeren Schein-Test ersetzen. Den Punkt als
PARTIAL dokumentieren und die übrigen Verträge weiterhin testen.

### 4. Dynamische Zusammenfassungen

Mit normalen, kontrollierten Testwerten prüfen:

- Zielansicht: Anzahl, Reihenfolge, fortlaufende Nummerierung und nur
  Basisdateinamen;
- Genreansicht: Anzahl, Reihenfolge und Nummerierung;
- Abschnittsansicht: Sondertext für „alle Abschnitte“ sowie Teilmenge in der
  kanonischen Abschnittsreihenfolge;
- Referenzansicht: eigener Ein-Datei-Text sowie mehrzeilige Projektansicht;
- nach jeder Aktualisierung entsprechen `GetValue()`, Hilfetext und Tooltip
  einander.

Keine echten Nutzerpfade, Zwischenablagen oder Drag-and-drop-Inhalte in
Testausgaben oder Fehlermeldungen übernehmen. Testnamen und Werte fest und
harmlos halten.

### 5. Reihenfolge und Geometrie

Die Hauptcontrols in ihrer aktuellen logischen Reihenfolge erfassen:

1. Audiodateien öffnen und Ziele löschen;
2. Zielansicht;
3. Genreauswahl und Genreansicht;
4. Referenzauswahl, Referenz löschen und Referenzansicht;
5. Abschnittsauswahl und Abschnittsansicht;
6. Projektmodus;
7. Analysieren;
8. Fortschrittsbalken und Fortschrittstext.

Nur eine dokumentierte, plattformübergreifend belastbare wx-Abfrage verwenden.
Wenn `GetChildren()` native Hilfsfenster enthält oder nicht der Tab-Reihenfolge
entspricht, keine falsche Garantie daraus ableiten. In diesem Fall die
Reihenfolge über gezielte Nachbarschafts-/Fokusprüfungen testen oder als nicht
vollständig automatisierbar melden.

Die bestehenden Layoutskripte nicht in Unit-Tests umschreiben. Sie bleiben
separate reale Geometrieprüfungen.

## Bewusste Nicht-Ziele

- Kein Produktivcode und keine Übersetzung wird geändert.
- Keine neue Helper-Funktion wird vorweggenommen.
- Keine Menüs, Tastenkürzel, Dialoge oder Worker-Callbacks werden umgebaut.
- Keine Screenshot-Baselines und keine pixelgenauen Farbtests.
- Keine Tests gegen private wx-Implementierungsdetails.

## Prüfungen

Zuerst fokussiert:

```text
.venv/bin/pytest -q tests/test_main_window_accessibility.py \
  tests/test_project_prompt.py tests/test_progress_display.py tests/test_theme.py
```

Danach vollständig:

```text
.venv/bin/pytest -q
```

Dann beide programmgesteuerten Layoutprüfungen in der echten wx-Umgebung:

```text
.venv/bin/python scripts/verify_window_size.py
.venv/bin/python scripts/verify_genre_button_layout.py
```

Für jedes Kommando Exit-Code, Zahl bestanden/fehlgeschlagen/übersprungen und
entscheidende Ausgabezeilen festhalten. `verify_window_size.py` besitzt am
Ausgangspunkt möglicherweise keinen aussagekräftigen Fehler-Exit-Code; daher
zusätzlich seine Klartextausgabe auswerten und diesen verbleibenden Mangel
offen nennen, nicht still als PASS behandeln.

## Review und Abschluss

1. Unabhängiger, nur lesender Review mit Fokus auf zu enge Tests,
   Plattformabhängigkeit, Accessibility-Lücken und falsche grüne Ergebnisse.
2. Review-Ergebnis als PASS, PASS MIT HINWEISEN oder FAIL dokumentieren.
3. Bei FAIL nicht eigenmächtig den Umfang erweitern; Findings und minimalen
   Korrekturplan melden.
4. Ein eigener Commit nur für Stufe 1, beispielsweise mit einer Nachricht, die
   begründet, dass der beobachtbare Accessibility-Vertrag vor der Extraktion
   fixiert wird.
5. In `docs/main-window-refactoring.md` den Status mit Commit-Hash aktualisieren.
6. Abschlussbericht mit geänderten Dateien, allen Nachweisen, Rückgängig-Weg,
   bekannten Restrisiken und nicht geprüften Plattformen.

Stufe 1 ist erst abgeschlossen, wenn Produktivcode unverändert ist, alle
ausführbaren Prüfungen grün sind und der unabhängige Review nicht FAIL lautet.
