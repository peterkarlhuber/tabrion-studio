#!/bin/bash
# Tabrion Studio QA kit: stop the running Studio of the example project.
cd "$(dirname "$0")" || exit 1
if [ ! -f "./_kit/common.sh" ]; then
  echo "FEHLER: Der Ordner _kit fehlt. Bitte das Testpaket neu entpacken."
  exit 1
fi
. "./_kit/common.sh"
ensure_terminal "$KIT_ROOT/$(basename "$0")"

if [ -x "$STUDIO" ]; then
  "$STUDIO" --stop "$PROJECT" --state-dir "$STATE_DIR" || echo "Es lief kein Tabrion Studio."
else
  echo "Tabrion Studio ist nicht installiert."
fi
pause
