# -*- coding: utf-8 -*-
"""
Stratum Stage 10 — embedded Python runtime sources.
================================================================================
LOCATION: 10_embed/py_sources.py

The Python text that gets compressed into libstratum.so by emit.py. Nothing
here imports from 09_wheel — Stage 10 is fully self-contained, so 09_wheel can
be deleted at any time without affecting this build path.

Three of these are served lazily, exactly like any generated class, through the
meta-path finder installed by STRATUM_BOOTSTRAP_PY. INIT_PY is different: it is
exec'd eagerly into the compiled module's own namespace by JNI_OnLoad, which is
what makes `stratum.getActivity()` work.

NOTE: stratum/core/stratum_object.py is NOT here. Stage 08 already writes it to
disk, and emit.py picks it up from there along with every generated class — one
source of truth, no chance of drift.
"""


# =============================================================================
# Meta-path finder/loader. Fixed infrastructure — this never changes per
# project, only the compressed module table does. Registers real source with
# linecache so tracebacks and inspect.getsource() still work despite there
# being no file on disk.
# =============================================================================
STRATUM_BOOTSTRAP_PY = '''# Stratum embedded bootstrap. Auto-generated. DO NOT EDIT.
import sys
import zlib
import importlib.abc
import importlib.machinery
import linecache

# `stratum` (this very module) carries the pybundle lookup functions directly —
# the compiled engine and the Python package are the SAME module object.
_core = sys.modules["stratum"]


class _StratumEmbeddedLoader(importlib.abc.Loader):
    def __init__(self, fullname, is_package):
        self._fullname = fullname
        self._is_package = is_package

    def create_module(self, spec):
        return None

    def get_source(self, fullname):
        raw = _core._pybundle_lookup(fullname)
        if raw is None:
            return None
        return zlib.decompress(raw).decode("utf-8")

    def exec_module(self, module):
        if self._is_package:
            module.__path__ = []
            return
        if self._fullname == "stratum._meta_blob":
            # Not Python source — the raw gzip bytes of _meta.json.gz,
            # stored verbatim by 10_embed/emit.py (dynamic mode). Expose it
            # as module.DATA for _dynamic.py's registry loader to read.
            raw = _core._pybundle_lookup(self._fullname)
            module.DATA = bytes(raw) if raw is not None else b""
            return
        src = self.get_source(self._fullname)
        if src is None:
            raise ImportError("No embedded source for " + self._fullname)
        filename = "<stratum-embedded:%s>" % self._fullname
        code = compile(src, filename, "exec")
        linecache.cache[filename] = (len(src), None, src.splitlines(True), filename)
        exec(code, module.__dict__)


class _StratumEmbeddedFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if not fullname.startswith("stratum."):
            return None
        if _core._pybundle_has(fullname):
            return importlib.machinery.ModuleSpec(
                fullname, _StratumEmbeddedLoader(fullname, is_package=False))
        if _core._pybundle_is_package(fullname):
            return importlib.machinery.ModuleSpec(
                fullname, _StratumEmbeddedLoader(fullname, is_package=True),
                is_package=True)
        return None


def install():
    for f in sys.meta_path:
        if isinstance(f, _StratumEmbeddedFinder):
            return
    sys.meta_path.insert(0, _StratumEmbeddedFinder())


install()
'''


