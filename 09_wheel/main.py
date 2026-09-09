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


def setContentView(activity, view) -> None:
    """Mount a View as the Activity's root content view."""
    act_ptr = getattr(activity, "_ptr", activity)
    view_ptr = getattr(view, "_ptr", view)
    _core.set_content_view(act_ptr, view_ptr)


set_content_view = setContentView


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
        zf.writestr("stratum/_stratum.so", so_data)
        records.append(sha256_record("stratum/_stratum.so", so_data))

        for f in sorted(py_dir.rglob("*")):
            if f.is_file() and f.suffix in (".py", ".pyi"):
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