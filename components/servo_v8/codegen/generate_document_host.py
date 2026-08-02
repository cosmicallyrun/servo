# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Generate a narrow Document host from Servo's production WebIDL corpus.

Every artifact is emitted member by member from the shapes named in
``production_webidl.DOCUMENT_HOST``: a member contributes its own declarations,
thunks, and accessor bodies, and the blocks a shape needs only once are emitted
where its first member appears. Nothing here is specific to a member name.
"""

from __future__ import annotations

import argparse
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import NamedTuple, TYPE_CHECKING

import generate
import production_webidl

if TYPE_CHECKING:
    import WebIDL


HEADER_NAME = "servo_v8_document_host_generated.h"
RUST_NAME = "servo_v8_document_host_generated.rs"
CPP_NAME = "servo_v8_document_host_generated.inc"

# A block is a run of generated lines with no blank line inside it; the writers
# below assemble artifacts out of blocks so that spacing never depends on which
# member emitted what.
Block = list[str]

# Wrapped C signatures use a fixed hanging indent rather than aligning under the
# open parenthesis, so renaming or adding a member cannot reflow lines it did not
# introduce.
C_SIGNATURE_INDENT = " " * 34

# The generated C++ conjunction is wrapped by column so it stays readable as the
# member list grows. Counting the trailing operator keeps the break stable when
# an operand happens to end exactly on the budget.
CPP_CONJUNCTION_WIDTH = 72


class Member(NamedTuple):
    """One selected production member paired with the shape that emits it."""

    shape: str
    qualified_name: str
    expected_interface: str | None
    attribute: WebIDL.IDLAttribute


class ShapeEmitter(NamedTuple):
    """The emitters for one member shape.

    ``*_blocks`` fields are the blocks a shape needs exactly once; they are
    emitted at the position of the shape's first member. The callables run once
    per member, in declaration order.
    """

    header_type_blocks: tuple[Block, ...]
    header_slots: Callable[[Member], Block]
    rust_type_blocks: tuple[Block, ...]
    rust_trait_members: Callable[[Member], Block]
    rust_vtable_fields: Callable[[Member], Block]
    rust_thunk_blocks: tuple[Block, ...]
    rust_thunks: Callable[[Member], tuple[Block, ...]]
    rust_vtable_init: Callable[[Member], Block]
    cpp_body_blocks: tuple[Block, ...]
    cpp_bodies: Callable[[Member], tuple[Block, ...]]
    cpp_vtable_terms: Callable[[Member], list[str]]


def generate_header(attributes: Mapping[str, WebIDL.IDLAttribute]) -> str:
    """Generate the C ABI for the selected Document host attributes."""

    members = _members(attributes)
    blocks = [
        [
            "/* Generated from Servo production WebIDL. Do not edit. */",
            "#ifndef SERVO_V8_DOCUMENT_HOST_GENERATED_H_",
            "#define SERVO_V8_DOCUMENT_HOST_GENERATED_H_",
        ],
        *_shape_blocks(members, "header_type_blocks"),
        [
            "typedef struct ServoV8DocumentHostVTable {",
            *_per_member(members, "header_slots"),
            "  ServoV8DropCallback drop;",
            "} ServoV8DocumentHostVTable;",
        ],
        ["#endif  /* SERVO_V8_DOCUMENT_HOST_GENERATED_H_ */"],
    ]
    return _render(blocks)


def generate_rust(attributes: Mapping[str, WebIDL.IDLAttribute]) -> str:
    """Generate Rust host trait and typed C ABI thunks."""

    members = _members(attributes)
    blocks = [
        ["// Generated from Servo production WebIDL. Do not edit."],
        [
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
            *_per_member(members, "rust_trait_members"),
            "}",
        ],
        *_shape_blocks(members, "rust_type_blocks"),
        [
            "#[derive(Clone, Copy)]",
            "#[repr(C)]",
            "pub struct DocumentHostVTable {",
            *_per_member(members, "rust_vtable_fields"),
            "    pub drop: Option<DropCallback>,",
            "}",
        ],
        *_interleaved_blocks(members, "rust_thunk_blocks", "rust_thunks"),
        [
            'unsafe extern "C" fn document_host_drop<T: DocumentHostBinding>(',
            "    native: *mut c_void,",
            ") {",
            "    // SAFETY: The vtable contract transfers this exact Box<T> for one drop.",
            "    drop(unsafe { Box::from_raw(native.cast::<T>()) });",
            "}",
        ],
        [
            "impl DocumentHostVTable {",
            "    pub fn for_type<T: DocumentHostBinding>() -> Self {",
            "        Self {",
            *_per_member(members, "rust_vtable_init"),
            "            drop: Some(document_host_drop::<T>),",
            "        }",
            "    }",
            "}",
        ],
    ]
    return _render(blocks)


def generate_cpp(attributes: Mapping[str, WebIDL.IDLAttribute]) -> str:
    """Generate C++ vtable validation and the V8 accessor callbacks."""

    members = _members(attributes)
    terms = [term for member in members for term in _emitter(member).cpp_vtable_terms(member)]
    # Continuation lines align under the first operand of the `return`.
    return_prefix = "  return "
    blocks = [
        ["// Generated from Servo production WebIDL. Do not edit."],
        [
            "bool IsDocumentHostVTableComplete(",
            "    const ServoV8DocumentHostVTable& vtable) {",
            *_conjunction([*terms, "vtable.drop"], return_prefix, " " * len(return_prefix)),
            "}",
        ],
        *_interleaved_blocks(members, "cpp_body_blocks", "cpp_bodies"),
        _cpp_accessor_installation(members),
    ]
    return _render(blocks)


def generate_outputs(attributes: Mapping[str, WebIDL.IDLAttribute]) -> dict[str, str]:
    """Generate every Document host artifact without writing it."""

    return {
        HEADER_NAME: generate_header(attributes),
        RUST_NAME: generate_rust(attributes),
        CPP_NAME: generate_cpp(attributes),
    }


def write_outputs(webidls_dir: Path, out_dir: Path) -> None:
    """Select the production Document slice and write its three artifacts."""

    with tempfile.TemporaryDirectory(prefix="servo-v8-document-host-webidl-") as cache_dir:
        attributes = production_webidl.select_document_host_attributes(
            Path(cache_dir),
            webidls_dir=webidls_dir,
        )
    out_dir.mkdir(parents=True, exist_ok=True)
    for filename, contents in generate_outputs(attributes).items():
        (out_dir / filename).write_text(contents, encoding="utf-8")


def _members(attributes: Mapping[str, WebIDL.IDLAttribute]) -> list[Member]:
    """Pair the selected attributes with the shapes the manifest expects."""

    expected = [member.qualified_name for member in production_webidl.DOCUMENT_HOST]
    if list(attributes) != expected:
        raise production_webidl.WebIDLSelectionError(
            f"Document host generation expected {expected}, found {list(attributes)}"
        )
    for member in production_webidl.DOCUMENT_HOST:
        if member.shape not in SHAPE_EMITTERS:
            raise production_webidl.WebIDLSelectionError(
                f"Document host generation cannot emit shape `{member.shape}`"
            )
    return [
        Member(
            shape=member.shape,
            qualified_name=member.qualified_name,
            expected_interface=member.expected_interface,
            attribute=attributes[member.qualified_name],
        )
        for member in production_webidl.DOCUMENT_HOST
    ]


def _emitter(member: Member) -> ShapeEmitter:
    return SHAPE_EMITTERS[member.shape]


def _per_member(members: Sequence[Member], field: str) -> list[str]:
    """Concatenate one emitter's lines for every member, in declaration order."""

    return [line for member in members for line in getattr(_emitter(member), field)(member)]


