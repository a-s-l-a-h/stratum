#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Stratum Pipeline — Stage 08: Python Wrapper & Stub Emitter
=============================================================
LOCATION: 08_pyi_emit/main.py
VERSION: v9 (merges v8 + v8fix + Patch C/D: non-silent parent-class
              fallback, deterministic caching, extended overload
              type-check table matching the new v9 tag set)

WHAT THIS STAGE EMITS (into 08_pyi_emit/output/)
    stratum/core/stratum_object.py
        Base class for every generated wrapper. Owns the JNI global-ref
        pointer (`_ptr`), releases it in __del__, and provides two lazy
        resolution helpers used by every generated class:
          _get_parent_class(fqn) -> resolves a parent Python class at
                                     IMPORT time of the subclass.
          _wrap_instance(ptr,fqn)-> resolves a RETURN-value wrapper class
                                     at CALL time.
        Both fall back to the generic StratumObject if the target class
        wasn't emitted into this build (excluded by targets.json) — this
        is what prevents "class needs a class that isn't in this build"
        from ever crashing anything. v9 change: this fallback is no
        longer SILENT — see "v9 FIX (parent-class resolution)" below.
    stratum/android/**/<ClassName>.py
        One executable Python class per Java class. Every method body is
        a short call into stratum._stratum.call_*(ptr, class_id, slot,
        *args). Both the ORIGINAL Java method name (camelCase, e.g.
        setText) and a snake_case alias (set_text) are defined.
    stratum/android/**/<ClassName>.pyi
        Type stubs for IDE autocomplete, kept in sync with the .py file.

WHY INNER CLASSES ($) ARE NORMALIZED CONSISTENTLY EVERYWHERE
    Java: android.view.View$OnClickListener
    On disk: stratum/android/view/View_OnClickListener.py
    In imports: stratum.android.view.View_OnClickListener
    _normalize_fqn() in stratum_object.py performs this EXACT same
    "$" -> "_" substitution before building the dotted import path.
    sanitize_id() below must keep producing the identical output, or
    inner-class imports fail with ModuleNotFoundError.

