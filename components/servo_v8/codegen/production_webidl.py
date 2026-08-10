# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Select narrowly supported members from Servo's production WebIDL corpus."""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import NamedTuple


SCRIPT_PATH = Path(__file__).resolve()
SERVO_ROOT = SCRIPT_PATH.parents[3]
SCRIPT_BINDINGS_ROOT = SERVO_ROOT / "components" / "script_bindings"
PRODUCTION_WEBIDLS_DIR = SCRIPT_BINDINGS_ROOT / "webidls"
sys.path[:0] = [
    str(SCRIPT_BINDINGS_ROOT / "third_party" / "WebIDL" / "parser"),
    str(SCRIPT_BINDINGS_ROOT / "third_party" / "ply"),
]

import WebIDL  # noqa: E402


SKIP_UNLESS_PATTERN = re.compile(r"// skip-unless ([A-Z_]+)\n")
DOCUMENT_HIDDEN = "Document.hidden"
DOCUMENT_BG_COLOR = "Document.bgColor"
DOCUMENT_URL = "Document.URL"
DOCUMENT_URI = "Document.documentURI"
DOCUMENT_COMPAT_MODE = "Document.compatMode"
DOCUMENT_CHARACTER_SET = "Document.characterSet"
DOCUMENT_CHARSET = "Document.charset"
DOCUMENT_INPUT_ENCODING = "Document.inputEncoding"
DOCUMENT_CONTENT_TYPE = "Document.contentType"
DOCUMENT_REFERRER = "Document.referrer"
DOCUMENT_LAST_MODIFIED = "Document.lastModified"
DOCUMENT_VISIBILITY_STATE = "Document.visibilityState"
DOCUMENT_READY_STATE = "Document.readyState"
DOCUMENT_TITLE = "Document.title"
NODE_NODE_TYPE = "Node.nodeType"
NODE_NODE_NAME = "Node.nodeName"
NODE_IS_CONNECTED = "Node.isConnected"
NODE_PARENT_ELEMENT = "Node.parentElement"
NODE_TEXT_CONTENT = "Node.textContent"
NODE_HAS_CHILD_NODES = "Node.hasChildNodes"
DOCUMENT_DOCUMENT_ELEMENT = "Document.documentElement"
DOCUMENT_GET_ELEMENTS_BY_CLASS_NAME = "Document.getElementsByClassName"
DOCUMENT_HEAD = "Document.head"
DOCUMENT_CHILDREN = "Document.children"
DOCUMENT_FIRST_ELEMENT_CHILD = "Document.firstElementChild"
DOCUMENT_LAST_ELEMENT_CHILD = "Document.lastElementChild"
DOCUMENT_CHILD_ELEMENT_COUNT = "Document.childElementCount"
DOCUMENT_GET_ELEMENT_BY_ID = "Document.getElementById"
DOCUMENT_QUERY_SELECTOR = "Document.querySelector"
DOCUMENT_QUERY_SELECTOR_ALL = "Document.querySelectorAll"
WINDOW_OR_WORKER_SET_TIMEOUT = "WindowOrWorkerGlobalScope.setTimeout"
WINDOW_OR_WORKER_CLEAR_TIMEOUT = "WindowOrWorkerGlobalScope.clearTimeout"
WINDOW_OR_WORKER_SET_INTERVAL = "WindowOrWorkerGlobalScope.setInterval"
WINDOW_OR_WORKER_CLEAR_INTERVAL = "WindowOrWorkerGlobalScope.clearInterval"
CONSOLE_DEBUG = "console.debug"
CONSOLE_ERROR = "console.error"
CONSOLE_INFO = "console.info"
CONSOLE_LOG = "console.log"
CONSOLE_TRACE = "console.trace"
CONSOLE_WARN = "console.warn"
ELEMENT_LOCAL_NAME = "Element.localName"
ELEMENT_TAG_NAME = "Element.tagName"
ELEMENT_ID = "Element.id"
ELEMENT_CLASS_NAME = "Element.className"
ELEMENT_HAS_ATTRIBUTES = "Element.hasAttributes"
ELEMENT_GET_ATTRIBUTE = "Element.getAttribute"
ELEMENT_HAS_ATTRIBUTE = "Element.hasAttribute"
ELEMENT_CHILDREN = "Element.children"
ELEMENT_NAMESPACE_URI = "Element.namespaceURI"
ELEMENT_PREFIX = "Element.prefix"
ELEMENT_FIRST_ELEMENT_CHILD = "Element.firstElementChild"
ELEMENT_LAST_ELEMENT_CHILD = "Element.lastElementChild"
ELEMENT_CHILD_ELEMENT_COUNT = "Element.childElementCount"
ELEMENT_PREVIOUS_ELEMENT_SIBLING = "Element.previousElementSibling"
ELEMENT_NEXT_ELEMENT_SIBLING = "Element.nextElementSibling"
ELEMENT_QUERY_SELECTOR = "Element.querySelector"
ELEMENT_CLOSEST = "Element.closest"
ELEMENT_MATCHES = "Element.matches"
ELEMENT_WEBKIT_MATCHES_SELECTOR = "Element.webkitMatchesSelector"
ELEMENT_GET_ELEMENTS_BY_CLASS_NAME = "Element.getElementsByClassName"
ELEMENT_QUERY_SELECTOR_ALL = "Element.querySelectorAll"
ELEMENT_REMOVE = "Element.remove"
HTML_COLLECTION_INTERFACE = "HTMLCollection"
HTML_COLLECTION_LENGTH = "HTMLCollection.length"
HTML_COLLECTION_ITEM = "HTMLCollection.item"
HTML_COLLECTION_NAMED_ITEM = "HTMLCollection.namedItem"

# Member shapes the generator knows how to emit. A shape names both the WebIDL
# form a selector accepts and the emitters that understand it, so a new member is
# supported by naming an existing shape rather than by touching the emitters.
READONLY_BOOLEAN = "readonly boolean"
WRITABLE_LEGACY_DOMSTRING = "CEReactions writable LegacyNullToEmptyString DOMString"
WRITABLE_DOMSTRING = "CEReactions writable DOMString"
READONLY_DOMSTRING = "readonly DOMString"
READONLY_USVSTRING = "readonly USVString"
READONLY_ENUM = "readonly enum"
READONLY_UNSIGNED_SHORT = "readonly unsigned short"
READONLY_UNSIGNED_LONG = "readonly unsigned long"
READONLY_NULLABLE_INTERFACE = "readonly nullable interface"
PURE_READONLY_NULLABLE_INTERFACE = "Pure readonly nullable interface"
SAMEOBJECT_READONLY_INTERFACE = "SameObject readonly interface"
DOMSTRING_TO_NONNULLABLE_INTERFACE = "operation DOMString -> non-nullable interface"
PURE_DOMSTRING_TO_NULLABLE_INTERFACE = "Pure operation DOMString -> nullable interface"
PURE_THROWS_DOMSTRING_TO_NULLABLE_INTERFACE = (
    "Pure Throws operation DOMString -> nullable interface"
)
NEWOBJECT_THROWS_DOMSTRING_TO_INTERFACE = (
    "NewObject Throws operation DOMString -> interface"
)

# Extended attributes change conversion, reaction, and lifetime semantics that
# the generated glue implements literally, so an unlisted one is silently wrong
# rather than merely unsupported. Each selector therefore allows exactly what its
# emitters honour and rejects the rest, as `generate.py` does for its own members.
READONLY_BOOLEAN_EXTENDED_ATTRIBUTES = frozenset()
WRITABLE_LEGACY_DOMSTRING_EXTENDED_ATTRIBUTES = frozenset({"CEReactions"})
WRITABLE_DOMSTRING_EXTENDED_ATTRIBUTES = frozenset({"CEReactions"})
# `[Constant]` is a SpiderMonkey JIT alias-set hint with no bearing on the value
# produced, and the V8 accessor is already installed with `kHasNoSideEffect`, so
# honouring it costs nothing. It is allowed rather than required so that dropping
# it upstream does not break the build.
READONLY_USVSTRING_EXTENDED_ATTRIBUTES = frozenset({"Constant"})
READONLY_DOMSTRING_EXTENDED_ATTRIBUTES = frozenset({"Constant"})
READONLY_ENUM_EXTENDED_ATTRIBUTES = frozenset()
READONLY_UNSIGNED_SHORT_EXTENDED_ATTRIBUTES = frozenset({"Constant"})
READONLY_UNSIGNED_LONG_EXTENDED_ATTRIBUTES = frozenset({"Pure"})
# `[Pure]` is a SpiderMonkey alias-set hint, like `[Constant]` but weaker, and
# says nothing the V8 accessor needs to honour.
READONLY_NULLABLE_INTERFACE_EXTENDED_ATTRIBUTES = frozenset({"Pure"})
# This operation requires `[Pure]` and maps it to V8's no-side-effect callback
# classification; accepting it is therefore part of the generated semantics.
PURE_DOMSTRING_TO_NULLABLE_INTERFACE_EXTENDED_ATTRIBUTES = frozenset({"Pure"})
PURE_THROWS_DOMSTRING_TO_NULLABLE_INTERFACE_EXTENDED_ATTRIBUTES = frozenset(
    {"Pure", "Throws"}
)
PURE_THROWS_DOMSTRING_TO_BOOLEAN_EXTENDED_ATTRIBUTES = frozenset(
    {"Pure", "Throws"}
)
NEWOBJECT_THROWS_DOMSTRING_TO_INTERFACE_EXTENDED_ATTRIBUTES = frozenset(
    {"NewObject", "Throws"}
)
SAMEOBJECT_READONLY_INTERFACE_EXTENDED_ATTRIBUTES = frozenset({"SameObject"})

# An enum crosses the ABI as its string value, so the generated glue is only
# correct for the exact value set it was written against. Pinning the set makes
# a new state upstream a build failure rather than an unvalidated string.
DOCUMENT_VISIBILITY_STATE_VALUES = ("visible", "hidden")
DOCUMENT_READY_STATE_VALUES = ("loading", "interactive", "complete")


