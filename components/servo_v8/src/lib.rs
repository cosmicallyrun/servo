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

const ABI_VERSION: u32 = 14;
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

/// A host for one Servo `Element` reachable from V8.
///
/// # Safety
///
/// Implementations must stay on the owning script thread, must not unwind,
/// and must root the DOM object they read for the duration of the call. The
/// implementation is dropped from a cppgc destructor during a V8 collection,
/// so its `Drop` must not re-enter V8 or pump an event loop.
pub unsafe trait ElementHostBinding: Sized + 'static {
    fn tag_name(&self) -> String;
}

#[repr(C)]
pub struct ElementHostVTable {
    pub get_tag_name: Option<unsafe extern "C" fn(*mut c_void, *mut OwnedUtf8) -> u8>,
    pub drop: Option<DropCallback>,
}

unsafe extern "C" fn element_host_get_tag_name<T: ElementHostBinding>(
    native: *mut c_void,
    output: *mut OwnedUtf8,
) -> u8 {
    if output.is_null() {
        return 0;
    }
    // SAFETY: The vtable contract requires a live Box<T> native pointer.
    let native = unsafe { &*native.cast::<T>() };
    let owner = Box::new(native.tag_name().into_bytes());
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

unsafe extern "C" fn element_host_drop<T: ElementHostBinding>(native: *mut c_void) {
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
        let vtable = ElementHostVTable {
            get_tag_name: Some(element_host_get_tag_name::<T>),
            drop: Some(element_host_drop::<T>),
        };
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

    /// A stand-in for one Servo Element.
    struct ElementHostProbe {
        tag_name: String,
        drops: Rc<Cell<usize>>,
        drop_reentry: Option<ElementDropReentryProbe>,
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
        fn tag_name(&self) -> String {
            self.tag_name.clone()
        }
    }

    struct DocumentHostProbe {
        hidden: Rc<Cell<bool>>,
        bg_color: Rc<RefCell<String>>,
        getter_calls: Rc<Cell<usize>>,
        bg_color_getter_calls: Rc<Cell<usize>>,
        bg_color_setter_calls: Rc<Cell<usize>>,
        drops: Rc<Cell<usize>>,
        /// Stands in for the address of a Servo Element the host would root.
        /// Stable for this probe's lifetime, which is what identity needs.
        element_identity: Box<u8>,
        head_identity: Box<u8>,
        document_element_present: bool,
        head_present: bool,
        element_drops: Rc<Cell<usize>>,
        element_drop_reentry: Option<ElementDropReentryProbe>,
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
                element_identity: Box::new(0),
                head_identity: Box::new(0),
                document_element_present: true,
                head_present: true,
                element_drops: Rc::new(Cell::new(0)),
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

        fn visibility_state(&self) -> String {
            // Mirrors Servo's enum-to-string, which is what crosses the ABI.
            if self.hidden.get() {
                "hidden"
            } else {
                "visible"
            }
            .to_owned()
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
                        tag_name: "HTML".to_owned(),
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
                        tag_name: "HEAD".to_owned(),
                        drops: Rc::clone(&self.element_drops),
                        drop_reentry: self.element_drop_reentry.clone(),
                    },
                )
            })
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
        let drop_reentry_attempts = Rc::new(RefCell::new(Vec::new()));
        host.element_drop_reentry = Some(ElementDropReentryProbe {
            runtime: runtime.raw.as_ptr(),
            realm,
            attempts: Rc::clone(&drop_reentry_attempts),
        });
        runtime.install_document_host(realm, host).unwrap();

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
        // Reading tagName off a foreign receiver must not reach a host.
        assert!(
            runtime
                .eval_bool_in_realm(
                    realm,
                    "(() => { \
                       const descriptor = Object.getOwnPropertyDescriptor( \
                         document.documentElement, 'tagName'); \
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
        // already happened, and exactly two live hosts remain.
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
            dropped_on_cache_hits + 2,
            "realm destruction must release both cached hosts, synchronously"
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
                    "document.documentElement === null && document.head === null",
                )
                .unwrap()
        );
        runtime.destroy_realm(nullable_realm).unwrap();
        drop(runtime);
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
