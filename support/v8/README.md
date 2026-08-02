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

Every generated C++ call into Rust is covered by the runtime's callback-depth
scope, including constructors, attributes, methods, cppgc tracing and
destruction. Rust-owned UTF-8 release callbacks use the same scope. A hostile
test implementation attempts to enter its runtime from each phase, including
inside marking and sweeping, and must be rejected before V8 is touched.

The production binding slice is generated from the enabled `Document.hidden`,
`Document.bgColor`, `Document.URL`, `Document.visibilityState`, and
`Node.nodeType`, `Document.documentElement`, and `Document.head` declarations
in Servo's real
`components/script_bindings/webidls/Document.webidl`. Which members are exposed
is a data manifest of `(qualified name, shape, exact returned interface)`
records, with the interface field absent for non-interface values. One selector
and one emitter are registered per shape, so widening the slice with a member
of a known shape is an edit to that manifest. The generator emits accessor
bodies and prototype registration as well as the ABI and Rust thunks. Each
shape also declares an extended-attribute allowlist and fails on anything
outside it, because an unlisted extended attribute usually changes conversion
or reaction semantics that the generated glue implements literally -- it
would be silently wrong rather than merely unsupported.

`Node.nodeType` lands on the existing `document` facade rather than needing a
second host: `Document` inherits from `Node`, so no second native pointer and
no second vtable are involved. An enum crosses the ABI as its string value, so
its selector pins the exact value set the glue was generated against; a new
state appearing upstream becomes a build failure rather than an unvalidated
string.

`Document.documentElement`, `Document.head`, and `Element.tagName` are the
members whose value or receiver is another DOM object, and they rest on the
per-realm wrapper cache described in `wrapper-identity-design.md`. The two
Document getters prove that distinct DOM identities get distinct stable
wrappers in one realm. That cache was recorded here as blocked by a cross-heap
cycle; it is not, because every edge between the heaps points from cppgc into
SpiderMonkey and none point back. The design doc records the constraint that
keeps that true, along with why the DOM object's address is a safe cache key
and why realm teardown must release hosts synchronously.

Each pipeline realm owns
a stable V8 `document` facade. Its native accessors recover tagged per-context
embedder state from the holder's creation context and call typed Rust C ABI
thunks. The Rust host owns a `Trusted<Document>` rather than a raw DOM pointer;
realm destruction first detaches and resets all V8 handles, then drops that
host synchronously and exactly once. The callbacks root the live Servo document
for the operation. The `bgColor` setter uses an owned UTF-8 transfer across the
C ABI and leaves custom-element reactions on Servo's current or backup element
queue. Servo drains them only after the V8 callback and sidecar borrow unwind;
`ce-reactions-boundary.md` records the ordering and remaining limitation. A
C++/Rust reentry barrier turns accidental recursive entry into a deterministic
failure. Failed installation leaves ownership with Rust, so every host
transfer is transactional.

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

Setting `document.bgColor` can enqueue a customized element's
`attributeChangedCallback`, which is SpiderMonkey JavaScript. That callback is
never invoked inside the live V8 setter. It stays on Servo's rooted current or
backup element queue and runs from Servo's existing microtask checkpoint after
the V8 frames, C++/Rust callback, authoritative-entry guard, and sidecar borrow
have all unwound. A defensive native-value short circuit remains for any other
page-reachable reentry route, but the CEReactions proof now requires that this
normal mutation path performs a real V8 `Document.hidden` round trip with no
short-circuit warning. `authoritative_cereactions_proof.html` needs both
authoritative features and so is not part of `run_proofs.sh`. After building
with both features, run its focused checks with:

```sh
support/v8/run_cereactions_proof.sh
```

An additional non-default experiment makes one tightly scoped classic script
V8-authoritative:

```sh
cargo build -p servoshell --features v8-classic-script-authoritative
```

Any classic script with the exact `data-servo-v8="authoritative"` attribute
takes this path, in every execution mode: parser-blocking, deferred, async,
and dynamically inserted, inline or external. Servo still performs the normal
HTML fetch, CSP, ordering, and settings-stack work, but V8 alone compiles and
executes that script.

String timer handlers opt in too, but through a prologue directive rather than
an attribute, because a timer handler is a bare source string with no element
to carry one:

```js
setTimeout('"use servo-v8"; /* body */', 0);
```

Like `"use strict"`, that evaluates to a harmless expression statement in an
engine that does not recognise it, and only a genuine prologue directive counts
— `"use servo-v8-nope"` does not opt in. The choice is deliberately not
inherited from the scheduling script: the V8 host exposes no `setTimeout`, so
an authoritative script cannot schedule a timer and inheritance could only ever
select SpiderMonkey.

That completes classic-script coverage on the window script thread. Module
scripts remain on SpiderMonkey because they are a different creation path, not
a kind of classic script, and worker and service-worker scripts remain
SpiderMonkey-owned by design.

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

`document.open()` was recorded here as a gap, on the assumption that it
installs a fresh global the realm would not follow. That was wrong, and
`authoritative_document_open_proof.html` now pins the correct behaviour: HTML
reuses the same `Document` and `Window`, and so does Servo. `open()` clears the
children, removes listeners, resets the URL, and builds a new parser, but the
global — and therefore the realm — is the same object throughout, and the host
reads through a live `Trusted<Document>` so the new URL is picked up rather
than cached.

The current visible host
surface is deliberately limited to `window`, `document.hidden`,
`document.bgColor`, `document.URL`, `document.visibilityState`,
`document.nodeType`, `document.documentElement`, `document.head`, and
`Element.tagName` on the elements that return.

V8 installs its own `console` on every context, and with no inspector attached
its methods silently discard everything. An authoritative script therefore
logged nothing while the identical script on SpiderMonkey logged normally —
a silent divergence rather than a visible gap, which is the one failure mode
this experiment is meant not to have. The realm now deletes it, so
`console.log` is a `ReferenceError` until a real host binding exists. Anything
else V8 puts on the global that the web platform also defines deserves the same
treatment.

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

Each failure is tagged with the realm that produced it — for a rejection, the
realm that created the promise rather than whichever happened to be entered —
and Servo maps that realm back to a pipeline so the failure is reported on the
owning global. A page therefore observes its own failing V8 promise through
`onerror`, which `authoritative_job_error_proof.html` pins. A failure whose
realm cannot be determined, or whose pipeline has already gone, still falls
back to the log because there is no global left to fire on.

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
| `authoritative_visibility_nodetype_proof.html` | the enum and numeric shapes, the latter inherited from `Node` |
| `authoritative_timer_proof.html` | a string timer handler runs on V8, handed off across a task boundary |
| `authoritative_realm_surface_proof.html` | the realm exposes no API it cannot implement |
| `authoritative_document_open_proof.html` | the realm survives `document.open()` |
| `authoritative_job_error_proof.html` | a page sees its own failing V8 promise via `onerror` |
| `authoritative_wrapper_identity_proof.html` | a DOM object handed to V8 keeps one stable wrapper |

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
The current exported C ABI is version 14 and remains experimental. The original
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
