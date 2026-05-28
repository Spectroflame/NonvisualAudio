#!/usr/bin/env bash
# Erststart-Helfer für NonvisualAudio (macOS).
#
# Die App ist nicht über den App Store signiert/notarisiert. Beim ersten
# Start blockiert macOS sie deshalb ("kann nicht auf Schadsoftware geprüft
# werden"). Dieses Skript entfernt die Quarantäne-Markierung von der App
# und startet sie. Danach lässt sie sich jederzeit normal per Doppelklick
# öffnen.

cd "$(dirname "$0")"
APP="NonvisualAudio.app"

if [ ! -d "$APP" ]; then
    echo "Fehler: $APP wurde nicht in diesem Ordner gefunden."
    echo "Bitte dieses Skript im selben Ordner wie NonvisualAudio.app lassen."
    read -r -p "Mit der Eingabetaste schließen ..." _
    exit 1
fi

echo "Entferne die Quarantäne-Markierung von $APP ..."
xattr -dr com.apple.quarantine "$APP" 2>/dev/null || true

echo "Starte NonvisualAudio ..."
open "$APP"

echo "Fertig. Ab jetzt kannst du die App direkt per Doppelklick öffnen."
