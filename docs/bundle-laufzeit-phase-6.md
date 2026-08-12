# Phase 6: ffmpeg-Minimalbuild statt Vollausbau

Diese Datei ist selbständig umsetzbar. Ein frischer Agent braucht keine
weiteren Erklärungen des Nutzers — alles Nötige steht hier oder an den
genannten Stellen im Repo.

**Diese Phase braucht vor Beginn die ausdrückliche Freigabe des
Nutzers.** Sie bringt als einzige eine externe Build-Kette und eine
Lizenzprüfung mit.

## Kontext für einen frischen Agenten

- Projekt: NonvisualAudio, Audio-Analyse-App für einen blinden Nutzer.
- **Zielrelease: nach 2.2.** Dieser Plan wird ausdrücklich *nicht* auf
  dem 2.2-Releasebranch umgesetzt — er würde dessen Stabilisierung
  gefährden. Der Arbeitsbranch wird beim Start der ersten Phase
  festgelegt und in `docs/bundle-laufzeit-uebersicht.md` unter „Status"
  vermerkt. Steht dort keiner: nachfragen, nicht raten.
- Teil des Bundle-, Startzeit- und Laufzeitplans (Übersicht:
  `docs/bundle-laufzeit-uebersicht.md`), Block E.
- Unabhängig von den Phasen 2 bis 5. Setzt Phase 1 voraus.
- **Vor Phase 7 durchführen**, wenn beide gemacht werden: Phase 7 misst
  eine Nebenläufigkeit gegen ffmpeg, und diese Messung soll gegen das
  tatsächlich ausgelieferte ffmpeg laufen, nicht gegen den Vollausbau.
- Testkommando (voll): `./.venv/bin/python -m pytest tests -q`

## Vor Beginn prüfen

1. **Freigabe des Nutzers für genau diese Phase liegt vor.**
2. Sauberer Arbeitsbaum (`git status`); letzter Commit ist der
   Rückkehrpunkt.
3. Status in `docs/bundle-laufzeit-uebersicht.md`: Phase 1 muss erledigt
   sein.
4. `src/nonvisualaudio/audio/ffmpeg_runner.py` lesen (wie das Binary
   gefunden und aufgerufen wird), dazu `NonvisualAudio.spec` und
   `tests/test_packaging.py`.

## Befund (worum es geht)

Das gebündelte ffmpeg ist mit 137,6 MB der zweitgrößte Posten im Bundle
(30 % von 456,7 MB entpackt). Es ist ein Vollausbau (evermeet-Build) mit
x264, x265, AV1, VP8/VP9, WebP, ZeroMQ, Rubberband, Bluray, Fontconfig
und weiterem — nichts davon berührt diese App je.

Gebraucht werden nachweislich nur:

- **Demuxer und Decoder** für die im README zugesagten Eingabeformate:
  WAV, AIFF, MP3, M4A/AAC, OGG (Vorbis), FLAC, Opus, WMA.
- **Filter:** `ebur128`, `aresample`, `concat`, `asplit`, `pan`.
- **Muxer:** `null` und `f32le` (Rohausgabe über die Pipe).

## Ziel dieser Phase

Ein selbst gebautes, minimales ffmpeg mit identischem Messverhalten,
das den Bundle-Posten deutlich verkleinert. Geschätzte Größenordnung
10 bis 20 MB — **das ist eine Schätzung, kein Messwert**, und der erste
echte Build ersetzt sie durch eine Zahl.

## Umsetzungsskizze

### Vorprüfung, bevor gebaut wird

Eine ausdrückliche Matrix aufstellen und dokumentieren, nicht im Kopf
behalten:

- Pro zugesagtem Eingabeformat: Container, Codec, benötigter Demuxer,
  benötigter Decoder, benötigter Parser.
- Pro benutztem Filter: der exakte Filtername und ob er von einer
  optionalen Bibliothek abhängt (`aresample` hängt an swresample; mit
  `--enable-libsoxr` oder ohne — das ist **messrelevant**, siehe unten).
- Die `--enable`/`--disable`-Konfiguration, die daraus folgt, plus die
  exakte ffmpeg-Version und der Commit-Hash.
