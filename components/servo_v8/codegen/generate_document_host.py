# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Generate a narrow Document host from Servo's production WebIDL corpus.

Every artifact is emitted member by member from the shapes named in
``production_webidl.DOCUMENT_HOST``: a member contributes its own declarations,
thunks, and callback bodies, and the blocks a shape needs only once are emitted
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
    attribute: WebIDL.IDLAttribute | WebIDL.IDLMethod


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


def generate_header(
    selected_members: Mapping[str, WebIDL.IDLAttribute | WebIDL.IDLMethod],
) -> str:
    """Generate the C ABI for the selected Document host members."""

    members = _members(selected_members)
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


def generate_rust(
    selected_members: Mapping[str, WebIDL.IDLAttribute | WebIDL.IDLMethod],
) -> str:
    """Generate Rust host trait and typed C ABI thunks."""

    members = _members(selected_members)
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
            "/// run and must never be retained after the callback returns.",
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


def generate_cpp(
    selected_members: Mapping[str, WebIDL.IDLAttribute | WebIDL.IDLMethod],
) -> str:
    """Generate C++ vtable validation and the V8 member callbacks."""

    members = _members(selected_members)
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
        _cpp_member_installation(members),
    ]
    return _render(blocks)


def generate_outputs(
    selected_members: Mapping[str, WebIDL.IDLAttribute | WebIDL.IDLMethod],
) -> dict[str, str]:
    """Generate every Document host artifact without writing it."""

    return {
        HEADER_NAME: generate_header(selected_members),
        RUST_NAME: generate_rust(selected_members),
        CPP_NAME: generate_cpp(selected_members),
    }


def write_outputs(webidls_dir: Path, out_dir: Path) -> None:
    """Validate every production host surface and generate the Document host."""

    with tempfile.TemporaryDirectory(prefix="servo-v8-document-host-webidl-") as cache_dir:
        selected_members = production_webidl.select_document_host_members(
            Path(cache_dir) / "document",
            webidls_dir=webidls_dir,
        )
        # Timers and console are hand-written native hosts, but their WebIDL
        # gates must still run on every build. Otherwise a production signature
        # could drift while only the dedicated Python tests notice.
        production_webidl.select_timer_host_members(
            Path(cache_dir) / "timers",
            webidls_dir=webidls_dir,
        )
        production_webidl.select_console_host_members(
            Path(cache_dir) / "console",
            webidls_dir=webidls_dir,
        )
        production_webidl.select_element_host_members(
            Path(cache_dir) / "element",
            webidls_dir=webidls_dir,
        )
        production_webidl.select_node_host_members(
            Path(cache_dir) / "node",
            webidls_dir=webidls_dir,
        )
        production_webidl.select_html_collection_interface(
            Path(cache_dir) / "html_collection",
            webidls_dir=webidls_dir,
        )
    out_dir.mkdir(parents=True, exist_ok=True)
    for filename, contents in generate_outputs(selected_members).items():
        (out_dir / filename).write_text(contents, encoding="utf-8")


def _members(
    selected_members: Mapping[str, WebIDL.IDLAttribute | WebIDL.IDLMethod],
) -> list[Member]:
    """Pair the selected members with the shapes the manifest expects."""

    expected = [member.qualified_name for member in production_webidl.DOCUMENT_HOST]
    if list(selected_members) != expected:
        raise production_webidl.WebIDLSelectionError(
            f"Document host generation expected {expected}, found {list(selected_members)}"
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
            attribute=selected_members[member.qualified_name],
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


def _rust_member_name(
    member: WebIDL.IDLAttribute | WebIDL.IDLMethod | WebIDL.IDLArgument,
) -> str:
    return generate.snake_case(member.identifier.name)


def _cpp_member_name(member: WebIDL.IDLAttribute | WebIDL.IDLMethod) -> str:
    return generate.upper_camel_case(member.identifier.name)


def _getter_name(attribute: WebIDL.IDLAttribute) -> str:
    return f"get_{_rust_member_name(attribute)}"


def _setter_name(attribute: WebIDL.IDLAttribute) -> str:
    return f"set_{_rust_member_name(attribute)}"


def _cpp_member_installation(members: Sequence[Member]) -> Block:
    """Generate all V8 function objects and prototype properties from the manifest."""

    lines = [
        "bool InstallDocumentHostMembers(",
        "    v8::Isolate* isolate,",
        "    v8::Local<v8::Context> context,",
        "    v8::Local<v8::Object> prototype) {",
    ]
    for member in members:
        local = _rust_member_name(member.attribute)
        if member.attribute.isAttr():
            lines.append(f"  v8::Local<v8::Function> {local}_getter;")
            if not member.attribute.readonly:
                lines.append(f"  v8::Local<v8::Function> {local}_setter;")
        else:
            lines.append(f"  v8::Local<v8::Function> {local}_method;")

    for member in members:
        local = _rust_member_name(member.attribute)
        callback = _cpp_member_name(member.attribute)
        if member.attribute.isAttr():
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
        else:
            _, arguments = member.attribute.signatures()[0]
            # WebIDL function length is the required prefix: optional arguments
            # (including those with a default value) do not contribute.
            arity = next(
                (index for index, argument in enumerate(arguments) if argument.optional),
                len(arguments),
            )
            side_effect_type = (
                "kHasNoSideEffect"
                if "Pure" in member.attribute._extendedAttrDict
                else "kHasSideEffect"
            )
            lines.extend(
                [
                    f"  if (!v8::Function::New(context, DocumentHostCall{callback},",
                    f"                         v8::Local<v8::Data>(), {arity},",
                    "                         v8::ConstructorBehavior::kThrow,",
                    f"                         v8::SideEffectType::{side_effect_type})",
                    f"           .ToLocal(&{local}_method)) {{",
                    "    return false;",
                    "  }",
                ]
            )

    for member in members:
        local = _rust_member_name(member.attribute)
        member_name = member.attribute.identifier.name
        if member.attribute.isAttr():
            lines.append(f'  {local}_getter->SetName(V8String(isolate, "get {member_name}"));')
            if not member.attribute.readonly:
                lines.append(f'  {local}_setter->SetName(V8String(isolate, "set {member_name}"));')
        else:
            lines.append(f'  {local}_method->SetName(V8String(isolate, "{member_name}"));')

    for member in members:
        if not member.attribute.isAttr():
            continue
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
        if member.attribute.isAttr():
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
        else:
            lines.extend(
                [
                    "  if (!prototype",
                    f'           ->DefineOwnProperty(context, V8String(isolate, "{member_name}"),',
                    f"                               {local}_method, v8::None)",
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
            f"  if (!state || !state->runtime || !state->native ||",
            f"      !state->vtable.{getter}) {{",
            '    ThrowTypeError(isolate, "invalid Document host state");',
            "    return;",
            "  }",
            "  if (state->runtime->rust_callback_depth != 0) {",
            '    ThrowTypeError(isolate, "re-entrant Document host callback");',
            "    return;",
            "  }",
            f"  bool {local} = false;",
            "  {",
            "    RustCallbackScope callback_scope(state->runtime);",
            f"    {local} = state->vtable.{getter}(state->native) != 0;",
            "  }",
            f"  info.GetReturnValue().Set(v8::Boolean::New(isolate, {local}));",
            "}",
        ],
    )


def _readonly_boolean_cpp_vtable_terms(member: Member) -> list[str]:
    return [f"vtable.{_getter_name(member.attribute)}"]


def _writable_domstring_header_slots(member: Member) -> Block:
    return [
        f"  uint8_t (*{_getter_name(member.attribute)})(void* native, ServoV8OwnedUtf8* output);",
        f"  uint8_t (*{_setter_name(member.attribute)})(void* native, void* host_context,",
        f"{C_SIGNATURE_INDENT}const uint8_t* value, size_t value_length);",
    ]


def _writable_domstring_rust_trait_members(member: Member) -> Block:
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


def _writable_domstring_rust_vtable_fields(member: Member) -> Block:
    return [
        f'    pub {_getter_name(member.attribute)}: Option<unsafe extern "C" fn(*mut c_void, *mut OwnedUtf8) -> u8>,',
        f"    pub {_setter_name(member.attribute)}: Option<",
        '        unsafe extern "C" fn(*mut c_void, *mut c_void, *const u8, usize) -> u8,',
        "    >,",
    ]


def _writable_domstring_rust_thunks(member: Member) -> tuple[Block, ...]:
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


def _writable_domstring_rust_vtable_init(member: Member) -> Block:
    getter = _getter_name(member.attribute)
    setter = _setter_name(member.attribute)
    return [
        f"            {getter}: Some(document_host_{getter}::<T>),",
        f"            {setter}: Some(document_host_{setter}::<T>),",
    ]


def _domstring_cpp_bodies(
    member: Member,
    *,
    legacy_null_to_empty: bool,
) -> tuple[Block, ...]:
    getter = _getter_name(member.attribute)
    setter = _setter_name(member.attribute)
    accessor = _cpp_member_name(member.attribute)
    qualified_name = member.qualified_name
    if legacy_null_to_empty:
        # [LegacyNullToEmptyString] replaces only null before the ordinary
        # WebIDL ToString conversion. Undefined and every other value still use
        # ToString exactly once.
        setter_conversion = [
            "  if (info[0]->IsNull()) {",
            "    value = v8::String::Empty(isolate);",
            "  } else if (!info[0]->ToString(context).ToLocal(&value)) {",
            "    return;",
            "  }",
        ]
    else:
        setter_conversion = [
            "  if (!info[0]->ToString(context).ToLocal(&value)) {",
            "    return;",
            "  }",
        ]
    return (
        [
            f"void DocumentHostGet{accessor}(",
            "    const v8::FunctionCallbackInfo<v8::Value>& info) {",
            "  v8::Isolate* isolate = info.GetIsolate();",
            "  auto* state = UnwrapDocumentHostState(info);",
            f"  if (!state || !state->runtime || !state->native ||",
            f"      !state->vtable.{getter}) {{",
            '    ThrowTypeError(isolate, "invalid Document host state");',
            "    return;",
            "  }",
            "  if (state->runtime->rust_callback_depth != 0) {",
            '    ThrowTypeError(isolate, "re-entrant Document host callback");',
            "    return;",
            "  }",
            "  ServoV8OwnedUtf8 value{};",
            "  bool succeeded = false;",
            "  {",
            "    RustCallbackScope callback_scope(state->runtime);",
            f"    succeeded = state->vtable.{getter}(state->native, &value) != 0;",
            "  }",
            "  if (!succeeded) {",
            f'    ThrowTypeError(isolate, "{qualified_name} host callback failed");',
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
            f"  if (!state || !state->runtime || !state->native ||",
            f"      !state->vtable.{setter}) {{",
            '    ThrowTypeError(isolate, "invalid Document host state");',
            "    return;",
            "  }",
            "  v8::Local<v8::Context> context = isolate->GetCurrentContext();",
            "  v8::Local<v8::String> value;",
            *setter_conversion,
            "  v8::String::Utf8Value utf8(isolate, value);",
            "  if (!*utf8) {",
            f'    ThrowTypeError(isolate, "could not encode {qualified_name} as UTF-8");',
            "    return;",
            "  }",
            "  if (!state->active_host_context ||",
            "      state->runtime->rust_callback_depth != 0) {",
            f'    ThrowTypeError(isolate, "{qualified_name} host callback failed");',
            "    return;",
            "  }",
            "  bool succeeded = false;",
            "  {",
            "    RustCallbackScope callback_scope(state->runtime);",
            f"    succeeded = state->vtable.{setter}(",
            "                    state->native, state->active_host_context,",
            "                    reinterpret_cast<const uint8_t*>(*utf8),",
            "                    static_cast<size_t>(utf8.length())) != 0;",
            "  }",
            "  if (!succeeded) {",
            f'    ThrowTypeError(isolate, "{qualified_name} host callback failed");',
            "  }",
            "}",
        ],
    )


def _legacy_domstring_cpp_bodies(member: Member) -> tuple[Block, ...]:
    return _domstring_cpp_bodies(member, legacy_null_to_empty=True)


def _writable_domstring_cpp_bodies(member: Member) -> tuple[Block, ...]:
    return _domstring_cpp_bodies(member, legacy_null_to_empty=False)


def _writable_domstring_cpp_vtable_terms(member: Member) -> list[str]:
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
            f"  if (!state || !state->runtime || !state->native ||",
            f"      !state->vtable.{getter}) {{",
            '    ThrowTypeError(isolate, "invalid Document host state");',
            "    return;",
            "  }",
            "  if (state->runtime->rust_callback_depth != 0) {",
            '    ThrowTypeError(isolate, "re-entrant Document host callback");',
            "    return;",
            "  }",
            "  ServoV8OwnedUtf8 value{};",
            "  bool succeeded = false;",
            "  {",
            "    RustCallbackScope callback_scope(state->runtime);",
            f"    succeeded = state->vtable.{getter}(state->native, &value) != 0;",
            "  }",
            "  if (!succeeded) {",
            f'    ThrowTypeError(isolate, "{qualified_name} host callback failed");',
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


# Both readonly string types use the same outbound UTF-8 transfer. Their
# semantic distinction is enforced by the WebIDL selector; unlike an argument,
# a getter result needs no JS-side surrogate conversion.
_readonly_domstring_header_slots = _readonly_usvstring_header_slots
_readonly_domstring_rust_trait_members = _readonly_usvstring_rust_trait_members
_readonly_domstring_rust_vtable_fields = _readonly_usvstring_rust_vtable_fields
_readonly_domstring_rust_thunks = _readonly_usvstring_rust_thunks
_readonly_domstring_rust_vtable_init = _readonly_usvstring_rust_vtable_init
_readonly_domstring_cpp_bodies = _readonly_usvstring_cpp_bodies
_readonly_domstring_cpp_vtable_terms = _readonly_usvstring_cpp_vtable_terms


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
            f"  if (!state || !state->runtime || !state->native ||",
            f"      !state->vtable.{getter}) {{",
            '    ThrowTypeError(isolate, "invalid Document host state");',
            "    return;",
            "  }",
            "  if (state->runtime->rust_callback_depth != 0) {",
            '    ThrowTypeError(isolate, "re-entrant Document host callback");',
            "    return;",
            "  }",
            "  uint16_t value = 0;",
            "  {",
            "    RustCallbackScope callback_scope(state->runtime);",
            f"    value = state->vtable.{getter}(state->native);",
            "  }",
            "  info.GetReturnValue().Set(v8::Integer::NewFromUnsigned(isolate, value));",
            "}",
        ],
    )


