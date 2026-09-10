#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Stratum Pipeline — Stage 05: Resolve & Slot Assignment
========================================================
LOCATION: 05_resolve/main.py
VERSION: v9 (merges v8 + v8fix + Patch A array/list tag hardening)

WHAT THIS STAGE DOES
    Reads Stage 04's per-class JSON (pass 1) OR Stage 05.5's PATCHED
    per-class JSON (pass 2 — see below) and assigns every class a
    deterministic class_id (0..N-1) and every constructor/method a
    deterministic slot (0..M-1). It also precomputes:
      - param_tags   : one character per parameter telling the C++ engine
                        exactly how to convert that argument at call time,
                        so the engine never has to parse a JNI signature
                        string at runtime — it just indexes an array.
      - ret_type_id  : which Call<Type>MethodA the engine should use for
                        the return value.
      - adapter_jni  : the JNI name of the Java adapter/interface class a
                        callback param ('a'/'p' tag) should be
                        instantiated/proxied as.

WHY THIS STAGE RUNS TWICE (DO NOT SKIP PASS 1 OR COLLAPSE THE TWO RUNS)
    Pass 1: 04_parse/output/  ->  05_resolve/output/
        Exists only so Stage 05.5 (which generates Java adapter .java
        files for callbacks/abstract classes) has a properly-shaped JSON
        corpus to read and patch.
    Stage 05.5 writes 05_5_abstract/output/patched/ — the SAME class
        JSON files, with every callback parameter now carrying
        needs_adapter / adapter_jni / needs_proxy / proxy_interface.
    Pass 2: 05_5_abstract/output/patched/  ->  05_resolve/output_patched/
        THIS is the run that actually feeds Stage 06 and Stage 08. Skip
        it and every callback silently does nothing (param_tag stays
        'L' instead of 'a'/'p', so no adapter/proxy is ever built).

v9 TAG TABLE (must match 06_cpp_emit's pack_arguments() switch and
08_pyi_emit's overload type_check dict EXACTLY — if you ever add a tag
here, add the matching case in both of those files too)
    Z B C S I J F D   primitives, packed straight into jvalue
    s                 String / CharSequence -> UTF-16 jstring
    a                 abstract-adapter param -> engine instantiates a
                      Stage 05.5 Java adapter, routes callbacks to Python
    p                 callable-to-proxy param -> engine builds a
                      java.lang.reflect.Proxy for a single-interface
                      listener, routes callbacks to Python
    [   byte[]        from Python bytes/bytearray
    ]   int[]         from Python list[int]
    q   long[]        from Python list[int]
    f   float[]       from Python list[float]
    d   double[]      from Python list[float]
    b   boolean[]     from Python list[bool]
    c   char[]        from Python list[str-of-1-char or int]
    h   short[]       from Python list[int]
    T   String[]      from Python list[str]
    A   Object[]      from Python list[wrapped-object/str/None]
    M   java.util.List / Collection / Iterable
                      from Python list -> built as a real java.util.ArrayList
    L   (fallback)    raw jobject pointer (wrapped `_ptr` int64), or None

NOTE ON `closure_mode`
    parents_only / parents_and_interfaces / full — unchanged semantics.
    Only controls which classes are INCLUDED; there is no per-class C++
    anymore, so "full" no longer risks a compiler OOM.
