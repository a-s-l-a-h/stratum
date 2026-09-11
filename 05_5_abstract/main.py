#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=============================================================================
Stratum Pipeline — Stage 05.5 : Abstract & Interface Adapter Generator
=============================================================================
LOCATION: 05_5_abstract/main.py
VERSION:  v9.2

DESCRIPTION:
    Android's SDK frequently uses abstract classes (e.g., CameraCaptureSession.
    StateCallback, CameraCaptureSession.CaptureCallback, WebViewClient) and
    multi-method interfaces (e.g., TextWatcher, SurfaceTextureListener) for
    event handling and lifecycle notifications.

    Dynamic proxies (java.lang.reflect.Proxy) can ONLY implement Java
    interfaces, and fail with an IllegalArgumentException when invoked on
    abstract classes. Furthermore, dynamic proxies cannot easily route
    arbitrary multi-method event callbacks to dynamic Python callables.

    This stage bridges that gap by:
      1. Analyzing parsed class metadata to detect abstract classes and
         multi-method interface callback targets.
      2. Generating real, compilable Java adapter source files (.java) that
         subclass the target abstract class or implement the interface.
      3. Overriding each callback method and routing its invocation back to
         the Stratum C++ engine via StratumInvocationHandler.nativeDispatch().
      4. Patching the resolved JSON corpus so subsequent pipeline stages
         (05_resolve Pass 2, 06_cpp_emit, 08_pyi_emit) assign slot tag 'a'
         (abstract adapter) instead of tag 'p' (dynamic proxy).

FIXES INCLUDED:
    - Fixed CaptureCallback exclusion: Detects and emits adapters for
      classes whose callback methods have default empty bodies rather
      than the strict `abstract` keyword (e.g. CaptureCallback, WebViewClient).
    - Fixed CharSequence / type alias collapse: Preserves concrete Java types
      (e.g., java.lang.CharSequence) directly from Stage 04 definitions.
    - Full primitive and array boxing for nativeDispatch Object[] argument arrays.