class DocumentHostMember(NamedTuple):
    qualified_name: str
    shape: str
    expected_interface: str | None = None
    expected_enum_values: tuple[str, ...] | None = None


# The supported Document slice is data: selection and generation both walk it,
# so widening the slice with a known shape is an edit to this tuple alone. An
# interface-valued member also pins its exact declared return interface even
# though the current V8 facade intentionally exposes only inherited Element
# behavior.
DOCUMENT_HOST: tuple[DocumentHostMember, ...] = (
    DocumentHostMember(DOCUMENT_HIDDEN, READONLY_BOOLEAN),
    DocumentHostMember(DOCUMENT_BG_COLOR, WRITABLE_LEGACY_DOMSTRING),
    DocumentHostMember(DOCUMENT_URL, READONLY_USVSTRING),
    DocumentHostMember(DOCUMENT_URI, READONLY_USVSTRING),
    DocumentHostMember(DOCUMENT_COMPAT_MODE, READONLY_DOMSTRING),
    DocumentHostMember(DOCUMENT_CHARACTER_SET, READONLY_DOMSTRING),
    DocumentHostMember(DOCUMENT_CHARSET, READONLY_DOMSTRING),
    DocumentHostMember(DOCUMENT_INPUT_ENCODING, READONLY_DOMSTRING),
    DocumentHostMember(DOCUMENT_CONTENT_TYPE, READONLY_DOMSTRING),
    DocumentHostMember(DOCUMENT_REFERRER, READONLY_DOMSTRING),
    DocumentHostMember(DOCUMENT_LAST_MODIFIED, READONLY_DOMSTRING),
    DocumentHostMember(
        DOCUMENT_VISIBILITY_STATE,
        READONLY_ENUM,
        expected_enum_values=DOCUMENT_VISIBILITY_STATE_VALUES,
    ),
    DocumentHostMember(
        DOCUMENT_READY_STATE,
        READONLY_ENUM,
        expected_enum_values=DOCUMENT_READY_STATE_VALUES,
    ),
    DocumentHostMember(DOCUMENT_TITLE, WRITABLE_DOMSTRING),
    # Document inherits from Node, so this lands on the existing document
    # facade: no second host, no second native pointer, no second vtable.
    DocumentHostMember(NODE_NODE_TYPE, READONLY_UNSIGNED_SHORT),
    # The first member whose value is another DOM object, and so the first that
    # needs the per-realm wrapper cache to preserve identity.
    DocumentHostMember(
        DOCUMENT_DOCUMENT_ELEMENT,
        READONLY_NULLABLE_INTERFACE,
        "Element",
    ),
    # This live descendant query transfers an HTMLCollection host without a
    # SpiderMonkey context or exception channel. The operation itself carries
    # no extended attributes in production WebIDL.
    DocumentHostMember(
        DOCUMENT_GET_ELEMENTS_BY_CLASS_NAME,
        DOMSTRING_TO_NONNULLABLE_INTERFACE,
        "HTMLCollection",
    ),
    # A second identity exercises multiple wrapper-cache entries in one realm.
    # HTMLHeadElement is exposed through the current inherited Element facade.
    DocumentHostMember(DOCUMENT_HEAD, READONLY_NULLABLE_INTERFACE, "HTMLHeadElement"),
    DocumentHostMember(
        DOCUMENT_CHILDREN,
        SAMEOBJECT_READONLY_INTERFACE,
        "HTMLCollection",
    ),
    # ParentNode mixin reads reuse the same stable Element wrappers. The parser
    # resolves included mixins onto Document, so these also prove that the
    # production inclusion remains enabled rather than pinning a detached copy.
    DocumentHostMember(
        DOCUMENT_FIRST_ELEMENT_CHILD,
        PURE_READONLY_NULLABLE_INTERFACE,
        "Element",
    ),
    DocumentHostMember(
        DOCUMENT_LAST_ELEMENT_CHILD,
        PURE_READONLY_NULLABLE_INTERFACE,
        "Element",
    ),
    DocumentHostMember(DOCUMENT_CHILD_ELEMENT_COUNT, READONLY_UNSIGNED_LONG),
    # The first operation exercises argument conversion and the ephemeral
    # SpiderMonkey JSContext needed by Servo's production DOM implementation.
    DocumentHostMember(
        DOCUMENT_GET_ELEMENT_BY_ID,
        PURE_DOMSTRING_TO_NULLABLE_INTERFACE,
        "Element",
    ),
    # ParentNode.querySelector is the first throwing operation. Its native ABI
    # distinguishes a null match from Servo's selector SyntaxError so V8 can
    # create the exception without leaving SpiderMonkey exception state set.
    DocumentHostMember(
        DOCUMENT_QUERY_SELECTOR,
        PURE_THROWS_DOMSTRING_TO_NULLABLE_INTERFACE,
        "Element",
    ),
    # querySelectorAll returns a new static NodeList whose native V8 host owns
    # the matched Servo roots directly; no SpiderMonkey wrapper crosses heaps.
    DocumentHostMember(
        DOCUMENT_QUERY_SELECTOR_ALL,
        NEWOBJECT_THROWS_DOMSTRING_TO_INTERFACE,
        "NodeList",
    ),
)

# These operations are installed by a separate per-realm timer host rather
# than the Document host generator. Keeping the exact production declarations
# here still gives the hand-written native bridge the same fail-closed WebIDL
# gate as generated Document members.
TIMER_HOST = (
    WINDOW_OR_WORKER_SET_TIMEOUT,
    WINDOW_OR_WORKER_CLEAR_TIMEOUT,
    WINDOW_OR_WORKER_SET_INTERVAL,
    WINDOW_OR_WORKER_CLEAR_INTERVAL,
)

# These namespace operations share one exact WebIDL shape. The V8 facade
# deliberately exposes only this logging slice: every other console method is
# absent rather than silently using V8's no-op delegate.
CONSOLE_HOST = (
    CONSOLE_DEBUG,
    CONSOLE_ERROR,
    CONSOLE_INFO,
    CONSOLE_LOG,
    CONSOLE_TRACE,
    CONSOLE_WARN,
)

# The Element surface implemented by the per-object wrapper host. ParentNode's
# child getters can return another Element wrapper, but every cross-heap edge
# still points from cppgc into a fresh Servo root; no Servo object retains V8.
ELEMENT_HOST = (
    ELEMENT_LOCAL_NAME,
    ELEMENT_TAG_NAME,
    ELEMENT_ID,
    ELEMENT_CLASS_NAME,
    ELEMENT_HAS_ATTRIBUTES,
    ELEMENT_GET_ATTRIBUTE,
    ELEMENT_HAS_ATTRIBUTE,
    ELEMENT_CHILDREN,
    ELEMENT_NAMESPACE_URI,
    ELEMENT_PREFIX,
    ELEMENT_FIRST_ELEMENT_CHILD,
    ELEMENT_LAST_ELEMENT_CHILD,
    ELEMENT_CHILD_ELEMENT_COUNT,
    ELEMENT_PREVIOUS_ELEMENT_SIBLING,
    ELEMENT_NEXT_ELEMENT_SIBLING,
    ELEMENT_QUERY_SELECTOR,
    ELEMENT_CLOSEST,
    ELEMENT_MATCHES,
    ELEMENT_WEBKIT_MATCHES_SELECTOR,
    ELEMENT_GET_ELEMENTS_BY_CLASS_NAME,
    ELEMENT_QUERY_SELECTOR_ALL,
    ELEMENT_REMOVE,
)

# Inherited behavior installed on the Node prototype shared by Element
# wrappers. The scalar members introduce no new cross-heap edge or interface
# input; parentElement reuses the existing nullable Element return path.
NODE_HOST = (
    NODE_NODE_TYPE,
    NODE_NODE_NAME,
    NODE_IS_CONNECTED,
    NODE_PARENT_ELEMENT,
    NODE_TEXT_CONTENT,
    NODE_HAS_CHILD_NODES,
)


class WebIDLSelectionError(RuntimeError):
    """Raised when a selected production member is absent or changes shape."""


def parse_webidl_corpus(
    webidls_dir: Path,
    cache_dir: Path,
    environment: Mapping[str, str] | None = None,
) -> list[WebIDL.IDLObjectWithIdentifier]:
    """Parse and merge every enabled ``.webidl`` file in a directory."""

    environment = os.environ if environment is None else environment
    parser = WebIDL.Parser(str(cache_dir))
    webidl_paths = sorted(webidls_dir.glob("*.webidl"))
    if not webidl_paths:
        raise WebIDLSelectionError(f"no WebIDL files found in `{webidls_dir}`")

    for webidl_path in webidl_paths:
        source = webidl_path.read_text(encoding="utf-8")
        filter_match = SKIP_UNLESS_PATTERN.search(source)
        if filter_match and not environment.get(filter_match.group(1)):
            continue
        parser.parse(source, str(webidl_path))

    return parser.finish()


def select_readonly_boolean_attribute(
    parser_results: Sequence[WebIDL.IDLObjectWithIdentifier],
    qualified_name: str,
) -> WebIDL.IDLAttribute:
    """Select one ordinary readonly, non-nullable boolean attribute."""

    member = _select_instance_attribute(
        parser_results,
        qualified_name,
        READONLY_BOOLEAN_EXTENDED_ATTRIBUTES,
    )
    if not member.readonly:
        raise WebIDLSelectionError(f"`{qualified_name}` must be readonly")
    if member.type.nullable():
        raise WebIDLSelectionError(f"`{qualified_name}` must be non-nullable")
    if not member.type.isBoolean():
        raise WebIDLSelectionError(f"`{qualified_name}` must use `boolean`, got `{member.type.prettyName()}`")

    return member


