# FlameSecondEar / V3 — Übernahme-Notiz aus NonvisualAudio 2.2

Diese Notiz beschreibt, welche Produktsemantik die 2.2-Änderung
„Frequenzbalance-Rework" einführt und was davon FlameSecondEar/V3
übernehmen soll. Sie ändert den bestehenden FlameSecondEar-Plan
nicht — sie ist Input für dessen nächste Überarbeitung.

Grundsatz: V3 übernimmt die Produktsemantik und Nutzererwartung,
nicht den Python-Code. Der Rust-Kern liefert Messdaten; die
materialtypische Interpretation und alle Reporttexte bleiben im
Reporting-Layer.

## 1. Neue Reporting-Semantik in 2.2

Drei Material-Modi steuern die Interpretation der Frequenzbalance.
Der Faktenblock (lautestes Band, Bandabstände, Gesamtspanne,
Resonanzliste) ist in allen Modi identisch; nur die Deutung wechselt.

- `music` (Default für direkte Aufrufe): das historische Verhalten,
  inklusive „klar X-lastige Klangbalance"-Urteil und der
  Musik-Band-Empfehlungen (air_boost, sub_absent, sub_dominant).
- `neutral` (kein Profil gewählt): keine Material-Annahme. Vorsichtige
  Zusatzsätze nur aus robusten Befunden („Der Subbass-Anteil ist sehr
  niedrig.", „Die Energie konzentriert sich stark im X-Bereich.",
  abgeschlossen mit „Das kann je nach Material gewollt sein.").
  Kein dramatisches Gesamturteil, kein Profil-Hinweis, keine
  musik- oder sprachspezifischen Empfehlungen. Empfehlungen nur aus
  robusten Befunden (True Peak, schmale Resonanzen, Stereo-Probleme).
- `speech` (ein Profil mit `material: "speech"` gewählt): sprachbezogene
  Banddeutung relativ zum Mitten-Anker (500 Hz–2 kHz). Wenig Subbass
  ist bewusst KEIN Befund. Geprüft werden: Subbass hoch (Rumpeln/
  Griffgeräusche/Plosive), 80–150 Hz (dröhnend vs. dünn), 150–250 Hz
  (Wärme/Fülle, nur Extreme), 250–500 Hz (Mumpf/Boxiness), Präsenz
  (Verständlichkeit vs. Härte), 6–10 kHz (Sibilanz), 10–20 kHz
  (Offenheit, extra-gehedged wegen Mikrofon-Varianz).
- Breitband und schmale Resonanz werden getrennt erklärt; ist ein Band
  insgesamt zurückgenommen UND enthält einen erkannten Peak, gibt es
  EINEN kombinierten Satz („Der X-Bereich ist insgesamt zurückhaltend,
  enthält aber eine schmale Auffälligkeit bei Y.").
- Ableitung des Modus: pure Funktion über die gewählten Profil-Keys —
  keine aufgelösten Profile → neutral; irgendein Profil mit
  `material == "speech"` → speech (Material-Deklaration gewinnt auch
  bei Mischauswahl); sonst music. Die UI-Schicht übergibt den Modus
  IMMER explizit; der Builder-Default `music` existiert nur für
  Rückwärtskompatibilität direkter Aufrufe.
- Mischauswahl: speech-Modus für den Hauptreport, aber jedes gewählte
  Musik-Genre behält seinen vollen Vergleichsabschnitt.
- Profile können ohne Lautheitsziel existieren (`target_lufs`,
  `lra_low`, `lra_high` explizit null). Der Genre-Vergleichsabschnitt
  ersetzt LUFS/LRA-Sätze dann durch einen neutralen Satz („Für
  Rohaufnahmen gibt es kein festes Lautheitsziel...") und behält
  Überschrift + Notes (Screenreader-Navigation bleibt stabil).
- Neues mitgeliefertes Profil: `speech_raw_recording`, Kategorie
  `speech` („Sprache"/„Speech"), DE „Rohe Sprachaufnahme",
  EN „Raw speech recording", `material: "speech"`, alle Ziele null.

## 2. Mess-Kontrakt: Was der Rust-Kern liefern muss

- Sechs öffentliche Bandenergien: sub 20–80, bass 80–250, low_mid
  250–500, mid 500–2k, presence 2–6k, air 6–20k Hz.
- Vier interne Subbandenergien: bass_low 80–150, bass_high 150–250,
  air_low 6–10k, air_high 10–20k Hz. Subbänder sind Messwerte zweiter
  Klasse: nie eigene Report-Bänder, nur Interpretations-Input.
- Einheit: dB relativ zur Gesamtenergie 20 Hz–Nyquist (PSD-Integration
  per Trapezregel über Welch-PSD, Hann, nperseg 4096, 50 % Overlap).
  Werte auf 2 Nachkommastellen gerundet; Stille-Sentinel −120 dB.
- Peak-Liste: (Frequenz, Prominenz in dB über lokaler
  Log-Frequenz-Median-Referenz), Erkennungsbereich 40–8000 Hz,
  Prominenzschwelle 4 dB, 1/3-Oktave-Unterdrückung, max. 6 Peaks.
- Streaming- und Batch-Pfad müssen identische Werte liefern (in 2.x
  per geteiltem Post-Processing `_spectrum_from_psd` strukturell
  garantiert — V3 sollte dieselbe Ein-Pfad-Architektur wählen).
- Fehlende Subbänder müssen als „nicht gemessen" darstellbar sein
  (2.x: Option/None) — der Reporting-Layer überspringt die
  betroffenen Befunde dann.

## 3. Was im Reporting-Layer bleibt (nicht im Rust-Kern)

- Alle Schwellwerte der neutral- und speech-Deutung (in 2.x benannte
  Konstanten in `reporting/builder.py`, alle relativ zum Mittenband):
  SPEECH_SUB_HIGH_REL_DB −10, SPEECH_BASS_LOW_HIGH_REL_DB −6,
  SPEECH_BASS_LOW_THIN_REL_DB −25, SPEECH_WARMTH_HIGH_REL_DB −4,
  SPEECH_WARMTH_LOW_REL_DB −25, SPEECH_MUD_NEAR_MID_DB 2,
  SPEECH_MUD_OVER_PRESENCE_DB 3, SPEECH_PRESENCE_LOW_REL_DB −10,
  SPEECH_PRESENCE_HIGH_REL_DB −3, SPEECH_SIBILANCE_REL_DB −8,
  SPEECH_AIR_LOW_REL_DB −32; NEUTRAL_SUB_LOW_DB −25;
  ENERGY_CONCENTRATION_SPREAD_DB 8. Erstschätzungen — vor Übernahme
  den dann aktuellen 2.x-Stand prüfen.
- Die Material-Modus-Ableitung aus der Profilauswahl.
- Alle Reporttexte und die i18n-Kataloge (DE/EN), inklusive der
  gehedgten Tonalität („wirkt", „kann", „prüfen", „A/B-vergleichen"),
  kurzer Sätze und des Verbots von Markdown-Symbolen im Report.
- Die Zusammenführung „zurückgenommenes Band + Peak im Band" zu einem
  kombinierten Satz.
- Die Profil-Datenhaltung (genres.json-Schema mit optionalen Zielen
  und `material`-Feld, User-Override-Merge, Editor-Passthrough
  unbekannter Felder).

## 4. Golden-/Regressionstests zum Übernehmen

- Streamer/Batch-Parität aller 10 Bandwerte (Toleranz 0,01 dB) über
  verschiedene Chunk-Größen (`tests/test_spectrum_streamer.py`).
- Subband-Zuordnung: 100-Hz-Sinus → bass_low; 8-kHz-Sinus → air_low;
  12-kHz-Sinus → air_high; Subband nie lauter als Elternband
  (`tests/test_spectrum.py`).
- Modus-Verhalten (`tests/test_builder.py`): neutral behält Fakten,
  kein „X-lastig"-Urteil, kein Profil-/Genre-Wort im Report, Musik-
  Band-Recs unterdrückt, robuste Recs (True Peak, Peak-Fix) bleiben;
  speech kennzeichnet die Sprach-Einordnung, kein Subbass-Mangel-
  Befund, Mumpf/Sibilanz/Offenheit-Befunde, kombinierter Satz bei
  zurückgenommener Präsenz + 2,6-kHz-Peak, toleriert fehlende
  Subbänder; music bleibt mit und ohne material-Parameter identisch.
- Vergleich ohne Lautheitsziel: keine LUFS/LRA-Sätze, neutraler
  Kein-Ziel-Satz, Notes bleiben (`tests/test_comparison.py`).
- Profil-Laden: explizites null gültig, fehlender Key malformed,
  halbes LRA-Paar malformed, `material`-Default music, Editor-
  Roundtrip erhält material + nulls (`tests/test_genre_profiles.py`).
- Projekt-Modus reicht den Material-Modus in die inneren Sektionen
  durch (`tests/test_project_report.py`).

## 5. UI-Begriffe zum Übernehmen

- Button/Dialog: DE „Genre / Profil wählen…", EN „Choose Genre /
  Profile…"; Dialogtitel DE „Genre / Profil wählen", EN „Choose
  genre / profile".
- Labels: DE „Ausgewählte Genres / Profile", EN „Selected genres /
  profiles"; Accessible Name DE „Genre / Profil", EN „Genre / Profile".
- Profilname: DE „Rohe Sprachaufnahme", EN „Raw speech recording";
  Kategorie DE „Sprache", EN „Speech".
- Keine Profilpflicht; Analyse ohne Auswahl bleibt erstklassiger
  Anwendungsfall ohne Hinweis-Nudging im Report.

## 6. Was NICHT aus der Python-Implementierung übernommen wird

- Der Batch-Pfad, der die ganze Datei als float64-Puffer
  materialisiert (scipy.signal.welch über den Vollpuffer) — V3 ist
  von Anfang an streaming-first.
- Die Dict-Merge-Eigenheiten des User-Override-Systems (Key-Ersetzen
  auf Listenebene, Legacy-String-Felder neben {en,de}-Dicts) — V3
  kann ein sauberes, versioniertes Schema definieren, solange die
  Semantik (Override ersetzt Built-in pro Key, unbekannte Felder
  überleben) erhalten bleibt.
- wx-spezifische A11y-Workarounds (Label-über-Feld-Layout,
  MoveBeforeInTabOrder, CallAfter-Fokus) — die zugrunde liegenden
  A11y-Anforderungen gelten, die Mechanik ist Toolkit-Sache.
- Die konkreten Python-Datenklassen/Modulgrenzen (BandEnergies-Felder
  mit None-Defaults usw.) — nur der Mess-Kontrakt aus Abschnitt 2
  ist verbindlich.
