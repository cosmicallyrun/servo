/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at https://mozilla.org/MPL/2.0/. */

//! Experimental native Servo–V8 bridge.
//!
//! V8 handle types stay in C++. Generated bindings will cross this boundary
//! with typed C ABI thunks containing only POD values and native pointers.
//! The bridge is thread-confined because each runtime owns a V8 isolate and
//! unified `CppHeap`.

use std::ffi::c_void;
use std::marker::PhantomData;
use std::ptr::NonNull;
use std::rc::Rc;
use std::sync::{Arc, Mutex};

const ABI_VERSION: u32 = 27;
const ERROR_CAPACITY: usize = 2048;

#[repr(C)]
struct RawRuntime {
    _private: [u8; 0],
}

#[repr(C)]
pub struct DomCell {
    _private: [u8; 0],
}

#[repr(C)]
pub struct TraceVisitor {
    _private: [u8; 0],
}

/// Identifies an independent V8 context owned by a [`Runtime`].
///
/// Realm IDs are runtime-local, never reused, and become invalid as soon as
/// [`Runtime::destroy_realm`] succeeds.
#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
#[repr(transparent)]
pub struct RealmId(u64);

/// Identifies a compiled classic script retained by one V8 realm.
///
/// Script IDs are runtime-local, never reused, and consumed by the first call
/// to [`Runtime::run_script_in_realm`], whether execution succeeds or throws.
#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
#[repr(transparent)]
pub struct ScriptId(u64);

/// Identifies one V8 function and argument list retained for a Servo timer.
///
/// Callback IDs are realm-local, never reused, and stop being valid when the
/// one-shot fires, the timer is cleared, or the realm is destroyed.
#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
#[repr(transparent)]
pub struct TimerCallbackId(u64);

#[repr(C)]
struct ErrorBuffer {
    data: *mut u8,
    capacity: usize,
    length: usize,
}

#[repr(C)]
struct RawScriptException {
    message: ErrorBuffer,
    resource_name: ErrorBuffer,
    stack: ErrorBuffer,
    line_number: u32,
    column_number: u32,
}

#[repr(C)]
struct RawScriptRunOutcome {
    status: u32,
    exception: RawScriptException,
}

#[repr(C)]
struct RawScriptCompileOutcome {
    status: u32,
    script_id: ScriptId,
    exception: RawScriptException,
}

const SCRIPT_RUN_COMPLETED: u32 = 0;
const SCRIPT_RUN_THROWN: u32 = 1;
const SCRIPT_RUN_TERMINATED: u32 = 2;
const SCRIPT_COMPILED: u32 = 0;
const SCRIPT_COMPILE_THROWN: u32 = 1;

#[derive(Debug, Eq, PartialEq)]
pub struct ScriptException {
    pub message: String,
    pub resource_name: String,
    pub stack: String,
    pub line_number: u32,
    pub column_number: u32,
}

/// One failed microtask job, and the realm it belongs to.
///
/// `realm_id` is `None` only when V8 could not name a context for the failure,
/// which leaves the embedder no global to report it on.
#[derive(Debug, Eq, PartialEq)]
pub struct JobError {
    pub realm_id: Option<RealmId>,
    pub exception: ScriptException,
}

#[derive(Debug, Eq, PartialEq)]
pub enum ScriptRunOutcome {
    Completed,
    Thrown(ScriptException),
    Terminated,
}

#[derive(Debug, Eq, PartialEq)]
pub enum ScriptCompileOutcome {
    Compiled(ScriptId),
    ParseError(ScriptException),
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
#[repr(C)]
pub struct Options {
    pub enable_turbolev: u8,
    pub enable_turbolev_future: u8,
    pub expose_gc: u8,
}

impl Default for Options {
    fn default() -> Self {
        Self {
            enable_turbolev: 1,
            enable_turbolev_future: 0,
            expose_gc: 0,
        }
    }
}

pub type TraceCallback = unsafe extern "C" fn(*mut c_void, *mut TraceVisitor);
pub type DropCallback = unsafe extern "C" fn(*mut c_void);

#[repr(C)]
pub struct RawInterfaceValue {
    pub is_null: u8,
    pub key: *const c_void,
    pub native: *mut c_void,
}

/// One Servo DOM object being handed to script.
///
/// `key` identifies the object for the realm's wrapper cache, so that reading
/// the same object twice yields the same JavaScript object. A raw address is
/// safe here only because the wrapper cell keeps that object alive for exactly
/// as long as a cache entry for it can be hit.
///
/// `native` is a freshly boxed host. The bridge takes it only when it creates
/// a new wrapper, and drops it through the vtable when an existing wrapper is
/// found, so ownership never straddles the two outcomes.
pub struct InterfaceHandle {
    pub key: *const c_void,
    pub native: *mut c_void,
}

impl InterfaceHandle {
    /// Boxes `host` and derives the cache key from `dom_object`.
    ///
    /// # Safety
    ///
    /// `dom_object` must be the address of the DOM object `host` roots, and
    /// that root must keep it alive for as long as the host lives. Passing an
    /// address the host does not root would let the cache outlive its object.
    pub unsafe fn new<T: ElementHostBinding>(dom_object: *const c_void, host: T) -> Self {
        Self {
            key: dom_object,
            native: Box::into_raw(Box::new(host)).cast::<c_void>(),
        }
    }
}

/// The complete native result space for a selector operation returning Element?.
///
/// Servo's scope-match algorithm reports selector parse failure as a
/// `SyntaxError` DOMException. Keeping that as a typed status prevents a
/// SpiderMonkey exception object or pending exception from crossing into V8.
pub enum SelectorElementResult {
    Match(Option<InterfaceHandle>),
    SyntaxError,
    HostFailure,
}

/// The complete native result space for a selector operation returning boolean.
///
/// Syntax failures remain typed data until V8 creates the realm-local
/// `DOMException`; internal host failures become a V8 `TypeError`.
pub enum SelectorBooleanResult {
    Match(bool),
    SyntaxError,
    HostFailure,
}

/// One newly-created static NodeList host being transferred to V8.
pub struct NodeListHandle {
    pub native: *mut c_void,
}

impl NodeListHandle {
    /// Transfers one collection host through the runtime's installed NodeList
    /// vtable.
    ///
    /// # Safety
    ///
    /// `T` must be the exact type previously passed to
    /// `Runtime::install_node_list_host` for the receiving runtime.
    pub unsafe fn new<T: NodeListHostBinding>(host: T) -> Self {
        Self {
            native: Box::into_raw(Box::new(host)).cast(),
        }
    }
}

/// The complete result space for ParentNode.querySelectorAll.
pub enum SelectorNodeListResult {
    Match(NodeListHandle),
    SyntaxError,
    HostFailure,
}

/// A static NodeList created for one querySelectorAll call.
///
/// # Safety
///
/// Implementations stay on their originating script thread. `length`, `item`,
/// and `Drop` must not unwind, re-enter V8 or cppgc, or pump an event loop.
/// Every item must
/// return a freshly-owned Element host whose cache key names the rooted Servo
/// object and whose concrete type is the runtime's installed Element host type.
pub unsafe trait NodeListHostBinding: Sized + 'static {
    fn length(&self) -> u32;
    fn item(&self, index: u32) -> Option<InterfaceHandle>;
}

#[derive(Clone, Copy)]
#[repr(C)]
pub struct NodeListHostVTable {
    pub get_length: Option<unsafe extern "C" fn(*mut c_void, *mut u32) -> u8>,
    pub item:
        Option<unsafe extern "C" fn(*mut c_void, u32, *mut RawInterfaceValue) -> u8>,
    pub drop: Option<DropCallback>,
}

/// A host for one Servo `Element` reachable from V8.
///
/// # Safety
///
/// Implementations must stay on the owning script thread, must not unwind,
/// and must root the DOM object they read for the duration of the call. The
/// implementation is dropped from a cppgc destructor during a V8 collection,
/// so its `Drop` must not re-enter V8 or pump an event loop.
pub unsafe trait ElementHostBinding: Sized + 'static {
    fn local_name(&self) -> String;
    fn tag_name(&self) -> String;
    fn id(&self) -> String;
    unsafe fn set_id(&self, host_context: *mut c_void, value: &str) -> bool;
    fn class_name(&self) -> String;
    unsafe fn set_class_name(&self, host_context: *mut c_void, value: &str) -> bool;
    fn has_attributes(&self) -> bool;
    unsafe fn get_attribute(
        &self,
        host_context: *mut c_void,
        name: &str,
    ) -> Result<Option<String>, ()>;
    unsafe fn has_attribute(&self, host_context: *mut c_void, name: &str) -> Option<bool>;
    fn node_type(&self) -> u16;
    fn node_name(&self) -> String;
    fn is_connected(&self) -> bool;
    fn text_content(&self) -> Option<String>;
    unsafe fn set_text_content(&self, host_context: *mut c_void, value: Option<&str>) -> bool;
    fn has_child_nodes(&self) -> bool;
    fn first_element_child(&self) -> Option<InterfaceHandle>;
    fn last_element_child(&self) -> Option<InterfaceHandle>;
    fn child_element_count(&self) -> u32;
    unsafe fn query_selector(
        &self,
        host_context: *mut c_void,
        selectors: &str,
    ) -> SelectorElementResult;
    unsafe fn closest(&self, host_context: *mut c_void, selectors: &str) -> SelectorElementResult;
    unsafe fn matches(&self, host_context: *mut c_void, selectors: &str) -> SelectorBooleanResult;
    unsafe fn webkit_matches_selector(
        &self,
        host_context: *mut c_void,
        selectors: &str,
    ) -> SelectorBooleanResult;
    unsafe fn query_selector_all(
        &self,
        host_context: *mut c_void,
        selectors: &str,
    ) -> SelectorNodeListResult;
}

#[repr(C)]
pub struct OptionalOwnedUtf8 {
    pub is_null: u8,
    pub value: OwnedUtf8,
}

#[repr(C)]
pub struct ElementHostVTable {
    pub get_local_name: Option<unsafe extern "C" fn(*mut c_void, *mut OwnedUtf8) -> u8>,
    pub get_tag_name: Option<unsafe extern "C" fn(*mut c_void, *mut OwnedUtf8) -> u8>,
    pub get_id: Option<unsafe extern "C" fn(*mut c_void, *mut OwnedUtf8) -> u8>,
    pub set_id: Option<unsafe extern "C" fn(*mut c_void, *mut c_void, *const u8, usize) -> u8>,
    pub get_class_name: Option<unsafe extern "C" fn(*mut c_void, *mut OwnedUtf8) -> u8>,
    pub set_class_name:
        Option<unsafe extern "C" fn(*mut c_void, *mut c_void, *const u8, usize) -> u8>,
    pub has_attributes: Option<unsafe extern "C" fn(*mut c_void, *mut u8) -> u8>,
    pub get_attribute: Option<
        unsafe extern "C" fn(
            *mut c_void,
            *mut c_void,
            *const u8,
            usize,
            *mut OptionalOwnedUtf8,
        ) -> u8,
    >,
    pub has_attribute:
        Option<unsafe extern "C" fn(*mut c_void, *mut c_void, *const u8, usize, *mut u8) -> u8>,
    pub get_node_type: Option<unsafe extern "C" fn(*mut c_void, *mut u16) -> u8>,
    pub get_node_name: Option<unsafe extern "C" fn(*mut c_void, *mut OwnedUtf8) -> u8>,
    pub get_is_connected: Option<unsafe extern "C" fn(*mut c_void, *mut u8) -> u8>,
    pub get_text_content: Option<unsafe extern "C" fn(*mut c_void, *mut OptionalOwnedUtf8) -> u8>,
    pub set_text_content:
        Option<unsafe extern "C" fn(*mut c_void, *mut c_void, u8, *const u8, usize) -> u8>,
    pub has_child_nodes: Option<unsafe extern "C" fn(*mut c_void, *mut u8) -> u8>,
    pub get_first_element_child:
        Option<unsafe extern "C" fn(*mut c_void, *mut RawInterfaceValue) -> u8>,
    pub get_last_element_child:
        Option<unsafe extern "C" fn(*mut c_void, *mut RawInterfaceValue) -> u8>,
    pub get_child_element_count: Option<unsafe extern "C" fn(*mut c_void, *mut u32) -> u8>,
    pub query_selector: Option<
        unsafe extern "C" fn(
            *mut c_void,
            *mut c_void,
            *const u8,
            usize,
            *mut RawSelectorElementOutcome,
        ) -> u8,
    >,
    pub closest: Option<
        unsafe extern "C" fn(
            *mut c_void,
            *mut c_void,
            *const u8,
            usize,
            *mut RawSelectorElementOutcome,
        ) -> u8,
    >,
    pub matches: Option<
        unsafe extern "C" fn(
            *mut c_void,
            *mut c_void,
            *const u8,
            usize,
            *mut RawSelectorBooleanOutcome,
        ) -> u8,
    >,
    pub webkit_matches_selector: Option<
        unsafe extern "C" fn(
            *mut c_void,
            *mut c_void,
            *const u8,
            usize,
            *mut RawSelectorBooleanOutcome,
        ) -> u8,
    >,
    pub query_selector_all: Option<
        unsafe extern "C" fn(
            *mut c_void,
            *mut c_void,
            *const u8,
            usize,
            *mut RawSelectorNodeListOutcome,
        ) -> u8,
    >,
    pub drop: Option<DropCallback>,
}

/// A realm-owned bridge to Servo's timer scheduler.
///
/// # Safety
///
/// Implementations and `Drop` must stay on the owning script thread, must not
/// unwind, re-enter V8, pump an event loop, or access the V8 sidecar `RefCell`.
/// `host_context` is valid only during the synchronous call that supplies it.
pub unsafe trait TimerHostBinding: Sized + 'static {
    fn schedule_function(
        &self,
        host_context: *mut c_void,
        callback_id: TimerCallbackId,
        timeout_ms: i32,
        is_interval: bool,
    ) -> Option<i32>;

    fn schedule_string(
        &self,
        host_context: *mut c_void,
        source: &str,
        timeout_ms: i32,
        is_interval: bool,
    ) -> Option<i32>;

    fn clear(&self, handle: i32);
}

/// A console logging level pinned to the experimental C ABI.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
#[repr(u32)]
pub enum ConsoleLevel {
    Debug = 0,
    Error = 1,
    Info = 2,
    Log = 3,
    Trace = 4,
    Warn = 5,
}

/// A realm-owned sink for the supported V8 console logging operations.
///
/// # Safety
///
/// Implementations and `Drop` must remain on the owning script thread, must
/// not unwind, re-enter V8, or pump an event loop. JavaScript values never
/// cross this trait: C++ formats them first and supplies only valid UTF-8.
pub unsafe trait ConsoleHostBinding: Sized + 'static {
    fn write(&self, level: ConsoleLevel, message: &str);
}

#[repr(C)]
struct TimerHostVTable {
    schedule_function: Option<
        unsafe extern "C" fn(*mut c_void, *mut c_void, TimerCallbackId, i32, u8, *mut i32) -> u8,
    >,
    schedule_string: Option<
        unsafe extern "C" fn(*mut c_void, *mut c_void, *const u8, usize, i32, u8, *mut i32) -> u8,
    >,
    clear: Option<unsafe extern "C" fn(*mut c_void, i32)>,
    drop: Option<DropCallback>,
}

unsafe extern "C" fn timer_host_schedule_function<T: TimerHostBinding>(
    native: *mut c_void,
    host_context: *mut c_void,
    callback_id: TimerCallbackId,
    timeout_ms: i32,
    is_interval: u8,
    handle: *mut i32,
) -> u8 {
    if native.is_null() || handle.is_null() || is_interval > 1 {
        return 0;
    }
    // SAFETY: The timer-host vtable contract supplies this exact live Box<T>.
    let native = unsafe { &*native.cast::<T>() };
    let Some(value) =
        native.schedule_function(host_context, callback_id, timeout_ms, is_interval != 0)
    else {
        return 0;
    };
    // SAFETY: handle is non-null and points to caller-owned writable storage.
    unsafe { *handle = value };
    1
}

unsafe extern "C" fn timer_host_schedule_string<T: TimerHostBinding>(
    native: *mut c_void,
    host_context: *mut c_void,
    source: *const u8,
    source_length: usize,
    timeout_ms: i32,
    is_interval: u8,
    handle: *mut i32,
) -> u8 {
    if native.is_null()
        || handle.is_null()
        || is_interval > 1
        || (source.is_null() && source_length != 0)
    {
        return 0;
    }
    let bytes = if source_length == 0 {
        &[]
    } else {
        // SAFETY: C++ keeps this UTF-8 allocation live for the synchronous call.
        unsafe { std::slice::from_raw_parts(source, source_length) }
    };
    let Ok(source) = std::str::from_utf8(bytes) else {
        return 0;
    };
    // SAFETY: The timer-host vtable contract supplies this exact live Box<T>.
    let native = unsafe { &*native.cast::<T>() };
    let Some(value) = native.schedule_string(host_context, source, timeout_ms, is_interval != 0)
    else {
        return 0;
    };
    // SAFETY: handle is non-null and points to caller-owned writable storage.
    unsafe { *handle = value };
    1
}

unsafe extern "C" fn timer_host_clear<T: TimerHostBinding>(native: *mut c_void, handle: i32) {
    if native.is_null() {
        return;
    }
    // SAFETY: The timer-host vtable contract supplies this exact live Box<T>.
    unsafe { &*native.cast::<T>() }.clear(handle);
}

unsafe extern "C" fn timer_host_drop<T: TimerHostBinding>(native: *mut c_void) {
    // SAFETY: The bridge hands back exactly the Box<T> it consumed, once.
    drop(unsafe { Box::from_raw(native.cast::<T>()) });
}

impl TimerHostVTable {
    fn for_type<T: TimerHostBinding>() -> Self {
        Self {
            schedule_function: Some(timer_host_schedule_function::<T>),
            schedule_string: Some(timer_host_schedule_string::<T>),
            clear: Some(timer_host_clear::<T>),
            drop: Some(timer_host_drop::<T>),
        }
    }
}

#[repr(C)]
struct ConsoleHostVTable {
    write: Option<unsafe extern "C" fn(*mut c_void, u32, *const u8, usize)>,
    drop: Option<DropCallback>,
}

unsafe extern "C" fn console_host_write<T: ConsoleHostBinding>(
    native: *mut c_void,
    level: u32,
    message: *const u8,
    message_length: usize,
) {
    if native.is_null() || (message.is_null() && message_length != 0) {
        return;
    }
    let level = match level {
        0 => ConsoleLevel::Debug,
        1 => ConsoleLevel::Error,
        2 => ConsoleLevel::Info,
        3 => ConsoleLevel::Log,
        4 => ConsoleLevel::Trace,
        5 => ConsoleLevel::Warn,
        _ => return,
    };
    let bytes = if message_length == 0 {
        &[]
    } else {
        // SAFETY: C++ keeps the message allocation live for this synchronous
        // callback and supplies its exact byte length.
        unsafe { std::slice::from_raw_parts(message, message_length) }
    };
    let Ok(message) = std::str::from_utf8(bytes) else {
        return;
    };
    // SAFETY: The console-host vtable contract supplies this exact live Box<T>.
    unsafe { &*native.cast::<T>() }.write(level, message);
}

unsafe extern "C" fn console_host_drop<T: ConsoleHostBinding>(native: *mut c_void) {
    // SAFETY: The bridge hands back exactly the Box<T> it consumed, once.
    drop(unsafe { Box::from_raw(native.cast::<T>()) });
}

impl ConsoleHostVTable {
    fn for_type<T: ConsoleHostBinding>() -> Self {
        Self {
            write: Some(console_host_write::<T>),
            drop: Some(console_host_drop::<T>),
        }
    }
}

unsafe fn element_host_write_owned_utf8(output: *mut OwnedUtf8, value: String) -> u8 {
    if output.is_null() {
        return 0;
    }
    let owner = Box::new(value.into_bytes());
    // SAFETY: output is non-null and points to caller-owned writable storage.
    unsafe {
        *output = OwnedUtf8 {
            data: owner.as_ptr(),
            length: owner.len(),
            owner: Box::into_raw(owner).cast::<c_void>(),
            drop_owner: Some(document_host_owned_utf8_drop),
        };
    }
    1
}

unsafe fn element_host_utf8<'a>(value: *const u8, value_length: usize) -> Option<&'a str> {
    if value.is_null() && value_length != 0 {
        return None;
    }
    let bytes = if value_length == 0 {
        &[]
    } else {
        // SAFETY: The ABI lends this exact byte range for the synchronous call.
        unsafe { std::slice::from_raw_parts(value, value_length) }
    };
    std::str::from_utf8(bytes).ok()
}

macro_rules! element_host_string_getter {
    ($function:ident, $method:ident) => {
        unsafe extern "C" fn $function<T: ElementHostBinding>(
            native: *mut c_void,
            output: *mut OwnedUtf8,
        ) -> u8 {
            if native.is_null() {
                return 0;
            }
            // SAFETY: The vtable contract supplies this exact live Box<T>.
            let value = unsafe { &*native.cast::<T>() }.$method();
            // SAFETY: The C++ caller owns writable output storage.
            unsafe { element_host_write_owned_utf8(output, value) }
        }
    };
}

element_host_string_getter!(element_host_get_local_name, local_name);
element_host_string_getter!(element_host_get_tag_name, tag_name);
element_host_string_getter!(element_host_get_id, id);
element_host_string_getter!(element_host_get_class_name, class_name);
element_host_string_getter!(element_host_get_node_name, node_name);

unsafe fn element_host_write_optional_owned_utf8(
    output: *mut OptionalOwnedUtf8,
    value: Option<String>,
) -> u8 {
    if output.is_null() {
        return 0;
    }
    let mut owned = OwnedUtf8 {
        data: std::ptr::null(),
        length: 0,
        owner: std::ptr::null_mut(),
        drop_owner: None,
    };
    let is_null = value.is_none();
    if let Some(value) = value {
        // SAFETY: `owned` is caller-owned writable storage in this frame.
        if unsafe { element_host_write_owned_utf8(&mut owned, value) } == 0 {
            return 0;
        }
    }
    // SAFETY: output is non-null and points to caller-owned writable storage.
    unsafe {
        *output = OptionalOwnedUtf8 {
            is_null: is_null as u8,
            value: owned,
        }
    };
    1
}

unsafe fn element_host_write_interface_value(
    output: *mut RawInterfaceValue,
    handle: Option<InterfaceHandle>,
) -> u8 {
    if output.is_null() {
        return 0;
    }
    // SAFETY: output is non-null and points to caller-owned writable storage.
    unsafe {
        *output = match handle {
            Some(handle) => RawInterfaceValue {
                is_null: 0,
                key: handle.key,
                native: handle.native,
            },
            None => RawInterfaceValue {
                is_null: 1,
                key: std::ptr::null(),
                native: std::ptr::null_mut(),
            },
        }
    };
    1
}

fn raw_selector_element_outcome(result: SelectorElementResult) -> RawSelectorElementOutcome {
    let (status, handle) = match result {
        SelectorElementResult::Match(handle) => (SELECTOR_RETURNED, handle),
        SelectorElementResult::SyntaxError => (SELECTOR_SYNTAX_ERROR, None),
        SelectorElementResult::HostFailure => (SELECTOR_HOST_FAILURE, None),
    };
    let value = match handle {
        Some(handle) => RawInterfaceValue {
            is_null: 0,
            key: handle.key,
            native: handle.native,
        },
        None => RawInterfaceValue {
            is_null: 1,
            key: std::ptr::null(),
            native: std::ptr::null_mut(),
        },
    };
    RawSelectorElementOutcome { status, value }
}

fn raw_selector_boolean_outcome(result: SelectorBooleanResult) -> RawSelectorBooleanOutcome {
    match result {
        SelectorBooleanResult::Match(value) => RawSelectorBooleanOutcome {
            status: SELECTOR_RETURNED,
            value: u8::from(value),
        },
        SelectorBooleanResult::SyntaxError => RawSelectorBooleanOutcome {
            status: SELECTOR_SYNTAX_ERROR,
            value: 0,
        },
        SelectorBooleanResult::HostFailure => RawSelectorBooleanOutcome {
            status: SELECTOR_HOST_FAILURE,
            value: 0,
        },
    }
}

fn raw_selector_node_list_outcome(result: SelectorNodeListResult) -> RawSelectorNodeListOutcome {
    match result {
        SelectorNodeListResult::Match(handle) => RawSelectorNodeListOutcome {
            status: SELECTOR_RETURNED,
            native: handle.native,
        },
        SelectorNodeListResult::SyntaxError => RawSelectorNodeListOutcome {
            status: SELECTOR_SYNTAX_ERROR,
            native: std::ptr::null_mut(),
        },
        SelectorNodeListResult::HostFailure => RawSelectorNodeListOutcome {
            status: SELECTOR_HOST_FAILURE,
            native: std::ptr::null_mut(),
        },
    }
}

unsafe extern "C" fn element_host_set_id<T: ElementHostBinding>(
    native: *mut c_void,
    host_context: *mut c_void,
    value: *const u8,
    value_length: usize,
) -> u8 {
    if native.is_null() || host_context.is_null() {
        return 0;
    }
    // SAFETY: The ABI lends this byte range for the synchronous call.
    let Some(value) = (unsafe { element_host_utf8(value, value_length) }) else {
        return 0;
    };
    // SAFETY: The vtable contract supplies this exact host and live context.
    // SAFETY: The live context is borrowed only for this synchronous call.
    unsafe { (&*native.cast::<T>()).set_id(host_context, value) as u8 }
}

unsafe extern "C" fn element_host_set_class_name<T: ElementHostBinding>(
    native: *mut c_void,
    host_context: *mut c_void,
    value: *const u8,
    value_length: usize,
) -> u8 {
    if native.is_null() || host_context.is_null() {
        return 0;
    }
    // SAFETY: The ABI lends this byte range for the synchronous call.
    let Some(value) = (unsafe { element_host_utf8(value, value_length) }) else {
        return 0;
    };
    // SAFETY: The vtable contract supplies this exact host and live context.
    // SAFETY: The live context is borrowed only for this synchronous call.
    unsafe { (&*native.cast::<T>()).set_class_name(host_context, value) as u8 }
}

unsafe extern "C" fn element_host_has_attributes<T: ElementHostBinding>(
    native: *mut c_void,
    output: *mut u8,
) -> u8 {
    if native.is_null() || output.is_null() {
        return 0;
    }
    // SAFETY: Both pointers satisfy the vtable contract.
    unsafe { *output = (&*native.cast::<T>()).has_attributes() as u8 };
    1
}

