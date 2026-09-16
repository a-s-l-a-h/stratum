#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Stratum Pipeline — Stage 10: Embedded Single-.so Build
================================================================================
LOCATION: 10_embed/main.py

WHAT THIS STAGE PRODUCES
    ONE self-contained native library per (python-version x abi):

        libstratum-cp314-arm64_v8a.so

    Drop it into a consuming Android project's jniLibs/<abi>/ (renamed to
    libstratum.so) and `import stratum` + `from stratum.android.widget
    import Button` work with no wheel, no loose .py files, no metadata
    blob on disk.

RELATIONSHIP TO THE EXISTING PIPELINE — READ THIS
    This stage MODIFIES NOTHING. Stages 00-09 are byte-for-byte
    untouched and keep producing the wheel exactly as before. Stage 10:
      - READS   06_cpp_emit/output/core/   (engine + metadata_table)
      - READS   08_pyi_emit/output/        (static per-class .py source)
      - WRITES  10_embed/output/work/      (its own private work dir)
      - WRITES  10_embed/output/           (the finished .so + sidecar)

    The one engine file that genuinely must change for the embedded
    model is bridge_main.cpp (module rename + pybundle hooks + manual
    PyInit). Rather than editing 06_cpp_emit/engine_src/bridge_main.cpp,
    this stage reads Stage 06's emitted copy, applies the patches IN
    MEMORY, and writes the patched result into work/. Stage 06's source
    and output are left alone, so Stage 07 still builds _stratum.so from
    the unpatched original.

WHAT GETS GENERATED INTO work/
    stratum_pybundle.h / .cpp   zlib-compressed source for every
                                 generated class + stratum_object.py +
                                 ui.py + reflect.py, keyed by dotted
                                 module name; plus kStratumBootstrapPy
                                 (the meta-path finder) and
                                 kStratumInitPy (the package __init__
                                 body), plus kStratumBuildInfoJson.
    bridge_main.cpp             patched copy of Stage 06's.
    CMakeLists.txt              rendered from 10_embed/templates/.
    StratumInit.cmake           rendered from 10_embed/templates/.

WHY SOURCE AND NOT MARSHALLED BYTECODE
    Marshalled .pyc is tied to the exact CPython minor version's magic
    number. If the build host's Python doesn't match the on-device
    embedded CPython exactly, marshal.loads() breaks silently. Plain
    source has no such coupling; compile() costs microseconds, once per
    module, then sys.modules caches it forever.

WHY ZLIB IS FREE HERE
    libz.so is a stable NDK system library present on every Android
    device since API 1. Linking it adds a dynamic-symbol reference, not
    zlib's actual code. The win on the embedded Python source text
    (typically 3-4x) is real.

USAGE
    python 10_embed/main.py \\
        --cpp       06_cpp_emit/output \\
        --static-py 08_pyi_emit/output \\
        --setup     00_setup/output/setup_report.json \\
        --nanobind  third_party/nanobind \\
        --abi       arm64-v8a \\
        --output    10_embed/output \\
        --no-log
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
import zlib
from pathlib import Path


def print_header(title: str) -> None:
    print("=" * 70)
    print(f"  {title}")
    print("=" * 70)


# =============================================================================
# The compiled module is now named `stratum` itself — it is simultaneously
# the engine AND the package. Any source that still says `stratum._stratum`
# must be retargeted to plain `stratum`. Applied at pack time so Stage 08's
# emitter stays completely unmodified.
#   "import stratum._stratum as _core"  ->  "import stratum as _core"
# =============================================================================
def fix_core_import(src: str) -> str:
    return re.sub(r"\bstratum\._stratum\b", "stratum", src)


