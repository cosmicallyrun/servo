/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at https://mozilla.org/MPL/2.0/. */

#include "servo_v8.h"

#include <algorithm>
#include <limits>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <unordered_map>
#include <utility>
#include <vector>

#include "include/cppgc/allocation.h"
#include "include/cppgc/heap.h"
#include "include/cppgc/member.h"
#include "include/cppgc/persistent.h"
#include "include/libplatform/libplatform.h"
#include "include/v8-cppgc.h"
#include "include/v8.h"

namespace {

void WriteError(ServoV8ErrorBuffer* error, const std::string& message) {
  if (!error) return;
  error->length = message.size();
  if (!error->data || error->capacity == 0) return;
  const size_t copied =
      std::min(message.size(), static_cast<size_t>(error->capacity - 1));
  message.copy(error->data, copied);
  error->data[copied] = '\0';
}

void ClearError(ServoV8ErrorBuffer* error) {
  if (!error) return;
  error->length = 0;
  if (error->data && error->capacity) error->data[0] = '\0';
}

v8::Local<v8::String> V8String(v8::Isolate* isolate, const char* value) {
  return v8::String::NewFromUtf8(isolate, value, v8::NewStringType::kInternalized)
      .ToLocalChecked();
}

void ThrowTypeError(v8::Isolate* isolate, const char* message) {
  isolate->ThrowException(v8::Exception::TypeError(V8String(isolate, message)));
}

class GlobalV8State {
 public:
  bool Initialize(const ServoV8Options& options, ServoV8ErrorBuffer* error) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (initialized_) {
      if (options.enable_turbolev != options_.enable_turbolev ||
          options.enable_turbolev_future != options_.enable_turbolev_future ||
          options.expose_gc != options_.expose_gc) {
        WriteError(error,
                   "V8 process flags were already initialized with different "
                   "options");
        return false;
      }
      return true;
    }

    std::string flags = "--maglev --turbofan";
    if (options.enable_turbolev) flags += " --turbolev";
    if (options.enable_turbolev_future) flags += " --turbolev-future";
    if (options.expose_gc) flags += " --expose-gc";
    v8::V8::SetFlagsFromString(flags.c_str(), flags.size());

    if (!v8::V8::InitializeICUDefaultLocation(nullptr)) {
      WriteError(error, "V8 failed to initialize ICU");
      return false;
    }
    platform_ = v8::platform::NewDefaultPlatform();
    v8::V8::InitializePlatform(platform_.get());
    if (!v8::V8::Initialize()) {
      WriteError(error, "V8 global initialization failed");
      v8::V8::DisposePlatform();
      platform_.reset();
      return false;
    }

    options_ = options;
    initialized_ = true;
    return true;
  }

  v8::Platform* platform() const { return platform_.get(); }

 private:
  std::mutex mutex_;
  std::unique_ptr<v8::Platform> platform_;
  ServoV8Options options_{};
  bool initialized_ = false;
};

GlobalV8State& GlobalState() {
  // Deliberately process-lifetime: isolates may be created on multiple Servo
  // script threads, and V8 platform shutdown is only safe after all are gone.
  static GlobalV8State* state = new GlobalV8State();
  return *state;
}

std::string TryCatchMessage(v8::Isolate* isolate,
                            const v8::TryCatch& try_catch) {
  if (try_catch.Exception().IsEmpty()) return "V8 operation failed";
  v8::String::Utf8Value text(isolate, try_catch.Exception());
  if (!*text) return "V8 exception could not be converted to UTF-8";
  return std::string(*text, text.length());
}

void WriteV8Value(v8::Isolate* isolate,
                  v8::Local<v8::Value> value,
                  ServoV8ErrorBuffer* output) {
  ClearError(output);
  if (value.IsEmpty()) return;
  v8::String::Utf8Value text(isolate, value);
  if (*text) WriteError(output, std::string(*text, text.length()));
}

void ClearScriptRunOutcome(ServoV8ScriptRunOutcome* outcome) {
  outcome->status = SERVO_V8_SCRIPT_RUN_COMPLETED;
  ClearError(&outcome->exception.message);
  ClearError(&outcome->exception.resource_name);
  ClearError(&outcome->exception.stack);
  outcome->exception.line_number = 0;
  outcome->exception.column_number = 0;
}

void ClearScriptException(ServoV8ScriptException* exception) {
  ClearError(&exception->message);
  ClearError(&exception->resource_name);
  ClearError(&exception->stack);
  exception->line_number = 0;
  exception->column_number = 0;
}

void ClearScriptCompileOutcome(ServoV8ScriptCompileOutcome* outcome) {
  outcome->status = SERVO_V8_SCRIPT_COMPILED;
  outcome->script_id = 0;
  ClearScriptException(&outcome->exception);
}

void CaptureScriptException(v8::Isolate* isolate,
                            v8::Local<v8::Context> context,
                            const v8::TryCatch& try_catch,
                            ServoV8ScriptException* exception) {
  v8::Local<v8::Message> message = try_catch.Message();
  if (!message.IsEmpty()) {
    WriteV8Value(isolate, message->Get(), &exception->message);
    WriteV8Value(isolate, message->GetScriptResourceName(),
                 &exception->resource_name);
    const int line_number = message->GetLineNumber(context).FromMaybe(0);
    const int start_column = message->GetStartColumn(context).FromMaybe(-1);
    exception->line_number = line_number > 0
                                 ? static_cast<uint32_t>(line_number)
                                 : 0;
    exception->column_number = start_column >= 0
                                   ? static_cast<uint32_t>(start_column + 1)
                                   : 0;
  } else {
    WriteV8Value(isolate, try_catch.Exception(), &exception->message);
  }
  v8::Local<v8::Value> stack;
  if (try_catch.StackTrace(context).ToLocal(&stack)) {
    WriteV8Value(isolate, stack, &exception->stack);
  }
}

}  // namespace

struct ServoV8TraceVisitor {
  cppgc::Visitor* visitor;
};

struct ServoV8DomCell final : public v8::Object::Wrappable {
 public:
  ServoV8DomCell(ServoV8Runtime* runtime,
                 void* native,
                 uint32_t interface_id,
                 const ServoV8EngineBindingSmokeVTable& vtable)
      : runtime_(runtime),
        native_(native),
        interface_id_(interface_id),
        vtable_(vtable) {}

  ~ServoV8DomCell() override;

  void SetWrapper(v8::Isolate* isolate, v8::Local<v8::Object> wrapper) {
    wrapper_.Reset(isolate, wrapper);
  }

  void Trace(cppgc::Visitor* visitor) const override;

  const v8::Object::WrapperTypeInfo* GetWrapperTypeInfo() const override {
    return &kTypeInfo;
  }

  const char* GetHumanReadableName() const override {
    return "ServoV8DomCell";
  }

  void* native() const { return native_; }
  ServoV8Runtime* runtime() const { return runtime_; }
  uint32_t interface_id() const { return interface_id_; }
  const ServoV8EngineBindingSmokeVTable& vtable() const { return vtable_; }

 private:
  static constexpr v8::Object::WrapperTypeInfo kTypeInfo{1};
  ServoV8Runtime* runtime_;
  void* native_;
  const uint32_t interface_id_;
  ServoV8EngineBindingSmokeVTable vtable_;
  v8::TracedReference<v8::Object> wrapper_;
};

// A wrapper for a Servo DOM object that already exists.
//
// ServoV8DomCell above is created the other way round -- JavaScript calls a
// constructor, so the JS object exists before the native does and identity is
// free. Handing back an object Servo already owns needs the reverse lookup, so
// these cells are registered in their realm's wrapper cache.
//
// The cell holds the only cross-heap edge: strong into SpiderMonkey, via a
// Trusted<T> inside the native host. Nothing in the SpiderMonkey heap points
// back, so no cross-heap cycle can form.
struct ServoV8HostCell final : public v8::Object::Wrappable {
 public:
  ServoV8HostCell(ServoV8Runtime* runtime,
                  void* native,
                  ServoV8DropCallback drop,
                  const void* key)
      : runtime_(runtime), native_(native), drop_(drop), key_(key) {}

  ~ServoV8HostCell() override {
    wrapper_.Reset();
    ReleaseHost();
  }

  // Releases the host now rather than whenever the next collection happens.
  //
  // Realm teardown must not leave hosts alive: each holds a Trusted<T> that
  // roots its DOM object, and through it the tree, so waiting for a GC would
  // pin a destroyed pipeline's DOM for as long as the isolate stays idle.
  // Servo relies on this release being synchronous.
  void ReleaseHost();

  void SetWrapper(v8::Isolate* isolate, v8::Local<v8::Object> wrapper) {
    wrapper_.Reset(isolate, wrapper);
  }

  v8::Local<v8::Object> wrapper(v8::Isolate* isolate) const {
    return wrapper_.Get(isolate);
  }

  void Trace(cppgc::Visitor* visitor) const override {
    v8::Object::Wrappable::Trace(visitor);
    visitor->Trace(wrapper_);
  }

  const v8::Object::WrapperTypeInfo* GetWrapperTypeInfo() const override {
    return &kTypeInfo;
  }

  const char* GetHumanReadableName() const override { return "ServoV8HostCell"; }

  void* native() const { return native_; }
  const void* key() const { return key_; }

 private:
  static constexpr v8::Object::WrapperTypeInfo kTypeInfo{2};
  ServoV8Runtime* runtime_;
  void* native_;
  ServoV8DropCallback drop_;
  // The DOM object's address. Safe as an identity only because this cell keeps
  // that object alive for exactly as long as the cache entry can be hit.
  const void* key_;
  v8::TracedReference<v8::Object> wrapper_;
};

struct ServoV8DocumentHostState {
  ServoV8Runtime* runtime = nullptr;
  void* native = nullptr;
  void* active_host_context = nullptr;
  ServoV8DocumentHostVTable vtable{};
};

struct ServoV8TimerHostState {
  ServoV8Runtime* runtime = nullptr;
  void* native = nullptr;
  ServoV8TimerHostVTable vtable{};
};

struct ServoV8ConsoleHostState {
  ServoV8Runtime* runtime = nullptr;
  void* native = nullptr;
  ServoV8ConsoleHostVTable vtable{};
};

struct ServoV8TimerCallback {
  v8::Global<v8::Function> function;
  std::vector<v8::Global<v8::Value>> arguments;
  int32_t handle = 0;
  bool is_interval = false;
};

struct ServoV8RealmState {
  ServoV8Runtime* runtime = nullptr;
  // Needed so a microtask failure can name the realm that produced it; Servo
  // maps that back to a pipeline and fires the event on the right global.
  ServoV8RealmId id = 0;
  v8::Global<v8::Context> context;
  v8::Global<v8::Object> document;
  std::unordered_map<ServoV8ScriptId, v8::Global<v8::Script>> scripts;
  std::unordered_map<ServoV8TimerCallbackId, ServoV8TimerCallback>
      timer_callbacks;
  std::unordered_map<int32_t, ServoV8TimerCallbackId> timer_handles;
  ServoV8TimerCallbackId next_timer_callback_id = 1;
  // Weak, so a wrapper script can no longer reach is collected rather than
  // pinned for the life of the realm along with the element behind it.
  std::unordered_map<const void*, cppgc::WeakPersistent<ServoV8HostCell>>
      wrappers;
  v8::Global<v8::ObjectTemplate> element_template;
  v8::Global<v8::Object> element_prototype;
  ServoV8DocumentHostState document_host;
  ServoV8TimerHostState timer_host;
  ServoV8ConsoleHostState console_host;
  bool tearing_down = false;
};

// One uncaught error from a microtask job, buffered until Rust asks for it.
//
// V8 reports these to the isolate message handler rather than to a TryCatch at
// the checkpoint boundary, so they arrive by callback while the drain is still
// running. The strings are owned here because the caller's outcome buffers
// belong to a single call, and one drain can produce many errors.
struct ServoV8PendingJobError {
  ServoV8RealmId realm_id = 0;
  std::string message;
  std::string resource_name;
  std::string stack;
  uint32_t line_number = 0;
  uint32_t column_number = 0;
};

// A rejection with no handler, held with its promise so that a handler added
// later can revoke it before anything is reported.
struct ServoV8PendingRejection {
  v8::Global<v8::Promise> promise;
  ServoV8PendingJobError error;
};

