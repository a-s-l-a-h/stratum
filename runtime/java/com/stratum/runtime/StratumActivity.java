package com.stratum.runtime;

import android.os.Bundle;
import android.util.Log;
import androidx.appcompat.app.AppCompatActivity;
import com.chaquo.python.PyObject;
import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

public class StratumActivity extends AppCompatActivity {

    private static final String TAG = "StratumActivity";

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        // ── Step 1: Start the Python runtime ──────────────────────────────
        if (!Python.isStarted()) {
            Python.start(new AndroidPlatform(this));
        }

        // ── Step 2: Locate _stratum.so via Python's own import machinery ──
        //
        // WHY THIS APPROACH:
        //   Chaquopy extracts only the correct ABI's .so into the app's
        //   private data dir at install time. Python's importlib already
        //   knows exactly which file was extracted (it handles arm64-v8a,
        //   armeabi-v7a, x86, x86_64 transparently). We ask Python where
        //   _stratum lives — this is always the right file for the current
        //   device ABI, with zero manual ABI detection needed.
        //
        // WHAT WE DO:
        //   find_spec("_stratum") returns the spec without executing the
        //   module, so there are no double-import side effects. spec.origin
        //   is the absolute path to the .so on disk.
        //
        // FALLBACK:
        //   If find_spec fails (e.g. _stratum is inside a package rather
        //   than a top-level extension module), we fall back to importing
        //   the stratum package and reading _stratum.__file__ directly.

        String soPath = findStratumSoPath();
        if (soPath == null) {
            throw new RuntimeException(
                    "[Stratum] Could not locate _stratum.so via Python import machinery. " +
                            "Ensure _stratum is built and packaged as a Chaquopy native module.");
        }
        Log.i(TAG, "Located _stratum.so → " + soPath);

        // ── Step 3: Load the .so into the JVM ─────────────────────────────
        //
        // System.load() is idempotent — if already loaded it's a no-op.
        // We do NOT silently ignore UnsatisfiedLinkError: if the .so is
        // found but fails to load (wrong ABI slipping through, missing
        // transitive dependency, etc.) we want a clear crash here, not a
        // mysterious NullPointerException inside C++ later.
        try {
            System.load(soPath);
            Log.i(TAG, "_stratum.so loaded into JVM successfully.");
        } catch (UnsatisfiedLinkError e) {
            // Only ignore "already loaded in another classloader" — that is
            // the one genuinely benign case (hot-reload / split APK edge cases).
            String msg = e.getMessage() != null ? e.getMessage() : "";
            if (msg.contains("already loaded")) {
                Log.w(TAG, "_stratum.so already loaded, continuing: " + msg);
            } else {
                // Real failure — surface it immediately with full context.
                throw new RuntimeException(
                        "[Stratum] System.load() failed for: " + soPath +
                                "\nABI: " + android.os.Build.SUPPORTED_ABIS[0] +
                                "\nReason: " + msg, e);
            }
        }

        // ── Step 4: Hand the Android Activity reference to C++ ────────────
        //
        // Must happen BEFORE nativeOnCreate() so that getActivity() works
        // from Python's onCreate handler.
        nativeSetActivity(this);
        Log.i(TAG, "Activity reference sent to C++ bridge.");

        // ── Step 5: Execute main.py ────────────────────────────────────────
        //
        // getModule() imports the module if not yet imported, or returns
        // the cached module object if already imported. This is idempotent.
        try {
            Python py = Python.getInstance();
            py.getModule("main");
            Log.i(TAG, "main.py loaded successfully.");

            // Tell the stratum package to scan main.py's globals and wire
            // up any lifecycle callbacks (@on_create, @on_resume, etc.)
            // that were registered via decorators during import.
            py.getModule("stratum").callAttr("_auto_register_lifecycle");
            Log.i(TAG, "Lifecycle callbacks registered.");

        } catch (Exception e) {
            throw new RuntimeException(
                    "[Stratum] main.py failed to load: " + e.getMessage(), e);
        }