# =============================================================================
# stratum/__init__.py body — exec'd directly into the compiled `stratum`
# module's namespace by JNI_OnLoad.
#
# Two differences from a wheel-style __init__.py, both required here:
#   1. No `from . import _dynamic` shim — dynamic mode isn't used in this build.
#   2. `from . import _stratum as _core` becomes a self-reference, because the
#      C functions live directly in THIS module's dict.
# =============================================================================
INIT_PY = '''# Stratum Runtime Entry Point (embedded). Auto-generated. DO NOT EDIT.
import sys as _sys
import types as _types
import importlib

# The compiled engine and this package are the same module object.
# Snapshot all native C++ bindings into an isolated module `_core` BEFORE
# any Python wrapper functions defined below overwrite them.
_core = _types.ModuleType("stratum._core")
_core.__dict__.update({_k: _v for _k, _v in _sys.modules[__name__].__dict__.items() if not _k.startswith('__')})
_sys.modules["stratum._core"] = _core
_sys.modules["stratum._stratum"] = _core

# Dynamic-mode builds embed stratum._dynamic; static-mode builds don't. Both
# modes always have stratum.core.stratum_object served through the bootstrap
# finder — this import works either way.
try:
    import stratum._dynamic as _stratum_dynamic  # absolute import: __package__-safe
    _stratum_dynamic.install()
except ModuleNotFoundError:
    pass  # expected in a static-mode build: stratum._dynamic simply isn't present
except Exception:
    import traceback
    traceback.print_exc()  # a REAL bug — never hide it again

from stratum.core.stratum_object import StratumObject


def getActivity():
    """Return the current Android Activity, or None before onCreate."""
    ptr = _core.get_activity_ptr()
    if not ptr:
        return None
    from stratum.android.app.Activity import Activity
    return Activity(_ptr=ptr)


get_activity = getActivity
stratum_get_activity = getActivity


def stratum_cast(obj, target_cls):
    """Safely cast an object or pointer to target_cls, verified by JNI IsInstanceOf."""
    if obj is None:
        return None
    return target_cls.from_ptr(obj)


stratum_cast_to = stratum_cast


def setContentView(activity, view) -> None:
    """Mount a View as the Activity's root content view."""
    act_ptr = getattr(activity, "_ptr", activity)
    view_ptr = getattr(view, "_ptr", view)
    _core.set_content_view(act_ptr, view_ptr)


set_content_view = setContentView


def allocate_direct_buffer(capacity: int):
    """Allocate an off-heap direct java.nio.ByteBuffer."""
    ptr = _core.allocate_direct_buffer(capacity)
    if not ptr:
        return None
    from stratum.core.stratum_object import _wrap_instance
    return _wrap_instance(ptr, "java.nio.ByteBuffer")


def surface_to_native_window(surface) -> int:
    """Acquire an ANativeWindow* pointer (as an integer) from an android.view.Surface."""
    s_ptr = getattr(surface, "_ptr", surface)
    return _core.surface_to_native_window(s_ptr)


def release_native_window(win_ptr: int) -> None:
    """Release an ANativeWindow* handle previously acquired."""
    _core.release_native_window(win_ptr)


def to_java(obj):
    """Recursively converts Python data (dict, list, int, float, bool, str,
    bytes) into a real Java object (HashMap, ArrayList, boxed primitives,
    byte[]). Existing wrappers keep their underlying Java reference."""
    if obj is None:
        return None
    if hasattr(obj, "_ptr"):
        return obj
    ptr = _core.to_java(obj)
    if not ptr:
        return None
    return StratumObject(_ptr=ptr)


def to_py(obj):
    """Recursively converts Java objects (Map, List, Bundle, boxed primitives,
    arrays) into native Python data. Non-data objects are wrapped."""
    if obj is None:
        return None
    # Defensive: an object with no _ptr is ALREADY native Python data. Never
    # treat a bare Python int as a raw JNI address.
    if not hasattr(obj, "_ptr"):
        return obj
    ptr = getattr(obj, "_ptr", None)
    if not isinstance(ptr, int) or ptr == 0:
        return None
    return _core.to_py(ptr)


def register_function(name: str, fn) -> None:
    """Registers a Python function so Java can call it via
    StratumRuntimeLookup.callPython(name, ...)."""
    register_callback("app#" + name, fn)


def export(fn_or_name):
    """Decorator exposing a Python function to Java.

        @stratum.export
        def calculate_score(points, multiplier):
            return points * multiplier
    """
    if callable(fn_or_name):
        register_function(fn_or_name.__name__, fn_or_name)
        return fn_or_name
    def decorator(fn):
        register_function(fn_or_name, fn)
        return fn
    return decorator


def to_java_array(items, component_type: str):
    """Converts a Python list into a strongly-typed native Java array (T[]),
    for APIs whose signature is erased to Object. component_type is required —
    guessing it from list contents risks a ClassCastException on an empty or
    mixed-type list."""
    Class = importlib.import_module("stratum.java.lang.Class").Class
    Array = importlib.import_module("stratum.java.lang.reflect.Array").Array
    loader_ptr = _core.get_app_classloader_ptr()
    if loader_ptr:
        ClassLoader = importlib.import_module("stratum.java.lang.ClassLoader").ClassLoader
        comp_cls = Class.forName(component_type, True, ClassLoader(_ptr=loader_ptr))
    else:
        comp_cls = Class.forName(component_type)
    arr = Array.newInstance(comp_cls, len(items))
    for i, item in enumerate(items):
        Array.set(arr, i, item)
    return arr


def remove_callback(key: str) -> None:
    """Release a stored Python callback (listener/adapter) by key."""
    _core.remove_callback(key)


def remove_callbacks_by_prefix(prefix: str) -> int:
    """Remove all stored callbacks matching a prefix."""
    return _core.remove_callbacks_by_prefix(prefix)


def callback_count() -> int:
    """Number of Python callbacks currently retained by the native side."""
    return _core.stratum_callback_count()


def register_callback(key: str, fn) -> None:
    """Bind a Python callable to a native callback key (or 'key#method')."""
    _core.register_callback(key, fn)


def create_stratum_view(key: str):
    """Construct a native com.stratum.runtime.StratumView bound to `key`.
    Prefer stratum.ui.CustomCanvasView over calling this directly."""
    ptr = _core.create_stratum_view(key)
    if not ptr:
        return None
    from stratum.core.stratum_object import _wrap_instance
    return _wrap_instance(ptr, "android.view.View")


_main_handler = None


def run_on_ui_thread(fn, *args, **kwargs) -> None:
    """Posts a callable to Android's Main Looper. Required before touching any
    View from a background thread, timer, sensor callback, or network response
    — Android throws CalledFromWrongThreadException otherwise. Handler/Looper
    are imported lazily, so this costs nothing if never called."""
    global _main_handler
    if _main_handler is None:
        from stratum.android.os.Handler import Handler
        from stratum.android.os.Looper import Looper
        _main_handler = Handler(Looper.getMainLooper())

    def _runner():
        fn(*args, **kwargs)

    _main_handler.post(_runner)


def ui_thread(fn):
    """Decorator: always runs the wrapped function on the Android UI thread."""
    def wrapper(*args, **kwargs):
        run_on_ui_thread(fn, *args, **kwargs)
    return wrapper


def set_log_enabled(enabled: bool) -> None:
    """Toggle runtime logging. Silent no-op if this .so was built with
    --no-log, which strips all logging code at compile time."""
    _core.set_log_enabled(enabled)


def _auto_register_lifecycle() -> None:
    try:
        main = _sys.modules.get("main") or importlib.import_module("main")
    except Exception:
        return
    for name in ("onCreate", "onResume", "onPause", "onStop", "onDestroy"):
        fn = getattr(main, name, None)
        if callable(fn):
            _core.set_lifecycle_callback(name, fn)


try:
    _auto_register_lifecycle()
except Exception:
    pass
'''


