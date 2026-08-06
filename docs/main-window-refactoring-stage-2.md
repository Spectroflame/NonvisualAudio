# MainWindow-Zerlegung – Stufe 2: Zusammenfassungslogik extrahieren

Diese Datei ist die selbständige Arbeitsanweisung für Stufe 2. Ein frischer
Agent soll zusätzlich die Übersicht
[`main-window-refactoring.md`](main-window-refactoring.md) lesen und dort
prüfen, dass Stufe 1 mit Commit-Hash als erledigt markiert ist. Fehlt dieser
Nachweis, Stufe 2 nicht beginnen.

## Kontext und Voraussetzung

- Projekt: NonvisualAudio, eine wxPython-Audioanalyse für blinde und
  sehbehinderte Audio-Fachleute.
- `MainWindow` erzeugt derzeit selbst die angezeigten Texte für Ziele, Genres,
  Berichtsabschnitte und Referenzen und schreibt sie zugleich in wx-Widgets
  sowie deren Accessibility-Hilfetexte.
- Das vermischt reine Textaufbereitung mit UI-Zustandsänderung und erschwert
  kopflose Tests.
- Stufe 1 hat das beobachtbare Verhalten mit echten wx-Widgets fixiert. Deren
  Tests sind die primäre Regressionsbarriere.

## Vor Beginn prüfen

1. Globale/projektlokale Anweisungen und eine eventuell vorhandene
   `AGENT_HANDOFF.md` vollständig lesen.
2. `git status --short`, Branch und Commit prüfen. Bei fremden Änderungen nicht
   beginnen, bevor der Nutzer den Umgang damit bestätigt hat.
3. In `docs/main-window-refactoring.md` verifizieren, dass Stufe 1 erledigt ist.
4. Vollständige Testsuite als neue Baseline ausführen:

   ```text
   .venv/bin/pytest -q
   ```

5. Vollständig lesen:
   `src/nonvisualaudio/ui/main_window.py`,
   `src/nonvisualaudio/ui/a11y.py`,
   `src/nonvisualaudio/localization.py`,
   die in Stufe 1 hinzugefügten Tests sowie
   `tests/test_project_prompt.py` und `tests/test_progress_display.py`.
6. Die aktuellen Signaturen und Rückgaben von `Path.name`, der
   Lokalisierungsfunktion `t()` und den verwendeten wx-Methoden in der
   installierten Version prüfen.

## Ziel

Ein neues wx-freies Modul
`src/nonvisualaudio/ui/main_window_summaries.py` erzeugt ausschließlich die
vier mehrzeiligen Klartextzusammenfassungen. `MainWindow` sammelt weiterhin den
aktuellen Zustand, schreibt den zurückgegebenen Text mit `ChangeValue()` in das
jeweilige Widget und aktualisiert mit `a11y.update_help()` Hilfetext und
Tooltip.

Das neue Modul darf `wx` weder direkt noch indirekt importieren. Es besitzt
keinen veränderlichen globalen Zustand und führt keine Datei-, Clipboard-,
Dialog-, Logging- oder Persistenzoperationen aus.

## Vorgeschlagene kleine API

Die Namen dürfen an den bestehenden Projektstil angepasst werden, die
Verantwortungsgrenzen nicht:

- `targets_summary(paths: Sequence[str]) -> str`
- `genres_summary(display_names: Sequence[str]) -> str`
- `sections_summary(display_names: Sequence[str], *, all_selected: bool) -> str`
- `reference_summary(paths: Sequence[str]) -> str`

Die Funktionen erhalten Snapshots bzw. nicht veränderliche Sichten und ändern
keine übergebenen Listen. Genre- und Abschnittsauflösung bleiben zunächst beim
Aufrufer, damit das neue Modul weder den veränderlichen Genrekatalog noch das
wx-basierte Abschnittsdialog-Modul kennen muss.

