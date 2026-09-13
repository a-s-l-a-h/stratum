
# Stratum Runtime: Android Host Infrastructure

## Overview

The `runtime/` directory contains the Java classes, ProGuard rules, and Android manifest entries that reside inside the consumer Android Studio project. These components host the Chaquopy Python runtime, load the `_stratum.so` native bridge, route Android lifecycle and UI events, and dispatch callback invocations back into Python.

---

## File Summary

```
runtime/
├── AndroidManifest.xml
├── consumer-rules.pro
└── java/
    └── com/
        └── stratum/
            └── runtime/
                ├── StratumActivity.java
                ├── StratumBootstrap.java
                ├── StratumInvocationHandler.java
                ├── StratumReceiver.java
                ├── StratumRuntimeLookup.java
                ├── StratumService.java
                └── StratumView.java
```

---

## Component Details

### 1. `StratumActivity.java`
The primary application entry point:
* Initializes the Chaquopy `Python` environment.
* Discovers the physical location of `_stratum.so` using Python's `importlib.util.find_spec` machinery and loads it via `System.load()`.
* Passes the Activity global reference to C++ via `nativeSetActivity()`.
* Executes `main.py` and triggers `_auto_register_lifecycle()`.
* Forwards Android lifecycle events (`onCreate`, `onResume`, `onPause`, `onStop`, `onDestroy`).
* Intercepts `onBackPressed()`: if Python defines an `onBackPressed()` function returning `True`, hardware back navigation is handled in Python.

### 2. `StratumBootstrap.java`
Ensures Python and `_stratum.so` are initialized when a background component (such as a Service or BroadcastReceiver) is triggered before any Activity has started (e.g. `BOOT_COMPLETED`).

### 3. `StratumInvocationHandler.java`
The native dispatch bridge. Used as the invocation target for both dynamic proxies and auto-generated adapters. Declares:
```java
public static native Object nativeDispatch(String key, String method, Object[] args);
```
Includes default return unboxing safety to prevent `NullPointerException` crashes in the ART runtime when a Python callback returns `None` for a primitive Java return type.

### 4. `StratumView.java`
A custom `ViewGroup` subclass enabling custom drawing and layout in Python:
* `onDraw(Canvas)`: Forwards canvas drawing to Python via `StratumInvocationHandler.nativeDispatch()`.
* `onMeasure(w, h)`: Forwards layout measurement, allowing Python to return custom `[width, height]` dimensions.
* `onLayout(...)` & `onTouchEvent(MotionEvent)`: Dispatches touch gestures and layout changes to Python.

### 5. `StratumService.java` & `StratumReceiver.java`
Drop-in Android `Service` and `BroadcastReceiver` components that bootstrap Python and route system intents to registered Python callbacks.

### 6. `StratumRuntimeLookup.java`
An optional utility for custom Java code in Android Studio to synchronously call Python functions registered using `@stratum.export`.

---

## Integration into Android Studio

1. **Copy Java Files**:
   Copy `runtime/java/com/stratum/runtime/*` into your Android Studio app project at:
   `app/src/main/java/com/stratum/runtime/`

2. **Copy Generated Adapters**:
   If Stage 05.5 was run with `--mode on`, copy all generated files from:
   `05_5_abstract/output/java/com/stratum/adapters/*`
   into:
   `app/src/main/java/com/stratum/adapters/`

3. **ProGuard / R8 Configuration**:
   Append the contents of `runtime/consumer-rules.pro` to your app's `proguard-rules.pro`:
   ```proguard
   -keep class com.stratum.runtime.** { *; }
   -keepclassmembers class com.stratum.runtime.** { *; }
   -keep class com.stratum.adapters.** { *; }
   -keepclassmembers class com.stratum.adapters.** { *; }
   -keepclasseswithmembernames class * {
       native <methods>;
   }
   ```

4. **AndroidManifest.xml**:
   Ensure `StratumActivity` is declared as your main launcher activity:
   ```xml
   <activity
       android:name="com.stratum.runtime.StratumActivity"
       android:exported="true">
       <intent-filter>
           <action android:name="android.intent.action.MAIN" />
           <category android:name="android.intent.category.LAUNCHER" />
       </intent-filter>
   </activity>
   ```