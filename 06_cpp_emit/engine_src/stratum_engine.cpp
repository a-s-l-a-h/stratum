//stratum_engine.cpp
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
static thread_local std::vector<std::string>* t_ctor_callback_keys = nullptr;
static std::recursive_mutex g_resolve_mutex;
static inline const char* get_str(uint32_t off) { return &g_str_pool[off]; }

// [Patch 12] Accept list OR tuple for every array-typed argument.
// PySequence_List handles both uniformly without needing separate code
// paths for nb::list vs nb::tuple.
static inline nb::list stratum_to_list(nb::handle item) {
    PyObject* seq = PySequence_List(item.ptr());
    if (!seq) { PyErr_Clear(); return nb::list(); }
    return nb::steal<nb::list>(seq);
}

// [Patch 12] Accept bytes, bytearray, or memoryview for byte[] params via
// the buffer protocol (covers bytearray/memoryview which bytes-only
// isinstance checks silently dropped to null before).
static inline bool stratum_get_buffer(nb::handle item, const char** out_ptr,
                                       Py_ssize_t* out_len, Py_buffer* view,
                                       bool* needs_release) {
    *needs_release = false;
    if (nb::isinstance<nb::bytes>(item)) {
        nb::bytes b = nb::cast<nb::bytes>(item);
        *out_ptr = b.c_str();
        *out_len = (Py_ssize_t)b.size();
        return true;
    }
    if (PyObject_CheckBuffer(item.ptr())) {
        if (PyObject_GetBuffer(item.ptr(), view, PyBUF_SIMPLE) == 0) {
            *out_ptr = (const char*)view->buf;
            *out_len = view->len;
            *needs_release = true;
            return true;
        }
        PyErr_Clear();
    }
    return false;
}

// Resolves ALL method/field IDs for one class, exactly once, the first
// time any of its methods/fields is touched. This is what makes
// class-to-class dependencies a non-issue: nothing is pre-linked,
// nothing is eager, nothing can fail to compile because of a missing
// dependency (there IS no per-class C++ TYPE anymore).
static void resolve_class_slots(JNIEnv* env, uint32_t class_id) {
    if (class_id >= g_class_count) throw std::runtime_error("Stratum: class_id out of range");
    std::lock_guard<std::recursive_mutex> lock(g_resolve_mutex);
    ClassMeta& cls = g_classes[class_id];
    if (cls.resolved.load(std::memory_order_acquire)) return;

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
    cls.resolved.store(true, std::memory_order_release);
}

