"""
Stratum Pipeline — Stage 05 : Resolve
========================================
  04_parse/output/        <- Stage 04 JSON (one file per class)
        |
        v
  05_resolve/main.py    <- THIS FILE
        |
        v
  05_resolve/output/    <- Fully-enriched JSON (Stage 06 codegen reads ONLY this)

targets.json format
--------------------
  {
    "enabled": false,
    "closure_mode": "parents_only",
    "targets": [
      { "fqn": "android.widget.TextView", "notes": "..." },
      { "fqn": "android.app.Activity",    "notes": "..." }
    ]
  }

  enabled (bool):
    false  — resolve full corpus, targets list is ignored (default)
    true   — resolve only the listed classes + whatever closure_mode pulls in

  closure_mode (string)  — controls what gets auto-included alongside seeds:
    "parents_only"     — [DEFAULT / RECOMMENDED]
                         Only pulls in parent/ancestor classes.
                         Required for correct C++ struct inheritance chain.
                         8 seeds -> ~30-50 classes. Safe. Use this.

    "parents_and_interfaces"
                         Pulls in parents AND directly-implemented interfaces.
                         Useful if you need interface proxies (Listener etc.)
                         in your seed list and want their parents resolved too.
                         8 seeds -> ~60-120 classes typically.

    "full"             — Pulls in parents + interfaces + method param/return
                         types + depends_on entries.
                         WARNING: causes combinatorial explosion.
                         8 seeds -> 800+ classes. Only use for full-corpus work
                         where you want everything cross-linked.

  Run with --help to see all command-line options.
  Run with --list-modes to print closure mode descriptions and exit.
"""

import argparse
import json
import re
import sys
from pathlib import Path


CLOSURE_MODES = ("parents_only", "parents_and_interfaces", "full")

CLOSURE_MODE_HELP = """\
Closure modes (set in targets.json OR override with --closure-mode):

  parents_only  [DEFAULT / RECOMMENDED]
      Pulls in only parent/ancestor classes.
      Required for C++ struct inheritance — Button -> TextView -> View -> ...
      Every ancestor must be defined or the generated C++ will not compile.
      Interfaces, method deps, depends_on are NOT followed (safe jobject fallback).
      Result: small output, ~30-50 classes for typical seeds. USE THIS.

  parents_and_interfaces
      Pulls in parents AND directly-implemented interfaces (and their parents).
      Use when your seeds include Listener/Callback interfaces and you want
      their full parent chain included too.
      Result: moderate output, ~60-120 classes typically.

  full
      Pulls in parents + interfaces + method param/return types + depends_on.
      WARNING: causes combinatorial explosion — 8 seeds can become 800+ classes,
      which causes nanobind SIGABRT at registration time.
      Only use this if you are resolving the entire corpus (enabled=false).
"""


def print_header(title):
    print("==================================================")
    print(f" {title}")
    print("==================================================")


# =============================================================================
# Helpers
# =============================================================================

def to_cpp_name(fqn: str) -> str:
    return re.sub(r"[.$]", "_", fqn)

def to_guard_macro(fqn: str) -> str:
    return "STRATUM_" + re.sub(r"[.$]", "_", fqn).upper() + "_H"

def jni_call_type(method: dict, is_nonvirtual: bool = False) -> str:
    ret       = method.get("return_jni", "void")
    is_static = method.get("is_static", False)
    suffix_map = {
        "void": "Void", "jboolean": "Boolean", "jbyte": "Byte",
        "jchar": "Char", "jshort": "Short", "jint": "Int",
        "jlong": "Long", "jfloat": "Float", "jdouble": "Double",
        "jstring": "Object", "jobject": "Object",
    }
    type_suffix = suffix_map.get(ret, "Object")
    if is_nonvirtual: return f"CallNonvirtual{type_suffix}Method"
    if is_static:     return f"CallStatic{type_suffix}Method"
    return f"Call{type_suffix}Method"

def jni_get_field_call(jni_type: str, is_static: bool = True) -> str:
    suffix_map = {
        "jboolean": "Boolean", "jbyte": "Byte", "jchar": "Char",
        "jshort": "Short", "jint": "Int", "jlong": "Long",
        "jfloat": "Float", "jdouble": "Double",
        "jstring": "Object", "jobject": "Object",
    }
    suffix = suffix_map.get(jni_type, "Object")
    return ("GetStatic" if is_static else "Get") + suffix + "Field"

def cpp_value_literal(raw, jni_type: str) -> str:
    if raw is None: return ""
    raw = str(raw).strip()
    if jni_type == "jboolean": return "true" if raw.lower() in ("true","1") else "false"
    if jni_type == "jfloat":   return raw if raw.endswith("f") else raw + "f"
    if jni_type == "jlong":    return raw + "LL" if not raw.upper().endswith("L") else raw + "L"
    if jni_type == "jstring":  return f'u8{raw}' if raw.startswith('"') else f'u8"{raw}"'
    return raw

def mangled_name(class_jni: str, method_name: str) -> str:
    if method_name.startswith("<"): return ""
    safe_class  = class_jni.replace("/", "_").replace("$", "_00024")
    safe_method = method_name.replace("_", "_1")
    return f"Java_{safe_class}_{safe_method}"