- Zielarchitekturen: welche Plattformen und Architekturen ausgeliefert
  werden (macOS arm64 und x86_64? Windows? Linux?). Das Bundle enthält
  heute bereits libsndfile für beide macOS-Architekturen.

### Lizenz

Vor dem Bauen klären und schriftlich festhalten, welche Lizenz der
Minimalbuild trägt. Ein Build ohne GPL-Komponenten kann LGPL sein, was
andere Pflichten auslöst als der heutige `--enable-gpl`-Vollausbau.
Erforderlich sind in jedem Fall: Lizenztexte, Copyright- und
Änderungshinweise, und je nach Lizenz ein Quellcodeangebot. Was
konkret mitgeliefert werden muss, vollständig auflisten — und nichts
davon ohne ausdrückliche Freigabe irgendwo hochladen.

### Messverhalten

Der springende Punkt: `ebur128` und der Resampler müssen **bitgleiche
Messwerte** liefern. Risikostellen:

- Ist `libsoxr` im heutigen Build aktiv und im neuen nicht (oder
  umgekehrt), kann `aresample` andere Koeffizienten verwenden. Das
  beträfe den Projektmodus (`target_rate`-Resampling) und die
  True-Peak-Messung.
- Unterschiedliche SIMD-Optimierungen können Gleitkommaergebnisse
  verschieben.

Deshalb wird nicht geraten, sondern verglichen: derselbe Korpus durch
altes und neues Binary, Ergebnisse gegeneinander.

## Tests

- **Formatmatrix:** jede zugesagte Eingabeform aus dem Goldkorpus
  dekodiert mit dem neuen Binary; `--check` gegen den Goldkorpus meldet
  null Abweichungen. Ein Format, das nicht mehr dekodiert, ist ein
  Blocker, kein Hinweis.
- **Fehlerfälle:** beschädigte Datei, abgeschnittene Datei, Datei mit
  falscher Endung, leere Datei, Datei mit sehr langer Header-Dauer. Die
  benutzersichtbaren Fehlermeldungen müssen dieselben bleiben — sie
  hängen an ffmpegs Exit-Code und stderr.
- **Direktvergleich alt gegen neu:** beide Binaries über denselben
  Korpus, `ebur128`-Summary Zeichen für Zeichen verglichen. Abweichungen
  in der letzten Nachkommastelle sind zu melden, nicht wegzurunden.
- **Timeout- und Abbruchverhalten** unverändert (`ffmpeg_runner.py`
  bindet den Prozess für die Abbruchlogik).
- `tests/test_packaging.py` erweitern: das gebündelte Binary startet,
  meldet die erwartete Version und kennt die benötigten Filter
  (`ffmpeg -filters` nach `ebur128` durchsuchen).
- Volle Suite grün.

## Fertig-Kriterien und Abschluss

1. Alle Tests grün, Goldkorpus-`--check` ohne Abweichung (Exit-Code 0,
   Zahlen im Abschlussbericht nennen).
2. Gemessene Bundle-Größe vorher/nachher aus einem echten Testbuild.
   Ohne Testbuild gilt die Einsparung als ungeprüft.
3. Die Konfigurationsmatrix, die ffmpeg-Version und die Lizenzlage sind
   im Repo dokumentiert — reproduzierbar, nicht nur im Abschlussbericht.
   Ein Build, den niemand nachbauen kann, ist ein Risiko, kein Gewinn.
4. Unabhängiger Review-Subagent (PASS/FAIL) vor dem Commit. Der Review
   prüft besonders die Formatabdeckung und die Lizenzliste.
5. Ein thematisch geschlossener Commit mit Warum-Begründung.
6. Status in `docs/bundle-laufzeit-uebersicht.md` auf erledigt setzen
   (mit Commit-Hash) und mit committen.

## Rückfallebene

Bleibt der Minimalbuild zu riskant — etwa weil sich die Messwerte
verschieben oder die Lizenzlage unklar ist — ist der Abbruch die
richtige Entscheidung, nicht das Nachjustieren von Toleranzen. Dann
bleibt der Vollausbau, und der Gewinn dieser Phase entfällt. Das ist
ein akzeptables Ergebnis und im Abschlussbericht so zu benennen.