def _readonly_unsigned_short_cpp_vtable_terms(member: Member) -> list[str]:
    return [f"vtable.{_getter_name(member.attribute)}"]


# `unsigned long` has the same infallible POD contract, widened to the WebIDL
# 32-bit unsigned range on both sides of the C ABI.
def _readonly_unsigned_long_header_slots(member: Member) -> Block:
    return [f"  uint32_t (*{_getter_name(member.attribute)})(void* native);"]


def _readonly_unsigned_long_rust_trait_members(member: Member) -> Block:
    return [f"    fn {_rust_member_name(member.attribute)}(&self) -> u32;"]


def _readonly_unsigned_long_rust_vtable_fields(member: Member) -> Block:
    return [f'    pub {_getter_name(member.attribute)}: Option<unsafe extern "C" fn(*mut c_void) -> u32>,']


def _readonly_unsigned_long_rust_thunks(member: Member) -> tuple[Block, ...]:
    name = _rust_member_name(member.attribute)
    getter = _getter_name(member.attribute)
    return (
        [
            f'unsafe extern "C" fn document_host_{getter}<T: DocumentHostBinding>(',
            "    native: *mut c_void,",
            ") -> u32 {",
            "    // SAFETY: The vtable contract requires a live Box<T> native pointer.",
            f"    unsafe {{ &*native.cast::<T>() }}.{name}()",
            "}",
        ],
    )


def _readonly_unsigned_long_rust_vtable_init(member: Member) -> Block:
    getter = _getter_name(member.attribute)
    return [f"            {getter}: Some(document_host_{getter}::<T>),"]


def _readonly_unsigned_long_cpp_bodies(member: Member) -> tuple[Block, ...]:
    getter = _getter_name(member.attribute)
    accessor = _cpp_member_name(member.attribute)
    return (
        [
            f"void DocumentHostGet{accessor}(",
            "    const v8::FunctionCallbackInfo<v8::Value>& info) {",
            "  v8::Isolate* isolate = info.GetIsolate();",
            "  auto* state = UnwrapDocumentHostState(info);",
            f"  if (!state || !state->runtime || !state->native ||",
            f"      !state->vtable.{getter}) {{",
            '    ThrowTypeError(isolate, "invalid Document host state");',
            "    return;",
            "  }",
            "  if (state->runtime->rust_callback_depth != 0) {",
            '    ThrowTypeError(isolate, "re-entrant Document host callback");',
            "    return;",
            "  }",
            "  uint32_t value = 0;",
            "  {",
            "    RustCallbackScope callback_scope(state->runtime);",
            f"    value = state->vtable.{getter}(state->native);",
            "  }",
            "  info.GetReturnValue().Set(v8::Integer::NewFromUnsigned(isolate, value));",
            "}",
        ],
    )


def _readonly_unsigned_long_cpp_vtable_terms(member: Member) -> list[str]:
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


# A `[SameObject]` collection is always present, but like other interface
# results it transfers a speculative native host alongside the DOM identity
# key used by the per-realm wrapper cache.
_HTML_COLLECTION_C_TYPE: Block = [
    "typedef struct ServoV8HTMLCollectionValue {",
    "  const void* key;",
    "  void* native;",
    "} ServoV8HTMLCollectionValue;",
]

_HTML_COLLECTION_RUST_TYPE: Block = [
    "#[derive(Clone, Copy)]",
    "#[repr(C)]",
    "pub struct RawHTMLCollectionValue {",
    "    pub key: *const c_void,",
    "    pub native: *mut c_void,",
    "}",
]


def _sameobject_readonly_interface_header_slots(member: Member) -> Block:
    return [
        f"  uint8_t (*{_getter_name(member.attribute)})(void* native, ServoV8HTMLCollectionValue* output);",
    ]


