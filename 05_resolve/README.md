# Stratum Pipeline — Stage 05: Resolve

## Quick Start

```bash
python 05_resolve/main.py --input "04_parse/output/" --output "05_resolve/output/"
```

---

## What This Stage Does

Stage 05 is the data-enrichment phase. It reads the isolated ASTs generated in Stage 04 and connects them into a complete class hierarchy. 

It calculates the Method Resolution Order (MRO), resolves inherited and overridden methods from parent classes, determines C++ `#include` requirements, and pre-computes the exact JNI mangled names and C++ types needed by Stage 06.

Crucially, Stage 05 acts as the **filter**. It reads `05_resolve/targets.json` to compute a "closure" — taking your seed classes and automatically pulling in any parent classes required to make the C++ compile safely.

---

## Command-Line Arguments

| Argument | Required | Description |
|---|---|---|
| `--input` | ✅ Yes | Path to Stage 04's output directory (`04_parse/output/`). |
| `--output` | ✅ Yes | Directory where the fully-enriched `.json` files and `resolve_summary.json` will be written. |
| `--closure-mode` | No | Overrides the `closure_mode` defined in `targets.json`. |
| `--list-modes` | No | Prints a detailed explanation of closure modes and exits. |

---

## Configuration: `05_resolve/targets.json`

On the first run, Stage 05 creates `05_resolve/targets.json`. This controls which classes are included in the final C++ generation.

### The `closure_mode` Setting

Because C++ structs inherit from one another (e.g., `Button` inherits from `TextView`, which inherits from `View`), you cannot generate C++ for `Button` without also generating C++ for `TextView` and `View`. The `closure_mode` handles this automatically.

*   `"parents_only"` **(Recommended)**: Pulls in only parent/ancestor classes. Safe, fast, and generates small binaries (~30-50 classes). Use this for simple UI apps.
*   `"parents_and_interfaces"`: Pulls in parents AND directly-implemented interfaces. Use this if your app relies on Java Callbacks/Listeners (like Camera or Surface processing).
*   `"full"`: **(Warning)** Pulls in everything, including method return types. Can explode 8 seeds into 800+ classes and cause runtime crashes or other crashes.

---

### Example 1: Basic App (Counter)
If you are building a simple app (like a counter), use `"parents_only"`. 

```json
{
  "enabled": true,
  "closure_mode": "parents_only",
  "targets": [
    { "fqn": "android.app.Activity" },
    { "fqn": "android.view.ContextThemeWrapper" },
    { "fqn": "android.content.ContextWrapper" },
    { "fqn": "android.content.Context" },
    { "fqn": "android.view.View" },
    { "fqn": "android.view.ViewGroup" },
    { "fqn": "android.widget.TextView" },
    { "fqn": "android.widget.Button" },
    { "fqn": "android.widget.LinearLayout" }
  ]
}
```

### Example 2: Advanced App (OpenCV / Camera)
If you are using Hardware Cameras, Surfaces, and Listeners, you need `"parents_and_interfaces"` so the C++ generator understands the listener interfaces. Note the use of `$` for inner classes (e.g., `View$OnClickListener`).

```json
{
  "enabled": true,
  "closure_mode": "parents_and_interfaces",
  "targets": [
    { "fqn": "android.app.Activity" },
    { "fqn": "android.content.Context" },
    { "fqn": "android.view.View" },
    { "fqn": "android.view.View$OnClickListener" },
    { "fqn": "android.view.ViewGroup" },
    { "fqn": "android.widget.FrameLayout" },
    { "fqn": "android.widget.LinearLayout" },
    { "fqn": "android.widget.Button" },
    { "fqn": "android.graphics.Bitmap" },
    { "fqn": "android.view.TextureView" },
    { "fqn": "android.view.TextureView$SurfaceTextureListener" },
    { "fqn": "android.hardware.camera2.CameraManager" },
    { "fqn": "android.hardware.camera2.CameraDevice" },
    { "fqn": "android.hardware.camera2.CameraDevice$StateCallback" },
    { "fqn": "android.hardware.camera2.CameraCaptureSession" },
    { "fqn": "android.hardware.camera2.CameraCaptureSession$StateCallback" },
    { "fqn": "android.hardware.camera2.CaptureRequest" }
  ]
}
```

---

## Exit Behaviour & Next Steps

If validation fails (e.g., missing JNI signatures), the script will prompt you in the terminal to either Stop `[S]`, Continue `[C]`, or Continue All `[A]`.

**Where do I go next?**
*   **Path A (Simple Apps):** If your app does not require implementing Java interfaces or abstract classes in Python (like Example 1 above), **skip Stage 05.5** and go directly to Stage 06 using `--input "05_resolve/output/"`.
*   **Path B (Advanced Apps):** If you need to pass Python functions into Android as Callbacks/Listeners (like Example 2 above), **proceed to Stage 05.5**.

***
***
