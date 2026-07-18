/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at https://mozilla.org/MPL/2.0/. */

#include "servo_v8.h"

#include <limits>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <unordered_map>
#include <utility>

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
  ServoV8DomCell(void* native,
                 uint32_t interface_id,
                 const ServoV8EngineBindingSmokeVTable& vtable)
      : native_(native), interface_id_(interface_id), vtable_(vtable) {}

  ~ServoV8DomCell() override {
    wrapper_.Reset();
    if (native_ && vtable_.drop) vtable_.drop(native_);
    native_ = nullptr;
  }

  void SetWrapper(v8::Isolate* isolate, v8::Local<v8::Object> wrapper) {
    wrapper_.Reset(isolate, wrapper);
  }

  void Trace(cppgc::Visitor* visitor) const override {
    v8::Object::Wrappable::Trace(visitor);
    visitor->Trace(wrapper_);
    if (native_ && vtable_.trace) {
      ServoV8TraceVisitor rust_visitor{visitor};
      vtable_.trace(native_, &rust_visitor);
    }
  }

  const v8::Object::WrapperTypeInfo* GetWrapperTypeInfo() const override {
    return &kTypeInfo;
  }

  const char* GetHumanReadableName() const override {
    return "ServoV8DomCell";
  }

  void* native() const { return native_; }
  uint32_t interface_id() const { return interface_id_; }
  const ServoV8EngineBindingSmokeVTable& vtable() const { return vtable_; }

 private:
  static constexpr v8::Object::WrapperTypeInfo kTypeInfo{1};
  void* native_;
  const uint32_t interface_id_;
  ServoV8EngineBindingSmokeVTable vtable_;
  v8::TracedReference<v8::Object> wrapper_;
};

struct ServoV8DocumentHostState {
  ServoV8Runtime* runtime = nullptr;
  void* native = nullptr;
  void* active_host_context = nullptr;
  ServoV8DocumentHostVTable vtable{};
};

struct ServoV8RealmState {
  ServoV8Runtime* runtime = nullptr;
  v8::Global<v8::Context> context;
  v8::Global<v8::Object> document;
  std::unordered_map<ServoV8ScriptId, v8::Global<v8::Script>> scripts;
  ServoV8DocumentHostState document_host;
  bool tearing_down = false;
};

struct ServoV8Runtime {
  std::unique_ptr<v8::ArrayBuffer::Allocator> allocator;
  v8::Isolate* isolate = nullptr;
  v8::Global<v8::Context> context;
  std::unordered_map<ServoV8RealmId, std::unique_ptr<ServoV8RealmState>> realms;
  ServoV8RealmId next_realm_id = 1;
  ServoV8ScriptId next_script_id = 1;
  std::thread::id owner_thread;
  uint32_t rust_callback_depth = 0;
  ServoV8EngineBindingSmokeVTable engine_binding_smoke_vtable{};
  bool engine_binding_smoke_installed = false;
  bool expose_gc = false;
};

