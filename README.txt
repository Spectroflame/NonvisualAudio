NonvisualAudio — Accessible Audio Analyzer
===========================================

Version 2.0
For macOS on Apple Silicon (M1, M2, M3, M4)


WHAT IT DOES
------------

NonvisualAudio measures your audio files the same way a sighted engineer
would read them off a spectrogram, loudness meter and dynamics analyzer,
and writes the result as a clear text report that any screen reader can
speak out loud. It replaces the pictures that other analyzers produce
with well structured, human readable sentences.

Everything happens locally on your computer. No data leaves the machine.
There is no Internet connection, no cloud upload, no artificial
intelligence talking to a server. The app is fully offline.


INSTALLING
----------

1. Open the disk image.
2. Drag NonvisualAudio.app onto the Applications shortcut.
3. Double click the file named First Launch Helper.command.
   A Terminal window opens briefly, removes the macOS quarantine flag
   that blocks unsigned apps, and launches NonvisualAudio for the first
   time.
4. From now on you can start the app normally, just like any other
   application — open it from Applications or type its name into
   Spotlight.

The helper is only needed once, because this version of NonvisualAudio
is not yet signed with a paid Apple Developer ID. If you prefer, you
can also clear the flag manually in Terminal with:

    xattr -dr com.apple.quarantine /Applications/NonvisualAudio.app


HOW TO USE
----------

The main window has a handful of controls, arranged top to bottom:

  Add Audio Files       pick one or several audio files
  Clear Files           remove all files from the list
  Selected audio files  read only list of the files you chose
  Choose Genres         open the genre reference dialog
  Choose Reference File pick a second audio file for a direct comparison
  Clear Reference       remove the reference file
  Choose Report Sections   pick which blocks the report should contain
  Selected report sections read only display of the current selection
  Project mode             checkbox: combine all files into one project
  Analyze               run the analysis
  Progress              shows percentage while the analysis is running

A typical flow

  1. Click Add Audio Files and choose one or more files. Supported
     formats are WAV, AIFF, MP3, M4A, AAC, FLAC, OGG and Opus.
  2. Optional: click Choose Genres and tick any number of genre profiles
     you want your files compared against. Pick none to get a standalone
     analysis of your file's absolute measurements.
  3. Optional: click Choose Reference Files and pick one or several
     audio files (or drop a folder onto the reference area) for a
     direct A versus B comparison. A single reference file is compared
     one-to-one; a multi-file reference is combined into a reference
     project so a whole album or audio drama can be A/B'd against the
     target as a whole.
  4. Optional: click Choose Report Sections and tick only the blocks you
     want in the report — for example only loudness, or only frequency
     balance. Default is every section enabled.
  5. Optional: turn on Project mode if your selected files belong to one
     album, audio drama, or other multi-track work. The analyzer then
     measures loudness, dynamics, and frequency balance over the whole
     set as if it were one continuous file, and adds a short Cross-Track
     Consistency block. Project mode resets to off every time the app
     launches; that's intentional, so it cannot silently persist after a
     single-file workflow.
  6. Click Analyze. A steady metronome click plays while the analysis
     runs. The progress bar shows where in the pipeline it is.
  7. The report opens in its own window. The cursor is placed at the
     top so your screen reader can start reading line by line right
     away. Press Control Shift C or the Copy Report button to put the
     full text on your clipboard.
  8. Close the results window to return to the main window. You can
     then run another analysis with different files, genres or a
     different reference, without restarting the app.


WHAT THE NUMBERS MEAN
---------------------

LOUDNESS SUMMARY

  Integrated loudness (LUFS)
    The average perceived loudness of the whole file, measured
    following the EBU R128 and ITU BS.1770 standards. More negative
    means quieter. Typical targets

      minus 23 LUFS   European broadcast, radio play
      minus 16 LUFS   podcast, conversational
      minus 14 LUFS   Spotify, Apple Music, Tidal normalization
      minus 9 LUFS    loud modern commercial pop
      minus 7 LUFS    club oriented electronic, modern trap

  Short term peak loudness
    The loudest three second window measured with the same standard.
    Good for spotting moments that stick out from the overall level.

  True peak dBTP
    The highest inter sample peak, measured in dB relative to full
    scale. Values at or above minus 0.5 dBTP risk clipping on lossy
    playback such as MP3 or streaming.

  Loudness range (LU)
    The spread between the quiet and loud passages, measured in
    loudness units. High LRA means dynamic programme, low LRA means
    heavy compression.

      below 4 LU     very tight, modern club or trap master
      5 to 10 LU     typical pop, podcast, radio drama
      10 to 16 LU    film dialogue, audiobook, jazz ensemble
      above 16 LU    classical orchestra, dynamic ambient


