# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from __future__ import annotations

import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True

import production_webidl  # noqa: E402


class ProductionDocumentHiddenTests(unittest.TestCase):
    def test_selects_real_document_hidden(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            attribute = production_webidl.select_document_hidden(
                Path(temporary_directory) / "cache",
                environment={},
            )

        self.assertEqual(attribute.identifier.name, "hidden")
        self.assertTrue(attribute.readonly)
        self.assertFalse(attribute.type.nullable())
        self.assertTrue(attribute.type.isBoolean())

    def test_selects_real_document_bg_color(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            _, attribute = production_webidl.select_document_host_attributes(
                Path(temporary_directory) / "cache",
                environment={},
            )

        self.assertEqual(attribute.identifier.name, "bgColor")
        self.assertFalse(attribute.readonly)
        self.assertFalse(attribute.type.nullable())
        self.assertTrue(attribute.type.isDOMString())
        self.assertTrue(attribute.getExtendedAttribute("CEReactions"))
        self.assertTrue(attribute.type.getExtendedAttribute("LegacyNullToEmptyString"))


class SyntheticSelectionTests(unittest.TestCase):
    def parse(self, sources: dict[str, str], environment: dict[str, str] | None = None):
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        root = Path(temporary_directory.name)
        webidls = root / "webidls"
        webidls.mkdir()
        for filename, source in sources.items():
            (webidls / filename).write_text(source, encoding="utf-8")
        return production_webidl.parse_webidl_corpus(
            webidls,
            root / "cache",
            environment={} if environment is None else environment,
        )

    def assert_rejected(self, source: str, expected: str) -> None:
        parser_results = self.parse({"Document.webidl": source})
        with self.assertRaisesRegex(
            production_webidl.WebIDLSelectionError,
            re.escape(expected),
        ):
            production_webidl.select_readonly_boolean_attribute(
                parser_results,
                production_webidl.DOCUMENT_HIDDEN,
            )

    def test_selects_attribute_from_partial_interface(self) -> None:
        parser_results = self.parse(
            {
                "Document.webidl": "interface Document {};",
                "DocumentPartial.webidl": """
                    partial interface Document {
                      readonly attribute boolean hidden;
                    };
                """,
            }
        )

        attribute = production_webidl.select_readonly_boolean_attribute(
            parser_results,
            production_webidl.DOCUMENT_HIDDEN,
        )

        self.assertEqual(attribute.identifier.name, "hidden")

    def test_honors_skip_unless(self) -> None:
        sources = {
            "Always.webidl": "interface Always {};",
            "Conditional.webidl": """// skip-unless ENABLE_CONDITIONAL
                interface Conditional {};
            """,
        }

        disabled = self.parse(sources)
        enabled = self.parse(sources, environment={"ENABLE_CONDITIONAL": "1"})

        self.assertEqual(
            [result.identifier.name for result in disabled if result.isInterface()],
            ["Always"],
        )
        self.assertEqual(
            sorted(result.identifier.name for result in enabled if result.isInterface()),
            ["Always", "Conditional"],
        )

    def test_rejects_missing_interface(self) -> None:
        self.assert_rejected(
            "interface Other { readonly attribute boolean hidden; };",
            "expected exactly one interface `Document`, found 0",
        )

    def test_rejects_missing_member(self) -> None:
        self.assert_rejected(
            "interface Document { readonly attribute boolean visible; };",
            "expected exactly one member `Document.hidden`, found 0",
        )

    def test_rejects_operation(self) -> None:
        self.assert_rejected(
            "interface Document { boolean hidden(); };",
            "`Document.hidden` must be an attribute",
        )

    def test_rejects_writable_attribute(self) -> None:
        self.assert_rejected(
            "interface Document { attribute boolean hidden; };",
            "`Document.hidden` must be readonly",
        )

    def test_rejects_nullable_attribute(self) -> None:
        self.assert_rejected(
            "interface Document { readonly attribute boolean? hidden; };",
            "`Document.hidden` must be non-nullable",
        )

    def test_rejects_non_boolean_attribute(self) -> None:
        self.assert_rejected(
            "interface Document { readonly attribute long hidden; };",
            "`Document.hidden` must use `boolean`, got `long`",
        )

    def test_rejects_malformed_qualified_name(self) -> None:
        parser_results = self.parse({"Document.webidl": "interface Document { readonly attribute boolean hidden; };"})
        with self.assertRaisesRegex(
            production_webidl.WebIDLSelectionError,
            re.escape("selected member `hidden` must have the form `Interface.member`"),
        ):
            production_webidl.select_readonly_boolean_attribute(parser_results, "hidden")

    def assert_bg_color_rejected(self, declaration: str, expected: str) -> None:
        parser_results = self.parse({"Document.webidl": f"interface Document {{ {declaration} }};"})
        with self.assertRaisesRegex(
            production_webidl.WebIDLSelectionError,
            re.escape(expected),
        ):
            production_webidl.select_writable_legacy_domstring_attribute(
                parser_results,
                production_webidl.DOCUMENT_BG_COLOR,
            )

    def test_rejects_readonly_bg_color(self) -> None:
        self.assert_bg_color_rejected(
            "readonly attribute DOMString bgColor;",
            "`Document.bgColor` must be writable",
        )

    def test_rejects_nullable_bg_color(self) -> None:
        self.assert_bg_color_rejected(
            "[CEReactions] attribute DOMString? bgColor;",
            "`Document.bgColor` must be non-nullable",
        )

    def test_rejects_non_domstring_bg_color(self) -> None:
        self.assert_bg_color_rejected(
            "[CEReactions] attribute USVString bgColor;",
            "`Document.bgColor` must use `DOMString`, got `USVString`",
        )

    def test_rejects_bg_color_without_ce_reactions(self) -> None:
        self.assert_bg_color_rejected(
            "attribute [LegacyNullToEmptyString] DOMString bgColor;",
            "`Document.bgColor` must carry `[CEReactions]`",
        )

    def test_rejects_bg_color_without_legacy_null_conversion(self) -> None:
        self.assert_bg_color_rejected(
            "[CEReactions] attribute DOMString bgColor;",
            "`Document.bgColor` must carry `[LegacyNullToEmptyString]` on its type",
        )


if __name__ == "__main__":
    unittest.main()
