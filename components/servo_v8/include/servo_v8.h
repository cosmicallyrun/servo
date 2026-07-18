/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at https://mozilla.org/MPL/2.0/. */

#ifndef SERVO_V8_BRIDGE_H_
#define SERVO_V8_BRIDGE_H_

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define SERVO_V8_ABI_VERSION 7u

typedef struct ServoV8Runtime ServoV8Runtime;
typedef struct ServoV8DomCell ServoV8DomCell;
typedef struct ServoV8TraceVisitor ServoV8TraceVisitor;
typedef uint64_t ServoV8RealmId;
typedef uint64_t ServoV8ScriptId;

typedef struct ServoV8ErrorBuffer {
  char* data;
  size_t capacity;
  size_t length;
} ServoV8ErrorBuffer;

typedef struct ServoV8Options {
  uint8_t enable_turbolev;
  uint8_t enable_turbolev_future;
  uint8_t expose_gc;
} ServoV8Options;

enum ServoV8ScriptRunStatus {
  SERVO_V8_SCRIPT_RUN_COMPLETED = 0,
  SERVO_V8_SCRIPT_RUN_THROWN = 1,
  SERVO_V8_SCRIPT_RUN_TERMINATED = 2,
};

enum ServoV8ScriptCompileStatus {
  SERVO_V8_SCRIPT_COMPILED = 0,
  SERVO_V8_SCRIPT_COMPILE_THROWN = 1,
};

typedef struct ServoV8ScriptException {
  ServoV8ErrorBuffer message;
  ServoV8ErrorBuffer resource_name;
  ServoV8ErrorBuffer stack;
  uint32_t line_number;
  uint32_t column_number;
} ServoV8ScriptException;

typedef struct ServoV8ScriptRunOutcome {
  uint32_t status;
  ServoV8ScriptException exception;
} ServoV8ScriptRunOutcome;

typedef struct ServoV8ScriptCompileOutcome {
  uint32_t status;
  ServoV8ScriptId script_id;
  ServoV8ScriptException exception;
} ServoV8ScriptCompileOutcome;

typedef void (*ServoV8TraceCallback)(void* native,
                                     ServoV8TraceVisitor* visitor);
typedef void (*ServoV8DropCallback)(void* native);

/* Generated typed WebIDL vtables contain only POD values and native pointers. */
#include "servo_v8_generated.h"
#include "servo_v8_document_host_generated.h"

uint32_t servo_v8_abi_version(void);

ServoV8Runtime* servo_v8_runtime_new(const ServoV8Options* options,
                                     ServoV8ErrorBuffer* error);
void servo_v8_runtime_delete(ServoV8Runtime* runtime);

int32_t servo_v8_realm_create(ServoV8Runtime* runtime,
                              ServoV8RealmId* realm_id,
                              ServoV8ErrorBuffer* error);
int32_t servo_v8_realm_destroy(ServoV8Runtime* runtime,
                               ServoV8RealmId realm_id,
                               ServoV8ErrorBuffer* error);

int32_t servo_v8_realm_eval_bool(ServoV8Runtime* runtime,
                                 ServoV8RealmId realm_id,
                                 const uint8_t* source,
                                 size_t source_length,
                                 uint8_t* result,
                                 ServoV8ErrorBuffer* error);

int32_t servo_v8_realm_compile(ServoV8Runtime* runtime,
                               ServoV8RealmId realm_id,
                               const uint8_t* source,
                               size_t source_length,
                               const uint8_t* resource_name,
                               size_t resource_name_length,
                               uint32_t line_number,
                               ServoV8ErrorBuffer* error);

/* Compiles and retains one classic script in its realm without executing it. */
int32_t servo_v8_realm_script_compile(ServoV8Runtime* runtime,
                                      ServoV8RealmId realm_id,
                                      const uint8_t* source,
                                      size_t source_length,
                                      const uint8_t* resource_name,
                                      size_t resource_name_length,
                                      uint32_t line_number,
                                      ServoV8ScriptCompileOutcome* outcome,
                                      ServoV8ErrorBuffer* error);

/* Executes and consumes a retained script, including when execution throws. */
int32_t servo_v8_realm_script_run(ServoV8Runtime* runtime,
                                  ServoV8RealmId realm_id,
                                  ServoV8ScriptId script_id,
                                  void* host_context,
                                  ServoV8ScriptRunOutcome* outcome,
                                  ServoV8ErrorBuffer* error);

/* Discards one retained script without executing it. */
int32_t servo_v8_realm_script_discard(ServoV8Runtime* runtime,
                                      ServoV8RealmId realm_id,
                                      ServoV8ScriptId script_id,
                                      ServoV8ErrorBuffer* error);

/* Consumes native only on success; failure leaves ownership with the caller. */
int32_t servo_v8_realm_install_document_host(
    ServoV8Runtime* runtime,
    ServoV8RealmId realm_id,
    void* native,
    const ServoV8DocumentHostVTable* vtable,
    ServoV8ErrorBuffer* error);

int32_t servo_v8_realm_document_hidden(ServoV8Runtime* runtime,
                                       ServoV8RealmId realm_id,
                                       uint8_t* result,
                                       ServoV8ErrorBuffer* error);

int32_t servo_v8_install_engine_binding_smoke(
    ServoV8Runtime* runtime,
    const ServoV8EngineBindingSmokeVTable* vtable,
    ServoV8ErrorBuffer* error);

int32_t servo_v8_eval_bool(ServoV8Runtime* runtime,
                           const uint8_t* source,
                           size_t source_length,
                           uint8_t* result,
                           ServoV8ErrorBuffer* error);

int32_t servo_v8_eval_i64(ServoV8Runtime* runtime,
                          const uint8_t* source,
                          size_t source_length,
                          int64_t* result,
                          ServoV8ErrorBuffer* error);

int32_t servo_v8_compile(ServoV8Runtime* runtime,
                         const uint8_t* source,
                         size_t source_length,
                         const uint8_t* resource_name,
                         size_t resource_name_length,
                         uint32_t line_number,
                         ServoV8ErrorBuffer* error);

void servo_v8_low_memory_notification(ServoV8Runtime* runtime);

/* May be called from another thread while the runtime is executing script. */
void servo_v8_terminate_execution(ServoV8Runtime* runtime);

/* Requires the runtime to have been created with expose_gc for test use. */
void servo_v8_collect_garbage_for_testing(ServoV8Runtime* runtime);

/* Returns the native allocation only when the live cell's interface ID matches. */
void* servo_v8_dom_cell_native(ServoV8DomCell* cell,
                               uint32_t expected_interface_id);

/* Traces a matching cell only from its live ServoV8TraceCallback visitor. */
void servo_v8_trace_dom_cell(ServoV8TraceVisitor* visitor,
                             ServoV8DomCell* cell,
                             uint32_t expected_interface_id);

#ifdef __cplusplus
}  /* extern "C" */
#endif

#endif  /* SERVO_V8_BRIDGE_H_ */
