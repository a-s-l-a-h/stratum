
# Stratum Pipeline — Stage 08: Python Wrapper & Stub Emitter

## Overview

Stage 08 (`08_pyi_emit/main.py`) generates the high-level Python API surface for Stratum. It supports two distinct generation modes:
1. **Static Mode (`--mode static`)**: Generates individual `.py` modules and `.pyi` type stubs for every Java class (traditional, transparent, IDE-friendly).
2. **Dynamic Mode (`--mode dynamic`)**: Compresses all class metadata into a single `_meta.json.gz` file and installs a custom Python import hook (`stratum._dynamic`). Classes are synthesized dynamically at runtime using `type()`. Produces extremely small wheels.

Regardless of mode, application code (`import stratum.android.widget.Button as Button`) works identically.

---

## Core Components Emitted

### 1. `stratum.core.stratum_object.StratumObject`
The universal base class for all wrapped Java instances:
* Holds `_ptr`: an integer representing a JNI `jobject` global reference.
* Memory management: In `__del__`, automatically removes associated callbacks and invokes `_core.delete_ref(self._ptr)`.
* Implements rich comparison (`__eq__`, `__ne__`) using JNI `IsSameObject`.
* Implements `__hash__`, `__str__`, and `__repr__` backed by real Java `hashCode()` and `toString()`.
* **Safe Downcasting**: `Class.from_ptr(ptr_or_obj)` verifies instance inheritance via JNI `IsInstanceOf` before casting.

### 2. Resilient Lazy Resolution (`_get_parent_class` & `_wrap_instance`)
Generated code never imports parent or return-type classes eagerly at module load time. Instead, it uses lazy helpers:
* If a class was excluded from the build via `targets.json`, the import hook catches `ModuleNotFoundError` and safely falls back to `StratumObject` without crashing.
* Any other genuine error (e.g. syntax error or circular dependency) logs an explicit warning to `stderr` once and caches the failure.

### 3. Overload Disambiguation Algorithm
When a Java method has multiple overloads under the same name, the generated dispatcher groups them by argument count (`argc`), and disambiguates between same-arity overloads by checking the first differing parameter:
* Distinguishes `bool` from `int` (handling Python's `isinstance(True, int) == True` quirk).
* Distinguishes boxed wrapper types (`java.lang.Integer`, `java.lang.Boolean`).
* Distinguishes arrays, `str`, `dict`, and `list`.
* For arbitrary Java object parameters, checks type compatibility against `_target_class_id` using JNI `is_instance_of`.

### 4. Field Accessors & Camel/Snake Aliases
* Methods are generated with both their native Java name (`setText`) and PEP 8 snake_case alias (`set_text`).
* Static fields: `sf_get_<name>()`, `sf_set_<name>(val)`.
* Instance fields: `f_get_<name>()`, `f_set_<name>(val)`.

---

## CLI Options

| Argument | Required | Default | Description |
| :--- | :---: | :---: | :--- |
| `--input` | **Yes** | — | Path to Stage 05 Pass 2 output (`05_resolve/output_patched/`). |
| `--output` | **Yes** | — | Target destination (`08_pyi_emit/output/`). |
| `--mode` | No | `static` | `static` emits per-class `.py`/`.pyi` files; `dynamic` emits `_meta.json.gz` + `_dynamic.py`. |

---

## Command Line Examples

### Generate Full Static Stubs (Standard):
```bash
python 08_pyi_emit/main.py \
    --input 05_resolve/output_patched \
    --output 08_pyi_emit/output \
    --mode static
```

### Generate Dynamic Compact Metadata (Small Footprint):
```bash
python 08_pyi_emit/main.py \
    --input 05_resolve/output_patched \
    --output 08_pyi_emit/output \
    --mode dynamic
```
