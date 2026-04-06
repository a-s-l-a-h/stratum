#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Stratum Pipeline — Stage 06 : C++ Emit (Nanobind)
=================================================
VERSION: 4_7 — NONE-POINTER + ULTRA-LOG PASS

ALL FIXES APPLIED (cumulative from v4.7):
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

  [FIX-23] *** CRITICAL *** nanobind pointer params: ALL pointer-type
           parameters now emit nb::arg("name").none(true) so that Python
           None is accepted and translates to nullptr in C++. Without this
           nanobind rejects None/null for any typed pointer param causing
           crashes when e.g. inflater.inflate(layout_id, None, False).

  [FIX-24] *** CRITICAL *** inflate/addView/removeView and all methods
           that accept a ViewGroup* parent now correctly accept None via
           nb::arg().none(true). The stub signature was blocking None.

  [FIX-25] Ultra-deep JNI tracing: every JNI call direction (Python→C++→JNI
           and JNI→C++→Python) is traced at LOGV (ultra-verbose) level.
           Controlled by STRATUM_ULTRA_LOG compile-time define.
           STRATUM_VERBOSE_LOG=1 → LOGD level (existing)
           STRATUM_ULTRA_LOG=1   → LOGV level (new, full call tracing)

  [FIX-26] setOnClickListener lambda support: single-method proxy interfaces
           now correctly unwrap Python lambdas via callable_to_proxy path.

  [FIX-27] getResources/getLayoutInflater return typed pointers correctly.
           StratumObject* wrapping promoted to typed wrapper when FQN known.

  [FIX-28] removeView/addView ViewGroup methods emit with none(true) arg.

  [FIX-29] inflate(int, ViewGroup, bool) — parent ViewGroup param tagged
           none(true) in the nanobind def so None is accepted.

COMPILE-TIME LOG LEVELS:
  STRATUM_ULTRA_LOG=1    → LOGV: every single JNI call, arg, return, pointer
  STRATUM_VERBOSE_LOG=1  → LOGD: method entry/exit, class init, IDs
  (default)              → LOGI/LOGW/LOGE only (production)

Set in CMakeLists.txt:
  target_compile_definitions(stratum PRIVATE STRATUM_VERBOSE_LOG=1)
  target_compile_definitions(stratum PRIVATE STRATUM_ULTRA_LOG=1)
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Dict, List, Set, Tuple, Any, Optional


# =============================================================================
# Global Configuration & State
# =============================================================================

GENERATED_FQNS: Set[str] = set()
NANOBIND_BATCH_SIZE: int = 100

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
    "[C": ("jcharArray",    "jchar",    "Char",    "uint16_t", "int"),
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

# =============================================================================
# [FIX-25] Ultra-verbose + verbose log macros
# STRATUM_ULTRA_LOG=1  → LOGV for every single JNI call direction
# STRATUM_VERBOSE_LOG=1 → LOGD for method/class init
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
// Enable with STRATUM_ULTRA_LOG=1 — very noisy, use only for debugging crashes
#if STRATUM_ULTRA_LOG
#define LOGV(...) __android_log_print(ANDROID_LOG_VERBOSE, "Stratum", __VA_ARGS__)
#else
#define LOGV(...) ((void)0)
#endif

// LOGT: trace direction markers Python->C++->JNI and JNI->C++->Python
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
    [FIX-1] Single-char names no longer prefixed with gen_.
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
    """[FIX-5] Normalise to JNI slash format."""
    return class_name.replace(".", "/")


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


def cpp_type_for_param(p: dict) -> str:
    conv      = p.get("conversion", "")
    java_type = p.get("java_type", "")
    jni_type  = p.get("jni_type", "")
    cpp_type  = p.get("cpp_type", "")

    if conv == "callable_to_proxy":                return "nb::object"
    if conv == "abstract_adapter":                 return "nb::object"
    if conv == "string_in":                        return "const std::string&"
    if conv in ("bool_in", "bool_out"):            return "bool"
    if java_type and java_type in GENERATED_FQNS:  return f"{struct_name(java_type)}*"
    if java_type == "[B":                          return "nb::bytes"

    if java_type in ARRAY_TYPE_MAP:
        _, _, _, cpp_t, _ = ARRAY_TYPE_MAP[java_type]
        return f"std::vector<{cpp_t}>"

    if java_type and (java_type.startswith("[L") or java_type.startswith("[[")):
        return "nb::list"

    if jni_type == "jobject" or cpp_type == "jobject": return "StratumObject*"
    if jni_type == "jstring":                          return "const std::string&"

    if conv in ("direct", "long_safe"):
        return cpp_type if cpp_type else "jlong"

    return cpp_type if cpp_type else "StratumObject*"


