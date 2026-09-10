//bridge_core.cpp
#include "bridge_core.h"
#include <pthread.h>

bool g_log_enabled = true;  // default ON, per spec — deep trace from first launch
#if STRATUM_LOG_ENABLED
const bool g_log_build_supported = true;
#else
const bool g_log_build_supported = false;
#endif

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
    // [Patch 14] Resolve getKey/getValue once via the Map.Entry interface
    // instead of once-per-entry via GetObjectClass — method IDs obtained
    // from an interface class remain valid for virtual calls on any
    // implementing instance, so this is safe regardless of the concrete
    // entry class.
    jclass entry_iface = find_class(env, "java/util/Map$Entry");
    jmethodID mkey = entry_iface ? env->GetMethodID(entry_iface, "getKey", "()Ljava/lang/Object;") : nullptr;
    jmethodID mval = entry_iface ? env->GetMethodID(entry_iface, "getValue", "()Ljava/lang/Object;") : nullptr;
    if (entry_iface) env->DeleteLocalRef(entry_iface);
    while (mhn && mnx && mkey && mval && env->CallBooleanMethod(iter, mhn)) {
        jobject entry = env->CallObjectMethod(iter, mnx);
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

// ── Bidirectional Data Serialization Bridge ─────────────────────────────────

static constexpr int STRATUM_MAX_RECURSION_DEPTH = 64;

jobject stratum_py_to_java(JNIEnv* env, nb::handle obj, int depth) {
    if (!env || obj.is_none()) return nullptr;
    if (depth > STRATUM_MAX_RECURSION_DEPTH) {
        LOGE("Stratum: maximum serialization depth exceeded in stratum_py_to_java");
        return nullptr;
    }

    // 1. Existing Java Object reference: return a local reference to avoid mutating GlobalRefs
    if (nb::hasattr(obj, "_ptr")) {
        int64_t p = nb::cast<int64_t>(obj.attr("_ptr"));
        if (!p) return nullptr;
        return env->NewLocalRef((jobject)(uintptr_t)p);
    }

    // 2. Boolean (must precede int_ check because bool inherits from int in Python)
    if (nb::isinstance<nb::bool_>(obj)) {
        static jclass s_bool_cls = nullptr;
        static jmethodID s_bool_valueOf = nullptr;
        if (!s_bool_cls) {
            jclass c = find_class(env, "java/lang/Boolean");
            if (c) {
                jmethodID mid = env->GetStaticMethodID(c, "valueOf", "(Z)Ljava/lang/Boolean;");
                if (mid) {
                    s_bool_valueOf = mid;
                    s_bool_cls = (jclass)env->NewGlobalRef(c);
                }
                env->DeleteLocalRef(c);
            }
        }
        if (s_bool_cls && s_bool_valueOf) {
            return env->CallStaticObjectMethod(s_bool_cls, s_bool_valueOf, nb::cast<bool>(obj) ? JNI_TRUE : JNI_FALSE);
        }
        return nullptr;
    }

    // 3. Integer: Box into Integer (or Long if exceeding 32-bit range)
    if (nb::isinstance<nb::int_>(obj)) {
        int64_t v = nb::cast<int64_t>(obj);
        if (v >= -2147483648LL && v <= 2147483647LL) {
            static jclass s_int_cls = nullptr;
            static jmethodID s_int_valueOf = nullptr;
            if (!s_int_cls) {
                jclass c = find_class(env, "java/lang/Integer");
                if (c) {
                    jmethodID mid = env->GetStaticMethodID(c, "valueOf", "(I)Ljava/lang/Integer;");
                    if (mid) {
                        s_int_valueOf = mid;
                        s_int_cls = (jclass)env->NewGlobalRef(c);
                    }
                    env->DeleteLocalRef(c);
                }
            }
            if (s_int_cls && s_int_valueOf) {
                return env->CallStaticObjectMethod(s_int_cls, s_int_valueOf, (jint)v);
            }
        } else {
            static jclass s_long_cls = nullptr;
            static jmethodID s_long_valueOf = nullptr;
            if (!s_long_cls) {
                jclass c = find_class(env, "java/lang/Long");
                if (c) {
                    jmethodID mid = env->GetStaticMethodID(c, "valueOf", "(J)Ljava/lang/Long;");
                    if (mid) {
                        s_long_valueOf = mid;
                        s_long_cls = (jclass)env->NewGlobalRef(c);
                    }
                    env->DeleteLocalRef(c);
                }
            }
            if (s_long_cls && s_long_valueOf) {
                return env->CallStaticObjectMethod(s_long_cls, s_long_valueOf, (jlong)v);
            }
        }
        return nullptr;
    }

    // 4. Float -> Double
    if (nb::isinstance<nb::float_>(obj)) {
        static jclass s_dbl_cls = nullptr;
        static jmethodID s_dbl_valueOf = nullptr;
        if (!s_dbl_cls) {
            jclass c = find_class(env, "java/lang/Double");
            if (c) {
                jmethodID mid = env->GetStaticMethodID(c, "valueOf", "(D)Ljava/lang/Double;");
                if (mid) {
                    s_dbl_valueOf = mid;
                    s_dbl_cls = (jclass)env->NewGlobalRef(c);
                }
                env->DeleteLocalRef(c);
            }
        }
        if (s_dbl_cls && s_dbl_valueOf) {
            return env->CallStaticObjectMethod(s_dbl_cls, s_dbl_valueOf, (jdouble)nb::cast<double>(obj));
        }
        return nullptr;
    }

    // 5. String
    if (nb::isinstance<nb::str>(obj)) {
        return (jobject)stratum_str_to_jstring(env, nb::cast<std::string>(obj));
    }

    // 6. Bytes -> byte[]
    if (nb::isinstance<nb::bytes>(obj)) {
        nb::bytes b = nb::cast<nb::bytes>(obj);
        jsize len = (jsize)b.size();
        jbyteArray ja = env->NewByteArray(len);
        if (len > 0) env->SetByteArrayRegion(ja, 0, len, reinterpret_cast<const jbyte*>(b.c_str()));
        return (jobject)ja;
    }

    // 7. Dictionary -> HashMap (with sub-frame protection against local ref overflow)
    if (nb::isinstance<nb::dict>(obj)) {
        static jclass s_map_cls = nullptr;
        static jmethodID s_map_init = nullptr;
        static jmethodID s_map_put = nullptr;
        if (!s_map_cls) {
            jclass c = find_class(env, "java/util/HashMap");
            if (c) {
                jmethodID init = env->GetMethodID(c, "<init>", "()V");
                jmethodID put  = env->GetMethodID(c, "put", "(Ljava/lang/Object;Ljava/lang/Object;)Ljava/lang/Object;");
                if (init && put) {
                    s_map_init = init;
                    s_map_put  = put;
                    s_map_cls  = (jclass)env->NewGlobalRef(c);
                }
                env->DeleteLocalRef(c);
            }
        }
        if (!s_map_cls || !s_map_init || !s_map_put) return nullptr;

        jobject jmap = env->NewObject(s_map_cls, s_map_init);
        nb::dict d = nb::cast<nb::dict>(obj);
        for (auto kv : d) {
            JniLocalFrame loop_frame(env, 16);
            jobject jk = stratum_py_to_java(env, kv.first, depth + 1);
            jobject jv = stratum_py_to_java(env, kv.second, depth + 1);
            jobject prev = env->CallObjectMethod(jmap, s_map_put, jk, jv);
            if (prev) env->DeleteLocalRef(prev);
            if (env->ExceptionCheck()) { env->ExceptionClear(); break; }
        }
        return jmap;
    }

    // 8. List / Tuple / Set -> ArrayList (with sub-frame protection against local ref overflow)
    if (nb::isinstance<nb::list>(obj) || nb::isinstance<nb::tuple>(obj) || nb::isinstance<nb::set>(obj)) {
        static jclass s_list_cls = nullptr;
        static jmethodID s_list_init = nullptr;
        static jmethodID s_list_add = nullptr;
        if (!s_list_cls) {
            jclass c = find_class(env, "java/util/ArrayList");
            if (c) {
                jmethodID init = env->GetMethodID(c, "<init>", "()V");
                jmethodID add  = env->GetMethodID(c, "add", "(Ljava/lang/Object;)Z");
                if (init && add) {
                    s_list_init = init;
                    s_list_add  = add;
                    s_list_cls  = (jclass)env->NewGlobalRef(c);
                }
                env->DeleteLocalRef(c);
            }
        }
        if (!s_list_cls || !s_list_init || !s_list_add) return nullptr;

        jobject jlist = env->NewObject(s_list_cls, s_list_init);
        for (auto item : obj) {
            JniLocalFrame loop_frame(env, 16);
            jobject ji = stratum_py_to_java(env, item, depth + 1);
            env->CallBooleanMethod(jlist, s_list_add, ji);
            if (env->ExceptionCheck()) { env->ExceptionClear(); break; }
        }
        return jlist;
    }

    return nullptr;
}

nb::object stratum_java_to_py(JNIEnv* env, jobject obj, int depth) {
    if (!env || !obj) return nb::none();
    if (depth > STRATUM_MAX_RECURSION_DEPTH) {
        LOGE("Stratum: maximum serialization depth exceeded in stratum_java_to_py");
        return nb::none();
    }
    JniLocalFrame frame(env, 32);

    // 1. String / CharSequence
    if (g_jstring_class && env->IsInstanceOf(obj, g_jstring_class)) {
        return nb::str(stratum_jstring_to_str(env, obj).c_str());
    }

    // 2. Boolean
    static jclass s_bool_w = nullptr;
    static jmethodID s_bool_val = nullptr;
    if (!s_bool_w) {
        jclass c = find_class(env, "java/lang/Boolean");
        if (c) {
            jmethodID mid = env->GetMethodID(c, "booleanValue", "()Z");
            if (mid) {
                s_bool_val = mid;
                s_bool_w = (jclass)env->NewGlobalRef(c);
            }
            env->DeleteLocalRef(c);
        }
    }
    if (s_bool_w && env->IsInstanceOf(obj, s_bool_w)) {
        return nb::bool_(env->CallBooleanMethod(obj, s_bool_val) != JNI_FALSE);
    }

    // 3. Character (Java Character extends Object, not Number)
    static jclass s_char_w = nullptr;
    static jmethodID s_char_val = nullptr;
    if (!s_char_w) {
        jclass c = find_class(env, "java/lang/Character");
        if (c) {
            jmethodID mid = env->GetMethodID(c, "charValue", "()C");
            if (mid) {
                s_char_val = mid;
                s_char_w = (jclass)env->NewGlobalRef(c);
            }
            env->DeleteLocalRef(c);
        }
    }
    if (s_char_w && env->IsInstanceOf(obj, s_char_w)) {
        jchar jc = env->CallCharMethod(obj, s_char_val);
        std::string utf8;
        if (jc < 0x80) utf8 += (char)jc;
        else if (jc < 0x800) { utf8 += (char)(0xC0 | (jc >> 6)); utf8 += (char)(0x80 | (jc & 0x3F)); }
        else { utf8 += (char)(0xE0 | (jc >> 12)); utf8 += (char)(0x80 | ((jc >> 6) & 0x3F)); utf8 += (char)(0x80 | (jc & 0x3F)); }
        return nb::str(utf8.data(), utf8.size());
    }

    // 4. Number (Integer, Long, Byte, Short, Float, Double)
    static jclass s_num_w = nullptr;
    static jmethodID s_num_double = nullptr;
    static jmethodID s_num_long = nullptr;
    static jclass s_flt_w = nullptr;
    static jclass s_dbl_w = nullptr;
    if (!s_num_w) {
        jclass c = find_class(env, "java/lang/Number");
        if (c) {
            s_num_double = env->GetMethodID(c, "doubleValue", "()D");
            s_num_long   = env->GetMethodID(c, "longValue", "()J");
            s_num_w = (jclass)env->NewGlobalRef(c);
            env->DeleteLocalRef(c);
        }
        jclass cf = find_class(env, "java/lang/Float");
        if (cf) {
            s_flt_w = (jclass)env->NewGlobalRef(cf);
            env->DeleteLocalRef(cf);
        }
        jclass cd = find_class(env, "java/lang/Double");
        if (cd) {
            s_dbl_w = (jclass)env->NewGlobalRef(cd);
            env->DeleteLocalRef(cd);
        }
    }
    if (s_num_w && env->IsInstanceOf(obj, s_num_w)) {
        if ((s_flt_w && env->IsInstanceOf(obj, s_flt_w)) || (s_dbl_w && env->IsInstanceOf(obj, s_dbl_w))) {
            return nb::float_(env->CallDoubleMethod(obj, s_num_double));
        } else {
            return nb::int_((int64_t)env->CallLongMethod(obj, s_num_long));
        }
    }

    // 5. Primitive and Object Arrays
    jclass obj_cls = env->GetObjectClass(obj);
    jclass cls_cls = g_class_class ? g_class_class : env->FindClass("java/lang/Class");
    jmethodID mid_isArray = cls_cls ? env->GetMethodID(cls_cls, "isArray", "()Z") : nullptr;
    bool is_array = (mid_isArray && env->CallBooleanMethod(obj_cls, mid_isArray));
    if (!g_class_class && cls_cls) env->DeleteLocalRef(cls_cls);

    if (is_array) {
        static jclass s_ba_cls = nullptr, s_ia_cls = nullptr, s_fa_cls = nullptr,
                      s_ja_cls = nullptr, s_da_cls = nullptr, s_za_cls = nullptr,
                      s_ca_cls = nullptr, s_sa_cls = nullptr, s_oa_cls = nullptr;
        if (!s_ba_cls) {
            auto cache_arr = [&](const char* sig) -> jclass {
                jclass c = env->FindClass(sig);
                if (!c) { env->ExceptionClear(); return nullptr; }
                jclass g = (jclass)env->NewGlobalRef(c);
                env->DeleteLocalRef(c);
                return g;
            };
            s_ba_cls = cache_arr("[B");
            s_ia_cls = cache_arr("[I");
            s_fa_cls = cache_arr("[F");
            s_ja_cls = cache_arr("[J");
            s_da_cls = cache_arr("[D");
            s_za_cls = cache_arr("[Z");
            s_ca_cls = cache_arr("[C");
            s_sa_cls = cache_arr("[S");
            s_oa_cls = cache_arr("[Ljava/lang/Object;");
        }

        // byte[] -> bytes
        if (s_ba_cls && env->IsInstanceOf(obj, s_ba_cls)) {
            env->DeleteLocalRef(obj_cls);
            jbyteArray ba = (jbyteArray)obj;
            jsize len = env->GetArrayLength(ba);
            jbyte* buf = env->GetByteArrayElements(ba, nullptr);
            nb::bytes out(reinterpret_cast<const char*>(buf), (size_t)len);
            env->ReleaseByteArrayElements(ba, buf, JNI_ABORT);
            return out;
        }

        // int[] -> list[int]
        if (s_ia_cls && env->IsInstanceOf(obj, s_ia_cls)) {
            env->DeleteLocalRef(obj_cls);
            jintArray ia = (jintArray)obj;
            jsize len = env->GetArrayLength(ia);
            std::vector<jint> buf(len);
            if (len > 0) env->GetIntArrayRegion(ia, 0, len, buf.data());
            nb::list out;
            for (jsize i = 0; i < len; ++i) out.append(nb::int_((int64_t)buf[i]));
            return out;
        }

        // float[] -> list[float]
        if (s_fa_cls && env->IsInstanceOf(obj, s_fa_cls)) {
            env->DeleteLocalRef(obj_cls);
            jfloatArray fa = (jfloatArray)obj;
            jsize len = env->GetArrayLength(fa);
            std::vector<jfloat> buf(len);
            if (len > 0) env->GetFloatArrayRegion(fa, 0, len, buf.data());
            nb::list out;
            for (jsize i = 0; i < len; ++i) out.append(nb::float_((double)buf[i]));
            return out;
        }

        // long[] -> list[int]
        if (s_ja_cls && env->IsInstanceOf(obj, s_ja_cls)) {
            env->DeleteLocalRef(obj_cls);
            jlongArray ja = (jlongArray)obj;
            jsize len = env->GetArrayLength(ja);
            std::vector<jlong> buf(len);
            if (len > 0) env->GetLongArrayRegion(ja, 0, len, buf.data());
            nb::list out;
            for (jsize i = 0; i < len; ++i) out.append(nb::int_((int64_t)buf[i]));
            return out;
        }

        // double[] -> list[float]
        if (s_da_cls && env->IsInstanceOf(obj, s_da_cls)) {
            env->DeleteLocalRef(obj_cls);
            jdoubleArray da = (jdoubleArray)obj;
            jsize len = env->GetArrayLength(da);
            std::vector<jdouble> buf(len);
            if (len > 0) env->GetDoubleArrayRegion(da, 0, len, buf.data());
            nb::list out;
            for (jsize i = 0; i < len; ++i) out.append(nb::float_(buf[i]));
            return out;
        }

        // boolean[] -> list[bool]
        if (s_za_cls && env->IsInstanceOf(obj, s_za_cls)) {
            env->DeleteLocalRef(obj_cls);
            jbooleanArray za = (jbooleanArray)obj;
            jsize len = env->GetArrayLength(za);
            std::vector<jboolean> buf(len);
            if (len > 0) env->GetBooleanArrayRegion(za, 0, len, buf.data());
            nb::list out;
            for (jsize i = 0; i < len; ++i) out.append(nb::bool_(buf[i] != JNI_FALSE));
            return out;
        }

        // char[] -> str
        if (s_ca_cls && env->IsInstanceOf(obj, s_ca_cls)) {
            env->DeleteLocalRef(obj_cls);
            jcharArray ca = (jcharArray)obj;
            jsize len = env->GetArrayLength(ca);
            std::vector<jchar> buf(len);
            if (len > 0) env->GetCharArrayRegion(ca, 0, len, buf.data());
            std::string utf8;
            for (jsize i = 0; i < len; ) {
                uint32_t cp; uint16_t c1 = (uint16_t)buf[i++];
                if (c1 >= 0xD800 && c1 <= 0xDBFF && i < len) {
                    uint16_t c2 = (uint16_t)buf[i];
                    if (c2 >= 0xDC00 && c2 <= 0xDFFF) { cp = 0x10000u + (((uint32_t)(c1 - 0xD800u)) << 10) + (uint32_t)(c2 - 0xDC00u); ++i; }
                    else cp = c1;
                } else cp = c1;
                if (cp < 0x80) utf8 += (char)cp;
                else if (cp < 0x800) { utf8 += (char)(0xC0 | (cp >> 6)); utf8 += (char)(0x80 | (cp & 0x3F)); }
                else if (cp < 0x10000) { utf8 += (char)(0xE0 | (cp >> 12)); utf8 += (char)(0x80 | ((cp >> 6) & 0x3F)); utf8 += (char)(0x80 | (cp & 0x3F)); }
                else { utf8 += (char)(0xF0 | (cp >> 18)); utf8 += (char)(0x80 | ((cp >> 12) & 0x3F)); utf8 += (char)(0x80 | ((cp >> 6) & 0x3F)); utf8 += (char)(0x80 | (cp & 0x3F)); }
            }
            return nb::str(utf8.data(), utf8.size());
        }

        // short[] -> list[int]
        if (s_sa_cls && env->IsInstanceOf(obj, s_sa_cls)) {
            env->DeleteLocalRef(obj_cls);
            jshortArray sa = (jshortArray)obj;
            jsize len = env->GetArrayLength(sa);
            std::vector<jshort> buf(len);
            if (len > 0) env->GetShortArrayRegion(sa, 0, len, buf.data());
            nb::list out;
            for (jsize i = 0; i < len; ++i) out.append(nb::int_((int64_t)buf[i]));
            return out;
        }

        // Object[] -> recursive list
        if (s_oa_cls && env->IsInstanceOf(obj, s_oa_cls)) {
            env->DeleteLocalRef(obj_cls);
            jsize len = env->GetArrayLength((jarray)obj);
            nb::list py_arr;
            for (jsize i = 0; i < len; ++i) {
                JniLocalFrame item_frame(env, 16);
                jobject elem = env->GetObjectArrayElement((jobjectArray)obj, i);
                py_arr.append(stratum_java_to_py(env, elem, depth + 1));
            }
            return py_arr;
        }
        env->DeleteLocalRef(obj_cls);
    } else {
        env->DeleteLocalRef(obj_cls);
    }

    // 6. Map -> dict (recursive with concurrent modification safety)
    static jclass s_map_iface = nullptr;
    static jmethodID s_map_entrySet = nullptr;
    static jclass s_entry_iface = nullptr;
    static jmethodID s_entry_getKey = nullptr;
    static jmethodID s_entry_getVal = nullptr;
    if (!s_map_iface) {
        jclass c = find_class(env, "java/util/Map");
        if (c) {
            s_map_entrySet = env->GetMethodID(c, "entrySet", "()Ljava/util/Set;");
            s_map_iface = (jclass)env->NewGlobalRef(c);
            env->DeleteLocalRef(c);
        }
        jclass ec = find_class(env, "java/util/Map$Entry");
        if (ec) {
            s_entry_getKey = env->GetMethodID(ec, "getKey", "()Ljava/lang/Object;");
            s_entry_getVal = env->GetMethodID(ec, "getValue", "()Ljava/lang/Object;");
            s_entry_iface = (jclass)env->NewGlobalRef(ec);
            env->DeleteLocalRef(ec);
        }
    }
    if (s_map_iface && env->IsInstanceOf(obj, s_map_iface)) {
        jobject es = env->CallObjectMethod(obj, s_map_entrySet);
        nb::dict result;
        if (es) {
            jclass escls = env->GetObjectClass(es);
            jmethodID esit = env->GetMethodID(escls, "iterator", "()Ljava/util/Iterator;");
            env->DeleteLocalRef(escls);
            jobject iter = env->CallObjectMethod(es, esit);
            env->DeleteLocalRef(es);
            if (iter) {
                jclass ic = env->GetObjectClass(iter);
                jmethodID mhn = env->GetMethodID(ic, "hasNext", "()Z");
                jmethodID mnx = env->GetMethodID(ic, "next", "()Ljava/lang/Object;");
                env->DeleteLocalRef(ic);
                while (mhn && mnx && env->CallBooleanMethod(iter, mhn)) {
                    if (env->ExceptionCheck()) { env->ExceptionClear(); break; }
                    JniLocalFrame item_frame(env, 16);
                    jobject entry = env->CallObjectMethod(iter, mnx);
                    if (env->ExceptionCheck()) { env->ExceptionClear(); break; }
                    if (entry) {
                        jobject ek = env->CallObjectMethod(entry, s_entry_getKey);
                        jobject ev = env->CallObjectMethod(entry, s_entry_getVal);
                        result[stratum_java_to_py(env, ek, depth + 1)] = stratum_java_to_py(env, ev, depth + 1);
                    }
                }
                env->DeleteLocalRef(iter);
            }
        }
        return result;
    }

    // 7. Collection / List / Set -> list (recursive with exception safety)
    static jclass s_coll_iface = nullptr;
    static jmethodID s_coll_iterator = nullptr;
    if (!s_coll_iface) {
        jclass c = find_class(env, "java/util/Collection");
        if (c) {
            s_coll_iterator = env->GetMethodID(c, "iterator", "()Ljava/util/Iterator;");
            s_coll_iface = (jclass)env->NewGlobalRef(c);
            env->DeleteLocalRef(c);
        }
    }
    if (s_coll_iface && env->IsInstanceOf(obj, s_coll_iface)) {
        jobject iter = env->CallObjectMethod(obj, s_coll_iterator);
        nb::list result;
        if (iter) {
            jclass ic = env->GetObjectClass(iter);
            jmethodID mhn = env->GetMethodID(ic, "hasNext", "()Z");
            jmethodID mnx = env->GetMethodID(ic, "next", "()Ljava/lang/Object;");
            env->DeleteLocalRef(ic);
            while (mhn && mnx && env->CallBooleanMethod(iter, mhn)) {
                if (env->ExceptionCheck()) { env->ExceptionClear(); break; }
                JniLocalFrame item_frame(env, 16);
                jobject item = env->CallObjectMethod(iter, mnx);
                if (env->ExceptionCheck()) { env->ExceptionClear(); break; }
                result.append(stratum_java_to_py(env, item, depth + 1));
            }
            env->DeleteLocalRef(iter);
        }
        return result;
    }

    // 8. android.os.BaseBundle / Bundle -> dict (recursive with exception safety)
    static jclass s_bundle_cls = nullptr;
    static jmethodID s_bundle_keySet = nullptr;
    static jmethodID s_bundle_get = nullptr;
    static bool s_bundle_checked = false;
    if (!s_bundle_checked) {
        s_bundle_checked = true;
        jclass c = find_class(env, "android/os/BaseBundle");
        if (!c) { env->ExceptionClear(); c = find_class(env, "android/os/Bundle"); }
        if (c) {
            s_bundle_keySet = env->GetMethodID(c, "keySet", "()Ljava/util/Set;");
            if (!s_bundle_keySet) env->ExceptionClear();
            s_bundle_get = env->GetMethodID(c, "get", "(Ljava/lang/String;)Ljava/lang/Object;");
            if (!s_bundle_get) env->ExceptionClear();
            s_bundle_cls = (jclass)env->NewGlobalRef(c);
            env->DeleteLocalRef(c);
        }
    }
    if (s_bundle_cls && s_bundle_keySet && s_bundle_get && env->IsInstanceOf(obj, s_bundle_cls)) {
        jobject keyset = env->CallObjectMethod(obj, s_bundle_keySet);
        nb::dict result;
        if (keyset) {
            jclass kcls = env->GetObjectClass(keyset);
            jmethodID kit = env->GetMethodID(kcls, "iterator", "()Ljava/util/Iterator;");
            env->DeleteLocalRef(kcls);
            jobject iter = env->CallObjectMethod(keyset, kit);
            env->DeleteLocalRef(keyset);
            if (iter) {
                jclass ic = env->GetObjectClass(iter);
                jmethodID mhn = env->GetMethodID(ic, "hasNext", "()Z");
                jmethodID mnx = env->GetMethodID(ic, "next", "()Ljava/lang/Object;");
                env->DeleteLocalRef(ic);
                while (mhn && mnx && env->CallBooleanMethod(iter, mhn)) {
                    if (env->ExceptionCheck()) { env->ExceptionClear(); break; }
                    JniLocalFrame item_frame(env, 16);
                    jstring k = (jstring)env->CallObjectMethod(iter, mnx);
                    if (env->ExceptionCheck()) { env->ExceptionClear(); break; }
                    if (k) {
                        jobject v = env->CallObjectMethod(obj, s_bundle_get, k);
                        if (env->ExceptionCheck()) { env->ExceptionClear(); v = nullptr; }
                        std::string k_str = stratum_jstring_to_str(env, k);
                        result[nb::str(k_str.c_str())] = stratum_java_to_py(env, v, depth + 1);
                    }
                }
                env->DeleteLocalRef(iter);
            }
        }
        return result;
    }

    // 9. Non-data Java Object: wrap into typed StratumObject (100% leak-proof)
    jobject gref = env->NewGlobalRef(obj);
    int64_t gref_ptr = (int64_t)(uintptr_t)gref;
    try {
        nb::object wrap_fn = nb::module_::import_("stratum.core.stratum_object").attr("_wrap_instance");
        jclass cls = env->GetObjectClass(obj);
        jclass ccls = g_class_class ? g_class_class : env->FindClass("java/lang/Class");
        jmethodID mid_getName = ccls ? env->GetMethodID(ccls, "getName", "()Ljava/lang/String;") : nullptr;
        jstring jname = (mid_getName && cls) ? (jstring)env->CallObjectMethod(cls, mid_getName) : nullptr;
        std::string class_name = jname ? stratum_jstring_to_str(env, jname) : "java.lang.Object";
        if (!g_class_class && ccls) env->DeleteLocalRef(ccls);
        if (cls) env->DeleteLocalRef(cls);
        return wrap_fn(gref_ptr, class_name.c_str());
    } catch (nb::python_error& e) {
        e.restore();
        PyErr_Clear();
        env->DeleteGlobalRef(gref);
        return nb::none();
    } catch (...) {
        PyErr_Clear();
        env->DeleteGlobalRef(gref);
        return nb::none();
    }
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
    LOGT(">> JAVA->PY DISPATCH key='%s' argc=%d", routed_key.c_str(), args ? env->GetArrayLength(args) : 0);

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
        jobject rv = (jobject)(uintptr_t)nb::cast<int64_t>(py_result.attr("_ptr"));
        LOGT("<< PY->JAVA DISPATCH key='%s' returned object ptr=%p", routed_key.c_str(), (void*)rv);
        return rv;
    }
    LOGT("<< PY->JAVA DISPATCH key='%s' returned null/void", routed_key.c_str());
    return nullptr;
}