
# Stratum Pipeline — Stage 05: Resolve & Slot Assignment

## Overview

Stage 05 (`05_resolve/main.py`) is the deterministic indexing and layout engine of Stratum. It translates high-level Java class descriptions (from Stage 04 parse or Stage 04.5 sanitize) into a fixed, indexed slot layout where every class has an integer `class_id` (`0..N-1`) and every constructor, method, and field is assigned a zero-indexed `slot` (`0..M-1`).

By precomputing parameter encoding tags (`param_tags`), return type identifiers (`ret_type_id`), and adapter class signatures ahead of time, Stage 05 eliminates all runtime JNI descriptor parsing in the C++ engine.

---

## The Critical Two-Pass Architecture

Stage 05 is run **twice** during a full pipeline build. **Never skip Pass 1 or combine the two passes.**

```
[04_parse or 04_5_sanitize]
            │
            ▼
┌──────────────────────┐
│ 05_resolve (Pass 1)  │ ──► Writes: 05_resolve/output/
└──────────────────────┘
            │
            ▼
┌──────────────────────┐
│ 05_5_abstract        │ ──► Emits Java adapters (.java)
└──────────────────────┘     Patches JSON: 05_5_abstract/output/patched/
            │
            ▼
┌──────────────────────┐
│ 05_resolve (Pass 2)  │ ──► Writes: 05_resolve/output_patched/
└──────────────────────┘
            │
            ├──► Feeds 06_cpp_emit (metadata_table.cpp)
            └──► Feeds 08_pyi_emit (stratum.android.* wrappers)
```

1. **Pass 1 (`04_parse/output/` ➔ `05_resolve/output/`)**:
   Produces an initial deterministic slot assignment and closure indexing so that Stage 05.5 has a normalized schema to inspect for abstract classes and callback interfaces.
2. **Pass 2 (`05_5_abstract/output/patched/` ➔ `05_resolve/output_patched/`)**:
   Processes the patched JSONs where callback parameters now carry `needs_adapter: true` and `adapter_jni`. Pass 2 converts these into `'a'` (abstract adapter) or `'p'` (dynamic proxy) tags and embeds the adapter JNI paths directly into method records. **Stage 06 and Stage 08 consume Pass 2 output.**

---

## Internal Mechanisms

### 1. Deterministic Method & Field Ordering
To ensure that C++ metadata tables and Python wrapper dispatchers match identically without inter-process communication, methods and fields are sorted deterministically:
* **Constructors**: Sorted alphabetically by `jni_signature`.
* **Methods**: Sorted by `(name, jni_signature)`.
* Constructors occupy slots `0..K-1`; normal methods occupy slots `K..M-1`.
* **Fields**: Sorted by declaration order as parsed from bytecode.

### 2. Parameter Tag Encoding Table (`param_tags`)
Every method has a precomputed string of single-character tags matching its parameter list. The C++ engine’s `pack_arguments()` switch statement and Python’s overload dispatcher rely strictly on this table:

| Tag | Java Type / Equivalent | Input Type Expected from Python | C++ Conversion Handling |
| :---: | :--- | :--- | :--- |
| `Z` | `boolean` | `bool` | `jvalue.z = JNI_TRUE/FALSE` |
| `B` | `byte` | `int` | `jvalue.b = (jbyte)val` |
| `C` | `char` | `str` (1 char) or `int` | Decodes UTF-8 codepoint to `jchar` |
| `S` | `short` | `int` | `jvalue.s = (jshort)val` |
| `I` | `int` | `int` | `jvalue.i = (jint)val` (range-checked) |
| `J` | `long` | `int` | `jvalue.j = (jlong)val` |
| `F` | `float` | `float` | `jvalue.f = (jfloat)val` |
| `D` | `double` | `float` | `jvalue.d = (jdouble)val` |
| `s` | `String`, `CharSequence` | `str` / `None` | UTF-8 ➔ UTF-16 `jstring` |
| `[` | `byte[]` | `bytes`, `bytearray`, `memoryview` | Direct buffer or `SetByteArrayRegion` |
| `]` | `int[]` | `list[int]` or `tuple[int]` | `SetIntArrayRegion` |
| `q` | `long[]` | `list[int]` | `SetLongArrayRegion` |
| `f` | `float[]` | `list[float]` | `SetFloatArrayRegion` |
| `d` | `double[]` | `list[float]` | `SetDoubleArrayRegion` |
| `b` | `boolean[]` | `list[bool]` | `SetBooleanArrayRegion` |
| `c` | `char[]` | `list[str\|int]` | Encoded to `jcharArray` |
| `h` | `short[]` | `list[int]` | `SetShortArrayRegion` |
| `T` | `String[]` | `list[str]` | `NewObjectArray` of `java.lang.String` |
| `A` | `Object[]` (or any typed object array) | `list[StratumObject\|str\|None]` | `NewObjectArray` of `java.lang.Object` |
| `M` | `java.util.List`, `Collection`, `Iterable` | `list`, `tuple`, `set` | Instantiates `java.util.ArrayList` |
| `N` | `java.util.Map` | `dict` | Instantiates `java.util.HashMap` |
| `a` | Abstract class / Callback Adapter | `callable` or `dict` | Instantiates `com.stratum.adapters.Adapter_*` |
| `p` | Dynamic Interface Proxy | `callable` or `dict` | Instantiates `java.lang.reflect.Proxy` |
| `L` | Boxed types, arbitrary Java objects | `StratumObject`, primitive, or `None` | Auto-boxes primitives; passes raw pointer |