def param_is_nullable_pointer(p: dict) -> bool:
    """
    [FIX-23] Returns True if this parameter is a pointer type that should
    accept None from Python (translates to nullptr in C++).
    All pointer-type params (StratumObject*, typed struct pointers) must
    accept None/nullptr so Python None works correctly.
    """
    t = cpp_type_for_param(p)
    return t.endswith("*")


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

    if ret_sig in ARRAY_TYPE_MAP:
        _, _, _, cpp_t, _ = ARRAY_TYPE_MAP[ret_sig]
        return f"std::vector<{cpp_t}>"

    if is_string_array_sig(ret_sig): return "nb::list"
    if ret_sig.startswith("["):      return "nb::list"

    sig_java_type = extract_return_java_type(sig)
    if sig_java_type and sig_java_type in GENERATED_FQNS:
        return f"{struct_name(sig_java_type)}*"

    ret_jni = get_return_jni(m)
    if ret_jni == "jobject" or ret_cpp == "jobject":
        return "StratumObject*"

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
    if ret_decl == "bool":                 return "return false;"
    if ret_decl == "nb::bytes":            return 'return nb::bytes("", 0);'
    if ret_decl.startswith("std::vector"): return "return {};"
    if ret_decl == "nb::list":             return "return nb::list();"
    if ret_decl.endswith("*"):             return "return nullptr;"
    return "return 0;"


# =============================================================================
# Exception Propagation Generator
# =============================================================================

def emit_exception_check(lines: list, indent: str = "    ",
                          ret_decl: str = "void") -> None:
    """Full Java-exception-to-C++-exception translator with LOGV tracing."""
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
    """[FIX-25] Emit param conversion with LOGV ultra-tracing."""
    conv      = p.get("conversion", "")
    name      = sanitize_id(p["name"])
    jtype     = p.get("jni_type", "jobject")
    java_type = p.get("java_type", "")

    if conv == "bool_in":
        lines.append(
            f"{indent}jboolean jni_{name} = {name} ? JNI_TRUE : JNI_FALSE;")
        lines.append(
            f"{indent}LOGD(\"param {name} (bool) = %d\", (int)jni_{name});")
        lines.append(
            f"{indent}LOGV_BOOL(\"{name}\", {name});")

    elif conv == "string_in" or jtype == "jstring":
        lines.append(
            f"{indent}jstring jni_{name} = env->NewStringUTF({name}.c_str());")
        lines.append(
            f"{indent}LOGD(\"param {name} (string) = %s\", {name}.c_str());")
        lines.append(
            f"{indent}LOGV_STR(\"{name}\", {name});")

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
    java_type = p.get("java_type", "")  # ADD THIS LINE
    conv      = p.get("conversion", "")

    if conv == "abstract_adapter":
        lines.append(f"{indent}if (jni_{name}) env->DeleteGlobalRef(jni_{name});")
    elif conv == "callable_to_proxy":
        lines.append(f"{indent}if (jni_{name}) env->DeleteGlobalRef(jni_{name});")
    elif conv == "string_in" or jtype == "jstring":
        lines.append(f"{indent}env->DeleteLocalRef(jni_{name});")
    elif (java_type in ARRAY_TYPE_MAP
          or (java_type and (java_type.startswith("[L") or java_type.startswith("[[")))):
        lines.append(f"{indent}env->DeleteLocalRef(jni_{name});")


def jni_args(params: list) -> str:
    """[FIX-14] Index-stable ordering."""
    return ", ".join(f"jni_{sanitize_id(p['name'])}" for p in params)