struct ServoV8Runtime {
  std::unique_ptr<v8::ArrayBuffer::Allocator> allocator;
  v8::Isolate* isolate = nullptr;
  std::vector<ServoV8PendingJobError> pending_job_errors;
  std::vector<ServoV8PendingRejection> pending_rejections;
  // Only buffer while a checkpoint is on the stack. Everywhere else the
  // embedder already observes failures through a TryCatch it owns.
  bool draining_microtasks = false;
  v8::Global<v8::Context> context;
  std::unordered_map<ServoV8RealmId, std::unique_ptr<ServoV8RealmState>> realms;
  ServoV8RealmId next_realm_id = 1;
  ServoV8ScriptId next_script_id = 1;
  std::thread::id owner_thread;
  uint32_t rust_callback_depth = 0;
  ServoV8EngineBindingSmokeVTable engine_binding_smoke_vtable{};
  bool engine_binding_smoke_installed = false;
  ServoV8ElementHostVTable element_host_vtable{};
  bool element_host_installed = false;
  bool expose_gc = false;
};

namespace {

constexpr v8::CppHeapPointerTag kServoDomTag =
    v8::CppHeapPointerTag::kFirstObjectWrappableTag;
// A separate tag, so unwrapping one cell type can never mis-cast the other.
constexpr v8::CppHeapPointerTag kServoHostTag =
    static_cast<v8::CppHeapPointerTag>(
        static_cast<uint16_t>(v8::CppHeapPointerTag::kFirstObjectWrappableTag) +
        1);
constexpr int kServoRealmStateEmbedderSlot = 1;
constexpr v8::EmbedderDataTypeTag kServoRealmStateEmbedderTag = 1;

class RustCallbackScope {
 public:
  explicit RustCallbackScope(ServoV8Runtime* runtime) : runtime_(runtime) {
    ++runtime_->rust_callback_depth;
  }

  ~RustCallbackScope() { --runtime_->rust_callback_depth; }

 private:
  ServoV8Runtime* runtime_;
};

class ActiveHostContextScope {
 public:
  ActiveHostContextScope(ServoV8DocumentHostState* state, void* host_context)
      : state_(state) {
    state_->active_host_context = host_context;
  }

  ~ActiveHostContextScope() { state_->active_host_context = nullptr; }

 private:
  ServoV8DocumentHostState* state_;
};

// Installs one ephemeral host context on every live realm.
//
// A microtask checkpoint drains the isolate's single queue, so a job may
// belong to any realm and call that realm's Document host. The host context is
// in fact a per-script-thread value — one embedding JSContext for the thread —
// rather than a per-realm one, so every realm gets it for the drain.
//
// Realms are created and destroyed only by Servo, never by running JavaScript,
// and CheckRuntime rejects bridge re-entry from a Rust host callback, so the
// realm map cannot change between construction and destruction.
class AllRealmsHostContextScope {
 public:
  AllRealmsHostContextScope(ServoV8Runtime* runtime, void* host_context)
      : runtime_(runtime) {
    for (auto& entry : runtime_->realms) {
      entry.second->document_host.active_host_context = host_context;
    }
  }

  ~AllRealmsHostContextScope() {
    for (auto& entry : runtime_->realms) {
      entry.second->document_host.active_host_context = nullptr;
    }
  }

 private:
  ServoV8Runtime* runtime_;
};

constexpr uint32_t kServoRuntimeIsolateSlot = 0;

// Formats a captured stack trace the way `TryCatch::StackTrace` would, so a
// job error and a script error read the same on the Rust side.
std::string FormatStackTrace(v8::Isolate* isolate,
                             v8::Local<v8::StackTrace> trace) {
  if (trace.IsEmpty()) return std::string();
  std::string formatted;
  for (int index = 0; index < trace->GetFrameCount(); ++index) {
    v8::Local<v8::StackFrame> frame = trace->GetFrame(isolate, index);
    v8::String::Utf8Value function(isolate, frame->GetFunctionName());
    v8::String::Utf8Value script(isolate, frame->GetScriptName());
    formatted += "\n    at ";
    formatted += (*function && function.length()) ? *function : "<anonymous>";
    formatted += " (";
    formatted += (*script && script.length()) ? *script : "<unknown>";
    formatted += ":" + std::to_string(frame->GetLineNumber());
    formatted += ":" + std::to_string(frame->GetColumn()) + ")";
  }
  return formatted;
}

// Receives uncaught errors that V8 reports rather than propagating.
//
// A microtask that throws is caught inside V8's own microtask builtin, which
// reports the message and lets execution continue, so no TryCatch the embedder
// installs at the checkpoint boundary can observe it. This is the only channel
// that can. It must not call into Rust: it runs with the drain still on the
// stack and the host context installed on every realm.
ServoV8RealmId RealmIdForContext(v8::Isolate* isolate,
                                v8::Local<v8::Context> context) {
  if (context.IsEmpty()) return 0;
  auto* realm = static_cast<ServoV8RealmState*>(
      context->GetAlignedPointerFromEmbedderData(
          isolate, kServoRealmStateEmbedderSlot, kServoRealmStateEmbedderTag));
  return realm ? realm->id : 0;
}

void FillFromMessage(v8::Isolate* isolate,
                     v8::Local<v8::Message> message,
                     ServoV8PendingJobError* pending) {
  if (message.IsEmpty()) return;
  if (pending->message.empty()) {
    v8::String::Utf8Value text(isolate, message->Get());
    if (*text) pending->message.assign(*text, text.length());
  }
  v8::String::Utf8Value resource(isolate, message->GetScriptResourceName());
  if (*resource) pending->resource_name.assign(*resource, resource.length());
  pending->stack = FormatStackTrace(isolate, message->GetStackTrace());
  v8::Local<v8::Context> context = isolate->GetCurrentContext();
  if (context.IsEmpty()) return;
  const int line = message->GetLineNumber(context).FromMaybe(0);
  const int column = message->GetStartColumn(context).FromMaybe(-1);
  if (line > 0) pending->line_number = static_cast<uint32_t>(line);
  if (column >= 0) pending->column_number = static_cast<uint32_t>(column + 1);
}

void OnUncaughtMessage(v8::Local<v8::Message> message,
                       v8::Local<v8::Value> data) {
  v8::Isolate* isolate = v8::Isolate::GetCurrent();
  if (!isolate) return;
  auto* runtime =
      static_cast<ServoV8Runtime*>(isolate->GetData(kServoRuntimeIsolateSlot));
  if (!runtime || !runtime->draining_microtasks) return;

  v8::HandleScope handle_scope(isolate);
  ServoV8PendingJobError pending;
  pending.realm_id =
      RealmIdForContext(isolate, isolate->GetEnteredOrMicrotaskContext());
  // Registered with the default data, so V8 passes the thrown value itself.
  if (!data.IsEmpty()) {
    v8::String::Utf8Value text(isolate, data);
    if (*text) pending.message.assign(*text, text.length());
  }
  FillFromMessage(isolate, message, &pending);
  runtime->pending_job_errors.push_back(std::move(pending));
}

// Tracks rejections that currently have no handler.
//
// A promise reaction that throws does not reach the message listener: the
// throw rejects the derived promise instead, which is this channel. That makes
// this the path an ordinary `Promise.then(...)` failure takes, so it is the
// one that matters most for page script.
//
// Recording rather than reporting immediately is what HTML wants: a handler
// attached later in the same drain revokes the entry, so only rejections still
// unhandled when the caller pulls are surfaced.
void OnPromiseReject(v8::PromiseRejectMessage message) {
  v8::Isolate* isolate = v8::Isolate::GetCurrent();
  if (!isolate) return;
  auto* runtime =
      static_cast<ServoV8Runtime*>(isolate->GetData(kServoRuntimeIsolateSlot));
  if (!runtime) return;

  v8::HandleScope handle_scope(isolate);
  v8::Local<v8::Promise> promise = message.GetPromise();
  if (promise.IsEmpty()) return;

  auto matches = [&](const ServoV8PendingRejection& entry) {
    return entry.promise.Get(isolate) == promise;
  };

  switch (message.GetEvent()) {
    case v8::kPromiseRejectWithNoHandler: {
      ServoV8PendingJobError pending;
      // A rejection belongs to the realm that created the promise, which is
      // not necessarily the one currently entered.
      v8::Local<v8::Context> creation_context;
      pending.realm_id =
          promise->GetCreationContext(isolate).ToLocal(&creation_context)
              ? RealmIdForContext(isolate, creation_context)
              : RealmIdForContext(isolate,
                                  isolate->GetEnteredOrMicrotaskContext());
      v8::Local<v8::Value> value = message.GetValue();
      if (!value.IsEmpty()) {
        v8::String::Utf8Value text(isolate, value);
        if (*text) pending.message.assign(*text, text.length());
        FillFromMessage(isolate, v8::Exception::CreateMessage(isolate, value),
                        &pending);
      }
      ServoV8PendingRejection entry;
      entry.promise.Reset(isolate, promise);
      entry.error = std::move(pending);
      runtime->pending_rejections.push_back(std::move(entry));
      break;
    }
    case v8::kPromiseHandlerAddedAfterReject: {
      auto& rejections = runtime->pending_rejections;
      for (auto entry = rejections.begin(); entry != rejections.end();
           ++entry) {
        if (matches(*entry)) {
          rejections.erase(entry);
          break;
        }
      }
      break;
    }
    default:
      // The remaining events are deprecated and no longer emitted.
      break;
  }
}

class MicrotaskDrainScope {
 public:
  explicit MicrotaskDrainScope(ServoV8Runtime* runtime) : runtime_(runtime) {
    runtime_->draining_microtasks = true;
  }

  ~MicrotaskDrainScope() { runtime_->draining_microtasks = false; }

