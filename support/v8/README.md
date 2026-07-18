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

The first production binding slice is generated separately from the enabled
`Document.hidden` and `Document.bgColor` declarations in Servo's real
`components/script_bindings/webidls/Document.webidl`. Each pipeline realm owns
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
hosts, reentrant callbacks, or failed reads abort the experiment rather than
silently returning Servo's native value. It makes only `document.hidden`
V8-authoritative; SpiderMonkey still parses and executes page JavaScript and
owns all other DOM bindings.

An additional non-default experiment makes one tightly scoped classic script
V8-authoritative:

```sh
cargo build -p servoshell --features v8-classic-script-authoritative
```

Only a parser-inserted, parsing-blocking classic script with the exact
`data-servo-v8="authoritative"` attribute takes this path. Servo still performs
the normal HTML fetch, CSP, ordering, and settings-stack work, but V8 alone
compiles and executes that script. Async, defer, dynamic, module, timer, worker,
and service-worker scripts remain on SpiderMonkey. The current visible host
surface is deliberately limited to `window`, `document.hidden`, and
`document.bgColor`. V8 microtask checkpoint integration is not implemented, so
this mode must not yet be used for promise- or microtask-dependent scripts.

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
The current exported C ABI is version 7 and remains experimental. The original
Runtime compile/eval APIs retain a default context for the standalone binding
smoke tests; Servo's compile shadow uses the pipeline-selected realm APIs. The
realm API can also retain an opaque compiled classic-script handle and consume
it during one later execution. That execution deliberately does not perform an
implicit V8 microtask checkpoint; task-boundary integration remains Servo's
responsibility.

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
  support/v8/turbolev_probe.js
rg -n '"name":"V8\.TFTurboshaftTurbolevGraphBuilding"' \
  "$trace_dir"/*.json
```