        // ── Step 6: Fire the onCreate lifecycle into Python ────────────────
        nativeOnCreate();
        Log.i(TAG, "nativeOnCreate dispatched.");
    }

    // ── Lifecycle forwarding ───────────────────────────────────────────────

    // ── Hardware Back Button Intercept ─────────────────────────────────────
    @Override
    public void onBackPressed() {
        try {
            Python py = Python.getInstance();
            PyObject main = py.getModule("main");

            // Check if our Python file has the onBackPressed function
            if (main.containsKey("onBackPressed")) {
                PyObject result = main.callAttr("onBackPressed");

                // If Python returns True, it successfully navigated back a screen.
                if (result != null && result.toBoolean()) {
                    return; // Stop Android from closing the app
                }
            }
        } catch (Exception e) {
            Log.e(TAG, "Error routing onBackPressed to Python", e);
        }

        // If Python returned False (empty history stack), let Android close the app!
        super.onBackPressed();
    }

    @Override
    protected void onResume() {
        super.onResume();
        nativeOnResume();
    }

    @Override
    protected void onPause() {
        super.onPause();
        nativeOnPause();
    }

    @Override
    protected void onStop() {
        super.onStop();
        nativeOnStop();
    }

    @Override
    protected void onDestroy() {
        super.onDestroy();
        nativeOnDestroy();
    }

    // ── SO path resolution ─────────────────────────────────────────────────

    /**
     * Asks Python's import system where _stratum.so lives on disk.
     *
     * Strategy 1 (preferred): importlib.util.find_spec("_stratum")
     *   — finds the extension module without importing it.
     *   — works for top-level extension modules.
     *
     * Strategy 2 (fallback): import stratum; stratum._stratum.__file__
     *   — works when _stratum is a submodule of the stratum package.
     *
     * Returns null if both strategies fail.
     */
    private String findStratumSoPath() {
        Python py = Python.getInstance();

        // ── Strategy 1: find_spec (no import side-effects) ────────────────
        try {
            PyObject importlib = py.getModule("importlib.util");
            PyObject spec = importlib.callAttr("find_spec", "_stratum");
            if (spec != null && !spec.equals(py.getBuiltins().callAttr("eval", "None"))) {
                PyObject origin = spec.get("origin");
                if (origin != null) {
                    String path = origin.toString();
                    if (path.endsWith(".so")) {
                        Log.i(TAG, "find_spec found _stratum.so: " + path);
                        return path;
                    }
                }
            }
        } catch (Exception e) {
            Log.w(TAG, "find_spec strategy failed: " + e.getMessage());
        }

        // ── Strategy 2: import stratum package, read _stratum.__file__ ────
        try {
            PyObject stratum = py.getModule("stratum");
            PyObject submodule = stratum.get("_stratum");
            if (submodule != null) {
                PyObject fileAttr = submodule.get("__file__");
                if (fileAttr != null) {
                    String path = fileAttr.toString();
                    if (path.endsWith(".so")) {
                        Log.i(TAG, "stratum._stratum.__file__ found: " + path);
                        return path;
                    }
                }
            }
        } catch (Exception e) {
            Log.w(TAG, "stratum._stratum strategy failed: " + e.getMessage());
        }

        // ── Strategy 3: import stratum, derive from __init__.py path ──────
        //
        // Last resort: your original approach. Less precise (string replace)
        // but works when _stratum.so sits next to __init__.py in the package
        // dir and Chaquopy named it exactly "_stratum.so" without an ABI tag.
        try {
            PyObject stratum = py.getModule("stratum");
            PyObject fileAttr = stratum.get("__file__");
            if (fileAttr != null) {
                String initPath = fileAttr.toString();
                // initPath is something like:
                //   /data/user/0/com.example/files/chaquopy/AssetFinder/stratum/__init__.py
                // The .so sits in the same directory.
                String dir = initPath.substring(0, initPath.lastIndexOf('/') + 1);

                // Ask Python to list .so files in that directory so we pick
                // the exact filename regardless of ABI tag in the name.
                PyObject os = py.getModule("os");
                PyObject listdir = os.callAttr("listdir", dir);
                PyObject builtins = py.getBuiltins();

                // Convert to Java list via str(listdir)... easier: iterate
                // We'll do it the safe way via Python itself.
                PyObject result = py.getModule("builtins").callAttr("next",
                        builtins.callAttr("filter",
                                py.getModule("builtins").callAttr("eval",
                                        "lambda f: f.startswith('_stratum') and f.endswith('.so')"
                                ),
                                listdir
                        ),
                        py.getBuiltins().callAttr("eval", "None")
                );

                if (result != null) {
                    String soName = result.toString();
                    String fullPath = dir + soName;
                    Log.i(TAG, "Directory scan found: " + fullPath);
                    return fullPath;
                }

                // Absolute last resort: the plain rename assumption
                String guessed = dir + "_stratum.so";
                Log.w(TAG, "Guessing SO path: " + guessed);
                return guessed;
            }
        } catch (Exception e) {
            Log.e(TAG, "All SO location strategies failed: " + e.getMessage());
        }

        return null;
    }

    // ── Native method declarations ─────────────────────────────────────────

    private static native void nativeSetActivity(Object activity);
    private static native void nativeOnCreate();
    private static native void nativeOnResume();
    private static native void nativeOnPause();
    private static native void nativeOnStop();
    private static native void nativeOnDestroy();
}






















