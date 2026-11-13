#!/bin/bash
# Tabrion Studio QA kit: start Studio for the example project and open the browser.
cd "$(dirname "$0")" || exit 1
if [ ! -f "./_kit/common.sh" ]; then
  echo "FEHLER: Der Ordner _kit fehlt. Bitte das Testpaket neu entpacken."
  exit 1
fi
. "./_kit/common.sh"
ensure_terminal "$KIT_ROOT/$(basename "$0")"

[ -x "$STUDIO" ] || fail "Tabrion Studio ist noch nicht installiert. Bitte zuerst \"1 - Installieren\" ausfuehren."

echo "Tabrion Studio startet und oeffnet den Browser."
echo "Dieses Fenster bitte offen lassen, solange du testest."
echo "Beenden: \"3 - Studio beenden\" oder dieses Fenster schliessen."
echo
"$STUDIO" "$PROJECT" --state-dir "$STATE_DIR"
STATUS=$?
echo
echo "Tabrion Studio wurde beendet (Code $STATUS)."
pause
