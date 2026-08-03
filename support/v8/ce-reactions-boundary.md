# V8-originated custom-element reactions

Status: implemented for the current `Document.bgColor`, `Element.id`,
`Element.className`, and `Node.textContent` mutation surface.

## The mixed-engine hazard

`Document.bgColor`, `Element.id`, `Element.className`, and `Node.textContent`
are `[CEReactions]`. Servo's ordinary SpiderMonkey binding pushes an element
queue, performs the mutation, then pops the queue and invokes the callbacks
before returning to JavaScript. Doing the same inside a V8 host setter runs
SpiderMonkey `attributeChangedCallback` code while V8, the C ABI callback, and
a mutable borrow of the sidecar are all still on the native stack. A callback
that reaches a V8-authoritative API then recursively enters the same isolate
and `RefCell`.

That is page-reachable control flow, so crashing, panicking, or treating it as
an embedding invariant violation is not acceptable.

## Boundary used by the experiment

The V8 host setter performs the native Servo mutation without pushing its own
custom-element reaction queue. Servo's existing reaction machinery therefore
uses whichever outer queue is already active, or its rooted backup element
queue when there is none. The backup path schedules a
`Microtask::CustomElementReaction` on Servo's SpiderMonkey microtask queue.

The resulting sequence is:

1. V8 enters a `Document.bgColor`, Element attribute, or `Node.textContent` setter.
2. Rust mutates the real Servo attribute and enqueues its reaction.
3. Rust and C++ return; the V8 script or V8 microtask finishes.
4. The authoritative-entry guard and sidecar borrow are released.
5. At Servo's existing checkpoint, V8 jobs drain first and Servo's queue drains
   second.
6. The custom-element reaction invokes SpiderMonkey JavaScript with no V8
   frame or host callback live. A callback may now enter a V8-authoritative
   getter normally.

The queue is already part of Servo's traced script-thread state, so elements,
callbacks, and their SpiderMonkey arguments remain rooted while deferred.
Exceptions use Servo's existing custom-element callback reporting path rather
than leaking through the V8 setter's exception channel.

## Remaining ordering limitation

This is safe but not yet fully WebIDL-observable-order equivalent. A native
single-engine `[CEReactions]` wrapper invokes reactions immediately after each
operation. Here they wait until the mixed-engine checkpoint, and V8 promise
jobs drain before the SpiderMonkey reaction queue. A V8 script that combines a
custom-element mutation with promise jobs can therefore observe a different
relative order.

The current surface is explicitly experimental and opt-in. Do not add another
mutating or callback-taking V8 binding without either accepting and testing
that member's ordering or replacing the two engine-owned queues with one
Servo-owned FIFO capable of representing V8 jobs and custom-element reactions.

## Runtime proof

`authoritative_cereactions_proof.html` defines a customized body for `bgcolor`,
an autonomous custom element for `id` and `class`, and a custom child removed
by `textContent`. Every attribute or disconnection callback reads
V8-authoritative `document.hidden`. The proof requires both authoritative
features. It succeeds only when the page renders lime, the body callback fires
twice, both Element attribute callbacks and the child disconnection fire, the
hidden host count includes all five callbacks' real round trips, and no reentry
short-circuit is logged. After building with both features,
`run_cereactions_proof.sh` checks all of those signals together.