 private:
  ServoV8Runtime* runtime_;
};

bool CheckRuntime(ServoV8Runtime* runtime, ServoV8ErrorBuffer* error) {
  if (!runtime || !runtime->isolate) {
    WriteError(error, "invalid Servo V8 runtime");
    return false;
  }
  if (runtime->owner_thread != std::this_thread::get_id()) {
    WriteError(error, "Servo V8 runtime used from a non-owner thread");
    return false;
  }
  if (runtime->rust_callback_depth != 0) {
    WriteError(error, "Servo V8 runtime re-entered from a Rust host callback");
    return false;
  }
  return true;
}

ServoV8RealmState* FindRealm(ServoV8Runtime* runtime,
                             ServoV8RealmId realm_id,
                             ServoV8ErrorBuffer* error) {
  const auto realm = runtime->realms.find(realm_id);
  if (realm == runtime->realms.end()) {
    WriteError(error, "unknown or destroyed Servo V8 realm " +
                          std::to_string(realm_id));
    return {};
  }
  return realm->second.get();
}

// cppgc clears WeakPersistent handles during major-GC weakness processing and,
// because this runtime requires atomic sweeping, finishes every HostCell
// destructor before V8 invokes this epilogue. Erasing the now-empty off-heap
// handles here is therefore same-thread and collection-safe. It also bounds
// the weak persistent region by the live wrapper set instead of every Element
// this realm has ever exposed.
void PruneDeadWrapperCacheEntries(v8::Isolate* isolate,
                                  v8::GCType,
                                  v8::GCCallbackFlags,
                                  void* data) {
  auto* runtime = static_cast<ServoV8Runtime*>(data);
  if (!runtime || runtime->isolate != isolate ||
      runtime->owner_thread != std::this_thread::get_id() ||
      runtime->rust_callback_depth != 0) {
    return;
  }
  for (auto& realm_entry : runtime->realms) {
    auto& wrappers = realm_entry.second->wrappers;
    for (auto entry = wrappers.begin(); entry != wrappers.end();) {
      if (entry->second.Get()) {
        ++entry;
      } else {
        entry = wrappers.erase(entry);
      }
    }
  }
}

v8::Local<v8::Context> FindRealmContext(ServoV8Runtime* runtime,
                                        ServoV8RealmId realm_id,
                                        ServoV8ErrorBuffer* error) {
  ServoV8RealmState* realm = FindRealm(runtime, realm_id, error);
  return realm ? realm->context.Get(runtime->isolate)
               : v8::Local<v8::Context>();
}

ServoV8DomCell* UnwrapDomCell(v8::Isolate* isolate,
                              v8::Local<v8::Object> wrapper) {
  return v8::Object::Unwrap<kServoDomTag, ServoV8DomCell>(isolate, wrapper);
}

ServoV8DomCell* UnwrapDomCell(
    const v8::FunctionCallbackInfo<v8::Value>& info) {
  return UnwrapDomCell(info.GetIsolate(), info.This());
}

ServoV8DocumentHostState* UnwrapDocumentHostState(
    const v8::FunctionCallbackInfo<v8::Value>& info) {
  v8::Local<v8::Object> receiver = info.This();
  auto* realm = static_cast<ServoV8RealmState*>(
      receiver->GetAlignedPointerFromEmbedderDataInCreationContext(
          info.GetIsolate(), kServoRealmStateEmbedderSlot,
          kServoRealmStateEmbedderTag));
  if (!realm || realm->tearing_down || !realm->runtime ||
      realm->runtime->isolate != info.GetIsolate() ||
      realm->document.IsEmpty() ||
      !receiver->StrictEquals(realm->document.Get(info.GetIsolate()))) {
    return nullptr;
  }
  return &realm->document_host;
}

// Drops a host that was never installed into a cell, using the same re-entry
// barrier as an installed host's ReleaseHost path. Cache hits create one
// speculative Rust host that must be discarded, and wrapper allocation can
// fail after that host has crossed the ABI; neither path may let Drop enter V8
// beneath the live accessor callback.
void DropUnownedElementHost(ServoV8Runtime* runtime,
                            void* native,
                            ServoV8DropCallback drop) {
  if (!native || !drop) return;
  RustCallbackScope callback_scope(runtime);
  drop(native);
}

v8::Local<v8::Object> WrapperForInterfaceValue(
    ServoV8RealmState* realm,
    v8::Isolate* isolate,
    v8::Local<v8::Context> context,
    const ServoV8InterfaceValue& value);

#include "servo_v8_generated.inc"
#include "servo_v8_document_host_generated.inc"

// Finds or creates the wrapper for one DOM object, preserving identity.
//
// A hit drops the surplus host the caller speculatively allocated, so
// ownership never straddles the two outcomes.
v8::Local<v8::Object> WrapperForInterfaceValue(
    ServoV8RealmState* realm,
    v8::Isolate* isolate,
    v8::Local<v8::Context> context,
    const ServoV8InterfaceValue& value) {
  ServoV8Runtime* runtime = realm->runtime;
  const ServoV8DropCallback drop = runtime->element_host_vtable.drop;

  const auto entry = realm->wrappers.find(value.key);
  if (entry != realm->wrappers.end()) {
    if (ServoV8HostCell* cell = entry->second.Get()) {
      DropUnownedElementHost(runtime, value.native, drop);
      return cell->wrapper(isolate);
    }
    // cppgc clears the weak entry once the cell dies. The stale slot is only
    // removed here, which is also what makes a later address reuse safe.
    realm->wrappers.erase(entry);
  }

  v8::Local<v8::Object> wrapper;
  if (!realm->element_template.Get(isolate)
           ->NewInstance(context)
           .ToLocal(&wrapper)) {
    DropUnownedElementHost(runtime, value.native, drop);
    return v8::Local<v8::Object>();
  }
  if (realm->element_prototype.IsEmpty() ||
      !wrapper
           ->SetPrototype(context, realm->element_prototype.Get(isolate))
           .FromMaybe(false)) {
    DropUnownedElementHost(runtime, value.native, drop);
    return v8::Local<v8::Object>();
  }

  v8::CppHeap* cpp_heap = isolate->GetCppHeap();
  auto* cell = cppgc::MakeGarbageCollected<ServoV8HostCell>(
      cpp_heap->GetAllocationHandle(), runtime, value.native, drop, value.key);
  // Keep the cell reachable across the Wrap call, which can allocate.
  cppgc::Persistent<ServoV8HostCell> pending(cell);
  v8::Object::Wrap<kServoHostTag>(isolate, wrapper, cell);
  cell->SetWrapper(isolate, wrapper);
  realm->wrappers[value.key] = cell;
  pending.Clear();
  return wrapper;
}

void* UnwrapElementHostNative(const v8::FunctionCallbackInfo<v8::Value>& info) {
  v8::Local<v8::Value> receiver = info.This();
  if (!receiver->IsObject()) return nullptr;
  auto* cell = v8::Object::Unwrap<kServoHostTag, ServoV8HostCell>(
      info.GetIsolate(), receiver.As<v8::Object>());
  return cell ? cell->native() : nullptr;
}

bool ElementHostCallbackState(
    const v8::FunctionCallbackInfo<v8::Value>& info,
    ServoV8RealmState** realm_output,
    void** native_output) {
  v8::Isolate* isolate = info.GetIsolate();
  auto* realm = static_cast<ServoV8RealmState*>(
      info.This()->GetAlignedPointerFromEmbedderDataInCreationContext(
          isolate, kServoRealmStateEmbedderSlot, kServoRealmStateEmbedderTag));
  void* native = UnwrapElementHostNative(info);
  if (!realm || realm->tearing_down || !realm->runtime ||
      realm->runtime->isolate != isolate || realm->context.IsEmpty() ||
      realm->context.Get(isolate) != isolate->GetCurrentContext() || !native) {
    ThrowTypeError(isolate, "invalid Element host state");
    return false;
  }
  if (realm->runtime->rust_callback_depth != 0) {
    ThrowTypeError(isolate, "re-entrant Element host callback");
    return false;
  }
  *realm_output = realm;
  *native_output = native;
  return true;
}

using ElementStringGetter =
    uint8_t (*)(void* native, ServoV8OwnedUtf8* output);
using ElementStringGetterSlot =
    ElementStringGetter ServoV8ElementHostVTable::*;

void ElementHostGetString(const v8::FunctionCallbackInfo<v8::Value>& info,
                          ElementStringGetterSlot getter_slot,
                          const char* member_name) {
  v8::Isolate* isolate = info.GetIsolate();
  ServoV8RealmState* realm = nullptr;
  void* native = nullptr;
  if (!ElementHostCallbackState(info, &realm, &native)) return;
  const ElementStringGetter getter =
      realm->runtime->element_host_vtable.*getter_slot;
  if (!getter) {
    ThrowTypeError(isolate, "Element string getter is not installed");
    return;
  }

  ServoV8OwnedUtf8 value{};
  {
    RustCallbackScope callback_scope(realm->runtime);
    if (!getter(native, &value)) {
      ThrowTypeError(isolate, member_name);
      return;
    }
  }
  DocumentHostOwnedUtf8Scope value_scope(realm->runtime, &value);
  if ((!value.data && value.length != 0) ||
      value.length > static_cast<size_t>(std::numeric_limits<int>::max())) {
    ThrowTypeError(isolate, "invalid Element string UTF-8 result");
    return;
  }
  v8::Local<v8::String> result;
  if (!v8::String::NewFromUtf8(isolate,
                               reinterpret_cast<const char*>(value.data),
                               v8::NewStringType::kNormal,
                               static_cast<int>(value.length))
           .ToLocal(&result)) {
    return;
  }
  info.GetReturnValue().Set(result);
}

void ElementHostGetLocalName(
    const v8::FunctionCallbackInfo<v8::Value>& info) {
  ElementHostGetString(info, &ServoV8ElementHostVTable::get_local_name,
                       "Element.localName host callback failed");
}

void ElementHostGetTagName(const v8::FunctionCallbackInfo<v8::Value>& info) {
  ElementHostGetString(info, &ServoV8ElementHostVTable::get_tag_name,
                       "Element.tagName host callback failed");
}

void ElementHostGetId(const v8::FunctionCallbackInfo<v8::Value>& info) {
  ElementHostGetString(info, &ServoV8ElementHostVTable::get_id,
                       "Element.id host callback failed");
}

void ElementHostGetClassName(
    const v8::FunctionCallbackInfo<v8::Value>& info) {
  ElementHostGetString(info, &ServoV8ElementHostVTable::get_class_name,
                       "Element.className host callback failed");
}

using ElementStringSetter = uint8_t (*)(void* native,
                                        void* host_context,
                                        const uint8_t* value,
                                        size_t value_length);
using ElementStringSetterSlot =
    ElementStringSetter ServoV8ElementHostVTable::*;

void ElementHostSetString(const v8::FunctionCallbackInfo<v8::Value>& info,
                          ElementStringSetterSlot setter_slot,
                          const char* member_name) {
  v8::Isolate* isolate = info.GetIsolate();
  ServoV8RealmState* realm = nullptr;
  void* native = nullptr;
  // Brand-check before conversion, as WebIDL requires.
  if (!ElementHostCallbackState(info, &realm, &native)) return;
  const ElementStringSetter setter =
      realm->runtime->element_host_vtable.*setter_slot;
  if (!setter || !realm->document_host.active_host_context) {
    ThrowTypeError(isolate, "Element mutation requires a live host context");
    return;
  }
  v8::Local<v8::String> string;
  if (!info[0]->ToString(isolate->GetCurrentContext()).ToLocal(&string)) return;
  v8::String::Utf8Value value(isolate, string);
  if (!*value && value.length() != 0) return;
  {
    RustCallbackScope callback_scope(realm->runtime);
    if (!setter(native, realm->document_host.active_host_context,
                reinterpret_cast<const uint8_t*>(*value), value.length())) {
      ThrowTypeError(isolate, member_name);
      return;
    }
  }
}

void ElementHostSetId(const v8::FunctionCallbackInfo<v8::Value>& info) {
  ElementHostSetString(info, &ServoV8ElementHostVTable::set_id,
                       "Element.id host callback failed");
}

void ElementHostSetClassName(
    const v8::FunctionCallbackInfo<v8::Value>& info) {
  ElementHostSetString(info, &ServoV8ElementHostVTable::set_class_name,
                       "Element.className host callback failed");
}

void ElementHostHasAttributes(
    const v8::FunctionCallbackInfo<v8::Value>& info) {
  v8::Isolate* isolate = info.GetIsolate();
  ServoV8RealmState* realm = nullptr;
  void* native = nullptr;
  if (!ElementHostCallbackState(info, &realm, &native)) return;
  uint8_t result = 0;
  {
    RustCallbackScope callback_scope(realm->runtime);
    if (!realm->runtime->element_host_vtable.has_attributes(native, &result) ||
        result > 1) {
      ThrowTypeError(isolate, "Element.hasAttributes host callback failed");
      return;
    }
  }
  info.GetReturnValue().Set(result != 0);
}

bool ElementHostOperationName(
    const v8::FunctionCallbackInfo<v8::Value>& info,
    const char* member_name,
    ServoV8RealmState** realm_output,
    void** native_output,
    v8::Local<v8::String>* name_output) {
  v8::Isolate* isolate = info.GetIsolate();
  // Brand-check before required-argument checks and user-code conversion.
  if (!ElementHostCallbackState(info, realm_output, native_output)) return false;
  if (!(*realm_output)->document_host.active_host_context) {
    ThrowTypeError(isolate, "Element operation requires a live host context");
    return false;
  }
  if (info.Length() < 1) {
    ThrowTypeError(isolate, member_name);
    return false;
  }
  return info[0]->ToString(isolate->GetCurrentContext()).ToLocal(name_output);
}

void ElementHostGetAttribute(
    const v8::FunctionCallbackInfo<v8::Value>& info) {
  v8::Isolate* isolate = info.GetIsolate();
  ServoV8RealmState* realm = nullptr;
  void* native = nullptr;
  v8::Local<v8::String> name_string;
  if (!ElementHostOperationName(info, "Element.getAttribute requires 1 argument",
                                &realm, &native, &name_string)) {
    return;
  }
  v8::String::Utf8Value name(isolate, name_string);
  if (!*name && name.length() != 0) return;
  ServoV8OptionalOwnedUtf8 result{};
  {
    RustCallbackScope callback_scope(realm->runtime);
    if (!realm->runtime->element_host_vtable.get_attribute(
            native, realm->document_host.active_host_context,
            reinterpret_cast<const uint8_t*>(*name), name.length(), &result)) {
      ThrowTypeError(isolate, "Element.getAttribute host callback failed");
      return;
    }
  }
  DocumentHostOwnedUtf8Scope result_scope(realm->runtime, &result.value);
  const bool malformed_null =
      result.is_null != 0 &&
      (result.value.data || result.value.length != 0 || result.value.owner ||
       result.value.drop_owner);
  if (result.is_null > 1 || malformed_null ||
      (!result.value.data && result.value.length != 0) ||
      result.value.length >
          static_cast<size_t>(std::numeric_limits<int>::max())) {
    ThrowTypeError(isolate, "invalid Element.getAttribute result");
    return;
  }
  if (result.is_null) {
    info.GetReturnValue().Set(v8::Null(isolate));
    return;
  }
  v8::Local<v8::String> value;
  if (!v8::String::NewFromUtf8(
           isolate, reinterpret_cast<const char*>(result.value.data),
           v8::NewStringType::kNormal, static_cast<int>(result.value.length))
           .ToLocal(&value)) {
    return;
  }
  info.GetReturnValue().Set(value);
}

void ElementHostHasAttribute(
    const v8::FunctionCallbackInfo<v8::Value>& info) {
  v8::Isolate* isolate = info.GetIsolate();
  ServoV8RealmState* realm = nullptr;
  void* native = nullptr;
  v8::Local<v8::String> name_string;
  if (!ElementHostOperationName(info, "Element.hasAttribute requires 1 argument",
                                &realm, &native, &name_string)) {
    return;
  }
  v8::String::Utf8Value name(isolate, name_string);
  if (!*name && name.length() != 0) return;
  uint8_t result = 0;
  {
    RustCallbackScope callback_scope(realm->runtime);
    if (!realm->runtime->element_host_vtable.has_attribute(
            native, realm->document_host.active_host_context,
            reinterpret_cast<const uint8_t*>(*name), name.length(), &result) ||
        result > 1) {
      ThrowTypeError(isolate, "Element.hasAttribute host callback failed");
      return;
    }
  }
  info.GetReturnValue().Set(result != 0);
}

bool InstallElementPrototype(ServoV8RealmState* realm,
                             v8::Local<v8::Context> context,
                             v8::Local<v8::Object> prototype) {
  v8::Isolate* isolate = realm->runtime->isolate;
  struct ElementAccessor {
    const char* name;
    v8::FunctionCallback getter;
    v8::FunctionCallback setter;
  };
  const ElementAccessor accessors[] = {
      {"localName", &ElementHostGetLocalName, nullptr},
      {"tagName", &ElementHostGetTagName, nullptr},
      {"id", &ElementHostGetId, &ElementHostSetId},
      {"className", &ElementHostGetClassName, &ElementHostSetClassName},
  };
  for (const auto& accessor : accessors) {
    v8::Local<v8::Function> getter;
    if (!v8::Function::New(context, accessor.getter, {}, 0,
                           v8::ConstructorBehavior::kThrow,
                           v8::SideEffectType::kHasNoSideEffect)
             .ToLocal(&getter)) {
      return false;
    }
    getter->SetName(V8String(isolate, (std::string("get ") + accessor.name).c_str()));
    v8::Local<v8::Function> setter;
    if (accessor.setter) {
      if (!v8::Function::New(context, accessor.setter, {}, 1,
                             v8::ConstructorBehavior::kThrow,
                             v8::SideEffectType::kHasSideEffect)
               .ToLocal(&setter)) {
        return false;
      }
      setter->SetName(
          V8String(isolate, (std::string("set ") + accessor.name).c_str()));
    }
    prototype->SetAccessorProperty(V8String(isolate, accessor.name), getter,
                                   setter, v8::None);
  }

  struct ElementOperation {
    const char* name;
    v8::FunctionCallback callback;
    int length;
    v8::SideEffectType side_effect_type;
  };
  const ElementOperation operations[] = {
      {"hasAttributes", &ElementHostHasAttributes, 0,
       v8::SideEffectType::kHasNoSideEffect},
      {"getAttribute", &ElementHostGetAttribute, 1,
       v8::SideEffectType::kHasNoSideEffect},
      {"hasAttribute", &ElementHostHasAttribute, 1,
       v8::SideEffectType::kHasSideEffect},
  };
  for (const auto& operation : operations) {
    v8::Local<v8::String> name = V8String(isolate, operation.name);
    v8::Local<v8::Function> function;
    if (!v8::Function::New(context, operation.callback, {}, operation.length,
                           v8::ConstructorBehavior::kThrow,
                           operation.side_effect_type)
             .ToLocal(&function)) {
      return false;
    }
    function->SetName(name);
    if (!prototype->DefineOwnProperty(context, name, function, v8::None)
             .FromMaybe(false)) {
      return false;
    }
  }
  return true;
}

ServoV8RealmState* CallbackRealm(
    const v8::FunctionCallbackInfo<v8::Value>& info) {
  v8::Isolate* isolate = info.GetIsolate();
  auto* runtime =
      static_cast<ServoV8Runtime*>(isolate->GetData(kServoRuntimeIsolateSlot));
  if (!runtime || runtime->isolate != isolate || !info.Data()->IsBigInt()) {
    return nullptr;
  }
  bool lossless = false;
  const ServoV8RealmId realm_id =
      info.Data().As<v8::BigInt>()->Uint64Value(&lossless);
  if (!lossless) return nullptr;
  const auto entry = runtime->realms.find(realm_id);
  if (entry == runtime->realms.end()) return nullptr;
  ServoV8RealmState* realm = entry->second.get();
  if (realm->tearing_down || realm->runtime != runtime ||
      realm->context.IsEmpty() ||
      realm->context.Get(isolate) != isolate->GetCurrentContext()) {
    return nullptr;
  }
  return realm;
}

bool TimerDelay(v8::Local<v8::Context> context,
                const v8::FunctionCallbackInfo<v8::Value>& info,
                int32_t* timeout_ms) {
  *timeout_ms = 0;
  return info.Length() < 2 || info[1]->IsUndefined() ||
         info[1]->Int32Value(context).To(timeout_ms);
}

bool TimerHandle(v8::Local<v8::Context> context,
                 const v8::FunctionCallbackInfo<v8::Value>& info,
                 int32_t* handle) {
  *handle = 0;
  return info.Length() < 1 || info[0]->IsUndefined() ||
         info[0]->Int32Value(context).To(handle);
}

void ScheduleTimer(const v8::FunctionCallbackInfo<v8::Value>& info,
                   bool is_interval) {
  v8::Isolate* isolate = info.GetIsolate();
  ServoV8RealmState* realm = CallbackRealm(info);
  if (!realm || !realm->timer_host.native ||
      realm->runtime->rust_callback_depth != 0) {
    ThrowTypeError(isolate, "invalid timer host state");
    return;
  }
  if (info.Length() < 1) {
    ThrowTypeError(isolate, "setTimeout/setInterval requires a handler");
    return;
  }

  v8::Local<v8::Context> context = isolate->GetCurrentContext();
  int32_t timeout_ms = 0;
  if (!TimerDelay(context, info, &timeout_ms)) return;
  int32_t handle = 0;
  ServoV8TimerHostState& host = realm->timer_host;

  if (info[0]->IsFunction()) {
    if (!host.vtable.schedule_function) {
      ThrowTypeError(isolate, "timer function host callback is unavailable");
      return;
    }
    if (realm->next_timer_callback_id == 0) {
      ThrowTypeError(isolate, "timer callback ID space is exhausted");
      return;
    }
    const ServoV8TimerCallbackId callback_id =
        realm->next_timer_callback_id++;

    ServoV8TimerCallback callback;
    callback.function.Reset(isolate, info[0].As<v8::Function>());
    callback.arguments.reserve(
        info.Length() > 2 ? static_cast<size_t>(info.Length() - 2) : 0);
    for (int index = 2; index < info.Length(); ++index) {
      callback.arguments.emplace_back(isolate, info[index]);
    }
    callback.is_interval = is_interval;
    auto [entry, inserted] =
        realm->timer_callbacks.try_emplace(callback_id, std::move(callback));
    if (!inserted) {
      ThrowTypeError(isolate, "timer callback ID collision");
      return;
    }

    uint8_t scheduled = 0;
    {
      RustCallbackScope callback_scope(realm->runtime);
      scheduled = host.vtable.schedule_function(
          host.native, realm->document_host.active_host_context, callback_id,
          timeout_ms, is_interval ? 1 : 0, &handle);
    }
    if (!scheduled || handle <= 0) {
      realm->timer_callbacks.erase(entry);
      ThrowTypeError(isolate, "timer function host callback failed");
      return;
    }
    entry->second.handle = handle;
    const bool handle_inserted =
        realm->timer_handles.try_emplace(handle, callback_id).second;
    if (!handle_inserted) {
      {
        RustCallbackScope callback_scope(realm->runtime);
        host.vtable.clear(host.native, handle);
      }
      realm->timer_callbacks.erase(entry);
      ThrowTypeError(isolate, "timer host returned a duplicate active handle");
      return;
    }
  } else {
    if (!host.vtable.schedule_string) {
      ThrowTypeError(isolate, "timer string host callback is unavailable");
      return;
    }
    v8::Local<v8::String> source;
    if (!info[0]->ToString(context).ToLocal(&source)) return;
    v8::String::Utf8Value utf8(isolate, source);
    if (!*utf8 && utf8.length() != 0) return;
    uint8_t scheduled = 0;
    {
      RustCallbackScope callback_scope(realm->runtime);
      scheduled = host.vtable.schedule_string(
          host.native, realm->document_host.active_host_context,
          reinterpret_cast<const uint8_t*>(*utf8),
          static_cast<size_t>(utf8.length()), timeout_ms,
          is_interval ? 1 : 0, &handle);
    }
    if (!scheduled || handle < 0) {
      ThrowTypeError(isolate, "timer string host callback failed");
      return;
    }
  }

  info.GetReturnValue().Set(handle);
}

void SetTimeout(const v8::FunctionCallbackInfo<v8::Value>& info) {
  ScheduleTimer(info, false);
}

void SetInterval(const v8::FunctionCallbackInfo<v8::Value>& info) {
  ScheduleTimer(info, true);
}

void ClearTimer(const v8::FunctionCallbackInfo<v8::Value>& info) {
  v8::Isolate* isolate = info.GetIsolate();
  ServoV8RealmState* realm = CallbackRealm(info);
  if (!realm || !realm->timer_host.native || !realm->timer_host.vtable.clear ||
      realm->runtime->rust_callback_depth != 0) {
    ThrowTypeError(isolate, "invalid timer host state");
    return;
  }
  int32_t handle = 0;
  if (!TimerHandle(isolate->GetCurrentContext(), info, &handle)) return;

  const auto mapping = realm->timer_handles.find(handle);
  if (mapping != realm->timer_handles.end()) {
    realm->timer_callbacks.erase(mapping->second);
    realm->timer_handles.erase(mapping);
  }
  {
    RustCallbackScope callback_scope(realm->runtime);
    realm->timer_host.vtable.clear(realm->timer_host.native, handle);
  }
}

bool InstallTimerGlobals(ServoV8RealmState* realm,
                         v8::Local<v8::Context> context,
                         v8::Local<v8::Object> global) {
  v8::Isolate* isolate = realm->runtime->isolate;
  v8::Local<v8::BigInt> data = v8::BigInt::NewFromUnsigned(isolate, realm->id);
  const v8::PropertyAttribute attributes = v8::DontEnum;
  struct TimerOperation {
    const char* name;
    v8::FunctionCallback callback;
    int length;
  };
  const TimerOperation operations[] = {
      {"setTimeout", &SetTimeout, 1},
      {"clearTimeout", &ClearTimer, 0},
      {"setInterval", &SetInterval, 1},
      {"clearInterval", &ClearTimer, 0},
  };
  for (const auto& operation : operations) {
    v8::Local<v8::String> name = V8String(isolate, operation.name);
    v8::Local<v8::Function> function;
    if (!v8::Function::New(context, operation.callback, data, operation.length,
                           v8::ConstructorBehavior::kThrow)
             .ToLocal(&function)) {
      return false;
    }
    function->SetName(name);
    if (!global->DefineOwnProperty(context, name, function, attributes)
             .FromMaybe(false)) {
      return false;
    }
  }
  return true;
}

bool AppendConsoleValue(v8::Isolate* isolate,
                        v8::Local<v8::Context> context,
                        v8::Local<v8::Value> value,
                        std::string* output) {
  v8::Local<v8::String> rendered;
  // ToDetailString is explicitly side-effect free. Console inspection must
  // not run a page-defined toString getter merely to produce embedder output.
  if (!value->ToDetailString(context).ToLocal(&rendered)) return false;
  v8::String::Utf8Value utf8(isolate, rendered);
  if (!*utf8) return false;
  output->append(*utf8, utf8.length());
  return true;
}

bool FormatConsoleArguments(v8::Isolate* isolate,
                            v8::Local<v8::Context> context,
                            const v8::FunctionCallbackInfo<v8::Value>& info,
                            std::string* output) {
  for (int index = 0; index < info.Length(); ++index) {
    if (index != 0) output->push_back(' ');
    if (!AppendConsoleValue(isolate, context, info[index], output)) {
      return false;
    }
  }
  return true;
}

void AppendTraceStack(v8::Isolate* isolate, std::string* output) {
  v8::Local<v8::StackTrace> stack = v8::StackTrace::CurrentStackTrace(
      isolate, 32, v8::StackTrace::kDetailed);
  if (stack.IsEmpty()) return;
  for (int index = 0; index < stack->GetFrameCount(); ++index) {
    v8::Local<v8::StackFrame> frame = stack->GetFrame(isolate, index);
    if (frame.IsEmpty()) continue;
    output->append("\n    at ");

    v8::Local<v8::String> function = frame->GetFunctionName();
    if (function.IsEmpty() || function->Length() == 0) {
      output->append("<anonymous>");
    } else {
      v8::String::Utf8Value function_utf8(isolate, function);
      if (*function_utf8) output->append(*function_utf8, function_utf8.length());
    }

    v8::Local<v8::String> resource = frame->GetScriptNameOrSourceURL();
    output->append(" (");
    if (resource.IsEmpty() || resource->Length() == 0) {
      output->append("<anonymous>");
    } else {
      v8::String::Utf8Value resource_utf8(isolate, resource);
      if (*resource_utf8) output->append(*resource_utf8, resource_utf8.length());
    }
    output->push_back(':');
    output->append(std::to_string(frame->GetLineNumber()));
    output->push_back(':');
    output->append(std::to_string(frame->GetColumn()));
    output->push_back(')');
  }
}

void ConsoleWrite(const v8::FunctionCallbackInfo<v8::Value>& info,
                  uint32_t level) {
  v8::Isolate* isolate = info.GetIsolate();
  ServoV8RealmState* realm = CallbackRealm(info);
  if (!realm || !realm->console_host.native ||
      !realm->console_host.vtable.write) {
    ThrowTypeError(isolate, "invalid or uninstalled console host state");
    return;
  }
  if (realm->runtime->rust_callback_depth != 0) {
    ThrowTypeError(isolate, "re-entrant console host callback");
    return;
  }

  std::string message;
  if (!FormatConsoleArguments(isolate, isolate->GetCurrentContext(), info,
                              &message)) {
    return;
  }
  if (level == SERVO_V8_CONSOLE_TRACE) AppendTraceStack(isolate, &message);
  {
    RustCallbackScope callback_scope(realm->runtime);
    realm->console_host.vtable.write(
        realm->console_host.native, level,
        reinterpret_cast<const uint8_t*>(message.data()), message.size());
  }
}

void ConsoleDebug(const v8::FunctionCallbackInfo<v8::Value>& info) {
  ConsoleWrite(info, SERVO_V8_CONSOLE_DEBUG);
}

void ConsoleError(const v8::FunctionCallbackInfo<v8::Value>& info) {
  ConsoleWrite(info, SERVO_V8_CONSOLE_ERROR);
}

void ConsoleInfo(const v8::FunctionCallbackInfo<v8::Value>& info) {
  ConsoleWrite(info, SERVO_V8_CONSOLE_INFO);
}

void ConsoleLog(const v8::FunctionCallbackInfo<v8::Value>& info) {
  ConsoleWrite(info, SERVO_V8_CONSOLE_LOG);
}

void ConsoleTrace(const v8::FunctionCallbackInfo<v8::Value>& info) {
  ConsoleWrite(info, SERVO_V8_CONSOLE_TRACE);
}

void ConsoleWarn(const v8::FunctionCallbackInfo<v8::Value>& info) {
  ConsoleWrite(info, SERVO_V8_CONSOLE_WARN);
}

bool InstallConsoleGlobal(ServoV8RealmState* realm,
                          v8::Local<v8::Context> context,
                          v8::Local<v8::Object> global) {
  v8::Isolate* isolate = realm->runtime->isolate;
  v8::Local<v8::BigInt> data = v8::BigInt::NewFromUnsigned(isolate, realm->id);
  v8::Local<v8::Object> console = v8::Object::New(isolate);
  const v8::PropertyAttribute method_attributes = v8::None;
  struct ConsoleOperation {
    const char* name;
    v8::FunctionCallback callback;
  };
  const ConsoleOperation operations[] = {
      {"debug", &ConsoleDebug}, {"error", &ConsoleError},
      {"info", &ConsoleInfo},   {"log", &ConsoleLog},
      {"trace", &ConsoleTrace}, {"warn", &ConsoleWarn},
  };
  for (const auto& operation : operations) {
    v8::Local<v8::String> name = V8String(isolate, operation.name);
    v8::Local<v8::Function> function;
    if (!v8::Function::New(context, operation.callback, data, 0,
                           v8::ConstructorBehavior::kThrow)
             .ToLocal(&function)) {
      return false;
    }
    function->SetName(name);
    if (!console->DefineOwnProperty(context, name, function, method_attributes)
             .FromMaybe(false)) {
      return false;
    }
  }

  const v8::PropertyAttribute tag_attributes =
      static_cast<v8::PropertyAttribute>(v8::ReadOnly | v8::DontEnum);
  if (!console
           ->DefineOwnProperty(context, v8::Symbol::GetToStringTag(isolate),
                               V8String(isolate, "Console"), tag_attributes)
           .FromMaybe(false)) {
    return false;
  }

  // Replace V8's inspector-oriented console. With no inspector delegate its
  // methods silently discard output, which is worse than an explicit gap.
  if (!global->Delete(context, V8String(isolate, "console")).FromMaybe(false)) {
    return false;
  }
  return global
      ->DefineOwnProperty(context, V8String(isolate, "console"), console,
                          v8::DontEnum)
      .FromMaybe(false);
}

void ResetDocumentHost(ServoV8DocumentHostState* state) {
  void* native = std::exchange(state->native, nullptr);
  const ServoV8DropCallback drop = state->vtable.drop;
  state->vtable = {};
  if (native && drop) {
    RustCallbackScope callback_scope(state->runtime);
    drop(native);
  }
}

void ResetTimerHost(ServoV8TimerHostState* state) {
  void* native = std::exchange(state->native, nullptr);
  const ServoV8DropCallback drop = state->vtable.drop;
  state->vtable = {};
  if (native && drop) {
    RustCallbackScope callback_scope(state->runtime);
    drop(native);
  }
}

void ResetConsoleHost(ServoV8ConsoleHostState* state) {
  void* native = std::exchange(state->native, nullptr);
  const ServoV8DropCallback drop = state->vtable.drop;
  state->vtable = {};
  if (native && drop) {
    RustCallbackScope callback_scope(state->runtime);
    drop(native);
  }
}

// Drops buffered job state owned by one realm before that realm's context and
// embedder state are detached. Pending rejections contain strong V8 handles;
// leaving one behind would keep the destroyed context alive and would make the
// handle outlive the realm state used for attribution.
void ClearPendingJobStateForRealm(ServoV8Runtime* runtime,
                                  ServoV8RealmId realm_id) {
  auto& errors = runtime->pending_job_errors;
  errors.erase(
      std::remove_if(errors.begin(), errors.end(),
                     [realm_id](const ServoV8PendingJobError& error) {
                       return error.realm_id == realm_id;
                     }),
      errors.end());

  auto& rejections = runtime->pending_rejections;
  for (auto entry = rejections.begin(); entry != rejections.end();) {
    if (entry->error.realm_id != realm_id) {
      ++entry;
      continue;
    }
    entry->promise.Reset();
    entry = rejections.erase(entry);
  }
}

// Every v8::Global must be reset before Isolate::Dispose. This also covers
// failures whose realm could not be determined and therefore could not be
// removed by ClearPendingJobStateForRealm.
void ClearPendingJobState(ServoV8Runtime* runtime) {
  runtime->pending_job_errors.clear();
  for (auto& rejection : runtime->pending_rejections) {
    rejection.promise.Reset();
  }
  runtime->pending_rejections.clear();
}

void DetachRealm(ServoV8Runtime* runtime, ServoV8RealmState* realm) {
  realm->tearing_down = true;
  ClearPendingJobStateForRealm(runtime, realm->id);
  if (!realm->context.IsEmpty()) {
    v8::Local<v8::Context> context = realm->context.Get(runtime->isolate);
    context->SetAlignedPointerInEmbedderData(
        kServoRealmStateEmbedderSlot, nullptr, kServoRealmStateEmbedderTag);
  }
  realm->scripts.clear();
  realm->timer_handles.clear();
  realm->timer_callbacks.clear();
  // Release every host now. The cells stay cppgc-owned and die whenever the
  // next collection runs, but their Servo roots must not outlive the realm --
  // otherwise a torn-down pipeline's DOM stays pinned until the isolate
  // happens to collect, which for an idle tab may be never.
  for (auto& entry : realm->wrappers) {
    if (ServoV8HostCell* cell = entry.second.Get()) cell->ReleaseHost();
  }
  realm->wrappers.clear();
  realm->element_template.Reset();
  realm->element_prototype.Reset();
  realm->document.Reset();
  realm->context.Reset();
  ResetConsoleHost(&realm->console_host);
  ResetTimerHost(&realm->timer_host);
  ResetDocumentHost(&realm->document_host);
  runtime->isolate->ContextDisposedNotification(
      v8::ContextDependants::kSomeDependants);
}

bool CompileAndRun(ServoV8Runtime* runtime,
                   v8::Local<v8::Context> context,
                   const uint8_t* source,
                   size_t source_length,
                   v8::Local<v8::Value>* result,
                   ServoV8ErrorBuffer* error) {
  if (!source && source_length != 0) {
    WriteError(error, "source pointer is null");
    return false;
  }
  if (source_length > static_cast<size_t>(std::numeric_limits<int>::max())) {
    WriteError(error, "source is too large for a V8 string");
    return false;
  }

  v8::Isolate* isolate = runtime->isolate;
  v8::TryCatch try_catch(isolate);
  v8::Local<v8::String> source_string;
  if (!v8::String::NewFromUtf8(isolate, reinterpret_cast<const char*>(source),
                               v8::NewStringType::kNormal,
                               static_cast<int>(source_length))
           .ToLocal(&source_string)) {
    WriteError(error, "V8 could not allocate the source string");
    return false;
  }
  v8::Local<v8::Script> script;
  if (!v8::Script::Compile(context, source_string).ToLocal(&script) ||
      !script->Run(context).ToLocal(result)) {
    WriteError(error, TryCatchMessage(isolate, try_catch));
    return false;
  }
  isolate->PerformMicrotaskCheckpoint();
  return true;
}

bool CompileScript(ServoV8Runtime* runtime,
                   v8::Local<v8::Context> context,
                   const uint8_t* source,
                   size_t source_length,
                   const uint8_t* resource_name,
                   size_t resource_name_length,
                   uint32_t line_number,
                   v8::Local<v8::Script>* result,
                   ServoV8ScriptException* exception,
                   bool* threw,
                   ServoV8ErrorBuffer* error) {
  if (threw) *threw = false;
  if ((!source && source_length != 0) ||
      (!resource_name && resource_name_length != 0)) {
    WriteError(error, "script source or resource-name pointer is null");
    return false;
  }
  if (source_length > static_cast<size_t>(std::numeric_limits<int>::max()) ||
      resource_name_length >
          static_cast<size_t>(std::numeric_limits<int>::max())) {
    WriteError(error, "script source or resource name is too large");
    return false;
  }
  const uint32_t zero_based_line = line_number > 0 ? line_number - 1 : 0;
  if (zero_based_line >
      static_cast<uint32_t>(std::numeric_limits<int>::max())) {
    WriteError(error, "script line number is too large");
    return false;
  }

  v8::Isolate* isolate = runtime->isolate;
  v8::TryCatch try_catch(isolate);
  v8::Local<v8::String> source_string;
  v8::Local<v8::String> resource_name_string;
  if (!v8::String::NewFromUtf8(
           isolate, reinterpret_cast<const char*>(source),
           v8::NewStringType::kNormal, static_cast<int>(source_length))
           .ToLocal(&source_string) ||
      !v8::String::NewFromUtf8(
           isolate, reinterpret_cast<const char*>(resource_name),
           v8::NewStringType::kNormal,
           static_cast<int>(resource_name_length))
           .ToLocal(&resource_name_string)) {
    WriteError(error, "V8 could not allocate script source metadata");
    return false;
  }
  v8::ScriptOrigin origin(resource_name_string,
                          static_cast<int>(zero_based_line));
  v8::Local<v8::Script> compiled_script;
  if (!v8::Script::Compile(context, source_string, &origin)
           .ToLocal(&compiled_script)) {
    if (exception && threw && try_catch.HasCaught()) {
      *threw = true;
      CaptureScriptException(isolate, context, try_catch, exception);
      return true;
    }
    WriteError(error, TryCatchMessage(isolate, try_catch));
    return false;
  }
  if (result) *result = compiled_script;
  return true;
}

}  // namespace

