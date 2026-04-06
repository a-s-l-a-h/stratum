package com.stratum.runtime;

import java.lang.reflect.InvocationHandler;
import java.lang.reflect.Method;

public class StratumInvocationHandler implements InvocationHandler {

    private final String callbackKey;

    public StratumInvocationHandler(String callbackKey) {
        this.callbackKey = callbackKey;
    }

    @Override
    public Object invoke(Object proxy, Method method, Object[] args) throws Throwable {
        String name = method.getName();

        // Safely return primitives so Android doesn't throw a NullPointerException!
        if (name.equals("toString")) return "StratumProxy";
        if (name.equals("hashCode")) return System.identityHashCode(proxy);
        if (name.equals("equals")) return proxy == args[0];

        // Send the method name to C++ so we can route multi-method interfaces!
        nativeDispatch(callbackKey, name, args != null ? args : new Object[0]);
        return null;
    }

    // FIX: Signature now correctly expects 3 arguments!
    public static native void nativeDispatch(String key, String method, Object[] args);
}