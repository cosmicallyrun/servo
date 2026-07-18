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

import generate_document_host  # noqa: E402
import production_webidl  # noqa: E402


class DocumentHostGenerationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary_directory = tempfile.TemporaryDirectory()
        cls.attributes = production_webidl.select_document_host_attributes(
            Path(cls.temporary_directory.name) / "cache",
            environment={},
        )
        cls.outputs = generate_document_host.generate_outputs(cls.attributes)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary_directory.cleanup()

    def test_generates_c_vtable_from_real_document_attributes(self) -> None:
        output = self.outputs[generate_document_host.HEADER_NAME]
        expected_fragments = (
            "typedef struct ServoV8OwnedUtf8 {",
            "const uint8_t* data;",
            "uint8_t (*get_hidden)(void* native);",
            "uint8_t (*get_bg_color)(void* native, ServoV8OwnedUtf8* output);",
            "uint8_t (*set_bg_color)(void* native, void* host_context,",
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
            "unsafe fn set_bg_color(",
            "pub struct OwnedUtf8 {",
            "Box::new(native.bg_color().into_bytes())",
            "Box::from_raw(owner.cast::<Vec<u8>>())",
            "std::str::from_utf8(bytes)",
            "set_bg_color: Some(document_host_set_bg_color::<T>)",
        )
        for fragment in expected_fragments:
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, output)

    def test_generates_cpp_webidl_conversion_and_raii(self) -> None:
        output = self.outputs[generate_document_host.CPP_NAME]
        expected_fragments = (
            "class DocumentHostOwnedUtf8Scope {",
            "DocumentHostGetHidden(",
            "DocumentHostGetBgColor(",
            "DocumentHostSetBgColor(",
            "auto* state = UnwrapDocumentHostState(info);",
            "if (info[0]->IsNull()) {",
            "info[0]->ToString(context)",
            "v8::String::Utf8Value utf8(isolate, value);",
            "CallDocumentHostSetBgColor(",
        )
        for fragment in expected_fragments:
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, output)

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