def method_var_name(method_name: str, overload_index: int, is_static: bool) -> str:
    prefix = "g_smid" if is_static else "g_mid"
    safe   = re.sub(r"[^A-Za-z0-9]", "_", method_name)
    suffix = f"_{overload_index}" if overload_index > 0 else ""
    return f"{prefix}_{safe}{suffix}"

def wrapper_name(simple_class: str, method_name: str, overload_index: int) -> str:
    sc = re.sub(r"[^A-Za-z0-9]", "_", simple_class)
    sm = re.sub(r"[^A-Za-z0-9]", "_", method_name)
    sx = f"_{overload_index}" if overload_index > 0 else ""
    return f"{sc}_{sm}{sx}"

def is_object_jni_type(t: str) -> bool:
    return (t in ("jobject", "jstring", "jobjectArray")
            or (t.endswith("Array") and t != "jbooleanArray"))

def return_jni_suffix(ret_jni: str) -> str:
    m = {
        "void": "Void", "jboolean": "Boolean", "jbyte": "Byte",
        "jchar": "Char", "jshort": "Short", "jint": "Int",
        "jlong": "Long", "jfloat": "Float", "jdouble": "Double",
    }
    return m.get(ret_jni, "Object")


# =============================================================================
# Registry
# =============================================================================

def load_registry(parse_dir: Path) -> dict:
    registry = {}
    skipped  = 0
    for jf in sorted(parse_dir.rglob("*.json")):
        if jf.name in ("parse_summary.json", "resolve_summary.json"):
            continue
        try:
            data = json.loads(jf.read_text(encoding="utf-8"))
            fqn  = data.get("fqn", "")
            if fqn:
                registry[fqn] = data
            else:
                skipped += 1
        except Exception as e:
            print(f"  WARN  load failed: {jf.name}  ({e})")
            skipped += 1
    if skipped:
        print(f"  WARN  {skipped} files had no FQN and were skipped")
    return registry


# =============================================================================
# Target closure — mode-controlled
# =============================================================================

def compute_target_closure(seed_fqns: list, registry: dict,
                           closure_mode: str) -> set:
    """
    Expand seed classes into a self-consistent set for C++ codegen.

    closure_mode controls what gets pulled in beyond the seeds themselves:

    "parents_only"  [RECOMMENDED]
        Walk the parent chain for every class in the closure.
        This is the minimum required for correct C++ struct inheritance.
            struct Button : TextView : View : ...
        Every ancestor must be present or the generated C++ will not compile.
        Interfaces and method types are NOT followed — they fall back to
        jobject in Stage 06 codegen, which is safe and correct.

    "parents_and_interfaces"
        As above, plus directly-implemented interfaces (and their parents).
        Use when you have Listener/Callback seeds that need their parent
        interfaces resolved.

    "full"
        As above, plus method param/return types and depends_on entries.
        WARNING: causes combinatorial explosion (8 seeds -> 800+ classes).
        This triggers SIGABRT during nanobind registration at runtime.
        Only use when enabled=false (full corpus resolve).
    """
    if closure_mode not in CLOSURE_MODES:
        print(f"  WARN  unknown closure_mode '{closure_mode}', "
              f"falling back to 'parents_only'")
        closure_mode = "parents_only"

    closure: set  = set()
    queue:   list = []

    for fqn in seed_fqns:
        if fqn not in registry:
            print(f"  WARN  target '{fqn}' not found in corpus — skipping")
            continue
        if fqn not in closure:
            closure.add(fqn)
            queue.append(fqn)

    def enqueue(candidate: str) -> None:
        if candidate and candidate in registry and candidate not in closure:
            closure.add(candidate)
            queue.append(candidate)

    while queue:
        fqn  = queue.pop()
        data = registry.get(fqn)
        if data is None:
            continue

        # ── Always: walk parent chain ────────────────────────────────────────
        # Required for C++ struct inheritance — every ancestor must be defined.
        enqueue(data.get("parent_fqn", ""))

        # ── parents_and_interfaces / full: add interfaces ────────────────────
        if closure_mode in ("parents_and_interfaces", "full"):
            for iface in data.get("interfaces", []):
                enqueue(iface)

        # ── full only: add method param/return types + depends_on ────────────
        # WARNING: this causes combinatorial explosion.
        if closure_mode == "full":
            for method in data.get("methods", []):
                ret_class = method.get("return_jni_class") or method.get("jni_class")
                if ret_class:
                    enqueue(ret_class.replace("/", "."))
                for param in method.get("params", []):
                    pc = param.get("jni_class") or param.get("proxy_interface")
                    if pc:
                        enqueue(pc.replace("/", "."))
            for dep_jni in data.get("depends_on", []):
                enqueue(dep_jni.replace("/", "."))

    return closure


# =============================================================================
# targets.json loader / creator
# =============================================================================

