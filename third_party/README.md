
# Stratum Pipeline — Third Party Dependencies

## What is this folder?

The `third_party/` directory serves as the local cache and workspace for all external dependencies required to analyze Java classes and compile the C++ bridge. 

**You generally do not need to manually manage or modify the contents of this folder.** The Stratum pipeline scripts (specifically Stage 00 and Stage 07) will automatically populate it as needed.

---

## What you can expect to see here

As you run the pipeline, the following files and folders will appear in this directory:

### `nanobind/`
Automatically cloned from GitHub during **Stage 00**. This contains the nanobind C++ library (and its submodules, like `robin_map`) which Stratum uses to bind C++ classes to Python. Stage 07 points to this folder when compiling the final `_stratum.so` library.

### `chaquopy/`
Automatically created and populated during **Stage 00**. This folder acts as a cache for the Chaquopy Python target ZIP files (e.g., `target-3.10.13-0-arm64-v8a.zip`) downloaded from Maven Central. Stage 07 extracts these to get the `Python.h` headers and `libpython.so` files needed for cross-compilation without having to re-download them every time.

### `ndk25/` *(User-placed)*
If you followed the "Quick Start" recommendations in Stage 00, this is the standard location where you would extract the Android NDK (`android-ndk-r25c`). It contains the `clang++` compiler used in Stage 07.

### `android-<api>.jar` *(User-placed)*
If you are using a standalone Android JAR instead of a full Android SDK installation, this is the recommended place to store it (e.g., `android-35.jar`). Stage 01 will read this file to extract the Android API `.class` files.

---

## Version Control Note

Because this folder contains large binaries, cached ZIPs, and cloned Git repositories, the contents of this directory are typically ignored by `.gitignore`. Only this `README.md` is tracked in version control.