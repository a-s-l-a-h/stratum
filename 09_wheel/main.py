"""
Stage 08 — Build stratum Python wheel (.whl)
"""

import argparse
import base64
import hashlib
import sys
import zipfile
from pathlib import Path


def print_header(title):
    print("==================================================")
    print(f" {title}")
    print("==================================================")


def sha256_record(name: str, data: bytes) -> str:
    digest = base64.urlsafe_b64encode(
        hashlib.sha256(data).digest()
    ).rstrip(b"=").decode()
    return f"{name},sha256={digest},{len(data)}"


INIT_PY_CONTENT = '''\
# stratum — Python bridge to Android native API
from . import _stratum as _core
import importlib
import sys
import os

# --- Core Re-exports ---
def getActivity():
    """Return the current Android Activity. Call inside onCreate()."""
    return _core.getActivity()

def setContentView(activity, view):
    """Set the root view. Equivalent to Activity.setContentView(view)."""
    return _core.setContentView(activity, view)

def cast_to(obj, cls_name):
    """
    Cast a StratumObject to a specific Stratum wrapper class.
    cls_name should be the fully qualified java name (e.g. "android.hardware.camera2.CameraManager")
    or the stratum class name (e.g. "android.view.Surface").
    """
    safe_name = cls_name.replace(".", "_").replace("$", "_")
    target_cls = getattr(_core, safe_name, None)
    if target_cls is not None and hasattr(target_cls, "_stratum_cast"):
        return target_cls._stratum_cast(obj)
    return _core.cast_to(obj, cls_name)

def wrap_surface(obj):
    """Wrap a SurfaceTexture/Surface StratumObject into a StratumSurface."""
    return _core.wrap_surface(obj)

def remove_callback(key):
    """Remove a stored callback."""
    return _core.remove_callback(key)

def __getattr__(name):
    """Lazy loading for dynamically generated classes."""
    attr = getattr(_core, name, None)
    if attr is not None:
        return attr
    # If not found in _core, it might be a generated class we need to import
    # from its package's __init__.py.
    # This part might need refinement depending on how deeply nested packages are handled.
    # For now, we'll raise AttributeError to indicate it's not directly available.
    raise AttributeError(f"module 'stratum' has no attribute {name!r}")

def _auto_register_lifecycle():
    """
    Scans main.py for onCreate / onResume / onPause / onStop / onDestroy
    and registers them via set_lifecycle_callback.
    """
    try:
        main = sys.modules.get("main")
        if main is None:
            main = importlib.import_module("main")
    except ImportError as e:
        import traceback
        print(f"Stratum: _auto_register_lifecycle: could not import main: {e}")
        traceback.print_exc()
        return

    registered = []
    for name in ("onCreate", "onResume", "onPause", "onStop", "onDestroy"):
        fn = getattr(main, name, None)
        if callable(fn):
            _core.set_lifecycle_callback(name, fn)
            registered.append(name)

    print(f"Stratum: registered lifecycle callbacks: {registered}")
    if "onCreate" not in registered:
        print("Stratum: WARNING — no onCreate found in main.py!")
        print("         Make sure you define:  def onCreate(): ...")


# --- Expose generated classes and factories ---
# This section is dynamically populated by Stage 09's wheel building process
# based on the __init__.py files generated within each package.
# However, for direct access during development and immediate import,
# we add the commonly used ones here.

# Commonly used classes and factory functions
try:
    # Explicitly import common ones that are likely to be used directly
    # This makes them available without needing __getattr__ lookup for common cases.
    from ._stratum import (
        android_app_Activity,
        android_view_View,
        android_view_ViewGroup,
        android_widget_Button,
        android_widget_TextView,
        android_widget_EditText,
        android_graphics_SurfaceTexture,
        java_util_ArrayList, # <-- This is the crucial addition
        create_android_view_TextureView,
        create_android_view_View,
        create_android_view_ViewGroup,
        create_android_widget_Button,
        create_android_widget_TextView,
        create_android_widget_EditText,
        create_android_graphics_SurfaceTexture,
    )
except ImportError:
    # If these are not directly available, __getattr__ will be tried later,
    # but it's better to have them explicitly imported for clarity and performance.
    pass

# Auto-register lifecycle callbacks when the stratum package is first imported
# This should happen early in the app's lifecycle.
try:
    _auto_register_lifecycle()
except Exception:
    # If _auto_register_lifecycle itself fails, log it but don't crash import.
    # The user will see errors when they try to use lifecycle methods.
    import traceback
    print("Stratum: ERROR during automatic lifecycle registration!")
    traceback.print_exc()
'''