# =============================================================================
# Meta-path finder/loader. Fixed infrastructure — never changes per project,
# only the compressed table does. Registers real source with linecache so
# tracebacks and inspect.getsource() still work with no file on disk.
# =============================================================================
STRATUM_BOOTSTRAP_PY = '''# Stratum embedded bootstrap. Auto-generated. DO NOT EDIT.
import sys
import zlib
import importlib.abc
import importlib.machinery
import linecache

# `stratum` (this very module) carries the pybundle lookup functions
# directly — the compiled engine and the Python package are the SAME
# module object. See bridge_main.cpp.
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
# module's own namespace by JNI_OnLoad, so getActivity()/to_java()/@export
# land as stratum.getActivity() etc.
#
# TWO differences from 09_wheel's INIT_PY, both required by this model:
#   1. No `from . import _dynamic` shim — dynamic mode isn't used here.
#   2. `from . import _stratum as _core` becomes a self-reference, since
#      the C functions live directly in THIS module's dict.
# =============================================================================
INIT_PY = '''# Stratum Runtime Entry Point (embedded). Auto-generated. DO NOT EDIT.
import sys as _sys
import importlib

# The compiled engine and this package are the same module object, and this
# body is exec'd with that module's __dict__ as globals — so every m.def()'d
# C function (call_v, to_java, get_activity_ptr, ...) is already present in
# this namespace. `_core` is a self-alias kept for source compatibility with
# stratum.ui / stratum.reflect.
_core = _sys.modules[__name__]

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
    """Decorator exposing a Python function to Java."""
    if callable(fn_or_name):
        register_function(fn_or_name.__name__, fn_or_name)
        return fn_or_name
    def decorator(fn):
        register_function(fn_or_name, fn)
        return fn
    return decorator


def to_java_array(items, component_type: str):
    """Converts a Python list into a strongly-typed native Java array (T[])."""
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
    """Number of Python callbacks currently retained natively."""
    return _core.stratum_callback_count()


def register_callback(key: str, fn) -> None:
    """Bind a Python callable to a native callback key (or 'key#method')."""
    _core.register_callback(key, fn)


def create_stratum_view(key: str):
    """Construct a native com.stratum.runtime.StratumView bound to `key`.
    Prefer stratum.ui.CustomCanvasView instead of calling this directly."""
    ptr = _core.create_stratum_view(key)
    if not ptr:
        return None
    from stratum.core.stratum_object import _wrap_instance
    return _wrap_instance(ptr, "android.view.View")


_main_handler = None


def run_on_ui_thread(fn, *args, **kwargs) -> None:
    """Posts a callable to Android's Main Looper. Required before touching
    any View from a background thread, timer, sensor callback, or network
    response — Android throws CalledFromWrongThreadException otherwise."""
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
    """Toggle runtime logging. Silent no-op if built with --no-log."""
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
# module. Only touches stratum._core, which the __init__ body above defines.
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
Rare-path escape hatch for Java code with no generated AOT wrapper: a class
you wrote yourself in Android Studio, or one excluded by targets.json. Prefer
the generated stratum.android.* classes for anything they already cover.
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
    """Call an INSTANCE method, resolved against the object's REAL runtime class."""
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
    """Universal entry point: class-name string -> static call, wrapped object
    (has ._ptr) -> instance call."""
    if isinstance(target, str):
        return call_java_static(target, method_name, *args)
    elif hasattr(target, "_ptr"):
        return call_java_method(target, method_name, *args)
    else:
        raise TypeError("call_java: target must be a class name (str) or a wrapped object")
