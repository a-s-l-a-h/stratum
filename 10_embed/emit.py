#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Stratum Pipeline — Stage 10a: Engine + Python Bundle Emit (embedded mode)
================================================================================
LOCATION: 10_embed/emit.py

WHAT THIS EMITS (into 10_embed/output/core/) — 8 fixed files, regardless of how
many Java classes are in the build:

    metadata_table.h / .cpp     Deduplicated string pool + flat per-class
                                 method/field description arrays. Pure DATA —
                                 compile time doesn't grow with corpus size.
    bridge_core.h / .cpp        JNI thread attach/detach, exception translation,
                                 UTF-8<->UTF-16, callback storage, logging.
    stratum_engine.cpp          THE dispatcher: call_v/z/i/j/d/str/o,
                                 new_instance, delete_ref, field get/set.
    bridge_main.cpp             nanobind NB_MODULE(stratum) entry point,
                                 JNI_OnLoad, Activity/lifecycle glue, plus the
                                 embedded-boot block.
    stratum_pybundle.h / .cpp   NEW. zlib-compressed Python source for every
                                 generated class + stratum_object.py + ui.py +
                                 reflect.py, keyed by dotted module name; plus
                                 kStratumBootstrapPy (meta-path finder),
                                 kStratumInitPy (package __init__ body) and
                                 kStratumBuildInfoJson (build provenance).

RELATIONSHIP TO STAGES 06 / 07 / 09
    None. This stage owns private copies of the four engine sources under
    10_embed/engine_src/ and its own Python sources in 10_embed/py_sources.py.
    Stages 06, 07 and 09 can be deleted at any point without affecting this
    build path.

WHY THE PYBUNDLE IS A SEPARATE .CPP FROM metadata_table.cpp
    Different data, different origin: metadata_table describes Java classes and
    is built from Stage 05's resolved JSON; the pybundle holds Python source
    text built from Stage 08's generated .py files. Keeping them separate means
    either can be regenerated without touching the other, and the compile unit
    that holds several MB of hex byte arrays stays isolated.

WHY SOURCE AND NOT MARSHALLED BYTECODE
    Marshalled .pyc is tied to the exact CPython minor version's magic number.
    If the build host's Python doesn't match the on-device embedded CPython
    exactly, marshal.loads() breaks silently. Plain source has no such
    coupling; compile() costs microseconds, once per module, then sys.modules
    caches it forever.

USAGE
    python 10_embed/emit.py \\
        --input     05_resolve/output_patched \\
        --static-py 08_pyi_emit/output \\
        --setup     00_setup/output/setup_report.json \\
        --output    10_embed/output
