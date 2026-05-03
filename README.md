# NonvisualAudio

An accessible audio analyzer for blind and low-vision audio professionals.

NonvisualAudio reports the same measurements a sighted engineer reads off a
visual analyzer — loudness, dynamics, and frequency balance — as a cleanly
structured plain-text report that works with VoiceOver (macOS), NVDA/JAWS
(Windows), and Orca (Linux).

**Everything runs locally.** No cloud, no LLM, no network calls — the source
tree contains zero network libraries.

## Features

- **Input formats**: WAV, AIFF, MP3, M4A/AAC, OGG, FLAC, Opus, WMA
- **Batch analysis**: analyze several files in one pass, each gets its own
  section in the final report
- **Project mode** for albums and audio dramas: turn the toggle on and
  every selected file is treated as one continuous piece. Loudness,
  dynamics, and frequency balance are measured over the whole set as if
  it were a single bounced file (loudness via ffmpeg's `concat` filter
  feeding `ebur128`, dynamics and spectrum via numpy concatenation), and
  a Cross-Track Consistency block flags how much per-track integrated
  loudness varies and which track is loudest / quietest. The mode
  resets to off on every launch so it cannot silently persist
- **Splittable report**: a Choose Report Sections dialog lets the user
  enable only the blocks they want — only loudness, only frequency
  balance, only dynamics, etc. The selection is remembered between
  launches; the default is every section enabled
- **42 genre references** across 13 categories (Audio Drama, Podcast,
  Spoken Word, Pop, Rock, Rap & Hip Hop, R&B & Soul, Reggae, Jazz, Folk &
  Country, Electronic, Classical, Film & Cinema) — pick any number of them
  per analysis
- **Reference comparison**: supply a second audio file — or several files /
  a folder — as the reference. A single file is compared one-to-one; a
  multi-file reference is combined into a "reference project" with the
  same pipeline as the target, so a freshly mastered album can be A/B'd
  against a previously released album as a whole instead of track by
  track. Works independently of, and on top of, genre references