def select_writable_legacy_domstring_attribute(
    parser_results: Sequence[WebIDL.IDLObjectWithIdentifier],
    qualified_name: str,
) -> WebIDL.IDLAttribute:
    """Select one CEReactions writable LegacyNullToEmptyString DOMString."""

    member = _select_instance_attribute(
        parser_results,
        qualified_name,
        WRITABLE_LEGACY_DOMSTRING_EXTENDED_ATTRIBUTES,
    )
    if member.readonly:
        raise WebIDLSelectionError(f"`{qualified_name}` must be writable")
    if member.type.nullable():
        raise WebIDLSelectionError(f"`{qualified_name}` must be non-nullable")
    if not member.type.isDOMString():
        raise WebIDLSelectionError(f"`{qualified_name}` must use `DOMString`, got `{member.type.prettyName()}`")
    if not member.getExtendedAttribute("CEReactions"):
        raise WebIDLSelectionError(f"`{qualified_name}` must carry `[CEReactions]`")
    if not member.type.getExtendedAttribute("LegacyNullToEmptyString"):
        raise WebIDLSelectionError(f"`{qualified_name}` must carry `[LegacyNullToEmptyString]` on its type")

    return member


def select_writable_domstring_attribute(
    parser_results: Sequence[WebIDL.IDLObjectWithIdentifier],
    qualified_name: str,
) -> WebIDL.IDLAttribute:
    """Select one ordinary CEReactions writable, non-nullable DOMString."""

    member = _select_instance_attribute(
        parser_results,
        qualified_name,
        WRITABLE_DOMSTRING_EXTENDED_ATTRIBUTES,
    )
    if member.readonly:
        raise WebIDLSelectionError(f"`{qualified_name}` must be writable")
    if member.type.nullable():
        raise WebIDLSelectionError(f"`{qualified_name}` must be non-nullable")
    if not member.type.isDOMString():
        raise WebIDLSelectionError(
            f"`{qualified_name}` must use `DOMString`, got `{member.type.prettyName()}`"
        )
    if not member.getExtendedAttribute("CEReactions"):
        raise WebIDLSelectionError(f"`{qualified_name}` must carry `[CEReactions]`")
    if member.type.getExtendedAttribute("LegacyNullToEmptyString"):
        raise WebIDLSelectionError(
            f"`{qualified_name}` must not carry `[LegacyNullToEmptyString]` on its type"
        )

    return member


def _select_instance_attribute(
    parser_results: Sequence[WebIDL.IDLObjectWithIdentifier],
    qualified_name: str,
    allowed_extended_attributes: frozenset[str],
) -> WebIDL.IDLAttribute:
    """Resolve one instance attribute and gate its extended attributes."""

    interface_name, member_name = _split_qualified_name(qualified_name)
    interfaces = [
        result for result in parser_results if result.isInterface() and result.identifier.name == interface_name
    ]
    if len(interfaces) != 1:
        raise WebIDLSelectionError(f"expected exactly one interface `{interface_name}`, found {len(interfaces)}")

    members = [member for member in interfaces[0].members if member.identifier.name == member_name]
    if len(members) != 1:
        raise WebIDLSelectionError(f"expected exactly one member `{qualified_name}`, found {len(members)}")

    member = members[0]
    if not member.isAttr():
        raise WebIDLSelectionError(f"`{qualified_name}` must be an attribute")
    if member.isStatic():
        raise WebIDLSelectionError(f"`{qualified_name}` must be an instance attribute")

    # `_extendedAttrDict` is the only view of *every* extended attribute a member
    # carries; `getExtendedAttribute` can only confirm the ones we already name.
    unsupported = set(member._extendedAttrDict) - allowed_extended_attributes
    if unsupported:
        raise WebIDLSelectionError(
            f"`{qualified_name}` carries extended attributes that are not implemented: "
            + ", ".join(sorted(unsupported))
        )

    return member


def select_readonly_usvstring_attribute(
    parser_results: Sequence[WebIDL.IDLObjectWithIdentifier],
    qualified_name: str,
) -> WebIDL.IDLAttribute:
    """Select one ordinary readonly, non-nullable USVString attribute."""

    member = _select_instance_attribute(parser_results, qualified_name, READONLY_USVSTRING_EXTENDED_ATTRIBUTES)
    if not member.readonly:
        raise WebIDLSelectionError(f"`{qualified_name}` must be readonly")
    if member.type.nullable():
        raise WebIDLSelectionError(f"`{qualified_name}` must be non-nullable")
    # `USVString` and `DOMString` differ in whether lone surrogates are
    # replaced, so accepting either here would silently pick one conversion.
    if not member.type.isUSVString():
        raise WebIDLSelectionError(f"`{qualified_name}` must use `USVString`, got `{member.type.prettyName()}`")

    return member


def select_readonly_domstring_attribute(
    parser_results: Sequence[WebIDL.IDLObjectWithIdentifier],
    qualified_name: str,
) -> WebIDL.IDLAttribute:
    """Select one ordinary readonly, non-nullable DOMString attribute."""

    member = _select_instance_attribute(
        parser_results,
        qualified_name,
        READONLY_DOMSTRING_EXTENDED_ATTRIBUTES,
    )
    if not member.readonly:
        raise WebIDLSelectionError(f"`{qualified_name}` must be readonly")
    if member.type.nullable():
        raise WebIDLSelectionError(f"`{qualified_name}` must be non-nullable")
    if not member.type.isDOMString():
        raise WebIDLSelectionError(
            f"`{qualified_name}` must use `DOMString`, got `{member.type.prettyName()}`"
        )

    return member


def select_readonly_enum_attribute(
    parser_results: Sequence[WebIDL.IDLObjectWithIdentifier],
    qualified_name: str,
    expected_values: Sequence[str],
) -> WebIDL.IDLAttribute:
    """Select one readonly, non-nullable enum attribute with a pinned value set."""

    member = _select_instance_attribute(parser_results, qualified_name, READONLY_ENUM_EXTENDED_ATTRIBUTES)
    if not member.readonly:
        raise WebIDLSelectionError(f"`{qualified_name}` must be readonly")
    if member.type.nullable():
        raise WebIDLSelectionError(f"`{qualified_name}` must be non-nullable")
    if not member.type.isEnum():
        raise WebIDLSelectionError(f"`{qualified_name}` must use an enum, got `{member.type.prettyName()}`")

    values = tuple(member.type.inner.values())
    if values != tuple(expected_values):
        raise WebIDLSelectionError(
            f"`{qualified_name}` must have the values {list(expected_values)}, got {list(values)}"
        )

    return member


def select_readonly_unsigned_short_attribute(
    parser_results: Sequence[WebIDL.IDLObjectWithIdentifier],
    qualified_name: str,
) -> WebIDL.IDLAttribute:
    """Select one readonly, non-nullable `unsigned short` attribute."""

    member = _select_instance_attribute(
        parser_results, qualified_name, READONLY_UNSIGNED_SHORT_EXTENDED_ATTRIBUTES
    )
    if not member.readonly:
        raise WebIDLSelectionError(f"`{qualified_name}` must be readonly")
    if member.type.nullable():
        raise WebIDLSelectionError(f"`{qualified_name}` must be non-nullable")
    if member.type.tag() != WebIDL.IDLType.Tags.uint16:
        raise WebIDLSelectionError(
            f"`{qualified_name}` must use `unsigned short`, got `{member.type.prettyName()}`"
        )

    return member


def select_readonly_unsigned_long_attribute(
    parser_results: Sequence[WebIDL.IDLObjectWithIdentifier],
    qualified_name: str,
) -> WebIDL.IDLAttribute:
    """Select one readonly, non-nullable `unsigned long` attribute."""

    member = _select_instance_attribute(
        parser_results, qualified_name, READONLY_UNSIGNED_LONG_EXTENDED_ATTRIBUTES
    )
    actual_attributes = set(member._extendedAttrDict)
    if actual_attributes != {"Pure"}:
        raise WebIDLSelectionError(
            f"`{qualified_name}` must carry exactly ['Pure'], "
            f"got {sorted(actual_attributes)}"
        )
    if not member.readonly:
        raise WebIDLSelectionError(f"`{qualified_name}` must be readonly")
    if member.type.nullable():
        raise WebIDLSelectionError(f"`{qualified_name}` must be non-nullable")
    if member.type.tag() != WebIDL.IDLType.Tags.uint32:
        raise WebIDLSelectionError(
            f"`{qualified_name}` must use `unsigned long`, got `{member.type.prettyName()}`"
        )

    return member


def select_readonly_nullable_interface_attribute(
    parser_results: Sequence[WebIDL.IDLObjectWithIdentifier],
    qualified_name: str,
    expected_interface: str,
) -> WebIDL.IDLAttribute:
    """Select one readonly, nullable attribute returning a named interface."""

    member = _select_instance_attribute(
        parser_results, qualified_name, READONLY_NULLABLE_INTERFACE_EXTENDED_ATTRIBUTES
    )
    if not member.readonly:
        raise WebIDLSelectionError(f"`{qualified_name}` must be readonly")
    # Nullable is required rather than merely tolerated: the generated glue
    # returns JS null for an absent object and has no other way to say "none".
    if not member.type.nullable():
        raise WebIDLSelectionError(f"`{qualified_name}` must be nullable")
    if not member.type.inner.isInterface():
        raise WebIDLSelectionError(
            f"`{qualified_name}` must return an interface, got `{member.type.prettyName()}`"
        )

    actual_interface = member.type.inner.name
    if actual_interface != expected_interface:
        raise WebIDLSelectionError(
            f"`{qualified_name}` must return `{expected_interface}`, got `{actual_interface}`"
        )

    return member


def select_pure_readonly_nullable_interface_attribute(
    parser_results: Sequence[WebIDL.IDLObjectWithIdentifier],
    qualified_name: str,
    expected_interface: str,
) -> WebIDL.IDLAttribute:
    """Select the exact `[Pure]` nullable-interface attribute shape."""

    member = select_readonly_nullable_interface_attribute(
        parser_results, qualified_name, expected_interface
    )
    actual_attributes = set(member._extendedAttrDict)
    if actual_attributes != {"Pure"}:
        raise WebIDLSelectionError(
            f"`{qualified_name}` must carry exactly ['Pure'], "
            f"got {sorted(actual_attributes)}"
        )
    return member


