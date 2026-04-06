# Stratum Pipeline — Stage 00: Setup & Validation

## Quick Start

```bash
python 00_setup/main.py --ndk-path "C:\Projects\stratum\stratum\third_party\ndk25\android-ndk-r25c" --jar-path "C:\Projects\stratum\stratum\third_party\android-35.jar" --api-version 35 --ndk-api 24 --chaquopy-version "3.10.13-0" --output "00_setup/output/"
```

> The paths above assume the following layout inside your project's `third_party/` folder:
> - `third_party/android-35.jar` — downloaded from [github.com/sable/android-platforms](https://github.com/sable/android-platforms)
> - `third_party/ndk25/android-ndk-r25c` — downloaded from [dl.google.com/android/repository/android-ndk-r25c-windows.zip](https://dl.google.com/android/repository/android-ndk-r25c-windows.zip) (see NDK section below for all platforms)
>
> Adjust both paths to wherever you extracted these on your machine.

---

## What This Stage Does

Stage 00 validates your build environment and writes a `setup_report.json` to the output directory. Every later stage (01–08) reads this file as its single source of truth — you never pass paths again after Stage 00.

---

## Command-Line Arguments

### Required

| Argument | Type | Description |
|---|---|---|
| `--ndk-path` | PATH | Path to your Android NDK folder (r25 or higher required). This is where `clang++` lives — the C++ compiler that builds your `_stratum.so` for Android. |
| `--output` | PATH | Directory where `setup_report.json` is written. Always use `"00_setup/output/"`. |

### Jar — Pick One

| Argument | Type | Description |
|---|---|---|
| `--jar-path` | PATH | *(Recommended)* Direct path to `android.jar` for the target API level. Used by `javap` in Stages 01–04 to understand Android class signatures. Has no effect on device compatibility or your compiled `.so`. |
| `--sdk-path` | PATH | Path to the full Android SDK root. Stage 00 auto-locates `android.jar` inside `<sdk-path>/platforms/android-<api-version>/android.jar`. Use this if you have the full SDK installed. |

### Optional

| Argument | Type | Default | Description |
|---|---|---|---|
| `--jdk-path` | PATH | system PATH | Path to your JDK 17+ installation folder. Only needed if `javap` is not on your PATH or `JAVA_HOME` is not set. |
| `--cmake-path` | PATH | system PATH | Path to a CMake 3.15+ installation folder. Only needed if `cmake` is not on your PATH. |
| `--api-version` | NUMBER | `35` | API level of the `android.jar` you are providing. Must match the jar file and your `compileSdk` in `build.gradle`. Used only in Stages 01–04 (javap reflection). |
| `--ndk-api` | NUMBER | `24` | Android API level your C++ code is compiled against. Determines the minimum Android version that can run your `.so`. Must equal your `minSdk` in `build.gradle`. |
| `--nanobind-version` | TAG | `v2.12.0` | Nanobind GitHub release tag to clone. Only cloned once — skipped if already present in `third_party/nanobind/`. |
| `--chaquopy-version` | STRING | `3.12.0-0` | Chaquopy target ZIP version to cache. Format: `<python-version>-<build>` e.g. `3.10.13-0`. |

---

## Argument Relationships

The following values **must match** each other and your `build.gradle`:

```
--api-version   ==  compileSdk   in build.gradle
--ndk-api       ==  minSdk       in build.gradle
--jar-path      ==  android-<api-version>.jar
```

**Example `build.gradle`:**
```groovy
compileSdk 35    // ← matches --api-version 35
targetSdk  35
minSdk     24    // ← matches --ndk-api 24
```

---

## Choosing `--ndk-api`

| Value | Android Version | Estimated Device Coverage |
|---|---|---|
| `24` | Android 7.0 | ~99% — recommended (Chaquopy minimum) |
| `26` | Android 8.0 | ~97% |
| `28` | Android 9.0 | ~95% |

> ⚠️ Do not set `--ndk-api` higher than `33` with NDK r25c. Stage 06 automatically caps values above 33 down to 33.

---

## How to Get the Required Tools

### android.jar

Download a standalone `android-<api>.jar` without installing the full Android SDK:

- **Source:** [github.com/sable/android-platforms](https://github.com/sable/android-platforms)
- Pick the release matching your `--api-version` (e.g. `android-35` → `android-35.jar`)
- Recommended location: `third_party/android-35.jar`

Pass it with:
```bash
--jar-path "C:\Projects\stratum\stratum\third_party\android-35.jar"
```

---

### Android NDK r25c

The NDK contains `clang++`, the C++ compiler used to build your `.so` for Android.

Direct download links:

| Platform | Link |
|---|---|
| Windows | [android-ndk-r25c-windows.zip](https://dl.google.com/android/repository/android-ndk-r25c-windows.zip) |
| macOS | [android-ndk-r25c-darwin.dmg](https://dl.google.com/android/repository/android-ndk-r25c-darwin.dmg) |
| Linux | [android-ndk-r25c-linux.zip](https://dl.google.com/android/repository/android-ndk-r25c-linux.zip) |

- Extract to: `third_party/ndk25/android-ndk-r25c`

Pass it with:
```bash
--ndk-path "C:\Projects\stratum\stratum\third_party\ndk25\android-ndk-r25c"
```

> NDK r25c supports compile targets up to API 33. Stage 06 automatically caps `--ndk-api` values above 33.

---

### Chaquopy

Chaquopy Python target ZIPs are downloaded automatically by Stage 00 from Maven Central — no manual step needed.

- **Maven index (browse available versions):** [repo1.maven.org/maven2/com/chaquo/python/target](https://repo1.maven.org/maven2/com/chaquo/python/target)
- Version format: `<python-version>-<build>` e.g. `3.10.13-0`, `3.12.0-0`
- Files are cached to `third_party/chaquopy/<version>/` after the first run

Pass the version with:
```bash
--chaquopy-version "3.10.13-0"
```

---

### JDK 17+

- **Download:** [adoptium.net/temurin/releases/?version=17](https://adoptium.net/temurin/releases/?version=17) — Eclipse Temurin JDK 17 LTS
- Pick your OS, select **JDK**, and download the installer (`.msi` for Windows, `.pkg` for macOS, `.tar.gz` for Linux)
- Only needed if `javap` is not already on your system PATH
- Skip `--jdk-path` if `java --version` works in your terminal

---

### CMake 3.15+

- **Download:** [cmake.org/download](https://cmake.org/download)
- Can also be installed via Android Studio: SDK Manager → SDK Tools → CMake
- Skip `--cmake-path` if `cmake --version` works in your terminal

---

### nanobind

Cloned automatically by Stage 00 from GitHub — no manual step needed.

- Cloned once to `third_party/nanobind/`, skipped on subsequent runs
- Default version: `v2.12.0` (tested and stable)
- Override with `--nanobind-version <tag>` if needed

---

## Output: `setup_report.json`

On success, Stage 00 writes the following file to your `--output` directory:

```json
{
  "all_ok": true,
  "javap_path": "C:\\Program Files\\Eclipse Adoptium\\jdk-17\\bin\\javap.exe",
  "javap_version": "Found v17.0.18 at C:\\Program Files\\Eclipse Adoptium\\jdk-17\\bin\\javap.exe",
  "ndk_path": "C:\\Projects\\stratum\\stratum\\third_party\\ndk25\\android-ndk-r25c",
  "sdk_path": null,
  "jar_path": "C:\\Projects\\stratum\\stratum\\third_party\\android-35.jar",
  "cmake_path": "C:\\Program Files\\CMake\\bin\\cmake.exe",
  "cmake_version": "Found v4.3.0 at C:\\Program Files\\CMake\\bin\\cmake.exe",
  "python_version": "Found v3.14.0",
  "jinja2_ok": true,
  "nanobind_present": true,
  "nanobind_path": "C:\\Projects\\stratum\\stratum\\third_party\\nanobind",
  "android_api": "35",
  "ndk_api": "24",
  "timestamp": "2026-03-26T21:57:20.556701"
}
```

### Field Reference

| Field | Used By | Description |
|---|---|---|
| `all_ok` | All stages | `true` only if every check passed. Later stages abort if `false`. |
| `javap_path` | Stages 01–04 | Absolute path to the `javap` binary. |
| `javap_version` | Diagnostic | Human-readable version string from the javap check. |
| `ndk_path` | Stage 06 | Absolute path to the NDK root directory. |
| `sdk_path` | Optional | Absolute path to the Android SDK root, or `null` if `--jar-path` was used directly. |
| `jar_path` | Stages 01–04 | Absolute path to `android.jar` used for class reflection. |
| `cmake_path` | Stage 06 | Absolute path to the `cmake` binary. |
| `cmake_version` | Diagnostic | Human-readable version string from the CMake check. |
| `python_version` | Diagnostic | Python version found on the system. |
| `jinja2_ok` | Stage 05 | Whether Jinja2 was found. Required for template rendering. |
| `nanobind_present` | Stage 06 | Whether nanobind source was found or cloned. |
| `nanobind_path` | Stage 06 | Absolute path to the `third_party/nanobind` directory. |
| `android_api` | Stages 01–04 | The `--api-version` value passed in (jar API level). |
| `ndk_api` | Stage 06 | The `--ndk-api` value passed in (C++ compile target / minSdk). |
| `timestamp` | Diagnostic | ISO 8601 timestamp of when the report was generated. |

---

## Exit Behavior

| Result | Exit Code | Description |
|---|---|---|
| All checks passed | `0` | `setup_report.json` written, safe to proceed to Stage 01. |
| One or more checks failed | `1` | Errors printed to stdout. Fix each listed issue and re-run. |