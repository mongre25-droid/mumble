#!/bin/bash

# Prefer the installed application bundle created by Install Mumble.command.
# This keeps permissions, bundle identity, and the private venv on one path even
# after the downloaded release folder has been moved or deleted.
MAC_APP="$HOME/Applications/Mumble.app"
if [ -x "$MAC_APP/Contents/MacOS/MumbleLauncher" ]; then
  open "$MAC_APP"
  exit $?
fi

# Developer/source-checkout fallback.
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PY="$DIR/app/.venv/bin/python"
if [ ! -x "$PY" ]; then
  echo "Mumble is not installed. Run 'Install Mumble.command' first."
  read -n 1 -s -r -p "Press any key to close."
  echo ""
  exit 1
fi
nohup "$PY" "$DIR/app/mumble_mac.py" > /dev/null 2>&1 &
exit 0