Falls sich beim Implementieren zeigt, dass eine andere Signatur deutlich
weniger Kopplung erzeugt, vor der Änderung begründen und prüfen, ob dies den
vereinbarten Umfang erweitert. Keine Klasse oder abstrakte Presenter-Schicht
für vier reine Funktionen einführen.

## Sicherheits- und Accessibility-Härtung für Listenelemente

Dateinamen sowie nutzerdefinierte Genre-Anzeigenamen sind Untrusted Input. Der
Ausgangspunkt setzt sie in mehrzeilige UI-Texte ein. Zeilenumbrüche,
Steuerzeichen, ANSI-Escapes und Unicode-Bidi-Steuerzeichen könnten dadurch
zusätzliche scheinbare Listeneinträge oder eine irreführende Vorlesereihenfolge
erzeugen.

Das neue Modul darf diesen unsicheren Sink nicht unverändert duplizieren. Es
braucht einen kleinen, kontextspezifischen Helfer für genau ein Listenelement:

- `\n`, `\r`, Tab, C0/C1, DEL und ESC werden sichtbar und einzeilig
  neutralisiert;
- Unicode-Bidi-Steuerzeichen werden sichtbar neutralisiert, nicht ausgeführt;
- normale Unicode-Zeichen, Umlaute, Leerzeichen und Satzzeichen bleiben
  unverändert;
- Pfade werden weiterhin auf den Basisdateinamen reduziert;
- keine HTML-/Markdown-Escapes einführen, weil das Ziel ein nativer
  `wx.TextCtrl` ist;
- keine neue Dependency verwenden;
- keine stillschweigende Kürzung normaler Dateinamen einführen. Falls sehr lange
  nutzerdefinierte Anzeigenamen begrenzt werden sollen, ist das ein eigener,
  vorher freizugebender Verhaltensschritt.

Dies ist die einzige beabsichtigte Verhaltensänderung dieser Stufe. Sie ist kein
allgemeiner Sanitizer und darf nicht nebenbei Logging, Export oder Berichte
verändern. Die genaue sichtbare Ersatzdarstellung muss in Tests festgelegt und
im Abschlussbericht genannt werden.

## Änderungen an `MainWindow`

Nur diese vier Methoden werden auf Delegation umgestellt:

- `_refresh_targets_view()`;
- `_update_genre_label()`;
- `_update_sections_label()`;
- `_update_reference_label()`.

Sie behalten ihre Namen und Aufrufstellen. Jede Methode soll weiterhin genau
zwei UI-Aufgaben ausführen:

1. Text aus dem neuen Modul beziehen und per `ChangeValue()` setzen;
2. denselben Text per `a11y.update_help()` als Accessibility-Hilfe und Tooltip
   setzen.

Nicht ändern:

- Widget-Typen, Konstruktion, Größe, Sizer und Reihenfolge;
- Namen der Widget-Attribute, damit bestehende Tests und Aufrufer stabil
  bleiben;
- Fokus, Enable/Disable-Logik und Default-Button;
- Event-Bindings, Menüs, Accelerators, Dialoge und Drag-and-drop;
- Ziel-/Referenzlisten, Genre-/Abschnittsauswahl oder Projektmodus;
- Analyse-, Worker-, Fortschritts-, Abbruch- und Schließlogik;
- Übersetzungsdateien, sofern ein fehlender Schlüssel nicht durch einen
  tatsächlich fehlschlagenden Test belegt wird. Neue Texte sind für die
  vorgeschlagene sichtbare Escape-Darstellung nicht vorgesehen.

## Tests

Eine neue kopflose Datei `tests/test_main_window_summaries.py` testet die vier
Funktionen ohne `wx.App` und ohne reale Dateien. Mindestens:

- leerer Zustand für jede Zusammenfassung;
- genau ein Element;
- mehrere Elemente mit stabiler Reihenfolge und Nummerierung;
- korrekte Singular-/Pluraltexte in Deutsch und Englisch;
- Ziel- und Referenzpfade zeigen nur Basisnamen;
- Abschnittssonderfall „alle“ sowie Teilmenge in der vom Aufrufer gelieferten
  kanonischen Reihenfolge;