def _sameobject_readonly_interface_rust_trait_members(member: Member) -> Block:
    name = _rust_member_name(member.attribute)
    return [
        "    /// Transfers one boxed live-collection host; the realm cache preserves `SameObject`.",
        f"    fn {name}(&self) -> HTMLCollectionHandle;",
    ]


def _sameobject_readonly_interface_rust_vtable_fields(member: Member) -> Block:
    return [
        f'    pub {_getter_name(member.attribute)}: Option<unsafe extern "C" fn(*mut c_void, *mut RawHTMLCollectionValue) -> u8>,',
    ]


def _sameobject_readonly_interface_rust_thunks(member: Member) -> tuple[Block, ...]:
    name = _rust_member_name(member.attribute)
    getter = _getter_name(member.attribute)
    return (
        [
            f'unsafe extern "C" fn document_host_{getter}<T: DocumentHostBinding>(',
            "    native: *mut c_void,",
            "    output: *mut RawHTMLCollectionValue,",
            ") -> u8 {",
            "    if output.is_null() {",
            "        return 0;",
            "    }",
            "    // SAFETY: The vtable contract requires a live Box<T> native pointer.",
            f"    let handle = unsafe {{ &*native.cast::<T>() }}.{name}();",
            "    // SAFETY: output is non-null and points to caller-owned writable storage.",
            "    unsafe {",
            "        *output = RawHTMLCollectionValue {",
            "            key: handle.key,",
            "            native: handle.native,",
            "        };",
            "    }",
            "    1",
            "}",
        ],
    )


def _sameobject_readonly_interface_rust_vtable_init(member: Member) -> Block:
    getter = _getter_name(member.attribute)
    return [f"            {getter}: Some(document_host_{getter}::<T>),"]


def _sameobject_readonly_interface_cpp_bodies(member: Member) -> tuple[Block, ...]:
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
            "      !realm->runtime->html_collection_host_installed ||",
            "      !realm->runtime->element_host_installed ||",
            "      realm->html_collection_template.IsEmpty() ||",
            "      realm->element_template.IsEmpty()) {",
            '    ThrowTypeError(isolate, "HTMLCollection host is not installed in this realm");',
            "    return;",
            "  }",
            "  ServoV8HTMLCollectionValue value{};",
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
            "    DropUnownedHTMLCollectionHost(state->runtime, value.native);",
            f'    ThrowTypeError(isolate, "{qualified_name} host callback failed");',
            "    return;",
            "  }",
            "  if (!value.key || !value.native) {",
            "    DropUnownedHTMLCollectionHost(state->runtime, value.native);",
            f'    ThrowTypeError(isolate, "invalid {qualified_name} interface result");',
            "    return;",
            "  }",
            "  v8::Local<v8::Context> context = isolate->GetCurrentContext();",
            "  v8::Local<v8::Object> wrapper =",
            "      WrapperForHTMLCollectionValue(realm, context, value);",
            "  if (wrapper.IsEmpty()) {",
            f'    ThrowTypeError(isolate, "{qualified_name} wrapper could not be created");',
            "    return;",
            "  }",
            "  info.GetReturnValue().Set(wrapper);",
            "}",
        ],
    )


def _sameobject_readonly_interface_cpp_vtable_terms(member: Member) -> list[str]:
    return [f"vtable.{_getter_name(member.attribute)}"]


def _domstring_to_nonnullable_interface_argument(
    member: Member,
) -> WebIDL.IDLArgument:
    _, arguments = member.attribute.signatures()[0]
    return arguments[0]


def _domstring_to_nonnullable_interface_header_slots(member: Member) -> Block:
    name = _rust_member_name(member.attribute)
    argument = _rust_member_name(
        _domstring_to_nonnullable_interface_argument(member)
    )
    return [
        f"  uint8_t (*{name})(void* native,",
        f"{C_SIGNATURE_INDENT}const uint8_t* {argument},",
        f"{C_SIGNATURE_INDENT}size_t {argument}_length,",
        f"{C_SIGNATURE_INDENT}ServoV8HTMLCollectionValue* output);",
    ]


def _domstring_to_nonnullable_interface_rust_trait_members(member: Member) -> Block:
    name = _rust_member_name(member.attribute)
    argument = _rust_member_name(
        _domstring_to_nonnullable_interface_argument(member)
    )
    return [
        "    /// Returns a live collection host for this exact string query.",
        f"    fn {name}(&self, {argument}: &str) -> HTMLCollectionHandle;",
    ]


def _domstring_to_nonnullable_interface_rust_vtable_fields(
    member: Member,
) -> Block:
    name = _rust_member_name(member.attribute)
    return [
        f"    pub {name}: Option<",
        "        unsafe extern \"C\" fn(",
        "            *mut c_void,",
        "            *const u8,",
        "            usize,",
        "            *mut RawHTMLCollectionValue,",
        "        ) -> u8,",
        "    >,",
    ]


def _domstring_to_nonnullable_interface_rust_thunks(
    member: Member,
) -> tuple[Block, ...]:
    name = _rust_member_name(member.attribute)
    argument = _rust_member_name(
        _domstring_to_nonnullable_interface_argument(member)
    )
    return (
        [
            f'unsafe extern "C" fn document_host_{name}<T: DocumentHostBinding>(',
            "    native: *mut c_void,",
            f"    {argument}: *const u8,",
            f"    {argument}_length: usize,",
            "    output: *mut RawHTMLCollectionValue,",
            ") -> u8 {",
            "    if native.is_null() || output.is_null() ||",
            f"        ({argument}.is_null() && {argument}_length != 0)",
            "    {",
            "        return 0;",
            "    }",
            f"    let {argument}_bytes = if {argument}_length == 0 {{",
            "        &[]",
            "    } else {",
            "        // SAFETY: The ABI contract lends this byte range for the callback.",
            f"        unsafe {{ std::slice::from_raw_parts({argument}, {argument}_length) }}",
            "    };",
            f"    let Ok({argument}) = std::str::from_utf8({argument}_bytes) else {{",
            "        return 0;",
            "    };",
            "    // SAFETY: The vtable contract supplies this exact live Box<T>.",
            f"    let handle = unsafe {{ &*native.cast::<T>() }}.{name}({argument});",
            "    // SAFETY: output is non-null and points to caller-owned writable storage.",
            "    unsafe {",
            "        *output = RawHTMLCollectionValue {",
            "            key: handle.key,",
            "            native: handle.native,",
            "        };",
            "    }",
            "    1",
            "}",
        ],
    )


def _domstring_to_nonnullable_interface_rust_vtable_init(member: Member) -> Block:
    name = _rust_member_name(member.attribute)
    return [f"            {name}: Some(document_host_{name}::<T>),"]


def _domstring_to_nonnullable_interface_cpp_bodies(
    member: Member,
) -> tuple[Block, ...]:
    name = _rust_member_name(member.attribute)
    callback = _cpp_member_name(member.attribute)
    argument = _rust_member_name(
        _domstring_to_nonnullable_interface_argument(member)
    )
    qualified_name = member.qualified_name
    return (
        [
            f"void DocumentHostCall{callback}(",
            "    const v8::FunctionCallbackInfo<v8::Value>& info) {",
            "  v8::Isolate* isolate = info.GetIsolate();",
            "  auto* state = UnwrapDocumentHostState(info);",
            f"  if (!state || !state->native || !state->vtable.{name}) {{",
            '    ThrowTypeError(isolate, "invalid Document host state");',
            "    return;",
            "  }",
            "  auto* realm = static_cast<ServoV8RealmState*>(",
            "      info.This()->GetAlignedPointerFromEmbedderDataInCreationContext(",
            "          isolate, kServoRealmStateEmbedderSlot, kServoRealmStateEmbedderTag));",
            "  if (!realm || realm->runtime != state->runtime ||",
            "      !realm->runtime->html_collection_host_installed ||",
            "      !realm->runtime->element_host_installed ||",
            "      realm->html_collection_template.IsEmpty() ||",
            "      realm->element_template.IsEmpty()) {",
            '    ThrowTypeError(isolate, "HTMLCollection host is not installed in this realm");',
            "    return;",
            "  }",
            "  if (info.Length() < 1) {",
            f'    ThrowTypeError(isolate, "{qualified_name} requires one argument");',
            "    return;",
            "  }",
            "  v8::Local<v8::Context> context = isolate->GetCurrentContext();",
            f"  v8::Local<v8::String> {argument}_value;",
            f"  if (!info[0]->ToString(context).ToLocal(&{argument}_value)) return;",
            f"  v8::String::Utf8Value {argument}_utf8(isolate, {argument}_value);",
            f"  if (!*{argument}_utf8 && {argument}_utf8.length() != 0) {{",
            f'    ThrowTypeError(isolate, "{qualified_name} argument conversion failed");',
            "    return;",
            "  }",
            "  ServoV8HTMLCollectionValue value{};",
            "  bool succeeded = false;",
            "  {",
            "    if (state->runtime->rust_callback_depth != 0) {",
            '      ThrowTypeError(isolate, "re-entrant Document host callback");',
            "      return;",
            "    }",
            "    RustCallbackScope callback_scope(state->runtime);",
            f"    succeeded = state->vtable.{name}(",
            "                    state->native,",
            f"                    reinterpret_cast<const uint8_t*>(*{argument}_utf8),",
            f"                    static_cast<size_t>({argument}_utf8.length()),",
            "                    &value) != 0;",
            "  }",
            "  if (!succeeded) {",
            "    DropUnownedHTMLCollectionHost(state->runtime, value.native);",
            f'    ThrowTypeError(isolate, "{qualified_name} host callback failed");',
            "    return;",
            "  }",
            "  if (!value.key || !value.native) {",
            "    DropUnownedHTMLCollectionHost(state->runtime, value.native);",
            f'    ThrowTypeError(isolate, "invalid {qualified_name} interface result");',
            "    return;",
            "  }",
            "  v8::Local<v8::Object> wrapper =",
            "      WrapperForHTMLCollectionValue(realm, context, value);",
            "  if (wrapper.IsEmpty()) {",
            f'    ThrowTypeError(isolate, "{qualified_name} wrapper could not be created");',
            "    return;",
            "  }",
            "  info.GetReturnValue().Set(wrapper);",
            "}",
        ],
    )


