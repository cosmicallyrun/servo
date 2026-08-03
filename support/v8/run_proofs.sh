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
  "authoritative_defer_proof.html:(0, 255, 0):1:1"
  "authoritative_async_proof.html:(0, 255, 0):1:1"
  "authoritative_dynamic_proof.html:(0, 255, 0):1:1"
  "authoritative_url_proof.html:(0, 255, 0):1:1"
  "authoritative_visibility_nodetype_proof.html:(0, 255, 0):1:1"
  "authoritative_ready_state_proof.html:(0, 255, 0):1:1"
  "authoritative_title_proof.html:(0, 255, 0):1:1"
  "authoritative_metadata_proof.html:(0, 255, 0):1:1"
  "authoritative_timer_proof.html:(0, 255, 0):1:1"
  "authoritative_realm_surface_proof.html:(0, 255, 0):1:1"
  "authoritative_document_open_proof.html:(0, 255, 0):1:1"
  "authoritative_wrapper_identity_proof.html:(0, 255, 0):1:1"
  "authoritative_get_element_by_id_proof.html:(0, 255, 0):1:1"
  # bgColor is set by the SpiderMonkey error handler, not by V8.
  "authoritative_job_error_proof.html:(0, 255, 0):0:0"
)

failures=0
for proof in "${proofs[@]}"; do
  IFS=: read -r page expected_rgb expected_get expected_set <<<"$proof"
  png="$out/${page%.html}.png"
  log="$out/${page%.html}.log"

  RUST_LOG=warn,script::script_thread=debug \
    "$servoshell" -z -x --hard-fail -o "$png" "$here/$page" >"$log" 2>&1

  # Realm teardown reports the host-call counts this proof depends on.
  counts="$(sed -n 's/.*, \([0-9]*\) Document.bgColor getter, \([0-9]*\) Document.bgColor setter,.*/\1 \2/p' "$log" | tail -1)"
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
control_counts="$(sed -n 's/.*, \([0-9]*\) Document.bgColor getter, \([0-9]*\) Document.bgColor setter,.*/\1 \2/p' "$out/control.log" | tail -1)"
if [ "$control_counts" = "2 2" ]; then
  echo "ok    control: two executions report two getters and two setters"
else
  echo "FAIL  control: expected '2 2' host calls, got '${control_counts:-none}'"
  failures=$((failures + 1))
fi

# Many authoritative scripts on one page, alternating a direct set with a set
# deferred into a microtask. This exercises repeated realm entry, the
# check-then-set reentry guard, and a checkpoint at every task boundary. An odd
# count ends lime. Generated rather than committed so the count is easy to
# raise.
stress="$out/stress.html"
python3 - "$stress" <<'PY'
import sys

SCRIPTS = 51
assert SCRIPTS % 2 == 1, "an odd count must end lime"
toggle = 'document.bgColor = document.bgColor === "lime" ? "red" : "lime";'
parts = ['<!doctype html><html><body bgcolor="red">']
for index in range(SCRIPTS):
    body = toggle if index % 2 == 0 else f"Promise.resolve().then(() => {{ {toggle} }});"
    parts.append(f'<script data-servo-v8="authoritative">{body}</script>')
parts.append("</body></html>")
open(sys.argv[1], "w").write("\n".join(parts))
PY
RUST_LOG=warn,script::script_thread=debug \
  "$servoshell" -z -x --hard-fail -o "$out/stress.png" "$stress" >"$out/stress.log" 2>&1
stress_counts="$(sed -n 's/.*, \([0-9]*\) Document.bgColor getter, \([0-9]*\) Document.bgColor setter,.*/\1 \2/p' "$out/stress.log" | tail -1)"
stress_rgb="$(python3 - "$out/stress.png" <<'PY'
import sys
from collections import Counter
try:
    from PIL import Image
except ImportError:
    print("no-pillow"); sys.exit(0)
image = Image.open(sys.argv[1]).convert("RGB")
data = image.get_flattened_data() if hasattr(image, "get_flattened_data") else image.getdata()
colours = Counter(data)
print(colours.most_common(1)[0][0] if len(colours) == 1 else "mixed")
PY
)"
if [ "$stress_counts" = "51 51" ] && [ "$stress_rgb" = "(0, 255, 0)" ] &&
   ! grep -q "re-entrant\|skipping V8\|panicked at" "$out/stress.log"; then
  echo "ok    stress: 51 scripts report 51 getters and 51 setters, no guard rejections"
else
  echo "FAIL  stress: expected '51 51' and lime, got '${stress_counts:-none}' and $stress_rgb"
  grep -o "re-entrant.*\|skipping V8.*\|panicked at.*" "$out/stress.log" | head -3
  failures=$((failures + 1))
fi

if [ "$failures" -ne 0 ]; then
  echo "$failures proof(s) failed" >&2
  exit 1
fi
echo "all proofs passed"
