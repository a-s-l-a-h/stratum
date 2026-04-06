# Stratum Pipeline — Stage 05.5: Abstract & Interface Adapters

## Quick Start

```bash
python 05_5_abstract/main.py --mode on --input "05_resolve/output/" --output "05_5_abstract/output/" --output-java "05_5_abstract/output_java/"
```

---

## What This Stage Does

Python (via Nanobind) cannot natively instantiate Java `interface` types or `abstract class` types. 

Stage 05.5 bridges this gap. It looks at the interfaces and abstract classes you requested and generates **concrete Java classes** (Adapters). These Java Adapters implement the required interfaces and forward the method calls through JNI down to your Python code via a `StratumInvocationHandler`. 

It also **patches** the JSON ASTs from Stage 05 so that Stage 06 knows to use these generated Adapters instead of the raw interfaces.

---

## Command-Line Arguments

| Argument | Required | Description |
|---|---|---|
| `--mode` | No | `"on"` generates Java files and patches JSON. `"off"` (default) acts as a passthrough, just copying the JSONs. |
| `--input` | ✅ Yes | Path to Stage 05's output directory (`05_resolve/output/`). |
| `--output` | ✅ Yes | Directory where the **patched** JSON ASTs will be written. |
| `--output-java`| ✅ Yes | Directory where the generated `.java` source files will be written. |

---

## Configuration: `05_5_abstract/targets.json`

Similar to previous stages, this stage generates a `targets.json` on its first run (inside the `05_5_abstract` folder). You list the specific Callbacks, Listeners, or Abstract classes you want to bridge to Python.

**Example `targets.json`:**
```json
{
  "enabled": true,
  "avoid": [
    "android.app.admin.NetworkEvent"
  ],
  "targets": [
    { "fqn": "android.hardware.camera2.CameraDevice$StateCallback" },
    { "fqn": "android.hardware.camera2.CameraCaptureSession$StateCallback" },
    { "fqn": "android.view.TextureView$SurfaceTextureListener" }
  ]
}
```
*Note: Inner classes must use the `$` notation, just like in Stage 05.*

---

## Outputs

### 1. Java Code (`output_java/`)
It generates `.java` files for every target. For example, `Adapter_android_hardware_camera2_CameraDevice_StateCallback.java`. 

**Important:** These generated `.java` files must be manually copied into your Android app's source tree (e.g., `app/src/main/java/com/stratum/adapters/`) so they are compiled into your final APK.

### 2. Patched JSONs (`output/patched/`)
It creates a mirror of the Stage 05 JSON files, but modifies method parameters. If a method expects a `StateCallback`, the JSON is patched to accept your new `Adapter_...StateCallback` instead.

---

## Exit Behaviour & Next Steps

| Result | Exit Code | Description |
|---|---|---|
| Success | `0` | Java adapters generated and JSONs patched. Proceed to Stage 06. |
| Name Collision | `1` | A generated adapter name conflicts with an existing class. |

**Next Step:** Proceed to Stage 06 (C++ Generation), making sure to use the **patched** output from this stage as your input:
```bash
python 06_cpp_emit/main.py --input "05_5_abstract/output/patched/" ...
```