### 3. Return Type Identifiers (`ret_type_id`)
Determines which JNI `Call<Type>MethodA` / `Get<Type>Field` function is invoked:

| `ret_type_id` | Native Return Type | JNI Call Dispatched | Python Wrapper Conversion |
| :---: | :--- | :--- | :--- |
| `0` | `void` | `CallVoidMethodA` | Returns `None` |
| `1` | `jboolean` | `CallBooleanMethodA` | Returns `bool` |
| `2` | `jbyte` | `CallByteMethodA` | Returns `int` |
| `3` | `jchar` | `CallCharMethodA` | Returns `int` |
| `4` | `jshort` | `CallShortMethodA` | Returns `int` |
| `5` | `jint` | `CallIntMethodA` | Returns `int` |
| `6` | `jlong` | `CallLongMethodA` | Returns `int` |
| `7` | `jfloat` | `CallFloatMethodA` | Returns `float` |
| `8` | `jdouble` | `CallDoubleMethodA` | Returns `float` |
| `9` | `jstring` | `CallObjectMethodA` | Converts UTF-16 ➔ Python `str` |
| `10` | `jobject` | `CallObjectMethodA` | Returns global ref wrapped in `StratumObject` |
| `11` | Array types (`[B`, `[I`, `Object[]`) | `CallObjectMethodA` | Native list/bytes via `call_arr` |
| `12` | `java.util.List` / `Collection` | `CallObjectMethodA` | Recursive Python `list` via `call_list` |
| `13` | `java.util.Map` | `CallObjectMethodA` | Recursive Python `dict` via `call_map` |

---

## Configuration (`05_resolve/targets.json`)

If `targets.json` exists in `05_resolve/`, filtering and dependency closure resolution can be applied:

```json
{
  "enabled": false,
  "closure_mode": "parents_only",
  "targets": [
    { "fqn": "android.app.Activity" },
    { "fqn": "android.view.View" }
  ]
}
```

* `enabled`: When `true`, processes only the seed classes and their computed closure. When `false`, processes every class in the input directory.
* `closure_mode`:
  * `"parents_only"`: Includes the target class and all its superclasses up to `java.lang.Object`.
  * `"parents_and_interfaces"`: Includes superclasses and implemented interfaces.
  * `"full"`: Includes superclasses, interfaces, method parameter types, and return types.

---

## CLI Options

| Argument | Required | Description |
| :--- | :---: | :--- |
| `--input` | **Yes** | Path to input directory (`04_parse/output/` on Pass 1, `05_5_abstract/output/patched/` on Pass 2). |
| `--output` | **Yes** | Destination directory (`05_resolve/output/` on Pass 1, `05_resolve/output_patched/` on Pass 2). |
| `--closure-mode` | No | Overrides `closure_mode` in `targets.json` (`parents_only`, `parents_and_interfaces`, `full`). |

---

## Command Line Examples

### Run Pass 1 (Initial index for Stage 05.5):
```bash
python 05_resolve/main.py \
    --input 04_parse/output \
    --output 05_resolve/output
```

### Run Pass 2 (Final index post-adapter patching):
```bash
python 05_resolve/main.py \
    --input 05_5_abstract/output/patched \
    --output 05_resolve/output_patched
```