def _shape_blocks(members: Sequence[Member], field: str) -> list[Block]:
    """Collect each shape's once-only blocks, in order of first use.

    Keyed on block identity rather than on shape: distinct shapes share blocks,
    because every string-valued member needs the same owned-UTF-8 transfer, and
    a shared block must still be emitted exactly once.
    """

    blocks: list[Block] = []
    seen: set[int] = set()
    for shape in dict.fromkeys(member.shape for member in members):
        for block in getattr(SHAPE_EMITTERS[shape], field):
            if id(block) not in seen:
                seen.add(id(block))
                blocks.append(block)
    return blocks


def _interleaved_blocks(members: Sequence[Member], shared_field: str, member_field: str) -> list[Block]:
    """Emit each shape's once-only blocks just before its first member's blocks."""

    blocks: list[Block] = []
    seen: set[int] = set()
    for member in members:
        emitter = _emitter(member)
        for block in getattr(emitter, shared_field):
            if id(block) not in seen:
                seen.add(id(block))
                blocks.append(block)
        blocks.extend(getattr(emitter, member_field)(member))
    return blocks


def _render(blocks: Sequence[Block]) -> str:
    """Join blocks with one blank line between them and a trailing newline."""

    lines: list[str] = []
    for block in blocks:
        if lines:
            lines.append("")
        lines.extend(block)
    return "\n".join([*lines, ""])


def _conjunction(terms: Sequence[str], first_prefix: str, continuation_prefix: str) -> list[str]:
    """Wrap ``a && b && ...;`` across lines without exceeding the width budget."""

    lines: list[str] = []
    current = first_prefix
    first_on_line = True
    for index, term in enumerate(terms):
        tail = ";" if index == len(terms) - 1 else " &&"
        candidate = current + term if first_on_line else f"{current} && {term}"
        if not first_on_line and len(candidate) + len(tail) > CPP_CONJUNCTION_WIDTH:
            lines.append(f"{current} &&")
            candidate = continuation_prefix + term
        current = candidate
        first_on_line = False
    return [*lines, f"{current};"]


def _rust_member_name(attribute: WebIDL.IDLAttribute) -> str:
    return generate.snake_case(attribute.identifier.name)


def _cpp_member_name(attribute: WebIDL.IDLAttribute) -> str:
    return generate.upper_camel_case(attribute.identifier.name)


def _getter_name(attribute: WebIDL.IDLAttribute) -> str:
    return f"get_{_rust_member_name(attribute)}"


