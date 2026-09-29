
# Stratum Pipeline — Stage 06: Universal Engine & Metadata Emit

## Overview

Stage 06 (`06_cpp_emit/main.py`) emits the universal, static C++ JNI bridge engine and metadata lookup tables into `06_cpp_emit/output/core/`.

### The Architecture Breakthrough
Traditional bindings generate separate C++ source/header files per Java class, leading to:
1. Thousands of compilation units causing compiler out-of-memory (OOM) errors.
2. Inter-class C++ type dependencies where a missing class breaks the entire compilation.
3. 15–45 minute compilation times.

**Stratum resolves this completely:**
Stage 06 emits **exactly 6 static files** regardless of whether you are compiling 5 classes or 10,000 classes. All classes, methods, and fields are represented as compact binary data in a flat metadata table. Resolution of JNI IDs occurs **lazily on first use at runtime**.

---

## Emitted Files

All files are emitted into `06_cpp_emit/output/core/`:

| File | Purpose |
| :--- | :--- |
| `metadata_table.h` | C struct definitions (`MethodMeta`, `FieldMeta`, `ClassMeta`) and extern declarations. |
| `metadata_table.cpp` | Deduplicated string pool and flat static arrays for all classes, methods, and fields. |
| `bridge_core.h` | JNI thread management, `JniLocalFrame` RAII helper, and type conversion prototypes. |
| `bridge_core.cpp` | Global callback registry, thread-safe detach handlers, and bidirectional data serialization (`stratum_py_to_java`, `stratum_java_to_py`). |
| `stratum_engine.cpp` | The core runtime dispatcher: `call_v`, `call_z`, `call_i`, `call_j`, `call_d`, `call_str`, `call_o`, `call_arr`, `call_list`, `call_map`, `new_instance`, `delete_ref`, field getters, and field setters. |
| `bridge_main.cpp` | Nanobind module definition (`NB_MODULE(_stratum)`), `JNI_OnLoad` initialization, and Android lifecycle entry points. |

---

## Runtime Safety & Design Patterns

### 1. `JniLocalFrame` RAII Guard (Local Reference Overflow Prevention)
Android's ART runtime enforces a strict ceiling of **512 local JNI references**. Tight Python loops iterating through UI views, reading fields, or invoking methods would easily overflow this table and crash the process.
Every call and field access function wraps execution in:
```cpp
JniLocalFrame _local_frame(env, 32);
```
This ensures all temporary JNI local references are freed immediately upon returning from the dispatch function.

### 2. Deadlock-Proof Lazy Resolution (`std::recursive_mutex`)
Class slots are resolved on first touch via `resolve_class_slots()`. This requires calling `find_class()`, which can invoke classloaders and trigger static initializers (`<clinit>`). If a static initializer or constructor calls back into Stratum on the same thread, a standard `std::mutex` deadlocks. Stratum uses `std::recursive_mutex` to ensure re-entrancy safety.

### 3. Stack Protection in `pack_arguments()`
Arguments packed from Python tuples into C++ `jvalue jargs[32]` arrays are strictly bounds-checked. Passing more than 32 arguments or mismatched argument counts throws a catchable `std::runtime_error` rather than corrupting native stack memory.

### 4. Background Thread Attachment via `pthread_key`
Python's garbage collector may run `__del__` (invoking `delete_ref()`) on arbitrary OS threads never registered with the JVM. `get_env()` automatically attaches unattached threads via `AttachCurrentThread()` and registers a POSIX thread-specific destructor (`pthread_key_t`) that cleanly detaches the thread upon termination, preventing resource leaks.

---

## CLI Options

| Argument | Required | Description |
| :--- | :---: | :--- |
| `--input` | **Yes** | Path to Stage 05 Pass 2 output directory (`05_resolve/output_patched/`). |
| `--output` | **Yes** | Destination folder for C++ engine files (`06_cpp_emit/output/`). |

---

## Command Line Example

```bash
python 06_cpp_emit/main.py \
    --input 05_resolve/output_patched \
    --output 06_cpp_emit/output
```