def _domstring_to_nonnullable_interface_cpp_vtable_terms(
    member: Member,
) -> list[str]:
    return [f"vtable.{_rust_member_name(member.attribute)}"]


# This operation has the same nullable Element result contract as the
# interface-valued attributes above, but it also converts one JavaScript value
# to DOMString and passes the embedding's ephemeral JSContext through to Servo.
def _pure_domstring_to_nullable_interface_argument(
    member: Member,
) -> WebIDL.IDLArgument:
    _, arguments = member.attribute.signatures()[0]
    return arguments[0]


def _pure_domstring_to_nullable_interface_header_slots(member: Member) -> Block:
    name = _rust_member_name(member.attribute)
    argument = _rust_member_name(
        _pure_domstring_to_nullable_interface_argument(member)
    )
    return [
        f"  uint8_t (*{name})(void* native, void* host_context,",
        f"{C_SIGNATURE_INDENT}const uint8_t* {argument},",
        f"{C_SIGNATURE_INDENT}size_t {argument}_length,",
        f"{C_SIGNATURE_INDENT}ServoV8InterfaceValue* output);",
    ]


def _pure_domstring_to_nullable_interface_rust_trait_members(member: Member) -> Block:
    name = _rust_member_name(member.attribute)
    argument = _rust_member_name(
        _pure_domstring_to_nullable_interface_argument(member)
    )
    return [
        "    /// `host_context` is borrowed for this call; `None` is JavaScript `null`.",
        f"    unsafe fn {name}(",
        "        &self,",
        "        host_context: *mut c_void,",
        f"        {argument}: &str,",
        "    ) -> Option<InterfaceHandle>;",
    ]


def _pure_domstring_to_nullable_interface_rust_vtable_fields(member: Member) -> Block:
    name = _rust_member_name(member.attribute)
    return [
        f"    pub {name}: Option<",
        "        unsafe extern \"C\" fn(",
        "            *mut c_void,",
        "            *mut c_void,",
        "            *const u8,",
        "            usize,",
        "            *mut RawInterfaceValue,",
        "        ) -> u8,",
        "    >,",
    ]