def _setter_name(attribute: WebIDL.IDLAttribute) -> str:
    return f"set_{_rust_member_name(attribute)}"


def _cpp_accessor_installation(members: Sequence[Member]) -> Block:
    """Generate all V8 function objects and prototype properties from the manifest."""

    lines = [
        "bool InstallDocumentHostAccessors(",
        "    v8::Isolate* isolate,",
        "    v8::Local<v8::Context> context,",
        "    v8::Local<v8::Object> prototype) {",
    ]
    for member in members:
        local = _rust_member_name(member.attribute)
        lines.append(f"  v8::Local<v8::Function> {local}_getter;")
        if not member.attribute.readonly:
            lines.append(f"  v8::Local<v8::Function> {local}_setter;")

    for member in members:
        local = _rust_member_name(member.attribute)
        callback = _cpp_member_name(member.attribute)
        lines.extend(
            [
                f"  if (!v8::Function::New(context, DocumentHostGet{callback},",
                "                         v8::Local<v8::Data>(), 0,",
                "                         v8::ConstructorBehavior::kThrow,",
                "                         v8::SideEffectType::kHasNoSideEffect)",
                f"           .ToLocal(&{local}_getter)) {{",
                "    return false;",
                "  }",
            ]
        )
        if not member.attribute.readonly:
            lines.extend(
                [
                    f"  if (!v8::Function::New(context, DocumentHostSet{callback},",
                    "                         v8::Local<v8::Data>(), 1,",
                    "                         v8::ConstructorBehavior::kThrow,",
                    "                         v8::SideEffectType::kHasSideEffect)",
                    f"           .ToLocal(&{local}_setter)) {{",
                    "    return false;",
                    "  }",
                ]
            )

    for member in members:
        local = _rust_member_name(member.attribute)
        member_name = member.attribute.identifier.name
        lines.append(f'  {local}_getter->SetName(V8String(isolate, "get {member_name}"));')
        if not member.attribute.readonly:
            lines.append(f'  {local}_setter->SetName(V8String(isolate, "set {member_name}"));')

    for member in members:
        local = _rust_member_name(member.attribute)
        setter = f"{local}_setter" if not member.attribute.readonly else "v8::Undefined(isolate)"
        lines.extend(
            [
                f"  v8::PropertyDescriptor {local}_descriptor({local}_getter, {setter});",
                f"  {local}_descriptor.set_enumerable(true);",
                f"  {local}_descriptor.set_configurable(true);",
            ]
        )

    for member in members:
        local = _rust_member_name(member.attribute)
        member_name = member.attribute.identifier.name
        lines.extend(
            [
                "  if (!prototype",
                f'           ->DefineProperty(context, V8String(isolate, "{member_name}"),',
                f"                            {local}_descriptor)",
                "           .FromMaybe(false)) {",
                "    return false;",
                "  }",
            ]
        )
    lines.extend(["  return true;", "}"])
    return lines


def _readonly_boolean_header_slots(member: Member) -> Block:
    return [f"  uint8_t (*{_getter_name(member.attribute)})(void* native);"]


def _readonly_boolean_rust_trait_members(member: Member) -> Block:
    return [f"    fn {_rust_member_name(member.attribute)}(&self) -> bool;"]


def _readonly_boolean_rust_vtable_fields(member: Member) -> Block:
    return [f'    pub {_getter_name(member.attribute)}: Option<unsafe extern "C" fn(*mut c_void) -> u8>,']


def _readonly_boolean_rust_thunks(member: Member) -> tuple[Block, ...]:
    getter = _getter_name(member.attribute)
    return (
        [
            f'unsafe extern "C" fn document_host_{getter}<T: DocumentHostBinding>(',
            "    native: *mut c_void,",
            ") -> u8 {",
            "    // SAFETY: The vtable contract requires a live Box<T> native pointer.",
            "    let native = unsafe { &*native.cast::<T>() };",
            f"    u8::from(native.{_rust_member_name(member.attribute)}())",
            "}",
        ],
    )


def _readonly_boolean_rust_vtable_init(member: Member) -> Block:
    getter = _getter_name(member.attribute)
    return [f"            {getter}: Some(document_host_{getter}::<T>),"]


def _readonly_boolean_cpp_bodies(member: Member) -> tuple[Block, ...]:
    getter = _getter_name(member.attribute)
    local = _rust_member_name(member.attribute)
    accessor = _cpp_member_name(member.attribute)
    return (
        [
            f"void DocumentHostGet{accessor}(",
            "    const v8::FunctionCallbackInfo<v8::Value>& info) {",
            "  v8::Isolate* isolate = info.GetIsolate();",
            "  auto* state = UnwrapDocumentHostState(info);",
            f"  if (!state || !state->native || !state->vtable.{getter}) {{",
            '    ThrowTypeError(isolate, "invalid Document host state");',
            "    return;",
            "  }",
            f"  bool {local} = false;",
            f"  if (!CallDocumentHostGet{accessor}(state, &{local})) {{",
            '    ThrowTypeError(isolate, "re-entrant Document host callback");',
            "    return;",
            "  }",
            f"  info.GetReturnValue().Set(v8::Boolean::New(isolate, {local}));",
            "}",
        ],
    )


