#!/usr/bin/env bash
# Proves that the all-HTML-classic-scripts feature selects unmarked inline,
# external, deferred, and async script elements, plus their V8 microtasks.
#
# Usage:
#   cargo build -p servoshell \
#     --features v8-all-html-classic-scripts-authoritative
#   support/v8/run_all_html_classic_scripts_proof.sh [path/to/servoshell] [enabled|disabled]

set -uo pipefail

servoshell="${1:-target/debug/servoshell}"
mode="${2:-enabled}"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
out="$(mktemp -d "${TMPDIR:-/tmp}/servo-v8-all-classic.XXXXXX")"
trap 'rm -rf "$out"' EXIT

if [ ! -x "$servoshell" ]; then
  echo "no servoshell at $servoshell" >&2
  exit 1
fi

case "$mode" in
  enabled) expected_calls=7 ;;
  disabled) expected_calls=0 ;;
  *)
    echo "mode must be 'enabled' or 'disabled', got '$mode'" >&2
    exit 2
    ;;
esac

png="$out/all-classic.png"
log="$out/all-classic.log"
status=0
RUST_LOG=warn,script::script_thread=debug \
  "$servoshell" -z -x --hard-fail -o "$png" \
    "$here/all_html_classic_scripts_proof.html" >"$log" 2>&1 || status=$?

counts="$(sed -n 's/.*, \([0-9]*\) Document.bgColor getter, \([0-9]*\) Document.bgColor setter,.*/\1 \2/p' "$log" | tail -1)"
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
if [ "$counts" != "$expected_calls $expected_calls" ]; then
  echo "FAIL  expected $expected_calls V8 getter/setter calls, got '${counts:-none}'"
  failures=$((failures + 1))
fi
if grep -Eq "re-entrant|skipping V8|panicked at" "$log"; then
  echo "FAIL  found a V8 guard rejection or panic"
  grep -E "re-entrant|skipping V8|panicked at" "$log" | head -3
  failures=$((failures + 1))
fi

if [ "$failures" -ne 0 ]; then
  exit 1
fi
echo "ok    all unmarked HTML classic scripts mode=$mode: rgb=$rgb bgColor get/set=$counts"
