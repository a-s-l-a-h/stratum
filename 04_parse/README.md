
# Stratum Pipeline — Stage 04: Parse (`javap` to JSON AST)

## Overview

Stage 04 is the compiler front-end of the Stratum pipeline. It parses the raw text disassembly emitted by `javap -s -p` in **Stage 03** into structured, strongly typed **JSON Intermediate Representation (IR)** files.

During this stage, raw JVM bytecode descriptors (such as `(Landroid/view/View;I)V`) are parsed and mapped to their corresponding C++ types (`uint32_t`, `std::string`, `jobject`), Python types (`int`, `str`, `object`), and Stratum conversion rules. It normalizes method overloads, tracks constants, resolves inheritance and interface dependencies, and flags candidate methods for dynamic proxy generation.

---

## Quick Start

```bash
# Standard execution
python 04_parse/main.py \
    --input 03_javap/output \
    --output 04_parse/output
```

---

## Command-Line Arguments

| Argument | Required | Type | Description |
|---|---|---|---|
| `--input` | ✅ Yes | `str` | Directory containing `.javap` output from Stage 03 (`03_javap/output/`). |
| `--output` | ✅ Yes | `str` | Destination directory where the structured `.json` AST files and `parse_summary.json` will be written. |

---

## Architecture & Technical Mechanics

### 1. Robust Type & Descriptor Mapping
Java method signatures and field descriptors are parsed via a state-machine tokenizer (`parse_descriptor`) and translated through mapping tables:
* **Primitives (`PRIMITIVE_MAP`):** Translates JNI scalar tags (`Z`, `B`, `C`, `S`, `I`, `J`, `F`, `D`, `V`) to C++ integer/float primitives and Python primitive types.
* **Strings (`STRING_TYPES`):** Maps `java/lang/String` and `java/lang/CharSequence` to `jstring` / `std::string` / `str` with `string_in` / `string_out` converters.
* **Boxed Primitives CheckJNI Fix (`BOXED_PRIMITIVES`):**
  * Java methods returning boxed objects (e.g., `java.lang.Integer`) return an `Object` reference at the JVM descriptor level (`Ljava/lang/Integer;`).
  * `map_return_type()` strictly enforces `jni_type: "jobject"` with `conversion: "unbox_out"`.
  * **Why:** Calling a primitive method (like `CallIntMethodA` or `GetIntField`) on an object-returning JVM descriptor causes an instant, fatal ART abort under Android's `CheckJNI`.

### 2. v10 Array Return Type Resolution
* In older versions, array return types often fell back to the placeholder string `"array"`. When emitted into generated Java adapters (`public array delete(byte[] a)`), this triggered fatal `cannot find symbol class array` compile errors.
* `parse_descriptor()` now decodes the true element type (primitives `[B` → `byte[]`, references `[Ljava/lang/String;` → `java.lang.String[]`), correctly tracking dimension counts (`array_dims`) and concrete element types (`element_type`).

### 3. Proxy Interface Candidate Detection
* The parser scans parameters for known callback interfaces using `PROXY_SUFFIXES = ("Listener", "Callback", "Observer", "Runnable")`.
* **Important Design Decision:** `Handler` was intentionally removed from this list. `android.os.Handler` is a concrete class, not an interface; creating a dynamic `java.lang.reflect.Proxy` on a class throws an `IllegalArgumentException` at runtime. Handlers are therefore mapped as normal Java object references (`tag 'L'`).

### 4. Constructor Normalization
* Constructors are identified by matching class simple names or FQNs.
* They are assigned the standard internal name `__init__`.
* Constructors are forced to return `void` (`return_jni: "void"`, `is_void: true`).
* They are tagged with `jni_new_sig = descriptor`, which Stage 05 and Stage 06 require for instantiating objects via `NewObjectA`.

### 5. Generics & Declaration Sanitization
* The helper `strip_generics()` strips generic signatures (e.g., `<T, V extends List<T>>`) from return types and method names before token splitting.
* Annotations (`@interface`) are normalized and extracted as interface declarations with an `is_annotation: true` flag.

