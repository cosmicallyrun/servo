#!/usr/bin/env bash
# Checks that V8-originated CEReactions wait until the V8 host callback and
# Servo sidecar borrow have unwound. This focused proof needs both authoritative
# features, unlike run_proofs.sh.
#
# Usage:
#   cargo build -p servoshell \
#     --features v8-classic-script-authoritative,v8-document-hidden-authoritative
#   support/v8/run_cereactions_proof.sh [path/to/servoshell]

set -uo pipefail

servoshell="${1:-target/debug/servoshell}"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
out="$(mktemp -d "${TMPDIR:-/tmp}/servo-v8-cereactions.XXXXXX")"
trap 'rm -rf "$out"' EXIT

if [ ! -x "$servoshell" ]; then
  echo "no servoshell at $servoshell" >&2
  exit 1
fi

png="$out/cereactions.png"
log="$out/cereactions.log"
status=0
RUST_LOG=warn,script::script_thread=debug \
  "$servoshell" -z -x --hard-fail \
    --enable-experimental-web-platform-features \
    -o "$png" "$here/authoritative_cereactions_proof.html" >"$log" 2>&1 || status=$?

counts="$(sed -n 's/.*after \([0-9]*\) Document.hidden, \([0-9]*\) Document.bgColor getter, \([0-9]*\) Document.bgColor setter,.*/\1 \2 \3/p' "$log" | tail -1)"
rgb="$(python3 - "$png" <<'PY'
import sys
from collections import Counter
try:
    from PIL import Image
except ImportError:
    print("no-pillow"); sys.exit(0)
try:
    image = Image.open(sys.argv[1]).convert("RGB")
except Exception as error:
    print(f"unreadable: {error}"); sys.exit(0)
data = image.get_flattened_data() if hasattr(image, "get_flattened_data") else image.getdata()
colours = Counter(data)
print(colours.most_common(1)[0][0] if len(colours) == 1 else f"mixed: {colours.most_common(3)}")
PY
)"

failures=0
if [ "$status" -ne 0 ]; then
  echo "FAIL  servoshell exited with status $status"
  failures=$((failures + 1))
fi
if [ "$rgb" != "(0, 255, 0)" ]; then
  echo "FAIL  expected a uniformly lime render, got $rgb"
  failures=$((failures + 1))
fi
if ! grep -Eq 'RESULT ceFired=2 elementFired=2 childDisconnected=1 ceHidden=(true|false) elementHidden=(true|false) childHidden=(true|false)' "$log"; then
  echo "FAIL  expected all five custom-element callbacks to read document.hidden"
  failures=$((failures + 1))
fi
if [ "$counts" != "6 1 1" ]; then
  echo "FAIL  expected hidden/bgColor-get/bgColor-set counts '6 1 1', got '${counts:-none}'"
  failures=$((failures + 1))
fi
if grep -Eq "answered from the host's own native source|re-entrant authoritative V8|panicked at" "$log"; then
  echo "FAIL  found a reentry short circuit or panic"
  grep -E "answered from the host's own native source|re-entrant authoritative V8|panicked at" "$log" | head -3
  failures=$((failures + 1))
fi

if [ "$failures" -ne 0 ]; then
  exit 1
fi
echo "ok    Document and Element CEReactions deferred: rgb=$rgb hidden/bgColor-get/bgColor-set=$counts"
