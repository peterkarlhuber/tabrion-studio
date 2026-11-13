#!/bin/bash
# Tabrion Studio QA kit: remove everything the kit installed or wrote (Python, libraries, Studio's settings,
# trust records and logs). The example project and the scripts stay until you delete the folder.
cd "$(dirname "$0")" || exit 1
if [ ! -f "./_kit/common.sh" ]; then
  echo "FEHLER: Der Ordner _kit fehlt. Bitte das Testpaket neu entpacken."
  exit 1
fi
. "./_kit/common.sh"
ensure_terminal "$KIT_ROOT/$(basename "$0")"

echo "Deinstallieren entfernt Python, die Bibliotheken und alle Einstellungen und Protokolle"
echo "von Tabrion Studio aus diesem Ordner: $KIT_ROOT"
printf 'Fortfahren? (j/n) '
read -r ANSWER || ANSWER=n
case "$ANSWER" in
  j|J|ja|Ja|JA|y|Y|yes) ;;
  *) echo "Abgebrochen, nichts wurde entfernt."; pause; exit 0 ;;
esac

if [ -x "$STUDIO" ]; then
  "$STUDIO" --stop "$PROJECT" --state-dir "$STATE_DIR" >/dev/null 2>&1 || true
fi
case "$KIT" in
  /*/_kit) ;;
  *) fail "Unerwarteter Ordner ($KIT); es wurde nichts entfernt." ;;
esac
[ -f "$KIT/common.sh" ] || fail "Unerwarteter Ordner ($KIT); es wurde nichts entfernt."
rm -rf "$KIT/env" "$KIT/python" "$KIT/cache" "$KIT/studio-daten"
echo
echo "Entfernt. Zum Schluss den Ordner \"$(basename "$KIT_ROOT")\" einfach in den Papierkorb ziehen."
echo "Ausserhalb dieses Ordners hat das Testpaket nichts installiert."
pause
