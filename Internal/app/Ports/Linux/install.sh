#!/usr/bin/env bash
#
# Mumble — Linux installer.
#
# The 1:1 mirror of the Windows "Install Mumble.bat" / macOS "Install Mumble.command"
# flow, adapted to Linux: detect the package manager, install the system GUI/audio
# libraries that CANNOT come from pip (WebKitGTK, PyGObject, PortAudio), create a
# per-user virtualenv that can SEE those system libraries, pip-install the Python
# deps, pre-fetch the on-device Whisper model, and register a desktop launcher.
#
# Safe to re-run (idempotent). Nothing here needs root except the one system-package
# step, which is run via sudo and clearly announced first.

set -u
RED=$'\e[31m'; GRN=$'\e[32m'; YLW=$'\e[33m'; BLU=$'\e[34m'; BLD=$'\e[1m'; RST=$'\e[0m'
say()  { printf '%s\n' "$*"; }
ok()   { printf '%s✓%s %s\n' "$GRN" "$RST" "$*"; }
warn() { printf '%s!%s %s\n' "$YLW" "$RST" "$*"; }
err()  { printf '%s✗%s %s\n' "$RED" "$RST" "$*" >&2; }
hdr()  { printf '\n%s%s%s\n' "$BLD" "$*" "$RST"; }

# The runtime lock currently includes SciPy 1.18, whose supported interpreter
# range starts at Python 3.12. Fail before creating a venv or invoking pip on an
# older distro Python so the user gets an actionable error instead of a long,
# doomed dependency build.
MIN_PYTHON_MAJOR=3
MIN_PYTHON_MINOR=12

