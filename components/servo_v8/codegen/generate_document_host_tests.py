# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True

import generate  # noqa: E402
import generate_document_host  # noqa: E402
import production_webidl  # noqa: E402


class DocumentHostGenerationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary_directory = tempfile.TemporaryDirectory()
        cls.members = production_webidl.select_document_host_members(
            Path(cls.temporary_directory.name) / "cache",
            environment={},
        )
        cls.outputs = generate_document_host.generate_outputs(cls.members)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary_directory.cleanup()

    def test_generates_c_vtable_from_real_document_members(self) -> None:
        output = self.outputs[generate_document_host.HEADER_NAME]
        expected_fragments = (
            "typedef struct ServoV8OwnedUtf8 {",
            "const uint8_t* data;",
            "uint8_t (*get_hidden)(void* native);",
            "uint8_t (*get_bg_color)(void* native, ServoV8OwnedUtf8* output);",
            "uint8_t (*set_bg_color)(void* native, void* host_context,",
            "uint8_t (*get_ready_state)(void* native, ServoV8OwnedUtf8* output);",
            "uint8_t (*get_head)(void* native, ServoV8InterfaceValue* output);",
            "uint8_t (*get_element_by_id)(void* native, void* host_context,",
            "const uint8_t* element_id,",
            "ServoV8DropCallback drop;",
        )
        for fragment in expected_fragments:
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, output)

    def test_generates_rust_owned_string_and_typed_callbacks(self) -> None:
        output = self.outputs[generate_document_host.RUST_NAME]
        expected_fragments = (
            "fn hidden(&self) -> bool;",
            "fn bg_color(&self) -> String;",
            "fn ready_state(&self) -> String;",
            "unsafe fn set_bg_color(",
            "pub struct OwnedUtf8 {",
            "Box::new(native.bg_color().into_bytes())",
            "Box::from_raw(owner.cast::<Vec<u8>>())",
            "std::str::from_utf8(bytes)",
            "fn head(&self) -> Option<InterfaceHandle>;",
            "unsafe fn get_element_by_id(",
            "std::slice::from_raw_parts(element_id, element_id_length)",
            "set_bg_color: Some(document_host_set_bg_color::<T>)",
            "get_element_by_id: Some(document_host_get_element_by_id::<T>)",
        )
        for fragment in expected_fragments:
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, output)

    def test_generates_cpp_webidl_conversion_and_raii(self) -> None:
        output = self.outputs[generate_document_host.CPP_NAME]
        expected_fragments = (
            "class DocumentHostOwnedUtf8Scope {",
            "RustCallbackScope callback_scope(runtime_);",
            "DocumentHostOwnedUtf8Scope value_scope(state->runtime, &value);",
            "DocumentHostGetHidden(",
            "DocumentHostGetBgColor(",
            "DocumentHostSetBgColor(",
            "DocumentHostGetDocumentElement(",
            "DocumentHostGetHead(",
            "DocumentHostCallGetElementById(",
            "auto* state = UnwrapDocumentHostState(info);",
            "if (info[0]->IsNull()) {",
            "info[0]->ToString(context)",
            "v8::String::Utf8Value utf8(isolate, value);",
            "state->vtable.set_bg_color(",
            "state->vtable.get_ready_state(state->native, &value)",
            "value.is_null > 1",
            "Document.getElementById requires one argument",
            "InstallDocumentHostMembers(",
        )
        for fragment in expected_fragments:
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, output)
        self.assertNotIn("CallDocumentHostGet", output)
        self.assertNotIn("CallDocumentHostSet", output)

    def test_rejects_a_selection_that_does_not_match_the_manifest(self) -> None:
        incomplete = {
            production_webidl.DOCUMENT_HIDDEN: self.members[production_webidl.DOCUMENT_HIDDEN],
        }

        with self.assertRaises(production_webidl.WebIDLSelectionError):
            generate_document_host.generate_outputs(incomplete)

    def test_emits_one_slot_per_manifest_member(self) -> None:
        header = self.outputs[generate_document_host.HEADER_NAME]

        for member in production_webidl.DOCUMENT_HOST:
            member_name = member.qualified_name.split(".")[1]
            with self.subTest(member=member.qualified_name):
                slot = generate.snake_case(member_name)
                if member.shape != production_webidl.PURE_DOMSTRING_TO_NULLABLE_INTERFACE:
                    slot = f"get_{slot}"
                self.assertIn(f"(*{slot})", header)

    def test_registers_every_manifest_member_on_the_document_prototype(self) -> None:
        output = self.outputs[generate_document_host.CPP_NAME]

        for member in production_webidl.DOCUMENT_HOST:
            member_name = member.qualified_name.split(".")[1]
            callback = generate.upper_camel_case(member_name)
            local = generate.snake_case(member_name)
            with self.subTest(member=member.qualified_name):
                if member.shape == production_webidl.PURE_DOMSTRING_TO_NULLABLE_INTERFACE:
                    self.assertIn(f"DocumentHostCall{callback}", output)
                    self.assertIn(f"{local}_method, v8::None", output)
                else:
                    self.assertIn(f"DocumentHostGet{callback}", output)
                    self.assertIn(f"v8::PropertyDescriptor {local}_descriptor", output)
                self.assertIn(f'V8String(isolate, "{member_name}")', output)

    def test_cli_writes_exactly_the_three_document_host_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            out_dir = Path(temporary_directory) / "out"
            with mock.patch.dict("os.environ", {}, clear=True):
                generate_document_host.main(
                    [str(production_webidl.PRODUCTION_WEBIDLS_DIR), str(out_dir)]
                )
            written = {
                path.name: path.read_text(encoding="utf-8")
                for path in out_dir.iterdir()
            }

        self.assertEqual(written, self.outputs)


if __name__ == "__main__":
    unittest.main()