def build_wheel(
    so_path:      Path,
    pyi_dir:      Path,
    output_dir:   Path,
    version:      str,
    min_api:      str,
    abi:          str,
    chaquopy_ver: str,
) -> Path:

    py_ver   = ".".join(chaquopy_ver.split(".")[:2])   
    py_tag   = "cp" + py_ver.replace(".", "")           
    abi_tag  = abi.replace("-", "_")                    
    ver_safe = version.replace("-", "_")

    wheel_name = f"stratum-{ver_safe}-{py_tag}-{py_tag}-android_{min_api}_{abi_tag}.whl"
    wheel_path = output_dir / wheel_name
    output_dir.mkdir(parents=True, exist_ok=True)

    dist_info = f"stratum-{ver_safe}.dist-info"
    records   = []

    with zipfile.ZipFile(wheel_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        init_data = INIT_PY_CONTENT.encode()
        entry = "stratum/__init__.py"
        zf.writestr(entry, init_data)
        records.append(sha256_record(entry, init_data))
        print(f"   __init__.py : written")

        so_data = so_path.read_bytes()
        entry   = "stratum/_stratum.so"
        zf.writestr(entry, so_data)
        records.append(sha256_record(entry, so_data))
        so_mb = round(len(so_data) / (1024*1024), 2)
        print(f"   _stratum.so  : {so_mb} MB")

        pyi_files = sorted(pyi_dir.rglob("*.pyi"))
        for pyi_file in pyi_files:
            rel      = pyi_file.relative_to(pyi_dir)
            entry    = f"stratum/{rel.as_posix()}"
            pyi_data = pyi_file.read_bytes()
            zf.writestr(entry, pyi_data)
            records.append(sha256_record(entry, pyi_data))
        print(f"   .pyi stubs  : {len(pyi_files)} files")

        pkg_classes: dict = {}
        for pyi_file in pyi_files:
            if pyi_file.name == "__init__.pyi":
                continue

            rel       = pyi_file.relative_to(pyi_dir)
            parts     = list(rel.parts)
            sub_parts = parts[:-1]
            pkg_key   = "/".join(parts[1:-1])
            simple    = pyi_file.stem

            fqn_prefix = "_".join(sub_parts + [simple])

            if pkg_key not in pkg_classes:
                pkg_classes[pkg_key] = []
            pkg_classes[pkg_key].append((simple, fqn_prefix))

        for pkg_key, class_list in pkg_classes.items():
            init_lines = [
                "# Auto-generated by Stratum Stage 09 — DO NOT EDIT",
                "import stratum._stratum as _m",
                "",
            ]

            for simple, fqn_prefix in sorted(class_list):
                init_lines.append(f"{simple} = getattr(_m, '{fqn_prefix}', None)")
                init_lines.append(f"create_{simple} = getattr(_m, 'create_{fqn_prefix}', None)")
                init_lines.append("")

            init_data = "\n".join(init_lines).encode("utf-8")

            if pkg_key == "":
                entry = "stratum/android/__init__.py"
            else:
                entry = f"stratum/android/{pkg_key}/__init__.py"
            zf.writestr(entry, init_data)
            records.append(sha256_record(entry, init_data))
            print(f"   pkg: android/{pkg_key}  ({len(class_list)} classes)")

        if "" not in pkg_classes:
            top_data = "# Auto-generated by Stratum Stage 08 — DO NOT EDIT\n".encode("utf-8")
            entry    = "stratum/android/__init__.py"
            zf.writestr(entry, top_data)
            records.append(sha256_record(entry, top_data))
        print(f"   android pkgs : {len(pkg_classes)} packages generated")

        wheel_meta = (
            f"Wheel-Version: 1.0\n"
            f"Generator: stratum-pipeline\n"
            f"Root-Is-Purelib: false\n"
            f"Tag: {py_tag}-{py_tag}-android_{min_api}_{abi_tag}\n"
        ).encode()
        entry = f"{dist_info}/WHEEL"
        zf.writestr(entry, wheel_meta)
        records.append(sha256_record(entry, wheel_meta))

        metadata = (
            f"Metadata-Version: 2.1\n"
            f"Name: stratum\n"
            f"Version: {ver_safe}\n"
            f"Summary: Python bridge to Android native API\n"
            f"Requires-Python: >={py_ver}\n"
        ).encode()
        entry = f"{dist_info}/METADATA"
        zf.writestr(entry, metadata)
        records.append(sha256_record(entry, metadata))

        record_entry = f"{dist_info}/RECORD"
        record_text  = "\n".join(records) + f"\n{record_entry},,\n"
        zf.writestr(record_entry, record_text)

    return wheel_path


def main():
    parser = argparse.ArgumentParser(description="Stratum Stage 08 - Build .whl")
    parser.add_argument("--so",       required=True, help="07_build/output/_stratum.so")
    parser.add_argument("--pyi",      required=True, help="07_pyi_emit/output/")
    parser.add_argument("--output",   required=True, help="08_wheel/output/")
    parser.add_argument("--version",  default="0.1.0")
    parser.add_argument("--min-api",  default="21")
    parser.add_argument("--abi",      default="arm64-v8a", choices=["arm64-v8a", "armeabi-v7a", "x86_64", "x86"])
    parser.add_argument("--chaquopy", default="3.12.0-0")
    args = parser.parse_args()

    print_header("STRATUM PIPELINE - STAGE 08 (WHEEL)")

    so_path = Path(args.so)
    pyi_dir = Path(args.pyi)
    out_dir = Path(args.output)

    for p, label in [
        (so_path, "stratum.so"),
        (pyi_dir, "pyi output dir"),
    ]:
        if not p.exists():
            print(f"ERROR: Not found: {p}  ({label})")
            sys.exit(1)

    py_ver   = ".".join(args.chaquopy.split(".")[:2])
    
    wheel_path = build_wheel(
        so_path      = so_path,
        pyi_dir      = pyi_dir,
        output_dir   = out_dir,
        version      = args.version,
        min_api      = args.min_api,
        abi          = args.abi,
        chaquopy_ver = args.chaquopy,
    )

    whl_size = round(wheel_path.stat().st_size / (1024*1024), 2)
    whl_name = wheel_path.name

    print()
    print_header("STAGE 08 COMPLETE")
    print(f"-> Wheel : {wheel_path}")
    print(f"-> Size  : {whl_size} MB")
    print()


if __name__ == "__main__":
    main()