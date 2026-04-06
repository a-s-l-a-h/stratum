import argparse
import json
import subprocess
import sys
import shutil
import platform
from pathlib import Path
from datetime import datetime
from urllib.request import urlretrieve
# --- Helper Functions ---

def get_executable(base_path, cmd_name):
    """Safely find an executable, fully cross-platform (Windows/Mac/Linux)"""
    is_windows = platform.system() == "Windows"
    exe_name = f"{cmd_name}.exe" if is_windows else cmd_name
    
    if base_path:
        target = Path(base_path) / "bin" / exe_name
        if target.exists():
            return str(target.resolve())
            
    sys_path = shutil.which(cmd_name)
    return str(Path(sys_path).resolve()) if sys_path else None

# --- Verification & Setup Functions ---

def check_python():
    """Ensure Python is 3.10+"""
    v = sys.version_info
    version_str = f"{v.major}.{v.minor}.{v.micro}"
    if v.major == 3 and v.minor >= 10:
        return True, f"Found v{version_str}"
    return False, f"Found v{version_str}. Require Python 3.10+"

def check_jinja2():
    """Ensure Jinja2 is installed via pip"""
    try:
        import jinja2
        return True, f"Found v{jinja2.__version__}"
    except ImportError:
        return False, "Not installed. Run: pip install jinja2"

def setup_chaquopy(version):
    """Download available Chaquopy target ZIPs to third_party to cache them. Ignores missing ABIs."""
    project_root = Path(__file__).parent.parent
    chaquo_dir = project_root / "third_party" / "chaquopy" / version
    chaquo_dir.mkdir(parents=True, exist_ok=True)
    
    abis = ["arm64-v8a", "armeabi-v7a", "x86_64", "x86"]
    base_url = "https://repo1.maven.org/maven2/com/chaquo/python/target"
    
    msgs = []
    success_count = 0
    
    for abi in abis:
        zip_name = f"target-{version}-{abi}.zip"
        zip_path = chaquo_dir / zip_name
        
        if zip_path.exists():
            msgs.append(f"{abi} (cached)")
            success_count += 1
            continue
            
        try:
            urlretrieve(f"{base_url}/{version}/{zip_name}", zip_path)
            msgs.append(f"{abi} (dl)")
            success_count += 1
        except Exception as e:
            # 404 means the architecture isn't provided for this Python version. Skip it.
            msgs.append(f"{abi} (skipped/404)")
            if zip_path.exists():
                zip_path.unlink() # cleanup empty/failed file
                
    if success_count > 0:
        return True, f"Found {success_count}/4 ABIs: {', '.join(msgs)}"
    else:
        return False, f"Failed to find ANY architecture for {version}. Is the version number correct?"

def setup_nanobind(version):
    """Check for Nanobind in third_party/. If missing, clone specific version WITH SUBMODULES."""
    project_root = Path(__file__).parent.parent
    third_party_dir = project_root / "third_party"
    third_party_dir.mkdir(parents=True, exist_ok=True)
    
    nano_dir = third_party_dir / "nanobind"
    nano_header = nano_dir / "include" / "nanobind" / "nanobind.h"
    # Check if robin_map exists to verify submodules actually cloned
    robin_map_header = nano_dir / "ext" / "robin_map" / "include" / "tsl" / "robin_map.h"

    if nano_header.exists() and robin_map_header.exists():
        return True, str(nano_dir.resolve()), f"Found at {nano_dir} (Submodules OK)"

    print(f"\n---> Nanobind (or its submodules) missing. Cloning {version} recursively...")
    
    git_exe = shutil.which("git")
    if not git_exe:
        return False, None, "Command 'git' not found on PATH. Please install Git to download nanobind."

    try:
        if nano_dir.exists():
            shutil.rmtree(nano_dir)
            
        # Clone with --recursive to fetch nanobind's dependencies (like robin_map)
        # --shallow-submodules keeps the download fast by skipping git history
        subprocess.run([
            git_exe, "clone", 
            "--recursive", 
            "--shallow-submodules",
            "--depth", "1", 
            "--branch", version, 
            "https://github.com/wjakob/nanobind.git", 
            str(nano_dir)
        ], check=True)
        
        return True, str(nano_dir.resolve()), f"Successfully cloned {version} with submodules to third_party/nanobind"
    except subprocess.CalledProcessError as e:
        return False, None, f"Git clone failed: {e}"
    except Exception as e:
        return False, None, f"Error setting up nanobind: {e}"

