# Stratum Python API Reference & Developer Guide

> **Official Developer Guide for Stratum**  
> High-performance, zero-overhead Python-to-Android native bridge.

---

## ⚡ Quick Cheat Sheet: The 5 Golden Rules

Before writing code, keep these fundamental architectural rules in mind:

1. **Imports Mirror Android Packages:** Classes are imported directly from their corresponding Android package structure under `stratum.`:  
   ```python
   from stratum.android.widget.TextView import TextView
   from stratum.android.view.View import View
   ```
2. **Inner Classes Use Underscores (`_` instead of `$`):** Java nested classes and interfaces (e.g., `Paint$Style` or `View$OnClickListener`) are mapped using an underscore:  
   ```python
   from stratum.android.graphics.Paint_Style import Paint_Style
   from stratum.android.view.View_OnClickListener import View_OnClickListener
   ```
   *(Never use dot notation like `Paint.Style`—it will raise an `AttributeError` at runtime).*
3. **Static vs. Instance Field Access:** Static constants use `sf_get_*()` and `sf_set_*()`. Instance fields on an object use `f_get_*()` and `f_set_*()`:  
   ```python
   visible_state = View.sf_get_VISIBLE()
   sensor_values = sensor_event.f_get_values()
   ```
4. **Both `camelCase` and `snake_case` are Supported:** Every Java method is exposed with both its original Java name and an idiomatic Python alias:  
   ```python
   tv.setText("Hello")
   tv.set_text("Hello")  # Identical native call
   ```
5. **Retain Root Views in Global Scope:** Python's garbage collector will destroy your view hierarchy when `onCreate()` returns unless assigned to a module-level variable:  
   ```python
   app_layout = None  # Global module reference

   def onCreate():
       global app_layout
       app_layout = LinearLayout(stratum.getActivity())
       stratum.setContentView(stratum.getActivity(), app_layout)
   ```

---

## Table of Contents

