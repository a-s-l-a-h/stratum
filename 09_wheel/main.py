#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Stratum Pipeline — Stage 09: Build Python Wheel (.whl)
=========================================================
LOCATION: 09_wheel/main.py
VERSION: v9 (unchanged from v8 — no bugs identified in this stage)

WHAT THIS STAGE DOES
    Zips _stratum.so (from Stage 07) together with every generated
    Python .py/.pyi file (from Stage 08) into a standard installable
    wheel. Also injects stratum/__init__.py, which provides:
      - getActivity() / get_activity()
      - setContentView() / set_content_view()
      - set_log_enabled(bool) — runtime toggle for the compile-time
        logging level baked in at Stage 07.
      - _auto_register_lifecycle() — scans your app's main.py for
        onCreate/onResume/onPause/onStop/onDestroy and wires them to the
        native Activity lifecycle automatically on import.

WHEEL NAMING
    stratum-<version>-<pytag>-<pytag>-android_<minapi>_<abi>.whl
    Match --abi and --chaquopy here to EXACTLY what you used in
    07_build/main.py, or Chaquopy on the Android side won't pick the
    wheel up for the correct device architecture / Python version.
"""

import argparse
import base64
import hashlib
import zipfile
from pathlib import Path


def sha256_record(name: str, data: bytes) -> str:
    d = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
    return f"{name},sha256={d},{len(data)}"


INIT_PY = '''# Stratum Runtime Entry Point. Auto-generated. DO NOT EDIT.
# Optional dynamic-mode class loader (see 08_pyi_emit --mode dynamic).
# In a static-mode wheel (the default, unchanged pipeline) stratum/_dynamic.py
# simply doesn't exist in the wheel, ImportError is swallowed, and nothing
# below this block changes behavior at all.
try:
    from . import _dynamic as _stratum_dynamic
    _stratum_dynamic.install()
except ImportError:
    pass

from . import _stratum as _core
from stratum.core.stratum_object import StratumObject
import importlib
import sys


def getActivity():
    """Return the current Android Activity, or None before onCreate."""
    ptr = _core.get_activity_ptr()
    if not ptr:
        return None
    from stratum.android.app.Activity import Activity
    return Activity(_ptr=ptr)


get_activity = getActivity
stratum_get_activity = getActivity


def stratum_cast(obj, target_cls):
    """Safely cast an object or pointer to target_cls with JNI IsInstanceOf verification."""
    if obj is None:
        return None
    return target_cls.from_ptr(obj)


stratum_cast_to = stratum_cast


def setContentView(activity, view) -> None:
    """Mount a View as the Activity's root content view."""
    act_ptr = getattr(activity, "_ptr", activity)
    view_ptr = getattr(view, "_ptr", view)
    _core.set_content_view(act_ptr, view_ptr)


set_content_view = setContentView


def allocate_direct_buffer(capacity: int):
    """Allocate an off-heap direct java.nio.ByteBuffer."""
    ptr = _core.allocate_direct_buffer(capacity)
    if not ptr:
        return None
    from stratum.core.stratum_object import _wrap_instance
    return _wrap_instance(ptr, "java.nio.ByteBuffer")


def surface_to_native_window(surface) -> int:
    """Acquire an ANativeWindow* pointer (as an integer) from an android.view.Surface."""
    s_ptr = getattr(surface, "_ptr", surface)
    return _core.surface_to_native_window(s_ptr)


def release_native_window(win_ptr: int) -> None:
    """Release an ANativeWindow* handle previously acquired."""
    _core.release_native_window(win_ptr)


def to_java(obj):
    """Recursively converts Python data (dict, list, int, float, bool, str, bytes)
    into a real Java object (HashMap, ArrayList, Boxed primitives, byte[]).
    Existing StratumObject instances retain their underlying Java reference."""
    if obj is None:
        return None
    if hasattr(obj, "_ptr"):
        return obj
    ptr = _core.to_java(obj)
    if not ptr:
        return None
    return StratumObject(_ptr=ptr)


def to_py(obj):
    """Recursively converts Java objects (Map, List, Bundle, boxed primitives,
    and arrays) into native Python data (dict, list, int, float, bool, str, bytes).
    Non-data Java objects are wrapped into typed StratumObject instances."""
    if obj is None:
        return None
    # 100% DEFENSIVE GUARD: If the object does not have _ptr, it is ALREADY native
    # Python data (e.g. an int, bool, float, str, list, dict). Never treat a bare
    # Python numeric value as a raw JNI memory address!
    if not hasattr(obj, "_ptr"):
        return obj
    ptr = getattr(obj, "_ptr", None)
    if not isinstance(ptr, int) or ptr == 0:
        return None
    return _core.to_py(ptr)


def remove_callback(key: str) -> None:
    """Release a stored Python callback (listener/adapter) by its key,
    if you tracked it. Reduces the g_callbacks map growth flagged by the
    logcat warning that fires once it exceeds ~10,000 live entries."""
    _core.remove_callback(key)


def remove_callbacks_by_prefix(prefix: str) -> int:
    """Remove all stored callbacks matching a given prefix."""
    return _core.remove_callbacks_by_prefix(prefix)


