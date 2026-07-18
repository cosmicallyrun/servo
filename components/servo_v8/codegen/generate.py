# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Generate the first V8 binding slice from Servo's WebIDL parser output.

This intentionally supports only non-nullable WebIDL `long` values, references
to the generated interface itself, and one ordinary interface. Unsupported IDL
fails generation instead of silently falling back to an incorrect conversion.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve()
SERVO_ROOT = SCRIPT_PATH.parents[3]
SCRIPT_BINDINGS_ROOT = SERVO_ROOT / "components" / "script_bindings"
sys.path[:0] = [
    str(SCRIPT_BINDINGS_ROOT / "third_party" / "WebIDL" / "parser"),
    str(SCRIPT_BINDINGS_ROOT / "third_party" / "ply"),
]

import WebIDL  # noqa: E402

MethodInfo = tuple[WebIDL.IDLMethod, list[WebIDL.IDLArgument]]
Analysis = tuple[list[WebIDL.IDLArgument], list[WebIDL.IDLAttribute], list[MethodInfo]]

# ABI-stable IDs are assigned explicitly. Never derive these from parse or
# declaration order: generated handles and native cells use them for runtime
# interface validation across the C ABI.
STABLE_INTERFACE_IDS = {
    "EngineBindingSmoke": 1,
}

LANGUAGE_RESERVED_IDENTIFIERS = frozenset(
    """
    Self _Alignas _Alignof _Atomic _Bool _Complex _Generic _Imaginary _Noreturn
    _Static_assert _Thread_local abstract alignas alignof and and_eq as asm async
    atomic_cancel atomic_commit atomic_noexcept auto await become bitand bitor bool
    box break case catch char char16_t char32_t char8_t class co_await co_return
    co_yield compl concept const const_cast consteval constexpr constinit continue
    crate decltype default delete do double dyn dynamic_cast else enum explicit export
    extern false final float fn for friend gen goto if impl in inline int let long loop
    macro match mod move mut mutable namespace new noexcept not not_eq nullptr operator
    or or_eq override priv private protected pub public ref register reinterpret_cast
    requires restrict return self short signed sizeof static static_assert static_cast
    struct super switch synchronized template this thread_local throw trait true try
    type typedef typeid typename union unsafe unsigned unsized use using virtual void
    volatile wchar_t where while xor xor_eq yield
    """.split()
)

# WebIDL arguments are emitted directly into C, C++, and Rust callback bodies.
# Reject names that would shadow glue locals rather than silently changing them.
GENERATED_CALLBACK_LOCALS = frozenset(
    {
        "cell",
        "cpp_heap",
        "info",
        "isolate",
        "native",
        "pending",
        "runtime",
        "visitor",
    }
)


def fail(message: str) -> None:
    raise RuntimeError(f"unsupported V8 WebIDL binding: {message}")


def stable_interface_id(interface_name: str) -> int:
    try:
        return STABLE_INTERFACE_IDS[interface_name]
    except KeyError:
        fail(f"interface `{interface_name}` has no stable C ABI ID")