1. [Application Lifecycle & Entry Point (`main.py`)](#1-application-lifecycle--entry-point-mainpy)
2. [Importing Classes & Naming Conventions](#2-importing-classes--naming-conventions)
3. [Method Invocations, Overloads & Varargs](#3-method-invocations-overloads--varargs)
4. [Fields, Constants & Enums (`sf_get_*` / `f_get_*`)](#4-fields-constants--enums-sf_get_--f_get_)
5. [Type Marshaling & Data Conversion](#5-type-marshaling--data-conversion)
6. [Event Listeners & Callback Dispatch](#6-event-listeners--callback-dispatch)
7. [Thread Safety & UI Thread Dispatch](#7-thread-safety--ui-thread-dispatch)
8. [Custom 2D Graphics & Views (`CustomCanvasView`)](#8-custom-2d-graphics--views-customcanvasview)
9. [Direct ByteBuffers & Zero-Copy Native Memory](#9-direct-bytebuffers--zero-copy-native-memory)
10. [Type Casting & Object Identity](#10-type-casting--object-identity)
11. [Calling Python from Java (`@stratum.export`)](#11-calling-python-from-java-stratumexport)
12. [Dynamic Reflection Escape Hatch (`stratum.reflect`)](#12-dynamic-reflection-escape-hatch-stratumreflect)
13. [Working with XML Layouts & Android Resources](#13-working-with-xml-layouts--android-resources)
14. [Complete Production-Grade Examples](#14-complete-production-grade-examples)

---

## 1. Application Lifecycle & Entry Point (`main.py`)

Every Stratum application begins in `main.py`. The native runtime automatically discovers and binds top-level lifecycle functions on launch.

```python
import stratum

def onCreate():
    """
    Called when the Activity is first created.
    Initialize components, instantiate views, and mount your layout here.
    """
    activity = stratum.getActivity()
    # Build your layout...

def onResume():
    """Called when the application begins interacting with the user."""
    pass

def onPause():
    """Called when the application is partially obscured or losing focus."""
    pass

def onStop():
    """Called when the application is no longer visible to the user."""
    pass

def onDestroy():
    """Called before the Activity is destroyed by the system."""
    pass

def onBackPressed() -> bool:
    """
    Intercepts the hardware or gesture back button.
    Return True  -> Consumes the event (e.g., handles in-app navigation).
    Return False -> Allows Android to perform default back action (exits app).
    """
    if can_navigate_back():
        pop_navigation_stack()
        return True
    return False
```

### Core Top-Level Module APIs

| Function | Description |
| :--- | :--- |
| `stratum.getActivity()` / `get_activity()` | Returns the current Android `Activity` wrapped as `stratum.android.app.Activity`. Returns `None` if called prior to initialization. |
| `stratum.setContentView(activity, view)` | Attaches the given `View` instance as the root content view of the specified `Activity`. |
| `stratum.set_log_enabled(enabled: bool)` | Toggles deep C++ bridge logging in logcat at runtime (if enabled at build time). |

---

## 2. Importing Classes & Naming Conventions

### Standard Classes

Import paths directly mirror the Android SDK hierarchy under the `stratum.` root package:

```python
from stratum.android.widget.TextView import TextView
from stratum.android.widget.Button import Button
from stratum.android.widget.LinearLayout import LinearLayout
from stratum.android.view.View import View
from stratum.android.content.Intent import Intent
from stratum.android.graphics.Bitmap import Bitmap
```

### Inner Classes, Static Classes & Enums

In Java bytecode, inner classes are separated by `$`. In Stratum, **all inner classes and nested types use an underscore (`_`)**:

| Java Class / Interface | Stratum Python Import |
| :--- | :--- |
| `android.graphics.Paint.Style` | `from stratum.android.graphics.Paint_Style import Paint_Style` |
| `android.view.View.OnClickListener` | `from stratum.android.view.View_OnClickListener import View_OnClickListener` |
| `android.view.ViewGroup.LayoutParams` | `from stratum.android.view.ViewGroup_LayoutParams import ViewGroup_LayoutParams` |
| `android.graphics.Bitmap.CompressFormat` | `from stratum.android.graphics.Bitmap_CompressFormat import Bitmap_CompressFormat` |
| `android.hardware.camera2.CameraDevice.StateCallback` | `from stratum.android.hardware.camera2.CameraDevice_StateCallback import CameraDevice_StateCallback` |

> ⚠️ **Common Mistake:** Attempting `Paint.Style` will fail with an `AttributeError`. You must import `Paint_Style` directly.

---

## 3. Method Invocations, Overloads & Varargs

### Dual Naming: camelCase & snake_case

Stratum generates both the original Java method name and an idiomatic Python alias for every method:

```python
tv = TextView(activity)

# Both call the exact same underlying JNI method ID:
tv.setText("Status: Active")
tv.set_text("Status: Active")

tv.setVisibility(View.sf_get_VISIBLE())
tv.set_visibility(View.sf_get_VISIBLE())
```

### Dynamic Overload Resolution

Java allows multiple methods with the same name and differing parameter types. Stratum inspects incoming argument counts, types, and inheritance hierarchies dynamically:

```python
from stratum.android.widget.LinearLayout import LinearLayout

layout = LinearLayout(activity)

# Invokes: addView(View child)
layout.addView(my_button)

# Invokes: addView(View child, int index)
layout.addView(my_button, 0)

# Invokes: addView(View child, ViewGroup.LayoutParams params)
layout.addView(my_button, custom_params)
```

### Varargs Auto-Packing

Methods that take variable arguments (`int...`, `String...`, `Object...`) can be called with ordinary unpacked Python arguments:

```python
from stratum.android.animation.ValueAnimator import ValueAnimator

# Java: ValueAnimator.ofInt(int... values)
# Automatically packed into a native int[]:
animator = ValueAnimator.ofInt(0, 100, 250, 500)
```

---

## 4. Fields, Constants & Enums (`sf_get_*` / `f_get_*`)

Because Java fields are distinct from method tables, Stratum provides dedicated accessor functions:

### Static Fields & Constants (`sf_get_<name>` / `sf_set_<name>`)

Used for static configuration flags, layout dimensions, system constants, and enums:

```python
from stratum.android.view.View import View
from stratum.android.widget.LinearLayout import LinearLayout
from stratum.android.graphics.Color import Color
from stratum.android.graphics.Paint_Style import Paint_Style
from stratum.android.view.ViewGroup_LayoutParams import ViewGroup_LayoutParams

# Visibility flags
visible = View.sf_get_VISIBLE()        # View.VISIBLE
gone    = View.sf_get_GONE()           # View.GONE

# Layout orientations and parameters
vertical     = LinearLayout.sf_get_VERTICAL()
match_parent = ViewGroup_LayoutParams.sf_get_MATCH_PARENT()
wrap_content = ViewGroup_LayoutParams.sf_get_WRAP_CONTENT()

# Static color constants
color_red = Color.sf_get_RED()

# Enum types
style_fill   = Paint_Style.sf_get_FILL()
style_stroke = Paint_Style.sf_get_STROKE()
```

### Instance Fields (`f_get_<name>` / `f_set_<name>`)

Used to read or modify fields on an instantiated object:

```python
def on_sensor_changed(event):
    # Reads the float[] array from SensorEvent.values:
    vals = event.f_get_values()
    accel_x = vals[0]
    accel_y = vals[1]
    accel_z = vals[2]

    # Read timestamp (long)
    timestamp = event.f_get_timestamp()
```

---

## 5. Type Marshaling & Data Conversion

Stratum automatically marshals types across the Python-to-Java boundary.

### Type Mapping Reference

| Java Type | Python Input Argument | Python Return Value |
| :--- | :--- | :--- |
| `boolean` / `java.lang.Boolean` | `bool` | `bool` |
| `byte`, `short`, `int`, `long` | `int` | `int` |
| `float`, `double` | `float` or `int` | `float` |
| `String`, `CharSequence` | `str` | `str` |
| `byte[]` | `bytes`, `bytearray`, `memoryview` | `bytes` |
| `int[]`, `long[]`, `short[]` | `list[int]` or `tuple[int]` | `list[int]` |
| `float[]`, `double[]` | `list[float]` or `tuple[float]` | `list[float]` |
| `boolean[]` | `list[bool]` or `tuple[bool]` | `list[bool]` |
| `String[]` | `list[str]` or `tuple[str]` | `list[str]` |
| `Object[]` | `list[Any]` or `tuple[Any]` | `list[Any]` |
| `java.util.List` / `Collection` | `list`, `tuple`, `set` | `list` |
| `java.util.Map` | `dict` | `dict` |

### Deep Conversion Helpers

- `stratum.to_java(obj)`: Recursively converts Python structures (`dict`, `list`, primitive scalars) into real Java objects (`HashMap`, `ArrayList`, boxed primitives).
- `stratum.to_py(obj)`: Recursively unboxes Java data structures (`Map`, `List`, `Bundle`, arrays) into native Python `dict`, `list`, `str`, `int`, etc.
- `stratum.to_java_array(items, "com.example.Type")`: Constructs a strongly-typed native Java array (`T[]`).

#### Example: Building a Typed Array

Certain Android APIs (such as Camera2 metering or custom graphics filters) expect an array of a concrete class rather than `Object[]`:

```python
from stratum.android.graphics.Rect import Rect
from stratum.android.hardware.camera2.params.MeteringRectangle import MeteringRectangle

focus_rect = Rect(0, 0, 100, 100)
metering_rect = MeteringRectangle(focus_rect, 1000)

# Build a native MeteringRectangle[] array:
metering_array = stratum.to_java_array(
    [metering_rect], 
    "android.hardware.camera2.params.MeteringRectangle"
)
```

---

## 6. Event Listeners & Callback Dispatch

### Single-Method Listeners

For functional interfaces (`View.OnClickListener`, `Runnable`), pass a regular Python function or lambda:

```python
from stratum.android.widget.Button import Button

btn = Button(activity)

def handle_click(view):
    print("Button pressed!")

btn.setOnClickListener(handle_click)
# Or with a lambda:
btn.setOnClickListener(lambda v: print("Clicked!"))
```

### Multi-Method Interfaces & Abstract Classes

For listeners containing multiple methods or abstract classes with default implementations (`SensorEventListener`, `SurfaceTextureListener`, `CameraDevice.StateCallback`), pass a **Python dictionary mapping method names to functions**:

```python
from stratum.android.view.TextureView import TextureView

texture_view = TextureView(activity)

def on_available(surface_texture, width, height):
    print(f"SurfaceTexture ready: {width}x{height}")

def on_destroyed(surface_texture) -> bool:
    print("SurfaceTexture destroyed")
    return True  # Important: return value is forwarded back to Java!

texture_view.setSurfaceTextureListener({
    "onSurfaceTextureAvailable": on_available,
    "onSurfaceTextureDestroyed": on_destroyed,
    "onSurfaceTextureSizeChanged": lambda st, w, h: None,
    "onSurfaceTextureUpdated": lambda st: None,
})
```

### Return Values from Callbacks

If a Java callback expects a return value (such as `onTouch` or `onLongClick` returning a `boolean`), your Python function must return the appropriate type. The native bridge converts the return value back to Java automatically:

```python
def on_view_touch(view, motion_event) -> bool:
    action = motion_event.getAction()
    if action == 0:  # MotionEvent.ACTION_DOWN
        print("Touch down detected")
        return True   # Consumes the touch event
    return False      # Event propagates to parent view
```

---

## 7. Thread Safety & UI Thread Dispatch

Android enforces that UI modifications must occur strictly on the **Main Looper (UI thread)**. Modifying views from a background worker thread raises `android.view.ViewRootImpl$CalledFromWrongThreadException`.

Stratum provides two execution mechanisms to handle this:

### 1. `stratum.run_on_ui_thread(fn, *args, **kwargs)`

Dispatches a callable directly to Android's main loop:

```python
import threading
import time
import stratum

def worker_thread():
    time.sleep(2.0)
    result = "Background task complete"
    
    # Safely update the UI from the background thread:
    stratum.run_on_ui_thread(status_label.setText, result)

threading.Thread(target=worker_thread, daemon=True).start()
```

### 2. The `@stratum.ui_thread` Decorator

Ensures that any invocation of the decorated function is scheduled on the UI thread:

```python
@stratum.ui_thread
def display_alert(message: str, error: bool):
    status_label.setText(message)
    status_label.setTextColor(0xFFFF0000 if error else 0xFF00FF00)

# Can be called from ANY thread safely:
display_alert("Sync finished successfully", error=False)
```

---

## 8. Custom 2D Graphics & Views (`CustomCanvasView`)

`CustomCanvasView` (from `stratum.ui`) provides a native bridge for hardware-accelerated 2D graphics, custom layouts, and touch interactions without writing Java code.

### Methods to Override

- `on_draw(self, canvas)`: Custom drawing using `android.graphics.Canvas` and `Paint`.
- `on_touch_event(self, event) -> bool`: Handles raw touch events (`MotionEvent`). Return `True` to consume the gesture.
- `on_measure(self, width_spec, height_spec)`: Optional custom measurement. Return a list of two ints `[width, height]` to report custom dimensions.
- `on_layout(self, changed, left, top, right, bottom)`: Optional custom layout for child views.
- `self.invalidate()`: Requests an immediate screen redraw.

### Example: Touch-Driven Vector Canvas

```python
import stratum
from stratum.ui import CustomCanvasView
from stratum.android.graphics.Paint import Paint
from stratum.android.graphics.Paint_Style import Paint_Style

class ReticleView(CustomCanvasView):
    def __init__(self, activity):
        super().__init__(activity)

        # Reticle ring paint
        self.ring_paint = Paint()
        self.ring_paint.setAntiAlias(True)
        self.ring_paint.setColor(0xFF00FFCC)
        self.ring_paint.setStrokeWidth(4.0)
        self.ring_paint.setStyle(Paint_Style.sf_get_STROKE())

        # Coordinates
        self.x = 300.0
        self.y = 500.0

    def on_draw(self, canvas):
        # 1. Clear background
        canvas.drawColor(0xFF121212)

        # 2. Draw target reticle
        canvas.drawCircle(self.x, self.y, 75.0, self.ring_paint)
        canvas.drawLine(self.x - 100.0, self.y, self.x + 100.0, self.y, self.ring_paint)
        canvas.drawLine(self.x, self.y - 100.0, self.x, self.y + 100.0, self.ring_paint)

    def on_touch_event(self, event) -> bool:
        self.x = float(event.getX())
        self.y = float(event.getY())
        self.invalidate()  # Request immediate redraw
        return True
```

---

## 9. Direct ByteBuffers & Zero-Copy Native Memory

When processing large datasets (audio PCM streams, camera frames, bitmap pixel buffers), copying data through JNI is inefficient. Stratum provides direct memory mapping using Python's standard `memoryview` interface without requiring external libraries.

### Allocating and Mapping Native Buffers

```python
import stratum

# 1. Allocate 1MB of direct off-heap native memory in the JVM
byte_buffer = stratum.allocate_direct_buffer(1024 * 1024)

# 2. Extract a zero-copy Python memoryview of the native address
raw_view = stratum._stratum.bytebuffer_to_memoryview(byte_buffer._ptr)

# raw_view is a standard Python memoryview:
print(f"Allocated native bytes: {len(raw_view)}")

# 3. Modify bytes directly in native memory using standard slicing:
raw_view[0:4] = b"\x00\xFF\x00\xFF"  # Updates Java memory immediately
```

### Bitmaps and Pixel Buffers

You can copy pixel buffers directly between Android `Bitmap` objects and direct `ByteBuffer` instances:

```python
# Copy bitmap pixels into direct memory:
byte_buffer.rewind()
bitmap.copyPixelsToBuffer(byte_buffer)

# Manipulate or inspect pixels via standard Python memoryview:
view = stratum._stratum.bytebuffer_to_memoryview(byte_buffer._ptr)
first_pixel_alpha = view[3]

# Copy processed pixels back to an output bitmap:
byte_buffer.rewind()
bitmap.copyPixelsFromBuffer(byte_buffer)
```

### Native Surface Window Pointers

For direct rendering with native pipelines, obtain an `ANativeWindow*` handle:

```python
# Acquire raw pointer to the native window
win_ptr = stratum.surface_to_native_window(surface)

# ... interact with native window ...

# Always release the reference when finished
stratum.release_native_window(win_ptr)
```

---

## 10. Type Casting & Object Identity

### Downcasting (`from_ptr` / `stratum_cast`)

When an Android framework API returns a generic base class (such as `Context.getSystemService(...)` returning `Object`), downcast it to its concrete type:

```python
import stratum
from stratum.android.hardware.SensorManager import SensorManager

raw_service = activity.getSystemService("sensor")

# Method 1: Using the target class from_ptr method (Recommended)
sensor_mgr = SensorManager.from_ptr(raw_service)

# Method 2: Using the top-level helper
sensor_mgr = stratum.stratum_cast(raw_service, SensorManager)
```

Both methods verify inheritance using native JNI `IsInstanceOf` checks and will raise a `TypeError` if the instance does not match the target class.

### Object Identity (`==`)

Stratum overrides equality (`==`) on `StratumObject` instances to check whether two distinct Python wrappers reference the **exact same underlying Java object** using JNI `IsSameObject`:

```python
child_view1 = layout.getChildAt(0)
child_view2 = layout.getChildAt(0)

# True: They are different Python wrappers, but share the same Java reference
if child_view1 == child_view2:
    print("Same native Java object reference")
```

---

## 11. Calling Python from Java (`@stratum.export`)

You can expose Python functions to be invoked by custom Java code:

### 1. In Python (`main.py`):

```python
import stratum

@stratum.export
def process_message(sender: str, message: str) -> str:
    print(f"Message from {sender}: {message}")
    return f"Acknowledged: {message.upper()}"
```

### 2. In Java:

```java
import com.stratum.runtime.StratumRuntimeLookup;

Object result = StratumRuntimeLookup.callPython("process_message", "Alice", "System check");
// result contains "Acknowledged: SYSTEM CHECK"
```

---

## 12. Dynamic Reflection Escape Hatch (`stratum.reflect`)

If your project uses custom `.java` files compiled in Android Studio or system APIs that were excluded from the generated wrapper set, you can call them dynamically using `stratum.reflect`:

```python
from stratum import reflect

# 1. Call a static method by fully qualified class name:
result = reflect.call_java_static(
    "com.example.utils.EncryptionHelper",
    "sha256",
    "my_payload_data"
)

# 2. Call an instance method on any wrapped Java object:
output = reflect.call_java_method(
    my_java_instance,
    "performCustomAction",
    100,
    True
)
```

---

## 13. Working with XML Layouts & Android Resources

Stratum integrates with standard Android XML layouts and resources declared in your project's `res/` directory.

### Inflating an XML Layout

To inflate an XML layout (e.g., `res/layout/activity_main.xml`):

```python
import stratum
from stratum.android.widget.FrameLayout import FrameLayout
from stratum.android.widget.TextView import TextView
from stratum.android.widget.Button import Button

def get_res_id(activity, res_name: str, res_type: str = "id") -> int:
    """Finds an Android R.<type>.<name> integer ID at runtime."""
    res = activity.getResources()
    pkg = activity.getPackageName()
    res_id = res.getIdentifier(res_name, res_type, pkg)
    if res_id == 0:
        raise KeyError(f"Resource '{res_name}' of type '{res_type}' not found.")
    return res_id

def onCreate():
    activity = stratum.getActivity()
    inflater = activity.getLayoutInflater()

    # 1. Find layout resource ID for R.layout.activity_main
    layout_id = get_res_id(activity, "activity_main", "layout")

    # 2. Inflate layout and mount to activity
    root_view = FrameLayout.from_ptr(inflater.inflate(layout_id, None, False))
    stratum.setContentView(activity, root_view)

    # 3. Find nested views by their R.id.<name>
    text_view_id = get_res_id(activity, "title_text", "id")
    title_tv = TextView.from_ptr(root_view.findViewById(text_view_id))
    title_tv.setText("Bound via Stratum XML Inflation")

    button_id = get_res_id(activity, "action_button", "id")
    btn = Button.from_ptr(root_view.findViewById(button_id))
    btn.setOnClickListener(lambda v: title_tv.setText("Button Clicked!"))
```

---

## 14. Complete Production-Grade Examples

### Example 1: Pure Programmatic UI & Asynchronous Worker

A complete, standalone application featuring vertical layouts, styling, click counters, and a background thread updating the UI safely.

```python
import time
import threading
import stratum

from stratum.android.widget.LinearLayout import LinearLayout
from stratum.android.widget.TextView import TextView
from stratum.android.widget.Button import Button
from stratum.android.view.ViewGroup_LayoutParams import ViewGroup_LayoutParams

# Keep references in global scope to prevent garbage collection
app_layout = None
counter_tv = None
uptime_tv = None

click_count = 0
running = True

def on_click_increment(view):
    global click_count
    click_count += 1
    counter_tv.setText(f"Button Clicks: {click_count}")

def background_clock():
    seconds = 0
    while running:
        time.sleep(1.0)
        seconds += 1
        # Safely post text update to UI thread:
        stratum.run_on_ui_thread(uptime_tv.setText, f"Service Uptime: {seconds}s")

def onCreate():
    global app_layout, counter_tv, uptime_tv
    activity = stratum.getActivity()

    # 1. Root Vertical Layout
    app_layout = LinearLayout(activity)
    app_layout.setOrientation(LinearLayout.sf_get_VERTICAL())
    app_layout.setPadding(60, 100, 60, 60)
    app_layout.setBackgroundColor(0xFF1E1E1E)

    # Layout params
    match_parent = ViewGroup_LayoutParams.sf_get_MATCH_PARENT()
    wrap_content = ViewGroup_LayoutParams.sf_get_WRAP_CONTENT()

    # 2. Title Label
    title_tv = TextView(activity)
    title_tv.setText("Stratum Native Dashboard")
    title_tv.setTextSize(26.0)
    title_tv.setTextColor(0xFF00FFCC)
    title_tv.setPadding(0, 0, 0, 40)
    app_layout.addView(title_tv)

    # 3. Counter Display
    counter_tv = TextView(activity)
    counter_tv.setText("Button Clicks: 0")
    counter_tv.setTextSize(18.0)
    counter_tv.setTextColor(0xFFFFFFFF)
    counter_tv.setPadding(0, 0, 0, 20)
    app_layout.addView(counter_tv)

    # 4. Action Button
    action_btn = Button(activity)
    action_btn.setText("Increment Counter")
    action_btn.setOnClickListener(on_click_increment)
    app_layout.addView(action_btn)

    # 5. Uptime Label
    uptime_tv = TextView(activity)
    uptime_tv.setText("Service Uptime: 0s")
    uptime_tv.setTextSize(16.0)
    uptime_tv.setTextColor(0xFFAAAAAA)
    uptime_tv.setPadding(0, 40, 0, 0)
    app_layout.addView(uptime_tv)

    # 6. Mount Layout
    stratum.setContentView(activity, app_layout)

    # 7. Start Background Thread
    threading.Thread(target=background_clock, daemon=True).start()

def onDestroy():
    global running
    running = False
```

---

### Example 2: Hardware Sensor Listener (Accelerometer HUD)

Demonstrates registering hardware sensor listeners using multi-method interface dictionaries, unboxing floating-point sensor arrays, and dynamically modifying views.

```python
import stratum

from stratum.android.widget.LinearLayout import LinearLayout
from stratum.android.widget.TextView import TextView
from stratum.android.hardware.SensorManager import SensorManager
from stratum.android.hardware.Sensor import Sensor

# Retain globals
app_layout = None
accel_x_tv = None
accel_y_tv = None
accel_z_tv = None
sensor_manager = None

def on_sensor_changed(event):
    # Unpack float array from native SensorEvent:
    values = event.f_get_values()
    x, y, z = values[0], values[1], values[2]

    # Update labels on UI thread
    accel_x_tv.setText(f"Axis X: {x:+.3f} m/s²")
    accel_y_tv.setText(f"Axis Y: {y:+.3f} m/s²")
    accel_z_tv.setText(f"Axis Z: {z:+.3f} m/s²")

def onCreate():
    global app_layout, accel_x_tv, accel_y_tv, accel_z_tv, sensor_manager
    activity = stratum.getActivity()

    # Layout construction
    app_layout = LinearLayout(activity)
    app_layout.setOrientation(LinearLayout.sf_get_VERTICAL())
    app_layout.setPadding(60, 100, 60, 60)
    app_layout.setBackgroundColor(0xFF0F172A)

    title = TextView(activity)
    title.setText("Live Accelerometer Monitor")
    title.setTextSize(22.0)
    title.setTextColor(0xFF38BDF8)
    title.setPadding(0, 0, 0, 40)
    app_layout.addView(title)

    accel_x_tv = TextView(activity)
    accel_x_tv.setTextSize(18.0)
    accel_x_tv.setTextColor(0xFFF1F5F9)
    app_layout.addView(accel_x_tv)

    accel_y_tv = TextView(activity)
    accel_y_tv.setTextSize(18.0)
    accel_y_tv.setTextColor(0xFFF1F5F9)
    app_layout.addView(accel_y_tv)

    accel_z_tv = TextView(activity)
    accel_z_tv.setTextSize(18.0)
    accel_z_tv.setTextColor(0xFFF1F5F9)
    app_layout.addView(accel_z_tv)

    stratum.setContentView(activity, app_layout)

    # Obtain SensorManager
    raw_service = activity.getSystemService("sensor")
    sensor_manager = SensorManager.from_ptr(raw_service)

    if sensor_manager:
        # TYPE_ACCELEROMETER = 1
        accelerometer = sensor_manager.getDefaultSensor(1)
        if accelerometer:
            sensor_manager.registerListener({
                "onSensorChanged": on_sensor_changed,
                "onAccuracyChanged": lambda sensor, accuracy: None,
            }, accelerometer, 3)  # SENSOR_DELAY_NORMAL = 3
```

---

### Example 3: Interactive Touch Vector Canvas (`CustomCanvasView`)

Demonstrates interactive 2D geometry drawing, touch coordinate tracking, and immediate frame invalidation.

```python
import stratum
from stratum.ui import CustomCanvasView
from stratum.android.graphics.Paint import Paint
from stratum.android.graphics.Paint_Style import Paint_Style

class RadarView(CustomCanvasView):
    def __init__(self, activity):
        super().__init__(activity)

        # Background grid paint
        self.grid_paint = Paint()
        self.grid_paint.setAntiAlias(True)
        self.grid_paint.setColor(0xFF006633)
        self.grid_paint.setStrokeWidth(3.0)
        self.grid_paint.setStyle(Paint_Style.sf_get_STROKE())

        # Primary reticle paint
        self.reticle_paint = Paint()
        self.reticle_paint.setAntiAlias(True)
        self.reticle_paint.setColor(0xFF00FF66)
        self.reticle_paint.setStrokeWidth(5.0)
        self.reticle_paint.setStyle(Paint_Style.sf_get_STROKE())

        # Touch indicator blip paint
        self.blip_paint = Paint()
        self.blip_paint.setAntiAlias(True)
        self.blip_paint.setColor(0x8800FF66)
        self.blip_paint.setStyle(Paint_Style.sf_get_FILL())

        # Text paint
        self.text_paint = Paint()
        self.text_paint.setAntiAlias(True)
        self.text_paint.setColor(0xFF00FF66)
        self.text_paint.setTextSize(36.0)

        # Default center
        self.touch_x = 400.0
        self.touch_y = 600.0

    def on_draw(self, canvas):
        # 1. Fill dark canvas background
        canvas.drawColor(0xFF0A0F0D)

        w = float(self.getWidth()) if self.getWidth() > 0 else 800.0
        h = float(self.getHeight()) if self.getHeight() > 0 else 1200.0
        cx = w / 2.0
        cy = h / 2.0

        # 2. Draw concentric radar range circles
        for radius in (150.0, 300.0, 450.0, 600.0):
            canvas.drawCircle(cx, cy, radius, self.grid_paint)

        # Draw crosshairs
        canvas.drawLine(cx, cy - 650.0, cx, cy + 650.0, self.grid_paint)
        canvas.drawLine(cx - 450.0, cy, cx + 450.0, cy, self.grid_paint)

        # 3. Draw active touch target
        canvas.drawCircle(self.touch_x, self.touch_y, 50.0, self.blip_paint)
        canvas.drawCircle(self.touch_x, self.touch_y, 50.0, self.reticle_paint)
        canvas.drawCircle(self.touch_x, self.touch_y, 8.0, self.reticle_paint)

        # 4. Draw HUD text metrics
        canvas.drawText("TACTICAL TOUCH MONITOR", 50.0, 100.0, self.text_paint)
        canvas.drawText(f"X: {int(self.touch_x)}  Y: {int(self.touch_y)}", 50.0, 160.0, self.text_paint)

    def on_touch_event(self, event) -> bool:
        self.touch_x = float(event.getX())
        self.touch_y = float(event.getY())
        self.invalidate()  # Trigger on_draw immediately
        return True

# Retain globally
canvas_app = None

def onCreate():
    global canvas_app
    activity = stratum.getActivity()
    canvas_app = RadarView(activity)
    stratum.setContentView(activity, canvas_app)
```

---

### Example 4: Camera2 Hardware Preview Pipeline

Demonstrates controlling device camera hardware: handling `TextureView` surface callbacks, calculating coordinate transforms, opening a `CameraDevice`, and initiating a `CameraCaptureSession`.

```python
import stratum

from stratum.android.view.TextureView import TextureView
from stratum.android.view.Surface import Surface
from stratum.android.graphics.SurfaceTexture import SurfaceTexture
from stratum.android.graphics.Matrix import Matrix
from stratum.android.hardware.camera2.CameraManager import CameraManager
from stratum.android.hardware.camera2.CameraDevice import CameraDevice
from stratum.android.hardware.camera2.CaptureRequest import CaptureRequest
from stratum.android.hardware.camera2.CameraCaptureSession import CameraCaptureSession
from stratum.android.os.Looper import Looper
from stratum.android.os.Handler import Handler

# Retained handles
viewfinder = None
camera_device = None
capture_session = None
handler = None

def on_camera_opened(device):
    global camera_device, capture_session
    camera_device = CameraDevice.from_ptr(device)

    # 1. Acquire Surface from TextureView
    st = SurfaceTexture.from_ptr(viewfinder.getSurfaceTexture())
    st.setDefaultBufferSize(1280, 720)
    surface = Surface(st)

    # 2. Build Repeating CaptureRequest
    # TEMPLATE_PREVIEW = 1
    builder = camera_device.createCaptureRequest(1)
    builder.addTarget(surface)

    # CONTROL_AF_MODE_CONTINUOUS_PICTURE = 4
    af_mode_key = CaptureRequest.sf_get_CONTROL_AF_MODE()
    builder.set(af_mode_key, 4)

    def on_session_configured(session):
        global capture_session
        capture_session = CameraCaptureSession.from_ptr(session)
        # Start repeating preview request
        capture_session.setRepeatingRequest(builder.build(), None, handler)

    # 3. Create CameraCaptureSession
    camera_device.createCaptureSession([surface], {
        "onConfigured": on_session_configured,
        "onConfigureFailed": lambda s: None,
    }, handler)

def on_surface_available(surface_texture, width, height):
    global handler
    activity = stratum.getActivity()

    # Main Looper Handler
    handler = Handler(Looper.getMainLooper())

    # Obtain CameraManager
    raw_mgr = activity.getSystemService("camera")
    cam_mgr = CameraManager.from_ptr(raw_mgr)

    # Get camera ID list
    cam_ids = cam_mgr.getCameraIdList()
    if len(cam_ids) > 0:
        primary_camera = str(cam_ids[0])
        # Open hardware camera
        cam_mgr.openCamera(primary_camera, {
            "onOpened": on_camera_opened,
            "onDisconnected": lambda dev: None,
            "onError": lambda dev, err: None,
        }, handler)

def onCreate():
    global viewfinder
    activity = stratum.getActivity()

    viewfinder = TextureView(activity)
    viewfinder.setSurfaceTextureListener({
        "onSurfaceTextureAvailable": on_surface_available,
        "onSurfaceTextureSizeChanged": lambda st, w, h: None,
        "onSurfaceTextureDestroyed": lambda st: True,
        "onSurfaceTextureUpdated": lambda st: None,
    })

    stratum.setContentView(activity, viewfinder)

def onDestroy():
    global capture_session, camera_device
    if capture_session:
        capture_session.close()
    if camera_device:
        camera_device.close()
```