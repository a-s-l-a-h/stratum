#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Stage 07 — Download Chaquopy Python target, render templates, build stratum.so

Mirrors the lvpy builder approach exactly:
  1. Download Chaquopy target zip for the ABI  → gets libpython.so + Python.h
  2. Render StratumInit.cmake to the BUILD folder with ABSOLUTE paths
  3. Pass it via -DCMAKE_PROJECT_TOP_LEVEL_INCLUDES=<absolute_path>
  4. Render CMakeLists.txt with Chaquopy python include + lib paths
  5. cmake configure + build → stratum.so

Templates:
  07_build/templates/CMakeLists.txt.tpl   — main build file
  07_build/templates/StratumInit.cmake.tpl — nanobind Python hints (per-build)

Ninja:
  Auto-found: pip ninja → system PATH → NDK cmake folder → NDK prebuilt
  Passed via -DCMAKE_MAKE_PROGRAM — no system PATH needed.
"""

import argparse
import json
import shutil
import subprocess
import sys
import time
import zipfile
import sysconfig
from pathlib import Path
from urllib.request import urlretrieve


MAVEN_BASE = "https://repo1.maven.org/maven2/com/chaquo/python/target"


def print_header(title):
    print("==================================================")
    print(f" {title}")
    print("==================================================")


# ── Template renderer ─────────────────────────────────────────────────────────

def render_template(tpl_path: Path, variables: dict) -> str:
    text = tpl_path.read_text(encoding="utf-8")
    for key, value in variables.items():
        text = text.replace(f"{{{{{key}}}}}", value)
    return text


# ── NDK API cap ───────────────────────────────────────────────────────────────

def resolve_android_platform(android_api: str, ndk_max: int = 33) -> str:
    """
    NDK r25c max is API 33.
    'latest' causes --target=aarch64-none-linux-androidlatest which breaks
    the linker (crtbegin_dynamic.o not found). Always return a real number.
    """
    try:
        api_int = int(android_api)
        if api_int > ndk_max:
            print(f"   NOTE: API {android_api} > NDK r25c max ({ndk_max})."
                  f" Capping to {ndk_max}.")
            return str(ndk_max)
    except ValueError:
        pass
    return android_api


# ── Ninja finder ──────────────────────────────────────────────────────────────

def find_ninja(ndk_path: Path):
    """
    Find ninja. Returns full absolute path or None.
    Order: pip ninja → system PATH → NDK cmake folder → NDK prebuilt
    """
    # 1. pip ninja package — recommended
    try:
        import ninja as ninja_pkg
        for name in ["ninja.exe", "ninja"]:
            candidate = Path(ninja_pkg.BIN_DIR) / name
            if candidate.exists():
                return str(candidate)
    except ImportError:
        pass

    # 2. System PATH
    found = shutil.which("ninja")
    if found:
        return found

    # 3. NDK cmake subfolder — NDK ships its own ninja here
    ndk_cmake = ndk_path / "cmake"
    if ndk_cmake.exists():
        for version_dir in sorted(ndk_cmake.iterdir(), reverse=True):
            for name in ["ninja.exe", "ninja"]:
                candidate = version_dir / "bin" / name
                if candidate.exists():
                    return str(candidate)

    # 4. NDK prebuilt bin — scan all subfolders
    prebuilt = ndk_path / "prebuilt"
    if prebuilt.exists():
        for subfolder in prebuilt.iterdir():
            for name in ["ninja.exe", "ninja"]:
                candidate = subfolder / "bin" / name
                if candidate.exists():
                    return str(candidate)

    return None


# ── Chaquopy target downloader ────────────────────────────────────────────────

def ensure_chaquopy_target(
    target_dir: Path,
    chaquopy_version: str,
    abi: str,
):
    """
    Download and extract Chaquopy Python target for the given ABI.
    Returns dict with 'include' and 'lib_dir' absolute posix paths, or None.
    """
    py_ver = ".".join(chaquopy_version.split(".")[:2])  # e.g. "3.12.0-0" → "3.12"
    abi_dest = target_dir / abi

    python_h = abi_dest / "include" / f"python{py_ver}" / "Python.h"
    libpython = abi_dest / "jniLibs" / abi / f"libpython{py_ver}.so"

    # Already present — skip download
    if python_h.exists() and libpython.exists():
        print(f"   Chaquopy {abi}: already present, skipping download")
        return {
            "include": python_h.parent.resolve().as_posix(),
            "lib_dir": libpython.parent.resolve().as_posix(),
            "py_ver":  py_ver,
        }

    # Locate cached zip or Download
    zip_name = f"target-{chaquopy_version}-{abi}.zip"
    zip_path = target_dir / zip_name
    url      = f"{MAVEN_BASE}/{chaquopy_version}/{zip_name}"
    
    # Check third_party cache first
    project_root = Path(__file__).parent.parent
    cached_zip = project_root / "third_party" / "chaquopy" / chaquopy_version / zip_name
    
    if cached_zip.exists():
        print(f"   Copying {zip_name} from third_party cache ...")
        shutil.copy2(cached_zip, zip_path)
    else:
        print(f"   Downloading {zip_name} ...")
        print(f"   URL: {url}")
        try:
            urlretrieve(url, zip_path)
        except Exception as e:
            print(f"   ERROR: Download failed: {e}")
            return None

    # Extract
    extract_tmp = target_dir / f"_raw_{abi}"
    print(f"   Extracting ...")
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(extract_tmp)

    # Find the root inside the zip (may be one level deep)
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

    # Verify
    python_h  = abi_dest / "include" / f"python{py_ver}" / "Python.h"
    libpython = abi_dest / "jniLibs" / abi / f"libpython{py_ver}.so"

    if not python_h.exists():
        print(f"   ERROR: Python.h not found after extraction: {python_h}")
        return None
    if not libpython.exists():
        print(f"   ERROR: libpython{py_ver}.so not found after extraction: {libpython}")
        return None

    print(f"   OK: {abi_dest}")
    return {
        "include": python_h.parent.resolve().as_posix(),
        "lib_dir": libpython.parent.resolve().as_posix(),
        "py_ver":  py_ver,
    }


# ── Step A — Render templates ─────────────────────────────────────────────────

def step_a(
    tpl_dir:          Path,
    cpp_dir:          Path,
    nanobind_dir:     Path,
    output_dir:       Path,
    build_dir:        Path,
    chaquopy_paths:   dict,    # from ensure_chaquopy_target()
) -> tuple:

    core_files = sorted((cpp_dir / "core").rglob("*.cpp"))
    gen_files  = sorted((cpp_dir / "generated").rglob("*.cpp"))
    all_cpp    = core_files + gen_files

    if not all_cpp:
        raise RuntimeError(f"No .cpp files found under {cpp_dir}")

    def fwd(p) -> str:
        return str(Path(p).resolve()).replace("\\", "/")

    src_lines = "\n".join(f"    {fwd(f)}" for f in all_cpp)

    py_ver   = chaquopy_paths["py_ver"]
    py_inc   = chaquopy_paths["include"]    # absolute posix
    py_lib   = chaquopy_paths["lib_dir"]    # absolute posix
    py_major = py_ver.split(".")[0]
    py_minor = py_ver.split(".")[1]

    host_python = Path(sys.executable).resolve().as_posix()

    # ── StratumInit.cmake → build_dir (ABSOLUTE path) ─────────────────────
    build_dir.mkdir(parents=True, exist_ok=True)
    init_vars = {
        "INC_ABS":     py_inc,
        "HOST_PYTHON": host_python,
        "PY_VER":      py_ver,
        "PY_MAJOR":    py_major,
        "PY_MINOR":    py_minor,
    }
    init_text = render_template(tpl_dir / "StratumInit.cmake.tpl", init_vars)
    init_file = build_dir / "StratumInit.cmake"
    init_file.write_text(init_text, encoding="utf-8")
    print(f"   StratumInit : {init_file}")

    # ── CMakeLists.txt → output_dir ───────────────────────────────────────
    cmake_vars = {
        "NANOBIND_DIR":         fwd(nanobind_dir),
        "SOURCE_FILES":         src_lines,
        "CORE_INCLUDE_DIR":     fwd(cpp_dir / "core"),
        "NANOBIND_INCLUDE_DIR": fwd(nanobind_dir / "include"),
        "PYTHON_VERSION":       py_ver,
        "PYTHON_INCLUDE":       py_inc,
        "PYTHON_LIB_DIR":       py_lib,
    }
    cmake_text = render_template(tpl_dir / "CMakeLists.txt.tpl", cmake_vars)
    cmake_file = output_dir / "CMakeLists.txt"
    cmake_file.write_text(cmake_text, encoding="utf-8")
    print(f"   CMakeLists  : {cmake_file}")
    print(f"   Sources     : {len(all_cpp)} .cpp files")
    print(f"   Python      : {py_ver}  include={py_inc}")
    print(f"   libpython   : {py_lib}")

    return cmake_file, init_file


# ── Step B — CMake configure + build ─────────────────────────────────────────

def step_b(
    cmake_exe:    str,
    cmake_file:   Path,
    init_file:    Path,
    ninja_exe:    str,
    ndk_path:     Path,
    build_dir:    Path,
    output_dir:   Path,
    android_abi:  str,
    android_api:  str,
    verbose_log:  bool,
    ultra_log:    bool,
) -> dict:

    toolchain = ndk_path / "build" / "cmake" / "android.toolchain.cmake"
    if not toolchain.exists():
        return {"success": False,
                "error": f"NDK toolchain not found: {toolchain}"}

    platform_str = resolve_android_platform(android_api)

    # Clear stale CMakeCache
    cmake_cache = build_dir / "CMakeCache.txt"
    if cmake_cache.exists():
        cmake_cache.unlink()
        print("   NOTE: Cleared stale CMakeCache.txt")

    # ── LOGGING FLAGS ──
    cxx_flags_list = []
    if ultra_log:
        cxx_flags_list.append("-DSTRATUM_ULTRA_LOG=1")
    elif verbose_log:
        cxx_flags_list.append("-DSTRATUM_VERBOSE_LOG=1")
        
    cxx_flags = " ".join(cxx_flags_list)

    configure_cmd = [
        cmake_exe,
        str(cmake_file.parent),
        f"-B{build_dir}",
        f"-DCMAKE_TOOLCHAIN_FILE={toolchain}",
        f"-DANDROID_ABI={android_abi}",
        f"-DANDROID_PLATFORM=android-{platform_str}",
        "-DANDROID_STL=c++_static",
        "-DCMAKE_BUILD_TYPE=Release",
        "-G", "Ninja",
        f"-DCMAKE_MAKE_PROGRAM={ninja_exe}",
        f"-DCMAKE_PROJECT_TOP_LEVEL_INCLUDES={init_file.resolve().as_posix()}",
        f"-DCMAKE_CXX_FLAGS={cxx_flags}",
    ]

    print()
    print("-- CMake configure --")
    print(" ".join(f'"{c}"' if " " in str(c) else str(c) for c in configure_cmd))
    print()

    t0  = time.time()
    cfg = subprocess.run(configure_cmd, text=True)
    if cfg.returncode != 0:
        return {"success": False, "error": "cmake configure failed",
                "build_time_seconds": round(time.time() - t0, 1)}

    build_cmd = [cmake_exe, "--build", str(build_dir),
                 "--config", "Release", "--parallel"]
    print()
    print("-- CMake build --")
    print(" ".join(str(c) for c in build_cmd))
    print()

    bld     = subprocess.run(build_cmd, text=True)
    elapsed = round(time.time() - t0, 1)

    if bld.returncode != 0:
        return {"success": False, "error": "cmake build failed",
                "build_time_seconds": elapsed}

    so_files = list(build_dir.rglob("_stratum.so"))
    if not so_files:
        return {"success": False,
                "error": "Build succeeded but _stratum.so not found",
                "build_time_seconds": elapsed}

    so_dest = output_dir / "_stratum.so"
    shutil.copy2(so_files[0], so_dest)

    return {
        "success":            True,
        "so_path":            str(so_dest),
        "so_size_mb":         round(so_dest.stat().st_size / (1024*1024), 2),
        "abi":                android_abi,
        "android_platform":   platform_str,
        "build_time_seconds": elapsed,
    }


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Stratum Stage 07 - Build stratum.so")
    parser.add_argument("--cpp",       required=True,
        help="06_cpp_emit/output/")
    parser.add_argument("--setup",     required=True,
        help="00_setup/output/setup_report.json")
    parser.add_argument("--nanobind",  required=True,
        help="nanobind source dir")
    parser.add_argument("--templates", default="07_build/templates",
        help="07_build/templates/ (default: 07_build/templates)")
    parser.add_argument("--abi",       default="arm64-v8a",
        choices=["arm64-v8a", "armeabi-v7a", "x86_64", "x86"])
    parser.add_argument("--chaquopy",  default="3.12.0-0",
        help="Chaquopy target version e.g. 3.12.0-0 (default: 3.12.0-0)")
    parser.add_argument("--output",    required=True,
        help="07_build/output/")
        
    # LOGGING ARGUMENTS (Updated)
    parser.add_argument("--verbose-log", action="store_true",
        help="Enable LOGD statements (method entry/exit, class init, IDs)")
    parser.add_argument("--ultra-log", action="store_true",
        help="Enable ultra-deep LOGV tracing (EVERY single JNI call, argument, and return value)")
  
    args = parser.parse_args()

    print_header("STRATUM PIPELINE - STAGE 07 (BUILD)")

    setup      = json.loads(Path(args.setup).read_text(encoding="utf-8"))
    cmake_exe  = setup.get("cmake_path", "cmake")
    ndk_path   = Path(setup["ndk_path"])
    android_api = setup.get("ndk_api", setup.get("android_api", "21"))

    print(f"-> CMake      : {cmake_exe}")
    print(f"-> NDK        : {ndk_path}")
    print(f"-> API        : {android_api}")
    print(f"-> ABI        : {args.abi}")
    print(f"-> Chaquopy   : {args.chaquopy}")
    print(f"-> Python     : {sys.executable}")
    
    if args.ultra_log:
        print(f"-> Logging    : ULTRA (LOGV deep tracing)")
    elif args.verbose_log:
        print(f"-> Logging    : VERBOSE (LOGD diagnostics)")
    else:
        print(f"-> Logging    : OFF (Production / Errors only)")

    cpp_dir      = Path(args.cpp)
    nanobind_dir = Path(args.nanobind)
    tpl_dir      = Path(args.templates)
    output_dir   = Path(args.output)

    build_dir = output_dir / "build" / args.abi

    for p, label in [
        (cpp_dir,      "06_cpp_emit/output/"),
        (nanobind_dir, "nanobind source"),
        (ndk_path,     "NDK"),
        (tpl_dir,      "07_build/templates/"),
    ]:
        if not p.exists():
            print(f"ERROR: Not found: {p}  ({label})")
            sys.exit(1)

    # ── Find Ninja ─────────────────────────────────────────────────────────────
    print()
    print("-> Finding ninja ...")
    ninja_exe = find_ninja(ndk_path)
    if not ninja_exe:
        print("ERROR: ninja not found.")
        print("  Fix:  pip install ninja")
        sys.exit(1)
    print(f"   ninja : {ninja_exe}")

    output_dir.mkdir(parents=True, exist_ok=True)

    # ── Download Chaquopy Python target ────────────────────────────────────────
    print()
    print(f"-> Chaquopy Python target ({args.abi}) ...")
    target_dir = output_dir / "python-target"
    target_dir.mkdir(parents=True, exist_ok=True)

    chaquopy_paths = ensure_chaquopy_target(target_dir, args.chaquopy, args.abi)
    if not chaquopy_paths:
        print("ERROR: Failed to get Chaquopy Python target.")
        sys.exit(1)

    # ── Step A — Render templates ──────────────────────────────────────────────
    print()
    print("-> Step A: Rendering templates ...")
    try:
        cmake_file, init_file = step_a(
            tpl_dir        = tpl_dir,
            cpp_dir        = cpp_dir,
            nanobind_dir   = nanobind_dir,
            output_dir     = output_dir,
            build_dir      = build_dir,
            chaquopy_paths = chaquopy_paths,
        )
    except Exception as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    # ── Step B — Build ─────────────────────────────────────────────────────────
    print()
    print("-> Step B: Building stratum.so ...")
    report = step_b(
        cmake_exe   = cmake_exe,
        cmake_file  = cmake_file,
        init_file   = init_file,
        ninja_exe   = ninja_exe,
        ndk_path    = ndk_path,
        build_dir   = build_dir,
        output_dir  = output_dir,
        android_abi = args.abi,
        android_api = android_api,
        verbose_log = args.verbose_log,
        ultra_log   = args.ultra_log,
    )

    report["chaquopy_version"] = args.chaquopy
    report_file = output_dir / "build_report.json"
    report_file.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print()
    print_header("STAGE 07 COMPLETE")

    if report["success"]:
        print(f"-> _stratum.so : {report['so_path']}")
        print(f"-> Size       : {report['so_size_mb']} MB")
        print(f"-> ABI        : {report['abi']}")
        print(f"-> Platform   : android-{report['android_platform']}")
        print(f"-> Chaquopy   : {args.chaquopy}")
        print(f"-> Build time : {report['build_time_seconds']}s")
        print()
        print("Build succeeded. Proceed to Stage 08 (pyi stubs).")
    else:
        print(f"-> FAILED : {report.get('error')}")
        print()
        print("Check compiler output above.")
        print("Fix C++ in 06_cpp_emit, rerun Stage 06 then Stage 07.")
        sys.exit(1)


if __name__ == "__main__":
    main()