=============================================================================
"""

import argparse
import copy
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


# =============================================================================
# Package & Runtime Constants
# =============================================================================

ADAPTER_PACKAGE: str = "com.stratum.adapters"
DISPATCH_CLASS: str = "com.stratum.runtime.StratumInvocationHandler"
ADAPTER_CLASS_PREFIX: str = "Adapter_"

# Standard summary and metadata file names that should never be parsed as class JSONs
SKIP_SUMMARIES: frozenset = frozenset({
    "parse_summary.json",
    "resolve_summary.json",
    "manifest.json",
    "cpp_summary.json",
    "pyi_summary.json",
    "extract_summary.json",
    "javap_summary.json",
})

# Methods defined directly on java.lang.Object that must never be treated as interface callbacks
OBJECT_METHODS: frozenset = frozenset({
    "equals",
    "hashCode",
    "toString",
    "getClass",
    "notify",
    "notifyAll",
    "wait",
    "finalize",
    "clone",
})

# Primitive type lookup for Java signatures
PRIMITIVE_JAVA_MAP: Dict[str, str] = {
    "jboolean": "boolean",
    "jbyte": "byte",
    "jchar": "char",
    "jshort": "short",
    "jint": "int",
    "jlong": "long",
    "jfloat": "float",
    "jdouble": "double",
    "void": "void",
}

# Primitive array type lookup for Java signatures
PRIMITIVE_ARRAY_MAP: Dict[str, str] = {
    "jbooleanArray": "boolean[]",
    "jbyteArray": "byte[]",
    "jcharArray": "char[]",
    "jshortArray": "short[]",
    "jintArray": "int[]",
    "jlongArray": "long[]",
    "jfloatArray": "float[]",
    "jdoubleArray": "double[]",
}

# Primitive boxing expressions for passing values into Object[] for nativeDispatch
BOXING_EXPRESSIONS: Dict[str, str] = {
    "jboolean": "Boolean.valueOf({var})",
    "jbyte": "Byte.valueOf({var})",
    "jchar": "Character.valueOf({var})",
    "jshort": "Short.valueOf({var})",
    "jint": "Integer.valueOf({var})",
    "jlong": "Long.valueOf({var})",
    "jfloat": "Float.valueOf({var})",
    "jdouble": "Double.valueOf({var})",
}

# Default return values when Java requires a return statement in an overridden method
JAVA_DEFAULT_RETURNS: Dict[str, str] = {
    "jboolean": "return false;",
    "jbyte": "return 0;",
    "jchar": "return 0;",
    "jshort": "return 0;",
    "jint": "return 0;",
    "jlong": "return 0L;",
    "jfloat": "return 0.0f;",
    "jdouble": "return 0.0;",
    "void": "",
}

# Default zero/null literals for constructor chaining
NULL_DEFAULTS: Dict[str, str] = {
    "jboolean": "false",
    "jbyte": "0",
    "jchar": "0",
    "jshort": "0",
    "jint": "0",
    "jlong": "0L",
    "jfloat": "0.0f",
    "jdouble": "0.0",
}


# =============================================================================
# Logging Infrastructure
# =============================================================================

class Logger:
    """Provides formatted console logging with timestamps and log levels."""

    @staticmethod
    def info(msg: str) -> None:
        print(f"[INFO]  {msg}", flush=True)

    @staticmethod
    def ok(msg: str) -> None:
        print(f"[OK]    {msg}", flush=True)

    @staticmethod
    def warn(msg: str) -> None:
        print(f"[WARN]  {msg}", flush=True)

    @staticmethod
    def skip(msg: str) -> None:
        print(f"[SKIP]  {msg}", flush=True)

    @staticmethod
    def error(msg: str) -> None:
        print(f"[ERROR] {msg}", flush=True)

    @staticmethod
    def debug(msg: str) -> None:
        print(f"[DEBUG] {msg}", flush=True)

    @staticmethod
    def header(title: str) -> None:
        print("=" * 78, flush=True)
        print(f"  {title}", flush=True)
        print("=" * 78, flush=True)

    @staticmethod
    def section(title: str) -> None:
        print(f"\n--- {title} ---", flush=True)


# =============================================================================
# Name & Identifier Normalization Utilities
# =============================================================================

def fqn_to_jni(fqn: str) -> str:
    """Convert dotted FQN to slash-separated JNI class name."""
    return fqn.replace(".", "/")


def sanitize_class_name(name: str) -> str:
    """Ensure a simple class name contains only valid Java identifier characters."""
    sanitized = re.sub(r"[^a-zA-Z0-9_]", "_", name)
    if sanitized and sanitized[0].isdigit():
        sanitized = "_" + sanitized
    return sanitized


def adapter_class_name(fqn: str) -> str:
    """
    Derive the unqualified name of the generated Java adapter class.
    Example:
        android.hardware.camera2.CameraDevice$StateCallback
        -> Adapter_android_hardware_camera2_CameraDevice_StateCallback
    """
    safe_suffix = re.sub(r"[.$]", "_", fqn)
    return f"{ADAPTER_CLASS_PREFIX}{safe_suffix}"


def adapter_full_class(fqn: str) -> str:
    """
    Derive the fully qualified Java name of the generated adapter.
    Example: com.stratum.adapters.Adapter_android_view_View_OnClickListener
    """
    return f"{ADAPTER_PACKAGE}.{adapter_class_name(fqn)}"


def adapter_jni(fqn: str) -> str:
    """
    Derive the JNI internal slash path for the adapter class.
    Example: com/stratum/adapters/Adapter_android_view_View_OnClickListener
    """
    return fqn_to_jni(adapter_full_class(fqn))


def clean_generics(sig: str) -> str:
    """Remove generic type arguments (e.g. Map<String, List<Integer>> -> Map)."""
    result = []
    depth = 0
    for char in sig:
        if char == "<":
            depth += 1
        elif char == ">":
            depth = max(0, depth - 1)
        elif depth == 0:
            result.append(char)
    return "".join(result)


# =============================================================================
# Type Resolution & Conversion Helpers
# =============================================================================

def jni_to_java_type(jni_type: str, java_type_hint: str = "") -> str:
    """
    Convert a JNI type name and a Java type hint into a valid Java source type declaration.

    Design Rule:
        We trust the captured `java_type` from parsing whenever available.
        Only when `java_type` is generic or absent do we fall back to the JNI descriptor.
    """
    if jni_type in PRIMITIVE_JAVA_MAP:
        return PRIMITIVE_JAVA_MAP[jni_type]

    if jni_type in PRIMITIVE_ARRAY_MAP:
        return PRIMITIVE_ARRAY_MAP[jni_type]

    norm_hint = java_type_hint.strip() if java_type_hint else ""
    if norm_hint:
        norm_hint = norm_hint.replace("/", ".").replace("$", ".")
        norm_hint = clean_generics(norm_hint)

        if norm_hint.endswith("CharSequence"):
            return "CharSequence"

        if norm_hint.startswith("[L") and norm_hint.endswith(";"):
            base_type = norm_hint[2:-1]
            if base_type.startswith("java.lang."):
                base_type = base_type[10:]
            return f"{base_type}[]"

        if not norm_hint.startswith("["):
            if norm_hint.startswith("java.lang."):
                return norm_hint[10:]
            return norm_hint

    if jni_type == "jstring":
        return "String"
    if jni_type == "jobjectArray":
        return "Object[]"

    return "Object"


def java_return_default(jni_type: str) -> str:
    """Generate a valid Java return statement matching the expected return type."""
    return JAVA_DEFAULT_RETURNS.get(jni_type, "return null;")


def box_for_dispatch(jni_type: str, var_name: str) -> str:
    """
    Box primitive values so they can be bundled into an Object[] for nativeDispatch.
    Reference types remain unboxed.
    """
    pattern = BOXING_EXPRESSIONS.get(jni_type)
    if pattern:
        return pattern.format(var=var_name)
    return var_name


def null_default_for(param: Dict[str, Any]) -> str:
    """
    Generate an appropriate zero or null literal for constructor chaining
    when generating convenience constructors.
    """
    jni_type = param.get("jni_type", "jobject")
    return NULL_DEFAULTS.get(jni_type, "null")


# =============================================================================
# Registry & Metadata Loader
# =============================================================================

def load_registry(resolve_dir: Path) -> Dict[str, Tuple[Dict[str, Any], Path]]:
    """
    Recursively load all parsed/resolved class JSON files from the input directory.
    Returns a dictionary mapping class FQN to (json_data, file_path).
    """
    Logger.section("Loading Class Metadata Registry")
    registry: Dict[str, Tuple[Dict[str, Any], Path]] = {}
    total_found = 0
    skipped_count = 0
    error_count = 0

    for json_path in sorted(resolve_dir.rglob("*.json")):
        if json_path.name in SKIP_SUMMARIES:
            skipped_count += 1
            continue

        total_found += 1
        try:
            raw_text = json_path.read_text(encoding="utf-8")
            data = json.loads(raw_text)
            fqn = data.get("fqn", "").strip()

            if not fqn:
                skipped_count += 1
                continue

            if fqn in registry:
                Logger.warn(f"Duplicate entry for FQN '{fqn}' at {json_path.name}; keeping first")
                skipped_count += 1
                continue

            registry[fqn] = (data, json_path)

        except Exception as err:
            Logger.error(f"Failed to read/parse {json_path}: {err}")
            error_count += 1

    Logger.info(f"Loaded {len(registry):,} classes into registry (Scanned: {total_found}, Skipped: {skipped_count}, Errors: {error_count})")
    return registry


# =============================================================================
# Method Extraction & Filtering
# =============================================================================

def all_methods_of(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Collect all method dictionaries declared or referenced in a class JSON payload.
    """
    methods: List[Dict[str, Any]] = []
    seen_ids: Set[int] = set()

    for category in ("declared_methods", "overridden_methods", "inherited_methods", "methods"):
        for method_obj in data.get(category, []):
            ident = id(method_obj)
            if ident not in seen_ids:
                seen_ids.add(ident)
                methods.append(method_obj)

    return methods


