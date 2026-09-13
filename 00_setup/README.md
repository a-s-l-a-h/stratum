

# Stratum Pipeline — Stage 00: Setup & Environment Validation

## Overview

**Stage 00 (`00_setup/main.py`)** is the foundation and pre-flight gatekeeper of the entire Stratum compilation pipeline. It validates your host system, verifies cross-compilation toolchains, automates the acquisition of third-party C++/Python bindings, and generates `setup_report.json`.

Every downstream stage in the Stratum pipeline reads `setup_report.json` as its single source of truth. You configure your paths and environment once in Stage 00; subsequent stages (01 through 09) consume this report without requiring redundant path arguments.

```
       ┌──────────────────────────────────────────────────────────┐
       │                   Stage 00 (main.py)                     │
       └─────────────────────────────┬────────────────────────────┘
                                     │ Generates
                                     ▼
                        00_setup/setup_report.json
                                     │
         ┌───────────────────────────┼───────────────────────────┐
         ▼                           ▼                           ▼
   Stage 01 (Extract)          Stage 03 (Javap)            Stage 07 (Build)
  • jar_path                  • javap_path                • cmake_path
  • android_api                                           • ndk_path
                                                          • ndk_api
```

---

## What This Stage Does

1. **Python Environment Inspection**: Verifies Python 3.10+ and checks for required host packages (`jinja2`).
2. **Automated Third-Party Dependency Bootstrap**:
   - **Nanobind**: Inspects `third_party/nanobind/`. If missing or incomplete (e.g., missing the `tsl/robin_map` submodule), it clones the release recursively using shallow submodules.
   - **Chaquopy Target Caching**: Downloads and caches target ABI packages (`arm64-v8a`, `armeabi-v7a`, `x86_64`, `x86`) containing `Python.h` and target libraries directly from Maven Central into `third_party/chaquopy/<version>/`.
3. **Java Toolchain Verification**: Resolves and tests `javap`, ensuring the host JDK is version 17 or newer.
4. **Android Build System Verification**:
   - Locates and validates CMake (version 3.15+ required).
   - Resolves `android.jar` either directly or via the Android SDK tree.
   - Verifies the Android NDK (r25+ required) and ensures the host-specific Clang++ binary is functional.
5. **State File Generation**: Emits `setup_report.json` containing verified paths and tool versions.

---

## Prerequisites Matrix

| Requirement | Minimum Version | Purpose | Can Stage 00 Auto-Fetch? |
|---|---|---|---|
| **Python** | `3.10+` | Host pipeline runner | No (Host pre-installed) |
| **Jinja2** | Any modern | Template rendering (Stage 07) | No (`pip install jinja2`) |
| **Git** | Any | Cloning nanobind submodules | No (Must be on system `PATH`) |
| **JDK (`javap`)** | `17+` (e.g., Temurin 17) | Bytecode parsing in Stage 03 | No (Host pre-installed) |
| **CMake** | `3.15+` | Native build generator | No (Host/SDK pre-installed) |
| **Android NDK** | `r25c+` (LLVM 14+) | Compiling `_stratum.so` in Stage 07 | No (Must download NDK) |
| **android.jar** | API level 24–35 | Stub class definitions for reflection | No (Must supply jar/SDK) |
| **Nanobind** | `v2.12.0` (default) | C++17 Python binding runtime | **Yes** (Cloned automatically) |
| **Chaquopy Targets**| `3.12.0-0` (default) | Target Python headers & `.so` stubs | **Yes** (Downloaded from Maven) |

---

## Command-Line Reference

```bash
python 00_setup/main.py [OPTIONS] --ndk-path <PATH> --output <PATH>
```

### Required Arguments

| Argument | Type | Description |
|---|---|---|
| `--ndk-path` | `PATH` | Absolute or relative path to the Android NDK root folder (e.g., `android-ndk-r25c`). Requires NDK r25 or higher. |
| `--output` | `PATH` | Directory where `setup_report.json` will be written (standard: `00_setup/output/`). |

### Android Jar Resolution (Specify At Least One)

| Argument | Type | Description |
|---|---|---|
| `--jar-path` | `PATH` | Direct path to a standalone `android.jar`. **Recommended**: Bypasses the need for a full Android SDK directory. |
| `--sdk-path` | `PATH` | Path to the full Android SDK root. If passed, Stage 00 will resolve the jar automatically at `<sdk-path>/platforms/android-<api-version>/android.jar`. |

### Optional Toolchain Configuration

| Argument | Type | Default | Description |
|---|---|---|---|
| `--jdk-path` | `PATH` | `None` (System PATH) | Path to JDK root (where `bin/javap` resides). Useful if `JAVA_HOME` is not set or you have multiple JDKs installed. |
| `--cmake-path` | `PATH` | `None` (System PATH) | Direct path to the `cmake` executable or its directory. If omitted, checks `<sdk-path>/cmake/` and system `PATH`. |
| `--api-version` | `INT/STR` | `35` | The Android SDK API level to inspect (matches `android.jar`). |
| `--ndk-api` | `INT/STR` | `24` | The compilation target API level (`minSdkVersion`). Sets the minimum Android OS level that can load your native engine. |
| `--nanobind-version` | `STRING` | `v2.12.0` | Release tag of Nanobind to clone into `third_party/nanobind/`. |
| `--chaquopy-version` | `STRING` | `3.12.0-0` | Chaquopy target ZIP release to download. Format: `<py-ver>-<build>` (e.g., `3.12.0-0`, `3.10.13-0`). |

