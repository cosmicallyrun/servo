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
        children = attributes[production_webidl.DOCUMENT_CHILDREN]
        self.assertTrue(children.readonly)
        self.assertFalse(children.type.nullable())
        self.assertEqual(children.type.name, "HTMLCollection")
        self.assertEqual(set(children._extendedAttrDict), {"SameObject"})
        for qualified_name in (
            production_webidl.DOCUMENT_FIRST_ELEMENT_CHILD,
            production_webidl.DOCUMENT_LAST_ELEMENT_CHILD,
        ):
            with self.subTest(member=qualified_name):
                attribute = attributes[qualified_name]
                self.assertTrue(attribute.readonly)
                self.assertTrue(attribute.type.nullable())
                self.assertEqual(attribute.type.inner.name, "Element")
                self.assertEqual(set(attribute._extendedAttrDict), {"Pure"})
        child_count = attributes[production_webidl.DOCUMENT_CHILD_ELEMENT_COUNT]
        self.assertTrue(child_count.readonly)
        self.assertEqual(child_count.type.prettyName(), "unsigned long")
        self.assertEqual(set(child_count._extendedAttrDict), {"Pure"})
        class_query = attributes[
            production_webidl.DOCUMENT_GET_ELEMENTS_BY_CLASS_NAME
        ]
        return_type, arguments = class_query.signatures()[0]
        self.assertEqual(return_type.name, "HTMLCollection")
        self.assertFalse(return_type.nullable())
        self.assertEqual(arguments[0].identifier.name, "classNames")
        self.assertTrue(arguments[0].type.isDOMString())
        self.assertEqual(set(class_query._extendedAttrDict), set())
        tag_query = attributes[
            production_webidl.DOCUMENT_GET_ELEMENTS_BY_TAG_NAME
        ]
        tag_return, tag_arguments = tag_query.signatures()[0]
        self.assertEqual(tag_return.name, "HTMLCollection")
        self.assertFalse(tag_return.nullable())
        self.assertEqual(tag_arguments[0].identifier.name, "qualifiedName")
        self.assertTrue(tag_arguments[0].type.isDOMString())
        self.assertEqual(set(tag_query._extendedAttrDict), set())
        tag_ns_query = attributes[
            production_webidl.DOCUMENT_GET_ELEMENTS_BY_TAG_NAME_NS
        ]
        tag_ns_return, tag_ns_arguments = tag_ns_query.signatures()[0]
        self.assertEqual(tag_ns_return.name, "HTMLCollection")
        self.assertFalse(tag_ns_return.nullable())
        self.assertEqual(
            [argument.identifier.name for argument in tag_ns_arguments],
            ["namespace", "qualifiedName"],
        )
        self.assertTrue(tag_ns_arguments[0].type.nullable())
        self.assertTrue(tag_ns_arguments[0].type.inner.isDOMString())
        self.assertFalse(tag_ns_arguments[1].type.nullable())
        self.assertTrue(tag_ns_arguments[1].type.isDOMString())
        self.assertEqual(set(tag_ns_query._extendedAttrDict), set())
        method = attributes[production_webidl.DOCUMENT_GET_ELEMENT_BY_ID]
        return_type, arguments = method.signatures()[0]
        self.assertEqual(return_type.inner.name, "Element")
        self.assertEqual(arguments[0].identifier.name, "elementId")
        self.assertTrue(arguments[0].type.isDOMString())
        query_all = attributes[production_webidl.DOCUMENT_QUERY_SELECTOR_ALL]
        return_type, arguments = query_all.signatures()[0]
        self.assertEqual(return_type.name, "NodeList")
        self.assertEqual(arguments[0].identifier.name, "selectors")
        self.assertEqual(set(query_all._extendedAttrDict), {"NewObject", "Throws"})
        create_element = attributes[production_webidl.DOCUMENT_CREATE_ELEMENT]
        create_return, create_arguments = create_element.signatures()[0]
        self.assertEqual(create_return.name, "Element")
        self.assertFalse(create_return.nullable())
        self.assertEqual(
            set(create_element._extendedAttrDict), {"CEReactions", "NewObject", "Throws"}
        )
        self.assertEqual(
            [argument.identifier.name for argument in create_arguments],
            ["localName", "options"],
        )
        self.assertTrue(create_arguments[0].type.isDOMString())
        self.assertFalse(create_arguments[0].optional)
        self.assertTrue(create_arguments[1].optional)
        self.assertEqual(
            [type_.prettyName() for type_ in create_arguments[1].type.memberTypes],
            ["DOMString", "ElementCreationOptions"],
        )
        self.assertIsNone(create_arguments[1].defaultValue.value)
        fragment = attributes[production_webidl.DOCUMENT_CREATE_DOCUMENT_FRAGMENT]
        fragment_return, fragment_arguments = fragment.signatures()[0]
        self.assertEqual(fragment_return.name, "DocumentFragment")
        self.assertFalse(fragment_return.nullable())
        self.assertEqual(fragment_arguments, [])
        self.assertEqual(set(fragment._extendedAttrDict), {"NewObject"})
        text = attributes[production_webidl.DOCUMENT_CREATE_TEXT_NODE]
        text_return, text_arguments = text.signatures()[0]
        self.assertEqual(text_return.name, "Text")
        self.assertFalse(text_return.nullable())
        self.assertEqual(set(text._extendedAttrDict), {"NewObject"})
        self.assertEqual([argument.identifier.name for argument in text_arguments], ["data"])
        self.assertTrue(text_arguments[0].type.isDOMString())
        self.assertFalse(text_arguments[0].optional)

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


