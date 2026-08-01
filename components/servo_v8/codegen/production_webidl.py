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
DOCUMENT_VISIBILITY_STATE = "Document.visibilityState"
NODE_NODE_TYPE = "Node.nodeType"
DOCUMENT_DOCUMENT_ELEMENT = "Document.documentElement"

# Member shapes the generator knows how to emit. A shape names both the WebIDL
# form a selector accepts and the emitters that understand it, so a new member is
# supported by naming an existing shape rather than by touching the emitters.
READONLY_BOOLEAN = "readonly boolean"
WRITABLE_LEGACY_DOMSTRING = "CEReactions writable LegacyNullToEmptyString DOMString"
READONLY_USVSTRING = "readonly USVString"
READONLY_ENUM = "readonly enum"
READONLY_UNSIGNED_SHORT = "readonly unsigned short"
READONLY_NULLABLE_INTERFACE = "readonly nullable interface"

# Extended attributes change conversion, reaction, and lifetime semantics that
# the generated glue implements literally, so an unlisted one is silently wrong
# rather than merely unsupported. Each selector therefore allows exactly what its
# emitters honour and rejects the rest, as `generate.py` does for its own members.
READONLY_BOOLEAN_EXTENDED_ATTRIBUTES = frozenset()
WRITABLE_LEGACY_DOMSTRING_EXTENDED_ATTRIBUTES = frozenset({"CEReactions"})
# `[Constant]` is a SpiderMonkey JIT alias-set hint with no bearing on the value
# produced, and the V8 accessor is already installed with `kHasNoSideEffect`, so
# honouring it costs nothing. It is allowed rather than required so that dropping
# it upstream does not break the build.
READONLY_USVSTRING_EXTENDED_ATTRIBUTES = frozenset({"Constant"})
READONLY_ENUM_EXTENDED_ATTRIBUTES = frozenset()
READONLY_UNSIGNED_SHORT_EXTENDED_ATTRIBUTES = frozenset({"Constant"})
# `[Pure]` is a SpiderMonkey alias-set hint, like `[Constant]` but weaker, and
# says nothing the V8 accessor needs to honour.
READONLY_NULLABLE_INTERFACE_EXTENDED_ATTRIBUTES = frozenset({"Pure"})

# An enum crosses the ABI as its string value, so the generated glue is only
# correct for the exact value set it was written against. Pinning the set makes
# a new state upstream a build failure rather than an unvalidated string.
DOCUMENT_VISIBILITY_STATE_VALUES = ("visible", "hidden")

# The supported Document slice is data, in declaration order: selection and
# generation both walk it, so widening the slice is an edit to this tuple alone.
DOCUMENT_HOST: tuple[tuple[str, str], ...] = (
    (DOCUMENT_HIDDEN, READONLY_BOOLEAN),
    (DOCUMENT_BG_COLOR, WRITABLE_LEGACY_DOMSTRING),
    (DOCUMENT_URL, READONLY_USVSTRING),
    (DOCUMENT_VISIBILITY_STATE, READONLY_ENUM),
    # Document inherits from Node, so this lands on the existing document
    # facade: no second host, no second native pointer, no second vtable.
    (NODE_NODE_TYPE, READONLY_UNSIGNED_SHORT),
    # The first member whose value is another DOM object, and so the first that
    # needs the per-realm wrapper cache to preserve identity.
    (DOCUMENT_DOCUMENT_ELEMENT, READONLY_NULLABLE_INTERFACE),
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
    READONLY_USVSTRING: select_readonly_usvstring_attribute,
    READONLY_ENUM: lambda results, name: select_readonly_enum_attribute(
        results, name, DOCUMENT_VISIBILITY_STATE_VALUES
    ),
    READONLY_UNSIGNED_SHORT: select_readonly_unsigned_short_attribute,
    READONLY_NULLABLE_INTERFACE: lambda results, name: select_readonly_nullable_interface_attribute(
        results, name, "Element"
    ),
}


def select_document_host_attributes(
    cache_dir: Path,
    environment: Mapping[str, str] | None = None,
    webidls_dir: Path = PRODUCTION_WEBIDLS_DIR,
) -> dict[str, WebIDL.IDLAttribute]:
    """Load the production corpus and select the supported Document slice.

    The result is keyed by qualified name and ordered like ``DOCUMENT_HOST``,
    which is the order the generated artifacts declare their members in.
    """

    parser_results = parse_webidl_corpus(webidls_dir, cache_dir, environment)
    return {
        qualified_name: _SHAPE_SELECTORS[shape](parser_results, qualified_name)
        for qualified_name, shape in DOCUMENT_HOST
    }


def _split_qualified_name(qualified_name: str) -> tuple[str, str]:
    parts = qualified_name.split(".")
    if len(parts) != 2 or not all(parts):
        raise WebIDLSelectionError(f"selected member `{qualified_name}` must have the form `Interface.member`")
    return parts[0], parts[1]
