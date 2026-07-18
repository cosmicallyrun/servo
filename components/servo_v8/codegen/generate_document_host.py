# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Generate a narrow Document host from Servo's production WebIDL corpus."""

from __future__ import annotations

import argparse
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

import production_webidl

if TYPE_CHECKING:
    import WebIDL


HEADER_NAME = "servo_v8_document_host_generated.h"
RUST_NAME = "servo_v8_document_host_generated.rs"
CPP_NAME = "servo_v8_document_host_generated.inc"


def generate_header(attributes: Sequence[WebIDL.IDLAttribute]) -> str:
    """Generate the C ABI for the selected Document host attributes."""

    hidden, bg_color = _validated_attributes(attributes)
    return "\n".join(
        [
            "/* Generated from Servo production WebIDL. Do not edit. */",
            "#ifndef SERVO_V8_DOCUMENT_HOST_GENERATED_H_",
            "#define SERVO_V8_DOCUMENT_HOST_GENERATED_H_",
            "",
            "typedef struct ServoV8OwnedUtf8 {",
            "  const uint8_t* data;",
            "  size_t length;",
            "  void* owner;",
            "  ServoV8DropCallback drop_owner;",
            "} ServoV8OwnedUtf8;",
            "",
            "typedef struct ServoV8DocumentHostVTable {",
            f"  uint8_t (*{_getter_name(hidden)})(void* native);",
            f"  uint8_t (*{_getter_name(bg_color)})(void* native, ServoV8OwnedUtf8* output);",
            f"  uint8_t (*{_setter_name(bg_color)})(void* native, void* host_context,",
            "                                  const uint8_t* value, size_t value_length);",
            "  ServoV8DropCallback drop;",
            "} ServoV8DocumentHostVTable;",
            "",
            "#endif  /* SERVO_V8_DOCUMENT_HOST_GENERATED_H_ */",
            "",
        ]
    )


def generate_rust(attributes: Sequence[WebIDL.IDLAttribute]) -> str:
    """Generate Rust host trait and typed C ABI thunks."""

    hidden, bg_color = _validated_attributes(attributes)
    hidden_member = _member_name(hidden)
    hidden_getter = _getter_name(hidden)
    bg_member = _rust_member_name(bg_color)
    bg_getter = _getter_name(bg_color)
    bg_setter = _setter_name(bg_color)
    return "\n".join(
        [
            "// Generated from Servo production WebIDL. Do not edit.",
            "",
            "/// Native implementation contract for the selected Document host binding.",
            "///",
            "/// # Safety",
            "///",
            "/// Implementations and `T::Drop` must not unwind, re-enter V8 or cppgc, pump an",
            "/// event loop, tear down a pipeline, or access the V8 sidecar `RefCell`.",
            "/// Each installed native pointer must be transferred from exactly one `Box<T>`,",
            "/// remain valid for every callback, and be passed to `drop` exactly once.",
            "/// `host_context` is an ephemeral pointer supplied only during one V8 script",
            "/// run and must never be retained after the setter returns.",
            "pub unsafe trait DocumentHostBinding: Sized + 'static {",
            f"    fn {hidden_member}(&self) -> bool;",
            f"    fn {bg_member}(&self) -> String;",
            "",
            "    /// # Safety",
            "    ///",
            "    /// `host_context` is the live opaque context supplied to the current run.",
            f"    unsafe fn set_{bg_member}(",
            "        &self,",
            "        host_context: *mut c_void,",
            "        value: &str,",
            "    ) -> bool;",
            "}",
            "",
            "#[derive(Clone, Copy)]",
            "#[repr(C)]",
            "pub struct OwnedUtf8 {",
            "    pub data: *const u8,",
            "    pub length: usize,",
            "    pub owner: *mut c_void,",
            "    pub drop_owner: Option<DropCallback>,",
            "}",
            "",
            "#[derive(Clone, Copy)]",
            "#[repr(C)]",
            "pub struct DocumentHostVTable {",
            f'    pub {hidden_getter}: Option<unsafe extern "C" fn(*mut c_void) -> u8>,',
            f'    pub {bg_getter}: Option<unsafe extern "C" fn(*mut c_void, *mut OwnedUtf8) -> u8>,',
            f'    pub {bg_setter}: Option<',
            '        unsafe extern "C" fn(*mut c_void, *mut c_void, *const u8, usize) -> u8,',
            "    >,",
            "    pub drop: Option<DropCallback>,",
            "}",
            "",
            f'unsafe extern "C" fn document_host_{hidden_getter}<T: DocumentHostBinding>(',
            "    native: *mut c_void,",
            ") -> u8 {",
            "    // SAFETY: The vtable contract requires a live Box<T> native pointer.",
            "    let native = unsafe { &*native.cast::<T>() };",
            f"    u8::from(native.{hidden_member}())",
            "}",
            "",
            "unsafe extern \"C\" fn document_host_owned_utf8_drop(owner: *mut c_void) {",
            "    // SAFETY: Every successful getter transfers one Box<Vec<u8>> owner.",
            "    drop(unsafe { Box::from_raw(owner.cast::<Vec<u8>>()) });",
            "}",
            "",
            f'unsafe extern "C" fn document_host_{bg_getter}<T: DocumentHostBinding>(',
            "    native: *mut c_void,",
            "    output: *mut OwnedUtf8,",
            ") -> u8 {",
            "    if output.is_null() {",
            "        return 0;",
            "    }",
            "    // SAFETY: The vtable contract requires a live Box<T> native pointer.",
            "    let native = unsafe { &*native.cast::<T>() };",
            f"    let owner = Box::new(native.{bg_member}().into_bytes());",
            "    // SAFETY: output is non-null and points to caller-owned writable storage.",
            "    unsafe {",
            "        output.write(OwnedUtf8 {",
            "            data: owner.as_ptr(),",
            "            length: owner.len(),",
            "            owner: Box::into_raw(owner).cast(),",
            "            drop_owner: Some(document_host_owned_utf8_drop),",
            "        });",
            "    }",
            "    1",
            "}",
            "",
            f'unsafe extern "C" fn document_host_{bg_setter}<T: DocumentHostBinding>(',
            "    native: *mut c_void,",
            "    host_context: *mut c_void,",
            "    value: *const u8,",
            "    value_length: usize,",
            ") -> u8 {",
            "    if host_context.is_null() || (value.is_null() && value_length != 0) {",
            "        return 0;",
            "    }",
            "    // SAFETY: C++ supplies a live native and a synchronous UTF-8 byte view.",
            "    let native = unsafe { &*native.cast::<T>() };",
            "    let bytes = if value_length == 0 {",
            "        &[]",
            "    } else {",
            "        // SAFETY: A non-empty C++ view has a non-null pointer and exact length.",
            "        unsafe { std::slice::from_raw_parts(value, value_length) }",
            "    };",
            "    let Ok(value) = std::str::from_utf8(bytes) else {",
            "        return 0;",
            "    };",
            "    // SAFETY: The caller supplies the ephemeral host context for this run.",
            f"    u8::from(unsafe {{ native.set_{bg_member}(host_context, value) }})",
            "}",
            "",
            'unsafe extern "C" fn document_host_drop<T: DocumentHostBinding>(',
            "    native: *mut c_void,",
            ") {",
            "    // SAFETY: The vtable contract transfers this exact Box<T> for one drop.",
            "    drop(unsafe { Box::from_raw(native.cast::<T>()) });",
            "}",
            "",
            "impl DocumentHostVTable {",
            "    pub fn for_type<T: DocumentHostBinding>() -> Self {",
            "        Self {",
            f"            {hidden_getter}: Some(document_host_{hidden_getter}::<T>),",
            f"            {bg_getter}: Some(document_host_{bg_getter}::<T>),",
            f"            {bg_setter}: Some(document_host_{bg_setter}::<T>),",
            "            drop: Some(document_host_drop::<T>),",
            "        }",
            "    }",
            "}",
            "",
        ]
    )


