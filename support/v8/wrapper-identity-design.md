# Per-realm wrapper identity for DOM objects

Status: implemented, initially at exported C ABI version 19 and extended with a
shared Element prototype at ABI version 22 and its inherited Node prototype at
ABI version 23. ABI version 24 adds ParentNode Element traversal.
ABI version 27 adds static querySelectorAll NodeLists whose items converge on
the same Element cache while each collection wrapper remains `[NewObject]`.
ABI version 28 adds `[SameObject]` live `children` HTMLCollections. ABI version
29 adds fresh live `getElementsByClassName` HTMLCollections whose per-call keys
keep different filters separate. ABI version 30 adds `Element.remove`, whose
retained Element wrapper can leave the tree while remaining the same rooted
object in pre-existing static NodeLists. ABI version 31 adds
`Element.previousElementSibling` and `Element.nextElementSibling`, whose
nullable results converge on the same wrapper cache and update after removal.
ABI version 32 adds nullable string `Element.namespaceURI` and `Element.prefix`
accessors; they introduce no cross-heap return value or wrapper-cache entry.
ABI version 33 adds inherited `Node.parentElement` on Element wrappers; a
non-null parent converges through the existing Element wrapper cache and a
removed child returns null without creating a cache entry.
ABI version 34 adds `Element.getAttributeNS` and `Element.hasAttributeNS`.
Their scalar nullable-string/boolean results use no wrapper-cache entry.
ABI version 35 adds `Element.getAttributeNames`, whose fresh scalar string
sequence snapshots use no wrapper-cache entry.
ABI version 36 adds fresh live `Document.getElementsByTagName` and
`Element.getElementsByTagName` HTMLCollections, reusing the per-call collection
identity and Element item cache introduced at ABI version 29.
ABI version 40 adds the namespace/local-name variants for both receivers; they
reuse the same collection lifetime and item cache with a distinct exact-match
filter.
`Document.documentElement`, `Document.head`, `Document.getElementById()`, the
Element/Node scalar slices, Element removal, and ParentNode traversal are built
on it.

This is the subsystem every interface-typed binding waits on.
`Document.documentElement`, `Document.head`, `Document.getElementById`, the
Element and Node slices, ParentNode traversal, and eventually anything that
hands a DOM node to script all need the same thing: asking for the same DOM
object twice must produce the *same* JavaScript object, while two different DOM
objects must never share one wrapper.

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
`null`, which is what `documentElement`, `head`, empty first/last child getters,
and an unsuccessful `getElementById` yield.

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

The manifest carries `Document.documentElement`, `Document.head`, the
ParentNode children/first/last/count getters, `Document.getElementById`, and
`Document.getElementsByClassName`, `Document.getElementsByTagName`, and
`Document.getElementsByTagNameNS` like any other members, while separately
gating the exact matching Element declarations.
It pins their
declared interfaces (`Element`, `HTMLHeadElement`, and `HTMLCollection`). The
Document generator emits its ABI slot, Rust trait method, thunk, checked C++
callback, and prototype registration; the parallel Element path uses the
hand-written per-wrapper host table after passing the same WebIDL gate.
Interface attributes use the `readonly nullable interface` shape; `children`
uses an exact `[SameObject]` non-nullable `HTMLCollection` shape; ParentNode's
two interface getters additionally require exact `[Pure]`, its count uses a
32-bit unsigned shape, and `getElementById` pins one required DOMString argument
and `[Pure]`. The class-name and qualified tag-name operations pin one required
DOMString argument; namespace tag-name operations pin a nullable DOMString
namespace followed by one required DOMString local name. All return a
non-nullable `HTMLCollection` and carry no extended attributes.
WebIDL drift
in any declared return type remains a build failure rather than silent type
erasure.

The wrapper cache, cell, and per-realm `Element` template remain hand-written
infrastructure in `bridge.cc`; member-specific code no longer lives there.

`querySelectorAll` collections deliberately do not enter the identity map:
WebIDL marks every result `[NewObject]`, so two calls must produce different
NodeList wrappers even when their snapshots contain the same elements. They do
reuse the same cell type with an explicit host-kind brand. Each realm keeps
weak handles to these uncached cells solely so teardown can release their
static `Trusted<Element>` vectors synchronously; the major-GC epilogue prunes
cleared collection handles alongside cleared Element-cache entries. Calling
`item()` or reading an indexed value still routes the returned Element through
the ordinary cache, so collection and direct query paths preserve item
identity.

`children` has the opposite collection lifetime. Its `HTMLCollection` is live
and `[SameObject]`, so each realm keeps a second weak cache keyed by the
ParentNode owner. It cannot share the Element map: an Element and that
Element's children collection intentionally use the same owner address while
representing different JavaScript objects. The collection cell holds a
`Trusted<Node>` and rereads direct child elements, ids, and HTML names on every
callback. Cache hits discard the speculative host; major-GC pruning and realm
teardown walk both maps and release both kinds of roots.