DYNAMICS SUMMARY

  Crest factor (dB)
    Sample peak minus overall RMS. A simple measure of how far the
    peaks stick out above the average level.

      below 6 dB    heavily limited, brick walled
      6 to 10 dB    moderate, pop or broadcast
      10 to 14 dB   open and natural
      above 14 dB   wide, healthy dynamic range

  Dynamic range score
    A simplified DR score inspired by the TT DR measurement. Higher
    numbers mean more dynamic. This is a quick cross check on the
    crest factor, more robust to occasional single peaks.


FREQUENCY BALANCE

  The spectrum of the file is split into six bands and each is rated
  relative to the full spectrum energy

    Sub         below 80 Hz
    Bass        80 to 250 Hz
    Low mid     250 to 500 Hz
    Mid         500 to 2000 Hz
    Presence    2000 to 6000 Hz
    Air         above 6000 Hz

  After the band description the report lists any narrow spectral
  peaks that stick up at least 3.5 dB above their surroundings, with
  their exact frequency in Hz, how far they stand out, and a short
  note on how that frequency is typically perceived (boxiness,
  sibilance, nasal colouration, etc.).


COMPARISON SECTIONS

  If you chose one or more genre references, the report includes a
  Comparison To section for each. It tells you how your file's
  integrated loudness and loudness range compare against the typical
  values for that genre, and gives the genre's tonal character for
  context.

  If you chose a reference file, an extra Comparison To Reference File
  section describes the differences in loudness, dynamics and
  frequency balance between your file and the reference.


WHERE THE GENRE TARGETS COME FROM
---------------------------------

The target loudness and loudness range values for each genre come
from established standards and mastering practice

  EBU R128                  European broadcast
  ITU R BS.1770             loudness measurement specification
  ATSC A 85                 US broadcast
  Audible ACX               audiobook delivery requirements
  Streaming platform targets Spotify, Apple Music, Tidal, YouTube
  Mastering engineer practice for genres without a published spec

The full list of profiles with their values lives in the source file
genre_profiles.py inside the app bundle. These are typical values,
not hard rules — two songs in the same genre can legitimately land a
few loudness units apart.


PRIVACY
-------

NonvisualAudio does not connect to the Internet at any point. The
source code contains no network libraries. No telemetry. No crash
reporting. No preferences file. Nothing is written to your disk other
than a short audio click played during analysis, and that click is
generated in memory and never touches the file system in the released
version. When you quit the app it leaves no trace behind.


WHAT IT DOES NOT DO
-------------------

  It does not normalize, compress, limit or otherwise modify your
  files. It only reads them.

  It does not give you visual plots, spectrograms, waveform pictures
  or loudness meters. The whole point is that the useful information
  is in the words of the report.

  It does not transcribe speech, detect music, identify instruments,
  classify genre automatically, or do anything that would require
  machine learning. All analysis is plain signal processing and rule
  based text generation. The same input always produces the same
  output.


SYSTEM REQUIREMENTS
-------------------

  macOS 11 (Big Sur) or newer
  Apple Silicon Mac (M1 through M4)
  About 300 megabytes of free disk space

Intel Macs are not supported in this release. Windows and Linux
builds exist as a plan but have not been produced yet.


FEEDBACK AND HELP
-----------------

Please report anything that is unclear, broken, reads oddly with your
screen reader, or is missing. Suggestions for new genre profiles, new
measurements, or new features are very welcome.

The fastest way to file a bug report or feature request is the project's
issue tracker on GitHub. The app's About dialog (F1, or Help → About
NonvisualAudio) has a "Report a Bug" button that opens the issue page
directly in your default browser; the same dialog also has a "Show
README" button that re-opens this file.

  Issue tracker:  https://github.com/Spectroflame/NonvisualAudio/issues
  Project page:   https://github.com/Spectroflame/NonvisualAudio