def emit_return_conversion(ret_decl: str, ret_conv: str, lines: list,
                            m: dict, indent: str = "    ",
                            method_name: str = "") -> None:
    """[FIX-25] Return conversion with LOGV ultra-tracing."""
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

    elif ret_decl == "std::string" or is_string_return(m):
        lines += [
            f"{indent}if (!raw) {{ LOGD(\"return string = null\"); "
            f"LOGT_JNI_TO_PY(\"\", \"{mname}\", \"string(null)\"); return \"\"; }}",
            f"{indent}const char* _ch = "
            f"env->GetStringUTFChars((jstring)raw, nullptr);",
            f"{indent}std::string _res(_ch);",
            f"{indent}env->ReleaseStringUTFChars((jstring)raw, _ch);",
            f"{indent}env->DeleteLocalRef((jobject)raw);",
            f"{indent}LOGD(\"return string = %s\", _res.c_str());",
            f"{indent}LOGV_RET_STR(\"{mname}\", _res);",
            f"{indent}LOGT_JNI_TO_PY(\"\", \"{mname}\", \"string\");",
            f"{indent}return _res;",
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

    elif ret_sig in ARRAY_TYPE_MAP and ret_sig != "[B":
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

    elif ret_decl.endswith("*") and ret_decl != "StratumObject*":
        inner = ret_decl[:-1]
        lines += [
            f"{indent}if (!raw) {{ LOGV_RET_PTR(\"{mname}\", nullptr); "
            f"LOGT_JNI_TO_PY(\"\", \"{mname}\", \"typed*(null)\"); return nullptr; }}",
            f"{indent}auto* _w = new {inner}(raw);",
            f"{indent}env->DeleteLocalRef((jobject)raw);",
            f"{indent}LOGD(\"return typed obj = %p\", raw);",
            f"{indent}LOGV_RET_PTR(\"{mname}\", raw);",
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
    [FIX-3] Returns NewGlobalRef. [FIX-25] LOGV tracing.
    [STAGE-05.5] Also handles abstract_adapter params (Java class that
    extends an abstract Android class, created via Adapter_*.java).
    """
    for p in m.get("params", []):
        conv  = p.get("conversion", "")
        pname = sanitize_id(p["name"])
        fn    = f"create_proxy_m{mi}_{pname}"

        # ── ABSTRACT ADAPTER (Stage 05.5) ────────────────────────────────────
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
                f"    // Store Python callable(s) under the key",
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
                f"        LOGE(\"Did you copy Adapter_*.java to "
                f"runtime/java/com/stratum/adapters/?\");",
                f"        throw std::runtime_error(",
                f"            \"Adapter not found: {adj}\");",
                f"    }}",
                f"    jmethodID _ctor = env->GetMethodID(",
                f"        _cls, \"<init>\", \"(Ljava/lang/String;)V\");",
                f"    if (!_ctor) {{",
                f"        env->ExceptionClear();",
                f"        env->DeleteLocalRef(_cls);",
                f"        LOGE(\"Adapter (String) ctor not found: {adj}\");",
                f"        throw std::runtime_error(",
                f"            \"Adapter ctor not found: {adj}\");",
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

        # ── INTERFACE PROXY (existing — unchanged) ────────────────────────────
        if conv != "callable_to_proxy":
            continue

        iface         = m.get("proxy_interface", "")
        if not iface:
            jtype = p.get("java_type", "").replace(".", "/")
            iface = jtype if jtype else "java/lang/Runnable"
        iface         = to_jni_slash(iface)
        base_key      = f"{fqn}#{m['name']}#{pname}"
        iface_methods = m.get("proxy_methods", [])

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
# [FIX-17] native_entries builder — ONLY truly native (JNI) methods
# =============================================================================

def build_native_entries(groups: dict, ctor_len: int,
                          decl_len: int) -> List[Tuple[int, dict, bool]]:
    """[FIX-17] Only is_native=True methods enter RegisterNatives."""
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
    """[FIX-18] Always virtual. [FIX-25] LOGV tracing."""
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
# [FIX-16] Instance method emitter — ALWAYS virtual
# =============================================================================

def _emit_instance_method(
    m: dict,
    global_idx: int,
    fqn: str,
    sname: str,
    seen_inst: set,
    lines: list,
) -> None:
    """[FIX-16] Virtual dispatch only. [FIX-23] none(true) for pointer params.
    [FIX-25] Full LOGV tracing."""
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
    # GIL is held here during param conversion!

    # Log each argument
    for p in params:
        pname = sanitize_id(p["name"])
        ptype = cpp_type_for_param(p)
        if ptype.endswith("*"):
            lines.append(f"    LOGV_PTR(\"{pname}\", {pname} ? {pname}->obj_ : nullptr);")
        elif "string" in ptype:
            lines.append(f"    LOGV_STR(\"{pname}\", {pname});")
        elif ptype == "bool":
            lines.append(f"    LOGV_BOOL(\"{pname}\", {pname});")
        elif ptype == "nb::object":
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
    # Release GIL strictly during the Java method execution
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
# [FIX-23] nanobind arg helper — 
# =============================================================================

def nb_arg_for_param(p: dict, param_name: str) -> str:
    """
    [FIX-30/31] Prevent nanobind SIGABRT on overloaded methods.
    nanobind asserts if overloads share an argument name (e.g. 'arg0') but have 
    different .none() annotations. Appending the C++ type guarantees unique 
    names across overloads, allowing us to safely use .none(true) only on pointers.
    """
    ptype = cpp_type_for_param(p)
    is_ptr = ptype.endswith("*") or ptype == "nb::object"
    
    # Strip non-alphanumeric characters to create a clean, safe suffix
    safe_type = re.sub(r'[^a-zA-Z0-9]', '', ptype)
    unique_name = f"{param_name}_{safe_type}"
    
    if is_ptr:
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
        f"// Stratum Stage 06 v4.8 — DO NOT EDIT",
        f"// Class  : {fqn}",
        f"// FIX-16 : all calls virtual (no NonVirtual)",
        f"// FIX-17 : RegisterNatives only for is_native methods",
        f"// FIX-19 : LOGD diagnostics (STRATUM_VERBOSE_LOG=1)",
        f"// FIX-23 : pointer params accept None via nb::arg().none(true)",
        f"// FIX-25 : ultra-deep LOGV tracing (STRATUM_ULTRA_LOG=1)",
        f"// ============================================================",
        f"#include <jni.h>",
        f"#include <stdint.h>",
        f"#include <string>",
        f"#include <vector>",
        f"#include <mutex>",
        f"#include <atomic>",
        f"#include <stdexcept>",
        f'#include "bridge_core.h"',
        f'#include "stratum_structs.h"',
        f"#include <nanobind/nanobind.h>",
        f"#include <nanobind/stl/string.h>",
        f"#include <nanobind/stl/vector.h>",
        f"#include <nanobind/stl/list.h>",
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

    # ── Constructors / Destructor ──────────────────────────────────────────────
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
            f"    obj_ = env ? env->NewGlobalRef(obj) : obj;",
            f"    LOGD(\"{sname} constructed obj_=%p\", obj_);",
            f"    LOGV(\"CTOR {sname} raw=%p global=%p\", obj, obj_);",
            f"}}",
            f"{sname}::~{sname}() {{",
            f"    JNIEnv* env = get_env_safe();",
            f"    if (env && obj_) {{",
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
        # GIL is held here during param conversion!
        #lines.append(f"    nb::gil_scoped_release _release;")
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

    # ── Constructor factories ──────────────────────────────────────────────────
    # ── Constructor factories ──────────────────────────────────────────────────
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
            # GIL is held here during param conversion!
            #lines.append(f"    nb::gil_scoped_release _release;")
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

    # Constructors with none(true) for nullable pointer args [FIX-23]
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

    # Instance methods with none(true) [FIX-23]
    # Instance methods with none(true) [FIX-23]
    seen_nb_inst: Set[str] = set()
    
    # [FIX-32] Pre-collect ALL instance method names (including inherited!)
    # This prevents FATAL SIGABRT when a static method has the same name 
    # as an inherited instance method (e.g., ProgressDialog.show).
    inst_names: Set[str] = set()
    for m in groups["declared"] + groups["overridden"] + groups["inherited"]:
        if not m.get("is_static") and not m.get("is_constructor"):
            inst_names.add(sanitize_id(m["name"]))

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

        # [FIX-23] updatead HOTFIX: ALWAYS emit nb::arg annotations if there are params!
        # Mixing nb::arg() on some overloads but not others causes nanobind to SIGABRT.
        if params:
            nb_args = ", ".join(
                nb_arg_for_param(p, sanitize_id(p["name"]))
                for p in params
            )
            lines.append(f"    cls.def(\"{mname}\", {cast}, {nb_args});")
        else:
            lines.append(f"    cls.def(\"{mname}\", {cast});")

    # Static methods with none(true) [FIX-23]
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
        py_m    = mname + "_static"  # [FIX-33] Bulletproof namespace separation
        ptypes  = ", ".join(t for _, t in cpp_params)
        sig_key = f"{py_m}({ptypes})"
        if sig_key in seen_nb_static:
            continue
        seen_nb_static.add(sig_key)
        global_idx = ctor_len + idx_in_bindable
        fn_name    = f"static_{prefix}_{mname}_{global_idx}"

        # HOTFIX: ALWAYS emit nb::arg annotations for static methods too.
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


    lines += [
        f"    // Stratum Object Pointer Extraction & Safe Casting",
        f"    cls.def(\"_get_jobject_ptr\", []({sname}* self) -> int64_t {{",
        f"        return (int64_t)(uintptr_t)(self ? self->obj_ : nullptr);",
        f"    }});",
        f"    cls.def_static(\"_stratum_cast\", [](nb::object py_obj) -> {sname}* {{",
        f"        if (py_obj.is_none()) return nullptr;",
        f"        if (!nb::hasattr(py_obj, \"_get_jobject_ptr\")) {{",
        f"            LOGE(\"CAST FAILED: Python object lacks _get_jobject_ptr. Not a Stratum wrapper.\");",
        f"            throw std::runtime_error(\"Object is not a Stratum wrapper\");",
        f"        }}",
        f"        int64_t ptr = nb::cast<int64_t>(py_obj.attr(\"_get_jobject_ptr\")());",
        f"        if (!ptr) {{",
        f"            LOGW(\"CAST WARNING: Underlying jobject pointer is null\");",
        f"            return nullptr;",
        f"        }}",
        f"        LOGV(\"CAST OK: Extracted ptr=%p -> cast to {sname}\", (void*)ptr);",
        f"        return new {sname}((jobject)(uintptr_t)ptr);",
        f"    }}, nb::arg(\"obj\").none(true), nb::rv_policy::take_ownership);"
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
# stratum_structs.h
# =============================================================================

def emit_stratum_structs_h(
    all_classes: list,
    all_fqns: Optional[Set[str]] = None,
) -> str:
    for cls in all_classes:
        if cls.get("parent_fqn") == cls.get("fqn"):
            cls["parent_fqn"] = ""

    lines = [
        "// stratum_structs.h — Stratum Stage 06 v4.8 — DO NOT EDIT",
        "// [FIX-16] All virtual. [FIX-23] none(true) for pointer params.",
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
            #lines += [f"struct {sn} {{", f"    jobject obj_;"]
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
    return """\
// bridge_core.h — Stratum Stage 06 v4.8 — DO NOT EDIT
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

JNIEnv*      get_env();
JNIEnv*      get_env_safe();
jclass       find_class(JNIEnv* env, const char* name);
void         store_callback(const std::string& key, nb::callable fn);
nb::callable get_callback(const std::string& key);
void         remove_callback(const std::string& key);
"""


# =============================================================================
# bridge_core.cpp
# =============================================================================

def emit_bridge_core_cpp() -> str:
    return r"""
// bridge_core.cpp — Stratum Stage 06 v4.8 — DO NOT EDIT
// [FIX-13] get_env() null guard. [FIX-12] g_activity deleted in OnUnload.
// [FIX-25] Ultra-deep LOGV tracing throughout.
#include "bridge_core.h"
#include "stratum_structs.h"
#include <pthread.h>
#include <android/log.h>

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
        LOGV("FIND_CLASS_OK via FindClass: %s cls=%p", name, (void*)cls);
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
            LOGV("FIND_CLASS_FAIL loadClass threw: %s", name);
            env->ExceptionClear();
            return nullptr;
        }
        if (cls) {
            LOGD("find_class: found %s via ClassLoader", name);
            LOGV("FIND_CLASS_OK via ClassLoader: %s cls=%p", name, (void*)cls);
        } else {
            LOGE("find_class: NOT found: %s", name);
            LOGV("FIND_CLASS_NOTFOUND: %s", name);
        }
        return cls;
    }
    LOGE("find_class: no ClassLoader available for %s", name);
    LOGV("FIND_CLASS_NOCLASSLOADER: %s", name);
    return nullptr;
}

