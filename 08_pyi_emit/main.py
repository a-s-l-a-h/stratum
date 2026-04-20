#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Stratum Pipeline — Stage 08 : Emit Python .pyi type stubs
==========================================================
  05_resolve/output/      <- Stage 05 enriched JSON (one file per class)
        |
        v
  08_pyi_emit/main.py   <- THIS FILE
        |
        v
  08_pyi_emit/output/   <- .pyi stub files, one per class + __init__.pyi per package
"""

import argparse
import json
import re
import sys
from pathlib import Path


# =============================================================================
# Utilities
# =============================================================================

def print_header(title: str) -> None:
    print("==================================================")
    print(f" {title}")
    print("==================================================")


def sanitize_id(s: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_]", "_", s)
    if s and s[0].isdigit():
        s = "_" + s
    s = re.sub(r"_+", "_", s).strip("_") or "_unknown"
    if len(s) == 1:
        s = "gen_" + s
    return s


def safe_class_name(simple_name: str) -> str:
    return sanitize_id(simple_name)


def fqn_to_module_path(fqn: str) -> str:
    parts = fqn.split(".")
    parts[-1] = sanitize_id(parts[-1])
    return "/".join(parts)


# =============================================================================
# Type mapping helpers (UPDATED for Stage 06 sync)
# =============================================================================

def python_type_for_param(p: dict) -> str:
    conv = p.get("conversion", "")
    if conv == "callable_to_proxy":
        return "Callable[..., None]"
    if conv in ("string_in", "string_out"):
        return "str"
    if conv in ("bool_in", "bool_out"):
        return "bool"
    
    py = p.get("python_type", "")
    if py in ("str", "bool", "int", "float", "None", "list"):
        return py

    java_type = p.get("java_type", "")
    if java_type and "." in java_type:
        simple_name = safe_class_name(java_type.split(".")[-1])
        # [Action 42] Wrap in Optional if nullable
        if p.get("nullable", True):
            return f"Optional['{simple_name}']"
        return f"'{simple_name}'"

    return "object"


def python_return_type(m: dict) -> str:
    ret_conv = m.get("return_conversion", "none")
    ret_py   = m.get("return_python", "")

    if ret_conv == "string_out":
        return "str"
    if ret_conv == "bool_out":
        return "bool"
    if ret_conv == "none" and m.get("is_void", False):
        return "None"
    if m.get("return_jni", "") == "void":
        return "None"

    if ret_py in ("str", "bool", "int", "float", "None", "list"):
        return ret_py

    sig = m.get("jni_signature", "")
    if ")" in sig:
        ret_sig = sig.split(")")[-1]
        if ret_sig.startswith("L") and ret_sig.endswith(";"):
            java_type = ret_sig[1:-1].replace("/", ".")
            # [Action 41] Direct ByteBuffer typing
            if java_type == "java.nio.ByteBuffer":
                return "Union[bytes, memoryview]"
            simple_name = safe_class_name(java_type.split(".")[-1])
            return f"'{simple_name}'"

    return "object"


# Helper to format a single parameter handling varargs [Action 38]
def format_param(p: dict) -> str:
    pname = sanitize_id(p.get("name", f"arg{p.get('index', 0)}"))
    ptype = python_type_for_param(p)
    if p.get("is_varargs", False):
        return f"*{pname}: {ptype}"
    return f"{pname}: {ptype}"


# =============================================================================
# Collect methods from Stage-05 enriched JSON
# =============================================================================

def collect_methods(cls: dict) -> tuple[list, list, list, list]:
    constructors       = cls.get("constructors",       [])
    declared_methods   = cls.get("declared_methods",   [])
    overridden_methods = cls.get("overridden_methods", [])
    inherited_methods  = cls.get("inherited_methods",  [])

    has_resolved = any([constructors, declared_methods,
                        overridden_methods, inherited_methods])

    if not has_resolved:
        raw = cls.get("methods", [])
        constructors = [m for m in raw if m.get("is_constructor")]
        non_ctor     = [m for m in raw if not m.get("is_constructor")]
        static_methods   = [m for m in non_ctor if m.get("is_static")]
        instance_methods = [m for m in non_ctor if not m.get("is_static")]
        return constructors, instance_methods, static_methods, []

    all_non_ctor = declared_methods + overridden_methods
    static_methods   = [m for m in all_non_ctor if m.get("is_static")]
    instance_methods = [m for m in all_non_ctor if not m.get("is_static")]

    return constructors, instance_methods, static_methods, inherited_methods


def sig_key(method_name: str, params: list) -> str:
    types = ",".join(python_type_for_param(p) for p in params)
    return f"{method_name}({types})"


# =============================================================================
# Emit one class stub
# =============================================================================

def emit_class_pyi(cls: dict) -> str:
    fqn         = cls.get("fqn", "")
    simple_raw  = cls.get("simple_name", fqn.split(".")[-1])
    py_cls_name = safe_class_name(simple_raw)

    parent_fqn    = cls.get("parent_fqn", "")
    parent_simple = ""
    parent_import = ""

    if parent_fqn and parent_fqn not in ("java.lang.Object", ""):
        parent_raw    = parent_fqn.split(".")[-1]
        parent_simple = safe_class_name(parent_raw)
        parent_pkg    = ".".join(parent_fqn.split(".")[:-1])
        parent_file   = sanitize_id(parent_raw)
        parent_import = f"from stratum.{parent_pkg}.{parent_file} import {parent_simple}"

    constructors, inst_methods, static_methods, inherited = collect_methods(cls)

    lines: list[str] = []

    lines.append(f"# {fqn}")
    lines.append(f"# Auto-generated by Stratum Stage 08 — DO NOT EDIT")
    lines.append(f"")
    lines.append(f"from __future__ import annotations")
    lines.append(f"from typing import Callable, Optional, List, Union")
    lines.append(f"")

    if parent_import:
        lines.append(parent_import)
        lines.append(f"")

    if parent_simple:
        lines.append(f"class {py_cls_name}({parent_simple}):")
    else:
        lines.append(f"class {py_cls_name}:")

    body_lines: list[str] = []
    seen_ctor_sigs: set[str] = set()

    if not constructors:
        body_lines.append(f"    def __init__(self) -> None: ...")
    elif len(constructors) == 1:
        ctor   = constructors[0]
        params = ctor.get("params", [])
        parts  = ["self"] + [format_param(p) for p in params]
        body_lines.append(f"    def __init__({', '.join(parts)}) -> None: ...")
    else:
        body_lines.append(f"    from typing import overload")
        for ctor in constructors:
            params = ctor.get("params", [])
            sk = sig_key("__init__", params)
            if sk in seen_ctor_sigs:
                continue
            seen_ctor_sigs.add(sk)
            parts = ["self"] + [format_param(p) for p in params]
            body_lines.append(f"    @overload")
            body_lines.append(f"    def __init__({', '.join(parts)}) -> None: ...")

    body_lines.append(f"")

    seen_inst: set[str] = set()
    inst_names: set[str] = set()

    for m in inst_methods:
        raw_name = m.get("name", "unknown")
        mname    = sanitize_id(raw_name)
        params   = m.get("params", [])
        ret      = python_return_type(m)

        sk = sig_key(mname, params)
        if sk in seen_inst:
            continue
        seen_inst.add(sk)
        inst_names.add(mname)

        parts = ["self"] + [format_param(p) for p in params]
        body_lines.append(f"    def {mname}({', '.join(parts)}) -> {ret}: ...")

    seen_inh: set[str] = set(seen_inst)
    for m in inherited:
        raw_name = m.get("name", "unknown")
        mname    = sanitize_id(raw_name)
        if m.get("is_static"):
            continue
        
        inst_names.add(mname)
        
        params = m.get("params", [])
        ret    = python_return_type(m)

        sk = sig_key(mname, params)
        if sk in seen_inh:
            continue
        seen_inh.add(sk)

        parts = ["self"] + [format_param(p) for p in params]
        declaring = m.get("declaring_class", "")
        comment   = f"  # inherited from {declaring}" if declaring else "  # inherited"
        body_lines.append(f"    def {mname}({', '.join(parts)}) -> {ret}: ...{comment}")

    seen_static: set[str] = set()
    for m in static_methods:
        raw_name = m.get("name", "unknown")
        mname    = sanitize_id(raw_name) + "_static"

        params = m.get("params", [])
        ret    = python_return_type(m)

        sk = sig_key(mname, params)
        if sk in seen_static:
            continue
        seen_static.add(sk)

        parts = [format_param(p) for p in params]
        body_lines.append(f"    @staticmethod")
        body_lines.append(f"    def {mname}({', '.join(parts)}) -> {ret}: ...")

    for f in cls.get("fields", []):
        fname = sanitize_id(f.get("name", "UNKNOWN"))
        # [Action 37] jchar maps to str now
        ftype = {
            "jboolean": "bool", "jbyte": "int", "jchar": "str", "jshort": "int",
            "jint": "int", "jlong": "int", "jfloat": "float", "jdouble": "float",
            "jstring": "str"
        }.get(f.get("jni_type", ""), "object")
        
        is_static = f.get("is_static", False)
        is_final = f.get("is_final", False)
        prefix = "sf" if is_static else "f"

        if is_static:
            body_lines.append(f"    @staticmethod")
            body_lines.append(f"    def {prefix}_get_{fname}() -> {ftype}: ...")
            if not is_final:
                body_lines.append(f"    @staticmethod")
                body_lines.append(f"    def {prefix}_set_{fname}(val: {ftype}) -> None: ...")
        else:
            body_lines.append(f"    def {prefix}_get_{fname}(self) -> {ftype}: ...")
            if not is_final:
                body_lines.append(f"    def {prefix}_set_{fname}(self, val: {ftype}) -> None: ...")
    
    body_lines.append(f"")
    body_lines.append(f"    @staticmethod")
    body_lines.append(f"    def _stratum_cast(obj: object) -> Optional['{py_cls_name}']: ...")
    body_lines.append(f"    def _get_jobject_ptr(self) -> int: ...")

    non_empty = [l for l in body_lines if l.strip()]
    if not non_empty:
        body_lines.append(f"    ...")

    lines.extend(body_lines)
    lines.append(f"")
    return "\n".join(lines)


def emit_package_init(safe_simple_names: list[str]) -> str:
    lines = ["# Auto-generated by Stratum Stage 08 — DO NOT EDIT", ""]
    for name in sorted(set(safe_simple_names)):
        lines.append(f"from .{name} import {name}")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Stratum Stage 08 — Emit .pyi stubs from Stage 05 resolve output"
    )
    ap.add_argument("--input",  required=True,
                    help="Path to 05_resolve/output/")
    ap.add_argument("--output", required=True,
                    help="Path to 08_pyi_emit/output/")
    args = ap.parse_args()

    print_header("STRATUM PIPELINE - STAGE 08 (PYI EMIT)")

    input_dir  = Path(args.input)
    output_dir = Path(args.output)

    if not input_dir.exists():
        print(f"ERROR: Input not found: {input_dir}")
        sys.exit(1)

    json_files = sorted(
        f for f in input_dir.rglob("*.json")
        if f.name not in ("parse_summary.json", "resolve_summary.json", "cpp_summary.json")
    )
    if not json_files:
        print("ERROR: No JSON files found. Did Stage 05 succeed?")
        sys.exit(1)

    print(f"-> Found {len(json_files)} class JSON files")

    all_classes: list[tuple[Path, dict]] = []
    for jf in json_files:
        try:
            cls = json.loads(jf.read_text(encoding="utf-8"))
            if cls.get("fqn"):
                all_classes.append((jf, cls))
        except Exception as e:
            print(f"  WARN  {jf.name}: {e}")

    packages: dict[str, list[str]] = {}
    output_dir.mkdir(parents=True, exist_ok=True)
    failed: list[dict] = []
    total = len(all_classes)

    for i, (jf, cls) in enumerate(all_classes, 1):
        try:
            fqn        = cls["fqn"]
            simple_raw = cls.get("simple_name", fqn.split(".")[-1])
            py_name    = safe_class_name(simple_raw)

            pkg_parts  = fqn.split(".")[:-1]
            pkg_key    = "/".join(pkg_parts)
            packages.setdefault(pkg_key, []).append(py_name)

            pyi_path = output_dir / Path(*pkg_parts) / f"{py_name}.pyi"
            pyi_path.parent.mkdir(parents=True, exist_ok=True)

            pyi_text = emit_class_pyi(cls)
            pyi_path.write_text(pyi_text, encoding="utf-8")

        except Exception as e:
            failed.append({"file": str(jf), "error": str(e)})
            print(f"  [{i:4d}/{total}] FAIL  {jf.name}  ->  {e}")

    for pkg_key, names in packages.items():
        init_path = output_dir / Path(pkg_key) / "__init__.pyi"
        init_path.write_text(emit_package_init(names), encoding="utf-8")

    # [Actions 39 & 40] Write Top-level android/__init__.pyi with Base classes
    top_init = output_dir / "android" / "__init__.pyi"
    top_init.parent.mkdir(parents=True, exist_ok=True)
    
    top_init_content = """# Auto-generated by Stratum Stage 08 — DO NOT EDIT
