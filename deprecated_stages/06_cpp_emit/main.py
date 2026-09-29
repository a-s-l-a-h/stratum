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
            "    {%d, %d, %d, %s, %s, nullptr, nullptr, nullptr}," % (
                pool.get_offset(cls.get("jni_name", "")),
                cls.get("method_count", 0),
                cls.get("field_count", 0),
                m_ptr, f_ptr,
            )
        )
    cpp.append("};")
    cpp.append("")
    cpp.append(f"extern const uint32_t g_class_count = {len(classes)};")
    cpp.append("")
    cpp.append(f"extern const char g_str_pool[{len(pool.pool)}] = {{")
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
        "#include <atomic>",
        "struct ClassMeta {",
        "    uint32_t          jni_name_offset;",
        "    uint16_t          method_count;",
        "    uint16_t          field_count;",
        "    const MethodMeta* methods;",
        "    const FieldMeta*  fields;",
        "    jclass            class_ref;",
        "    jmethodID*        method_ids;",
        "    jfieldID*         field_ids;",
        "    std::atomic<bool> resolved{false};",
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
BRIDGE_CORE_H = (Path(__file__).parent / "engine_src" / "bridge_core.h").read_text(encoding="utf-8")

BRIDGE_CORE_CPP = (Path(__file__).parent / "engine_src" / "bridge_core.cpp").read_text(encoding="utf-8")

# =============================================================================
# stratum_engine.cpp — the universal dispatcher. This is where v9 FIX 2,
# v9 FIX 3, and v9 FIX 4 live.
# =============================================================================
STRATUM_ENGINE_CPP = (Path(__file__).parent / "engine_src" / "stratum_engine.cpp").read_text(encoding="utf-8")

# =============================================================================
# bridge_main.cpp — nanobind entry point + Activity/lifecycle glue.
# Unchanged from v8/v8fix — no bugs identified here.
# =============================================================================
BRIDGE_MAIN_CPP = (Path(__file__).parent / "engine_src" / "bridge_main.cpp").read_text(encoding="utf-8")


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