ServoV8DomCell::~ServoV8DomCell() {
  wrapper_.Reset();
  void* native = std::exchange(native_, nullptr);
  if (!native || !vtable_.drop) return;
  RustCallbackScope callback_scope(runtime_);
  vtable_.drop(native);
}

void ServoV8DomCell::Trace(cppgc::Visitor* visitor) const {
  v8::Object::Wrappable::Trace(visitor);
  visitor->Trace(wrapper_);
  if (!native_ || !vtable_.trace) return;
  ServoV8TraceVisitor rust_visitor{visitor};
  RustCallbackScope callback_scope(runtime_);
  vtable_.trace(native_, &rust_visitor);
}

void ServoV8HostCell::ReleaseHost() {
  if (!native_) return;
  void* native = native_;
  native_ = nullptr;
  if (!drop_) return;
  // Guarded like every other Rust callback: without this, CheckRuntime would
  // accept a re-entrant bridge call made from a host's Drop, which during
  // sweeping would re-enter V8 mid-collection.
  RustCallbackScope callback_scope(runtime_);
  drop_(native);
}

extern "C" uint32_t servo_v8_abi_version(void) {
  return SERVO_V8_ABI_VERSION;
}

extern "C" ServoV8Runtime* servo_v8_runtime_new(
    const ServoV8Options* options,
    ServoV8ErrorBuffer* error) {
  ClearError(error);
  const ServoV8Options actual_options = options ? *options : ServoV8Options{};
  if (!GlobalState().Initialize(actual_options, error)) return nullptr;

  auto runtime = std::make_unique<ServoV8Runtime>();
  runtime->allocator.reset(v8::ArrayBuffer::Allocator::NewDefaultAllocator());

  v8::CppHeapCreateParams cpp_heap_params({});
  // Atomic is load-bearing in two ways beyond the missing mutation barriers.
  // Destructors run in the GC pause on the owner thread, which is what makes
  // it sound for a cell to drop a host holding non-atomic Rust state (an Rc,
  // a Trusted). And weak references are cleared during marking while
  // destructors run during sweeping, so a wrapper-cache entry can read as
  // empty while its host is still alive -- atomic sweeping closes that window
  // before control returns to the embedder.
  cpp_heap_params.marking_support = cppgc::Heap::MarkingType::kAtomic;
  cpp_heap_params.sweeping_support = cppgc::Heap::SweepingType::kAtomic;
  std::unique_ptr<v8::CppHeap> cpp_heap =
      v8::CppHeap::Create(GlobalState().platform(), cpp_heap_params);

  v8::Isolate::CreateParams isolate_params;
  isolate_params.array_buffer_allocator = runtime->allocator.get();
  isolate_params.cpp_heap = cpp_heap.release();
  runtime->isolate = v8::Isolate::New(isolate_params);
  if (!runtime->isolate) {
    WriteError(error, "V8 failed to allocate an isolate");
    return nullptr;
  }
  runtime->owner_thread = std::this_thread::get_id();
  runtime->expose_gc = actual_options.expose_gc != 0;
  runtime->isolate->SetMicrotasksPolicy(v8::MicrotasksPolicy::kExplicit);
  runtime->isolate->SetData(kServoRuntimeIsolateSlot, runtime.get());

  {
    v8::Isolate::Scope isolate_scope(runtime->isolate);
    v8::HandleScope handle_scope(runtime->isolate);
    // Must be inside the isolate scope: registering a listener allocates on
    // the V8 heap. The default data makes V8 pass the thrown value to the
    // listener, and the stack stays empty unless capture is enabled.
    runtime->isolate->AddMessageListener(&OnUncaughtMessage);
    runtime->isolate->SetCaptureStackTraceForUncaughtExceptions(true, 16);
    runtime->isolate->SetPromiseRejectCallback(&OnPromiseReject);
    v8::Local<v8::Context> context = v8::Context::New(runtime->isolate);
    if (context.IsEmpty()) {
      WriteError(error, "V8 failed to allocate a context");
      runtime->isolate->Dispose();
      runtime->isolate = nullptr;
      return nullptr;
    }
    runtime->context.Reset(runtime->isolate, context);
  }
  runtime->isolate->AddGCEpilogueCallback(
      &PruneDeadWrapperCacheEntries, runtime.get(),
      v8::kGCTypeMarkSweepCompact);
  return runtime.release();
}

