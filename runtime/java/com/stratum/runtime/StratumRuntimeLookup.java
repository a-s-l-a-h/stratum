package com.stratum.runtime;

/**
 * OPTIONAL UTILITY — rare-use-case Java-to-Python dynamic lookup.
 *
 * Nothing in the core pipeline (StratumActivity, StratumView,
 * StratumReceiver, StratumService) depends on this class or is changed
 * by it. It exists purely for custom Java code you write yourself in
 * Android Studio (a third-party SDK callback, a manual click listener,
 * etc.) that needs to call an exported Python function.
 *
 * Example:
 *     Object result = StratumRuntimeLookup.callPython("processPayload", data);
 */
public final class StratumRuntimeLookup {

    private StratumRuntimeLookup() {} // Prevent instantiation

    /**
     * Dispatches a call to a Python function registered via
     * stratum.export / stratum.register_function.
     *
     * @param functionName Name of the registered Python function.
     * @param args          Arguments passed to Python (converted by the
     *                      existing, unmodified nativeDispatch bridge).
     * @return Converted return value from Python, or null.
     */
    public static Object callPython(String functionName, Object... args) {
        return StratumInvocationHandler.nativeDispatch(
                "app",
                functionName,
                args != null ? args : new Object[0]
        );
    }
}