#!/usr/bin/env bash
# Mumble Linux per-user integration cleanup.
set -u

if [ -z "${HOME:-}" ] || [ "${HOME#/}" = "$HOME" ]; then
  printf 'HOME must be set to an absolute path.\n' >&2
  exit 1
fi

xdg_home_or_default() {
  case "${1:-}" in
    /*) printf '%s\n' "$1" ;;
    *)  printf '%s\n' "$2" ;;
  esac
}

ROOT="$(cd -- "$(dirname -- "$(readlink -f -- "$0")")" && pwd)"
XDG_CONFIG="$(xdg_home_or_default "${XDG_CONFIG_HOME:-}" "$HOME/.config")"
XDG_DATA="$(xdg_home_or_default "${XDG_DATA_HOME:-}" "$HOME/.local/share")"
# Canonicalise the BASE before deriving any recursive-delete target. Textual
# checks alone consider values such as /./ or /tmp/.. different from '/', even
# though the kernel resolves both to the filesystem root.
if ! XDG_DATA_CANON="$(readlink -m -- "$XDG_DATA")" \
   || [ -z "$XDG_DATA_CANON" ] || [ "$XDG_DATA_CANON" = "/" ]; then
  printf 'Refusing unsafe XDG data base: %s\n' "$XDG_DATA" >&2
  exit 1
fi
XDG_DATA="$XDG_DATA_CANON"
DATA_DIR="$XDG_DATA/Mumble"
APPS_DIR="$XDG_DATA/applications"
DESKTOP="$APPS_DIR/mumble-voice-to-text.desktop"
UNINSTALL_DESKTOP="$APPS_DIR/mumble-voice-to-text-uninstall.desktop"
ICON="$XDG_DATA/icons/hicolor/256x256/apps/mumble-voice-to-text.png"
AUTOSTART="$XDG_CONFIG/autostart/mumble-voice-to-text.desktop"

rm -f -- "$AUTOSTART"
rm -f -- "$DESKTOP" "$UNINSTALL_DESKTOP" "$ICON"
rm -f -- "$ROOT/mumble"

# Pre-migration builds used the generic autostart ID. Remove it only when its
# contents positively identify this voice-to-text app; Mumble VoIP may own the
# same filename.
LEGACY_AUTOSTART="$XDG_CONFIG/autostart/mumble.desktop"
if [ -f "$LEGACY_AUTOSTART" ] \
   && { grep -Fxq 'X-Mumble-VoiceToText=true' "$LEGACY_AUTOSTART" \
        || grep -Fxq 'Comment=Private, on-device voice-to-text' "$LEGACY_AUTOSTART"; }; then
  rm -f -- "$LEGACY_AUTOSTART"
fi

# Clean up application-menu entries from pre-migration releases, but only when
# their contents identify them as this voice-to-text app. A generic
# ~/.local/share/applications/mumble.desktop may belong to Mumble VoIP or to the
# user, and must never be removed blindly.
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

if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$APPS_DIR" >/dev/null 2>&1 || true
fi

if [ "${1:-}" = "--purge-data" ]; then
  # XDG_DATA_HOME is allowed anywhere on the filesystem, not only beneath HOME.
  # Remove exactly its Mumble child while refusing an empty/root data base. This
  # retains the safety invariant without breaking valid paths such as /mnt/data.
  if [ -z "$XDG_DATA" ] || [ "$XDG_DATA" = "/" ] \
     || [ "$DATA_DIR" = "/" ] || [ "$(dirname -- "$DATA_DIR")" != "$XDG_DATA" ] \
     || [ "$(basename -- "$DATA_DIR")" != "Mumble" ]; then
    printf 'Refusing to purge unexpected data path: %s\n' "$DATA_DIR" >&2
    exit 1
  fi
  # rm -rf does not follow a final symlink, but make that invariant explicit:
  # an attacker-controlled Mumble link is removed as a link, never canonicalised
  # into and recursively deleted at its destination.
  if [ -L "$DATA_DIR" ]; then
    rm -f -- "$DATA_DIR"
  else
    rm -rf -- "$DATA_DIR"
  fi
  printf 'Mumble integration and user data removed.\n'
else
  printf 'Mumble integration removed. User data was kept at: %s\n' "$DATA_DIR"
  printf 'You may now delete the Mumble folder. Use --purge-data to remove data too.\n'
fi