extern "C" void servo_v8_runtime_delete(ServoV8Runtime* runtime) {
  if (!runtime) return;
  if (runtime->isolate) {
    // A void destructor cannot report misuse. Leaking is safer than disposing
    // an isolate on the wrong thread or beneath an active Rust callback.
    if (runtime->owner_thread != std::this_thread::get_id() ||
        runtime->rust_callback_depth != 0) {
      return;
    }
    {
      v8::Isolate::Scope isolate_scope(runtime->isolate);
      v8::HandleScope handle_scope(runtime->isolate);
      runtime->isolate->RemoveGCEpilogueCallback(
          &PruneDeadWrapperCacheEntries, runtime);
      for (auto& realm : runtime->realms) {
        DetachRealm(runtime, realm.second.get());
      }
      runtime->realms.clear();
      ClearPendingJobState(runtime);
      runtime->context.Reset();
    }
    runtime->isolate->Dispose();
    runtime->isolate = nullptr;
  }
  delete runtime;
}

extern "C" int32_t servo_v8_realm_create(
    ServoV8Runtime* runtime,
    ServoV8RealmId* realm_id,
    ServoV8ErrorBuffer* error) {
  ClearError(error);
  if (!realm_id || !CheckRuntime(runtime, error)) return 0;
  if (runtime->next_realm_id == 0) {
    WriteError(error, "Servo V8 realm ID space is exhausted");
    return 0;
  }

  v8::Isolate* isolate = runtime->isolate;
  v8::Isolate::Scope isolate_scope(isolate);
  v8::HandleScope handle_scope(isolate);
  auto realm = std::make_unique<ServoV8RealmState>();
  realm->runtime = runtime;
  const ServoV8RealmId id = runtime->next_realm_id++;
  realm->id = id;
  realm->document_host.runtime = runtime;
  realm->timer_host.runtime = runtime;
  realm->console_host.runtime = runtime;
  v8::Local<v8::Context> context = v8::Context::New(isolate);
  if (context.IsEmpty()) {
    WriteError(error, "V8 failed to allocate a realm context");
    return 0;
  }

  context->SetAlignedPointerInEmbedderData(
      kServoRealmStateEmbedderSlot, realm.get(),
      kServoRealmStateEmbedderTag);
  v8::Context::Scope context_scope(context);
  v8::TryCatch try_catch(isolate);
  v8::Local<v8::Object> document_prototype = v8::Object::New(isolate);
  v8::Local<v8::Object> document = v8::Object::New(isolate);
  v8::Local<v8::Object> global = context->Global();
  const v8::PropertyAttribute immutable = static_cast<v8::PropertyAttribute>(
      v8::ReadOnly | v8::DontDelete);
  if (!InstallDocumentHostMembers(isolate, context, document_prototype)) {
    context->SetAlignedPointerInEmbedderData(
        kServoRealmStateEmbedderSlot, nullptr,
        kServoRealmStateEmbedderTag);
    WriteError(error, TryCatchMessage(isolate, try_catch));
    return 0;
  }
  if (!InstallConsoleGlobal(realm.get(), context, global)) {
    context->SetAlignedPointerInEmbedderData(
        kServoRealmStateEmbedderSlot, nullptr,
        kServoRealmStateEmbedderTag);
    WriteError(error, TryCatchMessage(isolate, try_catch));
    return 0;
  }
  if (!InstallTimerGlobals(realm.get(), context, global)) {
    context->SetAlignedPointerInEmbedderData(
        kServoRealmStateEmbedderSlot, nullptr,
        kServoRealmStateEmbedderTag);
    WriteError(error, TryCatchMessage(isolate, try_catch));
    return 0;
  }

  // Element instances are built from a FunctionTemplate instance template,
  // which is what makes them wrappable by v8::Object::Wrap. WebIDL members
  // live on one realm-shared prototype, not as own properties on each wrapper.
  v8::Local<v8::FunctionTemplate> element_constructor =
      v8::FunctionTemplate::New(isolate);
  element_constructor->SetClassName(V8String(isolate, "Element"));
  v8::Local<v8::ObjectTemplate> element_instance =
      element_constructor->InstanceTemplate();
  v8::Local<v8::Object> element_prototype = v8::Object::New(isolate);
  if (!InstallElementPrototype(realm.get(), context, element_prototype) ||
      !document->SetPrototype(context, document_prototype).FromMaybe(false) ||
      !global
           ->DefineOwnProperty(context, V8String(isolate, "window"), global,
                               immutable)
           .FromMaybe(false) ||
      !global
           ->DefineOwnProperty(context, V8String(isolate, "document"), document,
                               immutable)
           .FromMaybe(false)) {
    context->SetAlignedPointerInEmbedderData(
        kServoRealmStateEmbedderSlot, nullptr,
        kServoRealmStateEmbedderTag);
    WriteError(error, TryCatchMessage(isolate, try_catch));
    return 0;
  }

  realm->element_template.Reset(isolate, element_instance);
  realm->element_prototype.Reset(isolate, element_prototype);
  realm->context.Reset(isolate, context);
  realm->document.Reset(isolate, document);
  auto [entry, inserted] = runtime->realms.try_emplace(id, std::move(realm));
  if (!inserted) {
    context->SetAlignedPointerInEmbedderData(
        kServoRealmStateEmbedderSlot, nullptr,
        kServoRealmStateEmbedderTag);
    WriteError(error, "Servo V8 realm ID collision");
    return 0;
  }
  *realm_id = id;
  return 1;
}