---

## Parameter Relationships & Compatibility

To avoid runtime linkage errors on Android devices, keep your parameters aligned with your final Android Studio configuration (`app/build.gradle`):

```
--api-version   ==   compileSdk / targetSdk   (Android Java API stubs)
--ndk-api       ==   minSdk                   (Device compatibility floor)
--jar-path      ==   android-<api-version>.jar
```

### Relationship Rules

1. **`--api-version` vs `--ndk-api`**:
   - `--api-version` defines **what Java classes and methods exist** during the code-generation stages (Stages 01–06).
   - `--ndk-api` defines **the minimum Android OS libc/ndk ABI** that the compiled C++ engine will target (Stage 07).
   - *Example*: Target modern Android 15 features using `--api-version 35`, while setting `--ndk-api 24` so devices running Android 7.0+ can still run the native engine.

2. **NDK r25c Platform Clamping**:
   - Android NDK r25c toolchain headers cap native compilation at API level 33. If you specify `--ndk-api` higher than 33, Stage 07 automatically clamps it to 33 to prevent linker errors (`crtbegin_dynamic.o not found`).

---

## Quick Start Examples

### Example 1: Recommended Setup (Standalone `android.jar`)

```bash
# Windows
python 00_setup/main.py ^
    --ndk-path "third_party/ndk25/android-ndk-r25c" ^
    --jar-path "third_party/android-35.jar" ^
    --api-version 35 ^
    --ndk-api 24 ^
    --chaquopy-version "3.12.0-0" ^
    --output "00_setup/output/"

# Linux / macOS
python 00_setup/main.py \
    --ndk-path "third_party/ndk25/android-ndk-r25c" \
    --jar-path "third_party/android-35.jar" \
    --api-version 35 \
    --ndk-api 24 \
    --chaquopy-version "3.12.0-0" \
    --output "00_setup/output/"
```

### Example 2: Using a Full Android SDK Installation

```bash
# Windows
python 00_setup/main.py ^
    --sdk-path "C:/Users/<User>/AppData/Local/Android/Sdk" ^
    --ndk-path "C:/Users/<User>/AppData/Local/Android/Sdk/ndk/25.2.9519653" ^
    --api-version 35 ^
    --ndk-api 24 ^
    --output "00_setup/output/"
```

### Example 3: Explicit JDK and Custom Tool Paths

```bash
python 00_setup/main.py \
    --jdk-path "/usr/lib/jvm/java-17-openjdk-amd64" \
    --cmake-path "/usr/local/bin/cmake" \
    --ndk-path "/opt/android-ndk-r25c" \
    --jar-path "third_party/android-35.jar" \
    --output "00_setup/output/"
```

---

## Detailed Step-by-Step Validation Logic

When you execute `00_setup/main.py`, the following validation pipeline runs in sequence:

### 1. Python & Jinja2 Validation
- Ensures the active Python interpreter is $\ge$ 3.10.
- Attempts `import jinja2`. Jinja2 is required in Stage 07 to render `CMakeLists.txt` and `StratumInit.cmake`.

### 2. Nanobind Setup & Integrity Check
- Inspects `third_party/nanobind/include/nanobind/nanobind.h`.
- **Submodule Verification**: Nanobind depends on an internal hash-map submodule (`tsl::robin_map`). Stage 00 explicitly checks for:
  ```
  third_party/nanobind/ext/robin_map/include/tsl/robin_map.h
  ```
- If the directory or submodule header is missing, it clones the specified tag recursively:
  ```bash
  git clone --recursive --shallow-submodules --depth 1 --branch <version> https://github.com/wjakob/nanobind.git third_party/nanobind
  ```

### 3. Chaquopy Target Downloading & Caching
- Downloads pre-built CPython static/dynamic libraries and include files from Maven Central for all 4 primary Android architectures:
  - `arm64-v8a`
  - `armeabi-v7a`
  - `x86_64`
  - `x86`
- Downloads are placed into: `third_party/chaquopy/<version>/target-<version>-<abi>.zip`.
- If an architecture returns an HTTP 404 (common when certain architectures are dropped in specific Python versions), Stage 00 skips it gracefully while ensuring at least one valid ABI was cached.

### 4. Java Disassembler (`javap`)
- Locates `javap` via `--jdk-path/bin/` or system `PATH`.
- Runs `javap -version` and parses the major version integer.
- Enforces version $\ge$ 17 (JDK 17 LTS is required to properly parse modern Android bytecode metadata, constant pools, and sealed class flags).

### 5. CMake Resolution
- Evaluates `--cmake-path`, `<sdk-path>/cmake/<latest_version>/bin/`, and system `PATH`.
- Runs `cmake --version` and checks for major/minor version $\ge$ 3.15.