def select_sameobject_readonly_interface_attribute(
    parser_results: Sequence[WebIDL.IDLObjectWithIdentifier],
    qualified_name: str,
    expected_interface: str,
) -> WebIDL.IDLAttribute:
    """Select the exact `[SameObject]` non-nullable interface attribute shape."""

    member = _select_instance_attribute(
        parser_results,
        qualified_name,
        SAMEOBJECT_READONLY_INTERFACE_EXTENDED_ATTRIBUTES,
    )
    actual_attributes = set(member._extendedAttrDict)
    if actual_attributes != {"SameObject"}:
        raise WebIDLSelectionError(
            f"`{qualified_name}` must carry exactly ['SameObject'], "
            f"got {sorted(actual_attributes)}"
        )
    if not member.readonly:
        raise WebIDLSelectionError(f"`{qualified_name}` must be readonly")
    if member.type.nullable():
        raise WebIDLSelectionError(f"`{qualified_name}` must be non-nullable")
    if not member.type.isInterface():
        raise WebIDLSelectionError(
            f"`{qualified_name}` must return an interface, got `{member.type.prettyName()}`"
        )

    actual_interface = member.type.name
    if actual_interface != expected_interface:
        raise WebIDLSelectionError(
            f"`{qualified_name}` must return `{expected_interface}`, got `{actual_interface}`"
        )

    return member


def select_domstring_to_nonnullable_interface_operation(
    parser_results: Sequence[WebIDL.IDLObjectWithIdentifier],
    qualified_name: str,
    expected_interface: str,
    expected_argument_name: str,
) -> WebIDL.IDLMethod:
    """Select an unannotated instance operation with one exact DOMString input."""

    interface_name, member_name = _split_qualified_name(qualified_name)
    interfaces = [
        result
        for result in parser_results
        if result.isInterface() and result.identifier.name == interface_name
    ]
    if len(interfaces) != 1:
        raise WebIDLSelectionError(
            f"expected exactly one interface `{interface_name}`, found {len(interfaces)}"
        )
    members = [
        member
        for member in interfaces[0].members
        if member.identifier.name == member_name
    ]
    if len(members) != 1:
        raise WebIDLSelectionError(
            f"expected exactly one member `{qualified_name}`, found {len(members)}"
        )

    member = members[0]
    if not member.isMethod():
        raise WebIDLSelectionError(f"`{qualified_name}` must be an operation")
    if member.isStatic():
        raise WebIDLSelectionError(f"`{qualified_name}` must be an instance operation")
    if member.isSpecial():
        raise WebIDLSelectionError(f"`{qualified_name}` must be an ordinary operation")
    actual_attributes = set(member._extendedAttrDict)
    if actual_attributes:
        raise WebIDLSelectionError(
            f"`{qualified_name}` must carry no extended attributes, "
            f"got {sorted(actual_attributes)}"
        )

    signatures = member.signatures()
    if len(signatures) != 1:
        raise WebIDLSelectionError(
            f"`{qualified_name}` must have exactly one signature, found {len(signatures)}"
        )
    return_type, arguments = signatures[0]
    if return_type.nullable() or not return_type.isInterface():
        raise WebIDLSelectionError(
            f"`{qualified_name}` must return a non-nullable interface, "
            f"got `{return_type.prettyName()}`"
        )
    if return_type.name != expected_interface:
        raise WebIDLSelectionError(
            f"`{qualified_name}` must return `{expected_interface}`, "
            f"got `{return_type.name}`"
        )
    if len(arguments) != 1:
        raise WebIDLSelectionError(
            f"`{qualified_name}` must take exactly one argument, found {len(arguments)}"
        )
    argument = arguments[0]
    if (
        argument.identifier.name != expected_argument_name
        or argument.optional
        or argument.variadic
        or argument.defaultValue is not None
        or argument.type.nullable()
        or not argument.type.isDOMString()
    ):
        raise WebIDLSelectionError(
            f"`{qualified_name}` must take required non-nullable "
            f"`DOMString {expected_argument_name}`"
        )
    argument_attributes = set(argument._extendedAttrDict) | set(
        argument.type._extendedAttrDict
    )
    if argument_attributes:
        raise WebIDLSelectionError(
            f"`{qualified_name}` argument `{expected_argument_name}` carries extended "
            "attributes that are not implemented: "
            + ", ".join(sorted(argument_attributes))
        )
    return member


def select_pure_domstring_to_nullable_interface_operation(
    parser_results: Sequence[WebIDL.IDLObjectWithIdentifier],
    qualified_name: str,
    expected_interface: str,
) -> WebIDL.IDLMethod:
    """Select one pure instance operation with the exact generated signature."""

    # Preserve the older non-throwing shape's diagnostics: callers use these
    # to distinguish an omitted `[Pure]` hint from newly unsupported behavior.
    interface_name, member_name = _split_qualified_name(qualified_name)
    interfaces = [
        result
        for result in parser_results
        if result.isInterface() and result.identifier.name == interface_name
    ]
    if len(interfaces) == 1:
        members = [
            member
            for member in interfaces[0].members
            if member.identifier.name == member_name
        ]
        if len(members) == 1 and members[0].isMethod():
            attributes = set(members[0]._extendedAttrDict)
            unsupported = (
                attributes - PURE_DOMSTRING_TO_NULLABLE_INTERFACE_EXTENDED_ATTRIBUTES
            )
            if unsupported:
                raise WebIDLSelectionError(
                    f"`{qualified_name}` carries extended attributes that are not implemented: "
                    + ", ".join(sorted(unsupported))
                )
            if "Pure" not in attributes:
                raise WebIDLSelectionError(
                    f"`{qualified_name}` must carry `[Pure]`"
                )

    return _select_domstring_to_nullable_interface_operation(
        parser_results,
        qualified_name,
        expected_interface,
        PURE_DOMSTRING_TO_NULLABLE_INTERFACE_EXTENDED_ATTRIBUTES,
    )


def select_pure_throws_domstring_to_nullable_interface_operation(
    parser_results: Sequence[WebIDL.IDLObjectWithIdentifier],
    qualified_name: str,
    expected_interface: str,
) -> WebIDL.IDLMethod:
    """Select the exact throwing ParentNode selector-operation shape."""

    return _select_domstring_to_nullable_interface_operation(
        parser_results,
        qualified_name,
        expected_interface,
        PURE_THROWS_DOMSTRING_TO_NULLABLE_INTERFACE_EXTENDED_ATTRIBUTES,
    )


def select_pure_throws_domstring_to_boolean_operation(
    parser_results: Sequence[WebIDL.IDLObjectWithIdentifier],
    qualified_name: str,
) -> WebIDL.IDLMethod:
    """Select one exact throwing selector operation returning a boolean."""

    interface_name, member_name = _split_qualified_name(qualified_name)
    interfaces = [
        result
        for result in parser_results
        if result.isInterface() and result.identifier.name == interface_name
    ]
    if len(interfaces) != 1:
        raise WebIDLSelectionError(
            f"expected exactly one interface `{interface_name}`, found {len(interfaces)}"
        )
    members = [
        member
        for member in interfaces[0].members
        if member.identifier.name == member_name
    ]
    if len(members) != 1:
        raise WebIDLSelectionError(
            f"expected exactly one member `{qualified_name}`, found {len(members)}"
        )

    member = members[0]
    if not member.isMethod():
        raise WebIDLSelectionError(f"`{qualified_name}` must be an operation")
    if member.isStatic():
        raise WebIDLSelectionError(f"`{qualified_name}` must be an instance operation")
    if member.isSpecial():
        raise WebIDLSelectionError(f"`{qualified_name}` must be an ordinary operation")
    actual_attributes = set(member._extendedAttrDict)
    if actual_attributes != PURE_THROWS_DOMSTRING_TO_BOOLEAN_EXTENDED_ATTRIBUTES:
        raise WebIDLSelectionError(
            f"`{qualified_name}` must carry exactly "
            f"{sorted(PURE_THROWS_DOMSTRING_TO_BOOLEAN_EXTENDED_ATTRIBUTES)}, "
            f"got {sorted(actual_attributes)}"
        )

    signatures = member.signatures()
    if len(signatures) != 1:
        raise WebIDLSelectionError(
            f"`{qualified_name}` must have exactly one signature, found {len(signatures)}"
        )
    return_type, arguments = signatures[0]
    if return_type.nullable() or not return_type.isBoolean():
        raise WebIDLSelectionError(
            f"`{qualified_name}` must return non-nullable `boolean`, "
            f"got `{return_type.prettyName()}`"
        )
    if len(arguments) != 1:
        raise WebIDLSelectionError(
            f"`{qualified_name}` must take exactly one argument, found {len(arguments)}"
        )
    argument = arguments[0]
    if argument.optional or argument.variadic:
        raise WebIDLSelectionError(
            f"`{qualified_name}` argument `{argument.identifier.name}` "
            "must be required and non-variadic"
        )
    if argument.type.nullable() or not argument.type.isDOMString():
        raise WebIDLSelectionError(
            f"`{qualified_name}` argument `{argument.identifier.name}` must use "
            f"non-nullable `DOMString`, got `{argument.type.prettyName()}`"
        )
    argument_attributes = set(argument._extendedAttrDict) | set(
        argument.type._extendedAttrDict
    )
    if argument_attributes:
        raise WebIDLSelectionError(
            f"`{qualified_name}` argument `{argument.identifier.name}` carries "
            "extended attributes that are not implemented: "
            + ", ".join(sorted(argument_attributes))
        )
    return member