def load_or_create_targets(targets_file: Path,
                           registry: dict,
                           cli_closure_mode: str) -> tuple:
    """
    Read 05_resolve/targets.json.
    Returns (filter_enabled: bool, seed_fqns: list[str], closure_mode: str).

    cli_closure_mode (non-empty string) overrides the value in the JSON.
    Creates a starter file if it does not exist yet (never overwrites).
    """
    default = {
        "enabled": False,
        "closure_mode": "parents_only",
        "_comment_enabled": (
            "Set enabled=true to filter the resolve output to only the listed "
            "classes plus whatever closure_mode auto-includes. "
            "enabled=false resolves the full corpus (original behaviour)."
        ),
        "_comment_closure_mode": (
            "parents_only [RECOMMENDED ~30-50 classes] | "
            "parents_and_interfaces [~60-120 classes] | "
            "full [WARNING: 800+ classes, causes SIGABRT crash at runtime]"
        ),
        "targets": [
            {"fqn": "android.app.Activity",    "notes": "example — edit freely"},
            {"fqn": "android.widget.TextView", "notes": "example — edit freely"},
            {"fqn": "android.widget.Button",   "notes": "example — edit freely"},
        ]
    }

    if not targets_file.exists():
        targets_file.parent.mkdir(parents=True, exist_ok=True)
        targets_file.write_text(json.dumps(default, indent=2), encoding="utf-8")
        print(f"-> Created starter targets file : {targets_file}")
        print("   Edit it and set \"enabled\": true to activate filtering.")
        return False, [], "parents_only"

    print(f"-> [SAFE] {targets_file} already exists — loading.")
    raw     = json.loads(targets_file.read_text(encoding="utf-8"))
    enabled = bool(raw.get("enabled", False))
    seeds   = [t["fqn"] for t in raw.get("targets", []) if t.get("fqn")]

    # CLI flag overrides JSON when explicitly provided
    if cli_closure_mode:
        closure_mode = cli_closure_mode
        print(f"   closure_mode : {closure_mode}  "
              f"(overridden by --closure-mode CLI flag)")
    else:
        closure_mode = raw.get("closure_mode", "parents_only")
        print(f"   closure_mode : {closure_mode}  (from targets.json)")

    if closure_mode not in CLOSURE_MODES:
        print(f"   WARN  invalid closure_mode '{closure_mode}' — "
              f"valid values: {', '.join(CLOSURE_MODES)}")
        print(f"   WARN  falling back to 'parents_only'")
        closure_mode = "parents_only"

    return enabled, seeds, closure_mode


# =============================================================================
# Ancestry / Interfaces / MRO
# =============================================================================

def build_ancestry(fqn: str, registry: dict) -> tuple:
    ancestry   = []
    unresolved = []
    seen = {fqn}
    cur  = registry.get(fqn, {}).get("parent_fqn", "")
    while cur and cur not in seen:
        seen.add(cur)
        ancestry.append(cur)
        if cur not in registry:
            unresolved.append(cur)
            break
        cur = registry[cur].get("parent_fqn", "")
    return ancestry, unresolved

def build_ancestry_details(ancestry: list, registry: dict) -> list:
    details = []
    for depth, anc in enumerate(ancestry, start=1):
        d = registry.get(anc, {})
        details.append({
            "fqn":           anc,
            "jni_name":      anc.replace(".", "/"),
            "cpp_name":      to_cpp_name(anc),
            "simple_name":   anc.split(".")[-1],
            "depth":         depth,
            "in_corpus":     anc in registry,
            "is_abstract":   d.get("is_abstract",   False),
            "is_interface":  d.get("is_interface",  False),
            "is_annotation": d.get("is_annotation", False),
            "is_enum":       d.get("is_enum",       False),
            "is_final":      d.get("is_final",      False),
            "parent_fqn":    d.get("parent_fqn",    ""),
            "method_count":  len(d.get("methods",   [])),
            "field_count":   len(d.get("fields",    [])),
        })
    return details

def collect_all_interfaces(fqn: str, registry: dict, cache: dict) -> list:
    if fqn in cache: return cache[fqn]
    data = registry.get(fqn)
    if data is None:
        cache[fqn] = []
        return []
    cache[fqn] = []
    result = []
    def add(x):
        if x not in result: result.append(x)
    for iface in data.get("interfaces", []):
        add(iface)
        for sub in collect_all_interfaces(iface, registry, cache): add(sub)
    parent = data.get("parent_fqn", "")
    if parent:
        for sub in collect_all_interfaces(parent, registry, cache): add(sub)
    cache[fqn] = result
    return result

def build_interface_details(fqn: str, all_ifaces: list,
                            ancestry: list, registry: dict) -> list:
    own_ifaces = set(registry.get(fqn, {}).get("interfaces", []))
    anc_source = {}
    for anc in ancestry:
        for iface in registry.get(anc, {}).get("interfaces", []):
            if iface not in anc_source: anc_source[iface] = anc
    details = []
    for iface in all_ifaces:
        source = ("self" if iface in own_ifaces
                  else f"ancestor:{anc_source.get(iface,'?')}")
        d = registry.get(iface, {})
        details.append({
            "fqn":           iface,
            "jni_name":      iface.replace(".", "/"),
            "cpp_name":      to_cpp_name(iface),
            "simple_name":   iface.split(".")[-1],
            "in_corpus":     iface in registry,
            "source":        source,
            "is_annotation": d.get("is_annotation", False),
            "method_count":  len(d.get("methods", [])),
        })
    return details

def build_mro(fqn: str, registry: dict, _seen: set = None) -> list:
    if _seen is None: _seen = set()
    if fqn in _seen:  return []
    _seen.add(fqn)
    mro  = [fqn]
    data = registry.get(fqn, {})
    parent = data.get("parent_fqn", "")
    if parent:
        for x in build_mro(parent, registry, _seen):
            if x not in mro: mro.append(x)
    for iface in data.get("interfaces", []):
        for x in build_mro(iface, registry, _seen):
            if x not in mro: mro.append(x)
    return mro


