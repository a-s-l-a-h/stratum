//bridge_main.cpp
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