extern "C" int32_t servo_v8_realm_destroy(
    ServoV8Runtime* runtime,
    ServoV8RealmId realm_id,
    ServoV8ErrorBuffer* error) {
  ClearError(error);
  if (!CheckRuntime(runtime, error)) return 0;
  const auto entry = runtime->realms.find(realm_id);
  if (entry == runtime->realms.end()) {
    WriteError(error, "unknown or destroyed Servo V8 realm " +
                          std::to_string(realm_id));
    return 0;
  }
  v8::Isolate::Scope isolate_scope(runtime->isolate);
  v8::HandleScope handle_scope(runtime->isolate);
  DetachRealm(runtime, entry->second.get());
  runtime->realms.erase(entry);
  return 1;
}

extern "C" int32_t servo_v8_realm_eval_bool(
    ServoV8Runtime* runtime,
    ServoV8RealmId realm_id,
    const uint8_t* source,
    size_t source_length,
    uint8_t* result,
    ServoV8ErrorBuffer* error) {
  ClearError(error);
  if (!result || !CheckRuntime(runtime, error)) return 0;
  v8::Isolate* isolate = runtime->isolate;
  v8::Isolate::Scope isolate_scope(isolate);
  v8::HandleScope handle_scope(isolate);
  v8::Local<v8::Context> context =
      FindRealmContext(runtime, realm_id, error);
  if (context.IsEmpty()) return 0;
  v8::Context::Scope context_scope(context);
  v8::Local<v8::Value> value;
  if (!CompileAndRun(runtime, context, source, source_length, &value, error)) {
    return 0;
  }
  *result = value->BooleanValue(isolate) ? 1 : 0;
  return 1;
}