# =============================================================================
# stratum/ui.py — served lazily through the meta-path finder like any other
# module. Only touches stratum._core, which the INIT_PY body above defines.
# =============================================================================
STRATUM_UI_PY = '''# Stratum UI helpers (embedded). Auto-generated. DO NOT EDIT.
"""
High-level Python wrapper around the StratumView runtime bridge class.
See runtime/java/.../StratumView.java for the Java side.
"""
import uuid
import weakref
import stratum
from stratum.core.stratum_object import StratumObject, _wrap_instance


class CustomCanvasView(StratumObject):
    """Subclass this for custom 2D canvas drawing, custom touch handling, and
    (optionally) custom measurement/layout. Real View methods (invalidate(),
    setPadding(), ...) are available via attribute delegation to a CLONED
    android.view.View wrapper of the same underlying Java object — cloned so
    its lifetime is independent of this object's."""

    def __init__(self, activity=None):
        self._ptr = None
        self._key = "view_" + uuid.uuid4().hex
        self._view_wrapper = None

        self_ref = weakref.ref(self)

        def _on_draw(canvas_ptr):
            view = self_ref()
            if view is not None:
                view._internal_on_draw(canvas_ptr)

        def _on_measure(w_spec, h_spec):
            view = self_ref()
            return view._internal_on_measure(w_spec, h_spec) if view is not None else None

        def _on_layout(changed, l, t, r, b):
            view = self_ref()
            if view is not None:
                view._internal_on_layout(changed, l, t, r, b)

        def _on_touch(event_ptr):
            view = self_ref()
            return view._internal_on_touch(event_ptr) if view is not None else False

        stratum.register_callback(self._key + "#onDraw", _on_draw)
        stratum.register_callback(self._key + "#onMeasure", _on_measure)
        stratum.register_callback(self._key + "#onLayout", _on_layout)
        stratum.register_callback(self._key + "#onTouchEvent", _on_touch)

        native_ptr = stratum._core.create_stratum_view(self._key)
        if not native_ptr:
            self._cleanup_callbacks()
            raise RuntimeError("Stratum: failed to construct StratumView (activity not ready?)")
        super().__init__(_ptr=native_ptr)

    def _cleanup_callbacks(self):
        if self.__dict__.get("_key"):
            try:
                stratum.remove_callbacks_by_prefix(self._key)
            except Exception:
                pass

    def __del__(self):
        self._cleanup_callbacks()
        super().__del__()

    def _as_view(self):
        if self._view_wrapper is None:
            if not self._ptr:
                return None
            cloned = stratum._core.clone_ref(self._ptr)
            self._view_wrapper = _wrap_instance(cloned, "android.view.View")
        return self._view_wrapper

    def __getattr__(self, name):
        if name.startswith("_") or not self.__dict__.get("_ptr"):
            raise AttributeError(name)
        view = self._as_view()
        if view is None:
            raise AttributeError(name)
        return getattr(view, name)

    def _internal_on_draw(self, canvas_ptr):
        canvas = _wrap_instance(canvas_ptr, "android.graphics.Canvas") if isinstance(canvas_ptr, int) else canvas_ptr
        self.on_draw(canvas)

    def _internal_on_measure(self, width_spec, height_spec):
        return self.on_measure(width_spec, height_spec)

    def _internal_on_layout(self, changed, l, t, r, b):
        self.on_layout(bool(changed), l, t, r, b)

    def _internal_on_touch(self, motion_event_ptr):
        event = _wrap_instance(motion_event_ptr, "android.view.MotionEvent") if isinstance(motion_event_ptr, int) else motion_event_ptr
        return bool(self.on_touch_event(event))

    def on_draw(self, canvas):
        pass

    def on_measure(self, width_spec, height_spec):
        return None  # return [width, height] to report a custom size

    def on_layout(self, changed, left, top, right, bottom):
        pass

    def on_touch_event(self, event) -> bool:
        return False
'''