def select_newobject_throws_domstring_to_interface_operation(
    parser_results: Sequence[WebIDL.IDLObjectWithIdentifier],
    qualified_name: str,
    expected_interface: str,
) -> WebIDL.IDLMethod:
    """Select one exact throwing selector operation returning a new interface."""

    interface_name, member_name = _split_qualified_name(qualified_name)
    interfaces = [
        result
        for result in parser_results
        if result.isInterface() and result.identifier.name == interface_name
    ]
    if len(interfaces) != 1:
        raise WebIDLSelectionError(
            f"expected exactly one interface `{interface_name}`, found {len(interfaces)}"
        )
    members = [
        member
        for member in interfaces[0].members
        if member.identifier.name == member_name
    ]
    if len(members) != 1:
        raise WebIDLSelectionError(
            f"expected exactly one member `{qualified_name}`, found {len(members)}"
        )

    member = members[0]
    if not member.isMethod() or member.isStatic() or member.isSpecial():
        raise WebIDLSelectionError(
            f"`{qualified_name}` must be an ordinary instance operation"
        )
    actual_attributes = set(member._extendedAttrDict)
    if actual_attributes != NEWOBJECT_THROWS_DOMSTRING_TO_INTERFACE_EXTENDED_ATTRIBUTES:
        raise WebIDLSelectionError(
            f"`{qualified_name}` must carry exactly "
            f"{sorted(NEWOBJECT_THROWS_DOMSTRING_TO_INTERFACE_EXTENDED_ATTRIBUTES)}, "
            f"got {sorted(actual_attributes)}"
        )
    signatures = member.signatures()
    if len(signatures) != 1:
        raise WebIDLSelectionError(
            f"`{qualified_name}` must have exactly one signature, found {len(signatures)}"
        )
    return_type, arguments = signatures[0]
    if return_type.nullable() or not return_type.isInterface():
        raise WebIDLSelectionError(
            f"`{qualified_name}` must return a non-nullable interface, "
            f"got `{return_type.prettyName()}`"
        )
    if return_type.name != expected_interface:
        raise WebIDLSelectionError(
            f"`{qualified_name}` must return `{expected_interface}`, "
            f"got `{return_type.name}`"
        )
    if len(arguments) != 1:
        raise WebIDLSelectionError(
            f"`{qualified_name}` must take exactly one argument, found {len(arguments)}"
        )
    argument = arguments[0]
    if argument.optional or argument.variadic:
        raise WebIDLSelectionError(
            f"`{qualified_name}` argument `{argument.identifier.name}` "
            "must be required and non-variadic"
        )
    if argument.type.nullable() or not argument.type.isDOMString():
        raise WebIDLSelectionError(
            f"`{qualified_name}` argument `{argument.identifier.name}` must use "
            f"non-nullable `DOMString`, got `{argument.type.prettyName()}`"
        )
    argument_attributes = set(argument._extendedAttrDict) | set(
        argument.type._extendedAttrDict
    )
    if argument_attributes:
        raise WebIDLSelectionError(
            f"`{qualified_name}` argument `{argument.identifier.name}` carries "
            "extended attributes that are not implemented: "
            + ", ".join(sorted(argument_attributes))
        )
    return member


def _select_domstring_to_nullable_interface_operation(
    parser_results: Sequence[WebIDL.IDLObjectWithIdentifier],
    qualified_name: str,
    expected_interface: str,
    expected_attributes: frozenset[str],
) -> WebIDL.IDLMethod:
    """Select one instance operation with the shared DOMString/result shape."""

    interface_name, member_name = _split_qualified_name(qualified_name)
    interfaces = [
        result
        for result in parser_results
        if result.isInterface() and result.identifier.name == interface_name
    ]
    if len(interfaces) != 1:
        raise WebIDLSelectionError(
            f"expected exactly one interface `{interface_name}`, found {len(interfaces)}"
        )

    members = [
        member
        for member in interfaces[0].members
        if member.identifier.name == member_name
    ]
    if len(members) != 1:
        raise WebIDLSelectionError(
            f"expected exactly one member `{qualified_name}`, found {len(members)}"
        )

    member = members[0]
    if not member.isMethod():
        raise WebIDLSelectionError(f"`{qualified_name}` must be an operation")
    if member.isStatic():
        raise WebIDLSelectionError(f"`{qualified_name}` must be an instance operation")
    if member.isSpecial():
        raise WebIDLSelectionError(f"`{qualified_name}` must be an ordinary operation")

    actual_attributes = set(member._extendedAttrDict)
    if actual_attributes != expected_attributes:
        raise WebIDLSelectionError(
            f"`{qualified_name}` must carry exactly {sorted(expected_attributes)}, "
            f"got {sorted(actual_attributes)}"
        )

    signatures = member.signatures()
    if len(signatures) != 1:
        raise WebIDLSelectionError(
            f"`{qualified_name}` must have exactly one signature, found {len(signatures)}"
        )
    return_type, arguments = signatures[0]
    if not return_type.nullable():
        raise WebIDLSelectionError(f"`{qualified_name}` must return a nullable interface")
    if not return_type.inner.isInterface():
        raise WebIDLSelectionError(
            f"`{qualified_name}` must return an interface, got `{return_type.prettyName()}`"
        )
    actual_interface = return_type.inner.name
    if actual_interface != expected_interface:
        raise WebIDLSelectionError(
            f"`{qualified_name}` must return `{expected_interface}`, got `{actual_interface}`"
        )

    if len(arguments) != 1:
        raise WebIDLSelectionError(
            f"`{qualified_name}` must take exactly one argument, found {len(arguments)}"
        )
    argument = arguments[0]
    if argument.optional or argument.variadic:
        raise WebIDLSelectionError(
            f"`{qualified_name}` argument `{argument.identifier.name}` must be required and non-variadic"
        )
    if argument.type.nullable() or not argument.type.isDOMString():
        raise WebIDLSelectionError(
            f"`{qualified_name}` argument `{argument.identifier.name}` must use non-nullable `DOMString`, "
            f"got `{argument.type.prettyName()}`"
        )
    argument_attributes = set(argument._extendedAttrDict) | set(argument.type._extendedAttrDict)
    if argument_attributes:
        raise WebIDLSelectionError(
            f"`{qualified_name}` argument `{argument.identifier.name}` carries extended attributes "
            "that are not implemented: " + ", ".join(sorted(argument_attributes))
        )

    return member


def _select_timer_operation(
    parser_results: Sequence[WebIDL.IDLObjectWithIdentifier],
    qualified_name: str,
) -> WebIDL.IDLMethod:
    """Select one exact production timer operation from its interface mixin."""

    interface_name, member_name = _split_qualified_name(qualified_name)
    mixins = [
        result
        for result in parser_results
        if result.isInterfaceMixin() and result.identifier.name == interface_name
    ]
    if len(mixins) != 1:
        raise WebIDLSelectionError(
            f"expected exactly one interface mixin `{interface_name}`, found {len(mixins)}"
        )
    members = [member for member in mixins[0].members if member.identifier.name == member_name]
    if len(members) != 1:
        raise WebIDLSelectionError(
            f"expected exactly one member `{qualified_name}`, found {len(members)}"
        )
    member = members[0]
    if not member.isMethod() or member.isStatic() or member.isSpecial():
        raise WebIDLSelectionError(f"`{qualified_name}` must be an ordinary instance operation")

    is_setter = member_name in {"setTimeout", "setInterval"}
    expected_attributes = {"Throws"} if is_setter else set()
    actual_attributes = set(member._extendedAttrDict)
    if actual_attributes != expected_attributes:
        raise WebIDLSelectionError(
            f"`{qualified_name}` must carry exactly {sorted(expected_attributes)}, "
            f"got {sorted(actual_attributes)}"
        )
    signatures = member.signatures()
    if len(signatures) != 1:
        raise WebIDLSelectionError(
            f"`{qualified_name}` must have exactly one signature, found {len(signatures)}"
        )
    return_type, arguments = signatures[0]
    expected_return_tag = WebIDL.IDLType.Tags.int32 if is_setter else WebIDL.IDLType.Tags.undefined
    if return_type.nullable() or return_type.tag() != expected_return_tag:
        expected_return = "long" if is_setter else "undefined"
        raise WebIDLSelectionError(
            f"`{qualified_name}` must return non-nullable `{expected_return}`, "
            f"got `{return_type.prettyName()}`"
        )

    if is_setter:
        if len(arguments) != 3:
            raise WebIDLSelectionError(
                f"`{qualified_name}` must take exactly three arguments, found {len(arguments)}"
            )
        handler, timeout, trailing = arguments
        if handler.identifier.name != "handler" or handler.optional or handler.variadic:
            raise WebIDLSelectionError(
                f"`{qualified_name}` must begin with required `handler`"
            )
        if handler.type.nullable() or not handler.type.isUnion():
            raise WebIDLSelectionError(
                f"`{qualified_name}` handler must use the non-nullable TimerHandler union"
            )
        handler_types = tuple(part.prettyName() for part in handler.type.memberTypes)
        if handler_types != ("TrustedScript", "DOMString", "Function"):
            raise WebIDLSelectionError(
                f"`{qualified_name}` handler union must be "
                "['TrustedScript', 'DOMString', 'Function'], "
                f"got {list(handler_types)}"
            )
        _require_plain_timer_argument(qualified_name, handler)
        _require_optional_long_default_zero(qualified_name, timeout, "timeout")
        if (
            trailing.identifier.name != "arguments"
            or not trailing.optional
            or not trailing.variadic
            or trailing.defaultValue is not None
            or trailing.type.nullable()
            or not trailing.type.isAny()
        ):
            raise WebIDLSelectionError(
                f"`{qualified_name}` must end with variadic `any... arguments`"
            )
        _require_plain_timer_argument(qualified_name, trailing)
    else:
        if len(arguments) != 1:
            raise WebIDLSelectionError(
                f"`{qualified_name}` must take exactly one argument, found {len(arguments)}"
            )
        _require_optional_long_default_zero(qualified_name, arguments[0], "handle")

    return member


