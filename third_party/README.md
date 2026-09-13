
# Stratum Third-Party Dependencies

## Overview

The `third_party/` directory houses cached binaries, submodules, and API data files required for building the native extension and sanitizing Android APIs.

---

## Directory Structure

```
third_party/
├── chaquopy/
│   └── 3.12.0-0/
│       ├── target-3.12.0-0-arm64-v8a.zip
│       ├── target-3.12.0-0-x86_64.zip
│       └── ...
├── nanobind/
│   ├── CMakeLists.txt
│   ├── include/nanobind/
│   └── ext/robin_map/
├── api_versions/
│   └── 35/
│       └── api-versions.xml (Optional, for 04_5_sanitize)
└── hiddenapi/
    └── 35/
        └── hiddenapi-flags.csv (Optional, for 04_5_sanitize)
```

---

## Dependency Descriptions

### 1. `chaquopy/`
* **Source**: Maven Central (`com.chaquo.python.target`).
* **Purpose**: Provides target Python headers (`Python.h`) and pre-built `libpythonX.Y.so` stub libraries for cross-compiling `_stratum.so` via Android NDK Clang.
* **Management**: Automatically downloaded and verified by Stage 00 (`00_setup/main.py`) and Stage 07 (`07_build/main.py`).

### 2. `nanobind/`
* **Source**: [https://github.com/wjakob/nanobind](https://github.com/wjakob/nanobind)
* **Purpose**: Ultra-lightweight binding library exposing C++ dispatch routines to CPython with minimal binary overhead.
* **Important**: Must be cloned recursively with submodules to include `ext/robin_map`:
  ```bash
  git clone --recursive --shallow-submodules --depth 1 --branch v2.12.0 https://github.com/wjakob/nanobind.git third_party/nanobind
  ```
  Stage 00 automatically performs this recursive clone if the directory is missing.

### 3. `api_versions/` (Optional)
* **Source**: `<Android-SDK>/platforms/android-<API>/data/api-versions.xml`.
* **Purpose**: Used by Stage 04.5 (`04_5_sanitize`) to annotate when classes, methods, and fields were introduced (`since`), deprecated, or removed in the Android SDK.

### 4. `hiddenapi/` (Optional)
* **Source**: Android Open Source Project (AOSP) internal build trees.
* **Purpose**: Used by Stage 04.5 (`04_5_sanitize`) to strip non-public APIs flagged as `blocked` or `restricted` by ART's hidden API enforcement policy.
