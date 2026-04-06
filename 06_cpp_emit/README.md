# Stratum Pipeline — Stage 06: C++ Emit (Nanobind)

## Quick Start

**Path A: If you skipped Stage 05.5 (Simple Apps)**
```bash
python 06_cpp_emit/main.py --input "05_resolve/output/" --output "06_cpp_emit/output/"
```

**Path B: If you ran Stage 05.5 (Advanced Apps with Callbacks)**
```bash
python 06_cpp_emit/main.py --input "05_5_abstract/output/patched/" --output "06_cpp_emit/output/"
```

---

## What This Stage Does

Stage 06 is the heavy lifter. It takes the fully resolved (and optionally patched) JSON ASTs and translates them into **production-ready C++ code using the [nanobind](https://nanobind.readthedocs.io/en/latest/) library**. 

This C++ code acts as the ultimate two-way bridge between your Python scripts and the Android JVM. It handles thread attachment, JNI signature lookups, global reference memory management, Java exception translation, and Python-to-Java type conversions.

---

## Command-Line Arguments

| Argument | Required | Description |
|---|---|---|
| `--input` | ✅ Yes | Path to the resolved JSON ASTs (from `05_resolve` or `05_5_abstract`). |
| `--output` | ✅ Yes | Directory where the generated C++ source files and headers will be written. |
| `--batch-size` | No | Number of classes to register per C++ batch function. Default is `100`. (Prevents compiler Out-of-Memory crashes). |

---

## Architecture: How the Generated C++ Works

The generated C++ revolves around a base class called `StratumObject`. Everything emitted by Stratum inherits from this.

### 1. The C++ Struct Hierarchy
Stratum mirrors the Java inheritance tree in C++. 
If Java has `class Button extends TextView`, Stratum generates:
```cpp
struct Stratum_android_widget_TextView : public Stratum_android_view_View { ... };
struct Stratum_android_widget_Button : public Stratum_android_widget_TextView { ... };
```
Because of this, if a Python function expects a `View`, you can pass a `Button` and C++ polymorphism will natively handle it.

### 2. Memory Management (Global Refs)
When Python instantiates a Java object via a wrapper:
1. JNI creates the object (`NewObject`).
2. C++ grabs a global reference (`NewGlobalRef`) to prevent the JVM Garbage Collector from destroying it.
3. The pointer is wrapped in the C++ struct and handed to Python.
4. When Python's Garbage Collector cleans up the object, the C++ struct destructor fires and calls `DeleteGlobalRef`, allowing Java to finally free the memory.

### 3. Variable Mapping & Type Conversions
The generator implements automatic type translation in both directions using Nanobind:

| Java Type | C++ JNI Type | C++ Target Type | Python Type |
|---|---|---|---|
| Primitives (`int`, `boolean`) | `jint`, `jboolean` | `int32_t`, `bool` | `int`, `bool` |
| `java.lang.String` | `jstring` | `std::string` | `str` |
| `byte[]` | `jbyteArray` | `nanobind::bytes` | `bytes` |
| Primitive Arrays (`int[]`) | `jintArray` | `std::vector<int32_t>` | `list[int]` |
| Object Arrays (`Object[]`) | `jobjectArray` | `nanobind::list` | `list` |
| Any Java Object | `jobject` | `Stratum_..._ClassName*` | Custom Class |

### 4. Nullable Pointers `nb::arg().none(true)`
By default, C++ strongly-typed libraries reject Python `None`. Stage 06 explicitly tags every object pointer parameter with `.none(true)`. If you pass `None` in Python, the C++ wrapper safely converts it to `nullptr`, which translates perfectly to `null` in Java.

### 5. Exception Translation
Every JNI method call is followed by an exception check. If Java throws an exception (e.g., `NullPointerException`), the generated C++ catches it via `env->ExceptionCheck()`, retrieves the Java message, clears the JNI error state, and throws a `std::runtime_error` which bubbles up into Python as an exception stack trace.

### 6. Module Batching
Nanobind relies heavily on C++ templates. Compiling 500+ classes into a single initialization block will cause Clang/GCC to run out of RAM and crash. Stage 06 avoids this by chunking class registrations into `register_batch_0`, `register_batch_1`, etc., keeping compile times low and memory usage safe.

---

## Outputs

The generated C++ is split into two directories:

### `06_cpp_emit/output/core/`
The handwritten structural foundation of the bridge:
*   **`stratum_structs.h`**: Forward declarations and definitions of every C++ struct (`StratumObject`, `Stratum_android_app_Activity`, etc.).
*   **`bridge_core.h` / `bridge_core.cpp`**: The JNI lifecycle manager. Contains `JNI_OnLoad`, thread attachment (`get_env()`), the callback dictionary (`g_callbacks`), and dynamic `ClassLoader` logic.
*   **`bridge_main.cpp`**: The Nanobind module entry point (`NB_MODULE`). Wires together the batches and registers base lifecycle overrides.

### `06_cpp_emit/output/generated/`
Contains one `.cpp` file per Java class (e.g., `android_widget_Button.cpp`). 
Each file contains:
*   `GetMethodID` caches (resolved at runtime on first use).
*   Instance method wrappers with GIL (Global Interpreter Lock) release logic (so Python doesn't freeze the UI thread while Java runs).
*   Nanobind class registration block (`cls.def(...)`).

### `06_cpp_emit/output/api_surface_reference.md`
A highly useful Markdown document detailing the exact Python API that was generated. You can use this to look up exactly how a method was translated, what its arguments are, and what it returns.

---

## Advanced: Debugging and Logging

Stage 06 embeds a powerful C++ macro logging system to trace JNI issues. By default, it is quiet for maximum performance. You can turn it on by adding flags to your CMake build later.

| CMake Flag | Logcat Tag Output | Description |
|---|---|---|
| *(None)* | `Stratum` | Only warnings, Java exceptions, and fatal errors. |
| `STRATUM_VERBOSE_LOG=1` | `Stratum` | Logs class initializations, Method ID lookups, and method entry/exit events. |
| `STRATUM_ULTRA_LOG=1` | `Stratum/TRACE`, `Stratum/ARG` | **Warning: Extremely Noisy.** Logs the direction of every JNI call (`PY->CPP->JNI`), traces every single argument converted to C++, and traces every return value pointer. Use only when debugging crashes. |

*To view ultra-logs on device:*
```bash
adb logcat -s 'Stratum:V' 'Stratum/TRACE:V' 'Stratum/ARG:V' 'Stratum/RET:V'
```

---

## Exit Behaviour & Next Steps

| Result | Exit Code | Description |
|---|---|---|
| Success | `0` | C++ files emitted safely. Ready to be compiled. |
| Missing Input | `1` | Stage 05 (or 05.5) was not run or the path is incorrect. |
| Prefix Collision | `0` | (Warning printed) Two classes resolved to the same C++ name. Auto-fixed with a numeric suffix (e.g., `ClassName_2`). |

**Next Step:**
You are now done with the Python generation pipeline! 
You will take the contents of `06_cpp_emit/output/core/` and `06_cpp_emit/output/generated/` and compile them using Android Studio's **CMake / NDK**. (Stage 07 handles building).