def _require_plain_timer_argument(qualified_name: str, argument: WebIDL.IDLArgument) -> None:
    attributes = set(argument._extendedAttrDict) | set(argument.type._extendedAttrDict)
    if attributes:
        raise WebIDLSelectionError(
            f"`{qualified_name}` argument `{argument.identifier.name}` carries extended "
            "attributes that are not implemented: " + ", ".join(sorted(attributes))
        )


def _require_optional_long_default_zero(
    qualified_name: str,
    argument: WebIDL.IDLArgument,
    expected_name: str,
) -> None:
    default = argument.defaultValue
    if (
        argument.identifier.name != expected_name
        or not argument.optional
        or argument.variadic
        or argument.type.nullable()
        or argument.type.tag() != WebIDL.IDLType.Tags.int32
        or default is None
        or default.value != 0
        or default.type.tag() != WebIDL.IDLType.Tags.int32
    ):
        raise WebIDLSelectionError(
            f"`{qualified_name}` argument `{expected_name}` must be optional non-nullable "
            "`long` with default 0"
        )
    _require_plain_timer_argument(qualified_name, argument)


def _select_console_log_operation(
    parser_results: Sequence[WebIDL.IDLObjectWithIdentifier],
    qualified_name: str,
) -> WebIDL.IDLMethod:
    """Select one exact variadic logging operation from the console namespace."""

    namespace_name, member_name = _split_qualified_name(qualified_name)
    namespaces = [
        result
        for result in parser_results
        if result.isNamespace() and result.identifier.name == namespace_name
    ]
    if len(namespaces) != 1:
        raise WebIDLSelectionError(
            f"expected exactly one namespace `{namespace_name}`, found {len(namespaces)}"
        )
    members = [
        member
        for member in namespaces[0].members
        if member.identifier.name == member_name
    ]
    if len(members) != 1:
        raise WebIDLSelectionError(
            f"expected exactly one member `{qualified_name}`, found {len(members)}"
        )
    member = members[0]
    if not member.isMethod() or member.isSpecial():
        raise WebIDLSelectionError(
            f"`{qualified_name}` must be an ordinary namespace operation"
        )
    attributes = set(member._extendedAttrDict)
    if attributes:
        raise WebIDLSelectionError(
            f"`{qualified_name}` carries extended attributes that are not implemented: "
            + ", ".join(sorted(attributes))
        )
    signatures = member.signatures()
    if len(signatures) != 1:
        raise WebIDLSelectionError(
            f"`{qualified_name}` must have exactly one signature, found {len(signatures)}"
        )
    return_type, arguments = signatures[0]
    if return_type.nullable() or return_type.tag() != WebIDL.IDLType.Tags.undefined:
        raise WebIDLSelectionError(
            f"`{qualified_name}` must return non-nullable `undefined`, "
            f"got `{return_type.prettyName()}`"
        )
    if len(arguments) != 1:
        raise WebIDLSelectionError(
            f"`{qualified_name}` must take exactly one variadic argument, "
            f"found {len(arguments)}"
        )
    argument = arguments[0]
    expected_name = "data" if member_name == "trace" else "messages"
    if (
        argument.identifier.name != expected_name
        or not argument.optional
        or not argument.variadic
        or argument.defaultValue is not None
        or argument.type.nullable()
        or not argument.type.isAny()
    ):
        raise WebIDLSelectionError(
            f"`{qualified_name}` must take variadic `any... {expected_name}`"
        )
    argument_attributes = set(argument._extendedAttrDict) | set(
        argument.type._extendedAttrDict
    )
    if argument_attributes:
        raise WebIDLSelectionError(
            f"`{qualified_name}` argument `{expected_name}` carries extended attributes "
            "that are not implemented: " + ", ".join(sorted(argument_attributes))
        )
    return member


def _select_element_host_member(
    parser_results: Sequence[WebIDL.IDLObjectWithIdentifier],
    qualified_name: str,
) -> WebIDL.IDLAttribute | WebIDL.IDLMethod:
    """Select one exact scalar member from the production Element interface."""

    interface_name, member_name = _split_qualified_name(qualified_name)
    interfaces = [
        result
        for result in parser_results
        if result.isInterface() and result.identifier.name == interface_name
    ]
    if len(interfaces) != 1:
        raise WebIDLSelectionError(
            f"expected exactly one interface `{interface_name}`, found {len(interfaces)}"
        )
    members = [
        member
        for member in interfaces[0].members
        if member.identifier.name == member_name
    ]
    if len(members) != 1:
        raise WebIDLSelectionError(
            f"expected exactly one member `{qualified_name}`, found {len(members)}"
        )
    member = members[0]

    attribute_attributes = {
        "localName": {"Constant"},
        "tagName": {"Pure"},
        "id": {"CEReactions", "Pure"},
        "className": {"CEReactions", "Pure"},
        "children": {"SameObject"},
        "namespaceURI": {"Constant"},
        "prefix": {"Constant"},
        "firstElementChild": {"Pure"},
        "lastElementChild": {"Pure"},
        "childElementCount": {"Pure"},
        "previousElementSibling": {"Pure"},
        "nextElementSibling": {"Pure"},
    }
    if member_name in attribute_attributes:
        if not member.isAttr() or member.isStatic():
            raise WebIDLSelectionError(
                f"`{qualified_name}` must be an instance attribute"
            )
        expected_attributes = attribute_attributes[member_name]
        actual_attributes = set(member._extendedAttrDict)
        if actual_attributes != expected_attributes:
            raise WebIDLSelectionError(
                f"`{qualified_name}` must carry exactly {sorted(expected_attributes)}, "
                f"got {sorted(actual_attributes)}"
            )
        expected_readonly = member_name in {
            "localName",
            "tagName",
            "children",
            "namespaceURI",
            "prefix",
            "firstElementChild",
            "lastElementChild",
            "childElementCount",
            "previousElementSibling",
            "nextElementSibling",
        }
        if member.readonly != expected_readonly:
            state = "readonly" if expected_readonly else "writable"
            raise WebIDLSelectionError(f"`{qualified_name}` must be {state}")
        if member_name == "children":
            if (
                member.type.nullable()
                or not member.type.isInterface()
                or member.type.name != "HTMLCollection"
            ):
                raise WebIDLSelectionError(
                    f"`{qualified_name}` must use non-nullable `HTMLCollection`, "
                    f"got `{member.type.prettyName()}`"
                )
        elif member_name in {"namespaceURI", "prefix"}:
            if (
                not member.type.nullable()
                or not member.type.inner.isDOMString()
            ):
                raise WebIDLSelectionError(
                    f"`{qualified_name}` must use nullable `DOMString`, "
                    f"got `{member.type.prettyName()}`"
                )
        elif member_name in {
            "firstElementChild",
            "lastElementChild",
            "previousElementSibling",
            "nextElementSibling",
        }:
            if (
                not member.type.nullable()
                or not member.type.inner.isInterface()
                or member.type.inner.name != "Element"
            ):
                raise WebIDLSelectionError(
                    f"`{qualified_name}` must use nullable `Element`, "
                    f"got `{member.type.prettyName()}`"
                )
        elif member_name == "childElementCount":
            if member.type.nullable() or member.type.tag() != WebIDL.IDLType.Tags.uint32:
                raise WebIDLSelectionError(
                    f"`{qualified_name}` must use non-nullable `unsigned long`, "
                    f"got `{member.type.prettyName()}`"
                )
        elif member.type.nullable() or not member.type.isDOMString():
            raise WebIDLSelectionError(
                f"`{qualified_name}` must use non-nullable `DOMString`, "
                f"got `{member.type.prettyName()}`"
            )
        return member

    if not member.isMethod() or member.isStatic() or member.isSpecial():
        raise WebIDLSelectionError(
            f"`{qualified_name}` must be an ordinary instance operation"
        )
    if member_name in {"querySelector", "closest"}:
        # The parser resolves ParentNode's included members onto Element. Reuse
        # the same exact operation selector as Document so the mixin cannot
        # drift independently across the two native hosts.
        return select_pure_throws_domstring_to_nullable_interface_operation(
            parser_results, qualified_name, "Element"
        )
    if member_name in {"matches", "webkitMatchesSelector"}:
        return select_pure_throws_domstring_to_boolean_operation(
            parser_results, qualified_name
        )
    if member_name == "getElementsByClassName":
        return select_domstring_to_nonnullable_interface_operation(
            parser_results,
            qualified_name,
            "HTMLCollection",
            "classNames",
        )
    if member_name == "querySelectorAll":
        return select_newobject_throws_domstring_to_interface_operation(
            parser_results, qualified_name, "NodeList"
        )
    if member_name == "remove":
        expected_attributes = {"CEReactions", "Unscopable"}
        actual_attributes = set(member._extendedAttrDict)
        if actual_attributes != expected_attributes:
            raise WebIDLSelectionError(
                f"`{qualified_name}` must carry exactly {sorted(expected_attributes)}, "
                f"got {sorted(actual_attributes)}"
            )
        signatures = member.signatures()
        if len(signatures) != 1:
            raise WebIDLSelectionError(
                f"`{qualified_name}` must have exactly one signature, found {len(signatures)}"
            )
        return_type, arguments = signatures[0]
        if return_type.nullable() or return_type.tag() != WebIDL.IDLType.Tags.undefined:
            raise WebIDLSelectionError(
                f"`{qualified_name}` must return non-nullable `undefined`, "
                f"got `{return_type.prettyName()}`"
            )
        if arguments:
            raise WebIDLSelectionError(
                f"`{qualified_name}` must take no arguments, found {len(arguments)}"
            )
        return member
    expected_attributes = (
        {"Pure"} if member_name in {"hasAttributes", "getAttribute"} else set()
    )
    actual_attributes = set(member._extendedAttrDict)
    if actual_attributes != expected_attributes:
        raise WebIDLSelectionError(
            f"`{qualified_name}` must carry exactly {sorted(expected_attributes)}, "
            f"got {sorted(actual_attributes)}"
        )
    signatures = member.signatures()
    if len(signatures) != 1:
        raise WebIDLSelectionError(
            f"`{qualified_name}` must have exactly one signature, found {len(signatures)}"
        )
    return_type, arguments = signatures[0]
    if member_name == "getAttribute":
        if not return_type.nullable() or not return_type.inner.isDOMString():
            raise WebIDLSelectionError(
                f"`{qualified_name}` must return nullable `DOMString`, "
                f"got `{return_type.prettyName()}`"
            )
    elif return_type.nullable() or not return_type.isBoolean():
        raise WebIDLSelectionError(
            f"`{qualified_name}` must return non-nullable `boolean`, "
            f"got `{return_type.prettyName()}`"
        )

    expected_count = 0 if member_name == "hasAttributes" else 1
    if len(arguments) != expected_count:
        raise WebIDLSelectionError(
            f"`{qualified_name}` must take exactly {expected_count} argument(s), "
            f"found {len(arguments)}"
        )
    if arguments:
        argument = arguments[0]
        if (
            argument.identifier.name != "name"
            or argument.optional
            or argument.variadic
            or argument.type.nullable()
            or not argument.type.isDOMString()
        ):
            raise WebIDLSelectionError(
                f"`{qualified_name}` must take required non-nullable `DOMString name`"
            )
        attributes = set(argument._extendedAttrDict) | set(
            argument.type._extendedAttrDict
        )
        if attributes:
            raise WebIDLSelectionError(
                f"`{qualified_name}` argument `name` carries extended attributes "
                "that are not implemented: " + ", ".join(sorted(attributes))
            )
    return member


