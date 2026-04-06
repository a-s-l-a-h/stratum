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

Usage:
    python 08_pyi_emit/main.py --input "05_resolve/output/" --output "08_pyi_emit/output/"

Changes from the old Stage 07 (which read 04_parse output):
  - Reads Stage 05 enriched JSON, not Stage 04 raw JSON.
  - Methods are taken from declared_methods + overridden_methods + inherited_methods
    + constructors (the four resolve-stage lists).  The raw `methods` list is used
    as a fallback only if none of the resolved lists are present.
  - Inner-class $-names are sanitised to _ for Python identifiers everywhere:
    class names, filenames, import paths.
  - Parent import uses parent_fqn (always correct in Stage 05 JSON) rather than
    trying to look up simple_name in a side-table.
  - Duplicate method signatures (same name + same param types) are deduplicated
    per-class so the stub is always valid Python.
  - All identifier sanitisation goes through one central sanitize_id() so there
    is no mismatch between how names appear in stubs vs imports.
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
    """
    Convert any Java identifier / FQN fragment to a safe Python identifier.
    Rules:
      - Replace every non-alphanumeric, non-underscore character with '_'
      - Prepend '_' if the result starts with a digit
      - Collapse runs of '_' to a single '_'
      - Strip leading/trailing '_'
      - Guarantee minimum length of 1; prefix 'gen_' when result is a single char
        (avoids clashes with single-letter builtins like 'T', 'V' …)
    """
    s = re.sub(r"[^a-zA-Z0-9_]", "_", s)
    if s and s[0].isdigit():
        s = "_" + s
    s = re.sub(r"_+", "_", s).strip("_") or "_unknown"
    if len(s) == 1:
        s = "gen_" + s
    return s


def safe_class_name(simple_name: str) -> str:
    """Java simple name (may contain '$') → Python class identifier."""
    return sanitize_id(simple_name)


def fqn_to_module_path(fqn: str) -> str:
    """
    android.widget.LinearLayout$LayoutParams
      → android/widget/LinearLayout_LayoutParams   (path fragment)
    Used for file placement and import construction.
    """
    parts = fqn.split(".")
    parts[-1] = sanitize_id(parts[-1])          # sanitise the class-name segment
    return "/".join(parts)


# =============================================================================
# Type mapping helpers
# =============================================================================

# =============================================================================
# Type mapping helpers (UPDATED)
# =============================================================================

def python_type_for_param(p: dict) -> str:
    """Return the Python type annotation string for a method parameter dict."""
    conv = p.get("conversion", "")
    if conv == "callable_to_proxy":
        return "Callable[..., None]"
    if conv in ("string_in", "string_out"):
        return "str"
    if conv in ("bool_in", "bool_out"):
        return "bool"
    
    # Check explicitly mapped python primitives first
    py = p.get("python_type", "")
    if py in ("str", "bool", "int", "float", "None", "list"):
        return py

    # If it's a known Java FQN (e.g., android.content.Context), return it as a Forward Reference string
    java_type = p.get("java_type", "")
    if java_type and "." in java_type:
        simple_name = safe_class_name(java_type.split(".")[-1])
        return f"'{simple_name}'"  # Wrap in quotes to avoid circular import errors

    return "object"


def python_return_type(m: dict) -> str:
    """Return the Python return-type annotation string for a method dict."""
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

    # Extract return Java type
    sig = m.get("jni_signature", "")
    if ")" in sig:
        ret_sig = sig.split(")")[-1]
        if ret_sig.startswith("L") and ret_sig.endswith(";"):
            java_type = ret_sig[1:-1].replace("/", ".")
            simple_name = safe_class_name(java_type.split(".")[-1])
            return f"'{simple_name}'"

    return "object"


# =============================================================================
# Collect methods from Stage-05 enriched JSON
# =============================================================================