# Per the XDG Base Directory specification, relative XDG paths are invalid and
# must be ignored. Keeping this logic in the installer also prevents a launch
# from an arbitrary working directory from scattering files into that directory.
xdg_home_or_default() {
  case "${1:-}" in
    /*) printf '%s\n' "$1" ;;
    *)  printf '%s\n' "$2" ;;
  esac
}

# Quote one argument for a Desktop Entry Exec= field. Desktop files do not use
# shell parsing: their quoting rules require these four characters to be escaped,
# and a literal percent sign must be written as %% so it is not treated as a
# field code such as %f or %U.
desktop_exec_quote() {
  local value=$1
  if [[ $value =~ [[:cntrl:]] ]]; then
    return 1
  fi
  value=${value//\\/\\\\}
  value=${value//\"/\\\"}
  value=${value//\`/\\\`}
  value=${value//\$/\\\$}
  value=${value//%/%%}
  printf '"%s"' "$value"
}

ROOT="$(cd -- "$(dirname -- "$(readlink -f -- "$0")")" && pwd)"
APP="$ROOT/app"
VENV="$APP/.venv"
PY="$VENV/bin/python"

if [ -z "${HOME:-}" ] || [ "${HOME#/}" = "$HOME" ]; then
  err "HOME must be set to an absolute path."
  exit 1
fi
CURRENT_USER="${USER:-$(id -un)}"

say "${BLD}${BLU}Mumble — private, on-device voice-to-text — Linux installer${RST}"
say "Install dir: $ROOT"

# ---------------------------------------------------------------------------
# 1. Detect the package manager + the matching system-dependency line.
# ---------------------------------------------------------------------------
hdr "1/6  System dependencies"

PKG=""; SUDO=""
if   command -v apt-get >/dev/null 2>&1; then PKG=apt
elif command -v dnf     >/dev/null 2>&1; then PKG=dnf
elif command -v pacman  >/dev/null 2>&1; then PKG=pacman
elif command -v zypper  >/dev/null 2>&1; then PKG=zypper
fi
command -v sudo >/dev/null 2>&1 && SUDO="sudo"
[ "$(id -u)" = "0" ] && SUDO=""

REQ=()
OPT=()
INSTALL=()

# REQUIRED = the app cannot run without these (WebKitGTK + PyGObject + PortAudio +
#            a toolchain for any dependency lacking a wheel). OPTIONAL = display-server input
#            helpers (both X11 and Wayland sets, the app probes at runtime) PLUS
#            the GStreamer codecs WebKitGTK needs to DECODE the Reader's mp3 audio
#            (HTML5 <audio> in WebKitGTK plays through GStreamer; without these the
#            Reader synthesises fine but stays silent). mp3 decode lives in
#            plugins-good/libav — best-effort so a missing package never blocks.
case "$PKG" in
  apt)
    REQ=(python3-venv python3-pip python3-dev build-essential python3-gi python3-gi-cairo gir1.2-gtk-3.0 gir1.2-webkit2-4.1 libportaudio2 xdg-utils)
    OPT=(gir1.2-ayatanaappindicator3-0.1 xclip wl-clipboard xdotool ydotool wtype libnotify-bin gstreamer1.0-plugins-good gstreamer1.0-libav desktop-file-utils)
    INSTALL=(apt-get install -y) ;;
  dnf)
    REQ=(python3-pip python3-devel gcc gcc-c++ python3-gobject gtk3 webkit2gtk4.1 portaudio xdg-utils)
    OPT=(libayatana-appindicator-gtk3 xclip wl-clipboard xdotool ydotool wtype libnotify gstreamer1-plugins-good gstreamer1-libav desktop-file-utils)
    INSTALL=(dnf install -y) ;;
  pacman)
    REQ=(python-pip base-devel python-gobject gtk3 webkit2gtk-4.1 portaudio xdg-utils)
    OPT=(libayatana-appindicator xclip wl-clipboard xdotool ydotool wtype libnotify gst-plugins-good gst-libav desktop-file-utils)
    INSTALL=(pacman -S --needed --noconfirm) ;;
  zypper)
    REQ=(python3-pip python3-devel gcc gcc-c++ python3-gobject python3-gobject-cairo typelib-1_0-Gtk-3_0 typelib-1_0-WebKit2-4_1 libwebkit2gtk-4_1-0 libportaudio2 xdg-utils)
    OPT=(typelib-1_0-AyatanaAppIndicator3-0_1 xclip wl-clipboard xdotool ydotool wtype libnotify-tools gstreamer-plugins-good gstreamer-plugins-libav desktop-file-utils)
    INSTALL=(zypper install -y) ;;
  *)
    PKG="" ;;
esac

if [ -z "$PKG" ]; then
  warn "Could not detect apt/dnf/pacman/zypper. Install these manually, then re-run:"
  say  "    Python venv + pip + a C compiler + python3 headers"
  say  "    PyGObject (gi) + GTK3 + WebKitGTK 4.1 typelibs"
  say  "    PortAudio runtime; GStreamer plugins-good + libav (Reader mp3 audio);"
  say  "    xdg-utils; and: xclip wl-clipboard xdotool ydotool wtype libnotify"
else
  say "Detected ${BLD}$PKG${RST}. Mumble needs these system packages:"
  say "  ${BLD}required${RST}: ${REQ[*]}"
  say "  ${BLD}optional${RST}: ${OPT[*]}"
  say ""
  say "The package manager will install the required set first, then best-effort optional helpers."
  case "${MUMBLE_INSTALL_SYSTEM_PACKAGES:-}" in
    1|y|Y|yes|YES) ans="y" ;;
    0|n|N|no|NO)  ans="n" ;;
    *)
      if [ -t 0 ] || [ -t 1 ]; then
        printf 'Install system packages now? [Y/n] '
        read -r ans </dev/tty 2>/dev/null || ans="n"
      else
        ans="n"
        warn "No interactive terminal; system packages were not changed."
        say "Set MUMBLE_INSTALL_SYSTEM_PACKAGES=1 to opt in during an unattended install."
      fi ;;
  esac
  case "${ans:-y}" in
    n|N|no|NO)
      warn "Skipping system packages. Install the required set shown above before launching Mumble." ;;
    *)
      if [ "$(id -u)" != "0" ] && [ -z "$SUDO" ]; then
        err "System packages need root privileges, but sudo is not installed."
        err "Install the required packages as root, then re-run with MUMBLE_INSTALL_SYSTEM_PACKAGES=0."
        exit 1
      fi
      _priv=()
      [ -n "$SUDO" ] && _priv=("$SUDO")
      if "${_priv[@]}" "${INSTALL[@]}" "${REQ[@]}"; then
        ok "Required system packages installed."
      else
        err "Required system-package installation failed; Mumble cannot run without it."
        exit 1
      fi
      # Combine optional packages into one install command per package manager so
      # the user is prompted for sudo only once. If the combined install fails (one
      # name missing on this distro), fall back to installing them individually so
      # a single unavailable package never aborts the rest.
      if [ "${#OPT[@]}" -gt 0 ]; then
        if "${_priv[@]}" "${INSTALL[@]}" "${OPT[@]}" >/dev/null 2>&1; then
          ok "optional packages installed"
        else
          for p in "${OPT[@]}"; do
            "${_priv[@]}" "${INSTALL[@]}" "$p" >/dev/null 2>&1 \
              && ok "optional: $p" \
              || warn "optional: $p unavailable (skipped)"
          done
        fi
      fi ;;
  esac
fi

# ---------------------------------------------------------------------------
# 2. Virtualenv that can see the SYSTEM PyGObject (the #1 install gotcha).
# ---------------------------------------------------------------------------
hdr "2/6  Python virtual environment"

# PyGObject is installed by the distro into its own Python's site-packages.
# A pyenv/Conda python earlier on PATH cannot normally see those files even in a
# --system-site-packages venv, so prefer the distro interpreter when available.
if [ -x /usr/bin/python3 ]; then
  PYBIN=/usr/bin/python3
else
  PYBIN="$(command -v python3 || command -v python || true)"
fi
if [ -z "$PYBIN" ]; then
  err "python3 not found on PATH. Install Python ${MIN_PYTHON_MAJOR}.${MIN_PYTHON_MINOR}+ and re-run."
  exit 1
fi
say "Using interpreter: $PYBIN ($("$PYBIN" --version 2>&1))"

if ! "$PYBIN" - "$MIN_PYTHON_MAJOR" "$MIN_PYTHON_MINOR" <<'PYEOF'
import sys
required = tuple(map(int, sys.argv[1:3]))
raise SystemExit(0 if sys.version_info[:2] >= required else 1)
PYEOF
then
  err "Python ${MIN_PYTHON_MAJOR}.${MIN_PYTHON_MINOR} or newer is required by the locked runtime dependencies."
  err "Install a newer Python, ensure python3 resolves to it, then re-run."
  exit 1
fi

_venv_rebuild=""
if [ -x "$PY" ]; then
  if ! grep -Eiq '^include-system-site-packages[[:space:]]*=[[:space:]]*true[[:space:]]*$' "$VENV/pyvenv.cfg" 2>/dev/null; then
    _venv_rebuild="it was created without --system-site-packages"
  elif ! "$PY" - "$PYBIN" <<'PYEOF'
import os
import sys
try:
    same = os.path.samefile(sys._base_executable, sys.argv[1])
except (AttributeError, OSError):
    same = os.path.realpath(getattr(sys, "_base_executable", "")) == os.path.realpath(sys.argv[1])
raise SystemExit(0 if same else 1)
PYEOF
  then
    _venv_rebuild="it uses a different base interpreter than $PYBIN"
  elif ! "$PY" - "$MIN_PYTHON_MAJOR" "$MIN_PYTHON_MINOR" <<'PYEOF'
import sys
required = tuple(map(int, sys.argv[1:3]))
raise SystemExit(0 if sys.version_info[:2] >= required else 1)
PYEOF
  then
    _venv_rebuild="its Python is older than ${MIN_PYTHON_MAJOR}.${MIN_PYTHON_MINOR}"
  fi
fi

if [ ! -x "$PY" ] || [ -n "$_venv_rebuild" ]; then
  # --system-site-packages is MANDATORY: PyGObject is a system package, not a pip
  # wheel, so the venv must be able to import the system `gi`.
  if [ -n "$_venv_rebuild" ]; then
    warn "Rebuilding the installer-managed venv because $_venv_rebuild."
    "$PYBIN" -m venv --clear --system-site-packages "$VENV" \
      || { err "venv repair failed"; exit 1; }
  else
    "$PYBIN" -m venv --system-site-packages "$VENV" \
      || { err "venv creation failed"; exit 1; }
  fi
  ok "Created venv ($VENV) with --system-site-packages"
else
  ok "Reusing existing venv"
fi

"$PY" -m pip install --upgrade pip >/dev/null 2>&1 && ok "pip upgraded" || warn "pip upgrade skipped"

# ---------------------------------------------------------------------------
# 3. Directory integrity — verify the essential app files are present before we
#    start installing into the venv. A partial clone or broken unzip produces a
#    confusing ImportError much later; this catches it early.
# ---------------------------------------------------------------------------
hdr "3/6  Directory integrity check"

MISSING_FILES=""
for _f in "mumble_linux.py" "overlay_linux.py" "branding.py" "requirements.txt" \
          "assets/mumble.png" "webui/index.html"; do
  if [ ! -f "$APP/$_f" ]; then
    MISSING_FILES="$MISSING_FILES  $APP/$_f\n"
  fi
done
if [ -n "$MISSING_FILES" ]; then
  err "Essential files are missing — the app directory may be incomplete:"
  printf '%b' "$MISSING_FILES"
  err "Re-download Mumble or check the repository checkout, then re-run this installer."
  exit 1
fi
ok "Essential app files present"

# ---------------------------------------------------------------------------
# 4. Python dependencies.
# ---------------------------------------------------------------------------
hdr "4/6  Python dependencies"
if "$PY" -m pip install -r "$APP/requirements.txt"; then
  ok "Python dependencies installed"
else
  err "pip install reported errors."
  err "If an input package failed to build, install your distro's python3 headers + gcc and re-run."
  exit 1
fi
# Sanity-check that the GTK stack is actually importable in this venv.
if "$PY" - <<'PYEOF' >/dev/null 2>&1
import gi; gi.require_version("Gtk","3.0"); gi.require_version("WebKit2","4.1")
from gi.repository import Gtk, WebKit2
PYEOF
then ok "GTK + WebKitGTK reachable from the venv"
else
  err "Could not import GTK/WebKit2 4.1 from the venv. Re-run step 1's system-package command, then re-run this installer."
  exit 1
fi

# Import sounddevice separately because pip can install its Python wheel even
# when the required PortAudio shared library is absent. Without this gate the
# installer can report success and the controller then fails on first launch.
if "$PY" - <<'PYEOF' >/dev/null 2>&1
import sounddevice
PYEOF
then
  ok "PortAudio reachable from the venv"
else
  err "Could not load PortAudio through sounddevice. Install your distro's PortAudio runtime and re-run."
  exit 1
fi

if command -v xdg-open >/dev/null 2>&1; then
  ok "xdg-open available for files, folders, and external links"
else
  err "xdg-open is missing. Install your distro's xdg-utils package and re-run."
  exit 1
fi

# ---------------------------------------------------------------------------
# 5. Pre-fetch the on-device Whisper model (small.en — Windows-parity default).
# ---------------------------------------------------------------------------
hdr "5/6  Speech model (on-device, ~465 MB, one-time download)"
if [ "${MUMBLE_SKIP_MODEL_DOWNLOAD:-0}" = "1" ]; then
  warn "Speech-model prefetch skipped by MUMBLE_SKIP_MODEL_DOWNLOAD=1."
elif "$PY" - <<'PYEOF'
import sys
try:
    from faster_whisper import WhisperModel
    WhisperModel("small.en", device="cpu", compute_type="int8")
    print("model ready")
except Exception as e:
    print("defer:", e); sys.exit(3)
PYEOF
then ok "Speech model cached"
else warn "Model not pre-fetched (offline?). Mumble will download it on first dictation."; fi

# ---------------------------------------------------------------------------
# 6. Launcher + application-menu entry.
# ---------------------------------------------------------------------------
hdr "6/6  Desktop integration"

LAUNCHER="$ROOT/mumble"
if ! cat > "$LAUNCHER" <<'EOF'
#!/usr/bin/env bash
# Mumble voice-to-text launcher. Resolve paths at launch time so moving a
# complete installation folder does not leave embedded paths behind.
ROOT="$(cd -- "$(dirname -- "$(readlink -f -- "$0")")" && pwd)"
APP="$ROOT/app"
cd -- "$APP" || exit 1
exec "$APP/.venv/bin/python" "$APP/mumble_linux.py" "$@"
EOF
then
  err "Could not write launcher: $LAUNCHER"
  exit 1
fi
chmod 0755 "$LAUNCHER" \
  || { err "Could not make the launcher executable."; exit 1; }
chmod +x "$ROOT/uninstall.sh" \
  || { err "Could not make the uninstaller executable."; exit 1; }
ok "Launcher: $LAUNCHER"

XDG_DATA="$(xdg_home_or_default "${XDG_DATA_HOME:-}" "$HOME/.local/share")"
APPS_DIR="$XDG_DATA/applications"
mkdir -p "$APPS_DIR" \
  || { err "Could not create the application-menu directory."; exit 1; }
DESKTOP="$APPS_DIR/mumble-voice-to-text.desktop"
UNINSTALL_DESKTOP="$APPS_DIR/mumble-voice-to-text-uninstall.desktop"
ICON_DIR="$XDG_DATA/icons/hicolor/256x256/apps"
ICON_PATH="$ICON_DIR/mumble-voice-to-text.png"
mkdir -p "$ICON_DIR" \
  || { err "Could not create the application-icon directory."; exit 1; }
cp -- "$APP/assets/mumble.png" "$ICON_PATH" \
  || { err "Could not install the application icon."; exit 1; }
chmod 0644 "$ICON_PATH" \
  || { err "Could not set application-icon permissions."; exit 1; }

if ! DESKTOP_LAUNCHER="$(desktop_exec_quote "$LAUNCHER")" \
   || ! DESKTOP_UNINSTALLER="$(desktop_exec_quote "$ROOT/uninstall.sh")"; then
  err "The install path contains control characters that desktop entries cannot represent."
  exit 1
fi
if ! cat > "$DESKTOP" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=Mumble
GenericName=Voice to Text
Comment=Private, on-device voice-to-text — speak, it types
Exec=$DESKTOP_LAUNCHER
Icon=mumble-voice-to-text
Terminal=false
Categories=Utility;Accessibility;AudioVideo;
Keywords=dictation;speech;voice;transcribe;whisper;
StartupNotify=false
EOF
then
  err "Could not write app-menu entry: $DESKTOP"
  exit 1
fi
chmod 0644 "$DESKTOP" \
  || { err "Could not set app-menu entry permissions."; exit 1; }
ok "App menu entry: $DESKTOP"

if ! cat > "$UNINSTALL_DESKTOP" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=Uninstall Mumble
Comment=Remove Mumble desktop integration
Exec=$DESKTOP_UNINSTALLER
Icon=mumble-voice-to-text
Terminal=true
Categories=Settings;Utility;
EOF
then
  err "Could not write uninstaller entry: $UNINSTALL_DESKTOP"
  exit 1
fi
chmod 0644 "$UNINSTALL_DESKTOP" \
  || { err "Could not set uninstaller-entry permissions."; exit 1; }
ok "Uninstaller entry: $UNINSTALL_DESKTOP"

# Older releases used the generic mumble.desktop ID, which shadows the unrelated
# Mumble VoIP package on Debian and other distributions. Remove only entries
# positively identified as ours; never delete an arbitrary user override.
LEGACY_DESKTOP="$APPS_DIR/mumble.desktop"
LEGACY_UNINSTALL_DESKTOP="$APPS_DIR/mumble-uninstall.desktop"
if [ -f "$LEGACY_DESKTOP" ] \
   && grep -Fq 'GenericName=Voice to Text' "$LEGACY_DESKTOP"; then
  rm -f -- "$LEGACY_DESKTOP"
fi
if [ -f "$LEGACY_UNINSTALL_DESKTOP" ] \
   && grep -Fq 'Comment=Remove Mumble desktop integration' "$LEGACY_UNINSTALL_DESKTOP"; then
  rm -f -- "$LEGACY_UNINSTALL_DESKTOP"
fi

if command -v desktop-file-validate >/dev/null 2>&1; then
  if ! desktop-file-validate "$DESKTOP" "$UNINSTALL_DESKTOP"; then
    rm -f -- "$DESKTOP" "$UNINSTALL_DESKTOP"
    err "Desktop-entry validation failed; invalid launchers were removed."
    exit 1
  fi
  ok "Desktop entries validated"
fi
command -v update-desktop-database >/dev/null 2>&1 \
  && update-desktop-database "$APPS_DIR" >/dev/null 2>&1 \
  || true

# Match the first-install lifecycle on the other platforms: start at login by
# default, with Settings able to disable it later. Generate the entry through
# the same module used by the UI so it always targets this venv launcher.
if (cd "$APP" && "$PY" -c 'import autostart; raise SystemExit(0 if autostart.enable() else 1)'); then
  ok "Login autostart enabled"
else
  warn "Could not enable login autostart; enable it later from Mumble Settings."
fi

hdr "Done."
say "Launch Mumble with:  ${BLD}$LAUNCHER${RST}   (or find ${BLD}Mumble${RST} in your app menu)"
say "Default hotkeys:     ${BLD}Ctrl+Super${RST} record · choose a sticky mode in Mumble · ${BLD}Ctrl+Alt+V${RST} paste-latest · ${BLD}Ctrl+Alt+D${RST} History"
say ""
say "${YLW}Linux input note:${RST} global hotkeys read evdev on both X11 and Wayland, which needs"
say "your user in the ${BLD}input${RST} group:  ${BLU}sudo usermod -aG input \"$CURRENT_USER\"${RST}  then log out/in."
say "Shortcut injection uses xdotool on X11 and wtype/ydotool on Wayland."