void store_callback(const std::string& key, nb::callable fn) {
    std::lock_guard<std::mutex> lock(g_callback_mutex);
    g_callbacks[key] = std::make_shared<nb::callable>(std::move(fn));
    LOGD("store_callback: key=%s", key.c_str());
    LOGV("CALLBACK_STORE key=%s", key.c_str());
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

// ── StratumObject implementation ──────────────────────────────────────────────

StratumObject::~StratumObject() {
    JNIEnv* env = get_env_safe();
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

// ── nativeDispatch ────────────────────────────────────────────────────────────
extern "C" JNIEXPORT void JNICALL
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

    // CRITICAL FIX: Acquire GIL *BEFORE* looking up nanobind callbacks!
    nb::gil_scoped_acquire _acquire;

    nb::callable fn = get_callback(routed_key);
    if (!fn.is_valid()) fn = get_callback(base_key);
    if (!fn.is_valid()) {
        LOGW("nativeDispatch: no callback for key=%s", routed_key.c_str());
        LOGV("DISPATCH_NO_CALLBACK key=%s", routed_key.c_str());
        return;
    }

    LOGV("DISPATCH_CALLBACK_FOUND key=%s", routed_key.c_str());

    //nb::gil_scoped_acquire _acquire;
    try {
        jsize len = args ? env->GetArrayLength(args) : 0;
        LOGV("DISPATCH_ARGS_LEN %d", (int)len);
        if (len == 0) {
            LOGV("DISPATCH_CALL_NOARGS");
            fn();
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
            LOGV("DISPATCH_CALL_WITH_ARGS count=%d", (int)len);
            fn(*nb::tuple(py_args));
            LOGV("DISPATCH_CALL_WITH_ARGS_DONE");
        }
    }
    catch (nb::python_error& e) {
        LOGE("nativeDispatch Python error: %s", e.what());
        e.restore();
        if (PyErr_Occurred()) PyErr_Print();
    }
    catch (const std::exception& e) {
        LOGE("nativeDispatch exception: %s", e.what());
        PyErr_SetString(PyExc_RuntimeError, e.what());
    }
    LOGV("DISPATCH_DONE key=%s", base_key.c_str());
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


def emit_bridge_main(classes: list) -> str:
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

    factory_classes = [
        c for c in ordered
        if _has_context_ctor(c) and c["fqn"] != "android.app.Activity"
    ]
    batches: List[List[dict]] = [
        ordered[i:i + batch_size]
        for i in range(0, len(ordered), batch_size)
    ]

    lines = [
        "// bridge_main.cpp — Stratum Stage 06 v4.8 — DO NOT EDIT",
        "// [FIX-11] StratumObject registered first.",
        "// [FIX-12] g_activity deleted in OnUnload.",
        "// [FIX-16] No NonVirtual calls anywhere.",
        "// [FIX-23] nb::arg().none(true) for all pointer params.",
        "// [FIX-25] Ultra-deep LOGV tracing.",
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

    for cls in ordered:
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
        f"static const size_t g_total_classes = {len(ordered)};",
        "",
    ]

    # ── JNI_OnLoad ─────────────────────────────────────────────────────────────
    lines += [
        "extern \"C\" JNIEXPORT jint JNICALL JNI_OnLoad(JavaVM* vm, void*) {",
        "    g_jvm = vm;",
        "    LOGI(\"JNI_OnLoad begin\");",
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
        "    LOGI(\"JNI_OnLoad: %zu classes, lazy init on first touch.\","
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
        "    nb::gil_scoped_acquire gil;",
        "    try {",
        "        LOGV(\"LIFECYCLE_CALL_START: %s\", name);",
        "        fn();",
        "        LOGV(\"LIFECYCLE_CALL_DONE: %s\", name);",
        "        LOGD(\"dispatch_lifecycle: %s done\", name);",
        "    }",
        "    catch (nb::python_error& e) {",
        "        e.restore();",
        "        LOGE(\"dispatch_lifecycle Python error in %s\", name);",
        "        if (PyErr_Occurred()) PyErr_Print();",
        "    }",
        "    catch (const std::exception& e) {",
        "        LOGE(\"dispatch_lifecycle C++ error in %s: %s\","
        " name, e.what());",
        "    }",
        "    catch (...) {",
        "        LOGE(\"dispatch_lifecycle unknown error in %s\", name);",
        "    }",
        "}",
        "",
    ]

    # ── NB_MODULE ─────────────────────────────────────────────────────────────
    lines += [
        "NB_MODULE(_stratum, m) {",
        "    try {",
        f"        LOGI(\"NB_MODULE: {len(ordered)} classes"
        f" in {len(batches)} batches\");",
        f"        LOGV(\"NB_MODULE_START total={len(ordered)} batches={len(batches)}\");",
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
        "            .def(\"_get_jobject_ptr\", [](StratumObject* self) -> int64_t {",
        "                return (int64_t)(uintptr_t)(self ? self->obj_ : nullptr);",
        "            });",
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
        "        m.def(\"cast_to\", [](StratumObject* o,",
        "                              const std::string& cls_name)"
        " -> StratumObject* {",
        "            LOGV(\"cast_to %s o=%p\", cls_name.c_str(), o ? o->obj_ : nullptr);",
        "            if (!o || !o->obj_) {",
        "                LOGE(\"cast_to: null object\");",
        "                throw std::runtime_error(\"cast_to: null\");",
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
        "        m.def(\"getActivity\","
        " []() -> Stratum_android_app_Activity* {",
        "            LOGV(\"getActivity called g_activity=%p\", g_activity);",
        "            if (!g_activity) {",
        "                LOGE(\"getActivity: g_activity is null\");",
        "                throw std::runtime_error(\"Activity is null\");",
        "            }",
        "            return new Stratum_android_app_Activity(g_activity);",
        "        }, nb::rv_policy::take_ownership);",
        "",
    ]

    # Factory functions with none(true) [FIX-23]
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
            f"            //nb::gil_scoped_release _rel;",
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

    if any(c["fqn"] == "android.view.View" for c in ordered):
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
            "                //nb::gil_scoped_release _rel;",
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
        "        LOGI(\"NB_MODULE: load complete\");",
        "        LOGV(\"NB_MODULE_COMPLETE\");",
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
        "    if (g_activity) env->DeleteGlobalRef(g_activity);",
        "    g_activity = activity ? env->NewGlobalRef(activity) : nullptr;",
        "    LOGI(\"g_activity set to %p\", g_activity);",
        "    LOGV(\"NATIVE_SET_ACTIVITY_DONE g_activity=%p\", g_activity);",
        "}",
    ]

    return "\n".join(lines)


# =============================================================================
# Topological sort
# =============================================================================

def topological_sort(classes: list) -> list:
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

    return ordered


# =============================================================================
# Diagnostics Markdown Generator
# =============================================================================

def generate_markdown_report(classes: list, output_dir: Path) -> None:
    lines = [
        "# Stratum API Surface Report (v4.8)",
        f"Generated on: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        f"Total Emitted Classes: **{len(classes)}**",
        "",
        "## Fix Summary (v4.8)",
        "- FIX-16: All instance method calls use virtual dispatch",
        "- FIX-17: RegisterNatives only for truly native methods",
        "- FIX-18: JNICALL stubs never use NonVirtual calls",
        "- FIX-19: LOGD diagnostics (STRATUM_VERBOSE_LOG=1)",
        "- FIX-20: Inner-class constructors skipped in factory generation",
        "- FIX-21: Override class var index unified with all_for_init",
        "- FIX-22: Context-inherited methods work via virtual",
        "- FIX-23: **NEW** All pointer params emit nb::arg().none(true) — "
        "Python None now accepted for all nullable pointers",
        "- FIX-24: inflate/addView/removeView ViewGroup* param accepts None",
        "- FIX-25: **NEW** Ultra-deep LOGV tracing (STRATUM_ULTRA_LOG=1) — "
        "every JNI call direction, every arg, every return value logged",
        "- FIX-26: setOnClickListener lambda support via callable_to_proxy",
        "- FIX-27: getResources/getLayoutInflater return typed wrappers",
        "- FIX-28: removeView/addView ViewGroup methods with none(true)",
        "- FIX-29: inflate(int, ViewGroup, bool) parent param tagged none(true)",
        "",
        "## Logging Levels",
        "```cmake",
        "# Production (default) — only errors/warnings:",
        "# (no defines needed)",
        "",
        "# Debug — method entry/exit, class init, method IDs:",
        "target_compile_definitions(stratum PRIVATE STRATUM_VERBOSE_LOG=1)",
        "",
        "# Ultra-debug — EVERY JNI call direction, every arg, every return:",
        "target_compile_definitions(stratum PRIVATE STRATUM_ULTRA_LOG=1)",
        "# Note: STRATUM_ULTRA_LOG=1 also enables STRATUM_VERBOSE_LOG",
        "```",
        "",
        "## Log Tags in logcat",
        "- `Stratum` — general logs (LOGI/LOGW/LOGE/LOGD)",
        "- `Stratum/TRACE` — call direction markers (>> PY->CPP->JNI, << JNI->CPP->PY)",
        "- `Stratum/ARG` — argument values per call",
        "- `Stratum/RET` — return values per call",
        "",
        "## logcat filter for ultra debugging",
        "```bash",
        "adb logcat -s 'Stratum:V' 'Stratum/TRACE:V' 'Stratum/ARG:V' 'Stratum/RET:V'",
        "```",
        "",
        "---",
        "",
    ]

    for cls in classes:
        fqn     = cls.get("fqn", "Unknown")
        py_name = cpp_class_prefix(fqn)
        methods = get_methods_for_class(cls)
        lines.append(f"## {py_name} (`{fqn}`)")

        if cls.get("fields"):
            lines.append("#### Fields:")
            for f in cls["fields"]:
                lines.append(
                    f"- `get_{sanitize_id(f.get('name',''))}()`")

        bindable = methods["declared"] + methods["overridden"]
        if bindable:
            lines.append("#### Methods:")
            for m in bindable:
                params    = m.get("params", [])
                has_ptr   = any(cpp_type_for_param(p).endswith("*") for p in params)
                none_note = " *(accepts None)*" if has_ptr else ""
                lines.append(
                    f"- `{sanitize_id(m.get('name', ''))}()`"
                    f" → {m.get('return_cpp', 'void')}{none_note}")

        lines.append("\n---")

    md_path = output_dir / "api_surface_reference.md"
    md_path.write_text("\n".join(lines), encoding="utf-8")


# =============================================================================
# main
# =============================================================================

def main() -> None:
    global NANOBIND_BATCH_SIZE

    parser = argparse.ArgumentParser(
        description="Stratum Stage 06 v4.8 — Complete JNI Bridge Emit",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--input",      required=True,
        help="05_resolve/output/ directory")
    parser.add_argument("--output",     required=True,
        help="06_cpp_emit/output/ directory")
    parser.add_argument("--batch-size", type=int,
        default=NANOBIND_BATCH_SIZE,
        help=f"Classes per NB_MODULE batch (default: {NANOBIND_BATCH_SIZE})")
    args = parser.parse_args()
    NANOBIND_BATCH_SIZE = args.batch_size

    print_header("STRATUM PIPELINE — STAGE 06 v4.8 (NONE-POINTER + ULTRA-LOG)")
    print(f"  Dispatch       : compile-time RegisterNatives (is_native only)")
    print(f"  Calls          : ALL VIRTUAL (no NonVirtual anywhere) [FIX-16]")
    print(f"  RegisterNatives: only is_native=True methods [FIX-17]")
    print(f"  Pointer params : nb::arg().none(true) — Python None accepted [FIX-23]")
    print(f"  Logging LOGD   : STRATUM_VERBOSE_LOG=1 [FIX-19]")
    print(f"  Logging LOGV   : STRATUM_ULTRA_LOG=1   [FIX-25]")
    print(f"  Log tags       : Stratum, Stratum/TRACE, Stratum/ARG, Stratum/RET")
    print(f"  Inner ctors    : skipped in factory gen [FIX-20]")
    print(f"  Override idx   : unified with all_for_init [FIX-21]")
    print(f"  Context chain  : getResources etc work via virtual [FIX-22]")
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
        )
    )
    if not json_files:
        print("ERROR: No JSON files found. Did Stage 05 succeed?")
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

    GENERATED_FQNS.clear()
    for cls in all_classes:
        GENERATED_FQNS.add(cls["fqn"])

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
            print(f"  FAIL [{i}] {cls.get('fqn','?')} → {e}")

    (core_dir / "bridge_main.cpp").write_text(
        emit_bridge_main(emitted), encoding="utf-8")
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
            "version":        "4.8",
            "total_emitted":  len(emitted),
            "total_failed":   len(failed),
            "batches":        n_batches,
            "batch_size":     NANOBIND_BATCH_SIZE,
            "dispatch":       "compile_time_virtual_only",
            "fixes_applied": [
                "FIX-1:sanitize_id",
                "FIX-2:reconstruct_jni_sig_warn",
                "FIX-3:proxy_global_ref_cleanup",
                "FIX-4:null_methodid_logw",
                "FIX-5:override_cls_slash_normalise",
                "FIX-6:prefix_collision_suffix",
                "FIX-7:context_ctor_all_params",
                "FIX-8:stratum_surface_global_ref",
                "FIX-9:field_getter_global_ref",
                "FIX-10:null_return_consistent",
                "FIX-11:base_types_registered_first",
                "FIX-12:g_activity_unload_cleanup",
                "FIX-13:get_env_null_guard",
                "FIX-14:jni_args_index_stable",
                "FIX-15:stratum_structs_surface_fix",
                "FIX-16:ALL_VIRTUAL_NO_NONVIRTUAL",
                "FIX-17:REGISTER_NATIVES_NATIVE_ONLY",
                "FIX-18:JNICALL_STUBS_VIRTUAL_ONLY",
                "FIX-19:VERBOSE_LOGD_DIAGNOSTICS",
                "FIX-20:INNER_CTOR_SKIP",
                "FIX-21:OVERRIDE_IDX_UNIFIED",
                "FIX-22:CONTEXT_METHODS_VIRTUAL",
                "FIX-23:POINTER_PARAMS_NONE_TRUE",
                "FIX-24:INFLATE_VIEWGROUP_NONE_TRUE",
                "FIX-25:ULTRA_LOGV_TRACING",
                "FIX-26:LISTENER_LAMBDA_SUPPORT",
                "FIX-27:GETRESOURCES_TYPED_RETURN",
                "FIX-28:REMOVEVIEW_ADDVIEW_NONE_TRUE",
                "FIX-29:INFLATE_PARENT_NONE_TRUE",
            ],
            "failed": failed,
        }, indent=2),
        encoding="utf-8",
    )

    print_header("STAGE 06 v4.8 COMPLETE")
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