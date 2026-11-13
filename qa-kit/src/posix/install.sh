#!/bin/bash
# Tabrion Studio QA kit: install everything into this folder (needs internet once).
cd "$(dirname "$0")" || exit 1
if [ ! -f "./_kit/common.sh" ]; then
  echo "FEHLER: Der Ordner _kit fehlt. Bitte das Testpaket neu entpacken."
  exit 1
fi
. "./_kit/common.sh"
ensure_terminal "$KIT_ROOT/$(basename "$0")"
set -u

echo "Tabrion Studio (lokale Testversion) wird installiert."
echo "Alles bleibt in diesem Ordner: $KIT_ROOT"
echo

[ -n "$UV" ] && [ -f "$UV" ] || fail "Dieses System wird vom Testpaket nicht unterstuetzt ($(uname -s) $(uname -m))."

if [ "$(uname -s)" = "Darwin" ]; then
  # Files unpacked from a download carry the quarantine flag; the kit's own files are cleared so that
  # the other scripts and the bundled tools start without a further prompt.
  xattr -dr com.apple.quarantine "$KIT_ROOT" 2>/dev/null || true
fi
chmod +x "$UV" "$KIT_ROOT"/*.command "$KIT_ROOT"/*.sh 2>/dev/null || true

"$STUDIO" --stop "$PROJECT" --state-dir "$STATE_DIR" >/dev/null 2>&1 || true

echo "[1/4] Python 3.13 bereitstellen ..."
"$UV" venv --clear --python 3.13 "$ENV_DIR" || fail "Python konnte nicht bereitgestellt werden. Besteht eine Internetverbindung?"

echo "[2/4] Bibliotheken laden und pruefen (feste Versionen mit Pruefsummen) ..."
"$UV" pip install --python "$ENV_DIR/bin/python" --require-hashes --no-deps --only-binary :all: \
  -r "$KIT/runtime-lock.txt" || fail "Die Bibliotheken konnten nicht installiert werden."

echo "[3/4] Tabrion Studio installieren ..."
"$UV" pip install --python "$ENV_DIR/bin/python" --no-deps "$KIT"/wheels/*.whl \
  || fail "Tabrion Studio konnte nicht installiert werden."

echo "[4/4] Installation pruefen ..."
"$UV" pip check --python "$ENV_DIR/bin/python" || fail "Die Pruefung der Installation ist fehlgeschlagen."
VERSION="$("$STUDIO" --version)" || fail "Tabrion Studio startet nicht."

echo
echo "Fertig: $VERSION"
echo "Weiter mit \"2 - Studio starten\"."
pause