### 6. Overload Indexing & Dependency Graph
* **Deterministic Overload Indices:** Methods sharing the same name are counted and assigned an incremental `overload_index` alongside an `is_overloaded: bool` flag.
* **`depends_on` Indexing:** Referenced classes in parameters, return types, superclasses, and interfaces are extracted into a deduplicated `depends_on` list for topological analysis and circular dependency diagnostics.

---

## Directory Layout

```text
04_parse/
├── main.py
├── README.md
└── output/
    ├── parse_summary.json
    ├── android/
    │   ├── app/
    │   │   ├── Activity.json
    │   │   └── Service.json
    │   ├── view/
    │   │   ├── View.json
    │   │   └── ViewGroup.json
    │   └── widget/
    │       └── Button.json
    └── java/
        └── lang/
            └── Object.json
```

---

## Class JSON Schema Example

Below is an annotated excerpt of `android/widget/TextView.json`:

```json
{
  "fqn": "android.widget.TextView",
  "jni_name": "android/widget/TextView",
  "simple_name": "TextView",
  "package": "android.widget",
  "source_file": "TextView.java",
  "is_abstract": false,
  "is_interface": false,
  "is_annotation": false,
  "is_enum": false,
  "is_final": false,
  "is_inner_class": false,
  "parent_fqn": "android.view.View",
  "parent_jni": "android/view/View",
  "parent_simple": "View",
  "interfaces": [
    "android.view.ViewTreeObserver$OnPreDrawListener"
  ],
  "interfaces_jni": [
    "android/view/ViewTreeObserver$OnPreDrawListener"
  ],
  "methods": [
    {
      "name": "setText",
      "is_static": false,
      "is_constructor": false,
      "is_abstract": false,
      "is_native": false,
      "is_private": false,
      "is_protected": false,
      "is_public": true,
      "is_final": false,
      "return_hint": "void",
      "jni_signature": "(Ljava/lang/CharSequence;)V",
      "return_jni": "void",
      "return_cpp": "void",
      "return_python": "None",
      "return_java_type": "void",
      "return_jni_class": null,
      "return_conversion": "none",
      "return_is_array": false,
      "is_void": true,
      "params": [
        {
          "index": 0,
          "name": "arg0",
          "java_type": "java.lang.CharSequence",
          "jni_class": "java/lang/CharSequence",
          "needs_proxy": false,
          "is_array": false,
          "jni_type": "jstring",
          "cpp_type": "std::string",
          "python_type": "str",
          "conversion": "string_in"
        }
      ],
      "throws": [],
      "needs_proxy": false,
      "overload_index": 0,
      "is_overloaded": true
    }
  ],
  "fields": [
    {
      "name": "AUTO_SIZE_TEXT_TYPE_NONE",
      "java_type": "int",
      "is_static": true,
      "is_final": true,
      "is_private": false,
      "is_protected": false,
      "is_public": true,
      "constant_value": "0",
      "descriptor": "I",
      "jni_type": "jint",
      "cpp_type": "int32_t",
      "python_type": "int",
      "jni_signature": "I"
    }
  ],
  "depends_on": [
    "android/content/Context",
    "android/view/View",
    "java/lang/CharSequence"
  ]
}
```

---

## Output: `parse_summary.json`

At completion, Stage 04 writes an execution report:

```json
{
  "total_parsed": 4820,
  "total_methods": 76540,
  "total_fields": 18450,
  "total_needing_proxy": 1280,
  "total_annotations": 142,
  "total_inner_classes": 1890,
  "failed": 0,
  "failed_files": []
}
```

---

## Next Steps in Pipeline

The output JSON from this stage can be directed to either:
1. **Stage 04.5 (`04_5_sanitize`):** (Recommended for safety) To strip private methods/fields and blocked Android hidden APIs.
2. **Stage 05 Pass 1 (`05_resolve`):** Direct resolution (pass `--input 04_parse/output`).
