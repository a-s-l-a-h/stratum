#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Stratum Pipeline — Stage 06: Universal Engine & Metadata Emit
================================================================
LOCATION: 06_cpp_emit/main.py
VERSION: v9 (merges v8 + v8fix + Patch B array/list hardening +
              additional local-ref-frame coverage on field getters)

WHAT THIS STAGE EMITS (into 06_cpp_emit/output/core/) — exactly 6 fixed
files, regardless of how many Java classes are in the build:
    metadata_table.h / .cpp   Deduplicated string pool + flat per-class
                               method/field description arrays. Pure
                               DATA — compile time doesn't grow with
                               corpus size, only these tables' size does.
    bridge_core.h / .cpp      JNI thread attach/detach, Java exception
                               translation, UTF-8<->UTF-16 conversion,
                               Stage 05.5 callback storage, logging.
    stratum_engine.cpp        THE dispatcher: call_v/z/i/j/d/str/o,
                               new_instance, delete_ref,
                               field_get_i/z/j/d/str/o, field_set_i.
    bridge_main.cpp           nanobind NB_MODULE entry point, JNI_OnLoad,
                               Activity/lifecycle glue.

================================================================================
v9 CHANGE LOG — every fix below is annotated in-place with "v9 FIX N:"
================================================================================
v9 FIX 1 (JNI local reference table overflow, max=512 on ART):
    JniLocalFrame (PushLocalFrame/PopLocalFrame RAII) is now applied to
    EVERY engine function capable of creating a local JNI reference:
    call_str, call_o, new_instance (already had it in v8fix) AND
    field_get_str, field_get_o (v8fix missed these two — a tight loop
    reading a static/instance object or String field, e.g. iterating
    Bitmap rows or reading many View children's tags, would still
    overflow the 512-local-ref ART ceiling without this).

v9 FIX 2 (class-resolution deadlock):
    g_resolve_mutex is std::recursive_mutex, not std::mutex. Loading a
    Java class via find_class()->ClassLoader.loadClass() runs that
    class's <clinit> static initializer; if THAT initializer (or a
    Stage 05.5 adapter's constructor invoked while packing an 'a'/'p'
    argument) causes another Stratum class to resolve on the SAME
    thread, a non-recursive mutex deadlocks forever. Recursive mutex
    makes this reentrant and safe.

v9 FIX 3 (arrays/lists silently becoming Java null):
    pack_arguments() now has a real case for every tag defined in
    05_resolve's compute_param_tags(): '[' ']' 'q' 'f' 'd' 'b' 'c' 'h'
    'T' 'A' 'M', each building the correct JNI array type (or a real
    java.util.ArrayList for 'M') from the Python bytes/list the caller
    passed. Only the true fallback case (tag not recognised, or a bare
    int/None) uses the "raw pointer or null" behaviour.

v9 FIX 4 (stack safety on jvalue jargs[32]):
    pack_arguments() throws a catchable std::runtime_error (never
    silently corrupts the stack) if the caller passes more than 32
    arguments, AND if the argument count doesn't match the method's own
    declared param_count (catches a corrupted/mismatched call before it
    can touch memory). jargs[] is always zero-initialized.

WHY THIS FIXES "CLASS DEPENDS ON ANOTHER CLASS, WHOLE BUILD BREAKS"
    There is no per-class C++ TYPE anymore. A "class" is one row in a
    flat array; resolve_class_slots() only runs the first time Python
    actually touches that class, lazily, never at load time.

