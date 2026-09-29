package com.stratum.runtime;

import android.os.Bundle;
import android.util.Log;
import androidx.appcompat.app.AppCompatActivity;

/**
 * Base activity for Stratum applications using direct embedded CPython.
 * Handles JVM library loading, Activity handoff to C++, and lifecycle forwarding.
 */
public class StratumActivity extends AppCompatActivity {

    private static final String TAG = "StratumActivity";

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        // ── Step 1: Ensure libstratum.so is loaded in JVM ──────────────────
        try {
            System.loadLibrary("stratum");
            Log.i(TAG, "libstratum.so loaded into JVM.");
        } catch (UnsatisfiedLinkError e) {
            String msg = e.getMessage() != null ? e.getMessage() : "";
            if (!msg.contains("already loaded")) {
                throw new RuntimeException("[Stratum] Failed to load libstratum.so: " + msg, e);
            }
        }

        // ── Step 2: Hand the Android Activity reference to C++ bridge ──────
        // Must happen BEFORE nativeOnCreate() so getActivity() works in Python
        nativeSetActivity(this);
        Log.i(TAG, "Activity reference registered in C++ bridge.");

        // ── Step 3: Import main.py ─────────────────────────────────────────
        if (!onImportMain()) {
            throw new RuntimeException("[Stratum] main.py failed to load.");
        }
        Log.i(TAG, "main.py loaded successfully.");

        // ── Step 4: Fire onCreate lifecycle event into Python ──────────────
        nativeOnCreate();
        Log.i(TAG, "nativeOnCreate dispatched to Python.");
    }

    /**
     * Subclasses (MainActivity) override this to route module import through PythonHost.
     */
    protected boolean onImportMain() {
        return true;
    }

    /**
     * Subclasses (MainActivity) override this to route back-press to Python.
     */
    protected boolean onBackPressedPy() {
        return false;
    }

    // ── Hardware Back Button Intercept ─────────────────────────────────────
    @Override
    public void onBackPressed() {
        try {
            if (onBackPressedPy()) {
                return; // Python handled the back action (e.g. navigated screens)
            }
        } catch (Throwable t) {
            Log.e(TAG, "Error routing onBackPressed to Python", t);
        }
        // Python did not consume it; let Android handle default back behavior
        super.onBackPressed();
    }

    // ── Lifecycle forwarding to C++ bridge ─────────────────────────────────
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

    // ── Native JNI methods exported by libstratum.so ───────────────────────
    private static native void nativeSetActivity(Object activity);
    private static native void nativeOnCreate();
    private static native void nativeOnResume();
    private static native void nativeOnPause();
    private static native void nativeOnStop();
    private static native void nativeOnDestroy();
}