unsafe extern "C" fn element_host_get_attribute<T: ElementHostBinding>(
    native: *mut c_void,
    host_context: *mut c_void,
    name: *const u8,
    name_length: usize,
    output: *mut OptionalOwnedUtf8,
) -> u8 {
    if native.is_null() || host_context.is_null() || output.is_null() {
        return 0;
    }
    // SAFETY: The ABI lends this byte range for the synchronous call.
    let Some(name) = (unsafe { element_host_utf8(name, name_length) }) else {
        return 0;
    };
    // SAFETY: The vtable contract supplies this exact host and live context.
    let result = unsafe { (&*native.cast::<T>()).get_attribute(host_context, name) };
    let Ok(value) = result else {
        return 0;
    };
    // SAFETY: output is caller-owned writable storage.
    unsafe { element_host_write_optional_owned_utf8(output, value) }
}

unsafe extern "C" fn element_host_has_attribute<T: ElementHostBinding>(
    native: *mut c_void,
    host_context: *mut c_void,
    name: *const u8,
    name_length: usize,
    output: *mut u8,
) -> u8 {
    if native.is_null() || host_context.is_null() || output.is_null() {
        return 0;
    }
    // SAFETY: The ABI lends this byte range for the synchronous call.
    let Some(name) = (unsafe { element_host_utf8(name, name_length) }) else {
        return 0;
    };
    // SAFETY: The vtable contract supplies this exact host and live context.
    let result = unsafe { (&*native.cast::<T>()).has_attribute(host_context, name) };
    let Some(value) = result else {
        return 0;
    };
    // SAFETY: output is non-null and points to caller-owned writable storage.
    unsafe { *output = value as u8 };
    1
}

unsafe extern "C" fn element_host_get_node_type<T: ElementHostBinding>(
    native: *mut c_void,
    output: *mut u16,
) -> u8 {
    if native.is_null() || output.is_null() {
        return 0;
    }
    // SAFETY: Both pointers satisfy the vtable contract.
    unsafe { *output = (&*native.cast::<T>()).node_type() };
    1
}

unsafe extern "C" fn element_host_get_is_connected<T: ElementHostBinding>(
    native: *mut c_void,
    output: *mut u8,
) -> u8 {
    if native.is_null() || output.is_null() {
        return 0;
    }
    // SAFETY: Both pointers satisfy the vtable contract.
    unsafe { *output = (&*native.cast::<T>()).is_connected() as u8 };
    1
}

unsafe extern "C" fn element_host_get_text_content<T: ElementHostBinding>(
    native: *mut c_void,
    output: *mut OptionalOwnedUtf8,
) -> u8 {
    if native.is_null() || output.is_null() {
        return 0;
    }
    // SAFETY: The vtable contract supplies this exact live Box<T>.
    let value = unsafe { (&*native.cast::<T>()).text_content() };
    // SAFETY: output is caller-owned writable storage.
    unsafe { element_host_write_optional_owned_utf8(output, value) }
}

unsafe extern "C" fn element_host_set_text_content<T: ElementHostBinding>(
    native: *mut c_void,
    host_context: *mut c_void,
    is_null: u8,
    value: *const u8,
    value_length: usize,
) -> u8 {
    if native.is_null() || host_context.is_null() || is_null > 1 {
        return 0;
    }
    let value = if is_null != 0 {
        if !value.is_null() || value_length != 0 {
            return 0;
        }
        None
    } else {
        // SAFETY: The ABI lends this byte range for the synchronous call.
        let Some(value) = (unsafe { element_host_utf8(value, value_length) }) else {
            return 0;
        };
        Some(value)
    };
    // SAFETY: The vtable contract supplies this exact host and live context.
    unsafe { (&*native.cast::<T>()).set_text_content(host_context, value) as u8 }
}

unsafe extern "C" fn element_host_has_child_nodes<T: ElementHostBinding>(
    native: *mut c_void,
    output: *mut u8,
) -> u8 {
    if native.is_null() || output.is_null() {
        return 0;
    }
    // SAFETY: Both pointers satisfy the vtable contract.
    unsafe { *output = (&*native.cast::<T>()).has_child_nodes() as u8 };
    1
}

unsafe extern "C" fn element_host_get_first_element_child<T: ElementHostBinding>(
    native: *mut c_void,
    output: *mut RawInterfaceValue,
) -> u8 {
    if native.is_null() || output.is_null() {
        return 0;
    }
    // SAFETY: The vtable contract supplies this exact live Box<T>.
    let handle = unsafe { (&*native.cast::<T>()).first_element_child() };
    // SAFETY: output is caller-owned writable storage.
    unsafe { element_host_write_interface_value(output, handle) }
}

unsafe extern "C" fn element_host_get_last_element_child<T: ElementHostBinding>(
    native: *mut c_void,
    output: *mut RawInterfaceValue,
) -> u8 {
    if native.is_null() || output.is_null() {
        return 0;
    }
    // SAFETY: The vtable contract supplies this exact live Box<T>.
    let handle = unsafe { (&*native.cast::<T>()).last_element_child() };
    // SAFETY: output is caller-owned writable storage.
    unsafe { element_host_write_interface_value(output, handle) }
}

unsafe extern "C" fn element_host_get_child_element_count<T: ElementHostBinding>(
    native: *mut c_void,
    output: *mut u32,
) -> u8 {
    if native.is_null() || output.is_null() {
        return 0;
    }
    // SAFETY: Both pointers satisfy the vtable contract.
    unsafe { *output = (&*native.cast::<T>()).child_element_count() };
    1
}

unsafe extern "C" fn element_host_query_selector<T: ElementHostBinding>(
    native: *mut c_void,
    host_context: *mut c_void,
    selectors: *const u8,
    selectors_length: usize,
    output: *mut RawSelectorElementOutcome,
) -> u8 {
    if native.is_null()
        || host_context.is_null()
        || output.is_null()
        || (selectors.is_null() && selectors_length != 0)
    {
        return 0;
    }
    // SAFETY: The ABI lends this byte range for the synchronous call.
    let Some(selectors) = (unsafe { element_host_utf8(selectors, selectors_length) }) else {
        return 0;
    };
    // SAFETY: The vtable contract supplies this exact host and live context.
    let result = unsafe { (&*native.cast::<T>()).query_selector(host_context, selectors) };
    // SAFETY: output is non-null and points to caller-owned writable storage.
    unsafe { *output = raw_selector_element_outcome(result) };
    1
}

unsafe extern "C" fn element_host_closest<T: ElementHostBinding>(
    native: *mut c_void,
    host_context: *mut c_void,
    selectors: *const u8,
    selectors_length: usize,
    output: *mut RawSelectorElementOutcome,
) -> u8 {
    if native.is_null()
        || host_context.is_null()
        || output.is_null()
        || (selectors.is_null() && selectors_length != 0)
    {
        return 0;
    }
    // SAFETY: The ABI lends this byte range for the synchronous call.
    let Some(selectors) = (unsafe { element_host_utf8(selectors, selectors_length) }) else {
        return 0;
    };
    // SAFETY: The vtable contract supplies this exact host and live context.
    let result = unsafe { (&*native.cast::<T>()).closest(host_context, selectors) };
    // SAFETY: output is non-null and points to caller-owned writable storage.
    unsafe { *output = raw_selector_element_outcome(result) };
    1
}

unsafe extern "C" fn element_host_matches<T: ElementHostBinding>(
    native: *mut c_void,
    host_context: *mut c_void,
    selectors: *const u8,
    selectors_length: usize,
    output: *mut RawSelectorBooleanOutcome,
) -> u8 {
    if native.is_null()
        || host_context.is_null()
        || output.is_null()
        || (selectors.is_null() && selectors_length != 0)
    {
        return 0;
    }
    // SAFETY: The ABI lends this byte range for the synchronous call.
    let Some(selectors) = (unsafe { element_host_utf8(selectors, selectors_length) }) else {
        return 0;
    };
    // SAFETY: The vtable contract supplies this exact host and live context.
    let result = unsafe { (&*native.cast::<T>()).matches(host_context, selectors) };
    // SAFETY: output is non-null and points to caller-owned writable storage.
    unsafe { *output = raw_selector_boolean_outcome(result) };
    1
}

unsafe extern "C" fn element_host_webkit_matches_selector<T: ElementHostBinding>(
    native: *mut c_void,
    host_context: *mut c_void,
    selectors: *const u8,
    selectors_length: usize,
    output: *mut RawSelectorBooleanOutcome,
) -> u8 {
    if native.is_null()
        || host_context.is_null()
        || output.is_null()
        || (selectors.is_null() && selectors_length != 0)
    {
        return 0;
    }
    // SAFETY: The ABI lends this byte range for the synchronous call.
    let Some(selectors) = (unsafe { element_host_utf8(selectors, selectors_length) }) else {
        return 0;
    };
    // SAFETY: The vtable contract supplies this exact host and live context.
    let result = unsafe { (&*native.cast::<T>()).webkit_matches_selector(host_context, selectors) };
    // SAFETY: output is non-null and points to caller-owned writable storage.
    unsafe { *output = raw_selector_boolean_outcome(result) };
    1
}

unsafe extern "C" fn element_host_query_selector_all<T: ElementHostBinding>(
    native: *mut c_void,
    host_context: *mut c_void,
    selectors: *const u8,
    selectors_length: usize,
    output: *mut RawSelectorNodeListOutcome,
) -> u8 {
    if native.is_null()
        || host_context.is_null()
        || output.is_null()
        || (selectors.is_null() && selectors_length != 0)
    {
        return 0;
    }
    // SAFETY: The ABI lends this byte range for the synchronous call.
    let Some(selectors) = (unsafe { element_host_utf8(selectors, selectors_length) }) else {
        return 0;
    };
    // SAFETY: The vtable contract supplies this exact host and live context.
    let result = unsafe { (&*native.cast::<T>()).query_selector_all(host_context, selectors) };
    // SAFETY: output is non-null and points to caller-owned writable storage.
    unsafe { *output = raw_selector_node_list_outcome(result) };
    1
}

unsafe extern "C" fn node_list_host_get_length<T: NodeListHostBinding>(
    native: *mut c_void,
    output: *mut u32,
) -> u8 {
    if native.is_null() || output.is_null() {
        return 0;
    }
    // SAFETY: The vtable contract supplies this exact live Box<T>.
    unsafe { *output = (&*native.cast::<T>()).length() };
    1
}

unsafe extern "C" fn node_list_host_item<T: NodeListHostBinding>(
    native: *mut c_void,
    index: u32,
    output: *mut RawInterfaceValue,
) -> u8 {
    if native.is_null() || output.is_null() {
        return 0;
    }
    // SAFETY: The vtable contract supplies this exact live Box<T>.
    let item = unsafe { (&*native.cast::<T>()).item(index) };
    // SAFETY: output is caller-owned writable storage.
    unsafe { element_host_write_interface_value(output, item) }
}

unsafe extern "C" fn node_list_host_drop<T: NodeListHostBinding>(native: *mut c_void) {
    if native.is_null() {
        return;
    }
    // SAFETY: The bridge returns the exact Box<T> it consumed, once.
    drop(unsafe { Box::from_raw(native.cast::<T>()) });
}

fn node_list_host_vtable<T: NodeListHostBinding>() -> NodeListHostVTable {
    NodeListHostVTable {
        get_length: Some(node_list_host_get_length::<T>),
        item: Some(node_list_host_item::<T>),
        drop: Some(node_list_host_drop::<T>),
    }
}

fn element_host_vtable<T: ElementHostBinding>() -> ElementHostVTable {
    ElementHostVTable {
        get_local_name: Some(element_host_get_local_name::<T>),
        get_tag_name: Some(element_host_get_tag_name::<T>),
        get_id: Some(element_host_get_id::<T>),
        set_id: Some(element_host_set_id::<T>),
        get_class_name: Some(element_host_get_class_name::<T>),
        set_class_name: Some(element_host_set_class_name::<T>),
        has_attributes: Some(element_host_has_attributes::<T>),
        get_attribute: Some(element_host_get_attribute::<T>),
        has_attribute: Some(element_host_has_attribute::<T>),
        get_node_type: Some(element_host_get_node_type::<T>),
        get_node_name: Some(element_host_get_node_name::<T>),
        get_is_connected: Some(element_host_get_is_connected::<T>),
        get_text_content: Some(element_host_get_text_content::<T>),
        set_text_content: Some(element_host_set_text_content::<T>),
        has_child_nodes: Some(element_host_has_child_nodes::<T>),
        get_first_element_child: Some(element_host_get_first_element_child::<T>),
        get_last_element_child: Some(element_host_get_last_element_child::<T>),
        get_child_element_count: Some(element_host_get_child_element_count::<T>),
        query_selector: Some(element_host_query_selector::<T>),
        closest: Some(element_host_closest::<T>),
        matches: Some(element_host_matches::<T>),
        webkit_matches_selector: Some(element_host_webkit_matches_selector::<T>),
        query_selector_all: Some(element_host_query_selector_all::<T>),
        drop: Some(element_host_drop::<T>),
    }
}

unsafe extern "C" fn element_host_drop<T: ElementHostBinding>(native: *mut c_void) {
    if native.is_null() {
        return;
    }
    // SAFETY: The bridge hands back exactly the Box<T> it was given, once.
    drop(unsafe { Box::from_raw(native.cast::<T>()) });
}

include!(concat!(env!("OUT_DIR"), "/servo_v8_generated.rs"));
include!(concat!(
    env!("OUT_DIR"),
    "/servo_v8_document_host_generated.rs"
));

unsafe extern "C" {
    fn servo_v8_abi_version() -> u32;
    fn servo_v8_runtime_new(options: *const Options, error: *mut ErrorBuffer) -> *mut RawRuntime;
    fn servo_v8_runtime_delete(runtime: *mut RawRuntime);
    fn servo_v8_realm_create(
        runtime: *mut RawRuntime,
        realm_id: *mut RealmId,
        error: *mut ErrorBuffer,
    ) -> i32;
    fn servo_v8_realm_destroy(
        runtime: *mut RawRuntime,
        realm_id: RealmId,
        error: *mut ErrorBuffer,
    ) -> i32;
    fn servo_v8_realm_eval_bool(
        runtime: *mut RawRuntime,
        realm_id: RealmId,
        source: *const u8,
        source_length: usize,
        result: *mut u8,
        error: *mut ErrorBuffer,
    ) -> i32;
    fn servo_v8_realm_compile(
        runtime: *mut RawRuntime,
        realm_id: RealmId,
        source: *const u8,
        source_length: usize,
        resource_name: *const u8,
        resource_name_length: usize,
        line_number: u32,
        error: *mut ErrorBuffer,
    ) -> i32;
    fn servo_v8_realm_script_compile(
        runtime: *mut RawRuntime,
        realm_id: RealmId,
        source: *const u8,
        source_length: usize,
        resource_name: *const u8,
        resource_name_length: usize,
        line_number: u32,
        outcome: *mut RawScriptCompileOutcome,
        error: *mut ErrorBuffer,
    ) -> i32;
    fn servo_v8_realm_script_run(
        runtime: *mut RawRuntime,
        realm_id: RealmId,
        script_id: ScriptId,
        host_context: *mut c_void,
        outcome: *mut RawScriptRunOutcome,
        error: *mut ErrorBuffer,
    ) -> i32;
    fn servo_v8_realm_script_discard(
        runtime: *mut RawRuntime,
        realm_id: RealmId,
        script_id: ScriptId,
        error: *mut ErrorBuffer,
    ) -> i32;
    fn servo_v8_runtime_perform_microtask_checkpoint(
        runtime: *mut RawRuntime,
        host_context: *mut c_void,
        outcome: *mut RawScriptRunOutcome,
        error: *mut ErrorBuffer,
    ) -> i32;
    fn servo_v8_runtime_take_pending_job_error(
        runtime: *mut RawRuntime,
        realm_id: *mut RealmId,
        exception: *mut RawScriptException,
        has_error: *mut u8,
        error: *mut ErrorBuffer,
    ) -> i32;
    fn servo_v8_realm_install_document_host(
        runtime: *mut RawRuntime,
        realm_id: RealmId,
        native: *mut c_void,
        vtable: *const DocumentHostVTable,
        error: *mut ErrorBuffer,
    ) -> i32;
    fn servo_v8_realm_install_timer_host(
        runtime: *mut RawRuntime,
        realm_id: RealmId,
        native: *mut c_void,
        vtable: *const TimerHostVTable,
        error: *mut ErrorBuffer,
    ) -> i32;
    fn servo_v8_realm_install_console_host(
        runtime: *mut RawRuntime,
        realm_id: RealmId,
        native: *mut c_void,
        vtable: *const ConsoleHostVTable,
        error: *mut ErrorBuffer,
    ) -> i32;
    fn servo_v8_realm_timer_callback_run(
        runtime: *mut RawRuntime,
        realm_id: RealmId,
        callback_id: TimerCallbackId,
        host_context: *mut c_void,
        outcome: *mut RawScriptRunOutcome,
        error: *mut ErrorBuffer,
    ) -> i32;
    fn servo_v8_realm_timer_callback_clear(
        runtime: *mut RawRuntime,
        realm_id: RealmId,
        callback_id: TimerCallbackId,
        error: *mut ErrorBuffer,
    ) -> i32;
    fn servo_v8_realm_document_hidden(
        runtime: *mut RawRuntime,
        realm_id: RealmId,
        result: *mut u8,
        error: *mut ErrorBuffer,
    ) -> i32;
    fn servo_v8_install_element_host(
        runtime: *mut RawRuntime,
        vtable: *const ElementHostVTable,
        error: *mut ErrorBuffer,
    ) -> i32;
    fn servo_v8_install_node_list_host(
        runtime: *mut RawRuntime,
        vtable: *const NodeListHostVTable,
        error: *mut ErrorBuffer,
    ) -> i32;
    fn servo_v8_install_engine_binding_smoke(
        runtime: *mut RawRuntime,
        vtable: *const EngineBindingSmokeVTable,
        error: *mut ErrorBuffer,
    ) -> i32;
    fn servo_v8_eval_bool(
        runtime: *mut RawRuntime,
        source: *const u8,
        source_length: usize,
        result: *mut u8,
        error: *mut ErrorBuffer,
    ) -> i32;
    fn servo_v8_eval_i64(
        runtime: *mut RawRuntime,
        source: *const u8,
        source_length: usize,
        result: *mut i64,
        error: *mut ErrorBuffer,
    ) -> i32;
    fn servo_v8_compile(
        runtime: *mut RawRuntime,
        source: *const u8,
        source_length: usize,
        resource_name: *const u8,
        resource_name_length: usize,
        line_number: u32,
        error: *mut ErrorBuffer,
    ) -> i32;
    fn servo_v8_low_memory_notification(runtime: *mut RawRuntime);
    fn servo_v8_terminate_execution(runtime: *mut RawRuntime);
    #[cfg(test)]
    fn servo_v8_collect_garbage_for_testing(runtime: *mut RawRuntime);
    #[cfg(test)]
    fn servo_v8_realm_wrapper_cache_size_for_testing(
        runtime: *mut RawRuntime,
        realm_id: RealmId,
        result: *mut usize,
        error: *mut ErrorBuffer,
    ) -> i32;
    fn servo_v8_dom_cell_native(cell: *mut DomCell, expected_interface_id: u32) -> *mut c_void;
    fn servo_v8_trace_dom_cell(
        visitor: *mut TraceVisitor,
        cell: *mut DomCell,
        expected_interface_id: u32,
    );
}

#[derive(Debug)]
pub struct Error(String);

impl std::fmt::Display for Error {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(&self.0)
    }
}

impl std::error::Error for Error {}

fn error_buffer(storage: &mut [u8; ERROR_CAPACITY]) -> ErrorBuffer {
    ErrorBuffer {
        data: storage.as_mut_ptr(),
        capacity: storage.len(),
        length: 0,
    }
}

fn error_from(storage: &[u8; ERROR_CAPACITY], error: &ErrorBuffer) -> Error {
    Error(text_from(storage, error))
}

fn text_from(storage: &[u8; ERROR_CAPACITY], buffer: &ErrorBuffer) -> String {
    let length = buffer.length.min(storage.len().saturating_sub(1));
    String::from_utf8_lossy(&storage[..length]).into_owned()
}

pub struct Runtime {
    raw: NonNull<RawRuntime>,
    interrupt_state: Arc<InterruptState>,
    // V8 isolates and cppgc persistent handles are confined to their owner
    // thread. Rc is !Send + !Sync and costs no storage here.
    _thread_confined: PhantomData<Rc<()>>,
}

struct InterruptState {
    // Stored as an address so the synchronization primitive remains Send +
    // Sync without claiming that the owner-thread runtime itself is Send.
    // Holding this lock is the lifetime guard for a cross-thread termination
    // request; Runtime::drop clears it before deleting the native runtime.
    raw_address: Mutex<usize>,
}

/// A cloneable cross-thread request handle that can terminate active V8 code.
///
/// It does not make [`Runtime`] transferable. It exposes only V8's documented
/// thread-safe termination request and becomes inert when Runtime is dropped.
#[derive(Clone)]
pub struct InterruptHandle {
    state: Arc<InterruptState>,
}

impl InterruptHandle {
    /// Requests termination if the owning runtime is still live.
    pub fn terminate_execution(&self) -> bool {
        let raw_address = *self.state.raw_address.lock().unwrap();
        let Some(raw) = NonNull::new(raw_address as *mut RawRuntime) else {
            return false;
        };
        // SAFETY: Holding raw_address's lock prevents Runtime::drop from
        // deleting this native runtime until the thread-safe V8 request
        // returns.
        unsafe { servo_v8_terminate_execution(raw.as_ptr()) };
        true
    }
}

impl Runtime {
    pub fn new(options: Options) -> Result<Self, Error> {
        // SAFETY: This is a pure ABI version query with no preconditions.
        let actual_abi = unsafe { servo_v8_abi_version() };
        if actual_abi != ABI_VERSION {
            return Err(Error(format!(
                "Servo V8 ABI mismatch: Rust expects {ABI_VERSION}, C++ provides {actual_abi}"
            )));
        }

        let mut storage = [0; ERROR_CAPACITY];
        let mut error = error_buffer(&mut storage);
        // SAFETY: Both pointers remain valid for the duration of the call.
        let raw = unsafe { servo_v8_runtime_new(&options, &mut error) };
        let Some(raw) = NonNull::new(raw) else {
            return Err(error_from(&storage, &error));
        };
        let interrupt_state = Arc::new(InterruptState {
            raw_address: Mutex::new(raw.as_ptr() as usize),
        });
        Ok(Self {
            raw,
            interrupt_state,
            _thread_confined: PhantomData,
        })
    }

    pub fn interrupt_handle(&self) -> InterruptHandle {
        InterruptHandle {
            state: Arc::clone(&self.interrupt_state),
        }
    }

    /// Creates an independent context in this runtime's isolate.
    pub fn create_realm(&mut self) -> Result<RealmId, Error> {
        let mut storage = [0; ERROR_CAPACITY];
        let mut error = error_buffer(&mut storage);
        let mut realm_id = RealmId(0);
        // SAFETY: The output and error buffers remain valid for the call.
        let succeeded =
            unsafe { servo_v8_realm_create(self.raw.as_ptr(), &mut realm_id, &mut error) };
        if succeeded == 0 {
            return Err(error_from(&storage, &error));
        }
        Ok(realm_id)
    }

    /// Destroys a realm. Its ID is permanently invalid after this succeeds.
    pub fn destroy_realm(&mut self, realm_id: RealmId) -> Result<(), Error> {
        let mut storage = [0; ERROR_CAPACITY];
        let mut error = error_buffer(&mut storage);
        // SAFETY: The runtime is live and the error buffer is valid for the call.
        let succeeded = unsafe { servo_v8_realm_destroy(self.raw.as_ptr(), realm_id, &mut error) };
        if succeeded == 0 {
            return Err(error_from(&storage, &error));
        }
        Ok(())
    }

    /// Evaluates a boolean expression in a selected realm.
    pub fn eval_bool_in_realm(&mut self, realm_id: RealmId, source: &str) -> Result<bool, Error> {
        let mut storage = [0; ERROR_CAPACITY];
        let mut error = error_buffer(&mut storage);
        let mut result = 0;
        // SAFETY: Source, result, and error buffers remain valid for the call.
        let succeeded = unsafe {
            servo_v8_realm_eval_bool(
                self.raw.as_ptr(),
                realm_id,
                source.as_ptr(),
                source.len(),
                &mut result,
                &mut error,
            )
        };
        if succeeded == 0 {
            return Err(error_from(&storage, &error));
        }
        Ok(result != 0)
    }

    /// Compiles a classic script in a selected realm without executing it.
    pub fn compile_in_realm(
        &mut self,
        realm_id: RealmId,
        source: &str,
        resource_name: &str,
        line_number: u32,
    ) -> Result<(), Error> {
        let mut storage = [0; ERROR_CAPACITY];
        let mut error = error_buffer(&mut storage);
        // SAFETY: Both strings and the error buffer remain valid for the call.
        let succeeded = unsafe {
            servo_v8_realm_compile(
                self.raw.as_ptr(),
                realm_id,
                source.as_ptr(),
                source.len(),
                resource_name.as_ptr(),
                resource_name.len(),
                line_number,
                &mut error,
            )
        };
        if succeeded == 0 {
            return Err(error_from(&storage, &error));
        }
        Ok(())
    }

    /// Compiles and retains a classic script without executing it.
    pub fn compile_script_in_realm(
        &mut self,
        realm_id: RealmId,
        source: &str,
        resource_name: &str,
        line_number: u32,
    ) -> Result<ScriptCompileOutcome, Error> {
        let mut error_storage = [0; ERROR_CAPACITY];
        let mut message_storage = [0; ERROR_CAPACITY];
        let mut resource_storage = [0; ERROR_CAPACITY];
        let mut stack_storage = [0; ERROR_CAPACITY];
        let mut error = error_buffer(&mut error_storage);
        let mut outcome = RawScriptCompileOutcome {
            status: SCRIPT_COMPILED,
            script_id: ScriptId(0),
            exception: RawScriptException {
                message: error_buffer(&mut message_storage),
                resource_name: error_buffer(&mut resource_storage),
                stack: error_buffer(&mut stack_storage),
                line_number: 0,
                column_number: 0,
            },
        };
        // SAFETY: Both strings and every output buffer remain valid for the
        // duration of the call and have independent backing storage.
        let succeeded = unsafe {
            servo_v8_realm_script_compile(
                self.raw.as_ptr(),
                realm_id,
                source.as_ptr(),
                source.len(),
                resource_name.as_ptr(),
                resource_name.len(),
                line_number,
                &mut outcome,
                &mut error,
            )
        };
        if succeeded == 0 {
            return Err(error_from(&error_storage, &error));
        }
        match outcome.status {
            SCRIPT_COMPILED => Ok(ScriptCompileOutcome::Compiled(outcome.script_id)),
            SCRIPT_COMPILE_THROWN => Ok(ScriptCompileOutcome::ParseError(ScriptException {
                message: text_from(&message_storage, &outcome.exception.message),
                resource_name: text_from(&resource_storage, &outcome.exception.resource_name),
                stack: text_from(&stack_storage, &outcome.exception.stack),
                line_number: outcome.exception.line_number,
                column_number: outcome.exception.column_number,
            })),
            status => Err(Error(format!(
                "V8 returned unknown classic-script compile status {status}"
            ))),
        }
    }

