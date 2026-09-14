

# Stratum Python API Guide

> **This is a usage guide, not a build guide.** It assumes you already have a Stratum wheel installed in a Chaquopy Android project (see the main [`README.md`](README.md) for that). Everything below is about **how to write Python code against the Stratum API**, verified against the actual generated code.

If something behaves unexpectedly, check [Troubleshooting](#16-troubleshooting--common-pitfalls) before assuming it's a bug — the failure modes below cover the overwhelming majority of real issues people hit.

---

## Table of Contents

1. [Getting Started](#1-getting-started)
2. [Core Concept: StratumObject](#2-core-concept-stratumobject)
3. [Importing Classes & Naming Conventions](#3-importing-classes--naming-conventions)
4. [Method Calls, Overloads & Varargs](#4-method-calls-overloads--varargs)
5. [Fields & Constants](#5-fields--constants)
6. [Type Marshaling](#6-type-marshaling)
7. [Event Listeners & Callbacks](#7-event-listeners--callbacks)
8. [Threading & the UI Thread](#8-threading--the-ui-thread)
9. [Custom 2D Views (`CustomCanvasView`)](#9-custom-2d-views-customcanvasview)
10. [Direct ByteBuffers & Zero-Copy Memory](#10-direct-bytebuffers--zero-copy-memory)
11. [Casting & Object Identity](#11-casting--object-identity)
12. [Calling Python from Java](#12-calling-python-from-java)
13. [The Reflection Escape Hatch](#13-the-reflection-escape-hatch)
14. [XML Layouts](#14-xml-layouts-read-this-before-using)
15. [Worked Examples (Known-Good)](#15-worked-examples-known-good)
16. [Troubleshooting & Common Pitfalls](#16-troubleshooting--common-pitfalls)
17. [Quick Reference Cheat Sheet](#17-quick-reference-cheat-sheet)

---

## 1. Getting Started

Every Stratum app has a `main.py` with recognized top-level lifecycle functions. Stratum discovers and wires these automatically on launch — you don't call them yourself.

```python
import stratum

def onCreate():
    """Called once, when the Activity is first created. Build your UI here."""
    activity = stratum.get_activity()
    # ...

def onResume():   pass   # App is interactive
def onPause():    pass   # App losing focus
def onStop():     pass   # App no longer visible
def onDestroy():  pass   # Activity is being torn down

def onBackPressed() -> bool:
    """Return True to consume the back button yourself; False for default behavior."""
    return False
```

### Top-level module functions

| Function | Description |
|---|---|
| `stratum.get_activity()` / `getActivity()` | Current `Activity`, wrapped as `stratum.android.app.Activity`. `None` before `onCreate`. |
| `stratum.setContentView(activity, view)` / `set_content_view(...)` | Mounts a `View` as the Activity's root content view. |
| `stratum.set_log_enabled(bool)` | Toggles deep native/JNI logcat tracing at runtime (only meaningful if the engine was compiled with logging support — see Troubleshooting). |
| `stratum.run_on_ui_thread(fn, *args, **kwargs)` | Posts a call to the main looper. Fire-and-forget — see [§8](#8-threading--the-ui-thread). |
| `@stratum.ui_thread` | Decorator version of the above. Also fire-and-forget. |
| `stratum.to_java(obj)` / `stratum.to_py(obj)` | Deep-convert Python ⇄ Java data structures. |
| `stratum.to_java_array(items, "fully.qualified.Type")` | Build a strongly-typed Java array. |
| `@stratum.export` | Expose a Python function to Java. |
| `stratum.register_callback(key, fn)` | Low-level callback registration (used internally by `stratum.ui`). Not normally needed directly. |
| `stratum.remove_callback(key)` / `remove_callbacks_by_prefix(prefix)` / `callback_count()` | Manual callback bookkeeping — see [§7](#managing-callback-memory). |
| `stratum.stratum_cast(obj, TargetClass)` | Safe downcast with `instanceof` verification. |
| `stratum.allocate_direct_buffer(capacity)` | Allocate an off-heap `java.nio.ByteBuffer` — see [§10](#10-direct-bytebuffers--zero-copy-memory). |
| `stratum.surface_to_native_window(surface)` / `release_native_window(win_ptr)` | Native rendering handle — see [§10](#10-direct-bytebuffers--zero-copy-memory). |

---

## 2. Core Concept: StratumObject

Every wrapped Java object is a Python instance of a class deriving from `StratumObject`. It holds exactly one thing: `_ptr`, an integer JNI global-reference handle.

```python
tv = TextView(activity)
print(tv._ptr)       # the underlying JNI global ref, as an int
print(tv)             # calls Java toString()
tv == other_view       # JNI IsSameObject, not Python identity
hash(tv)               # Java hashCode()
```

**Lifetime rule:** when a Python wrapper is garbage-collected, its `__del__` does two things:

1. Releases the JNI global reference it held.
2. **Unregisters every callback that was attached *through that specific object*.** Stratum tracks callbacks internally with a key tied to the object's pointer, and clears all of them the moment the object is destroyed.

That second point is the important one. It means: if you build a `SensorManager`, `CameraDevice`, `Handler`, or a view you plan to keep updating, and you don't keep a live Python reference to it (a module-level `global`, a list, an attribute on something long-lived), two things go wrong the moment it's collected — you lose the ability to call methods on it from Python, **and any listener you registered through it silently stops firing, with no error raised.** See [Golden Rule](#golden-rule-retain-everything-you-need-later) in Troubleshooting.

---

## 3. Importing Classes & Naming Conventions

Import paths mirror the Android SDK package structure under `stratum.`, one module per class:

```python
from stratum.android.widget.TextView import TextView
from stratum.android.widget.Button import Button
from stratum.android.widget.LinearLayout import LinearLayout
from stratum.android.view.View import View
from stratum.android.content.Intent import Intent
from stratum.android.graphics.Bitmap import Bitmap
```

> ⚠️ **Always import the class from its own module like this.** A shortcut like `from stratum.android.widget import Button, TextView` (importing straight from the package) is **not reliable** — depending on import order it can bind the *module* object instead of the *class*, which will fail when you try to call it. Always use the fully-qualified `from stratum.<pkg>.<ClassName> import <ClassName>` form shown above.

### Inner classes use `_` instead of `$`

Java nested types (`Paint.Style`, `View.OnClickListener`, …) are compiled as `_`-joined classes:

| Java | Stratum import |
|---|---|
| `android.graphics.Paint.Style` | `from stratum.android.graphics.Paint_Style import Paint_Style` |
| `android.view.View.OnClickListener` | `from stratum.android.view.View_OnClickListener import View_OnClickListener` |
| `android.view.ViewGroup.LayoutParams` | `from stratum.android.view.ViewGroup_LayoutParams import ViewGroup_LayoutParams` |
| `android.hardware.camera2.CameraDevice.StateCallback` | `from stratum.android.hardware.camera2.CameraDevice_StateCallback import CameraDevice_StateCallback` |

> ⚠️ `Paint.Style` (dot notation) **will not work** — it raises `AttributeError`. Always import the underscore form directly.

---

## 4. Method Calls, Overloads & Varargs

### camelCase *and* snake_case both work

```python
tv.setText("Status: Active")
tv.set_text("Status: Active")     # identical call, PEP 8 alias

tv.setVisibility(View.sf_get_VISIBLE())
tv.set_visibility(View.sf_get_VISIBLE())
```

### Overloads resolve automatically by argument shape

```python
layout.addView(my_button)                       # addView(View)
layout.addView(my_button, 0)                     # addView(View, int)
layout.addView(my_button, custom_layout_params)   # addView(View, LayoutParams)
```

Disambiguation checks argument **count** first, then the type of the first argument where the candidate overloads actually differ (primitive vs. string vs. boolean vs. another wrapped Java type, verified with a real `instanceof` check where possible). See [Troubleshooting](#overload-picked-the-wrong-method) if a call ever seems to hit the wrong overload.

### Varargs

Java `int...`, `String...`, `Object...`, and primitive-array varargs parameters accept plain unpacked Python arguments — no need to build an array yourself:

```python
from stratum.android.animation.ValueAnimator import ValueAnimator
animator = ValueAnimator.ofInt(0, 100, 250, 500)   # Java: ofInt(int... values)
```

### Constructors

```python
tv = TextView(activity)          # calls the matching Java constructor
```

If a class has **no public/protected constructor at all** (common for system classes obtained only via a factory — `CameraDevice`, `MediaCodec`, `Window`, …), constructing it directly raises `TypeError` telling you to use the relevant factory/service call instead. Use `activity.getSystemService(...)` or the appropriate `on*Opened`/`on*Created` callback for those classes.

If the class *does* have constructors but you call it with an argument count that matches **none** of them, you'll get a clear native error about an argument-count mismatch rather than a Python-level `TypeError` — either way, the fix is to check the constructor signature you're targeting.

---

## 5. Fields & Constants

Static constants use `sf_get_*` / `sf_set_*`; instance fields use `f_get_*` / `f_set_*`. Only mutable (non-`final`) fields get a setter.

```python
from stratum.android.view.View import View
from stratum.android.widget.LinearLayout import LinearLayout
from stratum.android.graphics.Color import Color
from stratum.android.view.ViewGroup_LayoutParams import ViewGroup_LayoutParams

visible      = View.sf_get_VISIBLE()
gone         = View.sf_get_GONE()
vertical     = LinearLayout.sf_get_VERTICAL()
match_parent = ViewGroup_LayoutParams.sf_get_MATCH_PARENT()
color_red    = Color.sf_get_RED()
```

```python
# Assume `event` here is already a wrapped SensorEvent object
# (see §7 for why callback parameters sometimes need wrapping first!)
vals = event.f_get_values()          # float[] -> list[float]
x, y, z = vals[0], vals[1], vals[2]
timestamp = event.f_get_timestamp()  # long -> int
```

---

## 6. Type Marshaling

Stratum converts types automatically at the call boundary (both directions, for normal method calls and field access):

| Java type | Python argument | Python return |
|---|---|---|
| `boolean` / `Boolean` | `bool` | `bool` |
| `byte`/`short`/`int`/`long` | `int` | `int` |
| `float`/`double` | `float` or `int` | `float` |
| `String` / `CharSequence` | `str` | `str` |
| `byte[]` | `bytes`, `bytearray`, `memoryview` | `bytes` |
| `int[]`/`long[]`/`short[]` | `list[int]` / `tuple[int]` | `list[int]` |
| `float[]`/`double[]` | `list[float]` / `tuple[float]` | `list[float]` |
| `boolean[]` | `list[bool]` / `tuple[bool]` | `list[bool]` |
| `String[]` | `list[str]` / `tuple[str]` | `list[str]` |
| `Object[]` | `list[Any]` / `tuple[Any]` | `list[Any]` |
| `java.util.List`/`Collection` | `list`, `tuple`, `set` | `list` |
| `java.util.Map` | `dict` | `dict` |

This table describes normal **method calls and field access**. It does **not** describe what arrives as a listener/callback *argument* from Java — that's a narrower set of automatic conversions, covered in [§7](#7-event-listeners--callbacks).

### Deep conversion helpers

```python
stratum.to_java(py_obj)   # dict/list/scalars -> real HashMap/ArrayList/boxed primitives
stratum.to_py(java_obj)   # Map/List/Bundle/arrays -> native dict/list/str/int/...
stratum.to_java_array(items, "com.example.Type")   # strongly-typed T[] array
```

Example — building a typed array for an API that needs a concrete element type rather than `Object[]`:

```python
from stratum.android.graphics.Rect import Rect
from stratum.android.hardware.camera2.params.MeteringRectangle import MeteringRectangle

focus_rect = Rect(0, 0, 100, 100)
metering_rect = MeteringRectangle(focus_rect, 1000)

metering_array = stratum.to_java_array(
    [metering_rect],
    "android.hardware.camera2.params.MeteringRectangle"
)
```

---

## 7. Event Listeners & Callbacks

### Single-method listeners → plain function or lambda

```python
btn = Button(activity)

def handle_click(view):
    print("Button pressed!")

btn.setOnClickListener(handle_click)
# or:
btn.setOnClickListener(lambda v: print("Clicked!"))
```

### Multi-method interfaces / abstract callbacks → a `dict` of functions

```python
texture_view.setSurfaceTextureListener({
    "onSurfaceTextureAvailable":   lambda st, w, h: print(f"ready {w}x{h}"),
    "onSurfaceTextureDestroyed":   lambda st: True,   # return value forwarded to Java!
    "onSurfaceTextureSizeChanged": lambda st, w, h: None,
    "onSurfaceTextureUpdated":     lambda st: None,
})
```

The dict's keys must exactly match the Java interface's method names. Every method the interface declares should be present — even as a no-op `lambda *a: None` — since Stratum's generated adapter always overrides all of them.

### ⚠️ Object-typed callback parameters must be wrapped manually

Only primitives (`bool`/`int`/`long`/`float`/`double`) and `String` are automatically converted when Android calls into a Python listener. **Any other object-typed parameter — `SensorEvent`, `CameraDevice`, `MotionEvent` on a raw listener, a `Session`, a `View`, etc. — arrives as a plain Python `int` (a raw pointer), not a usable wrapped object.**

Calling a method directly on it fails with exactly this error:

```
AttributeError: 'int' object has no attribute 'f_get_values'
```

**Fix: wrap it yourself, first line of the callback, using `from_ptr` on the concrete class:**

```python
from stratum.android.hardware.SensorEvent import SensorEvent

def on_sensor_changed(raw_event):
    event = SensorEvent.from_ptr(raw_event)   # <-- wrap before using
    values = event.f_get_values()
    ...
```

If you don't know (or don't want to import) the concrete class, wrap by fully-qualified name instead:

```python
from stratum.core.stratum_object import _wrap_instance

def on_sensor_changed(raw_event):
    event = _wrap_instance(raw_event, "android.hardware.SensorEvent")
    values = event.f_get_values()
    ...
```

`None` is passed through as `None` (not `0`), so a truthiness/`is None` check is safe before wrapping if a parameter is nullable.

This is exactly what the Camera2 example does with `CameraDevice.from_ptr(device)` and `CameraCaptureSession.from_ptr(session)` inside its `onOpened`/`onConfigured` callbacks — copy that pattern for **every** object-typed listener parameter.

> **Exception — `stratum.ui.CustomCanvasView`:** the `canvas` parameter in `on_draw`, and the `event` parameter in `on_touch_event`, are **already wrapped for you automatically** by that helper class. The manual-wrapping rule above applies to listeners you register yourself (`setOnClickListener`, `setSurfaceTextureListener`, `registerListener`, and similar), not to `CustomCanvasView`'s own override methods. See [§9](#9-custom-2d-views-customcanvasview).

### Return values matter

If the Java callback expects a `boolean` (or other) return — `onTouch`, `onLongClick`, `Comparator.compare`, `onSurfaceTextureDestroyed` — your Python function's return value **is forwarded back to Java** and can change behavior (e.g. whether a touch/gesture is consumed):

```python
def on_view_touch(view, motion_event) -> bool:
    if motion_event.getAction() == 0:   # ACTION_DOWN
        return True    # consume the event
    return False        # let it propagate
```

### Keeping a listener alive

A Python closure/lambda passed as a listener is kept alive natively for as long as the *object you attached it to* is alive. You don't need to manually retain the function itself — but you **do** need to keep the object you called `setXxxListener(...)`/`registerListener(...)` on reachable from Python (see [Golden Rule](#golden-rule-retain-everything-you-need-later)), or the listener is silently unregistered when that object is garbage-collected.

### <a name="managing-callback-memory"></a>Managing callback memory (advanced)

Every listener you register consumes an entry in a native table that only frees itself when its owning object is garbage-collected (or you unregister it yourself). If your app registers a very large number of short-lived listeners, you can check and manage this manually:

```python
stratum.callback_count()                     # how many callbacks are currently retained
stratum.remove_callback(key)                 # release one, if you tracked its key
stratum.remove_callbacks_by_prefix(prefix)   # bulk release
```

This is rarely needed for typical apps — normal object garbage collection handles it — but is available if you're building something long-running with many dynamically created listeners.

---

## 8. Threading & the UI Thread

Android requires all View mutations to happen on the **main looper**. Touching a view from a background thread raises `CalledFromWrongThreadException`.

```python
import threading, time, stratum

def worker_thread():
    time.sleep(2.0)
    stratum.run_on_ui_thread(status_label.setText, "Background task complete")

threading.Thread(target=worker_thread, daemon=True).start()
```

Or the decorator form:

```python
@stratum.ui_thread
def display_alert(message: str, error: bool):
    status_label.setText(message)
    status_label.setTextColor(0xFFFF0000 if error else 0xFF00FF00)

display_alert("Sync finished", error=False)   # safe from any thread
```

> **Both forms are fire-and-forget.** The call is *posted* to the main looper and executed asynchronously — `run_on_ui_thread(...)` and a `@stratum.ui_thread`-decorated function both return `None` immediately, not whatever the wrapped function returns. Don't rely on getting a return value back from code run this way; have the function update shared state or call another callback instead if you need a result.

> A listener callback invoked **directly by Android** (`onClick`, `onTouchEvent`, and — when registered without an explicit background `Handler` — sensor callbacks too) already runs *on* the thread that registered it, which is normally the UI thread if you registered from `onCreate`. You only need `run_on_ui_thread` when updating a view from code Android did **not** call for you: your own background `Thread`, a `queue` consumer, a timer, a network response callback, etc.

---

## 9. Custom 2D Views (`CustomCanvasView`)

This is Stratum's most reliable, fully-working feature end-to-end: real hardware-accelerated 2D drawing and touch handling, written entirely in Python, with **no Java view class needed**.

### What you override

| Method | Purpose |
|---|---|
| `on_draw(self, canvas)` | Draw using `android.graphics.Canvas` / `Paint`. `canvas` is already a wrapped object — no manual `from_ptr` needed. Called whenever the view needs to redraw. |
| `on_touch_event(self, event) -> bool` | Handle a `MotionEvent`. `event` is already a wrapped object. Return `True` to consume the gesture. |
| `on_measure(self, width_spec, height_spec)` | *(optional)* `width_spec`/`height_spec` are plain ints. Return `[width, height]` (a list or tuple of two plain ints) to report a custom measured size, or `None` to use the default. |
| `on_layout(self, changed, left, top, right, bottom)` | *(optional)* Custom layout of children. |
| `self.invalidate()` | Request an immediate redraw (call after any state change you want reflected on screen). |

Because `CustomCanvasView` is itself a `View` under the hood, real `View` methods (`setPadding`, `getWidth`, `setRotation`, etc.) are available directly on `self` — they're transparently delegated to the underlying Java `View`.

### Example: touch-driven canvas *(fully working)*

```python
import stratum
from stratum.ui import CustomCanvasView
from stratum.android.graphics.Paint import Paint
from stratum.android.graphics.Paint_Style import Paint_Style

class ReticleView(CustomCanvasView):
    def __init__(self, activity):
        super().__init__(activity)
        self.ring_paint = Paint()
        self.ring_paint.setAntiAlias(True)
        self.ring_paint.setColor(0xFF00FFCC)
        self.ring_paint.setStrokeWidth(4.0)
        self.ring_paint.setStyle(Paint_Style.sf_get_STROKE())

        self.x = 300.0
        self.y = 500.0

    def on_draw(self, canvas):
        canvas.drawColor(0xFF121212)
        canvas.drawCircle(self.x, self.y, 75.0, self.ring_paint)
        canvas.drawLine(self.x - 100.0, self.y, self.x + 100.0, self.y, self.ring_paint)
        canvas.drawLine(self.x, self.y - 100.0, self.x, self.y + 100.0, self.ring_paint)

    def on_touch_event(self, event) -> bool:
        self.x = float(event.getX())
        self.y = float(event.getY())
        self.invalidate()   # redraw immediately with the new touch position
        return True

# Module-level global — required, see "Retain Your Roots" below
reticle_view = None

def onCreate():
    global reticle_view
    activity = stratum.getActivity()
    reticle_view = ReticleView(activity)
    stratum.setContentView(activity, reticle_view)
```

Run this, tap/drag on screen — the ring follows your finger in real time. This pattern (state on `self`, mutate in a callback, call `self.invalidate()`) is the recommended way to build *any* interactive Stratum UI element, and is currently more reliable than driving a stock `TextView`/`Button` tree for anything beyond static layout — see [Troubleshooting](#a-view-doesnt-update-after-the-initial-draw).

> `CustomCanvasView` must be constructed after the Activity is available (i.e. inside or after `onCreate()`) — constructing it too early raises `RuntimeError: Stratum: failed to construct StratumView (activity not ready?)`.

---

## 10. Direct ByteBuffers & Zero-Copy Memory

For large buffers (audio PCM, camera frames, bitmap pixels), Stratum maps native memory directly into a Python `memoryview` — no JNI copy.

```python
import stratum

byte_buffer = stratum.allocate_direct_buffer(1024 * 1024)          # 1 MB off-heap, wrapped object
raw_view = stratum._stratum.bytebuffer_to_memoryview(byte_buffer._ptr)

print(len(raw_view))
raw_view[0:4] = b"\x00\xFF\x00\xFF"   # writes directly into Java-visible memory
```

`stratum._stratum` here is the underlying native extension module (its Python-facing name starts with an underscore because it's normally an implementation detail) — `bytebuffer_to_memoryview` has no separate high-level wrapper, so this direct call is the documented way to use it.

Bitmap pixel round-trip:

```python
byte_buffer.rewind()
bitmap.copyPixelsToBuffer(byte_buffer)

view = stratum._stratum.bytebuffer_to_memoryview(byte_buffer._ptr)
first_pixel_alpha = view[3]

byte_buffer.rewind()
bitmap.copyPixelsFromBuffer(byte_buffer)
```

Native surface windows (for direct rendering pipelines):

```python
win_ptr = stratum.surface_to_native_window(surface)
# ... use with native rendering code ...
stratum.release_native_window(win_ptr)   # always release when done
```

---

## 11. Casting & Object Identity

### Downcasting

```python
from stratum.android.hardware.SensorManager import SensorManager

raw_service = activity.getSystemService("sensor")
sensor_mgr = SensorManager.from_ptr(raw_service)          # verified via IsInstanceOf
# equivalent:
sensor_mgr = stratum.stratum_cast(raw_service, SensorManager)
```

`from_ptr` / `stratum_cast` raise `TypeError` if the underlying object isn't actually an instance of the target class — this is a real JNI check, not a blind cast. This is also the exact mechanism used to fix the raw-pointer callback problem described in [§7](#-object-typed-callback-parameters-must-be-wrapped-manually).

### Identity vs. equality

```python
child1 = layout.getChildAt(0)
child2 = layout.getChildAt(0)
child1 == child2   # True — same underlying Java object (JNI IsSameObject), even though
                     # child1 and child2 are two *different* Python wrapper instances
```

---

## 12. Calling Python from Java

If you have hand-written Java in your Android Studio project, it can call back into Python:

```python
# main.py
import stratum

@stratum.export
def process_message(sender: str, message: str) -> str:
    print(f"Message from {sender}: {message}")
    return f"Acknowledged: {message.upper()}"
```

```java
// your custom Java class
import com.stratum.runtime.StratumRuntimeLookup;

Object result = StratumRuntimeLookup.callPython("process_message", "Alice", "System check");
// result == "Acknowledged: SYSTEM CHECK"
```

---

## 13. The Reflection Escape Hatch

For classes excluded from your build (custom `.java` files, or SDK members filtered out of the pipeline run), `stratum.reflect` calls them dynamically without touching the pipeline:

```python
from stratum import reflect

result = reflect.call_java_static(
    "com.example.utils.EncryptionHelper", "sha256", "my_payload_data"
)

output = reflect.call_java_method(
    my_java_instance, "performCustomAction", 100, True
)
```

This is slower than a normal Stratum call (real Java reflection under the hood) — use it for occasional/one-off calls, not hot paths. It also requires the classes `java.lang.Class`, `java.lang.ClassLoader`, and `java.lang.reflect.*` to have been included in your build; if `stratum.reflect` itself fails to import a needed class, the error message tells you which FQN to add to `05_resolve/targets.json`.

---

## 14. XML Layouts (read this before using)

Stratum can inflate a `res/layout/*.xml` file the same way native Android code does:

```python
import stratum
from stratum.android.widget.FrameLayout import FrameLayout
from stratum.android.widget.TextView import TextView

def get_res_id(activity, res_name: str, res_type: str = "id") -> int:
    res = activity.getResources()
    pkg = activity.getPackageName()
    res_id = res.getIdentifier(res_name, res_type, pkg)
    if res_id == 0:
        raise KeyError(f"Resource '{res_name}' of type '{res_type}' not found.")
    return res_id

def onCreate():
    activity = stratum.getActivity()
    inflater = activity.getLayoutInflater()

    layout_id = get_res_id(activity, "activity_main", "layout")
    root_view = FrameLayout.from_ptr(inflater.inflate(layout_id, None, False))
    stratum.setContentView(activity, root_view)

    text_view_id = get_res_id(activity, "title_text", "id")
    title_tv = TextView.from_ptr(root_view.findViewById(text_view_id))
    title_tv.setText("Bound via Stratum XML Inflation")
```

> ⚠️ **This path is currently the least battle-tested part of the API and has been observed to crash in some projects.** Before filing an issue, check every item below — in practice, most "XML inflate crash" reports trace back to one of these:
>
> 1. **`res_name` must be the *bare* resource name with no prefix** — `"activity_main"`, not `"R.layout.activity_main"` and not `"@layout/activity_main"`.
> 2. **The resource must actually be compiled into the APK's `R` class** for the exact `applicationId`/package `activity.getPackageName()` returns — a resource that only exists in a different `productFlavor`/`buildType` than the one installed resolves to `0` and raises `KeyError` here (that's the intended, safe failure mode — if you instead see a native crash, the inflate got *past* ID resolution and something else is wrong).
> 3. **Every custom View referenced by the XML must be a real Java class the inflater can construct via `(Context, AttributeSet)`.** A layout that references a Stratum-only Python view (like `CustomCanvasView`) **cannot** be inflated from XML — Python classes have no Java bytecode for the inflater to instantiate. Build those parts of the tree programmatically (see [§9](#9-custom-2d-views-customcanvasview)) and inflate only the parts that use stock Android widgets.
> 4. **`from_ptr(...)` needs the target class to actually be part of your build.** If it was excluded from `05_resolve/targets.json` when the wheel was built, `from_ptr` falls back to a generic `StratumObject` (not a crash) — but downstream code expecting `TextView`-specific methods on that fallback will then fail with `AttributeError`.
> 5. If you still see a native crash (not a Python exception) on inflate, capture `adb logcat` around the crash and narrow the XML down by commenting out children until it disappears, then check that specific widget's constructor/attributes.
>
> **Until this is fully hardened, building layouts programmatically is the safer path for anything you need to ship today**; treat XML inflation as experimental.

---

## 15. Worked Examples (Known-Good)

These are verified working end-to-end and are good starting points to copy from.

### Example A — Interactive Touch Canvas *(fully working)*
See [§9](#example-touch-driven-canvas-fully-working) above — `ReticleView`-style drawing + touch tracking with `invalidate()`.

### Example B — Hardware Sensor Listener (Accelerometer)

```python
import stratum
from stratum.android.widget.LinearLayout import LinearLayout
from stratum.android.widget.TextView import TextView
from stratum.android.hardware.SensorManager import SensorManager
from stratum.android.hardware.SensorEvent import SensorEvent

app_layout = None
accel_x_tv = None
accel_y_tv = None
accel_z_tv = None
sensor_manager = None   # MUST stay a global — see §2 / Golden Rule

def on_sensor_changed(raw_event):
    # raw_event arrives as a bare int (a raw JNI pointer), not a usable
    # SensorEvent object — wrap it first. See §7.
    event = SensorEvent.from_ptr(raw_event)
    values = event.f_get_values()
    x, y, z = values[0], values[1], values[2]

    # Registered without an explicit background Handler, so this callback
    # runs on the thread that called registerListener() — the UI thread,
    # since registration happens inside onCreate(). Direct setText() calls
    # are therefore safe here. If you ever register with a background
    # Handler instead, wrap these calls in stratum.run_on_ui_thread(...).
    accel_x_tv.setText(f"Axis X: {x:+.3f} m/s^2")
    accel_y_tv.setText(f"Axis Y: {y:+.3f} m/s^2")
    accel_z_tv.setText(f"Axis Z: {z:+.3f} m/s^2")

def onCreate():
    global app_layout, accel_x_tv, accel_y_tv, accel_z_tv, sensor_manager
    activity = stratum.getActivity()

    app_layout = LinearLayout(activity)
    app_layout.setOrientation(LinearLayout.sf_get_VERTICAL())
    app_layout.setPadding(60, 100, 60, 60)

    accel_x_tv = TextView(activity); app_layout.addView(accel_x_tv)
    accel_y_tv = TextView(activity); app_layout.addView(accel_y_tv)
    accel_z_tv = TextView(activity); app_layout.addView(accel_z_tv)

    stratum.setContentView(activity, app_layout)

    raw_service = activity.getSystemService("sensor")
    sensor_manager = SensorManager.from_ptr(raw_service)
    if sensor_manager:
        accelerometer = sensor_manager.getDefaultSensor(1)   # TYPE_ACCELEROMETER
        if accelerometer:
            sensor_manager.registerListener({
                "onSensorChanged": on_sensor_changed,
                "onAccuracyChanged": lambda sensor, accuracy: None,
            }, accelerometer, 3)   # SENSOR_DELAY_NORMAL
```

### Example C — Camera2 Preview (why `from_ptr` shows up in the official demo)

The Camera2 example elsewhere in this repo wraps every object-typed callback parameter as soon as it receives it:

```python
def on_camera_opened(device):
    camera_device = CameraDevice.from_ptr(device)      # device arrives as a raw int

def on_session_configured(session):
    capture_session = CameraCaptureSession.from_ptr(session)  # same reason
```

This is the same rule as Example B — `device` and `session` are object-typed parameters delivered by a Java callback, so they arrive unwrapped. There's nothing camera-specific about it; the same fix applies to *every* listener callback parameter that isn't a primitive or a `String`.

**The pattern that reliably works, across all three examples:** module-level globals for every long-lived object, a plain function/lambda or dict-of-functions for the listener, `from_ptr`/`_wrap_instance` on the first line of any callback that receives an object parameter, and `self.invalidate()` / direct `setText` calls from inside the callback rather than from an unrelated background loop.

---

## 16. Troubleshooting & Common Pitfalls

### Golden Rule: retain everything you need later
When a Python wrapper is garbage-collected, it releases its JNI reference **and unregisters every callback that was attached through it.** So anything you build in `onCreate` and expect to still be working later — a view you'll keep updating, a `SensorManager`, a `CameraDevice`, a `Handler` — must be kept reachable: a module-level `global`, an attribute on a long-lived object, or an entry in a list you don't clear. If a listener "randomly stops firing" a few seconds after registration with no error, this is the first thing to check.

```python
# RISKY — nothing keeps `sensor_manager` alive after onCreate() returns,
# so its listener registration can silently be torn down later.
def onCreate():
    activity = stratum.getActivity()
    raw = activity.getSystemService("sensor")
    sensor_manager = SensorManager.from_ptr(raw)
    sensor_manager.registerListener({...}, accel, 3)

# CORRECT
sensor_manager = None
def onCreate():
    global sensor_manager
    activity = stratum.getActivity()
    raw = activity.getSystemService("sensor")
    sensor_manager = SensorManager.from_ptr(raw)
    sensor_manager.registerListener({...}, accel, 3)
```

### A view doesn't update after the initial draw
If a `setText()`/`setXxx()` call from inside a click handler or a background-thread callback doesn't visibly update the screen:

1. **Check the object is kept alive** (Golden Rule above) — a collected-and-recreated or collected-listener-owner will silently no-op.
2. **Check the callback actually fired** — add a `print(...)` at the top of the handler. If it never prints, the listener isn't firing (often an object-lifetime issue on the object you registered it on, not the widget you're trying to update).
3. **If the update comes from a non-Android-driven thread** (your own `threading.Thread`, a timer, a queue consumer), it **must** go through `stratum.run_on_ui_thread(...)` — a cross-thread view mutation can fail silently depending on device/OS version instead of raising a catchable Python exception.
4. **Check `adb logcat`**, not just your Python process's stdout — a Python exception raised inside a callback is logged there (`Stratum` tag) and can crash the app via a native `RuntimeException`, as shown in the sensor example crash log at the top of this guide. Filter with `adb logcat -s Stratum StratumTrace`.
5. If a listener parameter is object-typed, confirm you wrapped it with `from_ptr`/`_wrap_instance` — an un-wrapped raw int silently fails differently (an `AttributeError`, which *will* show up in logcat and crash the app — see item 4).
6. As a robustness check, try the `CustomCanvasView` pattern from [§9](#9-custom-2d-views-customcanvasview) for that piece of UI — since that code path is the most exercised part of Stratum, it's a good way to rule in/out whether the issue is specific to the standard widget tree.

### `AttributeError: 'int' object has no attribute 'f_get_...'` / `'...setXxx'` inside a listener
You're calling a method on a callback parameter that Stratum handed you as a raw pointer `int` instead of a wrapped object. This happens for **every** non-primitive, non-`String` parameter delivered to a listener you registered (`SensorEvent`, `CameraDevice`, a `Session`, etc.) — see [§7](#-object-typed-callback-parameters-must-be-wrapped-manually). Fix: wrap the parameter with `YourClass.from_ptr(raw_value)` (or `_wrap_instance(raw_value, "fully.qualified.Name")`) as the very first line of the callback, before touching it. Parameters delivered to `CustomCanvasView`'s own `on_draw`/`on_touch_event` are the one exception — those arrive pre-wrapped.

### Overload picked the "wrong" method
Overload resolution checks argument **count** first, then the concrete Python type of the first argument that actually differs between candidates. If a method with many overloads behaves unexpectedly:
- Check you're not passing `True`/`False` where Java expects an `int` (or vice versa) — Python's `bool` is technically an `int` subclass, which Stratum accounts for, but it's a common source of confusion when reading traces.
- Prefer passing values with the type Java actually expects rather than relying on Python's numeric coercion (`1.0` vs `1`) when a class has both an `int` and a `float` overload.

### `IllegalArgumentException` when passing a listener
Java's dynamic proxies can only implement **interfaces**, not abstract classes. If you're passing a Python function/dict to a parameter typed as an **abstract class** (`CameraDevice.StateCallback`, `WebViewClient`, etc.) rather than a plain interface, that class needs a **generated Java adapter** (produced by pipeline Stage 05.5) instead of a dynamic proxy. This is a build-time concern — if you hit it, the class needs to be added to `05_5_abstract/targets.json`'s seed list and the wheel rebuilt.

### A constructor raises `TypeError: ... has no accessible constructor`
This is intentional — some Android classes are only ever obtained through a factory method or system service (`CameraDevice`, `MediaCodec`, `Window`, …), never constructed directly. Use the appropriate `getSystemService(...)` call or the relevant `on*Opened`/`on*Created` callback instead.

### Nothing happens and there's no error at all
Check `adb logcat -s Stratum StratumTrace` — Stratum's native layer logs class-resolution failures, methods unavailable on the current device's API level, and Python exceptions raised inside callbacks there, even when nothing propagates back to a visible Python traceback in your own tooling. A large volume of `method unavailable on this device` / `field unavailable` warnings on startup is normal and harmless — it just means that specific member doesn't exist on your device's Android version; the real problem, if any, is the line marked `E` (error) that follows, if there is one. Enable deep tracing with `stratum.set_log_enabled(True)` (only meaningful if the engine was compiled with logging support) to see every call crossing the Python ⇄ Java boundary.

### XML layouts
See the dedicated warning list in [§14](#14-xml-layouts-read-this-before-using) — this is the most fragile part of the current API. Prefer building view trees programmatically until you've ruled out each item on that list.

---

## 17. Quick Reference Cheat Sheet

```python
# Imports — always fully qualified
from stratum.android.widget.TextView import TextView
from stratum.android.view.View_OnClickListener import View_OnClickListener   # inner class -> "_"

# Construct / call / alias
tv = TextView(activity)
tv.setText("x"); tv.set_text("x")            # same call

# Fields
View.sf_get_VISIBLE()                          # static
event.f_get_values()                           # instance (event must already be wrapped!)

# Listeners
btn.setOnClickListener(lambda v: ...)          # single-method
obj.setListener({"onA": fn_a, "onB": fn_b})     # multi-method

# Object-typed callback params need wrapping — see §7
def on_something(raw_obj):
    obj = SomeClass.from_ptr(raw_obj)

# Threading — both are fire-and-forget, return None immediately
stratum.run_on_ui_thread(view.setText, "x")
@stratum.ui_thread
def safe_update(): ...

# Casting
X.from_ptr(some_object)                         # verified downcast
stratum.stratum_cast(some_object, X)

# Data
stratum.to_java(py_dict_or_list)
stratum.to_py(java_map_or_list)
stratum.to_java_array(items, "java.lang.String")

# Custom drawing — canvas/event are pre-wrapped here, no from_ptr needed
class MyView(stratum.ui.CustomCanvasView):
    def on_draw(self, canvas): ...
    def on_touch_event(self, event) -> bool: ...

# Python <-> Java
@stratum.export
def my_fn(x): return x

from stratum import reflect
reflect.call_java_static("com.example.Foo", "bar", 1, 2)

# Keep every long-lived object as a module-level global — this is the
# single most common source of "it silently stopped working" reports.
```

---

**Still stuck?** Most reported issues to date match one of the patterns in [§16](#16-troubleshooting--common-pitfalls). If none apply, capture `adb logcat -s Stratum StratumTrace` around the failure and include it when reporting the issue.