### 6. Android Platform JAR
- Verifies the physical existence and read permissions of `android.jar`.

### 7. Android NDK & Clang Toolchain Check
- Parses `<ndk-path>/source.properties` to extract the `Pkg.Revision` string. Enforces major version $\ge$ 25.
- Tests for the host platform's cross-compiler binary under:
  ```
  <ndk-path>/toolchains/llvm/prebuilt/<host-os>-x86_64/bin/clang++
  ```

---

## Output: `setup_report.json`

On success, `00_setup/output/setup_report.json` is generated. Here is an annotated example:

```json
{
  "all_ok": true,
  "javap_path": "C:\\Program Files\\Eclipse Adoptium\\jdk-17.0.10\\bin\\javap.exe",
  "javap_version": "Found v17.0.10 at C:\\Program Files\\Eclipse Adoptium\\jdk-17.0.10\\bin\\javap.exe",
  "ndk_path": "E:\\Projects\\stratum\\stratum\\third_party\\ndk25\\android-ndk-r25c",
  "sdk_path": null,
  "jar_path": "E:\\Projects\\stratum\\stratum\\third_party\\android-35.jar",
  "cmake_path": "C:\\Program Files\\CMake\\bin\\cmake.exe",
  "cmake_version": "Found v3.28.1 at C:\\Program Files\\CMake\\bin\\cmake.exe",
  "python_version": "Found v3.11.8",
  "jinja2_ok": true,
  "nanobind_present": true,
  "nanobind_path": "E:\\Projects\\stratum\\stratum\\third_party\\nanobind",
  "android_api": "35",
  "ndk_api": "24",
  "timestamp": "2026-03-29T10:14:02.123456"
}
```

### Field Consumption Across Downstream Stages

| JSON Field | Consumed By | Usage in Pipeline |
|---|---|---|
| `all_ok` | All Stages | Must be `true`. Downstream scripts abort immediately if `false`. |
| `jar_path` | **Stage 01 (`01_extract`)** | Unpacked as a zip file to extract all `.class` files. |
| `android_api` | **Stage 01**, **Stage 02**, **Stage 04.5** | Tracks API version in extraction summaries and safety sanitizers. |
| `javap_path` | **Stage 03 (`03_javap`)** | Invoked across all targeted classes to extract JNI signatures. |
| `ndk_path` | **Stage 07 (`07_build`)** | Locates `android.toolchain.cmake` and Clang compiler. |
| `ndk_api` | **Stage 07 (`07_build`)** | Sets `-DANDROID_PLATFORM=android-<ndk_api>`. |
| `cmake_path` | **Stage 07 (`07_build`)** | Executed to configure and compile `_stratum.so`. |
| `nanobind_path`| **Stage 07 (`07_build`)** | Included as a CMake subdirectory target (`nanobind_add_module`). |

---

## Exit Codes & Diagnostics

| Exit Code | Meaning | Action |
|---|---|---|
| `0` | **Success** | All prerequisites met. Proceed to Stage 01 (`01_extract/main.py`). |
| `1` | **Validation Failure** | One or more components failed. Check the terminal errors and resolve each missing tool. |

---

## Troubleshooting Guide

### 1. `Command 'javap' not found` or `Require Java 17+`
- **Cause**: Java is not installed, or an outdated JDK (e.g., Java 8 or 11) is active on your `PATH`.
- **Solution**: Install [Eclipse Temurin JDK 17+](https://adoptium.net/temurin/releases/?version=17) and pass `--jdk-path "/path/to/jdk-17"`.

### 2. `Found NDK v... Require NDK r25 or higher`
- **Cause**: Stratum relies on modern C++17 nanobind features and Clang 14+ toolchains present only in NDK r25 or later.
- **Solution**: Download [Android NDK r25c](https://github.com/android/ndk/wiki/Unsupported-Downloads#r25c) and set `--ndk-path` to the unzipped root.

### 3. `Nanobind (or its submodules) missing` / Git Clone Failures
- **Cause**: Git is either missing from your `PATH` or the network blocked submodule cloning (`robin_map`).
- **Solution**: Verify `git --version`. Ensure network access to GitHub. If cloning manually, ensure submodules are initialized:
  ```bash
  git clone --recursive --depth 1 --branch v2.12.0 https://github.com/wjakob/nanobind.git third_party/nanobind
  ```

### 4. `Failed to find ANY architecture for <version>` (Chaquopy)
- **Cause**: The specified `--chaquopy-version` string does not exist on Maven Central or network access failed.
- **Solution**: Check the [Chaquopy Target Maven Repository](https://repo1.maven.org/maven2/com/chaquo/python/target/) to confirm the exact version string (e.g., `3.12.0-0` or `3.10.13-0`).

### 5. `ModuleNotFoundError: No module named 'jinja2'`
- **Cause**: The active Python virtual environment is missing Jinja2.
- **Solution**: Run `pip install jinja2`.

---

## Next Step in the Pipeline

Once Stage 00 completes with `[OK]`, proceed to **Stage 01: Extract**:

```bash
python 01_extract/main.py --setup "00_setup/output/setup_report.json" --output "01_extract/output/"
```