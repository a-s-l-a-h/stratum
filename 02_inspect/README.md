
# Stratum Pipeline — Stage 02: Inspect Classes & Target Configuration

## Overview & Architectural Context

Stage 02 acts as the boundary between automated extraction and build configuration. It traverses the `.class` filesystem tree produced by **Stage 01**, catalogs all available Fully Qualified Names (FQNs), and generates two browsable reference lists (a flat alphabetical directory and a package-tree breakdown).

Most importantly, Stage 02 initializes or preserves the primary build configuration file:
```
02_inspect/targets.json
```
This configuration dictates whether subsequent pipeline stages operate in **full API mode** (bridging the entire Android SDK) or **manual mode** (bridging a prioritized subset of classes). Because Stratum's v9/v10 C++ engine uses a unified metadata table rather than per-class C++ templates, `"mode": "full"` can be used without risk of compiler Out-Of-Memory (OOM) errors.

---

## Quick Start / CLI Demo

```bash
python 02_inspect/main.py \
    --input "01_extract/output/" \
    --output "02_inspect/output/"
```

---

## Command-Line Arguments

| Flag | Type | Required | Description |
|---|---|:---:|---|
| `--input` | `Path` | **Yes** | Root directory containing extracted classes and `extract_summary.json` from Stage 01. |
| `--output` | `Path` | **Yes** | Output directory where `available_classes.txt` and `available_by_package.txt` are written. |

---

## Execution Workflow

1. **Input Scan**: Recursively searches `--input` for all files ending in `.class`.
2. **FQN Normalization**:
   - Converts filesystem paths to dot-separated class names (e.g., `android/widget/Button.class` $\rightarrow$ `android.widget.Button`).
   - Splits FQNs into packages and simple names, indexing them into an alphabetized map.
3. **Artifact Generation**:
   - Generates `available_classes.txt` (single-column list of all available FQNs).
   - Generates `available_by_package.txt` (package-grouped layout with per-package class totals).
4. **Configuration Handling (`targets.json`)**:
   - Checks for `02_inspect/targets.json` directly in the stage directory (outside `output/` so it is preserved across clean builds).
   - **If missing**: Generates a default configuration containing starter targets.
   - **If present**: Leaves the existing file untouched (`[SAFE]` mode), preserving all custom developer edits.

---

## Output Structure

```text
02_inspect/
├── targets.json                       <-- Editable configuration (Version controlled)
└── output/
    ├── available_classes.txt          <-- Alphabetical flat list of all FQNs
    └── available_by_package.txt       <-- Package-grouped index with counts
```



---

## Target Configuration Reference (`02_inspect/targets.json`)

### Unified Schema Example

```json
{
  "android_version": "35",
  "mode": "full",
  "targets": [
    {
      "fqn": "android.app.Activity",
      "enabled": true,
      "priority": 1,
      "notes": "core lifecycle"
    },
    {
      "fqn": "android.view.View",
      "enabled": true,
      "priority": 1,
      "notes": "base of everything"
    },
    {
      "fqn": "android.widget.Button",
      "enabled": true,
      "priority": 1,
      "notes": "core UI"
    }
  ]
}
```

### Operational Modes (`mode`)

| Mode | Target Scope | Description |
|---|---|---|
| `"full"` | **All Classes** | Instructs Stage 03 to process every single `.class` file found in Stage 01. The `targets` list is ignored. |
| `"manual"` | **Selective** | Only processes items inside `targets` where `"enabled": true`. Useful for targeted testing or constrained builds. |

---

## Troubleshooting & Exit Codes

| Exit Code | Reason | Resolution |
|:---:|---|---|
| `0` | Success. Catalog lists written and `targets.json` verified. | Review `targets.json` and proceed to **Stage 03 (`03_javap`)**. |
| `1` | Input directory missing or contains 0 `.class` files. | Run Stage 01 (`01_extract`) first or run with `--force`. |