"""

import argparse
import json
import re
import sys
from pathlib import Path

CLOSURE_MODES = ("parents_only", "parents_and_interfaces", "full")

# Must stay in sync with CALL_DISPATCH in 08_pyi_emit/main.py and the
# switch in stratum_engine.cpp's call_d() (ret_type==7 -> float path).
RET_TYPE_MAP = {
    "void": 0, "jboolean": 1, "jbyte": 2, "jchar": 3, "jshort": 4,
    "jint": 5, "jlong": 6, "jfloat": 7, "jdouble": 8,
    "jstring": 9, "jobject": 10,
}

# v9.1: return types that get auto-converted to native Python list/dict
# instead of being wrapped as a generic Java object.
COLLECTION_RETURN_TYPES = {
    "java.util.List", "java.util.ArrayList", "java.util.LinkedList",
    "java.util.Collection", "java.util.Set", "java.util.HashSet",
    "java.util.LinkedHashSet", "java.util.TreeSet", "java.util.Queue",
    "java.util.Deque", "java.util.ArrayDeque",
}
MAP_RETURN_TYPES = {
    "java.util.Map", "java.util.HashMap", "java.util.LinkedHashMap",
    "java.util.TreeMap", "java.util.Hashtable", "java.util.SortedMap",
}


def print_header(title: str) -> None:
    print("=" * 70)
    print(f"  {title}")
    print("=" * 70)


def sanitize_id(s: str) -> str:
    """Make any Java identifier safe as a C/Python identifier."""
    s = re.sub(r"[^a-zA-Z0-9_]", "_", s)
    if s and s[0].isdigit():
        s = "_" + s
    return re.sub(r"_+", "_", s).strip("_") or "_unknown"


# =============================================================================
# v9 / Patch A: full array + collection tag detection.
#
# WHY THIS MATTERS: any parameter that isn't caught by one of the
# specific branches below falls through to the generic 'L' tag, which
# the C++ engine treats as "either a wrapped Stratum object pointer, or
# None" — a Python list or bytes object hitting that fallback silently
# becomes Java `null`. Every branch here exists to catch one concrete,
# common Android API shape (Canvas.drawLines(float[]),
# CameraDevice.createCaptureSession(List<Surface>), byte[] I/O, etc.)
# before it can fall through.
# =============================================================================
def compute_param_tags(params: list) -> str:
    tags = []
    for p in params:
        conv = p.get("conversion", "")
        java_type = p.get("java_type", "")
        jni_type = p.get("jni_type", "jobject")

        if conv == "abstract_adapter" or p.get("needs_adapter", False):
            tags.append("a")

        elif conv == "callable_to_proxy" or p.get("needs_proxy", False):
            tags.append("p")

        elif conv in ("string_in", "string_out") or java_type in (
            "java.lang.String", "java.lang.CharSequence",
            "java/lang/String", "java/lang/CharSequence",
        ):
            tags.append("s")

        # ── java.util.List / Collection / Iterable -> real ArrayList ────────
        elif java_type in (
            "java.util.List", "java.util.Collection",
            "java.util.ArrayList", "java.lang.Iterable",
        ):
            tags.append("M")

        # ── Primitive arrays (checked BEFORE the generic array fallback) ────
        elif java_type == "[B" or jni_type == "jbyteArray":
            tags.append("[")
        elif java_type == "[I" or jni_type == "jintArray":
            tags.append("]")
        elif java_type == "[J" or jni_type == "jlongArray":
            tags.append("q")
        elif java_type == "[F" or jni_type == "jfloatArray":
            tags.append("f")
        elif java_type == "[D" or jni_type == "jdoubleArray":
            tags.append("d")
        elif java_type == "[Z" or jni_type == "jbooleanArray":
            tags.append("b")
        elif java_type == "[C" or jni_type == "jcharArray":
            tags.append("c")
        elif java_type == "[S" or jni_type == "jshortArray":
            tags.append("h")

        # ── String[] ─────────────────────────────────────────────────────
        elif java_type in (
            "[Ljava.lang.String;", "[Ljava/lang/String;", "[java.lang.String",
        ):
            tags.append("T")

        # ── Any other object array (Object[], Surface[], etc.) ─────────────
        elif p.get("is_array", False) or java_type.startswith("[") or jni_type == "jobjectArray":
            tags.append("A")

        # ── Plain primitives ─────────────────────────────────────────────
        else:
            primitive = {
                "jboolean": "Z", "jbyte": "B", "jchar": "C", "jshort": "S",
                "jint": "I", "jlong": "J", "jfloat": "F", "jdouble": "D",
            }
            tags.append(primitive.get(jni_type, "L"))
    return "".join(tags)


def load_registry(parse_dir: Path) -> dict:
    registry = {}
    for jf in sorted(parse_dir.rglob("*.json")):
        if jf.name in ("parse_summary.json", "resolve_summary.json", "manifest.json"):
            continue
        try:
            data = json.loads(jf.read_text(encoding="utf-8"))
            fqn = data.get("fqn", "")
            if fqn:
                registry[fqn] = data
        except Exception as e:
            print(f"  [WARN] Failed to load {jf.name}: {e}")
    return registry


def compute_target_closure(seed_fqns: list, registry: dict, closure_mode: str) -> set:
    if closure_mode not in CLOSURE_MODES:
        closure_mode = "parents_only"
    closure = set()
    queue = []
    for fqn in seed_fqns:
        if fqn in registry:
            closure.add(fqn)
            queue.append(fqn)
    while queue:
        fqn = queue.pop()
        data = registry.get(fqn)
        if not data:
            continue
        parent = data.get("parent_fqn", "")
        if parent and parent in registry and parent not in closure:
            closure.add(parent)
            queue.append(parent)
        if closure_mode in ("parents_and_interfaces", "full"):
            for iface in data.get("interfaces", []):
                if iface in registry and iface not in closure:
                    closure.add(iface)
                    queue.append(iface)
    return closure


def index_and_save(selected_fqns: list, registry: dict, output_dir: Path, closure_mode: str) -> None:
    total_methods = 0
    total_fields = 0

    for class_id, fqn in enumerate(selected_fqns):
        data = registry[fqn]
        data["class_id"] = class_id
        data["safe_simple_name"] = sanitize_id(fqn.split(".")[-1])

        methods = data.get("methods", [])
        # Constructors first, then non-constructors, both sorted
        # deterministically. The ONLY hard requirement is that this sort
        # is reproducible: Stage 06 (C++ table) and Stage 08 (Python
        # wrapper) are generated from the exact same resolved JSON and
        # MUST agree on slot numbers without needing to communicate.
        ctors = sorted([m for m in methods if m.get("is_constructor")],
                        key=lambda m: m.get("jni_signature", ""))
        non_ctors = sorted([m for m in methods if not m.get("is_constructor")],
                            key=lambda m: (m.get("name", ""), m.get("jni_signature", "")))
        ordered = ctors + non_ctors

        for slot, m in enumerate(ordered):
            m["slot"] = slot
            m["param_tags"] = compute_param_tags(m.get("params", []))
            ret_fqn = m.get("return_java_type", "")
            if m.get("return_is_array", False):
                m["ret_type_id"] = 11
            elif ret_fqn in MAP_RETURN_TYPES:
                m["ret_type_id"] = 13
            elif ret_fqn in COLLECTION_RETURN_TYPES:
                m["ret_type_id"] = 12
            else:
                m["ret_type_id"] = RET_TYPE_MAP.get(m.get("return_jni", "void"), 10)
            m["safe_name"] = sanitize_id(m.get("name", "unknown"))

            # Which Java adapter/interface class a callback-style param
            # (tag 'a' or 'p') should be instantiated/proxied as.
            adapter_jni = ""
            for p in m.get("params", []):
                if p.get("adapter_jni"):
                    adapter_jni = p["adapter_jni"]
                    break
                if p.get("proxy_interface"):
                    adapter_jni = p["proxy_interface"].replace(".", "/")
                    break
            m["adapter_jni"] = adapter_jni

            # Statically-known FQN of an object return type (if any), so
            # Stage 08 can wrap the raw pointer in the CORRECT Python
            # class instead of a generic StratumObject.
            ret_java = m.get("return_java_type", "")
            m["return_fqn"] = ret_java if ret_java else ""

        data["methods"] = ordered
        data["constructors"] = ctors
        data["method_count"] = len(ordered)
        total_methods += len(ordered)

        # Fields — used for static SDK constants (Color.RED, View.VISIBLE)
        # and any mutable public/protected fields.
        fields = data.get("fields", [])
        for f_slot, f in enumerate(fields):
            f["slot"] = f_slot
            f_sig = f.get("jni_signature", f.get("jni_type", ""))
            # [Patch 15] Array-typed fields (Build.SUPPORTED_ABIS, etc.) get
            # their own ret_type_id so Stage 06/08 route them through
            # field_get_arr instead of the generic object getter.
            if f_sig.startswith("["):
                f["ret_type_id"] = 11
            else:
                f["ret_type_id"] = RET_TYPE_MAP.get(f.get("jni_type", "jobject"), 10)
            f["safe_name"] = sanitize_id(f.get("name", "unknown"))
        data["fields"] = fields
        data["field_count"] = len(fields)
        total_fields += len(fields)

        rel = Path(*fqn.split(".")).with_suffix(".json")
        out = output_dir / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(data, indent=2), encoding="utf-8")

    summary = {
        "stage": "05_resolve",
        "total_classes": len(selected_fqns),
        "total_methods": total_methods,
        "total_fields": total_fields,
        "closure_mode": closure_mode,
    }
    (output_dir / "resolve_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"-> Indexed {len(selected_fqns):,} classes ({total_methods:,} methods, {total_fields:,} fields).")


def main():
    ap = argparse.ArgumentParser(description="Stratum Stage 05 - Resolve & Index (v9)")
    ap.add_argument("--input", required=True,
                     help="04_parse/output/ on pass 1, or 05_5_abstract/output/patched/ on pass 2")
    ap.add_argument("--output", required=True, help="05_resolve/output/ (or output_patched/)")
    ap.add_argument("--closure-mode", choices=CLOSURE_MODES, default=None)
    args = ap.parse_args()

    print_header("STRATUM PIPELINE — STAGE 05 (RESOLVE & INDEX) v9")
    input_dir = Path(args.input)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    registry = load_registry(input_dir)
    print(f"-> Loaded {len(registry):,} class files.")

    targets_file = Path("05_resolve/targets.json")
    filter_enabled, seed_fqns, closure_mode = False, [], (args.closure_mode or "parents_only")

    if targets_file.exists():
        raw = json.loads(targets_file.read_text(encoding="utf-8"))
        filter_enabled = bool(raw.get("enabled", False))
        seed_fqns = [t["fqn"] for t in raw.get("targets", []) if t.get("fqn")]
        if not args.closure_mode:
            closure_mode = raw.get("closure_mode", "parents_only")
    else:
        targets_file.parent.mkdir(parents=True, exist_ok=True)
        targets_file.write_text(json.dumps({
            "enabled": False,
            "closure_mode": "parents_only",
            "targets": []
        }, indent=2), encoding="utf-8")

    if filter_enabled and seed_fqns:
        selected_fqns = sorted(compute_target_closure(seed_fqns, registry, closure_mode))
        print(f"-> Filter Mode: {len(seed_fqns)} seeds -> {len(selected_fqns):,} classes ({closure_mode}).")
    else:
        selected_fqns = sorted(registry.keys())
        print(f"-> Full Mode: Resolving all {len(selected_fqns):,} classes.")

    index_and_save(selected_fqns, registry, output_dir, closure_mode)
    print(f"-> Saved index to: {output_dir}")


if __name__ == "__main__":
    main()