def check_javap(jdk_path):
    """Find javap and ensure it is version 17+"""
    javap_path = get_executable(jdk_path, "javap")
    if not javap_path:
        return False, None, "Command 'javap' not found. Ensure Java is installed or pass --jdk-path."
        
    try:
        result = subprocess.run([javap_path, "-version"], capture_output=True, text=True, check=True)
        output = result.stdout.strip() or result.stderr.strip()
        version_str = output.split()[1] if len(output.split()) > 1 else output
        
        major_version = int(version_str.split('.')[0])
        if major_version >= 17:
            return True, javap_path, f"Found v{version_str} at {javap_path}"
        else:
            return False, javap_path, f"Found v{version_str}. Require Java 17+."
    except Exception as e:
        return False, javap_path, f"Failed to execute javap: {e}"

def check_cmake(sdk_path, explicit_cmake_path):
    """Find CMake and check version 3.15+"""
    cmake_path = None
    
    if explicit_cmake_path:
        cmake_path = get_executable(explicit_cmake_path, "cmake") or explicit_cmake_path
    elif sdk_path:
        cmake_dirs = list((Path(sdk_path) / "cmake").glob("*"))
        if cmake_dirs:
            latest_cmake = sorted(cmake_dirs)[-1]
            cmake_path = get_executable(latest_cmake, "cmake")
            
    if not cmake_path:
        cmake_path = get_executable(None, "cmake")
        
    if not cmake_path or not Path(cmake_path).exists():
        return False, None, "Command 'cmake' not found. Install CMake or pass --cmake-path."

    try:
        result = subprocess.run([cmake_path, "--version"], capture_output=True, text=True, check=True)
        version_str = result.stdout.splitlines()[0].split()[2]
        
        v_parts = version_str.split('-')[0].split('.')
        major, minor = int(v_parts[0]), int(v_parts[1])
        
        if (major == 3 and minor >= 15) or (major > 3):
            return True, str(Path(cmake_path).resolve()), f"Found v{version_str} at {cmake_path}"
        else:
            return False, cmake_path, f"Found v{version_str}. Require CMake 3.15+."
    except Exception as e:
        return False, cmake_path, f"Failed to execute cmake: {e}"

def check_android_jar(sdk_path, explicit_jar_path, api_version):
    """Ensure Android jar exists for the requested API version"""
    if explicit_jar_path:
        jar_path = Path(explicit_jar_path)
    elif sdk_path:
        jar_path = Path(sdk_path) / "platforms" / f"android-{api_version}" / "android.jar"
    else:
        return False, None, "No path provided. Must provide --jar-path or --sdk-path."

    if jar_path.exists():
        return True, str(jar_path.resolve()), f"Found at {jar_path}"
    else:
        return False, None, f"android.jar not found at {jar_path}. Check the path."

def check_ndk_clang(ndk_path):
    """Find clang++ in NDK (Requires r25+)"""
    if not ndk_path:
        return False, None, "NDK path not provided. Must provide --ndk-path."

    ndk_dir = Path(ndk_path)
    if not ndk_dir.exists():
        return False, None, f"Provided NDK path does not exist: {ndk_path}"

    host_os = platform.system().lower()
    if host_os == "darwin":
        host_dir = "darwin-x86_64"
    elif host_os == "windows":
        host_dir = "windows-x86_64"
    else:
        host_dir = "linux-x86_64"

    clang_path = ndk_dir / "toolchains" / "llvm" / "prebuilt" / host_dir / "bin" / ("clang++.exe" if host_os == "windows" else "clang++")
    
    if not clang_path.exists():
        return False, None, f"clang++ not found at {clang_path}. Is this a valid NDK directory?"

    ndk_props = ndk_dir / "source.properties"
    is_r25_plus = False
    rev_str = "Unknown"
    
    if ndk_props.exists():
        with open(ndk_props, 'r') as f:
            for line in f:
                if "Pkg.Revision" in line:
                    rev_str = line.split("=")[1].strip()
                    try:
                        major = int(rev_str.split(".")[0])
                        if major >= 25:
                            is_r25_plus = True
                    except ValueError:
                        pass
                    break

    if is_r25_plus:
        return True, str(ndk_dir.resolve()), f"Found clang++ (NDK v{rev_str})"
    else:
        return False, str(ndk_dir.resolve()), f"Found NDK v{rev_str}. Require NDK r25 or higher."

# --- Main Execution ---