extern "C" int32_t servo_v8_realm_compile(
    ServoV8Runtime* runtime,
    ServoV8RealmId realm_id,
    const uint8_t* source,
    size_t source_length,
    const uint8_t* resource_name,
    size_t resource_name_length,
    uint32_t line_number,
    ServoV8ErrorBuffer* error) {
  ClearError(error);
  if (!CheckRuntime(runtime, error)) return 0;
  v8::Isolate* isolate = runtime->isolate;
  v8::Isolate::Scope isolate_scope(isolate);
  v8::HandleScope handle_scope(isolate);
  v8::Local<v8::Context> context =
      FindRealmContext(runtime, realm_id, error);
  if (context.IsEmpty()) return 0;
  v8::Context::Scope context_scope(context);
  return CompileScript(runtime, context, source, source_length, resource_name,
                       resource_name_length, line_number, nullptr, nullptr,
                       nullptr, error)
             ? 1
             : 0;
}

extern "C" int32_t servo_v8_realm_script_compile(
    ServoV8Runtime* runtime,
    ServoV8RealmId realm_id,
    const uint8_t* source,
    size_t source_length,
    const uint8_t* resource_name,
    size_t resource_name_length,
    uint32_t line_number,
    ServoV8ScriptCompileOutcome* outcome,
    ServoV8ErrorBuffer* error) {
  ClearError(error);
  if (!outcome) {
    WriteError(error, "classic-script compile outcome pointer is null");
    return 0;
  }
  ClearScriptCompileOutcome(outcome);
  if (!CheckRuntime(runtime, error)) return 0;
  if (runtime->next_script_id == 0) {
    WriteError(error, "Servo V8 script ID space is exhausted");
    return 0;
  }
  ServoV8RealmState* realm = FindRealm(runtime, realm_id, error);
  if (!realm) return 0;

  v8::Isolate* isolate = runtime->isolate;
  v8::Isolate::Scope isolate_scope(isolate);
  v8::HandleScope handle_scope(isolate);
  v8::Local<v8::Context> context = realm->context.Get(isolate);
  v8::Context::Scope context_scope(context);
  v8::Local<v8::Script> compiled_script;
  bool threw = false;
  if (!CompileScript(runtime, context, source, source_length, resource_name,
                     resource_name_length, line_number, &compiled_script,
                     &outcome->exception, &threw, error)) {
    return 0;
  }
  if (threw) {
    outcome->status = SERVO_V8_SCRIPT_COMPILE_THROWN;
    return 1;
  }

  const ServoV8ScriptId id = runtime->next_script_id++;
  auto [entry, inserted] = realm->scripts.try_emplace(
      id, isolate, compiled_script);
  if (!inserted) {
    WriteError(error, "Servo V8 script ID collision");
    return 0;
  }
  outcome->script_id = id;
  return 1;
}

extern "C" int32_t servo_v8_realm_script_run(
    ServoV8Runtime* runtime,
    ServoV8RealmId realm_id,
    ServoV8ScriptId script_id,
    void* host_context,
    ServoV8ScriptRunOutcome* outcome,
    ServoV8ErrorBuffer* error) {
  ClearError(error);
  if (!outcome) {
    WriteError(error, "classic-script run outcome pointer is null");
    return 0;
  }
  ClearScriptRunOutcome(outcome);
  if (!CheckRuntime(runtime, error)) return 0;
  ServoV8RealmState* realm = FindRealm(runtime, realm_id, error);
  if (!realm) return 0;
  if (realm->document_host.active_host_context) {
    WriteError(error, "Document host context is already active");
    return 0;
  }
  auto entry = realm->scripts.find(script_id);
  if (entry == realm->scripts.end()) {
    WriteError(error, "unknown or consumed Servo V8 script " +
                          std::to_string(script_id) + " in realm " +
                          std::to_string(realm_id));
    return 0;
  }

  // Consume before entering user code. A throwing script must not become
  // accidentally replayable through the embedding API.
  v8::Global<v8::Script> retained = std::move(entry->second);
  realm->scripts.erase(entry);

  v8::Isolate* isolate = runtime->isolate;
  v8::Isolate::Scope isolate_scope(isolate);
  v8::HandleScope handle_scope(isolate);
  v8::Local<v8::Context> context = realm->context.Get(isolate);
  v8::Context::Scope context_scope(context);
  ActiveHostContextScope host_context_scope(&realm->document_host,
                                            host_context);
  v8::TryCatch try_catch(isolate);
  v8::Local<v8::Script> script = retained.Get(isolate);
  v8::Local<v8::Value> value;
  if (!script->Run(context).ToLocal(&value)) {
    if (try_catch.HasTerminated()) {
      outcome->status = SERVO_V8_SCRIPT_RUN_TERMINATED;
      return 1;
    }
    outcome->status = SERVO_V8_SCRIPT_RUN_THROWN;
    CaptureScriptException(isolate, context, try_catch,
                           &outcome->exception);
    return 1;
  }
  return 1;
}

extern "C" int32_t servo_v8_realm_script_discard(
    ServoV8Runtime* runtime,
    ServoV8RealmId realm_id,
    ServoV8ScriptId script_id,
    ServoV8ErrorBuffer* error) {
  ClearError(error);
  if (!CheckRuntime(runtime, error)) return 0;
  ServoV8RealmState* realm = FindRealm(runtime, realm_id, error);
  if (!realm) return 0;
  const auto entry = realm->scripts.find(script_id);
  if (entry == realm->scripts.end()) {
    WriteError(error, "unknown or consumed Servo V8 script " +
                          std::to_string(script_id) + " in realm " +
                          std::to_string(realm_id));
    return 0;
  }
  realm->scripts.erase(entry);
  return 1;
}

extern "C" int32_t servo_v8_runtime_perform_microtask_checkpoint(
    ServoV8Runtime* runtime,
    void* host_context,
    ServoV8ScriptRunOutcome* outcome,
    ServoV8ErrorBuffer* error) {
  ClearError(error);
  if (!outcome) {
    WriteError(error, "microtask checkpoint outcome pointer is null");
    return 0;
  }
  ClearScriptRunOutcome(outcome);
  if (!CheckRuntime(runtime, error)) return 0;

  // The script-run path's barrier, generalised isolate-wide: any realm holding
  // an active host context means a script or an earlier checkpoint is already
  // inside the bridge, and a checkpoint must not nest inside either.
  for (const auto& entry : runtime->realms) {
    if (entry.second->document_host.active_host_context) {
      WriteError(error, "Document host context is already active");
      return 0;
    }
  }

  v8::Isolate* isolate = runtime->isolate;
  v8::Isolate::Scope isolate_scope(isolate);
  v8::HandleScope handle_scope(isolate);
  AllRealmsHostContextScope host_context_scope(runtime, host_context);
  MicrotaskDrainScope drain_scope(runtime);
  v8::TryCatch try_catch(isolate);
  // Jobs carry their own context, so no realm context is entered here.
  isolate->PerformMicrotaskCheckpoint();
  if (try_catch.HasTerminated()) {
    outcome->status = SERVO_V8_SCRIPT_RUN_TERMINATED;
    return 1;
  }
  // A job that throws never reaches this TryCatch; it arrives through
  // OnUncaughtMessage while the drain is running. The caller pulls those with
  // servo_v8_runtime_take_pending_job_error, because one drain can produce
  // more errors than a single outcome can carry.
  return 1;
}

extern "C" int32_t servo_v8_runtime_take_pending_job_error(
    ServoV8Runtime* runtime,
    ServoV8RealmId* realm_id,
    ServoV8ScriptException* exception,
    uint8_t* has_error,
    ServoV8ErrorBuffer* error) {
  ClearError(error);
  if (!exception || !has_error || !realm_id) {
    WriteError(error, "microtask job error output pointer is null");
    return 0;
  }
  *has_error = 0;
  *realm_id = 0;
  ClearScriptException(exception);
  if (!CheckRuntime(runtime, error)) return 0;

  // Oldest first, so Rust observes failures in the order they happened.
  // Uncaught job exceptions drain before rejections that are still unhandled.
  ServoV8PendingJobError pending;
  if (!runtime->pending_job_errors.empty()) {
    pending = std::move(runtime->pending_job_errors.front());
    runtime->pending_job_errors.erase(runtime->pending_job_errors.begin());
  } else if (!runtime->pending_rejections.empty()) {
    pending = std::move(runtime->pending_rejections.front().error);
    runtime->pending_rejections.erase(runtime->pending_rejections.begin());
  } else {
    return 1;
  }
  *realm_id = pending.realm_id;
  WriteError(&exception->message, pending.message);
  WriteError(&exception->resource_name, pending.resource_name);
  WriteError(&exception->stack, pending.stack);
  exception->line_number = pending.line_number;
  exception->column_number = pending.column_number;
  *has_error = 1;
  return 1;
}


extern "C" int32_t servo_v8_realm_install_document_host(
    ServoV8Runtime* runtime,
    ServoV8RealmId realm_id,
    void* native,
    const ServoV8DocumentHostVTable* vtable,
    ServoV8ErrorBuffer* error) {
  ClearError(error);
  if (!CheckRuntime(runtime, error)) return 0;
  if (!native) {
    WriteError(error, "Document host native pointer is null");
    return 0;
  }
  if (!vtable || !IsDocumentHostVTableComplete(*vtable)) {
    WriteError(error, "Document host vtable is incomplete");
    return 0;
  }
  ServoV8RealmState* realm = FindRealm(runtime, realm_id, error);
  if (!realm) return 0;
  if (realm->tearing_down) {
    WriteError(error, "cannot install a Document host while its realm tears down");
    return 0;
  }
  if (realm->document_host.native) {
    WriteError(error, "Document host is already installed in this realm");
    return 0;
  }

  // Realm creation installs every V8 handle and accessor first, so this is a
  // no-fail ownership handoff after all validation has completed.
  realm->document_host.native = native;
  realm->document_host.vtable = *vtable;
  return 1;
}

extern "C" int32_t servo_v8_realm_install_timer_host(
    ServoV8Runtime* runtime,
    ServoV8RealmId realm_id,
    void* native,
    const ServoV8TimerHostVTable* vtable,
    ServoV8ErrorBuffer* error) {
  ClearError(error);
  if (!CheckRuntime(runtime, error)) return 0;
  if (!native) {
    WriteError(error, "timer host native pointer is null");
    return 0;
  }
  if (!vtable || !vtable->schedule_function || !vtable->schedule_string ||
      !vtable->clear || !vtable->drop) {
    WriteError(error, "timer host vtable is incomplete");
    return 0;
  }
  ServoV8RealmState* realm = FindRealm(runtime, realm_id, error);
  if (!realm) return 0;
  if (realm->tearing_down) {
    WriteError(error, "cannot install a timer host while its realm tears down");
    return 0;
  }
  if (realm->timer_host.native) {
    WriteError(error, "timer host is already installed in this realm");
    return 0;
  }

  realm->timer_host.native = native;
  realm->timer_host.vtable = *vtable;
  return 1;
}

extern "C" int32_t servo_v8_realm_install_console_host(
    ServoV8Runtime* runtime,
    ServoV8RealmId realm_id,
    void* native,
    const ServoV8ConsoleHostVTable* vtable,
    ServoV8ErrorBuffer* error) {
  ClearError(error);
  if (!CheckRuntime(runtime, error)) return 0;
  if (!native) {
    WriteError(error, "console host native pointer is null");
    return 0;
  }
  if (!vtable || !vtable->write || !vtable->drop) {
    WriteError(error, "console host vtable is incomplete");
    return 0;
  }
  ServoV8RealmState* realm = FindRealm(runtime, realm_id, error);
  if (!realm) return 0;
  if (realm->tearing_down) {
    WriteError(error, "cannot install a console host while its realm tears down");
    return 0;
  }
  if (realm->console_host.native) {
    WriteError(error, "console host is already installed in this realm");
    return 0;
  }

  realm->console_host.native = native;
  realm->console_host.vtable = *vtable;
  return 1;
}

