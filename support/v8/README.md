# Experimental Servo–V8 compile-shadow bridge

The current build target is native Apple silicon (`aarch64-apple-darwin`). Linux
ARM64 cross-compilation is intentionally deferred until the embedding boundary
is farther along. This is not yet an alternate production JavaScript backend.

The backend uses V8's public unified-heap API (`v8::CppHeap`) rather than raw
weak persistents. The initial bridge deliberately requests atomic cppgc marking
and sweeping: Servo's Rust DOM edge containers do not yet issue cppgc mutation
barriers, so incremental or concurrent tracing would be unsound.

The expected checkout layout is `servo/` and `v8/` as sibling directories. Set
`SERVO_V8_ROOT` to use another V8 checkout and `SERVO_V8_OUT_DIR` to use another
GN output directory. The currently validated V8 revision is
`72b8a475dfd36cb28cc9c536f01f7fbdebe74a36`. After checking out that revision
and syncing its DEPS, configure and build the native M-series artifact with:

```sh
v8_root="${SERVO_V8_ROOT:-../v8}"
v8_out="${SERVO_V8_OUT_DIR:-$v8_root/out/servo-v8}"
mkdir -p "$v8_out"
cp support/v8/args.gn "$v8_out/args.gn"
"$v8_root/buildtools/mac/gn" gen "$v8_out" --check
ninja -C "$v8_out" -j4 v8_monolith d8 build/config:shared_library_deps
cargo test -p servo-v8
(
  cd components/servo_v8
  PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s codegen -p '*_tests.py'
)
```

TurboLev is a runtime flag, not a GN target. `servo-v8` enables `--maglev`,
`--turbofan`, and `--turbolev` before global V8 initialization. The bridge is
compiled with V8's pinned Clang and custom libc++ so its C++ ABI exactly matches
the sandboxed monolith. V8 handles never cross the C ABI.

The build script links V8, its custom libc++/libc++abi, compiler-rt, and the C++
bridge into `libservo_v8_bridge.dylib` with V8's pinned `ld64.lld`. This is
required both because Apple's linker does not accept the custom libc++ and
libc++abi LLVM thin archives and because SpiderMonkey vendors irregexp symbols
in the `v8::internal`
namespace. Only the symbols listed in
`components/servo_v8/include/servo_v8.exports` are exported, keeping those C++
implementations out of Servo's process-wide link namespace. Servoshell copies
the dylib to `target/<profile>/lib`, and its existing
`@executable_path/lib/` rpath loads it. Other embedders must provide an
equivalent copy/rpath (and any required code signing) themselves.

`components/servo_v8/webidls/EngineBindingSmoke.webidl` remains the source of
truth for the synthetic constructor/GC binding slice. Its build script runs
Servo's vendored WebIDL
parser and generates the C ABI vtable, Rust implementation trait and typed
thunks, V8 conversion callbacks, and prototype registration into Cargo's output
directory. The generator deliberately rejects everything except the current
non-nullable `long` and same-interface reference slice so unsupported
conversions cannot compile silently. Its test also forms a bidirectional Rust
DOM cycle, traces the otherwise unreachable child through cppgc, and verifies
both Rust allocations are reclaimed exactly once after the last JS root is
removed. The survival check first exercises V8's normal low-memory path, then
both survival and final reclamation are asserted after test-only full
collections with an explicit no-heap-pointers stack state so conservative
stack scanning cannot hide a broken edge or make the test flaky.

The production binding slice is generated from the enabled `Document.hidden`,
`Document.bgColor`, and `Document.URL` declarations in Servo's real
`components/script_bindings/webidls/Document.webidl`. Which members are exposed
is a data manifest of `(qualified name, shape)` pairs, with one selector and
one emitter registered per shape, so widening the slice with a member of a
known shape is an edit to that manifest. Each shape also declares an
extended-attribute allowlist and fails on anything outside it, because an
unlisted extended attribute usually changes conversion or reaction semantics
that the generated glue implements literally -- it would be silently wrong
rather than merely unsupported. Each pipeline realm owns
a stable V8 `document` facade. Its native accessors recover tagged per-context
embedder state from the holder's creation context and call typed Rust C ABI
thunks. The Rust host owns a `Trusted<Document>` rather than a raw DOM pointer;
realm destruction first detaches and resets all V8 handles, then drops that
host synchronously and exactly once. The callbacks root the live Servo document
for the operation. The `bgColor` setter uses Servo's production CEReactions
stack and an owned UTF-8 transfer across the C ABI. A C++/Rust reentry barrier
turns accidental recursive entry into a deterministic failure. Failed
installation leaves ownership with Rust, so every host transfer is
transactional.

## Compile real Servo scripts in the V8 shadow