'''


# =============================================================================
# PART A — pack the Python sources into stratum_pybundle.h / .cpp
# =============================================================================

def c_raw_string(text: str) -> str:
    """Emits Python source as an unescaped C++ raw string literal. The
    delimiter is chosen so it cannot appear in generated Python source."""
    return 'R"PYSRC__STRATUM(' + text + ')PYSRC__STRATUM"'


def collect_modules(static_py_dir: Path) -> tuple:
    """Reads Stage 08's generated .py tree (skips .pyi — IDE-only, never
    embedded; skips __init__.py — intermediate packages are synthesized below
    so they get a real empty __path__) and maps dotted module name -> source,
    matching the existing static layout:
        stratum/android/widget/Button.py -> stratum.android.widget.Button"""
    modules = {}
    stratum_root = static_py_dir / "stratum"
    if not stratum_root.exists():
        raise FileNotFoundError(
            "No 'stratum/' tree under %s — did Stage 08 run with --mode static?"
            % static_py_dir)

    for f in sorted(stratum_root.rglob("*.py")):
        if f.name == "__init__.py":
            continue
        rel = f.relative_to(stratum_root)
        dotted = "stratum." + ".".join(rel.with_suffix("").parts)
        modules[dotted] = fix_core_import(f.read_text(encoding="utf-8"))

    if "stratum.core.stratum_object" not in modules:
        raise FileNotFoundError(
            "stratum/core/stratum_object.py missing from %s — Stage 08 always "
            "writes it; check that --static-py points at 08_pyi_emit/output/."
            % static_py_dir)

    # Support modules, packed exactly like any generated class, under their
    # fixed well-known dotted names.
    modules["stratum.ui"] = fix_core_import(STRATUM_UI_PY)
    modules["stratum.reflect"] = fix_core_import(STRATUM_REFLECT_PY)

    # Every intermediate package must resolve too, even with no source file.
    packages = set()
    for dotted in modules:
        pkg_parts = dotted.split(".")[:-1]
        for i in range(1, len(pkg_parts) + 1):
            packages.add(".".join(pkg_parts[:i]))
    # `stratum` itself IS the compiled module, never a synthetic package.
    packages.discard("stratum")
    return modules, packages


def emit_pybundle(modules: dict, packages: set, build_info: dict) -> tuple:
    h_lines = [
        "// stratum_pybundle.h — Stratum Stage 10 auto-generated. DO NOT EDIT.",
        "#pragma once",
        "#include <cstdint>",
        "",
        "const unsigned char* stratum_pybundle_find(const char* fullname, uint32_t* out_len);",
        "bool stratum_pybundle_is_package(const char* fullname);",
        "",
        "extern const char kStratumBootstrapPy[];",
        "extern const char kStratumInitPy[];",
        "",
        "// Build provenance — queryable at runtime via stratum.build_info(),",
        "// deliberately NOT encoded in the .so filename (only python-tag and abi",
        "// are, because those are the only two things that decide whether this",
        "// file can load at all).",
        "extern const char kStratumBuildInfoJson[];",
    ]

    cpp_lines = [
        "// stratum_pybundle.cpp — Stratum Stage 10 auto-generated. DO NOT EDIT.",
        '#include "stratum_pybundle.h"',
        "#include <cstring>",
        "",
        "extern const char kStratumBootstrapPy[] = " + c_raw_string(STRATUM_BOOTSTRAP_PY) + ";",
        "extern const char kStratumInitPy[] = " + c_raw_string(INIT_PY) + ";",
        "extern const char kStratumBuildInfoJson[] = "
        + c_raw_string(json.dumps(build_info, separators=(",", ":"))) + ";",
        "",
        "struct PyBundleEntry { const char* name; const unsigned char* data; uint32_t len; };",
        "",
    ]

    entries = []
    for i, (dotted, source) in enumerate(sorted(modules.items())):
        compressed = zlib.compress(source.encode("utf-8"), 9)
        hexb = ", ".join("0x%02x" % b for b in compressed)
        cpp_lines.append("static const unsigned char g_pysrc_%d[] = {%s};" % (i, hexb))
        entries.append((dotted, i, len(compressed)))

    cpp_lines.append("")
    cpp_lines.append("static const PyBundleEntry g_pybundle_table[] = {")
    for dotted, i, length in entries:
        cpp_lines.append('    {"%s", g_pysrc_%d, %d},' % (dotted, i, length))
    cpp_lines.append("};")
    cpp_lines.append("static const uint32_t g_pybundle_count = %d;" % len(entries))

    cpp_lines.append("")
    cpp_lines.append("static const char* g_pybundle_packages[] = {")
    for pkg in sorted(packages):
        cpp_lines.append('    "%s",' % pkg)
    cpp_lines.append("};")
    cpp_lines.append("static const uint32_t g_pybundle_package_count = %d;" % len(packages))

    cpp_lines.append("""
const unsigned char* stratum_pybundle_find(const char* fullname, uint32_t* out_len) {
    for (uint32_t i = 0; i < g_pybundle_count; ++i) {
        if (strcmp(g_pybundle_table[i].name, fullname) == 0) {
            *out_len = g_pybundle_table[i].len;
            return g_pybundle_table[i].data;
        }
    }
    *out_len = 0;
    return nullptr;
}

bool stratum_pybundle_is_package(const char* fullname) {
    for (uint32_t i = 0; i < g_pybundle_package_count; ++i) {
        if (strcmp(g_pybundle_packages[i], fullname) == 0) return true;
    }
    return false;
}
""")
    return "\n".join(h_lines), "\n".join(cpp_lines)


# =============================================================================
# PART B — patch a COPY of Stage 06's bridge_main.cpp
#
# Three surgical edits. Each anchor is asserted, so if Stage 06's engine
# source ever changes shape this fails loudly at build time instead of
# silently producing a .so that can't boot Python.
# =============================================================================

_PATCH_A_ANCHOR = """static std::unordered_map<std::string, nb::callable> g_lifecycle_cbs;"""

_PATCH_A_NEW = '''#include "stratum_pybundle.h"

