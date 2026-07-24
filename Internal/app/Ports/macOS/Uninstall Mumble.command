#!/bin/bash
# Remove Mumble from macOS while preserving user data/transcripts.
DEST="$HOME/Library/Application Support/Mumble"
APP="$DEST/app"
MAC_APP="$HOME/Applications/Mumble.app"
PID_FILE="$DEST/mumble.pid"
PLIST="$HOME/Library/LaunchAgents/com.mumble.app.plist"
LEGACY_PLIST="$HOME/Library/LaunchAgents/com.mumble.voice.plist"
DOMAIN="gui/$(id -u)"

launchctl bootout "$DOMAIN/com.mumble.app" 2>/dev/null || \
  launchctl unload "$PLIST" 2>/dev/null || true
launchctl bootout "$DOMAIN/com.mumble.voice" 2>/dev/null || \
  launchctl unload "$LEGACY_PLIST" 2>/dev/null || true
rm -f "$PLIST" "$LEGACY_PLIST"

is_mumble_pid() {
  PID="$1"
  case "$PID" in ''|*[!0-9]*) return 1 ;; esac
  kill -0 "$PID" 2>/dev/null || return 1
  CMD=$(ps -p "$PID" -o command= 2>/dev/null || true)
  case "$CMD" in
    *"$APP/mumble_mac.py"*|*"$MAC_APP/Contents/MacOS/MumbleLauncher"*) return 0 ;;
    *) return 1 ;;
  esac
}

stop_mumble_pid() {
  PID="$1"
  is_mumble_pid "$PID" || return 1
  echo "  Stopping Mumble (PID $PID)..."
  kill "$PID" 2>/dev/null || true
  COUNT=0
  while kill -0 "$PID" 2>/dev/null && [ "$COUNT" -lt 32 ]; do
    sleep 0.25
    COUNT=$((COUNT + 1))
  done
  if kill -0 "$PID" 2>/dev/null; then
    if is_mumble_pid "$PID"; then
      kill -9 "$PID" 2>/dev/null || true
    else
      echo "  PID $PID changed owners; refusing to force-stop it."
      return 1
    fi
  fi
  return 0
}

if [ -f "$PID_FILE" ]; then
  PID=$(cat "$PID_FILE" 2>/dev/null || true)
  if is_mumble_pid "$PID"; then
    stop_mumble_pid "$PID" || true
  elif [ -n "$PID" ]; then
    echo "  Ignoring stale PID file (PID $PID is not Mumble)."
  fi
  rm -f "$PID_FILE"
fi

# Safe fallback for an old build that predates the PID file. The port narrows
# discovery; the command line still has to match Mumble before we signal it.
LOCK_PIDS=$(lsof -nP -tiTCP:49517 -sTCP:LISTEN 2>/dev/null || true)
for PID in $LOCK_PIDS; do
  if is_mumble_pid "$PID"; then
    stop_mumble_pid "$PID" || true
  else
    echo "  Leaving unrelated PID $PID on port 49517 untouched."
  fi
done

if [ "$APP" = "$HOME/Library/Application Support/Mumble/app" ]; then
  rm -rf "$APP"
fi
if [ "$MAC_APP" = "$HOME/Applications/Mumble.app" ]; then
  rm -rf "$MAC_APP"
fi
echo ""
echo "  Mumble has been stopped and uninstalled."
echo "  Your transcripts are still in:  ~/Library/Application Support/Mumble"
echo "  Delete that folder to remove everything."
echo ""
read -n 1 -s -r -p "  Press any key to close."
echo ""
