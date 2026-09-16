#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Stratum Pipeline — Stage 10b: Build the Embedded Single-.so
================================================================================
LOCATION: 10_embed/build.py

WHAT THIS PRODUCES
    ONE self-contained native library per (python-version x abi):

        libstratum-cp314-arm64_v8a.so

    plus a sidecar libstratum-cp314-arm64_v8a.json describing the build.

    Drop the .so into a consuming Android project's jniLibs/<abi>/ RENAMED to
    libstratum.so, and:

        import stratum
        from stratum.android.widget import Button

    work with no wheel, no loose .py files, no metadata blob on disk.

FILENAME POLICY
    The name encodes ONLY the two hard binary-compatibility constraints:
      - the embedded CPython ABI (cp312/cp313/cp314) — because this .so links
        against a specific CPython's headers;
      - the Android ABI — unavoidable for any native code.
    NDK version, android.jar API level and Stratum's own version are NOT in the
    name. They live inside the .so, queryable as stratum.build_info(), plus the
    sidecar .json for humans and CI browsing without loading the library. This
    avoids names like libstratum-cp312-arm64-v8a-ndk25c-api35-v0.9.2.so that
    nobody wants to type or pattern-match in a build.gradle.

RELATIONSHIP TO STAGE 07
    None. This stage has its own templates under 10_embed/templates/ and reads
    only 10_embed/output/core/. Stage 07 can be deleted at any point.

USAGE (once per python-version x abi pair you want to publish)
    python 10_embed/build.py \\
        --core     10_embed/output/core \\
        --setup    00_setup/output/setup_report.json \\
        --nanobind third_party/nanobind \\
        --abi      arm64-v8a \\
        --output   10_embed/output \\
        --no-log
"""

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

TEMPLATES = Path(__file__).parent / "templates"


def print_header(title):
    print("=" * 70)
    print(f"  {title}")
    print("=" * 70)


def render_template(tpl_path: Path, variables: dict) -> str:
    text = tpl_path.read_text(encoding="utf-8")
    for key, value in variables.items():
        text = text.replace("{{%s}}" % key, value)
    return text


def resolve_android_platform(android_api: str, ndk_max: int = 33) -> str:
    """NDK r25c's max supported compile target is API 33. Requesting higher
    breaks the linker (crtbegin_dynamic.o not found), so clamp here rather
    than let that surface as a confusing link error."""
    try:
        if int(android_api) > ndk_max:
            print("   NOTE: API %s > NDK max (%d). Capping to %d."
                  % (android_api, ndk_max, ndk_max))
            return str(ndk_max)
    except ValueError:
        pass
    return android_api


def find_ninja(ndk_path: Path):
    """Order of preference: pip-installed ninja package (recommended) ->
    system PATH -> bundled inside the NDK's own cmake/ folder."""
    try:
        import ninja as ninja_pkg
        for name in ("ninja.exe", "ninja"):
            candidate = Path(ninja_pkg.BIN_DIR) / name
            if candidate.exists():
                return str(candidate)
    except ImportError:
        pass

    found = shutil.which("ninja")
    if found:
        return found

    for sub in ndk_path.rglob("ninja*"):
        if sub.is_file() and sub.name in ("ninja", "ninja.exe"):
            return str(sub)
    return None


def resolve_python_target(cpython_dir: Path, py_version: str, abi: str):
    """Resolves Python.h and libpython from the official python.org Android
    builds extracted by Stage 00 under
    third_party/cpython_android/<version>/<abi>/prefix/."""
    py_mm = ".".join(py_version.split(".")[:2])
    prefix_dir = cpython_dir / abi / "prefix"

    if not prefix_dir.exists():
        raise FileNotFoundError(
            "Official Python target not found for ABI '%s' at: %s\n"
            "Ensure Stage 00 ran with --python-target-version %s"
            % (abi, prefix_dir, py_version))

    include_dir = prefix_dir / "include" / ("python" + py_mm)
    if not (include_dir / "Python.h").exists():
        found = False
        for c in (prefix_dir / "include").glob("python*"):
            if (c / "Python.h").exists():
                include_dir = c
                found = True
                break
        if not found:
            raise FileNotFoundError("Python.h not found in %s" % (prefix_dir / "include"))

    lib_dir = prefix_dir / "lib"
    if not (lib_dir / ("libpython%s.so" % py_mm)).exists():
        if not list(lib_dir.glob("libpython*.so")):
            raise FileNotFoundError("No libpython*.so found in %s" % lib_dir)

    return {
        "include": include_dir.resolve().as_posix(),
        "lib_dir": lib_dir.resolve().as_posix(),
        "py_ver": py_mm,
    }