def _pure_domstring_to_nullable_interface_rust_thunks(
    member: Member,
) -> tuple[Block, ...]:
    name = _rust_member_name(member.attribute)
    argument = _rust_member_name(
        _pure_domstring_to_nullable_interface_argument(member)
    )
    return (
        [
            f'unsafe extern "C" fn document_host_{name}<T: DocumentHostBinding>(',
            "    native: *mut c_void,",
            "    host_context: *mut c_void,",
            f"    {argument}: *const u8,",
            f"    {argument}_length: usize,",
            "    output: *mut RawInterfaceValue,",
            ") -> u8 {",
            "    if native.is_null() || host_context.is_null() || output.is_null() ||",
            f"        ({argument}.is_null() && {argument}_length != 0)",
            "    {",
            "        return 0;",
            "    }",
            f"    let {argument}_bytes = if {argument}_length == 0 {{",
            "        &[]",
            "    } else {",
            "        // SAFETY: The ABI contract lends this byte range for the callback.",
            f"        unsafe {{ std::slice::from_raw_parts({argument}, {argument}_length) }}",
            "    };",
            f"    let Ok({argument}) = std::str::from_utf8({argument}_bytes) else {{",
            "        return 0;",
            "    };",
            "    // SAFETY: The vtable contract supplies a live Box<T> and lends the",
            "    // non-null host context only for this callback.",
            "    let handle = unsafe {",
            f"        (&*native.cast::<T>()).{name}(host_context, {argument})",
            "    };",
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


def _pure_domstring_to_nullable_interface_rust_vtable_init(member: Member) -> Block:
    name = _rust_member_name(member.attribute)
    return [f"            {name}: Some(document_host_{name}::<T>),"]


def _pure_domstring_to_nullable_interface_cpp_bodies(
    member: Member,
) -> tuple[Block, ...]:
    name = _rust_member_name(member.attribute)
    callback = _cpp_member_name(member.attribute)
    argument = _rust_member_name(
        _pure_domstring_to_nullable_interface_argument(member)
    )
    qualified_name = member.qualified_name
    return (
        [
            f"void DocumentHostCall{callback}(",
            "    const v8::FunctionCallbackInfo<v8::Value>& info) {",
            "  v8::Isolate* isolate = info.GetIsolate();",
            "  auto* state = UnwrapDocumentHostState(info);",
            f"  if (!state || !state->native || !state->vtable.{name}) {{",
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
            "  if (info.Length() < 1) {",
            f'    ThrowTypeError(isolate, "{qualified_name} requires one argument");',
            "    return;",
            "  }",
            "  if (!state->active_host_context) {",
            '    ThrowTypeError(isolate, "Document mutation requires a live host context");',
            "    return;",
            "  }",
            "  v8::Local<v8::Context> context = isolate->GetCurrentContext();",
            f"  v8::Local<v8::String> {argument}_value;",
            f"  if (!info[0]->ToString(context).ToLocal(&{argument}_value)) {{",
            "    return;",
            "  }",
            f"  v8::String::Utf8Value {argument}_utf8(isolate, {argument}_value);",
            f"  if (!*{argument}_utf8) {{",
            f'    ThrowTypeError(isolate, "{qualified_name} argument conversion failed");',
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
            f"    succeeded = state->vtable.{name}(",
            "                    state->native, state->active_host_context,",
            f"                    reinterpret_cast<const uint8_t*>(*{argument}_utf8),",
            f"                    static_cast<size_t>({argument}_utf8.length()),",
            "                    &value) != 0;",
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


def _pure_domstring_to_nullable_interface_cpp_vtable_terms(
    member: Member,
) -> list[str]:
    return [f"vtable.{_rust_member_name(member.attribute)}"]


# A `[Throws]` selector cannot reuse the ordinary nullable-interface outcome:
# JavaScript null and a DOMException are distinct results. The status is POD
# and the only specified Servo failure for scope-match is SyntaxError, so no
# SpiderMonkey exception object or pending-exception state crosses the ABI.
_SELECTOR_OUTCOME_C_TYPES: Block = [
    "enum ServoV8SelectorStatus {",
    "  SERVO_V8_SELECTOR_RETURNED = 0,",
    "  SERVO_V8_SELECTOR_SYNTAX_ERROR = 1,",
    "  SERVO_V8_SELECTOR_HOST_FAILURE = 2,",
    "};",
    "",
    "typedef struct ServoV8SelectorElementOutcome {",
    "  uint32_t status;",
    "  ServoV8InterfaceValue value;",
    "} ServoV8SelectorElementOutcome;",
    "",
    "typedef struct ServoV8SelectorBooleanOutcome {",
    "  uint32_t status;",
    "  uint8_t value;",
    "} ServoV8SelectorBooleanOutcome;",
    "",
    "typedef struct ServoV8SelectorNodeListOutcome {",
    "  uint32_t status;",
    "  void* native;",
    "} ServoV8SelectorNodeListOutcome;",
]

_SELECTOR_OUTCOME_RUST_TYPES: Block = [
    "const SELECTOR_RETURNED: u32 = 0;",
    "const SELECTOR_SYNTAX_ERROR: u32 = 1;",
    "const SELECTOR_HOST_FAILURE: u32 = 2;",
    "",
    "#[repr(C)]",
    "pub struct RawSelectorElementOutcome {",
    "    pub status: u32,",
    "    pub value: RawInterfaceValue,",
    "}",
    "",
    "#[repr(C)]",
    "pub struct RawSelectorBooleanOutcome {",
    "    pub status: u32,",
    "    pub value: u8,",
    "}",
    "",
    "#[repr(C)]",
    "pub struct RawSelectorNodeListOutcome {",
    "    pub status: u32,",
    "    pub native: *mut c_void,",
    "}",
]


def _pure_throws_domstring_to_nullable_interface_header_slots(
    member: Member,
) -> Block:
    name = _rust_member_name(member.attribute)
    argument = _rust_member_name(
        _pure_domstring_to_nullable_interface_argument(member)
    )
    return [
        f"  uint8_t (*{name})(void* native, void* host_context,",
        f"{C_SIGNATURE_INDENT}const uint8_t* {argument},",
        f"{C_SIGNATURE_INDENT}size_t {argument}_length,",
        f"{C_SIGNATURE_INDENT}ServoV8SelectorElementOutcome* output);",
    ]


def _pure_throws_domstring_to_nullable_interface_rust_trait_members(
    member: Member,
) -> Block:
    name = _rust_member_name(member.attribute)
    argument = _rust_member_name(
        _pure_domstring_to_nullable_interface_argument(member)
    )
    return [
        "    /// Returns a match, Servo's selector SyntaxError, or an internal failure.",
        "    /// `host_context` is borrowed only for this synchronous call.",
        f"    unsafe fn {name}(",
        "        &self,",
        "        host_context: *mut c_void,",
        f"        {argument}: &str,",
        "    ) -> SelectorElementResult;",
    ]


def _pure_throws_domstring_to_nullable_interface_rust_vtable_fields(
    member: Member,
) -> Block:
    name = _rust_member_name(member.attribute)
    return [
        f"    pub {name}: Option<",
        "        unsafe extern \"C\" fn(",
        "            *mut c_void,",
        "            *mut c_void,",
        "            *const u8,",
        "            usize,",
        "            *mut RawSelectorElementOutcome,",
        "        ) -> u8,",
        "    >,",
    ]


def _pure_throws_domstring_to_nullable_interface_rust_thunks(
    member: Member,
) -> tuple[Block, ...]:
    name = _rust_member_name(member.attribute)
    argument = _rust_member_name(
        _pure_domstring_to_nullable_interface_argument(member)
    )
    return (
        [
            f'unsafe extern "C" fn document_host_{name}<T: DocumentHostBinding>(',
            "    native: *mut c_void,",
            "    host_context: *mut c_void,",
            f"    {argument}: *const u8,",
            f"    {argument}_length: usize,",
            "    output: *mut RawSelectorElementOutcome,",
            ") -> u8 {",
            "    if native.is_null() || host_context.is_null() || output.is_null() ||",
            f"        ({argument}.is_null() && {argument}_length != 0)",
            "    {",
            "        return 0;",
            "    }",
            f"    let {argument}_bytes = if {argument}_length == 0 {{",
            "        &[]",
            "    } else {",
            "        // SAFETY: The ABI contract lends this byte range for the callback.",
            f"        unsafe {{ std::slice::from_raw_parts({argument}, {argument}_length) }}",
            "    };",
            f"    let Ok({argument}) = std::str::from_utf8({argument}_bytes) else {{",
            "        return 0;",
            "    };",
            "    // SAFETY: The vtable contract supplies a live Box<T> and lends the",
            "    // non-null host context only for this callback.",
            "    let result = unsafe {",
            f"        (&*native.cast::<T>()).{name}(host_context, {argument})",
            "    };",
            "    // SAFETY: output is non-null and points to caller-owned writable storage.",
            "    unsafe { *output = raw_selector_element_outcome(result) };",
            "    1",
            "}",
        ],
    )


def _pure_throws_domstring_to_nullable_interface_rust_vtable_init(
    member: Member,
) -> Block:
    name = _rust_member_name(member.attribute)
    return [f"            {name}: Some(document_host_{name}::<T>),"]


def _pure_throws_domstring_to_nullable_interface_cpp_bodies(
    member: Member,
) -> tuple[Block, ...]:
    name = _rust_member_name(member.attribute)
    callback = _cpp_member_name(member.attribute)
    argument = _rust_member_name(
        _pure_domstring_to_nullable_interface_argument(member)
    )
    qualified_name = member.qualified_name
    return (
        [
            f"void DocumentHostCall{callback}(",
            "    const v8::FunctionCallbackInfo<v8::Value>& info) {",
            "  v8::Isolate* isolate = info.GetIsolate();",
            "  auto* state = UnwrapDocumentHostState(info);",
            f"  if (!state || !state->native || !state->vtable.{name}) {{",
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
            "  if (info.Length() < 1) {",
            f'    ThrowTypeError(isolate, "{qualified_name} requires one argument");',
            "    return;",
            "  }",
            "  if (!state->active_host_context) {",
            '    ThrowTypeError(isolate, "Document mutation requires a live host context");',
            "    return;",
            "  }",
            "  v8::Local<v8::Context> context = isolate->GetCurrentContext();",
            f"  v8::Local<v8::String> {argument}_value;",
            f"  if (!info[0]->ToString(context).ToLocal(&{argument}_value)) {{",
            "    return;",
            "  }",
            f"  v8::String::Utf8Value {argument}_utf8(isolate, {argument}_value);",
            f"  if (!*{argument}_utf8 && {argument}_utf8.length() != 0) {{",
            f'    ThrowTypeError(isolate, "{qualified_name} argument conversion failed");',
            "    return;",
            "  }",
            "  ServoV8SelectorElementOutcome outcome{};",
            "  bool succeeded = false;",
            "  {",
            "    if (state->runtime->rust_callback_depth != 0) {",
            '      ThrowTypeError(isolate, "re-entrant Document host callback");',
            "      return;",
            "    }",
            "    RustCallbackScope callback_scope(state->runtime);",
            f"    succeeded = state->vtable.{name}(",
            "                    state->native, state->active_host_context,",
            f"                    reinterpret_cast<const uint8_t*>(*{argument}_utf8),",
            f"                    static_cast<size_t>({argument}_utf8.length()),",
            "                    &outcome) != 0;",
            "  }",
            "  if (!succeeded) {",
            "    DropUnownedElementHost(state->runtime, outcome.value.native,",
            "                           state->runtime->element_host_vtable.drop);",
            f'    ThrowTypeError(isolate, "{qualified_name} host callback failed");',
            "    return;",
            "  }",
            f'  ReturnSelectorElementOutcome(realm, context, outcome, "{qualified_name}",',
            "                             info.GetReturnValue());",
            "}",
        ],
    )


def _pure_throws_domstring_to_nullable_interface_cpp_vtable_terms(
    member: Member,
) -> list[str]:
    return [f"vtable.{_rust_member_name(member.attribute)}"]


def _newobject_throws_domstring_to_interface_header_slots(member: Member) -> Block:
    name = _rust_member_name(member.attribute)
    argument = _rust_member_name(
        _pure_domstring_to_nullable_interface_argument(member)
    )
    return [
        f"  uint8_t (*{name})(void* native, void* host_context,",
        f"{C_SIGNATURE_INDENT}const uint8_t* {argument},",
        f"{C_SIGNATURE_INDENT}size_t {argument}_length,",
        f"{C_SIGNATURE_INDENT}ServoV8SelectorNodeListOutcome* output);",
    ]


def _newobject_throws_domstring_to_interface_rust_trait_members(
    member: Member,
) -> Block:
    name = _rust_member_name(member.attribute)
    argument = _rust_member_name(
        _pure_domstring_to_nullable_interface_argument(member)
    )
    return [
        "    /// Returns a new static NodeList, a selector SyntaxError, or a host failure.",
        "    /// `host_context` is borrowed only for this synchronous call.",
        f"    unsafe fn {name}(",
        "        &self,",
        "        host_context: *mut c_void,",
        f"        {argument}: &str,",
        "    ) -> SelectorNodeListResult;",
    ]


def _newobject_throws_domstring_to_interface_rust_vtable_fields(
    member: Member,
) -> Block:
    name = _rust_member_name(member.attribute)
    return [
        f"    pub {name}: Option<",
        "        unsafe extern \"C\" fn(",
        "            *mut c_void,",
        "            *mut c_void,",
        "            *const u8,",
        "            usize,",
        "            *mut RawSelectorNodeListOutcome,",
        "        ) -> u8,",
        "    >,",
    ]


def _newobject_throws_domstring_to_interface_rust_thunks(
    member: Member,
) -> tuple[Block, ...]:
    name = _rust_member_name(member.attribute)
    argument = _rust_member_name(
        _pure_domstring_to_nullable_interface_argument(member)
    )
    return (
        [
            f'unsafe extern "C" fn document_host_{name}<T: DocumentHostBinding>(',
            "    native: *mut c_void,",
            "    host_context: *mut c_void,",
            f"    {argument}: *const u8,",
            f"    {argument}_length: usize,",
            "    output: *mut RawSelectorNodeListOutcome,",
            ") -> u8 {",
            "    if native.is_null() || host_context.is_null() || output.is_null() ||",
            f"        ({argument}.is_null() && {argument}_length != 0)",
            "    {",
            "        return 0;",
            "    }",
            f"    let {argument}_bytes = if {argument}_length == 0 {{",
            "        &[]",
            "    } else {",
            "        // SAFETY: The ABI contract lends this byte range for the callback.",
            f"        unsafe {{ std::slice::from_raw_parts({argument}, {argument}_length) }}",
            "    };",
            f"    let Ok({argument}) = std::str::from_utf8({argument}_bytes) else {{",
            "        return 0;",
            "    };",
            "    // SAFETY: The vtable contract supplies a live Box<T> and lends the",
            "    // non-null host context only for this callback.",
            "    let result = unsafe {",
            f"        (&*native.cast::<T>()).{name}(host_context, {argument})",
            "    };",
            "    // SAFETY: output is non-null and points to caller-owned writable storage.",
            "    unsafe { *output = raw_selector_node_list_outcome(result) };",
            "    1",
            "}",
        ],
    )


def _newobject_throws_domstring_to_interface_rust_vtable_init(
    member: Member,
) -> Block:
    name = _rust_member_name(member.attribute)
    return [f"            {name}: Some(document_host_{name}::<T>),"]


def _newobject_throws_domstring_to_interface_cpp_bodies(
    member: Member,
) -> tuple[Block, ...]:
    name = _rust_member_name(member.attribute)
    callback = _cpp_member_name(member.attribute)
    argument = _rust_member_name(
        _pure_domstring_to_nullable_interface_argument(member)
    )
    qualified_name = member.qualified_name
    return (
        [
            f"void DocumentHostCall{callback}(",
            "    const v8::FunctionCallbackInfo<v8::Value>& info) {",
            "  v8::Isolate* isolate = info.GetIsolate();",
            "  auto* state = UnwrapDocumentHostState(info);",
            f"  if (!state || !state->native || !state->active_host_context ||",
            f"      !state->vtable.{name}) {{",
            '    ThrowTypeError(isolate, "invalid Document host state");',
            "    return;",
            "  }",
            "  auto* realm = static_cast<ServoV8RealmState*>(",
            "      info.This()->GetAlignedPointerFromEmbedderDataInCreationContext(",
            "          isolate, kServoRealmStateEmbedderSlot, kServoRealmStateEmbedderTag));",
            "  if (!realm || realm->runtime != state->runtime ||",
            "      !realm->runtime->node_list_host_installed ||",
            "      !realm->runtime->element_host_installed ||",
            "      realm->node_list_template.IsEmpty() ||",
            "      realm->element_template.IsEmpty()) {",
            '    ThrowTypeError(isolate, "NodeList host is not installed in this realm");',
            "    return;",
            "  }",
            "  if (info.Length() < 1) {",
            f'    ThrowTypeError(isolate, "{qualified_name} requires one argument");',
            "    return;",
            "  }",
            "  v8::Local<v8::Context> context = isolate->GetCurrentContext();",
            f"  v8::Local<v8::String> {argument}_value;",
            f"  if (!info[0]->ToString(context).ToLocal(&{argument}_value)) return;",
            f"  v8::String::Utf8Value {argument}_utf8(isolate, {argument}_value);",
            f"  if (!*{argument}_utf8 && {argument}_utf8.length() != 0) return;",
            "  ServoV8SelectorNodeListOutcome outcome{};",
            "  bool succeeded = false;",
            "  {",
            "    if (state->runtime->rust_callback_depth != 0) {",
            '      ThrowTypeError(isolate, "re-entrant Document host callback");',
            "      return;",
            "    }",
            "    RustCallbackScope callback_scope(state->runtime);",
            f"    succeeded = state->vtable.{name}(",
            "                    state->native, state->active_host_context,",
            f"                    reinterpret_cast<const uint8_t*>(*{argument}_utf8),",
            f"                    static_cast<size_t>({argument}_utf8.length()),",
            "                    &outcome) != 0;",
            "  }",
            "  if (!succeeded) {",
            "    DropUnownedNodeListHost(state->runtime, outcome.native);",
            f'    ThrowTypeError(isolate, "{qualified_name} host callback failed");',
            "    return;",
            "  }",
            f'  ReturnSelectorNodeListOutcome(realm, context, outcome, "{qualified_name}",',
            "                            info.GetReturnValue());",
            "}",
        ],
    )


def _newobject_throws_domstring_to_interface_cpp_vtable_terms(
    member: Member,
) -> list[str]:
    return [f"vtable.{_rust_member_name(member.attribute)}"]


_CREATE_ELEMENT_C_TYPES: Block = [
    "enum ServoV8DocumentCreateElementStatus {",
    "  SERVO_V8_DOCUMENT_CREATE_ELEMENT_CREATED = 0,",
    "  SERVO_V8_DOCUMENT_CREATE_ELEMENT_INVALID_CHARACTER = 1,",
    "  SERVO_V8_DOCUMENT_CREATE_ELEMENT_HOST_FAILURE = 2,",
    "};",
    "",
    "typedef struct ServoV8DocumentCreateElementOutcome {",
    "  uint32_t status;",
    "  ServoV8OwnedUtf8 exception_message;",
    "  ServoV8InterfaceValue value;",
    "} ServoV8DocumentCreateElementOutcome;",
]

_CREATE_ELEMENT_RUST_TYPES: Block = [
    "const DOCUMENT_CREATE_ELEMENT_CREATED: u32 = 0;",
    "const DOCUMENT_CREATE_ELEMENT_INVALID_CHARACTER: u32 = 1;",
    "const DOCUMENT_CREATE_ELEMENT_HOST_FAILURE: u32 = 2;",
    "",
    "/// Structured native result for the narrow experimental createElement ABI.",
    "pub enum DocumentCreateElementResult {",
    "    Created(InterfaceHandle),",
    "    InvalidCharacter(String),",
    "    HostFailure,",
    "}",
    "",
    "#[repr(C)]",
    "pub struct RawDocumentCreateElementOutcome {",
    "    pub status: u32,",
    "    pub exception_message: OwnedUtf8,",
    "    pub value: RawInterfaceValue,",
    "}",
    "",
    "fn raw_document_create_element_outcome(",
    "    result: DocumentCreateElementResult,",
    ") -> RawDocumentCreateElementOutcome {",
    "    match result {",
    "        DocumentCreateElementResult::Created(handle) => RawDocumentCreateElementOutcome {",
    "            status: DOCUMENT_CREATE_ELEMENT_CREATED,",
    "            exception_message: raw_empty_owned_utf8(),",
    "            value: RawInterfaceValue {",
    "                is_null: 0,",
    "                key: handle.key,",
    "                native: handle.native,",
    "            },",
    "        },",
    "        DocumentCreateElementResult::InvalidCharacter(message) => {",
    "            RawDocumentCreateElementOutcome {",
    "                status: DOCUMENT_CREATE_ELEMENT_INVALID_CHARACTER,",
    "                exception_message: raw_owned_utf8(message),",
    "                value: raw_null_interface_value(),",
    "            }",
    "        },",
    "        DocumentCreateElementResult::HostFailure => RawDocumentCreateElementOutcome {",
    "            status: DOCUMENT_CREATE_ELEMENT_HOST_FAILURE,",
    "            exception_message: raw_empty_owned_utf8(),",
    "            value: raw_null_interface_value(),",
    "        },",
    "    }",
    "}",
]


def _create_element_header_slots(member: Member) -> Block:
    name = _rust_member_name(member.attribute)
    return [
        f"  uint8_t (*{name})(void* native, void* host_context,",
        f"{C_SIGNATURE_INDENT}const uint8_t* local_name,",
        f"{C_SIGNATURE_INDENT}size_t local_name_length,",
        f"{C_SIGNATURE_INDENT}uint8_t is_is_null,",
        f"{C_SIGNATURE_INDENT}const uint8_t* is,",
        f"{C_SIGNATURE_INDENT}size_t is_length,",
        f"{C_SIGNATURE_INDENT}ServoV8DocumentCreateElementOutcome* output);",
    ]


def _create_element_rust_trait_members(member: Member) -> Block:
    name = _rust_member_name(member.attribute)
    return [
        "    /// Creates an Element after the V8 callback has completed WebIDL conversion.",
        "    /// `is` is the dictionary member after the exact union/dictionary conversion.",
        f"    unsafe fn {name}(",
        "        &self,",
        "        host_context: *mut c_void,",
        "        local_name: &str,",
        "        is: Option<&str>,",
        "    ) -> DocumentCreateElementResult;",
    ]


def _create_element_rust_vtable_fields(member: Member) -> Block:
    name = _rust_member_name(member.attribute)
    return [
        f"    pub {name}: Option<",
        "        unsafe extern \"C\" fn(",
        "            *mut c_void,",
        "            *mut c_void,",
        "            *const u8,",
        "            usize,",
        "            u8,",
        "            *const u8,",
        "            usize,",
        "            *mut RawDocumentCreateElementOutcome,",
        "        ) -> u8,",
        "    >,",
    ]


def _create_element_rust_thunks(member: Member) -> tuple[Block, ...]:
    name = _rust_member_name(member.attribute)
    return (
        [
            f'unsafe extern "C" fn document_host_{name}<T: DocumentHostBinding>(',
            "    native: *mut c_void,",
            "    host_context: *mut c_void,",
            "    local_name: *const u8,",
            "    local_name_length: usize,",
            "    is_is_null: u8,",
            "    is: *const u8,",
            "    is_length: usize,",
            "    output: *mut RawDocumentCreateElementOutcome,",
            ") -> u8 {",
            "    if native.is_null() || host_context.is_null() || output.is_null() ||",
            "        (local_name.is_null() && local_name_length != 0) ||",
            "        is_is_null > 1 ||",
            "        (is_is_null == 1 && (!is.is_null() || is_length != 0)) ||",
            "        (is_is_null == 0 && is.is_null() && is_length != 0)",
            "    {",
            "        return 0;",
            "    }",
            "    let local_name_bytes = if local_name_length == 0 {",
            "        &[]",
            "    } else {",
            "        // SAFETY: The ABI contract lends this byte range for the callback.",
            "        unsafe { std::slice::from_raw_parts(local_name, local_name_length) }",
            "    };",
            "    let Ok(local_name) = std::str::from_utf8(local_name_bytes) else {",
            "        return 0;",
            "    };",
            "    let is = if is_is_null == 1 {",
            "        None",
            "    } else {",
            "        let is_bytes = if is_length == 0 {",
            "            &[]",
            "        } else {",
            "            // SAFETY: The ABI contract lends this byte range for the callback.",
            "            unsafe { std::slice::from_raw_parts(is, is_length) }",
            "        };",
            "        let Ok(is) = std::str::from_utf8(is_bytes) else {",
            "            return 0;",
            "        };",
            "        Some(is)",
            "    };",
            "    // SAFETY: The vtable contract supplies a live Box<T> and lends the",
            "    // non-null host context only for this callback.",
            "    let result = unsafe {",
            f"        (&*native.cast::<T>()).{name}(host_context, local_name, is)",
            "    };",
            "    // SAFETY: output is non-null and points to caller-owned writable storage.",
            "    unsafe { *output = raw_document_create_element_outcome(result) };",
            "    1",
            "}",
        ],
    )


def _create_element_rust_vtable_init(member: Member) -> Block:
    name = _rust_member_name(member.attribute)
    return [f"            {name}: Some(document_host_{name}::<T>),"]


def _create_element_cpp_bodies(member: Member) -> tuple[Block, ...]:
    name = _rust_member_name(member.attribute)
    callback = _cpp_member_name(member.attribute)
    qualified_name = member.qualified_name
    return (
        [
            f"void DocumentHostCall{callback}(",
            "    const v8::FunctionCallbackInfo<v8::Value>& info) {",
            "  v8::Isolate* isolate = info.GetIsolate();",
            "  auto* state = UnwrapDocumentHostState(info);",
            f"  if (!state || !state->native || !state->vtable.{name}) {{",
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
            "  if (info.Length() < 1) {",
            f'    ThrowTypeError(isolate, "{qualified_name} requires one argument");',
            "    return;",
            "  }",
            "  if (!state->active_host_context) {",
            '    ThrowTypeError(isolate, "Document mutation requires a live host context");',
            "    return;",
            "  }",
            "  v8::Local<v8::Context> context = isolate->GetCurrentContext();",
            "  v8::Local<v8::String> local_name_value;",
            "  if (!info[0]->ToString(context).ToLocal(&local_name_value)) return;",
            "  bool is_is_null = true;",
            "  v8::Local<v8::String> is_value;",
            "  if (info.Length() >= 2 && !info[1]->IsUndefined()) {",
            "    if (info[1]->IsNull()) {",
            "      // The dictionary arm accepts null and has no present `is` member.",
            "    } else if (info[1]->IsObject()) {",
            "      // Dictionary conversion reads inherited/proxy `is` exactly once.",
            "      v8::Local<v8::Value> is_member;",
            "      if (!info[1].As<v8::Object>()",
            "               ->Get(context, V8String(isolate, \"is\"))",
            "               .ToLocal(&is_member)) return;",
            "      if (!is_member->IsUndefined()) {",
            "        if (!is_member->ToString(context).ToLocal(&is_value)) return;",
            "        is_is_null = false;",
            "      }",
            "    } else {",
            "      // The DOMString arm is selected for every non-object value;",
            "      // its converted value is intentionally discarded by Document.createElement.",
            "      v8::Local<v8::String> discarded_options_string;",
            "      if (!info[1]->ToString(context).ToLocal(&discarded_options_string)) return;",
            "    }",
            "  }",
            "  // Utf8Value uses V8's replacement mode for orphan surrogates, matching",
            "  // Servo's current DOMString conversion through String::from_utf16_lossy.",
            "  v8::String::Utf8Value local_name_utf8(isolate, local_name_value);",
            "  if (!*local_name_utf8 && local_name_utf8.length() != 0) return;",
            "  v8::String::Utf8Value is_utf8(isolate,",
            "      is_is_null ? v8::String::Empty(isolate) : is_value);",
            "  if (!*is_utf8 && is_utf8.length() != 0) return;",
            "  ServoV8DocumentCreateElementOutcome outcome{};",
            "  bool succeeded = false;",
            "  {",
            "    if (state->runtime->rust_callback_depth != 0) {",
            '      ThrowTypeError(isolate, "re-entrant Document host callback");',
            "      return;",
            "    }",
            "    RustCallbackScope callback_scope(state->runtime);",
            f"    succeeded = state->vtable.{name}(",
            "        state->native, state->active_host_context,",
            "        reinterpret_cast<const uint8_t*>(*local_name_utf8),",
            "        static_cast<size_t>(local_name_utf8.length()), is_is_null ? 1 : 0,",
            "        is_is_null ? nullptr : reinterpret_cast<const uint8_t*>(*is_utf8),",
            "        is_is_null ? 0 : static_cast<size_t>(is_utf8.length()), &outcome) != 0;",
            "  }",
            "  DocumentHostOwnedUtf8Scope message_scope(state->runtime,",
            "                                            &outcome.exception_message);",
            "  auto fail = [&](const char* message) {",
            "    DropUnownedElementHost(state->runtime, outcome.value.native,",
            "                           state->runtime->element_host_vtable.drop);",
            "    outcome.value.native = nullptr;",
            "    ThrowTypeError(isolate, message);",
            "  };",
            "  if (!succeeded) {",
            f'    fail("{qualified_name} host callback failed");',
            "    return;",
            "  }",
            "  const bool valid_created =",
            "      outcome.value.is_null == 0 && outcome.value.key && outcome.value.native &&",
            "      outcome.status == SERVO_V8_DOCUMENT_CREATE_ELEMENT_CREATED &&",
            "      IsCanonicalEmptyOwnedUtf8(outcome.exception_message);",
            "  const bool canonical_null_value =",
            "      outcome.value.is_null == 1 && !outcome.value.key && !outcome.value.native;",
            "  const bool valid_invalid_character =",
            "      outcome.status == SERVO_V8_DOCUMENT_CREATE_ELEMENT_INVALID_CHARACTER &&",
            "      canonical_null_value && outcome.exception_message.data &&",
            "      outcome.exception_message.owner && outcome.exception_message.drop_owner &&",
            "      outcome.exception_message.length <=",
            "          static_cast<size_t>(std::numeric_limits<int>::max()) &&",
            "      IsValidUtf8(outcome.exception_message.data,",
            "                  outcome.exception_message.length);",
            "  const bool valid_host_failure =",
            "      outcome.status == SERVO_V8_DOCUMENT_CREATE_ELEMENT_HOST_FAILURE &&",
            "      canonical_null_value && IsCanonicalEmptyOwnedUtf8(outcome.exception_message);",
            "  if (!valid_created && !valid_invalid_character && !valid_host_failure) {",
            f'    fail("invalid {qualified_name} outcome");',
            "    return;",
            "  }",
            "  if (valid_host_failure) {",
            f'    fail("{qualified_name} host callback failed");',
            "    return;",
            "  }",
            "  if (valid_invalid_character) {",
            "    v8::Local<v8::String> message;",
            "    if (outcome.exception_message.length == 0) {",
            "      message = v8::String::Empty(isolate);",
            "    } else if (!v8::String::NewFromUtf8(",
            "                    isolate,",
            "                    reinterpret_cast<const char*>(outcome.exception_message.data),",
            "                    v8::NewStringType::kNormal,",
            "                    static_cast<int>(outcome.exception_message.length))",
            "                    .ToLocal(&message)) {",
            "      return;",
            "    }",
            "    ThrowDomException(realm, context, message,",
            "                      V8String(isolate, \"InvalidCharacterError\"));",
            "    return;",
            "  }",
            "  v8::Local<v8::Object> wrapper =",
            "      WrapperForInterfaceValue(realm, isolate, context, outcome.value);",
            "  outcome.value.native = nullptr;",
            "  if (wrapper.IsEmpty()) {",
            f'    ThrowTypeError(isolate, "{qualified_name} wrapper could not be created");',
            "    return;",
            "  }",
            "  info.GetReturnValue().Set(wrapper);",
            "}",
        ],
    )


def _create_element_cpp_vtable_terms(member: Member) -> list[str]:
    return [f"vtable.{_rust_member_name(member.attribute)}"]


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
        header_slots=_writable_domstring_header_slots,
        rust_type_blocks=(_OWNED_UTF8_RUST_TYPE,),
        rust_trait_members=_writable_domstring_rust_trait_members,
        rust_vtable_fields=_writable_domstring_rust_vtable_fields,
        rust_thunk_blocks=(_OWNED_UTF8_RUST_DROP,),
        rust_thunks=_writable_domstring_rust_thunks,
        rust_vtable_init=_writable_domstring_rust_vtable_init,
        cpp_body_blocks=(_OWNED_UTF8_CPP_SCOPE,),
        cpp_bodies=_legacy_domstring_cpp_bodies,
        cpp_vtable_terms=_writable_domstring_cpp_vtable_terms,
    ),
    production_webidl.WRITABLE_DOMSTRING: ShapeEmitter(
        header_type_blocks=(_OWNED_UTF8_C_TYPE,),
        header_slots=_writable_domstring_header_slots,
        rust_type_blocks=(_OWNED_UTF8_RUST_TYPE,),
        rust_trait_members=_writable_domstring_rust_trait_members,
        rust_vtable_fields=_writable_domstring_rust_vtable_fields,
        rust_thunk_blocks=(_OWNED_UTF8_RUST_DROP,),
        rust_thunks=_writable_domstring_rust_thunks,
        rust_vtable_init=_writable_domstring_rust_vtable_init,
        cpp_body_blocks=(_OWNED_UTF8_CPP_SCOPE,),
        cpp_bodies=_writable_domstring_cpp_bodies,
        cpp_vtable_terms=_writable_domstring_cpp_vtable_terms,
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
    production_webidl.READONLY_DOMSTRING: ShapeEmitter(
        header_type_blocks=(_OWNED_UTF8_C_TYPE,),
        header_slots=_readonly_domstring_header_slots,
        rust_type_blocks=(_OWNED_UTF8_RUST_TYPE,),
        rust_trait_members=_readonly_domstring_rust_trait_members,
        rust_vtable_fields=_readonly_domstring_rust_vtable_fields,
        rust_thunk_blocks=(_OWNED_UTF8_RUST_DROP,),
        rust_thunks=_readonly_domstring_rust_thunks,
        rust_vtable_init=_readonly_domstring_rust_vtable_init,
        cpp_body_blocks=(_OWNED_UTF8_CPP_SCOPE,),
        cpp_bodies=_readonly_domstring_cpp_bodies,
        cpp_vtable_terms=_readonly_domstring_cpp_vtable_terms,
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
    production_webidl.READONLY_UNSIGNED_LONG: ShapeEmitter(
        header_type_blocks=(),
        header_slots=_readonly_unsigned_long_header_slots,
        rust_type_blocks=(),
        rust_trait_members=_readonly_unsigned_long_rust_trait_members,
        rust_vtable_fields=_readonly_unsigned_long_rust_vtable_fields,
        rust_thunk_blocks=(),
        rust_thunks=_readonly_unsigned_long_rust_thunks,
        rust_vtable_init=_readonly_unsigned_long_rust_vtable_init,
        cpp_body_blocks=(),
        cpp_bodies=_readonly_unsigned_long_cpp_bodies,
        cpp_vtable_terms=_readonly_unsigned_long_cpp_vtable_terms,
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
    production_webidl.SAMEOBJECT_READONLY_INTERFACE: ShapeEmitter(
        header_type_blocks=(_HTML_COLLECTION_C_TYPE,),
        header_slots=_sameobject_readonly_interface_header_slots,
        rust_type_blocks=(_HTML_COLLECTION_RUST_TYPE,),
        rust_trait_members=_sameobject_readonly_interface_rust_trait_members,
        rust_vtable_fields=_sameobject_readonly_interface_rust_vtable_fields,
        rust_thunk_blocks=(),
        rust_thunks=_sameobject_readonly_interface_rust_thunks,
        rust_vtable_init=_sameobject_readonly_interface_rust_vtable_init,
        cpp_body_blocks=(),
        cpp_bodies=_sameobject_readonly_interface_cpp_bodies,
        cpp_vtable_terms=_sameobject_readonly_interface_cpp_vtable_terms,
    ),
    production_webidl.DOMSTRING_TO_NONNULLABLE_INTERFACE: ShapeEmitter(
        header_type_blocks=(_HTML_COLLECTION_C_TYPE,),
        header_slots=_domstring_to_nonnullable_interface_header_slots,
        rust_type_blocks=(_HTML_COLLECTION_RUST_TYPE,),
        rust_trait_members=_domstring_to_nonnullable_interface_rust_trait_members,
        rust_vtable_fields=_domstring_to_nonnullable_interface_rust_vtable_fields,
        rust_thunk_blocks=(),
        rust_thunks=_domstring_to_nonnullable_interface_rust_thunks,
        rust_vtable_init=_domstring_to_nonnullable_interface_rust_vtable_init,
        cpp_body_blocks=(),
        cpp_bodies=_domstring_to_nonnullable_interface_cpp_bodies,
        cpp_vtable_terms=_domstring_to_nonnullable_interface_cpp_vtable_terms,
    ),
    production_webidl.PURE_DOMSTRING_TO_NULLABLE_INTERFACE: ShapeEmitter(
        header_type_blocks=(),
        header_slots=_pure_domstring_to_nullable_interface_header_slots,
        rust_type_blocks=(),
        rust_trait_members=_pure_domstring_to_nullable_interface_rust_trait_members,
        rust_vtable_fields=_pure_domstring_to_nullable_interface_rust_vtable_fields,
        rust_thunk_blocks=(),
        rust_thunks=_pure_domstring_to_nullable_interface_rust_thunks,
        rust_vtable_init=_pure_domstring_to_nullable_interface_rust_vtable_init,
        cpp_body_blocks=(),
        cpp_bodies=_pure_domstring_to_nullable_interface_cpp_bodies,
        cpp_vtable_terms=_pure_domstring_to_nullable_interface_cpp_vtable_terms,
    ),
    production_webidl.PURE_THROWS_DOMSTRING_TO_NULLABLE_INTERFACE: ShapeEmitter(
        header_type_blocks=(_SELECTOR_OUTCOME_C_TYPES,),
        header_slots=_pure_throws_domstring_to_nullable_interface_header_slots,
        rust_type_blocks=(_SELECTOR_OUTCOME_RUST_TYPES,),
        rust_trait_members=_pure_throws_domstring_to_nullable_interface_rust_trait_members,
        rust_vtable_fields=_pure_throws_domstring_to_nullable_interface_rust_vtable_fields,
        rust_thunk_blocks=(),
        rust_thunks=_pure_throws_domstring_to_nullable_interface_rust_thunks,
        rust_vtable_init=_pure_throws_domstring_to_nullable_interface_rust_vtable_init,
        cpp_body_blocks=(),
        cpp_bodies=_pure_throws_domstring_to_nullable_interface_cpp_bodies,
        cpp_vtable_terms=_pure_throws_domstring_to_nullable_interface_cpp_vtable_terms,
    ),
    production_webidl.NEWOBJECT_THROWS_DOMSTRING_TO_INTERFACE: ShapeEmitter(
        header_type_blocks=(_SELECTOR_OUTCOME_C_TYPES,),
        header_slots=_newobject_throws_domstring_to_interface_header_slots,
        rust_type_blocks=(_SELECTOR_OUTCOME_RUST_TYPES,),
        rust_trait_members=_newobject_throws_domstring_to_interface_rust_trait_members,
        rust_vtable_fields=_newobject_throws_domstring_to_interface_rust_vtable_fields,
        rust_thunk_blocks=(),
        rust_thunks=_newobject_throws_domstring_to_interface_rust_thunks,
        rust_vtable_init=_newobject_throws_domstring_to_interface_rust_vtable_init,
        cpp_body_blocks=(),
        cpp_bodies=_newobject_throws_domstring_to_interface_cpp_bodies,
        cpp_vtable_terms=_newobject_throws_domstring_to_interface_cpp_vtable_terms,
    ),
    production_webidl.CREATE_ELEMENT: ShapeEmitter(
        header_type_blocks=(_OWNED_UTF8_C_TYPE, _CREATE_ELEMENT_C_TYPES),
        header_slots=_create_element_header_slots,
        rust_type_blocks=(_OWNED_UTF8_RUST_TYPE, _CREATE_ELEMENT_RUST_TYPES),
        rust_trait_members=_create_element_rust_trait_members,
        rust_vtable_fields=_create_element_rust_vtable_fields,
        rust_thunk_blocks=(_OWNED_UTF8_RUST_DROP,),
        rust_thunks=_create_element_rust_thunks,
        rust_vtable_init=_create_element_rust_vtable_init,
        cpp_body_blocks=(_OWNED_UTF8_CPP_SCOPE,),
        cpp_bodies=_create_element_cpp_bodies,
        cpp_vtable_terms=_create_element_cpp_vtable_terms,
    ),
}

# `[Pure]` changes the fail-closed selection contract but not the generated
# nullable-interface ABI or callback body.
SHAPE_EMITTERS[production_webidl.PURE_READONLY_NULLABLE_INTERFACE] = (
    SHAPE_EMITTERS[production_webidl.READONLY_NULLABLE_INTERFACE]
)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("webidls_dir", type=Path)
    parser.add_argument("out_dir", type=Path)
    arguments = parser.parse_args(argv)
    write_outputs(arguments.webidls_dir, arguments.out_dir)


if __name__ == "__main__":
    main()