def _readonly_boolean_cpp_vtable_terms(member: Member) -> list[str]:
    return [f"vtable.{_getter_name(member.attribute)}"]


def _legacy_domstring_header_slots(member: Member) -> Block:
    return [
        f"  uint8_t (*{_getter_name(member.attribute)})(void* native, ServoV8OwnedUtf8* output);",
        f"  uint8_t (*{_setter_name(member.attribute)})(void* native, void* host_context,",
        f"{C_SIGNATURE_INDENT}const uint8_t* value, size_t value_length);",
    ]


def _legacy_domstring_rust_trait_members(member: Member) -> Block:
    name = _rust_member_name(member.attribute)
    return [
        f"    fn {name}(&self) -> String;",
        "",
        "    /// # Safety",
        "    ///",
        "    /// `host_context` is the live opaque context supplied to the current run.",
        f"    unsafe fn set_{name}(",
        "        &self,",
        "        host_context: *mut c_void,",
        "        value: &str,",
        "    ) -> bool;",
    ]


def _legacy_domstring_rust_vtable_fields(member: Member) -> Block:
    return [
        f'    pub {_getter_name(member.attribute)}: Option<unsafe extern "C" fn(*mut c_void, *mut OwnedUtf8) -> u8>,',
        f"    pub {_setter_name(member.attribute)}: Option<",
        '        unsafe extern "C" fn(*mut c_void, *mut c_void, *const u8, usize) -> u8,',
        "    >,",
    ]


def _legacy_domstring_rust_thunks(member: Member) -> tuple[Block, ...]:
    name = _rust_member_name(member.attribute)
    getter = _getter_name(member.attribute)
    setter = _setter_name(member.attribute)
    return (
        [
            f'unsafe extern "C" fn document_host_{getter}<T: DocumentHostBinding>(',
            "    native: *mut c_void,",
            "    output: *mut OwnedUtf8,",
            ") -> u8 {",
            "    if output.is_null() {",
            "        return 0;",
            "    }",
            "    // SAFETY: The vtable contract requires a live Box<T> native pointer.",
            "    let native = unsafe { &*native.cast::<T>() };",
            f"    let owner = Box::new(native.{name}().into_bytes());",
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
        ],
        [
            f'unsafe extern "C" fn document_host_{setter}<T: DocumentHostBinding>(',
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
            f"    u8::from(unsafe {{ native.set_{name}(host_context, value) }})",
            "}",
        ],
    )


def _legacy_domstring_rust_vtable_init(member: Member) -> Block:
    getter = _getter_name(member.attribute)
    setter = _setter_name(member.attribute)
    return [
        f"            {getter}: Some(document_host_{getter}::<T>),",
        f"            {setter}: Some(document_host_{setter}::<T>),",
    ]


def _legacy_domstring_cpp_bodies(member: Member) -> tuple[Block, ...]:
    getter = _getter_name(member.attribute)
    setter = _setter_name(member.attribute)
    accessor = _cpp_member_name(member.attribute)
    qualified_name = member.qualified_name
    return (
        [
            f"void DocumentHostGet{accessor}(",
            "    const v8::FunctionCallbackInfo<v8::Value>& info) {",
            "  v8::Isolate* isolate = info.GetIsolate();",
            "  auto* state = UnwrapDocumentHostState(info);",
            f"  if (!state || !state->native || !state->vtable.{getter}) {{",
            '    ThrowTypeError(isolate, "invalid Document host state");',
            "    return;",
            "  }",
            "  ServoV8OwnedUtf8 value{};",
            f"  if (!CallDocumentHostGet{accessor}(state, &value)) {{",
            '    ThrowTypeError(isolate, "re-entrant Document host callback");',
            "    return;",
            "  }",
            "  DocumentHostOwnedUtf8Scope value_scope(state->runtime, &value);",
            "  if ((!value.data && value.length != 0) ||",
            "      value.length > static_cast<size_t>(std::numeric_limits<int>::max())) {",
            f'    ThrowTypeError(isolate, "invalid {qualified_name} UTF-8 result");',
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
        ],
        [
            f"void DocumentHostSet{accessor}(",
            "    const v8::FunctionCallbackInfo<v8::Value>& info) {",
            "  v8::Isolate* isolate = info.GetIsolate();",
            "  auto* state = UnwrapDocumentHostState(info);",
            f"  if (!state || !state->native || !state->vtable.{setter}) {{",
            '    ThrowTypeError(isolate, "invalid Document host state");',
            "    return;",
            "  }",
            "  v8::Local<v8::Context> context = isolate->GetCurrentContext();",
            "  v8::Local<v8::String> value;",
            # [LegacyNullToEmptyString] is why null becomes the empty string here
            # instead of the string "null" that ToString() would produce.
            "  if (info[0]->IsNull()) {",
            "    value = v8::String::Empty(isolate);",
            "  } else if (!info[0]->ToString(context).ToLocal(&value)) {",
            "    return;",
            "  }",
            "  v8::String::Utf8Value utf8(isolate, value);",
            "  if (!*utf8) {",
            f'    ThrowTypeError(isolate, "could not encode {qualified_name} as UTF-8");',
            "    return;",
            "  }",
            f"  if (!CallDocumentHostSet{accessor}(",
            "          state, reinterpret_cast<const uint8_t*>(*utf8),",
            "          static_cast<size_t>(utf8.length()))) {",
            f'    ThrowTypeError(isolate, "{qualified_name} host callback failed");',
            "  }",
            "}",
        ],
    )


