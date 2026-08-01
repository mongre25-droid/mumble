#!/bin/bash
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# The Python builder is the sole package authority. It creates and validates
# separate deterministic source candidates for both supported architectures.
# Its allowlist excludes the former shell patterns "app/test_*.py",
# "app/overlay.py", "app/requirements-dev.txt", and "app/_test_logs/*".
python3 "$DIR/build_release.py" \
  --output-dir "$DIR" \
  --architecture arm64 \
  --architecture x86_64
