# Stratum Pipeline — Stage 08: Emit Python Stubs (`.pyi`)

## Quick Start

**Standard Run (Works safely for all apps , Recommended for Stratum(v0.2))**
```bash
python 08_pyi_emit/main.py --input "05_resolve/output/" --output "08_pyi_emit/output/"
```

**Patched Run (Recommended if you ran Stage 05.5)**
```bash
python 08_pyi_emit/main.py --input "05_5_abstract/output/patched/" --output "08_pyi_emit/output/"
```

> **Good to know:** If you ran Stage 05.5 but accidentally use the standard `05_resolve` input here, **it will not cause any pipeline errors or crashes.** The script will still generate perfectly valid `.pyi` files. However, using the `patched/` folder is recommended because it ensures your IDE's autocomplete perfectly matches the specific Callback Adapters generated in Stage 05.5.

---

## What This Stage Does

Stage 08 generates Python stub files (`.pyi`). While the C++ generated in Stage 06 provides the actual *runtime* behavior, Python IDEs (like VSCode or PyCharm) cannot read C++ `.so` files to give you autocomplete.

This stage translates the Java class definitions into valid Python type-hints. When you type `activity.findViewBy...` in your Python code, your IDE will know exactly what methods exist, what arguments they take, and what they return, because it reads the `.pyi` files generated here.

---

## Command-Line Arguments

| Argument | Required | Description |
|---|---|---|
| `--input` | ✅ Yes | Path to the JSON ASTs. Use `05_resolve/output/` generally, or `05_5_abstract/output/patched/` for highest accuracy if using callbacks. |
| `--output` | ✅ Yes | Directory where the `.pyi` files and `__init__.pyi` packages will be written. |

---

## Key Features & Transformations

1. **Inner-Class Sanitization:** Java uses `$` for inner classes (e.g., `LinearLayout$LayoutParams`). This stage automatically sanitizes them to underscores (`LinearLayout_LayoutParams`) so they are valid Python identifiers.
2. **Method Overload Deduplication:** If a Java class has multiple constructors or overloaded methods (e.g., `inflate(int)` and `inflate(int, ViewGroup)`), Stage 08 safely wraps them using Python's `@overload` typing decorator.
3. **Field Accessors:** Exposes Java `public static final` fields as Python class methods (e.g., `Color.f_get_BLACK()`).
4. **Inherited Methods:** It emits inherited methods directly into the child stub as comments or method signatures. This ensures your IDE knows a `Button` has `setVisibility()` because it inherited it from `View`.

---

## Output Structure

The output mirrors the standard Android Java package structure, but as Python modules:

```text
08_pyi_emit/output/
├── pyi_summary.json
└── android/
    ├── __init__.pyi
    ├── app/
    │   ├── __init__.pyi
    │   └── Activity.pyi
    ├── widget/
    │   ├── __init__.pyi
    └── Button.pyi
```

Inside `Button.pyi`, you will see pure Python typing:
```python
from stratum.android.widget.TextView import TextView

class Button(TextView):
    def __init__(self, context: 'Context') -> None: ...
    def setText(self, text: str) -> None: ...
    def _get_jobject_ptr(self) -> int: ...
```

---

## Exit Behaviour & Next Steps

| Result | Exit Code | Description |
|---|---|---|
| Success | `0` | `.pyi` files generated safely. Proceed to Stage 09. |
| Missing Input | `1` | The `--input` path is incorrect or missing. |

**Next Step:** Proceed to the final stage, Stage 09 (Wheel Build), to package the `.so` and `.pyi` files together.