def main():
    parser = argparse.ArgumentParser(description="Stratum Stage 00 - Setup and Validation")
    parser.add_argument("--jdk-path", type=str, help="Path to JDK 17+ (Optional if JAVA_HOME/javap is on PATH)")
    parser.add_argument("--ndk-path", type=str, required=True, help="Path to Android NDK (r25+)")
    parser.add_argument("--sdk-path", type=str, help="Path to full Android SDK (Optional if --jar-path is used)")
    parser.add_argument("--jar-path", type=str, help="Direct path to android.jar (Bypasses --sdk-path)")
    parser.add_argument("--cmake-path", type=str, help="Direct path to CMake (Optional if cmake is on PATH)")
    
    parser.add_argument("--api-version", type=str, default="35", help="Target Android API version (Default: 35)")
    parser.add_argument("--ndk-api", type=str, default="24", help="NDK compile target API = your app minSdkVersion (Default: 24)")
    parser.add_argument("--nanobind-version", type=str, default="v2.12.0", help="Nanobind release version to clone (Default: v2.12.0)")
    parser.add_argument("--chaquopy-version", type=str, default="3.12.0-0", help="Chaquopy version to cache (Default: 3.12.0-0)")
    
    parser.add_argument("--output", type=str, required=True, help="Output directory for setup_report.json")
    args = parser.parse_args()

    print("==================================================")
    print("      STRATUM PIPELINE - STAGE 00 (SETUP)         ")
    print("==================================================\n")

    errors = []

    # 1. Python & Core Tools
    py_ok, py_msg = check_python()
    print(f"[{'OK' if py_ok else 'FAIL'}] Python 3.10+   : {py_msg}")
    if not py_ok: errors.append(py_msg)

    jinja_ok, jinja_msg = check_jinja2()
    print(f"[{'OK' if jinja_ok else 'FAIL'}] Jinja2         : {jinja_msg}")
    if not jinja_ok: errors.append(jinja_msg)

    nano_ok, nano_path, nano_msg = setup_nanobind(version=args.nanobind_version)
    print(f"[{'OK' if nano_ok else 'FAIL'}] Nanobind       : {nano_msg}")
    if not nano_ok: errors.append(nano_msg)

    chaquo_ok, chaquo_msg = setup_chaquopy(version=args.chaquopy_version)
    print(f"[{'OK' if chaquo_ok else 'FAIL'}] Chaquopy       : {chaquo_msg}")
    if not chaquo_ok: errors.append(chaquo_msg)

    # 2. Java Tools
    javap_ok, javap_path, javap_msg = check_javap(args.jdk_path)
    print(f"[{'OK' if javap_ok else 'FAIL'}] Java (javap)   : {javap_msg}")
    if not javap_ok: errors.append(javap_msg)

    # 3. C++ / Android Build Tools
    cmake_ok, cmake_path, cmake_msg = check_cmake(args.sdk_path, args.cmake_path)
    print(f"[{'OK' if cmake_ok else 'FAIL'}] CMake 3.15+    : {cmake_msg}")
    if not cmake_ok: errors.append(cmake_msg)

    jar_ok, jar_path, jar_msg = check_android_jar(args.sdk_path, args.jar_path, args.api_version)
    print(f"[{'OK' if jar_ok else 'FAIL'}] Android API {args.api_version} : {jar_msg}")
    if not jar_ok: errors.append(jar_msg)

    ndk_ok, ndk_resolved_path, ndk_msg = check_ndk_clang(args.ndk_path)
    print(f"[{'OK' if ndk_ok else 'FAIL'}] Android NDK    : {ndk_msg}")
    if not ndk_ok: errors.append(ndk_msg)

    # Compile Final Report
    all_ok = all([py_ok, jinja_ok, nano_ok, chaquo_ok, javap_ok, cmake_ok, jar_ok, ndk_ok])

    report = {
        "all_ok": all_ok,
        "javap_path": javap_path,
        "javap_version": javap_msg if javap_ok else None,
        "ndk_path": ndk_resolved_path,
        "sdk_path": str(Path(args.sdk_path).resolve()) if args.sdk_path else None,
        "jar_path": jar_path,
        "cmake_path": cmake_path,
        "cmake_version": cmake_msg if cmake_ok else None,
        "python_version": py_msg,
        "jinja2_ok": jinja_ok,
        "nanobind_present": nano_ok,
        "nanobind_path": nano_path,
        "android_api": args.api_version,
        "ndk_api": args.ndk_api,
        "timestamp": datetime.now().isoformat()
    }

    # Write Output to setup_report.json
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "setup_report.json"
    
    with open(out_file, "w") as f:
        json.dump(report, f, indent=2)

    print(f"\n-> Report written to: {out_file}\n")
    
    if not all_ok:
        print("==================================================")
        print(" SETUP FAILED: Please fix the following errors:")
        print("==================================================")
        for err in errors:
            print(f" X  {err}")
        print("\nExiting.")
        sys.exit(1)
    else:
        print("==================================================")
        print(" SETUP SUCCESS: All systems ready!")
        print("==================================================")

if __name__ == "__main__":
    main()