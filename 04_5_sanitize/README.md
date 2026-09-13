

# Stratum Pipeline — Stage 04.5: JNI Safety Sanitizer

## Overview

Stage 04.5 is an **optional but strongly recommended safety filter** that sits directly between **Stage 04 (`04_parse`)** and **Stage 05 (`05_resolve` Pass 1)**.

### Why This Stage is Preferred for Production Builds
When Stage 03 disassembles `android.jar` with `javap -p`, it extracts every single method and field—including `private` members, internal JVM state variables, and Android **Hidden APIs**.

If left unfiltered:
1. **Private members** cannot legally be resolved via JNI from external libraries, creating dead slots in the metadata table.
2. **Hidden/Blocked APIs** trigger immediate fatal crashes on physical Android hardware. On Android 9.0+ (API 28+), Android Runtime (ART) unconditionally throws `NoSuchMethodError` / `NoSuchFieldError` whenever native code attempts to resolve a member flagged as `blocked`.
3. **CheckJNI Aborts:** Debuggable builds running with CheckJNI will instantly abort the app process upon touching these members.

Stage 04.5 acts as a **pure filter**: it consumes the JSON files from `04_parse/output/` and produces clean, crash-proof JSON files in `04_5_sanitize/output/` with the exact same schema.

---

## Quick Start & Execution

### Standard Run (Always-On Protections)
```bash
python 04_5_sanitize/main.py \
    --input 04_parse/output \
    --output 04_5_sanitize/output \
    --api-version 35
```

### Strict Run (Recommended for Maximum Production Safety)
```bash
python 04_5_sanitize/main.py \
    --input 04_parse/output \
    --output 04_5_sanitize/output \
    --api-version 35 \
    --strict \
    --min-sdk 24
```
* Adding `--strict` strips APIs marked `restricted`, `unsupported`, or conditional (`max-target-*`).
* Adding `--min-sdk 24` removes legacy APIs permanently deleted from the platform before Android 7.0.

---

## Command-Line Arguments

| Argument | Required | Type | Default | Description |
|---|---|---|---|---|
| `--input` | ✅ Yes | `str` | — | Path to Stage 04 output directory (`04_parse/output/`). |
| `--output` | ✅ Yes | `str` | — | Destination directory for sanitized JSONs (`04_5_sanitize/output/`). |
| `--api-version` | ✅ Yes | `str` | — | Target Android API level (e.g., `35`). |
| `--strict` | ❌ No | `flag` | `False` | Also drops members tagged as `restricted`, `unsupported`, or `conditional`. |
| `--min-sdk` | ❌ No | `int` | `None` | Drops members permanently removed at or before API level `N`. |

---

## Exact Locations & Download URLs for Offline Metadata

Stage 04.5 operates **100% offline** (no network requests are made during pipeline execution). It looks for two metadata files in your local `third_party/` directory:

```text
stratum/
└── third_party/
    ├── api_versions/
    │   └── 35/
    │       └── api-versions.xml        <-- SDK Version History
    └── hiddenapi/
        └── 35/
            └── hiddenapi-flags.csv     <-- AOSP Hidden API Blacklist
```

---

### 1. `api-versions.xml` (Android SDK Version History)

This file tells Stratum which Android API level introduced (`since`), deprecated (`deprecated`), or deleted (`removed`) every class, method, and field.

#### Option A: Copy from Your Local Android SDK (Fastest & Guaranteed Matching)
If you have Android Studio or the Android SDK installed, this file is already on your computer!

* **Windows:**
  ```cmd
  copy "%LOCALAPPDATA%\Android\Sdk\platforms\android-35\data\api-versions.xml" "third_party\api_versions\35\api-versions.xml"
  ```
* **macOS:**
  ```bash
  mkdir -p third_party/api_versions/35
  cp ~/Library/Android/sdk/platforms/android-35/data/api-versions.xml third_party/api_versions/35/api-versions.xml
  ```
* **Linux:**
  ```bash
  mkdir -p third_party/api_versions/35
  cp ~/Android/Sdk/platforms/android-35/data/api-versions.xml third_party/api_versions/35/api-versions.xml
  ```