"""

import argparse
import json
import re
import sys
import time
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from py_sources import (STRATUM_BOOTSTRAP_PY, INIT_PY,
                        STRATUM_UI_PY, STRATUM_REFLECT_PY)

ENGINE_SRC = Path(__file__).parent / "engine_src"


def print_header(title: str) -> None:
    print("=" * 70)
    print(f"  {title}")
    print("=" * 70)


# =============================================================================
# String pool: every unique string used anywhere in the metadata table is
# stored exactly once. Method names like "get"/"close"/"setText" and signatures
# like "()V" repeat thousands of times across the Android SDK — deduplicating
# them is what keeps the .so a few MB instead of tens.
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

    # The header is built AFTER the loop above has fully populated `pool`.
    # Building it earlier captures a stale len(pool.pool)==1, so the header's
    # `extern const char g_str_pool[1];` disagrees with the real definition in
    # the .cpp — a fatal "redefinition with different type" error, since array
    # size is part of the C++ type.
    h = [
        "// metadata_table.h — Stratum Auto-generated. DO NOT EDIT.",
        "// Flat, deduplicated description of every class/method/field Stratum",
        "// knows about. Pure data — jmethodID/jfieldID resolution happens",
        "// lazily in stratum_engine.cpp, never at load time.",
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
        "// One entry per Java class. class_ref/method_ids/field_ids start null",
        "// and `resolved` starts false — populated ON FIRST USE by",
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
# The compiled module is now named `stratum` itself — it is simultaneously the
# engine AND the package. Stage 08 emits "import stratum._stratum as _core",
# which must be retargeted to plain "stratum". Done here at pack time so
# Stage 08's emitter needs no modification at all.
# =============================================================================


def fix_dynamic_registry_loader(src: str) -> str:
    """Stage 08's _dynamic.py reads its registry via pkgutil.get_data(),
    which assumes a real on-disk package. In the embedded model there is no
    disk — the blob lives in the pybundle table under 'stratum._meta_blob'.
    Call the C++ lookup DIRECTLY (no import statement) so this can never
    re-enter _StratumFinder.find_spec() and recurse."""
    old = '''        raw = pkgutil.get_data("stratum", "_meta.json")
        if raw is None:
            import importlib.resources as _res
            raw = _res.files("stratum").joinpath("_meta.json").read_bytes()'''
    new = '''        raw = _core._pybundle_lookup("stratum._meta_blob")'''
    if old not in src:
        raise RuntimeError(
            "fix_dynamic_registry_loader: expected pkgutil block not found in "
            "_dynamic.py — Stage 08's dynamic-mode emitter has changed shape.")
    return src.replace(old, new)

def fix_core_import(src: str) -> str:
    return re.sub(r"\bstratum\._stratum\b", "stratum", src)


def c_raw_string(text: str) -> str:
    """Emits Python source as an unescaped C++ raw string literal. The
    delimiter is chosen so it cannot appear in generated Python source."""
    return 'R"PYSRC__STRATUM(' + text + ')PYSRC__STRATUM"'


def collect_modules(py_dir: Path, mode: str) -> tuple:
    """mode='static': reads Stage 08's --mode static .py tree.
       mode='dynamic': reads Stage 08's --mode dynamic _meta.json.gz +
       _dynamic.py and packs them as two named entries instead of one file
       per class — this is where the size win comes from."""
    stratum_root = py_dir / "stratum"
    if not stratum_root.exists():
        raise FileNotFoundError(
            "No 'stratum/' tree under %s — did Stage 08 run?" % py_dir)

    modules = {
        "stratum.ui": fix_core_import(STRATUM_UI_PY),
        "stratum.reflect": fix_core_import(STRATUM_REFLECT_PY),
    }

    if mode == "dynamic":
        meta_path = stratum_root / "_meta.json"
        dynamic_py = stratum_root / "_dynamic.py"
        if not meta_path.exists() or not dynamic_py.exists():
            raise FileNotFoundError(
                "_meta.json / _dynamic.py missing from %s — run "
                "08_pyi_emit/main.py with --mode dynamic first." % stratum_root)
        # stratum_object.py still comes from Stage 08's core/ output — the
        # dynamic loader's _get_parent_class/_wrap_instance import it exactly
        # like static mode does.
        obj_path = stratum_root / "core" / "stratum_object.py"
        if not obj_path.exists():
            raise FileNotFoundError("stratum/core/stratum_object.py missing from %s" % stratum_root)
        modules["stratum.core.stratum_object"] = fix_core_import(obj_path.read_text(encoding="utf-8"))
        modules["stratum._dynamic"] = fix_dynamic_registry_loader(
            fix_core_import(dynamic_py.read_text(encoding="utf-8")))
        # Store uncompressed JSON bytes under a pseudo-module name.
        modules["stratum._meta_blob"] = ("__RAW_BYTES__", meta_path.read_bytes())

        # Register every intermediate package (stratum.android,
        # stratum.android.graphics, ...) so the BOOTSTRAP finder also knows
        # about them, not just _dynamic.py's own in-memory registry.
        packages = {"stratum.core"}
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        for fqn, rec in meta.get("classes", {}).items():
            pkg = rec.get("package", "")
            if pkg:
                parts = ["stratum"] + pkg.split(".")
                for i in range(1, len(parts) + 1):
                    packages.add(".".join(parts[:i]))
        packages.discard("stratum")
        return modules, packages

    # mode == "static" — unchanged from before
    for f in sorted(stratum_root.rglob("*.py")):
        if f.name == "__init__.py":
            continue
        rel = f.relative_to(stratum_root)
        dotted = "stratum." + ".".join(rel.with_suffix("").parts)
        modules[dotted] = fix_core_import(f.read_text(encoding="utf-8"))

    if "stratum.core.stratum_object" not in modules:
        raise FileNotFoundError(
            "stratum/core/stratum_object.py missing from %s." % py_dir)

    packages = set()
    for dotted in modules:
        pkg_parts = dotted.split(".")[:-1]
        for i in range(1, len(pkg_parts) + 1):
            packages.add(".".join(pkg_parts[:i]))
    packages.discard("stratum")
    return modules, packages
    """Reads Stage 08's generated .py tree and maps dotted module name ->
    source, matching the existing static layout:
        stratum/android/widget/Button.py -> stratum.android.widget.Button

    Skips .pyi (IDE-only, never embedded) and __init__.py (intermediate
    packages are synthesized below so they get a real empty __path__)."""
    modules = {}
    stratum_root = static_py_dir / "stratum"
    if not stratum_root.exists():
        raise FileNotFoundError(
            "No 'stratum/' tree under %s — did Stage 08 run with --mode static?"
            % static_py_dir)

    for f in sorted(stratum_root.rglob("*.py")):
        if f.name == "__init__.py":
            continue
        rel = f.relative_to(stratum_root)
        dotted = "stratum." + ".".join(rel.with_suffix("").parts)
        modules[dotted] = fix_core_import(f.read_text(encoding="utf-8"))

    if "stratum.core.stratum_object" not in modules:
        raise FileNotFoundError(
            "stratum/core/stratum_object.py missing from %s — Stage 08 always "
            "writes it. Check that --static-py points at 08_pyi_emit/output/."
            % static_py_dir)

    # Support modules, packed exactly like any generated class, under their
    # fixed well-known dotted names. No special-casing on the C++ side.
    modules["stratum.ui"] = fix_core_import(STRATUM_UI_PY)
    modules["stratum.reflect"] = fix_core_import(STRATUM_REFLECT_PY)

    # Every intermediate package must resolve as a package too, even though it
    # has no source file of its own.
    packages = set()
    for dotted in modules:
        pkg_parts = dotted.split(".")[:-1]
        for i in range(1, len(pkg_parts) + 1):
            packages.add(".".join(pkg_parts[:i]))
    # `stratum` itself IS the compiled module, never a synthetic package.
    packages.discard("stratum")
    return modules, packages


def emit_pybundle(modules: dict, packages: set, build_info: dict) -> tuple:
    """Each module is compressed individually (small, decompresses in
    microseconds) and referenced by a table the C++ side scans linearly. Table
    sizes here are hundreds to low thousands of entries, so a linear scan is
    fine and avoids extra machinery."""
    h_lines = [
        "// stratum_pybundle.h — Stratum Auto-generated. DO NOT EDIT.",
        "#pragma once",
        "#include <cstdint>",
        "",
        "// Looks up compressed source for a fully-qualified module name.",
        "// Returns nullptr and sets *out_len=0 if not found.",
        "const unsigned char* stratum_pybundle_find(const char* fullname, uint32_t* out_len);",
        "bool stratum_pybundle_is_package(const char* fullname);",
        "",
        "extern const char kStratumBootstrapPy[];",
        "extern const char kStratumInitPy[];",
        "",
        "// Build provenance — queryable at runtime via stratum.build_info(),",
        "// deliberately NOT encoded in the .so filename. Only python-tag and",
        "// abi go in the name, because those are the only two things that",
        "// decide whether this file can load at all.",
        "extern const char kStratumBuildInfoJson[];",
    ]

    cpp_lines = [
        "// stratum_pybundle.cpp — Stratum Auto-generated. DO NOT EDIT.",
        '#include "stratum_pybundle.h"',
        "#include <cstring>",
        "",
        "extern const char kStratumBootstrapPy[] = " + c_raw_string(STRATUM_BOOTSTRAP_PY) + ";",
        "extern const char kStratumInitPy[] = " + c_raw_string(INIT_PY) + ";",
        "extern const char kStratumBuildInfoJson[] = "
        + c_raw_string(json.dumps(build_info, separators=(",", ":"))) + ";",
        "",
        "struct PyBundleEntry { const char* name; const unsigned char* data; uint32_t len; };",
        "",
    ]

    entries = []
    for i, (dotted, source) in enumerate(sorted(modules.items())):
        if isinstance(source, tuple) and source[0] == "__RAW_BYTES__":
            # Already gzip-compressed by Stage 08 — store as-is, don't
            # zlib-compress again (that would just add overhead for no gain).
            raw = source[1]
        else:
            raw = zlib.compress(source.encode("utf-8"), 9)
        hexb = ", ".join("0x%02x" % b for b in raw)
        cpp_lines.append("static const unsigned char g_pysrc_%d[] = {%s};" % (i, hexb))
        entries.append((dotted, i, len(raw)))

    cpp_lines.append("")
    cpp_lines.append("static const PyBundleEntry g_pybundle_table[] = {")
    for dotted, i, length in entries:
        cpp_lines.append('    {"%s", g_pysrc_%d, %d},' % (dotted, i, length))
    cpp_lines.append("};")
    cpp_lines.append("static const uint32_t g_pybundle_count = %d;" % len(entries))

    cpp_lines.append("")
    cpp_lines.append("static const char* g_pybundle_packages[] = {")
    for pkg in sorted(packages):
        cpp_lines.append('    "%s",' % pkg)
    cpp_lines.append("};")
    cpp_lines.append("static const uint32_t g_pybundle_package_count = %d;" % len(packages))

    cpp_lines.append("""
