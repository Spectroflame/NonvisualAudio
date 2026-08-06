# Vorsichtiger Einstieg in die Zerlegung von `MainWindow`

Stand: 2026-08-06. Ausgangspunkt: Branch `version-2.2`, Commit
`7c4d1b0`. `src/nonvisualaudio/ui/main_window.py` hat an diesem Stand
1.398 Zeilen. Die vollständige Testsuite lief vor Erstellung dieses Plans mit

```text
.venv/bin/pytest -q
569 passed in 57.66s
Exit-Code 0
```

Diese Übersicht plant bewusst nur den ersten kleinen Block der Zerlegung.
Weitergehende Extraktionen (Menüs, Widget-Aufbau oder Lauf-/Abbruchsteuerung)
werden erst nach Auswertung dieses Blocks geplant. Damit wird aus dem Wunsch,
die Gottklasse zu verkleinern, kein unkontrollierter Architekturumbau.

Jede Stufe ist für eine eigene spätere Arbeitssitzung vorgesehen und steht
vollständig in einer eigenen Datei. Ein frischer Agent liest zuerst diese
Übersicht und danach nur die Datei der auszuführenden Stufe.

## Zielbild dieses ersten Blocks

`MainWindow` bleibt der wx-Frame, Kompositionspunkt und Besitzer der Widgets.
Ausgelagert wird zunächst nur die Erzeugung der mehrzeiligen, für Screenreader
wichtigen Zusammenfassungstexte. Vorher werden deren beobachtbares Verhalten
und die wichtigsten Accessibility-Verträge des Fensters durch Tests fixiert.

Nicht Teil dieses Blocks sind:

- Änderungen an Widget-Typen, Widget-Hierarchie oder Tab-Reihenfolge;
- Änderungen an Fokusführung, Tastenkürzeln, Menüs oder Drag-and-drop;
- Änderungen an Analyse, Worker, Fortschritt oder Abbruchlogik;
- ein allgemeiner View-, Presenter-, Controller- oder State-Machine-Umbau;
- neue Dependencies;
- optische Neugestaltung oder neue Funktionen.

## Status

Der ausführende Agent aktualisiert den Status erst nach vollständigem Abschluss
der jeweiligen Stufe und trägt den Commit-Hash ein.

- Stufe 1 – Accessibility-Charakterisierungstests: geplant
- Stufe 2 – wx-freie Zusammenfassungslogik extrahieren: geplant

## Stufen und Reihenfolge

1. [`main-window-refactoring-stage-1.md`](main-window-refactoring-stage-1.md):
   Mit echten wx-Widgets das aktuelle Accessibility- und Zustandsverhalten
   charakterisieren. In dieser Stufe wird kein Produktivcode geändert.
2. [`main-window-refactoring-stage-2.md`](main-window-refactoring-stage-2.md):
   Die reine Erzeugung der Ziel-, Genre-, Abschnitts- und Referenztexte in ein
   wx-freies Modul verschieben und `MainWindow` nur noch anwenden lassen.

Stufe 2 setzt eine abgeschlossene, grüne und unabhängig geprüfte Stufe 1
voraus. Die Stufen nicht zusammenlegen und nicht in einem gemeinsamen Commit
abarbeiten: Der Teststand vor der Extraktion ist der entscheidende
Verhaltensnachweis.

## Verbindliche Sicherheitsregeln für beide Stufen

1. Zu Sitzungsbeginn `git status --short`, aktuellen Branch/Commit und eine
   eventuell vorhandene `AGENT_HANDOFF.md` prüfen. Fremde Änderungen nicht
   anfassen oder mitcommitten; bei verändertem Arbeitsbaum nachfragen.
2. Die tatsächlich installierte wxPython-API nachsehen, bevor auf Methoden wie
   `GetHelpText`, Stilflags, Default-Button- oder Fokusabfragen vertraut wird.
3. Normale Nutzerabläufe müssen bytegleich formuliert bleiben. Eine Ausnahme
   ist ausschließlich die in Stufe 2 beschriebene Neutralisierung manipulativer
   Steuer- und Richtungszeichen; sie ist eine beabsichtigte Sicherheits- und
   Accessibility-Härtung und braucht eigene Regressionstests.
4. Tests dürfen keine fehlschlagenden Fälle überspringen, als erwarteten Fehler
   markieren oder Erwartungen an falsches Verhalten anpassen.
5. Nach jeder Stufe: fokussierter Teillauf, vollständige Testsuite,
   programmgesteuerte Layoutprüfungen, unabhängiger lesender Review und ein
   eigener thematisch geschlossener Commit.
6. Ein Screenshot allein ist kein Accessibility- oder Layoutnachweis. Ergebnisse
   müssen als Klartext mit PASS/FAIL/PARTIAL und konkreten Werten vorliegen.
7. Kein Push, Tag, Release, Build-Upload oder sonstige Veröffentlichung ist
   durch diesen Plan autorisiert.

## Abbruchkriterien

Die laufende Stufe wird gestoppt und als PARTIAL/FAIL gemeldet, wenn

- ein Test offenbart, dass der dokumentierte Ist-Zustand plattformabhängig und
  nicht zuverlässig messbar ist;
- für die Extraktion Widget-Typen, Fokus, Tab-Reihenfolge oder Event-Bindings
  geändert werden müssten;
- der Umfang über die in der Stufendatei genannten Produktivmethoden hinausgeht;
- drei unterschiedliche Lösungsansätze am selben Problem scheitern;
- die vollständige Testsuite oder eine verpflichtende Layoutprüfung nicht grün
  wird.

Dann nicht weiter umbauen, sondern Befund, Versuche, verbleibende Hypothesen und
einen minimalen nächsten Schritt dokumentieren.
