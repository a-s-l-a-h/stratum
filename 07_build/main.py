#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Stratum Pipeline — Stage 07: Compile the Universal Engine
============================================================
LOCATION: 07_build/main.py
VERSION: v9 (unchanged from v8 — no bugs identified in this stage)

WHAT THIS STAGE DOES
    Downloads/caches the Chaquopy Python target zip for the requested
    ABI (headers + libpython.so, compile-time only — the real libpython
    .so is provided by Chaquopy on-device at runtime, never bundled).
    Renders CMakeLists.txt and StratumInit.cmake with absolute paths,
    then runs CMake+Ninja against the NDK toolchain to compile the 4
    fixed engine files from Stage 06 into _stratum.so.

WHY THIS IS FAST
    Stage 06 emits exactly 4 .cpp files no matter how many Java classes
    are in your build. Expect single-digit-to-low-tens-of-seconds builds
    even with the full Android SDK included, vs. 15+ minutes (or OOM)
    with the old per-class C++ codegen pipeline.

--log-level FLAG
    0 = production (default): logging macros compiled out entirely.
    1 = basic: method calls, class/field resolution logged via LOGD.
    2 = deep/trace: + every argument and return value via LOGV.
    Use 1 or 2 while developing, always ship 0 for a release build.
"""

import argparse
import json
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from urllib.request import urlretrieve

MAVEN_BASE = "https://repo1.maven.org/maven2/com/chaquo/python/target"


def print_header(title):
    print("==================================================")
    print(f" {title}")
    print("==================================================")


def render_template(tpl_path: Path, variables: dict) -> str:
    text = tpl_path.read_text(encoding="utf-8")
    for key, value in variables.items():
        text = text.replace(f"{{{{{key}}}}}", value)
    return text


def resolve_android_platform(android_api: str, ndk_max: int = 33) -> str:
    """NDK r25c's max supported compile target is API 33. Requesting a
    higher API level than the NDK supports breaks the linker
    (crtbegin_dynamic.o not found), so we clamp it here rather than let
    that surface as a confusing link error."""
    try:
        api_int = int(android_api)
        if api_int > ndk_max:
            print(f"   NOTE: API {android_api} > NDK max ({ndk_max}). Capping to {ndk_max}.")
            return str(ndk_max)
    except ValueError:
        pass
    return android_api


def find_ninja(ndk_path: Path):
    """Order of preference: pip-installed ninja package (recommended) ->
    system PATH -> bundled inside the NDK's own cmake/ folder."""
    try:
        import ninja as ninja_pkg
        for name in ["ninja.exe", "ninja"]:
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


def ensure_chaquopy_target(target_dir: Path, chaquopy_version: str, abi: str):
    """Downloads (or reuses a cached copy of) the Chaquopy Python target
    zip for one ABI. This gives us Python.h and libpython<ver>.so for
    compile-time linking only — the actual interpreter .so that runs on
    the device is supplied by the Chaquopy Gradle plugin, not by us."""
    py_ver = ".".join(chaquopy_version.split(".")[:2])
    abi_dest = target_dir / abi
    python_h = abi_dest / "include" / f"python{py_ver}" / "Python.h"
    libpython = abi_dest / "jniLibs" / abi / f"libpython{py_ver}.so"

    if python_h.exists() and libpython.exists():
        return {
            "include": python_h.parent.resolve().as_posix(),
            "lib_dir": libpython.parent.resolve().as_posix(),
            "py_ver": py_ver,
        }

    zip_name = f"target-{chaquopy_version}-{abi}.zip"
    zip_path = target_dir / zip_name
    url = f"{MAVEN_BASE}/{chaquopy_version}/{zip_name}"

    cached = Path(__file__).parent.parent / "third_party" / "chaquopy" / chaquopy_version / zip_name
    if cached.exists():
        shutil.copy2(cached, zip_path)
    else:
        urlretrieve(url, zip_path)

    extract_tmp = target_dir / f"_raw_{abi}"
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(extract_tmp)

    src = extract_tmp
    if not (extract_tmp / "include").exists():
        subdirs = [f for f in extract_tmp.iterdir() if f.is_dir()]
        if subdirs:
            src = subdirs[0]

    if abi_dest.exists():
        shutil.rmtree(abi_dest)
    shutil.copytree(src, abi_dest)
    shutil.rmtree(extract_tmp, ignore_errors=True)
    zip_path.unlink(missing_ok=True)

    return {
        "include": (abi_dest / "include" / f"python{py_ver}").resolve().as_posix(),
        "lib_dir": (abi_dest / "jniLibs" / abi).resolve().as_posix(),
        "py_ver": py_ver,
    }


