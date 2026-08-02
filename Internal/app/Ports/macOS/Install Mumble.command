#!/bin/bash
# Mumble installer for macOS. Sets up Python deps, the speech model, start-at-login,
# and launches the menu-bar app. Safe to re-run.
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="$HERE/app"
DEST="$HOME/Library/Application Support/Mumble"
APP="$DEST/app"

echo ""
echo "  Installing Mumble  -  private voice-to-text"
echo "  ==========================================="
echo ""

# 0. Clear the "downloaded from the internet" quarantine flag on our own files.
# This is your own Mac and you just opened this installer on purpose, so this is
# a normal, allowed cleanup - no Apple account or payment involved. It means the
# menu-bar app and launchers won't re-trigger the "unidentified developer" prompt.
xattr -dr com.apple.quarantine "$HERE" 2>/dev/null || true

# 1. Find a supported Python runtime with its own reviewed dependency profile.
PY=""
for CANDIDATE in python3 python3.13 python3.12 \
    /opt/homebrew/bin/python3.13 /usr/local/bin/python3.13 \
    /opt/homebrew/bin/python3.12 /usr/local/bin/python3.12; do
  FOUND=$(command -v "$CANDIDATE" 2>/dev/null || true)
  if [ -n "$FOUND" ] && "$FOUND" -c 'import sys; raise SystemExit(0 if (3, 12) <= sys.version_info < (3, 14) else 1)' 2>/dev/null; then
    PY="$FOUND"
    break
  fi
done
if [ -z "$PY" ]; then
  echo "  Mumble requires Python 3.12 or 3.13; a compatible Python was not found."
  echo ""
  echo "  Recommended: install Python 3.12 or 3.13 from python.org (its package"
  echo "  includes Tk), then run this installer again."
  echo "  Homebrew alternative: brew install python@3.12 python-tk@3.12"
  open "https://www.python.org/downloads/macos/" 2>/dev/null || true
  read -n 1 -s -r -p "  Press any key to close."
  echo ""; exit 1
fi
PY_MINOR=$("$PY" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
if ! "$PY" -c 'import tkinter; assert tkinter.TkVersion >= 8.6' >/dev/null 2>&1; then
  echo "  Python is new enough, but its tkinter/Tk framework is missing."
  echo "  Install Python from python.org, or for this Homebrew Python run:"
  echo "      brew install python-tk@$PY_MINOR"
  echo "  Then run this installer again."
  read -n 1 -s -r -p "  Press any key to close."
  echo ""; exit 1
fi
echo "  Using Python: $PY ($("$PY" -c 'import platform; print(platform.python_version())'))"

# Process validation for the eventual atomic publish. Never trust a stale PID
# file or assume that anything using Mumble's port belongs to Mumble.
PID_FILE="$HOME/Library/Application Support/Mumble/mumble.pid"
MAC_APP="$HOME/Applications/Mumble.app"

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
  echo "  Stopping running Mumble instance (PID $PID)..."
  kill "$PID" 2>/dev/null || true
  COUNT=0
  while kill -0 "$PID" 2>/dev/null && [ "$COUNT" -lt 32 ]; do
    sleep 0.25
    COUNT=$((COUNT + 1))
  done
  if kill -0 "$PID" 2>/dev/null; then
    # Revalidate immediately before the force kill so PID reuse cannot target an
    # unrelated process.
    if is_mumble_pid "$PID"; then
      kill -9 "$PID" 2>/dev/null || true
    else
      echo "  Refusing to force-stop PID $PID because it is no longer Mumble."
      return 1
    fi
  fi
  return 0
}

# Preserve an explicit disabled toggle across reinstall. Missing/corrupt settings
# retain the first-install default (enabled).
AUTOSTART_WANTED=$("$PY" - "$DEST/settings.json" <<'PY'
import json, os, sys
path = sys.argv[1]
try:
    with open(path, encoding="utf-8") as f:
        enabled = bool(json.load(f).get("autostart", True))
except Exception:
    enabled = True
print("1" if enabled else "0")
PY
)

