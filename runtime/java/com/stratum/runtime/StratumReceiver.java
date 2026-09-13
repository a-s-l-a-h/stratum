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
        StratumInvocationHandler.nativeDispatch(KEY, "onReceive", new Object[]{ context, intent });
    }
}