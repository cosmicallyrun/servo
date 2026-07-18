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

import generate  # noqa: E402


class GeneratedIdentifierValidationTests(unittest.TestCase):
    def assert_rejected(self, source: str, expected: str) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            webidl = root / "input.webidl"
            webidl.write_text(source, encoding="utf-8")
            interface = generate.parse_interface(webidl, root / "out")
            with self.assertRaisesRegex(RuntimeError, re.escape(expected)):
                generate.analyze(interface)

    def test_rejects_lifecycle_callback_collisions(self) -> None:
        for operation in ("drop", "trace"):
            with self.subTest(operation=operation):
                self.assert_rejected(
                    f"""
                    interface EngineBindingSmoke {{
                      constructor(long value);
                      long {operation}();
                    }};
                    """,
                    f"generated identifier `{operation}` for operation `{operation}` "
                    f"collides with generated lifecycle callback `{operation}`",
                )

        self.assert_rejected(
            """
            interface EngineBindingSmoke {
              constructor(long value);
              readonly attribute long drop;
            };
            """,
            "generated identifier `drop` for attribute getter `drop` "
            "collides with reserved Rust destructor method `drop`",
        )

    def test_rejects_attribute_method_collision(self) -> None:
        self.assert_rejected(
            """
            interface EngineBindingSmoke {
              constructor(long value);
              attribute long value;
              long setValue(long rhs);
            };
            """,
            "generated identifier `set_value` for operation `setValue` collides with attribute setter `value`",
        )

    def test_rejects_hidden_native_argument(self) -> None:
        self.assert_rejected(
            """
            interface EngineBindingSmoke {
              constructor(long value);
              long add(long native);
            };
            """,
            "generated identifier `native` for operation `add` argument `native` "
            "collides with generated callback local `native`",
        )

    def test_rejects_rust_keyword_argument(self) -> None:
        self.assert_rejected(
            """
            interface EngineBindingSmoke {
              constructor(long value);
              long add(long type);
            };
            """,
            "generated identifier `type` for operation `add` argument `type` is reserved by C, C++, or Rust",
        )

    def test_rejects_cpp_callback_name_collision(self) -> None:
        self.assert_rejected(
            """
            interface EngineBindingSmoke {
              constructor(long value);
              readonly attribute long foo_bar;
              readonly attribute long foo__bar;
            };
            """,
            "generated identifier `GetFooBar` for C++ callback for attribute "
            "getter `foo__bar` collides with C++ callback for attribute getter `foo_bar`",
        )


class GeneratedStableInterfaceIdTests(unittest.TestCase):
    def generate_outputs(self) -> tuple[str, str, str]:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            webidl = root / "input.webidl"
            webidl.write_text(
                """
                interface EngineBindingSmoke {
                  constructor(long value);
                  long setChild(EngineBindingSmoke child);
                };
                """,
                encoding="utf-8",
            )
            interface = generate.parse_interface(webidl, root / "out")
            constructor_arguments, attributes, methods = generate.analyze(interface)
            return (
                generate.generate_header(interface, constructor_arguments, attributes, methods),
                generate.generate_rust(interface, constructor_arguments, attributes, methods),
                generate.generate_cpp(interface, constructor_arguments, attributes, methods),
            )

    def test_generates_stable_interface_id_for_all_abi_layers(self) -> None:
        header, rust, cpp = self.generate_outputs()

        self.assertIn(
            "#define SERVO_V8_ENGINE_BINDING_SMOKE_INTERFACE_ID UINT32_C(1)",
            header,
        )
        self.assertIn("pub const ENGINE_BINDING_SMOKE_INTERFACE_ID: u32 = 1;", rust)
        self.assertIn(
            "servo_v8_dom_cell_native(self.cell, ENGINE_BINDING_SMOKE_INTERFACE_ID)",
            rust,
        )
        self.assertIn(
            "servo_v8_trace_dom_cell(visitor, self.cell, ENGINE_BINDING_SMOKE_INTERFACE_ID)",
            rust,
        )
        self.assertIn(
            "cpp_heap->GetAllocationHandle(), native, SERVO_V8_ENGINE_BINDING_SMOKE_INTERFACE_ID,",
            cpp,
        )


if __name__ == "__main__":
    unittest.main()