def get_abstract_methods(data: Dict[str, Any], include_all_non_static: bool = False) -> List[Dict[str, Any]]:
    """
    Extract methods requiring implementation in an abstract class adapter.

    Args:
        data: Class metadata dictionary.
        include_all_non_static: If True, includes all non-static, non-private
            methods regardless of whether they have the strict `is_abstract` flag.
            This is required for callback classes (e.g., CaptureCallback, WebViewClient)
            where methods provide empty default implementations.
    """
    seen_keys: Set[str] = set()
    result: List[Dict[str, Any]] = []

    for method in all_methods_of(data):
        if method.get("is_constructor", False):
            continue
        if method.get("is_static", False):
            continue

        method_name = method.get("name", "")
        if not method_name or method_name in OBJECT_METHODS:
            continue

        is_abstract = method.get("is_abstract", False)
        if not is_abstract and not include_all_non_static:
            continue

        sig = method.get("jni_signature", "")
        dedup_key = f"{method_name}|{sig}"

        if dedup_key not in seen_keys:
            seen_keys.add(dedup_key)
            result.append(method)

    return result


def get_interface_methods(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Extract all methods from an interface that must be implemented by an adapter.
    Skips default interface methods, static methods, and java.lang.Object methods.
    """
    seen_keys: Set[str] = set()
    result: List[Dict[str, Any]] = []

    for method in all_methods_of(data):
        if method.get("is_constructor", False):
            continue
        if method.get("is_static", False):
            continue
        if method.get("is_default", False):
            continue

        method_name = method.get("name", "")
        if not method_name or method_name in OBJECT_METHODS:
            continue

        sig = method.get("jni_signature", "")
        dedup_key = f"{method_name}|{sig}"

        if dedup_key not in seen_keys:
            seen_keys.add(dedup_key)
            result.append(method)

    return result


# =============================================================================
# Target Detection
# =============================================================================

def detect_abstract_classes(registry: Dict[str, Tuple[Dict[str, Any], Path]], seed_fqns: Optional[List[str]] = None) -> List[str]:
    """
    Identify abstract classes in the registry requiring generated adapters.
    If a class is explicitly present in seed_fqns, we allow it even if its
    callback methods have default empty bodies.
    """
    Logger.section("Detecting Abstract Classes")
    seed_set = set(seed_fqns or [])
    detected: List[str] = []

    for fqn, (data, _) in registry.items():
        if not data.get("is_abstract", False):
            continue
        if data.get("is_interface", False):
            continue
        if data.get("is_annotation", False):
            continue

        is_seed = fqn in seed_set
        methods = get_abstract_methods(data, include_all_non_static=is_seed)

        if not methods:
            if is_seed:
                Logger.warn(f"Seed class {fqn} is abstract but has no candidate methods to override")
            continue

        Logger.info(f"Detected Abstract Class: {fqn} ({len(methods)} methods)")
        detected.append(fqn)

    Logger.info(f"Total abstract classes identified: {len(detected)}")
    return sorted(detected)


def detect_interface_targets(registry: Dict[str, Tuple[Dict[str, Any], Path]], seed_fqns: List[str]) -> List[str]:
    """
    Identify Java interfaces present in the seed targets that require an adapter.
    """
    Logger.section("Detecting Interface Targets from Seeds")
    detected: List[str] = []

    for fqn in seed_fqns:
        entry = registry.get(fqn)
        if not entry:
            Logger.warn(f"Target FQN not present in registry: {fqn}")
            continue

        data, _ = entry
        if data.get("is_interface", False):
            methods = get_interface_methods(data)
            if not methods:
                Logger.warn(f"Interface {fqn} contains 0 implementable methods — skipping")
                continue
            Logger.info(f"Detected Interface Callback: {fqn} ({len(methods)} methods)")
            detected.append(fqn)

    Logger.info(f"Total interface callback targets: {len(detected)}")
    return detected


# =============================================================================
# Collision Detection
# =============================================================================

def check_name_collisions(targets: List[str], registry: Dict[str, Tuple[Dict[str, Any], Path]]) -> List[str]:
    """
    Verify that generated adapter class names do not collide with each other
    or with existing classes in the Android SDK corpus.
    """
    Logger.section("Validating Adapter Name Uniqueness")
    collisions: List[str] = []
    corpus_fqns = set(registry.keys())
    generated_map: Dict[str, str] = {}

    for fqn in targets:
        cls_name = adapter_class_name(fqn)
        full_adapter_fqn = adapter_full_class(fqn)

        if full_adapter_fqn in corpus_fqns:
            msg = f"Collision: generated adapter name '{full_adapter_fqn}' matches existing class in corpus"
            Logger.error(msg)
            collisions.append(msg)

        if cls_name in generated_map:
            prev_fqn = generated_map[cls_name]
            msg = f"Collision: distinct classes '{fqn}' and '{prev_fqn}' yield identical adapter '{cls_name}'"
            Logger.error(msg)
            collisions.append(msg)
        else:
            generated_map[cls_name] = fqn

    if not collisions:
        Logger.ok(f"Validated {len(targets)} adapter names without collisions")
    return collisions


# =============================================================================
# Source Code Generation Blocks
# =============================================================================

def _build_adapter_header(fqn: str, cls_name: str, inheritance_clause: str) -> List[str]:
    """Generate the file banner, package declaration, and class opening."""
    return [
        "// Auto-generated by Stratum Stage 05.5 — DO NOT EDIT",
        f"// Source Definition: {fqn}",
        f"// Destination: runtime/java/{ADAPTER_PACKAGE.replace('.', '/')}/{cls_name}.java",
        f"package {ADAPTER_PACKAGE};",
        "",
        "import android.util.Log;",
        f"import {DISPATCH_CLASS};",
        "",
        f"public class {cls_name} {inheritance_clause} {{",
        "",
        f'    private static final String TAG = "StratumAdapter";',
        "    private final String key_;",
        "",
    ]


def _build_adapter_footer(cls_name: str) -> List[str]:
    """Generate diagnostic methods and the closing class brace."""
    return [
        "    @Override",
        "    public String toString() {",
        f'        return "{cls_name}[key=" + key_ + "]";',
        "    }",
        "",
        "}",
        "",
    ]


def _build_constructor_block(cls_name: str, ctors: List[Dict[str, Any]], has_no_arg: bool) -> List[str]:
    """
    Generate valid constructors for an abstract class adapter subclass.
    """
    lines: List[str] = []

    if has_no_arg or not ctors:
        lines.extend([
            f"    public {cls_name}(String key) {{",
            "        super();",
            "        this.key_ = key;",
            "    }",
            "",
        ])
    else:
        primary_ctor = ctors[0]
        params = primary_ctor.get("params", [])

        param_decls: List[str] = []
        forward_args: List[str] = []
        default_args: List[str] = []

        for idx, param in enumerate(params):
            param_type = jni_to_java_type(param.get("jni_type", "jobject"), param.get("java_type", ""))
            param_name = param.get("name", f"arg{idx}")

            param_decls.append(f"{param_type} {param_name}")
            forward_args.append(param_name)
            default_args.append(null_default_for(param))

        lines.extend([
            f"    public {cls_name}(String key, {', '.join(param_decls)}) {{",
            f"        super({', '.join(forward_args)});",
            "        this.key_ = key;",
            "    }",
            "",
            f"    public {cls_name}(String key) {{",
            f"        this(key, {', '.join(default_args)});",
            "    }",
            "",
        ])

    return lines


def _build_method_overrides(cls_name: str, methods: List[Dict[str, Any]], is_interface: bool) -> List[str]:
    """
    Generate overridden method stubs that dispatch calls to nativeDispatch.
    """
    lines: List[str] = []

    for method in methods:
        method_name = method.get("name", "unknown")
        params = method.get("params", [])
        return_jni = method.get("return_jni", "void")
        return_java = jni_to_java_type(return_jni, method.get("return_java_type", ""))
        return_stmt = java_return_default(return_jni)

        signature_params: List[str] = []
        boxing_elements: List[str] = []

        for idx, param in enumerate(params):
            jni_t = param.get("jni_type", "jobject")
            java_t = param.get("java_type", "")
            param_type = jni_to_java_type(jni_t, java_t)
            param_name = param.get("name", f"arg{idx}")

            # Specific adjustment: TextWatcher callback expects CharSequence, not String
            if method_name in ("beforeTextChanged", "onTextChanged") and param_type == "String" and idx == 0:
                param_type = "CharSequence"

            signature_params.append(f"{param_type} {param_name}")
            boxing_elements.append(box_for_dispatch(jni_t, param_name))

        if boxing_elements:
            args_expression = "new Object[]{ " + ", ".join(boxing_elements) + " }"
        else:
            args_expression = "new Object[0]"

        if return_java == "void":
            lines.extend([
                "    @Override",
                f"    public void {method_name}({', '.join(signature_params)}) {{",
                f"        StratumInvocationHandler.nativeDispatch(",
                f'            key_, "{method_name}", {args_expression});',
                "    }",
                "",
            ])
        else:
            # v9.1 FIX: previously the return value of nativeDispatch()
            # was discarded and a hardcoded default (return false/0/
            # null) was always returned. Any callback whose return
            # value controls behaviour — onLongClick/onTouch (consume
            # the gesture), shouldOverrideUrlLoading (intercept nav),
            # Comparator.compare — always used the default, so Python
            # code could never actually influence Java from here.
            unbox = {
                # Guards against a Python callback returning a plain int (e.g. `return 1`)
                # instead of True/False for a boolean-typed listener like onTouch/onLongClick.
                "boolean": "(__r instanceof Boolean) ? ((Boolean) __r).booleanValue() : (__r instanceof Number && ((Number) __r).intValue() != 0)",
                "int":     "((Integer) __r).intValue()",
                "long":    "((Long) __r).longValue()",
                "float":   "((Float) __r).floatValue()",
                "double":  "((Double) __r).doubleValue()",
                # Guards against Python returning a standard string ('a') instead of a char,
                # or a wrong-length string ("ab", ""), preventing ClassCastException on the UI thread.
                "char":    "(__r instanceof Character) ? ((Character) __r).charValue() : ((__r instanceof CharSequence && ((CharSequence) __r).length() > 0) ? ((CharSequence) __r).charAt(0) : '\\0')",
                "byte":    "((Byte) __r).byteValue()",
                "short":   "((Short) __r).shortValue()",
            }.get(return_java, f"({return_java}) __r")
            lines.extend([
                "    @Override",
                f"    public {return_java} {method_name}({', '.join(signature_params)}) {{",
                f"        Object __r = StratumInvocationHandler.nativeDispatch(",
                f'            key_, "{method_name}", {args_expression});',
                f"        if (__r == null) {{ {return_stmt} }}",
                f"        return {unbox};",
                "    }",
                "",
            ])

    return lines


# =============================================================================
# High-Level Adapter Emitters
# =============================================================================

def emit_abstract_adapter(fqn: str, data: Dict[str, Any]) -> str:
    """Generate Java adapter source code extending an abstract base class."""
    Logger.info(f"Generating Abstract Class Adapter: {fqn}")
    cls_name = adapter_class_name(fqn)
    abstract_methods = get_abstract_methods(data, include_all_non_static=True)
    java_fqn = fqn.replace("$", ".")

    raw_ctors = data.get("constructors", []) or [
        m for m in data.get("methods", []) if m.get("is_constructor", False)
    ]

    accessible_ctors = [
        c for c in raw_ctors
        if c.get("is_public", True) or c.get("is_protected", True)
    ]

    if raw_ctors and not accessible_ctors:
        raise RuntimeError(f"Cannot subclass {fqn}: all constructors are private or package-private")

    has_no_arg = any(len(c.get("params", [])) == 0 for c in accessible_ctors) or not accessible_ctors

    code_lines: List[str] = []
    code_lines.extend(_build_adapter_header(fqn, cls_name, f"extends {java_fqn}"))
    code_lines.extend(_build_constructor_block(cls_name, accessible_ctors, has_no_arg))
    code_lines.extend(_build_method_overrides(cls_name, abstract_methods, is_interface=False))
    code_lines.extend(_build_adapter_footer(cls_name))

    Logger.ok(f"Compiled abstract adapter: {cls_name} ({len(abstract_methods)} methods)")
    return "\n".join(code_lines)


def emit_interface_adapter(fqn: str, data: Dict[str, Any]) -> str:
    """Generate Java adapter source code implementing an interface."""
    Logger.info(f"Generating Interface Adapter: {fqn}")
    cls_name = adapter_class_name(fqn)
    interface_methods = get_interface_methods(data)
    java_fqn = fqn.replace("$", ".")

    code_lines: List[str] = []
    code_lines.extend(_build_adapter_header(fqn, cls_name, f"implements {java_fqn}"))
    code_lines.extend([
        f"    public {cls_name}(String key) {{",
        "        this.key_ = key;",
        "    }",
        "",
    ])
    code_lines.extend(_build_method_overrides(cls_name, interface_methods, is_interface=True))
    code_lines.extend(_build_adapter_footer(cls_name))

    Logger.ok(f"Compiled interface adapter: {cls_name} ({len(interface_methods)} methods)")
    return "\n".join(code_lines)


# =============================================================================
# JSON Corpus Patcher
# =============================================================================

def patch_class_json(data: Dict[str, Any], successfully_adapted: Set[str]) -> Dict[str, Any]:
    """
    Patch a single class JSON so that parameters expecting an adapted class
    are marked with `conversion: abstract_adapter` and carry adapter metadata.
    """
    data = copy.deepcopy(data)

    def patch_parameter_list(method_obj: Dict[str, Any]) -> bool:
        modified = False
        for param in method_obj.get("params", []):
            java_type = param.get("java_type", "").replace("$", ".")
            canonical_type = param.get("java_type", "")

            target_match = None
            if canonical_type in successfully_adapted:
                target_match = canonical_type
            elif java_type in successfully_adapted:
                target_match = java_type

            if target_match:
                param["conversion"] = "abstract_adapter"
                param["needs_proxy"] = False
                param["needs_adapter"] = True
                param["adapter_class"] = adapter_full_class(target_match)
                param["adapter_jni"] = adapter_jni(target_match)
                modified = True

        if modified:
            method_obj["needs_proxy"] = False
            method_obj["needs_adapter"] = True

        return modified

    for section_name in ("declared_methods", "overridden_methods", "inherited_methods", "constructors", "methods"):
        if section_name in data:
            data[section_name] = [
                m if not patch_parameter_list(m) else m
                for m in data[section_name]
            ]

    return data


# =============================================================================
# Configuration & Targets Loader
# =============================================================================

def load_targets(targets_path: Path) -> Tuple[bool, List[str], List[str]]:
    """
    Load 05_5_abstract/targets.json.
    If none exists, create a default file containing common callback targets.
    """
    Logger.section("Loading Adapter Configuration")
    default_config = {
        "enabled": True,
        "avoid": [
            "android.app.admin.NetworkEvent"
        ],
        "targets": [
            {"fqn": "android.hardware.camera2.CameraDevice$StateCallback"},
            {"fqn": "android.hardware.camera2.CameraCaptureSession$StateCallback"},
            {"fqn": "android.hardware.camera2.CameraCaptureSession$CaptureCallback"},
            {"fqn": "android.view.TextureView$SurfaceTextureListener"},
            {"fqn": "android.view.SurfaceHolder$Callback"},
            {"fqn": "android.widget.SeekBar$OnSeekBarChangeListener"},
            {"fqn": "android.widget.AdapterView$OnItemSelectedListener"},
            {"fqn": "android.text.TextWatcher"},
            {"fqn": "android.content.DialogInterface$OnClickListener"},
            {"fqn": "android.content.DialogInterface$OnDismissListener"},
            {"fqn": "android.content.DialogInterface$OnCancelListener"},
            {"fqn": "android.hardware.SensorEventListener"},
            {"fqn": "android.location.LocationListener"},
            {"fqn": "android.media.ImageReader$OnImageAvailableListener"},
            {"fqn": "android.webkit.WebViewClient"},
            {"fqn": "android.webkit.WebChromeClient"},
            {"fqn": "android.view.View$OnLongClickListener"},
        ],
    }

    if not targets_path.exists():
        Logger.info(f"Target configuration not found. Creating default: {targets_path}")
        targets_path.parent.mkdir(parents=True, exist_ok=True)
        targets_path.write_text(json.dumps(default_config, indent=2), encoding="utf-8")
        seeds = [t["fqn"] for t in default_config["targets"] if t.get("fqn")]
        return True, seeds, default_config["avoid"]

    Logger.info(f"Reading target configuration from: {targets_path}")
    content = json.loads(targets_path.read_text(encoding="utf-8"))
    enabled = bool(content.get("enabled", False))
    seeds = [t["fqn"] for t in content.get("targets", []) if t.get("fqn")]
    avoids = content.get("avoid", [])

    Logger.info(f"Configuration loaded: enabled={enabled}, seeds={len(seeds)}, avoid={len(avoids)}")
    return enabled, seeds, avoids


def build_fqn_normalizer(registry: Dict[str, Any]):
    """
    Create a mapping helper to resolve inner-class notation differences
    (e.g., matching 'android.view.View.OnClickListener' to 'android.view.View$OnClickListener').
    """
    alias_map: Dict[str, str] = {}
    for canonical_fqn in registry:
        dotted = canonical_fqn.replace("$", ".")
        alias_map[canonical_fqn] = canonical_fqn
        alias_map[dotted] = canonical_fqn

    def resolve(name: str) -> Optional[str]:
        return alias_map.get(name)

    return resolve


# =============================================================================
# Main Program Pipeline
# =============================================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Stratum Pipeline Stage 05.5 — Abstract & Interface Adapter Generator"
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Directory containing Stage 05 Pass 1 JSON files (05_resolve/output/)"
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Root output directory for Stage 05.5 (05_5_abstract/output/)"
    )
    parser.add_argument(
        "--output-java",
        default=None,
        dest="output_java",
        help="Override output directory for generated .java files"
    )
    parser.add_argument(
        "--mode",
        choices=["on", "off"],
        default="off",
        help="Set to 'on' to generate adapters; 'off' executes a passthrough copy"
    )
    args = parser.parse_args()

    start_timestamp = time.time()
    Logger.header("STRATUM PIPELINE — STAGE 05.5 (ADAPTER GENERATION)")
    Logger.info(f"Execution Mode : {args.mode.upper()}")
    Logger.info(f"Input Path     : {args.input}")
    Logger.info(f"Output Path    : {args.output}")

    input_dir = Path(args.input)
    output_dir = Path(args.output)

    if not input_dir.exists():
        Logger.error(f"Input directory does not exist: {input_dir}")
        sys.exit(1)

    patched_dir = output_dir / "patched"
    if args.output_java:
        java_out_dir = Path(args.output_java)
    else:
        java_out_dir = output_dir / "java" / "com" / "stratum" / "adapters"

    patched_dir.mkdir(parents=True, exist_ok=True)
    java_out_dir.mkdir(parents=True, exist_ok=True)

    # ── PASSTHROUGH MODE ──────────────────────────────────────────────────────
    if args.mode == "off":
        Logger.section("Passthrough Mode Active")
        Logger.info("Copying input files without modifications...")
        if patched_dir.exists():
            shutil.rmtree(patched_dir)
        shutil.copytree(input_dir, patched_dir)

        manifest = {
            "mode": "off",
            "adapter_count": 0,
            "adapters": [],
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        Logger.header("STAGE 05.5 COMPLETE (PASSTHROUGH)")
        return

    # ── ADAPTER GENERATION MODE ───────────────────────────────────────────────
    registry = load_registry(input_dir)
    if not registry:
        Logger.error("Class registry is empty. Please verify Stage 05 output.")
        sys.exit(1)

    normalizer = build_fqn_normalizer(registry)
    targets_config_path = Path("05_5_abstract") / "targets.json"
    filter_enabled, raw_seeds, raw_avoids = load_targets(targets_config_path)

    resolved_seeds: List[str] = []
    for raw in raw_seeds:
        norm = normalizer(raw)
        if norm:
            resolved_seeds.append(norm)
        else:
            Logger.warn(f"Target seed could not be resolved in registry: {raw}")

    resolved_avoids: Set[str] = set()
    for raw in raw_avoids:
        norm = normalizer(raw)
        if norm:
            resolved_avoids.add(norm)

    all_abstract_candidates = detect_abstract_classes(registry, resolved_seeds)

    if filter_enabled:
        abstract_targets = [fqn for fqn in resolved_seeds if fqn in set(all_abstract_candidates)]
        interface_targets = detect_interface_targets(registry, resolved_seeds)
    else:
        abstract_targets = [fqn for fqn in all_abstract_candidates if fqn not in resolved_avoids]
        interface_targets = detect_interface_targets(registry, resolved_seeds)

    # Combine unique targets while preserving ordering
    combined_targets: List[str] = []
    for fqn in abstract_targets + interface_targets:
        if fqn not in combined_targets:
            combined_targets.append(fqn)

    Logger.info(f"Selected {len(combined_targets)} classes for adapter generation "
                f"(Abstract: {len(abstract_targets)}, Interface: {len(interface_targets)})")

    if not combined_targets:
        Logger.warn("No targets selected. Operating as passthrough.")
        if patched_dir.exists():
            shutil.rmtree(patched_dir)
        shutil.copytree(input_dir, patched_dir)
        manifest = {
            "mode": "on",
            "adapter_count": 0,
            "adapters": [],
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return

    collision_errors = check_name_collisions(combined_targets, registry)
    if collision_errors:
        Logger.error("Aborting due to name collisions.")
        sys.exit(1)

    # ── GENERATE JAVA FILES ───────────────────────────────────────────────────
    Logger.section("Emitting Java Source Files")
    generated_records: List[Dict[str, str]] = []
    failed_records: List[Dict[str, str]] = []
    successfully_adapted: Set[str] = set()

    for idx, fqn in enumerate(combined_targets, 1):
        data, _ = registry[fqn]
        is_iface = data.get("is_interface", False)
        kind = "interface" if is_iface else "abstract"

        Logger.info(f"[{idx}/{len(combined_targets)}] ({kind.upper()}) {fqn}")

        try:
            if is_iface:
                java_source = emit_interface_adapter(fqn, data)
            else:
                java_source = emit_abstract_adapter(fqn, data)

            cls_name = adapter_class_name(fqn)
            target_java_file = java_out_dir / f"{cls_name}.java"
            target_java_file.write_text(java_source, encoding="utf-8")

            generated_records.append({
                "fqn": fqn,
                "kind": kind,
                "adapter_class": adapter_full_class(fqn),
                "adapter_jni": adapter_jni(fqn),
                "file": target_java_file.name,
            })
            successfully_adapted.add(fqn)
            Logger.ok(f"  -> Generated: {target_java_file.name}")

        except Exception as err:
            Logger.warn(f"  Failed generating adapter for {fqn}: {err}")
            failed_records.append({"fqn": fqn, "error": str(err)})

    # ── PATCH RESOLVED JSONS ──────────────────────────────────────────────────
    Logger.section("Patching Stage 05 JSON Files")
    patched_count = 0
    all_json_files = [
        p for p in sorted(input_dir.rglob("*.json"))
        if p.name not in SKIP_SUMMARIES
    ]

    for json_file in all_json_files:
        try:
            raw_data = json.loads(json_file.read_text(encoding="utf-8"))
            rel_path = json_file.relative_to(input_dir)
            out_file = patched_dir / rel_path
            out_file.parent.mkdir(parents=True, exist_ok=True)

            original_repr = json.dumps(raw_data, sort_keys=True)
            patched_data = patch_class_json(raw_data, successfully_adapted)
            new_repr = json.dumps(patched_data, sort_keys=True)

            out_file.write_text(json.dumps(patched_data, indent=2), encoding="utf-8")
            if original_repr != new_repr:
                patched_count += 1

        except Exception as err:
            Logger.warn(f"Error patching {json_file.name}: {err}")

    # Copy summaries forward to the patched directory
    for summary_name in SKIP_SUMMARIES:
        source_summary = input_dir / summary_name
        if source_summary.exists():
            shutil.copy2(source_summary, patched_dir / summary_name)

    # ── MANIFEST & SUMMARY ────────────────────────────────────────────────────
    manifest_data = {
        "mode": "on",
        "generated_count": len(generated_records),
        "failed_count": len(failed_records),
        "patched_json_count": patched_count,
        "adapters": generated_records,
        "failures": failed_records,
        "elapsed_seconds": round(time.time() - start_timestamp, 2),
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest_data, indent=2), encoding="utf-8")

    Logger.header("STAGE 05.5 EXECUTION SUMMARY")
    Logger.info(f"Adapters Generated : {len(generated_records)}")
    Logger.info(f"  - Abstract       : {len([r for r in generated_records if r['kind'] == 'abstract'])}")
    Logger.info(f"  - Interface      : {len([r for r in generated_records if r['kind'] == 'interface'])}")
    Logger.info(f"JSON Files Patched : {patched_count}")
    Logger.info(f"Failures / Skips   : {len(failed_records)}")
    Logger.info(f"Java Source Dir    : {java_out_dir.resolve()}")
    Logger.info(f"Patched JSON Dir   : {patched_dir.resolve()}")
    Logger.ok(f"Stage 05.5 completed successfully in {manifest_data['elapsed_seconds']}s")


if __name__ == "__main__":
    main()