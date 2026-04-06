# Stratum Python API Reference

> How Android Java APIs look from Python after the Stratum pipeline runs.

After building and installing the wheel, you import `_stratum` and call Android APIs as ordinary Python method calls. This document shows the Python-side equivalent for common Android patterns.

---

## Import and Basic Usage

```python
import _stratum as android

# All Android classes are attributes of the _stratum module
# java.lang.String  → android.String  (simplified)
# android.widget.Button → android.Button
# android.app.Activity  → android.Activity
```

The naming convention follows the class's simple name. Inner classes use the outer class name as a prefix, separated by `_`:

```python
# Java: android.hardware.camera2.CameraDevice.StateCallback
# Python:
android.CameraDevice_StateCallback

# Java: android.view.View.OnClickListener
# Python:
android.View_OnClickListener
```

---

## Activity Lifecycle

**Java:**
```java
public class MainActivity extends AppCompatActivity {
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
    }
}
```

**Python (Stratum entry point):**
```python
# main.py — called by StratumActivity
import _stratum as android

def on_create(activity, saved_instance_state):
    # activity is the Android Activity object passed from Java
    # build your UI here
    pass
```

`StratumActivity` calls your `main.py` and passes the Activity instance. From there you have full access to all bound Android APIs.

---

## Building UI

### LinearLayout with Button and TextView

**Java:**
```java
LinearLayout layout = new LinearLayout(this);
layout.setOrientation(LinearLayout.VERTICAL);

TextView label = new TextView(this);
label.setText("Count: 0");

Button button = new Button(this);
button.setText("Increment");

layout.addView(label);
layout.addView(button);
setContentView(layout);
```

**Python:**
```python
import _stratum as android

def on_create(activity, bundle):
    layout = android.LinearLayout(activity)
    layout.setOrientation(android.LinearLayout.VERTICAL)

    label = android.TextView(activity)
    label.setText("Count: 0")

    button = android.Button(activity)
    button.setText("Increment")

    layout.addView(label)
    layout.addView(button)
    activity.setContentView(layout)
```

---

## Click Listeners

**Java (anonymous class):**
```java
button.setOnClickListener(new View.OnClickListener() {
    @Override
    public void onClick(View v) {
        count++;
        label.setText("Count: " + count);
    }
});
```

**Python:**
```python
count = [0]  # mutable container for closure

def on_click(view):
    count[0] += 1
    label.setText(f"Count: {count[0]}")

button.setOnClickListener(on_click)
```

Stratum wraps the Python callable in an adapter and registers it as the `OnClickListener`. You pass a plain Python function.

---

## Intent / Navigation

**Java:**
```java
Intent intent = new Intent(this, SecondActivity.class);
intent.putExtra("key", "value");
startActivity(intent);
```

**Python:**
```python
intent = android.Intent(activity, android.SecondActivity)
intent.putExtra("key", "value")
activity.startActivity(intent)
```

---

## Bundle

**Java:**
```java
Bundle bundle = new Bundle();
bundle.putString("name", "Alice");
bundle.putInt("score", 42);

String name = bundle.getString("name");
int score = bundle.getInt("score");
```

**Python:**
```python
bundle = android.Bundle()
bundle.putString("name", "Alice")
bundle.putInt("score", 42)

name = bundle.getString("name")   # returns Python str
score = bundle.getInt("score")    # returns Python int
```

---

## Handler / Thread Posting

**Java:**
```java
Handler handler = new Handler(Looper.getMainLooper());
handler.post(new Runnable() {
    @Override
    public void run() {
        textView.setText("Updated from background");
    }
});
```

**Python:**
```python
handler = android.Handler(android.Looper.getMainLooper())

def update_ui():
    text_view.setText("Updated from background")

handler.post(update_ui)
```

---

## Camera2 — with Callbacks

Camera2 requires implementing abstract callback classes. These need Stage 05.5 adapter generation.

**Java:**
```java
CameraManager manager = (CameraManager) getSystemService(Context.CAMERA_SERVICE);
String[] ids = manager.getCameraIdList();

manager.openCamera(ids[0], new CameraDevice.StateCallback() {
    @Override
    public void onOpened(CameraDevice camera) {
        // camera is ready
    }
    @Override
    public void onDisconnected(CameraDevice camera) {}
    @Override
    public void onError(CameraDevice camera, int error) {}
}, handler);
```

**Python:**
```python
manager = activity.getSystemService(android.Context.CAMERA_SERVICE)
camera_ids = manager.getCameraIdList()

def on_camera_opened(camera_device):
    # camera_device is the CameraDevice object
    start_preview(camera_device)

def on_camera_disconnected(camera_device):
    camera_device.close()

def on_camera_error(camera_device, error_code):
    pass

state_callback = android.CameraDevice_StateCallback(
    on_opened=on_camera_opened,
    on_disconnected=on_camera_disconnected,
    on_error=on_camera_error,
)

manager.openCamera(camera_ids[0], state_callback, handler)
```

---

## TextureView and SurfaceTextureListener