- Eingabelisten bleiben unverändert;
- normale Umlaute, Leerzeichen und Satzzeichen bleiben unverändert;
- Zeilenumbruch, Wagenrücklauf, Tab, ESC, weitere C0/C1-Zeichen, DEL und
  Unicode-Bidi-Steuerzeichen erzeugen keine zweite physische Listenzeile und
  keine aktive Richtungsänderung;
- sehr lange, aber gültige normale Namen crashen nicht und werden nicht
  stillschweigend abgeschnitten.

Die Stufe-1-Tests müssen zusätzlich unverändert grün bleiben. Erwartungen nicht
an versehentliche neue Formulierungen anpassen; bei normalen Eingaben müssen
die bisherigen Texte exakt erhalten bleiben.

## Prüfungen

Zuerst fokussiert und kopflos:

```text
.venv/bin/pytest -q tests/test_main_window_summaries.py
```

Dann die betroffenen echten wx-Verträge:

```text
.venv/bin/pytest -q tests/test_main_window_accessibility.py \
  tests/test_project_prompt.py tests/test_progress_display.py tests/test_theme.py
```

Danach vollständig:

```text
.venv/bin/pytest -q
```

Dann die programmgesteuerten Layoutprüfungen:

```text
.venv/bin/python scripts/verify_window_size.py
.venv/bin/python scripts/verify_genre_button_layout.py
```

Zusätzlich Importgrenze prüfen, ohne hierfür Projektdateien umzuschreiben:

```text
.venv/bin/python -c "import sys; import nonvisualaudio.ui.main_window_summaries; assert 'wx' not in sys.modules"
```

Falls die laufende Testumgebung `wx` bereits anderweitig vorlädt, den
Importgrenzentest in einem frischen Prozess so anpassen, dass er nur den
Importeffekt des neuen Moduls misst. Kein irreführendes grünes Ergebnis
berichten.

Für jedes Kommando Exit-Code, Zahl bestanden/fehlgeschlagen/übersprungen und
entscheidende Ausgabezeilen festhalten. Layoutausgabe als Klartext auswerten;
ein Screenshot allein genügt nicht.

## Review und Abschluss

Der unabhängige, nur lesende Review sucht ausdrücklich nach:

- Änderungen an normalen deutschen oder englischen UI-Texten;
- verlorenen Accessibility-Hilfetexten oder Tooltips;
- importiertem `wx` oder verstecktem veränderlichem Zustand im neuen Modul;
- unsicherem Umgang mit Steuer-/Bidi-Zeichen;
- übermäßigem Refactor, unnötiger Abstraktion oder neuer Dependency;
- Layout-, Fokus-, Tab- oder Event-Regressionen;
- Tests, die nur die neue Implementierung spiegeln statt Verhalten zu prüfen.

Abschluss:

1. Review-Ergebnis PASS, PASS MIT HINWEISEN oder FAIL dokumentieren.
2. Bei FAIL nicht den Umfang eigenmächtig erweitern; Findings und minimalen
   Fix-Plan melden.
3. Ein eigener thematisch geschlossener Commit nur für Stufe 2.
4. In `docs/main-window-refactoring.md` den Status mit Commit-Hash aktualisieren.
5. Abschlussbericht mit geänderten Dateien, Befehlen, Exit-Codes,
   Testanzahlen, Rückgängig-Weg, Sicherheits-/Accessibility-Auswirkung,
   Restrisiken und nicht geprüften Plattformen.

Nach Stufe 2 endet dieser Plan. Eine weitere Zerlegung von Menüaufbau,
Eingabecontroller, Widget-Aufbau oder Lauf-/Abbruchzustand braucht eine neue
Bestandsaufnahme und ausdrückliche Freigabe; sie ist nicht stillschweigend Teil
dieser Arbeit.
