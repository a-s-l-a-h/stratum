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

        Object result = nativeDispatch(callbackKey, name, args != null ? args : new Object[0]);
        Class<?> retType = method.getReturnType();
        if (result == null && retType.isPrimitive()) {
            // Proxy.invoke() throws NullPointerException if a null is
            // returned for a primitive-typed interface method (e.g. a
            // Python callback that returned None, threw, or forgot to
            // return). Substitute the correct zero-value instead of
            // crashing the whole dispatch.
            if (retType == boolean.class) return Boolean.FALSE;
            if (retType == int.class)     return Integer.valueOf(0);
            if (retType == long.class)    return Long.valueOf(0L);
            if (retType == float.class)   return Float.valueOf(0f);
            if (retType == double.class)  return Double.valueOf(0.0);
            if (retType == byte.class)    return Byte.valueOf((byte) 0);
            if (retType == short.class)   return Short.valueOf((short) 0);
            if (retType == char.class)    return Character.valueOf((char) 0);
        }
        return result;
    }

    public static native Object nativeDispatch(String key, String method, Object[] args);
}