The non-default `v8-shadow` feature creates a V8 sidecar on Servo's main script
thread and compile-checks real inline and external classic scripts after source
unminification. The sidecar keeps one V8 isolate per Servo script thread and an
independent V8 context for each live Window pipeline. Realm IDs are opaque,
runtime-local, and invalid after the pipeline is destroyed. Module scripts and
workers currently skip the sidecar. SpiderMonkey remains the sole executor and
source of page script results; V8 initialization, realm, and compile failures
are diagnostic. `v8-shadow` also installs and probes the production
`Document.hidden` host once when each realm is created. Build it with:

```sh
cargo build -p servoshell --features v8-shadow
```

Two narrower experimental modes exercise that getter from Servo's production
SpiderMonkey WebIDL glue:

```sh
# Compare one live V8 host read with Servo's native result, but return native.
cargo build -p servoshell --features v8-document-hidden-diagnostic

# Return the V8 accessor's result with no native fallback.
cargo build -p servoshell --features v8-document-hidden-authoritative
```

The authoritative feature is intentionally strict: missing runtimes, realms,
hosts, or failed reads abort the experiment rather than silently returning
Servo's native value. It makes only `document.hidden` V8-authoritative;
SpiderMonkey still parses and executes page JavaScript and owns all other DOM
bindings.

Re-entrancy is the one deliberate exception, because it is reachable from
ordinary page script rather than from a bug. Setting `document.bgColor` runs
Servo's production CEReactions stack, which can synchronously invoke a custom
element's `attributeChangedCallback` — SpiderMonkey JS running inside a live V8
stack frame, with the sidecar already mutably borrowed — and that callback may
read `document.hidden`. Aborting there would let any page crash the script
thread. Such a read is instead answered from the host's own native source and
logged as a short circuit. This is not a SpiderMonkey fallback: the V8
accessor's host implementation reads `hidden_state_for_v8` and returns it
unchanged, so only the round trip is skipped, and the value cannot differ.
`authoritative_cereactions_proof.html` exercises exactly that path; it needs
both authoritative features and so is not part of `run_proofs.sh`.

The deeper fix is to stop running the CEReactions stack inline inside the V8
setter and push that boundary out to the V8 script and checkpoint boundary, so
reactions drain after the V8 frames unwind. That removes the re-entrancy class
rather than tolerating it, and should land before the host surface grows any
further mutating members.

An additional non-default experiment makes one tightly scoped classic script
V8-authoritative:

```sh
cargo build -p servoshell --features v8-classic-script-authoritative
```

Any classic script with the exact `data-servo-v8="authoritative"` attribute
takes this path, in every execution mode: parser-blocking, deferred, async,
and dynamically inserted, inline or external. Servo still performs the normal
HTML fetch, CSP, ordering, and settings-stack work, but V8 alone compiles and
executes that script. Module, timer, worker, and service-worker scripts remain
on SpiderMonkey.

Every mode routes through the same create/run pair, so the engine choice does
not depend on the script kind. Deferred and async scripts do widen the window
between compiling into the realm and running it: a script is compiled when it
is created and may not run until much later, by which time the pipeline can
have exited and taken the realm with it. Both the compile and run sides
therefore fail the script rather than aborting the process. That is still
strict — nothing re-runs it on SpiderMonkey.

Two things protect that window, and it is worth knowing which does the work.
The realm-identity check comparing the compile-time realm against the current
one is *not* the load-bearing one: a pipeline keeps one realm for its whole
life, so the ids match even when the global has been replaced underneath. The
protection that actually fires is HTML's own "can we run script" step, which
refuses a document that is no longer fully active, plus pipeline-exit ordering
that destroys the realm before the document is torn down.

That leaves a known gap: `document.open()` replaces the global on the
SpiderMonkey side without recreating the V8 realm, so the realm keeps the old
global and the identity check cannot detect it. This predates the widening but
defer and async make the window much larger. Recreating the realm there would
invalidate any retained handle compiled against the old global, which now fails
the script safely rather than aborting — so the fix is tractable, and it should
land before this route is used for anything beyond experiments.

The current visible host
surface is deliberately limited to `window`, `document.hidden`,
`document.bgColor`, and `document.URL`.

Servo performs a V8 microtask checkpoint at HTML's "clean up after running
script" boundary, in `ScriptThread::perform_a_microtask_checkpoint`, before its
own SpiderMonkey checkpoint. `microtask-checkpoint-design.md` records why the
drain stays on the isolate's single explicit queue rather than moving to
per-realm queues, and why V8 drains first.

A job that fails never reaches a `TryCatch` at that boundary. V8 catches an
uncaught job exception inside its own microtask builtin and reports it to the
isolate message handler, and a reaction that throws rejects its derived promise
instead — which is the channel an ordinary `Promise.then` failure takes. The
bridge installs both an `AddMessageListener` and a `SetPromiseRejectCallback`,
buffers what they deliver, and Servo pulls it after the drain, so a handler
attached during the same drain revokes its entry rather than reporting a
spurious failure. Failures are logged with resource, line, column, and stack.