def collect_methods(cls: dict) -> tuple[list, list, list, list]:
    """
    Returns (constructors, instance_methods, static_methods, inherited_methods).

    Stage 05 splits methods into four lists:
        constructors        — enriched constructor dicts
        declared_methods    — new methods declared on this class
        overridden_methods  — methods that override an ancestor
        inherited_methods   — methods inherited without override

    If none of those lists exist (raw Stage-04 fallback), we fall back to the
    raw `methods` list and split it manually.
    """
    # ── Prefer Stage-05 lists ─────────────────────────────────────────────────
    constructors       = cls.get("constructors",       [])
    declared_methods   = cls.get("declared_methods",   [])
    overridden_methods = cls.get("overridden_methods", [])
    inherited_methods  = cls.get("inherited_methods",  [])

    has_resolved = any([constructors, declared_methods,
                        overridden_methods, inherited_methods])

    if not has_resolved:
        # ── Stage-04 fallback (raw `methods` list) ────────────────────────────
        raw = cls.get("methods", [])
        constructors = [m for m in raw if m.get("is_constructor")]
        non_ctor     = [m for m in raw if not m.get("is_constructor")]
        static_methods   = [m for m in non_ctor if m.get("is_static")]
        instance_methods = [m for m in non_ctor if not m.get("is_static")]
        return constructors, instance_methods, static_methods, []

    # ── Normal Stage-05 path ──────────────────────────────────────────────────
    all_non_ctor = declared_methods + overridden_methods
    static_methods   = [m for m in all_non_ctor if m.get("is_static")]
    instance_methods = [m for m in all_non_ctor if not m.get("is_static")]

    return constructors, instance_methods, static_methods, inherited_methods


# =============================================================================
# Signature key — used for deduplication
# =============================================================================

def sig_key(method_name: str, params: list) -> str:
    """Stable dedup key: name + ordered param python types."""
    types = ",".join(python_type_for_param(p) for p in params)
    return f"{method_name}({types})"


# =============================================================================
# Emit one class stub
# =============================================================================

def emit_class_pyi(cls: dict) -> str:
    fqn         = cls.get("fqn", "")
    simple_raw  = cls.get("simple_name", fqn.split(".")[-1])
    py_cls_name = safe_class_name(simple_raw)

    # ── Parent ────────────────────────────────────────────────────────────────
    parent_fqn    = cls.get("parent_fqn", "")          # e.g. android.view.ViewGroup
    parent_simple = ""
    parent_import = ""

    if parent_fqn and parent_fqn not in ("java.lang.Object", ""):
        parent_raw    = parent_fqn.split(".")[-1]       # may contain $
        parent_simple = safe_class_name(parent_raw)
        parent_pkg    = ".".join(parent_fqn.split(".")[:-1])
        parent_file   = sanitize_id(parent_raw)         # filename segment
        parent_import = f"from stratum.{parent_pkg}.{parent_file} import {parent_simple}"

    # ── Collect methods ───────────────────────────────────────────────────────
    constructors, inst_methods, static_methods, inherited = collect_methods(cls)

    # ── Build file ────────────────────────────────────────────────────────────
    lines: list[str] = []

    lines.append(f"# {fqn}")
    lines.append(f"# Auto-generated by Stratum Stage 08 — DO NOT EDIT")
    lines.append(f"")
    lines.append(f"from __future__ import annotations")
    lines.append(f"from typing import Callable, Optional, List")
    lines.append(f"")

    if parent_import:
        lines.append(parent_import)
        lines.append(f"")

    # Class declaration
    if parent_simple:
        lines.append(f"class {py_cls_name}({parent_simple}):")
    else:
        lines.append(f"class {py_cls_name}:")

    body_lines: list[str] = []

    # ── Constructor(s) ────────────────────────────────────────────────────────
    # If there are multiple constructors we emit them as overloads.
    # If there are zero we emit a default __init__.
    seen_ctor_sigs: set[str] = set()

    if not constructors:
        body_lines.append(f"    def __init__(self) -> None: ...")
    elif len(constructors) == 1:
        ctor   = constructors[0]
        params = ctor.get("params", [])
        parts  = ["self"]
        for p in params:
            pname = sanitize_id(p.get("name", f"arg{p.get('index',0)}"))
            ptype = python_type_for_param(p)
            parts.append(f"{pname}: {ptype}")
        body_lines.append(f"    def __init__({', '.join(parts)}) -> None: ...")
    else:
        # Multiple constructors → use @overload
        body_lines.append(f"    from typing import overload")
        for ctor in constructors:
            params = ctor.get("params", [])
            sk = sig_key("__init__", params)
            if sk in seen_ctor_sigs:
                continue
            seen_ctor_sigs.add(sk)
            parts = ["self"]
            for p in params:
                pname = sanitize_id(p.get("name", f"arg{p.get('index',0)}"))
                ptype = python_type_for_param(p)
                parts.append(f"{pname}: {ptype}")
            body_lines.append(f"    @overload")
            body_lines.append(f"    def __init__({', '.join(parts)}) -> None: ...")

    body_lines.append(f"")

    # ── Instance methods ──────────────────────────────────────────────────────
    seen_inst: set[str] = set()
    inst_names: set[str] = set()   # track names that exist as instance methods

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

        parts = ["self"]
        for p in params:
            pname = sanitize_id(p.get("name", f"arg{p.get('index', 0)}"))
            ptype = python_type_for_param(p)
            parts.append(f"{pname}: {ptype}")

        body_lines.append(f"    def {mname}({', '.join(parts)}) -> {ret}: ...")

    # ── Inherited methods (emit only what is not already declared) ────────────
    # We include inherited methods so IDE autocompletion works on the stub
    # even when the parent stub is not present.  We mark them with a comment.
    # ── Inherited methods (emit only what is not already declared) ────────────
    # We include inherited methods so IDE autocompletion works on the stub
    # even when the parent stub is not present.  We mark them with a comment.
    seen_inh: set[str] = set(seen_inst)   # don't re-emit declared ones
    for m in inherited:
        raw_name = m.get("name", "unknown")
        mname    = sanitize_id(raw_name)
        if m.get("is_static"):
            continue                          # inherited statics omitted — noisy
        
        inst_names.add(mname)  # [FIX-32] Prevent static collision in .pyi
        
        params = m.get("params", [])
        ret    = python_return_type(m)

        sk = sig_key(mname, params)
        if sk in seen_inh:
            continue
        seen_inh.add(sk)

        parts = ["self"]
        for p in params:
            pname = sanitize_id(p.get("name", f"arg{p.get('index', 0)}"))
            ptype = python_type_for_param(p)
            parts.append(f"{pname}: {ptype}")

        declaring = m.get("declaring_class", "")
        comment   = f"  # inherited from {declaring}" if declaring else "  # inherited"
        body_lines.append(f"    def {mname}({', '.join(parts)}) -> {ret}: ...{comment}")

    # ── Static methods ────────────────────────────────────────────────────────

