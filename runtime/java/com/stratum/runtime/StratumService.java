package com.stratum.runtime;

import android.app.Service;
import android.content.Intent;
import android.os.IBinder;
import androidx.annotation.Nullable;

/**
 * Universal Android background Service bridge. Single global key —
 * branch on intent.getAction()/extras inside your one Python
 * onStartCommand handler if you need multiple distinct behaviors.
 */
public class StratumService extends Service {

    private static final String KEY = "StratumService";

    @Override
    public void onCreate() {
        super.onCreate();
        StratumBootstrap.ensureReady(this);
        StratumInvocationHandler.nativeDispatch(KEY, "onCreate", new Object[]{ this });
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        Object res = StratumInvocationHandler.nativeDispatch(KEY, "onStartCommand",
                new Object[]{ intent, flags, startId });
        if (res instanceof Number) {
            return ((Number) res).intValue();
        }
        return START_NOT_STICKY;
    }

    @Override
    public void onDestroy() {
        super.onDestroy();
        StratumInvocationHandler.nativeDispatch(KEY, "onDestroy", new Object[0]);
    }

    @Nullable
    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}