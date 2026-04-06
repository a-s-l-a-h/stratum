# Stratum Pipeline — Stage 04: Parse javap to JSON

## Quick Start

```bash
python 04_parse/main.py --input "03_javap/output/" --output "04_parse/output/"
```

---

## What This Stage Does

Stage 04 is the brain of the analysis phase. It reads the raw text `.javap` files generated in Stage 03 and parses them into structured, highly-detailed JSON Abstract Syntax Trees (ASTs). 

During this process, it decodes Java's raw JNI descriptors (like `(ILjava/lang/String;)V`) and maps them directly to their corresponding C++ types (`int32_t`, `std::string`) and Python types (`int`, `str`). It also detects callbacks, marks overloaded methods, and extracts static constants.

This JSON becomes the ultimate data source for Stage 05 (Resolution) and Stage 06 (C++ Generation).

---

## Command-Line Arguments

| Argument | Required | Description |
|---|---|---|
| `--input` | ✅ Yes | Path to Stage 03's output directory (`03_javap/output/`). Must contain the generated `.javap` text files. |
| `--output` | ✅ Yes | Directory where the parsed `.json` files and `parse_summary.json` will be written. |

---

## Key Features & Heuristics

Stage 04 does much more than just regex parsing. It applies several Stratum-specific rules to prepare the data for Nanobind:

1. **Type Mapping:** Converts JNI primitive descriptors (e.g., `Z` → `bool`, `I` → `int`, `F` → `float`), handles boxed primitives (`java.lang.Integer`), and String types (`java.lang.CharSequence`).
2. **Proxy Detection:** If a method parameter is a class ending in `Listener`, `Callback`, `Observer`, `Handler`, or `Runnable`, it flags it as `needs_proxy: true`. This tells the later C++ generator to wrap Python functions into Java interface instances.
3. **Constructor Patching:** Identifies constructors, forces their return type to `void`, and assigns them a special `jni_new_sig` property required for JNI object instantiation.
4. **Dependency Graphing:** Scans all method parameters, return types, and implemented interfaces to build a `depends_on` list. This is used later to sort C++ `#include` headers topologically.
5. **Overload Tracking:** Counts methods with the exact same name and assigns them an `overload_index`.

---

## Output: Generated `.json` AST Files

The parsed files are written to `04_parse/output/` preserving the package folder structure.

```text
04_parse/output/
├── parse_summary.json
└── android/
    ├── app/
    │   └── Activity.json
    ├── view/
    │   └── View.json
    └── widget/
        └── Button.json
```

### Anatomy of a Parsed Class JSON

If you open one of these `.json` files, you will see a fully resolved representation of the class.

```json
{
  "fqn": "android.widget.Button",
  "jni_name": "android/widget/Button",
  "simple_name": "Button",
  "package": "android.widget",
  "is_abstract": false,
  "is_interface": false,
  "parent_fqn": "android.widget.TextView",
  "depends_on": [
    "android/content/Context",
    "java/lang/CharSequence"
  ],
  "methods": [
    {
      "name": "setText",
      "is_static": false,
      "is_constructor": false,
      "jni_signature": "(Ljava/lang/CharSequence;)V",
      "return_jni": "void",
      "return_cpp": "void",
      "return_python": "None",
      "params": [
        {
          "index": 0,
          "name": "arg0",
          "java_type": "java.lang.CharSequence",
          "jni_type": "jstring",
          "cpp_type": "std::string",
          "python_type": "str",
          "conversion": "string_in",
          "needs_proxy": false
        }
      ],
      "overload_index": 0,
      "is_overloaded": true
    }
  ],
  "fields": [
    {
      "name": "NO_ID",
      "java_type": "int",
      "is_static": true,
      "is_final": true,
      "constant_value": "-1",
      "jni_type": "jint"
    }
  ]
}
```

---

## Output: `parse_summary.json`

Written to `04_parse/output/parse_summary.json` after the run finishes. Provides aggregate statistics on the extracted codebase.

```json
{
  "total_parsed": 18,
  "total_methods": 542,
  "total_fields": 112,
  "total_needing_proxy": 14,
  "total_annotations": 0,
  "total_inner_classes": 3,
  "failed": 0,
  "failed_files": []
}
```

### Field Reference

| Field | Description |
|---|---|
| `total_parsed` | Number of `.javap` files successfully converted to JSON. |
| `total_methods` | Total number of methods/constructors extracted across all classes. |
| `total_fields` | Total number of `static final` constants found. |
| `total_needing_proxy` | Number of methods requiring cross-language Callback/Listener bridging. |
| `total_inner_classes` | Classes with a `$` in their name. |

---

## Exit Behaviour

| Result | Exit Code | Description |
|---|---|---|
| All files parsed | `0` | JSON ASTs generated safely. Proceed to Stage 05. |
| Missing Input | `1` | Stage 03 was not run or the `--input` path is incorrect. |
| Missing `.javap` files | `1` | Input directory exists but is empty. |