def callback_count() -> int:
    """Number of Python callbacks currently retained by the native side."""
    return _core.stratum_callback_count()




def set_log_enabled(enabled: bool) -> None:
    """Toggle runtime logging. Only has any effect if this .so was
    compiled with --log-level 1 or 2 in Stage 07 — level 0 (the default)
    strips all logging code at compile time, so this call is a silent
    no-op on a production build."""
    _core.set_log_enabled(enabled)


def _auto_register_lifecycle() -> None:
    try:
        main = sys.modules.get("main") or importlib.import_module("main")
    except Exception:
        return
    for name in ("onCreate", "onResume", "onPause", "onStop", "onDestroy"):
        fn = getattr(main, name, None)
        if callable(fn):
            _core.set_lifecycle_callback(name, fn)


try:
    _auto_register_lifecycle()
except Exception:
    pass
'''


def main():
    ap = argparse.ArgumentParser(description="Stratum Stage 09 - Build Wheel (v9)")
    ap.add_argument("--so", required=True, help="Path to compiled _stratum.so")
    ap.add_argument("--py-src", required=True, help="Path to 08_pyi_emit/output/")
    ap.add_argument("--output", required=True, help="Output destination folder")
    ap.add_argument("--version", default="0.9.0")
    ap.add_argument("--min-api", default="24")
    ap.add_argument("--abi", default="arm64-v8a")
    ap.add_argument("--chaquopy", default="3.12.0-0")
    ap.add_argument("--include-pyi", choices=["yes", "no"], default="yes",
                     help="yes (default, unchanged v9 behavior) packs .pyi stub "
                          "files into the wheel too. no drops them from the "
                          "runtime wheel (they're IDE-only, never imported at "
                          "runtime) to shrink the .whl — use for production/"
                          "release builds. Works with either Stage 08 --mode.")
    args = ap.parse_args()

    print("=" * 70)
    print("  STRATUM PIPELINE — STAGE 09 (WHEEL) v9")
    print("=" * 70)

    so_path = Path(args.so)
    py_dir = Path(args.py_src)
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    py_ver = ".".join(args.chaquopy.split(".")[:2])
    py_tag = "cp" + py_ver.replace(".", "")
    abi_tag = args.abi.replace("-", "_")
    ver_safe = args.version.replace("-", "_")

    wheel_name = f"stratum-{ver_safe}-{py_tag}-{py_tag}-android_{args.min_api}_{abi_tag}.whl"
    wheel_path = out_dir / wheel_name
    dist_info = f"stratum-{ver_safe}.dist-info"
    records = []

    with zipfile.ZipFile(wheel_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        init_data = INIT_PY.encode("utf-8")
        zf.writestr("stratum/__init__.py", init_data)
        records.append(sha256_record("stratum/__init__.py", init_data))

        so_data = so_path.read_bytes()
        so_info = zipfile.ZipInfo("stratum/_stratum.so")
        so_info.external_attr = 0o755 << 16  # rwxr-xr-x — writestr() defaults to 0600, which
                                              # makes dlopen() refuse to map it on-device with
                                              # UnsatisfiedLinkError: ... Permission denied
        so_info.compress_type = zipfile.ZIP_DEFLATED
        zf.writestr(so_info, so_data)
        records.append(sha256_record("stratum/_stratum.so", so_data))

        allowed_suffixes = {".py"} if args.include_pyi == "no" else {".py", ".pyi"}
        for f in sorted(py_dir.rglob("*")):
            if not f.is_file():
                continue
            # .py / .pyi as before (.pyi optionally dropped via --include-pyi no).
            # _meta.json.gz is the new dynamic-mode metadata blob (08_pyi_emit
            # --mode dynamic) — it has neither suffix, so it needs its own check.
            is_meta_blob = f.name == "_meta.json.gz"
            if f.suffix not in allowed_suffixes and not is_meta_blob:
                continue
            rel = f.relative_to(py_dir)
            entry = rel.as_posix()
            data = f.read_bytes()
            zf.writestr(entry, data)
            records.append(sha256_record(entry, data))

        wheel_meta = (
            f"Wheel-Version: 1.0\nGenerator: stratum\nRoot-Is-Purelib: false\n"
            f"Tag: {py_tag}-{py_tag}-android_{args.min_api}_{abi_tag}\n"
        ).encode()
        zf.writestr(f"{dist_info}/WHEEL", wheel_meta)
        records.append(sha256_record(f"{dist_info}/WHEEL", wheel_meta))

        metadata = (
            f"Metadata-Version: 2.1\nName: stratum\nVersion: {ver_safe}\n"
            f"Summary: Python bridge to Android native API\nRequires-Python: >={py_ver}\n"
        ).encode()
        zf.writestr(f"{dist_info}/METADATA", metadata)
        records.append(sha256_record(f"{dist_info}/METADATA", metadata))

        record_entry = f"{dist_info}/RECORD"
        zf.writestr(record_entry, "\n".join(records) + f"\n{record_entry},,\n")

    size_mb = wheel_path.stat().st_size / (1024 * 1024)
    print(f"-> Packaged: {wheel_path.name} ({size_mb:.2f} MB)")


if __name__ == "__main__":
    main()