# Stratum Pipeline — Stage 03: Run javap

## Quick Start

```bash
python 03_javap/main.py --input "01_extract/output/" --targets "02_inspect/targets.json" --setup "00_setup/output/setup_report.json" --output "03_javap/output/"
```

---

## What This Stage Does

Stage 03 . It reads your `targets.json` file from Stage 02 to determine which classes you want to process, then runs the JDK's `javap` tool against each corresponding `.class` file extracted in Stage 01. 

Crucially, it runs `javap` with the `-s` and `-p` flags. This extracts the **JNI (Java Native Interface) type signatures** for every method and field. These signatures are absolutely required by C++ later in the pipeline to correctly locate and invoke Android methods via reflection.

---

## Command-Line Arguments

| Argument | Required | Description |
|---|---|---|
| `--input` | ✅ Yes | Path to Stage 01's output directory (`01_extract/output/`), which acts as the `-classpath` for `javap`. |
| `--targets` | ✅ Yes | Path to `02_inspect/targets.json`. Tells the script whether to run in `full` mode (all classes) or `manual` mode (only enabled classes). |
| `--setup` | ✅ Yes | Path to `00_setup/output/setup_report.json`. Used to locate the absolute path to your JDK's `javap` executable. |
| `--output` | ✅ Yes | Directory where the generated `.javap` text files and summary report will be saved. |

---

## How It Works

1. **Reads Configuration:** It checks the `"mode"` key in `targets.json`.
   - If `"mode": "full"`, it ignores the targets list and runs `javap` on **every** `.class` file found in the input directory.
   - If `"mode": "manual"`, it only processes the classes in the targets list where `"enabled": true`.
2. **Executes `javap`:** For each class, it runs:
   ```bash
   javap -s -p -classpath <input_dir> <fully.qualified.ClassName>
   ```
   - `-s`: Prints internal JNI type signatures (e.g., `(ILjava/lang/String;)V`).
   - `-p`: Shows all classes and members, including private ones.
3. **Saves Output:** The terminal output of `javap` is captured and saved to a `.javap` text file mirroring the original package structure.

---

## Output: Generated `.javap` Files

The extracted JNI descriptors are written to `03_javap/output/` preserving the package folder structure.

```text
03_javap/output/
├── javap_summary.json
└── android/
    ├── app/
    │   └── Activity.javap
    ├── content/
    │   └── Context.javap
    ├── view/
    │   └── View.javap
    └── widget/
        └── Button.javap
```

If you open one of these `.javap` files, you will see the raw output that Stage 04 will parse:

```java
public class android.widget.Button extends android.widget.TextView {
  public android.widget.Button(android.content.Context);
    descriptor: (Landroid/content/Context;)V

  public void setText(java.lang.CharSequence);
    descriptor: (Ljava/lang/CharSequence;)V
}
```

---

## Output: `javap_summary.json`

Written to `03_javap/output/javap_summary.json` after the run finishes. It provides a quick look at what succeeded and what failed.

```json
{
  "mode": "manual",
  "total_processed": 18,
  "successful": 18,
  "failed": 0,
  "failed_classes": []
}
```

If you made a typo in `targets.json` (e.g., spelling a class name wrong), `javap` will fail to find it. The error will be printed to the terminal and recorded in the `failed_classes` array here.

---

## Exit Behaviour

| Result | Exit Code | Description |
|---|---|---|
| All processes completed | `0` | `.javap` files generated. Safe to continue to Stage 04 (even if some individual classes failed to process). |
| Missing Input Files | `1` | One of the required input files/directories was not found. Re-run previous stages. |
| No targets to process | `1` | `targets.json` is set to manual but no classes have `"enabled": true`. |