v9 FIX (parent-class / return-type resolution — audit issue #3):
    v8's _get_parent_class() / _wrap_instance() caught EVERY exception
    (including real bugs, not just "class excluded from this build")
    and silently substituted StratumObject with no trace. That hides
    genuine problems (a syntax error in a generated file, a broken
    circular import) behind a class that LOOKS like it worked but whose
    isinstance() checks silently return wrong answers everywhere else in
    the app.
    v9 changes this to:
      1. Distinguish "module genuinely not present in this build"
         (ModuleNotFoundError — expected, silent, this is the normal
         "class was excluded by targets.json" case) from "module IS
         present but failed to import/resolve for some other reason"
         (any other exception — now printed as a visible warning to
         stderr, with an optional --stratum-debug traceback).
      2. Cache BOTH successes and failures, so the resolution outcome
         for a given FQN is deterministic for the lifetime of the
         process — it can't silently flip between StratumObject and the
         real class depending on import order/timing.
    This does not change behaviour for a correctly-built wheel (nothing
    should ever hit branch 2); it only makes a real problem visible
    instead of hiding it as an apparently-working-but-wrong class.

OVERLOAD DISPATCH
    Java allows multiple methods with the same name and different
    parameter lists; Python doesn't. Overloads sharing a name are
    dispatched at runtime by argument COUNT first, then by a cheap
    isinstance() check on the first differing argument if two overloads
    happen to share the same argument count. v9 extends the type-check
    table to cover every tag added in 05_resolve/main.py's v9
    compute_param_tags() (arrays, List/Collection) — v8's table only
    covered primitives/strings and would fall through to `True` (i.e.
    "always matches", picking whichever overload happened to sort
    first) for any array/list-typed first parameter.

FIELD ACCESS (sf_get_*/f_get_*/sf_set_*)
    Mirrors the field_get_*/field_set_* functions in stratum_engine.cpp.
    Static constants use `sf_`, instance fields use `f_`, matching the
    original per-class codegen pipeline's naming convention.
"""

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

# Must stay in sync with RET_TYPE_MAP in 05_resolve/main.py and the
# switch in stratum_engine.cpp.
CALL_DISPATCH = {
    0: "call_v", 1: "call_z", 2: "call_i", 3: "call_i", 4: "call_i",
    5: "call_i", 6: "call_j", 7: "call_d", 8: "call_d",
    9: "call_str", 10: "call_o", 11: "call_arr",
    12: "call_list", 13: "call_map",
}
FIELD_GET_DISPATCH = {
    1: "field_get_z", 2: "field_get_i", 3: "field_get_i",
    4: "field_get_i", 5: "field_get_i", 6: "field_get_j",
    7: "field_get_d", 8: "field_get_d",
    9: "field_get_str", 10: "field_get_o",
}
FIELD_SET_DISPATCH = {
    1: "field_set_z", 2: "field_set_i", 3: "field_set_i",
    4: "field_set_i", 5: "field_set_i", 6: "field_set_j",
    7: "field_set_d", 8: "field_set_d",
    9: "field_set_str", 10: "field_set_o",
}

_CAMEL_RE1 = re.compile(r"(.)([A-Z][a-z]+)")
_CAMEL_RE2 = re.compile(r"([a-z0-9])([A-Z])")


import keyword

def camel_to_snake(name: str) -> str:
    """
    setText        -> set_text
    getURL         -> get_url        (handles runs of capitals)
    isEnabled      -> is_enabled
    onTouchEvent   -> on_touch_event
    """
    s1 = _CAMEL_RE1.sub(r"\1_\2", name)
    s2 = _CAMEL_RE2.sub(r"\1_\2", s1)
    out = s2.lower()
    out = re.sub(r"[^a-zA-Z0-9_]", "_", out)
    out = re.sub(r"_+", "_", out).strip("_") or "_unknown"
    if out and out[0].isdigit():
        out = "_" + out
    if keyword.iskeyword(out) or out in ("id", "type", "list", "dict", "str", "int", "float", "object"):
        out += "_"
    return out


def sanitize_id(s: str) -> str:
    """Make any Java identifier safe as a C/Python identifier. Inner
    classes containing '$' become '_' (e.g. View$OnClickListener ->
    View_OnClickListener) — this MUST match _normalize_fqn() in the
    stratum_object.py template emitted below, character for character."""
    s = re.sub(r"[^a-zA-Z0-9_]", "_", s)
    if s and s[0].isdigit():
        s = "_" + s
    s = re.sub(r"_+", "_", s).strip("_") or "_unknown"
    if keyword.iskeyword(s):
        s += "_"
    return s


def fqn_to_module_parts(fqn: str) -> tuple:
    parts = fqn.split(".")
    pkg_parts = parts[:-1]
    cls_name = sanitize_id(parts[-1])
    return pkg_parts, cls_name


# v9 FIX: extended to cover every tag emitted by 05_resolve's v9
# compute_param_tags(). Without this, an overload whose FIRST
# differing parameter is e.g. an int[] ("]") or a List ("M") would
# fall through to the "True" default and always match the first
# overload tried, regardless of what was actually passed.
_OVERLOAD_TYPE_CHECK = {
    "I": "isinstance(args[0], int)",
    "J": "isinstance(args[0], int)",
    "S": "isinstance(args[0], int)",
    "B": "isinstance(args[0], int)",
    "s": "isinstance(args[0], str)",
    "F": "isinstance(args[0], (float, int))",
    "D": "isinstance(args[0], (float, int))",
    "Z": "isinstance(args[0], bool)",
    "[": "isinstance(args[0], (bytes, bytearray))",
    "]": "isinstance(args[0], list)",
    "q": "isinstance(args[0], list)",
    "f": "isinstance(args[0], list)",
    "d": "isinstance(args[0], list)",
    "b": "isinstance(args[0], list)",
    "c": "isinstance(args[0], list)",
    "h": "isinstance(args[0], list)",
    "T": "isinstance(args[0], list)",
    "A": "isinstance(args[0], list)",
    "M": "isinstance(args[0], list)",
}


def _overload_condition(tag0: str, param0: dict) -> str:
    """Build the runtime disambiguation check for the FIRST differing
    parameter between two overloads sharing the same argument count.
    Primitives/strings/arrays use the static table above. Plain-object
    params (tag 'L') have no static type info in Python, so instead of
    always matching (the old bug — this silently picked whichever
    overload sorted first alphabetically, e.g. Handler(Callback) beating
    Handler(Looper) every time), compare the wrapped object's concrete
    Java FQN against this overload's declared parameter type. Adapter/
    proxy params ('a'/'p') accept a callable or dict with no _FQN, so
    those still match by default — only concrete StratumObject args are
    checked."""
    if tag0 in _OVERLOAD_TYPE_CHECK:
        return _OVERLOAD_TYPE_CHECK[tag0]
    if tag0 in ("L", "a", "p"):
        java_type = param0.get("java_type", "")
        if java_type:
            return (
                f"(getattr(args[0], '_FQN', None) == '{java_type}') "
                f"or not hasattr(args[0], '_FQN')"
            )
    return "True"


def build_method_dispatcher(group: list, class_id: int) -> list:
    """Emits the Python body for one (possibly overloaded) method name.
    `group` is the list of all overloads sharing the same Java method
    name — usually length 1, sometimes more."""
    first = group[0]
    jname = first["name"]
    py_name = sanitize_id(jname)
    snake_name = camel_to_snake(jname)
    is_static = first.get("is_static", False)
    lines = []

    if is_static:
        lines.append("    @staticmethod")
        lines.append(f"    def {py_name}(*args):")
    else:
        lines.append(f"    def {py_name}(self, *args):")

    def emit_call(m, indent="        "):
        slot = m["slot"]
        ret_id = m.get("ret_type_id", 10)
        fn = CALL_DISPATCH.get(ret_id, "call_o")
        target = "0" if is_static else "self._ptr"
        if ret_id in (11, 12, 13):
            return f"{indent}return _core.{fn}({target}, {class_id}, {slot}, *args)"
        if ret_id == 10 and m.get("return_fqn"):
            return (f"{indent}_ptr = _core.{fn}({target}, {class_id}, {slot}, *args)\n"
                    f"{indent}return _wrap_instance(_ptr, '{m['return_fqn']}')")
        return f"{indent}return _core.{fn}({target}, {class_id}, {slot}, *args)"

    if len(group) == 1:
        lines.append(emit_call(first))
    else:
        by_argc = defaultdict(list)
        for m in group:
            by_argc[len(m.get("params", []))].append(m)

        lines.append("        argc = len(args)")
        for argc, overloads in sorted(by_argc.items()):
            lines.append(f"        if argc == {argc}:")
            if len(overloads) == 1:
                lines.append(emit_call(overloads[0], indent="            "))
            else:
                for ov in overloads:
                    tag0 = ov["param_tags"][0] if ov["param_tags"] else "L"
                    param0 = ov["params"][0] if ov.get("params") else {}
                    cond = _overload_condition(tag0, param0)
                    lines.append(f"            if {cond}:")
                    lines.append(emit_call(ov, indent="                "))

        # Fallback: if nothing above matched exactly (e.g. a subclass of
        # the expected type slipped through the isinstance check), still
        # try the first overload's slot rather than raising NameError.
        lines.append(emit_call(group[0], indent="        "))

    if snake_name != py_name:
        lines.append(f"    {snake_name} = {py_name}")
    lines.append("")
    return lines


def build_field_accessors(fields: list, class_id: int) -> list:
    """Emits sf_get_*/f_get_*/sf_set_* wrappers for every static/instance
    field, mirroring field_get_*/field_set_* in stratum_engine.cpp."""
    lines = []
    for f in fields:
        fslot = f.get("slot", 0)
        fget = FIELD_GET_DISPATCH.get(f.get("ret_type_id", 10), "field_get_o")
        # Sanitize field names so internal compiler fields like $assertionsDisabled
        # don't produce invalid Python syntax with '$' characters
        fname = sanitize_id(f.get("name", "unknown"))
        is_static = f.get("is_static", False)
        prefix = "sf" if is_static else "f"
        target = "0" if is_static else "self._ptr"

        if is_static:
            lines.append("    @staticmethod")
            lines.append(f"    def {prefix}_get_{fname}():")
            if fget == "field_get_o":
                lines.append(f"        _ptr = _core.{fget}({target}, {class_id}, {fslot})")
                lines.append(f"        return StratumObject(_ptr=_ptr) if _ptr else None")
            else:
                lines.append(f"        return _core.{fget}({target}, {class_id}, {fslot})")
        else:
            lines.append(f"    def {prefix}_get_{fname}(self):")
            if fget == "field_get_o":
                lines.append(f"        _ptr = _core.{fget}(self._ptr, {class_id}, {fslot})")
                lines.append(f"        return StratumObject(_ptr=_ptr) if _ptr else None")
            else:
                lines.append(f"        return _core.{fget}(self._ptr, {class_id}, {fslot})")
        lines.append("")

        # Every field type now has a matching engine setter — previously
        # only int-family fields (ret_type_id 2-5) were writable even
        # though the engine could always read bool/long/double/String/
        # object fields.
        if not f.get("is_final", False):
            rtid = f.get("ret_type_id", 10)
            fset = FIELD_SET_DISPATCH.get(rtid)
            if fset:
                val_expr = "getattr(val, '_ptr', val) or 0" if rtid == 10 else "val"
                if is_static:
                    lines.append("    @staticmethod")
                    lines.append(f"    def {prefix}_set_{fname}(val):")
                    lines.append(f"        _core.{fset}({target}, {class_id}, {fslot}, {val_expr})")
                else:
                    lines.append(f"    def {prefix}_set_{fname}(self, val):")
                    lines.append(f"        _core.{fset}(self._ptr, {class_id}, {fslot}, {val_expr})")
                lines.append("")
    return lines


def emit_python_class(data: dict) -> str:
    fqn = data["fqn"]
    class_id = data.get("class_id", 0)
    _, simple = fqn_to_module_parts(fqn)
    parent_fqn = data.get("parent_fqn", "")

    lines = [
        f"# Auto-generated Stratum wrapper for {fqn}. DO NOT EDIT.",
        "from __future__ import annotations",
        "import stratum._stratum as _core",
        "from stratum.core.stratum_object import StratumObject, _wrap_instance, _get_parent_class",
        "",
        f"class {simple}(_get_parent_class('{parent_fqn}')):",
        f'    """Wraps Java class `{fqn}`."""',
        f"    _CLASS_ID = {class_id}",
        f"    _FQN = '{fqn}'",
        "",
    ]

    # ── Constructors ─────────────────────────────────────────────────
    ctors = data.get("constructors", [])
    lines.append("    def __init__(self, *args, _ptr=None) -> None:")
    lines.append("        # _ptr is set when Stratum is wrapping an EXISTING jobject")
    lines.append("        # pointer (e.g. as a method return value); otherwise *args")
    lines.append("        # are forwarded to whichever constructor overload matches.")
    lines.append("        if _ptr is not None:")
    lines.append("            super().__init__(_ptr=_ptr)")
    lines.append("            return")
    if ctors:
        by_argc = defaultdict(list)
        for c in ctors:
            by_argc[len(c.get("params", []))].append(c)
        lines.append("        argc = len(args)")
        for argc, c_list in sorted(by_argc.items()):
            lines.append(f"        if argc == {argc}:")
            if len(c_list) == 1:
                slot = c_list[0]["slot"]
                lines.append(f"            ptr = _core.new_instance({class_id}, {slot}, *args)")
                lines.append("            super().__init__(_ptr=ptr)")
                lines.append("            return")
            else:
                # v9 FIX: multiple constructors share this argc (e.g.
                # Handler(Callback) vs Handler(Looper)) — disambiguate by
                # the first parameter's type instead of always calling
                # whichever constructor happened to sort first.
                for c in c_list:
                    tag0 = c["param_tags"][0] if c.get("param_tags") else "L"
                    param0 = c["params"][0] if c.get("params") else {}
                    cond = _overload_condition(tag0, param0)
                    slot = c["slot"]
                    lines.append(f"            if {cond}:")
                    lines.append(f"                ptr = _core.new_instance({class_id}, {slot}, *args)")
                    lines.append("                super().__init__(_ptr=ptr)")
                    lines.append("                return")
                # Fallback if nothing matched exactly.
                fallback_slot = c_list[0]["slot"]
                lines.append(f"            ptr = _core.new_instance({class_id}, {fallback_slot}, *args)")
                lines.append("            super().__init__(_ptr=ptr)")
                lines.append("            return")
        first_slot = ctors[0]["slot"]
        lines.append(f"        ptr = _core.new_instance({class_id}, {first_slot}, *args)")
        lines.append("        super().__init__(_ptr=ptr)")
    else:
        lines.append("        super().__init__(_ptr=None)")
    lines.append("")

    # ── Methods (grouped by name for overload dispatch) ─────────────
    grouped = defaultdict(list)
    for m in data.get("methods", []):
        if not m.get("is_constructor"):
            grouped[m["name"]].append(m)
    for _, group in grouped.items():
        lines.extend(build_method_dispatcher(group, class_id))

    # ── Fields (static SDK constants + any mutable fields) ──────────
    lines.extend(build_field_accessors(data.get("fields", []), class_id))

    return "\n".join(lines)


def emit_pyi_stub(data: dict) -> str:
    fqn = data["fqn"]
    _, simple = fqn_to_module_parts(fqn)
    lines = [
        f"# Stubs for {fqn}",
        "from __future__ import annotations",
        "from typing import Any, Optional",
        "from stratum.core.stratum_object import StratumObject",
        "",
        f"class {simple}(StratumObject):",
        "    def __init__(self, *args: Any, _ptr: Optional[int] = None) -> None: ...",
        f"    @classmethod",
        f"    def from_ptr(cls, obj_or_ptr: Any) -> {simple}: ...",
        f"    @classmethod",
        f"    def _stratum_cast(cls, obj_or_ptr: Any) -> {simple}: ...",
    ]
    grouped = defaultdict(list)
    for m in data.get("methods", []):
        if not m.get("is_constructor"):
            grouped[m["name"]].append(m)

    for jname, group in grouped.items():
        py_name = sanitize_id(jname)
        snake_name = camel_to_snake(jname)
        m = group[0]
        if m.get("is_static"):
            lines.append("    @staticmethod")
            lines.append(f"    def {py_name}(*args: Any) -> Any: ...")
            if snake_name != py_name:
                lines.append(f"    {snake_name} = {py_name}")
        else:
            lines.append(f"    def {py_name}(self, *args: Any) -> Any: ...")
            if snake_name != py_name:
                lines.append(f"    {snake_name} = {py_name}")

    for f in data.get("fields", []):
        fname = sanitize_id(f.get("name", "unknown"))
        is_static = f.get("is_static", False)
        prefix = "sf" if is_static else "f"
        if is_static:
            lines.append("    @staticmethod")
            lines.append(f"    def {prefix}_get_{fname}() -> Any: ...")
        else:
            lines.append(f"    def {prefix}_get_{fname}(self) -> Any: ...")

    return "\n".join(lines)


# v9: STRATUM_OBJECT_PY now distinguishes "class genuinely not in this
# build" (silent, expected) from "class IS in this build but failed to
# resolve" (a real bug — now printed as a warning instead of hidden).
# Both success and failure are cached so resolution is deterministic for
# the whole process lifetime instead of depending on import ordering.
STRATUM_OBJECT_PY = '''# Auto-generated by Stratum Core. DO NOT EDIT.
"""
Base class for every generated Stratum wrapper, plus two lazy
class-resolution helpers used everywhere in the generated tree.

Why lazy resolution matters:
    Every generated <ClassName>.py file needs to know its Python PARENT
    class (for `class Foo(Bar):`) and, at call time, the Python RETURN
    class for any method returning a Java object. Resolving either of
    these EAGERLY at Stage 08 codegen time would reintroduce exactly the
    problem this rewrite exists to remove: if that target class was
    excluded from the build (targets.json filtering), Python would abort
    at import time.

    Instead, BOTH _get_parent_class() and _wrap_instance() import lazily
    via importlib, cache the result (success OR failure), and fall back
    to the generic StratumObject only when the class genuinely isn't
    part of this build. Nothing in this tree can fail with an
    ImportError bubbling up into your application code.

v9: the fallback used to catch EVERY exception silently, which could
    hide a real bug (a broken generated file, a genuine circular import)
    behind a class that looks like it worked. Now:
      - ModuleNotFoundError -> silent fallback (expected: class excluded
        from this build via targets.json).
      - any OTHER exception -> a warning is printed to stderr (once per
        FQN) so a real problem is visible, THEN falls back to
        StratumObject so the app keeps running instead of crashing.
    Pass `--stratum-debug` as a sys.argv flag (e.g. via Chaquopy's
    app args, or just append it in your own main.py before this module
    is first imported) to get a full traceback for these warnings.
"""
import importlib
import sys
import traceback
import stratum._stratum as _core

_class_cache = {}      # fqn -> resolved class (success cache)
_failed_fqns = set()   # fqn -> already warned about (avoid log spam)


def _normalize_fqn(fqn: str) -> tuple:
    """Java: android.view.View$OnClickListener
       -> pkg="android.view", cls_name="View_OnClickListener"
    MUST match sanitize_id()'s "$" -> "_" substitution in
    08_pyi_emit/main.py exactly, or inner-class imports will fail."""
    import re
    parts = fqn.split(".")
    pkg = ".".join(parts[:-1])
    s = re.sub(r"[^a-zA-Z0-9_]", "_", parts[-1])
    if s and s[0].isdigit():
        s = "_" + s
    cls_name = re.sub(r"_+", "_", s).strip("_") or "_unknown"
    return pkg, cls_name


def _warn_once(fqn: str, context: str, exc: Exception) -> None:
    if fqn in _failed_fqns:
        return
    _failed_fqns.add(fqn)
    print(f"[Stratum] WARNING: {context} for '{fqn}' failed "
          f"({type(exc).__name__}: {exc}); falling back to StratumObject.",
          file=sys.stderr)
    if "--stratum-debug" in sys.argv:
        traceback.print_exc()


def _get_parent_class(parent_fqn: str):
    if not parent_fqn or parent_fqn in ("java.lang.Object", ""):
        return StratumObject

    cached = _class_cache.get(parent_fqn)
    if cached is not None:
        return cached

    try:
        pkg, cls_name = _normalize_fqn(parent_fqn)
        mod = importlib.import_module(f"stratum.{pkg}.{cls_name}")
        cls = getattr(mod, cls_name)
        _class_cache[parent_fqn] = cls
        return cls
    except ModuleNotFoundError:
        # Expected: this class simply wasn't included in the build
        # (excluded by 05_resolve/targets.json). Silent, no warning.
        _class_cache[parent_fqn] = StratumObject
        return StratumObject
    except Exception as e:
        # NOT expected: the module exists but something in it is
        # actually broken. Surface it instead of hiding it.
        _warn_once(parent_fqn, "resolving parent class", e)
        _class_cache[parent_fqn] = StratumObject
        return StratumObject


def _wrap_instance(ptr: int, fqn: str):
    if not ptr:
        return None

    cached = _class_cache.get(fqn)
    if cached is not None:
        return cached(_ptr=ptr)

    try:
        pkg, cls_name = _normalize_fqn(fqn)
        mod = importlib.import_module(f"stratum.{pkg}.{cls_name}")
        cls = getattr(mod, cls_name)
        _class_cache[fqn] = cls
        return cls(_ptr=ptr)
    except ModuleNotFoundError:
        _class_cache[fqn] = StratumObject
        return StratumObject(_ptr=ptr)
    except Exception as e:
        _warn_once(fqn, "wrapping return value", e)
        _class_cache[fqn] = StratumObject
        return StratumObject(_ptr=ptr)


class StratumObject:
    """Base class for all Stratum Java object wrappers. Owns a JNI
    global reference and releases it automatically when garbage
    collected."""

    _CLASS_ID = None
    _FQN = "java.lang.Object"

    def __init__(self, _ptr: int = None) -> None:
        self._ptr = _ptr

    def __del__(self) -> None:
        ptr = getattr(self, "_ptr", None)
        if ptr:
            # Delete callbacks attached to this object instance prefix and free ref
            _core.remove_callbacks_by_prefix(f"obj_{ptr}_")
            _core.delete_ref(ptr)
            self._ptr = None

    def _get_jobject_ptr(self) -> int:
        return self._ptr or 0

    def __bool__(self) -> bool:
        return bool(self._ptr)

    def __eq__(self, other) -> bool:
        if other is None:
            return not self._ptr
        if not isinstance(other, StratumObject):
            return NotImplemented
        if not self._ptr or not other._ptr:
            return self._ptr == other._ptr
        return bool(_core.is_same_object(self._ptr, other._ptr))

    def __ne__(self, other) -> bool:
        eq = self.__eq__(other)
        if eq is NotImplemented:
            return NotImplemented
        return not eq

    def __hash__(self) -> int:
        if not self._ptr:
            return 0
        return int(_core.hash_code(self._ptr))

    def __str__(self) -> str:
        if not self._ptr:
            return "null"
        return _core.to_string(self._ptr)

    def __repr__(self) -> str:
        if not self._ptr:
            return f"<{type(self).__name__} null>"
        s = _core.to_string(self._ptr)
        return f"<{type(self).__name__} ptr=0x{self._ptr:x} str='{s}'>"

    @classmethod
    def from_ptr(cls, obj_or_ptr):
        """Safely cast a pointer or existing StratumObject to this class type,
        verifying type compatibility via JNI IsInstanceOf."""
        if obj_or_ptr is None:
            return None
        ptr = obj_or_ptr._ptr if isinstance(obj_or_ptr, StratumObject) else int(obj_or_ptr)
        if not ptr:
            return None
        if cls._CLASS_ID is not None:
            if not _core.is_instance_of(ptr, cls._CLASS_ID):
                target_name = getattr(cls, "_FQN", cls.__name__)
                raise TypeError(f"Object at 0x{ptr:x} is not an instance of {target_name}")
        return cls(_ptr=_core.clone_ref(ptr))

    @classmethod
    def _stratum_cast(cls, obj_or_ptr):
        """Backward-compatible alias for from_ptr."""
        return cls.from_ptr(obj_or_ptr)
'''


def main():
    ap = argparse.ArgumentParser(description="Stratum Stage 08 - Python & Stub Emitter (v9)")
    ap.add_argument("--input", required=True,
                     help="05_resolve/output_patched/ (the SECOND-pass output)")
    ap.add_argument("--output", required=True, help="08_pyi_emit/output/")
    args = ap.parse_args()

    print("=" * 70)
    print("  STRATUM PIPELINE — STAGE 08 (PYTHON WRAPPER & STUB EMIT) v9")
    print("=" * 70)

    input_dir, output_dir = Path(args.input), Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    core_dir = output_dir / "stratum" / "core"
    core_dir.mkdir(parents=True, exist_ok=True)
    (core_dir / "stratum_object.py").write_text(STRATUM_OBJECT_PY, encoding="utf-8")
    (core_dir / "__init__.py").write_text(
        "from .stratum_object import StratumObject, _wrap_instance, _get_parent_class\n",
        encoding="utf-8")

    json_files = sorted(f for f in input_dir.rglob("*.json")
                         if f.name not in ("parse_summary.json", "resolve_summary.json", "manifest.json"))
    print(f"-> Emitting {len(json_files):,} Python classes.")

    packages = set()
    for jf in json_files:
        try:
            data = json.loads(jf.read_text(encoding="utf-8"))
            fqn = data.get("fqn", "")
            if not fqn:
                continue

            pkg_parts, simple = fqn_to_module_parts(fqn)
            dest_dir = output_dir / "stratum" / Path(*pkg_parts)
            dest_dir.mkdir(parents=True, exist_ok=True)

            (dest_dir / f"{simple}.py").write_text(emit_python_class(data), encoding="utf-8")
            (dest_dir / f"{simple}.pyi").write_text(emit_pyi_stub(data), encoding="utf-8")

            for i in range(len(pkg_parts)):
                packages.add(output_dir / "stratum" / Path(*pkg_parts[: i + 1]))
        except Exception as e:
            print(f"  [WARN] {jf.name}: {e}")

    for pkg in packages:
        init_py, init_pyi = pkg / "__init__.py", pkg / "__init__.pyi"
        if not init_py.exists():
            init_py.write_text("# Stratum Package\n", encoding="utf-8")
        if not init_pyi.exists():
            init_pyi.write_text("# Stratum Package Stubs\n", encoding="utf-8")

    print(f"-> Output generated in: {output_dir / 'stratum'}")


if __name__ == "__main__":
    main()