**Java:**
```java
textureView.setSurfaceTextureListener(new TextureView.SurfaceTextureListener() {
    @Override
    public void onSurfaceTextureAvailable(SurfaceTexture surface, int w, int h) {}
    @Override
    public void onSurfaceTextureSizeChanged(SurfaceTexture surface, int w, int h) {}
    @Override
    public boolean onSurfaceTextureDestroyed(SurfaceTexture surface) { return true; }
    @Override
    public void onSurfaceTextureUpdated(SurfaceTexture surface) {}
});
```

**Python:**
```python
def on_surface_available(surface_texture, width, height):
    open_camera(surface_texture)

def on_surface_size_changed(surface_texture, width, height):
    pass

def on_surface_destroyed(surface_texture):
    return True

def on_surface_updated(surface_texture):
    pass

listener = android.TextureView_SurfaceTextureListener(
    on_surface_texture_available=on_surface_available,
    on_surface_texture_size_changed=on_surface_size_changed,
    on_surface_texture_destroyed=on_surface_destroyed,
    on_surface_texture_updated=on_surface_updated,
)

texture_view.setSurfaceTextureListener(listener)
```

---

## CaptureRequest

**Java:**
```java
CaptureRequest.Builder builder = cameraDevice.createCaptureRequest(
    CameraDevice.TEMPLATE_PREVIEW
);
builder.addTarget(surface);
CaptureRequest request = builder.build();

captureSession.setRepeatingRequest(request, null, handler);
```

**Python:**
```python
builder = camera_device.createCaptureRequest(android.CameraDevice.TEMPLATE_PREVIEW)
builder.addTarget(surface)
request = builder.build()

capture_session.setRepeatingRequest(request, None, handler)
```

---

## Bitmap

**Java:**
```java
Bitmap bmp = Bitmap.createBitmap(640, 480, Bitmap.Config.ARGB_8888);
int pixel = bmp.getPixel(0, 0);
bmp.setPixel(10, 20, Color.RED);
```

**Python:**
```python
bmp = android.Bitmap.createBitmap(640, 480, android.Bitmap.Config.ARGB_8888)
pixel = bmp.getPixel(0, 0)
bmp.setPixel(10, 20, android.Color.RED)
```

---

## ByteBuffer (for image data)

**Java:**
```java
ByteBuffer buffer = ByteBuffer.allocateDirect(1024);
buffer.put((byte) 0xFF);
buffer.flip();
int remaining = buffer.remaining();
```

**Python:**
```python
buffer = android.ByteBuffer.allocateDirect(1024)
buffer.put(0xFF)
buffer.flip()
remaining = buffer.remaining()
```

For passing to OpenCV or numpy, you typically read the buffer's backing bytes:

```python
import numpy as np

# After filling a ByteBuffer with camera frame data:
byte_array = buffer.array()   # returns Python bytes
frame = np.frombuffer(byte_array, dtype=np.uint8).reshape((height, width, 3))
result = cv2.cvtColor(frame, cv2.COLOR_YUV2BGR_NV21)
```

---

## Color and Gravity constants

**Java:**
```java
view.setBackgroundColor(Color.RED);
layout.setGravity(Gravity.CENTER);
```

**Python:**
```python
view.setBackgroundColor(android.Color.RED)
layout.setGravity(android.Gravity.CENTER)
```

Static fields and constants are accessible as class attributes directly.

---

## LinearLayout.LayoutParams

**Java:**
```java
LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
    ViewGroup.LayoutParams.MATCH_PARENT,
    ViewGroup.LayoutParams.WRAP_CONTENT
);
params.setMargins(0, 16, 0, 16);
view.setLayoutParams(params);
```

**Python:**
```python
MATCH_PARENT = android.ViewGroup_LayoutParams.MATCH_PARENT
WRAP_CONTENT = android.ViewGroup_LayoutParams.WRAP_CONTENT

params = android.LinearLayout_LayoutParams(MATCH_PARENT, WRAP_CONTENT)
params.setMargins(0, 16, 0, 16)
view.setLayoutParams(params)
```

---

## Type Mapping

| Java type | Python type |
|-----------|-------------|
| `String` | `str` |
| `int`, `long`, `short`, `byte` | `int` |
| `float`, `double` | `float` |
| `boolean` | `bool` |
| `void` | `None` |
| `String[]` | `list[str]` |
| `int[]` | `list[int]` |
| Android object (e.g. `View`) | Wrapped Python object (opaque handle) |
| `null` | `None` |

---

## Notes and Limitations (v0.2)

This is v0.2. Not all Android APIs are fully supported yet. Known rough edges:

- Generic methods (e.g., methods returning `T` or `List<T>`) may fail to bind or return opaque objects.
- Some inner class constructors may not be available depending on how javap exposed them.
- Methods with overloaded signatures may bind to only one overload.
- Passing Python lambdas into Java callback slots works for basic cases; complex callback hierarchies may need explicit adapter classes via Stage 05.5.
- `null` returns from Java methods come back as `None` in Python.
- Static nested classes (`$`) are accessible as `ClassName_InnerName` in Python.

If a method is missing or a call fails at runtime, check that the class was in `02_inspect/targets.json` with `enabled: true` and that the method was picked up during Stage 03. Re-run from Stage 03 if you add classes.