def _legacy_domstring_cpp_vtable_terms(member: Member) -> list[str]:
    return [
        f"vtable.{_getter_name(member.attribute)}",
        f"vtable.{_setter_name(member.attribute)}",
    ]


# A readonly string getter is the DOMString shape's getter half: same owned
# UTF-8 transfer, no setter, and so no CEReactions stack and no host context.
def _readonly_usvstring_header_slots(member: Member) -> Block:
    return [
        f"  uint8_t (*{_getter_name(member.attribute)})(void* native, ServoV8OwnedUtf8* output);",
    ]


def _readonly_usvstring_rust_trait_members(member: Member) -> Block:
    return [f"    fn {_rust_member_name(member.attribute)}(&self) -> String;"]


def _readonly_usvstring_rust_vtable_fields(member: Member) -> Block:
    return [
        f'    pub {_getter_name(member.attribute)}: Option<unsafe extern "C" fn(*mut c_void, *mut OwnedUtf8) -> u8>,',
    ]


def _readonly_usvstring_rust_thunks(member: Member) -> tuple[Block, ...]:
    name = _rust_member_name(member.attribute)
    getter = _getter_name(member.attribute)
    return (
        [
            f'unsafe extern "C" fn document_host_{getter}<T: DocumentHostBinding>(',
            "    native: *mut c_void,",
            "    output: *mut OwnedUtf8,",
            ") -> u8 {",
            "    if output.is_null() {",
            "        return 0;",
            "    }",
            "    // SAFETY: The vtable contract requires a live Box<T> native pointer.",
            "    let native = unsafe { &*native.cast::<T>() };",
            f"    let owner = Box::new(native.{name}().into_bytes());",
            "    // SAFETY: output is non-null and points to caller-owned writable storage.",
            "    unsafe {",
            "        *output = OwnedUtf8 {",
            "            data: owner.as_ptr(),",
            "            length: owner.len(),",
            "            owner: Box::into_raw(owner).cast::<c_void>(),",
            "            drop_owner: Some(document_host_owned_utf8_drop),",
            "        };",
            "    }",
            "    1",
            "}",
        ],
    )


def _readonly_usvstring_rust_vtable_init(member: Member) -> Block:
    getter = _getter_name(member.attribute)
    return [f"            {getter}: Some(document_host_{getter}::<T>),"]


def _readonly_usvstring_cpp_bodies(member: Member) -> tuple[Block, ...]:
    getter = _getter_name(member.attribute)
    accessor = _cpp_member_name(member.attribute)
    qualified_name = member.qualified_name
    return (
        [
            f"void DocumentHostGet{accessor}(",
            "    const v8::FunctionCallbackInfo<v8::Value>& info) {",
            "  v8::Isolate* isolate = info.GetIsolate();",
            "  auto* state = UnwrapDocumentHostState(info);",
            f"  if (!state || !state->native || !state->vtable.{getter}) {{",
            '    ThrowTypeError(isolate, "invalid Document host state");',
            "    return;",
            "  }",
            "  ServoV8OwnedUtf8 value{};",
            f"  if (!CallDocumentHostGet{accessor}(state, &value)) {{",
            '    ThrowTypeError(isolate, "re-entrant Document host callback");',
            "    return;",
            "  }",
            "  DocumentHostOwnedUtf8Scope value_scope(state->runtime, &value);",
            "  if ((!value.data && value.length != 0) ||",
            "      value.length > static_cast<size_t>(std::numeric_limits<int>::max())) {",
            f'    ThrowTypeError(isolate, "invalid {qualified_name} UTF-8 result");',
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
        ],
    )


def _readonly_usvstring_cpp_vtable_terms(member: Member) -> list[str]:
    return [f"vtable.{_getter_name(member.attribute)}"]


# An enum crosses the ABI as its string value, so it reuses the owned-UTF-8
# getter wholesale; the selector pins the value set, which is what keeps the
# untyped string honest.
_readonly_enum_header_slots = _readonly_usvstring_header_slots
_readonly_enum_rust_trait_members = _readonly_usvstring_rust_trait_members
_readonly_enum_rust_vtable_fields = _readonly_usvstring_rust_vtable_fields
_readonly_enum_rust_thunks = _readonly_usvstring_rust_thunks
_readonly_enum_rust_vtable_init = _readonly_usvstring_rust_vtable_init
_readonly_enum_cpp_bodies = _readonly_usvstring_cpp_bodies
_readonly_enum_cpp_vtable_terms = _readonly_usvstring_cpp_vtable_terms