def generate_cpp(attributes: Sequence[WebIDL.IDLAttribute]) -> str:
    """Generate C++ vtable validation and the V8 accessor callback."""

    hidden, bg_color = _validated_attributes(attributes)
    hidden_getter = _getter_name(hidden)
    bg_getter = _getter_name(bg_color)
    bg_setter = _setter_name(bg_color)
    return "\n".join(
        [
            "// Generated from Servo production WebIDL. Do not edit.",
            "",
            "bool IsDocumentHostVTableComplete(",
            "    const ServoV8DocumentHostVTable& vtable) {",
            f"  return vtable.{hidden_getter} && vtable.{bg_getter} &&",
            f"         vtable.{bg_setter} && vtable.drop;",
            "}",
            "",
            "void DocumentHostGetHidden(",
            "    const v8::FunctionCallbackInfo<v8::Value>& info) {",
            "  v8::Isolate* isolate = info.GetIsolate();",
            "  auto* state = UnwrapDocumentHostState(info);",
            f"  if (!state || !state->native || !state->vtable.{hidden_getter}) {{",
            '    ThrowTypeError(isolate, "invalid Document host state");',
            "    return;",
            "  }",
            "  bool hidden = false;",
            "  if (!CallDocumentHostGetHidden(state, &hidden)) {",
            '    ThrowTypeError(isolate, "re-entrant Document host callback");',
            "    return;",
            "  }",
            "  info.GetReturnValue().Set(v8::Boolean::New(isolate, hidden));",
            "}",
            "",
            "class DocumentHostOwnedUtf8Scope {",
            " public:",
            "  explicit DocumentHostOwnedUtf8Scope(ServoV8OwnedUtf8* value)",
            "      : value_(value) {}",
            "  ~DocumentHostOwnedUtf8Scope() {",
            "    if (value_->owner && value_->drop_owner) {",
            "      value_->drop_owner(value_->owner);",
            "    }",
            "  }",
            "",
            " private:",
            "  ServoV8OwnedUtf8* value_;",
            "};",
            "",
            "void DocumentHostGetBgColor(",
            "    const v8::FunctionCallbackInfo<v8::Value>& info) {",
            "  v8::Isolate* isolate = info.GetIsolate();",
            "  auto* state = UnwrapDocumentHostState(info);",
            f"  if (!state || !state->native || !state->vtable.{bg_getter}) {{",
            '    ThrowTypeError(isolate, "invalid Document host state");',
            "    return;",
            "  }",
            "  ServoV8OwnedUtf8 value{};",
            "  if (!CallDocumentHostGetBgColor(state, &value)) {",
            '    ThrowTypeError(isolate, "re-entrant Document host callback");',
            "    return;",
            "  }",
            "  DocumentHostOwnedUtf8Scope value_scope(&value);",
            "  if ((!value.data && value.length != 0) ||",
            "      value.length > static_cast<size_t>(std::numeric_limits<int>::max())) {",
            '    ThrowTypeError(isolate, "invalid Document.bgColor UTF-8 result");',
            "    return;",
            "  }",
            "  v8::Local<v8::String> result;",
            "  if (!v8::String::NewFromUtf8(",
            "           isolate, reinterpret_cast<const char*>(value.data),",
            "           v8::NewStringType::kNormal, static_cast<int>(value.length))",
            "           .ToLocal(&result)) {",
            "    return;",
            "  }",
            "  info.GetReturnValue().Set(result);",
            "}",
            "",
            "void DocumentHostSetBgColor(",
            "    const v8::FunctionCallbackInfo<v8::Value>& info) {",
            "  v8::Isolate* isolate = info.GetIsolate();",
            "  auto* state = UnwrapDocumentHostState(info);",
            f"  if (!state || !state->native || !state->vtable.{bg_setter}) {{",
            '    ThrowTypeError(isolate, "invalid Document host state");',
            "    return;",
            "  }",
            "  v8::Local<v8::Context> context = isolate->GetCurrentContext();",
            "  v8::Local<v8::String> value;",
            "  if (info[0]->IsNull()) {",
            "    value = v8::String::Empty(isolate);",
            "  } else if (!info[0]->ToString(context).ToLocal(&value)) {",
            "    return;",
            "  }",
            "  v8::String::Utf8Value utf8(isolate, value);",
            "  if (!*utf8) {",
            '    ThrowTypeError(isolate, "could not encode Document.bgColor as UTF-8");',
            "    return;",
            "  }",
            "  if (!CallDocumentHostSetBgColor(",
            "          state, reinterpret_cast<const uint8_t*>(*utf8),",
            "          static_cast<size_t>(utf8.length()))) {",
            '    ThrowTypeError(isolate, "Document.bgColor host callback failed");',
            "  }",
            "}",
            "",
        ]
    )