    /// Executes and consumes a retained classic script.
    ///
    /// This deliberately does not perform a V8 microtask checkpoint. Servo's
    /// event-loop integration must request checkpoints at the HTML-defined
    /// task boundary once V8 jobs are connected to that event loop.
    pub fn run_script_in_realm(
        &mut self,
        realm_id: RealmId,
        script_id: ScriptId,
    ) -> Result<ScriptRunOutcome, Error> {
        // SAFETY: A null context disables host callbacks that require an
        // embedding-engine context.
        unsafe {
            self.run_script_in_realm_with_host_context(realm_id, script_id, std::ptr::null_mut())
        }
    }

    /// Executes and consumes a retained script with one ephemeral host context.
    ///
    /// # Safety
    ///
    /// `host_context` must remain valid for every synchronous native callback
    /// made during this invocation. The bridge clears it before returning and
    /// no generated binding may retain it.
    pub unsafe fn run_script_in_realm_with_host_context(
        &mut self,
        realm_id: RealmId,
        script_id: ScriptId,
        host_context: *mut c_void,
    ) -> Result<ScriptRunOutcome, Error> {
        let mut error_storage = [0; ERROR_CAPACITY];
        let mut message_storage = [0; ERROR_CAPACITY];
        let mut resource_storage = [0; ERROR_CAPACITY];
        let mut stack_storage = [0; ERROR_CAPACITY];
        let mut error = error_buffer(&mut error_storage);
        let mut outcome = RawScriptRunOutcome {
            status: SCRIPT_RUN_COMPLETED,
            exception: RawScriptException {
                message: error_buffer(&mut message_storage),
                resource_name: error_buffer(&mut resource_storage),
                stack: error_buffer(&mut stack_storage),
                line_number: 0,
                column_number: 0,
            },
        };
        // SAFETY: The runtime is live and the error buffer remains valid for
        // the duration of the call. Every outcome buffer has independent live
        // backing storage.
        let succeeded = unsafe {
            servo_v8_realm_script_run(
                self.raw.as_ptr(),
                realm_id,
                script_id,
                host_context,
                &mut outcome,
                &mut error,
            )
        };
        if succeeded == 0 {
            return Err(error_from(&error_storage, &error));
        }
        match outcome.status {
            SCRIPT_RUN_COMPLETED => Ok(ScriptRunOutcome::Completed),
            SCRIPT_RUN_THROWN => Ok(ScriptRunOutcome::Thrown(ScriptException {
                message: text_from(&message_storage, &outcome.exception.message),
                resource_name: text_from(&resource_storage, &outcome.exception.resource_name),
                stack: text_from(&stack_storage, &outcome.exception.stack),
                line_number: outcome.exception.line_number,
                column_number: outcome.exception.column_number,
            })),
            SCRIPT_RUN_TERMINATED => Ok(ScriptRunOutcome::Terminated),
            status => Err(Error(format!(
                "V8 returned unknown classic-script run status {status}"
            ))),
        }
    }

    /// Drains the isolate's explicit microtask queue with one ephemeral host
    /// context installed on every live realm.
    ///
    /// The queue is isolate-wide because V8 requires contexts that can access
    /// each other synchronously to share one queue, and same-origin Servo
    /// pipelines on one script thread do exactly that.
    ///
    /// Only termination is reported through the return value. A job that
    /// throws is buffered, because one drain can produce many errors; collect
    /// them with [`Runtime::take_pending_job_errors`]. An unhandled promise
    /// rejection is still silent and needs the promise-rejection callback.
    ///
    /// # Safety
    ///
    /// `host_context` must remain valid for every synchronous native callback
    /// made during the drain. The bridge clears it from every realm before
    /// returning and no generated binding may retain it.
    pub unsafe fn perform_microtask_checkpoint_with_host_context(
        &mut self,
        host_context: *mut c_void,
    ) -> Result<ScriptRunOutcome, Error> {
        let mut error_storage = [0; ERROR_CAPACITY];
        let mut message_storage = [0; ERROR_CAPACITY];
        let mut resource_storage = [0; ERROR_CAPACITY];
        let mut stack_storage = [0; ERROR_CAPACITY];
        let mut error = error_buffer(&mut error_storage);
        let mut outcome = RawScriptRunOutcome {
            status: SCRIPT_RUN_COMPLETED,
            exception: RawScriptException {
                message: error_buffer(&mut message_storage),
                resource_name: error_buffer(&mut resource_storage),
                stack: error_buffer(&mut stack_storage),
                line_number: 0,
                column_number: 0,
            },
        };
        // SAFETY: The runtime is live and the error buffer remains valid for
        // the duration of the call. Every outcome buffer has independent live
        // backing storage.
        let succeeded = unsafe {
            servo_v8_runtime_perform_microtask_checkpoint(
                self.raw.as_ptr(),
                host_context,
                &mut outcome,
                &mut error,
            )
        };
        if succeeded == 0 {
            return Err(error_from(&error_storage, &error));
        }
        match outcome.status {
            SCRIPT_RUN_COMPLETED => Ok(ScriptRunOutcome::Completed),
            SCRIPT_RUN_THROWN => Ok(ScriptRunOutcome::Thrown(ScriptException {
                message: text_from(&message_storage, &outcome.exception.message),
                resource_name: text_from(&resource_storage, &outcome.exception.resource_name),
                stack: text_from(&stack_storage, &outcome.exception.stack),
                line_number: outcome.exception.line_number,
                column_number: outcome.exception.column_number,
            })),
            SCRIPT_RUN_TERMINATED => Ok(ScriptRunOutcome::Terminated),
            status => Err(Error(format!(
                "V8 returned unknown microtask checkpoint status {status}"
            ))),
        }
    }

    /// Drains the isolate's explicit microtask queue with no host context, so
    /// jobs that call an embedding host fail deterministically.
    pub fn perform_microtask_checkpoint(&mut self) -> Result<ScriptRunOutcome, Error> {
        // SAFETY: A null context disables host callbacks that require an
        // embedding-engine context.
        unsafe { self.perform_microtask_checkpoint_with_host_context(std::ptr::null_mut()) }
    }

    /// Collects every uncaught error thrown by a microtask job, oldest first.
    ///
    /// V8 catches a throwing job inside its own microtask builtin, reports the
    /// message, and lets execution continue, so these never reach a `TryCatch`
    /// at the checkpoint boundary and must be pulled instead.
    pub fn take_pending_job_errors(&mut self) -> Result<Vec<JobError>, Error> {
        let mut errors = Vec::new();
        loop {
            let mut error_storage = [0; ERROR_CAPACITY];
            let mut message_storage = [0; ERROR_CAPACITY];
            let mut resource_storage = [0; ERROR_CAPACITY];
            let mut stack_storage = [0; ERROR_CAPACITY];
            let mut error = error_buffer(&mut error_storage);
            let mut exception = RawScriptException {
                message: error_buffer(&mut message_storage),
                resource_name: error_buffer(&mut resource_storage),
                stack: error_buffer(&mut stack_storage),
                line_number: 0,
                column_number: 0,
            };
            let mut has_error = 0u8;
            let mut realm_id = RealmId(0);
            // SAFETY: The runtime is live and every output buffer has
            // independent live backing storage for the duration of the call.
            let succeeded = unsafe {
                servo_v8_runtime_take_pending_job_error(
                    self.raw.as_ptr(),
                    &mut realm_id,
                    &mut exception,
                    &mut has_error,
                    &mut error,
                )
            };
            if succeeded == 0 {
                return Err(error_from(&error_storage, &error));
            }
            if has_error == 0 {
                return Ok(errors);
            }
            errors.push(JobError {
                realm_id: (realm_id != RealmId(0)).then_some(realm_id),
                exception: ScriptException {
                    message: text_from(&message_storage, &exception.message),
                    resource_name: text_from(&resource_storage, &exception.resource_name),
                    stack: text_from(&stack_storage, &exception.stack),
                    line_number: exception.line_number,
                    column_number: exception.column_number,
                },
            });
        }
    }

    /// Registers how the bridge talks to an `Element` host, once per runtime.
    ///
    /// The vtable is type-level; the hosts it describes are per DOM object and
    /// are handed over one at a time by an interface-typed getter.
    pub fn install_element_host<T: ElementHostBinding>(&mut self) -> Result<(), Error> {
        let vtable = element_host_vtable::<T>();
        let mut storage = [0; ERROR_CAPACITY];
        let mut error = error_buffer(&mut storage);
        // SAFETY: The runtime is live and both the vtable and error buffer
        // remain valid for the duration of the call.
        let succeeded =
            unsafe { servo_v8_install_element_host(self.raw.as_ptr(), &vtable, &mut error) };
        if succeeded == 0 {
            return Err(error_from(&storage, &error));
        }
        Ok(())
    }

    /// Registers the type-level host for static NodeLists returned by
    /// querySelectorAll. Each individual list is transferred separately.
    pub fn install_node_list_host<T: NodeListHostBinding>(&mut self) -> Result<(), Error> {
        let vtable = node_list_host_vtable::<T>();
        let mut storage = [0; ERROR_CAPACITY];
        let mut error = error_buffer(&mut storage);
        // SAFETY: The runtime is live and C++ copies the complete vtable
        // synchronously before this method returns.
        let succeeded = unsafe {
            servo_v8_install_node_list_host(self.raw.as_ptr(), &vtable, &mut error)
        };
        if succeeded == 0 {
            return Err(error_from(&storage, &error));
        }
        Ok(())
    }

    /// Discards a retained classic script without executing it.
    pub fn discard_script_in_realm(
        &mut self,
        realm_id: RealmId,
        script_id: ScriptId,
    ) -> Result<(), Error> {
        let mut storage = [0; ERROR_CAPACITY];
        let mut error = error_buffer(&mut storage);
        // SAFETY: The runtime is live and the error buffer remains valid for
        // the duration of the call.
        let succeeded = unsafe {
            servo_v8_realm_script_discard(self.raw.as_ptr(), realm_id, script_id, &mut error)
        };
        if succeeded == 0 {
            return Err(error_from(&storage, &error));
        }
        Ok(())
    }

    /// Installs a realm-owned host for the selected production `Document`
    /// bindings. After successful installation, the native host is destroyed
    /// synchronously when its realm or runtime is destroyed. Failed
    /// installation leaves ownership in Rust and drops the host here.
    pub fn install_document_host<T: DocumentHostBinding>(
        &mut self,
        realm_id: RealmId,
        host: T,
    ) -> Result<(), Error> {
        let vtable = DocumentHostVTable::for_type::<T>();
        let native = Box::into_raw(Box::new(host)).cast();
        let mut storage = [0; ERROR_CAPACITY];
        let mut error = error_buffer(&mut storage);
        // SAFETY: native is one live Box<T>. The generated vtable is complete.
        // The C ABI consumes native only when it returns success.
        let succeeded = unsafe {
            servo_v8_realm_install_document_host(
                self.raw.as_ptr(),
                realm_id,
                native,
                &vtable,
                &mut error,
            )
        };
        if succeeded == 0 {
            // SAFETY: C++ leaves native untouched on every failure path, so it
            // is still the exact Box<T> allocated above.
            drop(unsafe { Box::from_raw(native.cast::<T>()) });
            return Err(error_from(&storage, &error));
        }
        Ok(())
    }

    /// Installs one realm-owned host that connects V8 timer operations to the
    /// embedder's scheduler. Failed installation leaves ownership in Rust.
    pub fn install_timer_host<T: TimerHostBinding>(
        &mut self,
        realm_id: RealmId,
        host: T,
    ) -> Result<(), Error> {
        let vtable = TimerHostVTable::for_type::<T>();
        let native = Box::into_raw(Box::new(host)).cast();
        let mut storage = [0; ERROR_CAPACITY];
        let mut error = error_buffer(&mut storage);
        // SAFETY: native is one live Box<T>; C++ consumes it only on success.
        let succeeded = unsafe {
            servo_v8_realm_install_timer_host(
                self.raw.as_ptr(),
                realm_id,
                native,
                &vtable,
                &mut error,
            )
        };
        if succeeded == 0 {
            // SAFETY: Every C++ failure path leaves native untouched.
            drop(unsafe { Box::from_raw(native.cast::<T>()) });
            return Err(error_from(&storage, &error));
        }
        Ok(())
    }

    /// Installs one realm-owned sink for the supported console namespace
    /// logging operations. Failed installation leaves ownership in Rust.
    pub fn install_console_host<T: ConsoleHostBinding>(
        &mut self,
        realm_id: RealmId,
        host: T,
    ) -> Result<(), Error> {
        let vtable = ConsoleHostVTable::for_type::<T>();
        let native = Box::into_raw(Box::new(host)).cast();
        let mut storage = [0; ERROR_CAPACITY];
        let mut error = error_buffer(&mut storage);
        // SAFETY: native is one live Box<T>; C++ consumes it only on success.
        let succeeded = unsafe {
            servo_v8_realm_install_console_host(
                self.raw.as_ptr(),
                realm_id,
                native,
                &vtable,
                &mut error,
            )
        };
        if succeeded == 0 {
            // SAFETY: Every C++ failure path leaves native untouched.
            drop(unsafe { Box::from_raw(native.cast::<T>()) });
            return Err(error_from(&storage, &error));
        }
        Ok(())
    }

    /// Invokes a retained timer function with no embedding host context.
    pub fn run_timer_callback_in_realm(
        &mut self,
        realm_id: RealmId,
        callback_id: TimerCallbackId,
    ) -> Result<ScriptRunOutcome, Error> {
        // SAFETY: A null context disables embedding callbacks that need one.
        unsafe {
            self.run_timer_callback_in_realm_with_host_context(
                realm_id,
                callback_id,
                std::ptr::null_mut(),
            )
        }
    }

    /// Invokes a retained timer function with one ephemeral host context.
    ///
    /// # Safety
    ///
    /// `host_context` must remain valid for every synchronous native callback
    /// made during this invocation and may not be retained by a host.
    pub unsafe fn run_timer_callback_in_realm_with_host_context(
        &mut self,
        realm_id: RealmId,
        callback_id: TimerCallbackId,
        host_context: *mut c_void,
    ) -> Result<ScriptRunOutcome, Error> {
        let mut error_storage = [0; ERROR_CAPACITY];
        let mut message_storage = [0; ERROR_CAPACITY];
        let mut resource_storage = [0; ERROR_CAPACITY];
        let mut stack_storage = [0; ERROR_CAPACITY];
        let mut error = error_buffer(&mut error_storage);
        let mut outcome = RawScriptRunOutcome {
            status: SCRIPT_RUN_COMPLETED,
            exception: RawScriptException {
                message: error_buffer(&mut message_storage),
                resource_name: error_buffer(&mut resource_storage),
                stack: error_buffer(&mut stack_storage),
                line_number: 0,
                column_number: 0,
            },
        };
        // SAFETY: The runtime and every independent output buffer stay live.
        let succeeded = unsafe {
            servo_v8_realm_timer_callback_run(
                self.raw.as_ptr(),
                realm_id,
                callback_id,
                host_context,
                &mut outcome,
                &mut error,
            )
        };
        if succeeded == 0 {
            return Err(error_from(&error_storage, &error));
        }
        match outcome.status {
            SCRIPT_RUN_COMPLETED => Ok(ScriptRunOutcome::Completed),
            SCRIPT_RUN_THROWN => Ok(ScriptRunOutcome::Thrown(ScriptException {
                message: text_from(&message_storage, &outcome.exception.message),
                resource_name: text_from(&resource_storage, &outcome.exception.resource_name),
                stack: text_from(&stack_storage, &outcome.exception.stack),
                line_number: outcome.exception.line_number,
                column_number: outcome.exception.column_number,
            })),
            SCRIPT_RUN_TERMINATED => Ok(ScriptRunOutcome::Terminated),
            status => Err(Error(format!(
                "V8 returned unknown timer callback run status {status}"
            ))),
        }
    }

    /// Releases one retained timer function without invoking it. Clearing an
    /// already-fired or already-cleared callback is a successful no-op.
    pub fn clear_timer_callback_in_realm(
        &mut self,
        realm_id: RealmId,
        callback_id: TimerCallbackId,
    ) -> Result<(), Error> {
        let mut storage = [0; ERROR_CAPACITY];
        let mut error = error_buffer(&mut storage);
        // SAFETY: The runtime is live and the error buffer remains valid.
        let succeeded = unsafe {
            servo_v8_realm_timer_callback_clear(
                self.raw.as_ptr(),
                realm_id,
                callback_id,
                &mut error,
            )
        };
        if succeeded == 0 {
            return Err(error_from(&storage, &error));
        }
        Ok(())
    }

    /// Reads `document.hidden` through the installed V8 native accessor.
    pub fn document_hidden(&mut self, realm_id: RealmId) -> Result<bool, Error> {
        let mut storage = [0; ERROR_CAPACITY];
        let mut error = error_buffer(&mut storage);
        let mut result = 0;
        // SAFETY: result and the error buffer remain valid for the call.
        let succeeded = unsafe {
            servo_v8_realm_document_hidden(self.raw.as_ptr(), realm_id, &mut result, &mut error)
        };
        if succeeded == 0 {
            return Err(error_from(&storage, &error));
        }
        Ok(result != 0)
    }

    /// Installs the generated constructor/getter/setter/method binding.
    pub fn install_engine_binding_smoke<T: EngineBindingSmokeBinding>(
        &mut self,
    ) -> Result<(), Error> {
        let vtable = EngineBindingSmokeVTable::for_type::<T>();
        // SAFETY: The generated table contains monomorphized callbacks for T,
        // whose unsafe trait contract establishes the FFI invariants.
        unsafe { self.install_engine_binding_smoke_vtable(vtable) }
    }

    /// # Safety
    ///
    /// Every callback must obey its signature, must not unwind, and must stay
    /// callable until this runtime is dropped. `constructor` transfers one
    /// native allocation to the C++ `DomCell`; `drop` must destroy that exact
    /// allocation once. All other callbacks receive that same pointer.
    unsafe fn install_engine_binding_smoke_vtable(
        &mut self,
        vtable: EngineBindingSmokeVTable,
    ) -> Result<(), Error> {
        let mut storage = [0; ERROR_CAPACITY];
        let mut error = error_buffer(&mut storage);
        // SAFETY: The caller establishes callback validity. C++ copies the
        // table, and the runtime pointer is owned by self on this thread.
        let succeeded = unsafe {
            servo_v8_install_engine_binding_smoke(self.raw.as_ptr(), &vtable, &mut error)
        };
        if succeeded == 0 {
            return Err(error_from(&storage, &error));
        }
        Ok(())
    }

    pub fn eval_bool(&mut self, source: &str) -> Result<bool, Error> {
        let mut storage = [0; ERROR_CAPACITY];
        let mut error = error_buffer(&mut storage);
        let mut result = 0;
        // SAFETY: Source, result, and error buffers are valid for the call.
        let succeeded = unsafe {
            servo_v8_eval_bool(
                self.raw.as_ptr(),
                source.as_ptr(),
                source.len(),
                &mut result,
                &mut error,
            )
        };
        if succeeded == 0 {
            return Err(error_from(&storage, &error));
        }
        Ok(result != 0)
    }

    pub fn eval_i64(&mut self, source: &str) -> Result<i64, Error> {
        let mut storage = [0; ERROR_CAPACITY];
        let mut error = error_buffer(&mut storage);
        let mut result = 0;
        // SAFETY: Source, result, and error buffers are valid for the call.
        let succeeded = unsafe {
            servo_v8_eval_i64(
                self.raw.as_ptr(),
                source.as_ptr(),
                source.len(),
                &mut result,
                &mut error,
            )
        };
        if succeeded == 0 {
            return Err(error_from(&storage, &error));
        }
        Ok(result)
    }

    /// Compiles a classic script without executing it.
    pub fn compile(
        &mut self,
        source: &str,
        resource_name: &str,
        line_number: u32,
    ) -> Result<(), Error> {
        let mut storage = [0; ERROR_CAPACITY];
        let mut error = error_buffer(&mut storage);
        // SAFETY: Both strings and the error buffer remain valid for the call.
        let succeeded = unsafe {
            servo_v8_compile(
                self.raw.as_ptr(),
                source.as_ptr(),
                source.len(),
                resource_name.as_ptr(),
                resource_name.len(),
                line_number,
                &mut error,
            )
        };
        if succeeded == 0 {
            return Err(error_from(&storage, &error));
        }
        Ok(())
    }

    pub fn low_memory_notification(&mut self) {
        // SAFETY: The runtime is live and !Send keeps this call on its owner
        // thread.
        unsafe { servo_v8_low_memory_notification(self.raw.as_ptr()) }
    }

    #[cfg(test)]
    fn collect_garbage_for_testing(&mut self) {
        // SAFETY: Tests create this runtime with expose_gc, and Runtime's
        // thread confinement keeps the request on the isolate owner thread.
        unsafe { servo_v8_collect_garbage_for_testing(self.raw.as_ptr()) }
    }

    #[cfg(test)]
    fn wrapper_cache_size_for_testing(&mut self, realm_id: RealmId) -> Result<usize, Error> {
        let mut storage = [0; ERROR_CAPACITY];
        let mut error = error_buffer(&mut storage);
        let mut result = 0;
        // SAFETY: The result and error storage remain writable for the call;
        // Runtime's thread confinement keeps inspection on the owner thread.
        let succeeded = unsafe {
            servo_v8_realm_wrapper_cache_size_for_testing(
                self.raw.as_ptr(),
                realm_id,
                &mut result,
                &mut error,
            )
        };
        if succeeded == 0 {
            return Err(error_from(&storage, &error));
        }
        Ok(result)
    }
}

impl Drop for Runtime {
    fn drop(&mut self) {
        let raw_address = {
            let mut raw_address = self.interrupt_state.raw_address.lock().unwrap();
            std::mem::take(&mut *raw_address)
        };
        debug_assert_eq!(raw_address, self.raw.as_ptr() as usize);
        // SAFETY: Clearing the shared address made every InterruptHandle inert
        // and waited for any in-flight termination request. Runtime owns this
        // exact pointer and destroys it once on its owner thread.
        unsafe { servo_v8_runtime_delete(raw_address as *mut RawRuntime) }
    }
}

/// Reports a native DOM edge during a V8 cppgc trace callback.
///
/// # Safety
///
/// `visitor` must be the live visitor passed to the current trace callback,
/// `cell` must be a live cell from the same runtime's CppHeap, and
/// `expected_interface_id` must identify the cell's generated interface.
pub unsafe fn trace_dom_cell(
    visitor: *mut TraceVisitor,
    cell: *mut DomCell,
    expected_interface_id: u32,
) {
    // SAFETY: The caller upholds the V8 tracing lifetime and heap invariants.
    unsafe { servo_v8_trace_dom_cell(visitor, cell, expected_interface_id) }
}

#[cfg(test)]
mod tests {
    use std::cell::{Cell, RefCell};
    use std::rc::Rc;
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::thread;
    use std::time::Duration;

    use super::*;