// Generated below by the NB_MODULE(stratum, m) macro. Forward-declared here
// because JNI_OnLoad (earlier in this file) calls it manually: System.load()
// dlopens us directly, bypassing Python's file-based import discovery, so
// nothing else would ever trigger this module's init function.
extern "C" PyObject* PyInit_stratum();

static std::unordered_map<std::string, nb::callable> g_lifecycle_cbs;'''


_PATCH_B_ANCHOR = """NB_MODULE(_stratum, m) {
    m.def("call_v", &call_v);"""

_PATCH_B_NEW = '''// Passthrough helper exposed to the embedded bootstrap loader.
static nb::object stratum_pybundle_lookup(const std::string& fullname) {
    uint32_t len = 0;
    const unsigned char* data = stratum_pybundle_find(fullname.c_str(), &len);
    if (!data) return nb::none();
    return nb::bytes(reinterpret_cast<const char*>(data), len);
}

// Module renamed _stratum -> stratum: the compiled engine and the public
// Python package are now the SAME module object. One file is the package.
NB_MODULE(stratum, m) {
    m.attr("__path__") = nb::list();  // makes `stratum` a real package

    m.def("_pybundle_lookup", &stratum_pybundle_lookup);
    m.def("_pybundle_has", [](const std::string& n) {
        uint32_t len = 0;
        return stratum_pybundle_find(n.c_str(), &len) != nullptr;
    });
    m.def("_pybundle_is_package", [](const std::string& n) {
        return stratum_pybundle_is_package(n.c_str());
    });
    m.def("build_info", []() -> nb::object {
        nb::object json_mod = nb::module_::import_("json");
        return json_mod.attr("loads")(kStratumBuildInfoJson);
    });

    m.def("call_v", &call_v);'''


_PATCH_C_ANCHOR = """    LOGI("Stratum engine initialized. %u classes indexed lazily.", g_class_count);
    return JNI_VERSION_1_6;
}"""

_PATCH_C_NEW = '''    LOGI("Stratum engine initialized. %u classes indexed lazily.", g_class_count);

    // Manually invoke this library's own module-init entry point and register
    // it in sys.modules. Required because System.loadLibrary() dlopens us
    // directly — Python's import system only auto-runs an extension's init
    // function when it FINDS a matching file via its own search path, and in
    // the embedded model no such file exists. After this, `import stratum`
    // anywhere in main.py is a plain sys.modules cache hit.
    //
    // ORDERING REQUIREMENT: the Python interpreter must already be running
    // before System.loadLibrary("stratum") is called. StratumEmbeddedActivity
    // starts Python first — keep it that way.
    {
        PyGILState_STATE gstate = PyGILState_Ensure();
        PyObject* mod = PyInit_stratum();
        if (mod) {
            PyDict_SetItemString(PyImport_GetModuleDict(), "stratum", mod);

            // Now that `stratum` resolves in sys.modules: exec the bootstrap
            // (installs the meta-path finder that lazily serves every
            // generated class's compressed source), then the __init__ body
            // directly into this module's own namespace — so getActivity()/
            // to_java()/@export land as stratum.getActivity() etc.
            PyObject* g = PyModule_GetDict(mod);
            auto exec_embedded = [&](const char* src, const char* tag) {
                PyObject* code = Py_CompileString(src, tag, Py_file_input);
                if (!code) { PyErr_Print(); return; }
                PyObject* r = PyEval_EvalCode(code, g, g);
                if (!r) PyErr_Print();
                Py_XDECREF(r);
                Py_DECREF(code);
            };
            exec_embedded(kStratumBootstrapPy, "<stratum-embedded:bootstrap>");
            exec_embedded(kStratumInitPy, "<stratum-embedded:__init__>");

            LOGI("Stratum: embedded 'stratum' package registered and booted.");
        } else {
            PyErr_Print();
            LOGE("Stratum: PyInit_stratum() failed.");
        }
        PyGILState_Release(gstate);
    }

    return JNI_VERSION_1_6;
}'''


def patch_bridge_main(src: str) -> str:
    for label, anchor, new in (
        ("A (pybundle include + PyInit forward decl)", _PATCH_A_ANCHOR, _PATCH_A_NEW),
        ("B (NB_MODULE rename + pybundle bindings)", _PATCH_B_ANCHOR, _PATCH_B_NEW),
        ("C (JNI_OnLoad embedded boot)", _PATCH_C_ANCHOR, _PATCH_C_NEW),
    ):
        if src.count(anchor) != 1:
            raise RuntimeError(
                "Stage 10 patch %s: expected exactly 1 match for its anchor in "
                "bridge_main.cpp, found %d. Stage 06's engine source has changed "
                "shape — update the anchor in 10_embed/main.py." % (label, src.count(anchor)))
        src = src.replace(anchor, new)
    return src


# =============================================================================
# PART C — build (same toolchain logic Stage 07 uses, kept self-contained)
# =============================================================================

def render_template(tpl_path: Path, variables: dict) -> str:
    text = tpl_path.read_text(encoding="utf-8")
    for key, value in variables.items():
        text = text.replace("{{%s}}" % key, value)
    return text


def resolve_android_platform(android_api: str, ndk_max: int = 33) -> str:
    """NDK r25c caps native compilation at API 33; asking for higher breaks the
    linker (crtbegin_dynamic.o not found), so clamp rather than surface that as
    a confusing link error."""
    try:
        if int(android_api) > ndk_max:
            print("   NOTE: API %s > NDK max (%d). Capping to %d." % (android_api, ndk_max, ndk_max))
            return str(ndk_max)
    except ValueError:
        pass
    return android_api


def find_ninja(ndk_path: Path):
    try:
        import ninja as ninja_pkg
        for name in ("ninja.exe", "ninja"):
            candidate = Path(ninja_pkg.BIN_DIR) / name
            if candidate.exists():
                return str(candidate)
    except ImportError:
        pass
    found = shutil.which("ninja")
    if found:
        return found
    for sub in ndk_path.rglob("ninja*"):
        if sub.is_file() and sub.name in ("ninja", "ninja.exe"):
            return str(sub)
    return None


def resolve_python_target(cpython_dir: Path, py_version: str, abi: str):
    """Resolves Python.h and libpython from the official python.org Android
    builds extracted by Stage 00 under third_party/cpython_android/<ver>/<abi>/prefix/."""
    py_mm = ".".join(py_version.split(".")[:2])
    prefix_dir = cpython_dir / abi / "prefix"
    if not prefix_dir.exists():
        raise FileNotFoundError(
            "Official Python target not found for ABI '%s' at: %s\n"
            "Ensure Stage 00 ran with --python-target-version %s" % (abi, prefix_dir, py_version))

    include_dir = prefix_dir / "include" / ("python" + py_mm)
    if not (include_dir / "Python.h").exists():
        found = False
        for c in (prefix_dir / "include").glob("python*"):
            if (c / "Python.h").exists():
                include_dir = c
                found = True
                break
        if not found:
            raise FileNotFoundError("Python.h not found in %s" % (prefix_dir / "include"))

    lib_dir = prefix_dir / "lib"
    if not (lib_dir / ("libpython%s.so" % py_mm)).exists():
        if not list(lib_dir.glob("libpython*.so")):
            raise FileNotFoundError("No libpython*.so found in %s" % lib_dir)

    return {
        "include": include_dir.resolve().as_posix(),
        "lib_dir": lib_dir.resolve().as_posix(),
        "py_ver": py_mm,
    }


def main():
    ap = argparse.ArgumentParser(description="Stratum Stage 10 - Embedded Single-.so Build")
    ap.add_argument("--cpp", required=True, help="06_cpp_emit/output/")
    ap.add_argument("--static-py", required=True, help="08_pyi_emit/output/ (--mode static)")
    ap.add_argument("--setup", required=True, help="00_setup/output/setup_report.json")
    ap.add_argument("--nanobind", required=True, help="third_party/nanobind")
    ap.add_argument("--templates", default="10_embed/templates")
    ap.add_argument("--abi", default="arm64-v8a",
                    choices=["arm64-v8a", "armeabi-v7a", "x86_64", "x86"])
    ap.add_argument("--python-target-version", "--py-version", default=None,
                    help="Defaults to setup_report.json's python_target_version")
    ap.add_argument("--cpython-dir", default=None,
                    help="Override path to cpython_android/<version>")
    ap.add_argument("--stratum-version", default="0.9.0")
    ap.add_argument("--output", required=True, help="10_embed/output/")
    ap.add_argument("--log", dest="log_enabled", action="store_true", default=True,
                    help="Build WITH deep trace logging compiled in (default).")
    ap.add_argument("--no-log", dest="log_enabled", action="store_false",
                    help="Build WITHOUT logging — fully stripped, for production.")
    args = ap.parse_args()

    print_header("STRATUM PIPELINE — STAGE 10 (EMBEDDED SINGLE-.SO BUILD)")

    setup = json.loads(Path(args.setup).read_text(encoding="utf-8"))
    cmake_exe = setup.get("cmake_path", "cmake")
    ndk_path = Path(setup["ndk_path"])
    android_api = setup.get("ndk_api", "24")

    py_target_ver = args.python_target_version or setup.get("python_target_version", "3.14.7")
    py_target_dir = Path(args.cpython_dir or setup.get(
        "python_target_path",
        Path(__file__).parent.parent / "third_party" / "cpython_android" / py_target_ver))

    core_dir = Path(args.cpp) / "core"
    static_dir = Path(args.static_py)
    output_dir = Path(args.output)
    work_dir = output_dir / "work"
    build_dir = output_dir / "build" / args.abi
    work_dir.mkdir(parents=True, exist_ok=True)
    build_dir.mkdir(parents=True, exist_ok=True)

    for required in ("bridge_core.cpp", "bridge_core.h", "stratum_engine.cpp",
                     "metadata_table.cpp", "metadata_table.h", "bridge_main.cpp"):
        if not (core_dir / required).exists():
            print("ERROR: %s missing from %s. Run Stage 06 first." % (required, core_dir))
            sys.exit(1)

    ninja_exe = find_ninja(ndk_path)
    if not ninja_exe:
        print("ERROR: ninja not found. Run: pip install ninja")
        sys.exit(1)

    python_paths = resolve_python_target(py_target_dir, py_target_ver, args.abi)
    print("-> Target Python %s [%s]" % (python_paths["py_ver"], args.abi))
    print("   Include: %s" % python_paths["include"])
    print("   Lib dir: %s" % python_paths["lib_dir"])

    # ── 1. Pack the Python sources ───────────────────────────────────────
    modules, packages = collect_modules(static_dir)
    build_info = {
        "stratum_version": args.stratum_version,
        "android_api_jar": setup.get("android_api"),
        "ndk_api": android_api,
        "python_target_version": py_target_ver,
        "abi": args.abi,
        "logging": bool(args.log_enabled),
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    h_code, cpp_code = emit_pybundle(modules, packages, build_info)
    (work_dir / "stratum_pybundle.h").write_text(h_code, encoding="utf-8")
    (work_dir / "stratum_pybundle.cpp").write_text(cpp_code, encoding="utf-8")
    packed_kb = sum(len(zlib.compress(s.encode("utf-8"), 9)) for s in modules.values()) / 1024
    print("-> Packed %,d modules / %,d packages ({:.1f} KB compressed)."
          .replace("%,d", "{:,}").format(len(modules), len(packages), packed_kb))

    # ── 2. Patch a COPY of bridge_main.cpp (Stage 06's original untouched) ─
    patched = patch_bridge_main((core_dir / "bridge_main.cpp").read_text(encoding="utf-8"))
    (work_dir / "bridge_main.cpp").write_text(patched, encoding="utf-8")
    print("-> Patched bridge_main.cpp written to work dir (Stage 06's copy untouched).")

    # ── 3. Render build files ────────────────────────────────────────────
    tpl_dir = Path(args.templates)
    init_file = build_dir / "StratumInit.cmake"
    init_file.write_text(render_template(tpl_dir / "StratumInit.cmake.tpl", {
        "INC_ABS": python_paths["include"],
        "HOST_PYTHON": Path(sys.executable).resolve().as_posix(),
        "PY_VER": python_paths["py_ver"],
        "PY_MAJOR": python_paths["py_ver"].split(".")[0],
        "PY_MINOR": python_paths["py_ver"].split(".")[1],
    }), encoding="utf-8")

    cmake_file = output_dir / "CMakeLists.txt"
    cmake_file.write_text(render_template(tpl_dir / "CMakeLists.txt.tpl", {
        "NANOBIND_DIR": Path(args.nanobind).resolve().as_posix(),
        "CORE_INCLUDE_DIR": core_dir.resolve().as_posix(),
        "WORK_DIR": work_dir.resolve().as_posix(),
        "PYTHON_VERSION": python_paths["py_ver"],
        "PYTHON_INCLUDE": python_paths["include"],
        "PYTHON_LIB_DIR": python_paths["lib_dir"],
    }), encoding="utf-8")

    # ── 4. Configure + compile ───────────────────────────────────────────
    toolchain = ndk_path / "build" / "cmake" / "android.toolchain.cmake"
    t0 = time.time()
    subprocess.run([
        cmake_exe, str(output_dir),
        "-B%s" % build_dir,
        "-DCMAKE_TOOLCHAIN_FILE=%s" % toolchain,
        "-DANDROID_ABI=%s" % args.abi,
        "-DANDROID_PLATFORM=android-%s" % resolve_android_platform(android_api),
        "-DANDROID_STL=c++_static",
        "-DCMAKE_BUILD_TYPE=Release",
        "-G", "Ninja",
        "-DCMAKE_MAKE_PROGRAM=%s" % ninja_exe,
        "-DCMAKE_PROJECT_TOP_LEVEL_INCLUDES=%s" % init_file.resolve().as_posix(),
        "-DSTRATUM_LOG_ENABLED=%d" % (1 if args.log_enabled else 0),
    ], check=True)
    subprocess.run([cmake_exe, "--build", str(build_dir), "--config", "Release", "--parallel"],
                   check=True)
    elapsed = round(time.time() - t0, 2)

    # ── 5. Name + emit the deliverable ───────────────────────────────────
    # The filename encodes ONLY the two hard binary-compatibility constraints:
    # embedded CPython ABI and device architecture. NDK version, android.jar
    # API level and Stratum's own version are NOT in the name — they live
    # inside the .so as stratum.build_info(), plus the sidecar .json below
    # for humans/CI browsing without loading the library.
    py_tag = "cp" + "".join(python_paths["py_ver"].split("."))
    so_src = next(build_dir.rglob("libstratum.so"))
    so_dest = output_dir / ("libstratum-%s-%s.so" % (py_tag, args.abi.replace("-", "_")))
    shutil.copy2(so_src, so_dest)

    sidecar = dict(build_info)
    sidecar["file"] = so_dest.name
    sidecar["python_tag"] = py_tag
    sidecar["note"] = ("Rename to libstratum.so when placing under "
                       "jniLibs/<abi>/ in a consuming project.")
    (output_dir / (so_dest.stem + ".json")).write_text(
        json.dumps(sidecar, indent=2), encoding="utf-8")

    size_mb = round(so_dest.stat().st_size / (1024 * 1024), 2)
    print_header("STAGE 10 COMPLETE")
    print(" %s (%s MB) built in %ss" % (so_dest.name, size_mb, elapsed))
    print(" -> %s" % so_dest.resolve())
    print(" Place under app/src/main/jniLibs/%s/libstratum.so" % args.abi)


if __name__ == "__main__":
    main()