def generate_outputs(attributes: Sequence[WebIDL.IDLAttribute]) -> dict[str, str]:
    """Generate every Document host artifact without writing it."""

    return {
        HEADER_NAME: generate_header(attributes),
        RUST_NAME: generate_rust(attributes),
        CPP_NAME: generate_cpp(attributes),
    }


def write_outputs(webidls_dir: Path, out_dir: Path) -> None:
    """Select production ``Document.hidden`` and write its three artifacts."""

    with tempfile.TemporaryDirectory(prefix="servo-v8-document-host-webidl-") as cache_dir:
        attributes = production_webidl.select_document_host_attributes(
            Path(cache_dir),
            webidls_dir=webidls_dir,
        )
    out_dir.mkdir(parents=True, exist_ok=True)
    for filename, contents in generate_outputs(attributes).items():
        (out_dir / filename).write_text(contents, encoding="utf-8")


def _validated_attributes(
    attributes: Sequence[WebIDL.IDLAttribute],
) -> tuple[WebIDL.IDLAttribute, WebIDL.IDLAttribute]:
    if len(attributes) != 2:
        raise production_webidl.WebIDLSelectionError(
            f"Document host generation expected 2 members, found {len(attributes)}"
        )
    hidden, bg_color = attributes
    names = (hidden.identifier.name, bg_color.identifier.name)
    if names != ("hidden", "bgColor"):
        raise production_webidl.WebIDLSelectionError(
            f"Document host generation expected (`hidden`, `bgColor`), found {names}"
        )
    return hidden, bg_color


def _member_name(attribute: WebIDL.IDLAttribute) -> str:
    return attribute.identifier.name


def _rust_member_name(attribute: WebIDL.IDLAttribute) -> str:
    if attribute.identifier.name == "bgColor":
        return "bg_color"
    return _member_name(attribute)


def _getter_name(attribute: WebIDL.IDLAttribute) -> str:
    return f"get_{_rust_member_name(attribute)}"


def _setter_name(attribute: WebIDL.IDLAttribute) -> str:
    return f"set_{_rust_member_name(attribute)}"


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("webidls_dir", type=Path)
    parser.add_argument("out_dir", type=Path)
    arguments = parser.parse_args(argv)
    write_outputs(arguments.webidls_dir, arguments.out_dir)


if __name__ == "__main__":
    main()
