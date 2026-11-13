# Shared settings of the Tabrion Studio QA kit (macOS and Linux). Sourced by the four scripts; not run directly.
# Everything the kit installs or writes stays inside this folder.

KIT_ROOT="$(pwd -P)"  # every script cds to its own folder before sourcing this file
KIT="$KIT_ROOT/_kit"
ENV_DIR="$KIT/env"
STATE_DIR="$KIT/studio-daten"
PROJECT="$KIT_ROOT/Beispielprojekt"
STUDIO="$ENV_DIR/bin/tabrion-studio"

# Settings of a uv the user may have configured elsewhere must not redirect the kit.
unset UV_INDEX UV_INDEX_URL UV_DEFAULT_INDEX UV_EXTRA_INDEX_URL UV_FIND_LINKS UV_PYTHON_INSTALL_MIRROR \
  UV_PYPY_INSTALL_MIRROR UV_PYTHON_DOWNLOADS_JSON_URL UV_PYTHON_DOWNLOADS UV_OFFLINE UV_CONSTRAINT UV_OVERRIDE \
  UV_PYTHON UV_INSECURE_HOST PIP_INDEX_URL PIP_EXTRA_INDEX_URL 2>/dev/null
export UV_CACHE_DIR="$KIT/cache"
export UV_PYTHON_INSTALL_DIR="$KIT/python"
export UV_NO_CONFIG=1
export UV_NO_PROGRESS=1
export UV_PYTHON_PREFERENCE="${UV_PYTHON_PREFERENCE:-only-managed}"

case "$(uname -s)-$(uname -m)" in
  Darwin-arm64) UV="$KIT/bin/macos-arm64/uv" ;;
  Darwin-x86_64) UV="$KIT/bin/macos-x64/uv" ;;
  Linux-x86_64) UV="$KIT/bin/linux-x64/uv" ;;
  *) UV="" ;;
esac

pause() {
  echo
  printf 'Zum Schliessen die Eingabetaste druecken ... '
  read -r _ || true
}

fail() {
  echo
  echo "FEHLER: $1"
  pause
  exit 1
}

# Linux file managers may run a script without a terminal ("Run as program"); then reopen it in one, so that the
# tester sees what happens and can answer prompts.
ensure_terminal() {
  [ -t 0 ] && return 0
  [ "$(uname -s)" = "Linux" ] || return 0
  [ -n "${QA_KIT_NO_TERMINAL:-}" ] && return 0
  [ -n "${QA_KIT_REOPENED:-}" ] && return 0
  export QA_KIT_REOPENED=1
  for term in x-terminal-emulator gnome-terminal konsole xfce4-terminal mate-terminal lxterminal xterm; do
    command -v "$term" >/dev/null 2>&1 || continue
    case "$term" in
      gnome-terminal) exec "$term" -- bash "$1" ;;
      *) exec "$term" -e bash "$1" ;;
    esac
  done
  return 0
}
