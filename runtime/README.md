# Stratum Pipeline — Android Runtime

## What is this folder?

The `runtime/` folder contains the **static Java scaffolding** required to boot your compiled C++ library (`_stratum.so`) and Python environment on the Android device. 

Unlike the adapters in Stage 05.5 or the C++ in Stage 06, **these files are never auto-generated or modified by the pipeline.** They are permanent fixtures of the Stratum framework. You simply copy them into your Android Studio project once.

---

## Installation: Where do these files go?

You must copy the contents of this folder into your Android Studio project's source tree under the exact package name `com.stratum.runtime`.

**Expected Android Studio Project Structure:**
```text
MyAndroidApp/
└── app/
    └── src/
        └── main/
            └── java/
                ├── com/example/myapp/
                │   └── MainActivity.java     <-- Your app's main activity
                └── com/stratum/
                    ├── adapters/             <-- Stage 05.5 outputs go here (if any)
                    └── runtime/              <-- COPY FILES HERE
                        ├── StratumActivity.java
                        └── StratumInvocationHandler.java
```

---

## File 1: `StratumActivity.java`

This acts as the ultimate bootloader for the bridge. When the app launches, it performs a strict 6-step initialization sequence:

### The 6-Step Boot Sequence
1. **Start Python:** Initializes the Chaquopy Python VM.
2. **Locate `_stratum.so`:** Chaquopy extracts the correct `.so` for the device's CPU architecture (ABI) into a private app folder. Rather than guessing paths, `StratumActivity` uses Python's own `importlib.util.find_spec("_stratum")` to ask the Python VM exactly where the native library lives on disk.
3. **Load the C++ Bridge:** It calls `System.load(soPath)` to inject your compiled C++ directly into the Android JVM.
4. **Handover Context:** Calls `nativeSetActivity(this)` to give C++ a global JNI reference to the screen, allowing Python to draw UI elements.
5. **Execute `main.py`:** Imports your Python entry point. It then tells the Stratum package to scan your `main.py` for lifecycle functions (`onCreate`, `onResume`, etc.) and wires them up dynamically.
6. **Dispatch Lifecycles:** It triggers `nativeOnCreate()`, which flows down into C++ and finally invokes `onCreate()` in your Python code.

### Python Hardware Back-Button Intercept
`StratumActivity` automatically intercepts the Android hardware back button. 
It looks for a `def onBackPressed():` function in your `main.py`.
*   If Python returns `True`: Android ignores the button press (useful if Python handled navigating back a screen in your custom UI).
*   If Python returns `False` (or the function doesn't exist): Android closes the app normally.

---

## File 2: `StratumInvocationHandler.java`

This file is the engine behind Python Callbacks and Java Interfaces (used heavily if you ran Stage 05.5).

When an Android system service (like the Camera or a Button) expects a Java Interface (like a `StateCallback` or `OnClickListener`), C++ generates a dynamic Java proxy object that looks and acts like that interface. 

Every time Android calls a method on that proxy, the call is intercepted by `StratumInvocationHandler`.

### How it works:
1. Android triggers a callback (e.g., `onOpened(camera)`).
2. `StratumInvocationHandler` catches it via the `invoke()` method.
3. It intercepts basic JVM methods (`toString`, `hashCode`, `equals`) and handles them natively so Android doesn't crash trying to inspect the proxy.
4. It packages the method name (`"onOpened"`) and the arguments into an array.
5. It fires `nativeDispatch(key, method, args)` down into C++.
6. C++ uses the `key` to look up the exact Python `lambda` or `def` you provided, converts the Java arguments to Python objects, and executes your Python code.

---

## Final Setup Step: Update your `MainActivity.java`

Because `StratumActivity` takes over the boot process, your app's main activity simply needs to inherit from it. **You do not need to change your `AndroidManifest.xml`.**

Open your standard `MainActivity.java` and change it to extend `StratumActivity`. You can delete the default `onCreate` method entirely, because Stratum handles it now.

**`MainActivity.java`**
```java
package com.example.myapp;

import com.stratum.runtime.StratumActivity;

public class MainActivity extends StratumActivity {
    // Leave this empty! 
    // StratumActivity will automatically load and run your Python main.py
}
```

Your `AndroidManifest.xml` remains exactly as Android Studio generated it:

**`AndroidManifest.xml` (No changes needed)**
```xml
<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    xmlns:tools="http://schemas.android.com/tools">

    <application
        android:allowBackup="true"
        android:icon="@mipmap/ic_launcher"
        android:label="@string/app_name"
        android:theme="@style/Theme.MyApp">
        
        <!-- Standard MainActivity declaration -->
        <activity
            android:name=".MainActivity"
            android:exported="true">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity>
        
    </application>
</manifest>
```