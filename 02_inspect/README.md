# Stratum Pipeline — Stage 02: Inspect Classes

## Quick Start

```bash
python 02_inspect/main.py --input "01_extract/output/" --output "02_inspect/output/"
```

---

## What This Stage Does

Stage 02 scans all `.class` files extracted by Stage 01 and builds two human-readable indexes — a flat alphabetical list and a package-grouped tree — so you can see every class available in your `android.jar`. It then creates `02_inspect/targets.json` on first run, which is the file you edit to control which classes get bridged to Python in later stages.

---

## Command-Line Arguments

| Argument | Required | Description |
|---|---|---|
| `--input` | ✅ Yes | Path to Stage 01's output directory (`01_extract/output/`). Must contain `extract_summary.json` and the extracted `.class` files. |
| `--output` | ✅ Yes | Directory where `available_classes.txt` and `available_by_package.txt` are written. |

---

## Output Files

### `02_inspect/output/available_classes.txt`

Flat alphabetical list of every class found. One fully qualified name per line. Use this to look up exact class names when adding entries to `targets.json`.

```
android.app.Activity
android.app.AlertDialog
android.content.Context
android.content.Intent
android.graphics.Bitmap
android.os.Bundle
android.view.View
android.widget.Button
android.widget.TextView
...
```

### `02_inspect/output/available_by_package.txt`

Same classes grouped by package with per-package counts. Easier to browse by area of the API.

```
android.app (42)
  Activity
  AlertDialog
  Application
  ...

android.widget (198)
  Button
  CheckBox
  EditText
  ...
```

---

## `02_inspect/targets.json` — Developer Reference

### What It Is

The **only file in the entire pipeline you edit manually.** Controls which Android classes get bridged to Python. Lives in `02_inspect/` directly — never inside `output/` — so it is never auto-overwritten between runs.

### First Run Behaviour

On first run Stage 02 creates `targets.json` with 20 core starter classes. On every subsequent run it detects the file already exists and skips creation entirely, preserving all your edits.

---

### Recommended Mode for v0.2: `full`

Set `"mode": "full"` at the top of `targets.json`. In full mode the pipeline processes **all 7000+ classes** in the jar automatically — no need to list them individually. This gives you a complete Android API bridge and is the right choice for v0.2 onwards.

```json
"mode": "full"
```

> **Note:** Full mode generates a large amount of C++ and takes significantly longer to build than manual mode. Make sure your machine has enough disk space and time before starting Stage 06 (build).

---

### The Two Controls

**`mode`** — top-level switch that applies to the whole file:

```json
"mode": "manual"   →  only entries with "enabled": true are processed
"mode": "full"     →  all 7000+ classes are processed (targets list below is ignored)
```

**`enabled`** — per-class switch used only when `mode` is `"manual"`:

```json
"enabled": true    →  this class gets bridged
"enabled": false   →  skipped (kept in file for future reference)
```

---

### Example `targets.json`

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
      "fqn": "android.view.ViewGroup",
      "enabled": true,
      "priority": 1,
      "notes": "layout base"
    },
    {
      "fqn": "android.widget.Button",
      "enabled": true,
      "priority": 1,
      "notes": "core UI"
    },
    {
      "fqn": "android.widget.TextView",
      "enabled": true,
      "priority": 1,
      "notes": "text display"
    },
    {
      "fqn": "android.widget.EditText",
      "enabled": true,
      "priority": 1,
      "notes": "text input"
    },
    {
      "fqn": "android.widget.ImageView",
      "enabled": true,
      "priority": 1,
      "notes": "image display"
    },
    {
      "fqn": "android.widget.LinearLayout",
      "enabled": true,
      "priority": 1,
      "notes": "layout"
    },
    {
      "fqn": "android.widget.FrameLayout",
      "enabled": true,
      "priority": 1,
      "notes": "layout"
    },
    {
      "fqn": "android.widget.RecyclerView",
      "enabled": false,
      "priority": 2,
      "notes": "list display"
    },
    {
      "fqn": "android.content.Context",
      "enabled": true,
      "priority": 1,
      "notes": "Android context"
    },
    {
      "fqn": "android.content.Intent",
      "enabled": true,
      "priority": 1,
      "notes": "navigation"
    },
    {
      "fqn": "android.os.Bundle",
      "enabled": true,
      "priority": 1,
      "notes": "data passing"
    },
    {
      "fqn": "android.os.Handler",
      "enabled": true,
      "priority": 2,
      "notes": "thread posting"
    },
    {
      "fqn": "android.hardware.SensorManager",
      "enabled": true,
      "priority": 2,
      "notes": "sensors"
    },
    {
      "fqn": "android.hardware.camera2.CameraManager",
      "enabled": true,
      "priority": 1,
      "notes": "camera"
    },
    {
      "fqn": "android.hardware.camera2.CameraDevice",
      "enabled": true,
      "priority": 1,
      "notes": "camera"
    },
    {
      "fqn": "android.media.AudioRecord",
      "enabled": true,
      "priority": 2,
      "notes": "audio"
    },
    {
      "fqn": "android.graphics.Bitmap",
      "enabled": true,
      "priority": 1,
      "notes": "image data"
    },
    {
      "fqn": "android.location.LocationManager",
      "enabled": true,
      "priority": 2,
      "notes": "GPS"
    },
    {
      "fqn": "android.widget.DatePicker",
      "enabled": false,
      "priority": 3,
      "notes": "skip for now"
    }
  ]
}
```

---

### `priority` and `notes` Fields

These are **for you only** — no script reads them. Use however you like:

```
priority 1  →  must have
priority 2  →  nice to have
priority 3  →  skip for now
notes       →  your own reminder
```

---

### Adding a New Class (manual mode)

1. Find the exact class name in `02_inspect/output/available_classes.txt`
2. Add an entry to the `targets` array in `targets.json`:

```json
{
  "fqn": "android.widget.CheckBox",
  "enabled": true,
  "priority": 1,
  "notes": "your note here"
}
```

3. Rerun from Stage 03 onward — Stages 01 and 02 are untouched:

```bash
python 03_javap/main.py ...
python 04_parse/main.py ...
python 05_cpp_emit/main.py ...
python 06_build/main.py ...
python 07_pyi_emit/main.py ...
```

---

### Important — `android_version` Must Match

The `android_version` field in `targets.json` must match the API level that was extracted in Stage 01. Verify by checking:

```
01_extract/output/extract_summary.json  →  "android_version"
```

If they don't match, update `targets.json` to match the extract summary before proceeding.

---

### Quick Reference

```
Edit this file   →  add / enable / disable classes
Rerun Stage 03+  →  changes take effect
Never delete     →  version control this file
Never move to    →  output/ folder
```

---

## Exit Behaviour

| Result | Exit Code | Description |
|---|---|---|
| Success | `0` | Class indexes written, `targets.json` created or preserved. Proceed to Stage 03. |
| Input directory not found | `1` | Stage 01 has not been run or `--input` path is wrong. |
| No `.class` files found | `1` | Stage 01 output is empty or corrupt. Re-run Stage 01 with `--force`. |