extern "C" int32_t servo_v8_realm_timer_callback_run(
    ServoV8Runtime* runtime,
    ServoV8RealmId realm_id,
    ServoV8TimerCallbackId callback_id,
    void* host_context,
    ServoV8ScriptRunOutcome* outcome,
    ServoV8ErrorBuffer* error) {
  ClearError(error);
  if (!outcome) {
    WriteError(error, "timer callback run outcome pointer is null");
    return 0;
  }
  ClearScriptRunOutcome(outcome);
  if (!CheckRuntime(runtime, error)) return 0;
  ServoV8RealmState* realm = FindRealm(runtime, realm_id, error);
  if (!realm) return 0;
  if (realm->document_host.active_host_context) {
    WriteError(error, "Document host context is already active");
    return 0;
  }
  const auto entry = realm->timer_callbacks.find(callback_id);
  if (entry == realm->timer_callbacks.end()) {
    WriteError(error, "unknown or cleared Servo V8 timer callback " +
                          std::to_string(callback_id) + " in realm " +
                          std::to_string(realm_id));
    return 0;
  }

  v8::Isolate* isolate = runtime->isolate;
  v8::Isolate::Scope isolate_scope(isolate);
  v8::HandleScope handle_scope(isolate);
  v8::Local<v8::Context> context = realm->context.Get(isolate);
  v8::Context::Scope context_scope(context);

  // Root every value locally before a one-shot erases its strong registry
  // entry. A callback may also clear its own interval while it is running; the
  // locals make that safe without keeping the interval alive afterward.
  v8::Local<v8::Function> function = entry->second.function.Get(isolate);
  std::vector<v8::Local<v8::Value>> arguments;
  arguments.reserve(entry->second.arguments.size());
  for (const auto& argument : entry->second.arguments) {
    arguments.push_back(argument.Get(isolate));
  }
  const bool is_interval = entry->second.is_interval;
  const int32_t handle = entry->second.handle;
  if (!is_interval) {
    realm->timer_callbacks.erase(entry);
    realm->timer_handles.erase(handle);
  }

  ActiveHostContextScope host_context_scope(&realm->document_host,
                                            host_context);
  v8::TryCatch try_catch(isolate);
  v8::Local<v8::Value> value;
  if (!function
           ->Call(context, context->Global(),
                  static_cast<int>(arguments.size()), arguments.data())
           .ToLocal(&value)) {
    if (try_catch.HasTerminated()) {
      outcome->status = SERVO_V8_SCRIPT_RUN_TERMINATED;
      return 1;
    }
    outcome->status = SERVO_V8_SCRIPT_RUN_THROWN;
    CaptureScriptException(isolate, context, try_catch,
                           &outcome->exception);
    return 1;
  }
  return 1;
}

extern "C" int32_t servo_v8_realm_timer_callback_clear(
    ServoV8Runtime* runtime,
    ServoV8RealmId realm_id,
    ServoV8TimerCallbackId callback_id,
    ServoV8ErrorBuffer* error) {
  ClearError(error);
  if (!CheckRuntime(runtime, error)) return 0;
  ServoV8RealmState* realm = FindRealm(runtime, realm_id, error);
  if (!realm) return 0;
  const auto entry = realm->timer_callbacks.find(callback_id);
  if (entry == realm->timer_callbacks.end()) return 1;
  realm->timer_handles.erase(entry->second.handle);
  realm->timer_callbacks.erase(entry);
  return 1;
}

extern "C" int32_t servo_v8_realm_document_hidden(
    ServoV8Runtime* runtime,
    ServoV8RealmId realm_id,
    uint8_t* result,
    ServoV8ErrorBuffer* error) {
  ClearError(error);
  if (!result || !CheckRuntime(runtime, error)) return 0;
  ServoV8RealmState* realm = FindRealm(runtime, realm_id, error);
  if (!realm) return 0;

  v8::Isolate* isolate = runtime->isolate;
  v8::Isolate::Scope isolate_scope(isolate);
  v8::HandleScope handle_scope(isolate);
  v8::Local<v8::Context> context = realm->context.Get(isolate);
  v8::Context::Scope context_scope(context);
  v8::TryCatch try_catch(isolate);
  v8::Local<v8::Object> document = realm->document.Get(isolate);
  v8::Local<v8::Value> value;
  if (!document->Get(context, V8String(isolate, "hidden")).ToLocal(&value)) {
    WriteError(error, TryCatchMessage(isolate, try_catch));
    return 0;
  }
  if (!value->IsBoolean()) {
    WriteError(error, "Document.hidden host accessor did not return a boolean");
    return 0;
  }
  *result = value.As<v8::Boolean>()->Value() ? 1 : 0;
  return 1;
}

extern "C" int32_t servo_v8_install_element_host(
    ServoV8Runtime* runtime,
    const ServoV8ElementHostVTable* vtable,
    ServoV8ErrorBuffer* error) {
  ClearError(error);
  if (!CheckRuntime(runtime, error)) return 0;
  if (!vtable || !vtable->get_local_name || !vtable->get_tag_name ||
      !vtable->get_id || !vtable->set_id || !vtable->get_class_name ||
      !vtable->set_class_name || !vtable->has_attributes ||
      !vtable->get_attribute || !vtable->has_attribute || !vtable->drop) {
    WriteError(error, "Element host vtable is incomplete");
    return 0;
  }
  if (runtime->element_host_installed) {
    WriteError(error, "Element host is already installed");
    return 0;
  }
  // Type-level rather than per-realm: the vtable describes how to talk to any
  // Element host, while the hosts themselves are per object.
  runtime->element_host_vtable = *vtable;
  runtime->element_host_installed = true;
  return 1;
}

extern "C" int32_t servo_v8_install_engine_binding_smoke(
    ServoV8Runtime* runtime,
    const ServoV8EngineBindingSmokeVTable* vtable,
    ServoV8ErrorBuffer* error) {
  ClearError(error);
  if (!CheckRuntime(runtime, error)) return 0;
  if (!vtable || !IsEngineBindingSmokeVTableComplete(*vtable)) {
    WriteError(error, "EngineBindingSmoke vtable is incomplete");
    return 0;
  }
  if (runtime->engine_binding_smoke_installed) {
    WriteError(error, "EngineBindingSmoke is already installed");
    return 0;
  }

  runtime->engine_binding_smoke_vtable = *vtable;
  v8::Isolate* isolate = runtime->isolate;
  v8::Isolate::Scope isolate_scope(isolate);
  v8::HandleScope handle_scope(isolate);
  v8::Local<v8::Context> context = runtime->context.Get(isolate);
  v8::Context::Scope context_scope(context);
  v8::TryCatch try_catch(isolate);

  v8::Local<v8::External> data = v8::External::New(
      isolate, runtime, v8::kExternalPointerTypeTagDefault);
  v8::Local<v8::FunctionTemplate> constructor =
      CreateEngineBindingSmokeTemplate(isolate, data);

  v8::Local<v8::Function> function;
  if (!constructor->GetFunction(context).ToLocal(&function) ||
      !context->Global()
           ->Set(context,
                 V8String(isolate, kEngineBindingSmokeInterfaceName), function)
           .FromMaybe(false)) {
    WriteError(error, TryCatchMessage(isolate, try_catch));
    return 0;
  }
  runtime->engine_binding_smoke_installed = true;
  return 1;
}

extern "C" int32_t servo_v8_eval_bool(ServoV8Runtime* runtime,
                                       const uint8_t* source,
                                       size_t source_length,
                                       uint8_t* result,
                                       ServoV8ErrorBuffer* error) {
  ClearError(error);
  if (!result || !CheckRuntime(runtime, error)) return 0;
  v8::Isolate* isolate = runtime->isolate;
  v8::Isolate::Scope isolate_scope(isolate);
  v8::HandleScope handle_scope(isolate);
  v8::Local<v8::Context> context = runtime->context.Get(isolate);
  v8::Context::Scope context_scope(context);
  v8::Local<v8::Value> value;
  if (!CompileAndRun(runtime, context, source, source_length, &value, error)) {
    return 0;
  }
  *result = value->BooleanValue(isolate) ? 1 : 0;
  return 1;
}

extern "C" int32_t servo_v8_eval_i64(ServoV8Runtime* runtime,
                                      const uint8_t* source,
                                      size_t source_length,
                                      int64_t* result,
                                      ServoV8ErrorBuffer* error) {
  ClearError(error);
  if (!result || !CheckRuntime(runtime, error)) return 0;
  v8::Isolate* isolate = runtime->isolate;
  v8::Isolate::Scope isolate_scope(isolate);
  v8::HandleScope handle_scope(isolate);
  v8::Local<v8::Context> context = runtime->context.Get(isolate);
  v8::Context::Scope context_scope(context);
  v8::TryCatch try_catch(isolate);
  v8::Local<v8::Value> value;
  if (!CompileAndRun(runtime, context, source, source_length, &value, error)) {
    return 0;
  }
  if (!value->IntegerValue(context).To(result)) {
    WriteError(error, TryCatchMessage(isolate, try_catch));
    return 0;
  }
  return 1;
}

extern "C" int32_t servo_v8_compile(ServoV8Runtime* runtime,
                                     const uint8_t* source,
                                     size_t source_length,
                                     const uint8_t* resource_name,
                                     size_t resource_name_length,
                                     uint32_t line_number,
                                     ServoV8ErrorBuffer* error) {
  ClearError(error);
  if (!CheckRuntime(runtime, error)) return 0;
  v8::Isolate* isolate = runtime->isolate;
  v8::Isolate::Scope isolate_scope(isolate);
  v8::HandleScope handle_scope(isolate);
  v8::Local<v8::Context> context = runtime->context.Get(isolate);
  v8::Context::Scope context_scope(context);
  return CompileScript(runtime, context, source, source_length, resource_name,
                       resource_name_length, line_number, nullptr, nullptr,
                       nullptr, error)
             ? 1
             : 0;
}

extern "C" void servo_v8_low_memory_notification(ServoV8Runtime* runtime) {
  if (!runtime || !runtime->isolate ||
      runtime->owner_thread != std::this_thread::get_id() ||
      runtime->rust_callback_depth != 0) {
    return;
  }
  v8::Isolate::Scope isolate_scope(runtime->isolate);
  runtime->isolate->LowMemoryNotification();
}

extern "C" void servo_v8_terminate_execution(ServoV8Runtime* runtime) {
  // V8 explicitly permits TerminateExecution from another thread. The Rust
  // InterruptHandle holds its lifetime lock across this call, so runtime and
  // isolate cannot be deleted concurrently.
  if (runtime && runtime->isolate) runtime->isolate->TerminateExecution();
}

extern "C" void servo_v8_collect_garbage_for_testing(
    ServoV8Runtime* runtime) {
  if (!runtime || !runtime->isolate || !runtime->expose_gc ||
      runtime->owner_thread != std::this_thread::get_id() ||
      runtime->rust_callback_depth != 0) {
    return;
  }
  v8::Isolate::Scope isolate_scope(runtime->isolate);
  runtime->isolate->RequestGarbageCollectionForTesting(
      v8::Isolate::kFullGarbageCollection,
      v8::StackState::kNoHeapPointers);
}

extern "C" int32_t servo_v8_realm_wrapper_cache_size_for_testing(
    ServoV8Runtime* runtime,
    ServoV8RealmId realm_id,
    size_t* result,
    ServoV8ErrorBuffer* error) {
  ClearError(error);
  if (!result) {
    WriteError(error, "wrapper-cache size result pointer is null");
    return 0;
  }
  *result = 0;
  if (!CheckRuntime(runtime, error)) return 0;
  if (!runtime->expose_gc) {
    WriteError(error, "wrapper-cache test inspection is not enabled");
    return 0;
  }
  ServoV8RealmState* realm = FindRealm(runtime, realm_id, error);
  if (!realm) return 0;
  *result = realm->wrappers.size();
  return 1;
}

extern "C" void* servo_v8_dom_cell_native(ServoV8DomCell* cell,
                                            uint32_t expected_interface_id) {
  return cell && cell->interface_id() == expected_interface_id ? cell->native()
                                                                : nullptr;
}

extern "C" void servo_v8_trace_dom_cell(ServoV8TraceVisitor* visitor,
                                         ServoV8DomCell* cell,
                                         uint32_t expected_interface_id) {
  if (visitor && visitor->visitor && cell &&
      cell->interface_id() == expected_interface_id) {
    cppgc::Member<ServoV8DomCell> edge(cell);
    visitor->visitor->Trace(edge);
  }
}
