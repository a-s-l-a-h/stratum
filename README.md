## 🚀 Architecture Evolution: The Universal Data-Driven Engine

Stratum has undergone a major architectural transformation after v0.3_1, evolving from a brute-force, static C++ code generator into a **high-performance, data-driven JNI virtual engine**.

> **Note:** This new architecture was introduced **after v0.3_1** and is **not part of the v0.3_1 release**.

For a demonstration of the new architecture, visit the [Stratum OpenCV Demo](https://github.com/a-s-l-a-h/Stratum-OpenCV-Demo).


Instead of generating thousands of bloated, redundant C++ wrapper classes and Nanobind headers that cause compiler out-of-memory errors, Stratum compiles the entire Android SDK surface into an ahead-of-time (AOT) **Deduplicated String Pool** and flat **Class/Method Metadata Table**. All runtime calls funnel through an ultra-compact, 6-file C++ execution core executing in $O(1)$ constant time via direct table slots.

### Key Architectural Highlights

* **Universal Dispatch Engine (`stratum_engine.cpp`):** Replaces 6,000+ generated C++ files with unified dispatch primitives (`call_v`, `call_i`, `call_o`, `field_get_*`), dropping native compilation times from 45+ minutes to under 10 seconds.
* **Deduplicated String Pool (`metadata_table.h / .cpp`):** Collapses tens of thousands of repeating JNI signatures and identifiers across 5,981 Android classes into a compact, contiguous ~2 MB read-only byte pool (`g_str_pool`).
* **$O(1)$ Slot-Based Indexing:** Method and field IDs are resolved lazily on first access and cached in indexed memory slots, bypassing Java reflection overhead and matching raw C++ JNI call speeds.
* **ART Local-Ref Protection (`JniLocalFrame`):** RAII-managed local reference frames wrap all argument unboxing and field reads, preventing Android ART 512-local-reference table overflows during high-frequency loops.
* **Reentrant Class Resolution:** Employs recursive thread synchronization (`std::recursive_mutex`) to eliminate deadlocks caused by nested class initialization (`<clinit>`) during dynamic JNI loading.
* **Zero-Copy Direct Buffers:** Native hardware mapping via `bytebuffer_to_memoryview` provides direct Python access to camera streams, audio PCM data, and graphics buffers without memory copies.

### Performance & Footprint Comparison

| Metric | Legacy Architecture (v0.3_1) | Universal Engine (after v0.3_1) |
| :--- | :--- | :--- |
| **Generated C++ Files** | ~6,000+ `.cpp` source files | **6 fixed engine files** |
| **Dispatch Model** | Static Nanobind class templates | **Data-driven slot dispatch** |
| **C++ Build Time** | 20–60+ minutes (High RAM/OOM risk) | **< 10 seconds** |
| **Binary Metadata Size** | Tens of megabytes | **~2.0 MB compressed pool** |
| **Method Resolution** | Static link-time binding | **Lazy $O(1)$ JNI slot cache** |





# Run commands for quick look

###  Stage 00: Setup & Validation
───────────────────────────────────────────
```
python 00_setup/main.py --ndk-path third_party/ndk25/android-ndk-r25c --jar-path third_party/android-35.jar --api-version 35 --ndk-api 24 --chaquopy-version 3.10.13-0 --output 00_setup/output
```
───────────────────────────────────────────
### Stage 01: Extract android.jar
───────────────────────────────────────────
```
python 01_extract/main.py --setup 00_setup/output/setup_report.json --output 01_extract/output
```
───────────────────────────────────────────

###  Stage 02: Inspect Extracted Classes
───────────────────────────────────────────
```
python 02_inspect/main.py --input 01_extract/output --output 02_inspect/output
```
───────────────────────────────────────────
#### -> (Optional) Edit 02_inspect/targets.json
───────────────────────────────────────────

### Stage 03: Run javap Disassembly 
───────────────────────────────────────────
```
python 03_javap/main.py --input 01_extract/output --targets 02_inspect/targets.json --setup 00_setup/output/setup_report.json --output 03_javap/output
```
───────────────────────────────────────────

### Stage 04: Parse Disassembly to JSON 
───────────────────────────────────────────
```
python 04_parse/main.py --input 03_javap/output --output 04_parse/output
```
───────────────────────────────────────────

### Stage 04.5 (OPTIONAL): JNI Safety Sanitizer 
#### Pure filter — strips private/blocked members. Safe to skip entirely.
#### If you skip it: Stage 05 Pass 1 --input stays 04_parse/output (see below).
───────────────────────────────────────────
```
python 04_5_sanitize/main.py --input 04_parse/output --output 04_5_sanitize/output --api-version 35 --strict
```
───────────────────────────────────────────
#### (add --strict to also drop restricted/unsupported/conditional hiddenapi members)
#### (add --min-sdk N to also drop members removed at/before API N)

### Stage 05 (Pass 1): Initial Resolution
#### -> (Optional) Edit 05_resolve/targets.json
#
### WITHOUT Stage 04.5:
───────────────────────────────────────────
```
python 05_resolve/main.py --input 04_parse/output --output 05_resolve/output
```
───────────────────────────────────────────
#
### WITH Stage 04.5:
───────────────────────────────────────────
```
python 05_resolve/main.py --input 04_5_sanitize/output --output 05_resolve/output
```
───────────────────────────────────────────

### Stage 05.5: Abstract & Interface Adapters Generation 
#### -> (Optional) Edit 05_5_abstract/targets.json
####    "seeds_only": false (default) = curated seeds + full-registry pattern scan
####    "seeds_only": true            = ONLY the classes listed in "seeds"
───────────────────────────────────────────
```
python 05_5_abstract/main.py --input 05_resolve/output --output 05_5_abstract/output --mode on
```
───────────────────────────────────────────
#### -> Copy 05_5_abstract/output/java/com/stratum/adapters/*.java into Android Studio: app/src/main/java/com/stratum/adapters/

### Stage 05 (Pass 2): Final Resolution with Patched Callback Metadata 
#### (If 05.5 was skipped, point 06 & 08 to 05_resolve/output instead)
───────────────────────────────────────────
```
python 05_resolve/main.py --input 05_5_abstract/output/patched --output 05_resolve/output_patched
```
───────────────────────────────────────────

### Stage 06: Universal C++ Engine Emit
───────────────────────────────────────────
```
python 06_cpp_emit/main.py --input 05_resolve/output_patched --output 06_cpp_emit/output
```
───────────────────────────────────────────

### Stage 07: Compile _stratum.so 
#### Pass --no-log for production builds
───────────────────────────────────────────
```
python 07_build/main.py --cpp 06_cpp_emit/output --setup 00_setup/output/setup_report.json --nanobind third_party/nanobind --abi arm64-v8a --chaquopy 3.10.13-0 --output 07_build/output --log
```
───────────────────────────────────────────

### Stage 08 + 09: Python Classes/.pyi Emit + Wheel Assembly 

#### -- Dev build (unchanged, default): full per-class .py/.pyi, debuggable 
───────────────────────────────────────────
```
python 08_pyi_emit/main.py --input 05_resolve/output_patched --output 08_pyi_emit/output
```
───────────────────────────────────────────
───────────────────────────────────────────
```
python 09_wheel/main.py --so 07_build/output/_stratum.so --py-src 08_pyi_emit/output --output 09_wheel/output --version 0.9.0 --min-api 24 --abi arm64-v8a --chaquopy 3.10.13-0
```
───────────────────────────────────────────

### Production build: one _meta.json.gz blob + loader, no .pyi -> smaller .whl 
───────────────────────────────────────────
```
python 08_pyi_emit/main.py --input 05_resolve/output_patched --output 08_pyi_emit/output --mode dynamic
```
───────────────────────────────────────────
───────────────────────────────────────────
```
python 09_wheel/main.py --so 07_build/output/_stratum.so --py-src 08_pyi_emit/output --output 09_wheel/output --version 0.9.0 --min-api 24 --abi arm64-v8a --chaquopy 3.10.13-0 --include-pyi no --include-reflect yes
```
───────────────────────────────────────────



--------------------------------------------------------------------


# Project Directory Structure
```
stratum/
├── .gitignore
├── LICENSE
├── README.md
├── PYTHON_API.md
├── THIRD-PARTY-LICENSES.md
│
├── 00_setup/                               # Stage 00: Environment & Tooling Verification
│   ├── README.md
│   ├── main.py
│   └── output/                             # [Generated]
│       └── setup_report.json               # Environment verification report & paths
│
├── 01_extract/                             # Stage 01: Android JAR Extraction
│   ├── README.md
│   ├── main.py
│   └── output/                             # [Generated]
│       ├── android/                        # Mirrored extracted .class files
│       │   └── ...
│       └── extract_summary.json
│
├── 02_inspect/                             # Stage 02: Class Inspection & Target Configuration
│   ├── README.md
│   ├── main.py
│   ├── targets.json                        # [Tracked] Pipeline targets & inclusion mode
│   ├── target_jsons_pool/                  # Preset target configurations
│   └── output/                             # [Generated]
│       ├── available_classes.txt           # Flat list of all available Android FQNs
│       └── available_by_package.txt        # Classes grouped by package hierarchy
│
├── 03_javap/                               # Stage 03: Javap Bytecode Extraction
│   ├── README.md
│   ├── main.py
│   └── output/                             # [Generated]
│       ├── android/                        # Extracted .javap signatures per class
│       │   └── ...
│       └── javap_summary.json
│
├── 04_parse/                               # Stage 04: Signature & AST Parser
│   ├── README.md
│   ├── main.py
│   └── output/                             # [Generated]
│       ├── android/                        # Parsed class JSON representations
│       │   └── ...
│       └── parse_summary.json
│
├── 04_5_sanitize/                          # Stage 04.5: Optional JNI Safety & Hidden API Filter
│   ├── README.md
│   ├── main.py
│   └── output/                             # [Generated]
│       ├── android/                        # Stripped/sanitized class JSONs
│       └── sanitize_summary.json
│
├── 05_resolve/                             # Stage 05: Slot Assignment & Type Resolution (Pass 1 & Pass 2)
│   ├── README.md
│   ├── main.py
│   ├── targets.json                        # [Tracked] Closure & filter configuration
│   ├── target_jsons_pool/
│   ├── output/                             # [Generated - Pass 1] Used by Stage 05.5
│   │   ├── android/
│   │   └── resolve_summary.json
│   └── output_patched/                     # [Generated - Pass 2] Feeds Stage 06 and Stage 08
│       ├── android/
│       └── resolve_summary.json
│
├── 05_5_abstract/                          # Stage 05.5: Abstract Class & Multi-Method Adapter Generator
│   ├── README.md
│   ├── main.py
│   ├── targets.json                        # [Tracked] Abstract callback seeds & avoid list
│   └── output/                             # [Generated]
│       ├── java/
│       │   └── com/stratum/adapters/       # Generated Java adapter source files (.java)
│       │       └── Adapter_*.java
│       ├── patched/                        # Patched JSONs containing adapter metadata
│       └── manifest.json
│
├── 06_cpp_emit/                            # Stage 06: Universal Engine & Static Metadata Table Emit
│   ├── README.md
│   ├── main.py
│   ├── engine_src/                         # [Tracked] Static C++ JNI bridge template source
│   │   ├── bridge_core.h
│   │   ├── bridge_core.cpp
│   │   ├── bridge_main.cpp
│   │   └── stratum_engine.cpp
│   └── output/                             # [Generated]
│       └── core/                           # Final compile-ready C++ bridge sources
│           ├── bridge_core.h
│           ├── bridge_core.cpp
│           ├── bridge_main.cpp
│           ├── stratum_engine.cpp
│           ├── metadata_table.h            # Generated metadata definitions
│           └── metadata_table.cpp          # Deduplicated string pool & slot lookup arrays
│
├── 07_build/                               # Stage 07: CMake + NDK Engine Compilation
│   ├── README.md
│   ├── main.py
│   ├── templates/                          # [Tracked] CMake templates
│   │   ├── CMakeLists.txt.tpl
│   │   └── StratumInit.cmake.tpl
│   └── output/                             # [Generated]
│       ├── build/<abi>/                    # CMake / Ninja intermediate compilation artifacts
│       ├── python-target/                  # Unpacked Chaquopy headers & libraries
│       ├── CMakeLists.txt
│       └── _stratum.so                     # Built native binary shared library
│
├── 08_pyi_emit/                            # Stage 08: Python Wrapper & Type Stub Generator
│   ├── README.md
│   ├── main.py
│   └── output/                             # [Generated]
│       └── stratum/
│           ├── __init__.py
│           ├── core/
│           │   ├── __init__.py
│           │   └── stratum_object.py       # Core wrapper base class & lazy class resolver
│           ├── _dynamic.py                 # (When --mode dynamic is used)
│           ├── _meta.json.gz               # (When --mode dynamic is used)
│           └── android/                    # (When --mode static is used)
│               └── ... (.py and .pyi files per Android class)
│
├── 09_wheel/                               # Stage 09: Packaging
│   ├── README.md
│   ├── main.py
│   └── output/                             # [Generated]
│       └── stratum-<ver>-<pytag>-android_<minapi>_<abi>.whl  # Final installable Python wheel
│
├── runtime/                                # Runtime Java files (packaged into your Android app)
│   ├── README.md
│   └── java/
│       └── com/stratum/runtime/
│           ├── StratumActivity.java        # Android Activity managing Python lifecycle
│           └── StratumInvocationHandler.java # Generic Java Proxy dispatch handler
│
└── third_party/                            # External Dependencies & Offline Metadata Caches
    │
    ├── nanobind/                           # [Auto-cloned by Stage 00 via git]
    │   ├── CMakeLists.txt
    │   ├── include/nanobind/nanobind.h
    │   └── ext/robin_map/                  # Submodule initialized recursively
    │
    ├── chaquopy/                           # [Auto-downloaded by Stage 00 / Stage 07]
    │   └── <version>/                      # e.g., 3.12.0-0/
    │       ├── target-<version>-arm64-v8a.zip
    │       ├── target-<version>-armeabi-v7a.zip
    │       ├── target-<version>-x86_64.zip
    │       └── target-<version>-x86.zip
    │
    ├── api_versions/                       # [Expected / User Placed - NOT in git]
    │   └── <api-level>/                    # e.g., 35/
    │       └── api-versions.xml            # Copy from: <Android-SDK>/platforms/android-<api>/data/api-versions.xml
    │                                       # (Optional: Used by Stage 04.5 to tag sdk_since/deprecated/removed)
    │
    └── hiddenapi/                          # [Expected / User Placed - NOT in git]
        └── <api-level>/                    # e.g., 35/
            └── hiddenapi-flags.csv         # Placed manually from AOSP sources
                                            # (Optional: Used by Stage 04.5 to strip restricted/blocked APIs)

```
---------------------------------------------------


---

# Stratum

> **A high-performance build-time pipeline that generates a zero-reflection JNI C++ bridge and Python wheel for the entire Android SDK.**

Stratum extracts Android SDK class definitions, analyzes descriptors and inheritance hierarchies, and compiles a **Universal Engine** powered by `nanobind` and native JNI. The resulting native shared library (`_stratum.so`) and Python wheel give your Python app direct, transparent access to Android Java/Kotlin APIs at native C++ execution speeds.

---

## ⚡ Key Highlights & Architecture

* **Full SDK Coverage by Default:** No manual target curation required. Stratum parses and indexes the entire `android.jar` (thousands of classes and hundreds of thousands of methods) out of the box without running out of memory.
* **Constant Compile Times:** Stratum does not generate thousands of individual C++ files. The engine compiles exactly 4 core C++ files containing a compressed, deduplicated string pool and static metadata index. Builds complete in seconds to a couple of minutes.
* **Lazy Runtime Resolution:** `jclass`, `jmethodID`, and `jfieldID` lookups are resolved lazily on first access per class, cached thread-safely via recursive mutexes, and invoked directly via raw `Call<Type>MethodA` function pointers.
* **Bidirectional Data Marshaling:** Full native support for primitive arrays (`byte[]`, `int[]`, `float[]`, etc.), nested `java.util.List` / `Map` collections, `Bundle`, and direct zero-copy `java.nio.ByteBuffer` mappings to Python `memoryview`.
* **Two-Way Callbacks & Abstract Adapters:** Automatically generates real Java adapter classes for Android abstract callbacks (`CameraDevice.StateCallback`, `WebViewClient`, etc.) alongside dynamic interface proxies (`View.OnClickListener`).
* **Ultra-Compact Dynamic Wheels:** Supports both standard static Python wrappers (`.py`/`.pyi`) and an on-demand dynamic mode (`--mode dynamic`) where the entire SDK metadata is compressed into a single binary blob and generated on import.

---

## Architecture Overview

```
                      Python Application (main.py)
                                  │
                                  ▼
                     Stratum Python Package (stratum)
              [Static .py Wrappers  OR  Dynamic Import Hook]
                                  │
                                  ▼
                     Universal C++ Engine (_stratum.so)
     ┌─────────────────────────────────────────────────────────────┐
     │  nanobind Entry Points (call_v, call_i, call_o, call_arr)   │
     │  Lazy Metadata Index & String Pool (metadata_table.cpp)     │
     │  Thread-Safe Slot Dispatch & LocalRef Management (RAII)     │
     └─────────────────────────────────────────────────────────────┘
                                  │
                 JNI (Raw Pointer Invocation / CallXXXMethodA)
                                  ▼
                        Android JVM (ART)
     ┌─────────────────────────────────────────────────────────────┐
     │  Native Android SDK (android.view.*, android.hardware.*)    │
     │  Stratum Java Runtime (StratumActivity, StratumView, etc.)  │
     │  Generated Adapters (com.stratum.adapters.Adapter_*)        │
     └─────────────────────────────────────────────────────────────┘
```

---

## Tool Versions Used

> ⚠️ **Python Version Note:** The pipeline and Chaquopy runtime have been primarily tested and validated on **Python 3.10** (recommended). Python 3.11 and 3.12 are supported, but Python 3.10 provides the most stable parity with Android ecosystem dependencies.

| Tool | Tested / Recommended Version | Install / Download Link |
|---|---|---|
| **Python** | **3.10+** (Tested on **3.10**) | [python.org/downloads/release/python-31011/](https://www.python.org/downloads/release/python-31011/) |
| **Android NDK** | **r25c** (25.2.9519653) | [GitHub NDK r25c Release](https://github.com/android/ndk/releases/tag/r25c) \| [All NDK Releases Archive](https://github.com/android/ndk/wiki/Unsupported-Downloads) |
| **Android SDK / JAR** | **API 35** (Android 15) | Installed via Android Studio SDK Manager or local `android.jar` |
| **CMake** | **3.22+ / 3.31+** | [cmake.org/download](https://cmake.org/download/) \| [Kitware GitHub Releases](https://github.com/Kitware/CMake/releases) |
| **Ninja** | **1.13.0.post1** | `pip install ninja==1.13.0.post1` ([PyPI Link](https://pypi.org/project/ninja/)) |
| **nanobind** | **v2.12.0** | [GitHub nanobind v2.12.0 Tag](https://github.com/wjakob/nanobind/releases/tag/v2.12.0) |
| **Chaquopy Target** | **3.10.13-0** / **3.12.0-0** | [Maven Central Repository](https://repo1.maven.org/maven2/com/chaquo/python/target/) (Auto-downloaded by Stage 00) |
| **Java JDK** | **17+** | [Oracle JDK 17](https://www.oracle.com/java/technologies/javase/jdk17-archive-downloads.html) or [Eclipse Adoptium Temurin 17](https://adoptium.net/temurin/releases/?version=17) |

---

## Direct Downloads & Prerequisites Locations

### 1. Android NDK r25c Direct Archive Downloads
If you need an older or specific NDK version, download it from the official Google archives:
* **Windows (x86_64):** [android-ndk-r25c-windows.zip](https://dl.google.com/android/repository/android-ndk-r25c-windows.zip)
* **Linux (x86_64):** [android-ndk-r25c-linux.zip](https://dl.google.com/android/repository/android-ndk-r25c-linux.zip)
* **macOS (Universal):** [android-ndk-r25c-darwin.zip](https://dl.google.com/android/repository/android-ndk-r25c-darwin.zip)  
*(Extract into `third_party/ndk25/android-ndk-r25c`)*.  
*Older or alternative releases can always be found on the [Android NDK GitHub Wiki Archive](https://github.com/android/ndk/wiki/Unsupported-Downloads).*

### 2. Ninja Build Tool
```bash
pip install ninja==1.13.0.post1
```
*Note:* Ninja installed via pip resides directly in Python's site-packages folder and does **not** need to be manually added to your system `PATH`. Stage 00 and Stage 07 detect it dynamically using `import ninja; ninja.BIN_DIR`.

To verify your Ninja installation:
```bash
python -c "import ninja, os; p = os.path.join(ninja.BIN_DIR, 'ninja.exe' if os.name == 'nt' else 'ninja'); print('Found Ninja at:', p, '| Exists:', os.path.exists(p))"
```

### 3. CMake
Download the x64 installer for Windows:
* [cmake-3.31.5-windows-x86_64.msi](https://github.com/Kitware/CMake/releases/download/v3.31.5/cmake-3.31.5-windows-x86_64.msi)  
*(Make sure to select **"Add CMake to system PATH"** during setup).*

### 4. nanobind
Handled automatically by Stage 00, or clone recursively with submodules manually:
```bash
git clone --recursive --shallow-submodules --depth 1 --branch v2.12.0 https://github.com/wjakob/nanobind.git third_party/nanobind
```

### 5. API Versions XML (`api-versions.xml`)
Required if you run the optional JNI sanitizer (`04_5_sanitize`). This file maps which API level added, deprecated, or removed each Java method and field.
* **Where to find it on your machine:** After installing the Android SDK Platform via Android Studio, it is located at:
  * **Windows:**  
    `C:\Users\<Your-Username>\AppData\Local\Android\Sdk\platforms\android-<api-version>\data\api-versions.xml`
  * **macOS:**  
    `~/Library/Android/sdk/platforms/android-<api-version>/data/api-versions.xml`
  * **Linux:**  
    `~/Android/Sdk/platforms/android-<api-version>/data/api-versions.xml`
* **Where to place it in Stratum:**
  ```
  third_party/api_versions/<api-version>/api-versions.xml
  ```
  *(Example: `third_party/api_versions/35/api-versions.xml`)*

### 6. Hidden API Flags CSV (`hiddenapi-flags.csv`)
Used by `04_5_sanitize` to strip Android internal blocked and non-SDK private interfaces:
* **Where to find it:** Provided by the Android Open Source Project (AOSP) and documented by Android developer guides:
  * [Android Developer: Restrictions on non-SDK interfaces](https://developer.android.com/guide/app-compatibility/restrictions-non-sdk-interfaces)
  * [Android 16 Non-SDK Interface Lists](https://developer.android.com/about/versions/16/changes/non-sdk-16)
  * AOSP build artifact: generated under `out/soong/hiddenapi/hiddenapi-flags.csv` when compiling AOSP.
* **Where to place it in Stratum:**
  ```
  third_party/hiddenapi/<api-version>/hiddenapi-flags.csv
  ```
  *(Example: `third_party/hiddenapi/35/hiddenapi-flags.csv`)*

---

## Project Structure

```
stratum/
├── 00_setup/           # Toolchain verification & Chaquopy header downloads
├── 01_extract/         # Unpacks android.jar -> raw .class files
├── 02_inspect/         # Maps and discovers available classes
├── 03_javap/           # JNI descriptor disassembly using javap -s -p
├── 04_parse/           # Parses javap output into structured JSON
├── 04_5_sanitize/      # (Optional) JNI safety & hidden API filter
├── 05_resolve/         # Hierarchy resolver, slot indexing, param tagger
├── 05_5_abstract/      # Java callback adapter generator & patcher
├── 06_cpp_emit/        # Generates metadata table and universal C++ engine
├── 07_build/           # Compiles _stratum.so with NDK, CMake & Ninja
├── 08_pyi_emit/        # Generates Python classes, stubs, or dynamic meta
├── 09_wheel/           # Packages everything into a Chaquopy-ready wheel
├── runtime/            # Java runtime components to copy to Android Studio
└── third_party/        # NDK, nanobind, android.jar, and cached Chaquopy targets
```

---

## Complete Pipeline Execution

The pipeline is fully automated. You do not need to create manual lists of classes—by default, it parses and exposes the full SDK.

### Stage 00: Setup & Verification
Validates the toolchain, downloads target Chaquopy Python headers and libraries, and clones `nanobind` submodules.

```bash
python 00_setup/main.py ^
  --ndk-path "third_party/ndk25/android-ndk-r25c" ^
  --jar-path "third_party/android-35.jar" ^
  --api-version 35 ^
  --ndk-api 24 ^
  --chaquopy-version "3.10.13-0" ^
  --output "00_setup/output/"
```

### Stage 01: Extract Class Files
Unpacks `.class` files from `android.jar`. This output is cached and only needs to run once per Android API version.

```bash
python 01_extract/main.py ^
  --setup "00_setup/output/setup_report.json" ^
  --output "01_extract/output/"
```

### Stage 02: Inspect Classes
Maps packages and builds the index. Defaults to `mode: "full"`.

```bash
python 02_inspect/main.py ^
  --input "01_extract/output/" ^
  --output "02_inspect/output/"
```

### Stage 03: Disassemble Bytecode (javap)
Runs `javap -s -p` over the classes to extract exact JNI signatures and descriptors.

```bash
python 03_javap/main.py ^
  --input "01_extract/output/" ^
  --targets "02_inspect/targets.json" ^
  --setup "00_setup/output/setup_report.json" ^
  --output "03_javap/output/"
```

### Stage 04: Parse Disassembly
Transforms raw `javap` outputs into structured JSON metadata with type signatures, modifiers, and parameter definitions.

```bash
python 04_parse/main.py ^
  --input "03_javap/output/" ^
  --output "04_parse/output/"
```

### Stage 04.5: JNI Safety Sanitizer *(Optional)*
Strips private declarations and blocked hidden API members.
```bash
python 04_5_sanitize/main.py ^
  --input "04_parse/output/" ^
  --output "04_5_sanitize/output/" ^
  --api-version 35
```

---

### The Two-Pass Resolve & Adapter Phase (Stages 05 & 05.5)

To guarantee that Android abstract callbacks (e.g., `CameraCaptureSession.StateCallback`) and multi-method interfaces can trigger Python functions without reflection failures, Stratum uses a two-pass resolution sequence:

1. **Stage 05 (Pass 1):** Resolves inheritance trees and establishes initial slot maps.
2. **Stage 05.5:** Scans for abstract callback patterns, emits compilable Java adapter classes (`.java`), and marks callback arguments in the JSON as `conversion: abstract_adapter`.
3. **Stage 05 (Pass 2):** Finalizes slot assignments and param tags against the patched metadata.

```bash
# 1. Resolve Pass 1
python 05_resolve/main.py ^
  --input "04_parse/output/" ^
  --output "05_resolve/output/"

# 2. Generate Java Adapters and Patch Metadata
python 05_5_abstract/main.py ^
  --mode on ^
  --input "05_resolve/output/" ^
  --output "05_5_abstract/output/" ^
  --output-java "05_5_abstract/output_java/"

# 3. Resolve Pass 2 (Final Slot Indexing)
python 05_resolve/main.py ^
  --input "05_5_abstract/output/patched/" ^
  --output "05_resolve/output_patched/"
```

---

### Stage 06: Universal Engine Emit
Generates the compressed string pool, static dispatch tables, and engine wrappers into `06_cpp_emit/output/core/`.

```bash
python 06_cpp_emit/main.py ^
  --input "05_resolve/output_patched/" ^
  --output "06_cpp_emit/output/"
```

### Stage 07: Native Compilation
Compiles `_stratum.so` using the Android NDK Clang toolchain and Ninja.

```bash
python 07_build/main.py ^
  --cpp "06_cpp_emit/output/" ^
  --setup "00_setup/output/setup_report.json" ^
  --nanobind "third_party/nanobind" ^
  --abi arm64-v8a ^
  --chaquopy "3.10.13-0" ^
  --output "07_build/output/" ^
  --log
```

> **Production builds:** Use `--no-log` to compile out all logging overhead.

### Stage 08: Python Bindings & Type Stubs
Emits the Python package. Choose between standard static files or dynamic generation:

* **Static Mode (`--mode static`):** Writes individual `.py` and `.pyi` files for every class.
* **Dynamic Mode (`--mode dynamic`):** Compresses the entire SDK metadata into `_meta.json.gz` and builds classes on demand in Python. Recommended for packaging small wheels.

```bash
python 08_pyi_emit/main.py ^
  --input "05_resolve/output_patched/" ^
  --output "08_pyi_emit/output/" ^
  --mode dynamic
```

### Stage 09: Assemble Wheel
Bundles `_stratum.so`, Python modules, UI helpers, and the reflection engine into a standard wheel (`.whl`).

```bash
python 09_wheel/main.py ^
  --so "07_build/output/_stratum.so" ^
  --py-src "08_pyi_emit/output/" ^
  --output "09_wheel/output/" ^
  --version 0.9.0 ^
  --abi arm64-v8a ^
  --chaquopy "3.10.13-0" ^
  --include-reflect yes ^
  --include-pyi no
```

---

## Android Studio Integration

### 1. Add Runtime Files
Copy the runtime bridge classes into your Android application project:
```
stratum/runtime/java/com/stratum/runtime/*.java
  └── app/src/main/java/com/stratum/runtime/
```

### 2. Add Generated Adapters
Copy the generated adapters from Stage 05.5:
```
05_5_abstract/output_java/*.java
  └── app/src/main/java/com/stratum/adapters/
```

### 3. Update ProGuard Rules
Append `runtime/consumer-rules.pro` to your app's `proguard-rules.pro` to prevent R8 from stripping or renaming adapter classes accessed via JNI:
```proguard
-keep class com.stratum.runtime.** { *; }
-keepclassmembers class com.stratum.runtime.** { *; }
-keep class com.stratum.adapters.** { *; }
-keepclassmembers class com.stratum.adapters.** { *; }
-keepclasseswithmembernames class * {
    native <methods>;
}
```

### 4. Configure `MainActivity`
Update your `MainActivity` to inherit from `StratumActivity`:

```java
package com.example.yourapp;

import com.stratum.runtime.StratumActivity;

public class MainActivity extends StratumActivity {
    // StratumActivity automatically boots Chaquopy, loads _stratum.so,
    // passes Activity references to the C++ bridge, and executes main.py.
}
```

### 5. Install the Wheel
Copy the generated `.whl` from `09_wheel/output/` into your Android project:
```
app/src/main/python/
```
Ensure your `app/build.gradle` has Chaquopy enabled with the corresponding wheel dependency:
```groovy
pip {
    install "stratum-0.9.0-cp310-cp310-android_24_arm64_v8a.whl"
}
```

---

## Python API & Code Examples

### 1. Basic UI Creation & Layout
```python
import stratum
from stratum.android.widget import Button, LinearLayout, TextView
from stratum.android.view import ViewGroup

def onCreate():
    activity = stratum.get_activity()

    layout = LinearLayout(activity)
    layout.setOrientation(LinearLayout.VERTICAL)

    label = TextView(activity)
    label.setText("Hello from Stratum Native Python!")
    layout.addView(label)

    btn = Button(activity)
    btn.setText("Click Me")
    btn.setOnClickListener(lambda view: label.setText("Button Clicked!"))
    layout.addView(btn)

    stratum.setContentView(activity, layout)
```

### 2. Custom 2D Graphics via `CustomCanvasView`
`stratum.ui.CustomCanvasView` exposes Android's `onDraw`, `onMeasure`, and `onTouchEvent` without writing custom Java views:

```python
import stratum
from stratum.ui import CustomCanvasView
from stratum.android.graphics import Paint, Color

class MyDrawingView(CustomCanvasView):
    def __init__(self, activity):
        super().__init__(activity)
        self.paint = Paint()
        self.paint.setColor(Color.RED)
        self.paint.setStrokeWidth(8.0)

    def on_draw(self, canvas):
        canvas.drawColor(Color.WHITE)
        canvas.drawCircle(300.0, 300.0, 150.0, self.paint)

    def on_touch_event(self, motion_event):
        action = motion_event.getAction()
        # Handle touch interactions...
        self.invalidate()
        return True

def onCreate():
    activity = stratum.get_activity()
    view = MyDrawingView(activity)
    stratum.setContentView(activity, view)
```

### 3. Running Code on the UI Thread
```python
from stratum import ui_thread, run_on_ui_thread

@ui_thread
def update_label_safe(text_view, new_text):
    text_view.setText(new_text)

# Or pass a callable directly:
run_on_ui_thread(lambda: my_button.setEnabled(False))
```

### 4. Background Services & Broadcast Receivers
Stratum supports background execution even when no `Activity` is running.

**`main.py`:**
```python
import stratum

def onReceive(context, intent):
    action = intent.getAction()
    print(f"Broadcast received: {action}")

def onStartCommand(intent, flags, startId):
    print("Service executing in background...")
    return 1 # START_STICKY

# Register functions with Stratum runtime handlers:
stratum.register_callback("StratumReceiver#onReceive", onReceive)
stratum.register_callback("StratumService#onStartCommand", onStartCommand)
```

**`AndroidManifest.xml`:**
```xml
<service android:name="com.stratum.runtime.StratumService" />
<receiver android:name="com.stratum.runtime.StratumReceiver" android:exported="true">
    <intent-filter>
        <action android:name="android.intent.action.BOOT_COMPLETED" />
    </intent-filter>
</receiver>
```

### 5. Reflection Escape Hatch (`stratum.reflect`)
Call internal Java methods or custom application classes not covered by static wrappers:

```python
from stratum import reflect

# Call static methods
sdk_version = reflect.call_java_static(
    "android.os.SystemProperties",
    "get",
    "ro.build.version.sdk"
)

# Call methods on custom Java instances
reflect.call_java_method(custom_java_object, "customMethod", "arg1", 42)
```

### 6. Dynamic Runtime Logging
```python
import stratum

# Toggle deep C++/JNI logcat output at runtime:
stratum.set_log_enabled(True)
stratum.log_msg("Checking native pipeline status...")
```

Filter log output using `adb`:
```bash
adb logcat -s "Stratum" "StratumTrace"
```

---

## Pipeline Automation Script

To run the complete pipeline in a single command, use this PowerShell script (`build_pipeline.ps1`):

```powershell
$ErrorActionPreference = "Stop"

$NDK     = "third_party/ndk25/android-ndk-r25c"
$JAR     = "third_party/android-35.jar"
$CHAQUO  = "3.10.13-0"
$ABI     = "arm64-v8a"
$API     = "35"
$MIN_API = "24"

Write-Host "=== Stage 00: Setup ==="
python 00_setup/main.py --ndk-path $NDK --jar-path $JAR --api-version $API --ndk-api $MIN_API --chaquopy-version $CHAQUO --output 00_setup/output/

Write-Host "=== Stage 01: Extract ==="
python 01_extract/main.py --setup 00_setup/output/setup_report.json --output 01_extract/output/

Write-Host "=== Stage 02: Inspect ==="
python 02_inspect/main.py --input 01_extract/output/ --output 02_inspect/output/

Write-Host "=== Stage 03: Javap ==="
python 03_javap/main.py --input 01_extract/output/ --targets 02_inspect/targets.json --setup 00_setup/output/setup_report.json --output 03_javap/output/

Write-Host "=== Stage 04: Parse ==="
python 04_parse/main.py --input 03_javap/output/ --output 04_parse/output/

Write-Host "=== Stage 05: Resolve (Pass 1) ==="
python 05_resolve/main.py --input 04_parse/output/ --output 05_resolve/output/

Write-Host "=== Stage 05.5: Abstract Adapters ==="
python 05_5_abstract/main.py --mode on --input 05_resolve/output/ --output 05_5_abstract/output/ --output-java 05_5_abstract/output_java/

Write-Host "=== Stage 05: Resolve (Pass 2) ==="
python 05_resolve/main.py --input 05_5_abstract/output/patched/ --output 05_resolve/output_patched/

Write-Host "=== Stage 06: C++ Emit ==="
python 06_cpp_emit/main.py --input 05_resolve/output_patched/ --output 06_cpp_emit/output/

Write-Host "=== Stage 07: Build _stratum.so ==="
python 07_build/main.py --cpp 06_cpp_emit/output/ --setup 00_setup/output/setup_report.json --nanobind third_party/nanobind --abi $ABI --chaquopy $CHAQUO --output 07_build/output/ --log

Write-Host "=== Stage 08: Python & Stub Emit ==="
python 08_pyi_emit/main.py --input 05_resolve/output_patched/ --output 08_pyi_emit/output/ --mode dynamic

Write-Host "=== Stage 09: Packaging Wheel ==="
python 09_wheel/main.py --so 07_build/output/_stratum.so --py-src 08_pyi_emit/output/ --output 09_wheel/output/ --version 0.9.0 --min-api $MIN_API --abi $ABI --chaquopy $CHAQUO --include-reflect yes --include-pyi no

Write-Host "=== STRATUM BUILD COMPLETE! ==="
```

---

## Troubleshooting

* **Ninja / CMake not found:** Ensure `ninja` is installed via `pip install ninja`. Stage 00 and Stage 07 locate the binary via the Python module if it is not present on your global `PATH`.
* **`UnsatisfiedLinkError` at runtime:** Confirm the wheel's ABI matches your test device or emulator (e.g. `arm64-v8a` for physical devices, `x86_64` for standard Android Studio emulators).
* **Callback parameters crashing with `IllegalArgumentException`:** Abstract classes cannot be implemented via dynamic proxies (`Proxy.newProxyInstance`). Ensure you run Stage 05.5 (`05_5_abstract`) and copy the generated Java adapter files from `output_java/` into your Android Studio project under `com.stratum.adapters`.
* **R8 / ProGuard stripping native callbacks:** Make sure `runtime/consumer-rules.pro` is added to your application's `proguard-rules.pro`. Without it, minified release builds will rename adapter classes and cause JNI `FindClass` calls to fail.