# =============================================================================
# Method enrichment
# =============================================================================

def enrich_method(m: dict, class_jni: str, simple_class: str,
                  is_nonvirtual: bool = False) -> dict:
    m.setdefault("is_deprecated", False)
    m.setdefault("is_varargs",    False)
    m.setdefault("is_abstract",   False)
    m.setdefault("is_native",     False)
    params    = m.get("params", [])
    ret_jni   = m.get("return_jni", "void")
    is_static = m.get("is_static", False)
    ov_idx    = m.get("overload_index", 0)
    mname     = m.get("name", "")
    m["visibility"]          = "protected" if m.get("is_protected", False) else "public"
    m["jni_method_var"]      = method_var_name(mname, ov_idx, is_static)
    m["jni_call_type"]       = jni_call_type(m, is_nonvirtual)
    m["return_jni_call"]     = return_jni_suffix(ret_jni)
    m["native_mangled_name"] = mangled_name(class_jni, mname)
    m["cpp_wrapper_name"]    = wrapper_name(simple_class, mname, ov_idx)
    m["throws_jni"]          = [t.replace(".", "/") for t in m.get("throws", [])]
    has_obj_params = any(is_object_jni_type(p.get("jni_type","")) for p in params)
    has_obj_return = is_object_jni_type(ret_jni)
    m["param_count"]       = len(params)
    m["has_object_params"] = has_obj_params
    m["has_object_return"] = has_obj_return
    m["needs_local_frame"] = has_obj_params or has_obj_return
    return m


# ── Validation ────────────────────────────────────────────────────────────────

def validate_resolved_class(data: dict) -> list:
    """
    Validate only what Stage 06 actually needs.
    Skips checks that do not apply to the class type.
    Returns list of error strings. Empty list = all good.
    """
    errors = []
    fqn = data.get("fqn", "<unknown>")

    # Interfaces and annotations never have constructors
    # Stage 06 does not generate NewObject for them
    is_interface  = data.get("is_interface",  False)
    is_annotation = data.get("is_annotation", False)
    skip_ctor_check = is_interface or is_annotation

    # ── Constructor checks ────────────────────────────────────────────────────
    # Only if class is not interface/annotation AND has constructors
    if not skip_ctor_check:
        for m in data.get("constructors", []):
            if not m.get("jni_signature"):
                errors.append(
                    f"{fqn}.<init>[{m.get('ctor_index', 0)}] "
                    f"missing jni_signature"
                )
            if not m.get("jni_new_sig"):
                errors.append(
                    f"{fqn}.<init>[{m.get('ctor_index', 0)}] "
                    f"missing jni_new_sig — was Patch 1 applied?"
                )
            ret = m.get("return_jni", "")
            if ret not in ("void", "V", ""):
                errors.append(
                    f"{fqn}.<init>[{m.get('ctor_index', 0)}] "
                    f"return_jni='{ret}' should be void"
                )

    # ── Declared and overridden method checks ─────────────────────────────────
    # Inherited methods skipped — Stage 06 does not generate wrappers for them
    active_methods = (
        data.get("declared_methods",  []) +
        data.get("overridden_methods", [])
    )
    for m in active_methods:
        mname = m.get("name", "?")

        if not m.get("jni_signature") and not m.get("is_abstract"):
            errors.append(
                f"{fqn}#{mname} missing jni_signature"
            )

        if m.get("needs_proxy"):
            iface_jni = m.get("proxy_interface_jni", "")
            if not iface_jni:
                errors.append(
                    f"{fqn}#{mname} needs_proxy=True "
                    f"but proxy_interface_jni is empty"
                )
            elif "." in iface_jni:
                errors.append(
                    f"{fqn}#{mname} proxy_interface_jni has dots "
                    f"(must use slashes): '{iface_jni}'"
                )

    return errors


def handle_validation_errors(fqn: str, errors: list,
                              skip_all: list) -> bool:
    """
    Show validation errors and ask user what to do.

    skip_all is a single-element list [False] passed by reference.
    If user chooses 'A' (continue all), sets skip_all[0] = True
    so caller can skip future prompts.

    Returns:
        True  = continue (ignore errors for this class)
        False = stop pipeline
    """
    print()
    print(f"  {'─' * 55}")
    print(f"  VALIDATION ERRORS in: {fqn}")
    print(f"  {'─' * 55}")
    for err in errors:
        print(f"    ✗ {err}")
    print(f"  {'─' * 55}")

    # If user already chose 'A' (continue all), skip the prompt
    if skip_all[0]:
        print(f"  [AUTO-CONTINUE] Skipping prompt (Continue All active)")
        print(f"  {'─' * 55}")
        print()
        return True

    print()
    print("  What do you want to do?")
    print("  [S] Stop pipeline — fix errors first  (recommended)")
    print("  [C] Continue this class — skip errors for now")
    print("  [A] Continue All — skip prompts for all remaining errors")
    print()

    while True:
        try:
            choice = input("  Choice [S/C/A]: ").strip().upper()
        except (EOFError, KeyboardInterrupt):
            # Non-interactive environment or Ctrl+C — stop safely
            print()
            print("  Stopped by user.")
            return False

        if choice == "S":
            print()
            print("  Pipeline stopped. Fix the errors above and rerun Stage 05.")
            print()
            return False
        elif choice == "C":
            print()
            print(f"  Continuing with {fqn} despite errors.")
            print()
            return True
        elif choice == "A":
            skip_all[0] = True
            print()
            print("  Continuing all — no more prompts for validation errors.")
            print()
            return True
        else:
            print("  Invalid choice. Enter S, C, or A.")


