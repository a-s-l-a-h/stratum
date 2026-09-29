package com.stratum.runtime;

import android.content.Context;
import android.util.Log;

/**
 * Direct CPython equivalent of Chaquopy's StratumBootstrap.
 *
 * Ensures the runtime state is verified when background components
 * (StratumService, StratumReceiver) are invoked.
 *
 * All .so loading, asset unpacking, and Python initialization are
 * managed by the main activity setup.
 */
public final class StratumBootstrap {
    private static final String TAG = "StratumBootstrap";
    private static volatile boolean sReady = false;

    private StratumBootstrap() {}

    /**
     * Marks the runtime as initialized. Called by MainActivity once
     * Python and libraries are loaded.
     */
    public static void markReady() {
        sReady = true;
        Log.i(TAG, "Stratum runtime marked ready.");
    }

    public static boolean isReady() {
        return sReady;
    }

    /**
     * Ensures the runtime is ready for background operations.
     */
    public static synchronized void ensureReady(Context context) {
        if (!sReady) {
            Log.w(TAG, "Stratum component invoked before MainActivity initialization.");
        }
    }
}