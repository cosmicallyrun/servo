# V8 microtask checkpoint integration

Status: design only. Nothing here is implemented yet. The retained-script run
path still performs no checkpoint, so `v8-classic-script-authoritative` must
not be used for promise- or microtask-dependent scripts.

## The boundary

HTML's "clean up after running script" step 5 performs a microtask checkpoint
only once the script settings object stack has become empty. Servo implements
that in `script_bindings/settings_stack.rs`:

```rust
let stack_is_empty = settings_stack.with(|stack| { /* pop, assert, */ stack.is_empty() });
if !thread::panicking() && stack_is_empty {
    global.perform_a_microtask_checkpoint(cx.as_mut());
}
```

The V8-authoritative classic-script path already routes through that same
`run_a_script` wrapper, so the boundary needs no new plumbing in the HTML
layer — only a V8 drain at the point Servo already drains SpiderMonkey.

For window globals every checkpoint funnels through one function:

    settings_stack::run_a_script
      -> GlobalScope::perform_a_microtask_checkpoint
      -> Window::perform_a_microtask_checkpoint
      -> ScriptThread::perform_a_microtask_checkpoint   <-- integration point

`ScriptThread::perform_a_microtask_checkpoint` is the right place. It is
per-script-thread, which matches the sidecar's isolate; it already gates on
`can_continue_running_inner()`; it lives in the crate that owns the V8 feature
flags and the sidecar `RefCell`; and it is reached from the parser and event
loop task boundaries as well as from step 5. `script_bindings` needs no
changes and gains no dependency on `servo-v8`.

Worker globals reach `WorkerGlobalScope::perform_a_microtask_checkpoint`
instead and stay entirely SpiderMonkey-owned.

## One queue per isolate, not one per realm

The obvious first design — give each realm its own `v8::MicrotaskQueue` via
`Context::New(..., microtask_queue)` and drain per pipeline — is wrong here,
for two reasons found in V8's own headers at the pinned revision.

`include/v8-microtask-queue.h` states the requirement directly:

> Use the same instance of MicrotaskQueue for all Contexts that may access each
> other synchronously.

Servo places same-origin-related pipelines on one script thread precisely
because they can access each other synchronously, and the sidecar creates one
realm per live Window pipeline on that thread. Those realms therefore must
share a queue.

Separately, the non-cppgc `MicrotaskQueue::New` overload is already marked
`V8_DEPRECATE_SOON`, and the surviving cppgc-allocated form is gated behind the
`v8_cppgc_microtask_queue` GN flag. Our `args.gn` does not set it, and enabling
it would interact with the deliberately atomic-only cppgc configuration that
exists because Servo's DOM edge containers issue no mutation barriers.

So the drain stays on the isolate's default queue, which is what the bridge
already configures: `SetMicrotasksPolicy(MicrotasksPolicy::kExplicit)`. One
checkpoint drains jobs from every realm on the script thread, which is what
HTML's single per-event-loop queue wants anyway.

## Host context during the drain

`servo_v8_realm_script_run` takes a `void* host_context` and installs it with
an `ActiveHostContextScope` on that one realm, and the reentry barrier is that
realm's `document_host.active_host_context` already being non-null.

An isolate-wide checkpoint can run a job belonging to any realm, so a single
realm's scope is not enough. The host context is in fact a per-script-thread
value — it is the one `*mut JSContext` for the thread — so the checkpoint entry
point should set it on **every live realm** for the duration of the drain and
clear all of them on exit, including on the throwing path.

The reentry barrier generalises the same way: the checkpoint must fail if *any*
realm already has an active host context, which is exactly the condition that
says a script or an earlier checkpoint is already inside the bridge.

## Proposed C ABI

Requires bumping the exported ABI version from 7 to 8.

```c
/* Drains the isolate's explicit microtask queue with one ephemeral host
 * context installed on every live realm. */
int32_t servo_v8_runtime_perform_microtask_checkpoint(
    ServoV8Runtime* runtime,
    void* host_context,
    ServoV8MicrotaskCheckpointOutcome* outcome,
    ServoV8ErrorBuffer* error);
```

with `_servo_v8_runtime_perform_microtask_checkpoint` added to
`servo_v8.exports`. The outcome reuses `ServoV8ScriptException` so a job that
throws is transported with resource, stack, line, and column exactly as a
throwing script is today.

Open implementation question, to settle against V8 rather than by assumption:
an uncaught exception in a microtask is delivered to the isolate's message
listener rather than to a `TryCatch` at the checkpoint boundary. The bridge
will most likely need a message listener scoped to the drain, not a bare
`TryCatch`.

## Knowing whether to drain at all

The public `MicrotaskQueue` API has no "is the queue empty" query —
`IsRunningMicrotasks` and `GetMicrotasksScopeDepth` answer different questions.
Calling into the bridge on every checkpoint would put sidecar cost on every
SpiderMonkey script on the thread.

So the embedder tracks it: `ScriptThread` sets a `v8_may_have_pending_jobs`
flag when `run_authoritative_classic_script` returns, and clears it after the
drain. No authoritative script has run means no V8 job can exist, and the
checkpoint skips the bridge entirely.

## Execution sequence

1. `run_a_classic_script` enters `run_a_script`, pushing the settings stack.
2. `run_script_in_realm_with_host_context` executes the script. Jobs enqueue
   on the isolate's explicit queue. No checkpoint. The authoritative-entry
   guard is released when the run returns.
3. `ScriptThread` sets `v8_may_have_pending_jobs`.
4. `run_a_script` pops the settings stack. If it is now empty it calls
   `perform_a_microtask_checkpoint`, which reaches `ScriptThread`.
5. `ScriptThread::perform_a_microtask_checkpoint`, *before*
   `self.microtask_queue.checkpoint(...)`:
   a. returns immediately if the sidecar is absent or the flag is clear;
   b. takes the authoritative-entry guard and clears the flag;
   c. calls `servo_v8_runtime_perform_microtask_checkpoint` with `cx`;
   d. reports a thrown job through the existing script-exception path rather
      than panicking;
   e. releases the guard.
6. Servo's existing SpiderMonkey checkpoint runs.
7. Spec steps 4 and 5 — notify about rejected promises, clean up IndexedDB
   transactions — stay where they are, after both drains.

## Why V8 drains first

`MicrotaskQueue::checkpoint` in `components/script/microtask.rs` sets
`performing_a_microtask_checkpoint` and returns early on reentry. Draining V8
first, outside that flag, means a V8 job that enqueues a SpiderMonkey microtask
— today only reachable through a DOM host, for example CEReactions running a
custom element callback — is still serviced by step 6 within the same
checkpoint. Draining V8 second would strand such a job until the next task
boundary, which is observable and wrong.

## Known limitation to carry forward

Two queues drained back to back is not HTML's single FIFO queue. The
interleaving of V8 and SpiderMonkey jobs is not spec-accurate.

Today the difference is unobservable: only authoritative classic scripts can
enqueue V8 jobs, and the host surface is `window`, `document`, and two
`Document` attributes. It becomes observable as soon as the host surface grows
promise-returning APIs or custom-element reactions that queue on both sides.

The eventual fix is one Servo-owned queue with V8 jobs represented as a
`Microtask::V8Job` variant drained in enqueue order, which needs an ABI-level
job handle and an enqueue callback from V8. That is strictly larger than this
change and should not block it.