    static DROPS: AtomicUsize = AtomicUsize::new(0);
    thread_local! {
        static CALLBACK_REENTRY_RUNTIME: Cell<*mut RawRuntime> = Cell::new(std::ptr::null_mut());
        static CALLBACK_REENTRY_ATTEMPTS: RefCell<Vec<(&'static str, i32, String)>> =
            const { RefCell::new(Vec::new()) };
    }

    struct CallbackReentryConfig;

    impl CallbackReentryConfig {
        fn new(runtime: *mut RawRuntime) -> Self {
            CALLBACK_REENTRY_RUNTIME.with(|slot| {
                assert!(slot.replace(runtime).is_null());
            });
            CALLBACK_REENTRY_ATTEMPTS.with(|attempts| attempts.borrow_mut().clear());
            Self
        }
    }

    impl Drop for CallbackReentryConfig {
        fn drop(&mut self) {
            CALLBACK_REENTRY_RUNTIME.with(|slot| slot.set(std::ptr::null_mut()));
        }
    }

    fn attempt_callback_reentry(phase: &'static str) {
        CALLBACK_REENTRY_RUNTIME.with(|runtime| {
            let runtime = runtime.get();
            if runtime.is_null() {
                return;
            }
            let source = b"true";
            let mut storage = [0; ERROR_CAPACITY];
            let mut error = error_buffer(&mut storage);
            let mut result = 0;
            // SAFETY: Each test callback receives a still-live runtime. The
            // C++ callback scope must reject this nested entry before V8 is
            // touched, including while cppgc is tracing or sweeping.
            let succeeded = unsafe {
                servo_v8_eval_bool(
                    runtime,
                    source.as_ptr(),
                    source.len(),
                    &mut result,
                    &mut error,
                )
            };
            CALLBACK_REENTRY_ATTEMPTS.with(|attempts| {
                attempts
                    .borrow_mut()
                    .push((phase, succeeded, text_from(&storage, &error)));
            });
        });
    }

    fn compiled(result: Result<ScriptCompileOutcome, Error>) -> ScriptId {
        match result.unwrap() {
            ScriptCompileOutcome::Compiled(script_id) => script_id,
            ScriptCompileOutcome::ParseError(exception) => {
                panic!("unexpected V8 parse error: {exception:?}")
            },
        }
    }

    struct NativeSmoke {
        value: i32,
        child: Cell<Option<EngineBindingSmokeHandle>>,
    }

    struct CallbackReentrySmoke {
        value: i32,
    }

    impl Drop for CallbackReentrySmoke {
        fn drop(&mut self) {
            attempt_callback_reentry("drop");
        }
    }

    // SAFETY: Every attempted nested runtime entry is expected to be rejected
    // by the surrounding C++ callback scope. The callbacks do not unwind, and
    // this probe stores no outgoing cppgc edges.
    unsafe impl EngineBindingSmokeBinding for CallbackReentrySmoke {
        fn constructor(value: i32) -> Option<Self> {
            attempt_callback_reentry("constructor");
            Some(Self { value })
        }

        fn value(&self) -> i32 {
            attempt_callback_reentry("getter");
            self.value
        }

        fn set_value(&mut self, value: i32) {
            attempt_callback_reentry("setter");
            self.value = value;
        }

        fn add(&self, rhs: i32) -> i32 {
            attempt_callback_reentry("method");
            self.value.wrapping_add(rhs)
        }

        fn set_child(&self, _child: EngineBindingSmokeHandle) -> i32 {
            self.value
        }

        fn child_value(&self) -> i32 {
            self.value
        }

        unsafe fn trace(&self, _visitor: *mut TraceVisitor) {
            attempt_callback_reentry("trace");
        }
    }

    #[derive(Clone)]
    struct ElementProbeChild {
        identity: Rc<u8>,
        local_name: String,
        tag_name: String,
        state: Rc<ElementProbeState>,
    }

    #[derive(Default)]
    struct ElementProbeState {
        attributes: RefCell<Vec<(String, String)>>,
        text_content: RefCell<Option<String>>,
        has_child_nodes: Cell<bool>,
        is_connected: Cell<bool>,
        element_children: RefCell<Vec<ElementProbeChild>>,
    }

    impl ElementProbeState {
        fn with_attributes(attributes: &[(&str, &str)]) -> Rc<Self> {
            Self::with_node(attributes, "", false)
        }

        fn with_node(
            attributes: &[(&str, &str)],
            text_content: &str,
            has_child_nodes: bool,
        ) -> Rc<Self> {
            Rc::new(Self {
                attributes: RefCell::new(
                    attributes
                        .iter()
                        .map(|(name, value)| ((*name).to_owned(), (*value).to_owned()))
                        .collect(),
                ),
                text_content: RefCell::new(Some(text_content.to_owned())),
                has_child_nodes: Cell::new(has_child_nodes),
                is_connected: Cell::new(true),
                element_children: RefCell::new(Vec::new()),
            })
        }

        fn get(&self, name: &str) -> Option<String> {
            let name = name.to_ascii_lowercase();
            self.attributes
                .borrow()
                .iter()
                .find(|(attribute, _)| attribute == &name)
                .map(|(_, value)| value.clone())
        }

        fn set(&self, name: &str, value: &str) {
            if let Some((_, current)) = self
                .attributes
                .borrow_mut()
                .iter_mut()
                .find(|(attribute, _)| attribute == name)
            {
                *current = value.to_owned();
                return;
            }
            self.attributes
                .borrow_mut()
                .push((name.to_owned(), value.to_owned()));
        }
    }

    /// A stand-in for one Servo Element.
    struct ElementHostProbe {
        local_name: String,
        tag_name: String,
        identity: *const c_void,
        state: Rc<ElementProbeState>,
        // Child probes own their stand-in DOM identity through the host. The
        // top-level identities remain owned by DocumentHostProbe instead.
        _owned_identity: Option<Rc<u8>>,
        drops: Rc<Cell<usize>>,
        drop_reentry: Option<ElementDropReentryProbe>,
    }

    #[derive(Clone)]
    struct NodeListProbeItem {
        local_name: String,
        tag_name: String,
        identity: *const c_void,
        state: Rc<ElementProbeState>,
        owned_identity: Option<Rc<u8>>,
        element_drops: Rc<Cell<usize>>,
        drop_reentry: Option<ElementDropReentryProbe>,
    }

    impl NodeListProbeItem {
        fn interface_handle(&self) -> InterfaceHandle {
            // SAFETY: The list snapshot either owns the Rc used as its key or
            // shares the identity rooted by the realm's Document probe.
            unsafe {
                InterfaceHandle::new(
                    self.identity,
                    ElementHostProbe {
                        local_name: self.local_name.clone(),
                        tag_name: self.tag_name.clone(),
                        identity: self.identity,
                        state: Rc::clone(&self.state),
                        _owned_identity: self.owned_identity.clone(),
                        drops: Rc::clone(&self.element_drops),
                        drop_reentry: self.drop_reentry.clone(),
                    },
                )
            }
        }
    }

    struct NodeListHostProbe {
        items: Vec<NodeListProbeItem>,
        drops: Option<Rc<Cell<usize>>>,
    }

    impl Drop for NodeListHostProbe {
        fn drop(&mut self) {
            if let Some(drops) = &self.drops {
                drops.set(drops.get() + 1);
            }
        }
    }

    // SAFETY: The probe and every rooted snapshot item remain confined to the
    // runtime's owner thread and neither item() nor Drop can enter V8.
    unsafe impl NodeListHostBinding for NodeListHostProbe {
        fn length(&self) -> u32 {
            self.items.len() as u32
        }

        fn item(&self, index: u32) -> Option<InterfaceHandle> {
            self.items.get(index as usize).map(|item| item.interface_handle())
        }
    }

    #[derive(Clone)]
    struct ElementDropReentryProbe {
        runtime: *mut RawRuntime,
        realm: RealmId,
        attempts: Rc<RefCell<Vec<(i32, String)>>>,
    }

    impl Drop for ElementHostProbe {
        fn drop(&mut self) {
            if let Some(probe) = &self.drop_reentry {
                let source = b"true";
                let mut storage = [0; ERROR_CAPACITY];
                let mut error = error_buffer(&mut storage);
                let mut result = 0;
                // SAFETY: This deliberately hostile test-only Drop attempts
                // to enter its still-live runtime. The C++ callback-depth
                // guard must reject it before touching the isolate.
                let succeeded = unsafe {
                    servo_v8_realm_eval_bool(
                        probe.runtime,
                        probe.realm,
                        source.as_ptr(),
                        source.len(),
                        &mut result,
                        &mut error,
                    )
                };
                probe
                    .attempts
                    .borrow_mut()
                    .push((succeeded, text_from(&storage, &error)));
            }
            self.drops.set(self.drops.get() + 1);
        }
    }

    // SAFETY: The probe stays on its Runtime's thread and cannot unwind. Its
    // optional test-only Drop probe is rejected by the bridge before it can
    // actually re-enter V8.
    unsafe impl ElementHostBinding for ElementHostProbe {
        fn local_name(&self) -> String {
            self.local_name.clone()
        }

        fn tag_name(&self) -> String {
            self.tag_name.clone()
        }

        fn id(&self) -> String {
            self.state.get("id").unwrap_or_default()
        }

        unsafe fn set_id(&self, host_context: *mut c_void, value: &str) -> bool {
            assert!(!host_context.is_null());
            self.state.set("id", value);
            true
        }

        fn class_name(&self) -> String {
            self.state.get("class").unwrap_or_default()
        }

        unsafe fn set_class_name(&self, host_context: *mut c_void, value: &str) -> bool {
            assert!(!host_context.is_null());
            self.state.set("class", value);
            true
        }

        fn has_attributes(&self) -> bool {
            !self.state.attributes.borrow().is_empty()
        }

        unsafe fn get_attribute(
            &self,
            host_context: *mut c_void,
            name: &str,
        ) -> Result<Option<String>, ()> {
            assert!(!host_context.is_null());
            Ok(self.state.get(name))
        }

        unsafe fn has_attribute(&self, host_context: *mut c_void, name: &str) -> Option<bool> {
            assert!(!host_context.is_null());
            Some(self.state.get(name).is_some())
        }

        fn node_type(&self) -> u16 {
            1
        }

        fn node_name(&self) -> String {
            self.tag_name.clone()
        }

        fn is_connected(&self) -> bool {
            self.state.is_connected.get()
        }

        fn text_content(&self) -> Option<String> {
            self.state.text_content.borrow().clone()
        }

        unsafe fn set_text_content(&self, host_context: *mut c_void, value: Option<&str>) -> bool {
            assert!(!host_context.is_null());
            let value = value.unwrap_or_default();
            *self.state.text_content.borrow_mut() = Some(value.to_owned());
            self.state.has_child_nodes.set(!value.is_empty());
            self.state.element_children.borrow_mut().clear();
            true
        }

        fn has_child_nodes(&self) -> bool {
            self.state.has_child_nodes.get()
        }

        fn first_element_child(&self) -> Option<InterfaceHandle> {
            self.element_child(0)
        }

        fn last_element_child(&self) -> Option<InterfaceHandle> {
            let index = self.state.element_children.borrow().len().checked_sub(1)?;
            self.element_child(index)
        }

        fn child_element_count(&self) -> u32 {
            self.state.element_children.borrow().len() as u32
        }

        unsafe fn query_selector(
            &self,
            host_context: *mut c_void,
            selectors: &str,
        ) -> SelectorElementResult {
            assert!(!host_context.is_null());
            if selectors == "[" || selectors.is_empty() {
                return SelectorElementResult::SyntaxError;
            }
            let children = self.state.element_children.borrow();
            let index = children.iter().position(|child| {
                selectors.eq_ignore_ascii_case(&child.local_name)
                    || selectors
                        .strip_prefix('#')
                        .is_some_and(|id| child.state.get("id").as_deref() == Some(id))
            });
            drop(children);
            SelectorElementResult::Match(index.and_then(|index| self.element_child(index)))
        }

        unsafe fn closest(
            &self,
            host_context: *mut c_void,
            selectors: &str,
        ) -> SelectorElementResult {
            assert!(!host_context.is_null());
            match self.selector_matches(selectors) {
                Err(()) => SelectorElementResult::SyntaxError,
                Ok(true) => SelectorElementResult::Match(Some(self.interface_handle())),
                Ok(false) => SelectorElementResult::Match(None),
            }
        }

        unsafe fn matches(
            &self,
            host_context: *mut c_void,
            selectors: &str,
        ) -> SelectorBooleanResult {
            assert!(!host_context.is_null());
            match self.selector_matches(selectors) {
                Ok(value) => SelectorBooleanResult::Match(value),
                Err(()) => SelectorBooleanResult::SyntaxError,
            }
        }

        unsafe fn webkit_matches_selector(
            &self,
            host_context: *mut c_void,
            selectors: &str,
        ) -> SelectorBooleanResult {
            // SAFETY: This historical alias has the exact same WebIDL and
            // host-context contract as matches().
            unsafe { self.matches(host_context, selectors) }
        }

        unsafe fn query_selector_all(
            &self,
            host_context: *mut c_void,
            selectors: &str,
        ) -> SelectorNodeListResult {
            assert!(!host_context.is_null());
            if selectors == "[" || selectors.is_empty() {
                return SelectorNodeListResult::SyntaxError;
            }
            let items = self
                .state
                .element_children
                .borrow()
                .iter()
                .filter(|child| {
                    selectors == "*"
                        || selectors.eq_ignore_ascii_case(&child.local_name)
                        || selectors
                            .strip_prefix('#')
                            .is_some_and(|id| child.state.get("id").as_deref() == Some(id))
                })
                .map(|child| NodeListProbeItem {
                    local_name: child.local_name.clone(),
                    tag_name: child.tag_name.clone(),
                    identity: Rc::as_ptr(&child.identity).cast(),
                    state: Rc::clone(&child.state),
                    owned_identity: Some(Rc::clone(&child.identity)),
                    element_drops: Rc::clone(&self.drops),
                    drop_reentry: self.drop_reentry.clone(),
                })
                .collect();
            // SAFETY: Every test runtime that exposes this probe installs
            // NodeListHostProbe as its one type-level collection host.
            SelectorNodeListResult::Match(unsafe {
                NodeListHandle::new(NodeListHostProbe { items, drops: None })
            })
        }
    }

    impl ElementHostProbe {
        fn selector_matches(&self, selectors: &str) -> Result<bool, ()> {
            if selectors == "[" || selectors.is_empty() {
                return Err(());
            }
            Ok(selectors.eq_ignore_ascii_case(&self.local_name)
                || selectors.eq_ignore_ascii_case(&self.tag_name)
                || selectors
                    .strip_prefix('#')
                    .is_some_and(|id| self.state.get("id").as_deref() == Some(id))
                || selectors.strip_prefix('.').is_some_and(|class| {
                    self.state
                        .get("class")
                        .is_some_and(|classes| classes.split_ascii_whitespace().any(|c| c == class))
                }))
        }

        fn interface_handle(&self) -> InterfaceHandle {
            // SAFETY: identity names the same stand-in DOM allocation rooted
            // by this cloned probe host for the lifetime of the wrapper.
            unsafe {
                InterfaceHandle::new(
                    self.identity,
                    ElementHostProbe {
                        local_name: self.local_name.clone(),
                        tag_name: self.tag_name.clone(),
                        identity: self.identity,
                        state: Rc::clone(&self.state),
                        _owned_identity: self._owned_identity.clone(),
                        drops: Rc::clone(&self.drops),
                        drop_reentry: self.drop_reentry.clone(),
                    },
                )
            }
        }

        fn element_child(&self, index: usize) -> Option<InterfaceHandle> {
            let child = self.state.element_children.borrow().get(index)?.clone();
            // SAFETY: The returned host owns the Rc allocation used as its key
            // and shares the state that stands in for the rooted Servo Element.
            Some(unsafe {
                InterfaceHandle::new(
                    Rc::as_ptr(&child.identity).cast::<c_void>(),
                    ElementHostProbe {
                        local_name: child.local_name,
                        tag_name: child.tag_name,
                        identity: Rc::as_ptr(&child.identity).cast::<c_void>(),
                        state: child.state,
                        _owned_identity: Some(child.identity),
                        drops: Rc::clone(&self.drops),
                        drop_reentry: self.drop_reentry.clone(),
                    },
                )
            })
        }
    }

    #[derive(Clone, Debug, Eq, PartialEq)]
    struct ScheduledTimerFunction {
        callback_id: TimerCallbackId,
        timeout_ms: i32,
        is_interval: bool,
        host_context: usize,
        handle: i32,
    }

    #[derive(Clone, Debug, Eq, PartialEq)]
    struct ScheduledTimerString {
        source: String,
        timeout_ms: i32,
        is_interval: bool,
        host_context: usize,
        handle: i32,
    }

    struct TimerHostProbe {
        functions: Rc<RefCell<Vec<ScheduledTimerFunction>>>,
        strings: Rc<RefCell<Vec<ScheduledTimerString>>>,
        clears: Rc<RefCell<Vec<i32>>>,
        drops: Rc<Cell<usize>>,
        next_handle: Cell<i32>,
    }

    impl TimerHostProbe {
        fn new(
            functions: Rc<RefCell<Vec<ScheduledTimerFunction>>>,
            strings: Rc<RefCell<Vec<ScheduledTimerString>>>,
            clears: Rc<RefCell<Vec<i32>>>,
            drops: Rc<Cell<usize>>,
        ) -> Self {
            Self {
                functions,
                strings,
                clears,
                drops,
                next_handle: Cell::new(1),
            }
        }

        fn allocate_handle(&self) -> i32 {
            let handle = self.next_handle.get();
            self.next_handle.set(handle + 1);
            handle
        }
    }

    impl Drop for TimerHostProbe {
        fn drop(&mut self) {
            attempt_callback_reentry("timer host drop");
            self.drops.set(self.drops.get() + 1);
        }
    }

    // SAFETY: The probe is thread-confined, only records POD/string copies,
    // cannot unwind, and never re-enters V8 or retains host_context.
    unsafe impl TimerHostBinding for TimerHostProbe {
        fn schedule_function(
            &self,
            host_context: *mut c_void,
            callback_id: TimerCallbackId,
            timeout_ms: i32,
            is_interval: bool,
        ) -> Option<i32> {
            attempt_callback_reentry("timer schedule function");
            let handle = self.allocate_handle();
            self.functions.borrow_mut().push(ScheduledTimerFunction {
                callback_id,
                timeout_ms,
                is_interval,
                host_context: host_context as usize,
                handle,
            });
            Some(handle)
        }

        fn schedule_string(
            &self,
            host_context: *mut c_void,
            source: &str,
            timeout_ms: i32,
            is_interval: bool,
        ) -> Option<i32> {
            attempt_callback_reentry("timer schedule string");
            let handle = self.allocate_handle();
            self.strings.borrow_mut().push(ScheduledTimerString {
                source: source.to_owned(),
                timeout_ms,
                is_interval,
                host_context: host_context as usize,
                handle,
            });
            Some(handle)
        }

        fn clear(&self, handle: i32) {
            attempt_callback_reentry("timer clear");
            self.clears.borrow_mut().push(handle);
        }
    }

    struct ConsoleHostProbe {
        messages: Rc<RefCell<Vec<(ConsoleLevel, String)>>>,
        drops: Rc<Cell<usize>>,
        attempt_reentry: bool,
    }

    impl Drop for ConsoleHostProbe {
        fn drop(&mut self) {
            if self.attempt_reentry {
                attempt_callback_reentry("console host drop");
            }
            self.drops.set(self.drops.get() + 1);
        }
    }

    // SAFETY: The probe is owner-thread confined, copies only POD and UTF-8,
    // and its deliberately hostile re-entry attempts must be rejected by the
    // surrounding C++ RustCallbackScope.
    unsafe impl ConsoleHostBinding for ConsoleHostProbe {
        fn write(&self, level: ConsoleLevel, message: &str) {
            if self.attempt_reentry {
                attempt_callback_reentry("console write");
            }
            self.messages.borrow_mut().push((level, message.to_owned()));
        }
    }

    struct DocumentHostProbe {
        hidden: Rc<Cell<bool>>,
        bg_color: Rc<RefCell<String>>,
        title: Rc<RefCell<String>>,
        getter_calls: Rc<Cell<usize>>,
        bg_color_getter_calls: Rc<Cell<usize>>,
        bg_color_setter_calls: Rc<Cell<usize>>,
        drops: Rc<Cell<usize>>,
        /// Stands in for the address of a Servo Element the host would root.
        /// Stable for this probe's lifetime, which is what identity needs.
        element_identity: Box<u8>,
        head_identity: Box<u8>,
        id_element_identity: Box<u8>,
        element_state: Rc<ElementProbeState>,
        head_state: Rc<ElementProbeState>,
        id_element_state: Rc<ElementProbeState>,
        document_element_present: bool,
        head_present: bool,
        get_element_by_id_calls: Rc<RefCell<Vec<String>>>,
        element_drops: Rc<Cell<usize>>,
        node_list_drops: Rc<Cell<usize>>,
        element_drop_reentry: Option<ElementDropReentryProbe>,
    }

    impl DocumentHostProbe {
        fn new(
            hidden: Rc<Cell<bool>>,
            getter_calls: Rc<Cell<usize>>,
            drops: Rc<Cell<usize>>,
        ) -> Self {
            let id_element_state = ElementProbeState::with_node(
                &[
                    ("id", "target"),
                    ("class", "alpha beta"),
                    ("data-proof", "present"),
                ],
                "probe text",
                true,
            );
            id_element_state.element_children.borrow_mut().extend([
                ElementProbeChild {
                    identity: Rc::new(0),
                    local_name: "span".to_owned(),
                    tag_name: "SPAN".to_owned(),
                    state: ElementProbeState::with_node(&[("id", "first-child")], "first", false),
                },
                ElementProbeChild {
                    identity: Rc::new(0),
                    local_name: "em".to_owned(),
                    tag_name: "EM".to_owned(),
                    state: ElementProbeState::with_node(&[("id", "last-child")], "last", false),
                },
            ]);
            Self {
                hidden,
                bg_color: Rc::new(RefCell::new("red".to_owned())),
                title: Rc::new(RefCell::new("probe title".to_owned())),
                getter_calls,
                bg_color_getter_calls: Rc::new(Cell::new(0)),
                bg_color_setter_calls: Rc::new(Cell::new(0)),
                drops,
                element_identity: Box::new(0),
                head_identity: Box::new(0),
                id_element_identity: Box::new(0),
                element_state: ElementProbeState::with_attributes(&[]),
                head_state: ElementProbeState::with_attributes(&[]),
                id_element_state,
                document_element_present: true,
                head_present: true,
                get_element_by_id_calls: Rc::new(RefCell::new(Vec::new())),
                element_drops: Rc::new(Cell::new(0)),
                node_list_drops: Rc::new(Cell::new(0)),
                element_drop_reentry: None,
            }
        }
    }

    impl Drop for DocumentHostProbe {
        fn drop(&mut self) {
            self.drops.set(self.drops.get() + 1);
        }
    }

    // SAFETY: The getter is thread-confined with its Runtime, cannot unwind,
    // and neither the getter nor Drop re-enters V8 or pumps the event loop.
    unsafe impl DocumentHostBinding for DocumentHostProbe {
        fn hidden(&self) -> bool {
            self.getter_calls.set(self.getter_calls.get() + 1);
            self.hidden.get()
        }

        fn bg_color(&self) -> String {
            self.bg_color_getter_calls
                .set(self.bg_color_getter_calls.get() + 1);
            self.bg_color.borrow().clone()
        }

        fn url(&self) -> String {
            // A lone surrogate cannot survive a Rust String, so the USVString
            // guarantee is met by construction rather than by conversion.
            "https://example.com/probe?q=\u{2713}".to_owned()
        }

        fn document_uri(&self) -> String {
            "https://example.com/probe?q=\u{2713}".to_owned()
        }

        fn compat_mode(&self) -> String {
            "CSS1Compat".to_owned()
        }

        fn character_set(&self) -> String {
            "UTF-8".to_owned()
        }

        fn charset(&self) -> String {
            "UTF-8".to_owned()
        }

        fn input_encoding(&self) -> String {
            "UTF-8".to_owned()
        }

        fn content_type(&self) -> String {
            "text/html".to_owned()
        }

        fn referrer(&self) -> String {
            String::new()
        }

        fn last_modified(&self) -> String {
            "01/02/2026 03:04:05".to_owned()
        }

        fn visibility_state(&self) -> String {
            // Mirrors Servo's enum-to-string, which is what crosses the ABI.
            if self.hidden.get() {
                "hidden"
            } else {
                "visible"
            }
            .to_owned()
        }

        fn ready_state(&self) -> String {
            "complete".to_owned()
        }

        fn title(&self) -> String {
            self.title.borrow().clone()
        }

        fn node_type(&self) -> u16 {
            // Node.DOCUMENT_NODE.
            9
        }

        fn document_element(&self) -> Option<InterfaceHandle> {
            if !self.document_element_present {
                return None;
            }
            // SAFETY: `element_identity` lives as long as this host, which is
            // what the probe stands in for -- a real host roots its element.
            Some(unsafe {
                InterfaceHandle::new(
                    (&*self.element_identity as *const u8).cast::<c_void>(),
                    ElementHostProbe {
                        local_name: "html".to_owned(),
                        tag_name: "HTML".to_owned(),
                        identity: (&*self.element_identity as *const u8).cast(),
                        state: Rc::clone(&self.element_state),
                        _owned_identity: None,
                        drops: Rc::clone(&self.element_drops),
                        drop_reentry: self.element_drop_reentry.clone(),
                    },
                )
            })
        }

        fn head(&self) -> Option<InterfaceHandle> {
            if !self.head_present {
                return None;
            }
            // SAFETY: `head_identity` stands in for a distinct, rooted
            // HTMLHeadElement whose inherited Element facade reports HEAD.
            Some(unsafe {
                InterfaceHandle::new(
                    (&*self.head_identity as *const u8).cast::<c_void>(),
                    ElementHostProbe {
                        local_name: "head".to_owned(),
                        tag_name: "HEAD".to_owned(),
                        identity: (&*self.head_identity as *const u8).cast(),
                        state: Rc::clone(&self.head_state),
                        _owned_identity: None,
                        drops: Rc::clone(&self.element_drops),
                        drop_reentry: self.element_drop_reentry.clone(),
                    },
                )
            })
        }

        fn first_element_child(&self) -> Option<InterfaceHandle> {
            self.document_element()
        }

        fn last_element_child(&self) -> Option<InterfaceHandle> {
            self.document_element()
        }

        fn child_element_count(&self) -> u32 {
            u32::from(self.document_element_present)
        }

        unsafe fn get_element_by_id(
            &self,
            host_context: *mut c_void,
            element_id: &str,
        ) -> Option<InterfaceHandle> {
            assert!(!host_context.is_null());
            self.get_element_by_id_calls
                .borrow_mut()
                .push(element_id.to_owned());
            if element_id != "target" {
                return None;
            }
            // SAFETY: `id_element_identity` stands in for the rooted Element
            // returned for the probe's one known id.
            Some(unsafe {
                InterfaceHandle::new(
                    (&*self.id_element_identity as *const u8).cast::<c_void>(),
                    ElementHostProbe {
                        local_name: "div".to_owned(),
                        tag_name: "DIV".to_owned(),
                        identity: (&*self.id_element_identity as *const u8).cast(),
                        state: Rc::clone(&self.id_element_state),
                        _owned_identity: None,
                        drops: Rc::clone(&self.element_drops),
                        drop_reentry: self.element_drop_reentry.clone(),
                    },
                )
            })
        }

        unsafe fn query_selector(
            &self,
            host_context: *mut c_void,
            selectors: &str,
        ) -> SelectorElementResult {
            assert!(!host_context.is_null());
            match selectors {
                "[" | "" => SelectorElementResult::SyntaxError,
                "#target" | "div" => SelectorElementResult::Match(
                    // SAFETY: The probe's host context and identity remain
                    // live for the synchronous call.
                    unsafe { self.get_element_by_id(host_context, "target") },
                ),
                "html" => SelectorElementResult::Match(self.document_element()),
                "head" => SelectorElementResult::Match(self.head()),
                _ => SelectorElementResult::Match(None),
            }
        }

        unsafe fn query_selector_all(
            &self,
            host_context: *mut c_void,
            selectors: &str,
        ) -> SelectorNodeListResult {
            assert!(!host_context.is_null());
            if selectors == "[" || selectors.is_empty() {
                return SelectorNodeListResult::SyntaxError;
            }
            let mut items = Vec::new();
            let mut push = |identity: *const c_void,
                            local_name: &str,
                            tag_name: &str,
                            state: &Rc<ElementProbeState>| {
                items.push(NodeListProbeItem {
                    local_name: local_name.to_owned(),
                    tag_name: tag_name.to_owned(),
                    identity,
                    state: Rc::clone(state),
                    owned_identity: None,
                    element_drops: Rc::clone(&self.element_drops),
                    drop_reentry: self.element_drop_reentry.clone(),
                });
            };
            if (selectors == "*" || selectors.eq_ignore_ascii_case("html"))
                && self.document_element_present
            {
                push(
                    (&*self.element_identity as *const u8).cast(),
                    "html",
                    "HTML",
                    &self.element_state,
                );
            }
            if (selectors == "*" || selectors.eq_ignore_ascii_case("head"))
                && self.head_present
            {
                push(
                    (&*self.head_identity as *const u8).cast(),
                    "head",
                    "HEAD",
                    &self.head_state,
                );
            }
            if selectors == "*" || selectors == "#target" || selectors.eq_ignore_ascii_case("div")
            {
                push(
                    (&*self.id_element_identity as *const u8).cast(),
                    "div",
                    "DIV",
                    &self.id_element_state,
                );
            }
            // SAFETY: Every test runtime that exposes this probe installs
            // NodeListHostProbe as its one type-level collection host.
            SelectorNodeListResult::Match(unsafe {
                NodeListHandle::new(NodeListHostProbe {
                    items,
                    drops: Some(Rc::clone(&self.node_list_drops)),
                })
            })
        }

        unsafe fn set_bg_color(&self, host_context: *mut c_void, value: &str) -> bool {
            assert!(!host_context.is_null());
            self.bg_color_setter_calls
                .set(self.bg_color_setter_calls.get() + 1);
            *self.bg_color.borrow_mut() = value.to_owned();
            true
        }

        unsafe fn set_title(&self, host_context: *mut c_void, value: &str) -> bool {
            assert!(!host_context.is_null());
            *self.title.borrow_mut() = value.to_owned();
            true
        }
    }

    unsafe extern "C" fn adversarial_document_query_selector(
        native: *mut c_void,
        host_context: *mut c_void,
        selectors: *const u8,
        selectors_length: usize,
        output: *mut RawSelectorElementOutcome,
    ) -> u8 {
        if native.is_null()
            || host_context.is_null()
            || output.is_null()
            || (selectors.is_null() && selectors_length != 0)
        {
            return 0;
        }
        let selectors_bytes = if selectors_length == 0 {
            &[]
        } else {
            // SAFETY: The ABI lends this byte range for the synchronous call.
            unsafe { std::slice::from_raw_parts(selectors, selectors_length) }
        };
        let Ok(selectors) = std::str::from_utf8(selectors_bytes) else {
            return 0;
        };
        if selectors == "callback-failure" {
            return 0;
        }

        // SAFETY: The custom vtable is installed with a live
        // Box<DocumentHostProbe> and the host context is non-null above.
        let host = unsafe { &*native.cast::<DocumentHostProbe>() };
        let target_value = || {
            // SAFETY: The probe and its identity remain live for this
            // synchronous callback.
            let handle = unsafe { host.get_element_by_id(host_context, "target") }
                .expect("the adversarial probe always has its target");
            RawInterfaceValue {
                is_null: 0,
                key: handle.key,
                native: handle.native,
            }
        };
        let null_value = || RawInterfaceValue {
            is_null: 1,
            key: std::ptr::null(),
            native: std::ptr::null_mut(),
        };
        let outcome = match selectors {
            "host-failure" => raw_selector_element_outcome(SelectorElementResult::HostFailure),
            "invalid-status" => RawSelectorElementOutcome {
                status: u32::MAX,
                value: target_value(),
            },
            "syntax-with-value" => RawSelectorElementOutcome {
                status: SELECTOR_SYNTAX_ERROR,
                value: target_value(),
            },
            "null-with-value" => {
                let mut value = target_value();
                value.is_null = 1;
                RawSelectorElementOutcome {
                    status: SELECTOR_RETURNED,
                    value,
                }
            },
            "native-without-key" => {
                let mut value = target_value();
                value.key = std::ptr::null();
                RawSelectorElementOutcome {
                    status: SELECTOR_RETURNED,
                    value,
                }
            },
            "key-without-native" => RawSelectorElementOutcome {
                status: SELECTOR_RETURNED,
                value: RawInterfaceValue {
                    is_null: 0,
                    key: (&*host.id_element_identity as *const u8).cast(),
                    native: std::ptr::null_mut(),
                },
            },
            "valid" => RawSelectorElementOutcome {
                status: SELECTOR_RETURNED,
                value: target_value(),
            },
            _ => RawSelectorElementOutcome {
                status: SELECTOR_RETURNED,
                value: null_value(),
            },
        };
        // SAFETY: output is non-null and writable for this callback.
        unsafe { *output = outcome };
        1
    }

    unsafe extern "C" fn adversarial_element_matches(
        native: *mut c_void,
        host_context: *mut c_void,
        selectors: *const u8,
        selectors_length: usize,
        output: *mut RawSelectorBooleanOutcome,
    ) -> u8 {
        if native.is_null()
            || host_context.is_null()
            || output.is_null()
            || (selectors.is_null() && selectors_length != 0)
        {
            return 0;
        }
        let bytes = if selectors_length == 0 {
            &[]
        } else {
            // SAFETY: The ABI lends this byte range for the synchronous call.
            unsafe { std::slice::from_raw_parts(selectors, selectors_length) }
        };
        let Ok(selectors) = std::str::from_utf8(bytes) else {
            return 0;
        };
        if selectors == "callback-failure" {
            return 0;
        }
        let outcome = match selectors {
            "host-failure" => raw_selector_boolean_outcome(SelectorBooleanResult::HostFailure),
            "invalid-status" => RawSelectorBooleanOutcome {
                status: u32::MAX,
                value: 0,
            },
            "invalid-value" => RawSelectorBooleanOutcome {
                status: SELECTOR_RETURNED,
                value: 2,
            },
            "syntax-with-value" => RawSelectorBooleanOutcome {
                status: SELECTOR_SYNTAX_ERROR,
                value: 1,
            },
            _ => {
                // SAFETY: The custom vtable is installed with this exact host
                // type and the context remains live for the callback.
                let result = unsafe {
                    (&*native.cast::<ElementHostProbe>()).matches(host_context, selectors)
                };
                raw_selector_boolean_outcome(result)
            },
        };
        // SAFETY: output is non-null and writable for this callback.
        unsafe { *output = outcome };
        1
    }

    impl Drop for NativeSmoke {
        fn drop(&mut self) {
            DROPS.fetch_add(1, Ordering::SeqCst);
        }
    }

    // SAFETY: These methods do not unwind or re-enter V8. The trace callback
    // reports NativeSmoke's optional outgoing DOM edge.
    unsafe impl EngineBindingSmokeBinding for NativeSmoke {
        fn constructor(value: i32) -> Option<Self> {
            Some(Self {
                value,
                child: Cell::new(None),
            })
        }

        fn value(&self) -> i32 {
            self.value
        }

        fn set_value(&mut self, value: i32) {
            self.value = value;
        }

        fn add(&self, rhs: i32) -> i32 {
            self.value.wrapping_add(rhs)
        }

        fn set_child(&self, child: EngineBindingSmokeHandle) -> i32 {
            self.child.set(Some(child));
            self.child_value()
        }

        fn child_value(&self) -> i32 {
            let Some(child) = self.child.get() else {
                return i32::MIN;
            };
            // SAFETY: The child is live here. A mismatched interface ID must
            // never recover its native allocation across the generic C ABI.
            assert!(
                unsafe {
                    servo_v8_dom_cell_native(child.cell(), ENGINE_BINDING_SMOKE_INTERFACE_ID + 1)
                }
                .is_null()
            );
            // SAFETY: The child cell is traced for every live NativeSmoke and
            // owns the Box<NativeSmoke> identified by this native pointer.
            unsafe { (*child.native::<NativeSmoke>()).value }
        }

        unsafe fn trace(&self, visitor: *mut TraceVisitor) {
            if let Some(child) = self.child.get() {
                // SAFETY: The generated callback supplies the live V8 visitor,
                // and set_child accepts cells from this runtime only.
                unsafe { child.trace(visitor) };
            }
        }
    }

    #[test]
    fn evaluates_turbolev_code_and_calls_typed_rust_binding() {
        assert_eq!(ENGINE_BINDING_SMOKE_INTERFACE_NAME, "EngineBindingSmoke");
        assert_eq!(ENGINE_BINDING_SMOKE_INTERFACE_ID, 1);
        DROPS.store(0, Ordering::SeqCst);
        let options = Options {
            expose_gc: 1,
            ..Options::default()
        };
        let mut runtime = Runtime::new(options).unwrap();
        runtime
            .install_engine_binding_smoke::<NativeSmoke>()
            .unwrap();
        runtime
            .compile(
                "globalThis.shadowCompileMustNotExecute = true;",
                "servo-v8-smoke.js",
                1,
            )
            .unwrap();
        assert!(
            runtime
                .eval_bool("!Object.hasOwn(globalThis, 'shadowCompileMustNotExecute')")
                .unwrap()
        );

        assert!(
            runtime
                .compile("function syntax error {", "invalid.js", 7)
                .is_err()
        );

        assert!(
            runtime
                .eval_bool(
                    "(() => {\n\
                       const o = globalThis.kept = new EngineBindingSmoke(41);\n\
                       const child = new EngineBindingSmoke(7);\n\
                       o.setChild(child);\n\
                       child.setChild(o);\n\
                       const valueDescriptor = Object.getOwnPropertyDescriptor(\n\
                         EngineBindingSmoke.prototype, 'value');\n\
                       const methodError = new Error('method conversion');\n\
                       let methodErrorPreserved = false;\n\
                       try {\n\
                         o.add({ valueOf() { throw methodError; } });\n\
                       } catch (error) {\n\
                         methodErrorPreserved = error === methodError;\n\
                       }\n\
                       const setterError = new Error('setter conversion');\n\
                       let setterErrorPreserved = false;\n\
                       try {\n\
                         o.value = { valueOf() { throw setterError; } };\n\
                       } catch (error) {\n\
                         setterErrorPreserved = error === setterError;\n\
                       }\n\
                       return o.value === 41 && o.add(1) === 42 &&\n\
                         o.childValue() === 7 && child.childValue() === 41 &&\n\
                         methodErrorPreserved && setterErrorPreserved &&\n\
                         ((o.value = -7), o.value === -7) &&\n\
                         o instanceof EngineBindingSmoke &&\n\
                         Object.getPrototypeOf(o) === EngineBindingSmoke.prototype &&\n\
                         Object.hasOwn(EngineBindingSmoke.prototype, 'value') &&\n\
                         !Object.hasOwn(o, 'value') &&\n\
                         EngineBindingSmoke.length === 1 &&\n\
                         EngineBindingSmoke.prototype.add.length === 1 &&\n\
                         EngineBindingSmoke.prototype.setChild.length === 1 &&\n\
                         EngineBindingSmoke.prototype.childValue.length === 0 &&\n\
                         valueDescriptor.get.length === 0 &&\n\
                         valueDescriptor.set.length === 1;\n\
                     })()"
                )
                .unwrap()
        );
        runtime.low_memory_notification();
        runtime.collect_garbage_for_testing();
        assert_eq!(DROPS.load(Ordering::SeqCst), 0);
        assert!(runtime.eval_bool("kept.childValue() === 7").unwrap());
        assert_eq!(
            runtime
                .eval_i64(
                    "function hot(x) { return (x + 1) | 0; }\n\
                     let result = 0;\n\
                     for (let i = 0; i < 20000; ++i) result = hot(i);\n\
                     result"
                )
                .unwrap(),
            20_000
        );

        assert!(runtime.eval_bool("delete globalThis.kept").unwrap());
        runtime.collect_garbage_for_testing();
        assert_eq!(DROPS.load(Ordering::SeqCst), 2);
        drop(runtime);
        assert_eq!(DROPS.load(Ordering::SeqCst), 2);
    }

    #[test]
    fn generated_callbacks_reject_runtime_reentry_including_during_gc() {
        let options = Options {
            expose_gc: 1,
            ..Options::default()
        };
        let mut runtime = Runtime::new(options).unwrap();
        runtime
            .install_engine_binding_smoke::<CallbackReentrySmoke>()
            .unwrap();
        let _reentry_config = CallbackReentryConfig::new(runtime.raw.as_ptr());

        assert!(
            runtime
                .eval_bool(
                    "globalThis.reentrySmoke = new EngineBindingSmoke(4); \
                     reentrySmoke.value = 9; \
                     reentrySmoke.value === 9 && reentrySmoke.add(1) === 10"
                )
                .unwrap()
        );
        // The live wrapper makes cppgc invoke the Rust trace callback.
        runtime.collect_garbage_for_testing();
        assert!(runtime.eval_bool("delete globalThis.reentrySmoke").unwrap());
        // With no JS root, sweeping invokes the Rust destructor.
        runtime.collect_garbage_for_testing();

        let attempts = CALLBACK_REENTRY_ATTEMPTS.with(|attempts| attempts.borrow().clone());
        for expected_phase in ["constructor", "getter", "setter", "method", "trace", "drop"] {
            assert!(
                attempts
                    .iter()
                    .any(|(phase, _, _)| *phase == expected_phase),
                "missing hostile {expected_phase} callback: {attempts:?}"
            );
        }
        assert!(attempts.iter().all(|(_, status, error)| {
            *status == 0 && error.contains("re-entered from a Rust host callback")
        }));
    }

    #[test]
    fn isolates_realms_and_rejects_destroyed_ids() {
        let options = Options {
            expose_gc: 1,
            ..Options::default()
        };
        let mut runtime = Runtime::new(options).unwrap();
        let first = runtime.create_realm().unwrap();
        let second = runtime.create_realm().unwrap();
        assert_ne!(first, second);

        assert!(
            runtime
                .eval_bool_in_realm(
                    first,
                    "globalThis === window && window.window === window && \
                     window.document === document",
                )
                .unwrap()
        );

        assert!(
            runtime
                .eval_bool_in_realm(
                    first,
                    "globalThis.realmOnlyValue = 17; realmOnlyValue === 17",
                )
                .unwrap()
        );
        assert!(
            runtime
                .eval_bool_in_realm(second, "!Object.hasOwn(globalThis, 'realmOnlyValue')",)
                .unwrap()
        );
        assert!(
            runtime
                .eval_bool("!Object.hasOwn(globalThis, 'realmOnlyValue')")
                .unwrap()
        );
        runtime
            .compile_in_realm(first, "realmOnlyValue + 1;", "first-realm.js", 3)
            .unwrap();

        let ScriptCompileOutcome::ParseError(parse_error) = runtime
            .compile_script_in_realm(first, "function syntax error {", "parse-error.js", 7)
            .unwrap()
        else {
            panic!("invalid V8 source unexpectedly compiled");
        };
        assert!(!parse_error.message.is_empty());
        assert_eq!(parse_error.resource_name, "parse-error.js");
        assert_eq!(parse_error.line_number, 7);
        assert!(parse_error.column_number > 0);

        let retained = compiled(runtime.compile_script_in_realm(
            first,
            "globalThis.retainedScriptValue = 23;",
            "retained-first-realm.js",
            5,
        ));
        assert!(
            runtime
                .eval_bool_in_realm(first, "!Object.hasOwn(globalThis, 'retainedScriptValue')",)
                .unwrap()
        );
        assert_eq!(
            runtime.run_script_in_realm(first, retained).unwrap(),
            ScriptRunOutcome::Completed
        );
        assert!(
            runtime
                .eval_bool_in_realm(first, "retainedScriptValue === 23")
                .unwrap()
        );
        let consumed_error = runtime
            .run_script_in_realm(first, retained)
            .unwrap_err()
            .to_string();
        assert!(consumed_error.contains("unknown or consumed Servo V8 script"));

        let throwing = compiled(runtime.compile_script_in_realm(
            first,
            "throw new Error('retained boom');",
            "boom.js",
            9,
        ));
        let ScriptRunOutcome::Thrown(exception) =
            runtime.run_script_in_realm(first, throwing).unwrap()
        else {
            panic!("throwing retained script completed normally");
        };
        assert!(exception.message.contains("retained boom"));
        assert_eq!(exception.resource_name, "boom.js");
        assert_eq!(exception.line_number, 9);
        assert!(exception.column_number > 0);
        assert!(exception.stack.contains("boom.js"));
        assert!(runtime.run_script_in_realm(first, throwing).is_err());

        let discarded = compiled(runtime.compile_script_in_realm(
            first,
            "globalThis.discardedRan = true;",
            "discarded.js",
            1,
        ));
        runtime.discard_script_in_realm(first, discarded).unwrap();
        assert!(runtime.run_script_in_realm(first, discarded).is_err());
        assert!(
            runtime
                .eval_bool_in_realm(first, "!Object.hasOwn(globalThis, 'discardedRan')")
                .unwrap()
        );

        let microtask = compiled(runtime.compile_script_in_realm(
            first,
            "globalThis.retainedMicrotaskRan = false; \
                 Promise.resolve().then(() => retainedMicrotaskRan = true);",
            "microtask.js",
            1,
        ));
        assert_eq!(
            runtime.run_script_in_realm(first, microtask).unwrap(),
            ScriptRunOutcome::Completed
        );
        // The retained-script path does not checkpoint. This diagnostic eval
        // observes false, then its standalone helper performs a checkpoint.
        assert!(
            !runtime
                .eval_bool_in_realm(first, "retainedMicrotaskRan")
                .unwrap()
        );
        assert!(
            runtime
                .eval_bool_in_realm(first, "retainedMicrotaskRan")
                .unwrap()
        );

        // The explicit checkpoint is what Servo calls at the HTML task
        // boundary, so it must drain a retained script's jobs without any
        // diagnostic eval running first.
        let explicit = compiled(runtime.compile_script_in_realm(
            first,
            "globalThis.explicitMicrotaskRan = false; \
                 Promise.resolve().then(() => explicitMicrotaskRan = true);",
            "explicit-microtask.js",
            1,
        ));
        assert_eq!(
            runtime.run_script_in_realm(first, explicit).unwrap(),
            ScriptRunOutcome::Completed
        );
        assert_eq!(
            runtime.perform_microtask_checkpoint().unwrap(),
            ScriptRunOutcome::Completed
        );
        assert!(
            runtime
                .eval_bool_in_realm(first, "explicitMicrotaskRan")
                .unwrap()
        );
        // Draining an empty queue is not an error, so Servo may checkpoint at
        // every task boundary without tracking whether jobs exist.
        assert_eq!(
            runtime.perform_microtask_checkpoint().unwrap(),
            ScriptRunOutcome::Completed
        );

        // One checkpoint drains to exhaustion, including jobs enqueued by
        // jobs. HTML's checkpoint runs until the queue is empty, so a promise
        // chain must complete within a single task boundary.
        let nested = compiled(runtime.compile_script_in_realm(
            first,
            "globalThis.nestedMicrotaskDepth = 0; \
                 Promise.resolve() \
                   .then(() => nestedMicrotaskDepth++) \
                   .then(() => nestedMicrotaskDepth++) \
                   .then(() => nestedMicrotaskDepth++);",
            "nested-microtask.js",
            1,
        ));
        assert_eq!(
            runtime.run_script_in_realm(first, nested).unwrap(),
            ScriptRunOutcome::Completed
        );
        assert_eq!(
            runtime.perform_microtask_checkpoint().unwrap(),
            ScriptRunOutcome::Completed
        );
        assert!(
            runtime
                .eval_bool_in_realm(first, "nestedMicrotaskDepth === 3")
                .unwrap()
        );

        // A reaction that throws rejects its derived promise rather than
        // reaching a TryCatch at the checkpoint boundary, so this is the
        // channel an ordinary `Promise.then` failure takes. It must be
        // observable, and one failure must not cancel the rest of the drain.
        let throwing_job = compiled(runtime.compile_script_in_realm(
            first,
            "globalThis.jobAfterThrowRan = false; \
                 Promise.resolve().then(() => { throw new Error('job boom'); }); \
                 Promise.resolve().then(() => { jobAfterThrowRan = true; });",
            "throwing-job.js",
            4,
        ));
        assert_eq!(
            runtime.run_script_in_realm(first, throwing_job).unwrap(),
            ScriptRunOutcome::Completed
        );
        assert_eq!(
            runtime.perform_microtask_checkpoint().unwrap(),
            ScriptRunOutcome::Completed
        );
        let job_errors = runtime.take_pending_job_errors().unwrap();
        assert_eq!(job_errors.len(), 1);
        assert!(job_errors[0].exception.message.contains("job boom"));
        assert_eq!(job_errors[0].exception.resource_name, "throwing-job.js");
        // The failure must name the realm that produced it, or Servo has no
        // global to fire the event on.
        assert_eq!(job_errors[0].realm_id, Some(first));
        assert!(
            runtime
                .eval_bool_in_realm(first, "jobAfterThrowRan")
                .unwrap()
        );
        // Pulling is destructive, so a second pull reports nothing.
        assert!(runtime.take_pending_job_errors().unwrap().is_empty());

        // A rejection that gains a handler later must be revoked rather than
        // reported, which is why the promise identity is tracked.
        let handled_late = compiled(runtime.compile_script_in_realm(
            first,
            "globalThis.lateHandlerRan = false; \
                 const rejected = Promise.reject(new Error('handled later')); \
                 Promise.resolve().then(() => { \
                   rejected.catch(() => { lateHandlerRan = true; }); \
                 });",
            "handled-late.js",
            1,
        ));
        assert_eq!(
            runtime.run_script_in_realm(first, handled_late).unwrap(),
            ScriptRunOutcome::Completed
        );
        assert_eq!(
            runtime.perform_microtask_checkpoint().unwrap(),
            ScriptRunOutcome::Completed
        );
        assert!(runtime.eval_bool_in_realm(first, "lateHandlerRan").unwrap());
        assert!(runtime.take_pending_job_errors().unwrap().is_empty());

        // The queue is isolate-wide, so one checkpoint drains every realm.
        let across_realms = compiled(runtime.compile_script_in_realm(
            second,
            "globalThis.secondRealmMicrotaskRan = false; \
                 Promise.resolve().then(() => secondRealmMicrotaskRan = true);",
            "second-realm-microtask.js",
            1,
        ));
        assert_eq!(
            runtime.run_script_in_realm(second, across_realms).unwrap(),
            ScriptRunOutcome::Completed
        );
        assert_eq!(
            runtime.perform_microtask_checkpoint().unwrap(),
            ScriptRunOutcome::Completed
        );
        assert!(
            runtime
                .eval_bool_in_realm(second, "secondRealmMicrotaskRan")
                .unwrap()
        );

        // A rejection is retained through a v8::Global<Promise> until it is
        // handled or reported. Destroying its realm before the next checkpoint
        // must release that handle and discard the now-unreportable error,
        // rather than pinning the dead context until runtime teardown.
        let abandoned_rejection = compiled(runtime.compile_script_in_realm(
            first,
            "Promise.reject(new Error('realm is going away'));",
            "abandoned-rejection.js",
            1,
        ));
        assert_eq!(
            runtime
                .run_script_in_realm(first, abandoned_rejection)
                .unwrap(),
            ScriptRunOutcome::Completed
        );

        runtime.destroy_realm(first).unwrap();
        assert!(runtime.take_pending_job_errors().unwrap().is_empty());
        let compile_error = runtime
            .compile_in_realm(first, "1;", "destroyed-realm.js", 1)
            .unwrap_err()
            .to_string();
        assert!(compile_error.contains("unknown or destroyed Servo V8 realm"));
        assert!(runtime.eval_bool_in_realm(first, "true").is_err());
        assert!(runtime.destroy_realm(first).is_err());

        let second_script = compiled(runtime.compile_script_in_realm(
            second,
            "globalThis.secondRan = true;",
            "second.js",
            1,
        ));
        assert!(runtime.run_script_in_realm(first, second_script).is_err());
        assert_eq!(
            runtime.run_script_in_realm(second, second_script).unwrap(),
            ScriptRunOutcome::Completed
        );

        assert!(runtime.eval_bool_in_realm(second, "true").unwrap());
        runtime.destroy_realm(second).unwrap();
    }

    #[test]
    fn interrupt_handle_terminates_script_and_becomes_inert_on_drop() {
        let mut runtime = Runtime::new(Options {
            expose_gc: 1,
            ..Options::default()
        })
        .unwrap();
        let realm = runtime.create_realm().unwrap();
        let script =
            compiled(runtime.compile_script_in_realm(realm, "while (true) {}", "infinite.js", 1));
        let interrupt = runtime.interrupt_handle();
        let interrupt_thread = interrupt.clone();
        let requester = thread::spawn(move || {
            thread::sleep(Duration::from_millis(20));
            assert!(interrupt_thread.terminate_execution());
        });

        let outcome = runtime.run_script_in_realm(realm, script).unwrap();
        requester.join().unwrap();
        assert_eq!(outcome, ScriptRunOutcome::Terminated);
        drop(runtime);
        assert!(!interrupt.terminate_execution());
    }

    #[test]
    fn major_gc_prunes_dead_wrapper_cache_entries() {
        let mut runtime = Runtime::new(Options {
            expose_gc: 1,
            ..Options::default()
        })
        .unwrap();
        runtime
            .install_element_host::<ElementHostProbe>()
            .expect("Element host vtable installs once");
        let realm = runtime.create_realm().unwrap();
        let element_drops = Rc::new(Cell::new(0));
        let mut host = DocumentHostProbe::new(
            Rc::new(Cell::new(false)),
            Rc::new(Cell::new(0)),
            Rc::new(Cell::new(0)),
        );
        host.element_drops = Rc::clone(&element_drops);
        runtime.install_document_host(realm, host).unwrap();

        assert!(
            runtime
                .eval_bool_in_realm(
                    realm,
                    "globalThis.kept = document.documentElement; kept.tagName === 'HTML'",
                )
                .unwrap()
        );
        assert_eq!(runtime.wrapper_cache_size_for_testing(realm).unwrap(), 1);

        // A major collection must retain both the live wrapper and its cache
        // entry while JavaScript still reaches it.
        runtime.collect_garbage_for_testing();
        assert_eq!(runtime.wrapper_cache_size_for_testing(realm).unwrap(), 1);
        assert_eq!(element_drops.get(), 0);

        assert!(
            runtime
                .eval_bool_in_realm(realm, "globalThis.kept = null; true")
                .unwrap()
        );
        runtime.collect_garbage_for_testing();
        assert_eq!(runtime.wrapper_cache_size_for_testing(realm).unwrap(), 0);
        assert_eq!(element_drops.get(), 1);

        // The same DOM address can be wrapped again after pruning without a
        // stale entry colliding with the new cell.
        assert!(
            runtime
                .eval_bool_in_realm(
                    realm,
                    "globalThis.recreated = document.documentElement; \
                     recreated.tagName === 'HTML'",
                )
                .unwrap()
        );
        assert_eq!(runtime.wrapper_cache_size_for_testing(realm).unwrap(), 1);
        runtime.destroy_realm(realm).unwrap();
        assert_eq!(element_drops.get(), 2);
        assert!(runtime.wrapper_cache_size_for_testing(realm).is_err());
    }

    #[test]
    fn timer_host_retains_invokes_clears_and_drops_callbacks() {
        let mut runtime = Runtime::new(Options {
            expose_gc: 1,
            ..Options::default()
        })
        .unwrap();
        let _reentry = CallbackReentryConfig::new(runtime.raw.as_ptr());
        let realm = runtime.create_realm().unwrap();
        let functions = Rc::new(RefCell::new(Vec::new()));
        let strings = Rc::new(RefCell::new(Vec::new()));
        let clears = Rc::new(RefCell::new(Vec::new()));
        let drops = Rc::new(Cell::new(0));
        runtime
            .install_timer_host(
                realm,
                TimerHostProbe::new(
                    Rc::clone(&functions),
                    Rc::clone(&strings),
                    Rc::clone(&clears),
                    Rc::clone(&drops),
                ),
            )
            .unwrap();

        assert!(
            runtime
                .eval_bool_in_realm(
                    realm,
                    "(() => {\n\
                       const names = ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval'];\n\
                       const expectedLengths = [1, 0, 1, 0];\n\
                       if (!names.every((name, index) => {\n\
                         const descriptor = Object.getOwnPropertyDescriptor(globalThis, name);\n\
                         return descriptor && !descriptor.enumerable && descriptor.writable &&\n\
                           descriptor.configurable && descriptor.value.name === name &&\n\
                           descriptor.value.length === expectedLengths[index];\n\
                       })) return false;\n\
                       globalThis.timerHandle = setTimeout((left, right) => {\n\
                         globalThis.timerResult = left + right;\n\
                       }, 12, 40, 2);\n\
                       return timerHandle === 1;\n\
                     })()",
                )
                .unwrap()
        );
        let one_shot = functions.borrow()[0].clone();
        assert_eq!(one_shot.timeout_ms, 12);
        assert!(!one_shot.is_interval);
        assert_eq!(one_shot.host_context, 0);

        // The callback and arbitrary argument values are strong while active,
        // so a major V8 collection cannot collect them before Servo fires it.
        runtime.collect_garbage_for_testing();
        assert_eq!(
            runtime
                .run_timer_callback_in_realm(realm, one_shot.callback_id)
                .unwrap(),
            ScriptRunOutcome::Completed
        );
        assert!(
            runtime
                .eval_bool_in_realm(realm, "timerResult === 42")
                .unwrap()
        );
        assert!(
            runtime
                .run_timer_callback_in_realm(realm, one_shot.callback_id)
                .unwrap_err()
                .to_string()
                .contains("unknown or cleared Servo V8 timer callback")
        );

        assert!(
            runtime
                .eval_bool_in_realm(
                    realm,
                    "globalThis.intervalCount = 0;\n\
                     globalThis.intervalHandle = setInterval(\n\
                       increment => intervalCount += increment, 7, 3);\n\
                     intervalHandle === 2",
                )
                .unwrap()
        );
        let interval = functions.borrow()[1].clone();
        assert!(interval.is_interval);
        for _ in 0..2 {
            assert_eq!(
                runtime
                    .run_timer_callback_in_realm(realm, interval.callback_id)
                    .unwrap(),
                ScriptRunOutcome::Completed
            );
        }
        assert!(
            runtime
                .eval_bool_in_realm(realm, "intervalCount === 6")
                .unwrap()
        );
        assert!(
            runtime
                .eval_bool_in_realm(realm, "clearTimeout(intervalHandle); true")
                .unwrap()
        );
        assert_eq!(&*clears.borrow(), &[2]);
        assert!(
            runtime
                .run_timer_callback_in_realm(realm, interval.callback_id)
                .is_err()
        );

        assert!(
            runtime
                .eval_bool_in_realm(
                    realm,
                    "setTimeout('globalThis.stringTimerRan = \"\u{2713}\"', -9) === 3",
                )
                .unwrap()
        );
        assert_eq!(
            &*strings.borrow(),
            &[ScheduledTimerString {
                source: "globalThis.stringTimerRan = \"\u{2713}\"".to_owned(),
                timeout_ms: -9,
                is_interval: false,
                host_context: 0,
                handle: 3,
            }]
        );

        assert!(
            runtime
                .eval_bool_in_realm(
                    realm,
                    "globalThis.externalClearHandle = setInterval(() => {}, 5);\n\
                     externalClearHandle === 4",
                )
                .unwrap()
        );
        let external_clear = functions.borrow()[2].clone();
        runtime
            .clear_timer_callback_in_realm(realm, external_clear.callback_id)
            .unwrap();
        assert!(
            runtime
                .run_timer_callback_in_realm(realm, external_clear.callback_id)
                .is_err()
        );

        let schedule_with_context = compiled(runtime.compile_script_in_realm(
            realm,
            "setTimeout(() => {}, 0);",
            "timer-host-context.js",
            1,
        ));
        let mut host_context_token = 0_u8;
        // SAFETY: The token stays live for the synchronous run and the probe
        // records its address but never dereferences or retains it for use.
        assert_eq!(
            unsafe {
                runtime.run_script_in_realm_with_host_context(
                    realm,
                    schedule_with_context,
                    (&mut host_context_token as *mut u8).cast(),
                )
            }
            .unwrap(),
            ScriptRunOutcome::Completed
        );
        let with_context = functions.borrow()[3].clone();
        assert_eq!(
            with_context.host_context,
            (&mut host_context_token as *mut u8) as usize
        );
        runtime
            .clear_timer_callback_in_realm(realm, with_context.callback_id)
            .unwrap();

        assert!(
            runtime
                .eval_bool_in_realm(
                    realm,
                    "(() => {\n\
                       let missing = false, symbol = false, delay = false;\n\
                       try { setTimeout(); } catch (error) { missing = error instanceof TypeError; }\n\
                       try { setTimeout(Symbol('handler')); }\n\
                       catch (error) { symbol = error instanceof TypeError; }\n\
                       const sentinel = new Error('delay sentinel');\n\
                       try { setTimeout(() => {}, { valueOf() { throw sentinel; } }); }\n\
                       catch (error) { delay = error === sentinel; }\n\
                       return missing && symbol && delay;\n\
                     })()",
                )
                .unwrap()
        );

        runtime.destroy_realm(realm).unwrap();
        assert_eq!(drops.get(), 1);
        assert!(
            runtime
                .run_timer_callback_in_realm(realm, interval.callback_id)
                .is_err()
        );
        CALLBACK_REENTRY_ATTEMPTS.with(|attempts| {
            let attempts = attempts.borrow();
            assert!(
                attempts
                    .iter()
                    .any(|(phase, _, _)| *phase == "timer schedule function")
            );
            assert!(
                attempts
                    .iter()
                    .any(|(phase, _, _)| *phase == "timer schedule string")
            );
            assert!(attempts.iter().any(|(phase, _, _)| *phase == "timer clear"));
            assert!(
                attempts
                    .iter()
                    .any(|(phase, _, _)| *phase == "timer host drop")
            );
            assert!(attempts.iter().all(|(_, status, error)| {
                *status == 0 && error.contains("re-entered from a Rust host callback")
            }));
        });
    }

    #[test]
    fn console_host_formats_routes_guards_and_drops_messages() {
        let mut runtime = Runtime::new(Options {
            expose_gc: 1,
            ..Options::default()
        })
        .unwrap();
        let _reentry = CallbackReentryConfig::new(runtime.raw.as_ptr());
        let realm = runtime.create_realm().unwrap();

        // The facade exists as soon as the realm does, but cannot silently
        // discard output before an embedder host is installed.
        assert!(
            runtime
                .eval_bool_in_realm(
                    realm,
                    "(() => { try { console.log('lost'); } catch (error) { \
                       return error instanceof TypeError; } return false; })()",
                )
                .unwrap()
        );

        let messages = Rc::new(RefCell::new(Vec::new()));
        let drops = Rc::new(Cell::new(0));
        runtime
            .install_console_host(
                realm,
                ConsoleHostProbe {
                    messages: Rc::clone(&messages),
                    drops: Rc::clone(&drops),
                    attempt_reentry: true,
                },
            )
            .unwrap();

        assert!(
            runtime
                .eval_bool_in_realm(
                    realm,
                    "(() => {\n\
                       const globalDescriptor = Object.getOwnPropertyDescriptor(\n\
                         globalThis, 'console');\n\
                       const names = ['debug', 'error', 'info', 'log', 'trace', 'warn'];\n\
                       if (!globalDescriptor || globalDescriptor.enumerable ||\n\
                           !globalDescriptor.writable || !globalDescriptor.configurable ||\n\
                           Object.prototype.toString.call(console) !== '[object Console]' ||\n\
                           Object.keys(console).join(',') !== names.join(',') ||\n\
                           typeof console.assert !== 'undefined' ||\n\
                           !names.every(name => {\n\
                             const descriptor = Object.getOwnPropertyDescriptor(console, name);\n\
                             return descriptor && descriptor.enumerable && descriptor.writable &&\n\
                               descriptor.configurable && descriptor.value.name === name &&\n\
                               descriptor.value.length === 0;\n\
                           })) return false;\n\
                       console.debug('debug', 1);\n\
                       console.error('error', false);\n\
                       console.info();\n\
                       let touched = false;\n\
                       console.log('log', null, undefined, 7n, Symbol('s'), {\n\
                         toString() { touched = true; throw new Error('must not run'); }\n\
                       });\n\
                       console.trace('trace');\n\
                       console.warn('warn', '✓');\n\
                       Promise.resolve().then(() => console.log('microtask'));\n\
                       return !touched;\n\
                     })()",
                )
                .unwrap()
        );

        let messages = messages.borrow();
        assert_eq!(messages.len(), 7);
        assert_eq!(messages[0], (ConsoleLevel::Debug, "debug 1".to_owned()));
        assert_eq!(messages[1], (ConsoleLevel::Error, "error false".to_owned()));
        assert_eq!(messages[2], (ConsoleLevel::Info, String::new()));
        assert_eq!(messages[3].0, ConsoleLevel::Log);
        assert!(messages[3].1.starts_with("log null undefined 7 Symbol(s) "));
        assert_eq!(messages[4].0, ConsoleLevel::Trace);
        assert!(messages[4].1.starts_with("trace\n    at "));
        assert_eq!(messages[5], (ConsoleLevel::Warn, "warn ✓".to_owned()));
        assert_eq!(messages[6], (ConsoleLevel::Log, "microtask".to_owned()));
        drop(messages);

        // A rejected second transfer stays Rust-owned and is dropped here.
        assert!(
            runtime
                .install_console_host(
                    realm,
                    ConsoleHostProbe {
                        messages: Rc::new(RefCell::new(Vec::new())),
                        drops: Rc::clone(&drops),
                        attempt_reentry: false,
                    },
                )
                .is_err()
        );
        assert_eq!(drops.get(), 1);

        runtime.destroy_realm(realm).unwrap();
        assert_eq!(drops.get(), 2);
        CALLBACK_REENTRY_ATTEMPTS.with(|attempts| {
            let attempts = attempts.borrow();
            assert!(
                attempts
                    .iter()
                    .any(|(phase, _, _)| *phase == "console write")
            );
            assert!(
                attempts
                    .iter()
                    .any(|(phase, _, _)| *phase == "console host drop")
            );
            assert!(attempts.iter().all(|(_, status, error)| {
                *status == 0 && error.contains("re-entered from a Rust host callback")
            }));
        });
    }

    #[test]
    fn interface_returns_preserve_wrapper_identity() {
        let options = Options {
            expose_gc: 1,
            ..Options::default()
        };
        let mut runtime = Runtime::new(options).unwrap();
        runtime
            .install_element_host::<ElementHostProbe>()
            .expect("Element host vtable installs once");
        // Type-level, so a second install is a misuse rather than a no-op.
        assert!(runtime.install_element_host::<ElementHostProbe>().is_err());

        let realm = runtime.create_realm().unwrap();
        let element_drops = Rc::new(Cell::new(0));
        let mut host = DocumentHostProbe::new(
            Rc::new(Cell::new(false)),
            Rc::new(Cell::new(0)),
            Rc::new(Cell::new(0)),
        );
        host.element_drops = Rc::clone(&element_drops);
        let get_element_by_id_calls = Rc::clone(&host.get_element_by_id_calls);
        let drop_reentry_attempts = Rc::new(RefCell::new(Vec::new()));
        host.element_drop_reentry = Some(ElementDropReentryProbe {
            runtime: runtime.raw.as_ptr(),
            realm,
            attempts: Rc::clone(&drop_reentry_attempts),
        });
        runtime.install_document_host(realm, host).unwrap();

        let query_script = compiled(runtime.compile_script_in_realm(
            realm,
            "(() => {\n\
               const documentDescriptor = Object.getOwnPropertyDescriptor(\n\
                 Object.getPrototypeOf(document), 'querySelector');\n\
               globalThis.documentQueryWrongBrandStringified = false;\n\
               let documentWrongBrand = false;\n\
               try { documentDescriptor.value.call({}, { toString() {\n\
                 documentQueryWrongBrandStringified = true; return '#target';\n\
               }}); } catch (error) { documentWrongBrand = error instanceof TypeError; }\n\
               const conversionError = new Error('query conversion sentinel');\n\
               let documentConversion = false;\n\
               try { document.querySelector({ toString() { throw conversionError; }}); }\n\
               catch (error) { documentConversion = error === conversionError; }\n\
               let documentSymbol = false;\n\
               try { document.querySelector(Symbol('target')); }\n\
               catch (error) { documentSymbol = error instanceof TypeError; }\n\
               let documentMissing = false;\n\
               try { document.querySelector(); }\n\
               catch (error) { documentMissing = error instanceof TypeError; }\n\
               const target = document.querySelector({\n\
                 toString() { return '#target'; }\n\
               });\n\
               target.queryMarker = 47;\n\
               const elementDescriptor = Object.getOwnPropertyDescriptor(\n\
                 Object.getPrototypeOf(target), 'querySelector');\n\
               const selectorDescriptors = ['closest', 'matches', 'webkitMatchesSelector']\n\
                 .map(name => [name, Object.getOwnPropertyDescriptor(\n\
                   Object.getPrototypeOf(target), name)]);\n\
               globalThis.elementQueryWrongBrandStringified = false;\n\
               let elementWrongBrand = false;\n\
               try { elementDescriptor.value.call({}, { toString() {\n\
                 elementQueryWrongBrandStringified = true; return 'span';\n\
               }}); } catch (error) { elementWrongBrand = error instanceof TypeError; }\n\
               let elementConversion = false;\n\
               try { target.querySelector({ toString() { throw conversionError; }}); }\n\
               catch (error) { elementConversion = error === conversionError; }\n\
               let elementSymbol = false;\n\
               try { target.querySelector(Symbol('span')); }\n\
               catch (error) { elementSymbol = error instanceof TypeError; }\n\
               let elementMissing = false;\n\
               try { target.querySelector(); }\n\
               catch (error) { elementMissing = error instanceof TypeError; }\n\
               globalThis.selectorWrongBrandStringified = false;\n\
               const selectorWrongBrand = selectorDescriptors.every(([, descriptor]) => {\n\
                 try { descriptor.value.call({}, { toString() {\n\
                   selectorWrongBrandStringified = true; return '#target';\n\
                 }}); return false; }\n\
                 catch (error) { return error instanceof TypeError; }\n\
               });\n\
               const selectorMissing = selectorDescriptors.every(([, descriptor]) => {\n\
                 try { descriptor.value.call(target); return false; }\n\
                 catch (error) { return error instanceof TypeError; }\n\
               });\n\
               let matchesConversion = false;\n\
               try { target.matches({ toString() { throw conversionError; }}); }\n\
               catch (error) { matchesConversion = error === conversionError; }\n\
               let closestSymbol = false;\n\
               try { target.closest(Symbol('target')); }\n\
               catch (error) { closestSymbol = error instanceof TypeError; }\n\
               const syntaxChecks = [];\n\
               for (const callback of [\n\
                 () => document.querySelector('['),\n\
                 () => document.querySelector(''),\n\
                 () => target.querySelector('['),\n\
                 () => target.closest('['),\n\
                 () => target.matches('['),\n\
                 () => target.webkitMatchesSelector('['),\n\
               ]) {\n\
                 try { callback(); syntaxChecks.push(false); }\n\
                 catch (error) {\n\
                   syntaxChecks.push(\n\
                     error instanceof DOMException && error instanceof Error &&\n\
                     (typeof Error.isError !== 'function' || Error.isError(error)) &&\n\
                     Object.getPrototypeOf(error) === DOMException.prototype &&\n\
                     Object.prototype.toString.call(error) === '[object DOMException]' &&\n\
                     String(error) ===\n\
                       'SyntaxError: The string did not match the expected pattern.' &&\n\
                     error.constructor === DOMException && error.name === 'SyntaxError' &&\n\
                     error.message === 'The string did not match the expected pattern.' &&\n\
                     error.code === 12 && DOMException.SYNTAX_ERR === 12 &&\n\
                     !Object.hasOwn(error, 'name') && !Object.hasOwn(error, 'message') &&\n\
                     !Object.hasOwn(error, 'code') && !Object.hasOwn(error, 'stack'));\n\
                 }\n\
               }\n\
               let constructorRequiresNew = false;\n\
               try { DOMException(); }\n\
               catch (error) { constructorRequiresNew = error instanceof TypeError; }\n\
               const defaultException = new DOMException(undefined, undefined);\n\
               const namedException = new DOMException('custom', 'SyntaxError');\n\
               const prototypeDescriptor = Object.getOwnPropertyDescriptor(\n\
                 DOMException, 'prototype');\n\
               const syntaxConstantDescriptor = Object.getOwnPropertyDescriptor(\n\
                 DOMException, 'SYNTAX_ERR');\n\
               const nameDescriptor = Object.getOwnPropertyDescriptor(\n\
                 DOMException.prototype, 'name');\n\
               let getterRejectsWrongBrand = false;\n\
               try { nameDescriptor.get.call({}); }\n\
               catch (error) { getterRejectsWrongBrand = error instanceof TypeError; }\n\
               globalThis.querySelectorBindingProof =\n\
                 documentDescriptor && documentDescriptor.value.name === 'querySelector' &&\n\
                 documentDescriptor.value.length === 1 && documentDescriptor.writable &&\n\
                 documentDescriptor.enumerable && documentDescriptor.configurable &&\n\
                 elementDescriptor && elementDescriptor.value.name === 'querySelector' &&\n\
                 elementDescriptor.value.length === 1 && elementDescriptor.writable &&\n\
                 elementDescriptor.enumerable && elementDescriptor.configurable &&\n\
                 selectorDescriptors.every(([name, descriptor]) => descriptor &&\n\
                   descriptor.value.name === name && descriptor.value.length === 1 &&\n\
                   descriptor.writable && descriptor.enumerable && descriptor.configurable) &&\n\
                 !Object.hasOwn(document, 'querySelector') &&\n\
                 !Object.hasOwn(target, 'querySelector') && documentWrongBrand &&\n\
                 !documentQueryWrongBrandStringified && documentConversion &&\n\
                 documentSymbol && documentMissing && elementWrongBrand &&\n\
                 !elementQueryWrongBrandStringified && elementConversion &&\n\
                 elementSymbol && elementMissing && selectorWrongBrand &&\n\
                 !selectorWrongBrandStringified && selectorMissing &&\n\
                 matchesConversion && closestSymbol && syntaxChecks.every(Boolean) &&\n\
                 document.querySelector('#target') === target &&\n\
                 document.getElementById('target') === target && target.queryMarker === 47 &&\n\
                 document.querySelector('missing') === null &&\n\
                 document.querySelector(undefined) === null &&\n\
                 document.querySelector(null) === null &&\n\
                 target.querySelector('span') === target.firstElementChild &&\n\
                 target.querySelector('#last-child') === target.lastElementChild &&\n\
                 target.querySelector('missing') === null &&\n\
                 target.closest('#target') === target && target.closest('missing') === null &&\n\
                 target.matches('#target') && target.matches('div') &&\n\
                 !target.matches('span') && target.webkitMatchesSelector('.alpha') &&\n\
                 !target.webkitMatchesSelector('.missing') &&\n\
                 typeof DOMException === 'function' && DOMException.name === 'DOMException' &&\n\
                 DOMException.length === 0 && DOMException.prototype.constructor === DOMException &&\n\
                 Object.getPrototypeOf(DOMException.prototype) === Error.prototype &&\n\
                 prototypeDescriptor && !prototypeDescriptor.writable &&\n\
                 !prototypeDescriptor.enumerable && !prototypeDescriptor.configurable &&\n\
                 syntaxConstantDescriptor && !syntaxConstantDescriptor.writable &&\n\
                 syntaxConstantDescriptor.enumerable && !syntaxConstantDescriptor.configurable &&\n\
                 nameDescriptor && nameDescriptor.get.name === 'get name' &&\n\
                 nameDescriptor.get.length === 0 && nameDescriptor.set === undefined &&\n\
                 nameDescriptor.enumerable && nameDescriptor.configurable &&\n\
                 getterRejectsWrongBrand && constructorRequiresNew &&\n\
                 defaultException instanceof DOMException && defaultException instanceof Error &&\n\
                 defaultException.name === 'Error' && defaultException.message === '' &&\n\
                 defaultException.code === 0 && String(defaultException) === 'Error' &&\n\
                 namedException.name === 'SyntaxError' && namedException.message === 'custom' &&\n\
                 namedException.code === 12 && String(namedException) === 'SyntaxError: custom' &&\n\
                 new DOMException('', 'DOMStringSizeError').code === 0 &&\n\
                 new DOMException('', 'NoDataAllowedError').code === 0 &&\n\
                 new DOMException('', 'ValidationError').code === 0 &&\n\
                 DOMException.DOMSTRING_SIZE_ERR === 2 &&\n\
                 DOMException.NO_DATA_ALLOWED_ERR === 6 && DOMException.VALIDATION_ERR === 16;\n\
             })();",
            "query-selector-binding.js",
            1,
        ));
        let mut query_host_context_token = 0_u8;
        // SAFETY: The token remains live for this synchronous probe.
        let query_outcome = unsafe {
            runtime.run_script_in_realm_with_host_context(
                realm,
                query_script,
                (&mut query_host_context_token as *mut u8).cast(),
            )
        }
        .unwrap();
        assert_eq!(query_outcome, ScriptRunOutcome::Completed);
        assert!(
            runtime
                .eval_bool_in_realm(realm, "querySelectorBindingProof")
                .unwrap()
        );

        // The point of the wrapper cache: the same DOM object read twice must
        // be the same JavaScript object, not merely an equal one.
        assert!(
            runtime
                .eval_bool_in_realm(
                    realm,
                    "document.documentElement === document.documentElement",
                )
                .unwrap()
        );

        let operation_script = compiled(runtime.compile_script_in_realm(
            realm,
            "(() => {\n\
               const descriptor = Object.getOwnPropertyDescriptor(\n\
                 Object.getPrototypeOf(document), 'getElementById');\n\
               const documentPrototype = Object.getPrototypeOf(document);\n\
               const documentFirstDescriptor = Object.getOwnPropertyDescriptor(\n\
                 documentPrototype, 'firstElementChild');\n\
               const documentLastDescriptor = Object.getOwnPropertyDescriptor(\n\
                 documentPrototype, 'lastElementChild');\n\
               const documentCountDescriptor = Object.getOwnPropertyDescriptor(\n\
                 documentPrototype, 'childElementCount');\n\
               globalThis.getElementByIdWrongBrandStringified = false;\n\
               let rejectsWrongBrand = false;\n\
               try { descriptor.value.call({}, { toString() {\n\
                 getElementByIdWrongBrandStringified = true; return 'target';\n\
               }}); } catch (error) { rejectsWrongBrand = error instanceof TypeError; }\n\
               const conversionError = new Error('conversion sentinel');\n\
               let preservesConversionError = false;\n\
               try { document.getElementById({ toString() { throw conversionError; }}); }\n\
               catch (error) { preservesConversionError = error === conversionError; }\n\
               let rejectsSymbol = false;\n\
               try { document.getElementById(Symbol('target')); }\n\
               catch (error) { rejectsSymbol = error instanceof TypeError; }\n\
               let rejectsMissing = false;\n\
               try { document.getElementById(); }\n\
               catch (error) { rejectsMissing = error instanceof TypeError; }\n\
               const found = document.getElementById({\n\
                 toString() { return 'target'; }\n\
               });\n\
               found.marker = 29;\n\
               const elementPrototype = Object.getPrototypeOf(found);\n\
               const localNameDescriptor = Object.getOwnPropertyDescriptor(\n\
                 elementPrototype, 'localName');\n\
               const tagNameDescriptor = Object.getOwnPropertyDescriptor(\n\
                 elementPrototype, 'tagName');\n\
               const idDescriptor = Object.getOwnPropertyDescriptor(\n\
                 elementPrototype, 'id');\n\
               const classNameDescriptor = Object.getOwnPropertyDescriptor(\n\
                 elementPrototype, 'className');\n\
               const hasAttributesDescriptor = Object.getOwnPropertyDescriptor(\n\
                 elementPrototype, 'hasAttributes');\n\
               const getAttributeDescriptor = Object.getOwnPropertyDescriptor(\n\
                 elementPrototype, 'getAttribute');\n\
               const hasAttributeDescriptor = Object.getOwnPropertyDescriptor(\n\
                 elementPrototype, 'hasAttribute');\n\
               const firstElementChildDescriptor = Object.getOwnPropertyDescriptor(\n\
                 elementPrototype, 'firstElementChild');\n\
               const lastElementChildDescriptor = Object.getOwnPropertyDescriptor(\n\
                 elementPrototype, 'lastElementChild');\n\
               const childElementCountDescriptor = Object.getOwnPropertyDescriptor(\n\
                 elementPrototype, 'childElementCount');\n\
               const nodePrototype = Object.getPrototypeOf(elementPrototype);\n\
               const nodeTypeDescriptor = Object.getOwnPropertyDescriptor(\n\
                 nodePrototype, 'nodeType');\n\
               const nodeNameDescriptor = Object.getOwnPropertyDescriptor(\n\
                 nodePrototype, 'nodeName');\n\
               const isConnectedDescriptor = Object.getOwnPropertyDescriptor(\n\
                 nodePrototype, 'isConnected');\n\
               const textContentDescriptor = Object.getOwnPropertyDescriptor(\n\
                 nodePrototype, 'textContent');\n\
               const hasChildNodesDescriptor = Object.getOwnPropertyDescriptor(\n\
                 nodePrototype, 'hasChildNodes');\n\
               const initialNodeValues = found.nodeType === 1 &&\n\
                 found.nodeName === 'DIV' && found.isConnected === true &&\n\
                 found.textContent === 'probe text' && found.hasChildNodes();\n\
               const firstElementChild = found.firstElementChild;\n\
               const lastElementChild = found.lastElementChild;\n\
               firstElementChild.marker = 31;\n\
               lastElementChild.marker = 37;\n\
               const initialParentNodeValues = found.childElementCount === 2 &&\n\
                 firstElementChild.tagName === 'SPAN' &&\n\
                 firstElementChild.id === 'first-child' &&\n\
                 lastElementChild.tagName === 'EM' &&\n\
                 lastElementChild.id === 'last-child' &&\n\
                 firstElementChild !== lastElementChild &&\n\
                 found.firstElementChild === firstElementChild &&\n\
                 found.lastElementChild === lastElementChild &&\n\
                 found.firstElementChild.marker === 31 &&\n\
                 found.lastElementChild.marker === 37;\n\
               const documentParentNodeValues = document.childElementCount === 1 &&\n\
                 document.firstElementChild === document.documentElement &&\n\
                 document.lastElementChild === document.documentElement;\n\
               globalThis.elementWrongBrandStringified = false;\n\
               let elementRejectsWrongBrand = false;\n\
               try { getAttributeDescriptor.value.call({}, { toString() {\n\
                 elementWrongBrandStringified = true; return 'id';\n\
               }}); } catch (error) { elementRejectsWrongBrand = error instanceof TypeError; }\n\
               let setterRejectsWrongBrand = false;\n\
               try { idDescriptor.set.call({}, { toString() {\n\
                 elementWrongBrandStringified = true; return 'changed';\n\
               }}); } catch (error) { setterRejectsWrongBrand = error instanceof TypeError; }\n\
               const elementConversionError = new Error('element conversion sentinel');\n\
               let preservesElementConversionError = false;\n\
               try { found.getAttribute({ toString() { throw elementConversionError; }}); }\n\
               catch (error) { preservesElementConversionError = error === elementConversionError; }\n\
               let elementRejectsSymbol = false;\n\
               try { found.hasAttribute(Symbol('id')); }\n\
               catch (error) { elementRejectsSymbol = error instanceof TypeError; }\n\
               let elementRejectsMissing = false;\n\
               try { found.getAttribute(); }\n\
               catch (error) { elementRejectsMissing = error instanceof TypeError; }\n\
               const convertedAttribute = found.getAttribute({\n\
                 toString() { return 'DATA-PROOF'; }\n\
               });\n\
               globalThis.nodeWrongBrandStringified = false;\n\
               let nodeRejectsWrongBrand = false;\n\
               try { textContentDescriptor.set.call({}, { toString() {\n\
                 nodeWrongBrandStringified = true; return 'bad';\n\
               }}); } catch (error) { nodeRejectsWrongBrand = error instanceof TypeError; }\n\
               const nodeConversionError = new Error('node conversion sentinel');\n\
               let preservesNodeConversionError = false;\n\
               try { textContentDescriptor.set.call(found, {\n\
                 toString() { throw nodeConversionError; }\n\
               }); } catch (error) { preservesNodeConversionError = error === nodeConversionError; }\n\
               let nodeRejectsSymbol = false;\n\
               try { found.textContent = Symbol('text'); }\n\
               catch (error) { nodeRejectsSymbol = error instanceof TypeError; }\n\
               const textSetterResult = textContentDescriptor.set.call(found, {\n\
                 toString() { return 'changed text'; }\n\
               });\n\
               const textMutationWorked = textSetterResult === undefined &&\n\
                 found.textContent === 'changed text' && found.hasChildNodes();\n\
               found.textContent = undefined;\n\
               const undefinedTextWorked = found.textContent === '' &&\n\
                 !found.hasChildNodes();\n\
               found.textContent = 'missing argument reset';\n\
               const missingTextSetterResult = textContentDescriptor.set.call(found);\n\
               const missingTextWorked = missingTextSetterResult === undefined &&\n\
                 found.textContent === '' && !found.hasChildNodes();\n\
               found.textContent = 'null reset';\n\
               found.textContent = null;\n\
               const nullTextWorked = found.textContent === '' && !found.hasChildNodes();\n\
               const parentNodeChildrenCleared = found.childElementCount === 0 &&\n\
                 found.firstElementChild === null && found.lastElementChild === null;\n\
               found.id = { toString() { return 'changed-id'; }};\n\
               found.className = null;\n\
               globalThis.getElementByIdBindingProof =\n\
                 !Object.hasOwn(document, 'getElementById') && descriptor &&\n\
                 descriptor.writable && descriptor.enumerable &&\n\
                 descriptor.configurable && descriptor.value.length === 1 &&\n\
                 descriptor.value.name === 'getElementById' &&\n\
                 rejectsWrongBrand && !getElementByIdWrongBrandStringified &&\n\
                 preservesConversionError && rejectsSymbol && rejectsMissing &&\n\
                 found.tagName === 'DIV' &&\n\
                 found.localName === 'div' && found.id === 'changed-id' &&\n\
                 found.className === 'null' && found.hasAttributes() === true &&\n\
                 found.getAttribute('id') === 'changed-id' &&\n\
                 found.getAttribute('class') === 'null' &&\n\
                 found.getAttribute('missing') === null &&\n\
                 found.hasAttribute('ID') && !found.hasAttribute('missing') &&\n\
                 convertedAttribute === 'present' &&\n\
                 initialNodeValues && textMutationWorked && undefinedTextWorked &&\n\
                 missingTextWorked && nullTextWorked && parentNodeChildrenCleared &&\n\
                 initialParentNodeValues && documentParentNodeValues &&\n\
                 Object.getPrototypeOf(nodePrototype) === Object.prototype &&\n\
                 !Object.hasOwn(found, 'localName') &&\n\
                 !Object.hasOwn(found, 'tagName') && !Object.hasOwn(found, 'id') &&\n\
                 !Object.hasOwn(found, 'className') &&\n\
                 !Object.hasOwn(found, 'hasAttributes') &&\n\
                 !Object.hasOwn(found, 'getAttribute') &&\n\
                 !Object.hasOwn(found, 'hasAttribute') &&\n\
                 !Object.hasOwn(found, 'firstElementChild') &&\n\
                 !Object.hasOwn(found, 'lastElementChild') &&\n\
                 !Object.hasOwn(found, 'childElementCount') &&\n\
                 !Object.hasOwn(found, 'nodeType') &&\n\
                 !Object.hasOwn(found, 'nodeName') &&\n\
                 !Object.hasOwn(found, 'isConnected') &&\n\
                 !Object.hasOwn(found, 'textContent') &&\n\
                 !Object.hasOwn(found, 'hasChildNodes') &&\n\
                 localNameDescriptor && localNameDescriptor.get.length === 0 &&\n\
                 localNameDescriptor.set === undefined &&\n\
                 tagNameDescriptor && tagNameDescriptor.get.length === 0 &&\n\
                 tagNameDescriptor.set === undefined &&\n\
                 idDescriptor && idDescriptor.get.length === 0 &&\n\
                 idDescriptor.set.length === 1 &&\n\
                 classNameDescriptor && classNameDescriptor.get.length === 0 &&\n\
                 classNameDescriptor.set.length === 1 &&\n\
                 [localNameDescriptor, tagNameDescriptor, idDescriptor,\n\
                  classNameDescriptor].every(d => d.enumerable && d.configurable) &&\n\
                 hasAttributesDescriptor.value.name === 'hasAttributes' &&\n\
                 hasAttributesDescriptor.value.length === 0 &&\n\
                 getAttributeDescriptor.value.name === 'getAttribute' &&\n\
                 getAttributeDescriptor.value.length === 1 &&\n\
                 hasAttributeDescriptor.value.name === 'hasAttribute' &&\n\
                 hasAttributeDescriptor.value.length === 1 &&\n\
                 [hasAttributesDescriptor, getAttributeDescriptor,\n\
                  hasAttributeDescriptor].every(d => d.writable &&\n\
                    d.enumerable && d.configurable) &&\n\
                 [firstElementChildDescriptor, lastElementChildDescriptor,\n\
                  childElementCountDescriptor, documentFirstDescriptor,\n\
                  documentLastDescriptor, documentCountDescriptor].every(d =>\n\
                    d && d.get.length === 0 && d.set === undefined &&\n\
                    d.enumerable && d.configurable) &&\n\
                 [nodeTypeDescriptor, nodeNameDescriptor,\n\
                  isConnectedDescriptor].every(d => d && d.get.length === 0 &&\n\
                    d.set === undefined && d.enumerable && d.configurable) &&\n\
                 textContentDescriptor && textContentDescriptor.get.length === 0 &&\n\
                 textContentDescriptor.set.length === 1 &&\n\
                 textContentDescriptor.enumerable && textContentDescriptor.configurable &&\n\
                 hasChildNodesDescriptor.value.name === 'hasChildNodes' &&\n\
                 hasChildNodesDescriptor.value.length === 0 &&\n\
                 hasChildNodesDescriptor.writable &&\n\
                 hasChildNodesDescriptor.enumerable &&\n\
                 hasChildNodesDescriptor.configurable &&\n\
                 elementRejectsWrongBrand && setterRejectsWrongBrand &&\n\
                 !elementWrongBrandStringified && preservesElementConversionError &&\n\
                 elementRejectsSymbol && elementRejectsMissing &&\n\
                 nodeRejectsWrongBrand && !nodeWrongBrandStringified &&\n\
                 preservesNodeConversionError && nodeRejectsSymbol &&\n\
                 found !== document.documentElement && found !== document.head &&\n\
                 document.getElementById('target') === found &&\n\
                 document.getElementById('target').marker === 29 &&\n\
                 document.getElementById('missing') === null &&\n\
                 document.getElementById('') === null &&\n\
                 document.getElementById('a\\0b') === null &&\n\
                 document.getElementById('\\uD800') === null;\n\
             })();",
            "get-element-by-id-binding.js",
            1,
        ));
        let mut host_context_token = 0_u8;
        // SAFETY: The token remains live for this synchronous run, and the
        // probe validates but does not dereference or retain it.
        let operation_outcome = unsafe {
            runtime.run_script_in_realm_with_host_context(
                realm,
                operation_script,
                (&mut host_context_token as *mut u8).cast(),
            )
        }
        .unwrap();
        assert_eq!(operation_outcome, ScriptRunOutcome::Completed);
        assert!(
            runtime
                .eval_bool_in_realm(realm, "getElementByIdBindingProof")
                .unwrap()
        );
        assert_eq!(
            &*get_element_by_id_calls.borrow(),
            &[
                "target", "target", "target", "target", "target", "target", "missing", "", "a\0b",
                "\u{fffd}",
            ]
        );
        assert!(
            runtime
                .eval_bool_in_realm(realm, "document.documentElement.tagName === 'HTML'")
                .unwrap()
        );
        assert!(
            runtime
                .eval_bool_in_realm(
                    realm,
                    "document.head.tagName === 'HEAD' && \
                     document.head !== document.documentElement && \
                     document.head === document.head",
                )
                .unwrap()
        );
        assert!(
            runtime
                .eval_bool_in_realm(
                    realm,
                    "document.head.marker = 23; document.head.marker === 23",
                )
                .unwrap()
        );
        // A property set on the wrapper survives a re-read, which an
        // equal-but-distinct object would not manage.
        assert!(
            runtime
                .eval_bool_in_realm(
                    realm,
                    "document.documentElement.marker = 17; \
                     document.documentElement.marker === 17"
                )
                .unwrap()
        );
        // The wrapper is an object of its own, not the document facade.
        assert!(
            runtime
                .eval_bool_in_realm(
                    realm,
                    "document.documentElement !== document && \
                     typeof document.documentElement === 'object'"
                )
                .unwrap()
        );
        // Reading tagName off a foreign receiver must not reach a host. The
        // WebIDL accessor belongs to the shared Element prototype.
        assert!(
            runtime
                .eval_bool_in_realm(
                    realm,
                    "(() => { \
                       const descriptor = Object.getOwnPropertyDescriptor( \
                         Object.getPrototypeOf(document.documentElement), 'tagName'); \
                       try { descriptor.get.call({}); } \
                       catch (error) { return error instanceof TypeError; } \
                       return false; \
                     })()"
                )
                .unwrap()
        );

        // Realm destruction must release every element host synchronously,
        // not whenever the next collection happens. Each host roots its
        // element, and through it the tree, so waiting for a GC would pin a
        // destroyed pipeline's DOM for as long as the isolate stays idle.
        // Every read past the first hits the cache, and each hit drops the
        // host that read speculatively allocated -- so those drops have
        // already happened, and exactly five live hosts remain: the document
        // element, head, target, and the target's two Element children.
        let dropped_on_cache_hits = element_drops.get();
        assert!(
            dropped_on_cache_hits > 0,
            "cache hits must drop the surplus host they allocated"
        );
        assert_eq!(drop_reentry_attempts.borrow().len(), dropped_on_cache_hits);
        assert!(
            drop_reentry_attempts
                .borrow()
                .iter()
                .all(|(status, error)| {
                    *status == 0 && error.contains("re-entered from a Rust host callback")
                })
        );
        runtime.destroy_realm(realm).unwrap();
        assert_eq!(
            element_drops.get(),
            dropped_on_cache_hits + 5,
            "realm destruction must release all five cached hosts, synchronously"
        );
        assert_eq!(drop_reentry_attempts.borrow().len(), element_drops.get());
        assert!(
            drop_reentry_attempts
                .borrow()
                .iter()
                .all(|(status, error)| {
                    *status == 0 && error.contains("re-entered from a Rust host callback")
                })
        );

        let nullable_realm = runtime.create_realm().unwrap();
        let mut nullable_host = DocumentHostProbe::new(
            Rc::new(Cell::new(false)),
            Rc::new(Cell::new(0)),
            Rc::new(Cell::new(0)),
        );
        nullable_host.document_element_present = false;
        nullable_host.head_present = false;
        runtime
            .install_document_host(nullable_realm, nullable_host)
            .unwrap();
        assert!(
            runtime
                .eval_bool_in_realm(
                    nullable_realm,
                    "document.documentElement === null && document.head === null && \
                     document.firstElementChild === null && \
                     document.lastElementChild === null && \
                     document.childElementCount === 0",
                )
                .unwrap()
        );
        runtime.destroy_realm(nullable_realm).unwrap();
        drop(runtime);
    }

    #[test]
    fn query_selector_all_exposes_static_iterable_node_lists() {
        let mut runtime = Runtime::new(Options {
            expose_gc: 1,
            ..Options::default()
        })
        .unwrap();
        runtime
            .install_element_host::<ElementHostProbe>()
            .expect("Element host vtable installs once");

        let mut incomplete = node_list_host_vtable::<NodeListHostProbe>();
        incomplete.item = None;
        let mut storage = [0; ERROR_CAPACITY];
        let mut error = error_buffer(&mut storage);
        // SAFETY: This deliberately incomplete table is borrowed only for the
        // validating install call and is never retained after rejection.
        assert_eq!(
            unsafe {
                servo_v8_install_node_list_host(
                    runtime.raw.as_ptr(),
                    &incomplete,
                    &mut error,
                )
            },
            0
        );
        assert!(text_from(&storage, &error).contains("vtable is incomplete"));
        runtime
            .install_node_list_host::<NodeListHostProbe>()
            .expect("NodeList host vtable installs once");
        assert!(
            runtime
                .install_node_list_host::<NodeListHostProbe>()
                .is_err()
        );

        let realm = runtime.create_realm().unwrap();
        let document_drops = Rc::new(Cell::new(0));
        let element_drops = Rc::new(Cell::new(0));
        let node_list_drops = Rc::new(Cell::new(0));
        let mut document = DocumentHostProbe::new(
            Rc::new(Cell::new(false)),
            Rc::new(Cell::new(0)),
            Rc::clone(&document_drops),
        );
        document.element_drops = Rc::clone(&element_drops);
        document.node_list_drops = Rc::clone(&node_list_drops);
        runtime.install_document_host(realm, document).unwrap();

        let script = compiled(runtime.compile_script_in_realm(
            realm,
            "(() => {\n\
               const documentPrototype = Object.getPrototypeOf(document);\n\
               const documentQsa = Object.getOwnPropertyDescriptor(\n\
                 documentPrototype, 'querySelectorAll');\n\
               let documentWrongBrandStringified = false;\n\
               let documentWrongBrand = false;\n\
               try { documentQsa.value.call({}, { toString() {\n\
                 documentWrongBrandStringified = true; return '*';\n\
               }}); } catch (error) { documentWrongBrand = error instanceof TypeError; }\n\
               const conversionSentinel = new Error('qsa conversion');\n\
               let conversionPreserved = false;\n\
               try { document.querySelectorAll({ toString() { throw conversionSentinel; }}); }\n\
               catch (error) { conversionPreserved = error === conversionSentinel; }\n\
               let missingRejected = false;\n\
               try { document.querySelectorAll(); }\n\
               catch (error) { missingRejected = error instanceof TypeError; }\n\
               let symbolRejected = false;\n\
               try { document.querySelectorAll(Symbol('selector')); }\n\
               catch (error) { symbolRejected = error instanceof TypeError; }\n\
               let syntaxRejected = false;\n\
               try { document.querySelectorAll('['); }\n\
               catch (error) { syntaxRejected = error instanceof DOMException &&\n\
                 error.name === 'SyntaxError' && error.code === 12; }\n\
\n\
               const all = document.querySelectorAll('*');\n\
               const fresh = document.querySelectorAll('*');\n\
               const target = document.querySelector('#target');\n\
               const targetList = document.querySelectorAll('#target');\n\
               const elementQsa = Object.getOwnPropertyDescriptor(\n\
                 Object.getPrototypeOf(target), 'querySelectorAll');\n\
               let elementWrongBrandStringified = false;\n\
               let elementWrongBrand = false;\n\
               try { elementQsa.value.call({}, { toString() {\n\
                 elementWrongBrandStringified = true; return '*';\n\
               }}); } catch (error) { elementWrongBrand = error instanceof TypeError; }\n\
               let elementSyntaxRejected = false;\n\
               try { target.querySelectorAll('['); }\n\
               catch (error) { elementSyntaxRejected = error instanceof DOMException &&\n\
                 error.name === 'SyntaxError'; }\n\
               const descendants = target.querySelectorAll('*');\n\
               const firstDescendant = descendants[0];\n\
               const secondDescendant = descendants.item(1);\n\
               target.textContent = '';\n\
               const staticSnapshot = descendants.length === 2 &&\n\
                 descendants[0] === firstDescendant &&\n\
                 descendants.item(1) === secondDescendant &&\n\
                 firstDescendant.tagName === 'SPAN' &&\n\
                 secondDescendant.tagName === 'EM';\n\
\n\
               const listPrototype = NodeList.prototype;\n\
               const lengthDescriptor = Object.getOwnPropertyDescriptor(\n\
                 listPrototype, 'length');\n\
               const itemDescriptor = Object.getOwnPropertyDescriptor(\n\
                 listPrototype, 'item');\n\
               const iterableNames = ['values', 'keys', 'entries', 'forEach'];\n\
               const iterableDescriptors = iterableNames.every(name => {\n\
                 const descriptor = Object.getOwnPropertyDescriptor(listPrototype, name);\n\
                 return descriptor && descriptor.enumerable && descriptor.writable &&\n\
                   descriptor.configurable && descriptor.value === Array.prototype[name];\n\
               });\n\
               const indexDescriptor = Object.getOwnPropertyDescriptor(all, '0');\n\
               let illegalCall = false;\n\
               let illegalConstruct = false;\n\
               try { NodeList(); } catch (error) { illegalCall = error instanceof TypeError; }\n\
               try { new NodeList(); } catch (error) { illegalConstruct = error instanceof TypeError; }\n\
               let itemWrongBrandTouched = false;\n\
               let itemWrongBrand = false;\n\
               try { itemDescriptor.value.call({}, { valueOf() {\n\
                 itemWrongBrandTouched = true; return 0;\n\
               }}); } catch (error) { itemWrongBrand = error instanceof TypeError; }\n\
               let itemMissing = false;\n\
               let itemSymbol = false;\n\
               try { all.item(); } catch (error) { itemMissing = error instanceof TypeError; }\n\
               try { all.item(Symbol('index')); }\n\
               catch (error) { itemSymbol = error instanceof TypeError; }\n\
               let lengthWrongBrand = false;\n\
               try { lengthDescriptor.get.call({}); }\n\
               catch (error) { lengthWrongBrand = error instanceof TypeError; }\n\
\n\
               const iterated = Array.from(all);\n\
               const keys = Array.from(all.keys());\n\
               const entries = Array.from(all.entries());\n\
               let forEachGood = true;\n\
               const seen = [];\n\
               all.forEach((value, index, receiver) => {\n\
                 forEachGood &&= receiver === all && value === all[index];\n\
                 seen.push(value);\n\
               });\n\
               const genericIterator = listPrototype.values.call({\n\
                 0: 'generic', length: 1\n\
               });\n\
               globalThis.keptNodeList = all;\n\
               globalThis.nodeListProof =\n\
                 documentQsa && documentQsa.value.name === 'querySelectorAll' &&\n\
                 documentQsa.value.length === 1 && documentQsa.enumerable &&\n\
                 documentQsa.writable && documentQsa.configurable &&\n\
                 elementQsa && elementQsa.value.length === 1 &&\n\
                 documentWrongBrand && !documentWrongBrandStringified &&\n\
                 elementWrongBrand && !elementWrongBrandStringified &&\n\
                 conversionPreserved && missingRejected && symbolRejected &&\n\
                 syntaxRejected && elementSyntaxRejected && staticSnapshot &&\n\
                 all !== fresh && all instanceof NodeList &&\n\
                 Object.getPrototypeOf(all) === listPrototype &&\n\
                 Object.prototype.toString.call(all) === '[object NodeList]' &&\n\
                 NodeList.name === 'NodeList' && NodeList.length === 0 &&\n\
                 listPrototype.constructor === NodeList &&\n\
                 illegalCall && illegalConstruct &&\n\
                 lengthDescriptor && lengthDescriptor.enumerable &&\n\
                 lengthDescriptor.configurable && lengthDescriptor.set === undefined &&\n\
                 itemDescriptor && itemDescriptor.enumerable && itemDescriptor.writable &&\n\
                 itemDescriptor.configurable && itemDescriptor.value.length === 1 &&\n\
                 iterableDescriptors && listPrototype.values === listPrototype[Symbol.iterator] &&\n\
                 all.length === 3 && Object.keys(all).join(',') === '0,1,2' &&\n\
                 indexDescriptor && !indexDescriptor.writable &&\n\
                 indexDescriptor.enumerable && indexDescriptor.configurable &&\n\
                 all[0] === document.documentElement && all[2] === target &&\n\
                 all.item(0) === all[0] && all.item(3) === null &&\n\
                 targetList.length === 1 && targetList[0] === target &&\n\
                 itemWrongBrand && !itemWrongBrandTouched && itemMissing && itemSymbol &&\n\
                 lengthWrongBrand && iterated.length === 3 &&\n\
                 iterated.every((value, index) => value === all[index]) &&\n\
                 keys.join(',') === '0,1,2' && entries.length === 3 &&\n\
                 entries.every((entry, index) => entry[0] === index &&\n\
                   entry[1] === all[index]) && forEachGood && seen.length === 3 &&\n\
                 genericIterator.next().value === 'generic';\n\
             })();",
            "query-selector-all.js",
            1,
        ));
        let mut host_context_token = 0_u8;
        // SAFETY: The token remains live for this one synchronous host entry.
        let outcome = unsafe {
            runtime.run_script_in_realm_with_host_context(
                realm,
                script,
                (&mut host_context_token as *mut u8).cast(),
            )
        }
        .unwrap();
        assert_eq!(outcome, ScriptRunOutcome::Completed);
        assert!(runtime.eval_bool_in_realm(realm, "nodeListProof").unwrap());

        runtime.collect_garbage_for_testing();
        let dropped_after_unreachable_lists = node_list_drops.get();
        assert!(dropped_after_unreachable_lists > 0);
        assert!(
            runtime
                .eval_bool_in_realm(realm, "keptNodeList.length === 3")
                .unwrap()
        );
        assert!(
            runtime
                .eval_bool_in_realm(realm, "globalThis.keptNodeList = null; true")
                .unwrap()
        );
        runtime.collect_garbage_for_testing();
        assert!(node_list_drops.get() > dropped_after_unreachable_lists);

        let teardown_script = compiled(runtime.compile_script_in_realm(
            realm,
            "globalThis.teardownNodeList = document.querySelectorAll('#target');",
            "query-selector-all-teardown.js",
            1,
        ));
        // SAFETY: The token remains live for this one synchronous host entry.
        assert_eq!(
            unsafe {
                runtime.run_script_in_realm_with_host_context(
                    realm,
                    teardown_script,
                    (&mut host_context_token as *mut u8).cast(),
                )
            }
            .unwrap(),
            ScriptRunOutcome::Completed
        );
        let before_teardown = node_list_drops.get();
        runtime.destroy_realm(realm).unwrap();
        assert_eq!(node_list_drops.get(), before_teardown + 1);
        assert_eq!(document_drops.get(), 1);
    }

    #[test]
    fn document_hosts_are_realm_local_live_and_dropped_synchronously() {
        let options = Options {
            expose_gc: 1,
            ..Options::default()
        };
        let mut runtime = Runtime::new(options).unwrap();
        let first = runtime.create_realm().unwrap();
        let second = runtime.create_realm().unwrap();

        let first_hidden = Rc::new(Cell::new(false));
        let first_getter_calls = Rc::new(Cell::new(0));
        let first_drops = Rc::new(Cell::new(0));
        let second_hidden = Rc::new(Cell::new(true));
        let second_getter_calls = Rc::new(Cell::new(0));
        let second_drops = Rc::new(Cell::new(0));

        runtime
            .install_document_host(
                first,
                DocumentHostProbe::new(
                    Rc::clone(&first_hidden),
                    Rc::clone(&first_getter_calls),
                    Rc::clone(&first_drops),
                ),
            )
            .unwrap();
        runtime
            .install_document_host(
                second,
                DocumentHostProbe::new(
                    Rc::clone(&second_hidden),
                    Rc::clone(&second_getter_calls),
                    Rc::clone(&second_drops),
                ),
            )
            .unwrap();

        assert!(!runtime.document_hidden(first).unwrap());
        assert!(runtime.document_hidden(second).unwrap());
        assert_eq!(first_getter_calls.get(), 1);
        assert_eq!(second_getter_calls.get(), 1);

        first_hidden.set(true);
        second_hidden.set(false);
        assert!(runtime.document_hidden(first).unwrap());
        assert!(!runtime.document_hidden(second).unwrap());
        assert_eq!(first_getter_calls.get(), 2);
        assert_eq!(second_getter_calls.get(), 2);

        assert!(
            runtime
                .eval_bool_in_realm(
                    first,
                    "(() => {\n\
                       const prototype = Object.getPrototypeOf(document);\n\
                       const descriptor =\n\
                         Object.getOwnPropertyDescriptor(prototype, 'hidden');\n\
                       if (Object.hasOwn(document, 'hidden') ||\n\
                           !descriptor || !descriptor.enumerable ||\n\
                           !descriptor.configurable || descriptor.set !== undefined ||\n\
                           descriptor.get.length !== 0 ||\n\
                           descriptor.get.name !== 'get hidden') return false;\n\
                       let rejectsPlainObject = false;\n\
                       let rejectsDerivedObject = false;\n\
                       try { descriptor.get.call({}); }\n\
                       catch (error) { rejectsPlainObject = error instanceof TypeError; }\n\
                       try { Object.create(document).hidden; }\n\
                       catch (error) { rejectsDerivedObject = error instanceof TypeError; }\n\
                       return rejectsPlainObject && rejectsDerivedObject;\n\
                     })()",
                )
                .unwrap()
        );
        assert_eq!(first_getter_calls.get(), 2);

        let missing_context = compiled(runtime.compile_script_in_realm(
            first,
            "document.bgColor = 'must-not-set';",
            "missing-host-context.js",
            1,
        ));
        let ScriptRunOutcome::Thrown(missing_context_error) =
            runtime.run_script_in_realm(first, missing_context).unwrap()
        else {
            panic!("Document.bgColor setter ran without a host context");
        };
        assert!(
            missing_context_error
                .message
                .contains("Document.bgColor host callback failed")
        );
        assert!(
            runtime
                .eval_bool_in_realm(first, "document.bgColor === 'red'")
                .unwrap()
        );

        let missing_title_context = compiled(runtime.compile_script_in_realm(
            first,
            "document.title = 'must-not-set';",
            "missing-title-host-context.js",
            1,
        ));
        let ScriptRunOutcome::Thrown(missing_title_context_error) = runtime
            .run_script_in_realm(first, missing_title_context)
            .unwrap()
        else {
            panic!("Document.title setter ran without a host context");
        };
        assert!(
            missing_title_context_error
                .message
                .contains("Document.title host callback failed")
        );
        assert!(
            runtime
                .eval_bool_in_realm(first, "document.title === 'probe title'")
                .unwrap()
        );

        // Document.URL is the first read-only member added through the shape
        // manifest. It shares the owned-UTF-8 transfer with bgColor's getter
        // but has no setter, so the descriptor must expose a getter alone, and
        // assigning to it must be silently ignored rather than mutate anything.
        assert!(
            runtime
                .eval_bool_in_realm(
                    first,
                    "document.URL === 'https://example.com/probe?q=\u{2713}'"
                )
                .unwrap()
        );
        assert!(
            runtime
                .eval_bool_in_realm(
                    first,
                    "document.documentURI === document.URL && \
                     document.compatMode === 'CSS1Compat' && \
                     document.characterSet === 'UTF-8' && \
                     document.charset === document.characterSet && \
                     document.inputEncoding === document.characterSet && \
                     document.contentType === 'text/html' && \
                     document.referrer === '' && \
                     document.lastModified === '01/02/2026 03:04:05'",
                )
                .unwrap()
        );
        // The enum crosses the ABI as a string, so the host must only ever
        // produce a value the selector pinned.
        assert!(
            runtime
                .eval_bool_in_realm(
                    first,
                    "['visible', 'hidden'].includes(document.visibilityState)"
                )
                .unwrap()
        );
        assert!(
            runtime
                .eval_bool_in_realm(
                    first,
                    "document.readyState === 'complete' && \
                     ['loading', 'interactive', 'complete'].includes(document.readyState)",
                )
                .unwrap()
        );
        // Document inherits Node, so nodeType is served by the same facade and
        // must arrive as a number rather than a string.
        assert!(
            runtime
                .eval_bool_in_realm(
                    first,
                    "document.nodeType === 9 && typeof document.nodeType === 'number'"
                )
                .unwrap()
        );
        assert!(
            runtime
                .eval_bool_in_realm(
                    first,
                    "(() => {\n\
                       const descriptor = Object.getOwnPropertyDescriptor(\n\
                         Object.getPrototypeOf(document), 'URL');\n\
                       if (typeof descriptor.get !== 'function') return false;\n\
                       if (descriptor.set !== undefined) return false;\n\
                       if (!descriptor.enumerable || !descriptor.configurable) return false;\n\
                       let rejectsWrongBrand = false;\n\
                       try {\n\
                         descriptor.get.call({});\n\
                       } catch (error) {\n\
                         rejectsWrongBrand = error instanceof TypeError;\n\
                       }\n\
                       return rejectsWrongBrand;\n\
                     })()"
                )
                .unwrap()
        );

        let bg_color_script = compiled(runtime.compile_script_in_realm(
            first,
            "(() => {\n\
               const descriptor = Object.getOwnPropertyDescriptor(\n\
                 Object.getPrototypeOf(document), 'bgColor');\n\
               const titleDescriptor = Object.getOwnPropertyDescriptor(\n\
                 Object.getPrototypeOf(document), 'title');\n\
               globalThis.bgColorBrandStringified = false;\n\
               globalThis.titleBrandStringified = false;\n\
               let rejectsWrongBrand = false;\n\
               try {\n\
                 descriptor.set.call({}, { toString() {\n\
                   bgColorBrandStringified = true; return 'bad';\n\
                 }});\n\
               } catch (error) { rejectsWrongBrand = error instanceof TypeError; }\n\
               const conversionError = new Error('conversion sentinel');\n\
               let preservesConversionError = false;\n\
               try {\n\
                 descriptor.set.call(document, { toString() { throw conversionError; }});\n\
               } catch (error) { preservesConversionError = error === conversionError; }\n\
               let rejectsSymbol = false;\n\
               try { document.bgColor = Symbol('color'); }\n\
               catch (error) { rejectsSymbol = error instanceof TypeError; }\n\
               let titleRejectsWrongBrand = false;\n\
               try {\n\
                 titleDescriptor.set.call({}, { toString() {\n\
                   titleBrandStringified = true; return 'bad';\n\
                 }});\n\
               } catch (error) { titleRejectsWrongBrand = error instanceof TypeError; }\n\
               let titlePreservesConversionError = false;\n\
               try {\n\
                 titleDescriptor.set.call(document, {\n\
                   toString() { throw conversionError; }\n\
                 });\n\
               } catch (error) {\n\
                 titlePreservesConversionError = error === conversionError;\n\
               }\n\
               let titleRejectsSymbol = false;\n\
               try { document.title = Symbol('title'); }\n\
               catch (error) { titleRejectsSymbol = error instanceof TypeError; }\n\
               document.title = null;\n\
               const titleNullBecameString = document.title === 'null';\n\
               let titleConversions = 0;\n\
               document.title = { toString() {\n\
                 titleConversions++; return 'V8 title ✓';\n\
               }};\n\
               globalThis.titleBindingProof =\n\
                 !Object.hasOwn(document, 'title') && titleDescriptor &&\n\
                 titleDescriptor.enumerable && titleDescriptor.configurable &&\n\
                 titleDescriptor.get.length === 0 && titleDescriptor.set.length === 1 &&\n\
                 titleDescriptor.get.name === 'get title' &&\n\
                 titleDescriptor.set.name === 'set title' && titleRejectsWrongBrand &&\n\
                 !titleBrandStringified && titlePreservesConversionError &&\n\
                 titleRejectsSymbol &&\n\
                 titleNullBecameString && titleConversions === 1;\n\
               document.bgColor = null;\n\
               const nullBecameEmpty = document.bgColor === '';\n\
               document.bgColor = 'grü\\0n';\n\
               globalThis.bgColorBindingProof =\n\
                 !Object.hasOwn(document, 'bgColor') && descriptor &&\n\
                 descriptor.enumerable && descriptor.configurable &&\n\
                 descriptor.get.length === 0 && descriptor.set.length === 1 &&\n\
                 descriptor.get.name === 'get bgColor' &&\n\
                 descriptor.set.name === 'set bgColor' && rejectsWrongBrand &&\n\
                 !bgColorBrandStringified && preservesConversionError &&\n\
                 rejectsSymbol && nullBecameEmpty;\n\
             })();",
            "bg-color-binding.js",
            1,
        ));
        let mut host_context_token = 0_u8;
        // SAFETY: The token remains live for the synchronous call. The probe
        // validates but never dereferences or retains this opaque pointer.
        let bg_color_outcome = unsafe {
            runtime.run_script_in_realm_with_host_context(
                first,
                bg_color_script,
                (&mut host_context_token as *mut u8).cast(),
            )
        }
        .unwrap();
        assert_eq!(bg_color_outcome, ScriptRunOutcome::Completed);
        assert!(
            runtime
                .eval_bool_in_realm(
                    first,
                    "bgColorBindingProof && titleBindingProof && \
                     document.bgColor === 'grü\\0n' && document.title === 'V8 title ✓'",
                )
                .unwrap()
        );

        runtime.destroy_realm(first).unwrap();
        assert_eq!(first_drops.get(), 1);
        assert_eq!(second_drops.get(), 0);
        assert!(runtime.document_hidden(first).is_err());

        runtime.destroy_realm(second).unwrap();
        assert_eq!(first_drops.get(), 1);
        assert_eq!(second_drops.get(), 1);
    }

    #[test]
    fn document_host_operation_thunk_validates_abi_inputs() {
        let calls = Rc::new(RefCell::new(Vec::new()));
        let drops = Rc::new(Cell::new(0));
        let mut host = DocumentHostProbe::new(
            Rc::new(Cell::new(false)),
            Rc::new(Cell::new(0)),
            Rc::clone(&drops),
        );
        host.get_element_by_id_calls = Rc::clone(&calls);
        let native = Box::into_raw(Box::new(host)).cast::<c_void>();
        let vtable = DocumentHostVTable::for_type::<DocumentHostProbe>();
        let callback = vtable.get_element_by_id.unwrap();
        let query_callback = vtable.query_selector.unwrap();
        let query_all_callback = vtable.query_selector_all.unwrap();
        let mut host_context = 0_u8;
        let host_context = (&mut host_context as *mut u8).cast::<c_void>();
        let mut output = RawInterfaceValue {
            is_null: 0,
            key: std::ptr::null(),
            native: std::ptr::null_mut(),
        };
        let invalid_utf8 = [0xff];

        // SAFETY: Each pointer is either deliberately invalid in the precise
        // way the thunk must reject, or remains live for the synchronous call.
        unsafe {
            assert_eq!(
                callback(
                    native,
                    host_context,
                    invalid_utf8.as_ptr(),
                    invalid_utf8.len(),
                    &mut output,
                ),
                0,
            );
            assert_eq!(
                callback(native, host_context, std::ptr::null(), 1, &mut output,),
                0,
            );
            assert_eq!(
                callback(
                    native,
                    std::ptr::null_mut(),
                    std::ptr::null(),
                    0,
                    &mut output,
                ),
                0,
            );
            assert_eq!(
                callback(
                    native,
                    host_context,
                    std::ptr::null(),
                    0,
                    std::ptr::null_mut(),
                ),
                0,
            );
            // Null plus zero length is the valid ABI spelling of an empty
            // borrowed byte slice and reaches the host as an empty DOMString.
            assert_eq!(
                callback(native, host_context, std::ptr::null(), 0, &mut output,),
                1,
            );
            assert_eq!(output.is_null, 1);

            let mut query_output = RawSelectorElementOutcome {
                status: u32::MAX,
                value: RawInterfaceValue {
                    is_null: 0,
                    key: std::ptr::null(),
                    native: std::ptr::null_mut(),
                },
            };
            assert_eq!(
                query_callback(
                    native,
                    host_context,
                    invalid_utf8.as_ptr(),
                    invalid_utf8.len(),
                    &mut query_output,
                ),
                0,
            );
            assert_eq!(
                query_callback(native, host_context, std::ptr::null(), 1, &mut query_output,),
                0,
            );
            assert_eq!(
                query_callback(
                    native,
                    std::ptr::null_mut(),
                    std::ptr::null(),
                    0,
                    &mut query_output,
                ),
                0,
            );
            assert_eq!(
                query_callback(
                    native,
                    host_context,
                    std::ptr::null(),
                    0,
                    std::ptr::null_mut(),
                ),
                0,
            );
            let missing = b"missing";
            assert_eq!(
                query_callback(
                    native,
                    host_context,
                    missing.as_ptr(),
                    missing.len(),
                    &mut query_output,
                ),
                1,
            );
            assert_eq!(query_output.status, SELECTOR_RETURNED);
            assert_eq!(query_output.value.is_null, 1);
            assert_eq!(
                query_callback(native, host_context, std::ptr::null(), 0, &mut query_output,),
                1,
            );
            assert_eq!(query_output.status, SELECTOR_SYNTAX_ERROR);
            assert_eq!(query_output.value.is_null, 1);

            let mut query_all_output = RawSelectorNodeListOutcome {
                status: u32::MAX,
                native: std::ptr::null_mut(),
            };
            assert_eq!(
                query_all_callback(
                    native,
                    host_context,
                    invalid_utf8.as_ptr(),
                    invalid_utf8.len(),
                    &mut query_all_output,
                ),
                0,
            );
            assert_eq!(
                query_all_callback(
                    native,
                    host_context,
                    std::ptr::null(),
                    1,
                    &mut query_all_output,
                ),
                0,
            );
            assert_eq!(
                query_all_callback(
                    native,
                    std::ptr::null_mut(),
                    std::ptr::null(),
                    0,
                    &mut query_all_output,
                ),
                0,
            );
            assert_eq!(
                query_all_callback(
                    native,
                    host_context,
                    std::ptr::null(),
                    0,
                    std::ptr::null_mut(),
                ),
                0,
            );
            let target = b"#target";
            assert_eq!(
                query_all_callback(
                    native,
                    host_context,
                    target.as_ptr(),
                    target.len(),
                    &mut query_all_output,
                ),
                1,
            );
            assert_eq!(query_all_output.status, SELECTOR_RETURNED);
            assert!(!query_all_output.native.is_null());
            node_list_host_drop::<NodeListHostProbe>(query_all_output.native);
            assert_eq!(
                query_all_callback(
                    native,
                    host_context,
                    std::ptr::null(),
                    0,
                    &mut query_all_output,
                ),
                1,
            );
            assert_eq!(query_all_output.status, SELECTOR_SYNTAX_ERROR);
            assert!(query_all_output.native.is_null());
            vtable.drop.unwrap()(native);
        }
        assert_eq!(&*calls.borrow(), &[""]);
        assert_eq!(drops.get(), 1);
    }

    #[test]
    fn element_selector_operation_thunks_validate_abi_inputs() {
        let element_drops = Rc::new(Cell::new(0));
        let mut document = DocumentHostProbe::new(
            Rc::new(Cell::new(false)),
            Rc::new(Cell::new(0)),
            Rc::new(Cell::new(0)),
        );
        document.element_drops = Rc::clone(&element_drops);
        let mut host_context_token = 0_u8;
        let host_context = (&mut host_context_token as *mut u8).cast::<c_void>();
        // SAFETY: document and the stand-in Element identity remain live for
        // this entire synchronous thunk probe.
        let handle = unsafe { document.get_element_by_id(host_context, "target") }.unwrap();
        let native = handle.native;
        let closest = element_host_closest::<ElementHostProbe>;
        let matches = element_host_matches::<ElementHostProbe>;
        let webkit_matches = element_host_webkit_matches_selector::<ElementHostProbe>;
        let query_all = element_host_query_selector_all::<ElementHostProbe>;
        let invalid_utf8 = [0xff];
        let mut element_output = RawSelectorElementOutcome {
            status: u32::MAX,
            value: RawInterfaceValue {
                is_null: 0,
                key: std::ptr::null(),
                native: std::ptr::null_mut(),
            },
        };
        let mut boolean_output = RawSelectorBooleanOutcome {
            status: u32::MAX,
            value: u8::MAX,
        };

        // SAFETY: Each invalid pointer combination is intentional and every
        // valid byte range and output remains live for the call.
        unsafe {
            assert_eq!(
                closest(
                    native,
                    host_context,
                    invalid_utf8.as_ptr(),
                    invalid_utf8.len(),
                    &mut element_output,
                ),
                0,
            );
            assert_eq!(
                closest(
                    native,
                    std::ptr::null_mut(),
                    std::ptr::null(),
                    0,
                    &mut element_output,
                ),
                0,
            );
            assert_eq!(
                matches(
                    native,
                    host_context,
                    std::ptr::null(),
                    1,
                    &mut boolean_output,
                ),
                0,
            );
            assert_eq!(
                webkit_matches(
                    native,
                    host_context,
                    std::ptr::null(),
                    0,
                    std::ptr::null_mut(),
                ),
                0,
            );

            let self_selector = b"#target";
            assert_eq!(
                closest(
                    native,
                    host_context,
                    self_selector.as_ptr(),
                    self_selector.len(),
                    &mut element_output,
                ),
                1,
            );
            assert_eq!(element_output.status, SELECTOR_RETURNED);
            assert_eq!(element_output.value.is_null, 0);
            assert_eq!(element_output.value.key, handle.key);
            assert!(!element_output.value.native.is_null());
            element_host_drop::<ElementHostProbe>(element_output.value.native);

            assert_eq!(
                matches(
                    native,
                    host_context,
                    self_selector.as_ptr(),
                    self_selector.len(),
                    &mut boolean_output,
                ),
                1,
            );
            assert_eq!(boolean_output.status, SELECTOR_RETURNED);
            assert_eq!(boolean_output.value, 1);

            let miss = b"span";
            assert_eq!(
                webkit_matches(
                    native,
                    host_context,
                    miss.as_ptr(),
                    miss.len(),
                    &mut boolean_output,
                ),
                1,
            );
            assert_eq!(boolean_output.status, SELECTOR_RETURNED);
            assert_eq!(boolean_output.value, 0);

            let syntax = b"[";
            assert_eq!(
                matches(
                    native,
                    host_context,
                    syntax.as_ptr(),
                    syntax.len(),
                    &mut boolean_output,
                ),
                1,
            );
            assert_eq!(boolean_output.status, SELECTOR_SYNTAX_ERROR);
            assert_eq!(boolean_output.value, 0);

            let mut node_list_output = RawSelectorNodeListOutcome {
                status: u32::MAX,
                native: std::ptr::null_mut(),
            };
            assert_eq!(
                query_all(
                    native,
                    host_context,
                    invalid_utf8.as_ptr(),
                    invalid_utf8.len(),
                    &mut node_list_output,
                ),
                0,
            );
            assert_eq!(
                query_all(
                    native,
                    std::ptr::null_mut(),
                    std::ptr::null(),
                    0,
                    &mut node_list_output,
                ),
                0,
            );
            let all = b"*";
            assert_eq!(
                query_all(
                    native,
                    host_context,
                    all.as_ptr(),
                    all.len(),
                    &mut node_list_output,
                ),
                1,
            );
            assert_eq!(node_list_output.status, SELECTOR_RETURNED);
            assert!(!node_list_output.native.is_null());
            node_list_host_drop::<NodeListHostProbe>(node_list_output.native);
            element_host_drop::<ElementHostProbe>(native);
        }
        assert_eq!(element_drops.get(), 2);
    }

    #[test]
    fn selector_boolean_outcomes_reject_host_failures_and_malformed_values() {
        let mut runtime = Runtime::new(Options {
            expose_gc: 1,
            ..Options::default()
        })
        .unwrap();
        let mut vtable = element_host_vtable::<ElementHostProbe>();
        vtable.matches = Some(adversarial_element_matches);
        let mut storage = [0; ERROR_CAPACITY];
        let mut error = error_buffer(&mut storage);
        // SAFETY: The complete vtable contains callbacks for one exact host
        // type and C++ copies it synchronously.
        let installed =
            unsafe { servo_v8_install_element_host(runtime.raw.as_ptr(), &vtable, &mut error) };
        assert_eq!(
            installed,
            1,
            "custom Element vtable install failed: {:?}",
            error_from(&storage, &error)
        );

        let realm = runtime.create_realm().unwrap();
        let document_drops = Rc::new(Cell::new(0));
        let element_drops = Rc::new(Cell::new(0));
        let mut document = DocumentHostProbe::new(
            Rc::new(Cell::new(false)),
            Rc::new(Cell::new(0)),
            Rc::clone(&document_drops),
        );
        document.element_drops = Rc::clone(&element_drops);
        runtime.install_document_host(realm, document).unwrap();

        let script = compiled(runtime.compile_script_in_realm(
            realm,
            "(() => {\n\
               const target = document.getElementById('target');\n\
               const rejected = [\n\
                 'host-failure', 'invalid-status', 'invalid-value',\n\
                 'syntax-with-value', 'callback-failure'\n\
               ].every(selector => {\n\
                 try { target.matches(selector); return false; }\n\
                 catch (error) { return error instanceof TypeError; }\n\
               });\n\
               globalThis.selectorBooleanOutcomeProof =\n\
                 rejected && target.matches('#target') && !target.matches('span');\n\
             })();",
            "selector-boolean-adversarial.js",
            1,
        ));
        let mut host_context_token = 0_u8;
        // SAFETY: The token remains live for this synchronous probe.
        let outcome = unsafe {
            runtime.run_script_in_realm_with_host_context(
                realm,
                script,
                (&mut host_context_token as *mut u8).cast(),
            )
        }
        .unwrap();
        assert_eq!(outcome, ScriptRunOutcome::Completed);
        assert!(
            runtime
                .eval_bool_in_realm(realm, "selectorBooleanOutcomeProof")
                .unwrap()
        );
        assert_eq!(element_drops.get(), 0);
        runtime.destroy_realm(realm).unwrap();
        assert_eq!(element_drops.get(), 1);
        assert_eq!(document_drops.get(), 1);
    }

    #[test]
    fn query_selector_rejects_host_failures_and_malformed_owned_outcomes() {
        let mut runtime = Runtime::new(Options {
            expose_gc: 1,
            ..Options::default()
        })
        .unwrap();
        runtime
            .install_element_host::<ElementHostProbe>()
            .expect("Element host vtable installs once");
        let realm = runtime.create_realm().unwrap();
        let document_drops = Rc::new(Cell::new(0));
        let element_drops = Rc::new(Cell::new(0));
        let mut host = DocumentHostProbe::new(
            Rc::new(Cell::new(false)),
            Rc::new(Cell::new(0)),
            Rc::clone(&document_drops),
        );
        host.element_drops = Rc::clone(&element_drops);

        let mut vtable = DocumentHostVTable::for_type::<DocumentHostProbe>();
        vtable.query_selector = Some(adversarial_document_query_selector);
        let native = Box::into_raw(Box::new(host)).cast::<c_void>();
        let mut storage = [0; ERROR_CAPACITY];
        let mut error = error_buffer(&mut storage);
        // SAFETY: native is one live Box<DocumentHostProbe>, the complete
        // vtable uses that exact type, and C++ copies it on a successful
        // ownership transfer.
        let installed = unsafe {
            servo_v8_realm_install_document_host(
                runtime.raw.as_ptr(),
                realm,
                native,
                &vtable,
                &mut error,
            )
        };
        if installed == 0 {
            // SAFETY: A failed install does not consume native.
            drop(unsafe { Box::from_raw(native.cast::<DocumentHostProbe>()) });
            panic!(
                "custom Document host install failed: {:?}",
                error_from(&storage, &error)
            );
        }

        let script = compiled(runtime.compile_script_in_realm(
            realm,
            "(() => {\n\
               const malformed = [\n\
                 'invalid-status', 'syntax-with-value', 'null-with-value',\n\
                 'native-without-key', 'key-without-native'\n\
               ];\n\
               const malformedRejected = malformed.every(selector => {\n\
                 try { document.querySelector(selector); return false; }\n\
                 catch (error) { return error instanceof TypeError; }\n\
               });\n\
               let hostFailureRejected = false;\n\
               try { document.querySelector('host-failure'); }\n\
               catch (error) { hostFailureRejected = error instanceof TypeError; }\n\
               let callbackFailureRejected = false;\n\
               try { document.querySelector('callback-failure'); }\n\
               catch (error) { callbackFailureRejected = error instanceof TypeError; }\n\
               const target = document.querySelector('valid');\n\
               globalThis.adversarialQuerySelectorProof =\n\
                 malformedRejected && hostFailureRejected && callbackFailureRejected &&\n\
                 target && target.id === 'target' &&\n\
                 document.querySelector('missing') === null;\n\
             })();",
            "query-selector-adversarial.js",
            1,
        ));
        let mut host_context_token = 0_u8;
        // SAFETY: The token remains live for this synchronous probe.
        let outcome = unsafe {
            runtime.run_script_in_realm_with_host_context(
                realm,
                script,
                (&mut host_context_token as *mut u8).cast(),
            )
        }
        .unwrap();
        assert_eq!(outcome, ScriptRunOutcome::Completed);
        assert!(
            runtime
                .eval_bool_in_realm(realm, "adversarialQuerySelectorProof")
                .unwrap()
        );
        // Four malformed outcomes carried speculative native hosts. The
        // bridge must reject and drop all four without touching the one valid
        // wrapper retained by the realm.
        assert_eq!(element_drops.get(), 4);
        assert_eq!(document_drops.get(), 0);

        runtime.destroy_realm(realm).unwrap();
        assert_eq!(element_drops.get(), 5);
        assert_eq!(document_drops.get(), 1);
    }

    #[test]
    fn document_host_rejects_invalid_installs_and_runtime_drop_cleans_up() {
        let options = Options {
            expose_gc: 1,
            ..Options::default()
        };
        let mut runtime = Runtime::new(options).unwrap();
        let realm = runtime.create_realm().unwrap();
        let primary_drops = Rc::new(Cell::new(0));
        runtime
            .install_document_host(
                realm,
                DocumentHostProbe::new(
                    Rc::new(Cell::new(false)),
                    Rc::new(Cell::new(0)),
                    Rc::clone(&primary_drops),
                ),
            )
            .unwrap();

        let duplicate_drops = Rc::new(Cell::new(0));
        assert!(
            runtime
                .install_document_host(
                    realm,
                    DocumentHostProbe::new(
                        Rc::new(Cell::new(true)),
                        Rc::new(Cell::new(0)),
                        Rc::clone(&duplicate_drops),
                    ),
                )
                .is_err()
        );
        assert_eq!(duplicate_drops.get(), 1);
        assert_eq!(primary_drops.get(), 0);
        assert!(!runtime.document_hidden(realm).unwrap());

        let unknown_drops = Rc::new(Cell::new(0));
        let unknown = RealmId(u64::MAX);
        assert!(
            runtime
                .install_document_host(
                    unknown,
                    DocumentHostProbe::new(
                        Rc::new(Cell::new(false)),
                        Rc::new(Cell::new(0)),
                        Rc::clone(&unknown_drops),
                    ),
                )
                .is_err()
        );
        assert_eq!(unknown_drops.get(), 1);
        assert!(runtime.document_hidden(unknown).is_err());

        let destroyed = runtime.create_realm().unwrap();
        runtime.destroy_realm(destroyed).unwrap();
        let destroyed_drops = Rc::new(Cell::new(0));
        assert!(
            runtime
                .install_document_host(
                    destroyed,
                    DocumentHostProbe::new(
                        Rc::new(Cell::new(false)),
                        Rc::new(Cell::new(0)),
                        Rc::clone(&destroyed_drops),
                    ),
                )
                .is_err()
        );
        assert_eq!(destroyed_drops.get(), 1);
        assert!(runtime.document_hidden(destroyed).is_err());

        drop(runtime);
        assert_eq!(primary_drops.get(), 1);
        assert_eq!(duplicate_drops.get(), 1);
        assert_eq!(unknown_drops.get(), 1);
        assert_eq!(destroyed_drops.get(), 1);
    }
}