def main():
    ap = argparse.ArgumentParser(
        description="Stratum Stage 10b - Build embedded libstratum.so")
    ap.add_argument("--core", required=True, help="10_embed/output/core/ (from emit.py)")
    ap.add_argument("--setup", required=True, help="00_setup/output/setup_report.json")
    ap.add_argument("--nanobind", required=True, help="third_party/nanobind")
    ap.add_argument("--abi", default="arm64-v8a",
                    choices=["arm64-v8a", "armeabi-v7a", "x86_64", "x86"])
    ap.add_argument("--python-target-version", "--py-version", default=None,
                    help="Defaults to setup_report.json's python_target_version")
    ap.add_argument("--cpython-dir", default=None,
                    help="Override path to third_party/cpython_android/<version>")
    ap.add_argument("--output", required=True, help="10_embed/output/")
    ap.add_argument("--log", dest="log_enabled", action="store_true", default=True,
                    help="Build WITH deep trace logging compiled in (default). "
                         "Toggle at runtime via stratum.set_log_enabled().")
    ap.add_argument("--no-log", dest="log_enabled", action="store_false",
                    help="Build WITHOUT logging — macros stripped at compile "
                         "time, zero cost, for production/release.")
    args = ap.parse_args()

    print_header("STRATUM STAGE 10b — BUILD EMBEDDED libstratum.so")

    setup = json.loads(Path(args.setup).read_text(encoding="utf-8"))
    cmake_exe = setup.get("cmake_path", "cmake")
    ndk_path = Path(setup["ndk_path"])
    android_api = setup.get("ndk_api", "24")

    py_target_ver = args.python_target_version or setup.get("python_target_version", "3.14.7")
    py_target_dir = Path(args.cpython_dir or setup.get(
        "python_target_path",
        Path(__file__).parent.parent / "third_party" / "cpython_android" / py_target_ver))

    core_dir = Path(args.core)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    build_dir = output_dir / "build" / args.abi
    build_dir.mkdir(parents=True, exist_ok=True)

    for required in ("bridge_core.cpp", "bridge_core.h", "stratum_engine.cpp",
                     "metadata_table.cpp", "metadata_table.h",
                     "stratum_pybundle.cpp", "stratum_pybundle.h", "bridge_main.cpp"):
        if not (core_dir / required).exists():
            print("ERROR: %s missing from %s. Run 10_embed/emit.py first."
                  % (required, core_dir))
            sys.exit(1)

    ninja_exe = find_ninja(ndk_path)
    if not ninja_exe:
        print("ERROR: ninja not found. Run: pip install ninja")
        sys.exit(1)

    python_paths = resolve_python_target(py_target_dir, py_target_ver, args.abi)
    print("-> Target Python %s [%s]" % (python_paths["py_ver"], args.abi))
    print("   Include: %s" % python_paths["include"])
    print("   Lib dir: %s" % python_paths["lib_dir"])

    # ── Render build files ───────────────────────────────────────────────
    init_file = build_dir / "StratumInit.cmake"
    init_file.write_text(render_template(TEMPLATES / "StratumInit.cmake.tpl", {
        "INC_ABS": python_paths["include"],
        "HOST_PYTHON": Path(sys.executable).resolve().as_posix(),
        "PY_VER": python_paths["py_ver"],
        "PY_MAJOR": python_paths["py_ver"].split(".")[0],
        "PY_MINOR": python_paths["py_ver"].split(".")[1],
    }), encoding="utf-8")

    cmake_file = output_dir / "CMakeLists.txt"
    cmake_file.write_text(render_template(TEMPLATES / "CMakeLists.txt.tpl", {
        "NANOBIND_DIR": Path(args.nanobind).resolve().as_posix(),
        "CORE_INCLUDE_DIR": core_dir.resolve().as_posix(),
        "PYTHON_VERSION": python_paths["py_ver"],
        "PYTHON_INCLUDE": python_paths["include"],
        "PYTHON_LIB_DIR": python_paths["lib_dir"],
    }), encoding="utf-8")

    # ── Configure + compile ──────────────────────────────────────────────
    toolchain = ndk_path / "build" / "cmake" / "android.toolchain.cmake"
    t0 = time.time()
    subprocess.run([
        cmake_exe, str(output_dir),
        "-B%s" % build_dir,
        "-DCMAKE_TOOLCHAIN_FILE=%s" % toolchain,
        "-DANDROID_ABI=%s" % args.abi,
        "-DANDROID_PLATFORM=android-%s" % resolve_android_platform(android_api),
        "-DANDROID_STL=c++_static",
        "-DCMAKE_BUILD_TYPE=Release",
        "-G", "Ninja",
        "-DCMAKE_MAKE_PROGRAM=%s" % ninja_exe,
        "-DCMAKE_PROJECT_TOP_LEVEL_INCLUDES=%s" % init_file.resolve().as_posix(),
        "-DSTRATUM_LOG_ENABLED=%d" % (1 if args.log_enabled else 0),
    ], check=True)
    subprocess.run([cmake_exe, "--build", str(build_dir), "--config", "Release",
                    "--parallel"], check=True)
    elapsed = round(time.time() - t0, 2)

    # ── Name and emit the deliverable ────────────────────────────────────
    py_tag = "cp" + "".join(python_paths["py_ver"].split("."))
    so_src = next(build_dir.rglob("libstratum.so"))
    so_dest = output_dir / ("libstratum-%s-%s.so" % (py_tag, args.abi.replace("-", "_")))
    shutil.copy2(so_src, so_dest)

    sidecar = {
        "file": so_dest.name,
        "python_tag": py_tag,
        "python_target_version": py_target_ver,
        "abi": args.abi,
        "ndk_api": android_api,
        "android_api_jar": setup.get("android_api"),
        "logging": bool(args.log_enabled),
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "note": "Rename to libstratum.so when placing under jniLibs/<abi>/ in a consuming project.",
    }
    (output_dir / (so_dest.stem + ".json")).write_text(
        json.dumps(sidecar, indent=2), encoding="utf-8")

    size_mb = round(so_dest.stat().st_size / (1024 * 1024), 2)
    print_header("STAGE 10b COMPLETE")
    print(" %s (%s MB) built in %ss" % (so_dest.name, size_mb, elapsed))
    print(" -> %s" % so_dest.resolve())
    print(" Place at: app/src/main/jniLibs/%s/libstratum.so" % args.abi)


if __name__ == "__main__":
    main()