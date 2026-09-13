package com.stratum.runtime;

import android.content.Context;
import android.util.Log;
import com.chaquo.python.PyObject;
import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

/**
 * Ensures the Python interpreter and _stratum.so are ready even when a
 * background component (StratumService, StratumReceiver) is the very
 * first thing Android starts — e.g. a BOOT_COMPLETED broadcast firing
 * before any Activity has ever opened. Both Python.start() and
 * System.load() are idempotent, so calling this after StratumActivity
 * already ran is always a safe no-op.
 */
final class StratumBootstrap {
    private static final String TAG = "StratumBootstrap";
    private StratumBootstrap() {}

    static synchronized void ensureReady(Context context) {
        if (!Python.isStarted()) {
            Python.start(new AndroidPlatform(context.getApplicationContext()));
        }
        Python py = Python.getInstance();
        String soPath = findStratumSoPath(py);
        if (soPath != null) {
            try {
                System.load(soPath);
                Log.i(TAG, "Background bootstrap: loaded _stratum.so from " + soPath);
            } catch (UnsatisfiedLinkError e) {
                String msg = e.getMessage() != null ? e.getMessage() : "";
                if (!msg.contains("already loaded")) {
                    throw new RuntimeException("[Stratum] Background bootstrap: System.load failed: " + msg, e);
                }
            }
        } else {
            Log.w(TAG, "Could not determine exact _stratum.so path, falling back to System.loadLibrary");
            try {
                System.loadLibrary("_stratum");
            } catch (UnsatisfiedLinkError ignored) {}
        }
        try {
            py.getModule("main");
        } catch (Exception e) {
            throw new RuntimeException("[Stratum] Background bootstrap: main.py failed: " + e.getMessage(), e);
        }
    }

    private static String findStratumSoPath(Python py) {
        try {
            PyObject stratum = py.getModule("stratum");
            PyObject submodule = stratum.get("_stratum");
            if (submodule != null) {
                PyObject fileAttr = submodule.get("__file__");
                if (fileAttr != null) {
                    String path = fileAttr.toString();
                    if (path.endsWith(".so")) return path;
                }
            }
        } catch (Exception ignored) {}

        try {
            PyObject importlib = py.getModule("importlib.util");
            PyObject spec = importlib.callAttr("find_spec", "stratum._stratum");
            if (spec != null) {
                PyObject origin = spec.get("origin");
                if (origin != null) {
                    String path = origin.toString();
                    if (path.endsWith(".so")) return path;
                }
            }
        } catch (Exception ignored) {}

        try {
            PyObject stratum = py.getModule("stratum");
            PyObject fileAttr = stratum.get("__file__");
            if (fileAttr != null) {
                String initPath = fileAttr.toString();
                String dir = initPath.substring(0, initPath.lastIndexOf('/') + 1);
                return dir + "_stratum.so";
            }
        } catch (Exception ignored) {}

        return null;
    }
}