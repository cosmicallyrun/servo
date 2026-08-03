# Per-realm wrapper identity for DOM objects

Status: implemented, at exported C ABI version 19. `Document.documentElement`,
`Document.head`, `Document.getElementById()`, and `Element.tagName` are built on
it.

This is the subsystem every interface-typed binding waits on.
`Document.documentElement`, `Document.head`, `Document.getElementById`,
`Element.tagName`, and eventually anything that hands a DOM node to script all
need the same thing: asking for the same DOM object twice must produce the
*same* JavaScript object, while two different DOM objects must never share one
wrapper.

## What already exists, and why it does not generalise

The synthetic `EngineBindingSmoke` slice creates wrappers the easy way round.
JavaScript calls a constructor, V8 allocates the object first, and only then
does Rust allocate the native behind it:

```
new EngineBindingSmoke(7)
  -> V8 creates info.This()
  -> Rust allocates the native
  -> cppgc::MakeGarbageCollected<ServoV8DomCell>(native, ...)
  -> v8::Object::Wrap<kServoDomTag>(isolate, info.This(), cell)
```

Identity is free there, because the JS object exists before the native does and
there is exactly one of each.

Returning `document.documentElement` is the reverse. Servo already owns the
`Element`; it may already have been handed to script; and a second read must
not mint a second wrapper. So the missing piece is a lookup from an existing
Servo DOM object to a wrapper that may or may not exist yet.

## The cross-heap edge is one-way

Three heaps are involved: SpiderMonkey owns the DOM objects, V8 owns the JS
objects, and cppgc (unified with V8) owns the embedder cells.

A wrapper cell must hold the DOM object alive while script can still reach it,
so the cell holds a strong Servo root — `Trusted<Element>`, exactly as the
existing `Document` host holds `Trusted<Document>`. That is an edge from cppgc
into SpiderMonkey.

There is no edge back. No Servo DOM object holds a V8 handle, a cppgc pointer,
or a cell; the V8 side is reached only through the sidecar, never from the DOM.
Because every cross-heap edge points the same way, **a cross-heap cycle cannot
form**, and the usual reason embedders need an ephemeron/wrapper-tracing
protocol between the two collectors does not arise here.

That matters because an earlier audit recorded the opposite — that a V8-held
`Element` would create a cycle neither collector could break — and treated it
as a blocker. The direction of the edges is what settles it.

Collection then works out on its own terms:

- Script drops its last reference to the wrapper. V8 collects the wrapper,
  cppgc collects the cell, the cell's destructor drops the `Trusted<Element>`,
  and SpiderMonkey is free to collect the element.
- Servo detaches the element from the tree, but script still holds the wrapper.
  The element stays alive, which is correct: script can still reach it.

## Identity, and why the DOM object's address is a safe key

The cache lives on the V8 side, per realm, because Servo's DOM objects have one
reflector slot and it belongs to SpiderMonkey. A realm therefore holds a map
from the DOM object to a weak handle on its wrapper.

Keying that map on the DOM object's raw address looks unsafe, and the first
instinct is to invent an id instead: an address is only unique while the object
is alive, so a freed element could be replaced by a new allocation at the same
address and collide with a stale entry. That reasoning is correct in general
and does not apply here, because of what the cell holds:

> A cache entry can only be hit while its cell is alive. The cell holds
> `Trusted<Element>`. So while an entry is reachable, its element is alive, and
> its address cannot have been reused.

The hazard needs a live entry pointing at a dead object, and the strong root
makes that state unreachable. An id would add a second source of truth to keep
in sync for a collision the design already prevents.

Entries are `cppgc::WeakPersistent`, so an entry clears itself when its cell
dies rather than pinning wrappers for the life of the realm.

One ordering subtlety is worth stating because it looks like a bug. cppgc
clears weak references during marking, while destructors run later during
sweeping, so there is a window where the entry reads as empty but the cell and
its element are still alive. A lookup in that window misses and mints a second
wrapper for a still-live element — identity apparently broken.

It is not observable. Weak clearing only happens once the cell is unreachable
from V8, which means no JavaScript reference to the old wrapper survives; there
is nothing left for script to compare the new wrapper against. Identity is only
required to hold for wrappers script can still reach, and for those the entry
is still strong.

## Ownership across the ABI

An interface-typed getter hands back the DOM object's address as a cache key
together with a freshly boxed host, allocated before anyone knows whether it
will be needed.

- Cache hit: the bridge returns the existing wrapper and drops the surplus host
  through its drop callback, so ownership never straddles the two outcomes and
  nothing leaks on the path that allocated speculatively. That callback runs
  beneath the same C++ re-entry scope as an installed host's destructor; a
  hostile `Drop` therefore cannot enter V8 beneath the live accessor. Wrapper
  allocation failure uses the same guarded release path.
- Cache miss: the bridge allocates a cell, wraps a new object from the realm's
  `Element` template, and records the entry.

Allocating a host that may immediately be dropped is the price of resolving the
cache on the side that owns it; the alternative is a second round trip to ask
whether a wrapper exists before building one.

A nullable return needs no extra machinery: the null flag becomes JavaScript
`null`, which is what `documentElement`, `head`, and an unsuccessful
`getElementById` yield.

## Why not simply hold every wrapper strongly

