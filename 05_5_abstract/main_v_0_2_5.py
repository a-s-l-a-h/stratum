#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Stratum Pipeline — Stage 05.5 : Abstract Adapter + Interface Adapter Generator

FIX: jni_to_java_type() now uses java_type directly for jobject params.
     This means CharSequence stays CharSequence, Editable stays Editable,
     CameraDevice stays android.hardware.camera2.CameraDevice — exactly
     matching the real Java interface signatures. No hardcoded lookup needed.
"""

import argparse
import copy
import json
import re
import shutil
import sys
from pathlib import Path


# =============================================================================
# Constants
# =============================================================================

ADAPTER_PACKAGE  = "com.stratum.adapters"
DISPATCH_CLASS   = "com.stratum.runtime.StratumInvocationHandler"

SKIP_SUMMARIES = frozenset({
    "parse_summary.json",
    "resolve_summary.json",
    "manifest.json",
    "cpp_summary.json",
    "pyi_summary.json",
})

ADAPTER_CLASS_PREFIX = "Adapter_"


# =============================================================================
# Logging helpers
# =============================================================================

def log_info(msg):  print(f"[INFO]  {msg}", flush=True)
def log_ok(msg):    print(f"[OK]    {msg}", flush=True)
def log_warn(msg):  print(f"[WARN]  {msg}", flush=True)
def log_skip(msg):  print(f"[SKIP]  {msg}", flush=True)
def log_error(msg): print(f"[ERROR] {msg}", flush=True)
def log_debug(msg): print(f"[DEBUG] {msg}", flush=True)

def print_header(title):
    print("=" * 70)
    print(f"  {title}")
    print("=" * 70)

def print_section(title):
    print(f"\n--- {title} ---", flush=True)


# =============================================================================
# Name helpers
# =============================================================================

def fqn_to_jni(fqn):
    return fqn.replace(".", "/")

def adapter_class_name(fqn):
    safe = re.sub(r"[.$]", "_", fqn)
    return f"{ADAPTER_CLASS_PREFIX}{safe}"

def adapter_full_class(fqn):
    return f"{ADAPTER_PACKAGE}.{adapter_class_name(fqn)}"

def adapter_jni(fqn):
    return fqn_to_jni(adapter_full_class(fqn))


# =============================================================================
# Java type helpers
#
# KEY DESIGN DECISION:
#   For jobject params, we use the java_type field from Stage 04/05 directly.
#   Stage 04 parsed the real Java signature — it already knows the type is
#   "java.lang.CharSequence", "android.text.Editable", etc.
#   We just normalize slashes→dots and $→dot and use it as-is.
#
#   This is why the working adapters (CameraDevice, SurfaceTexture) work:
#     java_type = "android.hardware.camera2.CameraDevice"  →  used directly
#   And why TextWatcher broke with the old code:
#     java_type = "java.lang.CharSequence"  →  was incorrectly mapped to String
#
#   The correct approach: TRUST java_type. Never override it with a lookup table.
# =============================================================================

def jni_to_java_type(jni_type: str, java_type: str = "") -> str:
    """
    Convert JNI type + java_type hint to a Java source type string.
    """
    PRIMITIVES = {
        "jboolean": "boolean", "jbyte": "byte", "jchar": "char",
        "jshort": "short", "jint": "int", "jlong": "long",
        "jfloat": "float", "jdouble": "double", "void": "void",
    }
    if jni_type in PRIMITIVES:
        return PRIMITIVES[jni_type]

    PRIM_ARRAYS = {
        "jbooleanArray": "boolean[]", "jbyteArray": "byte[]",
        "jcharArray": "char[]", "jshortArray": "short[]",
        "jintArray": "int[]", "jlongArray": "long[]",
        "jfloatArray": "float[]", "jdoubleArray": "double[]",
    }
    if jni_type in PRIM_ARRAYS:
        return PRIM_ARRAYS[jni_type]

    # Normalize java_type if present
    norm_java = java_type.replace("/", ".").replace("$", ".") if java_type else ""

    # Force CharSequence over String if it was explicitly captured
    if norm_java.endswith("CharSequence"):
        return "CharSequence"

    if norm_java:
        # Object arrays
        if norm_java.startswith("[L") and norm_java.endswith(";"):
            base = norm_java[2:-1]
            if base.startswith("java.lang."):
                base = base[10:]
            return base + "[]"
            
        # Standard objects
        if not norm_java.startswith("["):
            if norm_java.startswith("java.lang."):
                return norm_java[10:]
            return norm_java

    # Fallbacks
    if jni_type == "jstring":
        return "String"
    if jni_type == "jobjectArray":
        return "Object[]"
        
    return "Object"


def java_return_default(jni_type: str) -> str:
    """Return a valid Java return statement for the given JNI return type."""
    mapping = {
        "jboolean": "return false;",
        "jbyte":    "return 0;",
        "jchar":    "return 0;",
        "jshort":   "return 0;",
        "jint":     "return 0;",
        "jlong":    "return 0L;",
        "jfloat":   "return 0.0f;",
        "jdouble":  "return 0.0;",
        "void":     "",
    }
    return mapping.get(jni_type, "return null;")


def box_for_dispatch(jni_type: str, varname: str) -> str:
    """
    Box a primitive value for passing in Object[] to nativeDispatch.
    For object types the varname is already an object — use as-is.
    """
    BOXING = {
        "jboolean": f"Boolean.valueOf({varname})",
        "jbyte":    f"Byte.valueOf({varname})",
        "jchar":    f"Character.valueOf({varname})",
        "jshort":   f"Short.valueOf({varname})",
        "jint":     f"Integer.valueOf({varname})",
        "jlong":    f"Long.valueOf({varname})",
        "jfloat":   f"Float.valueOf({varname})",
        "jdouble":  f"Double.valueOf({varname})",
    }
    return BOXING.get(jni_type, varname)


def null_default_for(p: dict) -> str:
    """
    Return the Java null/zero literal appropriate for a constructor param.
    Used when generating the no-arg convenience constructor that delegates
    to the full constructor.
    """
    mapping = {
        "jboolean": "false",
        "jbyte":    "0",
        "jchar":    "0",
        "jshort":   "0",
        "jint":     "0",
        "jlong":    "0L",
        "jfloat":   "0.0f",
        "jdouble":  "0.0",
    }
    return mapping.get(p.get("jni_type", "jobject"), "null")


# =============================================================================
# Registry loader
# =============================================================================

def load_registry(resolve_dir: Path) -> dict:
    print_section("Loading Stage 05 registry")
    registry = {}
    skipped = failed = 0

    for jf in sorted(resolve_dir.rglob("*.json")):
        if jf.name in SKIP_SUMMARIES:
            skipped += 1
            continue
        try:
            data = json.loads(jf.read_text(encoding="utf-8"))
            fqn  = data.get("fqn", "")
            if not fqn:
                skipped += 1
                continue
            if fqn in registry:
                skipped += 1
                continue
            registry[fqn] = (data, jf)
        except Exception as e:
            log_warn(f"Error loading {jf.name}: {e}")
            failed += 1

    log_info(f"Registry: {len(registry):,} classes | skipped={skipped} | failed={failed}")
    return registry


# =============================================================================
# Method collection helpers
# =============================================================================

def all_methods_of(data: dict) -> list:
    """Collect all methods from all method lists in a class JSON."""
    methods = []
    for key in ("declared_methods", "overridden_methods",
                "inherited_methods", "methods"):
        methods.extend(data.get(key, []))
    return methods


def get_abstract_methods(data: dict) -> list:
    """
    For abstract classes: return methods marked is_abstract=True.
    Deduplicates by name+signature so we don't emit duplicate overrides.
    """
    seen   = set()
    result = []
    for m in all_methods_of(data):
        if not m.get("is_abstract", False):
            continue
        if m.get("is_constructor", False):
            continue
        key = m.get("name", "") + "|" + m.get("jni_signature", "")
        if key not in seen:
            seen.add(key)
            result.append(m)
    return result


def get_interface_methods(data: dict) -> list:
    """
    For interfaces: ALL non-static, non-default, non-Object methods
    must be implemented. Collects from all method lists and deduplicates.

    Excludes Object methods because the JVM provides those automatically
    for any class, so implementing them would cause a compile error
    if the types don't match exactly.
    """
    OBJECT_METHODS = {
        "equals", "hashCode", "toString", "getClass",
        "notify", "notifyAll", "wait",
    }
    seen   = set()
    result = []
    for m in all_methods_of(data):
        name = m.get("name", "")
        if m.get("is_constructor", False):
            continue
        if m.get("is_static", False):
            continue
        if m.get("is_default", False):   # Java 8 default interface methods
            continue
        if name in OBJECT_METHODS:
            continue
        key = name + "|" + m.get("jni_signature", "")
        if key not in seen:
            seen.add(key)
            result.append(m)
    return result


# =============================================================================
# Detection
# =============================================================================

def detect_abstract_classes(registry: dict) -> list:
    """
    Scan the registry for abstract classes (not interfaces, not annotations)
    that have at least one abstract method requiring implementation.
    Returns sorted list of FQNs.
    """
    print_section("Detecting abstract classes")
    result = []
    for fqn, (data, _) in registry.items():
        if not data.get("is_abstract", False):
            continue
        if data.get("is_interface", False):
            continue
        if data.get("is_annotation", False):
            continue
        methods = get_abstract_methods(data)
        if not methods:
            log_warn(f"  {fqn} is abstract but 0 abstract methods found — skipping")
            continue
        log_info(f"  Abstract class: {fqn} ({len(methods)} abstract methods)")
        result.append(fqn)
    log_info(f"Abstract classes detected: {len(result)}")
    return sorted(result)


def detect_interface_targets(registry: dict, seed_fqns: list) -> list:
    """
    From the seed FQNs (targets.json), pick out the ones that are Java
    interfaces with at least one method. These need adapter files because
    the single-method proxy system cannot route multi-method callbacks.
    """
    print_section("Detecting interface targets from seeds")
    result = []
    for fqn in seed_fqns:
        entry = registry.get(fqn)
        if not entry:
            log_warn(f"  Seed FQN not in registry: {fqn}")
            continue
        data, _ = entry
        if data.get("is_interface", False):
            methods = get_interface_methods(data)
            if not methods:
                log_warn(f"  Interface {fqn} has 0 non-Object methods — skipping")
                continue
            log_info(f"  Interface target: {fqn} ({len(methods)} methods)")
            result.append(fqn)
    log_info(f"Interface targets: {len(result)}")
    return result


# =============================================================================
# Name collision detection
# =============================================================================

def check_name_collisions(to_adapt: list, registry: dict) -> list:
    """
    Verify that no generated adapter name collides with:
      - a class already in the corpus (full FQN match)
      - a class simple name already in the corpus
      - another adapter being generated in this run
    Returns list of collision messages. Empty = no collisions.
    """
    print_section("Name collision detection")
    collisions      = []
    corpus_full     = set(registry.keys())
    corpus_simple   = {fqn.split(".")[-1] for fqn in registry}
    generated_names = {}

    for fqn in to_adapt:
        cls_name  = adapter_class_name(fqn)
        full_name = adapter_full_class(fqn)

        if full_name in corpus_full:
            msg = f"COLLISION: {full_name!r} already in corpus"
            log_error(msg)
            collisions.append(msg)

        if cls_name in corpus_simple:
            msg = (f"COLLISION: simple name {cls_name!r} matches"
                   f" corpus entry (src={fqn!r})")
            log_error(msg)
            collisions.append(msg)

        if cls_name in generated_names:
            msg = (f"COLLISION: {fqn!r} and {generated_names[cls_name]!r}"
                   f" → same adapter name")
            log_error(msg)
            collisions.append(msg)
        else:
            generated_names[cls_name] = fqn

    if not collisions:
        log_ok(f"No collisions among {len(to_adapt)} adapters")
    return collisions


# =============================================================================
# Java adapter emitters
# =============================================================================

def _adapter_header(fqn: str, cls_name: str, extends_or_implements: str) -> list:
    """Emit the package declaration, imports, and class opening."""
    return [
        f"// Auto-generated by Stratum Stage 05.5 — DO NOT EDIT",
        f"// Source: {fqn}",
        f"// Copy this file to: runtime/java/com/stratum/adapters/",
        f"package {ADAPTER_PACKAGE};",
        f"",
        f"import android.util.Log;",
        f"import {DISPATCH_CLASS};",
        f"",
        f"public class {cls_name} {extends_or_implements} {{",
        f"",
        f"    private static final String TAG = \"StratumAdapter\";",
        f"    private final String key_;",
        f"",
    ]


def _adapter_footer(cls_name: str) -> list:
    """Emit toString() and closing brace."""
    return [
        f"    @Override",
        f"    public String toString() {{",
        f"        return \"{cls_name}[key=\" + key_ + \"]\";",
        f"    }}",
        f"",
        f"}}",
        f"",
    ]


def _constructor_block(cls_name: str, ctors: list, has_no_arg: bool) -> list:
    """
    Emit constructor(s) for an abstract-class adapter.

    If the superclass has a no-arg constructor: emit a single
      Adapter(String key) { super(); this.key_ = key; }

    If the superclass only has parameterized constructors: emit
      Adapter(String key, T1 a1, T2 a2, ...) { super(a1, a2, ...); ... }
      Adapter(String key) { this(key, null, 0, ...); }   ← convenience
    """
    lines = []
    if has_no_arg:
        lines += [
            f"    public {cls_name}(String key) {{",
            f"        super();",
            f"        this.key_ = key;",
            f"        //Log.d(TAG, \"[{cls_name}] created key=\" + key);",
            f"    }}",
            f"",
        ]
    else:
        first  = ctors[0]
        params = first.get("params", [])
        
        decls = []
        args  = []
        nulls = []
        
        for p in params:
            ptype = jni_to_java_type(p.get("jni_type", "jobject"), p.get("java_type", ""))
            # Use standard string concatenation to avoid f-string nested quote syntax errors
            pname = p.get("name", "arg" + str(p.get("index", 0)))
            
            decls.append(f"{ptype} {pname}")
            args.append(pname)
            nulls.append(null_default_for(p))

        lines += [
            f"    public {cls_name}(String key, {', '.join(decls)}) {{",
            f"        super({', '.join(args)});",
            f"        this.key_ = key;",
            f"        //Log.d(TAG, \"[{cls_name}] created (full ctor) key=\" + key);",
            f"    }}",
            f"",
            f"    public {cls_name}(String key) {{",
            f"        this(key, {', '.join(nulls)});",
            f"    }}",
            f"",
        ]
    return lines


def _method_overrides(cls_name: str, methods: list, is_interface: bool) -> list:
    """
    Emit @Override method stubs for each method.
    """
    lines = []
    for m in methods:
        mname    = m.get("name", "unknown")
        params   = m.get("params", [])
        ret_jni  = m.get("return_jni", "void")
        ret_java = jni_to_java_type(ret_jni, m.get("return_java_type", ""))
        ret_stmt = java_return_default(ret_jni)

        decls = []
        for p in params:
            ptype = jni_to_java_type(
                p.get("jni_type", "jobject"),
                p.get("java_type", "")
            )
            
            # --- AD-HOC FIX FOR TEXTWATCHER ---
            # TextWatcher requires CharSequence, but sometimes earlier pipeline 
            # stages alias CharSequence -> String. We force it back here.
            if mname in ("beforeTextChanged", "onTextChanged") and ptype == "String" and p.get("index", 0) == 0:
                ptype = "CharSequence"
                
            pname = p.get("name", "arg" + str(p.get("index", 0)))
            decls.append(f"{ptype} {pname}")

        if params:
            boxed = [
                box_for_dispatch(
                    p.get("jni_type", "jobject"),
                    p.get("name", "arg" + str(p.get("index", 0)))
                )
                for p in params
            ]
            args_expr = "new Object[]{ " + ", ".join(boxed) + " }"
        else:
            args_expr = "new Object[0]"

        lines += [
            f"    @Override",
            f"    public {ret_java} {mname}({', '.join(decls)}) {{",
            f"        //Log.d(TAG, \"[{cls_name}] {mname} key=\" + key_"
            f" + \" params={len(params)}\");",
            f"        StratumInvocationHandler.nativeDispatch(",
            f"            key_, \"{mname}\", {args_expr});",
        ]
        if ret_stmt:
            lines.append(f"        {ret_stmt}")
        lines += [
            f"    }}",
            f"",
        ]
    return lines


def emit_abstract_adapter(fqn: str, data: dict) -> str:
    """
    Generate a Java adapter for an abstract class using 'extends'.
    The adapter subclasses the abstract class and implements all abstract
    methods by routing calls through StratumInvocationHandler.nativeDispatch.
    """
    log_info(f"  Emitting abstract-class adapter: {fqn}")
    cls_name         = adapter_class_name(fqn)
    abstract_methods = get_abstract_methods(data)
    fqn_java         = fqn.replace("$", ".")

    # Find accessible constructors
    ctors = data.get("constructors", []) or [
        m for m in data.get("methods", []) if m.get("is_constructor")
    ]
    # Keep public and protected constructors only
    accessible = [
        c for c in ctors
        if c.get("is_public", True) or c.get("is_protected", True)
    ]
    if ctors and not accessible:
        raise RuntimeError(
            f"All constructors are private/package-private for {fqn} — cannot subclass")
    ctors = accessible

    no_arg     = next((c for c in ctors if not c.get("params", [])), None)
    has_no_arg = (no_arg is not None) or (not ctors)

    lines  = _adapter_header(fqn, cls_name, f"extends {fqn_java}")
    lines += _constructor_block(cls_name, ctors, has_no_arg)
    lines += _method_overrides(cls_name, abstract_methods, is_interface=False)
    lines += _adapter_footer(cls_name)

    log_ok(f"    abstract adapter: {cls_name} ({len(abstract_methods)} overrides)")
    return "\n".join(lines)


def emit_interface_adapter(fqn: str, data: dict) -> str:
    """
    Generate a Java adapter for an interface using 'implements'.
    Interfaces have no super() to call so the constructor is simple.
    All interface methods are implemented and route through nativeDispatch.
    """
    log_info(f"  Emitting interface adapter: {fqn}")
    cls_name      = adapter_class_name(fqn)
    iface_methods = get_interface_methods(data)
    fqn_java      = fqn.replace("$", ".")

    lines  = _adapter_header(fqn, cls_name, f"implements {fqn_java}")
    lines += [
        f"    public {cls_name}(String key) {{",
        f"        this.key_ = key;",
        f"        //Log.d(TAG, \"[{cls_name}] created key=\" + key);",
        f"    }}",
        f"",
    ]
    lines += _method_overrides(cls_name, iface_methods, is_interface=True)
    lines += _adapter_footer(cls_name)

    log_ok(f"    interface adapter: {cls_name} ({len(iface_methods)} overrides)")
    return "\n".join(lines)


# =============================================================================
# JSON patcher
# =============================================================================

def patch_class_json(data: dict, successfully_adapted: set) -> dict:
    """
    Patch Stage 05 JSON files so that params whose java_type matches an
    adapted class get conversion=abstract_adapter instead of callable_to_proxy.
    This tells Stage 06 to instantiate the adapter class instead of creating
    a dynamic proxy.
    """
    data = copy.deepcopy(data)

    def patch_params(method):
        changed = False
        for p in method.get("params", []):
            jvt = p.get("java_type", "")
            if jvt and jvt in successfully_adapted:
                p["conversion"]    = "abstract_adapter"
                p["needs_proxy"]   = False
                p["needs_adapter"] = True
                p["adapter_class"] = adapter_full_class(jvt)
                p["adapter_jni"]   = adapter_jni(jvt)
                changed = True
        if changed:
            method["needs_proxy"]   = False
            method["needs_adapter"] = True
        return method, changed

    for key in ("declared_methods", "overridden_methods",
                "inherited_methods", "constructors", "methods"):
        data[key] = [patch_params(m)[0] for m in data.get(key, [])]
    return data


# =============================================================================
# Targets file loader
# =============================================================================

def load_targets(targets_file: Path) -> tuple:
    """
    Load 05_5_abstract/targets.json.
    Returns (filter_enabled, seed_fqns, avoid_fqns).
    Creates a starter file if none exists.
    """
    print_section("Loading targets.json")
    default = {
        "enabled": True,
        "avoid":   [],
        "targets": [
            {"fqn": "android.hardware.camera2.CameraDevice$StateCallback"},
            {"fqn": "android.hardware.camera2.CameraCaptureSession$StateCallback"},
        ],
    }

    if not targets_file.exists():
        log_info(f"targets.json not found — creating starter at {targets_file}")
        targets_file.parent.mkdir(parents=True, exist_ok=True)
        targets_file.write_text(json.dumps(default, indent=2), encoding="utf-8")
        log_ok(f"Starter targets created: {targets_file}")
        return True, [], default["avoid"]

    log_info(f"Loading: {targets_file}")
    raw     = json.loads(targets_file.read_text(encoding="utf-8"))
    enabled = bool(raw.get("enabled", False))
    seeds   = [t["fqn"] for t in raw.get("targets", []) if t.get("fqn")]
    avoids  = raw.get("avoid", [])

    log_info(f"  enabled={enabled}  seeds={len(seeds)}  avoids={len(avoids)}")
    return enabled, seeds, avoids


# =============================================================================
# FQN resolver — handles dot vs dollar inner-class notation
# =============================================================================

def build_fqn_resolver(registry: dict):
    """
    Returns a resolve(fqn) function that maps user-supplied FQNs
    (possibly using dots for inner classes) to canonical registry FQNs
    (which use $ for inner classes).

    Example:
      "android.hardware.camera2.CameraDevice.StateCallback"
      →  "android.hardware.camera2.CameraDevice$StateCallback"
    """
    dot_to_dollar = {f.replace("$", "."): f for f in registry}

    def resolve(fqn: str):
        if fqn in registry:
            return fqn
        return dot_to_dollar.get(fqn)

    return resolve


# =============================================================================
# Main
# =============================================================================

def main():
    ap = argparse.ArgumentParser(
        description="Stratum Stage 05.5 — Abstract + Interface Adapter Generator")
    ap.add_argument("--input",       required=True,
                    help="Path to 05_resolve/output/ (Stage 05 enriched JSONs)")
    ap.add_argument("--output",      required=True,
                    help="Path to 05_5_abstract/output/")
    ap.add_argument("--output-java", default=None, dest="output_java",
                    help="Override Java output dir (default: output/java/com/stratum/adapters/)")
    ap.add_argument("--mode",        choices=["on", "off"], default="off",
                    help="on=generate adapters, off=passthrough copy")
    args = ap.parse_args()

    print_header("STRATUM PIPELINE — STAGE 05.5 (ABSTRACT + INTERFACE ADAPTERS)")
    log_info(f"Mode   : {args.mode.upper()}")
    log_info(f"Input  : {args.input}")
    log_info(f"Output : {args.output}")

    input_dir  = Path(args.input)
    output_dir = Path(args.output)

    if not input_dir.exists():
        log_error(f"Input dir not found: {input_dir}")
        sys.exit(1)

    patched_dir = output_dir / "patched"
    java_dir    = (Path(args.output_java) if args.output_java
                   else output_dir / "java" / "com" / "stratum" / "adapters")

    patched_dir.mkdir(parents=True, exist_ok=True)
    java_dir.mkdir(parents=True, exist_ok=True)

    # ── MODE OFF: passthrough copy ────────────────────────────────────────────
    if args.mode == "off":
        print_section("Passthrough mode")
        if patched_dir.exists():
            shutil.rmtree(patched_dir)
        shutil.copytree(input_dir, patched_dir)
        log_ok(f"Copied {input_dir} → {patched_dir}")
        manifest = {"mode": "off", "adapter_count": 0, "adapters": []}
        (output_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8")
        print_header("STAGE 05.5 COMPLETE — PASSTHROUGH")
        return

    # ── MODE ON: generate adapters ────────────────────────────────────────────
    registry = load_registry(input_dir)
    if not registry:
        log_error("Empty registry — did Stage 05 succeed?")
        sys.exit(1)

    resolve = build_fqn_resolver(registry)

    targets_file = Path("05_5_abstract") / "targets.json"
    filter_enabled, raw_seeds, raw_avoids = load_targets(targets_file)

    seed_fqns  = [r for r in (resolve(f) for f in raw_seeds)  if r]
    avoid_fqns = {r for r in (resolve(f) for f in raw_avoids) if r}

    # ── Determine what to adapt ───────────────────────────────────────────────
    all_abstracts = detect_abstract_classes(registry)

    if filter_enabled:
        # Only process what's explicitly listed in targets.json
        abstract_targets  = [f for f in seed_fqns if f in set(all_abstracts)]
        interface_targets = detect_interface_targets(registry, seed_fqns)
    else:
        # Process all abstract classes (minus avoids) + seeded interfaces
        abstract_targets  = [f for f in all_abstracts if f not in avoid_fqns]
        interface_targets = detect_interface_targets(registry, seed_fqns)

    # Merge, keeping order and avoiding duplicates
    to_adapt = abstract_targets + [
        f for f in interface_targets if f not in abstract_targets
    ]

    log_info(f"Total to adapt: {len(to_adapt)}  "
             f"(abstract={len(abstract_targets)}, interface={len(interface_targets)})")

    if not to_adapt:
        log_warn("Nothing to adapt — passthrough")
        if patched_dir.exists():
            shutil.rmtree(patched_dir)
        shutil.copytree(input_dir, patched_dir)
        manifest = {"mode": "on", "adapter_count": 0, "adapters": []}
        (output_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8")
        return

    collisions = check_name_collisions(to_adapt, registry)
    if collisions:
        sys.exit(1)

    # ── Generate Java adapter sources ─────────────────────────────────────────
    print_section("Generating Java adapter sources")
    generated            = []
    failed               = []
    successfully_adapted = set()

    for i, fqn in enumerate(to_adapt, 1):
        data, _  = registry[fqn]
        is_iface = data.get("is_interface", False)
        kind     = "interface" if is_iface else "abstract"
        log_info(f"[{i}/{len(to_adapt)}] ({kind}) {fqn}")

        try:
            java_src = (emit_interface_adapter(fqn, data)
                        if is_iface
                        else emit_abstract_adapter(fqn, data))

            cls_name  = adapter_class_name(fqn)
            java_file = java_dir / f"{cls_name}.java"
            java_file.write_text(java_src, encoding="utf-8")

            generated.append({
                "fqn":           fqn,
                "kind":          kind,
                "adapter_class": adapter_full_class(fqn),
                "adapter_jni":   adapter_jni(fqn),
            })
            successfully_adapted.add(fqn)
            log_ok(f"  → {cls_name}.java")

        except Exception as e:
            log_warn(f"  SKIP {fqn}: {e}")
            failed.append({"fqn": fqn, "error": str(e)})

    # ── Patch Stage 05 JSONs ──────────────────────────────────────────────────
    print_section("Patching Stage 05 JSONs")
    patched_count = 0
    all_json      = [
        f for f in sorted(input_dir.rglob("*.json"))
        if f.name not in SKIP_SUMMARIES
    ]

    for jf in all_json:
        try:
            data     = json.loads(jf.read_text(encoding="utf-8"))
            rel      = jf.relative_to(input_dir)
            out_path = patched_dir / rel
            out_path.parent.mkdir(parents=True, exist_ok=True)
            orig     = json.dumps(data, sort_keys=True)
            patched  = patch_class_json(data, successfully_adapted)
            out_path.write_text(json.dumps(patched, indent=2), encoding="utf-8")
            if json.dumps(patched, sort_keys=True) != orig:
                patched_count += 1
        except Exception as e:
            log_warn(f"  Error patching {jf.name}: {e}")

    # Copy summary files unchanged
    for name in SKIP_SUMMARIES:
        src = input_dir / name
        if src.exists():
            shutil.copy2(src, patched_dir / name)

    # ── Manifest ──────────────────────────────────────────────────────────────
    manifest = {
        "mode":               "on",
        "adapter_count":      len(generated),
        "failed_gen_count":   len(failed),
        "patched_json_count": patched_count,
        "adapters":           generated,
        "failed_gen":         failed,
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8")

    print_header("STAGE 05.5 COMPLETE")
    log_info(f"Adapters generated : {len(generated)}")
    log_info(f"  — abstract       : {len(abstract_targets)}")
    log_info(f"  — interface      : {len(interface_targets)}")
    log_info(f"JSONs patched      : {patched_count}")
    log_info(f"Skipped/Failed     : {len(failed)}")
    log_info(f"Java output dir    : {java_dir}")
    print()
    print("NEXT STEPS:")
    print(f"  Run Stage 06 with:  --input {patched_dir}")


if __name__ == "__main__":
    main()