# ── Static methods ────────────────────────────────────────────────────────
    seen_static: set[str] = set()
    for m in static_methods:
        raw_name = m.get("name", "unknown")
        mname    = sanitize_id(raw_name) + "_static"  # [FIX-33] Match C++ exactly

        params = m.get("params", [])
        ret    = python_return_type(m)

        sk = sig_key(mname, params)
        if sk in seen_static:
            continue
        seen_static.add(sk)

        parts = []
        for p in params:
            pname = sanitize_id(p.get("name", f"arg{p.get('index', 0)}"))
            ptype = python_type_for_param(p)
            parts.append(f"{pname}: {ptype}")

        body_lines.append(f"    @staticmethod")
        body_lines.append(f"    def {mname}({', '.join(parts)}) -> {ret}: ...")

    # ── Fields as class-level annotations ─────────────────────────────────────
# ── Fields as class-level annotations ─────────────────────────────────────
    # ── Fields as explicit methods ─────────────────────────────────────
    for f in cls.get("fields", []):
        fname = sanitize_id(f.get("name", "UNKNOWN"))
        ftype = {
            "jboolean": "bool", "jbyte": "int", "jchar": "int", "jshort": "int",
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
    
        # ---> INJECT NEW CASTING STUBS (OUTSIDE THE LOOP!) <---
    body_lines.append(f"")
    body_lines.append(f"    @staticmethod")
    body_lines.append(f"    def _stratum_cast(obj: object) -> Optional['{py_cls_name}']: ...")
    body_lines.append(f"    def _get_jobject_ptr(self) -> int: ...")

        # ── Ensure class body is never empty ──────────────────────────────────────

    # ── Ensure class body is never empty ──────────────────────────────────────
    non_empty = [l for l in body_lines if l.strip()]
    if not non_empty:
        body_lines.append(f"    ...")

    lines.extend(body_lines)
    lines.append(f"")
    return "\n".join(lines)


# =============================================================================
# Package __init__.pyi
# =============================================================================

def emit_package_init(safe_simple_names: list[str]) -> str:
    lines = ["# Auto-generated by Stratum Stage 08 — DO NOT EDIT", ""]
    for name in sorted(set(safe_simple_names)):
        lines.append(f"from .{name} import {name}")
    lines.append("")
    return "\n".join(lines)


# =============================================================================
# Main
# =============================================================================

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
        if f.name not in ("parse_summary.json", "resolve_summary.json")
    )
    if not json_files:
        print("ERROR: No JSON files found. Did Stage 05 succeed?")
        sys.exit(1)

    print(f"-> Found {len(json_files)} class JSON files")

    # ── Load all class data ───────────────────────────────────────────────────
    all_classes: list[tuple[Path, dict]] = []
    for jf in json_files:
        try:
            cls = json.loads(jf.read_text(encoding="utf-8"))
            if cls.get("fqn"):
                all_classes.append((jf, cls))
            else:
                print(f"  WARN  no fqn in {jf.name} — skipped")
        except Exception as e:
            print(f"  WARN  {jf.name}: {e}")

    # ── packages[pkg_path] = [safe_simple_name, ...] ─────────────────────────
    packages: dict[str, list[str]] = {}

    output_dir.mkdir(parents=True, exist_ok=True)
    failed: list[dict] = []
    total = len(all_classes)

    for i, (jf, cls) in enumerate(all_classes, 1):
        try:
            fqn        = cls["fqn"]
            simple_raw = cls.get("simple_name", fqn.split(".")[-1])
            py_name    = safe_class_name(simple_raw)   # Python-safe class name

            # Package path: everything before the last '.' in fqn
            pkg_parts  = fqn.split(".")[:-1]
            pkg_key    = "/".join(pkg_parts)

            packages.setdefault(pkg_key, []).append(py_name)

            # File: output/<pkg>/<PyClassName>.pyi
            pyi_path = output_dir / Path(*pkg_parts) / f"{py_name}.pyi"
            pyi_path.parent.mkdir(parents=True, exist_ok=True)

            pyi_text = emit_class_pyi(cls)
            pyi_path.write_text(pyi_text, encoding="utf-8")

            n_ctor   = len(cls.get("constructors",       []))
            n_decl   = len(cls.get("declared_methods",   []))
            n_ovr    = len(cls.get("overridden_methods",  []))
            n_inh    = len(cls.get("inherited_methods",  []))
            n_static = sum(1 for m in
                           cls.get("declared_methods", []) +
                           cls.get("overridden_methods", [])
                           if m.get("is_static"))

            tags = []
            if cls.get("is_annotation"):  tags.append("@ann")
            if cls.get("is_interface"):   tags.append("iface")
            if cls.get("is_abstract"):    tags.append("abs")
            if cls.get("is_enum"):        tags.append("enum")
            if cls.get("is_inner_class"): tags.append("inner")
            tag_str = f"[{','.join(tags)}]" if tags else ""

            print(
                f"  [{i:4d}/{total}] OK  {fqn} {tag_str}\n"
                f"           ctor={n_ctor}  decl={n_decl}  "
                f"ovr={n_ovr}  inh={n_inh}  static={n_static}"
            )

        except Exception as e:
            failed.append({"file": str(jf), "error": str(e)})
            print(f"  [{i:4d}/{total}] FAIL  {jf.name}  ->  {e}")

    # ── Emit __init__.pyi for every package ───────────────────────────────────
    for pkg_key, names in packages.items():
        init_path = output_dir / Path(pkg_key) / "__init__.pyi"
        init_path.write_text(emit_package_init(names), encoding="utf-8")

    # ── Top-level android/__init__.pyi (only if not already written) ──────────
    top_init = output_dir / "android" / "__init__.pyi"
    top_init.parent.mkdir(parents=True, exist_ok=True)
    if not top_init.exists():
        top_init.write_text(
            "# Auto-generated by Stratum Stage 08 — DO NOT EDIT\n",
            encoding="utf-8",
        )

    # ── Summary ───────────────────────────────────────────────────────────────
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

    if failed:
        print()
        print("FAILED FILES:")
        for f in failed:
            print(f"  {f['file']}")
            print(f"    {f['error']}")
        print()
        print("Fix above and rerun. Stages 01-07 are untouched.")
        sys.exit(1)
    else:
        print()
        print("All stubs emitted. Run:")
        print(f"  python 08_pyi_emit/main.py --input 05_resolve/output/ "
              f"--output 08_pyi_emit/output/")


if __name__ == "__main__":
    main()