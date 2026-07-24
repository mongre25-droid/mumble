#!/bin/bash

# Script to build a clean distribution zip for MacMumble.
#
# PI-006: The output zip includes the version in its filename and a companion
# SHA256 checksum file so users can verify download integrity.
#
# Notarization: This script produces an UNSIGNED zip for local / CI distribution.
# For Apple-notarized distribution (Developer ID), the maintainer should:
#   1. codesign --deep --force --options runtime --sign "Developer ID: ..." Mumble.app
#   2. zip -r Mumble-vX.Y.Z-macos.zip Mumble.app
#   3. xcrun notarytool submit Mumble-vX.Y.Z-macos.zip --apple-id ... --team-id ...
#   4. xcrun stapler staple Mumble.app
# See https://developer.apple.com/documentation/security/notarizing_macos_software_before_distribution

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

# Extract version from branding.py for the output filename.
VERSION=$(python3 -c "import sys; sys.path.insert(0, '$DIR/app'); from branding import VERSION; print(VERSION)" 2>/dev/null || echo "0.9")
echo "Building MacMumble v${VERSION}..."

ZIPNAME="Mumble-v${VERSION}-macos.zip"
OUTPUT="$DIR/$ZIPNAME"
CHECKSUM="$OUTPUT.sha256"
STAGE=$(mktemp -d "${TMPDIR:-/tmp}/mumble-macos.XXXXXX")
trap 'rm -rf "$STAGE"' EXIT
mkdir -p "$STAGE/MacMumble"

# First archive the actual macOS port directory, then unpack it below the public
# `MacMumble/` folder name. This avoids depending on the repository directory's
# name (`macOS`) and keeps the README/installer layout stable.
SOURCE_ZIP="$STAGE/source.zip"
zip -qr "$SOURCE_ZIP" . \
  -x "app/.venv/*" "*/__pycache__/*" "*/.DS_Store" "*/.git/*" \
  "app/test_*.py" "app/overlay.py" "app/requirements-dev.txt" "app/_test_logs/*" \
  "*/brand_exe.py" "*/assets_gen.py" "*/_rebuild_zip.py" \
  "Mumble-v*-macos.zip" "Mumble-v*-macos.zip.sha256"
(cd "$STAGE/MacMumble" && unzip -q "$SOURCE_ZIP")
rm -f "$OUTPUT" "$CHECKSUM"
(cd "$STAGE" && zip -qr "$OUTPUT" MacMumble)

# Generate integrity checksum for download verification.
(cd "$DIR" && shasum -a 256 "$ZIPNAME" > "$ZIPNAME.sha256")

echo "Done! $OUTPUT has been created."
echo "SHA256 checksum written to $CHECKSUM"
echo ""
echo "File sizes:"
ls -lh "$OUTPUT" "$CHECKSUM"
