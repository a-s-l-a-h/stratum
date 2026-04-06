# Stratum Pipeline — Stage 01: Extract Android JAR

## Quick Start

```bash
python 01_extract/main.py --setup "00_setup/output/setup_report.json" --output "01_extract/output/"
```

---

## What This Stage Does

Stage 01 opens the `android.jar` located by Stage 00 and extracts every `.class` file into a flat mirror on disk, preserving the original package folder structure (e.g. `android/widget/Button.class`). This unpacked mirror is what later stages walk to discover Android classes and their signatures.

On completion it writes an `extract_summary.json` to the output directory listing every package and how many classes it contains.

---

## Command-Line Arguments

| Argument | Required | Description |
|---|---|---|
| `--setup` | ✅ Yes | Path to `setup_report.json` written by Stage 00. All tool paths and API version are read from here — no need to pass them again. |
| `--output` | ✅ Yes | Directory where extracted `.class` files and `extract_summary.json` are written. |
| `--force` | No | Re-extract even if extraction was already completed. Without this flag, Stage 01 exits early on a cache hit and prints the existing output location. |

---

## Caching Behaviour

Stage 01 checks for an existing `extract_summary.json` in the output directory before doing any work.

- **Cache hit** — prints a message and exits immediately. Safe to re-run at any time without wasting time.
- **Cache miss** — performs full extraction.
- **`--force`** — bypasses the cache check and always re-extracts. Use this if you switch to a different `android.jar` or `--api-version`.

---

## Output: `extract_summary.json`

Written to `01_extract/output/extract_summary.json` after a successful run.

```json
{
  "android_version": "35",
  "total_classes": 14823,
  "extraction_time_seconds": 3.42,
  "packages": {
    "android.view": 412,
    "android.content": 387,
    "android.widget": 341,
    "android.graphics": 298,
    "android.os": 276
  },
  "timestamp": "2026-03-26T22:10:05.123456"
}
```

### Field Reference

| Field | Description |
|---|---|
| `android_version` | API level of the jar that was extracted, taken from `setup_report.json`. |
| `total_classes` | Total number of `.class` files extracted from the jar. |
| `extraction_time_seconds` | Wall-clock time the extraction took. |
| `packages` | Map of every package found to its class count, sorted largest first. |
| `timestamp` | ISO 8601 timestamp of when the extraction completed. |

---

## Output: Extracted Class Mirror

Extracted files are written to `01_extract/output/android/` preserving the original JAR folder structure:

```
01_extract/output/
├── extract_summary.json
└── android/
    ├── app/
    │   └── Activity.class
    ├── content/
    │   └── Context.class
    ├── view/
    │   └── View.class
    └── widget/
        └── Button.class
```

This directory is read by Stages 02–04 for class reflection via `javap`.

---

## Exit Behaviour

| Result | Exit Code | Description |
|---|---|---|
| Cache hit (no `--force`) | `0` | Already extracted. Nothing written. Safe to continue to Stage 02. |
| Extraction success | `0` | All classes extracted. `extract_summary.json` written. |
| `setup_report.json` not found | `1` | Run Stage 00 first. |
| `android.jar` not found | `1` | The `jar_path` in `setup_report.json` points to a missing file. Re-run Stage 00. |