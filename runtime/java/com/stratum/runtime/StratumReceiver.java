package com.stratum.runtime;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/**
 * Universal Broadcast Receiver bridge. Single global key — branch on
 * intent.getAction() in your one Python onReceive handler.
 */
public class StratumReceiver extends BroadcastReceiver {

    public static final String KEY = "StratumReceiver";

    @Override
    public void onReceive(Context context, Intent intent) {
        StratumBootstrap.ensureReady(context);
        // GUARD AGAINST CRASH IF PYTHON IS NOT RUNNING YET
        if (!StratumBootstrap.isReady()) {
            android.util.Log.w("StratumReceiver", "Ignored broadcast: Python runtime is not ready.");
            return;
        }
        StratumInvocationHandler.nativeDispatch(KEY, "onReceive", new Object[]{ context, intent });
    }
}