def snake_case(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def upper_snake_case(name: str) -> str:
    return snake_case(name).upper()


def upper_camel_case(name: str) -> str:
    return "".join(part[:1].upper() + part[1:] for part in snake_case(name).split("_"))


def require_long(type_: WebIDL.IDLType, context: str) -> None:
    if type_.nullable() or type_.tag() != WebIDL.IDLType.Tags.int32:
        fail(f"{context} must use non-nullable `long`, got `{type_.prettyName()}`")
    if type_.hasClamp() or type_.hasEnforceRange():
        fail(f"{context} may not use conversion-changing integer attributes")


def is_self_interface(type_: WebIDL.IDLType, interface_name: str) -> bool:
    return not type_.nullable() and type_.isNonCallbackInterface() and type_.inner.identifier.name == interface_name


def require_argument_type(type_: WebIDL.IDLType, interface_name: str, context: str) -> None:
    if is_self_interface(type_, interface_name):
        return
    require_long(type_, context)


def checked_arguments(
    arguments: list[WebIDL.IDLArgument], context: str, interface_name: str
) -> list[WebIDL.IDLArgument]:
    for argument in arguments:
        if argument.optional or argument.variadic:
            fail(f"{context} argument `{argument.identifier.name}` may not be optional or variadic")
        require_argument_type(argument.type, interface_name, f"{context} argument `{argument.identifier.name}`")
    return arguments


def one_signature(method: WebIDL.IDLMethod, context: str) -> tuple[WebIDL.IDLType, list[WebIDL.IDLArgument]]:
    signatures = method.signatures()
    if len(signatures) != 1:
        fail(f"{context} may not be overloaded")
    return signatures[0]


def c_type(type_: WebIDL.IDLType, interface_name: str) -> str:
    if is_self_interface(type_, interface_name):
        return f"ServoV8{interface_name}Handle"
    require_long(type_, "C ABI value")
    return "int32_t"


def rust_type(type_: WebIDL.IDLType, interface_name: str) -> str:
    if is_self_interface(type_, interface_name):
        return f"{interface_name}Handle"
    require_long(type_, "Rust ABI value")
    return "i32"


def c_arguments(arguments: list[WebIDL.IDLArgument], include_native: bool, interface_name: str) -> str:
    values = ["void* native"] if include_native else []
    values.extend(f"{c_type(argument.type, interface_name)} {argument.identifier.name}" for argument in arguments)
    return ", ".join(values) if values else "void"


def rust_arguments(arguments: list[WebIDL.IDLArgument], include_native: bool, interface_name: str) -> str:
    values = ["*mut c_void"] if include_native else []
    values.extend(rust_type(argument.type, interface_name) for argument in arguments)
    return ", ".join(values)


def rust_named_arguments(arguments: list[WebIDL.IDLArgument], interface_name: str) -> str:
    return ", ".join(
        f"{argument.identifier.name}: {rust_type(argument.type, interface_name)}" for argument in arguments
    )


def argument_names(arguments: list[WebIDL.IDLArgument]) -> str:
    return ", ".join(argument.identifier.name for argument in arguments)


def claim_generated_identifier(claimed: dict[str, str], identifier: str, description: str) -> None:
    if previous := claimed.get(identifier):
        fail(f"generated identifier `{identifier}` for {description} collides with {previous}")
    claimed[identifier] = description


def validate_language_identifier(identifier: str, description: str) -> None:
    if identifier in LANGUAGE_RESERVED_IDENTIFIERS:
        fail(f"generated identifier `{identifier}` for {description} is reserved by C, C++, or Rust")


def validate_argument_identifiers(arguments: list[WebIDL.IDLArgument], context: str) -> None:
    claimed = {name: f"generated callback local `{name}`" for name in GENERATED_CALLBACK_LOCALS}
    for argument in arguments:
        name = argument.identifier.name
        validate_language_identifier(name, f"{context} argument `{name}`")
        claim_generated_identifier(claimed, name, f"{context} argument `{name}`")
        if argument.type.isNonCallbackInterface():
            claim_generated_identifier(
                claimed,
                f"{name}_cell",
                f"temporary for {context} interface argument `{name}`",
            )


def validate_generated_identifiers(
    constructor_arguments: list[WebIDL.IDLArgument],
    attributes: list[WebIDL.IDLAttribute],
    methods: list[MethodInfo],
) -> None:
    vtable_fields = {
        "constructor": "generated constructor callback",
        "trace": "generated lifecycle callback `trace`",
        "drop": "generated lifecycle callback `drop`",
    }
    trait_methods = {
        "constructor": "generated constructor method",
        "trace": "generated lifecycle method `trace`",
        "drop": "reserved Rust destructor method `drop`",
    }
    cpp_callbacks: dict[str, str] = {}

    validate_argument_identifiers(constructor_arguments, "constructor")
    for attribute in attributes:
        source_name = attribute.identifier.name
        member = snake_case(source_name)
        validate_language_identifier(member, f"attribute `{source_name}`")
        claim_generated_identifier(trait_methods, member, f"attribute getter `{source_name}`")
        claim_generated_identifier(vtable_fields, f"get_{member}", f"attribute getter `{source_name}`")
        callback = upper_camel_case(source_name)
        claim_generated_identifier(
            cpp_callbacks,
            f"Get{callback}",
            f"C++ callback for attribute getter `{source_name}`",
        )
        if not attribute.readonly:
            claim_generated_identifier(trait_methods, f"set_{member}", f"attribute setter `{source_name}`")
            claim_generated_identifier(vtable_fields, f"set_{member}", f"attribute setter `{source_name}`")
            claim_generated_identifier(
                cpp_callbacks,
                f"Set{callback}",
                f"C++ callback for attribute setter `{source_name}`",
            )

    for method, arguments in methods:
        source_name = method.identifier.name
        member = snake_case(source_name)
        validate_language_identifier(member, f"operation `{source_name}`")
        claim_generated_identifier(vtable_fields, member, f"operation `{source_name}`")
        claim_generated_identifier(trait_methods, member, f"operation `{source_name}`")
        claim_generated_identifier(
            cpp_callbacks,
            upper_camel_case(source_name),
            f"C++ callback for operation `{source_name}`",
        )
        validate_argument_identifiers(arguments, f"operation `{source_name}`")


def conversion_lines(
    arguments: list[WebIDL.IDLArgument],
    start_index: int,
    context: str,
    interface_name: str,
) -> list[str]:
    lines: list[str] = []
    for index, argument in enumerate(arguments, start=start_index):
        name = argument.identifier.name
        if is_self_interface(argument.type, interface_name):
            lines.extend(
                [
                    f"  if (info.Length() <= {index} || !info[{index}]->IsObject()) {{",
                    f'    ThrowTypeError(isolate, "{context} expects a `{interface_name}` `{name}`");',
                    "    return;",
                    "  }",
                    f"  ServoV8DomCell* {name}_cell = UnwrapDomCell(",
                    f"      isolate, info[{index}].As<v8::Object>());",
                    f"  if (!{name}_cell) {{",
                    f'    ThrowTypeError(isolate, "{context} expects a `{interface_name}` `{name}`");',
                    "    return;",
                    "  }",
                    f"  ServoV8{interface_name}Handle {name}{{{name}_cell}};",
                ]
            )
        else:
            lines.extend(
                [
                    f"  int32_t {name} = 0;",
                    f"  if (info.Length() <= {index}) {{",
                    f'    ThrowTypeError(isolate, "{context} expects a long `{name}`");',
                    "    return;",
                    "  }",
                    f"  if (!info[{index}]->Int32Value(isolate->GetCurrentContext()).To(&{name})) {{",
                    "    return;",
                    "  }",
                ]
            )
    return lines


def parse_interface(webidl_path: Path, out_dir: Path) -> WebIDL.IDLInterface:
    parser = WebIDL.Parser(str(out_dir / "webidl-cache"))
    parser.parse(webidl_path.read_text(encoding="utf-8"), str(webidl_path))
    results = parser.finish()
    if len(results) != 1:
        fail(f"expected one WebIDL definition, found {len(results)}")
    interfaces = [result for result in results if result.isInterface()]
    if len(interfaces) != 1:
        fail(f"expected exactly one interface, found {len(interfaces)}")
    interface = interfaces[0]
    if interface.identifier.name != "EngineBindingSmoke":
        fail(f"expected `EngineBindingSmoke`, found `{interface.identifier.name}`")
    if interface.parent:
        fail("interface inheritance is not implemented in the first slice")
    if interface._extendedAttrDict:
        fail("interface extended attributes are not implemented in the first slice")
    return interface


def analyze(interface: WebIDL.IDLInterface) -> Analysis:
    name = interface.identifier.name
    constructor = interface.ctor()
    if not constructor:
        fail(f"interface `{name}` must have a constructor")
    unsupported_constructor_attributes = set(constructor._extendedAttrDict) - {"NewObject"}
    if unsupported_constructor_attributes:
        fail(
            "constructor extended attributes are not implemented: "
            + ", ".join(sorted(unsupported_constructor_attributes))
        )
    _, constructor_arguments = one_signature(constructor, f"{name} constructor")
    checked_arguments(constructor_arguments, f"{name} constructor", name)

    attributes = []
    methods = []
    for member in interface.members:
        member_name = member.identifier.name
        if member._extendedAttrDict:
            fail(f"extended attributes on member `{member_name}` are not implemented")
        if member.isAttr():
            if member.isStatic():
                fail(f"static attribute `{member_name}`")
            require_long(member.type, f"attribute `{member_name}`")
            attributes.append(member)
            continue
        if member.isMethod():
            if member.isStatic() or member.isSpecial():
                fail(f"static or special operation `{member_name}`")
            return_type, arguments = one_signature(member, f"operation `{member_name}`")
            require_long(return_type, f"operation `{member_name}` return type")
            checked_arguments(arguments, f"operation `{member_name}`", name)
            methods.append((member, arguments))
            continue
        fail(f"member `{member_name}` is neither an attribute nor an operation")
    validate_generated_identifiers(constructor_arguments, attributes, methods)
    return constructor_arguments, attributes, methods


def generate_header(
    interface: WebIDL.IDLInterface,
    constructor_arguments: list[WebIDL.IDLArgument],
    attributes: list[WebIDL.IDLAttribute],
    methods: list[MethodInfo],
) -> str:
    name = interface.identifier.name
    interface_id = stable_interface_id(name)
    interface_id_constant = f"SERVO_V8_{upper_snake_case(name)}_INTERFACE_ID"
    c_vtable = f"ServoV8{name}VTable"
    lines = [
        "/* Generated from WebIDL. Do not edit. */",
        f"#ifndef SERVO_V8_GENERATED_{upper_snake_case(name)}_H_",
        f"#define SERVO_V8_GENERATED_{upper_snake_case(name)}_H_",
        "",
        f"#define {interface_id_constant} UINT32_C({interface_id})",
        "",
        f"typedef struct ServoV8{name}Handle {{",
        "  ServoV8DomCell* cell;",
        f"}} ServoV8{name}Handle;",
        "",
        f"typedef struct {c_vtable} {{",
        f"  void* (*constructor)({c_arguments(constructor_arguments, False, name)});",
    ]
    for attribute in attributes:
        member = snake_case(attribute.identifier.name)
        lines.append(f"  int32_t (*get_{member})(void* native);")
        if not attribute.readonly:
            lines.append(f"  void (*set_{member})(void* native, int32_t value);")
    for method, arguments in methods:
        member = snake_case(method.identifier.name)
        lines.append(f"  int32_t (*{member})({c_arguments(arguments, True, name)});")
    lines.extend(
        [
            "  ServoV8TraceCallback trace;",
            "  ServoV8DropCallback drop;",
            f"}} {c_vtable};",
            "",
            f"#endif  /* SERVO_V8_GENERATED_{upper_snake_case(name)}_H_ */",
            "",
        ]
    )
    return "\n".join(lines)


def generate_rust(
    interface: WebIDL.IDLInterface,
    constructor_arguments: list[WebIDL.IDLArgument],
    attributes: list[WebIDL.IDLAttribute],
    methods: list[MethodInfo],
) -> str:
    name = interface.identifier.name
    interface_id = stable_interface_id(name)
    interface_id_constant = f"{upper_snake_case(name)}_INTERFACE_ID"
    snake_name = snake_case(name)
    binding_trait = f"{name}Binding"
    constructor_named_arguments = rust_named_arguments(constructor_arguments, name)
    constructor_argument_names = argument_names(constructor_arguments)
    lines = [
        "// Generated from WebIDL. Do not edit.",
        f'pub const {upper_snake_case(name)}_INTERFACE_NAME: &str = "{name}";',
        f"pub const {interface_id_constant}: u32 = {interface_id};",
        "",
        "/// Opaque cppgc identity passed for a WebIDL interface argument.",
        "#[derive(Clone, Copy)]",
        "#[repr(C)]",
        f"pub struct {name}Handle {{",
        "    cell: *mut DomCell,",
        "}",
        "",
        f"impl {name}Handle {{",
        "    pub fn cell(self) -> *mut DomCell {",
        "        self.cell",
        "    }",
        "",
        "    /// Returns the native object owned by this handle's cppgc cell.",
        "    ///",
        "    /// # Safety",
        "    ///",
        "    /// The caller must request the concrete type used to construct this binding",
        "    /// and must ensure the handle remains traced while using the pointer.",
        "    pub unsafe fn native<T>(self) -> *mut T {",
        "        // SAFETY: The handle can only be constructed by generated C++ unwrap code.",
        f"        unsafe {{ servo_v8_dom_cell_native(self.cell, {interface_id_constant}) }}.cast()",
        "    }",
        "",
        "    /// Reports this handle as a native DOM edge during a V8 trace callback.",
        "    ///",
        "    /// # Safety",
        "    ///",
        "    /// `visitor` must be the live visitor passed to the current trace callback,",
        "    /// and this handle must refer to a live cell in the same CppHeap.",
        "    pub unsafe fn trace(self, visitor: *mut TraceVisitor) {",
        "        // SAFETY: The caller upholds the V8 tracing lifetime and heap invariants.",
        f"        unsafe {{ servo_v8_trace_dom_cell(visitor, self.cell, {interface_id_constant}) }}",
        "    }",
        "}",
        "",
        "/// Native implementation contract for this generated WebIDL binding.",
        "///",
        "/// # Safety",
        "///",
        "/// Implementations and `T::Drop` must not unwind or re-enter V8/cppgc",
        "/// from any callback. Every stored cell handle must be reported during",
        "/// every `trace` call using only the visitor passed by V8. `T::Drop` must",
        "/// not dereference stored handles because referenced cells may already be",
        "/// finalized.",
        f"pub unsafe trait {binding_trait}: Sized + 'static {{",
        f"    fn constructor({constructor_named_arguments}) -> Option<Self>;",
    ]
    for attribute in attributes:
        member = snake_case(attribute.identifier.name)
        lines.append(f"    fn {member}(&self) -> i32;")
        if not attribute.readonly:
            lines.append(f"    fn set_{member}(&mut self, value: i32);")
    for method, arguments in methods:
        member = snake_case(method.identifier.name)
        named_arguments = rust_named_arguments(arguments, name)
        if named_arguments:
            named_arguments = ", " + named_arguments
        lines.append(f"    fn {member}(&self{named_arguments}) -> i32;")
    lines.extend(
        [
            "",
            "    /// # Safety",
            "    ///",
            "    /// `visitor` is valid only for the duration of this call.",
            "    unsafe fn trace(&self, visitor: *mut TraceVisitor);",
            "}",
            "",
            "#[derive(Clone, Copy)]",
            "#[repr(C)]",
            f"pub struct {name}VTable {{",
            '    pub constructor: Option<unsafe extern "C" fn('
            + rust_arguments(constructor_arguments, False, name)
            + ") -> *mut c_void>,",
        ]
    )
    for attribute in attributes:
        member = snake_case(attribute.identifier.name)
        lines.append(f'    pub get_{member}: Option<unsafe extern "C" fn(*mut c_void) -> i32>,')
        if not attribute.readonly:
            lines.append(f'    pub set_{member}: Option<unsafe extern "C" fn(*mut c_void, i32)>,')
    for method, arguments in methods:
        member = snake_case(method.identifier.name)
        args = rust_arguments(arguments, True, name)
        lines.append(f'    pub {member}: Option<unsafe extern "C" fn({args}) -> i32>,')
    lines.extend(
        [
            "    pub trace: Option<TraceCallback>,",
            "    pub drop: Option<DropCallback>,",
            "}",
            "",
            f'unsafe extern "C" fn {snake_name}_constructor<T: {binding_trait}>(',
            f"    {constructor_named_arguments}",
            ") -> *mut c_void {",
            f"    T::constructor({constructor_argument_names})",
            "        .map(|native| Box::into_raw(Box::new(native)).cast())",
            "        .unwrap_or(std::ptr::null_mut())",
            "}",
            "",
        ]
    )

    for attribute in attributes:
        member = snake_case(attribute.identifier.name)
        lines.extend(
            [
                f'unsafe extern "C" fn {snake_name}_get_{member}<T: {binding_trait}>(',
                "    native: *mut c_void,",
                ") -> i32 {",
                "    // SAFETY: The generated C++ cell passes the Box<T> allocated above.",
                "    let native = unsafe { &*native.cast::<T>() };",
                f"    native.{member}()",
                "}",
                "",
            ]
        )
        if not attribute.readonly:
            lines.extend(
                [
                    f'unsafe extern "C" fn {snake_name}_set_{member}<T: {binding_trait}>(',
                    "    native: *mut c_void,",
                    "    value: i32,",
                    ") {",
                    "    // SAFETY: The generated C++ cell passes the Box<T> allocated above.",
                    "    let native = unsafe { &mut *native.cast::<T>() };",
                    f"    native.set_{member}(value);",
                    "}",
                    "",
                ]
            )
    for method, arguments in methods:
        member = snake_case(method.identifier.name)
        named_arguments = rust_named_arguments(arguments, name)
        if named_arguments:
            named_arguments = "    " + named_arguments + ","
        call_arguments = argument_names(arguments)
        lines.extend(
            [
                f'unsafe extern "C" fn {snake_name}_{member}<T: {binding_trait}>(',
                "    native: *mut c_void,",
            ]
        )
        if named_arguments:
            lines.append(named_arguments)
        lines.extend(
            [
                ") -> i32 {",
                "    // SAFETY: The generated C++ cell passes the Box<T> allocated above.",
                "    let native = unsafe { &*native.cast::<T>() };",
                f"    native.{member}({call_arguments})",
                "}",
                "",
            ]
        )

    lines.extend(
        [
            f'unsafe extern "C" fn {snake_name}_trace<T: {binding_trait}>(',
            "    native: *mut c_void,",
            "    visitor: *mut TraceVisitor,",
            ") {",
            "    // SAFETY: The generated C++ cell passes the Box<T> allocated above.",
            "    let native = unsafe { &*native.cast::<T>() };",
            "    // SAFETY: V8 supplies the visitor for this callback only.",
            "    unsafe { native.trace(visitor) };",
            "}",
            "",
            f'unsafe extern "C" fn {snake_name}_drop<T: {binding_trait}>(native: *mut c_void) {{',
            "    // SAFETY: ServoV8DomCell owns and drops the constructor's Box exactly once.",
            "    drop(unsafe { Box::from_raw(native.cast::<T>()) });",
            "}",
            "",
            f"impl {name}VTable {{",
            f"    pub fn for_type<T: {binding_trait}>() -> Self {{",
            "        Self {",
            f"            constructor: Some({snake_name}_constructor::<T>),",
        ]
    )
    for attribute in attributes:
        member = snake_case(attribute.identifier.name)
        lines.append(f"            get_{member}: Some({snake_name}_get_{member}::<T>),")
        if not attribute.readonly:
            lines.append(f"            set_{member}: Some({snake_name}_set_{member}::<T>),")
    for method, arguments in methods:
        member = snake_case(method.identifier.name)
        lines.append(f"            {member}: Some({snake_name}_{member}::<T>),")
    lines.extend(
        [
            f"            trace: Some({snake_name}_trace::<T>),",
            f"            drop: Some({snake_name}_drop::<T>),",
            "        }",
            "    }",
            "}",
            "",
        ]
    )
    return "\n".join(lines)


def generate_cpp(
    interface: WebIDL.IDLInterface,
    constructor_arguments: list[WebIDL.IDLArgument],
    attributes: list[WebIDL.IDLAttribute],
    methods: list[MethodInfo],
) -> str:
    name = interface.identifier.name
    interface_id_constant = f"SERVO_V8_{upper_snake_case(name)}_INTERFACE_ID"
    runtime_vtable = f"{snake_case(name)}_vtable"
    c_vtable = f"ServoV8{name}VTable"
    lines = [
        "// Generated from WebIDL. Do not edit.",
        f'constexpr char k{name}InterfaceName[] = "{name}";',
        "",
        f"void {name}Constructor(const v8::FunctionCallbackInfo<v8::Value>& info) {{",
        "  v8::Isolate* isolate = info.GetIsolate();",
        "  if (!info.IsConstructCall()) {",
        f'    ThrowTypeError(isolate, "{name} must be constructed with new");',
        "    return;",
        "  }",
        "  auto* runtime = static_cast<ServoV8Runtime*>(",
        "      v8::Local<v8::External>::Cast(info.Data())",
        "          ->Value(v8::kExternalPointerTypeTagDefault));",
        f"  if (!runtime || !runtime->{runtime_vtable}.constructor) {{",
        f'    ThrowTypeError(isolate, "{name} binding is not initialized");',
        "    return;",
        "  }",
    ]
    lines.extend(conversion_lines(constructor_arguments, 0, f"{name} constructor", name))
    argument_names = ", ".join(argument.identifier.name for argument in constructor_arguments)
    lines.extend(
        [
            f"  void* native = runtime->{runtime_vtable}.constructor({argument_names});",
            "  if (!native) {",
            "    isolate->ThrowException(v8::Exception::Error(",
            f'        V8String(isolate, "Rust {name} allocation failed")));',
            "    return;",
            "  }",
            "",
            "  v8::CppHeap* cpp_heap = isolate->GetCppHeap();",
            "  auto* cell = cppgc::MakeGarbageCollected<ServoV8DomCell>(",
            f"      cpp_heap->GetAllocationHandle(), native, {interface_id_constant},",
            f"      runtime->{runtime_vtable});",
            "  cppgc::Persistent<ServoV8DomCell> pending(cell);",
            "  v8::Object::Wrap<kServoDomTag>(isolate, info.This(), cell);",
            "  cell->SetWrapper(isolate, info.This());",
            "  pending.Clear();",
            "  info.GetReturnValue().Set(info.This());",
            "}",
            "",
        ]
    )

    for attribute in attributes:
        member_name = attribute.identifier.name
        member = snake_case(member_name)
        callback = upper_camel_case(member_name)
        lines.extend(
            [
                f"void {name}Get{callback}(const v8::FunctionCallbackInfo<v8::Value>& info) {{",
                "  v8::Isolate* isolate = info.GetIsolate();",
                "  ServoV8DomCell* cell = UnwrapDomCell(info);",
                f"  if (!cell || !cell->vtable().get_{member}) {{",
                f'    ThrowTypeError(isolate, "invalid {name} receiver");',
                "    return;",
                "  }",
                f"  info.GetReturnValue().Set(cell->vtable().get_{member}(cell->native()));",
                "}",
                "",
            ]
        )
        if not attribute.readonly:
            lines.extend(
                [
                    f"void {name}Set{callback}(const v8::FunctionCallbackInfo<v8::Value>& info) {{",
                    "  v8::Isolate* isolate = info.GetIsolate();",
                    "  ServoV8DomCell* cell = UnwrapDomCell(info);",
                    f"  if (!cell || !cell->vtable().set_{member}) {{",
                    f'    ThrowTypeError(isolate, "invalid {name} receiver");',
                    "    return;",
                    "  }",
                    "  int32_t value = 0;",
                    "  if (info.Length() < 1) {",
                    f'    ThrowTypeError(isolate, "{name}.{member_name} expects a long");',
                    "    return;",
                    "  }",
                    "  if (!info[0]->Int32Value(isolate->GetCurrentContext()).To(&value)) {",
                    "    return;",
                    "  }",
                    f"  cell->vtable().set_{member}(cell->native(), value);",
                    "}",
                    "",
                ]
            )

    for method, arguments in methods:
        member_name = method.identifier.name
        member = snake_case(member_name)
        callback = upper_camel_case(member_name)
        lines.extend(
            [
                f"void {name}{callback}(const v8::FunctionCallbackInfo<v8::Value>& info) {{",
                "  v8::Isolate* isolate = info.GetIsolate();",
                "  ServoV8DomCell* cell = UnwrapDomCell(info);",
                f"  if (!cell || !cell->vtable().{member}) {{",
                f'    ThrowTypeError(isolate, "invalid {name} receiver");',
                "    return;",
                "  }",
            ]
        )
        lines.extend(conversion_lines(arguments, 0, f"{name}.{member_name}", name))
        argument_names = ", ".join(argument.identifier.name for argument in arguments)
        if argument_names:
            argument_names = ", " + argument_names
        lines.extend(
            [
                f"  info.GetReturnValue().Set(cell->vtable().{member}(cell->native(){argument_names}));",
                "}",
                "",
            ]
        )

    required_fields = ["vtable.constructor"]
    for attribute in attributes:
        member = snake_case(attribute.identifier.name)
        required_fields.append(f"vtable.get_{member}")
        if not attribute.readonly:
            required_fields.append(f"vtable.set_{member}")
    required_fields.extend(f"vtable.{snake_case(method.identifier.name)}" for method, _ in methods)
    required_fields.extend(["vtable.trace", "vtable.drop"])
    lines.extend(
        [
            f"bool Is{name}VTableComplete(const {c_vtable}& vtable) {{",
            "  return " + " &&\n         ".join(required_fields) + ";",
            "}",
            "",
            f"v8::Local<v8::FunctionTemplate> Create{name}Template(",
            "    v8::Isolate* isolate, v8::Local<v8::External> data) {",
            "  v8::Local<v8::FunctionTemplate> constructor =",
            f"      v8::FunctionTemplate::New(isolate, {name}Constructor, data,",
            f"                                v8::Local<v8::Signature>(), {len(constructor_arguments)});",
            f"  constructor->SetClassName(V8String(isolate, k{name}InterfaceName));",
            "  v8::Local<v8::ObjectTemplate> prototype = constructor->PrototypeTemplate();",
        ]
    )
    for attribute in attributes:
        member_name = attribute.identifier.name
        callback = upper_camel_case(member_name)
        setter = (
            f"v8::FunctionTemplate::New(isolate, {name}Set{callback}, "
            "v8::Local<v8::Value>(), v8::Local<v8::Signature>(), 1)"
            if not attribute.readonly
            else "v8::Local<v8::FunctionTemplate>()"
        )
        lines.extend(
            [
                "  prototype->SetAccessorProperty(",
                f'      V8String(isolate, "{member_name}"),',
                f"      v8::FunctionTemplate::New(isolate, {name}Get{callback},",
                "                                v8::Local<v8::Value>(),",
                "                                v8::Local<v8::Signature>(), 0),",
                f"      {setter});",
            ]
        )
    for method, arguments in methods:
        member_name = method.identifier.name
        callback = upper_camel_case(member_name)
        lines.extend(
            [
                "  prototype->Set(",
                f'      V8String(isolate, "{member_name}"),',
                f"      v8::FunctionTemplate::New(isolate, {name}{callback},",
                "                                v8::Local<v8::Value>(),",
                f"                                v8::Local<v8::Signature>(), {len(arguments)}));",
            ]
        )
    lines.extend(["  return constructor;", "}", ""])
    return "\n".join(lines)


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: generate.py WEBIDL OUT_DIR")
    webidl_path = Path(sys.argv[1]).resolve()
    out_dir = Path(sys.argv[2]).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    interface = parse_interface(webidl_path, out_dir)
    constructor_arguments, attributes, methods = analyze(interface)
    outputs = {
        "servo_v8_generated.h": generate_header(interface, constructor_arguments, attributes, methods),
        "servo_v8_generated.rs": generate_rust(interface, constructor_arguments, attributes, methods),
        "servo_v8_generated.inc": generate_cpp(interface, constructor_arguments, attributes, methods),
    }
    for filename, contents in outputs.items():
        (out_dir / filename).write_text(contents, encoding="utf-8")


if __name__ == "__main__":
    main()
