> [!NOTE]
> **📢 Python Runtime Update**
> 
> Stratum is transitioning to use the Python builds provided directly from [python.org](https://www.python.org/downloads/android/).
> 
> 👉 **Using or prefer Chaquopy?**  
> Chaquopy-based pipeline is fully preserved on the **[`last-chaquopy-based-v0.3_6_5`](https://github.com/a-s-l-a-h/stratum/tree/last-chaquopy-based-v0.3_6_5)** branch.


<div align="center">

# 🌉 Stratum

**An Ahead-of-Time (AOT) compiled metadata bridge that turns the entire Android SDK into a typed, zero-reflection Python API.**

Write real Android apps — UI, sensors, camera, canvas, background services — in plain Python.

* **AOT Compiled Metadata:** Extracts and precomputes all Android SDK class, method, and field signatures into compact, deduplicated C++ tables at build time—eliminating compiler OOMs and runtime SDK discovery.
* **Zero Runtime Java Reflection:** Bypasses `java.lang.reflect.Method.invoke()`. Dispatches calls directly through cached JNI method slots (`Call<Type>MethodA`) using pre-packed native argument arrays.

[🚀 Full Demo App](https://github.com/a-s-l-a-h/Stratum-OpenCV-Demo) • [Quick Start](#-quick-start) • [Why Stratum](#-why-stratum) • [Python API Guide](PYTHON_API.md) • [Architecture](#-architecture) • [Building the Engine](#-building-the-engine-pipeline)

</div>


---

> ⚡ **Looking for a fast, working example?**  
> Check out the **[Stratum OpenCV Demo](https://github.com/a-s-l-a-h/Stratum-OpenCV-Demo)** to see a complete, real-world Android app (camera, UI, and native performance) built entirely with Stratum.

---

## ✨ What is Stratum?

Stratum is a two-part system:

1. **A build pipeline** (Stages 00–09 in this repo) that reads `android.jar`, indexes the entire Android SDK, and compiles a single ultra-compact native engine (`_stratum.so`) plus a matching Python wheel.
2. **A Python API** (documented in full in [`PYTHON_API.md`](PYTHON_API.md)) that lets your Python code call *any* Android class the same way you'd use it in Java/Kotlin — construct objects, call methods, read/write fields, implement listeners — all dispatched through that one compiled engine.

```python
import stratum
from stratum.android.widget import Button, LinearLayout, TextView

def onCreate():
    activity = stratum.get_activity()

    layout = LinearLayout(activity)
    layout.setOrientation(LinearLayout.sf_get_VERTICAL())

    label = Button(activity)
    label.setText("Tap me")
    label.setOnClickListener(lambda v: label.setText("Tapped!"))

    layout.addView(label)
    stratum.setContentView(activity, layout)
```

That's it — no Java file, no AIDL, no manually-written JNI. This snippet works today; see [`PYTHON_API.md`](PYTHON_API.md) for a full tour of what's available and a set of **verified, working examples**.

---

## 🚀 Why Stratum?



Stratum takes a different approach: the whole SDK surface (classes, methods, fields, signatures) is compiled once into a **deduplicated string pool + flat metadata table**, and a single **6-file C++ dispatch engine** looks up `(class_id, slot)` pairs at call time — no reflection, no per-class glue code.

| | Stratum's Universal Engine |
|---|---|
| Generated C++ files | **6, fixed** |
| C++ build time  | **Seconds** |
| Method resolution | **Lazy, cached, O(1)** |
| Binary metadata  | **~2 MB pool** |

---

## 🧩 Core Capabilities

- **Full SDK surface** — import any `android.*` class the same way you'd write the Java import, just with dots instead of package syntax.
- **Natural method calls** — `view.setText("hi")` *and* `view.set_text("hi")` both work; overloads are resolved automatically by argument shape.
- **Real listeners** — pass a plain Python function, lambda, or `dict` of functions for multi-method interfaces (`SensorEventListener`, `SurfaceTextureListener`, `CameraDevice.StateCallback`, …).
- **Custom 2D views** — subclass `stratum.ui.CustomCanvasView` and implement `on_draw` / `on_touch_event` in pure Python, no Java view class required. *(This is Stratum's most battle-tested feature — see the [Touch Canvas example](PYTHON_API.md#example-touch-driven-canvas-fully-working).)*
- **Background-safe UI updates** — `stratum.run_on_ui_thread(...)` / `@stratum.ui_thread` post work to Android's main looper.
- **Bidirectional data marshaling** — Python `list`/`dict`/`bytes` ⇄ Java `ArrayList`/`HashMap`/`byte[]`, including zero-copy `ByteBuffer` access via `memoryview`.
- **Calling Python from Java** — `@stratum.export` exposes a Python function to any hand-written Java code via `StratumRuntimeLookup.callPython(...)`.
- **Reflection escape hatch** — `stratum.reflect` reaches classes that weren't included in your build (custom `.java` files, excluded APIs) without rebuilding the pipeline.

👉 **All of the above is documented with runnable examples in [`PYTHON_API.md`](PYTHON_API.md).** That file is the one to read if you're writing an app; this README is about the tool itself.

---

## 🚦 Quick Start

### 1. Get a wheel
Either build one yourself (see [Building the Engine](#-building-the-engine-pipeline) below), or use a wheel someone already produced for your target ABI.

### 2. Wire up your Android Studio project
```groovy
// app/build.gradle
chaquopy {
    defaultConfig {
        pip {
            install "stratum-0.9.0-cp310-cp310-android_24_arm64_v8a.whl"
        }
    }
}
```
```java
// MainActivity.java
public class MainActivity extends com.stratum.runtime.StratumActivity { }
```
Copy `runtime/java/com/stratum/runtime/*.java` (and any generated `com/stratum/adapters/*.java`) into your project, and add `runtime/consumer-rules.pro` to your ProGuard rules.

### 3. Write `main.py`
```python
import stratum
from stratum.android.widget import TextView

label = None

def onCreate():
    global label
    activity = stratum.get_activity()
    label = TextView(activity)
    label.setText("Hello from Stratum")
    stratum.setContentView(activity, label)
```

### 4. Read the API guide
Everything from here on — method overloads, listeners, threading, casting, custom views, reflection — is covered with working code in **[`PYTHON_API.md`](PYTHON_API.md)**.

---

## 🏗️ Architecture

```
                Python Application (main.py)
                            │
                            ▼
              Stratum Python Package (stratum)
        [Static .py wrappers  OR  dynamic import hook]
                            │
                            ▼
              Universal C++ Engine (_stratum.so)
   ┌─────────────────────────────────────────────────────┐
   │ nanobind entry points: call_v/call_i/call_o/call_arr │
   │ Lazy metadata index + string pool                    │
   │ Thread-safe slot dispatch, RAII local-ref frames      │
   └─────────────────────────────────────────────────────┘
                            │
              JNI (direct CallXXXMethodA / GetXXXField)
                            ▼
                     Android JVM (ART)
   ┌─────────────────────────────────────────────────────┐
   │ Native Android SDK, Stratum runtime (StratumActivity, │
   │ StratumView), generated adapters (Adapter_*)          │
   └─────────────────────────────────────────────────────┘
```

---

## 🛠️ Building the Engine Pipeline

The pipeline (Stages 00–09) is what *produces* the `.so` + wheel described above. You only need this if you're building Stratum itself, or adding/removing SDK coverage.

| Stage | Purpose |
|---|---|
| `00_setup` | Verify toolchain, fetch Chaquopy targets, clone nanobind |
| `01_extract` | Unzip `android.jar` into raw `.class` files |
| `02_inspect` | Index available classes, seed `targets.json` |
| `03_javap` | Disassemble bytecode into JNI signatures |
| `04_parse` | Parse `javap` output into structured JSON |
| `04_5_sanitize` *(optional)* | Strip private / blocked hidden-API members |
| `05_resolve` *(2 passes)* | Assign class/method/field slots, tag parameter conversions |
| `05_5_abstract` | Generate Java adapters for abstract callbacks & interfaces |
| `06_cpp_emit` | Emit the 6-file universal C++ engine + metadata table |
| `07_build` | Compile `_stratum.so` via CMake + NDK + Ninja |
| `08_pyi_emit` | Generate Python wrapper classes (static or dynamic mode) |
| `09_wheel` | Package everything into an installable `.whl` |

Full commands, flags, and troubleshooting for every stage live in each stage's own `README.md` (`00_setup/README.md`, `01_extract/README.md`, …). A condensed end-to-end script:

```powershell
python 00_setup/main.py --ndk-path third_party/ndk25/android-ndk-r25c --jar-path third_party/android-35.jar --api-version 35 --ndk-api 24 --chaquopy-version 3.10.13-0 --output 00_setup/output
python 01_extract/main.py --setup 00_setup/output/setup_report.json --output 01_extract/output
python 02_inspect/main.py --input 01_extract/output --output 02_inspect/output
python 03_javap/main.py --input 01_extract/output --targets 02_inspect/targets.json --setup 00_setup/output/setup_report.json --output 03_javap/output
python 04_parse/main.py --input 03_javap/output --output 04_parse/output
python 05_resolve/main.py --input 04_parse/output --output 05_resolve/output
python 05_5_abstract/main.py --input 05_resolve/output --output 05_5_abstract/output --mode on
python 05_resolve/main.py --input 05_5_abstract/output/patched --output 05_resolve/output_patched
python 06_cpp_emit/main.py --input 05_resolve/output_patched --output 06_cpp_emit/output
python 07_build/main.py --cpp 06_cpp_emit/output --setup 00_setup/output/setup_report.json --nanobind third_party/nanobind --abi arm64-v8a --chaquopy 3.10.13-0 --output 07_build/output --log
python 08_pyi_emit/main.py --input 05_resolve/output_patched --output 08_pyi_emit/output
python 09_wheel/main.py --so 07_build/output/_stratum.so --py-src 08_pyi_emit/output --output 09_wheel/output --version 0.9.0 --min-api 24 --abi arm64-v8a --chaquopy 3.10.13-0
```

Tool versions tested: Python 3.10, NDK r25c, CMake 3.22+, Ninja (via `pip install ninja`), nanobind v2.12.0, JDK 17+.

---

## 📚 Where to go next

- **Writing an app?** → [`PYTHON_API.md`](PYTHON_API.md) — the complete usage reference, with working examples and a troubleshooting section.
- **Extending the pipeline / adding SDK coverage?** → each `NN_stage/README.md` in this repo.
- **Something not working?** → check [`PYTHON_API.md#troubleshooting--common-pitfalls`](PYTHON_API.md#-troubleshooting--common-pitfalls) first — most "it doesn't update" / "it crashed" reports trace back to one of a handful of known gotchas listed there.

## License
See `LICENSE` and `THIRD-PARTY-LICENSES.md`.









----------------------------------------
---
---



# Run commands for quick look

###  Stage 00: Setup & Validation
───────────────────────────────────────────
```
python 00_setup/main.py --ndk-path third_party/ndk25/android-ndk-r25c --jar-path third_party/android-35.jar --api-version 35 --ndk-api 24 --python-target-version "3.14.7" --output 00_setup/output
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
python 07_build/main.py --cpp 06_cpp_emit/output --setup 00_setup/output/setup_report.json --nanobind third_party/nanobind --abi arm64-v8a --output 07_build/output --log
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
python 09_wheel/main.py --so 07_build/output/_stratum.so --py-src 08_pyi_emit/output --output 09_wheel/output --version 0.9.0 --min-api 24 --abi arm64-v8a --py-version 3.14.7 --format folder
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
python 09_wheel/main.py --so 07_build/output/_stratum.so --py-src 08_pyi_emit/output --output 09_wheel/output --version 0.9.0 --min-api 24 --abi arm64-v8a --py-version 3.14.7 --include-pyi no --include-reflect yes --format folder
```

---
or 
```
python 09_wheel/main.py ^
    --so 07_build/output/_stratum.so ^
    --py-src 08_pyi_emit/output ^
    --output 09_wheel/output ^
    --setup 00_setup/output/setup_report.json ^
    --version 0.9.0 ^
    --abi arm64-v8a ^
    --include-pyi no ^
    --include-reflect yes
    --format folder
```
───────────────────────────────────────────



--------------------------------------------------------------------
---

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