// ── v9 FIX 3 + FIX 4: full tag coverage, bounded, bounds-checked ────────
// Converts a Python nb::args tuple into a jvalue[] array according to
// the method's param_tags string (see 05_resolve/main.py
// compute_param_tags() — every tag emitted there has a case here).
static inline void pack_arguments(JNIEnv* env, const char* tags, const MethodMeta& mm,
                                   nb::args& args, jvalue* jargs, std::vector<jobject>& locals,
                                   int64_t caller_ptr = 0) {
    size_t n = nb::len(args);

    // Automatic Java varargs packing. Was 'A'/'T' only (Object.../
    // String...); [Patch 16] extends the same heuristic to primitive
    // vararg arrays ([I, [F, [J, [D, [Z, [C, [S) so calls like
    // ValueAnimator.ofInt(0, 100) don't throw an argument-count mismatch.
    nb::list auto_packed_args;
    bool is_varargs = false;
    if (mm.param_count > 0) {
        char last_tag = tags[mm.param_count - 1];
        static const std::string kVarargsArrayTags = "[]qfdbch";
        bool last_is_array_tag = (last_tag == 'A' || last_tag == 'T' ||
                                   kVarargsArrayTags.find(last_tag) != std::string::npos);
        if (last_is_array_tag) {
            bool last_arg_already_array = (n > 0) &&
                (nb::isinstance<nb::list>(args[n - 1]) ||
                 nb::isinstance<nb::tuple>(args[n - 1]) ||
                 nb::isinstance<nb::bytes>(args[n - 1]) ||
                 args[n - 1].is_none());
            if (n != (size_t)mm.param_count || (n > 0 && !last_arg_already_array)) {
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
            case 'I': {
                int64_t v = nb::cast<int64_t>(item);
                if (v < -2147483648LL || v > 4294967295LL) {
                    throw std::runtime_error(
                        "Stratum: value " + std::to_string(v) +
                        " exceeds 32-bit integer limits (did you mean to pass a 64-bit long?)");
                }
                jargs[i].i = (jint)(int32_t)(uint32_t)v;
                break;
            }
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
                else if (nb::hasattr(item, "_ptr")) {
                    jargs[i].l = (jobject)(uintptr_t)nb::cast<int64_t>(item.attr("_ptr"));
                } else {
                    Py_buffer view{}; bool needs_release = false;
                    const char* data = nullptr; Py_ssize_t len = 0;
                    if (stratum_get_buffer(item, &data, &len, &view, &needs_release)) {
                        jbyteArray ja = env->NewByteArray((jsize)len);
                        if (!ja) {
                            if (needs_release) PyBuffer_Release(&view);
                            stratum_check_java_exc(env);
                            throw std::runtime_error("Stratum: OOM allocating byte[]");
                        }
                        if (len > 0) env->SetByteArrayRegion(ja, 0, (jsize)len, reinterpret_cast<const jbyte*>(data));
                        jargs[i].l = ja; locals.push_back(ja);
                        if (needs_release) PyBuffer_Release(&view);
                    } else {
                        jargs[i].l = nullptr;
                    }
                }
                break;
            }

            // ── v9 FIX 3: int[] from Python list[int] ───────────────────
            case ']': {
                if (item.is_none()) jargs[i].l = nullptr;
                else if (nb::isinstance<nb::list>(item) || nb::isinstance<nb::tuple>(item)) {
                    nb::list l = stratum_to_list(item);
                    jsize sz = (jsize)nb::len(l);
                    jintArray ja = env->NewIntArray(sz);
                    if (!ja) { stratum_check_java_exc(env); throw std::runtime_error("Stratum: OOM allocating int[]"); }
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
                else if (nb::isinstance<nb::list>(item) || nb::isinstance<nb::tuple>(item)) {
                    nb::list l = stratum_to_list(item);
                    jsize sz = (jsize)nb::len(l);
                    jlongArray ja = env->NewLongArray(sz);
                    if (!ja) { stratum_check_java_exc(env); throw std::runtime_error("Stratum: OOM allocating long[]"); }
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
                else if (nb::isinstance<nb::list>(item) || nb::isinstance<nb::tuple>(item)) {
                    nb::list l = stratum_to_list(item);
                    jsize sz = (jsize)nb::len(l);
                    jfloatArray ja = env->NewFloatArray(sz);
                    if (!ja) { stratum_check_java_exc(env); throw std::runtime_error("Stratum: OOM allocating float[]"); }
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
                else if (nb::isinstance<nb::list>(item) || nb::isinstance<nb::tuple>(item)) {
                    nb::list l = stratum_to_list(item);
                    jsize sz = (jsize)nb::len(l);
                    jdoubleArray ja = env->NewDoubleArray(sz);
                    if (!ja) { stratum_check_java_exc(env); throw std::runtime_error("Stratum: OOM allocating double[]"); }
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
                else if (nb::isinstance<nb::list>(item) || nb::isinstance<nb::tuple>(item)) {
                    nb::list l = stratum_to_list(item);
                    jsize sz = (jsize)nb::len(l);
                    jbooleanArray ja = env->NewBooleanArray(sz);
                    if (!ja) { stratum_check_java_exc(env); throw std::runtime_error("Stratum: OOM allocating boolean[]"); }
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
                else if (nb::isinstance<nb::list>(item) || nb::isinstance<nb::tuple>(item)) {
                    nb::list l = stratum_to_list(item);
                    jsize sz = (jsize)nb::len(l);
                    jcharArray ja = env->NewCharArray(sz);
                    if (!ja) { stratum_check_java_exc(env); throw std::runtime_error("Stratum: OOM allocating char[]"); }
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
                else if (nb::isinstance<nb::list>(item) || nb::isinstance<nb::tuple>(item)) {
                    nb::list l = stratum_to_list(item);
                    jsize sz = (jsize)nb::len(l);
                    jshortArray ja = env->NewShortArray(sz);
                    if (!ja) { stratum_check_java_exc(env); throw std::runtime_error("Stratum: OOM allocating short[]"); }
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
                else if (nb::isinstance<nb::list>(item) || nb::isinstance<nb::tuple>(item)) {
                    nb::list l = stratum_to_list(item);
                    jsize sz = (jsize)nb::len(l);
                    jobjectArray ja = env->NewObjectArray(sz, g_jstring_class, nullptr);
                    if (!ja) { stratum_check_java_exc(env); throw std::runtime_error("Stratum: OOM allocating String[]"); }
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
                else if (nb::isinstance<nb::list>(item) || nb::isinstance<nb::tuple>(item)) {
                    nb::list l = stratum_to_list(item);
                    jsize sz = (jsize)nb::len(l);
                    jobjectArray ja = env->NewObjectArray(sz, g_object_class, nullptr);
                    if (!ja) { stratum_check_java_exc(env); throw std::runtime_error("Stratum: OOM allocating Object[]"); }
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
            // Built as a real java.util.ArrayList with full recursive element conversion
            case 'M': {
                if (item.is_none()) {
                    jargs[i].l = nullptr;
                } else if (nb::hasattr(item, "_ptr")) {
                    jargs[i].l = (jobject)(uintptr_t)nb::cast<int64_t>(item.attr("_ptr"));
                } else if (nb::isinstance<nb::list>(item) || nb::isinstance<nb::tuple>(item) || nb::isinstance<nb::set>(item)) {
                    jobject al = stratum_py_to_java(env, item);
                    jargs[i].l = al;
                    if (al) locals.push_back(al);
                } else {
                    jargs[i].l = nullptr;
                }
                break;
            }

            // ── Inbound java.util.Map / HashMap ─────────────────────────
            case 'N': {
                if (item.is_none()) {
                    jargs[i].l = nullptr;
                } else if (nb::hasattr(item, "_ptr")) {
                    jargs[i].l = (jobject)(uintptr_t)nb::cast<int64_t>(item.attr("_ptr"));
                } else if (nb::isinstance<nb::dict>(item)) {
                    jobject hm = stratum_py_to_java(env, item);
                    jargs[i].l = hm;
                    if (hm) locals.push_back(hm);
                } else {
                    jargs[i].l = nullptr;
                }
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
                if (!caller_ptr && t_ctor_callback_keys) t_ctor_callback_keys->push_back(key);
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
                if (!caller_ptr && t_ctor_callback_keys) t_ctor_callback_keys->push_back(key);
                if (nb::isinstance<nb::callable>(item)) {
                    store_callback(key, nb::cast<nb::callable>(item));
                } else if (nb::isinstance<nb::dict>(item)) {
                    nb::dict d = nb::cast<nb::dict>(item);
                    for (auto kv : d) store_callback(key + "#" + nb::cast<std::string>(kv.first), nb::cast<nb::callable>(kv.second));
                }
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
                    // Wrapped Java objects always have _ptr and are passed directly
                    jargs[i].l = (jobject)(uintptr_t)nb::cast<int64_t>(item.attr("_ptr"));
                } else if (nb::isinstance<nb::str>(item)) {
                    jstring js = stratum_str_to_jstring(env, nb::cast<std::string>(item));
                    jargs[i].l = js;
                    locals.push_back(js);
                } else if (nb::isinstance<nb::dict>(item) || nb::isinstance<nb::list>(item) || nb::isinstance<nb::tuple>(item) || nb::isinstance<nb::set>(item)) {
                    jobject jo = stratum_py_to_java(env, item);
                    jargs[i].l = jo;
                    if (jo) locals.push_back(jo);
                } else if (nb::isinstance<nb::bool_>(item)) {
                    // Must precede int_ check (bool subclasses int in Python)
                    jobject bo = stratum_py_to_java(env, item);
                    jargs[i].l = bo;
                    if (bo) locals.push_back(bo);
                } else if (nb::isinstance<nb::float_>(item)) {
                    jobject dbl = stratum_py_to_java(env, item);
                    jargs[i].l = dbl;
                    if (dbl) locals.push_back(dbl);
                } else if (nb::isinstance<nb::int_>(item)) {
                    // 100% Safe Auto-Boxing: All genuine StratumObjects were already caught by hasattr("_ptr") above.
                    // This is guaranteed to be a Python numeric int, safely boxed without address-space limits.
                    jobject io = stratum_py_to_java(env, item);
                    jargs[i].l = io;
                    if (io) locals.push_back(io);
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
    if (class_id >= g_class_count) throw std::runtime_error("Stratum: invalid class_id"); \
    ClassMeta& cls = g_classes[class_id];                                           \
    if (!cls.resolved.load(std::memory_order_acquire)) resolve_class_slots(env, class_id); \
    if (!cls.method_ids || slot >= cls.method_count) throw std::runtime_error("Stratum: invalid method slot"); \
    jmethodID mid = cls.method_ids[slot];                                           \
    if (!mid) throw std::runtime_error("Stratum: Method unavailable on device API"); \
    if (!cls.methods[slot].is_static && !ptr) {                                     \
        throw std::runtime_error("Stratum: Attempted to call a method on a null Java object"); \
    }                                                                               \
    LOGT(">> CALL [%s#%s] argc=%u ptr=0x%llx static=%d",                            \
         get_str(cls.jni_name_offset), get_str(cls.methods[slot].name_offset),      \
         (unsigned)nb::len(args), (unsigned long long)ptr, (int)cls.methods[slot].is_static); \
    jvalue jargs[32] = {};                                                          \
    std::vector<jobject> locals;                                                    \
    pack_arguments(env, get_str(cls.methods[slot].tags_offset), cls.methods[slot], args, jargs, locals, ptr);

#define LOGT_RET(fmt, val) LOGT("<< RET  [%s#%s] " fmt, get_str(cls.jni_name_offset), get_str(cls.methods[slot].name_offset), val)

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
    LOGT_RET("%s", "void");
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
    LOGT_RET("bool=%s", res != JNI_FALSE ? "true" : "false");
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
    LOGT_RET("int=%lld", (long long)res);
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
    LOGT_RET("long=%lld", (long long)res);
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
    LOGT_RET("double=%f", res);
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
    std::string s_res = stratum_jstring_to_str(env, res);
    LOGT_RET("str=%.80s", s_res.c_str());
    return s_res;
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
    if (!res) {
        LOGT_RET("obj=%s", "null");
        return 0;
    }
    jobject gref = env->NewGlobalRef(res);
    env->DeleteLocalRef(res);
    int64_t out_ptr = (int64_t)(uintptr_t)gref;
    LOGT_RET("obj=0x%llx", (unsigned long long)out_ptr);
    return out_ptr;
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
    if (!cls.resolved.load(std::memory_order_acquire)) resolve_class_slots(env, class_id);
    jmethodID mid = cls.method_ids[slot];
    if (!mid) throw std::runtime_error("Stratum: Constructor unavailable on this device API level");

    std::vector<std::string> ctor_keys;
    t_ctor_callback_keys = &ctor_keys;
    jvalue jargs[32] = {};
    std::vector<jobject> locals;
    pack_arguments(env, get_str(cls.methods[slot].tags_offset), cls.methods[slot], args, jargs, locals);
    t_ctor_callback_keys = nullptr;

    jobject obj;
    {
        nb::gil_scoped_release r;
        obj = env->NewObjectA(cls.class_ref, mid, jargs);
    }
    stratum_check_java_exc(env);

    if (!obj) return 0;
    jobject gref = env->NewGlobalRef(obj);
    env->DeleteLocalRef(obj);
    int64_t new_ptr = (int64_t)(uintptr_t)gref;

    for (const std::string& old_key : ctor_keys) {
        size_t us1 = old_key.find('_');
        size_t us2 = old_key.find('_', us1 + 1);
        std::string rest = (us2 != std::string::npos) ? old_key.substr(us2) : "";
        rekey_callback(old_key, "obj_" + std::to_string(new_ptr) + rest);
    }
    return new_ptr;
}

int64_t clone_ref(int64_t ptr) {
    if (!ptr) return 0;
    JNIEnv* env = get_env();
    if (!env) return 0;
    return (int64_t)(uintptr_t)env->NewGlobalRef((jobject)(uintptr_t)ptr);
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
    if (!g_classes[class_id].resolved.load(std::memory_order_acquire)) resolve_class_slots(env, class_id);
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
    bool is_stat = cls.fields[slot].is_static;
    jobject target = (jobject)(uintptr_t)ptr;
    uint8_t tid = cls.fields[slot].type_id;
    int64_t res;
    if (tid == 2) res = (int64_t)(is_stat ? env->GetStaticByteField(cls.class_ref, fid) : env->GetByteField(target, fid));
    else if (tid == 3) res = (int64_t)(is_stat ? env->GetStaticCharField(cls.class_ref, fid) : env->GetCharField(target, fid));
    else if (tid == 4) res = (int64_t)(is_stat ? env->GetStaticShortField(cls.class_ref, fid) : env->GetShortField(target, fid));
    else res = (int64_t)(is_stat ? env->GetStaticIntField(cls.class_ref, fid) : env->GetIntField(target, fid));
    stratum_check_java_exc(env);
    return res;
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

// [Patch 15] Array-typed field getter — mirrors call_arr's array-type
// dispatch but reads a field instead of invoking a method.
nb::object field_get_arr(int64_t ptr, uint32_t class_id, uint32_t slot) {
    JNIEnv* env = get_env();
    if (!env) throw std::runtime_error("Stratum: No JNIEnv on this thread");
    JniLocalFrame _local_frame(env, 8);
    ensure_class_resolved(env, class_id);
    ClassMeta& cls = g_classes[class_id];
    jfieldID fid = cls.field_ids[slot];
    if (!fid) throw std::runtime_error("Stratum: field unavailable on this device API level");
    if (!cls.fields[slot].is_static && !ptr) throw std::runtime_error("Stratum: field access on null Java object");
    jobject res = cls.fields[slot].is_static
        ? env->GetStaticObjectField(cls.class_ref, fid)
        : env->GetObjectField((jobject)(uintptr_t)ptr, fid);
    stratum_check_java_exc(env);
    if (!res) return nb::list();

    static jclass s_byte_arr_cls = nullptr, s_int_arr_cls = nullptr,
                  s_long_arr_cls = nullptr, s_flt_arr_cls = nullptr,
                  s_dbl_arr_cls  = nullptr, s_bool_arr_cls = nullptr,
                  s_char_arr_cls = nullptr, s_short_arr_cls = nullptr;
    static std::once_flag s_field_arr_flag;
    std::call_once(s_field_arr_flag, [&]() {
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
    });

    if (env->IsInstanceOf(res, s_byte_arr_cls)) {
        jbyteArray ba = (jbyteArray)res;
        jsize len = env->GetArrayLength(ba);
        jbyte* buf = env->GetByteArrayElements(ba, nullptr);
        nb::bytes out(reinterpret_cast<const char*>(buf), (size_t)len);
        env->ReleaseByteArrayElements(ba, buf, JNI_ABORT);
        env->DeleteLocalRef(res);
        return out;
    }
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
    if (env->IsInstanceOf(res, s_char_arr_cls)) {
        jcharArray ca = (jcharArray)res;
        jsize len = env->GetArrayLength(ca);
        std::vector<jchar> buf(len);
        if (len > 0) env->GetCharArrayRegion(ca, 0, len, buf.data());
        env->DeleteLocalRef(res);
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

    nb::list py_list;
    jsize len = env->GetArrayLength((jarray)res);
    for (jsize i = 0; i < len; ++i) {
        jobject elem = env->GetObjectArrayElement((jobjectArray)res, i);
        if (!elem) { py_list.append(nb::none()); continue; }
        if (g_jstring_class && env->IsInstanceOf(elem, g_jstring_class)) {
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
    bool is_stat = cls.fields[slot].is_static;
    jobject target = (jobject)(uintptr_t)ptr;
    uint8_t tid = cls.fields[slot].type_id;
    if (tid == 2) { if (is_stat) env->SetStaticByteField(cls.class_ref, fid, (jbyte)val); else env->SetByteField(target, fid, (jbyte)val); }
    else if (tid == 3) { if (is_stat) env->SetStaticCharField(cls.class_ref, fid, (jchar)val); else env->SetCharField(target, fid, (jchar)val); }
    else if (tid == 4) { if (is_stat) env->SetStaticShortField(cls.class_ref, fid, (jshort)val); else env->SetShortField(target, fid, (jshort)val); }
    else { if (is_stat) env->SetStaticIntField(cls.class_ref, fid, (jint)val); else env->SetIntField(target, fid, (jint)val); }
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

void field_set_str(int64_t ptr, uint32_t class_id, uint32_t slot, nb::object val) {
    JNIEnv* env = get_env();
    if (!env) throw std::runtime_error("Stratum: No JNIEnv on this thread");
    JniLocalFrame _local_frame(env, 8);
    ensure_class_resolved(env, class_id);
    ClassMeta& cls = g_classes[class_id];
    jfieldID fid = cls.field_ids[slot];
    if (!fid) throw std::runtime_error("Stratum: field unavailable");
    if (!cls.fields[slot].is_static && !ptr) throw std::runtime_error("Stratum: field access on null Java object");
    jstring js = val.is_none() ? nullptr : stratum_str_to_jstring(env, nb::cast<std::string>(val));
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
        // Pin the Java ByteBuffer alive for as long as Python holds the
        // memoryview — otherwise the JVM can free the backing memory
        // while Python is still reading/writing it.
        jobject owner_gref = env->NewGlobalRef(bb);
        nb::capsule owner(owner_gref, [](void* p) noexcept {
            JNIEnv* e = get_env();
            if (e && p) e->DeleteGlobalRef((jobject)p);
        });
        Py_buffer view;
        if (PyBuffer_FillInfo(&view, owner.ptr(), addr, (Py_ssize_t)cap, 0, PyBUF_WRITABLE) == -1) {
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