//package com.stratum.runtime;
//
//import android.os.Bundle;
//import androidx.appcompat.app.AppCompatActivity;
//import com.chaquo.python.PyObject;
//import com.chaquo.python.Python;
//import com.chaquo.python.android.AndroidPlatform;
//
//public class StratumActivity extends AppCompatActivity {
//
//    @Override
//    protected void onCreate(Bundle savedInstanceState) {
//        super.onCreate(savedInstanceState);
//
//        // 1. Start Python
//        if (!Python.isStarted()) {
//            Python.start(new AndroidPlatform(this));
//        }
//
//        // 2. Find and load the C++ _stratum.so bridge
//        String soPath;
//        try {
//            Python py = Python.getInstance();
//            PyObject builtins = py.getModule("builtins");
//            PyObject globals = builtins.callAttr("dict");
//            builtins.callAttr("exec",
//                    "import stratum\npath = stratum.__file__", globals);
//            String initPath = globals.callAttr("get", "path").toString();
//            soPath = initPath.replace("__init__.py", "_stratum.so");
//        } catch (Exception e) {
//            throw new RuntimeException("stratum path failed: " + e, e);
//        }
//
//        try {
//            System.load(soPath);
//        } catch (UnsatisfiedLinkError ignored) {}
//
//        // 3. Send the Android Activity instance to C++
//        nativeSetActivity(this);
//
//        // 4. Load your main.py code
//        try {
//            Python.getInstance().getModule("main");
//
//            // FIX: Force stratum to scan and register lifecycles NOW that main.py is fully loaded!
//            Python.getInstance().getModule("stratum").callAttr("_auto_register_lifecycle");
//
//        } catch (Exception e) {
//            throw new RuntimeException("main.py failed: " + e, e);
//        }
//
//        // 5. FIX: Actually trigger the onCreate method!
//        nativeOnCreate();
//    }
//
//    @Override
//    protected void onResume() {
//        super.onResume();
//        nativeOnResume();
//    }
//
//    @Override
//    protected void onPause() {
//        super.onPause();
//        nativeOnPause();
//    }
//
//    @Override
//    protected void onStop() {
//        super.onStop();
//        nativeOnStop();
//    }
//
//    @Override
//    protected void onDestroy() {
//        super.onDestroy();
//        nativeOnDestroy();
//    }
//
//    // Native bridge methods
//    private static native void nativeSetActivity(Object activity);
//
//    // FIX: Added the missing nativeOnCreate bridge
//    private static native void nativeOnCreate();
//
//    private static native void nativeOnResume();
//    private static native void nativeOnPause();
//    private static native void nativeOnStop();
//    private static native void nativeOnDestroy();
//}