def _select_node_host_member(
    parser_results: Sequence[WebIDL.IDLObjectWithIdentifier],
    qualified_name: str,
) -> WebIDL.IDLAttribute | WebIDL.IDLMethod:
    """Select one exact scalar member from the production Node interface."""

    interface_name, member_name = _split_qualified_name(qualified_name)
    interfaces = [
        result
        for result in parser_results
        if result.isInterface() and result.identifier.name == interface_name
    ]
    if len(interfaces) != 1:
        raise WebIDLSelectionError(
            f"expected exactly one interface `{interface_name}`, found {len(interfaces)}"
        )
    members = [
        member
        for member in interfaces[0].members
        if member.identifier.name == member_name
    ]
    if len(members) != 1:
        raise WebIDLSelectionError(
            f"expected exactly one member `{qualified_name}`, found {len(members)}"
        )
    member = members[0]

    attribute_attributes = {
        "nodeType": {"Constant"},
        "nodeName": {"Pure"},
        "isConnected": {"Pure"},
        "parentElement": {"Pure"},
        "textContent": {"CEReactions", "Pure", "SetterThrows"},
    }
    if member_name in attribute_attributes:
        if not member.isAttr() or member.isStatic():
            raise WebIDLSelectionError(
                f"`{qualified_name}` must be an instance attribute"
            )
        expected_attributes = attribute_attributes[member_name]
        actual_attributes = set(member._extendedAttrDict)
        if actual_attributes != expected_attributes:
            raise WebIDLSelectionError(
                f"`{qualified_name}` must carry exactly {sorted(expected_attributes)}, "
                f"got {sorted(actual_attributes)}"
            )
        expected_readonly = member_name != "textContent"
        if member.readonly != expected_readonly:
            state = "readonly" if expected_readonly else "writable"
            raise WebIDLSelectionError(f"`{qualified_name}` must be {state}")
        if member_name == "nodeType":
            if member.type.nullable() or member.type.tag() != WebIDL.IDLType.Tags.uint16:
                raise WebIDLSelectionError(
                    f"`{qualified_name}` must use non-nullable `unsigned short`, "
                    f"got `{member.type.prettyName()}`"
                )
        elif member_name == "isConnected":
            if member.type.nullable() or not member.type.isBoolean():
                raise WebIDLSelectionError(
                    f"`{qualified_name}` must use non-nullable `boolean`, "
                    f"got `{member.type.prettyName()}`"
                )
        elif member_name == "parentElement":
            if (
                not member.type.nullable()
                or not member.type.inner.isInterface()
                or member.type.inner.name != "Element"
            ):
                raise WebIDLSelectionError(
                    f"`{qualified_name}` must use nullable `Element`, "
                    f"got `{member.type.prettyName()}`"
                )
        elif member_name == "textContent":
            if not member.type.nullable() or not member.type.inner.isDOMString():
                raise WebIDLSelectionError(
                    f"`{qualified_name}` must use nullable `DOMString`, "
                    f"got `{member.type.prettyName()}`"
                )
        elif member.type.nullable() or not member.type.isDOMString():
            raise WebIDLSelectionError(
                f"`{qualified_name}` must use non-nullable `DOMString`, "
                f"got `{member.type.prettyName()}`"
            )
        return member

    if not member.isMethod() or member.isStatic() or member.isSpecial():
        raise WebIDLSelectionError(
            f"`{qualified_name}` must be an ordinary instance operation"
        )
    actual_attributes = set(member._extendedAttrDict)
    if actual_attributes != {"Pure"}:
        raise WebIDLSelectionError(
            f"`{qualified_name}` must carry exactly ['Pure'], "
            f"got {sorted(actual_attributes)}"
        )
    signatures = member.signatures()
    if len(signatures) != 1:
        raise WebIDLSelectionError(
            f"`{qualified_name}` must have exactly one signature, found {len(signatures)}"
        )
    return_type, arguments = signatures[0]
    if return_type.nullable() or not return_type.isBoolean():
        raise WebIDLSelectionError(
            f"`{qualified_name}` must return non-nullable `boolean`, "
            f"got `{return_type.prettyName()}`"
        )
    if arguments:
        raise WebIDLSelectionError(
            f"`{qualified_name}` must take no arguments, found {len(arguments)}"
        )
    return member


def _select_html_collection_interface(
    parser_results: Sequence[WebIDL.IDLObjectWithIdentifier],
) -> WebIDL.IDLInterface:
    """Pin the complete manually installed HTMLCollection WebIDL surface."""

    interfaces = [
        result
        for result in parser_results
        if result.isInterface() and result.identifier.name == HTML_COLLECTION_INTERFACE
    ]
    if len(interfaces) != 1:
        raise WebIDLSelectionError(
            f"expected exactly one interface `{HTML_COLLECTION_INTERFACE}`, "
            f"found {len(interfaces)}"
        )
    interface = interfaces[0]

    expected_interface_attributes = {
        "Exposed",
        "LegacyUnenumerableNamedProperties",
    }
    actual_interface_attributes = set(interface._extendedAttrDict)
    if actual_interface_attributes != expected_interface_attributes:
        raise WebIDLSelectionError(
            "`HTMLCollection` must carry exactly "
            f"{sorted(expected_interface_attributes)}, "
            f"got {sorted(actual_interface_attributes)}"
        )
    if interface.getExtendedAttribute("Exposed") != ["Window"]:
        raise WebIDLSelectionError(
            "`HTMLCollection` must carry exactly `[Exposed=Window]`"
        )
    if interface.getExtendedAttribute("LegacyUnenumerableNamedProperties") is not True:
        raise WebIDLSelectionError(
            "`HTMLCollection` must carry `[LegacyUnenumerableNamedProperties]`"
        )
    if interface.isCallback():
        raise WebIDLSelectionError("`HTMLCollection` must be an ordinary interface")
    if interface.parent is not None:
        raise WebIDLSelectionError("`HTMLCollection` must not inherit from another interface")
    if interface.ctor() is not None or interface.legacyFactoryFunctions:
        raise WebIDLSelectionError("`HTMLCollection` must not be constructible")
    if interface.maplikeOrSetlikeOrIterable is not None:
        raise WebIDLSelectionError(
            "`HTMLCollection` must not declare iterable, maplike, or setlike members"
        )

    expected_member_names = ["length", "item", "namedItem"]
    actual_member_names = [member.identifier.name for member in interface.members]
    if actual_member_names != expected_member_names:
        raise WebIDLSelectionError(
            f"`HTMLCollection` must declare exactly {expected_member_names}, "
            f"got {actual_member_names}"
        )

    length, item, named_item = interface.members
    if not length.isAttr() or length.isStatic():
        raise WebIDLSelectionError(
            f"`{HTML_COLLECTION_LENGTH}` must be an instance attribute"
        )
    if set(length._extendedAttrDict) != {"Pure"}:
        raise WebIDLSelectionError(
            f"`{HTML_COLLECTION_LENGTH}` must carry exactly ['Pure'], "
            f"got {sorted(length._extendedAttrDict)}"
        )
    if not length.readonly:
        raise WebIDLSelectionError(f"`{HTML_COLLECTION_LENGTH}` must be readonly")
    if length.type.nullable() or length.type.tag() != WebIDL.IDLType.Tags.uint32:
        raise WebIDLSelectionError(
            f"`{HTML_COLLECTION_LENGTH}` must use non-nullable `unsigned long`, "
            f"got `{length.type.prettyName()}`"
        )
    if length.type._extendedAttrDict:
        raise WebIDLSelectionError(
            f"`{HTML_COLLECTION_LENGTH}` type carries extended attributes that are not implemented: "
            + ", ".join(sorted(length.type._extendedAttrDict))
        )

    _validate_html_collection_getter(
        item,
        HTML_COLLECTION_ITEM,
        named=False,
        argument_name="index",
        argument_type="unsigned long",
    )
    _validate_html_collection_getter(
        named_item,
        HTML_COLLECTION_NAMED_ITEM,
        named=True,
        argument_name="name",
        argument_type="DOMString",
    )
    return interface


