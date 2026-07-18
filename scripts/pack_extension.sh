#!/usr/bin/env bash
# pack_extension.sh — produce the Chrome Web Store submission zip (Phase 7.5).
#
# Packages extension/ excluding store assets and icon sources. The zip root
# contains manifest.json directly, as CWS requires.
set -euo pipefail

cd "$(dirname "$0")/.."

VERSION=$(python3 -c "import json; print(json.load(open('extension/manifest.json'))['version'])")
OUT="tradingtransformer-v${VERSION}.zip"

rm -f "$OUT"
(
  cd extension
  zip -r "../$OUT" . \
      --exclude "store/*" \
      --exclude "icons/*.svg" \
      --exclude ".*"
)

echo "Packaged: $OUT"
unzip -l "$OUT" | tail -3