# 2. Build a fresh staged app tree. Replace rather than overlay: otherwise files
# removed by a newer release remain importable forever. User data lives in DEST,
# outside APP, and the working install stays intact if dependency setup fails.
if [ "$APP" != "$HOME/Library/Application Support/Mumble/app" ]; then
  echo "  Refusing unsafe install destination: $APP"
  exit 1
fi
STAGE="$DEST/app.installing.$$"
rm -rf "$STAGE"
mkdir -p "$STAGE"
trap 'rm -rf "$STAGE"' EXIT
cp -R "$SRC/." "$STAGE/"

# 3. Private environment + dependencies
cd "$STAGE"
echo "  Installing components (a few minutes the first time, needs internet)…"
"$PY" -m venv .venv
"./.venv/bin/python" -m pip install --upgrade pip -q
ARCH=$(uname -m)
PY_TAG=$("$PY" -c 'import sys; print(f"cp{sys.version_info.major}{sys.version_info.minor}")')
case "$ARCH" in
  arm64) DEPENDENCY_LOCK="requirements-lock-macos-arm64-${PY_TAG}.txt" ;;
  x86_64) DEPENDENCY_LOCK="requirements-lock-macos-x86_64-${PY_TAG}.txt" ;;
  *) echo "  Unsupported macOS architecture: $ARCH"; exit 1 ;;
esac
"./.venv/bin/python" -m pip install --require-hashes -r "$DEPENDENCY_LOCK" -q
"./.venv/bin/python" verify_dependency_closure.py "$DEPENDENCY_LOCK"
"./.venv/bin/python" -m pip check -q
if ! "./.venv/bin/python" - <<'PY'
import tkinter
import AppKit
import Quartz
from ApplicationServices import AXIsProcessTrusted
from pynput import keyboard, mouse
assert tkinter.TkVersion >= 8.6
PY
then
  echo "  The macOS UI/input frameworks failed their install check."
  echo "  Confirm Python 3.12 or 3.13 includes Tk, then run this installer again."
  exit 1
fi
VERSION=$("./.venv/bin/python" -c "from branding import VERSION; print(VERSION)" 2>/dev/null || echo "0.9")

# 4. Pre-download the speech model, one time. MUST match the app's default model
# (settings.py -> "small.en"); pre-fetching base.en here just made the app
# re-download small.en on first real use. ~470 MB.
echo "  Downloading the speech model (~470 MB, one time)…"
HF_HUB_DISABLE_XET=1 "./.venv/bin/python" -c "from faster_whisper import WhisperModel; from model_provenance import model_revision; WhisperModel('small.en', revision=model_revision('small.en'), device='cpu', compute_type='int8')" || true

# Stop a verified running copy only after the replacement is fully prepared.
if [ -f "$PID_FILE" ]; then
  OLD_PID=$(cat "$PID_FILE" 2>/dev/null || true)
  if is_mumble_pid "$OLD_PID"; then
    stop_mumble_pid "$OLD_PID" || exit 1
  elif [ -n "$OLD_PID" ]; then
    echo "  Ignoring stale PID file (PID $OLD_PID is not Mumble)."
  fi
  rm -f "$PID_FILE"
fi

# A missing/stale PID file falls back to the LISTENING lock socket, but every
# owning process is still identity-checked before it can receive a signal.
UNRELATED_LOCK=""
LOCK_PIDS=$(lsof -nP -tiTCP:49517 -sTCP:LISTEN 2>/dev/null || true)
for LOCK_PID in $LOCK_PIDS; do
  if is_mumble_pid "$LOCK_PID"; then
    stop_mumble_pid "$LOCK_PID" || exit 1
  else
    echo "  Port 49517 is held by unrelated PID $LOCK_PID; refusing to kill it."
    UNRELATED_LOCK=1
  fi
done
if [ -n "$UNRELATED_LOCK" ]; then
  echo "  Close the application using port 49517, then run this installer again."
  exit 1
fi