const unsigned char* stratum_pybundle_find(const char* fullname, uint32_t* out_len) {
    for (uint32_t i = 0; i < g_pybundle_count; ++i) {
        if (strcmp(g_pybundle_table[i].name, fullname) == 0) {
            *out_len = g_pybundle_table[i].len;
            return g_pybundle_table[i].data;
        }
    }
    *out_len = 0;
    return nullptr;
}

bool stratum_pybundle_is_package(const char* fullname) {
    for (uint32_t i = 0; i < g_pybundle_package_count; ++i) {
        if (strcmp(g_pybundle_packages[i], fullname) == 0) return true;
    }
    return false;
}
""")
    return "\n".join(h_lines), "\n".join(cpp_lines)


def main():
    ap = argparse.ArgumentParser(
        description="Stratum Stage 10a - Engine + Python Bundle Emit (embedded)")
    ap.add_argument("--input", required=True,
                    help="05_resolve/output_patched/ (the SECOND-pass output)")
    ap.add_argument("--static-py", required=True,
                    help="08_pyi_emit/output/ (must have been run with the SAME "
                         "--mode you pass here, static or dynamic)")
    ap.add_argument("--setup", required=True,
                    help="00_setup/output/setup_report.json (for build_info)")
    ap.add_argument("--output", required=True, help="10_embed/output/")
    ap.add_argument("--stratum-version", default="0.9.0")
    ap.add_argument("--mode", choices=["static", "dynamic"], default="dynamic",
                    help="dynamic (default, recommended): packs one compact "
                         "_meta.json.gz + the shared _dynamic.py factory — "
                         "smaller .so, classes built via type() on first import. "
                         "static: packs one real .py per class (must have run "
                         "08_pyi_emit with --mode static) — bigger .so, no "
                         "upfront registry-parse cost, easier to read a "
                         "traceback that points at real generated source.")
    args = ap.parse_args()

    print_header("STRATUM STAGE 10a — ENGINE + PYTHON BUNDLE EMIT")

    input_dir = Path(args.input)
    static_dir = Path(args.static_py)
    core_dir = Path(args.output) / "core"
    core_dir.mkdir(parents=True, exist_ok=True)

    for required in ("bridge_core.h", "bridge_core.cpp",
                     "stratum_engine.cpp", "bridge_main.cpp"):
        if not (ENGINE_SRC / required).exists():
            print("ERROR: %s missing from %s." % (required, ENGINE_SRC))
            print("       Copy the four engine files from 06_cpp_emit/engine_src/")
            print("       into 10_embed/engine_src/ (see Step 1 of the setup guide).")
            sys.exit(1)

    setup = json.loads(Path(args.setup).read_text(encoding="utf-8"))

    # ── 1. Metadata table (same as Stage 06) ─────────────────────────────
    json_files = sorted(f for f in input_dir.rglob("*.json")
                        if f.name not in ("parse_summary.json", "resolve_summary.json",
                                          "manifest.json"))
    classes = []
    for jf in json_files:
        data = json.loads(jf.read_text(encoding="utf-8"))
        if "class_id" in data:
            classes.append(data)
    classes.sort(key=lambda c: c["class_id"])
    print("-> %,d classes to index." % len(classes) if False else
          "-> {:,} classes to index.".format(len(classes)))

    pool = StringPool()
    meta_h, meta_cpp = emit_metadata_table(classes, pool)
    (core_dir / "metadata_table.h").write_text(meta_h, encoding="utf-8")
    (core_dir / "metadata_table.cpp").write_text(meta_cpp, encoding="utf-8")
    print("-> String pool: {:.1f} KB (deduplicated).".format(len(pool.pool) / 1024))

    # ── 2. Engine sources (copied verbatim from 10_embed/engine_src/) ────
    for name in ("bridge_core.h", "bridge_core.cpp",
                 "stratum_engine.cpp", "bridge_main.cpp"):
        (core_dir / name).write_text(
            (ENGINE_SRC / name).read_text(encoding="utf-8"), encoding="utf-8")
    print("-> Copied 4 engine files from engine_src/.")

    # ── 3. Python bundle ─────────────────────────────────────────────────
    modules, packages = collect_modules(static_dir, args.mode)
    build_info = {
        "stratum_version": args.stratum_version,
        "android_api_jar": setup.get("android_api"),
        "ndk_api": setup.get("ndk_api"),
        "python_target_version": setup.get("python_target_version"),
        "class_count": len(classes),
        "module_count": len(modules),
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    pb_h, pb_cpp = emit_pybundle(modules, packages, build_info)
    (core_dir / "stratum_pybundle.h").write_text(pb_h, encoding="utf-8")
    (core_dir / "stratum_pybundle.cpp").write_text(pb_cpp, encoding="utf-8")

    def _entry_len(s):
        if isinstance(s, tuple) and s[0] == "__RAW_BYTES__":
            return len(s[1])  # already gzip-compressed by Stage 08
        return len(zlib.compress(s.encode("utf-8"), 9))

    packed_kb = sum(_entry_len(s) for s in modules.values()) / 1024
    print("-> Packed {:,} Python modules / {:,} packages ({:.1f} KB compressed)."
          .format(len(modules), len(packages), packed_kb))
    print("-> Emitted 8 files to: %s" % core_dir)
    print_header("STAGE 10a COMPLETE")


if __name__ == "__main__":
    main()