# =============================================================================
# stratum/reflect.py — rare-path escape hatch, served lazily.
# =============================================================================
STRATUM_REFLECT_PY = '''# Stratum reflection escape hatch (embedded). Auto-generated. DO NOT EDIT.
"""
Rare-path escape hatch for Java code with no generated AOT wrapper: a class you
wrote yourself in Android Studio, or one excluded by 05_resolve/targets.json.
Prefer the generated stratum.android.* classes for anything they already cover.
"""
import importlib
import stratum
from stratum.core.stratum_object import _wrap_instance

_method_cache = {}
_loader_cache = None


def _require(fqn, py_path):
    try:
        return importlib.import_module(py_path)
    except ImportError as e:
        raise ImportError(
            "stratum.reflect needs '%s' in this build. If using a filtered/"
            "closure-mode build, add it to 05_resolve/targets.json and rebuild."
            % fqn
        ) from e


def _app_class_loader():
    # Reuses the SAME cached app ClassLoader find_class() already relies on
    # internally — available even before any Activity exists (e.g. from a
    # boot-triggered Service/Receiver).
    global _loader_cache
    if _loader_cache is not None:
        return _loader_cache
    ClassLoader = _require("java.lang.ClassLoader", "stratum.java.lang.ClassLoader").ClassLoader
    ptr = stratum._core.get_app_classloader_ptr()
    if not ptr:
        raise RuntimeError("stratum.reflect: app ClassLoader not ready yet")
    _loader_cache = ClassLoader(_ptr=ptr)
    return _loader_cache


def _forname(class_name):
    # 3-arg Class.forName(name, initialize, loader): a bare 1-arg forName()
    # called through JNI has no Java caller frame, so ART resolves it against
    # the BOOT classloader and cannot see your own app classes.
    Class = _require("java.lang.Class", "stratum.java.lang.Class").Class
    return Class.forName(class_name, True, _app_class_loader())


def _wrap_array(raw_list, element_fqn):
    return [_wrap_instance(p, element_fqn) for p in raw_list if p]


def _type_matches(param_class, value):
    name = param_class.getName()
    if isinstance(value, bool):
        return name in ("boolean", "java.lang.Boolean")
    if isinstance(value, int):
        return name in ("int", "long", "short", "byte",
                        "java.lang.Integer", "java.lang.Long",
                        "java.lang.Short", "java.lang.Byte")
    if isinstance(value, float):
        return name in ("float", "double", "java.lang.Float", "java.lang.Double")
    if isinstance(value, str):
        return name in ("java.lang.String", "java.lang.CharSequence")
    if value is None:
        return not param_class.isPrimitive()
    if hasattr(value, "_ptr"):
        return bool(param_class.isInstance(value))
    return False


def _resolve(cls_wrapper, class_name, method_name, args, want_static):
    key = (class_name, method_name, len(args), tuple(type(a).__name__ for a in args))
    cached = _method_cache.get(key)
    if cached is not None:
        return cached

    Modifier = _require("java.lang.reflect.Modifier", "stratum.java.lang.reflect.Modifier").Modifier
    candidates = _wrap_array(cls_wrapper.getMethods(), "java.lang.reflect.Method")
    matches = []
    for m in candidates:
        if m.getName() != method_name:
            continue
        if bool(Modifier.isStatic(m.getModifiers())) != want_static:
            continue
        params = _wrap_array(m.getParameterTypes(), "java.lang.Class")
        if len(params) != len(args):
            continue
        if all(_type_matches(p, a) for p, a in zip(params, args)):
            matches.append(m)

    if not matches:
        kind = "static method" if want_static else "method"
        raise TypeError("No matching %s '%s' with %d argument(s) on %s"
                        % (kind, method_name, len(args), class_name))

    chosen = matches[0]
    _method_cache[key] = chosen
    return chosen


def _invoke(method, target, args):
    packed = stratum.to_java_array(list(args), "java.lang.Object") if args else None
    try:
        result = method.invoke(target, packed)
    except Exception as e:
        raise RuntimeError("Java call failed: %s" % e) from e
    return stratum.to_py(result)


def call_java_static(class_name: str, method_name: str, *args):
    """Call a STATIC Java method by fully-qualified class name."""
    cls = _forname(class_name)
    method = _resolve(cls, class_name, method_name, args, want_static=True)
    return _invoke(method, None, args)


def call_java_method(obj, method_name: str, *args):
    """Call an INSTANCE method, resolved against the object's REAL runtime
    class (so this works on your own subclasses too)."""
    ptr = getattr(obj, "_ptr", None)
    if not ptr:
        raise ValueError("call_java_method: object has no live Java reference")
    class_name = stratum._core.get_class_name(ptr)
    if not class_name:
        raise RuntimeError("call_java_method: could not determine runtime class")
    cls = _forname(class_name)
    method = _resolve(cls, class_name, method_name, args, want_static=False)
    return _invoke(method, obj, args)


def call_java(target, method_name: str, *args):
    """Universal entry point: a class-name string -> static call; an existing
    wrapped object (has ._ptr) -> instance call."""
    if isinstance(target, str):
        return call_java_static(target, method_name, *args)
    elif hasattr(target, "_ptr"):
        return call_java_method(target, method_name, *args)
    else:
        raise TypeError("call_java: target must be a class name (str) or a wrapped object")
'''