def enrich_constructor(m: dict, ctor_index: int,
                       class_jni: str, simple_class: str) -> dict:
    m = enrich_method(m, class_jni, simple_class)
    m["ctor_index"]    = ctor_index
    # PATCH 4: Prefer jni_new_sig set by Stage 04 Patch 1.
    # Fall back to jni_signature if not present.
    # Then explicitly force constructor return types to void/V
    # so jni_call_type and generated C++ are always correct,
    # regardless of what enrich_method computed from return_jni.
    m["jni_new_sig"]        = m.get("jni_new_sig") or m.get("jni_signature", "")
    m["is_constructor"]     = True
    m["return_jni"]         = "void"
    m["return_cpp"]         = "void"
    m["return_python"]      = "None"
    m["return_java_type"]   = "void"
    m["return_conversion"]  = "none"
    m["is_void"]            = True
    safe_cls                = re.sub(r"[^A-Za-z0-9]", "_", simple_class)
    m["cpp_ctor_name"]      = f"{safe_cls}_new" + (f"_{ctor_index}" if ctor_index else "")
    return m

def mkey(m: dict) -> str:
    return f"{m['name']}{m.get('jni_signature','')}"

def resolve_methods(own_methods: list, ancestry: list, registry: dict,
                    class_jni: str, simple_class: str):
    own_key_set   = {mkey(m) for m in own_methods}
    overrides_map = {}
    inherited     = []
    seen_inh      = set(own_key_set)
    unresolved    = []

    for depth, anc_fqn in enumerate(ancestry, start=1):
        if anc_fqn not in registry:
            if anc_fqn not in unresolved: unresolved.append(anc_fqn)
            continue
        for m in registry[anc_fqn].get("methods", []):
            if m.get("is_constructor"): continue
            k = mkey(m)
            if k in own_key_set and k not in overrides_map:
                overrides_map[k] = anc_fqn
            elif k not in seen_inh:
                seen_inh.add(k)
                entry = dict(m)
                entry["declaring_class"]     = anc_fqn
                entry["declaring_class_jni"] = anc_fqn.replace(".", "/")
                entry["declaring_class_cpp"] = to_cpp_name(anc_fqn)
                entry["inherited_depth"]     = depth
                entry = enrich_method(entry, anc_fqn.replace(".", "/"),
                                      anc_fqn.split(".")[-1])
                inherited.append(entry)

    declared     = []
    overridden   = []
    constructors = []
    ctor_idx     = 0

    for m in own_methods:
        if m.get("is_constructor"):
            constructors.append(enrich_constructor(dict(m), ctor_idx,
                                                   class_jni, simple_class))
            ctor_idx += 1
            continue
        k  = mkey(m)
        em = enrich_method(dict(m), class_jni, simple_class)
        if k in overrides_map:
            em["overrides_in"]     = overrides_map[k]
            em["overrides_in_jni"] = overrides_map[k].replace(".", "/")
            em["overrides_in_cpp"] = to_cpp_name(overrides_map[k])
            em["jni_call_type"]    = jni_call_type(em, is_nonvirtual=True)
            overridden.append(em)
        else:
            declared.append(em)

    return declared, overridden, inherited, constructors, overrides_map, unresolved


# =============================================================================
# Fields / Enums / Inner class / Codegen hints / Dep graph
# =============================================================================

def enrich_fields(fields: list) -> list:
    enriched = []
    for f in fields:
        ef    = dict(f)
        jni_t = ef.get("jni_type", "jobject")
        name  = ef.get("name", "UNKNOWN")
        ef["jni_field_var"]     = f"g_fid_{re.sub(r'[^A-Za-z0-9]','_', name)}"
        ef["jni_get_call"]      = jni_get_field_call(jni_t,
                                      is_static=ef.get("is_static", True))
        ef["cpp_value_literal"] = cpp_value_literal(ef.get("constant_value"), jni_t)
        enriched.append(ef)
    return enriched

def build_enum_constants(fields: list) -> list:
    constants = []
    ordinal   = 0
    for f in fields:
        if not (f.get("is_static") and f.get("is_final")): continue
        if f.get("jni_type") not in ("jobject",):           continue
        name = f.get("name", "")
        if not name or name.startswith("$"):                continue
        constants.append({
            "name":          name,
            "ordinal":       ordinal,
            "cpp_enum_name": re.sub(r"[^A-Za-z0-9]","_", name).upper(),
            "jni_field_var": f"g_fid_{re.sub(r'[^A-Za-z0-9]','_', name)}",
        })
        ordinal += 1
    return constants