They are not yet routed to the owning global's `error` and `unhandledrejection`
events, because the realm does not travel with the error across the C ABI. That
is the next piece of this work.

Entering that route is guarded by an explicit test-then-set on the script
thread. `V8AuthoritativeScriptGuard::enter` fails on a flag that is already
set, and setting the flag is inseparable from arming its reset, so a rejected
recursive entry can never leave the guard state modified. HTML also allows a
created script never to run, so the compiled handle is owned by a
`V8RetainedScript` whose drop discards it. Execution consumes the handle first,
and discarding is deliberately best effort and infallible: it runs from a drop
path, and every way it can fail — a disposed sidecar, a realm already
destroyed, a sidecar borrowed further up the stack — means the handle is
already gone or is released by realm destruction. Realm and script IDs are
never reused, so a stale discard cannot free an unrelated handle.

### Prove that V8 alone executed the script

`authoritative_bgcolor_proof.html` starts with a red body and toggles
`document.bgColor` exactly once per execution, so the final colour counts
executions rather than merely observing that some engine ran:

```sh
cargo build -p servoshell --features v8-classic-script-authoritative
RUST_LOG=warn,script::script_thread=debug \
  target/debug/servoshell -z -x --hard-fail \
    -o /tmp/authoritative_bgcolor_proof.png \
    support/v8/authoritative_bgcolor_proof.html
```

Every pixel of the screenshot must be `(0, 255, 0)`, and realm teardown must
report exactly one `Document.bgColor` getter and one setter host call. Adding a
second identical authoritative script is the control: it reports two getters
and two setters and renders pure red, which is what a page would show if
SpiderMonkey had also executed the script.

Three proof pages use that same counting argument:

| Page | Proves |
| --- | --- |
| `authoritative_bgcolor_proof.html` | V8 alone executes an inline parser-blocking script |
| `authoritative_external_proof.html` | the same route covers external scripts |
| `authoritative_microtask_proof.html` | the task-boundary microtask checkpoint runs, with a live host context |
| `authoritative_defer_proof.html` | a deferred script runs on V8 after the widest compile-to-run window |
| `authoritative_async_proof.html` | an async script runs on V8 off the parser's critical path |
| `authoritative_dynamic_proof.html` | SpiderMonkey can insert a script that V8 then executes |
| `authoritative_url_proof.html` | `Document.URL` is served by the V8 host, getter-only |

`support/v8/run_proofs.sh` runs all of them and checks both signals each one
depends on, plus two cases it generates rather than commits: the
double-execution control, and a 51-script page alternating direct and
microtask-deferred sets that stresses the reentry guard across many task
boundaries.

Run with a debug log filter to see each source accepted by V8:

```sh
RUST_LOG=warn,script::script_thread=debug \
  target/debug/servoshell https://example.com/
```

The ordinary build does not enable or link `servo-v8`:

```sh
cargo check -p servoshell
```

Because `servo-v8` is a workspace member, explicit `--workspace` checks still
build it and therefore require the sibling V8 artifacts. Use the ordinary
Servoshell package command above when checking a tree without V8 provisioned.
The current exported C ABI is version 10 and remains experimental. The original
Runtime compile/eval APIs retain a default context for the standalone binding
smoke tests; Servo's compile shadow uses the pipeline-selected realm APIs. The
realm API can also retain an opaque compiled classic-script handle and consume
it during one later execution. That execution deliberately does not perform an
implicit V8 microtask checkpoint. Draining is a separate runtime-level call,
because the queue is isolate-wide rather than realm-scoped, and Servo makes it
at the task boundary.

## Verify TurboLev

To prove that the TurboLev frontend reaches Turboshaft independently of the
embedder smoke test, run `d8` with a forced hot function and trace Turbo:

```sh
v8_root="${SERVO_V8_ROOT:-../v8}"
v8_out="${SERVO_V8_OUT_DIR:-$v8_root/out/servo-v8}"
trace_dir="$(mktemp -d /tmp/v8-turbolev-trace.XXXXXX)"
"$v8_out/d8" \
  --no-sandbox-prohibit-insecure-mode \
  --turbolev --turbofan --maglev --allow-natives-syntax \
  --no-concurrent-recompilation --trace-turbo --trace-turbo-filter=tlv_probe \
  --trace-turbo-path="$trace_dir" \
  --trace-turbo-cfg-file="$trace_dir/turbo.cfg" \
  support/v8/turbolev_probe.js
rg -n '"name":"V8\.TFTurboshaftTurbolevGraphBuilding"' \
  "$trace_dir"/*.json
```