# `unsigned short` is the boolean shape widened: an infallible POD return with
# no out-parameter and so no error channel, where the returned value *is* the
# result rather than a status.
def _readonly_unsigned_short_header_slots(member: Member) -> Block:
    return [f"  uint16_t (*{_getter_name(member.attribute)})(void* native);"]


def _readonly_unsigned_short_rust_trait_members(member: Member) -> Block:
    return [f"    fn {_rust_member_name(member.attribute)}(&self) -> u16;"]


def _readonly_unsigned_short_rust_vtable_fields(member: Member) -> Block:
    return [f'    pub {_getter_name(member.attribute)}: Option<unsafe extern "C" fn(*mut c_void) -> u16>,']


def _readonly_unsigned_short_rust_thunks(member: Member) -> tuple[Block, ...]:
    name = _rust_member_name(member.attribute)
    getter = _getter_name(member.attribute)
    return (
        [
            f'unsafe extern "C" fn document_host_{getter}<T: DocumentHostBinding>(',
            "    native: *mut c_void,",
            ") -> u16 {",
            "    // SAFETY: The vtable contract requires a live Box<T> native pointer.",
            f"    unsafe {{ &*native.cast::<T>() }}.{name}()",
            "}",
        ],
    )


def _readonly_unsigned_short_rust_vtable_init(member: Member) -> Block:
    getter = _getter_name(member.attribute)
    return [f"            {getter}: Some(document_host_{getter}::<T>),"]


def _readonly_unsigned_short_cpp_bodies(member: Member) -> tuple[Block, ...]:
    getter = _getter_name(member.attribute)
    accessor = _cpp_member_name(member.attribute)
    return (
        [
            f"void DocumentHostGet{accessor}(",
            "    const v8::FunctionCallbackInfo<v8::Value>& info) {",
            "  v8::Isolate* isolate = info.GetIsolate();",
            "  auto* state = UnwrapDocumentHostState(info);",
            f"  if (!state || !state->native || !state->vtable.{getter}) {{",
            '    ThrowTypeError(isolate, "invalid Document host state");',
            "    return;",
            "  }",
            "  uint16_t value = 0;",
            f"  if (!CallDocumentHostGet{accessor}(state, &value)) {{",
            '    ThrowTypeError(isolate, "re-entrant Document host callback");',
            "    return;",
            "  }",
            "  info.GetReturnValue().Set(v8::Integer::NewFromUnsigned(isolate, value));",
            "}",
        ],
    )


def _readonly_unsigned_short_cpp_vtable_terms(member: Member) -> list[str]:
    return [f"vtable.{_getter_name(member.attribute)}"]


# An interface-typed member hands script another DOM object, so unlike every
# shape above it needs the per-realm wrapper cache to preserve identity. That
# cache, the wrapper cell, and the Element prototype are infrastructure and
# live in bridge.cc beside the hand-written document facade, so this shape
# emits no C++ body -- only the ABI slot and the Rust side that feeds it.
def _readonly_nullable_interface_header_slots(member: Member) -> Block:
    return [
        f"  uint8_t (*{_getter_name(member.attribute)})(void* native, ServoV8InterfaceValue* output);",
    ]


def _readonly_nullable_interface_rust_trait_members(member: Member) -> Block:
    name = _rust_member_name(member.attribute)
    return [
        f"    /// `None` is JavaScript `null`; `Some` transfers one boxed host.",
        f"    fn {name}(&self) -> Option<InterfaceHandle>;",
    ]


def _readonly_nullable_interface_rust_vtable_fields(member: Member) -> Block:
    return [
        f'    pub {_getter_name(member.attribute)}: Option<unsafe extern "C" fn(*mut c_void, *mut RawInterfaceValue) -> u8>,',
    ]


def _readonly_nullable_interface_rust_thunks(member: Member) -> tuple[Block, ...]:
    name = _rust_member_name(member.attribute)
    getter = _getter_name(member.attribute)
    return (
        [
            f'unsafe extern "C" fn document_host_{getter}<T: DocumentHostBinding>(',
            "    native: *mut c_void,",
            "    output: *mut RawInterfaceValue,",
            ") -> u8 {",
            "    if output.is_null() {",
            "        return 0;",
            "    }",
            "    // SAFETY: The vtable contract requires a live Box<T> native pointer.",
            f"    let handle = unsafe {{ &*native.cast::<T>() }}.{name}();",
            "    // SAFETY: output is non-null and points to caller-owned writable storage.",
            "    unsafe {",
            "        *output = match handle {",
            "            Some(handle) => RawInterfaceValue {",
            "                is_null: 0,",
            "                key: handle.key,",
            "                native: handle.native,",
            "            },",
            "            None => RawInterfaceValue {",
            "                is_null: 1,",
            "                key: std::ptr::null(),",
            "                native: std::ptr::null_mut(),",
            "            },",
            "        };",
            "    }",
            "    1",
            "}",
        ],
    )


def _readonly_nullable_interface_rust_vtable_init(member: Member) -> Block:
    getter = _getter_name(member.attribute)
    return [f"            {getter}: Some(document_host_{getter}::<T>),"]