def inner_class_identity(fqn: str) -> dict:
    if "$" not in fqn:
        return {"outer_class":"","outer_class_jni":"","outer_class_cpp":"",
                "inner_simple_name":fqn.split(".")[-1],"nesting_depth":0}
    dot_parts    = fqn.split(".")
    last         = dot_parts[-1]
    dollar_parts = last.split("$")
    outer_simple = dollar_parts[0]
    outer_fqn    = ".".join(dot_parts[:-1] + [outer_simple])
    return {
        "outer_class":       outer_fqn,
        "outer_class_jni":   outer_fqn.replace(".", "/"),
        "outer_class_cpp":   to_cpp_name(outer_fqn),
        "inner_simple_name": "$".join(dollar_parts[1:]),
        "nesting_depth":     last.count("$"),
    }

LONG_LIVED_SUFFIXES = (
    "Activity","Service","Fragment","View","Manager",
    "Context","Application","Dialog","Window",
)
PROXY_SUFFIXES = ("Listener","Callback","Observer","Handler","Runnable")

def build_codegen_hints(data: dict, declared: list, overridden: list,
                        inherited: list, constructors: list) -> dict:
    fqn         = data["fqn"]
    simple      = data["simple_name"]
    is_iface    = data.get("is_interface",  False)
    is_abstract = data.get("is_abstract",   False)
    is_ann      = data.get("is_annotation", False)
    is_enum     = data.get("is_enum",       False)

    if is_ann:        strategy = "annotation"
    elif is_enum:     strategy = "enum"
    elif is_iface:    strategy = "interface"
    elif is_abstract: strategy = "abstract"
    else:             strategy = "full"

    is_proxy_target = simple.endswith(PROXY_SUFFIXES) and is_iface
    proxy_methods   = []
    if is_proxy_target:
        for m in (declared + overridden + inherited):
            if m.get("is_abstract") or is_iface:
                n = m["name"]
                if n not in proxy_methods: proxy_methods.append(n)

    total_methods = len(declared) + len(overridden) + len(inherited)
    emit_wrapper  = not (is_iface and total_methods == 0 and not constructors)
    safe_fqn      = re.sub(r"[.$]","_", fqn)
    return {
        "emit_wrapper":       emit_wrapper,
        "wrapper_strategy":   strategy,
        "needs_global_ref":   simple.endswith(LONG_LIVED_SUFFIXES),
        "needs_proxy":        is_proxy_target,
        "proxy_method_list":  proxy_methods,
        "include_guard":      to_guard_macro(fqn),
        "suggested_filename": safe_fqn + ".h",
        "suggested_cpp_file": safe_fqn + ".cpp",
    }

def build_dependency_graph(data: dict, ancestry: list,
                           all_ifaces: list, registry: dict) -> tuple:
    own_jni = data["jni_name"]
    dep_set = set(data.get("depends_on", []))
    for a in ancestry:   dep_set.add(a.replace(".", "/"))
    for i in all_ifaces: dep_set.add(i.replace(".", "/"))
    for mlist_key in ("methods","declared_methods","overridden_methods",
                      "inherited_methods","constructors"):
        for m in data.get(mlist_key, []):
            jc = m.get("return_jni_class") or m.get("jni_class")
            if jc: dep_set.add(jc)
            for p in m.get("params", []):
                jc2 = p.get("jni_class") or p.get("proxy_interface")
                if jc2: dep_set.add(jc2)
    dep_sorted = sorted(
        x for x in dep_set
        if x and x != own_jni and not x.startswith("java/lang/Object")
    )
    def j2f(x): return x.replace("/", ".")
    in_corpus = [x for x in dep_sorted if j2f(x) in registry]
    external  = [x for x in dep_sorted if j2f(x) not in registry]
    anc_jni   = {a.replace(".", "/") for a in ancestry}
    fwd_decls = [x for x in in_corpus if x not in anc_jni]
    return dep_sorted, in_corpus, external, fwd_decls


# =============================================================================
# Master resolve_class
# =============================================================================

def resolve_class(data: dict, registry: dict, iface_cache: dict) -> dict:
    fqn      = data["fqn"]
    jni_name = data["jni_name"]
    simple   = data["simple_name"]

    data["cpp_class_name"]  = to_cpp_name(fqn)
    data["cpp_guard_macro"] = to_guard_macro(fqn)
    data["jni_class_var"]   = f"g_cls_{to_cpp_name(fqn)}"
    data.update(inner_class_identity(fqn))

    ancestry, unresolved_anc = build_ancestry(fqn, registry)
    data["ancestry"]         = ancestry
    data["ancestry_jni"]     = [a.replace(".", "/") for a in ancestry]
    data["ancestry_depth"]   = len(ancestry)
    data["ancestry_details"] = build_ancestry_details(ancestry, registry)
    data["parent_details"]   = (data["ancestry_details"][0]
                                if data["ancestry_details"] else None)

    all_ifaces = collect_all_interfaces(fqn, registry, iface_cache)
    data["all_interfaces"]          = all_ifaces
    data["all_interfaces_jni"]      = [i.replace(".", "/") for i in all_ifaces]
    data["all_interfaces_details"]  = build_interface_details(
                                          fqn, all_ifaces, ancestry, registry)
    data["method_resolution_order"] = build_mro(fqn, registry)

    own_methods = data.get("methods", [])
    (declared, overridden, inherited,
     constructors, _ov_map, unresolved_inh) = resolve_methods(
        own_methods, ancestry, registry, jni_name, simple)
    data["declared_methods"]   = declared
    data["overridden_methods"] = overridden
    data["inherited_methods"]  = inherited
    data["constructors"]       = constructors

    data["fields"]         = enrich_fields(data.get("fields", []))
    data["enum_constants"] = (build_enum_constants(data["fields"])
                              if data.get("is_enum") else [])

    seen_u         = set()
    unresolved_all = []
    for u in unresolved_anc + unresolved_inh:
        if u not in seen_u:
            seen_u.add(u)
            unresolved_all.append(u)
    data["unresolved_ancestors"] = unresolved_all

    deps, in_corpus, external, fwd = build_dependency_graph(
        data, ancestry, all_ifaces, registry)
    data["depends_on"]           = deps
    data["depends_on_in_corpus"] = in_corpus
    data["depends_on_external"]  = external
    data["forward_declarations"] = fwd

    data["codegen_hints"] = build_codegen_hints(
        data, declared, overridden, inherited, constructors)

    data["resolve_summary"] = {
        "ancestry_depth":       len(ancestry),
        "constructors":         len(constructors),
        "declared":             len(declared),
        "overridden":           len(overridden),
        "inherited":            len(inherited),
        "fields":               len(data["fields"]),
        "enum_constants":       len(data["enum_constants"]),
        "all_interfaces_count": len(all_ifaces),
        "unresolved":           len(unresolved_all),
    }
    return data


