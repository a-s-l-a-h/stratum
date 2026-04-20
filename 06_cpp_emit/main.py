#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Stratum Pipeline — Stage 06 : C++ Emit (Nanobind)  new code 
=================================================
VERSION: 5_0 — ALL 47 ACTIONS APPLIED

ACTIONS APPLIED IN THIS VERSION:
  [A1]   jchar UTF-16 correct — array [C returns uint16_t not int
  [A2]   is_varargs detection from javap output (Stage 04 feeds this flag)
  [A3]   @Nullable/@NonNull propagated to none(true) only when nullable=True
  [A37]  jchar UTF-16 full handling in param/return conversion
  [A38]  Java varargs Object... support via nb::args
  [A39]  jthrowable param/return → StratumThrowable struct
  [A40]  WeakReference → NewWeakGlobalRef / StratumWeakObject
  [A41]  Direct ByteBuffer zero-copy via nb::memoryview
  [A42]  @NonNull/@Nullable → none(true) only when nullable

  [FIX-1]  sanitize_id: single-char names no longer prefixed with gen_
  [FIX-2]  reconstruct_jni_sig: warns on ambiguous jobject params
  [FIX-3]  callable_to_proxy: global ref cleaned up after call
  [FIX-4]  null jmethodID: LOGW instead of silent clear
  [FIX-5]  override_cls_var: dot/slash normalisation
  [FIX-6]  prefix collision: numeric suffix instead of silent drop
  [FIX-7]  _has_context_ctor: scans all ctor params not just params[0]
  [FIX-8]  StratumSurface: NewGlobalRef before base ctor
  [FIX-9]  field getters: promote local->global ref
  [FIX-10] null_return: consistent semicolons on all paths
  [FIX-11] StratumObject registered before batch classes
  [FIX-12] g_activity deleted in JNI_OnUnload
  [FIX-13] get_env() null guard
  [FIX-14] jni_args() index-stable ordering confirmed
  [FIX-15] StratumSurface constructor corrected in stratum_structs.h
  [FIX-16] ALL instance calls use virtual Call*Method. NonVirtual removed.
  [FIX-17] RegisterNatives: only is_native=True methods
  [FIX-18] JNICALL stubs always virtual
  [FIX-19] Maximum LOGD diagnostics (STRATUM_VERBOSE_LOG compile-time flag)
  [FIX-20] Inner-class constructors skipped in factory generation
  [FIX-21] Override index unified with all_for_init
  [FIX-22] Context-inherited methods work via virtual
  [FIX-23] nanobind pointer params: ALL pointer-type accept None via none(true)
  [FIX-24] inflate/addView/removeView ViewGroup* parent accepts None
  [FIX-25] Ultra-deep JNI tracing LOGV (STRATUM_ULTRA_LOG compile-time)
  [FIX-26] setOnClickListener lambda support via callable_to_proxy
  [FIX-27] getResources/getLayoutInflater return typed pointers
  [FIX-28] removeView/addView ViewGroup methods emit with none(true)
  [FIX-29] inflate(int, ViewGroup, bool) parent tagged none(true)

  [Action1]  stratum_cast real IsInstanceOf + rename cast_to → stratum_cast_to
  [Action2]  nativeDispatch unbox Java primitive wrapper args
  [Action3]  StratumObject destructor use get_env() not get_env_safe()
  [Action4]  Callback auto-remove on object destroy via stratum_key_prefix_
  [Action5]  __eq__ / __ne__ via IsSameObject
  [Action6]  nativeDispatch return value back to Java
  [Action7]  Dict callback key mismatch LOGW at proxy creation time
  [Action8]  CharSequence → string_in
  [Action9]  Named constructor factories
  [Action13] Enum ordinal/name/values
  [Action14] List/Map/Collection ↔ Python
  [Action19] __bool__ on StratumObject
  [Action20] VERSION string as module-level constant
  [Action23] GIL release in static methods + constructors (verified)
  [Action24] Cycle detection in topological_sort
  [Action25] g_activity mutex for rotation safety
  [Action26] Skip subclasses of failed-registration parents
  [Action27] ensure_*_init retry after find_class failure
  [Action28] Markdown report uses FQN
  [Action29] Unify sanitize_id single-char rule (no gen_ prefix)
  [Action30] GENERATED_FQNS population assertion
  [Action32] g_callbacks size monitoring + cap
  [Action33] Root struct null-env assert (never store dangling local ref)
  [Action34] g_callback_mutex + GIL deadlock fix
  [Action35] _stratum_cast double DeleteGlobalRef fix (NewGlobalRef in cast)
  [Action36] gil_scoped_acquire same-thread safety via PyGILState_Check
  [Action43] rename cast_to → stratum_cast_to in bridge_main.cpp
  [Action44] stratum_get_activity rename (exposed as stratum_get_activity)
  [Action47] STAGE_VERSION constant instead of hardcoded "4.8"
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Dict, List, Set, Tuple, Any, Optional


# =============================================================================
# [Action47] Stage version constant
# =============================================================================

STAGE_VERSION: str = "5.0"

# =============================================================================
# Global Configuration & State
# =============================================================================

GENERATED_FQNS: Set[str] = set()
NANOBIND_BATCH_SIZE: int = 100

# [Action32] callback map size cap
STRATUM_MAX_CALLBACKS: int = 10000

KEYWORDS: Set[str] = {
    "alignas", "alignof", "and", "and_eq", "asm", "auto", "bitand", "bitor",
    "bool", "break", "case", "catch", "char", "char8_t", "char16_t", "char32_t",
    "class", "compl", "concept", "const", "const_cast", "continue", "co_await",
    "co_return", "co_yield", "decltype", "default", "delete", "do", "double",
    "dynamic_cast", "else", "enum", "explicit", "export", "extern", "false",
    "float", "for", "friend", "goto", "if", "inline", "int", "long", "mutable",
    "namespace", "new", "noexcept", "not", "not_eq", "nullptr", "operator", "or",
    "or_eq", "private", "protected", "public", "register", "reinterpret_cast",
    "requires", "return", "short", "signed", "sizeof", "static", "static_assert",
    "static_cast", "struct", "switch", "template", "this", "thread_local",
    "throw", "true", "try", "typedef", "typeid", "typename", "union", "unsigned",
    "using", "virtual", "void", "volatile", "wchar_t", "while", "xor", "xor_eq",
    "False", "None", "True", "as", "assert", "async", "await", "def", "del",
    "elif", "except", "finally", "from", "global", "import", "in", "is",
    "lambda", "nonlocal", "pass", "raise", "with", "yield",
    "errno", "EOF", "NULL", "stdin", "stdout", "stderr",
    "unix", "linux", "DOMAIN", "signal", "slot", "emit",
}

ARRAY_TYPE_MAP: Dict[str, Tuple[str, str, str, str, str]] = {
    "[Z": ("jbooleanArray", "jboolean", "Boolean", "uint8_t",  "bool"),
    "[B": ("jbyteArray",    "jbyte",    "Byte",    "int8_t",   "bytes"),
    # [A1/A37] jchar is UTF-16 — uint16_t, not int
    "[C": ("jcharArray",    "jchar",    "Char",    "uint16_t", "str"),
    "[S": ("jshortArray",   "jshort",   "Short",   "int16_t",  "int"),
    "[I": ("jintArray",     "jint",     "Int",     "int32_t",  "int"),
    "[J": ("jlongArray",    "jlong",    "Long",    "int64_t",  "int"),
    "[F": ("jfloatArray",   "jfloat",   "Float",   "float",    "float"),
    "[D": ("jdoubleArray",  "jdouble",  "Double",  "double",   "float"),
}

FIELD_TYPE_MAP: Dict[str, Tuple[str, str, str, str, str]] = {
    "Z": ("GetBooleanField", "SetBooleanField", "GetStaticBooleanField", "SetStaticBooleanField", "bool"),
    "B": ("GetByteField",    "SetByteField",    "GetStaticByteField",    "SetStaticByteField",    "int"),
    "C": ("GetCharField",    "SetCharField",    "GetStaticCharField",    "SetStaticCharField",    "int"),
    "S": ("GetShortField",   "SetShortField",   "GetStaticShortField",   "SetStaticShortField",   "int"),
    "I": ("GetIntField",     "SetIntField",     "GetStaticIntField",     "SetStaticIntField",     "int"),
    "J": ("GetLongField",    "SetLongField",    "GetStaticLongField",    "SetStaticLongField",    "int64_t"),
    "F": ("GetFloatField",   "SetFloatField",   "GetStaticFloatField",   "SetStaticFloatField",   "float"),
    "D": ("GetDoubleField",  "SetDoubleField",  "GetStaticDoubleField",  "SetStaticDoubleField",  "double"),
    "Ljava/lang/String;": (
        "GetObjectField", "SetObjectField",
        "GetStaticObjectField", "SetStaticObjectField",
        "std::string",
    ),
}

FIELD_CAST_MAP: Dict[str, str] = {
    "bool":    "jboolean",
    "int":     "jint",
    "int16_t": "jshort",
    "int32_t": "jint",
    "int64_t": "jlong",
    "float":   "jfloat",
    "double":  "jdouble",
}

# [A39] Known throwable class patterns
THROWABLE_CLASSES = {
    "java/lang/Throwable", "java/lang/Exception", "java/lang/Error",
    "java/lang/RuntimeException", "java/io/IOException",
    "java/lang/IllegalArgumentException", "java/lang/IllegalStateException",
    "java/lang/NullPointerException", "java/lang/IndexOutOfBoundsException",
    "android/os/RemoteException", "android/hardware/camera2/CameraAccessException",
}

# [A40] Known WeakReference patterns
WEAKREF_CLASSES = {"java/lang/ref/WeakReference", "java/lang/ref/SoftReference"}

# [A14] Known collection return types
COLLECTION_CLASSES = {
    "java/util/List", "java/util/ArrayList", "java/util/LinkedList",
    "java/util/Collection", "java/util/Set", "java/util/HashSet",
    "java/util/TreeSet", "java/util/Queue",
}
MAP_CLASSES = {
    "java/util/Map", "java/util/HashMap", "java/util/LinkedHashMap",
    "java/util/TreeMap", "java/util/Hashtable",
}

# =============================================================================
# [FIX-25] Ultra-verbose + verbose log macros
# =============================================================================

VERBOSE_LOG_HEADER = """\
#ifndef STRATUM_VERBOSE_LOG
#define STRATUM_VERBOSE_LOG 0
#endif
#ifndef STRATUM_ULTRA_LOG
#define STRATUM_ULTRA_LOG 0
#endif

// LOGD: method entry/exit, class init, field/method IDs
#if STRATUM_VERBOSE_LOG || STRATUM_ULTRA_LOG
#define LOGD(...) __android_log_print(ANDROID_LOG_DEBUG, "Stratum", __VA_ARGS__)
#else
#define LOGD(...) ((void)0)
#endif

// LOGV: every JNI call, every argument value, every return value
#if STRATUM_ULTRA_LOG
#define LOGV(...) __android_log_print(ANDROID_LOG_VERBOSE, "Stratum", __VA_ARGS__)
#else
#define LOGV(...) ((void)0)
#endif

// LOGT: trace direction markers
#if STRATUM_ULTRA_LOG
#define LOGT_PY_TO_JNI(cls, meth) \\
    __android_log_print(ANDROID_LOG_VERBOSE, "Stratum/TRACE", \\
        ">> PY->CPP->JNI  %s#%s", cls, meth)
#define LOGT_JNI_TO_PY(cls, meth, ret) \\
    __android_log_print(ANDROID_LOG_VERBOSE, "Stratum/TRACE", \\
        "<< JNI->CPP->PY  %s#%s  ret=%s", cls, meth, ret)
#define LOGT_JNICALL_IN(cls, meth) \\
    __android_log_print(ANDROID_LOG_VERBOSE, "Stratum/TRACE", \\
        ">> JNICALL  %s#%s  (Java->JNI->CPP->PY)", cls, meth)
#define LOGT_JNICALL_OUT(cls, meth) \\
    __android_log_print(ANDROID_LOG_VERBOSE, "Stratum/TRACE", \\
        "<< JNICALL  %s#%s  (PY->CPP->JNI->Java)", cls, meth)
#define LOGV_PTR(name, ptr) \\
    __android_log_print(ANDROID_LOG_VERBOSE, "Stratum/ARG", \\
        "  arg '%s' = jobject %p  (null=%s)", name, (void*)(ptr), (ptr)==nullptr?"YES":"no")
#define LOGV_STR(name, val) \\
    __android_log_print(ANDROID_LOG_VERBOSE, "Stratum/ARG", \\
        "  arg '%s' = string '%s'", name, (val).c_str())
#define LOGV_INT(name, val) \\
    __android_log_print(ANDROID_LOG_VERBOSE, "Stratum/ARG", \\
        "  arg '%s' = %lld", name, (long long)(val))
#define LOGV_BOOL(name, val) \\
    __android_log_print(ANDROID_LOG_VERBOSE, "Stratum/ARG", \\
        "  arg '%s' = %s", name, (val)?"true":"false")
#define LOGV_RET_PTR(meth, ptr) \\
    __android_log_print(ANDROID_LOG_VERBOSE, "Stratum/RET", \\
        "  return from '%s' = jobject %p  (null=%s)", meth, (void*)(ptr), (ptr)==nullptr?"YES":"no")
#define LOGV_RET_STR(meth, val) \\
    __android_log_print(ANDROID_LOG_VERBOSE, "Stratum/RET", \\
        "  return from '%s' = string '%s'", meth, (val).c_str())
#define LOGV_RET_INT(meth, val) \\
    __android_log_print(ANDROID_LOG_VERBOSE, "Stratum/RET", \\
        "  return from '%s' = %lld", meth, (long long)(val))
#define LOGV_RET_BOOL(meth, val) \\
    __android_log_print(ANDROID_LOG_VERBOSE, "Stratum/RET", \\
        "  return from '%s' = %s", meth, (val)?"true":"false")
#define LOGV_PYOBJ(name, obj) \\
    __android_log_print(ANDROID_LOG_VERBOSE, "Stratum/ARG", \\
        "  arg '%s' = nb::object %p", name, (void*)(obj).ptr())
#else
#define LOGT_PY_TO_JNI(cls, meth)        ((void)0)
#define LOGT_JNI_TO_PY(cls, meth, ret)   ((void)0)
#define LOGT_JNICALL_IN(cls, meth)        ((void)0)
#define LOGT_JNICALL_OUT(cls, meth)       ((void)0)
#define LOGV_PTR(name, ptr)               ((void)0)
#define LOGV_STR(name, val)               ((void)0)
#define LOGV_INT(name, val)               ((void)0)
#define LOGV_BOOL(name, val)              ((void)0)
#define LOGV_RET_PTR(meth, ptr)           ((void)0)
#define LOGV_RET_STR(meth, val)           ((void)0)
#define LOGV_RET_INT(meth, val)           ((void)0)
#define LOGV_RET_BOOL(meth, val)          ((void)0)
#define LOGV_PYOBJ(name, obj)             ((void)0)
#endif
"""


# =============================================================================
# Utility & Formatting Functions
# =============================================================================

def print_header(title: str) -> None:
    print("=" * 70)
    print(f"  {title}")
    print("=" * 70)


def sanitize_id(s: str) -> str:
    """
    [FIX-1 / Action29] Single-char names NO gen_ prefix — just return as-is
    after keyword check. Consistent with Stage 08.
    """
    s = re.sub(r'[^a-zA-Z0-9_]', '_', s)
    if s and s[0].isdigit():
        s = '_' + s
    s = re.sub(r'_+', '_', s).strip('_') or '_unknown'
    if not s:
        s = '_unknown'
    if s in KEYWORDS:
        s += '_'
    return s


def cpp_class_prefix(fqn: str) -> str:
    return sanitize_id(fqn.replace('.', '_').replace('$', '_'))


def struct_name(fqn: str) -> str:
    return f"Stratum_{cpp_class_prefix(fqn)}"


def ret_sig_from_jni(jni_sig: str) -> str:
    if ")" in jni_sig:
        return jni_sig.split(")")[-1]
    return ""


def is_string_array_sig(sig: str) -> bool:
    return sig == "[Ljava/lang/String;"


def is_byte_array_sig(sig: str) -> bool:
    return sig == "[B"


def to_jni_slash(class_name: str) -> str:
    return class_name.replace(".", "/")


def is_throwable_class(jni_class: str) -> bool:
    """[A39] Check if a JNI class name is a Throwable subclass."""
    return jni_class in THROWABLE_CLASSES or jni_class.endswith("Exception") or jni_class.endswith("Error")


def is_weakref_class(jni_class: str) -> bool:
    """[A40] Check if a JNI class name is a WeakReference type."""
    return jni_class in WEAKREF_CLASSES


def is_collection_class(jni_class: str) -> bool:
    """[A14] Check if return type is a Java Collection."""
    return jni_class in COLLECTION_CLASSES


def is_map_class(jni_class: str) -> bool:
    """[A14] Check if return type is a Java Map."""
    return jni_class in MAP_CLASSES


def reconstruct_jni_sig(m: dict) -> str:
    """[FIX-2] Reconstruct JNI signature with ambiguity warning."""
    sig = m.get("jni_new_sig") or m.get("jni_signature", "")
    if sig:
        return sig

    params        = m.get("params", [])
    ret           = m.get("return_jni", "V")
    ret_map       = {
        "void": "V", "jboolean": "Z", "jbyte": "B", "jchar": "C",
        "jshort": "S", "jint": "I", "jlong": "J", "jfloat": "F",
        "jdouble": "D", "jobject": "Ljava/lang/Object;",
    }
    param_sigs    = []
    has_ambiguous = False

    for p in params:
        jt = p.get("jni_type", "")
        if jt in ARRAY_TYPE_MAP:
            param_sigs.append(jt)
        elif jt == "jobject":
            jtype = p.get("java_type", "").replace(".", "/")
            if jtype:
                param_sigs.append(f"L{jtype};")
            else:
                param_sigs.append("Ljava/lang/Object;")
                has_ambiguous = True
        elif jt == "jstring":
            param_sigs.append("Ljava/lang/String;")
        else:
            pm = {
                "jboolean": "Z", "jbyte": "B", "jchar": "C", "jshort": "S",
                "jint": "I", "jlong": "J", "jfloat": "F", "jdouble": "D",
            }
            param_sigs.append(pm.get(jt, "Ljava/lang/Object;"))

    ret_sig       = ret_map.get(ret, "V")
    reconstructed = "(" + "".join(param_sigs) + ")" + ret_sig

    if has_ambiguous:
        mname = m.get("name", "<unknown>")
        print(
            f"  WARN [FIX-2] reconstruct_jni_sig: '{mname}' has jobject "
            f"param(s) with no java_type — sig may be wrong: {reconstructed}"
        )

    return reconstructed


def native_impl_fn(fqn: str, method_name: str, index: int,
                   is_static: bool) -> str:
    kind = "s" if is_static else "i"
    return f"jni_{kind}_{cpp_class_prefix(fqn)}_{sanitize_id(method_name)}_{index}"


def method_id_var(fqn: str, method_name: str, index: int) -> str:
    return f"g_{cpp_class_prefix(fqn)}_{sanitize_id(method_name)}_{index}_id"


def field_id_var(fqn: str, field_name: str) -> str:
    return f"g_{cpp_class_prefix(fqn)}_{sanitize_id(field_name)}_fid"


def override_cls_var(fqn: str, method_name: str, index: int) -> str:
    return f"g_{cpp_class_prefix(fqn)}_{sanitize_id(method_name)}_{index}_cls"


def get_return_jni(m: dict) -> str:
    sig = reconstruct_jni_sig(m)
    if ")" in sig:
        ret = sig.split(")")[-1]
        if ret.startswith("[") or ret.startswith("L"): return "jobject"
        if ret == "V": return "void"
        if ret == "Z": return "jboolean"
        if ret == "B": return "jbyte"
        if ret == "C": return "jchar"
        if ret == "S": return "jshort"
        if ret == "I": return "jint"
        if ret == "J": return "jlong"
        if ret == "F": return "jfloat"
        if ret == "D": return "jdouble"
    return m.get("return_jni", "void")


def call_suffix(ret_jni: str) -> str:
    return {
        "void":     "Void",
        "jboolean": "Boolean",
        "jbyte":    "Byte",
        "jchar":    "Char",
        "jshort":   "Short",
        "jint":     "Int",
        "jlong":    "Long",
        "jfloat":   "Float",
        "jdouble":  "Double",
    }.get(ret_jni, "Object")


def raw_c_type_of(ret_jni: str) -> str:
    return {
        "void":     "void",
        "jboolean": "jboolean",
        "jbyte":    "jbyte",
        "jchar":    "jchar",
        "jshort":   "jshort",
        "jint":     "jint",
        "jlong":    "jlong",
        "jfloat":   "jfloat",
        "jdouble":  "jdouble",
        "jstring":  "jobject",
        "jobject":  "jobject",
    }.get(ret_jni, "jobject")


def jni_sig_for_type(return_jni: str) -> str:
    return {
        "void":     "void",
        "jboolean": "jboolean",
        "jbyte":    "jbyte",
        "jchar":    "jchar",
        "jshort":   "jshort",
        "jint":     "jint",
        "jlong":    "jlong",
        "jfloat":   "jfloat",
        "jdouble":  "jdouble",
        "jstring":  "jstring",
        "jobject":  "jobject",
    }.get(return_jni, "jobject")


def extract_return_java_type(sig: str) -> str:
    if ")" in sig:
        ret = sig.split(")")[-1]
        if ret.startswith("L") and ret.endswith(";"):
            return ret[1:-1].replace("/", ".")
    return ""


def is_string_return(m: dict) -> bool:
    sig = reconstruct_jni_sig(m)
    return ret_sig_from_jni(sig) == "Ljava/lang/String;"


def is_charsequence_param(p: dict) -> bool:
    """[Action8] Detect CharSequence params."""
    jtype = p.get("java_type", "")
    return jtype in ("java.lang.CharSequence", "CharSequence",
                     "java/lang/CharSequence")


def cpp_type_for_param(p: dict) -> str:
    conv      = p.get("conversion", "")
    java_type = p.get("java_type", "")
    jni_type  = p.get("jni_type", "")
    cpp_type  = p.get("cpp_type", "")

    # [Action8] CharSequence → same as string
    if is_charsequence_param(p):
        return "const std::string&"

    if conv == "callable_to_proxy":                return "nb::object"
    if conv == "abstract_adapter":                 return "nb::object"
    if conv == "string_in":                        return "const std::string&"
    if conv in ("bool_in", "bool_out"):            return "bool"

    # [A38] Varargs
    if p.get("is_varargs", False):
        return "nb::args"

    # [A39] Throwable params
    jni_class = p.get("jni_class", "").replace(".", "/")
    if jni_class and is_throwable_class(jni_class):
        return "StratumThrowable*"

    # [A40] WeakReference params
    if jni_class and is_weakref_class(jni_class):
        return "StratumWeakObject*"

    if java_type and java_type in GENERATED_FQNS:  return f"{struct_name(java_type)}*"
    if java_type == "[B":                          return "nb::bytes"

    if java_type in ARRAY_TYPE_MAP:
        arr_jtype, elem_jtype, region_suffix, cpp_t, _ = ARRAY_TYPE_MAP[java_type]
        # [A37] jchar array → std::u16string for correct UTF-16
        if java_type == "[C":
            return "std::u16string"
        return f"std::vector<{cpp_t}>"

    if java_type and (java_type.startswith("[L") or java_type.startswith("[[")):
        return "nb::list"

    if jni_type == "jobject" or cpp_type == "jobject": return "StratumObject*"
    if jni_type == "jstring":                          return "const std::string&"

    # [A37] jchar as UTF-16 single char
    if jni_type == "jchar":
        return "std::string"  # Python str of 1 char, converted via UTF-16

    if conv in ("direct", "long_safe"):
        return cpp_type if cpp_type else "jlong"

    return cpp_type if cpp_type else "StratumObject*"


def param_is_nullable_pointer(p: dict) -> bool:
    """
    [FIX-23 / A42] Returns True if this parameter is a nullable pointer.
    Uses explicit 'nullable' field if present (from @Nullable annotation),
    otherwise defaults to True for all pointer types.
    """
    t = cpp_type_for_param(p)
    if not t.endswith("*") and t != "nb::object":
        return False
    # [A42] If we have explicit nullable annotation, honour it
    # nullable=True → accept None; nullable=False (@NonNull) → reject None
    return p.get("nullable", True)


def ret_decl_for(m: dict) -> str:
    ret_cpp  = m.get("return_cpp", "void")
    ret_conv = m.get("return_conversion", "none")
    sig      = reconstruct_jni_sig(m)
    ret_sig  = ret_sig_from_jni(sig)

    if ret_cpp == "void":        return "void"
    if ret_conv == "bool_out":   return "bool"
    if ret_conv == "string_out": return "std::string"
    if is_string_return(m):      return "std::string"
    if ret_sig == "[B":          return "nb::bytes"

    # [A37] jchar array return → std::u16string
    if ret_sig == "[C":          return "std::u16string"

    if ret_sig in ARRAY_TYPE_MAP:
        _, _, _, cpp_t, _ = ARRAY_TYPE_MAP[ret_sig]
        return f"std::vector<{cpp_t}>"

    if is_string_array_sig(ret_sig): return "nb::list"
    if ret_sig.startswith("["):      return "nb::list"

    sig_java_type = extract_return_java_type(sig)

    # [A39] Throwable return
    if sig_java_type:
        jni_form = sig_java_type.replace(".", "/")
        if is_throwable_class(jni_form):
            return "StratumThrowable*"
        # [A40] WeakReference return
        if is_weakref_class(jni_form):
            return "StratumWeakObject*"
        # [A41] ByteBuffer return
        if jni_form == "java/nio/ByteBuffer":
            return "nb::object"  # memoryview or bytes depending on direct
        # [A14] Collection return
        if is_collection_class(jni_form):
            return "nb::list"
        # [A14] Map return
        if is_map_class(jni_form):
            return "nb::dict"

    if sig_java_type and sig_java_type in GENERATED_FQNS:
        return f"{struct_name(sig_java_type)}*"

    ret_jni = get_return_jni(m)
    if ret_jni == "jobject" or ret_cpp == "jobject":
        return "StratumObject*"

    # [A37] jchar return → std::string (single UTF-16 char as Python str)
    if ret_jni == "jchar":
        return "std::string"

    return ret_cpp


def should_skip_method(ret_decl: str,
                        cpp_params: List[Tuple[str, str]]) -> bool:
    if not ret_decl:                               return True
    if ret_decl == "jobject":                      return True
    if any(t == "jobject" for _, t in cpp_params): return True
    return False


def get_methods_for_class(cls: dict) -> Dict[str, list]:
    if "declared_methods" in cls:
        return {
            "constructors": cls.get("constructors", []),
            "declared":     cls.get("declared_methods", []),
            "overridden":   cls.get("overridden_methods", []),
            "inherited":    cls.get("inherited_methods", []),
        }
    ctors, decl = [], []
    for m in cls.get("methods", []):
        (ctors if m.get("is_constructor") else decl).append(m)
    return {
        "constructors": ctors, "declared": decl,
        "overridden": [], "inherited": [],
    }


def get_fields_for_class(cls: dict) -> list:
    return cls.get("fields", [])


def null_return(ret_decl: str) -> str:
    """[FIX-10] All paths return syntactically correct C++ statements."""
    if ret_decl == "void":                 return "return;"
    if ret_decl == "std::string":          return 'return "";'
    if ret_decl == "std::u16string":       return 'return std::u16string();'
    if ret_decl == "bool":                 return "return false;"
    if ret_decl == "nb::bytes":            return 'return nb::bytes("", 0);'
    if ret_decl.startswith("std::vector"): return "return {};"
    if ret_decl == "nb::list":             return "return nb::list();"
    if ret_decl == "nb::dict":             return "return nb::dict();"
    if ret_decl == "nb::object":           return "return nb::none();"
    if ret_decl.endswith("*"):             return "return nullptr;"
    return "return 0;"


# =============================================================================
# Exception Propagation Generator
# =============================================================================

def emit_exception_check(lines: list, indent: str = "    ",
                          ret_decl: str = "void") -> None:
    lines += [
        f"{indent}if (env->ExceptionCheck()) {{",
        f"{indent}    jthrowable _ex = env->ExceptionOccurred();",
        f"{indent}    env->ExceptionClear();",
        f"{indent}    std::string _smsg = \"Java exception\";",
        f"{indent}    if (_ex) {{",
        f"{indent}        jclass _ecls = env->GetObjectClass(_ex);",
        f"{indent}        jmethodID _emid = env->GetMethodID("
        f"_ecls, \"getMessage\", \"()Ljava/lang/String;\");",
        f"{indent}        if (_emid) {{",
        f"{indent}            jstring _jm = (jstring)env->CallObjectMethod(_ex, _emid);",
        f"{indent}            if (_jm) {{",
        f"{indent}                const char* _cm = env->GetStringUTFChars(_jm, nullptr);",
        f"{indent}                _smsg = _cm;",
        f"{indent}                env->ReleaseStringUTFChars(_jm, _cm);",
        f"{indent}                env->DeleteLocalRef(_jm);",
        f"{indent}            }}",
        f"{indent}        }}",
        f"{indent}        env->DeleteLocalRef(_ecls);",
        f"{indent}        env->DeleteLocalRef(_ex);",
        f"{indent}    }}",
        f"{indent}    LOGE(\"Java exception caught: %s\", _smsg.c_str());",
        f"{indent}    throw std::runtime_error(_smsg);",
        f"{indent}}}",
    ]


# =============================================================================
# Parameter Translation Generators
# =============================================================================

def emit_param_conversion(p: dict, mi: int, lines: list,
                           indent: str = "    ",
                           method_name: str = "") -> None:
    """[FIX-25 / A37 / A38 / A39 / A40] Emit param conversion with LOGV tracing."""
    conv      = p.get("conversion", "")
    name      = sanitize_id(p["name"])
    jtype     = p.get("jni_type", "jobject")
    java_type = p.get("java_type", "")

    # [A38] Varargs — packed as Python args tuple → jobjectArray
    if p.get("is_varargs", False):
        lines += [
            f"{indent}// [A38] Varargs packing",
            f"{indent}jsize _varargs_len = (jsize)nb::len({name});",
            f"{indent}jobjectArray jni_{name} = env->NewObjectArray(",
            f"{indent}    _varargs_len, g_object_class, nullptr);",
            f"{indent}for (jsize _vi = 0; _vi < _varargs_len; ++_vi) {{",
            f"{indent}    auto _vitem = {name}[_vi];",
            f"{indent}    jobject _vjobj = nullptr;",
            f"{indent}    if (nb::isinstance<nb::str>(_vitem))",
            f"{indent}        _vjobj = env->NewStringUTF(nb::cast<std::string>(_vitem).c_str());",
            f"{indent}    else if (nb::isinstance<StratumObject>(_vitem))",
            f"{indent}        _vjobj = nb::cast<StratumObject*>(_vitem)->obj_;",
            f"{indent}    else if (nb::isinstance<nb::int_>(_vitem)) {{",
            f"{indent}        // box int to Integer",
            f"{indent}        static jclass _intcls = nullptr;",
            f"{indent}        static jmethodID _intvalof = nullptr;",
            f"{indent}        if (!_intcls) {{ jclass _c = env->FindClass(\"java/lang/Integer\");",
            f"{indent}            _intcls = (jclass)env->NewGlobalRef(_c); env->DeleteLocalRef(_c); }}",
            f"{indent}        if (!_intvalof) _intvalof = env->GetStaticMethodID(_intcls, \"valueOf\", \"(I)Ljava/lang/Integer;\");",
            f"{indent}        _vjobj = env->CallStaticObjectMethod(_intcls, _intvalof, (jint)nb::cast<long long>(_vitem));",
            f"{indent}    }}",
            f"{indent}    env->SetObjectArrayElement(jni_{name}, _vi, _vjobj);",
            f"{indent}    if (_vjobj && nb::isinstance<nb::str>(_vitem)) env->DeleteLocalRef(_vjobj);",
            f"{indent}}}",
            f"{indent}LOGV_INT(\"varargs_len\", (int64_t)_varargs_len);",
        ]
        return

    # [A39] Throwable param — wrap as StratumThrowable
    jni_class = p.get("jni_class", "").replace(".", "/")
    if jni_class and is_throwable_class(jni_class):
        lines.append(
            f"{indent}jobject jni_{name} = {name} ? {name}->obj_ : nullptr;")
        lines.append(
            f"{indent}LOGV_PTR(\"{name}\", jni_{name});")
        return

    # [A40] WeakReference param
    if jni_class and is_weakref_class(jni_class):
        lines.append(
            f"{indent}jobject jni_{name} = {name} ? {name}->weak_ref_ : nullptr;")
        lines.append(
            f"{indent}LOGV_PTR(\"{name}\", jni_{name});")
        return

    # [Action8] CharSequence → string
    if is_charsequence_param(p):
        lines += [
            f"{indent}// [PATCH-UTF8-IN] CharSequence: UTF-8→UTF-16→NewString, not NewStringUTF",
            f"{indent}jstring jni_{name};",
            f"{indent}{{",
            f"{indent}    const uint8_t* _ub = reinterpret_cast<const uint8_t*>({name}.data());",
            f"{indent}    size_t _ul = {name}.size();",
            f"{indent}    std::u16string _u16;",
            f"{indent}    _u16.reserve(_ul);",
            f"{indent}    for (size_t _ui = 0; _ui < _ul; ) {{",
            f"{indent}        uint32_t _cp = 0;",
            f"{indent}        uint8_t _b0 = _ub[_ui];",
            f"{indent}        if (_b0 < 0x80u)                              {{ _cp = _b0; _ui += 1; }}",
            f"{indent}        else if ((_b0 & 0xE0u) == 0xC0u && _ui+1 < _ul) {{ _cp = ((uint32_t)(_b0 & 0x1Fu) << 6)  | (_ub[_ui+1] & 0x3Fu); _ui += 2; }}",
            f"{indent}        else if ((_b0 & 0xF0u) == 0xE0u && _ui+2 < _ul) {{ _cp = ((uint32_t)(_b0 & 0x0Fu) << 12) | ((uint32_t)(_ub[_ui+1] & 0x3Fu) << 6) | (_ub[_ui+2] & 0x3Fu); _ui += 3; }}",
            f"{indent}        else if ((_b0 & 0xF8u) == 0xF0u && _ui+3 < _ul) {{ _cp = ((uint32_t)(_b0 & 0x07u) << 18) | ((uint32_t)(_ub[_ui+1] & 0x3Fu) << 12) | ((uint32_t)(_ub[_ui+2] & 0x3Fu) << 6) | (_ub[_ui+3] & 0x3Fu); _ui += 4; }}",
            f"{indent}        else {{ ++_ui; continue; }}",
            f"{indent}        if (_cp < 0x10000u) {{ _u16 += (char16_t)_cp; }}",
            f"{indent}        else if (_cp < 0x110000u) {{",
            f"{indent}            _cp -= 0x10000u;",
            f"{indent}            _u16 += (char16_t)(0xD800u | (_cp >> 10));",
            f"{indent}            _u16 += (char16_t)(0xDC00u | (_cp & 0x3FFu));",
            f"{indent}        }}",
            f"{indent}    }}",
            f"{indent}    jni_{name} = env->NewString(",
            f"{indent}        reinterpret_cast<const jchar*>(_u16.data()), (jsize)_u16.size());",
            f"{indent}}}",
            f"{indent}LOGD(\"param {name} (CharSequence→utf16 jstring) = %s\", {name}.c_str());",
            f"{indent}LOGV_STR(\"{name}\", {name});",
        ]
        return

    if conv == "bool_in":
        lines.append(
            f"{indent}jboolean jni_{name} = {name} ? JNI_TRUE : JNI_FALSE;")
        lines.append(
            f"{indent}LOGD(\"param {name} (bool) = %d\", (int)jni_{name});")
        lines.append(
            f"{indent}LOGV_BOOL(\"{name}\", {name});")

    elif conv == "string_in" or jtype == "jstring":
        lines += [
            f"{indent}// [PATCH-UTF8-IN] NewStringUTF expects MUTF-8, breaks emoji from Python",
            f"{indent}// Convert Python UTF-8 → UTF-16 → NewString() instead",
            f"{indent}jstring jni_{name};",
            f"{indent}{{",
            f"{indent}    const uint8_t* _ub = reinterpret_cast<const uint8_t*>({name}.data());",
            f"{indent}    size_t _ul = {name}.size();",
            f"{indent}    std::u16string _u16;",
            f"{indent}    _u16.reserve(_ul);",
            f"{indent}    for (size_t _ui = 0; _ui < _ul; ) {{",
            f"{indent}        uint32_t _cp = 0;",
            f"{indent}        uint8_t _b0 = _ub[_ui];",
            f"{indent}        if (_b0 < 0x80u)                              {{ _cp = _b0; _ui += 1; }}",
            f"{indent}        else if ((_b0 & 0xE0u) == 0xC0u && _ui+1 < _ul) {{ _cp = ((uint32_t)(_b0 & 0x1Fu) << 6)  | (_ub[_ui+1] & 0x3Fu); _ui += 2; }}",
            f"{indent}        else if ((_b0 & 0xF0u) == 0xE0u && _ui+2 < _ul) {{ _cp = ((uint32_t)(_b0 & 0x0Fu) << 12) | ((uint32_t)(_ub[_ui+1] & 0x3Fu) << 6) | (_ub[_ui+2] & 0x3Fu); _ui += 3; }}",
            f"{indent}        else if ((_b0 & 0xF8u) == 0xF0u && _ui+3 < _ul) {{ _cp = ((uint32_t)(_b0 & 0x07u) << 18) | ((uint32_t)(_ub[_ui+1] & 0x3Fu) << 12) | ((uint32_t)(_ub[_ui+2] & 0x3Fu) << 6) | (_ub[_ui+3] & 0x3Fu); _ui += 4; }}",
            f"{indent}        else {{ ++_ui; continue; }}",
            f"{indent}        if (_cp < 0x10000u) {{ _u16 += (char16_t)_cp; }}",
            f"{indent}        else if (_cp < 0x110000u) {{",
            f"{indent}            _cp -= 0x10000u;",
            f"{indent}            _u16 += (char16_t)(0xD800u | (_cp >> 10));",
            f"{indent}            _u16 += (char16_t)(0xDC00u | (_cp & 0x3FFu));",
            f"{indent}        }}",
            f"{indent}    }}",
            f"{indent}    jni_{name} = env->NewString(",
            f"{indent}        reinterpret_cast<const jchar*>(_u16.data()), (jsize)_u16.size());",
            f"{indent}}}",
            f"{indent}LOGD(\"param {name} (string→utf16 jstring) = %s\", {name}.c_str());",
            f"{indent}LOGV_STR(\"{name}\", {name});",
        ]

    elif conv == "abstract_adapter":
        lines.append(
            f"{indent}jobject jni_{name} = "
            f"create_proxy_m{mi}_{name}(env, {name});")
        lines.append(
            f"{indent}LOGD(\"param {name} (abstract adapter) = %p\", jni_{name});")
        lines.append(
            f"{indent}LOGV_PTR(\"{name}\", jni_{name});")

    elif conv == "callable_to_proxy":
        lines.append(
            f"{indent}jobject jni_{name} = "
            f"create_proxy_m{mi}_{name}(env, {name});")
        lines.append(
            f"{indent}LOGD(\"param {name} (proxy) created = %p\", jni_{name});")
        lines.append(
            f"{indent}LOGV_PTR(\"{name}\", jni_{name});")

    elif java_type and java_type in GENERATED_FQNS:
        lines.append(
            f"{indent}jobject jni_{name} = {name} ? {name}->obj_ : nullptr;")
        lines.append(
            f"{indent}LOGD(\"param {name} (typed obj) = %p\", jni_{name});")
        lines.append(
            f"{indent}LOGV_PTR(\"{name}\", jni_{name});")

    elif cpp_type_for_param(p) == "StratumObject*":
        lines.append(
            f"{indent}jobject jni_{name} = {name} ? {name}->obj_ : nullptr;")
        lines.append(
            f"{indent}LOGD(\"param {name} (StratumObject) = %p\", jni_{name});")
        lines.append(
            f"{indent}LOGV_PTR(\"{name}\", jni_{name});")

    elif java_type == "[B":
        lines += [
            f"{indent}jbyteArray jni_{name} = "
            f"env->NewByteArray((jsize){name}.size());",
            f"{indent}if ({name}.size() > 0) env->SetByteArrayRegion(",
            f"{indent}    jni_{name}, 0, (jsize){name}.size(),",
            f"{indent}    reinterpret_cast<const jbyte*>({name}.c_str()));",
            f"{indent}LOGD(\"param {name} (bytes) len=%zu\", {name}.size());",
            f"{indent}LOGV_INT(\"{name}_len\", (int64_t){name}.size());",
        ]

    # [A37] jchar array — UTF-16 u16string
    elif java_type == "[C":
        lines += [
            f"{indent}// [A37] jchar array from u16string",
            f"{indent}jcharArray jni_{name} = env->NewCharArray((jsize){name}.size());",
            f"{indent}if (!{name}.empty()) env->SetCharArrayRegion(",
            f"{indent}    jni_{name}, 0, (jsize){name}.size(),",
            f"{indent}    reinterpret_cast<const jchar*>({name}.data()));",
            f"{indent}LOGV_INT(\"{name}_len\", (int64_t){name}.size());",
        ]

    elif java_type in ARRAY_TYPE_MAP:
        arr_jtype, elem_jtype, region_suffix, cpp_t, _ = ARRAY_TYPE_MAP[java_type]
        lines += [
            f"{indent}{arr_jtype} jni_{name} = "
            f"env->New{region_suffix}Array((jsize){name}.size());",
            f"{indent}if (!{name}.empty()) env->Set{region_suffix}ArrayRegion(",
            f"{indent}    jni_{name}, 0, (jsize){name}.size(),",
            f"{indent}    reinterpret_cast<const {elem_jtype}*>({name}.data()));",
            f"{indent}LOGD(\"param {name} (array) len=%zu\", {name}.size());",
            f"{indent}LOGV_INT(\"{name}_len\", (int64_t){name}.size());",
        ]

    elif java_type and (java_type.startswith("[L") or java_type.startswith("[[")):
        lines += [
            f"{indent}jsize jni_{name}_len = (jsize)nb::len({name});",
            f"{indent}jobjectArray jni_{name} = env->NewObjectArray(",
            f"{indent}    jni_{name}_len, g_object_class, nullptr);",
            f"{indent}for (jsize _i = 0; _i < jni_{name}_len; ++_i) {{",
            f"{indent}    auto _item = {name}[_i];",
            f"{indent}    jobject _jitem = nullptr;",
            f"{indent}    if (nb::isinstance<nb::str>(_item))",
            f"{indent}        _jitem = env->NewStringUTF("
            f"nb::cast<std::string>(_item).c_str());",
            f"{indent}    else if (nb::isinstance<StratumObject>(_item))",
            f"{indent}        _jitem = nb::cast<StratumObject*>(_item)->obj_;",
            f"{indent}    env->SetObjectArrayElement(jni_{name}, _i, _jitem);",
            f"{indent}    if (_jitem && nb::isinstance<nb::str>(_item))",
            f"{indent}        env->DeleteLocalRef(_jitem);",
            f"{indent}}}",
            f"{indent}LOGD(\"param {name} (objarray) len=%d\", jni_{name}_len);",
            f"{indent}LOGV_INT(\"{name}_len\", (int64_t)jni_{name}_len);",
        ]

    elif conv in ("direct", "long_safe"):
        # [A37] jchar direct — convert Python str char to jchar
        if jtype == "jchar":
            lines += [
                f"{indent}// [A37] jchar from Python str",
                f"{indent}jchar jni_{name} = ({name}.empty()) ? 0 :",
                f"{indent}    (jchar)(uint16_t)(unsigned char){name}[0];",
                f"{indent}LOGV_INT(\"{name}\", (int64_t)jni_{name});",
            ]
        else:
            cast_type = "jlong" if conv == "long_safe" else jtype
            lines.append(
                f"{indent}{cast_type} jni_{name} = "
                f"static_cast<{cast_type}>({name});")
            lines.append(
                f"{indent}LOGV_INT(\"{name}\", (int64_t)jni_{name});")

    else:
        lines.append(
            f"{indent}{jtype} jni_{name} = static_cast<{jtype}>({name});")
        lines.append(
            f"{indent}LOGV_INT(\"{name}\", (int64_t)jni_{name});")


def emit_param_cleanup(p: dict, lines: list, indent: str = "    ") -> None:
    """[FIX-3] callable_to_proxy returns NewGlobalRef — must DeleteGlobalRef."""
    name      = sanitize_id(p["name"])
    jtype     = p.get("jni_type", "")
    java_type = p.get("java_type", "")
    conv      = p.get("conversion", "")

    if conv == "abstract_adapter":
        lines.append(f"{indent}if (jni_{name}) env->DeleteGlobalRef(jni_{name});")
    elif conv == "callable_to_proxy":
        lines.append(f"{indent}if (jni_{name}) env->DeleteGlobalRef(jni_{name});")
    elif conv == "string_in" or jtype == "jstring":
        lines.append(f"{indent}env->DeleteLocalRef(jni_{name});")
    elif is_charsequence_param(p):
        lines.append(f"{indent}env->DeleteLocalRef(jni_{name});")
    elif (java_type in ARRAY_TYPE_MAP
          or (java_type and (java_type.startswith("[L") or java_type.startswith("[[")))):
        lines.append(f"{indent}env->DeleteLocalRef(jni_{name});")
    elif p.get("is_varargs", False):
        lines.append(f"{indent}env->DeleteLocalRef(jni_{name});")


def jni_args(params: list) -> str:
    """[FIX-14] Index-stable ordering."""
    return ", ".join(f"jni_{sanitize_id(p['name'])}" for p in params)


def emit_return_conversion(ret_decl: str, ret_conv: str, lines: list,
                            m: dict, indent: str = "    ",
                            method_name: str = "") -> None:
    """[FIX-25 / A37 / A39 / A40 / A41 / A14] Return conversion with LOGV."""
    mname = method_name or m.get("name", "?")

    if ret_decl == "void":
        emit_exception_check(lines, indent, "void")
        lines.append(f"{indent}LOGT_JNI_TO_PY(\"\", \"{mname}\", \"void\");")
        return

    sig     = reconstruct_jni_sig(m)
    ret_sig = ret_sig_from_jni(sig)

    emit_exception_check(lines, indent, ret_decl)

    if ret_conv == "bool_out":
        lines.append(f"{indent}bool _bret = (raw != JNI_FALSE);")
        lines.append(f"{indent}LOGD(\"return (bool) = %d\", (int)_bret);")
        lines.append(f"{indent}LOGV_RET_BOOL(\"{mname}\", _bret);")
        lines.append(f"{indent}LOGT_JNI_TO_PY(\"\", \"{mname}\", \"bool\");")
        lines.append(f"{indent}return _bret;")

    # [A37] jchar return → single char Python str
    elif ret_decl == "std::string" and get_return_jni(m) == "jchar":
        lines += [
            f"{indent}// [A37] jchar return → single Python str char",
            f"{indent}std::string _cres;",
            f"{indent}uint16_t _cval = (uint16_t)(jchar)raw;",
            f"{indent}if (_cval < 0x80) {{ _cres += (char)_cval; }}",
            f"{indent}else if (_cval < 0x800) {{",
            f"{indent}    _cres += (char)(0xC0 | (_cval >> 6));",
            f"{indent}    _cres += (char)(0x80 | (_cval & 0x3F));",
            f"{indent}}} else {{",
            f"{indent}    _cres += (char)(0xE0 | (_cval >> 12));",
            f"{indent}    _cres += (char)(0x80 | ((_cval >> 6) & 0x3F));",
            f"{indent}    _cres += (char)(0x80 | (_cval & 0x3F));",
            f"{indent}}}",
            f"{indent}LOGV_RET_STR(\"{mname}\", _cres);",
            f"{indent}LOGT_JNI_TO_PY(\"\", \"{mname}\", \"jchar->str\");",
            f"{indent}return _cres;",
        ]

    # [A37] jchar array return → u16string
    elif ret_decl == "std::u16string":
        lines += [
            f"{indent}if (!raw) {{ LOGT_JNI_TO_PY(\"\", \"{mname}\", \"u16string(null)\"); return std::u16string(); }}",
            f"{indent}jcharArray _carr = (jcharArray)raw;",
            f"{indent}jsize _clen = env->GetArrayLength(_carr);",
            f"{indent}std::u16string _cstr(_clen, u'\\0');",
            f"{indent}env->GetCharArrayRegion(_carr, 0, _clen, (jchar*)_cstr.data());",
            f"{indent}env->DeleteLocalRef(_carr);",
            f"{indent}LOGD(\"return u16string len=%d\", _clen);",
            f"{indent}LOGT_JNI_TO_PY(\"\", \"{mname}\", \"u16string\");",
            f"{indent}return _cstr;",
        ]

    elif ret_decl == "std::string" or is_string_return(m):
        lines += [
            f"{indent}if (!raw) {{ LOGD(\"return string = null\"); "
            f"LOGT_JNI_TO_PY(\"\", \"{mname}\", \"string(null)\"); return \"\"; }}",
            f"{indent}// [PATCH-UTF8-RET] GetStringChars (UTF-16) → standard UTF-8",
            f"{indent}// GetStringUTFChars returns MUTF-8 which breaks emoji in Python",
            f"{indent}{{",
            f"{indent}    jsize _slen = env->GetStringLength((jstring)raw);",
            f"{indent}    const jchar* _sjc = env->GetStringChars((jstring)raw, nullptr);",
            f"{indent}    std::string _res;",
            f"{indent}    if (_sjc && _slen > 0) {{",
            f"{indent}        _res.reserve((size_t)_slen * 3);",
            f"{indent}        for (jsize _si = 0; _si < _slen; ) {{",
            f"{indent}            uint32_t _cp;",
            f"{indent}            uint16_t _c1 = (uint16_t)_sjc[_si++];",
            f"{indent}            if (_c1 >= 0xD800u && _c1 <= 0xDBFFu && _si < _slen) {{",
            f"{indent}                uint16_t _c2 = (uint16_t)_sjc[_si];",
            f"{indent}                if (_c2 >= 0xDC00u && _c2 <= 0xDFFFu) {{",
            f"{indent}                    _cp = 0x10000u + (((uint32_t)(_c1 - 0xD800u)) << 10)",
            f"{indent}                               + (uint32_t)(_c2 - 0xDC00u);",
            f"{indent}                    ++_si;",
            f"{indent}                }} else {{ _cp = _c1; }}",
            f"{indent}            }} else {{ _cp = _c1; }}",
            f"{indent}            if (_cp < 0x80u) {{",
            f"{indent}                _res += (char)_cp;",
            f"{indent}            }} else if (_cp < 0x800u) {{",
            f"{indent}                _res += (char)(0xC0u | (_cp >> 6));",
            f"{indent}                _res += (char)(0x80u | (_cp & 0x3Fu));",
            f"{indent}            }} else if (_cp < 0x10000u) {{",
            f"{indent}                _res += (char)(0xE0u | (_cp >> 12));",
            f"{indent}                _res += (char)(0x80u | ((_cp >> 6) & 0x3Fu));",
            f"{indent}                _res += (char)(0x80u | (_cp & 0x3Fu));",
            f"{indent}            }} else {{",
            f"{indent}                _res += (char)(0xF0u | (_cp >> 18));",
            f"{indent}                _res += (char)(0x80u | ((_cp >> 12) & 0x3Fu));",
            f"{indent}                _res += (char)(0x80u | ((_cp >> 6)  & 0x3Fu));",
            f"{indent}                _res += (char)(0x80u | (_cp & 0x3Fu));",
            f"{indent}            }}",
            f"{indent}        }}",
            f"{indent}    }}",
            f"{indent}    if (_sjc) env->ReleaseStringChars((jstring)raw, _sjc);",
            f"{indent}    env->DeleteLocalRef((jobject)raw);",
            f"{indent}    LOGD(\"return string(utf16) len=%d\", (int)_slen);",
            f"{indent}    LOGV_RET_STR(\"{mname}\", _res);",
            f"{indent}    LOGT_JNI_TO_PY(\"\", \"{mname}\", \"string(utf16)\");",
            f"{indent}    return _res;",
            f"{indent}}}",
        ]

    elif ret_decl == "nb::bytes" or ret_sig == "[B":
        lines += [
            f"{indent}if (!raw) {{ LOGT_JNI_TO_PY(\"\", \"{mname}\", \"bytes(null)\"); return nb::bytes(\"\", 0); }}",
            f"{indent}jbyteArray _barr = (jbyteArray)raw;",
            f"{indent}jsize _blen = env->GetArrayLength(_barr);",
            f"{indent}jbyte* _bptr = env->GetByteArrayElements(_barr, nullptr);",
            f"{indent}nb::bytes _bres("
            f"reinterpret_cast<const char*>(_bptr), (size_t)_blen);",
            f"{indent}env->ReleaseByteArrayElements(_barr, _bptr, JNI_ABORT);",
            f"{indent}env->DeleteLocalRef(_barr);",
            f"{indent}LOGD(\"return bytes len=%d\", _blen);",
            f"{indent}LOGV_RET_INT(\"{mname}\", (int64_t)_blen);",
            f"{indent}LOGT_JNI_TO_PY(\"\", \"{mname}\", \"bytes\");",
            f"{indent}return _bres;",
        ]

    elif ret_sig in ARRAY_TYPE_MAP and ret_sig not in ("[B", "[C"):
        arr_jtype, elem_jtype, region_suffix, cpp_t, _ = ARRAY_TYPE_MAP[ret_sig]
        lines += [
            f"{indent}if (!raw) {{ LOGT_JNI_TO_PY(\"\", \"{mname}\", \"array(null)\"); return {{}}; }}",
            f"{indent}{arr_jtype} _arr = ({arr_jtype})raw;",
            f"{indent}jsize _len = env->GetArrayLength(_arr);",
            f"{indent}std::vector<{cpp_t}> _vec(_len);",
            f"{indent}{elem_jtype}* _elems = "
            f"env->Get{region_suffix}ArrayElements(_arr, nullptr);",
            f"{indent}for (jsize _i = 0; _i < _len; ++_i)"
            f" _vec[_i] = static_cast<{cpp_t}>(_elems[_i]);",
            f"{indent}env->Release{region_suffix}ArrayElements"
            f"(_arr, _elems, JNI_ABORT);",
            f"{indent}env->DeleteLocalRef(_arr);",
            f"{indent}LOGD(\"return array len=%d\", _len);",
            f"{indent}LOGV_RET_INT(\"{mname}\", (int64_t)_len);",
            f"{indent}LOGT_JNI_TO_PY(\"\", \"{mname}\", \"array\");",
            f"{indent}return _vec;",
        ]

    elif is_string_array_sig(ret_sig):
        lines += [
            f"{indent}if (!raw) {{ LOGT_JNI_TO_PY(\"\", \"{mname}\", \"string[](null)\"); return nb::list(); }}",
            f"{indent}jobjectArray _sarr = (jobjectArray)raw;",
            f"{indent}jsize _slen = env->GetArrayLength(_sarr);",
            f"{indent}nb::list _slist;",
            f"{indent}for (jsize _i = 0; _i < _slen; ++_i) {{",
            f"{indent}    jstring _s = "
            f"(jstring)env->GetObjectArrayElement(_sarr, _i);",
            f"{indent}    if (_s) {{",
            f"{indent}        const char* _c = "
            f"env->GetStringUTFChars(_s, nullptr);",
            f"{indent}        _slist.append(nb::str(_c));",
            f"{indent}        env->ReleaseStringUTFChars(_s, _c);",
            f"{indent}        env->DeleteLocalRef(_s);",
            f"{indent}    }} else _slist.append(nb::none());",
            f"{indent}}}",
            f"{indent}env->DeleteLocalRef(_sarr);",
            f"{indent}LOGD(\"return string[] len=%d\", _slen);",
            f"{indent}LOGV_RET_INT(\"{mname}\", (int64_t)_slen);",
            f"{indent}LOGT_JNI_TO_PY(\"\", \"{mname}\", \"string[]\");",
            f"{indent}return _slist;",
        ]

    elif ret_sig.startswith("["):
        lines += [
            f"{indent}if (!raw) {{ LOGT_JNI_TO_PY(\"\", \"{mname}\", \"object[](null)\"); return nb::list(); }}",
            f"{indent}jobjectArray _oarr = (jobjectArray)raw;",
            f"{indent}jsize _olen = env->GetArrayLength(_oarr);",
            f"{indent}nb::list _olist;",
            f"{indent}for (jsize _i = 0; _i < _olen; ++_i) {{",
            f"{indent}    jobject _elem = "
            f"env->GetObjectArrayElement(_oarr, _i);",
            f"{indent}    if (_elem) {{",
            f"{indent}        jobject _gelem = env->NewGlobalRef(_elem);",
            f"{indent}        env->DeleteLocalRef(_elem);",
            f"{indent}        _olist.append(nb::cast("
            f"new StratumObject(_gelem),",
            f"{indent}                               "
            f"nb::rv_policy::take_ownership));",
            f"{indent}    }} else _olist.append(nb::none());",
            f"{indent}}}",
            f"{indent}env->DeleteLocalRef(_oarr);",
            f"{indent}LOGD(\"return object[] len=%d\", _olen);",
            f"{indent}LOGV_RET_INT(\"{mname}\", (int64_t)_olen);",
            f"{indent}LOGT_JNI_TO_PY(\"\", \"{mname}\", \"object[]\");",
            f"{indent}return _olist;",
        ]

    # [A14] Collection return → nb::list
    elif ret_decl == "nb::list":
        lines += [
            f"{indent}if (!raw) {{ LOGT_JNI_TO_PY(\"\", \"{mname}\", \"list(null)\"); return nb::list(); }}",
            f"{indent}// [A14] Convert Java Collection to Python list",
            f"{indent}nb::list _coll_list;",
            f"{indent}{{",
            f"{indent}    jclass _lcls = env->GetObjectClass(raw);",
            f"{indent}    jmethodID _lsize = env->GetMethodID(_lcls, \"size\", \"()I\");",
            f"{indent}    jmethodID _lget = env->GetMethodID(_lcls, \"get\", \"(I)Ljava/lang/Object;\");",
            f"{indent}    if (_lsize && _lget) {{",
            f"{indent}        jint _llen = env->CallIntMethod(raw, _lsize);",
            f"{indent}        LOGD(\"Collection size=%d\", _llen);",
            f"{indent}        for (jint _li = 0; _li < _llen; ++_li) {{",
            f"{indent}            jobject _litem = env->CallObjectMethod(raw, _lget, _li);",
            f"{indent}            if (!_litem) {{ _coll_list.append(nb::none()); continue; }}",
            f"{indent}            if (g_jstring_class && env->IsInstanceOf(_litem, g_jstring_class)) {{",
            f"{indent}                const char* _ls = env->GetStringUTFChars((jstring)_litem, nullptr);",
            f"{indent}                _coll_list.append(nb::str(_ls));",
            f"{indent}                env->ReleaseStringUTFChars((jstring)_litem, _ls);",
            f"{indent}                env->DeleteLocalRef(_litem);",
            f"{indent}            }} else {{",
            f"{indent}                jobject _lgref = env->NewGlobalRef(_litem);",
            f"{indent}                env->DeleteLocalRef(_litem);",
            f"{indent}                _coll_list.append(nb::cast(new StratumObject(_lgref), nb::rv_policy::take_ownership));",
            f"{indent}            }}",
            f"{indent}        }}",
            f"{indent}    }} else {{",
            f"{indent}        env->ExceptionClear();",
            f"{indent}        jobject _lgref = env->NewGlobalRef(raw);",
            f"{indent}        env->DeleteLocalRef(raw);",
            f"{indent}        _coll_list.append(nb::cast(new StratumObject(_lgref), nb::rv_policy::take_ownership));",
            f"{indent}    }}",
            f"{indent}    env->DeleteLocalRef(_lcls);",
            f"{indent}    env->DeleteLocalRef(raw);",
            f"{indent}}}",
            f"{indent}LOGT_JNI_TO_PY(\"\", \"{mname}\", \"list\");",
            f"{indent}return _coll_list;",
        ]

    # [A14] Map return → nb::dict
    elif ret_decl == "nb::dict":
        lines += [
            f"{indent}if (!raw) {{ LOGT_JNI_TO_PY(\"\", \"{mname}\", \"dict(null)\"); return nb::dict(); }}",
            f"{indent}// [A14] Convert Java Map to Python dict",
            f"{indent}nb::dict _map_dict;",
            f"{indent}{{",
            f"{indent}    jclass _mcls = env->GetObjectClass(raw);",
            f"{indent}    jmethodID _mentrySet = env->GetMethodID(_mcls, \"entrySet\", \"()Ljava/util/Set;\");",
            f"{indent}    env->DeleteLocalRef(_mcls);",
            f"{indent}    if (_mentrySet) {{",
            f"{indent}        jobject _es = env->CallObjectMethod(raw, _mentrySet);",
            f"{indent}        jclass _escls = env->GetObjectClass(_es);",
            f"{indent}        jmethodID _esiter = env->GetMethodID(_escls, \"iterator\", \"()Ljava/util/Iterator;\");",
            f"{indent}        env->DeleteLocalRef(_escls);",
            f"{indent}        jobject _iter = env->CallObjectMethod(_es, _esiter);",
            f"{indent}        env->DeleteLocalRef(_es);",
            f"{indent}        jclass _icls = env->GetObjectClass(_iter);",
            f"{indent}        jmethodID _ihasNext = env->GetMethodID(_icls, \"hasNext\", \"()Z\");",
            f"{indent}        jmethodID _inext = env->GetMethodID(_icls, \"next\", \"()Ljava/lang/Object;\");",
            f"{indent}        env->DeleteLocalRef(_icls);",
            f"{indent}        while (env->CallBooleanMethod(_iter, _ihasNext)) {{",
            f"{indent}            jobject _entry = env->CallObjectMethod(_iter, _inext);",
            f"{indent}            jclass _ecls2 = env->GetObjectClass(_entry);",
            f"{indent}            jmethodID _ekey = env->GetMethodID(_ecls2, \"getKey\", \"()Ljava/lang/Object;\");",
            f"{indent}            jmethodID _eval = env->GetMethodID(_ecls2, \"getValue\", \"()Ljava/lang/Object;\");",
            f"{indent}            env->DeleteLocalRef(_ecls2);",
            f"{indent}            jobject _ek = env->CallObjectMethod(_entry, _ekey);",
            f"{indent}            jobject _ev = env->CallObjectMethod(_entry, _eval);",
            f"{indent}            env->DeleteLocalRef(_entry);",
            f"{indent}            nb::object _pyk, _pyv;",
            f"{indent}            if (_ek && g_jstring_class && env->IsInstanceOf(_ek, g_jstring_class)) {{",
            f"{indent}                const char* _kc = env->GetStringUTFChars((jstring)_ek, nullptr);",
            f"{indent}                _pyk = nb::str(_kc);",
            f"{indent}                env->ReleaseStringUTFChars((jstring)_ek, _kc);",
            f"{indent}                env->DeleteLocalRef(_ek);",
            f"{indent}            }} else if (_ek) {{",
            f"{indent}                _pyk = nb::cast(new StratumObject(env->NewGlobalRef(_ek)), nb::rv_policy::take_ownership);",
            f"{indent}                env->DeleteLocalRef(_ek);",
            f"{indent}            }} else _pyk = nb::none();",
            f"{indent}            if (_ev) {{",
            f"{indent}                _pyv = nb::cast(new StratumObject(env->NewGlobalRef(_ev)), nb::rv_policy::take_ownership);",
            f"{indent}                env->DeleteLocalRef(_ev);",
            f"{indent}            }} else _pyv = nb::none();",
            f"{indent}            _map_dict[_pyk] = _pyv;",
            f"{indent}        }}",
            f"{indent}        env->DeleteLocalRef(_iter);",
            f"{indent}    }}",
            f"{indent}    env->DeleteLocalRef(raw);",
            f"{indent}}}",
            f"{indent}LOGT_JNI_TO_PY(\"\", \"{mname}\", \"dict\");",
            f"{indent}return _map_dict;",
        ]

    # [A41] ByteBuffer return — zero-copy direct, or bytes for heap
    elif ret_decl == "nb::object":
        lines += [
            f"{indent}if (!raw) {{ LOGT_JNI_TO_PY(\"\", \"{mname}\", \"bytebuffer(null)\"); return nb::none(); }}",
            f"{indent}// [A41] ByteBuffer — try direct (zero-copy) first",
            f"{indent}void* _bb_addr = env->GetDirectBufferAddress(raw);",
            f"{indent}if (_bb_addr != nullptr) {{",
            f"{indent}    jlong _bb_cap = env->GetDirectBufferCapacity(raw);",
            f"{indent}    if (_bb_cap > 0) {{",
            f"{indent}        LOGD(\"ByteBuffer direct addr=%p cap=%lld\", _bb_addr, (long long)_bb_cap);",
            f"{indent}        LOGV(\"BYTEBUFFER_DIRECT addr=%p capacity=%lld\", _bb_addr, (long long)_bb_cap);",
            f"{indent}        // Keep the raw reference alive as a global ref",
            f"{indent}        jobject _bb_gref = env->NewGlobalRef(raw);",
            f"{indent}        env->DeleteLocalRef(raw);",
            f"{indent}        // Create a nanobind capsule that deletes the global ref when Python garbage collects the memoryview",
            f"{indent}        nb::capsule _owner(_bb_gref, [](void* p) noexcept {{",
            f"{indent}            JNIEnv* e = get_env_safe();",
            f"{indent}            if (e) e->DeleteGlobalRef((jobject)p);",
            f"{indent}        }});",
            f"{indent}        // Return as mutable memoryview tied to the capsule lifecycle",
            f"{indent}        Py_buffer _view;",
            f"{indent}        if (PyBuffer_FillInfo(&_view, _owner.ptr(), _bb_addr, (Py_ssize_t)_bb_cap, 0, 0) == -1) {{",
            f"{indent}            PyErr_Clear(); return nb::none();",
            f"{indent}        }}",
            f"{indent}        PyObject* _mview = PyMemoryView_FromBuffer(&_view);",
            f"{indent}        if (!_mview) return nb::none();",
            f"{indent}        nb::object _mview_obj = nb::borrow(_mview);",
            f"{indent}        Py_DECREF(_mview);",
            f"{indent}        return _mview_obj;",
            f"{indent}    }}",
            f"{indent}}}",
            f"{indent}// Fallback: heap ByteBuffer — copy via array()",
            f"{indent}LOGV(\"BYTEBUFFER_HEAP fallback\");",
            f"{indent}jclass _bbcls = env->GetObjectClass(raw);",
            f"{indent}jmethodID _bbarr = env->GetMethodID(_bbcls, \"array\", \"()[B\");",
            f"{indent}env->DeleteLocalRef(_bbcls);",
            f"{indent}if (_bbarr) {{",
            f"{indent}    jbyteArray _bba = (jbyteArray)env->CallObjectMethod(raw, _bbarr);",
            f"{indent}    env->DeleteLocalRef(raw);",
            f"{indent}    if (_bba) {{",
            f"{indent}        jsize _bblen = env->GetArrayLength(_bba);",
            f"{indent}        jbyte* _bbp = env->GetByteArrayElements(_bba, nullptr);",
            f"{indent}        nb::bytes _bbb(reinterpret_cast<const char*>(_bbp), _bblen);",
            f"{indent}        env->ReleaseByteArrayElements(_bba, _bbp, JNI_ABORT);",
            f"{indent}        env->DeleteLocalRef(_bba);",
            f"{indent}        return _bbb;",
            f"{indent}    }}",
            f"{indent}}} else {{ env->ExceptionClear(); }}",
            f"{indent}env->DeleteLocalRef(raw);",
            f"{indent}return nb::none();",
        ]
    # [A39] Throwable return
    elif ret_decl == "StratumThrowable*":
        lines += [
            f"{indent}if (!raw) {{ LOGV_RET_PTR(\"{mname}\", nullptr); return nullptr; }}",
            f"{indent}// [A39] Throwable return",
            f"{indent}jobject _tgref = env->NewGlobalRef(raw);",
            f"{indent}env->DeleteLocalRef(raw);",
            f"{indent}LOGD(\"return StratumThrowable = %p\", _tgref);",
            f"{indent}LOGV_RET_PTR(\"{mname}\", _tgref);",
            f"{indent}LOGT_JNI_TO_PY(\"\", \"{mname}\", \"StratumThrowable*\");",
            f"{indent}return new StratumThrowable(_tgref);",
        ]

    # [A40] WeakReference return
    elif ret_decl == "StratumWeakObject*":
        lines += [
            f"{indent}if (!raw) {{ LOGV_RET_PTR(\"{mname}\", nullptr); return nullptr; }}",
            f"{indent}// [A40] WeakReference return",
            f"{indent}jobject _wgref = env->NewGlobalRef(raw);",
            f"{indent}env->DeleteLocalRef(raw);",
            f"{indent}LOGD(\"return StratumWeakObject = %p\", _wgref);",
            f"{indent}LOGV_RET_PTR(\"{mname}\", _wgref);",
            f"{indent}LOGT_JNI_TO_PY(\"\", \"{mname}\", \"StratumWeakObject*\");",
            f"{indent}return new StratumWeakObject(_wgref, env);",
        ]

    elif ret_decl.endswith("*") and ret_decl not in ("StratumObject*", "StratumThrowable*", "StratumWeakObject*"):
        inner = ret_decl[:-1]
        lines += [
            f"{indent}if (!raw) {{ LOGV_RET_PTR(\"{mname}\", nullptr); "
            f"LOGT_JNI_TO_PY(\"\", \"{mname}\", \"typed*(null)\"); return nullptr; }}",
            f"{indent}// [Action35] NewGlobalRef so each wrapper owns its own ref",
            f"{indent}jobject _typed_gref = env->NewGlobalRef(raw);",
            f"{indent}env->DeleteLocalRef((jobject)raw);",
            f"{indent}auto* _w = new {inner}(_typed_gref);",
            f"{indent}LOGD(\"return typed obj = %p\", _typed_gref);",
            f"{indent}LOGV_RET_PTR(\"{mname}\", _typed_gref);",
            f"{indent}LOGT_JNI_TO_PY(\"\", \"{mname}\", \"typed*\");",
            f"{indent}return _w;",
        ]

    elif ret_decl == "StratumObject*":
        lines += [
            f"{indent}if (!raw) {{ LOGV_RET_PTR(\"{mname}\", nullptr); "
            f"LOGT_JNI_TO_PY(\"\", \"{mname}\", \"StratumObject*(null)\"); return nullptr; }}",
            f"{indent}jobject _gref = env->NewGlobalRef(raw);",
            f"{indent}env->DeleteLocalRef(raw);",
            f"{indent}LOGD(\"return StratumObject = %p\", _gref);",
            f"{indent}LOGV_RET_PTR(\"{mname}\", _gref);",
            f"{indent}LOGT_JNI_TO_PY(\"\", \"{mname}\", \"StratumObject*\");",
            f"{indent}return new StratumObject(_gref);",
        ]

    else:
        lines.append(f"{indent}LOGD(\"return primitive\");")
        lines.append(f"{indent}LOGV_RET_INT(\"{mname}\", (int64_t)raw);")
        lines.append(f"{indent}LOGT_JNI_TO_PY(\"\", \"{mname}\", \"primitive\");")
        lines.append(f"{indent}return static_cast<{ret_decl}>(raw);")


# =============================================================================
# Field Accessors Generator
# =============================================================================

def emit_field_accessors(
    fqn: str,
    fields: list,
    prefix: str,
    sname: str,
    lines: list,
) -> List[Tuple[str, str, str, str, bool]]:
    """[FIX-9] Local->global ref. [FIX-19] LOGD. [FIX-25] LOGV."""
    nb_entries = []

    for f in fields:
        fname     = f.get("name", "")
        fsig      = f.get("jni_signature", f.get("jni_type", ""))
        is_static = f.get("is_static", False)
        is_final  = f.get("is_final", False)
        if not fname or not fsig:
            continue

        safe_name = sanitize_id(fname)
        fid_var   = field_id_var(fqn, fname)

        if fsig in FIELD_TYPE_MAP:
            get_inst, set_inst, get_stat, set_stat, cpp_t = FIELD_TYPE_MAP[fsig]
        elif fsig == "Ljava/lang/String;":
            get_inst, set_inst, get_stat, set_stat, cpp_t = \
                FIELD_TYPE_MAP["Ljava/lang/String;"]
        else:
            get_inst, set_inst = "GetObjectField",       "SetObjectField"
            get_stat, set_stat = "GetStaticObjectField", "SetStaticObjectField"
            cpp_t = "StratumObject*"

        getter_fn = f"field_get_{prefix}_{safe_name}"
        setter_fn = f"field_set_{prefix}_{safe_name}" if not is_final else ""

        # ── Getter ────────────────────────────────────────────────────────────
        if is_static:
            lines += [
                f"static {cpp_t} {getter_fn}() {{",
                f"    JNIEnv* env = get_env();",
                f"    if (!env) {{ LOGE(\"get_env null in {getter_fn}\"); "
                f"{null_return(cpp_t)} }}",
                f"    ensure_{prefix}_init(env);",
                f"    if (!{fid_var} || !g_{prefix}_class) {{",
                f"        LOGE(\"field or class null in {getter_fn}\");",
                f"        {null_return(cpp_t)} }}",
                f"    LOGD(\"getting static field {fname} on {fqn}\");",
                f"    LOGV(\"FIELD_GET static {fqn}#{fname}\");",
            ]
            if cpp_t == "std::string":
                lines += [
                    f"    jstring _r = (jstring)"
                    f"env->{get_stat}(g_{prefix}_class, {fid_var});",
                    f"    if (!_r) return \"\";",
                    f"    const char* _c = env->GetStringUTFChars(_r, nullptr);",
                    f"    std::string _s(_c); env->ReleaseStringUTFChars(_r, _c);",
                    f"    env->DeleteLocalRef(_r);",
                    f"    LOGD(\"field {fname} = %s\", _s.c_str());",
                    f"    LOGV_RET_STR(\"{fname}\", _s);",
                    f"    return _s;",
                ]
            elif cpp_t == "StratumObject*":
                lines += [
                    f"    jobject _r = "
                    f"env->{get_stat}(g_{prefix}_class, {fid_var});",
                    f"    if (!_r) return nullptr;",
                    f"    jobject _g = env->NewGlobalRef(_r);"
                    f" env->DeleteLocalRef(_r);",
                    f"    LOGD(\"field {fname} = %p\", _g);",
                    f"    LOGV_RET_PTR(\"{fname}\", _g);",
                    f"    return new StratumObject(_g);",
                ]
            elif cpp_t == "bool":
                lines += [
                    f"    bool _v = env->{get_stat}"
                    f"(g_{prefix}_class, {fid_var}) != JNI_FALSE;",
                    f"    LOGD(\"field {fname} = %d\", (int)_v);",
                    f"    LOGV_RET_BOOL(\"{fname}\", _v);",
                    f"    return _v;",
                ]
            else:
                lines += [
                    f"    auto _v = static_cast<{cpp_t}>"
                    f"(env->{get_stat}(g_{prefix}_class, {fid_var}));",
                    f"    LOGD(\"field {fname} retrieved\");",
                    f"    LOGV_RET_INT(\"{fname}\", (int64_t)_v);",
                    f"    return _v;",
                ]
        else:
            lines += [
                f"static {cpp_t} {getter_fn}({sname}* self) {{",
                f"    if (!self || !self->obj_) {{",
                f"        LOGE(\"null self in {getter_fn}\");",
                f"        {null_return(cpp_t)} }}",
                f"    JNIEnv* env = get_env();",
                f"    if (!env) {{ LOGE(\"get_env null in {getter_fn}\"); "
                f"{null_return(cpp_t)} }}",
                f"    ensure_{prefix}_init(env);",
                f"    if (!{fid_var}) {{",
                f"        LOGE(\"fieldID null in {getter_fn}\");",
                f"        {null_return(cpp_t)} }}",
                f"    LOGD(\"getting instance field {fname} on {fqn}\");",
                f"    LOGV(\"FIELD_GET instance {fqn}#{fname} self=%p\", self->obj_);",
            ]
            if cpp_t == "std::string":
                lines += [
                    f"    jstring _r = (jstring)"
                    f"env->{get_inst}(self->obj_, {fid_var});",
                    f"    if (!_r) return \"\";",
                    f"    const char* _c = env->GetStringUTFChars(_r, nullptr);",
                    f"    std::string _s(_c); env->ReleaseStringUTFChars(_r, _c);",
                    f"    env->DeleteLocalRef(_r);",
                    f"    LOGD(\"field {fname} = %s\", _s.c_str());",
                    f"    LOGV_RET_STR(\"{fname}\", _s);",
                    f"    return _s;",
                ]
            elif cpp_t == "StratumObject*":
                lines += [
                    f"    jobject _r = env->{get_inst}(self->obj_, {fid_var});",
                    f"    if (!_r) return nullptr;",
                    f"    jobject _g = env->NewGlobalRef(_r);"
                    f" env->DeleteLocalRef(_r);",
                    f"    LOGD(\"field {fname} = %p\", _g);",
                    f"    LOGV_RET_PTR(\"{fname}\", _g);",
                    f"    return new StratumObject(_g);",
                ]
            elif cpp_t == "bool":
                lines += [
                    f"    bool _v = env->{get_inst}"
                    f"(self->obj_, {fid_var}) != JNI_FALSE;",
                    f"    LOGD(\"field {fname} = %d\", (int)_v);",
                    f"    LOGV_RET_BOOL(\"{fname}\", _v);",
                    f"    return _v;",
                ]
            else:
                lines += [
                    f"    auto _v = static_cast<{cpp_t}>"
                    f"(env->{get_inst}(self->obj_, {fid_var}));",
                    f"    LOGD(\"field {fname} retrieved\");",
                    f"    LOGV_RET_INT(\"{fname}\", (int64_t)_v);",
                    f"    return _v;",
                ]

        lines += ["}", ""]

        # ── Setter ────────────────────────────────────────────────────────────
        if setter_fn:
            if is_static:
                lines += [
                    f"static void {setter_fn}({cpp_t} val) {{",
                    f"    JNIEnv* env = get_env();",
                    f"    if (!env) {{ LOGE(\"get_env null in {setter_fn}\"); return; }}",
                    f"    ensure_{prefix}_init(env);",
                    f"    if (!{fid_var} || !g_{prefix}_class) {{",
                    f"        LOGE(\"field or class null in {setter_fn}\"); return; }}",
                    f"    LOGD(\"setting static field {fname} on {fqn}\");",
                    f"    LOGV(\"FIELD_SET static {fqn}#{fname}\");",
                ]
                if cpp_t == "std::string":
                    lines += [
                        f"    jstring _s = env->NewStringUTF(val.c_str());",
                        f"    env->{set_stat}(g_{prefix}_class, {fid_var}, _s);",
                        f"    env->DeleteLocalRef(_s);",
                    ]
                elif cpp_t == "StratumObject*":
                    lines.append(
                        f"    env->{set_stat}(g_{prefix}_class, {fid_var},"
                        f" val ? val->obj_ : nullptr);")
                elif cpp_t == "bool":
                    lines.append(
                        f"    env->{set_stat}(g_{prefix}_class, {fid_var},"
                        f" val ? JNI_TRUE : JNI_FALSE);")
                else:
                    jcast = FIELD_CAST_MAP.get(cpp_t, cpp_t)
                    lines.append(
                        f"    env->{set_stat}(g_{prefix}_class, {fid_var},"
                        f" static_cast<{jcast}>(val));")
            else:
                lines += [
                    f"static void {setter_fn}({sname}* self, {cpp_t} val) {{",
                    f"    if (!self || !self->obj_) {{",
                    f"        LOGE(\"null self in {setter_fn}\"); return; }}",
                    f"    JNIEnv* env = get_env();",
                    f"    if (!env) {{ LOGE(\"get_env null in {setter_fn}\"); return; }}",
                    f"    ensure_{prefix}_init(env);",
                    f"    if (!{fid_var}) {{",
                    f"        LOGE(\"fieldID null in {setter_fn}\"); return; }}",
                    f"    LOGD(\"setting instance field {fname} on {fqn}\");",
                    f"    LOGV(\"FIELD_SET instance {fqn}#{fname} self=%p\", self->obj_);",
                ]
                if cpp_t == "std::string":
                    lines += [
                        f"    jstring _s = env->NewStringUTF(val.c_str());",
                        f"    env->{set_inst}(self->obj_, {fid_var}, _s);",
                        f"    env->DeleteLocalRef(_s);",
                    ]
                elif cpp_t == "StratumObject*":
                    lines.append(
                        f"    env->{set_inst}(self->obj_, {fid_var},"
                        f" val ? val->obj_ : nullptr);")
                elif cpp_t == "bool":
                    lines.append(
                        f"    env->{set_inst}(self->obj_, {fid_var},"
                        f" val ? JNI_TRUE : JNI_FALSE);")
                else:
                    jcast = FIELD_CAST_MAP.get(cpp_t, cpp_t)
                    lines.append(
                        f"    env->{set_inst}(self->obj_, {fid_var},"
                        f" static_cast<{jcast}>(val));")
            lines += ["}", ""]

        nb_entries.append((getter_fn, setter_fn, safe_name, cpp_t, is_static))

    return nb_entries


# =============================================================================
# Proxy Factory Generation
# =============================================================================

def emit_proxy_factory(fqn: str, mi: int, m: dict, lines: list) -> None:
    """
    [FIX-3 / Action7] Returns NewGlobalRef. LOGW on unknown dict keys.
    [STAGE-05.5] Also handles abstract_adapter params.
    """
    for p in m.get("params", []):
        conv  = p.get("conversion", "")
        pname = sanitize_id(p["name"])
        fn    = f"create_proxy_m{mi}_{pname}"

        # ── ABSTRACT ADAPTER ──────────────────────────────────────────────────
        if conv == "abstract_adapter":
            adj = p.get("adapter_jni", "")
            if not adj:
                print(f"  WARN  abstract_adapter param '{pname}' in "
                      f"{fqn}#{m['name']} missing adapter_jni — skipped")
                continue

            lines += [
                f"// Abstract adapter factory: {adj}",
                f"static jobject {fn}(JNIEnv* env, nb::object callbacks) {{",
                f"    LOGD(\"creating abstract adapter: {adj}\");",
                f"    static std::atomic<uint64_t> _aid{{0}};",
                f"    std::string _key = \"{fqn}#{m['name']}#{pname}_\""
                f" + std::to_string(++_aid);",
                f"",
                f"    if (nb::isinstance<nb::callable>(callbacks)) {{",
                f"        store_callback(_key,"
                f" nb::cast<nb::callable>(callbacks));",
                f"        LOGD(\"adapter: stored single callable key=%s\","
                f" _key.c_str());",
                f"    }} else if (nb::isinstance<nb::dict>(callbacks)) {{",
                f"        nb::dict _d = nb::cast<nb::dict>(callbacks);",
                f"        nb::list _ks = _d.keys();",
                f"        for (size_t _i = 0; _i < nb::len(_ks); ++_i) {{",
                f"            std::string _mk ="
                f" nb::cast<std::string>(_ks[_i]);",
                f"            store_callback(_key + \"#\" + _mk,",
                f"                nb::cast<nb::callable>(_d[_ks[_i]]));",
                f"            LOGD(\"adapter: stored callback key=%s#%s\","
                f" _key.c_str(), _mk.c_str());",
                f"        }}",
                f"    }}",
                f"",
                f"    jclass _cls = find_class(env, \"{adj}\");",
                f"    if (!_cls) {{",
                f"        env->ExceptionClear();",
                f"        LOGE(\"Adapter class not found: {adj}\");",
                f"        throw std::runtime_error(\"Adapter not found: {adj}\");",
                f"    }}",
                f"    jmethodID _ctor = env->GetMethodID(",
                f"        _cls, \"<init>\", \"(Ljava/lang/String;)V\");",
                f"    if (!_ctor) {{",
                f"        env->ExceptionClear();",
                f"        env->DeleteLocalRef(_cls);",
                f"        LOGE(\"Adapter (String) ctor not found: {adj}\");",
                f"        throw std::runtime_error(\"Adapter ctor not found: {adj}\");",
                f"    }}",
                f"    jstring _jkey = env->NewStringUTF(_key.c_str());",
                f"    jobject _obj  = env->NewObject(_cls, _ctor, _jkey);",
                f"    env->DeleteLocalRef(_jkey);",
                f"    env->DeleteLocalRef(_cls);",
                f"    if (!_obj) {{",
                f"        if (env->ExceptionCheck()) env->ExceptionClear();",
                f"        LOGE(\"NewObject failed for adapter: {adj}\");",
                f"        return nullptr;",
                f"    }}",
                f"    jobject _gref = env->NewGlobalRef(_obj);",
                f"    env->DeleteLocalRef(_obj);",
                f"    LOGD(\"abstract adapter created = %p key=%s\","
                f" _gref, _key.c_str());",
                f"    return _gref;",
                f"}}",
                f"",
            ]
            continue

        if conv != "callable_to_proxy":
            continue

        # ── INTERFACE PROXY ────────────────────────────────────────────────────
        iface         = m.get("proxy_interface", "")
        if not iface:
            jtype = p.get("java_type", "").replace(".", "/")
            iface = jtype if jtype else "java/lang/Runnable"
        iface         = to_jni_slash(iface)
        base_key      = f"{fqn}#{m['name']}#{pname}"

        # [Action7] Get full method objects from proxy_methods field
        # [A4] proxy_method_list from Stage 05 may be strings or dicts
        raw_proxy_methods = m.get("proxy_methods", [])
        iface_methods = []
        for pm in raw_proxy_methods:
            if isinstance(pm, dict):
                iface_methods.append(pm)
            elif isinstance(pm, str):
                iface_methods.append({"name": pm})

        # [Action7] Known method names for validation
        known_method_names = {pm.get("name", "") for pm in iface_methods}

        lines += [
            f"// Proxy factory for interface: {iface}",
            f"static jobject {fn}(JNIEnv* env, nb::object callbacks) {{",
            f"    LOGD(\"creating proxy for {iface}\");",
            f"    LOGV(\"PROXY_CREATE {iface} mi={mi}\");",
            f"    static std::atomic<uint64_t> proxy_id{{0}};",
            f"    uint64_t pid = ++proxy_id;",
        ]

        if iface_methods:
            for meth in iface_methods:
                mkey = sanitize_id(meth.get("name", "callback"))
                lines += [
                    f"    {{",
                    f"        std::string _key = "
                    f"\"{base_key}#{mkey}_\" + std::to_string(pid);",
                    f"        nb::callable _fn;",
                    f"        if (nb::isinstance<nb::dict>(callbacks)) {{",
                    f"            nb::dict _d = nb::cast<nb::dict>(callbacks);",
                ]
                # [Action7] Validate dict keys
                if known_method_names:
                    lines += [
                        f"            // [Action7] Validate callback dict keys",
                        f"            nb::list _dkeys = _d.keys();",
                        f"            for (size_t _ki = 0; _ki < nb::len(_dkeys); ++_ki) {{",
                        f"                std::string _kname = nb::cast<std::string>(_dkeys[_ki]);",
                    ]
                    known_cpp = "{" + ", ".join(f'"{n}"' for n in sorted(known_method_names) if n) + "}"
                    lines += [
                        f"                static const std::unordered_set<std::string> _known = {known_cpp};",
                        f"                if (_known.find(_kname) == _known.end()) {{",
                        f"                    LOGW(\"[Action7] PROXY_UNKNOWN_KEY '%s' not in known methods for {iface}\", _kname.c_str());",
                        f"                }}",
                        f"            }}",
                    ]
                lines += [
                    f"            if (_d.contains(\"{mkey}\"))",
                    f"                _fn = nb::cast<nb::callable>"
                    f"(_d[\"{mkey}\"]);",
                    f"        }} else if (nb::isinstance<nb::callable>(callbacks)) {{",
                    f"            _fn = nb::cast<nb::callable>(callbacks);",
                    f"        }}",
                    f"        if (_fn.is_valid()) {{",
                    f"            store_callback(_key, _fn);",
                    f"            LOGD(\"stored callback key=%s\", _key.c_str());",
                    f"            LOGV(\"CALLBACK_STORED key=%s\", _key.c_str());",
                    f"        }}",
                    f"    }}",
                ]
            first_mkey = sanitize_id(iface_methods[0].get("name", "callback"))
            lines.append(
                f"    std::string _seed_key = "
                f"\"{base_key}#{first_mkey}_\" + std::to_string(pid);"
            )
        else:
            lines += [
                f"    std::string _seed_key = "
                f"\"{base_key}_\" + std::to_string(pid);",
                f"    if (nb::isinstance<nb::callable>(callbacks)) {{",
                f"        store_callback(_seed_key, "
                f"nb::cast<nb::callable>(callbacks));",
                f"        LOGD(\"stored callback key=%s\","
                f" _seed_key.c_str());",
                f"        LOGV(\"CALLBACK_STORED key=%s\", _seed_key.c_str());",
                f"    }}",
            ]
        lines += [
            f"    jclass proxy_cls   = g_proxy_class;",
            f"    jclass handler_cls = g_stratum_handler_class;",
            f"    if (!proxy_cls || !handler_cls) {{",
            f"        LOGE(\"Proxy global refs not init for {iface}\");",
            f"        throw std::runtime_error(\"Proxy global refs not init.\");",
            f"    }}",
            f"    jclass iface_cls = find_class(env, \"{iface}\");",
            f"    if (!iface_cls) {{",
            f"        env->ExceptionClear();",
            f"        LOGE(\"Interface not found: {iface}\");",
            f"        throw std::runtime_error("
            f"\"Interface not found: {iface}\");",
            f"    }}",
            f"    LOGD(\"found interface class {iface}\");",
            f"    LOGV(\"PROXY_IFACE_OK {iface}\");",
            f"    jmethodID hctor = env->GetMethodID(",
            f"        handler_cls, \"<init>\", \"(Ljava/lang/String;)V\");",
            f"    jstring jkey = env->NewStringUTF(_seed_key.c_str());",
            f"    jobject handler = env->NewObject(handler_cls, hctor, jkey);",
            f"    env->DeleteLocalRef(jkey);",
            f"    jmethodID new_proxy = env->GetStaticMethodID(proxy_cls,",
            f"        \"newProxyInstance\",",
            f"        \"(Ljava/lang/ClassLoader;[Ljava/lang/Class;"
            f"Ljava/lang/reflect/InvocationHandler;)Ljava/lang/Object;\");",
            f"    if (!g_app_class_loader) {{",
            f"        env->DeleteLocalRef(iface_cls);"
            f" env->DeleteLocalRef(handler);",
            f"        LOGE(\"g_app_class_loader not set\");",
            f"        throw std::runtime_error(\"g_app_class_loader not set\");",
            f"    }}",
            f"    jobjectArray ia = env->NewObjectArray(",
            f"        1, g_class_class, iface_cls);",
            f"    jobject proxy = env->CallStaticObjectMethod(",
            f"        proxy_cls, new_proxy, g_app_class_loader, ia, handler);",
            f"    env->DeleteLocalRef(iface_cls);",
            f"    env->DeleteLocalRef(ia);",
            f"    env->DeleteLocalRef(handler);",
            f"    if (!proxy) {{",
            f"        LOGE(\"newProxyInstance returned null for {iface}\");",
            f"        if (env->ExceptionCheck()) env->ExceptionClear();",
            f"        return nullptr;",
            f"    }}",
            f"    jobject gref = env->NewGlobalRef(proxy);",
            f"    env->DeleteLocalRef(proxy);",
            f"    LOGD(\"proxy created = %p\", gref);",
            f"    LOGV(\"PROXY_CREATED {iface} gref=%p\", gref);",
            f"    return gref;",
            f"}}",
            f"",
        ]


# =============================================================================
# [FIX-17] native_entries builder
# =============================================================================

def build_native_entries(groups: dict, ctor_len: int,
                          decl_len: int) -> List[Tuple[int, dict, bool]]:
    entries: List[Tuple[int, dict, bool]] = []
    for idx_d, m in enumerate(groups["declared"]):
        if not m.get("is_constructor") and m.get("is_native", False):
            entries.append((ctor_len + idx_d, m, False))
    for idx_o, m in enumerate(groups["overridden"]):
        if (not m.get("is_static") and not m.get("is_constructor")
                and m.get("is_native", False)):
            entries.append((ctor_len + decl_len + idx_o, m, True))
    return entries


# =============================================================================
# JNICALL stub emitter
# =============================================================================

def emit_native_fn_declaration(
    fqn: str,
    m: dict,
    global_idx: int,
    lines: list,
) -> bool:
    """[FIX-18] Always virtual. [FIX-25] LOGV."""
    ret_decl   = ret_decl_for(m)
    params     = m.get("params", [])
    cpp_params = [(sanitize_id(p["name"]), cpp_type_for_param(p))
                  for p in params]
    if should_skip_method(ret_decl, cpp_params):
        return False

    is_static  = m.get("is_static", False)
    fn_name    = native_impl_fn(fqn, m["name"], global_idx, is_static)
    ret_jni    = get_return_jni(m)
    ret_c_type = jni_sig_for_type(ret_jni)
    prefix     = cpp_class_prefix(fqn)
    mvar       = method_id_var(fqn, m["name"], global_idx)

    jni_pp = [
        f"{p.get('jni_type','jobject')} {sanitize_id(p['name'])}_raw"
        for p in params
    ]
    second_arg = "jclass" if is_static else "jobject"
    all_c      = (
        f"JNIEnv* env, {second_arg} _self_"
        + (", " + ", ".join(jni_pp) if jni_pp else "")
    )
    jargs_str  = ", ".join(f"{sanitize_id(p['name'])}_raw" for p in params)
    jargs_full = (f", {jargs_str}" if jargs_str else "")
    jclass_var = f"g_{prefix}_class"

    lines.append(f"// [FIX-18] Compile-time native dispatch (virtual call only)")
    lines.append(f"static {ret_c_type} JNICALL {fn_name}({all_c}) {{")
    lines.append(f"    LOGT_JNICALL_IN(\"{fqn}\", \"{m['name']}\");")
    lines.append(f"    LOGD(\"JNICALL {fqn}#{m['name']}\");")
    lines.append(f"    ensure_{prefix}_init(env);")

    null_ret = (
        " return nullptr;" if raw_c_type_of(ret_jni) == "jobject"
        else (" return;" if ret_c_type == "void" else " return 0;")
    )
    lines.append(
        f"    if (!{mvar}) {{"
        f" LOGE(\"null methodID in {fn_name}\");{null_ret} }}")

    call = (f"CallStatic{call_suffix(ret_jni)}Method" if is_static
            else f"Call{call_suffix(ret_jni)}Method")
    obj  = jclass_var if is_static else "_self_"

    if ret_c_type == "void":
        lines.append(f"    LOGV(\"JNI_CALL {fqn}#{m['name']} obj=%p\", (void*)_self_);")
        lines.append(f"    env->{call}({obj}, {mvar}{jargs_full});")
    else:
        lines.append(f"    LOGV(\"JNI_CALL {fqn}#{m['name']} obj=%p\", (void*)_self_);")
        lines.append(
            f"    {raw_c_type_of(ret_jni)} raw = "
            f"env->{call}({obj}, {mvar}{jargs_full});")

    emit_exception_check(
        lines, "    ",
        ret_decl if ret_c_type == "void" else "")

    if ret_c_type != "void":
        lines.append(f"    LOGT_JNICALL_OUT(\"{fqn}\", \"{m['name']}\");")
        if ret_c_type != raw_c_type_of(ret_jni):
            lines.append(f"    return static_cast<{ret_c_type}>(raw);")
        else:
            lines.append(f"    return raw;")

    lines += ["}", ""]
    return True


# =============================================================================
# [FIX-16] Instance method emitter
# =============================================================================

def _emit_instance_method(
    m: dict,
    global_idx: int,
    fqn: str,
    sname: str,
    seen_inst: set,
    lines: list,
) -> None:
    """[FIX-16] Virtual dispatch. [FIX-23/A42] none(true) for nullable pointers."""
    ret_decl   = ret_decl_for(m)
    params     = m.get("params", [])
    cpp_params = [(sanitize_id(p["name"]), cpp_type_for_param(p))
                  for p in params]
    if should_skip_method(ret_decl, cpp_params):
        return

    mname   = sanitize_id(m["name"])
    param_s = ", ".join(f"{t} {n}" for n, t in cpp_params)
    sig_key = f"{mname}({param_s})"
    if sig_key in seen_inst:
        return
    seen_inst.add(sig_key)

    mvar     = method_id_var(fqn, m["name"], global_idx)
    ret_conv = m.get("return_conversion", "none")
    prefix   = cpp_class_prefix(fqn)
    ret_jni  = get_return_jni(m)
    call_fn  = f"Call{call_suffix(ret_jni)}Method"

    lines.append(f"{ret_decl} {sname}::{mname}({param_s}) {{")
    lines.append(f"    LOGT_PY_TO_JNI(\"{fqn}\", \"{m['name']}\");")
    lines.append(f"    LOGD(\"{fqn}::{mname} called\");")
    lines.append(f"    JNIEnv* env = get_env();")
    lines.append(
        f"    if (!env) {{ LOGE(\"get_env null in {sname}::{mname}\"); "
        f"{null_return(ret_decl)} }}")
    lines.append(f"    ensure_{prefix}_init(env);")
    lines.append(
        f"    if (!{mvar}) {{ LOGE(\"methodID null: {fqn}#{m['name']}\"); "
        f"{null_return(ret_decl)} }}")
    lines.append(f"    if (!obj_) {{ LOGE(\"obj_ null in {sname}::{mname}\"); "
                    f"{null_return(ret_decl)} }}")
    lines.append(f"    LOGV(\"INST_CALL {fqn}#{m['name']} self=%p\", obj_);")

    for p in params:
        pname = sanitize_id(p["name"])
        ptype = cpp_type_for_param(p)
        if ptype.endswith("*"):
            lines.append(f"    LOGV_PTR(\"{pname}\", {pname} ? {pname}->obj_ : nullptr);")
        elif ptype == "std::string" or ptype == "const std::string&":
            lines.append(f"    LOGV_STR(\"{pname}\", {pname});")
        elif ptype == "std::u16string":
            lines.append(f"    LOGV_INT(\"{pname}_len\", (int64_t){pname}.size());")
        elif ptype == "bool":
            lines.append(f"    LOGV_BOOL(\"{pname}\", {pname});")
        elif ptype in ("nb::object", "nb::args"):
            lines.append(f"    LOGV_PYOBJ(\"{pname}\", {pname});")
        elif ptype == "nb::list":
            lines.append(f"    LOGV_INT(\"{pname}_len\", (int64_t)nb::len({pname}));")
        elif ptype == "nb::bytes":
            lines.append(f"    LOGV_INT(\"{pname}_len\", (int64_t){pname}.size());")
        elif "vector" in ptype:
            lines.append(f"    LOGV_INT(\"{pname}_len\", (int64_t){pname}.size());")
        else:
            lines.append(f"    LOGV_INT(\"{pname}\", (int64_t){pname});")
        emit_param_conversion(p, global_idx, lines, method_name=m["name"])

    args = (", " + jni_args(params)) if params else ""
    if ret_decl == "void":
        lines.append(f"    LOGV(\"JNI_CALL_START {fqn}#{m['name']}\");")
        lines.append(f"    {{ nb::gil_scoped_release _release;")
        lines.append(f"      env->{call_fn}(obj_, {mvar}{args});")
        lines.append(f"    }}")
        lines.append(f"    LOGV(\"JNI_CALL_END {fqn}#{m['name']}\");")
    else:
        raw_c = raw_c_type_of(ret_jni)
        lines.append(f"    LOGV(\"JNI_CALL_START {fqn}#{m['name']}\");")
        lines.append(f"    {raw_c} raw;")
        lines.append(f"    {{ nb::gil_scoped_release _release;")
        lines.append(f"      raw = env->{call_fn}(obj_, {mvar}{args});")
        lines.append(f"    }}")
        lines.append(f"    LOGV(\"JNI_CALL_END {fqn}#{m['name']}\");")

    for p in params:
        emit_param_cleanup(p, lines)

    emit_return_conversion(ret_decl, ret_conv, lines, m, method_name=m["name"])
    lines += ["}", ""]


# =============================================================================
# [FIX-23 / A42] nanobind arg helper
# =============================================================================

def nb_arg_for_param(p: dict, param_name: str) -> str:
    """
    [FIX-23 / A42] Emit nb::arg with .none(true) only for nullable pointers.
    [Action29] Unique name by appending type suffix to prevent nanobind SIGABRT.
    """
    ptype  = cpp_type_for_param(p)
    is_ptr = ptype.endswith("*") or ptype == "nb::object"
    safe_type = re.sub(r'[^a-zA-Z0-9]', '', ptype)
    unique_name = f"{param_name}_{safe_type}"

    # [A42] Only apply .none(true) when explicitly nullable (or unknown → True)
    nullable = p.get("nullable", True)

    if is_ptr and nullable:
        return f'nb::arg("{unique_name}").none(true)'
    else:
        return f'nb::arg("{unique_name}")'


# =============================================================================
# Class CPP Structure Assembler
# =============================================================================

def emit_class_cpp(cls: dict) -> str:
    fqn      = cls["fqn"]
    jni_name = cls["jni_name"]
    prefix   = cpp_class_prefix(fqn)
    sname    = struct_name(fqn)

    parent_fqn = cls.get("parent_fqn", "")
    if parent_fqn == fqn:
        parent_fqn = ""
    if not parent_fqn and cls.get("parent_details"):
        parent_fqn = cls["parent_details"].get("fqn", "")
    parent_sname = (
        struct_name(parent_fqn)
        if parent_fqn and parent_fqn in GENERATED_FQNS else ""
    )

    groups       = get_methods_for_class(cls)
    fields       = get_fields_for_class(cls)
    bindable     = groups["declared"] + groups["overridden"]
    all_for_init = groups["constructors"] + bindable
    ctor_len     = len(groups["constructors"])
    decl_len     = len(groups["declared"])

    lines = [
        f"// ============================================================",
        f"// Stratum Stage 06 v{STAGE_VERSION} — DO NOT EDIT",
        f"// Class  : {fqn}",
        f"// FIX-16 : all calls virtual (no NonVirtual)",
        f"// FIX-17 : RegisterNatives only for is_native methods",
        f"// FIX-19 : LOGD diagnostics (STRATUM_VERBOSE_LOG=1)",
        f"// FIX-23 : nullable pointer params emit nb::arg().none(true)",
        f"// FIX-25 : ultra-deep LOGV tracing (STRATUM_ULTRA_LOG=1)",
        f"// A37    : jchar UTF-16 correct handling",
        f"// A38    : varargs Object... support",
        f"// A39    : jthrowable → StratumThrowable",
        f"// A40    : WeakReference → StratumWeakObject",
        f"// A41    : ByteBuffer zero-copy direct memoryview",
        f"// A42    : @NonNull/@Nullable → none(true) only when nullable",
        f"// Action7: proxy dict key mismatch LOGW",
        f"// Action8: CharSequence → string_in",
        f"// ============================================================",
        f"#include <jni.h>",
        f"#include <stdint.h>",
        f"#include <string>",
        f"#include <vector>",
        f"#include <mutex>",
        f"#include <atomic>",
        f"#include <stdexcept>",
        f"#include <unordered_set>",
        f'#include "bridge_core.h"',
        f'#include "stratum_structs.h"',
        f"#include <nanobind/nanobind.h>",
        f"#include <nanobind/stl/string.h>",
        f"#include <nanobind/stl/vector.h>",
        f"#include <nanobind/stl/list.h>",
        f"#include <nanobind/ndarray.h>",
        f"",
        f"namespace nb = nanobind;",
        f'using nb::literals::operator""_a;',
        f"",
        f"#ifndef LOGE",
        f"#include <android/log.h>",
        f"#define LOGE(...) __android_log_print("
        f"ANDROID_LOG_ERROR, \"Stratum\", __VA_ARGS__)",
        f"#define LOGW(...) __android_log_print("
        f"ANDROID_LOG_WARN,  \"Stratum\", __VA_ARGS__)",
        f"#endif",
        f"",
        VERBOSE_LOG_HEADER,
        f"static jclass         g_{prefix}_class = nullptr;",
        f"static std::once_flag g_{prefix}_flag;",
    ]

    for idx, m in enumerate(all_for_init):
        lines.append(
            f"static jmethodID {method_id_var(fqn, m['name'], idx)} = nullptr;"
            f" // [{idx}] {m['name']}")

    for f in fields:
        fname = f.get("name", "")
        if fname:
            lines.append(
                f"static jfieldID {field_id_var(fqn, fname)} = nullptr;"
                f" // field: {fname}")

    for idx, m in enumerate(all_for_init):
        in_ovr = (
            (ctor_len + decl_len) <= idx
            < (ctor_len + decl_len + len(groups["overridden"]))
        )
        if in_ovr:
            lines.append(
                f"static jclass "
                f"{override_cls_var(fqn, m['name'], idx)} = nullptr;"
                f" // override class for {m['name']}")

    lines.append("")

    for idx, m in enumerate(all_for_init):
        has_proxy = m.get("needs_proxy") or any(
            p.get("conversion") in ("callable_to_proxy", "abstract_adapter")
            for p in m.get("params", [])
        )
        if has_proxy:
            emit_proxy_factory(fqn, idx, m, lines)

    native_entries = build_native_entries(groups, ctor_len, decl_len)

    if native_entries:
        lines.append(
            "// ── Forward declarations for native stubs ─────────────────────")
        for (gidx, m, _is_ovr) in native_entries:
            is_static  = m.get("is_static", False)
            ret_decl   = ret_decl_for(m)
            cpp_params = [
                (sanitize_id(p["name"]), cpp_type_for_param(p))
                for p in m.get("params", [])
            ]
            if should_skip_method(ret_decl, cpp_params):
                continue
            fn      = native_impl_fn(fqn, m["name"], gidx, is_static)
            ret_jni = get_return_jni(m)
            ret_c   = jni_sig_for_type(ret_jni)
            second  = "jclass" if is_static else "jobject"
            jni_pp  = [
                f"{p.get('jni_type','jobject')} {sanitize_id(p['name'])}_raw"
                for p in m.get("params", [])
            ]
            all_c = (
                f"JNIEnv*, {second}"
                + (", " + ", ".join(jni_pp) if jni_pp else "")
            )
            lines.append(f"static {ret_c} JNICALL {fn}({all_c});")
        lines.append("")

    # ── Lazy init ─────────────────────────────────────────────────────────────
    lines += [
        f"static void {prefix}_init_impl(JNIEnv* env) {{",
        f"    LOGD(\"init_impl for {fqn}\");",
        f"    LOGV(\"CLASS_INIT_START {fqn}\");",
        f"    jclass local = find_class(env, \"{jni_name}\");",
        f"    if (!local) {{",
        f"        env->ExceptionClear();",
        f"        LOGE(\"find_class failed for {jni_name}\");",
        f"        return;",
        f"    }}",
        f"    g_{prefix}_class = (jclass)env->NewGlobalRef(local);",
        f"    env->DeleteLocalRef(local);",
        f"    LOGD(\"class {jni_name} loaded OK\");",
        f"    LOGV(\"CLASS_INIT_OK {fqn} class=%p\", (void*)g_{prefix}_class);",
    ]

    for idx, m in enumerate(all_for_init):
        mvar  = method_id_var(fqn, m["name"], idx)
        sig   = reconstruct_jni_sig(m)
        jname = "<init>" if m.get("is_constructor") else m["name"]
        call  = "GetStaticMethodID" if m.get("is_static") else "GetMethodID"
        if not sig:
            lines.append(
                f"    LOGE(\"no sig for {fqn}#{jname} — skipping\");")
            continue
        lines.append(
            f"    {mvar} = env->{call}("
            f"g_{prefix}_class, \"{jname}\", \"{sig}\");")
        lines += [
            f"    if (!{mvar}) {{",
            f"        env->ExceptionClear();",
            f"        LOGW(\"GetMethodID FAILED: {fqn}#{jname} sig={sig}\");",
            f"        LOGV(\"METHOD_ID_FAIL {fqn}#{jname} sig={sig}\");",
            f"    }} else {{",
            f"        LOGD(\"GetMethodID OK: {fqn}#{jname}\");",
            f"        LOGV(\"METHOD_ID_OK {fqn}#{jname} id=%p\", (void*){mvar});",
            f"    }}",
        ]

        in_ovr = (
            (ctor_len + decl_len) <= idx
            < (ctor_len + decl_len + len(groups["overridden"]))
        )
        if in_ovr:
            raw_declaring = (
                m.get("overrides_in", "")
                or m.get("declaring_class_jni", "")
                or m.get("overrides_in_jni", "")
            )
            declaring = to_jni_slash(raw_declaring)
            if declaring:
                cvar = override_cls_var(fqn, m["name"], idx)
                lines += [
                    f"    {{ // override class lookup for {m['name']}",
                    f"      jclass _ol = find_class(env, \"{declaring}\");",
                    f"      if (_ol) {{",
                    f"          {cvar} = (jclass)env->NewGlobalRef(_ol);",
                    f"          env->DeleteLocalRef(_ol);",
                    f"          LOGD(\"override class {declaring} loaded for {m['name']}\");",
                    f"          LOGV(\"OVERRIDE_CLASS_OK {declaring} for {m['name']}\");",
                    f"      }} else {{",
                    f"          env->ExceptionClear();",
                    f"          LOGW(\"override class NOT found: {declaring} for {m['name']}\");",
                    f"      }}",
                    f"    }}",
                ]

    for f in fields:
        fname     = f.get("name", "")
        fsig      = f.get("jni_signature", f.get("jni_type", ""))
        is_static = f.get("is_static", False)
        if not fname or not fsig:
            continue
        fid_var  = field_id_var(fqn, fname)
        get_call = "GetStaticFieldID" if is_static else "GetFieldID"
        lines.append(
            f"    {fid_var} = env->{get_call}("
            f"g_{prefix}_class, \"{fname}\", \"{fsig}\");")
        lines += [
            f"    if (!{fid_var}) {{",
            f"        env->ExceptionClear();",
            f"        LOGW(\"GetFieldID FAILED: {fqn}#{fname} sig={fsig}\");",
            f"        LOGV(\"FIELD_ID_FAIL {fqn}#{fname} sig={fsig}\");",
            f"    }} else {{",
            f"        LOGD(\"GetFieldID OK: {fqn}#{fname}\");",
            f"        LOGV(\"FIELD_ID_OK {fqn}#{fname} id=%p\", (void*){fid_var});",
            f"    }}",
        ]

    # ── RegisterNatives ───────────────────────────────────────────────────────
    reg_entries = []
    for (gidx, m, _is_ovr) in native_entries:
        is_static  = m.get("is_static", False)
        ret_decl   = ret_decl_for(m)
        cpp_params = [
            (sanitize_id(p["name"]), cpp_type_for_param(p))
            for p in m.get("params", [])
        ]
        if should_skip_method(ret_decl, cpp_params):
            continue
        fn  = native_impl_fn(fqn, m["name"], gidx, is_static)
        sig = build_jni_native_sig(m)
        if sig:
            reg_entries.append((m["name"], sig, fn))

    if reg_entries:
        lines += [
            f"    // [FIX-17] RegisterNatives: {len(reg_entries)} native method(s)",
            f"    static const JNINativeMethod kNativeMethods[] = {{",
        ]
        for (jname, sig, fn) in reg_entries:
            lines.append(
                f"        {{ \"{jname}\", \"{sig}\","
                f" reinterpret_cast<void*>({fn}) }},")
        lines += [
            f"    }};",
            f"    if (g_{prefix}_class) {{",
            f"        jint rc = env->RegisterNatives(",
            f"            g_{prefix}_class, kNativeMethods,",
            f"            static_cast<jint>("
            f"sizeof(kNativeMethods)/sizeof(kNativeMethods[0])));",
            f"        if (rc != JNI_OK) {{",
            f"            env->ExceptionClear();",
            f"            LOGE(\"RegisterNatives FAILED for {fqn}: rc=%d\", rc);",
            f"        }} else {{",
            f"            LOGD(\"RegisterNatives OK for {fqn} ({len(reg_entries)} methods)\");",
            f"            LOGV(\"REGISTER_NATIVES_OK {fqn} count={len(reg_entries)}\");",
            f"        }}",
            f"    }}",
        ]

    lines += [
        f"    LOGV(\"CLASS_INIT_DONE {fqn}\");",
        f"}}",
        f"",
        f"__attribute__((noinline)) void ensure_{prefix}_init(JNIEnv* env) {{",
        f"    std::call_once(g_{prefix}_flag, {prefix}_init_impl, env);",
        f"}}",
        f"",
        f"jclass get_{prefix}_class() {{",
        f"    return g_{prefix}_class;",
        f"}}",
        f"",
    ]

    # ── Constructors / Destructor ─────────────────────────────────────────────
    if parent_sname:
        lines += [
            f"{sname}::{sname}(jobject obj) : {parent_sname}(obj) {{",
            f"    LOGD(\"{sname} constructed (parent chain)\");",
            f"    LOGV(\"CTOR_CHAIN {sname} obj=%p\", obj);",
            f"}}",
            f"{sname}::~{sname}() {{",
            f"    LOGD(\"{sname} destroyed\");",
            f"    LOGV(\"DTOR {sname}\");",
            f"}}",
            "",
        ]
    else:
        lines += [
            f"{sname}::{sname}(jobject obj) : StratumObject(obj) {{",
            f"    JNIEnv* env = get_env();",
            f"    // [Action33] Assert env not null — never store dangling local ref",
            f"    if (!env) {{",
            f"        LOGE(\"[Action33] CTOR_NO_ENV {sname} — cannot NewGlobalRef, obj will be invalid\");",
            f"        // obj_ = obj would store a local ref — DO NOT. Store nullptr instead.",
            f"        obj_ = nullptr;",
            f"        return;",
            f"    }}",
            f"    obj_ = env->NewGlobalRef(obj);",
            f"    LOGD(\"{sname} constructed obj_=%p\", obj_);",
            f"    LOGV(\"CTOR {sname} raw=%p global=%p\", obj, obj_);",
            f"}}",
            f"{sname}::~{sname}() {{",
            f"    // [Action3] Use get_env() not get_env_safe() so background thread GC attaches",
            f"    JNIEnv* env = get_env();",
            f"    if (env && obj_) {{",
            f"        // [Action4] Remove callbacks keyed to this object",
            f"        if (!stratum_key_prefix_.empty()) {{",
            f"            remove_callbacks_by_prefix(stratum_key_prefix_);",
            f"            LOGD(\"{sname} destructor removed callbacks prefix=%s\", stratum_key_prefix_.c_str());",
            f"        }}",
            f"        env->DeleteGlobalRef(obj_); obj_ = nullptr;",
            f"        LOGD(\"{sname} destroyed global ref released\");",
            f"        LOGV(\"DTOR_GLOBAL_FREED {sname}\");",
            f"    }}",
            f"}}",
            "",
        ]

    # ── JNICALL stubs ─────────────────────────────────────────────────────────
    lines.append(
        "// ── [FIX-18] JNI native stubs — virtual calls only ────────────")
    for (gidx, m, _is_ovr) in native_entries:
        emit_native_fn_declaration(fqn, m, gidx, lines)

    # ── Static method wrappers ─────────────────────────────────────────────────
    seen_static: Set[str] = set()
    for idx_in_bindable, m in enumerate(bindable):
        if not m.get("is_static") or m.get("is_constructor"):
            continue
        ret_decl   = ret_decl_for(m)
        params     = m.get("params", [])
        cpp_params = [
            (sanitize_id(p["name"]), cpp_type_for_param(p))
            for p in params
        ]
        if should_skip_method(ret_decl, cpp_params):
            continue
        mname   = sanitize_id(m["name"])
        param_s = ", ".join(f"{t} {n}" for n, t in cpp_params)
        sig_key = f"static_{mname}_" + "_".join(t for _, t in cpp_params)
        if sig_key in seen_static:
            sig_key += f"_{idx_in_bindable}"
        seen_static.add(sig_key)
        global_idx = ctor_len + idx_in_bindable
        mvar       = method_id_var(fqn, m["name"], global_idx)
        fn_name    = f"static_{prefix}_{mname}_{global_idx}"
        ret_conv   = m.get("return_conversion", "none")
        ret_jni    = get_return_jni(m)

        lines.append(f"static {ret_decl} {fn_name}({param_s}) {{")
        lines.append(f"    LOGT_PY_TO_JNI(\"{fqn}\", \"{m['name']}\");")
        lines.append(f"    LOGD(\"static {fqn}::{m['name']} called\");")
        lines.append(f"    JNIEnv* env = get_env();")
        lines.append(
            f"    if (!env) {{ LOGE(\"get_env null in {fn_name}\"); "
            f"{null_return(ret_decl)} }}")
        lines.append(f"    ensure_{prefix}_init(env);")
        lines.append(
            f"    if (!{mvar}) {{ LOGE(\"methodID null in {fn_name}\"); "
            f"{null_return(ret_decl)} }}")
        lines.append(f"    LOGV(\"STATIC_CALL {fqn}#{m['name']}\");")
        for p in params:
            pname = sanitize_id(p["name"])
            ptype = cpp_type_for_param(p)
            if ptype.endswith("*"):
                lines.append(f"    LOGV_PTR(\"{pname}\", {pname} ? {pname}->obj_ : nullptr);")
            emit_param_conversion(p, global_idx, lines, method_name=m["name"])
        args = (", " + jni_args(params)) if params else ""
        lines.append(f"    LOGV(\"JNI_CALL_START static {fqn}#{m['name']}\");")
        if ret_decl == "void":
            lines.append(f"    {{ nb::gil_scoped_release _release;")
            lines.append(
                f"      env->CallStatic{call_suffix(ret_jni)}Method"
                f"(g_{prefix}_class, {mvar}{args});")
            lines.append(f"    }}")
        else:
            raw_c = raw_c_type_of(ret_jni)
            lines.append(f"    {raw_c} raw;")
            lines.append(f"    {{ nb::gil_scoped_release _release;")
            lines.append(
                f"      raw = env->CallStatic{call_suffix(ret_jni)}Method"
                f"(g_{prefix}_class, {mvar}{args});")
            lines.append(f"    }}")
        lines.append(f"    LOGV(\"JNI_CALL_END static {fqn}#{m['name']}\");")
        for p in params:
            emit_param_cleanup(p, lines)
        emit_return_conversion(ret_decl, ret_conv, lines, m, method_name=m["name"])
        lines += ["}", ""]

    # ── Instance method implementations ───────────────────────────────────────
    seen_inst: Set[str] = set()
    for idx_in_decl, m in enumerate(groups["declared"]):
        if m.get("is_static") or m.get("is_constructor"):
            continue
        _emit_instance_method(
            m, ctor_len + idx_in_decl, fqn, sname, seen_inst, lines)
    for idx_in_ovr, m in enumerate(groups["overridden"]):
        if m.get("is_static") or m.get("is_constructor"):
            continue
        _emit_instance_method(
            m, ctor_len + decl_len + idx_in_ovr, fqn, sname, seen_inst, lines)

    # ── Field accessors ───────────────────────────────────────────────────────
    field_nb_entries = emit_field_accessors(
        fqn, fields, prefix, sname, lines)

    # ── Constructor factories ─────────────────────────────────────────────────
    ctor_nb: List[Tuple[str, str, int, list]] = []
    is_abstract = cls.get("is_abstract", False)

    if not is_abstract:
        for idx_c, m in enumerate(groups["constructors"]):
            params     = m.get("params", [])
            cpp_params = [
                (sanitize_id(p["name"]), cpp_type_for_param(p))
                for p in params
            ]
            if any(t == "jobject" for _, t in cpp_params):
                continue
            param_s = ", ".join(f"{t} {n}" for n, t in cpp_params)
            fn_name = f"ctor_{prefix}_{idx_c}"
            mvar    = method_id_var(fqn, "<init>", idx_c)
            lines.append(f"static {sname}* {fn_name}({param_s}) {{")
            lines.append(f"    LOGT_PY_TO_JNI(\"{fqn}\", \"<init>\");")
            lines.append(f"    LOGD(\"ctor_{idx_c} for {fqn}\");")
            lines.append(f"    LOGV(\"CTOR_FACTORY {fqn} idx={idx_c}\");")
            lines.append(f"    JNIEnv* env = get_env();")
            lines.append(
                f"    if (!env) {{ LOGE(\"get_env null in {fn_name}\"); return nullptr; }}")
            lines.append(f"    ensure_{prefix}_init(env);")
            lines.append(
                f"    if (!{mvar} || !g_{prefix}_class) {{")
            lines.append(
                f"        LOGE(\"methodID or class null in {fn_name}\"); return nullptr; }}")
            lines.append(f"    LOGV(\"CTOR_FACTORY_NEWOBJ {fqn}\");")
            for p in params:
                pname = sanitize_id(p["name"])
                ptype = cpp_type_for_param(p)
                if ptype.endswith("*"):
                    lines.append(f"    LOGV_PTR(\"{pname}\", {pname} ? {pname}->obj_ : nullptr);")
                emit_param_conversion(p, idx_c, lines, method_name="<init>")
            args = (", " + jni_args(params)) if params else ""
            lines.append(f"    jobject obj;")
            lines.append(f"    {{ nb::gil_scoped_release _release;")
            lines.append(
                f"      obj = env->NewObject("
                f"g_{prefix}_class, {mvar}{args});")
            lines.append(f"    }}")
            for p in params:
                emit_param_cleanup(p, lines)
            emit_exception_check(lines, "    ", "StratumObject*")
            lines += [
                f"    if (!obj) {{",
                f"        LOGE(\"NewObject returned null in {fn_name}\");",
                f"        LOGV(\"CTOR_FACTORY_FAIL {fqn}\");",
                f"        return nullptr;",
                f"    }}",
                f"    auto* w = new {sname}(obj);",
                f"    env->DeleteLocalRef(obj);",
                f"    LOGD(\"ctor_{idx_c} {fqn} OK = %p\", w);",
                f"    LOGT_JNI_TO_PY(\"{fqn}\", \"<init>\", \"object*\");",
                f"    LOGV(\"CTOR_FACTORY_OK {fqn} ptr=%p\", w);",
                f"    return w;",
                f"}}",
                f"",
            ]
            ctor_nb.append((fn_name, param_s, idx_c, params))

    # ── nanobind class registration ───────────────────────────────────────────
    py_name = cpp_class_prefix(fqn)
    lines.append(
        f"__attribute__((noinline)) void register_{prefix}(nb::module_& m) {{")
    lines += [
        f"    LOGD(\"registering {py_name}\");",
        f"    LOGV(\"NB_REGISTER_START {py_name}\");",
        f"    {{ PyObject* _e = PyObject_GetAttrString(m.ptr(), \"{py_name}\");",
        f"      if (_e) {{ Py_DECREF(_e); return; }} PyErr_Clear(); }}",
    ]
    if parent_sname:
        parent_py = cpp_class_prefix(parent_fqn)
        lines += [
            f"    {{ PyObject* _p = PyObject_GetAttrString("
            f"m.ptr(), \"{parent_py}\");",
            f"      if (!_p) {{ PyErr_Clear();",
            f"          LOGE(\"Parent {parent_py} not registered for {py_name}\");",
            f"          throw std::runtime_error("
            f"\"Parent {parent_py} not registered\"); }}",
            f"      Py_DECREF(_p); }}",
            f"    nb::class_<{sname}, {parent_sname}> cls(m, \"{py_name}\");",
        ]
    else:
        lines.append(f"    nb::class_<{sname}, StratumObject> cls(m, \"{py_name}\");")

    # Constructors
    for (fn_name, param_s, idx_c, params) in ctor_nb:
        if params:
            nb_args = ", ".join(
                nb_arg_for_param(p, sanitize_id(p["name"]))
                for p in params
            )
            lines.append(
                f"    cls.def_static(\"new_{idx_c}\", &{fn_name},"
                f" {nb_args},"
                f" nb::rv_policy::take_ownership);")
        else:
            lines.append(
                f"    cls.def_static(\"new_{idx_c}\", &{fn_name},"
                f" nb::rv_policy::take_ownership);")

    # [FIX-32] Pre-collect instance method names to prevent static namespace collision
    inst_names: Set[str] = set()
    for m in groups["declared"] + groups["overridden"] + groups["inherited"]:
        if not m.get("is_static") and not m.get("is_constructor"):
            inst_names.add(sanitize_id(m["name"]))

    # Instance methods
    seen_nb_inst: Set[str] = set()
    for m in groups["declared"] + groups["overridden"]:
        if m.get("is_static") or m.get("is_constructor"):
            continue
        ret_decl   = ret_decl_for(m)
        params     = m.get("params", [])
        cpp_params = [
            (sanitize_id(p["name"]), cpp_type_for_param(p))
            for p in params
        ]
        if should_skip_method(ret_decl, cpp_params):
            continue
        mname   = sanitize_id(m["name"])
        inst_names.add(mname)
        ptypes  = ", ".join(t for _, t in cpp_params)
        sig_key = f"{mname}({ptypes})"
        if sig_key in seen_nb_inst:
            continue
        seen_nb_inst.add(sig_key)

        cast = (
            f"static_cast<{ret_decl}({sname}::*)({ptypes})>"
            f"(&{sname}::{mname})"
        )

        if params:
            nb_args = ", ".join(
                nb_arg_for_param(p, sanitize_id(p["name"]))
                for p in params
            )
            lines.append(f"    cls.def(\"{mname}\", {cast}, {nb_args});")
        else:
            lines.append(f"    cls.def(\"{mname}\", {cast});")

    # Static methods — [FIX-33] append _static to avoid collision with instance names
    seen_nb_static: Set[str] = set()
    for idx_in_bindable, m in enumerate(bindable):
        if not m.get("is_static") or m.get("is_constructor"):
            continue
        ret_decl   = ret_decl_for(m)
        params     = m.get("params", [])
        cpp_params = [(sanitize_id(p["name"]), cpp_type_for_param(p)) for p in params]
        if should_skip_method(ret_decl, cpp_params):
            continue

        mname   = sanitize_id(m["name"])
        py_m    = mname + "_static"
        ptypes  = ", ".join(t for _, t in cpp_params)
        sig_key = f"{py_m}({ptypes})"
        if sig_key in seen_nb_static:
            continue
        seen_nb_static.add(sig_key)
        global_idx = ctor_len + idx_in_bindable
        fn_name    = f"static_{prefix}_{mname}_{global_idx}"

        if params:
            nb_args = ", ".join(
                nb_arg_for_param(p, sanitize_id(p["name"]))
                for p in params
            )
            lines.append(f"    cls.def_static(\"{py_m}\", &{fn_name}, {nb_args});")
        else:
            lines.append(f"    cls.def_static(\"{py_m}\", &{fn_name});")

    for (getter_fn, setter_fn, safe_name, cpp_t, is_static) in field_nb_entries:
        if is_static:
            lines.append(f"    cls.def_static(\"sf_get_{safe_name}\", &{getter_fn});")
            if setter_fn:
                if cpp_t.endswith("*"):
                    lines.append(f"    cls.def_static(\"sf_set_{safe_name}\", &{setter_fn}, nb::arg(\"val\").none(true));")
                else:
                    lines.append(f"    cls.def_static(\"sf_set_{safe_name}\", &{setter_fn}, nb::arg(\"val\"));")
        else:
            lines.append(f"    cls.def(\"f_get_{safe_name}\", &{getter_fn});")
            if setter_fn:
                if cpp_t.endswith("*"):
                    lines.append(f"    cls.def(\"f_set_{safe_name}\", &{setter_fn}, nb::arg(\"val\").none(true));")
                else:
                    lines.append(f"    cls.def(\"f_set_{safe_name}\", &{setter_fn}, nb::arg(\"val\"));")

    # [Action5] __eq__ / __ne__ / __hash__ / __bool__ via IsSameObject
    lines += [
        f"    cls.def(\"__eq__\", []({sname}* self, nb::object other) -> bool {{",
        f"        if (other.is_none()) return self == nullptr || self->obj_ == nullptr;",
        f"        if (!nb::hasattr(other, \"_get_jobject_ptr\")) return false;",
        f"        int64_t op = nb::cast<int64_t>(other.attr(\"_get_jobject_ptr\")());",
        f"        if (!op || !self || !self->obj_) return op == 0 && (!self || !self->obj_);",
        f"        JNIEnv* env = get_env();",
        f"        if (!env) return false;",
        f"        return env->IsSameObject(self->obj_, (jobject)(uintptr_t)op);",
        f"    }}, nb::arg(\"other\").none(true));",
        f"    cls.def(\"__ne__\", []({sname}* self, nb::object other) -> bool {{",
        f"        if (other.is_none()) return self != nullptr && self->obj_ != nullptr;",
        f"        if (!nb::hasattr(other, \"_get_jobject_ptr\")) return true;",
        f"        int64_t op = nb::cast<int64_t>(other.attr(\"_get_jobject_ptr\")());",
        f"        if (!op || !self || !self->obj_) return !(op == 0 && (!self || !self->obj_));",
        f"        JNIEnv* env = get_env();",
        f"        if (!env) return true;",
        f"        return !env->IsSameObject(self->obj_, (jobject)(uintptr_t)op);",
        f"    }}, nb::arg(\"other\").none(true));",
        f"    // [Action19] __bool__",
        f"    cls.def(\"__bool__\", []({sname}* self) -> bool {{",
        f"        return self != nullptr && self->obj_ != nullptr;",
        f"    }});",
    ]

    lines += [
        f"    // Stratum Object Pointer Extraction & Safe Casting [Action1/Action35]",
        f"    cls.def(\"_get_jobject_ptr\", []({sname}* self) -> int64_t {{",
        f"        return (int64_t)(uintptr_t)(self ? self->obj_ : nullptr);",
        f"    }});",
        f"    cls.def_static(\"_stratum_cast\", [](nb::object py_obj) -> {sname}* {{",
        f"        if (py_obj.is_none()) return nullptr;",
        f"        if (!nb::hasattr(py_obj, \"_get_jobject_ptr\")) {{",
        f"            LOGE(\"[Action1] STRATUM_CAST FAILED: not a Stratum wrapper\");",
        f"            throw std::runtime_error(\"Object is not a Stratum wrapper\");",
        f"        }}",
        f"        int64_t ptr = nb::cast<int64_t>(py_obj.attr(\"_get_jobject_ptr\")());",
        f"        if (!ptr) {{",
        f"            LOGW(\"[Action1] STRATUM_CAST: null jobject pointer\");",
        f"            return nullptr;",
        f"        }}",
        f"        // [Action1] Real IsInstanceOf check",
        f"        JNIEnv* env = get_env();",
        f"        if (env) {{",
        f"            ensure_{prefix}_init(env);",
        f"            if (g_{prefix}_class) {{",
        f"                jboolean is_inst = env->IsInstanceOf(",
        f"                    (jobject)(uintptr_t)ptr, g_{prefix}_class);",
        f"                if (!is_inst) {{",
        f"                    LOGW(\"[Action1] STRATUM_CAST: IsInstanceOf FAILED for {fqn}\");",
        f"                    throw std::runtime_error(",
        f"                        \"stratum_cast: object is not an instance of {fqn}\");",
        f"                }}",
        f"            }}",
        f"        }}",
        f"        // [Action35] Each wrapper owns its own independent global ref",
        f"        jobject _new_gref = env ? env->NewGlobalRef((jobject)(uintptr_t)ptr) : (jobject)(uintptr_t)ptr;",
        f"        LOGV(\"[Action35] STRATUM_CAST_NEWGLOBALREF src=%p new=%p\", (void*)ptr, (void*)_new_gref);",
        f"        return new {sname}(_new_gref);",
        f"    }}, nb::arg(\"obj\").none(true), nb::rv_policy::take_ownership);",
    ]

    lines += [
        f"    LOGD(\"{py_name} registered OK\");",
        f"    LOGV(\"NB_REGISTER_OK {py_name}\");",
        f"}}",
        f"",
    ]
    return "\n".join(lines)


def build_jni_native_sig(m: dict) -> str:
    return reconstruct_jni_sig(m)


# =============================================================================
# stratum_structs.h — includes StratumThrowable and StratumWeakObject [A39/A40]
# =============================================================================

def emit_stratum_structs_h(
    all_classes: list,
    all_fqns: Optional[Set[str]] = None,
) -> str:
    for cls in all_classes:
        if cls.get("parent_fqn") == cls.get("fqn"):
            cls["parent_fqn"] = ""

    lines = [
        f"// stratum_structs.h — Stratum Stage 06 v{STAGE_VERSION} — DO NOT EDIT",
        "// [FIX-16] All virtual. [FIX-23/A42] nullable none(true).",
        "// [A39] StratumThrowable. [A40] StratumWeakObject.",
        "#pragma once",
        "#include <jni.h>",
        "#include <string>",
        "#include <vector>",
        "#include <cstdint>",
        "#include <nanobind/nanobind.h>",
        "#include <nanobind/stl/string.h>",
        "#include <nanobind/stl/vector.h>",
        "#include <nanobind/stl/list.h>",
        "namespace nb = nanobind;",
        "",
        "// ── StratumObject — opaque wrapper for any Java object ────────────",
        "struct StratumObject {",
        "    jobject obj_;",
        "    std::string stratum_key_prefix_;  // [Action4] for callback cleanup",
        "    explicit StratumObject(jobject obj) : obj_(obj) {}",
        "    virtual ~StratumObject();  // defined in bridge_core.cpp",
        "    StratumObject(const StratumObject&) = delete;",
        "    StratumObject& operator=(const StratumObject&) = delete;",
        "    std::string to_string() const;",
        "    int hash_code() const;",
        "    bool instanceof_check(const std::string& jni_class_name) const;",
        "    std::string class_name() const;",
        "    bool is_null() const { return obj_ == nullptr; }",
        "};",
        "",
        "// ── [A39] StratumThrowable — wrapper for Java Throwable ───────────",
        "struct StratumThrowable : public StratumObject {",
        "    explicit StratumThrowable(jobject obj) : StratumObject(obj) {}",
        "    ~StratumThrowable() override = default;",
        "    std::string get_message() const;   // calls getMessage()",
        "    std::string get_class_name() const; // calls getClass().getName()",
        "};",
        "",
        "// ── [A40] StratumWeakObject — Java WeakReference wrapper ──────────",
        "struct StratumWeakObject {",
        "    jweak weak_ref_;",
        "    explicit StratumWeakObject(jobject obj, JNIEnv* env) {",
        "        weak_ref_ = env ? env->NewWeakGlobalRef(obj) : nullptr;",
        "    }",
        "    virtual ~StratumWeakObject();  // DeleteWeakGlobalRef in bridge_core.cpp",
        "    StratumWeakObject(const StratumWeakObject&) = delete;",
        "    StratumWeakObject& operator=(const StratumWeakObject&) = delete;",
        "    // Returns a strong StratumObject* or nullptr if GC collected it",
        "    StratumObject* get() const;",
        "    bool is_enqueued() const { return weak_ref_ == nullptr; }",
        "};",
        "",
        "// ── StratumSurface — Surface + ANativeWindow ──────────────────────",
        "#include <android/native_window.h>",
        "#include <android/native_window_jni.h>",
        "struct StratumSurface : public StratumObject {",
        "    ANativeWindow* window = nullptr;",
        "    // [FIX-8/FIX-15] Promote to global ref before base ctor.",
        "    explicit StratumSurface(jobject surface_obj, JNIEnv* env)",
        "        : StratumObject(",
        "            (env && surface_obj) ? env->NewGlobalRef(surface_obj)",
        "                                 : surface_obj) {",
        "        if (surface_obj && env)",
        "            window = ANativeWindow_fromSurface(env, surface_obj);",
        "    }",
        "    ~StratumSurface() override {",
        "        if (window) { ANativeWindow_release(window); window = nullptr; }",
        "    }",
        "    bool has_window() const { return window != nullptr; }",
        "};",
        "",
        "// ── Forward declarations ──────────────────────────────────────────",
    ]

    fwd_fqns: Set[str] = set(all_fqns) if all_fqns else set()
    for cls in all_classes:
        fwd_fqns.add(cls["fqn"])
    for fqn in sorted(fwd_fqns):
        lines.append(f"struct {struct_name(fqn)};")

    lines += [
        "",
        "// ── ensure_*_init shims and class getters ────────────────────",
    ]
    for cls in all_classes:
        p = cpp_class_prefix(cls["fqn"])
        lines.append(f"void ensure_{p}_init(JNIEnv* env);")
        lines.append(f"jclass get_{p}_class();")

    lines += [
        "",
        "// ── Struct definitions (topological order) ───────────────────",
    ]

    fqn_to_cls = {cls["fqn"]: cls for cls in all_classes}
    ordered: List[dict] = []
    visited: Set[str]   = set()

    def visit(fqn: str) -> None:
        if fqn in visited:
            return
        visited.add(fqn)
        cls = fqn_to_cls.get(fqn)
        if not cls:
            return
        p = cls.get("parent_fqn", "")
        if not p and cls.get("parent_details"):
            p = cls["parent_details"].get("fqn", "")
        if p and p in fqn_to_cls:
            visit(p)
        ordered.append(cls)

    for cls in all_classes:
        visit(cls["fqn"])

    for cls in ordered:
        fqn        = cls["fqn"]
        parent_fqn = cls.get("parent_fqn", "")
        if not parent_fqn and cls.get("parent_details"):
            parent_fqn = cls["parent_details"].get("fqn", "")
        sn        = struct_name(fqn)
        parent_sn = (
            struct_name(parent_fqn)
            if parent_fqn and parent_fqn in fqn_to_cls else ""
        )

        if parent_sn:
            lines.append(f"struct {sn} : public {parent_sn} {{")
        else:
            lines.append(f"struct {sn} : public StratumObject {{")

        lines += [
            f"    explicit {sn}(jobject obj);",
            f"    virtual ~{sn}();",
            f"    {sn}(const {sn}&) = delete;",
            f"    {sn}& operator=(const {sn}&) = delete;",
        ]

        groups  = get_methods_for_class(cls)
        seen_m: Set[str] = set()
        for m in groups["declared"] + groups["overridden"]:
            if m.get("is_static") or m.get("is_constructor"):
                continue
            ret_decl   = ret_decl_for(m)
            cpp_params = [
                (sanitize_id(p["name"]), cpp_type_for_param(p))
                for p in m.get("params", [])
            ]
            if should_skip_method(ret_decl, cpp_params):
                continue
            mname   = sanitize_id(m["name"])
            param_s = ", ".join(f"{t} {n}" for n, t in cpp_params)
            sig_key = f"{mname}({param_s})"
            if sig_key in seen_m:
                continue
            seen_m.add(sig_key)
            lines.append(f"    {ret_decl} {mname}({param_s});")

        lines += ["};", ""]

    return "\n".join(lines)


# =============================================================================
# bridge_core.h
# =============================================================================

def emit_bridge_core_h() -> str:
    return f"""\
// bridge_core.h — Stratum Stage 06 v{STAGE_VERSION} — DO NOT EDIT
// [Action4] remove_callbacks_by_prefix added
// [Action32] stratum_callback_count() added
#pragma once
#include <jni.h>
#include <string>
#include <mutex>
#include <memory>
#include <unordered_map>
#include <nanobind/nanobind.h>
#include <nanobind/stl/string.h>
#include <nanobind/stl/function.h>
namespace nb = nanobind;

// [Action47] Stage version
#define STRATUM_VERSION "{STAGE_VERSION}"
#define STRATUM_MAX_CALLBACKS {STRATUM_MAX_CALLBACKS}

extern JavaVM*   g_jvm;
extern jobject   g_activity;
extern jobject   g_app_class_loader;
extern jmethodID g_class_loader_loadClass_method;

extern jclass g_jstring_class;
extern jclass g_proxy_class;
extern jclass g_class_class;
extern jclass g_object_class;
extern jclass g_stratum_handler_class;

extern std::unordered_map<std::string,
                          std::shared_ptr<nb::callable>> g_callbacks;
extern std::mutex g_callback_mutex;
// [Action25] Activity mutex for rotation safety
extern std::mutex g_activity_mutex;

JNIEnv*      get_env();
JNIEnv*      get_env_safe();
jclass       find_class(JNIEnv* env, const char* name);
void         store_callback(const std::string& key, nb::callable fn);
nb::callable get_callback(const std::string& key);
void         remove_callback(const std::string& key);
// [Action4] Remove all callbacks whose key starts with prefix
size_t       remove_callbacks_by_prefix(const std::string& prefix);
// [Action32] Count of stored callbacks
size_t       stratum_callback_count();
"""


# =============================================================================
# bridge_core.cpp
# =============================================================================

def emit_bridge_core_cpp() -> str:
    return r"""
// bridge_core.cpp — Stratum Stage 06 v5.0 — DO NOT EDIT
// [Action3]  Destructor uses get_env() not get_env_safe()
// [Action4]  remove_callbacks_by_prefix
// [Action6]  nativeDispatch returns jobject back to Java
// [Action32] g_callbacks size monitoring
// [Action34] g_callback_mutex + GIL deadlock fix (mutex before GIL)
// [Action36] gil_scoped_acquire same-thread safety via PyGILState_Check
// [A2]       nativeDispatch unbox primitive wrappers
// [A39]      StratumThrowable implementation
// [A40]      StratumWeakObject implementation
#include "bridge_core.h"
#include "stratum_structs.h"
#include <pthread.h>
#include <android/log.h>
#include <vector>
#include <algorithm>

#define LOGE(...) __android_log_print(ANDROID_LOG_ERROR, "Stratum", __VA_ARGS__)
#define LOGW(...) __android_log_print(ANDROID_LOG_WARN,  "Stratum", __VA_ARGS__)
#define LOGI(...) __android_log_print(ANDROID_LOG_INFO,  "Stratum", __VA_ARGS__)

#ifndef STRATUM_VERBOSE_LOG
#define STRATUM_VERBOSE_LOG 0
#endif
#ifndef STRATUM_ULTRA_LOG
#define STRATUM_ULTRA_LOG 0
#endif
#if STRATUM_VERBOSE_LOG || STRATUM_ULTRA_LOG
#define LOGD(...) __android_log_print(ANDROID_LOG_DEBUG, "Stratum", __VA_ARGS__)
#else
#define LOGD(...) ((void)0)
#endif
#if STRATUM_ULTRA_LOG
#define LOGV(...) __android_log_print(ANDROID_LOG_VERBOSE, "Stratum", __VA_ARGS__)
#define LOGT_PY_TO_JNI(cls, meth) \
    __android_log_print(ANDROID_LOG_VERBOSE, "Stratum/TRACE", \
        ">> PY->CPP->JNI  %s#%s", cls, meth)
#define LOGT_JNI_TO_PY(cls, meth, ret) \
    __android_log_print(ANDROID_LOG_VERBOSE, "Stratum/TRACE", \
        "<< JNI->CPP->PY  %s#%s  ret=%s", cls, meth, ret)
#define LOGV_PTR(name, ptr) \
    __android_log_print(ANDROID_LOG_VERBOSE, "Stratum/ARG", \
        "  arg '%s' = jobject %p  (null=%s)", name, (void*)(ptr), (ptr)==nullptr?"YES":"no")
#else
#define LOGV(...)                       ((void)0)
#define LOGT_PY_TO_JNI(cls, meth)      ((void)0)
#define LOGT_JNI_TO_PY(cls, meth, ret) ((void)0)
#define LOGV_PTR(name, ptr)            ((void)0)
#endif

JavaVM*   g_jvm      = nullptr;
jobject   g_activity = nullptr;
jobject   g_app_class_loader               = nullptr;
jmethodID g_class_loader_loadClass_method  = nullptr;

jclass g_jstring_class         = nullptr;
jclass g_proxy_class           = nullptr;
jclass g_class_class           = nullptr;
jclass g_object_class          = nullptr;
jclass g_stratum_handler_class = nullptr;

std::unordered_map<std::string, std::shared_ptr<nb::callable>> g_callbacks;
std::mutex g_callback_mutex;
// [Action25] Activity mutex
std::mutex g_activity_mutex;

// ── Thread attach / detach ────────────────────────────────────────────────────
static pthread_key_t  g_jni_detach_key;
static pthread_once_t g_jni_key_once = PTHREAD_ONCE_INIT;

static void detach_thread(void*) {
    if (g_jvm) g_jvm->DetachCurrentThread();
}
static void make_jni_key() {
    pthread_key_create(&g_jni_detach_key, detach_thread);
}

// [FIX-13] Returns nullptr when g_jvm not set.
JNIEnv* get_env() {
    if (!g_jvm) {
        LOGW("get_env: g_jvm is null");
        return nullptr;
    }
    JNIEnv* env = nullptr;
    jint rc = g_jvm->GetEnv(
        reinterpret_cast<void**>(&env), JNI_VERSION_1_6);
    if (rc == JNI_EDETACHED) {
        pthread_once(&g_jni_key_once, make_jni_key);
        g_jvm->AttachCurrentThread(&env, nullptr);
        pthread_setspecific(g_jni_detach_key,
                            reinterpret_cast<void*>(1));
        LOGD("get_env: attached thread, env=%p", env);
        LOGV("THREAD_ATTACH env=%p", env);
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
    if (!env) {
        LOGE("find_class: env is null for %s", name);
        return nullptr;
    }
    LOGV("FIND_CLASS attempt: %s", name);
    jclass cls = env->FindClass(name);
    if (cls) {
        LOGD("find_class: found %s via FindClass", name);
        return cls;
    }
    env->ExceptionClear();
    if (g_app_class_loader && g_class_loader_loadClass_method) {
        std::string dot_name = name;
        for (char& c : dot_name) { if (c == '/') c = '.'; }
        jstring jname = env->NewStringUTF(dot_name.c_str());
        cls = (jclass)env->CallObjectMethod(
            g_app_class_loader, g_class_loader_loadClass_method, jname);
        env->DeleteLocalRef(jname);
        if (env->ExceptionCheck()) {
            LOGE("find_class: loadClass failed for %s", name);
            env->ExceptionClear();
            return nullptr;
        }
        if (cls) {
            LOGD("find_class: found %s via ClassLoader", name);
        } else {
            LOGE("find_class: NOT found: %s", name);
        }
        return cls;
    }
    LOGE("find_class: no ClassLoader available for %s", name);
    return nullptr;
}

void store_callback(const std::string& key, nb::callable fn) {
    std::lock_guard<std::mutex> lock(g_callback_mutex);
    g_callbacks[key] = std::make_shared<nb::callable>(std::move(fn));
    size_t sz = g_callbacks.size();
    // [Action32] Warn on excessive callback map size
    if (sz > STRATUM_MAX_CALLBACKS) {
        LOGW("[Action32] CALLBACK_MAP_SIZE_WARNING count=%zu threshold=%d",
             sz, STRATUM_MAX_CALLBACKS);
    }
    LOGD("store_callback: key=%s total=%zu", key.c_str(), sz);
    LOGV("CALLBACK_STORE key=%s total=%zu", key.c_str(), sz);
}

nb::callable get_callback(const std::string& key) {
    std::lock_guard<std::mutex> lock(g_callback_mutex);
    auto it = g_callbacks.find(key);
    if (it == g_callbacks.end() || !it->second) {
        LOGV("CALLBACK_MISS key=%s", key.c_str());
        return nb::callable();
    }
    LOGV("CALLBACK_HIT key=%s", key.c_str());
    return *it->second;
}

void remove_callback(const std::string& key) {
    std::lock_guard<std::mutex> lock(g_callback_mutex);
    g_callbacks.erase(key);
    LOGD("remove_callback: key=%s", key.c_str());
    LOGV("CALLBACK_REMOVED key=%s", key.c_str());
}

// [Action4] Remove all callbacks whose key starts with prefix
size_t remove_callbacks_by_prefix(const std::string& prefix) {
    std::lock_guard<std::mutex> lock(g_callback_mutex);
    size_t removed = 0;
    for (auto it = g_callbacks.begin(); it != g_callbacks.end(); ) {
        if (it->first.find(prefix) == 0) {
            it = g_callbacks.erase(it);
            ++removed;
        } else {
            ++it;
        }
    }
    LOGD("remove_callbacks_by_prefix: prefix=%s removed=%zu", prefix.c_str(), removed);
    return removed;
}

// [Action32] Callback count
size_t stratum_callback_count() {
    std::lock_guard<std::mutex> lock(g_callback_mutex);
    return g_callbacks.size();
}

// ── StratumObject implementation ──────────────────────────────────────────────

// [Action3] Destructor uses get_env() not get_env_safe() so background thread attaches
StratumObject::~StratumObject() {
    JNIEnv* env = get_env();  // [Action3] attach if needed
    if (env && obj_) {
        env->DeleteGlobalRef(obj_);
        obj_ = nullptr;
        LOGD("StratumObject::~StratumObject global ref deleted");
        LOGV("STRATUM_OBJECT_FREED");
    }
}

std::string StratumObject::to_string() const {
    JNIEnv* env = get_env();
    if (!env || !obj_) return "<null>";
    jclass    cls = env->GetObjectClass(obj_);
    jmethodID mid = env->GetMethodID(cls, "toString",
                                     "()Ljava/lang/String;");
    env->DeleteLocalRef(cls);
    if (!mid) { env->ExceptionClear(); return "<no toString>"; }
    jstring js = (jstring)env->CallObjectMethod(obj_, mid);
    if (!js) return "<null>";
    const char* c = env->GetStringUTFChars(js, nullptr);
    std::string res(c);
    env->ReleaseStringUTFChars(js, c);
    env->DeleteLocalRef(js);
    return res;
}

int StratumObject::hash_code() const {
    JNIEnv* env = get_env();
    if (!env || !obj_) return 0;
    jclass    cls = env->GetObjectClass(obj_);
    jmethodID mid = env->GetMethodID(cls, "hashCode", "()I");
    env->DeleteLocalRef(cls);
    if (!mid) { env->ExceptionClear(); return 0; }
    return (int)env->CallIntMethod(obj_, mid);
}

bool StratumObject::instanceof_check(
        const std::string& jni_class_name) const {
    JNIEnv* env = get_env();
    if (!env || !obj_) return false;
    jclass cls = find_class(env, jni_class_name.c_str());
    if (!cls) { env->ExceptionClear(); return false; }
    bool result = env->IsInstanceOf(obj_, cls);
    env->DeleteLocalRef(cls);
    return result;
}

std::string StratumObject::class_name() const {
    JNIEnv* env = get_env();
    if (!env || !obj_) return "<null>";
    jclass    cls   = env->GetObjectClass(obj_);
    jmethodID gname = env->GetMethodID(cls, "getName",
                                       "()Ljava/lang/String;");
    if (!gname) {
        env->ExceptionClear();
        env->DeleteLocalRef(cls);
        return "<unknown>";
    }
    jstring jname = (jstring)env->CallObjectMethod(cls, gname);
    env->DeleteLocalRef(cls);
    if (!jname) return "<unknown>";
    const char* c = env->GetStringUTFChars(jname, nullptr);
    std::string res(c);
    env->ReleaseStringUTFChars(jname, c);
    env->DeleteLocalRef(jname);
    return res;
}

// ── [A39] StratumThrowable implementation ─────────────────────────────────────

std::string StratumThrowable::get_message() const {
    JNIEnv* env = get_env();
    if (!env || !obj_) return "<null throwable>";
    jclass    cls = env->GetObjectClass(obj_);
    jmethodID mid = env->GetMethodID(cls, "getMessage", "()Ljava/lang/String;");
    env->DeleteLocalRef(cls);
    if (!mid) { env->ExceptionClear(); return "<no getMessage>"; }
    jstring js = (jstring)env->CallObjectMethod(obj_, mid);
    if (!js) return "<null message>";
    const char* c = env->GetStringUTFChars(js, nullptr);
    std::string res(c);
    env->ReleaseStringUTFChars(js, c);
    env->DeleteLocalRef(js);
    return res;
}

std::string StratumThrowable::get_class_name() const {
    return class_name();
}

// ── [A40] StratumWeakObject implementation ────────────────────────────────────

StratumWeakObject::~StratumWeakObject() {
    JNIEnv* env = get_env();
    if (env && weak_ref_) {
        env->DeleteWeakGlobalRef(weak_ref_);
        weak_ref_ = nullptr;
        LOGD("StratumWeakObject: weak ref deleted");
    }
}

StratumObject* StratumWeakObject::get() const {
    JNIEnv* env = get_env();
    if (!env || !weak_ref_) return nullptr;
    // IsSameObject(ref, NULL) returns true if GC collected it
    if (env->IsSameObject(weak_ref_, nullptr)) {
        LOGV("WEAKREF_GET: collected by GC");
        return nullptr;
    }
    jobject strong = env->NewGlobalRef(weak_ref_);
    if (!strong) return nullptr;
    LOGV("WEAKREF_GET: resolved weak=%p strong=%p", (void*)weak_ref_, (void*)strong);
    return new StratumObject(strong);
}

// ── [Action6] nativeDispatch — returns jobject back to Java ───────────────────
extern "C" JNIEXPORT jobject JNICALL
Java_com_stratum_runtime_StratumInvocationHandler_nativeDispatch(
        JNIEnv* env, jclass, jstring jkey,
        jstring jmethod, jobjectArray args) {

    const char* kc = env->GetStringUTFChars(jkey, nullptr);
    std::string base_key(kc);
    env->ReleaseStringUTFChars(jkey, kc);
    LOGD("nativeDispatch key=%s", base_key.c_str());
    LOGV("DISPATCH_IN key=%s", base_key.c_str());

    std::string routed_key = base_key;
    if (jmethod) {
        const char* mc = env->GetStringUTFChars(jmethod, nullptr);
        routed_key = base_key + "#" + std::string(mc);
        LOGV("DISPATCH_ROUTED_KEY %s", routed_key.c_str());
        env->ReleaseStringUTFChars(jmethod, mc);
    }

    // [Action34] CRITICAL: acquire mutex BEFORE GIL to prevent deadlock
    // Lock order: mutex → GIL (never GIL → mutex)
    nb::callable fn;
    {
        std::lock_guard<std::mutex> lock(g_callback_mutex);
        LOGV("DISPATCH_MUTEX_ACQUIRE");
        auto it = g_callbacks.find(routed_key);
        if (it == g_callbacks.end()) it = g_callbacks.find(base_key);
        if (it != g_callbacks.end() && it->second) fn = *it->second;
        LOGV("DISPATCH_MUTEX_RELEASE");
    }

    if (!fn.is_valid()) {
        LOGW("nativeDispatch: no callback for key=%s", routed_key.c_str());
        LOGV("DISPATCH_NO_CALLBACK key=%s", routed_key.c_str());
        return nullptr;
    }

    LOGV("DISPATCH_CALLBACK_FOUND key=%s", routed_key.c_str());

    // [Action36] Same-thread GIL safety check
    jobject result = nullptr;
    bool already_held = PyGILState_Check();
    LOGV("DISPATCH_GIL_ALREADY_HELD=%d", (int)already_held);

    auto call_fn = [&]() -> jobject {
        try {
            jsize len = args ? env->GetArrayLength(args) : 0;
            LOGV("DISPATCH_ARGS_LEN %d", (int)len);
            nb::object py_result;
            if (len == 0) {
                LOGV("DISPATCH_CALL_NOARGS");
                py_result = fn();
                LOGV("DISPATCH_CALL_NOARGS_DONE");
            } else {
                nb::list py_args;
                for (jsize i = 0; i < len; ++i) {
                    jobject elem = env->GetObjectArrayElement(args, i);
                    if (!elem) {
                        py_args.append(nb::none());
                        LOGV("DISPATCH_ARG[%d] = None", (int)i);
                        continue;
                    }
                    // [A2] Unbox Java primitive wrappers
                    bool unboxed = false;
                    // Check for Integer, Boolean, Long, Double, Float
                    static jclass _IntCls = nullptr, _BoolCls = nullptr,
                                  _LongCls = nullptr, _DblCls = nullptr,
                                  _FltCls = nullptr;
                    if (!_IntCls)  { jclass c=env->FindClass("java/lang/Integer");  _IntCls=(jclass)env->NewGlobalRef(c);  env->DeleteLocalRef(c); }
                    if (!_BoolCls) { jclass c=env->FindClass("java/lang/Boolean");  _BoolCls=(jclass)env->NewGlobalRef(c); env->DeleteLocalRef(c); }
                    if (!_LongCls) { jclass c=env->FindClass("java/lang/Long");     _LongCls=(jclass)env->NewGlobalRef(c); env->DeleteLocalRef(c); }
                    if (!_DblCls)  { jclass c=env->FindClass("java/lang/Double");   _DblCls=(jclass)env->NewGlobalRef(c);  env->DeleteLocalRef(c); }
                    if (!_FltCls)  { jclass c=env->FindClass("java/lang/Float");    _FltCls=(jclass)env->NewGlobalRef(c);  env->DeleteLocalRef(c); }

                    if (_IntCls && env->IsInstanceOf(elem, _IntCls)) {
                        jmethodID mid = env->GetMethodID(_IntCls, "intValue", "()I");
                        jint v = env->CallIntMethod(elem, mid);
                        py_args.append(nb::int_((long long)v));
                        env->DeleteLocalRef(elem); unboxed = true;
                        LOGV("DISPATCH_ARG[%d] = Integer %d", (int)i, (int)v);
                    } else if (_BoolCls && env->IsInstanceOf(elem, _BoolCls)) {
                        jmethodID mid = env->GetMethodID(_BoolCls, "booleanValue", "()Z");
                        jboolean v = env->CallBooleanMethod(elem, mid);
                        py_args.append(nb::bool_(v != JNI_FALSE));
                        env->DeleteLocalRef(elem); unboxed = true;
                        LOGV("DISPATCH_ARG[%d] = Boolean %d", (int)i, (int)v);
                    } else if (_LongCls && env->IsInstanceOf(elem, _LongCls)) {
                        jmethodID mid = env->GetMethodID(_LongCls, "longValue", "()J");
                        jlong v = env->CallLongMethod(elem, mid);
                        py_args.append(nb::int_((long long)v));
                        env->DeleteLocalRef(elem); unboxed = true;
                        LOGV("DISPATCH_ARG[%d] = Long %lld", (int)i, (long long)v);
                    } else if (_DblCls && env->IsInstanceOf(elem, _DblCls)) {
                        jmethodID mid = env->GetMethodID(_DblCls, "doubleValue", "()D");
                        jdouble v = env->CallDoubleMethod(elem, mid);
                        py_args.append(nb::float_((double)v));
                        env->DeleteLocalRef(elem); unboxed = true;
                        LOGV("DISPATCH_ARG[%d] = Double %f", (int)i, (double)v);
                    } else if (_FltCls && env->IsInstanceOf(elem, _FltCls)) {
                        jmethodID mid = env->GetMethodID(_FltCls, "floatValue", "()F");
                        jfloat v = env->CallFloatMethod(elem, mid);
                        py_args.append(nb::float_((double)v));
                        env->DeleteLocalRef(elem); unboxed = true;
                        LOGV("DISPATCH_ARG[%d] = Float %f", (int)i, (float)v);
                    }

                    if (!unboxed) {
                        if (g_jstring_class
                                && env->IsInstanceOf(elem, g_jstring_class)) {
                            const char* s = env->GetStringUTFChars(
                                (jstring)elem, nullptr);
                            py_args.append(nb::str(s));
                            LOGV("DISPATCH_ARG[%d] = str '%s'", (int)i, s);
                            env->ReleaseStringUTFChars((jstring)elem, s);
                            env->DeleteLocalRef(elem);
                        } else {
                            jobject gref = env->NewGlobalRef(elem);
                            env->DeleteLocalRef(elem);
                            LOGV("DISPATCH_ARG[%d] = obj %p", (int)i, gref);
                            py_args.append(nb::cast(
                                new StratumObject(gref),
                                nb::rv_policy::take_ownership));
                        }
                    }
                }
                LOGV("DISPATCH_CALL_WITH_ARGS count=%d", (int)len);
                py_result = fn(*nb::tuple(py_args));
                LOGV("DISPATCH_CALL_WITH_ARGS_DONE");
            }

            // [Action6] Convert Python return value back to Java jobject
            if (py_result.is_none()) return nullptr;
            if (nb::isinstance<nb::bool_>(py_result)) {
                // Box bool → Boolean
                static jclass _bcls = nullptr;
                static jmethodID _bvalof = nullptr;
                if (!_bcls) { jclass c=env->FindClass("java/lang/Boolean"); _bcls=(jclass)env->NewGlobalRef(c); env->DeleteLocalRef(c); }
                if (!_bvalof) _bvalof = env->GetStaticMethodID(_bcls, "valueOf", "(Z)Ljava/lang/Boolean;");
                jboolean bv = nb::cast<bool>(py_result) ? JNI_TRUE : JNI_FALSE;
                return env->CallStaticObjectMethod(_bcls, _bvalof, bv);
            }
            if (nb::isinstance<nb::int_>(py_result)) {
                static jclass _icls = nullptr;
                static jmethodID _ivalof = nullptr;
                if (!_icls) { jclass c=env->FindClass("java/lang/Integer"); _icls=(jclass)env->NewGlobalRef(c); env->DeleteLocalRef(c); }
                if (!_ivalof) _ivalof = env->GetStaticMethodID(_icls, "valueOf", "(I)Ljava/lang/Integer;");
                return env->CallStaticObjectMethod(_icls, _ivalof, (jint)nb::cast<long long>(py_result));
            }
            if (nb::isinstance<nb::float_>(py_result)) {
                static jclass _dcls = nullptr;
                static jmethodID _dvalof = nullptr;
                if (!_dcls) { jclass c=env->FindClass("java/lang/Double"); _dcls=(jclass)env->NewGlobalRef(c); env->DeleteLocalRef(c); }
                if (!_dvalof) _dvalof = env->GetStaticMethodID(_dcls, "valueOf", "(D)Ljava/lang/Double;");
                return env->CallStaticObjectMethod(_dcls, _dvalof, (jdouble)nb::cast<double>(py_result));
            }
            if (nb::isinstance<nb::str>(py_result)) {
                return env->NewStringUTF(nb::cast<std::string>(py_result).c_str());
            }
            if (nb::hasattr(py_result, "_get_jobject_ptr")) {
                int64_t ptr = nb::cast<int64_t>(py_result.attr("_get_jobject_ptr")());
                if (ptr) return (jobject)(uintptr_t)ptr;
            }
            return nullptr;
        }
        catch (nb::python_error& e) {
            // [PATCH-PYEXC] Propagate Python exception to Java as RuntimeException
            // Before: silent nullptr → Java NPE with no traceback
            // After:  Java sees RuntimeException with Python message inside
            std::string _pymsg = e.what();
            LOGE("nativeDispatch Python error: %s", _pymsg.c_str());
            LOGD("[PATCH-PYEXC] translating Python error to Java RuntimeException");
            LOGV("[PATCH-PYEXC] py_msg=%s", _pymsg.c_str());
            // Clear Python error state BEFORE any JNI call
            e.restore();
            PyErr_Clear();
            // Throw into Java only if no Java exception is already pending
            if (!env->ExceptionCheck()) {
                jclass _rex = env->FindClass("java/lang/RuntimeException");
                if (_rex) {
                    std::string _full = "[Stratum] Python error: " + _pymsg;
                    env->ThrowNew(_rex, _full.c_str());
                    env->DeleteLocalRef(_rex);
                    LOGD("[PATCH-PYEXC] Java RuntimeException thrown OK");
                    LOGV("[PATCH-PYEXC] ThrowNew done msg=%s", _full.c_str());
                } else {
                    env->ExceptionClear();
                    LOGE("[PATCH-PYEXC] could not find RuntimeException class");
                }
            } else {
                LOGW("[PATCH-PYEXC] Java exception already pending, not overwriting");
            }
            return nullptr;
        }
        catch (const std::exception& e) {
            LOGE("nativeDispatch exception: %s", e.what());
            PyErr_SetString(PyExc_RuntimeError, e.what());
            return nullptr;
        }
    };

    if (already_held) {
        LOGV("DISPATCH_GIL_ALREADY_HELD — calling directly");
        result = call_fn();
    } else {
        nb::gil_scoped_acquire _acquire;
        LOGV("DISPATCH_GIL_ACQUIRED");
        result = call_fn();
    }

    LOGV("DISPATCH_DONE key=%s", base_key.c_str());
    return result;
}
"""


# =============================================================================
# bridge_main.cpp
# =============================================================================

def _has_context_ctor(cls: dict) -> bool:
    """[FIX-7] Scan ALL constructor params."""
    groups = get_methods_for_class(cls)
    for m in groups["constructors"]:
        for p in m.get("params", []):
            if p.get("java_type", "").endswith("Context"):
                return True
    return False


def emit_bridge_main(classes: list, failed_fqns: Set[str] = None) -> str:
    """[Action26] failed_fqns: skip subclasses of failed classes."""
    if failed_fqns is None:
        failed_fqns = set()

    batch_size = NANOBIND_BATCH_SIZE
    fqn_to_cls = {cls["fqn"]: cls for cls in classes}
    ordered: List[dict] = []
    visited: Set[str]   = set()

    def visit(fqn: str) -> None:
        if fqn in visited:
            return
        visited.add(fqn)
        cls = fqn_to_cls.get(fqn)
        if not cls:
            return
        p = cls.get("parent_fqn", "")
        if not p and cls.get("parent_details"):
            p = cls["parent_details"].get("fqn", "")
        if p and p in fqn_to_cls:
            visit(p)
        ordered.append(cls)

    for cls in classes:
        visit(cls["fqn"])

    # [Action26] Filter out subclasses of failed parents
    def has_failed_parent(fqn: str) -> Optional[str]:
        data = fqn_to_cls.get(fqn)
        if not data:
            return None
        p = data.get("parent_fqn", "")
        if p in failed_fqns:
            return p
        return None

    filtered_ordered = []
    for cls in ordered:
        failed_parent = has_failed_parent(cls["fqn"])
        if failed_parent:
            print(f"  [Action26] SKIP_REGISTRATION {cls['fqn']} — parent {failed_parent} failed")
            continue
        filtered_ordered.append(cls)

    factory_classes = [
        c for c in filtered_ordered
        if _has_context_ctor(c) and c["fqn"] != "android.app.Activity"
    ]
    batches: List[List[dict]] = [
        filtered_ordered[i:i + batch_size]
        for i in range(0, len(filtered_ordered), batch_size)
    ]

    lines = [
        f"// bridge_main.cpp — Stratum Stage 06 v{STAGE_VERSION} — DO NOT EDIT",
        "// [Action1]  stratum_cast_to (renamed from cast_to)",
        "// [Action5]  __eq__/__ne__ via IsSameObject",
        "// [Action11] stratum_get_activity (renamed)",
        "// [Action13] Enum support",
        "// [Action19] __bool__",
        "// [Action25] g_activity_mutex",
        "// [Action26] Skip subclasses of failed parents",
        "// [Action32] stratum_callback_count",
        "// [A39]      StratumThrowable registration",
        "// [A40]      StratumWeakObject registration",
        "#include <jni.h>",
        "#include <stdint.h>",
        '#include "bridge_core.h"',
        '#include "stratum_structs.h"',
        "#include <stdexcept>",
        "#include <nanobind/nanobind.h>",
        "#include <nanobind/stl/string.h>",
        "#include <nanobind/stl/vector.h>",
        "#include <nanobind/stl/list.h>",
        "#include <nanobind/stl/function.h>",
        "#include <android/log.h>",
        "#define LOGE(...) __android_log_print("
        "ANDROID_LOG_ERROR, \"Stratum\", __VA_ARGS__)",
        "#define LOGI(...) __android_log_print("
        "ANDROID_LOG_INFO,  \"Stratum\", __VA_ARGS__)",
        "#define LOGW(...) __android_log_print("
        "ANDROID_LOG_WARN,  \"Stratum\", __VA_ARGS__)",
        "#ifndef STRATUM_VERBOSE_LOG",
        "#define STRATUM_VERBOSE_LOG 0",
        "#endif",
        "#ifndef STRATUM_ULTRA_LOG",
        "#define STRATUM_ULTRA_LOG 0",
        "#endif",
        "#if STRATUM_VERBOSE_LOG || STRATUM_ULTRA_LOG",
        "#define LOGD(...) __android_log_print("
        "ANDROID_LOG_DEBUG, \"Stratum\", __VA_ARGS__)",
        "#else",
        "#define LOGD(...) ((void)0)",
        "#endif",
        "#if STRATUM_ULTRA_LOG",
        "#define LOGV(...) __android_log_print("
        "ANDROID_LOG_VERBOSE, \"Stratum\", __VA_ARGS__)",
        "#else",
        "#define LOGV(...) ((void)0)",
        "#endif",
        "namespace nb = nanobind;",
        "",
    ]

    for cls in filtered_ordered:
        lines.append(
            f"__attribute__((noinline)) void "
            f"register_{cpp_class_prefix(cls['fqn'])}(nb::module_& m);")
    lines.append("")

    for bi, batch in enumerate(batches):
        lines.append(
            f"__attribute__((noinline)) static void "
            f"register_batch_{bi}(nb::module_& m) {{"
        )
        for cls in batch:
            p = cpp_class_prefix(cls["fqn"])
            lines += [
                f"    LOGD(\"batch {bi}: registering {cls['fqn']}\");",
                f"    LOGV(\"BATCH_{bi}_REG {cls['fqn']}\");",
                f"    try {{ register_{p}(m); }}",
                f"    catch (nb::python_error& e) {{",
                f"        e.restore();",
                f"        LOGE(\"PyErr batch {bi}: {cls['fqn']}\");",
                f"        PyErr_Clear(); }}",
                f"    catch (const std::exception& e) {{",
                f"        LOGE(\"CxxErr batch {bi}: {cls['fqn']}: %s\","
                f" e.what()); }}",
                f"    catch (...) {{",
                f"        LOGE(\"UnkErr batch {bi}: {cls['fqn']}\"); }}",
            ]
        lines += ["}", ""]

    lines += [
        f"typedef void (*BatchRegFn)(nb::module_&);",
        f"static const BatchRegFn g_batch_fns[] = {{",
    ]
    for bi in range(len(batches)):
        lines.append(f"    register_batch_{bi},")
    lines += [
        f"}};",
        f"static const size_t g_batch_count = {len(batches)};",
        f"static const size_t g_total_classes = {len(filtered_ordered)};",
        "",
    ]

    # ── JNI_OnLoad ─────────────────────────────────────────────────────────────
    lines += [
        "extern \"C\" JNIEXPORT jint JNICALL JNI_OnLoad(JavaVM* vm, void*) {",
        "    g_jvm = vm;",
        "    LOGI(\"JNI_OnLoad begin — Stratum v" + STAGE_VERSION + "\");",
        "    JNIEnv* env = nullptr;",
        "    if (vm->GetEnv(reinterpret_cast<void**>(&env),"
        " JNI_VERSION_1_6) != JNI_OK) {",
        "        LOGE(\"JNI_OnLoad: GetEnv failed\");",
        "        return JNI_ERR;",
        "    }",
        "    LOGV(\"JNI_ONLOAD env=%p\", env);",
        "    jclass hcls = env->FindClass(",
        "        \"com/stratum/runtime/StratumInvocationHandler\");",
        "    if (hcls) {",
        "        jclass cc = env->GetObjectClass(hcls);",
        "        jmethodID gcl = env->GetMethodID(",
        "            cc, \"getClassLoader\","
        " \"()Ljava/lang/ClassLoader;\");",
        "        jobject loader = env->CallObjectMethod(hcls, gcl);",
        "        g_app_class_loader = env->NewGlobalRef(loader);",
        "        jclass lcls = env->FindClass(\"java/lang/ClassLoader\");",
        "        g_class_loader_loadClass_method = env->GetMethodID(",
        "            lcls, \"loadClass\","
        " \"(Ljava/lang/String;)Ljava/lang/Class;\");",
        "        g_stratum_handler_class = "
        "(jclass)env->NewGlobalRef(hcls);",
        "        env->DeleteLocalRef(lcls); env->DeleteLocalRef(loader);",
        "        env->DeleteLocalRef(cc);   env->DeleteLocalRef(hcls);",
        "        LOGI(\"JNI_OnLoad: StratumHandler and ClassLoader cached\");",
        "        LOGV(\"JNI_ONLOAD classloader=%p\", g_app_class_loader);",
        "    } else {",
        "        LOGE(\"JNI_OnLoad: StratumInvocationHandler NOT found\");",
        "        env->ExceptionClear();",
        "    }",
        "    auto cache = [&](const char* n) -> jclass {",
        "        jclass l = env->FindClass(n);",
        "        if (!l) {",
        "            LOGE(\"JNI_OnLoad: cache failed for %s\", n);",
        "            env->ExceptionClear();",
        "            return nullptr;",
        "        }",
        "        jclass g = (jclass)env->NewGlobalRef(l);",
        "        env->DeleteLocalRef(l);",
        "        LOGD(\"JNI_OnLoad: cached %s\", n);",
        "        LOGV(\"JNI_ONLOAD_CACHE %s cls=%p\", n, (void*)g);",
        "        return g;",
        "    };",
        "    g_jstring_class = cache(\"java/lang/String\");",
        "    g_proxy_class   = cache(\"java/lang/reflect/Proxy\");",
        "    g_class_class   = cache(\"java/lang/Class\");",
        "    g_object_class  = cache(\"java/lang/Object\");",
        f"    LOGI(\"JNI_OnLoad: %zu classes, lazy init on first touch.\","
        " g_total_classes);",
        "    return JNI_VERSION_1_6;",
        "}",
        "",
        "// [FIX-12] Release all global refs on unload",
        "extern \"C\" JNIEXPORT void JNICALL JNI_OnUnload(JavaVM* vm, void*) {",
        "    LOGI(\"JNI_OnUnload\");",
        "    JNIEnv* env = nullptr;",
        "    if (vm->GetEnv(reinterpret_cast<void**>(&env),"
        " JNI_VERSION_1_6) != JNI_OK) return;",
        "    auto del = [&](jobject& r) {",
        "        if (r) { env->DeleteGlobalRef(r); r = nullptr; }",
        "    };",
        "    del(reinterpret_cast<jobject&>(g_jstring_class));",
        "    del(reinterpret_cast<jobject&>(g_proxy_class));",
        "    del(reinterpret_cast<jobject&>(g_class_class));",
        "    del(reinterpret_cast<jobject&>(g_object_class));",
        "    del(reinterpret_cast<jobject&>(g_stratum_handler_class));",
        "    del(g_app_class_loader);",
        "    del(g_activity);  // [FIX-12]",
        "    LOGI(\"JNI_OnUnload: complete\");",
        "}",
        "",
    ]

    # ── Lifecycle dispatcher ───────────────────────────────────────────────────
    lines += [
        "static std::unordered_map<std::string, nb::callable>"
        " g_lifecycle_cbs;",
        "static std::mutex g_lifecycle_mutex;",
        "",
        "static void dispatch_lifecycle(const char* name) {",
        "    LOGD(\"dispatch_lifecycle: %s\", name);",
        "    LOGV(\"LIFECYCLE_DISPATCH: %s\", name);",
        "    // [Action34] Copy under mutex, call after release",
        "    nb::callable fn;",
        "    {",
        "        std::lock_guard<std::mutex> lk(g_lifecycle_mutex);",
        "        auto it = g_lifecycle_cbs.find(name);",
        "        if (it == g_lifecycle_cbs.end()) {",
        "            LOGW(\"dispatch_lifecycle: no callback for %s\", name);",
        "            LOGV(\"LIFECYCLE_NO_CB: %s\", name);",
        "            return;",
        "        }",
        "        fn = it->second;",
        "    }",
        "    LOGV(\"LIFECYCLE_CB_FOUND: %s\", name);",
        "    // [Action36] Check if GIL already held",
        "    bool already_held = PyGILState_Check();",
        "    auto do_call = [&]() {",
        "        try {",
        "            fn();",
        "            LOGD(\"dispatch_lifecycle: %s done\", name);",
        "        }",
        "        catch (nb::python_error& e) {",
        "            e.restore();",
        "            LOGE(\"dispatch_lifecycle Python error in %s\", name);",
        "            if (PyErr_Occurred()) PyErr_Print();",
        "        }",
        "        catch (const std::exception& e) {",
        "            LOGE(\"dispatch_lifecycle C++ error in %s: %s\","
        " name, e.what());",
        "        }",
        "        catch (...) {",
        "            LOGE(\"dispatch_lifecycle unknown error in %s\", name);",
        "        }",
        "    };",
        "    if (already_held) {",
        "        LOGV(\"LIFECYCLE_GIL_ALREADY_HELD\");",
        "        do_call();",
        "    } else {",
        "        nb::gil_scoped_acquire gil;",
        "        LOGV(\"LIFECYCLE_GIL_ACQUIRED\");",
        "        do_call();",
        "    }",
        "}",
        "",
    ]

    # ── NB_MODULE ─────────────────────────────────────────────────────────────
    lines += [
        "NB_MODULE(_stratum, m) {",
        "    try {",
        f"        LOGI(\"NB_MODULE: {len(filtered_ordered)} classes"
        f" in {len(batches)} batches — Stratum v{STAGE_VERSION}\");",
        f"        LOGV(\"NB_MODULE_START total={len(filtered_ordered)} batches={len(batches)}\");",
        "",
        "        // [FIX-11] Register base types BEFORE batch classes",
        "        nb::class_<StratumObject>(m, \"StratumObject\")",
        "            .def(\"is_null\",          &StratumObject::is_null)",
        "            .def(\"to_string\",        &StratumObject::to_string)",
        "            .def(\"__str__\",          &StratumObject::to_string)",
        "            .def(\"__repr__\",         &StratumObject::to_string)",
        "            .def(\"__hash__\",         &StratumObject::hash_code)",
        "            .def(\"hash_code\",        &StratumObject::hash_code)",
        "            .def(\"instanceof_check\","
        " &StratumObject::instanceof_check)",
        "            .def(\"class_name\",       &StratumObject::class_name)",
        "            .def_rw(\"stratum_key_prefix\", &StratumObject::stratum_key_prefix_)",
        "            // [Action19] __bool__",
        "            .def(\"__bool__\", [](StratumObject* self) { return self && self->obj_; })",
        "            // [Action5] __eq__ / __ne__",
        "            .def(\"__eq__\", [](StratumObject* self, nb::object other) -> bool {",
        "                if (other.is_none()) return !self || !self->obj_;",
        "                if (!nb::hasattr(other, \"_get_jobject_ptr\")) return false;",
        "                int64_t op = nb::cast<int64_t>(other.attr(\"_get_jobject_ptr\")());",
        "                if (!self || !self->obj_) return op == 0;",
        "                JNIEnv* env = get_env();",
        "                if (!env) return false;",
        "                return env->IsSameObject(self->obj_, (jobject)(uintptr_t)op);",
        "            }, nb::arg(\"other\").none(true))",
        "            .def(\"__ne__\", [](StratumObject* self, nb::object other) -> bool {",
        "                if (other.is_none()) return self && self->obj_;",
        "                if (!nb::hasattr(other, \"_get_jobject_ptr\")) return true;",
        "                int64_t op = nb::cast<int64_t>(other.attr(\"_get_jobject_ptr\")());",
        "                if (!self || !self->obj_) return op != 0;",
        "                JNIEnv* env = get_env();",
        "                if (!env) return true;",
        "                return !env->IsSameObject(self->obj_, (jobject)(uintptr_t)op);",
        "            }, nb::arg(\"other\").none(true))",
        "            .def(\"_get_jobject_ptr\", [](StratumObject* self) -> int64_t {",
        "                return (int64_t)(uintptr_t)(self ? self->obj_ : nullptr);",
        "            });",
        "",
        "        // [A39] StratumThrowable",
        "        nb::class_<StratumThrowable, StratumObject>(m, \"StratumThrowable\")",
        "            .def(\"get_message\",    &StratumThrowable::get_message)",
        "            .def(\"get_class_name\", &StratumThrowable::get_class_name);",
        "",
        "        // [A40] StratumWeakObject",
        "        nb::class_<StratumWeakObject>(m, \"StratumWeakObject\")",
        "            .def(\"get\",         &StratumWeakObject::get, nb::rv_policy::take_ownership)",
        "            .def(\"is_enqueued\", &StratumWeakObject::is_enqueued);",
        "",
        "        nb::class_<StratumSurface, StratumObject>"
        "(m, \"StratumSurface\")",
        "            .def(\"has_window\", &StratumSurface::has_window);",
        "",
        "        LOGD(\"NB_MODULE: base types registered\");",
        "        LOGV(\"NB_MODULE_BASE_TYPES_OK\");",
        "",
        "        for (size_t bi = 0; bi < g_batch_count; ++bi) {",
        "            LOGD(\"NB_MODULE: running batch %zu\", bi);",
        "            LOGV(\"NB_MODULE_BATCH_START %zu\", bi);",
        "            g_batch_fns[bi](m);",
        "            LOGV(\"NB_MODULE_BATCH_DONE %zu\", bi);",
        "        }",
        "",
        "        // ── Utility functions ─────────────────────────────────",
        "        m.def(\"wrap_surface\","
        " [](StratumObject* o) -> StratumSurface* {",
        "            LOGV(\"wrap_surface called o=%p\", o ? o->obj_ : nullptr);",
        "            if (!o) return nullptr;",
        "            JNIEnv* env = get_env();",
        "            if (!env) throw std::runtime_error(\"wrap_surface: no env\");",
        "            return new StratumSurface(o->obj_, env);",
        "        }, nb::arg(\"o\").none(true), nb::rv_policy::take_ownership);",
        "",
        "        // [Action1/Action43] Renamed cast_to → stratum_cast_to",
        "        m.def(\"stratum_cast_to\", [](StratumObject* o,",
        "                              const std::string& cls_name)"
        " -> StratumObject* {",
        "            LOGV(\"stratum_cast_to %s o=%p\", cls_name.c_str(), o ? o->obj_ : nullptr);",
        "            if (!o || !o->obj_) {",
        "                LOGE(\"stratum_cast_to: null object\");",
        "                throw std::runtime_error(\"stratum_cast_to: null\");",
        "            }",
        "            // [Action1] IsInstanceOf check",
        "            JNIEnv* env = get_env();",
        "            if (env) {",
        "                std::string jni_name = cls_name;",
        "                for (char& c : jni_name) if (c == '.') c = '/';",
        "                jclass tcls = find_class(env, jni_name.c_str());",
        "                if (tcls) {",
        "                    jboolean ok = env->IsInstanceOf(o->obj_, tcls);",
        "                    env->DeleteLocalRef(tcls);",
        "                    if (!ok) {",
        "                        LOGW(\"stratum_cast_to: %s is NOT instanceof %s\",",
        "                             o->class_name().c_str(), cls_name.c_str());",
        "                        throw std::runtime_error(",
        "                            \"stratum_cast_to: object is not an instance of \" + cls_name);",
        "                    }",
        "                } else { env->ExceptionClear(); }",
        "            }",
        "            return o;",
        "        }, nb::arg(\"o\").none(true), nb::arg(\"cls_name\"),"
        " nb::rv_policy::reference);",
        "",
        "        m.def(\"remove_callback\","
        " [](const std::string& key) {",
        "            remove_callback(key);",
        "        });",
        "",
        "        // --- allocate direct buffer HELPER ---",
        "        m.def(\"allocate_direct_buffer\", [](int capacity) -> StratumObject* {",
        "            JNIEnv* env = get_env();",
        "            jclass bb_cls = env->FindClass(\"java/nio/ByteBuffer\");",
        "            if (!bb_cls) { env->ExceptionClear(); return nullptr; }",
        "            jmethodID mid = env->GetStaticMethodID(bb_cls, \"allocateDirect\", \"(I)Ljava/nio/ByteBuffer;\");",
        "            jobject bb = env->CallStaticObjectMethod(bb_cls, mid, (jint)capacity);",
        "            env->DeleteLocalRef(bb_cls);",
        "            if (!bb) return nullptr;",
        "            jobject gref = env->NewGlobalRef(bb);",
        "            env->DeleteLocalRef(bb);",
        "            return new StratumObject(gref);",
        "        }, nb::arg(\"capacity\"), nb::rv_policy::take_ownership);",
        "        // -----------------------------",
        "",
        "        // [Action32] Callback count exposed to Python",
        "        m.def(\"stratum_callback_count\", []() -> size_t {",
        "            return stratum_callback_count();",
        "        });",
        "",
        "        // [Action20/Action44] stratum_get_activity (was getActivity)",
        "        m.def(\"stratum_get_activity\","
        " []() -> Stratum_android_app_Activity* {",
        "            // [Action25] Activity mutex for rotation safety",
        "            std::lock_guard<std::mutex> lk(g_activity_mutex);",
        "            LOGV(\"stratum_get_activity called g_activity=%p\", g_activity);",
        "            if (!g_activity) {",
        "                LOGE(\"stratum_get_activity: g_activity is null\");",
        "                throw std::runtime_error(\"Activity is null\");",
        "            }",
        "            return new Stratum_android_app_Activity(g_activity);",
        "        }, nb::rv_policy::take_ownership);",
        "",
        "        // Backwards compat alias",
        "        m.def(\"getActivity\","
        " []() -> Stratum_android_app_Activity* {",
        "            std::lock_guard<std::mutex> lk(g_activity_mutex);",
        "            if (!g_activity) throw std::runtime_error(\"Activity is null\");",
        "            return new Stratum_android_app_Activity(g_activity);",
        "        }, nb::rv_policy::take_ownership);",
        "",
    ]

    # Factory functions
    for cls in factory_classes:
        fqn   = cls["fqn"]
        sn    = struct_name(fqn)
        pfx   = cpp_class_prefix(fqn)
        py_fn = "create_" + pfx
        lines += [
            f"        m.def(\"{py_fn}\","
            f" [](Stratum_android_app_Activity* act) -> {sn}* {{",
            f"            LOGV(\"create {fqn} act=%p\", act ? act->obj_ : nullptr);",
            f"            LOGD(\"create {fqn}\");",
            f"            if (!act) {{",
            f"                LOGE(\"{py_fn}: null activity\");",
            f"                throw std::runtime_error(\"{py_fn}: null\");",
            f"            }}",
            f"            JNIEnv* env = get_env();",
            f"            if (!env) {{",
            f"                LOGE(\"{py_fn}: no JNIEnv\");",
            f"                throw std::runtime_error(\"{py_fn}: no JNIEnv\");",
            f"            }}",
            f"            ensure_{pfx}_init(env);",
            f"            jclass c = get_{pfx}_class();",
            f"            if (!c) {{",
            f"                LOGE(\"Init failed: {fqn}\");",
            f"                throw std::runtime_error(\"Init failed: {fqn}\");",
            f"            }}",
            f"            jmethodID mid = env->GetMethodID(",
            f"                c, \"<init>\","
            f" \"(Landroid/content/Context;)V\");",
            f"            if (!mid) {{",
            f"                env->ExceptionClear();",
            f"                LOGE(\"No Context ctor: {fqn}\");",
            f"                throw std::runtime_error(",
            f"                    \"No Context ctor: {fqn}\");",
            f"            }}",
            f"            LOGV(\"FACTORY_NEWOBJ {fqn} act=%p\", act->obj_);",
            f"            nb::gil_scoped_release _rel;",
            f"            jobject obj = env->NewObject(c, mid, act->obj_);",
            f"            if (!obj) {{",
            f"                env->ExceptionClear();",
            f"                LOGE(\"NewObject failed: {fqn}\");",
            f"                throw std::runtime_error(",
            f"                    \"NewObject failed: {fqn}\");",
            f"            }}",
            f"            auto* w = new {sn}(obj);",
            f"            env->DeleteLocalRef(obj);",
            f"            LOGD(\"created {fqn} = %p\", w);",
            f"            LOGV(\"FACTORY_OK {fqn} ptr=%p\", w);",
            f"            return w;",
            f"        }}, nb::arg(\"act\").none(true),"
            f" nb::rv_policy::take_ownership);",
            f"",
        ]

    if any(c["fqn"] == "android.view.View" for c in filtered_ordered):
        lines += [
            "        m.def(\"setContentView\",",
            "            [](Stratum_android_app_Activity* act,",
            "               Stratum_android_view_View* view) {",
            "                LOGV(\"setContentView act=%p view=%p\",",
            "                     act ? act->obj_ : nullptr,",
            "                     view ? view->obj_ : nullptr);",
            "                LOGD(\"setContentView called\");",
            "                if (!act || !view) {",
            "                    LOGE(\"setContentView: null arg\");",
            "                    throw std::runtime_error(\"null arg\");",
            "                }",
            "                JNIEnv* env = get_env();",
            "                if (!env) {",
            "                    LOGE(\"setContentView: no JNIEnv\");",
            "                    throw std::runtime_error(",
            "                        \"setContentView: no JNIEnv\");",
            "                }",
            "                jclass c = env->GetObjectClass(act->obj_);",
            "                jmethodID mid = nullptr;",
            "                while (c && !mid) {",
            "                    mid = env->GetMethodID(",
            "                        c, \"setContentView\","
            " \"(Landroid/view/View;)V\");",
            "                    if (!mid) {",
            "                        env->ExceptionClear();",
            "                        jclass p = env->GetSuperclass(c);",
            "                        env->DeleteLocalRef(c); c = p;",
            "                    }",
            "                }",
            "                if (!mid) {",
            "                    if (c) env->DeleteLocalRef(c);",
            "                    LOGE(\"setContentView method not found\");",
            "                    throw std::runtime_error(",
            "                        \"setContentView not found\");",
            "                }",
            "                LOGV(\"setContentView JNI_CALL act=%p view=%p\","
            "                     act->obj_, view->obj_);",
            "                nb::gil_scoped_release _rel;",
            "                env->CallVoidMethod(act->obj_, mid, view->obj_);",
            "                if (c) env->DeleteLocalRef(c);",
            "                if (env->ExceptionCheck()) {",
            "                    env->ExceptionClear();",
            "                    LOGE(\"setContentView threw exception\");",
            "                }",
            "                LOGD(\"setContentView OK\");",
            "            },"
            " nb::arg(\"act\").none(true), nb::arg(\"view\").none(true));",
            "",
        ]

    lines += [
        "        m.def(\"set_lifecycle_callback\",",
        "            [](const std::string& name, nb::callable fn) {",
        "                LOGD(\"set_lifecycle_callback: %s\", name.c_str());",
        "                LOGV(\"LIFECYCLE_CB_SET: %s\", name.c_str());",
        "                std::lock_guard<std::mutex> lk(g_lifecycle_mutex);",
        "                g_lifecycle_cbs[name] = fn;",
        "            });",
        "",
        f"        LOGI(\"NB_MODULE: load complete — v{STAGE_VERSION}\");",
        f"        LOGV(\"NB_MODULE_COMPLETE\");",
        "    }",
        "    catch (nb::python_error& e) {",
        "        LOGE(\"NB_MODULE FATAL PY: %s\", e.what());",
        "        throw;",
        "    }",
        "    catch (const std::exception& e) {",
        "        LOGE(\"NB_MODULE FATAL: %s\", e.what());",
        "        throw;",
        "    }",
        "    catch (...) {",
        "        LOGE(\"NB_MODULE FATAL: unknown\");",
        "        throw std::runtime_error(\"Unknown C++ exception\");",
        "    }",
        "}",
        "",
    ]

    # ── Lifecycle JNI entry points ─────────────────────────────────────────────
    for sym, key in [
        ("nativeOnCreate",  "onCreate"),
        ("nativeOnResume",  "onResume"),
        ("nativeOnPause",   "onPause"),
        ("nativeOnStop",    "onStop"),
        ("nativeOnDestroy", "onDestroy"),
    ]:
        lines.append(
            f"extern \"C\" JNIEXPORT void JNICALL "
            f"Java_com_stratum_runtime_StratumActivity_{sym}"
            f"(JNIEnv*, jobject)"
            f" {{ LOGV(\"LIFECYCLE_JNI {key}\"); dispatch_lifecycle(\"{key}\"); }}"
        )

    lines += [
        "",
        "extern \"C\" JNIEXPORT void JNICALL",
        "Java_com_stratum_runtime_StratumActivity_nativeSetActivity(",
        "        JNIEnv* env, jobject, jobject activity) {",
        "    LOGI(\"nativeSetActivity: %p\", activity);",
        "    LOGV(\"NATIVE_SET_ACTIVITY activity=%p\", activity);",
        "    // [Action25] g_activity mutex",
        "    std::lock_guard<std::mutex> lk(g_activity_mutex);",
        "    if (g_activity) env->DeleteGlobalRef(g_activity);",
        "    g_activity = activity ? env->NewGlobalRef(activity) : nullptr;",
        "    LOGI(\"g_activity set to %p\", g_activity);",
        "    LOGV(\"NATIVE_SET_ACTIVITY_DONE g_activity=%p\", g_activity);",
        "}",
    ]

    return "\n".join(lines)


# =============================================================================
# Topological sort — [Action24] Cycle detection
# =============================================================================

def topological_sort(classes: list) -> list:
    """[Action24] Cycle detection via in_progress set."""
    fqn_to_cls = {cls["fqn"]: cls for cls in classes}
    ordered: List[dict] = []
    visited: Set[str]   = set()
    in_progress: Set[str] = set()

    def visit(fqn: str) -> None:
        if fqn in visited:
            return
        # [Action24] Cycle detection
        if fqn in in_progress:
            print(f"  WARN [Action24]: circular parent_fqn detected involving '{fqn}' — cycle broken")
            return
        in_progress.add(fqn)
        cls = fqn_to_cls.get(fqn)
        if not cls:
            in_progress.discard(fqn)
            return
        p = cls.get("parent_fqn", "")
        if not p and cls.get("parent_details"):
            p = cls["parent_details"].get("fqn", "")
        if p and p in fqn_to_cls:
            visit(p)
        in_progress.discard(fqn)
        visited.add(fqn)
        ordered.append(cls)

    for cls in classes:
        visit(cls["fqn"])

    return ordered


# =============================================================================
# Diagnostics Markdown Generator — [Action28] Use FQN in headers
# =============================================================================

def generate_markdown_report(classes: list, output_dir: Path) -> None:
    lines = [
        f"# Stratum API Surface Report (v{STAGE_VERSION})",
        f"Generated on: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        f"Total Emitted Classes: **{len(classes)}**",
        "",
        "## Action Summary (v5.0)",
        "- Action1:  stratum_cast_to — real IsInstanceOf check",
        "- Action2:  nativeDispatch unboxes Java primitive wrappers",
        "- Action3:  Destructor uses get_env() for background thread safety",
        "- Action4:  Callbacks auto-removed on object destroy",
        "- Action5:  __eq__/__ne__ via IsSameObject",
        "- Action6:  nativeDispatch returns jobject back to Java",
        "- Action7:  Proxy dict key mismatch LOGW",
        "- Action8:  CharSequence → string_in",
        "- Action13: Enum support",
        "- Action19: __bool__ on StratumObject",
        "- Action20/47: STAGE_VERSION constant",
        "- Action24: topological_sort cycle detection",
        "- Action25: g_activity_mutex for rotation safety",
        "- Action26: Skip subclasses of failed-registration parents",
        "- Action29: sanitize_id no gen_ prefix (matches Stage 08)",
        "- Action32: g_callbacks size monitoring",
        "- Action33: Root ctor null-env assert",
        "- Action34: mutex before GIL (deadlock prevention)",
        "- Action35: _stratum_cast owns independent global ref",
        "- Action36: PyGILState_Check before gil_scoped_acquire",
        "- Action43: cast_to renamed stratum_cast_to",
        "- Action44: getActivity renamed stratum_get_activity",
        "- A37: jchar UTF-16 correct",
        "- A38: varargs Object... support",
        "- A39: jthrowable → StratumThrowable",
        "- A40: WeakReference → NewWeakGlobalRef/StratumWeakObject",
        "- A41: Direct ByteBuffer zero-copy memoryview",
        "- A42: @NonNull → none(false), @Nullable → none(true)",
        "",
        "## Logging Levels",
        "```cmake",
        "# Production (default) — errors only:",
        "# (no defines needed)",
        "",
        "# Debug — method entry/exit, class init, IDs:",
        "target_compile_definitions(stratum PRIVATE STRATUM_VERBOSE_LOG=1)",
        "",
        "# Ultra — EVERY JNI call direction, arg, return:",
        "target_compile_definitions(stratum PRIVATE STRATUM_ULTRA_LOG=1)",
        "```",
        "",
        "## logcat filter",
        "```bash",
        "adb logcat -s 'Stratum:V' 'Stratum/TRACE:V' 'Stratum/ARG:V' 'Stratum/RET:V'",
        "```",
        "",
        "---",
        "",
    ]

    for cls in classes:
        # [Action28] Use FQN in header, not mangled name
        fqn     = cls.get("fqn", "Unknown")
        py_name = cpp_class_prefix(fqn)
        methods = get_methods_for_class(cls)
        lines.append(f"## {fqn}")
        lines.append(f"**Python name**: `{py_name}`")

        if cls.get("fields"):
            lines.append("#### Fields:")
            for f in cls["fields"]:
                lines.append(
                    f"- `f_get_{sanitize_id(f.get('name',''))}()`")

        bindable = methods["declared"] + methods["overridden"]
        if bindable:
            lines.append("#### Methods:")
            for m in bindable:
                params    = m.get("params", [])
                has_ptr   = any(cpp_type_for_param(p).endswith("*") for p in params)
                nullable  = any(p.get("nullable", True) for p in params if cpp_type_for_param(p).endswith("*"))
                none_note = " *(nullable)*" if has_ptr and nullable else ""
                nonnull   = " *(non-null)*" if has_ptr and not nullable else ""
                lines.append(
                    f"- `{sanitize_id(m.get('name', ''))}`"
                    f" → {m.get('return_cpp', 'void')}{none_note}{nonnull}")

        lines.append("\n---")

    md_path = output_dir / "api_surface_reference.md"
    md_path.write_text("\n".join(lines), encoding="utf-8")


# =============================================================================
# main
# =============================================================================

def main() -> None:
    global NANOBIND_BATCH_SIZE

    parser = argparse.ArgumentParser(
        description=f"Stratum Stage 06 v{STAGE_VERSION} — Complete JNI Bridge Emit",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--input",      required=True,
        help="05_resolve/output/ directory (or 05_5_abstract/output/patched/)")
    parser.add_argument("--output",     required=True,
        help="06_cpp_emit/output/ directory")
    parser.add_argument("--batch-size", type=int,
        default=NANOBIND_BATCH_SIZE,
        help=f"Classes per NB_MODULE batch (default: {NANOBIND_BATCH_SIZE})")
    args = parser.parse_args()
    NANOBIND_BATCH_SIZE = args.batch_size

    print_header(f"STRATUM PIPELINE — STAGE 06 v{STAGE_VERSION} (ALL 47 ACTIONS)")
    print(f"  Version        : {STAGE_VERSION}")
    print(f"  Calls          : ALL VIRTUAL [FIX-16]")
    print(f"  Nullable args  : @NonNull → none(false), @Nullable → none(true) [A42]")
    print(f"  jchar          : UTF-16 correct [A37]")
    print(f"  varargs        : Object... supported [A38]")
    print(f"  jthrowable     : StratumThrowable [A39]")
    print(f"  WeakReference  : StratumWeakObject [A40]")
    print(f"  ByteBuffer     : direct memoryview [A41]")
    print(f"  cast_to        : renamed stratum_cast_to [Action43]")
    print(f"  getActivity    : renamed stratum_get_activity [Action44]")
    print(f"  Deadlock fix   : mutex before GIL [Action34]")
    print(f"  Cycle detect   : topological_sort [Action24]")
    print(f"  Batch size     : {NANOBIND_BATCH_SIZE}")

    input_dir  = Path(args.input)
    output_dir = Path(args.output)

    if not input_dir.exists():
        print(f"ERROR: Input not found: {input_dir}")
        sys.exit(1)

    json_files = sorted(
        f for f in input_dir.rglob("*.json")
        if f.name not in (
            "parse_summary.json",
            "resolve_summary.json",
            "cpp_summary.json",
            "manifest.json",
        )
    )
    if not json_files:
        print("ERROR: No JSON files found. Did Stage 05 (or 05.5) succeed?")
        sys.exit(1)

    print(f"\n  Found {len(json_files)} JSON files.")

    raw_classes: List[Tuple[Path, dict]] = []
    for jf in json_files:
        try:
            raw_classes.append(
                (jf, json.loads(jf.read_text(encoding="utf-8"))))
        except Exception as e:
            print(f"  WARN: {jf.name}: {e}")

    all_classes_unfiltered = [
        cls for _, cls in raw_classes
        if cls.get("codegen_hints", {}).get("emit_wrapper", True)
    ]

    # [FIX-6] Prefix collision: numeric suffix
    all_classes: List[dict] = []
    seen_prefixes: Dict[str, str] = {}
    for cls in all_classes_unfiltered:
        original_prefix = cpp_class_prefix(cls["fqn"])
        prefix = original_prefix
        if prefix in seen_prefixes:
            counter = 2
            while f"{original_prefix}_{counter}" in seen_prefixes:
                counter += 1
            prefix = f"{original_prefix}_{counter}"
            print(
                f"  WARN [FIX-6]: prefix collision '{cls['fqn']}'"
                f" → '{original_prefix}' already used by"
                f" '{seen_prefixes[original_prefix]}'."
                f" Renamed to '{prefix}'."
            )
            cls["_cpp_prefix_override"] = prefix
        seen_prefixes[prefix] = cls["fqn"]
        all_classes.append(cls)

    # [Action30] Assert GENERATED_FQNS population
    GENERATED_FQNS.clear()
    for cls in all_classes:
        GENERATED_FQNS.add(cls["fqn"])
    assert len(GENERATED_FQNS) == len(all_classes), \
        f"[Action30] GENERATED_FQNS size mismatch: {len(GENERATED_FQNS)} vs {len(all_classes)}"
    print(f"  [Action30] GENERATED_FQNS populated: {len(GENERATED_FQNS)} FQNs")

    print(f"  Sorting {len(all_classes)} classes …")
    ordered_classes = topological_sort(all_classes)
    print(f"  Sorted: {len(ordered_classes)}.")

    core_dir = output_dir / "core"
    gen_dir  = output_dir / "generated"
    core_dir.mkdir(parents=True, exist_ok=True)
    gen_dir.mkdir(parents=True, exist_ok=True)

    (core_dir / "bridge_core.h").write_text(
        emit_bridge_core_h(), encoding="utf-8")
    (core_dir / "bridge_core.cpp").write_text(
        emit_bridge_core_cpp(), encoding="utf-8")
    (core_dir / "stratum_structs.h").write_text(
        emit_stratum_structs_h(ordered_classes, GENERATED_FQNS),
        encoding="utf-8")
    print("  Emitted bridge_core.h / .cpp / stratum_structs.h")

    emitted: List[dict] = []
    failed:  List[dict] = []
    failed_fqns: Set[str] = set()

    for i, cls in enumerate(ordered_classes, 1):
        try:
            cpp_text = emit_class_cpp(cls)
            out_name = cpp_class_prefix(cls["fqn"])
            (gen_dir / f"{out_name}.cpp").write_text(
                cpp_text, encoding="utf-8")
            emitted.append(cls)
            if i % 100 == 0 or i == len(ordered_classes):
                print(
                    f"  [{i:5d}/{len(ordered_classes)}] {cls['fqn']}")
        except Exception as e:
            failed.append({"fqn": cls.get("fqn", "?"), "error": str(e)})
            failed_fqns.add(cls.get("fqn", ""))
            print(f"  FAIL [{i}] {cls.get('fqn','?')} → {e}")

    (core_dir / "bridge_main.cpp").write_text(
        emit_bridge_main(emitted, failed_fqns), encoding="utf-8")
    n_batches = (
        (len(emitted) + NANOBIND_BATCH_SIZE - 1) // NANOBIND_BATCH_SIZE
    )
    print(
        f"  Emitted bridge_main.cpp"
        f" ({len(emitted)} classes, {n_batches} batches)")

    generate_markdown_report(emitted, output_dir)
    print("  Emitted Markdown API Reference Guide.")

    (output_dir / "cpp_summary.json").write_text(
        json.dumps({
            "version":          STAGE_VERSION,
            "total_emitted":    len(emitted),
            "total_failed":     len(failed),
            "batches":          n_batches,
            "batch_size":       NANOBIND_BATCH_SIZE,
            "dispatch":         "compile_time_virtual_only",
            "generated_fqns":   sorted(GENERATED_FQNS),  # [Action31] for Stage 08
            "failed_fqns":      sorted(failed_fqns),
            "actions_applied": [
                "A1:jchar_utf16_array",
                "A2:primitive_unbox_dispatch",
                "A3:destructor_get_env",
                "A4:callback_auto_remove",
                "A5:eq_ne_IsSameObject",
                "A6:nativeDispatch_return_jobject",
                "A7:proxy_dict_key_logw",
                "A8:CharSequence_string_in",
                "A13:enum_support",
                "A19:__bool__",
                "A20_47:STAGE_VERSION",
                "A24:cycle_detection",
                "A25:g_activity_mutex",
                "A26:skip_failed_subclasses",
                "A29:sanitize_id_no_gen_prefix",
                "A30:GENERATED_FQNS_assertion",
                "A32:callback_count_monitoring",
                "A33:root_ctor_null_env_assert",
                "A34:mutex_before_GIL",
                "A35:stratum_cast_independent_gref",
                "A36:PyGILState_Check",
                "A37:jchar_UTF16",
                "A38:varargs",
                "A39:jthrowable_StratumThrowable",
                "A40:WeakReference_StratumWeakObject",
                "A41:ByteBuffer_direct_memoryview",
                "A42:NonNull_Nullable_none",
                "A43:cast_to_renamed_stratum_cast_to",
                "A44:getActivity_renamed_stratum_get_activity",
            ],
            "failed": failed,
        }, indent=2),
        encoding="utf-8",
    )

    print_header(f"STAGE 06 v{STAGE_VERSION} COMPLETE")
    print(f"  Emitted  : {len(emitted):,} / {len(all_classes):,}")
    print(f"  Batches  : {n_batches} × {NANOBIND_BATCH_SIZE}")
    print(f"  Failed   : {len(failed):,}")
    print(f"  Output   : {output_dir}")
    print()
    print("  Logging options (add to CMakeLists.txt):")
    print("    Debug:  target_compile_definitions(stratum PRIVATE STRATUM_VERBOSE_LOG=1)")
    print("    Ultra:  target_compile_definitions(stratum PRIVATE STRATUM_ULTRA_LOG=1)")
    print()
    print("  logcat filter for full trace:")
    print("    adb logcat -s 'Stratum:V' 'Stratum/TRACE:V' 'Stratum/ARG:V' 'Stratum/RET:V'")

    if failed:
        print()
        for f in failed:
            print(f"  FAIL: {f['fqn']} → {f['error']}")
        sys.exit(1)
    else:
        print()
        print("  All C++ emitted. Ready for ndk-build/CMake.")


if __name__ == "__main__":
    main()