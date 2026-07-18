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

const ABI_VERSION: u32 = 7;
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
    fn servo_v8_realm_install_document_host(
        runtime: *mut RawRuntime,
        realm_id: RealmId,
        native: *mut c_void,
        vtable: *const DocumentHostVTable,
        error: *mut ErrorBuffer,
    ) -> i32;
    fn servo_v8_realm_document_hidden(
        runtime: *mut RawRuntime,
        realm_id: RealmId,
        result: *mut u8,
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

    /// Installs a realm-owned host for Servo's production `Document.hidden`
    /// binding. After successful installation, the native host is destroyed
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

    struct DocumentHostProbe {
        hidden: Rc<Cell<bool>>,
        bg_color: Rc<RefCell<String>>,
        getter_calls: Rc<Cell<usize>>,
        bg_color_getter_calls: Rc<Cell<usize>>,
        bg_color_setter_calls: Rc<Cell<usize>>,
        drops: Rc<Cell<usize>>,
    }

    impl DocumentHostProbe {
        fn new(
            hidden: Rc<Cell<bool>>,
            getter_calls: Rc<Cell<usize>>,
            drops: Rc<Cell<usize>>,
        ) -> Self {
            Self {
                hidden,
                bg_color: Rc::new(RefCell::new("red".to_owned())),
                getter_calls,
                bg_color_getter_calls: Rc::new(Cell::new(0)),
                bg_color_setter_calls: Rc::new(Cell::new(0)),
                drops,
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

        unsafe fn set_bg_color(&self, host_context: *mut c_void, value: &str) -> bool {
            assert!(!host_context.is_null());
            self.bg_color_setter_calls
                .set(self.bg_color_setter_calls.get() + 1);
            *self.bg_color.borrow_mut() = value.to_owned();
            true
        }
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

        runtime.destroy_realm(first).unwrap();
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

        let bg_color_script = compiled(runtime.compile_script_in_realm(
            first,
            "(() => {\n\
               const descriptor = Object.getOwnPropertyDescriptor(\n\
                 Object.getPrototypeOf(document), 'bgColor');\n\
               globalThis.bgColorBrandStringified = false;\n\
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
                    "bgColorBindingProof && document.bgColor === 'grü\\0n'",
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
