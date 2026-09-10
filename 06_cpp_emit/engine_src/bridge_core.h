// bridge_core.h
#pragma once
#include <jni.h>
#include <string>
#include <vector>
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
// STRATUM_LOG_ENABLED: one switch, not three levels.
//   1 (default) = full deep trace compiled IN: every Python->C++->Java
//                  call, every argument, every return value, every
//                  Java->C++->Python callback dispatch. Toggle at
//                  runtime with stratum.set_log_enabled(bool).
//   0            = fully stripped at compile time. LOGD/LOGV/LOGT
//                  become ((void)0) — zero cost, nothing left in the
//                  binary. set_log_enabled() still EXISTS (so calling
//                  it never crashes) but is a documented no-op.
#ifndef STRATUM_LOG_ENABLED
#define STRATUM_LOG_ENABLED 1
#endif

extern bool g_log_enabled;
// True only if this .so was actually compiled with logging support.
// Exposed to Python so set_log_enabled() can tell the caller it had
// no effect, instead of pretending it worked.
extern const bool g_log_build_supported;

#include <android/log.h>
#define LOGE(...) __android_log_print(ANDROID_LOG_ERROR, "Stratum", __VA_ARGS__)
#define LOGW(...) __android_log_print(ANDROID_LOG_WARN,  "Stratum", __VA_ARGS__)
#define LOGI(...) __android_log_print(ANDROID_LOG_INFO,  "Stratum", __VA_ARGS__)

#if STRATUM_LOG_ENABLED
  #define LOGD(...) do { if (g_log_enabled) __android_log_print(ANDROID_LOG_DEBUG,   "Stratum", __VA_ARGS__); } while(0)
  #define LOGV(...) do { if (g_log_enabled) __android_log_print(ANDROID_LOG_VERBOSE, "Stratum", __VA_ARGS__); } while(0)
  // LOGT = "trace" — for the deep data-marshalling dumps (every arg
  // value, every return value crossing the bridge). Separate macro so
  // it reads clearly at call sites; same runtime gate as LOGV.
  #define LOGT(...) do { if (g_log_enabled) __android_log_print(ANDROID_LOG_VERBOSE, "StratumTrace", __VA_ARGS__); } while(0)
#else
  #define LOGD(...) ((void)0)
  #define LOGV(...) ((void)0)
  #define LOGT(...) ((void)0)
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
void rekey_callback(const std::string& old_key, const std::string& new_key);

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

// Bidirectional Data Bridge (safe recursive serialization between Python and Java)
jobject      stratum_py_to_java(JNIEnv* env, nb::handle obj, int depth = 0);
nb::object   stratum_java_to_py(JNIEnv* env, jobject obj, int depth = 0);

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