namespace {

constexpr v8::CppHeapPointerTag kServoDomTag =
    v8::CppHeapPointerTag::kFirstObjectWrappableTag;
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

bool CallDocumentHostGetHidden(ServoV8DocumentHostState* state,
                               bool* hidden) {
  if (!state || !hidden || !state->runtime ||
      state->runtime->rust_callback_depth != 0 || !state->native ||
      !state->vtable.get_hidden) {
    return false;
  }
  RustCallbackScope callback_scope(state->runtime);
  *hidden = state->vtable.get_hidden(state->native) != 0;
  return true;
}

bool CallDocumentHostGetBgColor(ServoV8DocumentHostState* state,
                                ServoV8OwnedUtf8* value) {
  if (!state || !value || !state->runtime ||
      state->runtime->rust_callback_depth != 0 || !state->native ||
      !state->vtable.get_bg_color) {
    return false;
  }
  RustCallbackScope callback_scope(state->runtime);
  return state->vtable.get_bg_color(state->native, value) != 0;
}

bool CallDocumentHostSetBgColor(ServoV8DocumentHostState* state,
                                const uint8_t* value,
                                size_t value_length) {
  if (!state || !state->runtime ||
      state->runtime->rust_callback_depth != 0 || !state->native ||
      !state->active_host_context || !state->vtable.set_bg_color) {
    return false;
  }
  RustCallbackScope callback_scope(state->runtime);
  return state->vtable.set_bg_color(state->native,
                                    state->active_host_context, value,
                                    value_length) != 0;
}

#include "servo_v8_generated.inc"
#include "servo_v8_document_host_generated.inc"

void ResetDocumentHost(ServoV8DocumentHostState* state) {
  void* native = std::exchange(state->native, nullptr);
  const ServoV8DropCallback drop = state->vtable.drop;
  state->vtable = {};
  if (native && drop) {
    RustCallbackScope callback_scope(state->runtime);
    drop(native);
  }
}

void DetachRealm(ServoV8Runtime* runtime, ServoV8RealmState* realm) {
  realm->tearing_down = true;
  if (!realm->context.IsEmpty()) {
    v8::Local<v8::Context> context = realm->context.Get(runtime->isolate);
    context->SetAlignedPointerInEmbedderData(
        kServoRealmStateEmbedderSlot, nullptr, kServoRealmStateEmbedderTag);
  }
  realm->scripts.clear();
  realm->document.Reset();
  realm->context.Reset();
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

  {
    v8::Isolate::Scope isolate_scope(runtime->isolate);
    v8::HandleScope handle_scope(runtime->isolate);
    v8::Local<v8::Context> context = v8::Context::New(runtime->isolate);
    if (context.IsEmpty()) {
      WriteError(error, "V8 failed to allocate a context");
      runtime->isolate->Dispose();
      runtime->isolate = nullptr;
      return nullptr;
    }
    runtime->context.Reset(runtime->isolate, context);
  }
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
      for (auto& realm : runtime->realms) {
        DetachRealm(runtime, realm.second.get());
      }
      runtime->realms.clear();
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
  realm->document_host.runtime = runtime;
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
  v8::Local<v8::Function> hidden_getter;
  v8::Local<v8::Function> bg_color_getter;
  v8::Local<v8::Function> bg_color_setter;
  const v8::PropertyAttribute immutable = static_cast<v8::PropertyAttribute>(
      v8::ReadOnly | v8::DontDelete);
  if (!v8::Function::New(context, DocumentHostGetHidden,
                         v8::Local<v8::Data>(), 0,
                         v8::ConstructorBehavior::kThrow,
                         v8::SideEffectType::kHasNoSideEffect)
           .ToLocal(&hidden_getter)) {
    context->SetAlignedPointerInEmbedderData(
        kServoRealmStateEmbedderSlot, nullptr,
        kServoRealmStateEmbedderTag);
    WriteError(error, TryCatchMessage(isolate, try_catch));
    return 0;
  }
  if (!v8::Function::New(context, DocumentHostGetBgColor,
                         v8::Local<v8::Data>(), 0,
                         v8::ConstructorBehavior::kThrow,
                         v8::SideEffectType::kHasNoSideEffect)
           .ToLocal(&bg_color_getter) ||
      !v8::Function::New(context, DocumentHostSetBgColor,
                         v8::Local<v8::Data>(), 1,
                         v8::ConstructorBehavior::kThrow,
                         v8::SideEffectType::kHasSideEffect)
           .ToLocal(&bg_color_setter)) {
    context->SetAlignedPointerInEmbedderData(
        kServoRealmStateEmbedderSlot, nullptr,
        kServoRealmStateEmbedderTag);
    WriteError(error, TryCatchMessage(isolate, try_catch));
    return 0;
  }
  hidden_getter->SetName(V8String(isolate, "get hidden"));
  bg_color_getter->SetName(V8String(isolate, "get bgColor"));
  bg_color_setter->SetName(V8String(isolate, "set bgColor"));
  v8::PropertyDescriptor hidden_descriptor(hidden_getter,
                                            v8::Undefined(isolate));
  hidden_descriptor.set_enumerable(true);
  hidden_descriptor.set_configurable(true);
  v8::PropertyDescriptor bg_color_descriptor(bg_color_getter,
                                              bg_color_setter);
  bg_color_descriptor.set_enumerable(true);
  bg_color_descriptor.set_configurable(true);
  if (!document_prototype
           ->DefineProperty(context, V8String(isolate, "hidden"),
                            hidden_descriptor)
           .FromMaybe(false) ||
      !document_prototype
           ->DefineProperty(context, V8String(isolate, "bgColor"),
                            bg_color_descriptor)
           .FromMaybe(false) ||
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

  const ServoV8RealmId id = runtime->next_realm_id++;
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