def _readonly_nullable_interface_cpp_bodies(member: Member) -> tuple[Block, ...]:
    getter = _getter_name(member.attribute)
    accessor = _cpp_member_name(member.attribute)
    qualified_name = member.qualified_name
    return (
        [
            f"void DocumentHostGet{accessor}(",
            "    const v8::FunctionCallbackInfo<v8::Value>& info) {",
            "  v8::Isolate* isolate = info.GetIsolate();",
            "  auto* state = UnwrapDocumentHostState(info);",
            f"  if (!state || !state->native || !state->vtable.{getter}) {{",
            '    ThrowTypeError(isolate, "invalid Document host state");',
            "    return;",
            "  }",
            "  auto* realm = static_cast<ServoV8RealmState*>(",
            "      info.This()->GetAlignedPointerFromEmbedderDataInCreationContext(",
            "          isolate, kServoRealmStateEmbedderSlot, kServoRealmStateEmbedderTag));",
            "  if (!realm || realm->runtime != state->runtime ||",
            "      !realm->runtime->element_host_installed ||",
            "      realm->element_template.IsEmpty()) {",
            '    ThrowTypeError(isolate, "Element host is not installed in this realm");',
            "    return;",
            "  }",
            "  ServoV8InterfaceValue value{};",
            "  bool succeeded = false;",
            "  {",
            "    if (state->runtime->rust_callback_depth != 0) {",
            '      ThrowTypeError(isolate, "re-entrant Document host callback");',
            "      return;",
            "    }",
            "    RustCallbackScope callback_scope(state->runtime);",
            f"    succeeded = state->vtable.{getter}(state->native, &value) != 0;",
            "  }",
            "  if (!succeeded) {",
            "    DropUnownedElementHost(state->runtime, value.native,",
            "                           state->runtime->element_host_vtable.drop);",
            f'    ThrowTypeError(isolate, "{qualified_name} host callback failed");',
            "    return;",
            "  }",
            "  const bool malformed =",
            "      value.is_null > 1 ||",
            "      (value.is_null != 0 && (value.key || value.native)) ||",
            "      (value.is_null == 0 && (!value.key || !value.native));",
            "  if (malformed) {",
            "    DropUnownedElementHost(state->runtime, value.native,",
            "                           state->runtime->element_host_vtable.drop);",
            f'    ThrowTypeError(isolate, "invalid {qualified_name} interface result");',
            "    return;",
            "  }",
            "  if (value.is_null != 0) {",
            "    info.GetReturnValue().SetNull();",
            "    return;",
            "  }",
            "  v8::Local<v8::Context> context = isolate->GetCurrentContext();",
            "  v8::Local<v8::Object> wrapper =",
            "      WrapperForInterfaceValue(realm, isolate, context, value);",
            "  if (wrapper.IsEmpty()) {",
            f'    ThrowTypeError(isolate, "{qualified_name} wrapper could not be created");',
            "    return;",
            "  }",
            "  info.GetReturnValue().Set(wrapper);",
            "}",
        ],
    )


def _readonly_nullable_interface_cpp_vtable_terms(member: Member) -> list[str]:
    return [f"vtable.{_getter_name(member.attribute)}"]


# The owned UTF-8 transfer is shared by every DOMString member: one C type, one
# Rust type, one Rust owner drop, and one C++ scope guard, emitted once.
_OWNED_UTF8_C_TYPE: Block = [
    "typedef struct ServoV8OwnedUtf8 {",
    "  const uint8_t* data;",
    "  size_t length;",
    "  void* owner;",
    "  ServoV8DropCallback drop_owner;",
    "} ServoV8OwnedUtf8;",
]

_OWNED_UTF8_RUST_TYPE: Block = [
    "#[derive(Clone, Copy)]",
    "#[repr(C)]",
    "pub struct OwnedUtf8 {",
    "    pub data: *const u8,",
    "    pub length: usize,",
    "    pub owner: *mut c_void,",
    "    pub drop_owner: Option<DropCallback>,",
    "}",
]

_OWNED_UTF8_RUST_DROP: Block = [
    'unsafe extern "C" fn document_host_owned_utf8_drop(owner: *mut c_void) {',
    "    // SAFETY: Every successful getter transfers one Box<Vec<u8>> owner.",
    "    drop(unsafe { Box::from_raw(owner.cast::<Vec<u8>>()) });",
    "}",
]

_OWNED_UTF8_CPP_SCOPE: Block = [
    "class DocumentHostOwnedUtf8Scope {",
    " public:",
    "  DocumentHostOwnedUtf8Scope(ServoV8Runtime* runtime,",
    "                                 ServoV8OwnedUtf8* value)",
    "      : runtime_(runtime), value_(value) {}",
    "  ~DocumentHostOwnedUtf8Scope() {",
    "    if (value_->owner && value_->drop_owner) {",
    "      RustCallbackScope callback_scope(runtime_);",
    "      value_->drop_owner(value_->owner);",
    "    }",
    "  }",
    "",
    " private:",
    "  ServoV8Runtime* runtime_;",
    "  ServoV8OwnedUtf8* value_;",
    "};",
]