A per-realm map of `v8::Global<v8::Object>` would give correct identity in a
dozen lines, and it is what a first attempt reaches for. It is rejected because
the lifetime it implies is wrong in a way that only shows up under load: every
element ever handed to script would be pinned until its pipeline is destroyed,
so a long-lived page that walks the DOM would accumulate wrappers and the Servo
elements behind them without bound. Weak entries are the difference between a
demo and something that can survive a real page.

It would also reintroduce the address-reuse hazard from the other direction, by
keeping entries alive past the point where anything guarantees their key still
identifies the object it was created for.

## Where the generator fits

The manifest carries `Document.documentElement`, `Document.head`, and
`Document.getElementById` like any other members and pins their exact declared
interfaces (`Element`, `HTMLHeadElement`, and `Element`). The generator emits
their ABI slots, Rust trait methods, thunks, checked C++ callbacks, and
prototype registration. Attributes use the `readonly nullable interface`
shape; the operation additionally pins one required DOMString argument and
`[Pure]`. The current wrapper deliberately exposes only inherited `Element`
behavior, but WebIDL drift in any declared return type is still a build failure
rather than silent type erasure.

The wrapper cache, cell, and per-realm `Element` template remain hand-written
infrastructure in `bridge.cc`; member-specific code no longer lives there.

## What this does not do

Nothing here gives V8 the ability to *mutate* the DOM, and nothing here accepts
a JavaScript function as a callback. Those are separate problems: the first
needs the CEReactions boundary moved out of the accessor, and the second needs a
story for a V8 function held by a Servo event target, which reverses the edge
direction this design depends on and so has to be reasoned about again from
scratch.

## The constraint this design depends on

No object in the SpiderMonkey heap may ever hold a V8 handle, a cppgc pointer,
or anything that keeps a wrapper cell alive. That, and not the strong root the
cells hold, is what would create a cycle neither collector can see through.

The tempting design that breaks it is the conventional one: a wrapper slot on
the DOM object itself, which is how a single-engine embedder normally gets
identity. Here that would close the loop — `Element` → wrapper → cell →
`Trusted<Element>` — so identity has to live in a side table instead, which is
what the per-realm cache is.

## Teardown must not wait for a collection

Realm destruction releases every host synchronously rather than letting the
cells' destructors do it whenever the next collection happens. Each host roots
its element and, through it, the tree; leaving that to a GC would pin a
destroyed pipeline's DOM for as long as the isolate stayed idle, which for a
background tab may be indefinitely. Servo already depends on the document
host's release being synchronous for the same reason, and the wrapper cells now
match it.

The cells themselves stay cppgc-owned and die on their own schedule. Only the
Servo roots are released early, which is the part with an observable cost.

## Releasing a host from a collection

A cell's destructor runs during sweeping and drops a host holding non-atomic
Rust state. Two properties make that sound, and both are worth naming because
they are configuration rather than luck:

- cppgc marking and sweeping are atomic, so destructors run in the pause on the
  owner thread. A concurrent sweeper would be dropping an `Rc` off-thread.
- Dropping a `Trusted<T>` performs no SpiderMonkey call and no allocation. It
  decrements a refcount and makes the object *eligible* for collection at the
  next SpiderMonkey GC; nothing is freed inside the V8 pause.

Every element-host drop performed by the bridge is wrapped in the same
re-entrancy scope every other Rust callback uses, including speculative hosts
discarded on wrapper-cache hits or allocation failure. Without it the runtime
check would *accept* a bridge call made from a host's `Drop`, which during
sweeping means re-entering V8 mid-collection and during a cache hit means
re-entering beneath an accessor.

## Dead entries are pruned after major GC

cppgc clears `WeakPersistent` handles during major-GC weakness processing.
This runtime requires atomic sweeping, so V8 finishes every `HostCell`
destructor before invoking the public GC epilogue callbacks. The runtime
registers one major-GC epilogue callback that walks every live realm and erases
only cleared cache entries. V8 prohibits JavaScript execution in the callback
but explicitly permits allocation; destroying same-thread weak handles and
unordered-map nodes therefore fits the callback contract.

The pass is linear in the current cache size, but cppgc has just walked the
same weak persistent region. Paying that cost once per major collection keeps
future marking work and map memory proportional to the live wrapper set rather
than to every element a long-lived realm has ever exposed. Realm teardown
still clears the cache synchronously and releases live Servo hosts first.

## Proofs

`authoritative_wrapper_identity_proof.html` and
`authoritative_get_element_by_id_proof.html` cover runtime behaviour against
real Servo DOM, and `interface_returns_preserve_wrapper_identity` covers the
bridge:

- the same DOM object read twice through V8 is the same JS object, checked by
  an expando surviving a re-read rather than by equality alone
- `documentElement`, `head`, and the id-selected `DIV` are distinct wrappers
  with independent expandos
- the wrapper is not the document facade, and `tagName` is brand-checked
- a cache hit drops the host the reading path speculatively allocated
- a hostile surplus-host `Drop` is rejected before it can re-enter V8
- realm destruction releases all three live hosts synchronously
- nullable interface attributes and an unsuccessful operation produce `null`
- a major GC retains a reachable wrapper entry, prunes it after the wrapper
  becomes unreachable, and permits the same DOM address to be wrapped again
