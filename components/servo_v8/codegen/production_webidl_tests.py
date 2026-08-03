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
            attributes = production_webidl.select_document_host_members(
                Path(temporary_directory) / "cache",
                environment={},
            )

        attribute = attributes[production_webidl.DOCUMENT_BG_COLOR]
        self.assertEqual(attribute.identifier.name, "bgColor")
        self.assertFalse(attribute.readonly)
        self.assertFalse(attribute.type.nullable())
        self.assertTrue(attribute.type.isDOMString())
        self.assertTrue(attribute.getExtendedAttribute("CEReactions"))
        self.assertTrue(attribute.type.getExtendedAttribute("LegacyNullToEmptyString"))

    def test_selects_real_document_title_with_ordinary_domstring_conversion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            attributes = production_webidl.select_document_host_members(
                Path(temporary_directory) / "cache",
                environment={},
            )

        attribute = attributes[production_webidl.DOCUMENT_TITLE]
        self.assertEqual(attribute.identifier.name, "title")
        self.assertFalse(attribute.readonly)
        self.assertFalse(attribute.type.nullable())
        self.assertTrue(attribute.type.isDOMString())
        self.assertTrue(attribute.getExtendedAttribute("CEReactions"))
        self.assertFalse(attribute.type.getExtendedAttribute("LegacyNullToEmptyString"))

    def test_selects_real_document_string_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            attributes = production_webidl.select_document_host_members(
                Path(temporary_directory) / "cache",
                environment={},
            )

        self.assertTrue(attributes[production_webidl.DOCUMENT_URI].type.isUSVString())
        domstring_members = (
            production_webidl.DOCUMENT_COMPAT_MODE,
            production_webidl.DOCUMENT_CHARACTER_SET,
            production_webidl.DOCUMENT_CHARSET,
            production_webidl.DOCUMENT_INPUT_ENCODING,
            production_webidl.DOCUMENT_CONTENT_TYPE,
            production_webidl.DOCUMENT_REFERRER,
            production_webidl.DOCUMENT_LAST_MODIFIED,
        )
        for qualified_name in domstring_members:
            with self.subTest(member=qualified_name):
                attribute = attributes[qualified_name]
                self.assertTrue(attribute.readonly)
                self.assertFalse(attribute.type.nullable())
                self.assertTrue(attribute.type.isDOMString())

    def test_selects_the_slice_keyed_in_manifest_order(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            attributes = production_webidl.select_document_host_members(
                Path(temporary_directory) / "cache",
                environment={},
            )

        self.assertEqual(
            list(attributes),
            [member.qualified_name for member in production_webidl.DOCUMENT_HOST],
        )

    def test_pins_each_real_interface_return(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            attributes = production_webidl.select_document_host_members(
                Path(temporary_directory) / "cache",
                environment={},
            )

        self.assertEqual(
            attributes[production_webidl.DOCUMENT_DOCUMENT_ELEMENT].type.inner.name,
            "Element",
        )
        self.assertEqual(
            attributes[production_webidl.DOCUMENT_HEAD].type.inner.name,
            "HTMLHeadElement",
        )
        method = attributes[production_webidl.DOCUMENT_GET_ELEMENT_BY_ID]
        return_type, arguments = method.signatures()[0]
        self.assertEqual(return_type.inner.name, "Element")
        self.assertEqual(arguments[0].identifier.name, "elementId")
        self.assertTrue(arguments[0].type.isDOMString())

    def test_pins_each_real_enum_value_set(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            members = production_webidl.select_document_host_members(
                Path(temporary_directory) / "cache",
                environment={},
            )

        self.assertEqual(
            tuple(
                members[
                    production_webidl.DOCUMENT_VISIBILITY_STATE
                ].type.inner.values()
            ),
            production_webidl.DOCUMENT_VISIBILITY_STATE_VALUES,
        )
        self.assertEqual(
            tuple(
                members[
                    production_webidl.DOCUMENT_READY_STATE
                ].type.inner.values()
            ),
            production_webidl.DOCUMENT_READY_STATE_VALUES,
        )


class ProductionTimerTests(unittest.TestCase):
    def test_pins_all_four_real_timer_operations(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            members = production_webidl.select_timer_host_members(
                Path(temporary_directory) / "cache",
                environment={},
            )

        self.assertEqual(list(members), list(production_webidl.TIMER_HOST))
        for qualified_name in (
            production_webidl.WINDOW_OR_WORKER_SET_TIMEOUT,
            production_webidl.WINDOW_OR_WORKER_SET_INTERVAL,
        ):
            method = members[qualified_name]
            return_type, arguments = method.signatures()[0]
            self.assertEqual(return_type.prettyName(), "long")
            self.assertEqual(
                [part.prettyName() for part in arguments[0].type.memberTypes],
                ["TrustedScript", "DOMString", "Function"],
            )
            self.assertEqual(arguments[1].defaultValue.value, 0)
            self.assertTrue(arguments[2].variadic)
            self.assertTrue(arguments[2].type.isAny())

        for qualified_name in (
            production_webidl.WINDOW_OR_WORKER_CLEAR_TIMEOUT,
            production_webidl.WINDOW_OR_WORKER_CLEAR_INTERVAL,
        ):
            return_type, arguments = members[qualified_name].signatures()[0]
            self.assertEqual(return_type.prettyName(), "undefined")
            self.assertEqual(arguments[0].defaultValue.value, 0)


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

    def test_rejects_unexpected_extended_attribute(self) -> None:
        self.assert_rejected(
            "interface Document { [Throws] readonly attribute boolean hidden; };",
            "`Document.hidden` carries extended attributes that are not implemented: Throws",
        )

    def test_reports_every_unexpected_extended_attribute(self) -> None:
        self.assert_rejected(
            'interface Document { [Throws, Pref="dom.hidden"] readonly attribute boolean hidden; };',
            "`Document.hidden` carries extended attributes that are not implemented: Pref, Throws",
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

    def assert_title_rejected(self, declaration: str, expected: str) -> None:
        parser_results = self.parse(
            {"Document.webidl": f"interface Document {{ {declaration} }};"}
        )
        with self.assertRaisesRegex(
            production_webidl.WebIDLSelectionError,
            re.escape(expected),
        ):
            production_webidl.select_writable_domstring_attribute(
                parser_results,
                production_webidl.DOCUMENT_TITLE,
            )

    def test_rejects_title_without_ce_reactions(self) -> None:
        self.assert_title_rejected(
            "attribute DOMString title;",
            "`Document.title` must carry `[CEReactions]`",
        )

    def test_rejects_readonly_title(self) -> None:
        self.assert_title_rejected(
            "readonly attribute DOMString title;",
            "`Document.title` must be writable",
        )

    def test_rejects_nullable_title(self) -> None:
        self.assert_title_rejected(
            "[CEReactions] attribute DOMString? title;",
            "`Document.title` must be non-nullable",
        )

    def test_rejects_non_domstring_title(self) -> None:
        self.assert_title_rejected(
            "[CEReactions] attribute USVString title;",
            "`Document.title` must use `DOMString`, got `USVString`",
        )

    def test_rejects_title_with_legacy_null_conversion(self) -> None:
        self.assert_title_rejected(
            "[CEReactions] attribute [LegacyNullToEmptyString] DOMString title;",
            "`Document.title` must not carry `[LegacyNullToEmptyString]` on its type",
        )

    def assert_compat_mode_rejected(self, declaration: str, expected: str) -> None:
        parser_results = self.parse(
            {"Document.webidl": f"interface Document {{ {declaration} }};"}
        )
        with self.assertRaisesRegex(
            production_webidl.WebIDLSelectionError,
            re.escape(expected),
        ):
            production_webidl.select_readonly_domstring_attribute(
                parser_results,
                production_webidl.DOCUMENT_COMPAT_MODE,
            )

    def test_rejects_writable_readonly_domstring(self) -> None:
        self.assert_compat_mode_rejected(
            "attribute DOMString compatMode;",
            "`Document.compatMode` must be readonly",
        )

    def test_rejects_nullable_readonly_domstring(self) -> None:
        self.assert_compat_mode_rejected(
            "readonly attribute DOMString? compatMode;",
            "`Document.compatMode` must be non-nullable",
        )

    def test_rejects_usvstring_for_readonly_domstring(self) -> None:
        self.assert_compat_mode_rejected(
            "readonly attribute USVString compatMode;",
            "`Document.compatMode` must use `DOMString`, got `USVString`",
        )

    def test_rejects_unimplemented_readonly_domstring_attribute(self) -> None:
        self.assert_compat_mode_rejected(
            "[Throws] readonly attribute DOMString compatMode;",
            "`Document.compatMode` carries extended attributes that are not implemented: Throws",
        )

    def test_rejects_bg_color_with_extended_attribute_beyond_ce_reactions(self) -> None:
        self.assert_bg_color_rejected(
            "[CEReactions, Throws] attribute [LegacyNullToEmptyString] DOMString bgColor;",
            "`Document.bgColor` carries extended attributes that are not implemented: Throws",
        )

    def test_rejects_the_wrong_nullable_interface_return(self) -> None:
        parser_results = self.parse(
            {
                "Document.webidl": """
                    interface Element {};
                    interface HTMLHeadElement : Element {};
                    interface Document { readonly attribute Element? head; };
                """,
            }
        )

        with self.assertRaisesRegex(
            production_webidl.WebIDLSelectionError,
            re.escape("`Document.head` must return `HTMLHeadElement`, got `Element`"),
        ):
            production_webidl.select_readonly_nullable_interface_attribute(
                parser_results,
                production_webidl.DOCUMENT_HEAD,
                "HTMLHeadElement",
            )

    def test_rejects_a_nonnullable_interface_return(self) -> None:
        parser_results = self.parse(
            {
                "Document.webidl": """
                    interface HTMLHeadElement {};
                    interface Document { readonly attribute HTMLHeadElement head; };
                """,
            }
        )

        with self.assertRaisesRegex(
            production_webidl.WebIDLSelectionError,
            re.escape("`Document.head` must be nullable"),
        ):
            production_webidl.select_readonly_nullable_interface_attribute(
                parser_results,
                production_webidl.DOCUMENT_HEAD,
                "HTMLHeadElement",
            )

    def assert_get_element_by_id_rejected(self, declaration: str, expected: str) -> None:
        parser_results = self.parse(
            {
                "Document.webidl": f"""
                    interface Element {{}};
                    interface Document {{ {declaration} }};
                """,
            }
        )
        with self.assertRaisesRegex(
            production_webidl.WebIDLSelectionError,
            re.escape(expected),
        ):
            production_webidl.select_pure_domstring_to_nullable_interface_operation(
                parser_results,
                production_webidl.DOCUMENT_GET_ELEMENT_BY_ID,
                "Element",
            )

    def test_selects_the_exact_get_element_by_id_operation(self) -> None:
        parser_results = self.parse(
            {
                "Document.webidl": """
                    interface Element {};
                    interface Document {
                      [Pure] Element? getElementById(DOMString elementId);
                    };
                """,
            }
        )

        method = production_webidl.select_pure_domstring_to_nullable_interface_operation(
            parser_results,
            production_webidl.DOCUMENT_GET_ELEMENT_BY_ID,
            "Element",
        )

        self.assertTrue(method.isMethod())
        self.assertTrue(method.getExtendedAttribute("Pure"))

    def test_rejects_get_element_by_id_without_pure(self) -> None:
        self.assert_get_element_by_id_rejected(
            "Element? getElementById(DOMString elementId);",
            "`Document.getElementById` must carry `[Pure]`",
        )

    def test_rejects_unimplemented_get_element_by_id_extended_attribute(self) -> None:
        self.assert_get_element_by_id_rejected(
            "[Pure, Throws] Element? getElementById(DOMString elementId);",
            "`Document.getElementById` carries extended attributes that are not implemented: Throws",
        )

    def test_rejects_overloaded_get_element_by_id(self) -> None:
        self.assert_get_element_by_id_rejected(
            """
              [Pure] Element? getElementById(DOMString elementId);
              [Pure] Element? getElementById(DOMString elementId, DOMString extra);
            """,
            "`Document.getElementById` must have exactly one signature, found 2",
        )

    def test_rejects_nonnullable_get_element_by_id_return(self) -> None:
        self.assert_get_element_by_id_rejected(
            "[Pure] Element getElementById(DOMString elementId);",
            "`Document.getElementById` must return a nullable interface",
        )

    def test_rejects_optional_get_element_by_id_argument(self) -> None:
        self.assert_get_element_by_id_rejected(
            "[Pure] Element? getElementById(optional DOMString elementId);",
            "`Document.getElementById` argument `elementId` must be required and non-variadic",
        )

    def test_rejects_wrong_get_element_by_id_argument_type(self) -> None:
        self.assert_get_element_by_id_rejected(
            "[Pure] Element? getElementById(USVString elementId);",
            "`Document.getElementById` argument `elementId` must use non-nullable `DOMString`, got `USVString`",
        )

    def test_rejects_changed_ready_state_enum_values(self) -> None:
        parser_results = self.parse(
            {
                "Document.webidl": """
                    enum DocumentReadyState { "loading", "complete" };
                    interface Document {
                      readonly attribute DocumentReadyState readyState;
                    };
                """,
            }
        )

        with self.assertRaisesRegex(
            production_webidl.WebIDLSelectionError,
            re.escape(
                "`Document.readyState` must have the values "
                "['loading', 'interactive', 'complete'], got ['loading', 'complete']"
            ),
        ):
            production_webidl.select_readonly_enum_attribute(
                parser_results,
                production_webidl.DOCUMENT_READY_STATE,
                production_webidl.DOCUMENT_READY_STATE_VALUES,
            )

    def assert_timer_rejected(self, declarations: str, member: str, expected: str) -> None:
        parser_results = self.parse(
            {
                "Timers.webidl": f"""
                    interface TrustedScript {{}};
                    callback Function = any (any... arguments);
                    typedef (TrustedScript or DOMString or Function) TimerHandler;
                    interface mixin WindowOrWorkerGlobalScope {{ {declarations} }};
                """,
            }
        )
        with self.assertRaisesRegex(
            production_webidl.WebIDLSelectionError,
            re.escape(expected),
        ):
            production_webidl._select_timer_operation(
                parser_results,
                f"WindowOrWorkerGlobalScope.{member}",
            )

    def test_selects_timer_operation_from_interface_mixin(self) -> None:
        parser_results = self.parse(
            {
                "Timers.webidl": """
                    interface TrustedScript {};
                    callback Function = any (any... arguments);
                    typedef (TrustedScript or DOMString or Function) TimerHandler;
                    interface mixin WindowOrWorkerGlobalScope {
                      [Throws] long setTimeout(
                        TimerHandler handler, optional long timeout = 0, any... arguments);
                    };
                """,
            }
        )
        method = production_webidl._select_timer_operation(
            parser_results,
            production_webidl.WINDOW_OR_WORKER_SET_TIMEOUT,
        )
        self.assertEqual(method.identifier.name, "setTimeout")

    def test_rejects_timer_without_throws(self) -> None:
        self.assert_timer_rejected(
            "long setTimeout(TimerHandler handler, optional long timeout = 0, any... arguments);",
            "setTimeout",
            "`WindowOrWorkerGlobalScope.setTimeout` must carry exactly ['Throws'], got []",
        )

    def test_rejects_changed_timer_handler_union(self) -> None:
        self.assert_timer_rejected(
            "[Throws] long setTimeout((DOMString or Function) handler, optional long timeout = 0, any... arguments);",
            "setTimeout",
            "`WindowOrWorkerGlobalScope.setTimeout` handler union must be "
            "['TrustedScript', 'DOMString', 'Function'], got ['DOMString', 'Function']",
        )

    def test_rejects_changed_timer_default(self) -> None:
        self.assert_timer_rejected(
            "[Throws] long setInterval(TimerHandler handler, optional long timeout = 1, any... arguments);",
            "setInterval",
            "`WindowOrWorkerGlobalScope.setInterval` argument `timeout` must be optional "
            "non-nullable `long` with default 0",
        )

    def test_rejects_nonvariadic_timer_arguments(self) -> None:
        self.assert_timer_rejected(
            "[Throws] long setTimeout(TimerHandler handler, optional long timeout = 0, any arguments);",
            "setTimeout",
            "`WindowOrWorkerGlobalScope.setTimeout` must end with variadic `any... arguments`",
        )

    def test_rejects_changed_clear_signature(self) -> None:
        self.assert_timer_rejected(
            "undefined clearTimeout(optional unsigned long handle = 0);",
            "clearTimeout",
            "`WindowOrWorkerGlobalScope.clearTimeout` argument `handle` must be optional "
            "non-nullable `long` with default 0",
        )


if __name__ == "__main__":
    unittest.main()