# =============================================================================
# Main
# =============================================================================

def main():
    ap = argparse.ArgumentParser(
        description=(
            "Stratum Stage 05 — Fully resolve & enrich class JSON for JNI codegen.\n\n"
            + CLOSURE_MODE_HELP
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--input",  required=True,
        help="Path to 04_parse/output/",
    )
    ap.add_argument(
        "--output", required=True,
        help="Path to 05_resolve/output/",
    )
    ap.add_argument(
        "--closure-mode",
        choices=CLOSURE_MODES,
        default=None,
        metavar="MODE",
        help=(
            "Override the closure_mode from targets.json. "
            "Choices: parents_only (recommended), "
            "parents_and_interfaces, "
            "full (warning: 800+ classes, causes SIGABRT). "
            "When omitted, the value in targets.json is used."
        ),
    )
    ap.add_argument(
        "--list-modes",
        action="store_true",
        help="Print closure mode descriptions and exit.",
    )
    args = ap.parse_args()

    if args.list_modes:
        print(CLOSURE_MODE_HELP)
        sys.exit(0)

    print_header("STRATUM PIPELINE - STAGE 05 (RESOLVE)")

    input_dir  = Path(args.input)
    output_dir = Path(args.output)

    if not input_dir.exists():
        print(f"ERROR: Input directory not found: {input_dir}")
        sys.exit(1)

    # ── Phase 1: Load full corpus into registry ───────────────────────────────
    print("-> Phase 1: loading corpus ...")
    registry = load_registry(input_dir)
    print(f"   {len(registry):,} classes loaded into registry")
    if not registry:
        print("ERROR: No class JSON files found. Did Stage 04 succeed?")
        sys.exit(1)

    # ── Phase 2: Load or create 05_resolve/targets.json ───────────────────────
    targets_file = Path("05_resolve/targets.json")
    filter_enabled, seed_fqns, closure_mode = load_or_create_targets(
        targets_file, registry, args.closure_mode or ""
    )

    # ── Phase 3: Determine which class files to resolve ───────────────────────
    all_json = sorted(
        f for f in input_dir.rglob("*.json")
        if f.name not in ("parse_summary.json", "resolve_summary.json")
    )

    if filter_enabled:
        # ── FILTER MODE ───────────────────────────────────────────────────────
        print(f"\n-> Filter mode ON")
        print(f"   Seeds        : {len(seed_fqns)} class(es)")
        print(f"   Closure mode : {closure_mode}")
        print(f"   Computing closure ...")

        closure = compute_target_closure(seed_fqns, registry, closure_mode)

        print(f"   Closure size : {len(closure):,} classes "
              f"(seeds + auto-discovered)")

        seed_set   = set(seed_fqns)
        auto_added = sorted(closure - seed_set)
        if auto_added:
            print(f"\n   Auto-included {len(auto_added)} class(es) "
                  f"via '{closure_mode}':")
            for fqn in auto_added:
                print(f"     + {fqn}")
        print()

        # Build fqn -> file path lookup
        fqn_to_file: dict = {}
        for jf in all_json:
            try:
                d   = json.loads(jf.read_text(encoding="utf-8"))
                fqn = d.get("fqn", "")
                if fqn:
                    fqn_to_file[fqn] = jf
            except Exception:
                pass

        # Warn about seeds not found in corpus
        for fqn in seed_fqns:
            if fqn not in fqn_to_file:
                print(f"  WARN  seed '{fqn}' has no file in corpus "
                      f"(check spelling or re-run Stage 04)")

        json_files = [fqn_to_file[fqn] for fqn in sorted(closure)
                      if fqn in fqn_to_file]
        print(f"   Files to resolve : {len(json_files):,}\n")

    else:
        # ── FULL MODE (original behaviour) ────────────────────────────────────
        print("\n-> Filter mode OFF — resolving full corpus (original behaviour)")
        json_files = all_json
        print(f"   Files to resolve : {len(json_files):,}\n")

    # ── Phase 4: Resolve ──────────────────────────────────────────────────────
    total = len(json_files)
    output_dir.mkdir(parents=True, exist_ok=True)
    iface_cache: dict = {}
    skip_all: list = [False]   # single-element list so handle_validation_errors can modify it

    sum_ctors = sum_declared = sum_overridden = 0
    sum_inherited = sum_fields = sum_enums = sum_unresolved = 0
    failed: list = []

    for i, jf in enumerate(json_files, 1):
        try:
            data = json.loads(jf.read_text(encoding="utf-8"))
            if not data.get("fqn"):
                raise ValueError("missing fqn")

            enriched = resolve_class(data, registry, iface_cache)
            rs       = enriched["resolve_summary"]

            # PATCH 5: Validate after resolve, prompt user on errors
            v_errors = validate_resolved_class(enriched)
            if v_errors:
                should_continue = handle_validation_errors(
                    enriched.get("fqn", str(jf)),
                    v_errors,
                    skip_all,
                )
                if not should_continue:
                    sys.exit(1)
                else:
                    failed.append({
                        "file":   str(jf),
                        "error":  f"validation warnings: {v_errors}",
                        "type":   "validation_warning",
                    })

            sum_ctors      += rs["constructors"]
            sum_declared   += rs["declared"]
            sum_overridden += rs["overridden"]
            sum_inherited  += rs["inherited"]
            sum_fields     += rs["fields"]
            sum_enums      += rs["enum_constants"]
            sum_unresolved += rs["unresolved"]

            rel      = jf.relative_to(input_dir)
            out_file = output_dir / rel
            out_file.parent.mkdir(parents=True, exist_ok=True)
            out_file.write_text(json.dumps(enriched, indent=2), encoding="utf-8")

            tags = []
            if enriched.get("is_annotation"):  tags.append("@ann")
            if enriched.get("is_interface"):   tags.append("iface")
            if enriched.get("is_abstract"):    tags.append("abs")
            if enriched.get("is_enum"):        tags.append("enum")
            if enriched.get("is_inner_class"): tags.append("inner")
            tag_str = f"[{','.join(tags)}]" if tags else ""
            warn    = (f"  !! unresolved={rs['unresolved']}"
                       if rs["unresolved"] else "")

            print(
                f"  [{i:4d}/{total}] {enriched['fqn']} {tag_str}\n"
                f"           anc={rs['ancestry_depth']}  "
                f"ctor={rs['constructors']}  "
                f"new={rs['declared']}  "
                f"ovr={rs['overridden']}  "
                f"inh={rs['inherited']}  "
                f"fld={rs['fields']}  "
                f"iface={rs['all_interfaces_count']}"
                f"{warn}"
            )

        except Exception as e:
            failed.append({"file": str(jf), "error": str(e)})
            print(f"  [{i:4d}/{total}] FAIL  {jf.name}  ->  {e}")

    # ── Summary ───────────────────────────────────────────────────────────────
    summary = {
        "stage":                          "05_resolve",
        "filter_enabled":                 filter_enabled,
        "closure_mode":                   closure_mode if filter_enabled else "n/a",
        "seed_classes":                   seed_fqns if filter_enabled else [],
        "input_dir":                      str(input_dir),
        "output_dir":                     str(output_dir),
        "total_classes":                  total,
        "total_resolved":                 total - len(failed),
        "total_failed":                   len(failed),
        "total_constructors":             sum_ctors,
        "total_declared_methods":         sum_declared,
        "total_overridden_methods":       sum_overridden,
        "total_inherited_methods":        sum_inherited,
        "total_fields":                   sum_fields,
        "total_enum_constants":           sum_enums,
        "total_unresolved_ancestor_refs": sum_unresolved,
        "failed_files":                   failed,
    }
    summary_file = output_dir / "resolve_summary.json"
    summary_file.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print()
    print_header("STAGE 05 COMPLETE")
    if filter_enabled:
        print(f"-> Mode          : FILTER  "
              f"({len(seed_fqns)} seeds -> {total} classes)")
        print(f"-> Closure mode  : {closure_mode}")
    else:
        print(f"-> Mode          : FULL CORPUS")
    print(f"-> Classes resolved   : {total - len(failed):,} / {total:,}")
    print(f"-> Constructors       : {sum_ctors:,}")
    print(f"-> Declared (new)     : {sum_declared:,}")
    print(f"-> Overridden         : {sum_overridden:,}")
    print(f"-> Inherited          : {sum_inherited:,}")
    print(f"-> Fields (constants) : {sum_fields:,}")
    print(f"-> Enum constants     : {sum_enums:,}")
    print(f"-> Unresolved refs    : {sum_unresolved:,}  "
          "(expected for java.lang.* / SDK types not in jar)")
    print(f"-> Failed             : {len(failed):,}")
    print(f"-> Output             : {output_dir}")
    print(f"-> Summary            : {summary_file}")

    if failed:
        print()
        print("FAILED FILES:")
        for f in failed:
            print(f"  {f['file']}")
            print(f"    {f['error']}")
        print()
        print("Fix above and rerun. Stages 01-04 are untouched.")
        sys.exit(1)
    else:
        print()
        if filter_enabled:
            print("Filtered resolve complete.")
            print(f"Closure mode '{closure_mode}': "
                  f"every required ancestor is included.")
        else:
            print("All classes resolved. Stage 06 reads exclusively from:")
        print(f"  {output_dir}")


if __name__ == "__main__":
    main()