def main():
    parser = argparse.ArgumentParser(description="Stratum Stage 07 - Build stratum.so (v9)")
    parser.add_argument("--cpp", required=True, help="06_cpp_emit/output/")
    parser.add_argument("--setup", required=True, help="00_setup/output/setup_report.json")
    parser.add_argument("--nanobind", required=True, help="nanobind source dir")
    parser.add_argument("--templates", default="07_build/templates")
    parser.add_argument("--abi", default="arm64-v8a", choices=["arm64-v8a", "armeabi-v7a", "x86_64", "x86"])
    parser.add_argument("--chaquopy", default="3.12.0-0")
    parser.add_argument("--output", required=True)
    parser.add_argument("--log-level", type=int, default=0, choices=[0, 1, 2],
                         help="0=production (stripped), 1=basic, 2=deep/trace. Default: 0")
    args = parser.parse_args()

    print_header("STRATUM PIPELINE — STAGE 07 (BUILD) v9")

    setup = json.loads(Path(args.setup).read_text(encoding="utf-8"))
    cmake_exe = setup.get("cmake_path", "cmake")
    ndk_path = Path(setup["ndk_path"])
    android_api = setup.get("ndk_api", "24")

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    build_dir = output_dir / "build" / args.abi

    ninja_exe = find_ninja(ndk_path)
    if not ninja_exe:
        print("ERROR: ninja not found. Run: pip install ninja")
        sys.exit(1)

    target_dir = output_dir / "python-target"
    target_dir.mkdir(parents=True, exist_ok=True)
    chaquopy_paths = ensure_chaquopy_target(target_dir, args.chaquopy, args.abi)

    tpl_dir = Path(args.templates)
    cpp_dir = Path(args.cpp)

    init_vars = {
        "INC_ABS": chaquopy_paths["include"],
        "HOST_PYTHON": Path(sys.executable).resolve().as_posix(),
        "PY_VER": chaquopy_paths["py_ver"],
        "PY_MAJOR": chaquopy_paths["py_ver"].split(".")[0],
        "PY_MINOR": chaquopy_paths["py_ver"].split(".")[1],
    }
    init_file = build_dir / "StratumInit.cmake"
    build_dir.mkdir(parents=True, exist_ok=True)
    init_file.write_text(render_template(tpl_dir / "StratumInit.cmake.tpl", init_vars), encoding="utf-8")

    cmake_vars = {
        "NANOBIND_DIR": Path(args.nanobind).resolve().as_posix(),
        "CORE_INCLUDE_DIR": (cpp_dir / "core").resolve().as_posix(),
        "PYTHON_VERSION": chaquopy_paths["py_ver"],
        "PYTHON_INCLUDE": chaquopy_paths["include"],
        "PYTHON_LIB_DIR": chaquopy_paths["lib_dir"],
    }
    cmake_file = output_dir / "CMakeLists.txt"
    cmake_file.write_text(render_template(tpl_dir / "CMakeLists.txt.tpl", cmake_vars), encoding="utf-8")

    toolchain = ndk_path / "build" / "cmake" / "android.toolchain.cmake"
    platform_str = resolve_android_platform(android_api)

    t0 = time.time()
    cfg_cmd = [
        cmake_exe, str(output_dir),
        f"-B{build_dir}",
        f"-DCMAKE_TOOLCHAIN_FILE={toolchain}",
        f"-DANDROID_ABI={args.abi}",
        f"-DANDROID_PLATFORM=android-{platform_str}",
        "-DANDROID_STL=c++_static",
        "-DCMAKE_BUILD_TYPE=Release",
        "-G", "Ninja",
        f"-DCMAKE_MAKE_PROGRAM={ninja_exe}",
        f"-DCMAKE_PROJECT_TOP_LEVEL_INCLUDES={init_file.resolve().as_posix()}",
        f"-DSTRATUM_LOG_LEVEL={args.log_level}",
    ]
    subprocess.run(cfg_cmd, check=True)

    bld_cmd = [cmake_exe, "--build", str(build_dir), "--config", "Release", "--parallel"]
    subprocess.run(bld_cmd, check=True)
    elapsed = round(time.time() - t0, 2)

    so_src = next(build_dir.rglob("_stratum.so"))
    so_dest = output_dir / "_stratum.so"
    shutil.copy2(so_src, so_dest)

    size_mb = round(so_dest.stat().st_size / (1024 * 1024), 2)
    print("\n==================================================")
    print(f" BUILD SUCCESS: _stratum.so ({size_mb} MB) compiled in {elapsed}s!")
    print("==================================================")


if __name__ == "__main__":
    main()