class ProductionConsoleTests(unittest.TestCase):
    def test_pins_the_real_console_logging_slice(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            members = production_webidl.select_console_host_members(
                Path(temporary_directory) / "cache",
                environment={},
            )

        self.assertEqual(list(members), list(production_webidl.CONSOLE_HOST))
        for qualified_name, method in members.items():
            with self.subTest(member=qualified_name):
                return_type, arguments = method.signatures()[0]
                self.assertEqual(return_type.prettyName(), "undefined")
                self.assertEqual(len(arguments), 1)
                self.assertTrue(arguments[0].variadic)
                self.assertTrue(arguments[0].type.isAny())
                expected_name = (
                    "data"
                    if qualified_name == production_webidl.CONSOLE_TRACE
                    else "messages"
                )
                self.assertEqual(arguments[0].identifier.name, expected_name)


class ProductionElementTests(unittest.TestCase):
    def test_pins_the_real_scalar_element_slice(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            members = production_webidl.select_element_host_members(
                Path(temporary_directory) / "cache",
                environment={},
            )

        self.assertEqual(list(members), list(production_webidl.ELEMENT_HOST))
        self.assertTrue(members[production_webidl.ELEMENT_LOCAL_NAME].readonly)
        self.assertTrue(members[production_webidl.ELEMENT_TAG_NAME].readonly)
        self.assertFalse(members[production_webidl.ELEMENT_ID].readonly)
        self.assertFalse(members[production_webidl.ELEMENT_CLASS_NAME].readonly)
        for qualified_name in (
            production_webidl.ELEMENT_NAMESPACE_URI,
            production_webidl.ELEMENT_PREFIX,
        ):
            with self.subTest(member=qualified_name):
                attribute = members[qualified_name]
                self.assertTrue(attribute.readonly)
                self.assertTrue(attribute.type.nullable())
                self.assertTrue(attribute.type.inner.isDOMString())
                self.assertEqual(set(attribute._extendedAttrDict), {"Constant"})
        children = members[production_webidl.ELEMENT_CHILDREN]
        self.assertTrue(children.readonly)
        self.assertFalse(children.type.nullable())
        self.assertEqual(children.type.name, "HTMLCollection")
        self.assertEqual(set(children._extendedAttrDict), {"SameObject"})
        return_type, arguments = members[
            production_webidl.ELEMENT_GET_ATTRIBUTE
        ].signatures()[0]
        self.assertTrue(return_type.nullable())
        self.assertTrue(return_type.inner.isDOMString())
        self.assertEqual(arguments[0].identifier.name, "name")
        get_attribute_ns = members[production_webidl.ELEMENT_GET_ATTRIBUTE_NS]
        get_ns_return, get_ns_arguments = get_attribute_ns.signatures()[0]
        self.assertTrue(get_ns_return.nullable())
        self.assertTrue(get_ns_return.inner.isDOMString())
        self.assertEqual(
            [argument.identifier.name for argument in get_ns_arguments],
            ["namespace", "localName"],
        )
        self.assertTrue(get_ns_arguments[0].type.nullable())
        self.assertTrue(get_ns_arguments[0].type.inner.isDOMString())
        self.assertFalse(get_ns_arguments[1].type.nullable())
        self.assertTrue(get_ns_arguments[1].type.isDOMString())
        self.assertEqual(set(get_attribute_ns._extendedAttrDict), {"Pure"})
        has_attribute_ns = members[production_webidl.ELEMENT_HAS_ATTRIBUTE_NS]
        has_ns_return, has_ns_arguments = has_attribute_ns.signatures()[0]
        self.assertTrue(has_ns_return.isBoolean())
        self.assertFalse(has_ns_return.nullable())
        self.assertEqual(
            [argument.identifier.name for argument in has_ns_arguments],
            ["namespace", "localName"],
        )
        self.assertTrue(has_ns_arguments[0].type.nullable())
        self.assertTrue(has_ns_arguments[0].type.inner.isDOMString())
        self.assertFalse(has_ns_arguments[1].type.nullable())
        self.assertTrue(has_ns_arguments[1].type.isDOMString())
        self.assertEqual(set(has_attribute_ns._extendedAttrDict), set())
        get_attribute_names = members[production_webidl.ELEMENT_GET_ATTRIBUTE_NAMES]
        names_return, names_arguments = get_attribute_names.signatures()[0]
        self.assertFalse(names_return.nullable())
        self.assertTrue(names_return.isSequence())
        self.assertTrue(names_return.inner.isDOMString())
        self.assertEqual(names_arguments, [])
        self.assertEqual(set(get_attribute_names._extendedAttrDict), {"Pure"})
        toggle_attribute = members[production_webidl.ELEMENT_TOGGLE_ATTRIBUTE]
        toggle_return, toggle_arguments = toggle_attribute.signatures()[0]
        self.assertTrue(toggle_return.isBoolean())
        self.assertFalse(toggle_return.nullable())
        self.assertEqual(set(toggle_attribute._extendedAttrDict), {"CEReactions", "Throws"})
        self.assertEqual(
            [argument.identifier.name for argument in toggle_arguments], ["name", "force"]
        )
        self.assertTrue(toggle_arguments[0].type.isDOMString())
        self.assertFalse(toggle_arguments[0].optional)
        self.assertTrue(toggle_arguments[1].type.isBoolean())
        self.assertTrue(toggle_arguments[1].optional)
        self.assertIsNone(toggle_arguments[1].defaultValue)
        set_attribute = members[production_webidl.ELEMENT_SET_ATTRIBUTE]
        set_return, set_arguments = set_attribute.signatures()[0]
        self.assertEqual(set_return.prettyName(), "undefined")
        self.assertFalse(set_return.nullable())
        self.assertEqual(set(set_attribute._extendedAttrDict), {"CEReactions", "Throws"})
        self.assertEqual(
            [argument.identifier.name for argument in set_arguments], ["name", "value"]
        )
        self.assertTrue(set_arguments[0].type.isDOMString())
        value_type = set_arguments[1].type
        self.assertTrue(value_type.isUnion())
        self.assertEqual(
            [type_.prettyName() for type_ in value_type.memberTypes],
            ["(TrustedHTML or TrustedScript or TrustedScriptURL)", "DOMString"],
        )
        self.assertEqual(
            [type_.prettyName() for type_ in value_type.memberTypes[0].memberTypes],
            ["TrustedHTML", "TrustedScript", "TrustedScriptURL"],
        )
        remove_attribute = members[production_webidl.ELEMENT_REMOVE_ATTRIBUTE]
        remove_return, remove_arguments = remove_attribute.signatures()[0]
        self.assertEqual(remove_return.prettyName(), "undefined")
        self.assertFalse(remove_return.nullable())
        self.assertEqual(set(remove_attribute._extendedAttrDict), {"CEReactions"})
        self.assertEqual([argument.identifier.name for argument in remove_arguments], ["name"])
        self.assertTrue(remove_arguments[0].type.isDOMString())
        self.assertFalse(remove_arguments[0].optional)
        remove_attribute_ns = members[production_webidl.ELEMENT_REMOVE_ATTRIBUTE_NS]
        remove_ns_return, remove_ns_arguments = remove_attribute_ns.signatures()[0]
        self.assertEqual(remove_ns_return.prettyName(), "undefined")
        self.assertFalse(remove_ns_return.nullable())
        self.assertEqual(set(remove_attribute_ns._extendedAttrDict), {"CEReactions"})
        self.assertEqual(
            [argument.identifier.name for argument in remove_ns_arguments],
            ["namespace", "localName"],
        )
        self.assertTrue(remove_ns_arguments[0].type.nullable())
        self.assertTrue(remove_ns_arguments[0].type.inner.isDOMString())
        self.assertFalse(remove_ns_arguments[0].optional)
        self.assertFalse(remove_ns_arguments[1].type.nullable())
        self.assertTrue(remove_ns_arguments[1].type.isDOMString())
        self.assertFalse(remove_ns_arguments[1].optional)
        for qualified_name in (
            production_webidl.ELEMENT_FIRST_ELEMENT_CHILD,
            production_webidl.ELEMENT_LAST_ELEMENT_CHILD,
            production_webidl.ELEMENT_PREVIOUS_ELEMENT_SIBLING,
            production_webidl.ELEMENT_NEXT_ELEMENT_SIBLING,
        ):
            with self.subTest(member=qualified_name):
                attribute = members[qualified_name]
                self.assertTrue(attribute.readonly)
                self.assertTrue(attribute.type.nullable())
                self.assertEqual(attribute.type.inner.name, "Element")
                self.assertEqual(set(attribute._extendedAttrDict), {"Pure"})
        child_count = members[production_webidl.ELEMENT_CHILD_ELEMENT_COUNT]
        self.assertTrue(child_count.readonly)
        self.assertEqual(child_count.type.prettyName(), "unsigned long")
        closest_return, closest_arguments = members[
            production_webidl.ELEMENT_CLOSEST
        ].signatures()[0]
        self.assertTrue(closest_return.nullable())
        self.assertEqual(closest_return.inner.name, "Element")
        self.assertEqual(closest_arguments[0].identifier.name, "selectors")
        for qualified_name in (
            production_webidl.ELEMENT_MATCHES,
            production_webidl.ELEMENT_WEBKIT_MATCHES_SELECTOR,
        ):
            with self.subTest(member=qualified_name):
                method = members[qualified_name]
                return_type, arguments = method.signatures()[0]
                self.assertTrue(return_type.isBoolean())
                self.assertEqual(arguments[0].identifier.name, "selectors")
                self.assertEqual(set(method._extendedAttrDict), {"Pure", "Throws"})
        class_query = members[
            production_webidl.ELEMENT_GET_ELEMENTS_BY_CLASS_NAME
        ]
        class_return, class_arguments = class_query.signatures()[0]
        self.assertEqual(class_return.name, "HTMLCollection")
        self.assertFalse(class_return.nullable())
        self.assertEqual(class_arguments[0].identifier.name, "classNames")
        self.assertTrue(class_arguments[0].type.isDOMString())
        self.assertEqual(set(class_query._extendedAttrDict), set())
        tag_query = members[production_webidl.ELEMENT_GET_ELEMENTS_BY_TAG_NAME]
        tag_return, tag_arguments = tag_query.signatures()[0]
        self.assertEqual(tag_return.name, "HTMLCollection")
        self.assertFalse(tag_return.nullable())
        self.assertEqual(tag_arguments[0].identifier.name, "localName")
        self.assertTrue(tag_arguments[0].type.isDOMString())
        self.assertEqual(set(tag_query._extendedAttrDict), set())
        tag_ns_query = members[production_webidl.ELEMENT_GET_ELEMENTS_BY_TAG_NAME_NS]
        tag_ns_return, tag_ns_arguments = tag_ns_query.signatures()[0]
        self.assertEqual(tag_ns_return.name, "HTMLCollection")
        self.assertFalse(tag_ns_return.nullable())
        self.assertEqual(
            [argument.identifier.name for argument in tag_ns_arguments],
            ["namespace", "localName"],
        )
        self.assertTrue(tag_ns_arguments[0].type.nullable())
        self.assertTrue(tag_ns_arguments[0].type.inner.isDOMString())
        self.assertFalse(tag_ns_arguments[1].type.nullable())
        self.assertTrue(tag_ns_arguments[1].type.isDOMString())
        self.assertEqual(set(tag_ns_query._extendedAttrDict), set())
        query_all_return, query_all_arguments = members[
            production_webidl.ELEMENT_QUERY_SELECTOR_ALL
        ].signatures()[0]
        self.assertEqual(query_all_return.name, "NodeList")
        self.assertEqual(query_all_arguments[0].identifier.name, "selectors")

    def test_pins_real_childnode_remove_included_on_element(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            members = production_webidl.select_element_host_members(
                Path(temporary_directory) / "cache",
                environment={},
            )

        remove = members[production_webidl.ELEMENT_REMOVE]
        self.assertEqual(remove._name.QName(), "::ChildNode::remove")
        self.assertEqual(set(remove._extendedAttrDict), {"CEReactions", "Unscopable"})
        return_type, arguments = remove.signatures()[0]
        self.assertEqual(return_type.prettyName(), "undefined")
        self.assertFalse(return_type.nullable())
        self.assertEqual(arguments, [])


class ProductionHTMLCollectionTests(unittest.TestCase):
    def test_pins_the_complete_real_interface(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            interface = production_webidl.select_html_collection_interface(
                Path(temporary_directory) / "cache",
                environment={},
            )

        self.assertEqual(interface.identifier.name, "HTMLCollection")
        self.assertEqual(
            interface._extendedAttrDict,
            {"Exposed": ["Window"], "LegacyUnenumerableNamedProperties": True},
        )
        self.assertIsNone(interface.ctor())
        self.assertIsNone(interface.parent)
        self.assertEqual(
            [member.identifier.name for member in interface.members],
            ["length", "item", "namedItem"],
        )
        length, item, named_item = interface.members
        self.assertTrue(length.readonly)
        self.assertEqual(length.type.prettyName(), "unsigned long")
        self.assertEqual(set(length._extendedAttrDict), {"Pure"})
        self.assertTrue(item.isGetter())
        self.assertTrue(item.isIndexed())
        self.assertFalse(item.isNamed())
        self.assertTrue(named_item.isGetter())
        self.assertTrue(named_item.isNamed())
        self.assertFalse(named_item.isIndexed())
        for member, argument_name, argument_type in (
            (item, "index", "unsigned long"),
            (named_item, "name", "DOMString"),
        ):
            with self.subTest(member=member.identifier.name):
                return_type, arguments = member.signatures()[0]
                self.assertTrue(return_type.nullable())
                self.assertEqual(return_type.inner.name, "Element")
                self.assertEqual(arguments[0].identifier.name, argument_name)
                self.assertEqual(arguments[0].type.prettyName(), argument_type)
                self.assertEqual(set(member._extendedAttrDict), {"Pure"})


class ProductionNodeTests(unittest.TestCase):
    def test_pins_the_real_scalar_node_slice(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            members = production_webidl.select_node_host_members(
                Path(temporary_directory) / "cache",
                environment={},
            )

        self.assertEqual(list(members), list(production_webidl.NODE_HOST))
        self.assertTrue(members[production_webidl.NODE_NODE_TYPE].readonly)
        self.assertTrue(members[production_webidl.NODE_NODE_NAME].readonly)
        self.assertTrue(members[production_webidl.NODE_IS_CONNECTED].readonly)
        parent_element = members[production_webidl.NODE_PARENT_ELEMENT]
        self.assertTrue(parent_element.readonly)
        self.assertTrue(parent_element.type.nullable())
        self.assertTrue(parent_element.type.inner.isInterface())
        self.assertEqual(parent_element.type.inner.name, "Element")
        self.assertEqual(set(parent_element._extendedAttrDict), {"Pure"})
        text_content = members[production_webidl.NODE_TEXT_CONTENT]
        self.assertFalse(text_content.readonly)
        self.assertTrue(text_content.type.nullable())
        return_type, arguments = members[
            production_webidl.NODE_HAS_CHILD_NODES
        ].signatures()[0]
        self.assertTrue(return_type.isBoolean())
        self.assertEqual(arguments, [])
        expected_mutations = (
            (production_webidl.NODE_INSERT_BEFORE, (("node", False), ("child", True))),
            (production_webidl.NODE_APPEND_CHILD, (("node", False),)),
            (production_webidl.NODE_REPLACE_CHILD, (("node", False), ("child", False))),
            (production_webidl.NODE_REMOVE_CHILD, (("child", False),)),
        )
        for qualified_name, expected_arguments in expected_mutations:
            with self.subTest(member=qualified_name):
                method = members[qualified_name]
                return_type, arguments = method.signatures()[0]
                self.assertFalse(return_type.nullable())
                self.assertTrue(return_type.isInterface())
                self.assertEqual(return_type.name, "Node")
                self.assertEqual(set(method._extendedAttrDict), {"CEReactions", "Throws"})
                self.assertEqual(len(arguments), len(expected_arguments))
                for argument, (expected_name, expected_nullable) in zip(
                    arguments, expected_arguments, strict=True
                ):
                    self.assertEqual(argument.identifier.name, expected_name)
                    self.assertFalse(argument.optional)
                    self.assertFalse(argument.variadic)
                    self.assertIsNone(argument.defaultValue)
                    self.assertEqual(argument.type.nullable(), expected_nullable)
                    node_type = argument.type.inner if expected_nullable else argument.type
                    self.assertTrue(node_type.isInterface())
                    self.assertEqual(node_type.name, "Node")
                    self.assertFalse(argument._extendedAttrDict)
                    self.assertFalse(argument.type._extendedAttrDict)

    def test_pins_real_node_parent_element_as_inherited_element_value(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            members = production_webidl.select_node_host_members(
                Path(temporary_directory) / "cache",
                environment={},
            )

        parent_element = members[production_webidl.NODE_PARENT_ELEMENT]
        self.assertEqual(list(members), list(production_webidl.NODE_HOST))
        self.assertTrue(parent_element.readonly)
        self.assertTrue(parent_element.type.nullable())
        self.assertEqual(parent_element.type.inner.name, "Element")
        self.assertEqual(set(parent_element._extendedAttrDict), {"Pure"})


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

    def create_element_source(
        self,
        declaration: str = (
            "[CEReactions, NewObject, Throws] "
            "Element createElement(DOMString localName, "
            "optional (DOMString or ElementCreationOptions) options = {});"
        ),
        dictionary: str = "dictionary ElementCreationOptions { DOMString is; };",
    ) -> str:
        return f"""
            interface Element {{}};
            {dictionary}
            interface Document {{ {declaration} }};
        """

    def assert_create_element_rejected(
        self,
        declaration: str,
        expected: str,
        dictionary: str = "dictionary ElementCreationOptions { DOMString is; };",
    ) -> None:
        parser_results = self.parse(
            {"Document.webidl": self.create_element_source(declaration, dictionary)}
        )
        with self.assertRaisesRegex(
            production_webidl.WebIDLSelectionError,
            re.escape(expected),
        ):
            production_webidl._select_create_element_operation(
                parser_results, production_webidl.DOCUMENT_CREATE_ELEMENT
            )

    def test_selects_exact_create_element_operation(self) -> None:
        parser_results = self.parse(
            {"Document.webidl": self.create_element_source()}
        )
        method = production_webidl._select_create_element_operation(
            parser_results, production_webidl.DOCUMENT_CREATE_ELEMENT
        )
        return_type, arguments = method.signatures()[0]

        self.assertEqual(return_type.name, "Element")
        self.assertEqual(
            set(method._extendedAttrDict), {"CEReactions", "NewObject", "Throws"}
        )
        self.assertEqual(
            [argument.identifier.name for argument in arguments], ["localName", "options"]
        )
        self.assertTrue(arguments[0].type.isDOMString())
        self.assertFalse(arguments[0].optional)
        self.assertTrue(arguments[1].optional)
        self.assertEqual(
            [member.prettyName() for member in arguments[1].type.memberTypes],
            ["DOMString", "ElementCreationOptions"],
        )
        self.assertIsNone(arguments[1].defaultValue.value)

    def test_rejects_create_element_shape_drift(self) -> None:
        self.assert_create_element_rejected(
            "[CEReactions, NewObject] Element createElement(DOMString localName, "
            "optional (DOMString or ElementCreationOptions) options = {});",
            "`Document.createElement` must carry exactly ['CEReactions', 'NewObject', 'Throws']",
        )
        self.assert_create_element_rejected(
            "[CEReactions, NewObject, Throws] Element createElement(DOMString? localName, "
            "optional (DOMString or ElementCreationOptions) options = {});",
            "`Document.createElement` first argument must be required non-nullable `DOMString localName`",
        )
        self.assert_create_element_rejected(
            "[CEReactions, NewObject, Throws] Element createElement(DOMString localName, "
            "optional (ElementCreationOptions or DOMString) options = {});",
            "`Document.createElement` second argument must be optional non-nullable "
            "`(DOMString or ElementCreationOptions) options = {}`",
        )
        self.assert_create_element_rejected(
            "[CEReactions, NewObject, Throws] Element createElement(DOMString localName, "
            "optional (DOMString or ElementCreationOptions) options = \"legacy\");",
            "`Document.createElement` second argument must be optional non-nullable "
            "`(DOMString or ElementCreationOptions) options = {}`",
        )
        self.assert_create_element_rejected(
            "[CEReactions, NewObject, Throws] Element createElement(DOMString localName, "
            "optional (DOMString or ElementCreationOptions) options = {});",
            "`ElementCreationOptions.is` must be optional non-nullable `DOMString is`",
            "dictionary ElementCreationOptions { DOMString? is; };",
        )

    def create_document_fragment_source(
        self,
        declaration: str = "[NewObject] DocumentFragment createDocumentFragment();",
    ) -> str:
        return f"""
            interface DocumentFragment {{}};
            interface Element {{}};
            interface Document {{ {declaration} }};
        """

    def assert_create_document_fragment_rejected(
        self, declaration: str, expected: str
    ) -> None:
        parser_results = self.parse(
            {"Document.webidl": self.create_document_fragment_source(declaration)}
        )
        with self.assertRaisesRegex(
            production_webidl.WebIDLSelectionError, re.escape(expected)
        ):
            production_webidl._select_create_document_fragment_operation(
                parser_results, production_webidl.DOCUMENT_CREATE_DOCUMENT_FRAGMENT
            )

    def test_selects_exact_create_document_fragment_operation(self) -> None:
        parser_results = self.parse(
            {"Document.webidl": self.create_document_fragment_source()}
        )
        method = production_webidl._select_create_document_fragment_operation(
            parser_results, production_webidl.DOCUMENT_CREATE_DOCUMENT_FRAGMENT
        )
        self.assertEqual(method.identifier.name, "createDocumentFragment")
        self.assertEqual(set(method._extendedAttrDict), {"NewObject"})
        self.assertEqual(method.signatures()[0][1], [])

    def test_rejects_create_document_fragment_shape_drift(self) -> None:
        self.assert_create_document_fragment_rejected(
            "DocumentFragment createDocumentFragment();",
            "`Document.createDocumentFragment` must carry exactly ['NewObject']",
        )
        self.assert_create_document_fragment_rejected(
            "[NewObject] DocumentFragment? createDocumentFragment();",
            "must return non-nullable `DocumentFragment`",
        )
        self.assert_create_document_fragment_rejected(
            "[NewObject] Element createDocumentFragment();",
            "must return non-nullable `DocumentFragment`",
        )
        self.assert_create_document_fragment_rejected(
            "[NewObject] DocumentFragment createDocumentFragment(DOMString value);",
            "must take zero arguments",
        )

    def create_text_node_source(
        self,
        declaration: str = "[NewObject] Text createTextNode(DOMString data);",
    ) -> str:
        return f"""
            interface Text {{}};
            interface Element {{}};
            interface Document {{ {declaration} }};
        """

    def assert_create_text_node_rejected(self, declaration: str, expected: str) -> None:
        parser_results = self.parse(
            {"Document.webidl": self.create_text_node_source(declaration)}
        )
        with self.assertRaisesRegex(
            production_webidl.WebIDLSelectionError, re.escape(expected)
        ):
            production_webidl._select_create_text_node_operation(
                parser_results, production_webidl.DOCUMENT_CREATE_TEXT_NODE
            )

    def test_selects_exact_create_text_node_operation(self) -> None:
        parser_results = self.parse(
            {"Document.webidl": self.create_text_node_source()}
        )
        method = production_webidl._select_create_text_node_operation(
            parser_results, production_webidl.DOCUMENT_CREATE_TEXT_NODE
        )
        return_type, arguments = method.signatures()[0]
        self.assertEqual(return_type.name, "Text")
        self.assertEqual([argument.identifier.name for argument in arguments], ["data"])
        self.assertEqual(set(method._extendedAttrDict), {"NewObject"})

    def test_rejects_create_text_node_shape_drift(self) -> None:
        self.assert_create_text_node_rejected(
            "Text createTextNode(DOMString data);",
            "must carry exactly ['NewObject']",
        )
        self.assert_create_text_node_rejected(
            "[NewObject] Text? createTextNode(DOMString data);",
            "must return non-nullable Text",
        )
        self.assert_create_text_node_rejected(
            "[NewObject] Element createTextNode(DOMString data);",
            "must return non-nullable Text",
        )
        self.assert_create_text_node_rejected(
            "[NewObject] Text createTextNode(DOMString? data);",
            "argument must be required non-nullable DOMString data",
        )
        self.assert_create_text_node_rejected(
            "[NewObject] Text createTextNode(optional DOMString data);",
            "argument must be required non-nullable DOMString data",
        )
        self.assert_create_text_node_rejected(
            "[NewObject] Text createTextNode(DOMString value);",
            "argument must be required non-nullable DOMString data",
        )

    def html_collection_source(
        self,
        *,
        interface_attributes: str = (
            "[Exposed=Window, LegacyUnenumerableNamedProperties]"
        ),
        constructor: str = "",
        length: str = "[Pure] readonly attribute unsigned long length;",
        item: str = "[Pure] getter Element? item(unsigned long index);",
        named_item: str = "[Pure] getter Element? namedItem(DOMString name);",
        extra: str = "",
    ) -> str:
        return f"""
            [Global=Window, Exposed=Window] interface Window {{}};
            [Exposed=Window] interface Element {{}};
            {interface_attributes}
            interface HTMLCollection {{
              {constructor}
              {length}
              {item}
              {named_item}
              {extra}
            }};
        """

    def assert_html_collection_rejected(self, expected: str, **changes: str) -> None:
        parser_results = self.parse(
            {"HTMLCollection.webidl": self.html_collection_source(**changes)}
        )
        with self.assertRaisesRegex(
            production_webidl.WebIDLSelectionError,
            re.escape(expected),
        ):
            production_webidl._select_html_collection_interface(parser_results)

    def test_rejects_html_collection_interface_attribute_drift(self) -> None:
        self.assert_html_collection_rejected(
            "`HTMLCollection` must carry exactly ['Exposed', "
            "'LegacyUnenumerableNamedProperties'], got ['Exposed']",
            interface_attributes="[Exposed=Window]",
        )
        self.assert_html_collection_rejected(
            "`HTMLCollection` must carry exactly `[Exposed=Window]`",
            interface_attributes=(
                "[Exposed=*, LegacyUnenumerableNamedProperties]"
            ),
        )

    def test_rejects_constructible_html_collection(self) -> None:
        self.assert_html_collection_rejected(
            "`HTMLCollection` must not be constructible",
            constructor="constructor();",
        )

    def test_rejects_extra_html_collection_member(self) -> None:
        self.assert_html_collection_rejected(
            "`HTMLCollection` must declare exactly ['length', 'item', "
            "'namedItem'], got ['length', 'item', 'namedItem', 'extra']",
            extra="undefined extra();",
        )

    def test_rejects_html_collection_length_drift(self) -> None:
        self.assert_html_collection_rejected(
            "`HTMLCollection.length` must carry exactly ['Pure'], got []",
            length="readonly attribute unsigned long length;",
        )
        self.assert_html_collection_rejected(
            "`HTMLCollection.length` must be readonly",
            length="[Pure] attribute unsigned long length;",
        )
        self.assert_html_collection_rejected(
            "`HTMLCollection.length` must use non-nullable `unsigned long`, "
            "got `unsigned short`",
            length="[Pure] readonly attribute unsigned short length;",
        )

    def test_rejects_html_collection_indexed_getter_drift(self) -> None:
        self.assert_html_collection_rejected(
            "`HTMLCollection.item` must be an indexed instance getter",
            item="[Pure] Element? item(unsigned long index);",
        )
        self.assert_html_collection_rejected(
            "`HTMLCollection.item` must carry exactly ['Pure'], got []",
            item="getter Element? item(unsigned long index);",
        )
        self.assert_html_collection_rejected(
            "`HTMLCollection.item` must return nullable `Element`, got `Element`",
            item="[Pure] getter Element item(unsigned long index);",
        )

    def test_rejects_html_collection_named_getter_drift(self) -> None:
        self.assert_html_collection_rejected(
            "`HTMLCollection.namedItem` must carry exactly ['Pure'], got []",
            named_item="getter Element? namedItem(DOMString name);",
        )
        self.assert_html_collection_rejected(
            "`HTMLCollection.namedItem` must take required non-nullable "
            "`DOMString name`",
            named_item="[Pure] getter Element? namedItem(DOMString key);",
        )
        self.assert_html_collection_rejected(
            "`HTMLCollection.namedItem` must return nullable `Element`, "
            "got `Element`",
            named_item="[Pure] getter Element namedItem(DOMString name);",
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

    def assert_children_rejected(self, declaration: str, expected: str) -> None:
        parser_results = self.parse(
            {
                "Document.webidl": f"""
                    interface Element {{}};
                    interface HTMLCollection {{}};
                    interface Document {{ {declaration} }};
                """,
            }
        )
        with self.assertRaisesRegex(
            production_webidl.WebIDLSelectionError,
            re.escape(expected),
        ):
            production_webidl.select_sameobject_readonly_interface_attribute(
                parser_results,
                production_webidl.DOCUMENT_CHILDREN,
                "HTMLCollection",
            )

    def test_rejects_children_without_sameobject(self) -> None:
        self.assert_children_rejected(
            "readonly attribute HTMLCollection children;",
            "`Document.children` must carry exactly ['SameObject'], got []",
        )

    def test_rejects_nullable_children(self) -> None:
        self.assert_children_rejected(
            "[SameObject] readonly attribute HTMLCollection? children;",
            "`Document.children` must be non-nullable",
        )

    def test_rejects_the_wrong_children_interface(self) -> None:
        self.assert_children_rejected(
            "[SameObject] readonly attribute Element children;",
            "`Document.children` must return `HTMLCollection`, got `Element`",
        )

    def test_rejects_writable_children(self) -> None:
        parser_results = self.parse(
            {
                "Document.webidl": """
                    interface HTMLCollection {};
                    interface Document {
                      [SameObject] readonly attribute HTMLCollection children;
                    };
                """,
            }
        )
        document = next(
            result
            for result in parser_results
            if result.isInterface() and result.identifier.name == "Document"
        )
        document.members[0].readonly = False
        with self.assertRaisesRegex(
            production_webidl.WebIDLSelectionError,
            re.escape("`Document.children` must be readonly"),
        ):
            production_webidl.select_sameobject_readonly_interface_attribute(
                parser_results,
                production_webidl.DOCUMENT_CHILDREN,
                "HTMLCollection",
            )

    def test_rejects_parent_node_interface_getter_without_pure(self) -> None:
        parser_results = self.parse(
            {
                "Document.webidl": """
                    interface Element {};
                    interface Document {
                      readonly attribute Element? firstElementChild;
                    };
                """,
            }
        )

        with self.assertRaisesRegex(
            production_webidl.WebIDLSelectionError,
            re.escape(
                "`Document.firstElementChild` must carry exactly ['Pure'], got []"
            ),
        ):
            production_webidl.select_pure_readonly_nullable_interface_attribute(
                parser_results,
                production_webidl.DOCUMENT_FIRST_ELEMENT_CHILD,
                "Element",
            )

    def test_rejects_parent_node_count_without_pure(self) -> None:
        parser_results = self.parse(
            {
                "Document.webidl": """
                    interface Document {
                      readonly attribute unsigned long childElementCount;
                    };
                """,
            }
        )

        with self.assertRaisesRegex(
            production_webidl.WebIDLSelectionError,
            re.escape(
                "`Document.childElementCount` must carry exactly ['Pure'], got []"
            ),
        ):
            production_webidl.select_readonly_unsigned_long_attribute(
                parser_results,
                production_webidl.DOCUMENT_CHILD_ELEMENT_COUNT,
            )

    def assert_get_elements_by_class_name_rejected(
        self,
        declaration: str,
        expected: str,
    ) -> None:
        parser_results = self.parse(
            {
                "Document.webidl": f"""
                    interface HTMLCollection {{}};
                    interface Document {{ {declaration} }};
                """,
            }
        )
        with self.assertRaisesRegex(
            production_webidl.WebIDLSelectionError,
            re.escape(expected),
        ):
            production_webidl.select_domstring_to_nonnullable_interface_operation(
                parser_results,
                production_webidl.DOCUMENT_GET_ELEMENTS_BY_CLASS_NAME,
                "HTMLCollection",
                "classNames",
            )

    def test_selects_exact_get_elements_by_class_name(self) -> None:
        parser_results = self.parse(
            {
                "Document.webidl": """
                    interface HTMLCollection {};
                    interface Document {
                      HTMLCollection getElementsByClassName(DOMString classNames);
                    };
                """,
            }
        )
        method = production_webidl.select_domstring_to_nonnullable_interface_operation(
            parser_results,
            production_webidl.DOCUMENT_GET_ELEMENTS_BY_CLASS_NAME,
            "HTMLCollection",
            "classNames",
        )
        self.assertEqual(set(method._extendedAttrDict), set())

    def test_rejects_get_elements_by_class_name_extended_attributes(self) -> None:
        self.assert_get_elements_by_class_name_rejected(
            "[Pure] HTMLCollection getElementsByClassName(DOMString classNames);",
            "`Document.getElementsByClassName` must carry no extended attributes, "
            "got ['Pure']",
        )

    def test_rejects_get_elements_by_class_name_return_drift(self) -> None:
        self.assert_get_elements_by_class_name_rejected(
            "HTMLCollection? getElementsByClassName(DOMString classNames);",
            "`Document.getElementsByClassName` must return a non-nullable interface, "
            "got `HTMLCollection?`",
        )
        self.assert_get_elements_by_class_name_rejected(
            "Document getElementsByClassName(DOMString classNames);",
            "`Document.getElementsByClassName` must return `HTMLCollection`, "
            "got `Document`",
        )

    def test_rejects_get_elements_by_class_name_argument_drift(self) -> None:
        expected = (
            "`Document.getElementsByClassName` must take required non-nullable "
            "`DOMString classNames`"
        )
        self.assert_get_elements_by_class_name_rejected(
            "HTMLCollection getElementsByClassName(optional DOMString classNames);",
            expected,
        )
        self.assert_get_elements_by_class_name_rejected(
            "HTMLCollection getElementsByClassName(DOMString names);",
            expected,
        )
        self.assert_get_elements_by_class_name_rejected(
            "HTMLCollection getElementsByClassName(USVString classNames);",
            expected,
        )

    def test_rejects_overloaded_get_elements_by_class_name(self) -> None:
        self.assert_get_elements_by_class_name_rejected(
            """
              HTMLCollection getElementsByClassName(DOMString classNames);
              HTMLCollection getElementsByClassName(
                  DOMString classNames, DOMString extra);
            """,
            "`Document.getElementsByClassName` must have exactly one signature, "
            "found 2",
        )

    def assert_get_elements_by_tag_name_rejected(
        self,
        declaration: str,
        expected: str,
    ) -> None:
        parser_results = self.parse(
            {
                "Document.webidl": f"""
                    interface HTMLCollection {{}};
                    interface Document {{ {declaration} }};
                """,
            }
        )
        with self.assertRaisesRegex(
            production_webidl.WebIDLSelectionError,
            re.escape(expected),
        ):
            production_webidl.select_domstring_to_nonnullable_interface_operation(
                parser_results,
                production_webidl.DOCUMENT_GET_ELEMENTS_BY_TAG_NAME,
                "HTMLCollection",
                "qualifiedName",
            )

    def test_selects_exact_get_elements_by_tag_name(self) -> None:
        parser_results = self.parse(
            {
                "Document.webidl": """
                    interface HTMLCollection {};
                    interface Document {
                      HTMLCollection getElementsByTagName(DOMString qualifiedName);
                    };
                """,
            }
        )
        method = production_webidl.select_domstring_to_nonnullable_interface_operation(
            parser_results,
            production_webidl.DOCUMENT_GET_ELEMENTS_BY_TAG_NAME,
            "HTMLCollection",
            "qualifiedName",
        )
        self.assertEqual(set(method._extendedAttrDict), set())

    def test_rejects_get_elements_by_tag_name_shape_drift(self) -> None:
        self.assert_get_elements_by_tag_name_rejected(
            "[Pure] HTMLCollection getElementsByTagName(DOMString qualifiedName);",
            "`Document.getElementsByTagName` must carry no extended attributes, "
            "got ['Pure']",
        )
        expected = (
            "`Document.getElementsByTagName` must take required non-nullable "
            "`DOMString qualifiedName`"
        )
        self.assert_get_elements_by_tag_name_rejected(
            "HTMLCollection getElementsByTagName(optional DOMString qualifiedName);",
            expected,
        )
        self.assert_get_elements_by_tag_name_rejected(
            "HTMLCollection getElementsByTagName(DOMString localName);",
            expected,
        )

    def test_element_get_elements_by_tag_name_pins_local_name_argument(self) -> None:
        parser_results = self.parse(
            {
                "Element.webidl": """
                    interface HTMLCollection {};
                    interface Element {
                      HTMLCollection getElementsByTagName(DOMString localName);
                    };
                """,
            }
        )
        method = production_webidl.select_domstring_to_nonnullable_interface_operation(
            parser_results,
            production_webidl.ELEMENT_GET_ELEMENTS_BY_TAG_NAME,
            "HTMLCollection",
            "localName",
        )
        self.assertEqual(set(method._extendedAttrDict), set())

        parser_results = self.parse(
            {
                "Element.webidl": """
                    interface HTMLCollection {};
                    interface Element {
                      HTMLCollection getElementsByTagName(DOMString qualifiedName);
                    };
                """,
            }
        )
        with self.assertRaisesRegex(
            production_webidl.WebIDLSelectionError,
            re.escape(
                "`Element.getElementsByTagName` must take required non-nullable "
                "`DOMString localName`"
            ),
        ):
            production_webidl.select_domstring_to_nonnullable_interface_operation(
                parser_results,
                production_webidl.ELEMENT_GET_ELEMENTS_BY_TAG_NAME,
                "HTMLCollection",
                "localName",
            )

    def assert_get_elements_by_tag_name_ns_rejected(
        self,
        interface_name: str,
        second_argument_name: str,
        declaration: str,
        expected: str,
    ) -> None:
        qualified_name = f"{interface_name}.getElementsByTagNameNS"
        parser_results = self.parse(
            {
                f"{interface_name}.webidl": f"""
                    interface HTMLCollection {{}};
                    interface {interface_name} {{ {declaration} }};
                """,
            }
        )
        with self.assertRaisesRegex(
            production_webidl.WebIDLSelectionError,
            re.escape(expected),
        ):
            production_webidl.select_nullable_domstring_domstring_to_nonnullable_interface_operation(
                parser_results,
                qualified_name,
                "HTMLCollection",
                second_argument_name,
            )

    def test_selects_exact_document_and_element_get_elements_by_tag_name_ns(self) -> None:
        for interface_name, second_argument_name in (
            ("Document", "qualifiedName"),
            ("Element", "localName"),
        ):
            with self.subTest(interface=interface_name):
                parser_results = self.parse(
                    {
                        f"{interface_name}.webidl": f"""
                            interface HTMLCollection {{}};
                            interface {interface_name} {{
                              HTMLCollection getElementsByTagNameNS(
                                  DOMString? namespace,
                                  DOMString {second_argument_name});
                            }};
                        """,
                    }
                )
                method = production_webidl.select_nullable_domstring_domstring_to_nonnullable_interface_operation(
                    parser_results,
                    f"{interface_name}.getElementsByTagNameNS",
                    "HTMLCollection",
                    second_argument_name,
                )
                return_type, arguments = method.signatures()[0]
                self.assertEqual(return_type.name, "HTMLCollection")
                self.assertEqual(set(method._extendedAttrDict), set())
                self.assertEqual(
                    [argument.identifier.name for argument in arguments],
                    ["namespace", second_argument_name],
                )
                self.assertTrue(arguments[0].type.nullable())
                self.assertFalse(arguments[1].type.nullable())

    def test_rejects_get_elements_by_tag_name_ns_shape_drift(self) -> None:
        qualified_name = production_webidl.DOCUMENT_GET_ELEMENTS_BY_TAG_NAME_NS
        cases = (
            (
                "[Pure] HTMLCollection getElementsByTagNameNS("
                "DOMString? namespace, DOMString qualifiedName);",
                f"`{qualified_name}` must carry no extended attributes, got ['Pure']",
            ),
            (
                "HTMLCollection? getElementsByTagNameNS("
                "DOMString? namespace, DOMString qualifiedName);",
                f"`{qualified_name}` must return non-nullable `HTMLCollection`, "
                "got `HTMLCollection?`",
            ),
            (
                "HTMLCollection getElementsByTagNameNS("
                "DOMString namespace, DOMString qualifiedName);",
                f"`{qualified_name}` first argument must be required nullable "
                "`DOMString? namespace`",
            ),
            (
                "HTMLCollection getElementsByTagNameNS("
                "DOMString? namespace, DOMString localName);",
                f"`{qualified_name}` second argument must be required non-nullable "
                "`DOMString qualifiedName`",
            ),
            (
                "HTMLCollection getElementsByTagNameNS("
                "DOMString? namespace, optional DOMString qualifiedName);",
                f"`{qualified_name}` second argument must be required non-nullable "
                "`DOMString qualifiedName`",
            ),
        )
        for declaration, expected in cases:
            with self.subTest(declaration=declaration):
                self.assert_get_elements_by_tag_name_ns_rejected(
                    "Document", "qualifiedName", declaration, expected
                )

    def test_rejects_element_get_elements_by_tag_name_ns_name_drift(self) -> None:
        qualified_name = production_webidl.ELEMENT_GET_ELEMENTS_BY_TAG_NAME_NS
        self.assert_get_elements_by_tag_name_ns_rejected(
            "Element",
            "localName",
            "HTMLCollection getElementsByTagNameNS("
            "DOMString? namespace, DOMString qualifiedName);",
            f"`{qualified_name}` second argument must be required non-nullable "
            "`DOMString localName`",
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

    def assert_query_selector_rejected(
        self, declaration: str, expected: str
    ) -> None:
        parser_results = self.parse(
            {
                "ParentNode.webidl": f"""
                    interface Element {{}};
                    interface Document {{ {declaration} }};
                """,
            }
        )
        with self.assertRaisesRegex(
            production_webidl.WebIDLSelectionError,
            re.escape(expected),
        ):
            production_webidl.select_pure_throws_domstring_to_nullable_interface_operation(
                parser_results,
                production_webidl.DOCUMENT_QUERY_SELECTOR,
                "Element",
            )

    def test_selects_exact_throwing_query_selector(self) -> None:
        parser_results = self.parse(
            {
                "ParentNode.webidl": """
                    interface Element {};
                    interface Document {
                      [Pure, Throws] Element? querySelector(DOMString selectors);
                    };
                """,
            }
        )
        method = production_webidl.select_pure_throws_domstring_to_nullable_interface_operation(
            parser_results,
            production_webidl.DOCUMENT_QUERY_SELECTOR,
            "Element",
        )
        self.assertTrue(method.getExtendedAttribute("Pure"))
        self.assertTrue(method.getExtendedAttribute("Throws"))

    def test_rejects_query_selector_without_throws(self) -> None:
        self.assert_query_selector_rejected(
            "[Pure] Element? querySelector(DOMString selectors);",
            "`Document.querySelector` must carry exactly ['Pure', 'Throws'], got ['Pure']",
        )

    def test_rejects_query_selector_argument_drift(self) -> None:
        self.assert_query_selector_rejected(
            "[Pure, Throws] Element? querySelector(optional DOMString selectors);",
            "`Document.querySelector` argument `selectors` must be required and non-variadic",
        )

    def assert_boolean_selector_rejected(
        self, declaration: str, member_name: str, expected: str
    ) -> None:
        qualified_name = f"Element.{member_name}"
        parser_results = self.parse(
            {
                "Element.webidl": f"interface Element {{ {declaration} }};",
            }
        )
        with self.assertRaisesRegex(
            production_webidl.WebIDLSelectionError,
            re.escape(expected),
        ):
            production_webidl.select_pure_throws_domstring_to_boolean_operation(
                parser_results,
                qualified_name,
            )

    def test_selects_exact_boolean_selector_operations(self) -> None:
        parser_results = self.parse(
            {
                "Element.webidl": """
                    interface Element {
                      [Pure, Throws] boolean matches(DOMString selectors);
                      [Pure, Throws] boolean webkitMatchesSelector(DOMString selectors);
                    };
                """,
            }
        )
        for qualified_name in (
            production_webidl.ELEMENT_MATCHES,
            production_webidl.ELEMENT_WEBKIT_MATCHES_SELECTOR,
        ):
            with self.subTest(member=qualified_name):
                method = production_webidl.select_pure_throws_domstring_to_boolean_operation(
                    parser_results,
                    qualified_name,
                )
                self.assertEqual(set(method._extendedAttrDict), {"Pure", "Throws"})

    def test_rejects_boolean_selector_without_throws(self) -> None:
        self.assert_boolean_selector_rejected(
            "[Pure] boolean matches(DOMString selectors);",
            "matches",
            "`Element.matches` must carry exactly ['Pure', 'Throws'], got ['Pure']",
        )

    def test_rejects_boolean_selector_return_drift(self) -> None:
        self.assert_boolean_selector_rejected(
            "[Pure, Throws] Element? matches(DOMString selectors);",
            "matches",
            "`Element.matches` must return non-nullable `boolean`, got `Element?`",
        )

    def test_rejects_boolean_selector_argument_drift(self) -> None:
        self.assert_boolean_selector_rejected(
            "[Pure, Throws] boolean matches(optional DOMString selectors);",
            "matches",
            "`Element.matches` argument `selectors` must be required and non-variadic",
        )

    def assert_query_selector_all_rejected(
        self, declaration: str, expected: str
    ) -> None:
        parser_results = self.parse(
            {
                "ParentNode.webidl": f"""
                    interface NodeList {{}};
                    interface Document {{ {declaration} }};
                """,
            }
        )
        with self.assertRaisesRegex(
            production_webidl.WebIDLSelectionError,
            re.escape(expected),
        ):
            production_webidl.select_newobject_throws_domstring_to_interface_operation(
                parser_results,
                production_webidl.DOCUMENT_QUERY_SELECTOR_ALL,
                "NodeList",
            )

    def test_selects_exact_query_selector_all(self) -> None:
        parser_results = self.parse(
            {
                "ParentNode.webidl": """
                    interface NodeList {};
                    interface Document {
                      [NewObject, Throws] NodeList querySelectorAll(DOMString selectors);
                    };
                """,
            }
        )
        method = production_webidl.select_newobject_throws_domstring_to_interface_operation(
            parser_results,
            production_webidl.DOCUMENT_QUERY_SELECTOR_ALL,
            "NodeList",
        )
        self.assertEqual(set(method._extendedAttrDict), {"NewObject", "Throws"})

    def test_rejects_query_selector_all_without_newobject(self) -> None:
        self.assert_query_selector_all_rejected(
            "[Throws] NodeList querySelectorAll(DOMString selectors);",
            "`Document.querySelectorAll` must carry exactly ['NewObject', 'Throws'], got ['Throws']",
        )

    def test_rejects_query_selector_all_without_throws(self) -> None:
        self.assert_query_selector_all_rejected(
            "[NewObject] NodeList querySelectorAll(DOMString selectors);",
            "`Document.querySelectorAll` must carry exactly ['NewObject', 'Throws'], got ['NewObject']",
        )

    def test_rejects_query_selector_all_extra_attributes(self) -> None:
        self.assert_query_selector_all_rejected(
            "[NewObject, SecureContext, Throws] NodeList querySelectorAll(DOMString selectors);",
            "`Document.querySelectorAll` must carry exactly ['NewObject', 'Throws'], "
            "got ['NewObject', 'SecureContext', 'Throws']",
        )

    def test_rejects_query_selector_all_nullable_return(self) -> None:
        self.assert_query_selector_all_rejected(
            "[NewObject, Throws] NodeList? querySelectorAll(DOMString selectors);",
            "`Document.querySelectorAll` must return a non-nullable interface, got `NodeList?`",
        )

    def test_rejects_query_selector_all_argument_drift(self) -> None:
        self.assert_query_selector_all_rejected(
            "[NewObject, Throws] NodeList querySelectorAll(optional DOMString selectors);",
            "`Document.querySelectorAll` argument `selectors` must be required and non-variadic",
        )

    def test_rejects_query_selector_all_wrong_interface(self) -> None:
        self.assert_query_selector_all_rejected(
            "[NewObject, Throws] Document querySelectorAll(DOMString selectors);",
            "`Document.querySelectorAll` must return `NodeList`, got `Document`",
        )

    def test_rejects_query_selector_all_wrong_argument_type(self) -> None:
        self.assert_query_selector_all_rejected(
            "[NewObject, Throws] NodeList querySelectorAll(long selectors);",
            "`Document.querySelectorAll` argument `selectors` must use non-nullable "
            "`DOMString`, got `long`",
        )

    def test_rejects_query_selector_all_extra_argument(self) -> None:
        self.assert_query_selector_all_rejected(
            "[NewObject, Throws] NodeList querySelectorAll(DOMString selectors, boolean extra);",
            "`Document.querySelectorAll` must take exactly one argument, found 2",
        )

    def test_rejects_static_query_selector_all(self) -> None:
        self.assert_query_selector_all_rejected(
            "[NewObject, Throws] static NodeList querySelectorAll(DOMString selectors);",
            "`Document.querySelectorAll` must be an ordinary instance operation",
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

    def assert_console_rejected(
        self,
        declaration: str,
        member: str,
        expected: str,
    ) -> None:
        parser_results = self.parse(
            {
                "Console.webidl": f'''[ClassString="Console", Exposed=*]
                    namespace console {{ {declaration} }};''',
            }
        )
        with self.assertRaisesRegex(
            production_webidl.WebIDLSelectionError,
            re.escape(expected),
        ):
            production_webidl._select_console_log_operation(
                parser_results,
                f"console.{member}",
            )

    def test_selects_console_log_from_namespace(self) -> None:
        parser_results = self.parse(
            {
                "Console.webidl": '''[ClassString="Console", Exposed=*]
                    namespace console { undefined log(any... messages); };''',
            }
        )
        method = production_webidl._select_console_log_operation(
            parser_results,
            production_webidl.CONSOLE_LOG,
        )
        self.assertEqual(method.identifier.name, "log")

    def test_rejects_console_logging_return_type_drift(self) -> None:
        self.assert_console_rejected(
            "boolean log(any... messages);",
            "log",
            "`console.log` must return non-nullable `undefined`, got `boolean`",
        )

    def test_rejects_nonvariadic_console_logging_arguments(self) -> None:
        self.assert_console_rejected(
            "undefined warn(any messages);",
            "warn",
            "`console.warn` must take variadic `any... messages`",
        )

    def test_rejects_console_trace_argument_name_drift(self) -> None:
        self.assert_console_rejected(
            "undefined trace(any... messages);",
            "trace",
            "`console.trace` must take variadic `any... data`",
        )

    def test_rejects_console_logging_extended_attributes(self) -> None:
        self.assert_console_rejected(
            "[Throws] undefined error(any... messages);",
            "error",
            "`console.error` carries extended attributes that are not implemented: Throws",
        )

    def assert_element_rejected(
        self,
        declaration: str,
        member: str,
        expected: str,
    ) -> None:
        parser_results = self.parse(
            {"Element.webidl": f"interface Element {{ {declaration} }};"}
        )
        with self.assertRaisesRegex(
            production_webidl.WebIDLSelectionError,
            re.escape(expected),
        ):
            production_webidl._select_element_host_member(
                parser_results,
                f"Element.{member}",
            )

    def parse_element_attribute_mutation_operation(self, declaration: str):
        return self.parse(
            {
                "Element.webidl": f"""
                    interface TrustedHTML {{}};
                    interface TrustedScript {{}};
                    interface TrustedScriptURL {{}};
                    typedef (TrustedHTML or TrustedScript or TrustedScriptURL) TrustedType;
                    interface Element {{ {declaration} }};
                """,
            }
        )

    def assert_element_attribute_mutation_rejected(
        self,
        declaration: str,
        member: str,
        expected: str,
    ) -> None:
        parser_results = self.parse_element_attribute_mutation_operation(declaration)
        with self.assertRaisesRegex(
            production_webidl.WebIDLSelectionError,
            re.escape(expected),
        ):
            production_webidl._select_element_host_member(
                parser_results,
                f"Element.{member}",
            )

    def test_selects_exact_element_attribute_mutation_operations(self) -> None:
        parser_results = self.parse_element_attribute_mutation_operation(
            """
                [CEReactions, Throws]
                boolean toggleAttribute(DOMString name, optional boolean force);
                [CEReactions, Throws]
                undefined setAttribute(
                    DOMString name, (TrustedType or DOMString) value
                );
                [CEReactions]
                undefined removeAttribute(DOMString name);
            """
        )
        for qualified_name in (
            production_webidl.ELEMENT_TOGGLE_ATTRIBUTE,
            production_webidl.ELEMENT_SET_ATTRIBUTE,
            production_webidl.ELEMENT_REMOVE_ATTRIBUTE,
        ):
            with self.subTest(member=qualified_name):
                member = production_webidl._select_element_host_member(
                    parser_results, qualified_name
                )
                self.assertTrue(member.isMethod())

    def test_rejects_element_attribute_mutation_operation_drift(self) -> None:
        cases = (
            (
                "[CEReactions] boolean toggleAttribute("
                "DOMString name, optional boolean force);",
                "toggleAttribute",
                "`Element.toggleAttribute` must carry exactly ['CEReactions', "
                "'Throws'], got ['CEReactions']",
            ),
            (
                "[CEReactions, Throws] boolean toggleAttribute("
                "DOMString name, optional boolean force = false);",
                "toggleAttribute",
                "`Element.toggleAttribute` second argument must be optional "
                "non-nullable `boolean force`",
            ),
            (
                "[CEReactions, Throws] undefined setAttribute("
                "DOMString name, ((TrustedHTML or TrustedScript) or DOMString) value);",
                "setAttribute",
                "`Element.setAttribute` second argument must be required "
                "non-nullable `(TrustedType or DOMString) value`",
            ),
            (
                "[CEReactions, Throws] undefined setAttribute("
                "DOMString name, (DOMString or TrustedType) value);",
                "setAttribute",
                "`Element.setAttribute` second argument must be required "
                "non-nullable `(TrustedType or DOMString) value`",
            ),
            (
                "[CEReactions, Throws] boolean setAttribute("
                "DOMString name, (TrustedType or DOMString) value);",
                "setAttribute",
                "`Element.setAttribute` must return non-nullable `undefined`, "
                "got `boolean`",
            ),
            (
                "[CEReactions, Throws] undefined removeAttribute(DOMString name);",
                "removeAttribute",
                "`Element.removeAttribute` must carry exactly ['CEReactions'], "
                "got ['CEReactions', 'Throws']",
            ),
            (
                "[CEReactions] undefined removeAttribute(optional DOMString name);",
                "removeAttribute",
                "`Element.removeAttribute` must take required non-nullable "
                "`DOMString name`",
            ),
        )
        for declaration, member, expected in cases:
            with self.subTest(member=member, declaration=declaration):
                self.assert_element_attribute_mutation_rejected(
                    declaration, member, expected
                )

    def test_selects_exact_writable_element_id(self) -> None:
        parser_results = self.parse(
            {
                "Element.webidl": """interface Element {
                    [CEReactions, Pure] attribute DOMString id;
                };""",
            }
        )
        member = production_webidl._select_element_host_member(
            parser_results,
            production_webidl.ELEMENT_ID,
        )
        self.assertFalse(member.readonly)

    def test_rejects_element_id_without_ce_reactions(self) -> None:
        self.assert_element_rejected(
            "[Pure] attribute DOMString id;",
            "id",
            "`Element.id` must carry exactly ['CEReactions', 'Pure'], got ['Pure']",
        )

    def test_rejects_element_children_without_sameobject(self) -> None:
        parser_results = self.parse(
            {
                "Element.webidl": """
                    interface HTMLCollection {};
                    interface Element {
                      readonly attribute HTMLCollection children;
                    };
                """,
            }
        )
        with self.assertRaisesRegex(
            production_webidl.WebIDLSelectionError,
            re.escape(
                "`Element.children` must carry exactly ['SameObject'], got []"
            ),
        ):
            production_webidl._select_element_host_member(
                parser_results,
                production_webidl.ELEMENT_CHILDREN,
            )

    def test_selects_exact_element_namespace_string_attributes(self) -> None:
        parser_results = self.parse(
            {
                "Element.webidl": """
                    interface Element {
                      [Constant] readonly attribute DOMString? namespaceURI;
                      [Constant] readonly attribute DOMString? prefix;
                    };
                """,
            }
        )
        for qualified_name in (
            production_webidl.ELEMENT_NAMESPACE_URI,
            production_webidl.ELEMENT_PREFIX,
        ):
            with self.subTest(member=qualified_name):
                member = production_webidl._select_element_host_member(
                    parser_results, qualified_name
                )
                self.assertTrue(member.readonly)
                self.assertEqual(set(member._extendedAttrDict), {"Constant"})
                self.assertTrue(member.type.nullable())
                self.assertTrue(member.type.inner.isDOMString())

    def test_rejects_element_namespace_string_attribute_drift(self) -> None:
        for member in ("namespaceURI", "prefix"):
            with self.subTest(member=member):
                self.assert_element_rejected(
                    f"readonly attribute DOMString? {member};",
                    member,
                    f"`Element.{member}` must carry exactly ['Constant'], got []",
                )
                self.assert_element_rejected(
                    f"[Constant, Throws] readonly attribute DOMString? {member};",
                    member,
                    f"`Element.{member}` must carry exactly ['Constant'], "
                    "got ['Constant', 'Throws']",
                )
                self.assert_element_rejected(
                    f"[Constant] readonly attribute DOMString {member};",
                    member,
                    f"`Element.{member}` must use nullable `DOMString`, got `DOMString`",
                )
                self.assert_element_rejected(
                    f"[Constant] readonly attribute USVString? {member};",
                    member,
                    f"`Element.{member}` must use nullable `DOMString`, got `USVString?`",
                )
                self.assert_element_rejected(
                    f"[Constant] static readonly attribute DOMString? {member};",
                    member,
                    f"`Element.{member}` must be an instance attribute",
                )

    def test_rejects_element_namespace_string_attribute_writable_drift(self) -> None:
        for member_name in ("namespaceURI", "prefix"):
            with self.subTest(member=member_name):
                parser_results = self.parse(
                    {
                        "Element.webidl": (
                            "interface Element { [Constant] readonly attribute "
                            f"DOMString? {member_name}; }};"
                        )
                    }
                )
                element = next(
                    result
                    for result in parser_results
                    if result.isInterface() and result.identifier.name == "Element"
                )
                element.members[0].readonly = False
                with self.assertRaisesRegex(
                    production_webidl.WebIDLSelectionError,
                    re.escape(f"`Element.{member_name}` must be readonly"),
                ):
                    production_webidl._select_element_host_member(
                        parser_results, f"Element.{member_name}"
                    )

    def test_rejects_nonnullable_get_attribute_return(self) -> None:
        self.assert_element_rejected(
            "[Pure] DOMString getAttribute(DOMString name);",
            "getAttribute",
            "`Element.getAttribute` must return nullable `DOMString`, got `DOMString`",
        )

    def test_selects_exact_namespace_attribute_operations(self) -> None:
        parser_results = self.parse(
            {
                "Element.webidl": """
                    interface Element {
                      [Pure] DOMString? getAttributeNS(
                        DOMString? namespace, DOMString localName
                      );
                      boolean hasAttributeNS(
                        DOMString? namespace, DOMString localName
                      );
                      [CEReactions] undefined removeAttributeNS(
                        DOMString? namespace, DOMString localName
                      );
                    };
                """,
            }
        )
        for qualified_name in (
            production_webidl.ELEMENT_GET_ATTRIBUTE_NS,
            production_webidl.ELEMENT_HAS_ATTRIBUTE_NS,
            production_webidl.ELEMENT_REMOVE_ATTRIBUTE_NS,
        ):
            with self.subTest(member=qualified_name):
                member = production_webidl._select_element_host_member(
                    parser_results, qualified_name
                )
                return_type, arguments = member.signatures()[0]
                self.assertEqual(
                    [argument.identifier.name for argument in arguments],
                    ["namespace", "localName"],
                )
                self.assertTrue(arguments[0].type.nullable())
                self.assertTrue(arguments[0].type.inner.isDOMString())
                self.assertFalse(arguments[1].type.nullable())
                self.assertTrue(arguments[1].type.isDOMString())
                if qualified_name == production_webidl.ELEMENT_GET_ATTRIBUTE_NS:
                    self.assertTrue(return_type.nullable())
                    self.assertTrue(return_type.inner.isDOMString())
                    self.assertEqual(set(member._extendedAttrDict), {"Pure"})
                elif qualified_name == production_webidl.ELEMENT_REMOVE_ATTRIBUTE_NS:
                    self.assertFalse(return_type.nullable())
                    self.assertEqual(return_type.prettyName(), "undefined")
                    self.assertEqual(set(member._extendedAttrDict), {"CEReactions"})
                else:
                    self.assertTrue(return_type.isBoolean())
                    self.assertFalse(return_type.nullable())
                    self.assertEqual(set(member._extendedAttrDict), set())

    def test_selects_exact_element_attribute_names_operation(self) -> None:
        parser_results = self.parse(
            {
                "Element.webidl": """
                    interface Element {
                      [Pure] sequence<DOMString> getAttributeNames();
                    };
                """
            }
        )
        member = production_webidl._select_element_host_member(
            parser_results,
            production_webidl.ELEMENT_GET_ATTRIBUTE_NAMES,
        )
        return_type, arguments = member.signatures()[0]
        self.assertTrue(return_type.isSequence())
        self.assertTrue(return_type.inner.isDOMString())
        self.assertFalse(return_type.nullable())
        self.assertEqual(arguments, [])
        self.assertEqual(set(member._extendedAttrDict), {"Pure"})

    def test_rejects_element_attribute_names_shape_drift(self) -> None:
        cases = (
            (
                "sequence<DOMString> getAttributeNames();",
                "`Element.getAttributeNames` must carry exactly ['Pure'], got []",
            ),
            (
                "[Pure, Throws] sequence<DOMString> getAttributeNames();",
                "`Element.getAttributeNames` must carry exactly ['Pure'], "
                "got ['Pure', 'Throws']",
            ),
            (
                "[Pure] DOMString getAttributeNames();",
                "`Element.getAttributeNames` must return non-nullable "
                "`sequence<DOMString>`, got `DOMString`",
            ),
            (
                "[Pure] sequence<USVString> getAttributeNames();",
                "`Element.getAttributeNames` must return non-nullable "
                "`sequence<DOMString>`, got `sequence<USVString>`",
            ),
            (
                "[Pure] sequence<Element> getAttributeNames();",
                "`Element.getAttributeNames` must return non-nullable "
                "`sequence<DOMString>`, got `sequence<Element>`",
            ),
            (
                "[Pure] sequence<DOMString> getAttributeNames(DOMString name);",
                "`Element.getAttributeNames` must take no arguments, found 1",
            ),
            (
                "[Pure] static sequence<DOMString> getAttributeNames();",
                "`Element.getAttributeNames` must be an ordinary instance operation",
            ),
            (
                """
                  [Pure] sequence<DOMString> getAttributeNames();
                  [Pure] sequence<DOMString> getAttributeNames(boolean extra);
                """,
                "`Element.getAttributeNames` must have exactly one signature, "
                "found 2",
            ),
        )
        for declaration, expected in cases:
            with self.subTest(declaration=declaration):
                self.assert_element_rejected(
                    declaration,
                    "getAttributeNames",
                    expected,
                )

    def test_rejects_namespace_attribute_operation_drift(self) -> None:
        cases = (
            (
                "DOMString? getAttributeNS(DOMString? namespace, DOMString localName);",
                "getAttributeNS",
                "`Element.getAttributeNS` must carry exactly ['Pure'], got []",
            ),
            (
                "[Pure, Throws] DOMString? getAttributeNS(DOMString? namespace, DOMString localName);",
                "getAttributeNS",
                "`Element.getAttributeNS` must carry exactly ['Pure'], "
                "got ['Pure', 'Throws']",
            ),
            (
                "[Pure] DOMString getAttributeNS(DOMString? namespace, DOMString localName);",
                "getAttributeNS",
                "`Element.getAttributeNS` must return nullable `DOMString`, got `DOMString`",
            ),
            (
                "[Pure] DOMString? getAttributeNS(DOMString namespace, DOMString localName);",
                "getAttributeNS",
                "`Element.getAttributeNS` first argument `namespace` must be "
                "required, non-variadic nullable `DOMString`",
            ),
            (
                "[Pure] DOMString? getAttributeNS(DOMString? namespace, optional DOMString localName);",
                "getAttributeNS",
                "`Element.getAttributeNS` second argument `localName` must be "
                "required, non-variadic non-nullable `DOMString`",
            ),
            (
                "[Pure] DOMString? getAttributeNS(DOMString? namespace);",
                "getAttributeNS",
                "`Element.getAttributeNS` must take exactly two arguments, found 1",
            ),
            (
                "[Pure] DOMString? getAttributeNS(DOMString? namespace, DOMString localName, DOMString extra);",
                "getAttributeNS",
                "`Element.getAttributeNS` must take exactly two arguments, found 3",
            ),
            (
                "[Pure] DOMString? getAttributeNS(DOMString? namespace, USVString localName);",
                "getAttributeNS",
                "`Element.getAttributeNS` second argument `localName` must be "
                "required, non-variadic non-nullable `DOMString`",
            ),
            (
                "[Pure] boolean hasAttributeNS(DOMString? namespace, DOMString localName);",
                "hasAttributeNS",
                "`Element.hasAttributeNS` must carry exactly [], got ['Pure']",
            ),
            (
                "DOMString hasAttributeNS(DOMString? namespace, DOMString localName);",
                "hasAttributeNS",
                "`Element.hasAttributeNS` must return non-nullable `boolean`, got `DOMString`",
            ),
            (
                "boolean hasAttributeNS(DOMString namespace, DOMString localName);",
                "hasAttributeNS",
                "`Element.hasAttributeNS` first argument `namespace` must be "
                "required, non-variadic nullable `DOMString`",
            ),
            (
                "undefined removeAttributeNS(DOMString? namespace, DOMString localName);",
                "removeAttributeNS",
                "`Element.removeAttributeNS` must carry exactly ['CEReactions'], got []",
            ),
            (
                "[CEReactions, Throws] undefined removeAttributeNS(DOMString? namespace, DOMString localName);",
                "removeAttributeNS",
                "`Element.removeAttributeNS` must carry exactly ['CEReactions'], "
                "got ['CEReactions', 'Throws']",
            ),
            (
                "[CEReactions] boolean removeAttributeNS(DOMString? namespace, DOMString localName);",
                "removeAttributeNS",
                "`Element.removeAttributeNS` must return non-nullable `undefined`, "
                "got `boolean`",
            ),
            (
                "[CEReactions] undefined removeAttributeNS(DOMString namespace, DOMString localName);",
                "removeAttributeNS",
                "`Element.removeAttributeNS` first argument `namespace` must be "
                "required, non-variadic nullable `DOMString`",
            ),
            (
                "[CEReactions] undefined removeAttributeNS(DOMString? namespace, optional DOMString localName);",
                "removeAttributeNS",
                "`Element.removeAttributeNS` second argument `localName` must be "
                "required, non-variadic non-nullable `DOMString`",
            ),
            (
                "[CEReactions] undefined removeAttributeNS(DOMString? namespace);",
                "removeAttributeNS",
                "`Element.removeAttributeNS` must take exactly two arguments, found 1",
            ),
            (
                "[CEReactions] undefined removeAttributeNS(DOMString? namespace, DOMString localName, DOMString extra);",
                "removeAttributeNS",
                "`Element.removeAttributeNS` must take exactly two arguments, found 3",
            ),
            (
                "[CEReactions] undefined removeAttributeNS(DOMString? namespace, USVString localName);",
                "removeAttributeNS",
                "`Element.removeAttributeNS` second argument `localName` must be "
                "required, non-variadic non-nullable `DOMString`",
            ),
        )
        for declaration, member, expected in cases:
            with self.subTest(member=member, declaration=declaration):
                self.assert_element_rejected(declaration, member, expected)

    def test_rejects_optional_has_attribute_name(self) -> None:
        self.assert_element_rejected(
            "boolean hasAttribute(optional DOMString name);",
            "hasAttribute",
            "`Element.hasAttribute` must take required non-nullable `DOMString name`",
        )

    def test_rejects_nonnullable_first_element_child(self) -> None:
        self.assert_element_rejected(
            "[Pure] readonly attribute Element firstElementChild;",
            "firstElementChild",
            "`Element.firstElementChild` must use nullable `Element`, got `Element`",
        )

    def test_selects_exact_nondocumenttypechildnode_sibling_attributes(self) -> None:
        parser_results = self.parse(
            {
                "ChildNode.webidl": """
                    interface mixin NonDocumentTypeChildNode {
                      [Pure] readonly attribute Element? previousElementSibling;
                      [Pure] readonly attribute Element? nextElementSibling;
                    };
                    interface Element {};
                    Element includes NonDocumentTypeChildNode;
                """,
            }
        )
        for qualified_name in (
            production_webidl.ELEMENT_PREVIOUS_ELEMENT_SIBLING,
            production_webidl.ELEMENT_NEXT_ELEMENT_SIBLING,
        ):
            with self.subTest(member=qualified_name):
                member = production_webidl._select_element_host_member(
                    parser_results, qualified_name
                )
                self.assertTrue(member.readonly)
                self.assertEqual(set(member._extendedAttrDict), {"Pure"})
                self.assertTrue(member.type.nullable())
                self.assertEqual(member.type.inner.name, "Element")

    def assert_element_sibling_rejected(
        self, declaration: str, member: str, expected: str
    ) -> None:
        sources = {
            "Element.webidl": f"interface Element {{ {declaration} }};"
        }
        if "HTMLDivElement" in declaration:
            sources["HTMLDivElement.webidl"] = "interface HTMLDivElement {};"
        parser_results = self.parse(sources)
        with self.assertRaisesRegex(
            production_webidl.WebIDLSelectionError,
            re.escape(expected),
        ):
            production_webidl._select_element_host_member(
                parser_results, f"Element.{member}"
            )

    def test_rejects_sibling_attribute_extended_attribute_drift(self) -> None:
        for member in ("previousElementSibling", "nextElementSibling"):
            with self.subTest(member=member):
                self.assert_element_sibling_rejected(
                    f"readonly attribute Element? {member};",
                    member,
                    f"`Element.{member}` must carry exactly ['Pure'], got []",
                )
                self.assert_element_sibling_rejected(
                    f"[Pure, Throws] readonly attribute Element? {member};",
                    member,
                    f"`Element.{member}` must carry exactly ['Pure'], "
                    "got ['Pure', 'Throws']",
                )

    def test_rejects_sibling_attribute_shape_drift(self) -> None:
        cases = (
            (
                "[Pure] attribute Element? previousElementSibling;",
                "previousElementSibling",
                "`Element.previousElementSibling` must be readonly",
            ),
            (
                "[Pure] readonly attribute Element nextElementSibling;",
                "nextElementSibling",
                "`Element.nextElementSibling` must use nullable `Element`, got `Element`",
            ),
            (
                "[Pure] readonly attribute HTMLDivElement? previousElementSibling;",
                "previousElementSibling",
                "`Element.previousElementSibling` must use nullable `Element`, "
                "got `HTMLDivElement?`",
            ),
            (
                "[Pure] readonly attribute DOMString nextElementSibling;",
                "nextElementSibling",
                "`Element.nextElementSibling` must use nullable `Element`, "
                "got `DOMString`",
            ),
            (
                "[Pure] static readonly attribute Element? previousElementSibling;",
                "previousElementSibling",
                "`Element.previousElementSibling` must be an instance attribute",
            ),
        )
        for declaration, member, expected in cases:
            with self.subTest(member=member, declaration=declaration):
                self.assert_element_sibling_rejected(declaration, member, expected)

    def test_rejects_wrong_child_element_count_width(self) -> None:
        self.assert_element_rejected(
            "[Pure] readonly attribute unsigned short childElementCount;",
            "childElementCount",
            "`Element.childElementCount` must use non-nullable `unsigned long`, "
            "got `unsigned short`",
        )

    def test_selects_exact_childnode_remove_operation(self) -> None:
        parser_results = self.parse(
            {
                "ChildNode.webidl": """
                    interface mixin ChildNode {
                      [CEReactions, Unscopable] undefined remove();
                    };
                    interface Element {};
                    Element includes ChildNode;
                """,
            }
        )
        member = production_webidl._select_element_host_member(
            parser_results,
            production_webidl.ELEMENT_REMOVE,
        )
        self.assertEqual(member._name.QName(), "::ChildNode::remove")

    def assert_remove_rejected(self, declaration: str, expected: str) -> None:
        self.assert_element_rejected(declaration, "remove", expected)

    def test_rejects_childnode_remove_missing_or_extra_extended_attributes(self) -> None:
        self.assert_remove_rejected(
            "[CEReactions] undefined remove();",
            "`Element.remove` must carry exactly ['CEReactions', 'Unscopable'], "
            "got ['CEReactions']",
        )
        self.assert_remove_rejected(
            "[CEReactions, Throws, Unscopable] undefined remove();",
            "`Element.remove` must carry exactly ['CEReactions', 'Unscopable'], "
            "got ['CEReactions', 'Throws', 'Unscopable']",
        )

    def test_rejects_childnode_remove_arguments_and_wrong_return(self) -> None:
        self.assert_remove_rejected(
            "[CEReactions, Unscopable] undefined remove(long index);",
            "`Element.remove` must take no arguments, found 1",
        )
        self.assert_remove_rejected(
            "[CEReactions, Unscopable] boolean remove();",
            "`Element.remove` must return non-nullable `undefined`, got `boolean`",
        )

    def test_rejects_childnode_remove_overload_static_and_special_shapes(self) -> None:
        self.assert_remove_rejected(
            """
              [CEReactions, Unscopable] undefined remove();
              [CEReactions, Unscopable] undefined remove(long index);
            """,
            "`Element.remove` must have exactly one signature, found 2",
        )
        self.assert_remove_rejected(
            "[CEReactions] static undefined remove();",
            "`Element.remove` must be an ordinary instance operation",
        )
        self.assert_remove_rejected(
            "stringifier DOMString remove();",
            "`Element.remove` must be an ordinary instance operation",
        )

    def assert_node_rejected(
        self,
        declaration: str,
        member: str,
        expected: str,
    ) -> None:
        parser_results = self.parse(
            {
                "Element.webidl": "interface Element {};",
                "Node.webidl": f"interface Node {{ {declaration} }};",
            }
        )
        with self.assertRaisesRegex(
            production_webidl.WebIDLSelectionError,
            re.escape(expected),
        ):
            production_webidl._select_node_host_member(
                parser_results,
                f"Node.{member}",
            )

    def test_selects_exact_nullable_node_text_content(self) -> None:
        parser_results = self.parse(
            {
                "Node.webidl": """interface Node {
                    [CEReactions, Pure, SetterThrows]
                    attribute DOMString? textContent;
                };""",
            }
        )
        member = production_webidl._select_node_host_member(
            parser_results,
            production_webidl.NODE_TEXT_CONTENT,
        )
        self.assertFalse(member.readonly)
        self.assertTrue(member.type.nullable())

    def test_selects_exact_node_parent_element(self) -> None:
        parser_results = self.parse(
            {
                "Node.webidl": """
                    interface Element {};
                    interface Node {
                      [Pure] readonly attribute Element? parentElement;
                    };
                """,
            }
        )
        member = production_webidl._select_node_host_member(
            parser_results,
            production_webidl.NODE_PARENT_ELEMENT,
        )
        self.assertTrue(member.readonly)
        self.assertEqual(set(member._extendedAttrDict), {"Pure"})
        self.assertTrue(member.type.nullable())
        self.assertEqual(member.type.inner.name, "Element")

    def test_rejects_node_parent_element_shape_drift(self) -> None:
        cases = (
            (
                "readonly attribute Element? parentElement;",
                "`Node.parentElement` must carry exactly ['Pure'], got []",
            ),
            (
                "[Pure, Throws] readonly attribute Element? parentElement;",
                "`Node.parentElement` must carry exactly ['Pure'], "
                "got ['Pure', 'Throws']",
            ),
            (
                "[Pure] attribute Element? parentElement;",
                "`Node.parentElement` must be readonly",
            ),
            (
                "[Pure] readonly attribute Element parentElement;",
                "`Node.parentElement` must use nullable `Element`, got `Element`",
            ),
            (
                "[Pure] readonly attribute Node? parentElement;",
                "`Node.parentElement` must use nullable `Element`, got `Node?`",
            ),
            (
                "[Pure] readonly attribute DOMString? parentElement;",
                "`Node.parentElement` must use nullable `Element`, got `DOMString?`",
            ),
            (
                "[Pure] static readonly attribute Element? parentElement;",
                "`Node.parentElement` must be an instance attribute",
            ),
        )
        for declaration, expected in cases:
            with self.subTest(declaration=declaration):
                self.assert_node_rejected(
                    declaration,
                    "parentElement",
                    expected,
                )

    def test_rejects_node_text_content_without_setter_throws(self) -> None:
        self.assert_node_rejected(
            "[CEReactions, Pure] attribute DOMString? textContent;",
            "textContent",
            "`Node.textContent` must carry exactly ['CEReactions', 'Pure', 'SetterThrows'], "
            "got ['CEReactions', 'Pure']",
        )

    def test_rejects_nullable_node_name(self) -> None:
        self.assert_node_rejected(
            "[Pure] readonly attribute DOMString? nodeName;",
            "nodeName",
            "`Node.nodeName` must use non-nullable `DOMString`, got `DOMString?`",
        )

    def test_rejects_has_child_nodes_arguments(self) -> None:
        self.assert_node_rejected(
            "[Pure] boolean hasChildNodes(boolean unexpected);",
            "hasChildNodes",
            "`Node.hasChildNodes` must take no arguments, found 1",
        )

    def test_selects_exact_node_mutation_operations(self) -> None:
        parser_results = self.parse(
            {
                "Node.webidl": """interface Node {
                    [CEReactions, Throws] Node insertBefore(Node node, Node? child);
                    [CEReactions, Throws] Node appendChild(Node node);
                    [CEReactions, Throws] Node replaceChild(Node node, Node child);
                    [CEReactions, Throws] Node removeChild(Node child);
                };""",
            }
        )
        expected = (
            ("insertBefore", (("node", False), ("child", True))),
            ("appendChild", (("node", False),)),
            ("replaceChild", (("node", False), ("child", False))),
            ("removeChild", (("child", False),)),
        )
        for member_name, arguments in expected:
            with self.subTest(member=member_name):
                member = production_webidl._select_node_host_member(
                    parser_results, f"Node.{member_name}"
                )
                self.assertEqual(set(member._extendedAttrDict), {"CEReactions", "Throws"})
                self.assertEqual(member.signatures()[0][0].name, "Node")
                self.assertEqual(
                    tuple(
                        (argument.identifier.name, argument.type.nullable())
                        for argument in member.signatures()[0][1]
                    ),
                    arguments,
                )

    def test_rejects_node_mutation_operation_shape_drift(self) -> None:
        cases = (
            (
                "[CEReactions] Node appendChild(Node node);",
                "appendChild",
                "`Node.appendChild` must carry exactly ['CEReactions', 'Throws'], got ['CEReactions']",
            ),
            (
                "[CEReactions, Throws] Node appendChild(Node? node);",
                "appendChild",
                "`Node.appendChild` must take required non-nullable `Node node`",
            ),
            (
                "[CEReactions, Throws] Node insertBefore(Node node, Node child);",
                "insertBefore",
                "`Node.insertBefore` must take required nullable `Node child`",
            ),
            (
                "[CEReactions, Throws] Node replaceChild(Node node, optional Node? child = null);",
                "replaceChild",
                "`Node.replaceChild` must take required non-nullable `Node child`",
            ),
            (
                "[CEReactions, Throws] Node removeChild(Node child, Node extra);",
                "removeChild",
                "`Node.removeChild` must take exactly 1 argument(s), found 2",
            ),
            (
                "[CEReactions, Throws] Element appendChild(Node node);",
                "appendChild",
                "`Node.appendChild` must return non-nullable `Node`, got `Element`",
            ),
            (
                "[CEReactions, Throws] static Node removeChild(Node child);",
                "removeChild",
                "`Node.removeChild` must be an ordinary instance operation",
            ),
            (
                "[CEReactions, Throws] Node appendChild([Clamp] Node node);",
                "appendChild",
                "`Node.appendChild` argument `node` carries extended attributes that are not implemented: Clamp",
            ),
            (
                "[CEReactions, Throws] Node appendChild(Node node); [CEReactions, Throws] Node appendChild(Node node, Node child);",
                "appendChild",
                "`Node.appendChild` must have exactly one signature, found 2",
            ),
        )
        for declaration, member, expected in cases:
            with self.subTest(declaration=declaration):
                self.assert_node_rejected(declaration, member, expected)


if __name__ == "__main__":
    unittest.main()