#### Option B: Direct Download from Official AOSP Git
If you do not have the full Android SDK installed, download it directly from Google's Android Open Source Project repository:
* **AOSP Git Webview:** [platform/development/+/refs/heads/main/sdk/api-versions.xml](https://android.googlesource.com/platform/development/+/refs/heads/main/sdk/api-versions.xml)
* **Direct Raw Download (API 35 / Android 15):**
  ```bash
  curl -Lo third_party/api_versions/35/api-versions.xml \
    "https://android.googlesource.com/platform/development/+/refs/heads/android15-release/sdk/api-versions.xml?format=TEXT"
  ```
  *(Note: AOSP web responses with `?format=TEXT` are base64-encoded. If using `curl`, decode with `base64 --decode`).*

---

### 2. `hiddenapi-flags.csv` (AOSP Hidden API Access Flags)

This file contains the complete mapping of non-SDK and restricted interfaces with their access enforcement tier (`blocked`, `unsupported`, `max-target-o`, `max-target-p`, `max-target-q`, `max-target-r`, `public-api`).

#### Where to Find It:
1. **Official Google Android Documentation:** (preferred)
   * [Android Developer Guide: Restrictions on non-SDK interfaces](https://developer.android.com/guide/app-compatibility/restrictions-non-sdk-interfaces)
   * [Android 15 Non-SDK Interface Changes](https://developer.android.com/about/versions/15/changes/non-sdk-15)
   * [Android 16 Non-SDK Interface Changes](https://developer.android.com/about/versions/16/changes/non-sdk-16)

2. **Official AOSP Build Artifacts & Prebuilts:**
   In AOSP, this file is generated when building the platform at:
   `out/soong/hiddenapi/hiddenapi-flags.csv`
   Google also stores prebuilt hidden API artifacts in the Android Runtime repository:
   * **AOSP Repository:** [platform/prebuilts/runtime/+/refs/heads/main/hiddenapi/](https://android.googlesource.com/platform/prebuilts/runtime/+/refs/heads/main/hiddenapi/)
   * **Android 15 Frameworks Base Branch:** [platform/frameworks/base/+/refs/heads/android15-release](https://android.googlesource.com/platform/frameworks/base/+/refs/heads/android15-release)

3. **Community & Reverse-Engineering Prebuilts (Direct Raw Download):**
   Open-source Android compatibility projects maintain direct, pre-extracted CSVs for each release:

   * **Alternative Mirror:** [AOSP HiddenApi Dumps on GitHub](https://github.com/anggrayudi/android-hidden-api)

---

## Graceful Degradation

If either (or both) metadata files are missing:
* **Stage 04.5 will NOT crash.**
* It prints an informational warning telling you where to place the missing file.
* It continues execution and executes the **Always-On private member stripping**.
* Missing version/hidden fields will simply be recorded as `null` in the JSON output.

---

## Sanitization Rules

### 1. Always-On (Crash-Proof Invariants)
* **Drops `is_private == True`:** Strips all private fields and methods leaked by `javap -p`.
* **Drops `hidden_status == 'blocked'`:** Strips any method/field explicitly blocked by ART to prevent native crashes on physical hardware.
* **Constructor Recalculation:** Re-evaluates `has_accessible_constructor`. If every constructor was private, this is set to `false`.
* **Preserves Class Files:** Never deletes a `.json` class file, ensuring all class types remain available as valid type references.
* **Preserves High `sdk_since`:** Does **not** strip APIs merely because they are new, keeping `if Build.VERSION.SDK_INT >= 33:` runtime checks fully functional.

### 2. Optional Strict Filtering
* **`--strict`:** Drops any member whose status is `restricted`, `unsupported`, or conditional (`max-target-*`). This guarantees that only 100% public SDK APIs survive into the metadata tables.
* **`--min-sdk N`:** Drops members that were permanently deleted from Android at or before API level `N`.

---

## Pipeline Routing

Stage 04.5 is a pure, transparent filter. To use it, simply point Stage 05 (Pass 1) to read from this stage's output folder:

```bash
# 1. Run Stage 04 (Parse)
python 04_parse/main.py --input 03_javap/output --output 04_parse/output

# 2. Run Stage 04.5 (Sanitize)
python 04_5_sanitize/main.py --input 04_parse/output --output 04_5_sanitize/output --api-version 35 --strict

# 3. Run Stage 05 Pass 1 (Pointing to 04_5_sanitize output)
python 05_resolve/main.py --input 04_5_sanitize/output --output 05_resolve/output
```

*(If you ever choose to skip Stage 04.5, point `--input` in Stage 05 directly at `04_parse/output`).*
```