def _validate_html_collection_getter(
    member: WebIDL.IDLInterfaceMember,
    qualified_name: str,
    *,
    named: bool,
    argument_name: str,
    argument_type: str,
) -> None:
    """Validate one exact HTMLCollection indexed or named getter."""

    if not member.isMethod() or member.isStatic() or not member.isGetter():
        kind = "named" if named else "indexed"
        article = "a" if named else "an"
        raise WebIDLSelectionError(
            f"`{qualified_name}` must be {article} {kind} instance getter"
        )
    if member.isNamed() != named or member.isIndexed() == named:
        kind = "named" if named else "indexed"
        raise WebIDLSelectionError(f"`{qualified_name}` must be a {kind} getter")
    if set(member._extendedAttrDict) != {"Pure"}:
        raise WebIDLSelectionError(
            f"`{qualified_name}` must carry exactly ['Pure'], "
            f"got {sorted(member._extendedAttrDict)}"
        )
    signatures = member.signatures()
    if len(signatures) != 1:
        raise WebIDLSelectionError(
            f"`{qualified_name}` must have exactly one signature, "
            f"found {len(signatures)}"
        )
    return_type, arguments = signatures[0]
    if (
        not return_type.nullable()
        or not return_type.inner.isInterface()
        or return_type.inner.name != "Element"
    ):
        raise WebIDLSelectionError(
            f"`{qualified_name}` must return nullable `Element`, "
            f"got `{return_type.prettyName()}`"
        )
    if len(arguments) != 1:
        raise WebIDLSelectionError(
            f"`{qualified_name}` must take exactly one argument, found {len(arguments)}"
        )
    argument = arguments[0]
    type_matches = (
        argument.type.tag() == WebIDL.IDLType.Tags.uint32
        if argument_type == "unsigned long"
        else argument.type.isDOMString()
    )
    if (
        argument.identifier.name != argument_name
        or argument.optional
        or argument.variadic
        or argument.defaultValue is not None
        or argument.type.nullable()
        or not type_matches
    ):
        raise WebIDLSelectionError(
            f"`{qualified_name}` must take required non-nullable "
            f"`{argument_type} {argument_name}`"
        )
    argument_attributes = set(argument._extendedAttrDict) | set(
        argument.type._extendedAttrDict
    )
    if argument_attributes:
        raise WebIDLSelectionError(
            f"`{qualified_name}` argument `{argument_name}` carries extended attributes "
            "that are not implemented: " + ", ".join(sorted(argument_attributes))
        )


def select_document_hidden(
    cache_dir: Path,
    environment: Mapping[str, str] | None = None,
    webidls_dir: Path = PRODUCTION_WEBIDLS_DIR,
) -> WebIDL.IDLAttribute:
    """Load Servo's production corpus and select ``Document.hidden``."""

    parser_results = parse_webidl_corpus(webidls_dir, cache_dir, environment)
    return select_readonly_boolean_attribute(parser_results, DOCUMENT_HIDDEN)


_SHAPE_SELECTORS = {
    READONLY_BOOLEAN: select_readonly_boolean_attribute,
    WRITABLE_LEGACY_DOMSTRING: select_writable_legacy_domstring_attribute,
    WRITABLE_DOMSTRING: select_writable_domstring_attribute,
    READONLY_DOMSTRING: select_readonly_domstring_attribute,
    READONLY_USVSTRING: select_readonly_usvstring_attribute,
    READONLY_UNSIGNED_SHORT: select_readonly_unsigned_short_attribute,
    READONLY_UNSIGNED_LONG: select_readonly_unsigned_long_attribute,
}


def _select_document_host_member(
    parser_results: Sequence[WebIDL.IDLObjectWithIdentifier],
    member: DocumentHostMember,
) -> WebIDL.IDLAttribute | WebIDL.IDLMethod:
    if member.shape == READONLY_ENUM:
        if member.expected_interface is not None:
            raise WebIDLSelectionError(
                f"enum member `{member.qualified_name}` cannot pin a returned interface"
            )
        if member.expected_enum_values is None:
            raise WebIDLSelectionError(
                f"`{member.qualified_name}` must pin its enum values"
            )
        return select_readonly_enum_attribute(
            parser_results,
            member.qualified_name,
            member.expected_enum_values,
        )
    if member.expected_enum_values is not None:
        raise WebIDLSelectionError(
            f"non-enum member `{member.qualified_name}` cannot pin enum values"
        )
    if member.shape in {
        READONLY_NULLABLE_INTERFACE,
        PURE_READONLY_NULLABLE_INTERFACE,
        SAMEOBJECT_READONLY_INTERFACE,
        DOMSTRING_TO_NONNULLABLE_INTERFACE,
        PURE_DOMSTRING_TO_NULLABLE_INTERFACE,
        PURE_THROWS_DOMSTRING_TO_NULLABLE_INTERFACE,
        NEWOBJECT_THROWS_DOMSTRING_TO_INTERFACE,
    }:
        if member.expected_interface is None:
            raise WebIDLSelectionError(
                f"`{member.qualified_name}` must pin its returned interface"
            )
        if member.shape == READONLY_NULLABLE_INTERFACE:
            return select_readonly_nullable_interface_attribute(
                parser_results,
                member.qualified_name,
                member.expected_interface,
            )
        if member.shape == PURE_READONLY_NULLABLE_INTERFACE:
            return select_pure_readonly_nullable_interface_attribute(
                parser_results,
                member.qualified_name,
                member.expected_interface,
            )
        if member.shape == SAMEOBJECT_READONLY_INTERFACE:
            return select_sameobject_readonly_interface_attribute(
                parser_results,
                member.qualified_name,
                member.expected_interface,
            )
        if member.shape == DOMSTRING_TO_NONNULLABLE_INTERFACE:
            if member.expected_interface != "HTMLCollection":
                raise WebIDLSelectionError(
                    f"`{member.qualified_name}` collection shape must return "
                    "`HTMLCollection`"
                )
            return select_domstring_to_nonnullable_interface_operation(
                parser_results,
                member.qualified_name,
                member.expected_interface,
                "classNames",
            )
        if member.shape == NEWOBJECT_THROWS_DOMSTRING_TO_INTERFACE:
            return select_newobject_throws_domstring_to_interface_operation(
                parser_results,
                member.qualified_name,
                member.expected_interface,
            )
        selector = (
            select_pure_throws_domstring_to_nullable_interface_operation
            if member.shape == PURE_THROWS_DOMSTRING_TO_NULLABLE_INTERFACE
            else select_pure_domstring_to_nullable_interface_operation
        )
        return selector(
            parser_results, member.qualified_name, member.expected_interface
        )
    if member.expected_interface is not None:
        raise WebIDLSelectionError(
            f"non-interface member `{member.qualified_name}` cannot pin a returned interface"
        )
    return _SHAPE_SELECTORS[member.shape](parser_results, member.qualified_name)


def select_document_host_members(
    cache_dir: Path,
    environment: Mapping[str, str] | None = None,
    webidls_dir: Path = PRODUCTION_WEBIDLS_DIR,
) -> dict[str, WebIDL.IDLAttribute | WebIDL.IDLMethod]:
    """Load the production corpus and select the supported Document slice.

    The result is keyed by qualified name and ordered like ``DOCUMENT_HOST``,
    which is the order the generated artifacts declare their members in.
    """

    parser_results = parse_webidl_corpus(webidls_dir, cache_dir, environment)
    return {
        member.qualified_name: _select_document_host_member(parser_results, member)
        for member in DOCUMENT_HOST
    }


def select_timer_host_members(
    cache_dir: Path,
    environment: Mapping[str, str] | None = None,
    webidls_dir: Path = PRODUCTION_WEBIDLS_DIR,
) -> dict[str, WebIDL.IDLMethod]:
    """Load the production corpus and pin the four timer operations."""

    parser_results = parse_webidl_corpus(webidls_dir, cache_dir, environment)
    return {
        qualified_name: _select_timer_operation(parser_results, qualified_name)
        for qualified_name in TIMER_HOST
    }


def select_console_host_members(
    cache_dir: Path,
    environment: Mapping[str, str] | None = None,
    webidls_dir: Path = PRODUCTION_WEBIDLS_DIR,
) -> dict[str, WebIDL.IDLMethod]:
    """Load the production corpus and pin the supported console logging slice."""

    parser_results = parse_webidl_corpus(webidls_dir, cache_dir, environment)
    return {
        qualified_name: _select_console_log_operation(
            parser_results, qualified_name
        )
        for qualified_name in CONSOLE_HOST
    }


def select_element_host_members(
    cache_dir: Path,
    environment: Mapping[str, str] | None = None,
    webidls_dir: Path = PRODUCTION_WEBIDLS_DIR,
) -> dict[str, WebIDL.IDLAttribute | WebIDL.IDLMethod]:
    """Load the production corpus and pin the scalar Element host slice."""

    parser_results = parse_webidl_corpus(webidls_dir, cache_dir, environment)
    return {
        qualified_name: _select_element_host_member(
            parser_results, qualified_name
        )
        for qualified_name in ELEMENT_HOST
    }


def select_node_host_members(
    cache_dir: Path,
    environment: Mapping[str, str] | None = None,
    webidls_dir: Path = PRODUCTION_WEBIDLS_DIR,
) -> dict[str, WebIDL.IDLAttribute | WebIDL.IDLMethod]:
    """Load the production corpus and pin the inherited Node scalar slice."""

    parser_results = parse_webidl_corpus(webidls_dir, cache_dir, environment)
    return {
        qualified_name: _select_node_host_member(parser_results, qualified_name)
        for qualified_name in NODE_HOST
    }


def select_html_collection_interface(
    cache_dir: Path,
    environment: Mapping[str, str] | None = None,
    webidls_dir: Path = PRODUCTION_WEBIDLS_DIR,
) -> WebIDL.IDLInterface:
    """Load the production corpus and pin the complete HTMLCollection interface."""

    parser_results = parse_webidl_corpus(webidls_dir, cache_dir, environment)
    return _select_html_collection_interface(parser_results)


def _split_qualified_name(qualified_name: str) -> tuple[str, str]:
    parts = qualified_name.split(".")
    if len(parts) != 2 or not all(parts):
        raise WebIDLSelectionError(f"selected member `{qualified_name}` must have the form `Interface.member`")
    return parts[0], parts[1]
