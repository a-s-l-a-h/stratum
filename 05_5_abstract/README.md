
# Stratum Pipeline — Stage 05.5: Abstract & Interface Adapter Generator

## Overview

Stage 05.5 bridges one of the most critical gaps in Android-JNI interaction: **subclassing abstract classes and handling multi-method callback interfaces from Python.**

While simple single-method interfaces can be proxied via `java.lang.reflect.Proxy`, Android APIs heavily rely on abstract base classes (e.g., `CameraDevice.StateCallback`, `CameraCaptureSession.CaptureCallback`, `WebViewClient`) or multi-method interfaces (`TextWatcher`, `SurfaceTextureListener`). `java.lang.reflect.Proxy` throws `IllegalArgumentException` if asked to proxy an abstract class.

Stage 05.5 detects these targets, emits compilable `.java` adapter source files extending the base classes or implementing the interfaces, routes callback invocations to C++ via `StratumInvocationHandler.nativeDispatch()`, and patches the class JSON metadata so downstream stages use native adapters.

---

## How It Works

```
        Class Metadata from 05_resolve/output/
                         │
                         ▼
        ┌──────────────────────────────────┐
        │ Class Analysis & Target Selection│
        │ - Pattern matching (*Callback)   │
        │ - Constructor accessibility      │
        │ - Reserved/Structural filtering  │
        └──────────────────────────────────┘
                         │
         ┌───────────────┴───────────────┐
         ▼                               ▼
┌──────────────────┐           ┌──────────────────┐
│ Emit Java Source │           │ Patch JSON Tree  │
│  com/stratum/    │           │ Sets 'conversion'│
│  adapters/*.java │           │ to 'abstract_    │
└──────────────────┘           │ adapter'         │
                               └──────────────────┘
                                         │
                                         ▼
                             05_5_abstract/output/patched/
```

### 1. Target Detection Heuristics
A class is selected for adapter generation if:
1. It is explicitly listed in `05_5_abstract/targets.json` (`seeds`).
2. **OR** its name matches common callback naming patterns (`Callback`, `Listener`, `Observer`, `Client`, `Filter`), it is abstract (or a known stubbed callback with default empty bodies like `WebViewClient`), and it is **not** a reserved structural class.
3. **Safety Guard**: It must have at least one accessible (`public` or `protected`) constructor.
4. **Safety Guard**: It must not contain package-private abstract methods (which cannot be overridden outside their original package).

### 2. Generated Adapter Architecture
Every adapter file emitted into `com.stratum.adapters` follows this structure:
* Holds an immutable string identifier: `private final String key_;`.
* Implements matching constructors forwarding default or supplied arguments via `super(...)`.
* Overrides all target methods to invoke:
  ```java
  Object __r = StratumInvocationHandler.nativeDispatch(key_, "methodName", new Object[]{ ... });
  ```
* Safely unboxes return values (e.g. `boolean`, `int`, `char`) with defensive casting to ensure that returning a Python value (like `True` or `1` from `onLongClick` or `onTouch`) correctly influences Android's event dispatch.

---

## Configuration (`05_5_abstract/targets.json`)

```json
{
  "seeds_only": false,
  "seeds": [
    "android.hardware.camera2.CameraDevice$StateCallback",
    "android.hardware.camera2.CameraCaptureSession$StateCallback",
    "android.hardware.camera2.CameraCaptureSession$CaptureCallback",
    "android.webkit.WebViewClient",
    "android.webkit.WebChromeClient"
  ],
  "avoid": [
    "android.os.Handler",
    "android.content.Context",
    "android.net.Uri"
  ],
  "reserved_structural": [
    "android.view.View",
    "android.view.ViewGroup",
    "android.app.Activity",
    "android.app.Service",
    "android.content.BroadcastReceiver"
  ]
}
```

* `seeds`: High-priority target classes that are guaranteed to have adapters generated.
* `seeds_only`: When `true`, suppresses the full registry scan and only generates adapters for the explicit `seeds`.
* `avoid`: Classes explicitly blacklisted from adapter generation.
* `reserved_structural`: Classes that must never receive generic auto-generated adapters because they are implemented as specialized runtime core components in `runtime/java/`.

---

## CLI Options

| Argument | Required | Default | Description |
| :--- | :---: | :---: | :--- |
| `--input` | **Yes** | — | Input directory from Stage 05 Pass 1 (`05_resolve/output/`). |
| `--output` | **Yes** | — | Root output directory (`05_5_abstract/output/`). |
| `--output-java` | No | `output/java/...` | Override directory where `.java` adapter files will be written. |
| `--mode` | No | `off` | `on` executes adapter generation and JSON patching; `off` executes a direct passthrough copy to `output/patched/`. |

---

## Command Line Example

```bash
python 05_5_abstract/main.py \
    --input 05_resolve/output \
    --output 05_5_abstract/output \
    --mode on
```

Output directory structure:
```
05_5_abstract/output/
├── java/
│   └── com/
│       └── stratum/
│           └── adapters/
│               ├── Adapter_android_hardware_camera2_CameraDevice_StateCallback.java
│               ├── Adapter_android_webkit_WebViewClient.java
│               └── ...
├── patched/
│   ├── android/
│   │   └── hardware/camera2/CameraDevice.json
│   └── ...
└── manifest.json
```
