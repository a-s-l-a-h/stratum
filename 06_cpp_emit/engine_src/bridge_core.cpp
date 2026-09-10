//bridge_core.cpp
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

void rekey_callback(const std::string& old_key, const std::string& new_key) {
    std::lock_guard<std::mutex> lock(g_callback_mutex);
    auto it = g_callbacks.find(old_key);
    if (it != g_callbacks.end()) {
        g_callbacks[new_key] = it->second;
        g_callbacks.erase(it);
    }
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

    std::shared_ptr<nb::callable> fn_ptr;
    {
        std::lock_guard<std::mutex> lock(g_callback_mutex);
        auto it = g_callbacks.find(routed_key);
        if (it == g_callbacks.end()) it = g_callbacks.find(base_key);
        if (it != g_callbacks.end()) fn_ptr = it->second;
    }
    if (!fn_ptr) { LOGW("nativeDispatch: no callback bound for %s", routed_key.c_str()); return nullptr; }

    nb::gil_scoped_acquire acquire;
    // Only touch the callable's refcount now that the GIL is actually held.
    nb::callable fn = *fn_ptr;
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