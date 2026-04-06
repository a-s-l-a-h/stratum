
# Stratum Pipeline — Stage 09: Build Python Wheel (`.whl`)

## Quick Start

**Building for Real Devices (ARM64)**
```bash
python 09_wheel/main.py --so "07_build/output/_stratum.so" --pyi "08_pyi_emit/output/" --output "09_wheel/output/" --version 0.1.0 --abi arm64-v8a --chaquopy 3.10.13-0
```

**Building for Emulators (x86_64)**
*(Make sure you ran Stage 07 for x86_64 first!)*
```bash
python 09_wheel/main.py --so "07_build/output_x86_64/_stratum.so" --pyi "08_pyi_emit/output/" --output "09_wheel/output_x86_64/" --version 0.1.0 --abi x86_64 --chaquopy 3.10.13-0
```

---

## What This Stage Does

Stage 09 is the packaging step. It takes the compiled C++ library (`_stratum.so`) from Stage 07 and the Python type stubs (`.pyi`) from Stage 08, and bundles them into a standard **Python Wheel (`.whl`)**. 

This wheel can be directly installed via pip or specified in your Android Studio `build.gradle` file for Chaquopy to consume.

It also automatically generates the master `__init__.py` file for the module, providing crucial runtime helper functions (`cast_to`, `getActivity`) and **automatic Android lifecycle registration**.

---

## Command-Line Arguments

| Argument | Required | Description |
|---|---|---|
| `--so` | ✅ Yes | Path to the compiled C++ library from Stage 07. |
| `--pyi` | ✅ Yes | Path to the `.pyi` type stubs from Stage 08. |
| `--output` | ✅ Yes | Directory where the `.whl` file will be saved. |
| `--version` | No | Version number for your wheel (e.g., `0.1.0`). |
| `--abi` | No | **Must match the ABI used in Stage 07.** Default: `arm64-v8a`. |
| `--chaquopy` | No | Target Chaquopy Python version. Used to tag the wheel correctly (e.g., `cp310`). |
| `--min-api` | No | Minimum Android API level. Used for wheel tagging. Default: `21`. |

---

## The ABI Multi-Architecture Workflow

If your app needs to run on both physical phones (`arm64-v8a`) and Android Studio Emulators (`x86_64`), you must generate a separate wheel for each architecture.

1. **Run Stage 07 twice** (saving to different output folders).
2. **Run Stage 09 twice** (pointing to the respective Stage 07 output folders, and saving to different Stage 09 output folders).

When you put both `.whl` files into your Android app's directory (e.g., `app/libs/`), Chaquopy is smart enough to pick the correct wheel for the device currently running the app!

---

## Key Runtime Features Injected into the Wheel

The master `__init__.py` injected into the wheel provides these core functions to your Python scripts:

*   **`stratum.getActivity()`**: Returns the Android `Activity` context, required to instantiate UI elements.
*   **`stratum.setContentView(activity, view)`**: Mounts your Python-created View to the Android screen.
*   **`stratum.cast_to(obj, cls_name)`**: Safely casts a generic `StratumObject` into a specific type (e.g., casting a system service into `CameraManager`).
*   **Auto-Lifecycle Binding**: When your Python `main.py` is imported, Stratum automatically scans it for functions named `onCreate`, `onResume`, `onPause`, etc., and binds them to the Android Activity lifecycle.

---

## Output

After running, the `--output` folder will contain the wheel file:
`stratum-0.1.0-cp310-cp310-android_21_arm64_v8a.whl`

### Wheel Anatomy
If you unzip the `.whl`, you will see:
*   `stratum/_stratum.so` (The native bridge)
*   `stratum/__init__.py` (Core helpers and dynamic loaders)
*   `stratum/android/.../*.pyi` (Your type hints)
*   `stratum-0.1.0.dist-info/` (Pip metadata)

## Next Steps

**Congratulations! You have completed the Stratum Pipeline.** 
You can now copy this `.whl` file into your Android Studio project. Now look to runtime folder .