WHY THIS FIXES "HIDDEN/RESTRICTED API CRASHES THE APP"
    GetMethodID/GetFieldID returning NULL is never fatal — we
    ExceptionClear() and leave that slot null. It only ever surfaces as
    a normal catchable Python exception if that SPECIFIC slot is later
    invoked, never a process abort. Because Stage 01 only ever extracts
    from the public android.jar stub (never a real device's classes.dex),
    hidden/@hide/greylist members are structurally absent from the
    corpus to begin with.

WHY delete_ref() USES get_env() AND NOT get_env_safe()
    Python's GC can run __del__ on a thread never attached to the JVM.
    get_env_safe() would return nullptr there and leak the JNI global
    ref forever, eventually hitting Android's ~51,200 global-reference
    ceiling and aborting the whole process with "JNI ERROR: global
    reference table overflow". get_env() attaches the thread first.

WHY EVERY call_*() CHECKS `ptr` BEFORE AN INSTANCE CALL
    Call<Type>MethodA on a null jobject is undefined behaviour and
    reliably segfaults the entire process, uncatchable from Python.
    RESOLVE_AND_LOOKUP() throws a normal std::runtime_error instead.

LOGGING (compile-time strip + runtime toggle)
    STRATUM_LOG_LEVEL is a CMake compile definition (see
    07_build/templates/CMakeLists.txt.tpl and 07_build/main.py's
    --log-level flag):
      0 (default/production) -> LOGD/LOGV fully stripped, zero cost.
      1 (basic)               -> LOGD compiled in.
      2 (deep/trace)          -> LOGD + LOGV compiled in.
    A runtime bool g_log_enabled sits on top (stratum.set_log_enabled)
    so a level-1/2 build can still ship quiet by default.
"""

import argparse
import json
from pathlib import Path


def print_header(title: str) -> None:
    print("=" * 70)
    print(f"  {title}")
    print("=" * 70)


# =============================================================================
# String pool: every unique string used anywhere in the metadata table is
# stored exactly once. Method names like "get"/"close"/"setText" and
# signatures like "()V" repeat thousands of times across the Android SDK
# — deduplicating them is what keeps the .so a few MB instead of tens.
# =============================================================================
class StringPool:
    def __init__(self):
        self.pool = bytearray(b"\x00")  # offset 0 is always a valid empty string
        self.offsets = {"": 0}

    def get_offset(self, s: str) -> int:
        if not s:
            return 0
        if s in self.offsets:
            return self.offsets[s]
        offset = len(self.pool)
        self.pool.extend(s.encode("utf-8") + b"\x00")
        self.offsets[s] = offset
        return offset


def emit_metadata_table(classes: list, pool: StringPool) -> tuple:
    cpp = ['// metadata_table.cpp — Stratum Auto-generated. DO NOT EDIT.',
           '#include "metadata_table.h"', ""]

    for cls in classes:
        cid = cls["class_id"]
        methods, fields = cls.get("methods", []), cls.get("fields", [])

        if methods:
            cpp.append(f"static const MethodMeta g_methods_cls_{cid}[] = {{")
            for m in methods:
                name_val = "<init>" if m.get("is_constructor") else m.get("name", "")
                cpp.append(
                    "    {%d, %d, %d, %d, %d, %d, %d, %d}," % (
                        pool.get_offset(name_val),
                        pool.get_offset(m.get("jni_signature", "")),
                        pool.get_offset(m.get("param_tags", "")),
                        pool.get_offset(m.get("adapter_jni", "")),
                        m.get("ret_type_id", 0),
                        1 if m.get("is_static") else 0,
                        1 if m.get("is_constructor") else 0,
                        len(m.get("params", [])),
                    )
                )
            cpp.append("};")
            cpp.append("")

        if fields:
            cpp.append(f"static const FieldMeta g_fields_cls_{cid}[] = {{")
            for f in fields:
                cpp.append(
                    "    {%d, %d, %d, %d}," % (
                        pool.get_offset(f.get("name", "")),
                        pool.get_offset(f.get("jni_signature", f.get("jni_type", ""))),
                        f.get("ret_type_id", 10),
                        1 if f.get("is_static") else 0,
                    )
                )
            cpp.append("};")
            cpp.append("")

    cpp.append(f"ClassMeta g_classes[{len(classes)}] = {{")
    for cls in classes:
        cid = cls["class_id"]
        m_ptr = f"g_methods_cls_{cid}" if cls.get("method_count", 0) else "nullptr"
        f_ptr = f"g_fields_cls_{cid}" if cls.get("field_count", 0) else "nullptr"
        cpp.append(
            "    {%d, %d, %d, %s, %s, nullptr, nullptr, nullptr, false}," % (
                pool.get_offset(cls.get("jni_name", "")),
                cls.get("method_count", 0),
                cls.get("field_count", 0),
                m_ptr, f_ptr,
            )
        )
    cpp.append("};")
    cpp.append("")
    cpp.append(f"const uint32_t g_class_count = {len(classes)};")
    cpp.append("")
    cpp.append(f"const char g_str_pool[{len(pool.pool)}] = {{")
    hexb = [f"0x{b:02x}" for b in pool.pool]
    for i in range(0, len(hexb), 16):
        cpp.append("    " + ", ".join(hexb[i:i + 16]) + ",")
    cpp.append("};")

    # v9 FIX: build the header AFTER `pool` is fully populated by the loop
    # above. Building it earlier (before the loop) captured a stale
    # len(pool.pool)==1, causing `extern const char g_str_pool[1];` in the
    # header to disagree with the real `const char g_str_pool[N] = {...};`
    # definition in the .cpp — a fatal "redefinition with different type"
    # compile error, since array size is part of the C++ type.
    h = [
        "// metadata_table.h — Stratum Auto-generated. DO NOT EDIT.",
        "// Flat, deduplicated description of every class/method/field",
        "// Stratum knows about. Pure data — jmethodID/jfieldID resolution",
        "// happens lazily in stratum_engine.cpp, never at load time.",
        "#pragma once",
        "#include <jni.h>",
        "#include <stdint.h>",
        "#include <stdbool.h>",
        "",
        "struct MethodMeta {",
        "    uint32_t name_offset;",
        "    uint32_t sig_offset;",
        "    uint32_t tags_offset;",
        "    uint32_t adapter_jni_offset;",
        "    uint8_t  ret_type;",
        "    uint8_t  is_static;",
        "    uint8_t  is_constructor;",
        "    uint8_t  param_count;",
        "};",
        "",
        "struct FieldMeta {",
        "    uint32_t name_offset;",
        "    uint32_t sig_offset;",
        "    uint8_t  type_id;",
        "    uint8_t  is_static;",
        "};",
        "",
        "// One entry per Java class. class_ref/method_ids/field_ids start",
        "// null and `resolved` starts false — populated ON FIRST USE by",
        "// resolve_class_slots() in stratum_engine.cpp, never eagerly.",
        "struct ClassMeta {",
        "    uint32_t          jni_name_offset;",
        "    uint16_t          method_count;",
        "    uint16_t          field_count;",
        "    const MethodMeta* methods;",
        "    const FieldMeta*  fields;",
        "    jclass            class_ref;",
        "    jmethodID*        method_ids;",
        "    jfieldID*         field_ids;",
        "    bool              resolved;",
        "};",
        "",
        f"extern const char g_str_pool[{len(pool.pool)}];",
        f"extern ClassMeta g_classes[{len(classes)}];",
        "extern const uint32_t g_class_count;",
    ]

    return "\n".join(h), "\n".join(cpp)


# =============================================================================
# bridge_core.h — public interface shared by the rest of the engine.
# v9 note: JniLocalFrame lives here so every .cpp file in the engine can
# use it consistently (see v9 FIX 1 above).
# =============================================================================
BRIDGE_CORE_H = r"""#pragma once
// bridge_core.h — Stratum Auto-generated. DO NOT EDIT.
#include <jni.h>
#include <string>
#include <mutex>
#include <memory>
#include <unordered_map>
#include <nanobind/nanobind.h>
#include <nanobind/stl/string.h>
namespace nb = nanobind;

// ── Logging ──────────────────────────────────────────────────────────────
// Compile-time gate via -DSTRATUM_LOG_LEVEL=0|1|2 (07_build --log-level).
//   0 = production: LOGD/LOGV fully stripped, zero runtime cost.
//   1 = basic: method calls, class/field resolution.
//   2 = deep/trace: + every argument value, every return value.
// g_log_enabled is a RUNTIME toggle on top (stratum.set_log_enabled), so
// a level-1/2 build can still ship quiet by default.
#ifndef STRATUM_LOG_LEVEL
#define STRATUM_LOG_LEVEL 0
#endif

extern bool g_log_enabled;

#include <android/log.h>
#define LOGE(...) __android_log_print(ANDROID_LOG_ERROR, "Stratum", __VA_ARGS__)
#define LOGW(...) __android_log_print(ANDROID_LOG_WARN,  "Stratum", __VA_ARGS__)
#define LOGI(...) __android_log_print(ANDROID_LOG_INFO,  "Stratum", __VA_ARGS__)

#if STRATUM_LOG_LEVEL >= 1
  #define LOGD(...) do { if (g_log_enabled) __android_log_print(ANDROID_LOG_DEBUG, "Stratum", __VA_ARGS__); } while(0)
#else
  #define LOGD(...) ((void)0)
#endif

#if STRATUM_LOG_LEVEL >= 2
  #define LOGV(...) do { if (g_log_enabled) __android_log_print(ANDROID_LOG_VERBOSE, "Stratum", __VA_ARGS__); } while(0)
#else
  #define LOGV(...) ((void)0)
#endif

// ── Globals ──────────────────────────────────────────────────────────────
extern JavaVM*   g_jvm;
extern jobject   g_activity;
extern jobject   g_app_class_loader;
extern jmethodID g_class_loader_loadClass_method;
extern jclass    g_jstring_class;
extern jclass    g_proxy_class;
extern jclass    g_class_class;
extern jclass    g_object_class;
extern jclass    g_stratum_handler_class;
extern std::unordered_map<std::string, std::shared_ptr<nb::callable>> g_callbacks;
extern std::mutex g_callback_mutex;
extern std::mutex g_activity_mutex;

// ── API ──────────────────────────────────────────────────────────────────
JNIEnv*      get_env();        // attaches current thread if needed — use
                                // this EVERYWHERE, including __del__
                                // callbacks and GC threads.
JNIEnv*      get_env_safe();   // never attaches — only for paths that are
                                // OK to no-op if the thread isn't attached
                                // (delete_ref deliberately does NOT use this).
jclass       find_class(JNIEnv* env, const char* name); // FindClass, falls
                                // back to the app's own ClassLoader (needed
                                // for Stage 05.5-generated adapters).
void         store_callback(const std::string& key, nb::callable fn);
nb::callable get_callback(const std::string& key);
void         remove_callback(const std::string& key);
size_t       remove_callbacks_by_prefix(const std::string& prefix);
size_t       stratum_callback_count();

// Object inspection & downcast verification
bool         is_instance_of(int64_t ptr, uint32_t class_id);
std::string  object_to_string(int64_t ptr);
int32_t      object_hash_code(int64_t ptr);

// Checks for a pending Java exception. If present: clears it, extracts the
// message, and throws std::runtime_error (which nanobind turns into a
// normal Python exception at the call boundary). NEVER aborts the process.
void         stratum_check_java_exc(JNIEnv* env);

jstring      stratum_str_to_jstring(JNIEnv* env, const std::string& utf8);
std::string  stratum_jstring_to_str(JNIEnv* env, jobject jstr_obj);

// v9.1: List/Collection -> Python list, Map -> Python dict (return-value path)
nb::list     stratum_collection_to_list(JNIEnv* env, jobject collection);
nb::dict     stratum_map_to_dict(JNIEnv* env, jobject map);

// ── v9 FIX 1: RAII local-reference frame ────────────────────────────────
// Wrap ANY block of JNI code that can create local references (object
// method calls, object field reads, NewObjectA, array element access,
// etc.) in a JniLocalFrame. Without this, a Python loop that repeatedly
// calls into Java (rendering a frame, iterating pixels, reading many
// fields) will accumulate local refs until ART aborts the whole process
// with "JNI ERROR (app bug): local reference table overflow (max=512)".
// PushLocalFrame/PopLocalFrame bounds that accumulation to the lifetime
// of a single dispatcher call, regardless of how many JNI calls happen
// inside it.
struct JniLocalFrame {
    JNIEnv* env_;
    bool    active_;
    explicit JniLocalFrame(JNIEnv* env, jint capacity = 32) : env_(env), active_(false) {
        if (env_ && env_->PushLocalFrame(capacity) == 0) {
            active_ = true;
        }
        // If PushLocalFrame fails (extremely low memory), we simply don't
        // pop later — we do NOT throw here, because failing to get extra
        // local-ref headroom is not itself fatal; the JNI call that
        // follows may still succeed within the existing frame.
    }
    ~JniLocalFrame() {
        if (active_) {
            env_->PopLocalFrame(nullptr);
        }
    }
    JniLocalFrame(const JniLocalFrame&) = delete;
    JniLocalFrame& operator=(const JniLocalFrame&) = delete;
};
"""

BRIDGE_CORE_CPP = r"""// bridge_core.cpp — Stratum Auto-generated. DO NOT EDIT.
#include "bridge_core.h"
#include <pthread.h>

bool g_log_enabled = true;  // runtime toggle; only has effect if STRATUM_LOG_LEVEL >= 1

JavaVM*   g_jvm = nullptr;
jobject   g_activity = nullptr;
jobject   g_app_class_loader = nullptr;
jmethodID g_class_loader_loadClass_method = nullptr;
jclass    g_jstring_class = nullptr;
jclass    g_proxy_class = nullptr;
jclass    g_class_class = nullptr;
jclass    g_object_class = nullptr;
jclass    g_stratum_handler_class = nullptr;

std::unordered_map<std::string, std::shared_ptr<nb::callable>> g_callbacks;
std::mutex g_callback_mutex;
std::mutex g_activity_mutex;

static pthread_key_t  g_jni_detach_key;
static pthread_once_t g_jni_key_once = PTHREAD_ONCE_INIT;
static void detach_thread(void*) { if (g_jvm) g_jvm->DetachCurrentThread(); }
static void make_jni_key() { pthread_key_create(&g_jni_detach_key, detach_thread); }

JNIEnv* get_env() {
    if (!g_jvm) return nullptr;
    JNIEnv* env = nullptr;
    jint rc = g_jvm->GetEnv(reinterpret_cast<void**>(&env), JNI_VERSION_1_6);
    if (rc == JNI_EDETACHED) {
        // Attach on demand. This is what makes it safe to call get_env()
        // from Python's __del__/GC path, which may run on a thread the
        // JVM has never seen before. Without this, DeleteGlobalRef would
        // silently no-op and leak references until the app hits Android's
        // ~51,200 global-reference ceiling and is killed by ART.
        pthread_once(&g_jni_key_once, make_jni_key);
        g_jvm->AttachCurrentThread(&env, nullptr);
        pthread_setspecific(g_jni_detach_key, reinterpret_cast<void*>(1));
        LOGD("get_env: attached background thread, env=%p", env);
    }
    return env;
}

JNIEnv* get_env_safe() {
    if (!g_jvm) return nullptr;
    JNIEnv* env = nullptr;
    g_jvm->GetEnv(reinterpret_cast<void**>(&env), JNI_VERSION_1_6);
    return env;
}

jclass find_class(JNIEnv* env, const char* name) {
    if (!env || !name) return nullptr;
    jclass cls = env->FindClass(name);
    if (cls) return cls;
    env->ExceptionClear();
    if (g_app_class_loader && g_class_loader_loadClass_method) {
        std::string dotted = name;
        for (char& c : dotted) if (c == '/') c = '.';
        jstring jn = env->NewStringUTF(dotted.c_str());
        cls = (jclass)env->CallObjectMethod(g_app_class_loader, g_class_loader_loadClass_method, jn);
        env->DeleteLocalRef(jn);
        if (env->ExceptionCheck()) { env->ExceptionClear(); return nullptr; }
        return cls;
    }
    return nullptr;
}

static constexpr size_t STRATUM_MAX_CALLBACKS = 10000;

void store_callback(const std::string& key, nb::callable fn) {
    std::lock_guard<std::mutex> lock(g_callback_mutex);
    g_callbacks[key] = std::make_shared<nb::callable>(std::move(fn));
    size_t sz = g_callbacks.size();
    if (sz > STRATUM_MAX_CALLBACKS && sz % 1000 == 0) {
        // g_callbacks only grows — every 'a'/'p' argument allocates a new
        // key that is never auto-removed (the Java-side adapter's
        // lifetime isn't tracked). This warns in logcat instead of
        // silently leaking. Call stratum.remove_callback(key) if you
        // know a listener is no longer needed.
        LOGW("Stratum: g_callbacks has grown to %zu entries — possible leak.", sz);
    }
}

size_t stratum_callback_count() {
    std::lock_guard<std::mutex> lock(g_callback_mutex);
    return g_callbacks.size();
}
nb::callable get_callback(const std::string& key) {
    std::lock_guard<std::mutex> lock(g_callback_mutex);
    auto it = g_callbacks.find(key);
    return (it != g_callbacks.end() && it->second) ? *it->second : nb::callable();
}
void remove_callback(const std::string& key) {
    std::lock_guard<std::mutex> lock(g_callback_mutex);
    g_callbacks.erase(key);
}

size_t remove_callbacks_by_prefix(const std::string& prefix) {
    std::lock_guard<std::mutex> lock(g_callback_mutex);
    size_t removed = 0;
    for (auto it = g_callbacks.begin(); it != g_callbacks.end(); ) {
        if (it->first.rfind(prefix, 0) == 0) {
            it = g_callbacks.erase(it);
            ++removed;
        } else {
            ++it;
        }
    }
    LOGD("remove_callbacks_by_prefix '%s' removed %zu", prefix.c_str(), removed);
    return removed;
}

void stratum_check_java_exc(JNIEnv* env) {
    if (!env || !env->ExceptionCheck()) return;
    jthrowable ex = env->ExceptionOccurred();
    env->ExceptionClear();
    std::string msg = "Java exception";
    if (ex) {
        jclass ecls = env->GetObjectClass(ex);
        jmethodID emid = env->GetMethodID(ecls, "getMessage", "()Ljava/lang/String;");
        if (emid) {
            jstring jm = (jstring)env->CallObjectMethod(ex, emid);
            if (jm) {
                const char* c = env->GetStringUTFChars(jm, nullptr);
                if (c) { msg = c; env->ReleaseStringUTFChars(jm, c); }
                env->DeleteLocalRef(jm);
            }
        }
        env->DeleteLocalRef(ecls);
        env->DeleteLocalRef(ex);
    }
    LOGE("Java exception: %s", msg.c_str());
    throw std::runtime_error(msg);
}

// UTF-8 <-> UTF-16 conversion done by hand (avoids NewStringUTF/
// GetStringUTFChars's modified-UTF-8 surprises with emoji / non-BMP text).
jstring stratum_str_to_jstring(JNIEnv* env, const std::string& utf8) {
    if (!env) return nullptr;
    const uint8_t* ub = reinterpret_cast<const uint8_t*>(utf8.data());
    size_t ul = utf8.size();
    std::u16string u16; u16.reserve(ul);
    for (size_t i = 0; i < ul; ) {
        uint32_t cp; uint8_t b0 = ub[i];
        if (b0 < 0x80u) { cp = b0; i += 1; }
        else if ((b0 & 0xE0u) == 0xC0u && i + 1 < ul) { cp = ((uint32_t)(b0 & 0x1Fu) << 6) | (ub[i+1] & 0x3Fu); i += 2; }
        else if ((b0 & 0xF0u) == 0xE0u && i + 2 < ul) { cp = ((uint32_t)(b0 & 0x0Fu) << 12) | ((uint32_t)(ub[i+1] & 0x3Fu) << 6) | (ub[i+2] & 0x3Fu); i += 3; }
        else if ((b0 & 0xF8u) == 0xF0u && i + 3 < ul) { cp = ((uint32_t)(b0 & 0x07u) << 18) | ((uint32_t)(ub[i+1] & 0x3Fu) << 12) | ((uint32_t)(ub[i+2] & 0x3Fu) << 6) | (ub[i+3] & 0x3Fu); i += 4; }
        else { ++i; continue; }
        if (cp < 0x10000u) u16 += (char16_t)cp;
        else { cp -= 0x10000u; u16 += (char16_t)(0xD800u | (cp >> 10)); u16 += (char16_t)(0xDC00u | (cp & 0x3FFu)); }
    }
    return env->NewString(reinterpret_cast<const jchar*>(u16.data()), (jsize)u16.size());
}

std::string stratum_jstring_to_str(JNIEnv* env, jobject jstr_obj) {
    if (!env || !jstr_obj) return "";
    jstring jstr = (jstring)jstr_obj;
    jsize slen = env->GetStringLength(jstr);
    const jchar* sjc = env->GetStringChars(jstr, nullptr);
    std::string res;
    if (sjc && slen > 0) {
        res.reserve((size_t)slen * 3);
        for (jsize i = 0; i < slen; ) {
            uint32_t cp; uint16_t c1 = (uint16_t)sjc[i++];
            if (c1 >= 0xD800u && c1 <= 0xDBFFu && i < slen) {
                uint16_t c2 = (uint16_t)sjc[i];
                if (c2 >= 0xDC00u && c2 <= 0xDFFFu) { cp = 0x10000u + (((uint32_t)(c1-0xD800u))<<10) + (uint32_t)(c2-0xDC00u); ++i; }
                else cp = c1;
            } else cp = c1;
            if (cp < 0x80u) res += (char)cp;
            else if (cp < 0x800u) { res += (char)(0xC0u|(cp>>6)); res += (char)(0x80u|(cp&0x3Fu)); }
            else if (cp < 0x10000u) { res += (char)(0xE0u|(cp>>12)); res += (char)(0x80u|((cp>>6)&0x3Fu)); res += (char)(0x80u|(cp&0x3Fu)); }
            else { res += (char)(0xF0u|(cp>>18)); res += (char)(0x80u|((cp>>12)&0x3Fu)); res += (char)(0x80u|((cp>>6)&0x3Fu)); res += (char)(0x80u|(cp&0x3Fu)); }
        }
    }
    if (sjc) env->ReleaseStringChars(jstr, sjc);
    env->DeleteLocalRef((jobject)jstr);
    return res;
}

nb::list stratum_collection_to_list(JNIEnv* env, jobject collection) {
    nb::list result;
    if (!env || !collection) return result;
    JniLocalFrame frame(env, 16);
    jclass cls = env->GetObjectClass(collection);
    jmethodID msize = env->GetMethodID(cls, "size", "()I");
    jmethodID mget  = env->GetMethodID(cls, "get",  "(I)Ljava/lang/Object;");
    if (msize && mget) {
        jint len = env->CallIntMethod(collection, msize);
        for (jint i = 0; i < len; ++i) {
            jobject item = env->CallObjectMethod(collection, mget, i);
            if (!item) { result.append(nb::none()); continue; }
            if (g_jstring_class && env->IsInstanceOf(item, g_jstring_class)) {
                result.append(nb::str(stratum_jstring_to_str(env, item).c_str()));
            } else {
                jobject gref = env->NewGlobalRef(item);
                env->DeleteLocalRef(item);
                result.append(nb::cast((int64_t)(uintptr_t)gref));
            }
        }
    } else {
        env->ExceptionClear();
        jmethodID miter = env->GetMethodID(cls, "iterator", "()Ljava/util/Iterator;");
        if (miter) {
            jobject iter = env->CallObjectMethod(collection, miter);
            if (iter) {
                jclass ic = env->GetObjectClass(iter);
                jmethodID mhn = env->GetMethodID(ic, "hasNext", "()Z");
                jmethodID mnx = env->GetMethodID(ic, "next", "()Ljava/lang/Object;");
                env->DeleteLocalRef(ic);
                while (mhn && mnx && env->CallBooleanMethod(iter, mhn)) {
                    jobject item = env->CallObjectMethod(iter, mnx);
                    if (!item) { result.append(nb::none()); continue; }
                    if (g_jstring_class && env->IsInstanceOf(item, g_jstring_class)) {
                        result.append(nb::str(stratum_jstring_to_str(env, item).c_str()));
                    } else {
                        jobject gref = env->NewGlobalRef(item);
                        env->DeleteLocalRef(item);
                        result.append(nb::cast((int64_t)(uintptr_t)gref));
                    }
                }
                env->DeleteLocalRef(iter);
            }
        } else {
            env->ExceptionClear();
        }
    }
    env->DeleteLocalRef(cls);
    return result;
}

nb::dict stratum_map_to_dict(JNIEnv* env, jobject map) {
    nb::dict result;
    if (!env || !map) return result;
    JniLocalFrame frame(env, 16);
    jclass mcls = env->GetObjectClass(map);
    jmethodID mes = env->GetMethodID(mcls, "entrySet", "()Ljava/util/Set;");
    env->DeleteLocalRef(mcls);
    if (!mes) { env->ExceptionClear(); return result; }
    jobject es = env->CallObjectMethod(map, mes);
    if (!es) return result;
    jclass escls = env->GetObjectClass(es);
    jmethodID esit = env->GetMethodID(escls, "iterator", "()Ljava/util/Iterator;");
    env->DeleteLocalRef(escls);
    jobject iter = env->CallObjectMethod(es, esit);
    env->DeleteLocalRef(es);
    if (!iter) return result;
    jclass ic = env->GetObjectClass(iter);
    jmethodID mhn = env->GetMethodID(ic, "hasNext", "()Z");
    jmethodID mnx = env->GetMethodID(ic, "next", "()Ljava/lang/Object;");
    env->DeleteLocalRef(ic);
    while (mhn && mnx && env->CallBooleanMethod(iter, mhn)) {
        jobject entry = env->CallObjectMethod(iter, mnx);
        jclass ec = env->GetObjectClass(entry);
        jmethodID mkey = env->GetMethodID(ec, "getKey", "()Ljava/lang/Object;");
        jmethodID mval = env->GetMethodID(ec, "getValue", "()Ljava/lang/Object;");
        env->DeleteLocalRef(ec);
        jobject ek = env->CallObjectMethod(entry, mkey);
        jobject ev = env->CallObjectMethod(entry, mval);
        env->DeleteLocalRef(entry);
        nb::object pyk, pyv;
        if (ek && g_jstring_class && env->IsInstanceOf(ek, g_jstring_class)) {
            pyk = nb::str(stratum_jstring_to_str(env, ek).c_str());
        } else if (ek) {
            jobject gref = env->NewGlobalRef(ek);
            env->DeleteLocalRef(ek);
            pyk = nb::cast((int64_t)(uintptr_t)gref);
        } else pyk = nb::none();
        if (ev && g_jstring_class && env->IsInstanceOf(ev, g_jstring_class)) {
            pyv = nb::str(stratum_jstring_to_str(env, ev).c_str());
        } else if (ev) {
            jobject gref = env->NewGlobalRef(ev);
            env->DeleteLocalRef(ev);
            pyv = nb::cast((int64_t)(uintptr_t)gref);
        } else pyv = nb::none();
        result[pyk] = pyv;
    }
    env->DeleteLocalRef(iter);
    return result;
}

// Called by a Stage 05.5 Java adapter / dynamic proxy when Android
// invokes a callback method. Looks up the stored Python callable by key
// and dispatches into it.
extern "C" JNIEXPORT jobject JNICALL
Java_com_stratum_runtime_StratumInvocationHandler_nativeDispatch(
        JNIEnv* env, jclass, jstring jkey, jstring jmethod, jobjectArray args) {
    const char* kc = env->GetStringUTFChars(jkey, nullptr);
    std::string base_key(kc);
    env->ReleaseStringUTFChars(jkey, kc);

    std::string routed_key = base_key;
    if (jmethod) {
        const char* mc = env->GetStringUTFChars(jmethod, nullptr);
        routed_key = base_key + "#" + std::string(mc);
        env->ReleaseStringUTFChars(jmethod, mc);
    }

    nb::callable fn;
    {
        std::lock_guard<std::mutex> lock(g_callback_mutex);
        auto it = g_callbacks.find(routed_key);
        if (it == g_callbacks.end()) it = g_callbacks.find(base_key);
        if (it != g_callbacks.end() && it->second) fn = *it->second;
    }
    if (!fn.is_valid()) { LOGW("nativeDispatch: no callback bound for %s", routed_key.c_str()); return nullptr; }

    nb::gil_scoped_acquire acquire;
    JniLocalFrame frame(env, 32);

    // Cache primitive wrapper classes once. Without unboxing, a Java
    // caller passing e.g. onProgressChanged(SeekBar, int, boolean) hands
    // Python opaque object pointers instead of a real int/bool.
    static jclass s_int_cls = nullptr, s_bool_cls = nullptr,
                  s_long_cls = nullptr, s_dbl_cls = nullptr, s_flt_cls = nullptr;
    if (!s_int_cls)  { jclass c = env->FindClass("java/lang/Integer");  s_int_cls  = (jclass)env->NewGlobalRef(c); env->DeleteLocalRef(c); }
    if (!s_bool_cls) { jclass c = env->FindClass("java/lang/Boolean");  s_bool_cls = (jclass)env->NewGlobalRef(c); env->DeleteLocalRef(c); }
    if (!s_long_cls) { jclass c = env->FindClass("java/lang/Long");     s_long_cls = (jclass)env->NewGlobalRef(c); env->DeleteLocalRef(c); }
    if (!s_dbl_cls)  { jclass c = env->FindClass("java/lang/Double");   s_dbl_cls  = (jclass)env->NewGlobalRef(c); env->DeleteLocalRef(c); }
    if (!s_flt_cls)  { jclass c = env->FindClass("java/lang/Float");    s_flt_cls  = (jclass)env->NewGlobalRef(c); env->DeleteLocalRef(c); }

    nb::object py_result;
    try {
        jsize len = args ? env->GetArrayLength(args) : 0;
        if (len == 0) {
            py_result = fn();
        } else {
            nb::list py_args;
            for (jsize i = 0; i < len; ++i) {
                jobject elem = env->GetObjectArrayElement(args, i);
                if (!elem) { py_args.append(nb::none()); continue; }

                if (g_jstring_class && env->IsInstanceOf(elem, g_jstring_class)) {
                    py_args.append(nb::str(stratum_jstring_to_str(env, elem).c_str()));
                } else if (env->IsInstanceOf(elem, s_int_cls)) {
                    jmethodID mid = env->GetMethodID(s_int_cls, "intValue", "()I");
                    py_args.append(nb::int_((int64_t)env->CallIntMethod(elem, mid)));
                    env->DeleteLocalRef(elem);
                } else if (env->IsInstanceOf(elem, s_bool_cls)) {
                    jmethodID mid = env->GetMethodID(s_bool_cls, "booleanValue", "()Z");
                    py_args.append(nb::bool_(env->CallBooleanMethod(elem, mid) != JNI_FALSE));
                    env->DeleteLocalRef(elem);
                } else if (env->IsInstanceOf(elem, s_long_cls)) {
                    jmethodID mid = env->GetMethodID(s_long_cls, "longValue", "()J");
                    py_args.append(nb::int_((int64_t)env->CallLongMethod(elem, mid)));
                    env->DeleteLocalRef(elem);
                } else if (env->IsInstanceOf(elem, s_dbl_cls)) {
                    jmethodID mid = env->GetMethodID(s_dbl_cls, "doubleValue", "()D");
                    py_args.append(nb::float_((double)env->CallDoubleMethod(elem, mid)));
                    env->DeleteLocalRef(elem);
                } else if (env->IsInstanceOf(elem, s_flt_cls)) {
                    jmethodID mid = env->GetMethodID(s_flt_cls, "floatValue", "()F");
                    py_args.append(nb::float_((double)env->CallFloatMethod(elem, mid)));
                    env->DeleteLocalRef(elem);
                } else {
                    jobject gref = env->NewGlobalRef(elem);
                    env->DeleteLocalRef(elem);
                    py_args.append(nb::cast((int64_t)(uintptr_t)gref));
                }
            }
            py_result = fn(*nb::tuple(py_args));
        }
    } catch (nb::python_error& e) {
        // Propagate to Java instead of silently swallowing it — a
        // crashing Python callback previously looked to Java like it
        // succeeded and returned null.
        std::string msg = e.what();
        e.restore();
        PyErr_Clear();
        LOGE("nativeDispatch: Python callback raised: %s", msg.c_str());
        if (!env->ExceptionCheck()) {
            jclass rex = env->FindClass("java/lang/RuntimeException");
            if (rex) {
                env->ThrowNew(rex, (std::string("[Stratum] Python callback error: ") + msg).c_str());
                env->DeleteLocalRef(rex);
            } else {
                env->ExceptionClear();
            }
        }
        return nullptr;
    } catch (const std::exception& e) {
        LOGE("nativeDispatch: native error: %s", e.what());
        return nullptr;
    }

    // Convert the Python return value into a boxed Java object so
    // callbacks with a non-void return type (onTouch, onKey,
    // onLongClick, Comparator.compare, ...) get a real value.
    // NOTE: this only takes effect once StratumInvocationHandler.java's
    // invoke() actually forwards it — see the companion Java patch.
    if (!py_result || py_result.is_none()) return nullptr;
    if (nb::isinstance<nb::bool_>(py_result)) {
        jmethodID valueOf = env->GetStaticMethodID(s_bool_cls, "valueOf", "(Z)Ljava/lang/Boolean;");
        return env->CallStaticObjectMethod(s_bool_cls, valueOf, nb::cast<bool>(py_result) ? JNI_TRUE : JNI_FALSE);
    }
    if (nb::isinstance<nb::int_>(py_result)) {
        jmethodID valueOf = env->GetStaticMethodID(s_int_cls, "valueOf", "(I)Ljava/lang/Integer;");
        return env->CallStaticObjectMethod(s_int_cls, valueOf, (jint)nb::cast<int64_t>(py_result));
    }
    if (nb::isinstance<nb::float_>(py_result)) {
        jmethodID valueOf = env->GetStaticMethodID(s_dbl_cls, "valueOf", "(D)Ljava/lang/Double;");
        return env->CallStaticObjectMethod(s_dbl_cls, valueOf, (jdouble)nb::cast<double>(py_result));
    }
    if (nb::isinstance<nb::str>(py_result)) {
        return stratum_str_to_jstring(env, nb::cast<std::string>(py_result));
    }
    if (nb::hasattr(py_result, "_ptr")) {
        return (jobject)(uintptr_t)nb::cast<int64_t>(py_result.attr("_ptr"));
    }
    return nullptr;
}
"""

# =============================================================================
# stratum_engine.cpp — the universal dispatcher. This is where v9 FIX 2,
# v9 FIX 3, and v9 FIX 4 live.
# =============================================================================
STRATUM_ENGINE_CPP = r"""// stratum_engine.cpp — Stratum Auto-generated. DO NOT EDIT.
// Every Java method call AND every static/instance field access in the
// whole app funnels through the functions below, indexed purely by
// (class_id, slot).
#include "bridge_core.h"
#include "metadata_table.h"
#include <vector>
#include <stdexcept>
#include <atomic>

// ── v9 FIX 2: recursive_mutex, not mutex ────────────────────────────────
// resolve_class_slots() below calls find_class(), which — if the class
// isn't already loaded — invokes the JVM ClassLoader, which runs that
// Java class's <clinit> static initializer. If that initializer (or a
// Stage 05.5 Java adapter's constructor, invoked from inside
// pack_arguments() while packing an 'a'/'p' tagged parameter) causes
// ANOTHER Stratum class to need resolving on the SAME thread, a plain
// std::mutex deadlocks the app forever (self-lock). recursive_mutex
// allows the same thread to re-enter safely; a different thread still
// blocks normally until the first resolution completes.
static std::recursive_mutex g_resolve_mutex;
static inline const char* get_str(uint32_t off) { return &g_str_pool[off]; }

// Resolves ALL method/field IDs for one class, exactly once, the first
// time any of its methods/fields is touched. This is what makes
// class-to-class dependencies a non-issue: nothing is pre-linked,
// nothing is eager, nothing can fail to compile because of a missing
// dependency (there IS no per-class C++ TYPE anymore).
static void resolve_class_slots(JNIEnv* env, uint32_t class_id) {
    std::lock_guard<std::recursive_mutex> lock(g_resolve_mutex);
    ClassMeta& cls = g_classes[class_id];
    if (cls.resolved) return;  // re-entrant call after another thread/frame already resolved it

    const char* jni_name = get_str(cls.jni_name_offset);
    jclass local = find_class(env, jni_name);
    if (!local) {
        env->ExceptionClear();
        // A missing CLASS (not method/field) is the one case that must
        // fail loudly — nothing useful can be done with it. It still
        // only THROWS; it never aborts the process.
        throw std::runtime_error(std::string("Stratum: class not found on this device: ") + jni_name);
    }
    cls.class_ref = (jclass)env->NewGlobalRef(local);
    env->DeleteLocalRef(local);
    LOGD("resolved class %s (class_id=%u)", jni_name, class_id);

    if (cls.method_count > 0) {
        cls.method_ids = new jmethodID[cls.method_count];
        for (uint16_t i = 0; i < cls.method_count; ++i) {
            const MethodMeta& mm = cls.methods[i];
            const char* mname = mm.is_constructor ? "<init>" : get_str(mm.name_offset);
            const char* msig  = get_str(mm.sig_offset);
            cls.method_ids[i] = mm.is_static
                ? env->GetStaticMethodID(cls.class_ref, mname, msig)
                : env->GetMethodID(cls.class_ref, mname, msig);
            if (!cls.method_ids[i]) {
                // NOT a crash. This is how a method missing on THIS
                // device's Android version gets handled: silently
                // recorded as unavailable here, only surfaces as a
                // catchable Python exception if that slot is invoked.
                env->ExceptionClear();
                LOGW("method unavailable on this device: %s#%s%s", jni_name, mname, msig);
            }
        }
    }
    if (cls.field_count > 0) {
        cls.field_ids = new jfieldID[cls.field_count];
        for (uint16_t i = 0; i < cls.field_count; ++i) {
            const FieldMeta& fm = cls.fields[i];
            const char* fname = get_str(fm.name_offset);
            const char* fsig  = get_str(fm.sig_offset);
            cls.field_ids[i] = fm.is_static
                ? env->GetStaticFieldID(cls.class_ref, fname, fsig)
                : env->GetFieldID(cls.class_ref, fname, fsig);
            if (!cls.field_ids[i]) { env->ExceptionClear(); LOGW("field unavailable: %s#%s", jni_name, fname); }
        }
    }
    cls.resolved = true;
}

// ── v9 FIX 3 + FIX 4: full tag coverage, bounded, bounds-checked ────────
// Converts a Python nb::args tuple into a jvalue[] array according to
// the method's param_tags string (see 05_resolve/main.py
// compute_param_tags() — every tag emitted there has a case here).
static inline void pack_arguments(JNIEnv* env, const char* tags, const MethodMeta& mm,
                                   nb::args& args, jvalue* jargs, std::vector<jobject>& locals,
                                   int64_t caller_ptr = 0) {
    size_t n = nb::len(args);

    // Automatic Java varargs packing for Object... / String... (tag 'A' or 'T')
    nb::list auto_packed_args;
    bool is_varargs = false;
    if (mm.param_count > 0) {
        char last_tag = tags[mm.param_count - 1];
        if (last_tag == 'A' || last_tag == 'T') {
            if (n != (size_t)mm.param_count ||
                (n > 0 && !nb::isinstance<nb::list>(args[n - 1]) && !args[n - 1].is_none())) {
                is_varargs = true;
            }
        }
    }

    if (is_varargs) {
        size_t fixed_count = (size_t)mm.param_count - 1;
        if (n < fixed_count) {
            throw std::runtime_error(
                std::string("Stratum: argument count mismatch: method expects at least ") +
                std::to_string((int)fixed_count) + " but got " + std::to_string((int)n));
        }
        nb::list vararg_list;
        for (size_t vi = fixed_count; vi < n; ++vi) {
            vararg_list.append(args[vi]);
        }
        for (size_t fi = 0; fi < fixed_count; ++fi) {
            auto_packed_args.append(args[fi]);
        }
        auto_packed_args.append(vararg_list);
        n = (size_t)mm.param_count;
    }

    if (n > 32) {
        throw std::runtime_error("Stratum: a single call cannot take more than 32 arguments");
    }
    if (n != (size_t)mm.param_count) {
        throw std::runtime_error(
            std::string("Stratum: argument count mismatch: method expects ") +
            std::to_string((int)mm.param_count) + " but got " + std::to_string((int)n));
    }

    for (size_t i = 0; i < n; ++i) {
        char tag = tags[i];
        nb::handle item = is_varargs ? nb::handle(auto_packed_args[i]) : args[i];
        switch (tag) {
            // ── Plain primitives ─────────────────────────────────────
            case 'Z': jargs[i].z = nb::cast<bool>(item) ? JNI_TRUE : JNI_FALSE; break;
            case 'B': jargs[i].b = (jbyte)nb::cast<int>(item); break;
            case 'C': {
                std::string s = nb::cast<std::string>(item);
                if (s.empty()) {
                    jargs[i].c = 0;
                } else {
                    const uint8_t* u = reinterpret_cast<const uint8_t*>(s.data());
                    uint32_t cp = u[0];
                    if ((u[0] & 0xE0) == 0xC0 && s.size() >= 2) {
                        cp = ((u[0] & 0x1F) << 6) | (u[1] & 0x3F);
                    } else if ((u[0] & 0xF0) == 0xE0 && s.size() >= 3) {
                        cp = ((u[0] & 0x0F) << 12) | ((u[1] & 0x3F) << 6) | (u[2] & 0x3F);
                    }
                    jargs[i].c = (jchar)(cp <= 0xFFFF ? cp : 0xFFFD);
                }
                break;
            }
            case 'S': jargs[i].s = (jshort)nb::cast<int>(item); break;
            case 'I': jargs[i].i = (jint)nb::cast<int32_t>(item); break;
            case 'J': jargs[i].j = (jlong)nb::cast<int64_t>(item); break;
            case 'F': jargs[i].f = (jfloat)nb::cast<float>(item); break;
            case 'D': jargs[i].d = (jdouble)nb::cast<double>(item); break;

            // ── String ────────────────────────────────────────────────
            case 's': {
                if (item.is_none()) jargs[i].l = nullptr;
                else { jstring js = stratum_str_to_jstring(env, nb::cast<std::string>(item));
                       jargs[i].l = js; locals.push_back(js); }
                break;
            }

            // ── v9 FIX 3: byte[] from Python bytes/bytearray ────────────
            case '[': {
                if (item.is_none()) jargs[i].l = nullptr;
                else if (nb::isinstance<nb::bytes>(item)) {
                    nb::bytes b = nb::cast<nb::bytes>(item);
                    jbyteArray ja = env->NewByteArray((jsize)b.size());
                    if (b.size() > 0) env->SetByteArrayRegion(ja, 0, (jsize)b.size(), reinterpret_cast<const jbyte*>(b.c_str()));
                    jargs[i].l = ja; locals.push_back(ja);
                } else if (nb::hasattr(item, "_ptr")) {
                    jargs[i].l = (jobject)(uintptr_t)nb::cast<int64_t>(item.attr("_ptr"));
                } else jargs[i].l = nullptr;
                break;
            }

            // ── v9 FIX 3: int[] from Python list[int] ───────────────────
            case ']': {
                if (item.is_none()) jargs[i].l = nullptr;
                else if (nb::isinstance<nb::list>(item)) {
                    nb::list l = nb::cast<nb::list>(item);
                    jsize sz = (jsize)nb::len(l);
                    jintArray ja = env->NewIntArray(sz);
                    std::vector<jint> buf(sz);
                    for (jsize k = 0; k < sz; ++k) buf[k] = (jint)nb::cast<int64_t>(l[k]);
                    if (sz > 0) env->SetIntArrayRegion(ja, 0, sz, buf.data());
                    jargs[i].l = ja; locals.push_back(ja);
                } else if (nb::hasattr(item, "_ptr")) {
                    jargs[i].l = (jobject)(uintptr_t)nb::cast<int64_t>(item.attr("_ptr"));
                } else jargs[i].l = nullptr;
                break;
            }

            // ── v9 FIX 3: long[] ──────────────────────────────────────
            case 'q': {
                if (item.is_none()) jargs[i].l = nullptr;
                else if (nb::isinstance<nb::list>(item)) {
                    nb::list l = nb::cast<nb::list>(item);
                    jsize sz = (jsize)nb::len(l);
                    jlongArray ja = env->NewLongArray(sz);
                    std::vector<jlong> buf(sz);
                    for (jsize k = 0; k < sz; ++k) buf[k] = (jlong)nb::cast<int64_t>(l[k]);
                    if (sz > 0) env->SetLongArrayRegion(ja, 0, sz, buf.data());
                    jargs[i].l = ja; locals.push_back(ja);
                } else if (nb::hasattr(item, "_ptr")) {
                    jargs[i].l = (jobject)(uintptr_t)nb::cast<int64_t>(item.attr("_ptr"));
                } else jargs[i].l = nullptr;
                break;
            }

            // ── v9 FIX 3: float[] ─────────────────────────────────────
            case 'f': {
                if (item.is_none()) jargs[i].l = nullptr;
                else if (nb::isinstance<nb::list>(item)) {
                    nb::list l = nb::cast<nb::list>(item);
                    jsize sz = (jsize)nb::len(l);
                    jfloatArray ja = env->NewFloatArray(sz);
                    std::vector<jfloat> buf(sz);
                    for (jsize k = 0; k < sz; ++k) buf[k] = (jfloat)nb::cast<double>(l[k]);
                    if (sz > 0) env->SetFloatArrayRegion(ja, 0, sz, buf.data());
                    jargs[i].l = ja; locals.push_back(ja);
                } else if (nb::hasattr(item, "_ptr")) {
                    jargs[i].l = (jobject)(uintptr_t)nb::cast<int64_t>(item.attr("_ptr"));
                } else jargs[i].l = nullptr;
                break;
            }

            // ── v9 FIX 3: double[] ────────────────────────────────────
            case 'd': {
                if (item.is_none()) jargs[i].l = nullptr;
                else if (nb::isinstance<nb::list>(item)) {
                    nb::list l = nb::cast<nb::list>(item);
                    jsize sz = (jsize)nb::len(l);
                    jdoubleArray ja = env->NewDoubleArray(sz);
                    std::vector<jdouble> buf(sz);
                    for (jsize k = 0; k < sz; ++k) buf[k] = (jdouble)nb::cast<double>(l[k]);
                    if (sz > 0) env->SetDoubleArrayRegion(ja, 0, sz, buf.data());
                    jargs[i].l = ja; locals.push_back(ja);
                } else if (nb::hasattr(item, "_ptr")) {
                    jargs[i].l = (jobject)(uintptr_t)nb::cast<int64_t>(item.attr("_ptr"));
                } else jargs[i].l = nullptr;
                break;
            }

            // ── v9 FIX 3: boolean[] ───────────────────────────────────
            case 'b': {
                if (item.is_none()) jargs[i].l = nullptr;
                else if (nb::isinstance<nb::list>(item)) {
                    nb::list l = nb::cast<nb::list>(item);
                    jsize sz = (jsize)nb::len(l);
                    jbooleanArray ja = env->NewBooleanArray(sz);
                    std::vector<jboolean> buf(sz);
                    for (jsize k = 0; k < sz; ++k) buf[k] = nb::cast<bool>(l[k]) ? JNI_TRUE : JNI_FALSE;
                    if (sz > 0) env->SetBooleanArrayRegion(ja, 0, sz, buf.data());
                    jargs[i].l = ja; locals.push_back(ja);
                } else if (nb::hasattr(item, "_ptr")) {
                    jargs[i].l = (jobject)(uintptr_t)nb::cast<int64_t>(item.attr("_ptr"));
                } else jargs[i].l = nullptr;
                break;
            }

            // ── v9 FIX 3: char[] ──────────────────────────────────────
            case 'c': {
                if (item.is_none()) jargs[i].l = nullptr;
                else if (nb::isinstance<nb::list>(item)) {
                    nb::list l = nb::cast<nb::list>(item);
                    jsize sz = (jsize)nb::len(l);
                    jcharArray ja = env->NewCharArray(sz);
                    std::vector<jchar> buf(sz);
                    for (jsize k = 0; k < sz; ++k) {
                        if (nb::isinstance<nb::str>(l[k])) {
                            std::string s = nb::cast<std::string>(l[k]);
                            buf[k] = s.empty() ? 0 : (jchar)(uint16_t)(unsigned char)s[0];
                        } else {
                            buf[k] = (jchar)nb::cast<int>(l[k]);
                        }
                    }
                    if (sz > 0) env->SetCharArrayRegion(ja, 0, sz, buf.data());
                    jargs[i].l = ja; locals.push_back(ja);
                } else if (nb::hasattr(item, "_ptr")) {
                    jargs[i].l = (jobject)(uintptr_t)nb::cast<int64_t>(item.attr("_ptr"));
                } else jargs[i].l = nullptr;
                break;
            }

            // ── v9 FIX 3: short[] ─────────────────────────────────────
            case 'h': {
                if (item.is_none()) jargs[i].l = nullptr;
                else if (nb::isinstance<nb::list>(item)) {
                    nb::list l = nb::cast<nb::list>(item);
                    jsize sz = (jsize)nb::len(l);
                    jshortArray ja = env->NewShortArray(sz);
                    std::vector<jshort> buf(sz);
                    for (jsize k = 0; k < sz; ++k) buf[k] = (jshort)nb::cast<int>(l[k]);
                    if (sz > 0) env->SetShortArrayRegion(ja, 0, sz, buf.data());
                    jargs[i].l = ja; locals.push_back(ja);
                } else if (nb::hasattr(item, "_ptr")) {
                    jargs[i].l = (jobject)(uintptr_t)nb::cast<int64_t>(item.attr("_ptr"));
                } else jargs[i].l = nullptr;
                break;
            }

            // ── v9 FIX 3: String[] ────────────────────────────────────
            case 'T': {
                if (item.is_none()) jargs[i].l = nullptr;
                else if (nb::isinstance<nb::list>(item)) {
                    nb::list l = nb::cast<nb::list>(item);
                    jsize sz = (jsize)nb::len(l);
                    jobjectArray ja = env->NewObjectArray(sz, g_jstring_class, nullptr);
                    for (jsize k = 0; k < sz; ++k) {
                        auto elem = l[k];
                        if (elem.is_none()) {
                            env->SetObjectArrayElement(ja, k, nullptr);
                        } else if (nb::isinstance<nb::str>(elem)) {
                            jstring js = stratum_str_to_jstring(env, nb::cast<std::string>(elem));
                            env->SetObjectArrayElement(ja, k, js);
                            env->DeleteLocalRef(js);
                        } else if (nb::hasattr(elem, "_ptr")) {
                            env->SetObjectArrayElement(ja, k, (jobject)(uintptr_t)nb::cast<int64_t>(elem.attr("_ptr")));
                        }
                    }
                    jargs[i].l = ja; locals.push_back(ja);
                } else if (nb::hasattr(item, "_ptr")) {
                    jargs[i].l = (jobject)(uintptr_t)nb::cast<int64_t>(item.attr("_ptr"));
                } else jargs[i].l = nullptr;
                break;
            }

            // ── v9 FIX 3: generic Object[] (Surface[], Object[], etc.) ──
            case 'A': {
                if (item.is_none()) jargs[i].l = nullptr;
                else if (nb::isinstance<nb::list>(item)) {
                    nb::list l = nb::cast<nb::list>(item);
                    jsize sz = (jsize)nb::len(l);
                    jobjectArray ja = env->NewObjectArray(sz, g_object_class, nullptr);
                    for (jsize k = 0; k < sz; ++k) {
                        auto elem = l[k];
                        if (elem.is_none()) {
                            env->SetObjectArrayElement(ja, k, nullptr);
                        } else if (nb::isinstance<nb::str>(elem)) {
                            jstring js = stratum_str_to_jstring(env, nb::cast<std::string>(elem));
                            env->SetObjectArrayElement(ja, k, js);
                            env->DeleteLocalRef(js);
                        } else if (nb::hasattr(elem, "_ptr")) {
                            env->SetObjectArrayElement(ja, k, (jobject)(uintptr_t)nb::cast<int64_t>(elem.attr("_ptr")));
                        }
                    }
                    jargs[i].l = ja; locals.push_back(ja);
                } else if (nb::hasattr(item, "_ptr")) {
                    jargs[i].l = (jobject)(uintptr_t)nb::cast<int64_t>(item.attr("_ptr"));
                } else jargs[i].l = nullptr;
                break;
            }

            // ── v9 FIX 3: java.util.List / Collection / Iterable ────────
            // Built as a real java.util.ArrayList so the callee can use
            // any List method on it (size(), get(), iterator(), ...).
            case 'M': {
                if (item.is_none()) {
                    jargs[i].l = nullptr;
                } else if (nb::isinstance<nb::list>(item)) {
                    jclass alcls = find_class(env, "java/util/ArrayList");
                    if (!alcls) throw std::runtime_error("Stratum: java/util/ArrayList not found");
                    jmethodID alctor = env->GetMethodID(alcls, "<init>", "()V");
                    jmethodID aladd  = env->GetMethodID(alcls, "add", "(Ljava/lang/Object;)Z");
                    jobject al = env->NewObject(alcls, alctor);

                    nb::list l = nb::cast<nb::list>(item);
                    jsize sz = (jsize)nb::len(l);
                    for (jsize k = 0; k < sz; ++k) {
                        auto elem = l[k];
                        if (elem.is_none()) {
                            env->CallBooleanMethod(al, aladd, nullptr);
                        } else if (nb::isinstance<nb::str>(elem)) {
                            jstring js = stratum_str_to_jstring(env, nb::cast<std::string>(elem));
                            env->CallBooleanMethod(al, aladd, js);
                            env->DeleteLocalRef(js);
                        } else if (nb::hasattr(elem, "_ptr")) {
                            env->CallBooleanMethod(al, aladd, (jobject)(uintptr_t)nb::cast<int64_t>(elem.attr("_ptr")));
                        }
                    }
                    env->DeleteLocalRef(alcls);
                    jargs[i].l = al; locals.push_back(al);
                } else if (nb::hasattr(item, "_ptr")) {
                    jargs[i].l = (jobject)(uintptr_t)nb::cast<int64_t>(item.attr("_ptr"));
                } else jargs[i].l = nullptr;
                break;
            }

            // ── abstract adapter (Stage 05.5 generated Java class) ──────
            case 'a': {
                if (item.is_none()) {
                    jargs[i].l = nullptr;
                    break;
                }
                if (nb::hasattr(item, "_ptr")) {
                    jargs[i].l = (jobject)(uintptr_t)nb::cast<int64_t>(item.attr("_ptr"));
                    break;
                }
                static std::atomic<uint64_t> s_aid{0};
                std::string key = (caller_ptr ? ("obj_" + std::to_string(caller_ptr) + "_a_") : "adapter_") + std::to_string(++s_aid);
                if (nb::isinstance<nb::callable>(item)) {
                    store_callback(key, nb::cast<nb::callable>(item));
                } else if (nb::isinstance<nb::dict>(item)) {
                    nb::dict d = nb::cast<nb::dict>(item);
                    for (auto kv : d) store_callback(key + "#" + nb::cast<std::string>(kv.first), nb::cast<nb::callable>(kv.second));
                }
                const char* adapter_name = get_str(mm.adapter_jni_offset);
                jclass acls = find_class(env, adapter_name);
                if (!acls) throw std::runtime_error(std::string("Stratum: adapter class not compiled into APK: ") + adapter_name);
                jmethodID actor = env->GetMethodID(acls, "<init>", "(Ljava/lang/String;)V");
                jstring jk = env->NewStringUTF(key.c_str());
                jobject aobj = env->NewObject(acls, actor, jk);
                env->DeleteLocalRef(jk); env->DeleteLocalRef(acls);
                stratum_check_java_exc(env);
                jargs[i].l = aobj; locals.push_back(aobj);
                break;
            }

            // ── callable-to-proxy (dynamic java.lang.reflect.Proxy) ──────
            case 'p': {
                if (item.is_none()) {
                    jargs[i].l = nullptr;
                    break;
                }
                if (nb::hasattr(item, "_ptr")) {
                    jargs[i].l = (jobject)(uintptr_t)nb::cast<int64_t>(item.attr("_ptr"));
                    break;
                }
                static std::atomic<uint64_t> s_pid{0};
                std::string key = (caller_ptr ? ("obj_" + std::to_string(caller_ptr) + "_p_") : "proxy_") + std::to_string(++s_pid);
                if (nb::isinstance<nb::callable>(item)) store_callback(key, nb::cast<nb::callable>(item));
                const char* iface_name = get_str(mm.adapter_jni_offset);
                jclass iface_cls = find_class(env, iface_name);
                if (!iface_cls) throw std::runtime_error(std::string("Stratum: interface not found: ") + iface_name);
                jmethodID hctor = env->GetMethodID(g_stratum_handler_class, "<init>", "(Ljava/lang/String;)V");
                jstring jk = env->NewStringUTF(key.c_str());
                jobject handler = env->NewObject(g_stratum_handler_class, hctor, jk);
                env->DeleteLocalRef(jk);
                jmethodID new_proxy = env->GetStaticMethodID(g_proxy_class, "newProxyInstance",
                    "(Ljava/lang/ClassLoader;[Ljava/lang/Class;Ljava/lang/reflect/InvocationHandler;)Ljava/lang/Object;");
                jobjectArray ia = env->NewObjectArray(1, g_class_class, iface_cls);
                jobject proxy = env->CallStaticObjectMethod(g_proxy_class, new_proxy, g_app_class_loader, ia, handler);
                env->DeleteLocalRef(iface_cls); env->DeleteLocalRef(ia); env->DeleteLocalRef(handler);
                stratum_check_java_exc(env);
                jargs[i].l = proxy; locals.push_back(proxy);
                break;
            }

            default: {
                if (item.is_none()) {
                    jargs[i].l = nullptr;
                } else if (nb::hasattr(item, "_ptr")) {
                    jargs[i].l = (jobject)(uintptr_t)nb::cast<int64_t>(item.attr("_ptr"));
                } else if (nb::isinstance<nb::int_>(item)) {
                    // FIX: Allow integer pointer (e.g. from sf_get_*) to pass as jobject
                    jargs[i].l = (jobject)(uintptr_t)nb::cast<int64_t>(item);
                } else if (nb::isinstance<nb::str>(item)) {
                    jstring js = stratum_str_to_jstring(env, nb::cast<std::string>(item));
                    jargs[i].l = js;
                    locals.push_back(js);
                } else {
                    jargs[i].l = nullptr;
                }
                break;
            }
        }
    }
}

// RESOLVE_AND_LOOKUP() — shared setup for every call_* method-dispatch
// function. v9 FIX 1: wraps the whole call in a JniLocalFrame so any
// local refs created while packing arguments / making the call are
// bounded to this one dispatch, not accumulated across a loop.
// The explicit null-pointer guard prevents Call<Type>MethodA on a null
// jobject, which is undefined behaviour and reliably segfaults the
// entire process — uncatchable from Python.
#define RESOLVE_AND_LOOKUP()                                                        \
    JNIEnv* env = get_env();                                                        \
    if (!env) throw std::runtime_error("Stratum: No JNIEnv on this thread");        \
    JniLocalFrame _local_frame(env, 32);                                            \
    ClassMeta& cls = g_classes[class_id];                                           \
    if (!cls.resolved) resolve_class_slots(env, class_id);                          \
    jmethodID mid = cls.method_ids[slot];                                           \
    if (!mid) throw std::runtime_error("Stratum: Method unavailable on device API"); \
    if (!cls.methods[slot].is_static && !ptr) {                                     \
        throw std::runtime_error("Stratum: Attempted to call a method on a null Java object"); \
    }                                                                               \
    jvalue jargs[32] = {};  /* v9 FIX 4: zero-init, always bounded to 32 */          \
    std::vector<jobject> locals;                                                    \
    pack_arguments(env, get_str(cls.methods[slot].tags_offset), cls.methods[slot], args, jargs, locals, ptr);

#define CLEANUP_AND_CHECK() stratum_check_java_exc(env);

// ── Generic method-call dispatch primitives ─────────────────────────────────

void call_v(int64_t ptr, uint32_t class_id, uint32_t slot, nb::args args) {
    RESOLVE_AND_LOOKUP()
    if (cls.methods[slot].is_static) {
        nb::gil_scoped_release r;
        env->CallStaticVoidMethodA(cls.class_ref, mid, jargs);
    } else {
        nb::gil_scoped_release r;
        env->CallVoidMethodA((jobject)(uintptr_t)ptr, mid, jargs);
    }
    CLEANUP_AND_CHECK()
}

bool call_z(int64_t ptr, uint32_t class_id, uint32_t slot, nb::args args) {
    RESOLVE_AND_LOOKUP()
    jboolean res;
    if (cls.methods[slot].is_static) {
        nb::gil_scoped_release r;
        res = env->CallStaticBooleanMethodA(cls.class_ref, mid, jargs);
    } else {
        nb::gil_scoped_release r;
        res = env->CallBooleanMethodA((jobject)(uintptr_t)ptr, mid, jargs);
    }
    CLEANUP_AND_CHECK()
    return res != JNI_FALSE;
}

int64_t call_i(int64_t ptr, uint32_t class_id, uint32_t slot, nb::args args) {
    RESOLVE_AND_LOOKUP()
    int64_t res = 0;
    uint8_t rt = cls.methods[slot].ret_type;
    bool is_stat = cls.methods[slot].is_static;
    jobject target = (jobject)(uintptr_t)ptr;
    jclass c_ref = cls.class_ref;

    nb::gil_scoped_release r;
    if (rt == 2) {
        res = (int64_t)(is_stat ? env->CallStaticByteMethodA(c_ref, mid, jargs)
                                : env->CallByteMethodA(target, mid, jargs));
    } else if (rt == 3) {
        res = (int64_t)(is_stat ? env->CallStaticCharMethodA(c_ref, mid, jargs)
                                : env->CallCharMethodA(target, mid, jargs));
    } else if (rt == 4) {
        res = (int64_t)(is_stat ? env->CallStaticShortMethodA(c_ref, mid, jargs)
                                : env->CallShortMethodA(target, mid, jargs));
    } else {
        res = (int64_t)(is_stat ? env->CallStaticIntMethodA(c_ref, mid, jargs)
                                : env->CallIntMethodA(target, mid, jargs));
    }
    CLEANUP_AND_CHECK()
    return res;
}

int64_t call_j(int64_t ptr, uint32_t class_id, uint32_t slot, nb::args args) {
    RESOLVE_AND_LOOKUP()
    jlong res;
    if (cls.methods[slot].is_static) {
        nb::gil_scoped_release r;
        res = env->CallStaticLongMethodA(cls.class_ref, mid, jargs);
    } else {
        nb::gil_scoped_release r;
        res = env->CallLongMethodA((jobject)(uintptr_t)ptr, mid, jargs);
    }
    CLEANUP_AND_CHECK()
    return res;
}

double call_d(int64_t ptr, uint32_t class_id, uint32_t slot, nb::args args) {
    RESOLVE_AND_LOOKUP()
    double res;
    if (cls.methods[slot].ret_type == 7) { // float
        jfloat f;
        if (cls.methods[slot].is_static) {
            nb::gil_scoped_release r;
            f = env->CallStaticFloatMethodA(cls.class_ref, mid, jargs);
        } else {
            nb::gil_scoped_release r;
            f = env->CallFloatMethodA((jobject)(uintptr_t)ptr, mid, jargs);
        }
        res = (double)f;
    } else {
        if (cls.methods[slot].is_static) {
            nb::gil_scoped_release r;
            res = env->CallStaticDoubleMethodA(cls.class_ref, mid, jargs);
        } else {
            nb::gil_scoped_release r;
            res = env->CallDoubleMethodA((jobject)(uintptr_t)ptr, mid, jargs);
        }
    }
    CLEANUP_AND_CHECK()
    return res;
}

std::string call_str(int64_t ptr, uint32_t class_id, uint32_t slot, nb::args args) {
    RESOLVE_AND_LOOKUP()
    jobject res;
    if (cls.methods[slot].is_static) {
        nb::gil_scoped_release r;
        res = env->CallStaticObjectMethodA(cls.class_ref, mid, jargs);
    } else {
        nb::gil_scoped_release r;
        res = env->CallObjectMethodA((jobject)(uintptr_t)ptr, mid, jargs);
    }
    CLEANUP_AND_CHECK()
    return stratum_jstring_to_str(env, res); // frees its own local ref internally
}

int64_t call_o(int64_t ptr, uint32_t class_id, uint32_t slot, nb::args args) {
    RESOLVE_AND_LOOKUP()
    jobject res;
    if (cls.methods[slot].is_static) {
        nb::gil_scoped_release r;
        res = env->CallStaticObjectMethodA(cls.class_ref, mid, jargs);
    } else {
        nb::gil_scoped_release r;
        res = env->CallObjectMethodA((jobject)(uintptr_t)ptr, mid, jargs);
    }
    CLEANUP_AND_CHECK()
    if (!res) return 0;
    jobject gref = env->NewGlobalRef(res);
    env->DeleteLocalRef(res);
    return (int64_t)(uintptr_t)gref;
}

nb::object call_arr(int64_t ptr, uint32_t class_id, uint32_t slot, nb::args args) {
    RESOLVE_AND_LOOKUP()
    jobject res;
    if (cls.methods[slot].is_static) {
        nb::gil_scoped_release r;
        res = env->CallStaticObjectMethodA(cls.class_ref, mid, jargs);
    } else {
        nb::gil_scoped_release r;
        res = env->CallObjectMethodA((jobject)(uintptr_t)ptr, mid, jargs);
    }
    CLEANUP_AND_CHECK()
    if (!res) return nb::list();

    static jclass s_byte_arr_cls = nullptr, s_int_arr_cls = nullptr,
                  s_long_arr_cls = nullptr, s_flt_arr_cls = nullptr,
                  s_dbl_arr_cls  = nullptr, s_bool_arr_cls = nullptr,
                  s_char_arr_cls = nullptr, s_short_arr_cls = nullptr;
    if (!s_byte_arr_cls) {
        auto get_arr_cls = [&](const char* sig) {
            jclass c = env->FindClass(sig);
            jclass g = (jclass)env->NewGlobalRef(c);
            env->DeleteLocalRef(c);
            return g;
        };
        s_byte_arr_cls  = get_arr_cls("[B");
        s_int_arr_cls   = get_arr_cls("[I");
        s_long_arr_cls  = get_arr_cls("[J");
        s_flt_arr_cls   = get_arr_cls("[F");
        s_dbl_arr_cls   = get_arr_cls("[D");
        s_bool_arr_cls  = get_arr_cls("[Z");
        s_char_arr_cls  = get_arr_cls("[C");
        s_short_arr_cls = get_arr_cls("[S");
    }

    // 1. byte[] -> return bytes
    if (env->IsInstanceOf(res, s_byte_arr_cls)) {
        jbyteArray ba = (jbyteArray)res;
        jsize len = env->GetArrayLength(ba);
        jbyte* buf = env->GetByteArrayElements(ba, nullptr);
        nb::bytes out(reinterpret_cast<const char*>(buf), (size_t)len);
        env->ReleaseByteArrayElements(ba, buf, JNI_ABORT);
        env->DeleteLocalRef(res);
        return out;
    }

    // 2. int[] -> return list[int]
    if (env->IsInstanceOf(res, s_int_arr_cls)) {
        jintArray ia = (jintArray)res;
        jsize len = env->GetArrayLength(ia);
        std::vector<jint> buf(len);
        if (len > 0) env->GetIntArrayRegion(ia, 0, len, buf.data());
        env->DeleteLocalRef(res);
        nb::list out;
        for (jsize i = 0; i < len; ++i) out.append(nb::int_((int64_t)buf[i]));
        return out;
    }

    // 3. float[] -> return list[float]
    if (env->IsInstanceOf(res, s_flt_arr_cls)) {
        jfloatArray fa = (jfloatArray)res;
        jsize len = env->GetArrayLength(fa);
        std::vector<jfloat> buf(len);
        if (len > 0) env->GetFloatArrayRegion(fa, 0, len, buf.data());
        env->DeleteLocalRef(res);
        nb::list out;
        for (jsize i = 0; i < len; ++i) out.append(nb::float_((double)buf[i]));
        return out;
    }

    // 4. long[] -> return list[int]
    if (env->IsInstanceOf(res, s_long_arr_cls)) {
        jlongArray ja = (jlongArray)res;
        jsize len = env->GetArrayLength(ja);
        std::vector<jlong> buf(len);
        if (len > 0) env->GetLongArrayRegion(ja, 0, len, buf.data());
        env->DeleteLocalRef(res);
        nb::list out;
        for (jsize i = 0; i < len; ++i) out.append(nb::int_((int64_t)buf[i]));
        return out;
    }

    // 5. double[] -> return list[float]
    if (env->IsInstanceOf(res, s_dbl_arr_cls)) {
        jdoubleArray da = (jdoubleArray)res;
        jsize len = env->GetArrayLength(da);
        std::vector<jdouble> buf(len);
        if (len > 0) env->GetDoubleArrayRegion(da, 0, len, buf.data());
        env->DeleteLocalRef(res);
        nb::list out;
        for (jsize i = 0; i < len; ++i) out.append(nb::float_(buf[i]));
        return out;
    }

    // 6. boolean[] -> return list[bool]
    if (env->IsInstanceOf(res, s_bool_arr_cls)) {
        jbooleanArray za = (jbooleanArray)res;
        jsize len = env->GetArrayLength(za);
        std::vector<jboolean> buf(len);
        if (len > 0) env->GetBooleanArrayRegion(za, 0, len, buf.data());
        env->DeleteLocalRef(res);
        nb::list out;
        for (jsize i = 0; i < len; ++i) out.append(nb::bool_(buf[i] != JNI_FALSE));
        return out;
    }

    // 7. char[] -> return str
    if (env->IsInstanceOf(res, s_char_arr_cls)) {
        jcharArray ca = (jcharArray)res;
        jsize len = env->GetArrayLength(ca);
        std::vector<jchar> buf(len);
        if (len > 0) env->GetCharArrayRegion(ca, 0, len, buf.data());
        env->DeleteLocalRef(res);
        std::string utf8;
        for (jsize i = 0; i < len; ++i) {
            uint16_t c = (uint16_t)buf[i];
            if (c < 0x80) { utf8 += (char)c; }
            else if (c < 0x800) { utf8 += (char)(0xC0 | (c >> 6)); utf8 += (char)(0x80 | (c & 0x3F)); }
            else { utf8 += (char)(0xE0 | (c >> 12)); utf8 += (char)(0x80 | ((c >> 6) & 0x3F)); utf8 += (char)(0x80 | (c & 0x3F)); }
        }
        return nb::str(utf8.c_str());
    }

    // 8. short[] -> return list[int]
    if (env->IsInstanceOf(res, s_short_arr_cls)) {
        jshortArray sa = (jshortArray)res;
        jsize len = env->GetArrayLength(sa);
        std::vector<jshort> buf(len);
        if (len > 0) env->GetShortArrayRegion(sa, 0, len, buf.data());
        env->DeleteLocalRef(res);
        nb::list out;
        for (jsize i = 0; i < len; ++i) out.append(nb::int_((int64_t)buf[i]));
        return out;
    }

    // 9. Object[] and String[]
    nb::list py_list;
    jsize len = env->GetArrayLength((jarray)res);
    for (jsize i = 0; i < len; ++i) {
        jobject elem = env->GetObjectArrayElement((jobjectArray)res, i);
        if (!elem) {
            py_list.append(nb::none());
        } else if (g_jstring_class && env->IsInstanceOf(elem, g_jstring_class)) {
            py_list.append(nb::str(stratum_jstring_to_str(env, elem).c_str()));
        } else {
            jobject gref = env->NewGlobalRef(elem);
            env->DeleteLocalRef(elem);
            py_list.append(nb::cast((int64_t)(uintptr_t)gref));
        }
    }
    env->DeleteLocalRef(res);
    return py_list;
}
nb::object call_list(int64_t ptr, uint32_t class_id, uint32_t slot, nb::args args) {
    RESOLVE_AND_LOOKUP()
    jobject res;
    if (cls.methods[slot].is_static) {
        nb::gil_scoped_release r;
        res = env->CallStaticObjectMethodA(cls.class_ref, mid, jargs);
    } else {
        nb::gil_scoped_release r;
        res = env->CallObjectMethodA((jobject)(uintptr_t)ptr, mid, jargs);
    }
    CLEANUP_AND_CHECK()
    if (!res) return nb::list();
    nb::list result = stratum_collection_to_list(env, res);
    env->DeleteLocalRef(res);
    return result;
}

nb::object call_map(int64_t ptr, uint32_t class_id, uint32_t slot, nb::args args) {
    RESOLVE_AND_LOOKUP()
    jobject res;
    if (cls.methods[slot].is_static) {
        nb::gil_scoped_release r;
        res = env->CallStaticObjectMethodA(cls.class_ref, mid, jargs);
    } else {
        nb::gil_scoped_release r;
        res = env->CallObjectMethodA((jobject)(uintptr_t)ptr, mid, jargs);
    }
    CLEANUP_AND_CHECK()
    if (!res) return nb::dict();
    nb::dict result = stratum_map_to_dict(env, res);
    env->DeleteLocalRef(res);
    return result;
}

int64_t new_instance(uint32_t class_id, uint32_t slot, nb::args args) {
    JNIEnv* env = get_env();
    if (!env) throw std::runtime_error("Stratum: No JNIEnv on this thread");
    JniLocalFrame _local_frame(env, 32); // v9 FIX 1
    ClassMeta& cls = g_classes[class_id];
    if (!cls.resolved) resolve_class_slots(env, class_id);
    jmethodID mid = cls.method_ids[slot];
    if (!mid) throw std::runtime_error("Stratum: Constructor unavailable on this device API level");

    jvalue jargs[32] = {};
    std::vector<jobject> locals;
    pack_arguments(env, get_str(cls.methods[slot].tags_offset), cls.methods[slot], args, jargs, locals);

    jobject obj;
    {
        nb::gil_scoped_release r;
        obj = env->NewObjectA(cls.class_ref, mid, jargs);
    }
    stratum_check_java_exc(env);

    if (!obj) return 0;
    jobject gref = env->NewGlobalRef(obj);
    env->DeleteLocalRef(obj);
    return (int64_t)(uintptr_t)gref;
}

void delete_ref(int64_t ptr) {
    if (!ptr) return;
    // Automatically clear any callbacks associated with this object prefix
    remove_callbacks_by_prefix("obj_" + std::to_string(ptr) + "_");
    JNIEnv* env = get_env();
    if (env) {
        env->DeleteGlobalRef((jobject)(uintptr_t)ptr);
    }
}

static inline void ensure_class_resolved(JNIEnv* env, uint32_t class_id) {
    if (!g_classes[class_id].resolved) resolve_class_slots(env, class_id);
}

// ── Field access primitives ──────────────────────────────────────────────
// Mirrors call_*(): FieldMeta/field_ids get resolved lazily by
// resolve_class_slots() above; these are the dispatch entry points per
// field return type. Without this whole block, static SDK constants
// (Color.RED, View.VISIBLE, Gravity.CENTER, ViewGroup.LayoutParams.
// MATCH_PARENT — all of which appear in nearly every real Android UI
// snippet) would be unreachable from Python even though the C++ side
// already has their jfieldIDs cached.

int64_t field_get_i(int64_t ptr, uint32_t class_id, uint32_t slot) {
    JNIEnv* env = get_env();
    if (!env) throw std::runtime_error("Stratum: No JNIEnv on this thread");
    ensure_class_resolved(env, class_id);
    ClassMeta& cls = g_classes[class_id];
    jfieldID fid = cls.field_ids[slot];
    if (!fid) throw std::runtime_error("Stratum: field unavailable on this device API level");
    if (!cls.fields[slot].is_static && !ptr) throw std::runtime_error("Stratum: field access on null Java object");
    jint res = cls.fields[slot].is_static
        ? env->GetStaticIntField(cls.class_ref, fid)
        : env->GetIntField((jobject)(uintptr_t)ptr, fid);
    stratum_check_java_exc(env);
    return (int64_t)res;
}

bool field_get_z(int64_t ptr, uint32_t class_id, uint32_t slot) {
    JNIEnv* env = get_env();
    if (!env) throw std::runtime_error("Stratum: No JNIEnv on this thread");
    ensure_class_resolved(env, class_id);
    ClassMeta& cls = g_classes[class_id];
    jfieldID fid = cls.field_ids[slot];
    if (!fid) throw std::runtime_error("Stratum: field unavailable");
    if (!cls.fields[slot].is_static && !ptr) throw std::runtime_error("Stratum: field access on null Java object");
    jboolean res = cls.fields[slot].is_static
        ? env->GetStaticBooleanField(cls.class_ref, fid)
        : env->GetBooleanField((jobject)(uintptr_t)ptr, fid);
    stratum_check_java_exc(env);
    return res != JNI_FALSE;
}

int64_t field_get_j(int64_t ptr, uint32_t class_id, uint32_t slot) {
    JNIEnv* env = get_env();
    if (!env) throw std::runtime_error("Stratum: No JNIEnv on this thread");
    ensure_class_resolved(env, class_id);
    ClassMeta& cls = g_classes[class_id];
    jfieldID fid = cls.field_ids[slot];
    if (!fid) throw std::runtime_error("Stratum: field unavailable");
    if (!cls.fields[slot].is_static && !ptr) throw std::runtime_error("Stratum: field access on null Java object");
    jlong res = cls.fields[slot].is_static
        ? env->GetStaticLongField(cls.class_ref, fid)
        : env->GetLongField((jobject)(uintptr_t)ptr, fid);
    stratum_check_java_exc(env);
    return res;
}

double field_get_d(int64_t ptr, uint32_t class_id, uint32_t slot) {
    JNIEnv* env = get_env();
    if (!env) throw std::runtime_error("Stratum: No JNIEnv on this thread");
    ensure_class_resolved(env, class_id);
    ClassMeta& cls = g_classes[class_id];
    jfieldID fid = cls.field_ids[slot];
    if (!fid) throw std::runtime_error("Stratum: field unavailable");
    if (!cls.fields[slot].is_static && !ptr) throw std::runtime_error("Stratum: field access on null Java object");
    double res;
    if (cls.fields[slot].type_id == 7) { // float
        jfloat f = cls.fields[slot].is_static
            ? env->GetStaticFloatField(cls.class_ref, fid)
            : env->GetFloatField((jobject)(uintptr_t)ptr, fid);
        res = (double)f;
    } else {
        res = cls.fields[slot].is_static
            ? env->GetStaticDoubleField(cls.class_ref, fid)
            : env->GetDoubleField((jobject)(uintptr_t)ptr, fid);
    }
    stratum_check_java_exc(env);
    return res;
}

// v9 FIX 1: wrapped in JniLocalFrame — a tight loop reading a String
// field repeatedly (e.g. reading many EditText.getText()-style fields
// via field access, or scanning many View tags) could otherwise
// accumulate local refs across iterations before this fix.
std::string field_get_str(int64_t ptr, uint32_t class_id, uint32_t slot) {
    JNIEnv* env = get_env();
    if (!env) throw std::runtime_error("Stratum: No JNIEnv on this thread");
    JniLocalFrame _local_frame(env, 8);
    ensure_class_resolved(env, class_id);
    ClassMeta& cls = g_classes[class_id];
    jfieldID fid = cls.field_ids[slot];
    if (!fid) throw std::runtime_error("Stratum: field unavailable");
    if (!cls.fields[slot].is_static && !ptr) throw std::runtime_error("Stratum: field access on null Java object");
    jobject res = cls.fields[slot].is_static
        ? env->GetStaticObjectField(cls.class_ref, fid)
        : env->GetObjectField((jobject)(uintptr_t)ptr, fid);
    stratum_check_java_exc(env);
    return stratum_jstring_to_str(env, res); // frees its own local ref internally
}

// v9 FIX 1: wrapped in JniLocalFrame — same reasoning as field_get_str.
int64_t field_get_o(int64_t ptr, uint32_t class_id, uint32_t slot) {
    JNIEnv* env = get_env();
    if (!env) throw std::runtime_error("Stratum: No JNIEnv on this thread");
    JniLocalFrame _local_frame(env, 8);
    ensure_class_resolved(env, class_id);
    ClassMeta& cls = g_classes[class_id];
    jfieldID fid = cls.field_ids[slot];
    if (!fid) throw std::runtime_error("Stratum: field unavailable");
    if (!cls.fields[slot].is_static && !ptr) throw std::runtime_error("Stratum: field access on null Java object");
    jobject res = cls.fields[slot].is_static
        ? env->GetStaticObjectField(cls.class_ref, fid)
        : env->GetObjectField((jobject)(uintptr_t)ptr, fid);
    stratum_check_java_exc(env);
    if (!res) return 0;
    jobject gref = env->NewGlobalRef(res);
    env->DeleteLocalRef(res);
    return (int64_t)(uintptr_t)gref;
}

// Setter for the common case (int-family fields). Most real SDK usage is
// read-only constants; add field_set_z/field_set_str/field_set_o the
// same way (mirroring the getters above) if/when mutable String/bool/
// object fields are actually needed.
void field_set_i(int64_t ptr, uint32_t class_id, uint32_t slot, int64_t val) {
    JNIEnv* env = get_env();
    if (!env) throw std::runtime_error("Stratum: No JNIEnv on this thread");
    ensure_class_resolved(env, class_id);
    ClassMeta& cls = g_classes[class_id];
    jfieldID fid = cls.field_ids[slot];
    if (!fid) throw std::runtime_error("Stratum: field unavailable");
    if (!cls.fields[slot].is_static && !ptr) throw std::runtime_error("Stratum: field access on null Java object");
    if (cls.fields[slot].is_static) env->SetStaticIntField(cls.class_ref, fid, (jint)val);
    else env->SetIntField((jobject)(uintptr_t)ptr, fid, (jint)val);
    stratum_check_java_exc(env);
}

void field_set_z(int64_t ptr, uint32_t class_id, uint32_t slot, bool val) {
    JNIEnv* env = get_env();
    if (!env) throw std::runtime_error("Stratum: No JNIEnv on this thread");
    ensure_class_resolved(env, class_id);
    ClassMeta& cls = g_classes[class_id];
    jfieldID fid = cls.field_ids[slot];
    if (!fid) throw std::runtime_error("Stratum: field unavailable");
    if (!cls.fields[slot].is_static && !ptr) throw std::runtime_error("Stratum: field access on null Java object");
    jboolean jv = val ? JNI_TRUE : JNI_FALSE;
    if (cls.fields[slot].is_static) env->SetStaticBooleanField(cls.class_ref, fid, jv);
    else env->SetBooleanField((jobject)(uintptr_t)ptr, fid, jv);
    stratum_check_java_exc(env);
}

void field_set_j(int64_t ptr, uint32_t class_id, uint32_t slot, int64_t val) {
    JNIEnv* env = get_env();
    if (!env) throw std::runtime_error("Stratum: No JNIEnv on this thread");
    ensure_class_resolved(env, class_id);
    ClassMeta& cls = g_classes[class_id];
    jfieldID fid = cls.field_ids[slot];
    if (!fid) throw std::runtime_error("Stratum: field unavailable");
    if (!cls.fields[slot].is_static && !ptr) throw std::runtime_error("Stratum: field access on null Java object");
    if (cls.fields[slot].is_static) env->SetStaticLongField(cls.class_ref, fid, (jlong)val);
    else env->SetLongField((jobject)(uintptr_t)ptr, fid, (jlong)val);
    stratum_check_java_exc(env);
}

void field_set_d(int64_t ptr, uint32_t class_id, uint32_t slot, double val) {
    JNIEnv* env = get_env();
    if (!env) throw std::runtime_error("Stratum: No JNIEnv on this thread");
    ensure_class_resolved(env, class_id);
    ClassMeta& cls = g_classes[class_id];
    jfieldID fid = cls.field_ids[slot];
    if (!fid) throw std::runtime_error("Stratum: field unavailable");
    if (!cls.fields[slot].is_static && !ptr) throw std::runtime_error("Stratum: field access on null Java object");
    if (cls.fields[slot].type_id == 7) { // float
        if (cls.fields[slot].is_static) env->SetStaticFloatField(cls.class_ref, fid, (jfloat)val);
        else env->SetFloatField((jobject)(uintptr_t)ptr, fid, (jfloat)val);
    } else {
        if (cls.fields[slot].is_static) env->SetStaticDoubleField(cls.class_ref, fid, (jdouble)val);
        else env->SetDoubleField((jobject)(uintptr_t)ptr, fid, (jdouble)val);
    }
    stratum_check_java_exc(env);
}

void field_set_str(int64_t ptr, uint32_t class_id, uint32_t slot, const std::string& val) {
    JNIEnv* env = get_env();
    if (!env) throw std::runtime_error("Stratum: No JNIEnv on this thread");
    JniLocalFrame _local_frame(env, 8);
    ensure_class_resolved(env, class_id);
    ClassMeta& cls = g_classes[class_id];
    jfieldID fid = cls.field_ids[slot];
    if (!fid) throw std::runtime_error("Stratum: field unavailable");
    if (!cls.fields[slot].is_static && !ptr) throw std::runtime_error("Stratum: field access on null Java object");
    jstring js = stratum_str_to_jstring(env, val);
    if (cls.fields[slot].is_static) env->SetStaticObjectField(cls.class_ref, fid, js);
    else env->SetObjectField((jobject)(uintptr_t)ptr, fid, js);
    stratum_check_java_exc(env);
}

void field_set_o(int64_t ptr, uint32_t class_id, uint32_t slot, int64_t val_ptr) {
    JNIEnv* env = get_env();
    if (!env) throw std::runtime_error("Stratum: No JNIEnv on this thread");
    ensure_class_resolved(env, class_id);
    ClassMeta& cls = g_classes[class_id];
    jfieldID fid = cls.field_ids[slot];
    if (!fid) throw std::runtime_error("Stratum: field unavailable");
    if (!cls.fields[slot].is_static && !ptr) throw std::runtime_error("Stratum: field access on null Java object");
    jobject val = (jobject)(uintptr_t)val_ptr;
    if (cls.fields[slot].is_static) env->SetStaticObjectField(cls.class_ref, fid, val);
    else env->SetObjectField((jobject)(uintptr_t)ptr, fid, val);
    stratum_check_java_exc(env);
}

// Comparing raw int64 pointer values in Python is NOT identity —
// two different global refs (e.g. one from a return value, one from a
// field getter) can point at the same Java object with different
// jobject handle values. Use real JNI identity comparison instead.
bool is_same_object(int64_t ptr_a, int64_t ptr_b) {
    if (!ptr_a && !ptr_b) return true;
    if (!ptr_a || !ptr_b) return false;
    JNIEnv* env = get_env();
    if (!env) return ptr_a == ptr_b;
    return env->IsSameObject((jobject)(uintptr_t)ptr_a, (jobject)(uintptr_t)ptr_b);
}

// ── General-Purpose: Direct ByteBuffer Native Mapping ────────────────────────
// Zero-copy bridge for audio PCM, camera buffers, MediaCodec, and Bitmaps.
nb::object bytebuffer_to_memoryview(int64_t ptr) {
    if (!ptr) return nb::none();
    JNIEnv* env = get_env();
    if (!env) return nb::none();
    JniLocalFrame frame(env, 8);
    jobject bb = (jobject)(uintptr_t)ptr;

    void* addr = env->GetDirectBufferAddress(bb);
    jlong cap = addr ? env->GetDirectBufferCapacity(bb) : 0;
    if (addr && cap > 0) {
        Py_buffer view;
        if (PyBuffer_FillInfo(&view, nullptr, addr, (Py_ssize_t)cap, 0, PyBUF_WRITABLE) == -1) {
            PyErr_Clear();
            return nb::none();
        }
        PyObject* mv = PyMemoryView_FromBuffer(&view);
        if (!mv) return nb::none();
        nb::object mvo = nb::borrow(mv);
        Py_DECREF(mv);
        return mvo;
    }

    // Non-direct (heap-backed) ByteBuffer — fall back to copying its
    // backing byte[] via .array(). Without this, any non-direct buffer
    // silently returned None instead of usable data.
    jclass bcls = env->GetObjectClass(bb);
    jmethodID marr = env->GetMethodID(bcls, "array", "()[B");
    if (!marr) { env->ExceptionClear(); return nb::none(); }
    jbyteArray ba = (jbyteArray)env->CallObjectMethod(bb, marr);
    if (env->ExceptionCheck()) { env->ExceptionClear(); return nb::none(); }
    if (!ba) return nb::none();
    jsize blen = env->GetArrayLength(ba);
    jbyte* bp = env->GetByteArrayElements(ba, nullptr);
    nb::bytes result(reinterpret_cast<const char*>(bp), (size_t)blen);
    env->ReleaseByteArrayElements(ba, bp, JNI_ABORT);
    return result;
}

int64_t allocate_direct_buffer(int32_t capacity) {
    JNIEnv* env = get_env();
    if (!env || capacity <= 0) return 0;
    JniLocalFrame frame(env, 8);
    jclass bb_cls = env->FindClass("java/nio/ByteBuffer");
    if (!bb_cls) { env->ExceptionClear(); return 0; }
    jmethodID mid = env->GetStaticMethodID(bb_cls, "allocateDirect", "(I)Ljava/nio/ByteBuffer;");
    if (!mid) { env->ExceptionClear(); env->DeleteLocalRef(bb_cls); return 0; }
    jobject bb = env->CallStaticObjectMethod(bb_cls, mid, (jint)capacity);
    env->DeleteLocalRef(bb_cls);
    if (!bb) return 0;
    jobject gref = env->NewGlobalRef(bb);
    return (int64_t)(uintptr_t)gref;
}

#include <android/native_window.h>
#include <android/native_window_jni.h>

int64_t surface_to_native_window(int64_t surface_ptr) {
    if (!surface_ptr) return 0;
    JNIEnv* env = get_env();
    if (!env) return 0;
    ANativeWindow* win = ANativeWindow_fromSurface(env, (jobject)(uintptr_t)surface_ptr);
    return (int64_t)(uintptr_t)win;
}

void release_native_window(int64_t win_ptr) {
    if (win_ptr) {
        ANativeWindow_release((ANativeWindow*)(uintptr_t)win_ptr);
    }
}

bool is_instance_of(int64_t ptr, uint32_t class_id) {
    if (!ptr || class_id >= g_class_count) return false;
    JNIEnv* env = get_env();
    if (!env) return false;
    ensure_class_resolved(env, class_id);
    jclass c = g_classes[class_id].class_ref;
    if (!c) return false;
    return env->IsInstanceOf((jobject)(uintptr_t)ptr, c) != JNI_FALSE;
}

std::string object_to_string(int64_t ptr) {
    if (!ptr) return "null";
    JNIEnv* env = get_env();
    if (!env) return "";
    JniLocalFrame frame(env, 8);
    jobject obj = (jobject)(uintptr_t)ptr;
    jclass cls = env->GetObjectClass(obj);
    if (!cls) { env->ExceptionClear(); return ""; }
    jmethodID mid = env->GetMethodID(cls, "toString", "()Ljava/lang/String;");
    env->DeleteLocalRef(cls);
    if (!mid) { env->ExceptionClear(); return ""; }
    jstring js = (jstring)env->CallObjectMethod(obj, mid);
    if (env->ExceptionCheck()) { env->ExceptionClear(); return ""; }
    return stratum_jstring_to_str(env, js);
}

int32_t object_hash_code(int64_t ptr) {
    if (!ptr) return 0;
    JNIEnv* env = get_env();
    if (!env) return 0;
    JniLocalFrame frame(env, 8);
    jobject obj = (jobject)(uintptr_t)ptr;
    jclass cls = env->GetObjectClass(obj);
    if (!cls) { env->ExceptionClear(); return 0; }
    jmethodID mid = env->GetMethodID(cls, "hashCode", "()I");
    env->DeleteLocalRef(cls);
    if (!mid) { env->ExceptionClear(); return 0; }
    jint h = env->CallIntMethod(obj, mid);
    if (env->ExceptionCheck()) { env->ExceptionClear(); return 0; }
    return (int32_t)h;
}
"""

# =============================================================================
# bridge_main.cpp — nanobind entry point + Activity/lifecycle glue.
# Unchanged from v8/v8fix — no bugs identified here.
# =============================================================================
BRIDGE_MAIN_CPP = r"""// bridge_main.cpp — Stratum Auto-generated. DO NOT EDIT.
#include "bridge_core.h"
#include "metadata_table.h"
#include <nanobind/nanobind.h>
#include <nanobind/stl/string.h>
namespace nb = nanobind;

// Forward declarations — defined in stratum_engine.cpp.
void call_v(int64_t, uint32_t, uint32_t, nb::args);
bool call_z(int64_t, uint32_t, uint32_t, nb::args);
int64_t call_i(int64_t, uint32_t, uint32_t, nb::args);
int64_t call_j(int64_t, uint32_t, uint32_t, nb::args);
double call_d(int64_t, uint32_t, uint32_t, nb::args);
std::string call_str(int64_t, uint32_t, uint32_t, nb::args);
int64_t call_o(int64_t, uint32_t, uint32_t, nb::args);
nb::object call_arr(int64_t, uint32_t, uint32_t, nb::args);
nb::object call_list(int64_t, uint32_t, uint32_t, nb::args);
nb::object call_map(int64_t, uint32_t, uint32_t, nb::args);
int64_t new_instance(uint32_t, uint32_t, nb::args);
void delete_ref(int64_t);
int64_t field_get_i(int64_t, uint32_t, uint32_t);
bool field_get_z(int64_t, uint32_t, uint32_t);
int64_t field_get_j(int64_t, uint32_t, uint32_t);
double field_get_d(int64_t, uint32_t, uint32_t);
std::string field_get_str(int64_t, uint32_t, uint32_t);
int64_t field_get_o(int64_t, uint32_t, uint32_t);
void field_set_i(int64_t, uint32_t, uint32_t, int64_t);
void field_set_z(int64_t, uint32_t, uint32_t, bool);
void field_set_j(int64_t, uint32_t, uint32_t, int64_t);
void field_set_d(int64_t, uint32_t, uint32_t, double);
void field_set_str(int64_t, uint32_t, uint32_t, const std::string&);
void field_set_o(int64_t, uint32_t, uint32_t, int64_t);
bool is_same_object(int64_t, int64_t);
nb::object bytebuffer_to_memoryview(int64_t);
int64_t allocate_direct_buffer(int32_t);
int64_t surface_to_native_window(int64_t);
void release_native_window(int64_t);
bool is_instance_of(int64_t, uint32_t);
std::string object_to_string(int64_t);
int32_t object_hash_code(int64_t);

static std::unordered_map<std::string, nb::callable> g_lifecycle_cbs;
static std::mutex g_lifecycle_mutex;

static void dispatch_lifecycle(const char* name) {
    nb::callable fn;
    {
        std::lock_guard<std::mutex> lk(g_lifecycle_mutex);
        auto it = g_lifecycle_cbs.find(name);
        if (it != g_lifecycle_cbs.end()) fn = it->second;
    }
    if (fn.is_valid()) {
        nb::gil_scoped_acquire gil;
        try { fn(); } catch (const std::exception& e) { LOGE("Lifecycle %s error: %s", name, e.what()); }
    }
}

extern "C" JNIEXPORT jint JNICALL JNI_OnLoad(JavaVM* vm, void*) {
    g_jvm = vm;
    JNIEnv* env = nullptr;
    if (vm->GetEnv(reinterpret_cast<void**>(&env), JNI_VERSION_1_6) != JNI_OK) return JNI_ERR;

    jclass hcls = env->FindClass("com/stratum/runtime/StratumInvocationHandler");
    if (hcls) {
        jclass cc = env->GetObjectClass(hcls);
        jmethodID gcl = env->GetMethodID(cc, "getClassLoader", "()Ljava/lang/ClassLoader;");
        jobject loader = env->CallObjectMethod(hcls, gcl);
        g_app_class_loader = env->NewGlobalRef(loader);
        jclass lcls = env->FindClass("java/lang/ClassLoader");
        g_class_loader_loadClass_method = env->GetMethodID(lcls, "loadClass", "(Ljava/lang/String;)Ljava/lang/Class;");
        g_stratum_handler_class = (jclass)env->NewGlobalRef(hcls);
        env->DeleteLocalRef(lcls); env->DeleteLocalRef(loader); env->DeleteLocalRef(cc); env->DeleteLocalRef(hcls);
    } else {
        env->ExceptionClear();
        LOGE("JNI_OnLoad: StratumInvocationHandler not found. Ensure runtime/java files are copied into your Android Studio project.");
    }

    auto cache = [&](const char* n) -> jclass {
        jclass l = env->FindClass(n);
        if (!l) { env->ExceptionClear(); return nullptr; }
        jclass g = (jclass)env->NewGlobalRef(l); env->DeleteLocalRef(l); return g;
    };
    g_jstring_class = cache("java/lang/String");
    g_proxy_class   = cache("java/lang/reflect/Proxy");
    g_class_class   = cache("java/lang/Class");
    g_object_class  = cache("java/lang/Object");

    LOGI("Stratum engine initialized. %u classes indexed lazily.", g_class_count);
    return JNI_VERSION_1_6;
}

NB_MODULE(_stratum, m) {
    m.def("call_v", &call_v);
    m.def("call_z", &call_z);
    m.def("call_i", &call_i);
    m.def("call_j", &call_j);
    m.def("call_d", &call_d);
    m.def("call_str", &call_str);
    m.def("call_o", &call_o);
    m.def("call_arr", &call_arr);
    m.def("call_list", &call_list);
    m.def("call_map", &call_map);
    m.def("new_instance", &new_instance);
    m.def("delete_ref", &delete_ref);

    m.def("field_get_i", &field_get_i);
    m.def("field_get_z", &field_get_z);
    m.def("field_get_j", &field_get_j);
    m.def("field_get_d", &field_get_d);
    m.def("field_get_str", &field_get_str);
    m.def("field_get_o", &field_get_o);
    m.def("field_set_i", &field_set_i);
    m.def("field_set_z", &field_set_z);
    m.def("field_set_j", &field_set_j);
    m.def("field_set_d", &field_set_d);
    m.def("field_set_str", &field_set_str);
    m.def("field_set_o", &field_set_o);
    m.def("is_same_object", &is_same_object);
    m.def("is_instance_of", &is_instance_of);
    m.def("to_string", &object_to_string);
    m.def("hash_code", &object_hash_code);
    m.def("remove_callback", [](const std::string& key) { remove_callback(key); });
    m.def("remove_callbacks_by_prefix", [](const std::string& prefix) { return remove_callbacks_by_prefix(prefix); });
    m.def("stratum_callback_count", []() -> size_t { return stratum_callback_count(); });

    m.def("bytebuffer_to_memoryview", &bytebuffer_to_memoryview);
    m.def("allocate_direct_buffer", &allocate_direct_buffer);
    m.def("surface_to_native_window", &surface_to_native_window);
    m.def("release_native_window", &release_native_window);

    m.def("get_activity_ptr", []() -> int64_t {
        std::lock_guard<std::mutex> lk(g_activity_mutex);
        return (int64_t)(uintptr_t)g_activity;
    });

    m.def("set_lifecycle_callback", [](const std::string& name, nb::callable fn) {
        std::lock_guard<std::mutex> lk(g_lifecycle_mutex);
        g_lifecycle_cbs[name] = fn;
    });

    m.def("set_log_enabled", [](bool enabled) { g_log_enabled = enabled; });
    m.def("is_log_enabled",  []() -> bool { return g_log_enabled; });
    m.def("log_msg", [](const std::string& msg) { LOGD("[Python] %s", msg.c_str()); });

    m.def("set_content_view", [](int64_t act_ptr, int64_t view_ptr) {
        if (!act_ptr || !view_ptr) throw std::runtime_error("set_content_view: null activity or view");
        JNIEnv* env = get_env();
        jobject act = (jobject)(uintptr_t)act_ptr;
        jobject view = (jobject)(uintptr_t)view_ptr;
        jclass c = env->GetObjectClass(act);
        jmethodID mid = env->GetMethodID(c, "setContentView", "(Landroid/view/View;)V");
        env->DeleteLocalRef(c);
        if (mid) {
            nb::gil_scoped_release r;
            env->CallVoidMethod(act, mid, view);
        }
        stratum_check_java_exc(env);
    });
}

extern "C" JNIEXPORT void JNICALL
Java_com_stratum_runtime_StratumActivity_nativeSetActivity(JNIEnv* env, jobject, jobject act) {
    std::lock_guard<std::mutex> lk(g_activity_mutex);
    if (g_activity) env->DeleteGlobalRef(g_activity);
    g_activity = act ? env->NewGlobalRef(act) : nullptr;
}
extern "C" JNIEXPORT void JNICALL Java_com_stratum_runtime_StratumActivity_nativeOnCreate(JNIEnv*, jobject)  { dispatch_lifecycle("onCreate"); }
extern "C" JNIEXPORT void JNICALL Java_com_stratum_runtime_StratumActivity_nativeOnResume(JNIEnv*, jobject)  { dispatch_lifecycle("onResume"); }
extern "C" JNIEXPORT void JNICALL Java_com_stratum_runtime_StratumActivity_nativeOnPause(JNIEnv*, jobject)   { dispatch_lifecycle("onPause"); }
extern "C" JNIEXPORT void JNICALL Java_com_stratum_runtime_StratumActivity_nativeOnStop(JNIEnv*, jobject)    { dispatch_lifecycle("onStop"); }
extern "C" JNIEXPORT void JNICALL Java_com_stratum_runtime_StratumActivity_nativeOnDestroy(JNIEnv*, jobject) { dispatch_lifecycle("onDestroy"); }
"""


def main():
    ap = argparse.ArgumentParser(description="Stratum Stage 06 - Universal Engine Emit (v9)")
    ap.add_argument("--input", required=True,
                     help="05_resolve/output_patched/ (the SECOND-pass output — see 05_resolve/main.py docstring)")
    ap.add_argument("--output", required=True, help="06_cpp_emit/output/")
    args = ap.parse_args()

    print_header("STRATUM PIPELINE — STAGE 06 (UNIVERSAL ENGINE EMIT) v9")
    input_dir, output_dir = Path(args.input), Path(args.output)
    core_dir = output_dir / "core"
    core_dir.mkdir(parents=True, exist_ok=True)

    json_files = sorted(f for f in input_dir.rglob("*.json")
                         if f.name not in ("parse_summary.json", "resolve_summary.json", "manifest.json"))
    classes = []
    for jf in json_files:
        data = json.loads(jf.read_text(encoding="utf-8"))
        if "class_id" in data:
            classes.append(data)
    classes.sort(key=lambda c: c["class_id"])
    print(f"-> {len(classes):,} classes to index.")

    pool = StringPool()
    h_code, cpp_code = emit_metadata_table(classes, pool)

    (core_dir / "metadata_table.h").write_text(h_code, encoding="utf-8")
    (core_dir / "metadata_table.cpp").write_text(cpp_code, encoding="utf-8")
    (core_dir / "bridge_core.h").write_text(BRIDGE_CORE_H, encoding="utf-8")
    (core_dir / "bridge_core.cpp").write_text(BRIDGE_CORE_CPP, encoding="utf-8")
    (core_dir / "stratum_engine.cpp").write_text(STRATUM_ENGINE_CPP, encoding="utf-8")
    (core_dir / "bridge_main.cpp").write_text(BRIDGE_MAIN_CPP, encoding="utf-8")

    print(f"-> String pool: {len(pool.pool)/1024:.1f} KB (deduplicated).")
    print(f"-> Emitted 6 fixed engine files to: {core_dir}")
    print_header("STAGE 06 COMPLETE (v9)")


if __name__ == "__main__":
    main()