SHAPE_EMITTERS = {
    production_webidl.READONLY_BOOLEAN: ShapeEmitter(
        header_type_blocks=(),
        header_slots=_readonly_boolean_header_slots,
        rust_type_blocks=(),
        rust_trait_members=_readonly_boolean_rust_trait_members,
        rust_vtable_fields=_readonly_boolean_rust_vtable_fields,
        rust_thunk_blocks=(),
        rust_thunks=_readonly_boolean_rust_thunks,
        rust_vtable_init=_readonly_boolean_rust_vtable_init,
        cpp_body_blocks=(),
        cpp_bodies=_readonly_boolean_cpp_bodies,
        cpp_vtable_terms=_readonly_boolean_cpp_vtable_terms,
    ),
    production_webidl.WRITABLE_LEGACY_DOMSTRING: ShapeEmitter(
        header_type_blocks=(_OWNED_UTF8_C_TYPE,),
        header_slots=_legacy_domstring_header_slots,
        rust_type_blocks=(_OWNED_UTF8_RUST_TYPE,),
        rust_trait_members=_legacy_domstring_rust_trait_members,
        rust_vtable_fields=_legacy_domstring_rust_vtable_fields,
        rust_thunk_blocks=(_OWNED_UTF8_RUST_DROP,),
        rust_thunks=_legacy_domstring_rust_thunks,
        rust_vtable_init=_legacy_domstring_rust_vtable_init,
        cpp_body_blocks=(_OWNED_UTF8_CPP_SCOPE,),
        cpp_bodies=_legacy_domstring_cpp_bodies,
        cpp_vtable_terms=_legacy_domstring_cpp_vtable_terms,
    ),
    production_webidl.READONLY_USVSTRING: ShapeEmitter(
        header_type_blocks=(_OWNED_UTF8_C_TYPE,),
        header_slots=_readonly_usvstring_header_slots,
        rust_type_blocks=(_OWNED_UTF8_RUST_TYPE,),
        rust_trait_members=_readonly_usvstring_rust_trait_members,
        rust_vtable_fields=_readonly_usvstring_rust_vtable_fields,
        rust_thunk_blocks=(_OWNED_UTF8_RUST_DROP,),
        rust_thunks=_readonly_usvstring_rust_thunks,
        rust_vtable_init=_readonly_usvstring_rust_vtable_init,
        cpp_body_blocks=(_OWNED_UTF8_CPP_SCOPE,),
        cpp_bodies=_readonly_usvstring_cpp_bodies,
        cpp_vtable_terms=_readonly_usvstring_cpp_vtable_terms,
    ),
    production_webidl.READONLY_ENUM: ShapeEmitter(
        header_type_blocks=(_OWNED_UTF8_C_TYPE,),
        header_slots=_readonly_enum_header_slots,
        rust_type_blocks=(_OWNED_UTF8_RUST_TYPE,),
        rust_trait_members=_readonly_enum_rust_trait_members,
        rust_vtable_fields=_readonly_enum_rust_vtable_fields,
        rust_thunk_blocks=(_OWNED_UTF8_RUST_DROP,),
        rust_thunks=_readonly_enum_rust_thunks,
        rust_vtable_init=_readonly_enum_rust_vtable_init,
        cpp_body_blocks=(_OWNED_UTF8_CPP_SCOPE,),
        cpp_bodies=_readonly_enum_cpp_bodies,
        cpp_vtable_terms=_readonly_enum_cpp_vtable_terms,
    ),
    production_webidl.READONLY_UNSIGNED_SHORT: ShapeEmitter(
        header_type_blocks=(),
        header_slots=_readonly_unsigned_short_header_slots,
        rust_type_blocks=(),
        rust_trait_members=_readonly_unsigned_short_rust_trait_members,
        rust_vtable_fields=_readonly_unsigned_short_rust_vtable_fields,
        rust_thunk_blocks=(),
        rust_thunks=_readonly_unsigned_short_rust_thunks,
        rust_vtable_init=_readonly_unsigned_short_rust_vtable_init,
        cpp_body_blocks=(),
        cpp_bodies=_readonly_unsigned_short_cpp_bodies,
        cpp_vtable_terms=_readonly_unsigned_short_cpp_vtable_terms,
    ),
    production_webidl.READONLY_NULLABLE_INTERFACE: ShapeEmitter(
        header_type_blocks=(),
        header_slots=_readonly_nullable_interface_header_slots,
        rust_type_blocks=(),
        rust_trait_members=_readonly_nullable_interface_rust_trait_members,
        rust_vtable_fields=_readonly_nullable_interface_rust_vtable_fields,
        rust_thunk_blocks=(),
        rust_thunks=_readonly_nullable_interface_rust_thunks,
        rust_vtable_init=_readonly_nullable_interface_rust_vtable_init,
        cpp_body_blocks=(),
        cpp_bodies=_readonly_nullable_interface_cpp_bodies,
        cpp_vtable_terms=_readonly_nullable_interface_cpp_vtable_terms,
    ),
}


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("webidls_dir", type=Path)
    parser.add_argument("out_dir", type=Path)
    arguments = parser.parse_args(argv)
    write_outputs(arguments.webidls_dir, arguments.out_dir)


if __name__ == "__main__":
    main()