# Publish the fully prepared tree only after setup and process checks succeed.
rm -rf "$APP"
mv "$STAGE" "$APP"
trap - EXIT
cd "$APP"

# 5. Create macOS Application Wrapper (Mumble.app)
echo "  Building native Mumble.app in ~/Applications..."
mkdir -p "$MAC_APP/Contents/MacOS"
mkdir -p "$MAC_APP/Contents/Resources"

cat > "$MAC_APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>MumbleLauncher</string>
    <key>CFBundleIconFile</key>
    <string>mumble.icns</string>
    <key>CFBundleIdentifier</key>
    <string>com.mumble.app</string>
    <key>CFBundleName</key>
    <string>Mumble</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleVersion</key>
    <string>$VERSION</string>
    <key>CFBundleShortVersionString</key>
    <string>$VERSION</string>
    <key>LSMinimumSystemVersion</key>
    <string>12.0</string>
    <key>NSMicrophoneUsageDescription</key>
    <string>Mumble records audio only when you dictate or explicitly start Meeting capture, then transcribes it on this Mac.</string>
    <key>NSHighResolutionCapable</key>
    <true/>
    <key>NSSupportsAutomaticGraphicsSwitching</key>
    <true/>
    <key>LSUIElement</key>
    <true/>
</dict>
</plist>
EOF

# The icon is cosmetic - a missing/old icns must never abort the install.
if [ -f "$APP/assets/mumble.icns" ]; then
  cp "$APP/assets/mumble.icns" "$MAC_APP/Contents/Resources/mumble.icns" 2>/dev/null || true
else
  echo "  (note: app icon not found - using the default; everything else is fine)"
fi

cat > "$MAC_APP/Contents/MacOS/MumbleLauncher" <<EOF
#!/bin/bash
# Delegate to the hidden background python daemon
exec "$APP/.venv/bin/python" "$APP/mumble_mac.py"
EOF
chmod +x "$MAC_APP/Contents/MacOS/MumbleLauncher"

# 5b. Make the built app launch cleanly and hold onto its permissions.
#  - Strip quarantine so macOS doesn't prompt when it opens.
#  - Ad-hoc code-sign it ("-" = sign locally, FREE, no Apple Developer account).
#    This is not notarization and does not guarantee TCC permission persistence;
#    a Developer ID signed/notarized release is still needed for distribution.
xattr -dr com.apple.quarantine "$MAC_APP" 2>/dev/null || true
codesign --force --deep --sign - "$MAC_APP" 2>/dev/null || true

# 6. Reconcile the LaunchAgent with the saved setting. First install defaults
# on; an explicit off value survives every reinstall. Use the same Python module
# as the Settings toggle so launchctl state and plist contents cannot diverge.
if [ "$AUTOSTART_WANTED" = "1" ]; then
  if "$APP/.venv/bin/python" -c 'import autostart; raise SystemExit(0 if autostart.enable() else 1)'; then
    echo "  Start at login: enabled"
  else
    echo "  Warning: Mumble installed, but launchd did not enable start at login."
  fi
else
  if "$APP/.venv/bin/python" -c 'import autostart; raise SystemExit(0 if autostart.disable() else 1)'; then
    echo "  Start at login: left disabled (preserved from Settings)"
  else
    echo "  Warning: could not fully remove the disabled LaunchAgent."
  fi
fi

# 7. Launch now via the Application Bundle
open "$MAC_APP"


echo ""
echo "  Done! Mumble is now in your menu bar (top-right of the screen)."
echo ""
echo "  IMPORTANT - grant these permissions when macOS asks, or turn them"
echo "  on in  System Settings > Privacy & Security:"
echo "     - Microphone     (so Mumble can hear you)"
echo "     - Accessibility  (so the hotkey works and it can paste for you)"
echo "     - Input Monitoring, if listed (so global hotkeys are received)"
echo ""
echo "  To use it: click into any text box, press  Control + Option + D,"
echo "  speak, then press it again."
echo ""
read -n 1 -s -r -p "  All set - press any key to close this window."
echo ""