`Document.getElementsByClassName`, `Element.getElementsByClassName`, and the
qualified-name and namespace/local-name `getElementsByTagName` operations are
live but not `[SameObject]`. This
implementation returns a fresh collection wrapper for every call, which the
DOM Standard permits, and uses the collection host's own native address as a
unique cache key. Keying only on the receiver would incorrectly alias
`children`, different filters, and repeated calls. The cell keeps that host
alive for as long as its weak wrapper entry can be hit; if an address is later
reused, the old weak entry is already cleared and lookup erases it before
installing the new wrapper. Items still enter the ordinary Element cache, so
collection access, selectors, and `getElementById` converge on one wrapper for
each underlying Element.

ABI v40's namespace filter stores the normalized namespace and local name on
that per-call host. Collection reads share Servo's production predicate:
namespace `*` and local-name `*` are independent wildcards, null and the empty
string denote the empty namespace, and all non-wildcard comparisons are exact
and case-sensitive. Keeping this predicate in the production
`HTMLCollection` module prevents the V8 facade from drifting on SVG, MathML,
custom namespaces, or HTML case behavior.

ABI v41's `Element.removeAttributeNS` reuses the existing Element host and
wrapper identity. Its synchronous mutation callback neither creates a wrapper
nor changes either cache; Servo owns the attribute and custom-element reaction
lifetime after the callback returns.

## Borrowed Node mutation inputs

ABI v37 adds structural mutation without adding a Servo-to-V8 edge. The
Element wrapper cells already own `Trusted<Element>` roots; after C++ validates
an argument as an Element-backed Node from the receiver's realm, it lends the
installed native host to Rust for exactly one synchronous callback. Rust roots
the receiver and every argument locally, calls Servo's production Node
algorithm, and never retains or drops the borrowed input. The JavaScript locals
and cppgc cells keep those native hosts alive until the callback returns.

The returned Node receives a freshly owned Element host and passes through the
ordinary wrapper cache. A cache hit drops that speculative host and returns the
existing JavaScript object, so `parent.appendChild(child) === child` without a
new cross-heap reference. `HierarchyRequestError` and `NotFoundError` cross as
typed POD plus one owned UTF-8 message; C++ releases the message on every
success, failure, and malformed-result path and constructs the exception in
the calling V8 realm.

ABI v42 extends that slice to DocumentFragment through one concrete generic
Node host. It owns `Trusted<Node>` and stores an explicit dynamic interface
kind (`Element` or `DocumentFragment`). The wrapper cache key is the rooted
Node allocation address, and every hit must also match the kind stored in its
cell; a mismatch is malformed and the speculative host is dropped. The key no
longer depends on a per-interface Rust host type. This means every mixed Node
mutation argument has the exact same installed Rust `T` and therefore cannot
be cast through a wrong monomorphized vtable. C++ checks an Element receiver's
`kElement` brand before dispatching Element-only callbacks; Rust then performs
a checked downcast, never an unchecked cast. Common Node callbacks use the
rooted Node directly.

`Document.createDocumentFragment` creates a fresh boxed
DocumentFragment-kind host for each Servo allocation. A cache hit still drops
the speculative host and returns the pre-existing wrapper, while separate
allocations necessarily remain separate wrappers. When a fragment is appended
to an Element, Servo's production algorithm splices its children, returns the
fragment's existing wrapper, and leaves it empty. Node creation and event
listeners beyond this Element/DocumentFragment slice remain separate problems:
a V8 function held by a Servo event target reverses the edge direction this
design depends on and must be reasoned about again from scratch.

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

`authoritative_wrapper_identity_proof.html`,
`authoritative_get_element_by_id_proof.html`, and
`authoritative_element_scalar_proof.html`,
`authoritative_node_scalar_proof.html`, and
`authoritative_parent_node_proof.html`, and
`authoritative_query_selector_all_proof.html`, and
`authoritative_children_collection_proof.html`, and
`authoritative_get_elements_by_class_name_proof.html`, and
`authoritative_get_elements_by_tag_name_proof.html`, and
`authoritative_get_elements_by_tag_name_ns_proof.html`, and
`authoritative_element_remove_proof.html`, and
`authoritative_element_sibling_proof.html`, and
`authoritative_element_namespace_proof.html`, and
`authoritative_parent_element_proof.html`, and
`authoritative_attribute_namespace_proof.html`, and
`authoritative_attribute_names_proof.html`, and
`authoritative_node_mutation_proof.html` cover runtime behaviour
against real Servo DOM, and `interface_returns_preserve_wrapper_identity`
covers the bridge:

- the same DOM object read twice through V8 is the same JS object, checked by
  an expando surviving a re-read rather than by equality alone
- `documentElement`, `head`, the id-selected `DIV`, and its two Element children
  are distinct wrappers with independent expandos
- the wrapper is not the document facade, and members live on one shared,
  brand-checked Element prototype rather than being copied onto each wrapper
- a cache hit drops the host the reading path speculatively allocated
- a hostile surplus-host `Drop` is rejected before it can re-enter V8
- realm destruction releases all five live hosts synchronously
- nullable interface attributes and an unsuccessful operation produce `null`
- a major GC retains a reachable wrapper entry, prunes it after the wrapper
  becomes unreachable, and permits the same DOM address to be wrapped again
