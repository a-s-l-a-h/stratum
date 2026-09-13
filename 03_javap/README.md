
# Stratum Pipeline — Stage 03: Extract JNI Signatures via javap

## Overview & Architectural Context

Stage 03 executes the Java Class File Disassembler (`javap`) against every class selected in **Stage 02**. This step extracts internal JNI type signatures for all constructors, methods, and fields.

In the Stratum architecture:
- Android runtime reflection in C++ (`GetMethodID`, `GetStaticMethodID`, `GetFieldID`, `NewObjectA`) requires exact JVM bytecode signatures (e.g., `(ILjava/lang/String;)V`).
- Stage 03 runs `javap` with:
  - `-s`: Exposes internal JNI type descriptors.
  - `-p`: Exposes all members (public, protected, package-private, and private).
- The raw output files (`.javap`) preserve parameter types, exception throws, and constant field values, providing the complete raw syntax necessary for Stage 04's AST parser.

---

## Quick Start / CLI Demo

```bash
python 03_javap/main.py \
    --input "01_extract/output/" \
    --targets "02_inspect/targets.json" \
    --setup "00_setup/output/setup_report.json" \
    --output "03_javap/output/"
```

---

## Command-Line Arguments

| Flag | Type | Required | Description |
|---|---|:---:|---|
| `--input` | `Path` | **Yes** | Path to Stage 01 output (`01_extract/output/`). Serves as the `-classpath` for `javap`. |
| `--targets` | `Path` | **Yes** | Path to `02_inspect/targets.json`. Determines execution mode (`full` vs `manual`). |
| `--setup` | `Path` | **Yes** | Path to `00_setup/output/setup_report.json`. Locates the verified JDK `javap` binary. |
| `--output` | `Path` | **Yes** | Destination directory where individual `.javap` output files and `javap_summary.json` are written. |

---

## Execution Workflow

1. **Environment & Tool Resolution**: Reads `setup_report.json` to obtain the verified JDK path for `javap`. If not configured, defaults to system `javap`.
2. **Target Filtering**:
   - If `targets.json` specifies `"mode": "full"`, scans `--input` and builds a complete list of all `.class` files.
   - If `targets.json` specifies `"mode": "manual"`, filters the `targets` array for entries where `"enabled": true`.
3. **Disassembly Subprocess Execution**:
   - For each target class, runs:
     ```bash
     javap -s -p -classpath <input_dir> <fqn>
     ```
   - Enforces a 30-second timeout per class to prevent hanging on corrupt bytecode.
4. **File Mirroring**:
   - Outputs each disassembled class result to `--output/<package_path>/<ClassName>.javap`.
5. **Report Generation**:
   - Compiles metrics into `javap_summary.json` detailing successful extractions and recording any failed FQNs with stderr logs.

---

## Output Structure

```text
03_javap/output/
├── javap_summary.json
└── android/
    ├── app/
    │   ├── Activity.javap
    │   └── Service.javap
    ├── view/
    │   ├── View.javap
    │   └── ViewGroup.javap
    └── widget/
        ├── Button.javap
        └── TextView.javap
```

### Sample `.javap` Disassembly Output

```java
Compiled from "Button.java"
public class android.widget.Button extends android.widget.TextView {
  public android.widget.Button(android.content.Context);
    descriptor: (Landroid/content/Context;)V

  public android.widget.Button(android.content.Context, android.util.AttributeSet);
    descriptor: (Landroid/content/Context;Landroid/util/AttributeSet;)V

  public java.lang.CharSequence getAccessibilityClassName();
    descriptor: ()Ljava/lang/CharSequence;

  public android.widget.Button(android.content.Context, android.util.AttributeSet, int);
    descriptor: (Landroid/content/Context;Landroid/util/AttributeSet;I)V
}
```

---

## Summary Report Schema: `javap_summary.json`

```json
{
  "mode": "manual",
  "total_processed": 18,
  "successful": 18,
  "failed": 0,
  "failed_classes": []
}
```

### Failed Class Format (if errors occur)

```json
{
  "mode": "manual",
  "total_processed": 1,
  "successful": 0,
  "failed": 1,
  "failed_classes": [
    {
      "fqn": "android.widget.NonExistentView",
      "error": "Error: class not found: android.widget.NonExistentView"
    }
  ]
}
```

---

## Troubleshooting & Exit Codes

| Exit Code | Reason | Resolution |
|:---:|---|---|
| `0` | All target classes disassembled successfully (or non-fatal class errors logged). | Inspect summary and proceed to **Stage 04 (`04_parse`)**. |
| `1` | Missing inputs (`--input`, `--targets`, or `--setup` does not exist). | Ensure Stages 00, 01, and 02 have been executed. |
| `1` | No classes to process. | Ensure `targets.json` has at least one class with `"enabled": true` or set `"mode": "full"`. |
