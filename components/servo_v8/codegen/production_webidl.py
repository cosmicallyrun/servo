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


def select_document_hidden(
    cache_dir: Path,
    environment: Mapping[str, str] | None = None,
    webidls_dir: Path = PRODUCTION_WEBIDLS_DIR,
) -> WebIDL.IDLAttribute:
    """Load Servo's production corpus and select ``Document.hidden``."""

    parser_results = parse_webidl_corpus(webidls_dir, cache_dir, environment)
    return select_readonly_boolean_attribute(parser_results, DOCUMENT_HIDDEN)


def select_document_host_attributes(
    cache_dir: Path,
    environment: Mapping[str, str] | None = None,
    webidls_dir: Path = PRODUCTION_WEBIDLS_DIR,
) -> tuple[WebIDL.IDLAttribute, WebIDL.IDLAttribute]:
    """Load the production corpus and select the supported Document slice."""

    parser_results = parse_webidl_corpus(webidls_dir, cache_dir, environment)
    return (
        select_readonly_boolean_attribute(parser_results, DOCUMENT_HIDDEN),
        select_writable_legacy_domstring_attribute(parser_results, DOCUMENT_BG_COLOR),
    )


def _split_qualified_name(qualified_name: str) -> tuple[str, str]:
    parts = qualified_name.split(".")
    if len(parts) != 2 or not all(parts):
        raise WebIDLSelectionError(f"selected member `{qualified_name}` must have the form `Interface.member`")
    return parts[0], parts[1]
