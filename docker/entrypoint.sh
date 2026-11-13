#!/bin/sh
# Programmordner /opt/usv-monitor ist ein Volume: Er hält Einstellungen (config.json, *.yaml) und Daten (data/, models/).
# Beim Start ersetzt der Code des Images den Code der vorigen Version; Laufzeitdateien bleiben unberührt.
set -e
APP=/opt/usv-monitor
MANIFEST="$APP/.image-files"
mkdir -p "$APP"

if [ -f "$MANIFEST" ]; then
    while IFS= read -r entry; do
        case "$entry" in
            ""|.|..|*/*|data|models|.venv) continue ;;
        esac
        rm -rf "$APP/$entry"
    done < "$MANIFEST"
fi

cd /opt/src
find . -mindepth 1 -maxdepth 1 -printf '%P\n' > "$MANIFEST"
cp -a . "$APP/"

cd "$APP"
exec python app.py "$@"