from typing import Optional

class StratumObject:
    def is_null(self) -> bool: ...
    def to_string(self) -> str: ...
    def hash_code(self) -> int: ...
    def instanceof_check(self, jni_class_name: str) -> bool: ...
    def class_name(self) -> str: ...

class StratumThrowable(StratumObject):
    def get_message(self) -> str: ...
    def get_class_name(self) -> str: ...

class StratumWeakObject:
    def get(self) -> Optional[StratumObject]: ...
    def is_enqueued(self) -> bool: ...

class StratumSurface(StratumObject):
    def has_window(self) -> bool: ...
"""
    top_init.write_text(top_init_content, encoding="utf-8")

    summary = {
        "stage":        "08_pyi_emit",
        "input_dir":    str(input_dir),
        "output_dir":   str(output_dir),
        "total_stubs":  total - len(failed),
        "total_failed": len(failed),
        "packages":     sorted(packages.keys()),
        "failed_files": failed,
    }
    (output_dir / "pyi_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )

    print()
    print_header("STAGE 08 COMPLETE")
    print(f"-> Stubs emitted : {total - len(failed):,} / {total:,}")
    print(f"-> Packages      : {len(packages):,}")
    print(f"-> Failed        : {len(failed):,}")
    print(f"-> Output        : {output_dir}")

if __name__ == "__main__":
    main()