#!/usr/bin/env bash
# Runs every V8-authoritative runtime proof and checks both signals each one
# relies on: the rendered colour, and the per-realm host-call counts logged at
# teardown.
#
# Each proof page starts with a red body and toggles document.bgColor once per
# execution, so the final colour counts executions rather than merely observing
# that some engine ran. Lime is reachable only when V8 executed the script
# exactly once; if SpiderMonkey had also run it the page would be red.
#
# Usage:
#   cargo build -p servoshell --features v8-classic-script-authoritative
#   support/v8/run_proofs.sh [path/to/servoshell]

set -uo pipefail

servoshell="${1:-target/debug/servoshell}"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
out="$(mktemp -d "${TMPDIR:-/tmp}/servo-v8-proofs.XXXXXX")"
trap 'rm -rf "$out"' EXIT

if [ ! -x "$servoshell" ]; then
  echo "no servoshell at $servoshell" >&2
  exit 1
fi

# page:expected_rgb:expected_bgcolor_getters:expected_bgcolor_setters
proofs=(
  "authoritative_bgcolor_proof.html:(0, 255, 0):1:1"
  "authoritative_external_proof.html:(0, 255, 0):1:1"
  "authoritative_microtask_proof.html:(0, 255, 0):1:1"
)

failures=0
for proof in "${proofs[@]}"; do
  IFS=: read -r page expected_rgb expected_get expected_set <<<"$proof"
  png="$out/${page%.html}.png"
  log="$out/${page%.html}.log"

  RUST_LOG=warn,script::script_thread=debug \
    "$servoshell" -z -x --hard-fail -o "$png" "$here/$page" >"$log" 2>&1

  # Realm teardown reports the host-call counts this proof depends on.
  counts="$(sed -n 's/.*after \([0-9]*\) Document.hidden, \([0-9]*\) Document.bgColor getter, and \([0-9]*\) Document.bgColor setter.*/\2 \3/p' "$log" | tail -1)"
  read -r actual_get actual_set <<<"${counts:-none none}"

  actual_rgb="$(python3 - "$png" <<'PY'
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
# A proof is only meaningful if the whole page is one colour.
print(colours.most_common(1)[0][0] if len(colours) == 1 else f"mixed: {colours.most_common(3)}")
PY
)"

  if [ "$actual_rgb" = "$expected_rgb" ] &&
     [ "$actual_get" = "$expected_get" ] &&
     [ "$actual_set" = "$expected_set" ]; then
    echo "ok    $page  rgb=$actual_rgb bgColor get=$actual_get set=$actual_set"
  else
    echo "FAIL  $page"
    echo "        rgb      expected $expected_rgb  got $actual_rgb"
    echo "        getters  expected $expected_get  got $actual_get"
    echo "        setters  expected $expected_set  got $actual_set"
    sed -n 's/.*\(panicked at.*\)/        \1/p' "$log" | head -3
    failures=$((failures + 1))
  fi
done

# The counting argument only means something if double execution really does
# render red. Build that control here rather than committing a page whose whole
# purpose is to fail.
control="$out/control_double.html"
sed 's|</body>|  <script data-servo-v8="authoritative">document.bgColor = document.bgColor === "lime" ? "red" : "lime";</script>\n  </body>|' \
  "$here/authoritative_bgcolor_proof.html" >"$control"
RUST_LOG=warn,script::script_thread=debug \
  "$servoshell" -z -x --hard-fail -o "$out/control.png" "$control" >"$out/control.log" 2>&1
control_counts="$(sed -n 's/.*after \([0-9]*\) Document.hidden, \([0-9]*\) Document.bgColor getter, and \([0-9]*\) Document.bgColor setter.*/\2 \3/p' "$out/control.log" | tail -1)"
if [ "$control_counts" = "2 2" ]; then
  echo "ok    control: two executions report two getters and two setters"
else
  echo "FAIL  control: expected '2 2' host calls, got '${control_counts:-none}'"
  failures=$((failures + 1))
fi

if [ "$failures" -ne 0 ]; then
  echo "$failures proof(s) failed" >&2
  exit 1
fi
echo "all proofs passed"
