
# Stratum Pipeline — Stage 09: Python Wheel Packaging

## Overview

Stage 09 (`09_wheel/main.py`) bundles the compiled C++ shared library (`_stratum.so`), the Python wrapper modules (from Stage 08), the runtime helpers, and package metadata into a standard, PEP-compliant Python wheel (`.whl`) ready for deployment in Chaquopy Android projects.

---

## Wheel Packaging Details

### Standard Wheel Filename Structure
```
stratum-<version>-<pytag>-<pytag>-android_<minapi>_<abi>.whl
```
Example: `stratum-0.9.0-cp312-cp312-android_24_arm64_v8a.whl`

### Injected Core Modules
In addition to the generated SDK classes, Stage 09 injects runtime convenience modules directly into the root `stratum/` package:

1. **`stratum/__init__.py`**:
   * `getActivity()` / `get_activity()`: Returns the active Android `Activity` wrapped in `stratum.android.app.Activity`.
   * `setContentView(activity, view)`: Mounts a View to the activity.
   * `run_on_ui_thread(fn, *args, **kwargs)` / `@ui_thread`: Dispatches calls to Android's main looper.
   * `to_java(py_obj)`: Recursively converts native Python structures (dict, list, int, etc.) to Java (`HashMap`, `ArrayList`, Boxed primitives).
   * `to_py(java_ptr)`: Recursively converts Java structures into native Python data.
   * `@export`: Decorator exposing a Python function for Java callers via `StratumRuntimeLookup.callPython()`.
2. **`stratum/ui.py`**:
   * Provides `CustomCanvasView`, allowing 2D hardware-accelerated canvas rendering in pure Python via overridden `on_draw(canvas)`, `on_touch_event(event)`, and `on_measure(w, h)` methods.
3. **`stratum/reflect.py`** (Optional via `--include-reflect`):
   * Dynamic Java reflection fallback allowing Python to call un-indexed or custom Java classes at runtime via `call_java()`, `call_java_static()`, and `call_java_method()`.

### Native Permission Preservation
When packaging `_stratum.so`, standard ZIP tools assign default permissions (`0600`). On Android, this causes dynamic loader failure (`dlopen failed: Permission denied`). Stage 09 explicitly sets Unix permissions:
```python
so_info.external_attr = 0o755 << 16  # rwxr-xr-x
```

---

## CLI Options

| Argument | Required | Default | Description |
| :--- | :---: | :---: | :--- |
| `--so` | **Yes** | — | Path to compiled `_stratum.so` from Stage 07. |
| `--py-src` | **Yes** | — | Path to Python source directory from Stage 08. |
| `--output` | **Yes** | — | Output directory for the generated `.whl`. |
| `--version` | No | `0.9.0` | Package semantic version. |
| `--min-api` | No | `24` | Target Android minimum SDK level. |
| `--abi` | No | `arm64-v8a` | Target ABI architecture. |
| `--setup` | No | `None` | Path to `setup_report.json` to inherit Python version and NDK API automatically. |
| `--py-version` | No | `3.14.7` | Target CPython Android version. |
| `--include-reflect` | No | `yes` | Include `stratum/reflect.py` escape hatch (`yes` or `no`). |
| `--include-pyi` | No | `yes` | Include `.pyi` IDE stub files in the wheel (`yes` or `no`). |

---

## Command Line Example

```bash
python 09_wheel/main.py \
    --so 07_build/output/_stratum.so \
    --py-src 08_pyi_emit/output \
    --output 09_wheel/output \
    --version 0.9.0 \
    --min-api 24 \
    --abi arm64-v8a \
    --chaquopy 3.12.0-0 \
    --include-pyi no
```