- **Measurements**:
  - Integrated LUFS (EBU R128 / ITU BS.1770 via ffmpeg's `ebur128` filter)
  - Short-term peak LUFS, true peak dBTP, loudness range (LRA)
  - Crest factor, simplified DR score, compression assessment
  - Energy per frequency band (sub, bass, low-mid, mid, presence, upper
    highs / "air")
  - Narrow spectral peaks called out in exact Hz with a character note
    ("boxiness", "sibilance", "nasal or telephone-like character", etc.)
- **Screen-reader-first UI**:
  - Built on wxPython so every widget is a native control on its
    platform (Cocoa on macOS, Win32 on Windows, GTK on Linux) and goes
    straight through the platform's native accessibility bridge
  - Modal results window focused on a read-only text control, where
    the screen reader starts reading the report immediately
  - Checkbox-based genre picker (the most reliably announced control
    across VoiceOver, NVDA and Orca)
  - Friendly error dialog with a clear headline, an explanation, and a
    concrete next step — batch failures continue to process the other
    files and summarise the problems in the report itself
  - Metronome-style click plays during analysis so the user has audio
    feedback that work is progressing (nothing is written to disk; the
    sample is held in memory and fed straight to PortAudio via
    sounddevice)
  - Progress bar reports the current pipeline step
  - All numbers in the report are spelled out ("minus 21.4 LUFS" rather
    than "-21.4") so screen readers speak them naturally
  - No Markdown symbols in output: just ALL CAPS section headings and
    plain sentences

## Example report

```
FILE INFO
Filename: interview raw.wav
Duration: 12 minutes 34 seconds
Sample rate: 48000 Hz
Channels: stereo

LOUDNESS SUMMARY
Integrated loudness: minus 21.4 LUFS.
Short term peak loudness: minus 14.2 LUFS.
True peak: minus 1.1 dBTP.
Loudness range: 8.7 LU.
The file sits at a moderate loudness level typical of broadcast material.

DYNAMICS SUMMARY
Crest factor: 13.2 dB.
Dynamic range score: 11.
Dynamics are open and natural.

FREQUENCY BALANCE
The low end below 80 Hz is restrained.
The bass region (80 to 250 Hz) is present and balanced.
The midrange is noticeably forward compared to the bass.
The air above 6 kHz is restrained.

Detected 2 prominent spectral peaks:
Peak 1: 480 Hz (low midrange), about 4.2 dB above the surrounding spectrum.
  Typically perceived as boxiness or a honky, boxy character.
Peak 2: 3200 Hz (presence), about 5.1 dB above the surrounding spectrum.
  Typically perceived as bite, edge, or harshness on vocals.

RECOMMENDATIONS
Consider a gentle cut of about 2 dB around 480 Hz with a wide Q to open up
the midrange.
```

## Requirements

- Python 3.11 or newer
- Apple Silicon for the prebuilt macOS bundle; any platform for running
  from source

## Running from source

```bash
git clone https://github.com/Spectroflame/NonvisualAudio.git
cd NonvisualAudio

python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

### ffmpeg binary

NonvisualAudio bundles its own ffmpeg so the packaged app is self-contained
on end users' machines. The binary is **not checked into git** because it
is a ~77 MB platform-specific blob. For a packaged build, place a static
ffmpeg in the resources folder:

```
src/nonvisualaudio/resources/bin/
└── darwin/ffmpeg          # static macOS build, e.g. from evermeet.cx
```

For quick iteration during development the app falls back to any `ffmpeg`
on `$PATH`, so a Homebrew-installed ffmpeg works fine too.

### Run

```bash
python -m nonvisualaudio

# verbose logging on stderr:
NVA_DEBUG=1 python -m nonvisualaudio
```

## Tests

```bash
pytest
```

108 unit tests cover templates, report builder, splittable-report
section selection, project-mode aggregation and rendering,
genre/reference comparison, dynamics, spectrum analysis, drop/paste,
themes, and localisation.

## Building a standalone macOS app

```bash
# one-time: download a static ffmpeg into the resources folder
curl -sL https://evermeet.cx/ffmpeg/getrelease/zip -o /tmp/ffmpeg.zip
unzip -o /tmp/ffmpeg.zip -d src/nonvisualaudio/resources/bin/darwin/
chmod +x src/nonvisualaudio/resources/bin/darwin/ffmpeg

# build the .app
pip install pyinstaller
pyinstaller --clean --noconfirm NonvisualAudio.spec

# ad-hoc deep sign (required on Apple Silicon)
codesign --force --deep --sign - dist/NonvisualAudio.app
```

The resulting `dist/NonvisualAudio.app` is self-contained and can be
redistributed. For a friendlier distribution, package it into a DMG
together with a `First Launch Helper.command` script that strips
`com.apple.quarantine` on the recipient's Mac.

## Pre-built downloads

Every release tag on GitHub triggers
[a multi-platform build](.github/workflows/build.yml) that publishes
self-contained archives under the matching GitHub Release:

- `NonvisualAudio-macOS-arm64.zip` — Apple Silicon Macs
- `NonvisualAudio-Windows-x64.zip` — Windows 10 and newer
- `NonvisualAudio-Linux-x64.tar.gz` — Linux (glibc-based)

The same workflow can be triggered manually from the Actions tab on
GitHub to produce a development snapshot without cutting a release.

On macOS the distributed app is ad-hoc signed; users need to clear
the quarantine attribute on first launch, for example with
`xattr -cr /Applications/NonvisualAudio.app`.

## Architecture

```
src/nonvisualaudio/
├── app.py                 # wxPython application entry point
├── errors.py              # UserFacingError — structured, friendly errors
├── audio/                 # Decoding (soundfile first, ffmpeg fallback)
├── analysis/              # Loudness, dynamics, spectrum — returns dataclasses
├── reporting/             # Templates, report builder, genre profiles,
│                          # comparison sections
├── ui/                    # wxPython widgets, background worker, click
│                          # sound, results / genre / error dialogs
└── resources/bin/darwin/  # Bundled ffmpeg (gitignored)
```

The four layers are strictly top-to-bottom:
`audio → analysis → reporting → ui`. Tests live next to the code in
`tests/`.

## Privacy

- No `urllib`, `requests`, `httpx`, `socket`, or any other network module
  is imported anywhere in the source. Verifiable by `grep`.
- Logging is disabled by default (`WARNING` level) and goes to stderr
  only. Set `NVA_DEBUG=1` to enable DEBUG output during development.
- No preferences file, no cache directory, no telemetry. The app leaves
  no trace on disk after quitting.

## License

MIT.

## Acknowledgements

- ffmpeg's `ebur128` filter for the reference EBU R128 loudness
  implementation
- wxPython for the cross-platform accessibility bridge — native
  controls on every platform, which gives NSAccessibility on macOS,
  UIA on Windows, and AT-SPI on Linux without any extra glue
- sounddevice / PortAudio for in-memory click playback
- scipy.signal, numpy, soundfile
