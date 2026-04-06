#stage 04_parse/main.py , 
import argparse
import json
import re
import sys
from pathlib import Path


def print_header(title):
    print("==================================================")
    print(f" {title}")
    print("==================================================")


# ── Type Mapper ────────────────────────────────────────────────────────────────

PRIMITIVE_MAP = {
    "V": ("void",     "void",      "None",  "none"),
    "Z": ("jboolean", "uint8_t",   "bool",  "bool_in"),
    "B": ("jbyte",    "int8_t",    "int",   "direct"),
    "C": ("jchar",    "uint16_t",  "str",   "direct"),
    "S": ("jshort",   "int16_t",   "int",   "direct"),
    "I": ("jint",     "int32_t",   "int",   "direct"),
    "J": ("jlong",    "int64_t",   "int",   "long_safe"),
    "F": ("jfloat",   "float",     "float", "direct"),
    "D": ("jdouble",  "double",    "float", "direct"),
}

PROXY_SUFFIXES = ("Listener", "Callback", "Observer", "Handler", "Runnable")

STRING_TYPES = {
    "java/lang/String",
    "java/lang/CharSequence",
}

BOXED_PRIMITIVES = {
    "java/lang/Boolean":   ("jboolean", "uint8_t",  "bool",  "bool_in"),
    "java/lang/Byte":      ("jbyte",    "int8_t",   "int",   "direct"),
    "java/lang/Character": ("jchar",    "uint16_t", "str",   "direct"),
    "java/lang/Short":     ("jshort",   "int16_t",  "int",   "direct"),
    "java/lang/Integer":   ("jint",     "int32_t",  "int",   "direct"),
    "java/lang/Long":      ("jlong",    "int64_t",  "int",   "long_safe"),
    "java/lang/Float":     ("jfloat",   "float",    "float", "direct"),
    "java/lang/Double":    ("jdouble",  "double",   "float", "direct"),
}


def map_object_type(jni_class: str) -> dict:
    if jni_class in STRING_TYPES:
        return {
            "jni_type":    "jstring",
            "cpp_type":    "std::string",
            "python_type": "str",
            "conversion":  "string_in",
        }

    if jni_class in BOXED_PRIMITIVES:
        jni_t, cpp_t, py_t, conv = BOXED_PRIMITIVES[jni_class]
        return {
            "jni_type":    jni_t,
            "cpp_type":    cpp_t,
            "python_type": py_t,
            "conversion":  conv,
            "is_boxed":    True,
        }

    simple = jni_class.split("/")[-1]
    needs_proxy = simple.endswith(PROXY_SUFFIXES)

    return {
        "jni_type":        "jobject",
        "cpp_type":        "nb::callable" if needs_proxy else "jobject",
        "python_type":     "Callable[[], None]" if needs_proxy else "object",
        "conversion":      "callable_to_proxy" if needs_proxy else "object_in",
        "needs_proxy":     needs_proxy,
        "proxy_interface": jni_class if needs_proxy else None,
    }


def map_return_type(jni_class: str) -> dict:
    if jni_class in STRING_TYPES:
        return {
            "jni_type":    "jstring",
            "cpp_type":    "std::string",
            "python_type": "str",
            "conversion":  "string_out",
        }

    if jni_class in BOXED_PRIMITIVES:
        jni_t, cpp_t, py_t, _ = BOXED_PRIMITIVES[jni_class]
        return {
            "jni_type":    jni_t,
            "cpp_type":    cpp_t,
            "python_type": py_t,
            "conversion":  "unbox_out",
            "is_boxed":    True,
        }

    return {
        "jni_type":    "jobject",
        "cpp_type":    "jobject",
        "python_type": "object",
        "conversion":  "object_out",
    }


# ── Descriptor Parser ──────────────────────────────────────────────────────────

def parse_descriptor(descriptor: str) -> tuple[list[dict], dict]:
    descriptor = descriptor.strip()
    if descriptor.startswith("descriptor:"):
        descriptor = descriptor[len("descriptor:"):].strip()

    params = []
    idx = 0

    if not descriptor.startswith("("):
        return [], {"jni_type": "void", "cpp_type": "void",
                    "python_type": "None", "conversion": "none"}

    idx = 1
    param_index = 0

    while idx < len(descriptor) and descriptor[idx] != ")":
        ch = descriptor[idx]

        if ch in PRIMITIVE_MAP:
            jni_t, cpp_t, py_t, conv = PRIMITIVE_MAP[ch]
            params.append({
                "index":       param_index,
                "name":        f"arg{param_index}",
                "java_type":   ch,
                "jni_type":    jni_t,
                "cpp_type":    cpp_t,
                "python_type": py_t,
                "conversion":  conv,
                "needs_proxy": False,
                "is_array":    False,
            })
            idx += 1

        elif ch == "[":
            # Count array dimensions
            dims = 0
            while idx < len(descriptor) and descriptor[idx] == "[":
                dims += 1
                idx += 1
            # Element type
            elem_jni = "jobject"
            elem_java = "array"
            if descriptor[idx] in PRIMITIVE_MAP:
                prim_ch = descriptor[idx]
                prim = PRIMITIVE_MAP[prim_ch]
                elem_java = prim_ch
                elem_jni = prim[0]
                idx += 1
            elif descriptor[idx] == "L":
                idx += 1
                start = idx
                while idx < len(descriptor) and descriptor[idx] != ";":
                    idx += 1
                elem_java = descriptor[start:idx].replace("/", ".")
                idx += 1  # skip ;

            params.append({
                "index":       param_index,
                "name":        f"arg{param_index}",
                "java_type":   f"{'[' * dims}{elem_java}",
                "element_type": elem_java,
                "array_dims":  dims,
                "jni_type":    "jobjectArray" if dims > 1 or elem_jni == "jobject" else f"j{PRIMITIVE_MAP.get(elem_java, ('','','',''))[0].lstrip('j')}Array",
                "cpp_type":    "jobject",
                "python_type": "list",
                "conversion":  "array_in",
                "needs_proxy": False,
                "is_array":    True,
            })

        elif ch == "L":
            idx += 1
            start = idx
            while idx < len(descriptor) and descriptor[idx] != ";":
                idx += 1
            jni_class = descriptor[start:idx]
            idx += 1
            info = map_object_type(jni_class)
            entry = {
                "index":       param_index,
                "name":        f"arg{param_index}",
                "java_type":   jni_class.replace("/", "."),
                "jni_class":   jni_class,
                "needs_proxy": False,
                "is_array":    False,
            }
            entry.update(info)
            params.append(entry)

        else:
            idx += 1
            continue

        param_index += 1

    idx += 1  # skip ')'

    if idx >= len(descriptor):
        ret = {"jni_type": "void", "cpp_type": "void",
               "python_type": "None", "conversion": "none",
               "java_type": "void", "is_array": False}
    else:
        ret_ch = descriptor[idx]
        if ret_ch in PRIMITIVE_MAP:
            jni_t, cpp_t, py_t, conv = PRIMITIVE_MAP[ret_ch]
            if ret_ch == "Z":
                conv = "bool_out"
            ret = {"jni_type": jni_t, "cpp_type": cpp_t,
                   "python_type": py_t, "conversion": conv,
                   "java_type": ret_ch, "is_array": False}
        elif ret_ch == "L":
            idx += 1
            start = idx
            while idx < len(descriptor) and descriptor[idx] != ";":
                idx += 1
            jni_class = descriptor[start:idx]
            ret = map_return_type(jni_class)
            ret["java_type"] = jni_class.replace("/", ".")
            ret["jni_class"] = jni_class
            ret["is_array"] = False
        elif ret_ch == "[":
            dims = 0
            while idx < len(descriptor) and descriptor[idx] == "[":
                dims += 1
                idx += 1
            ret = {"jni_type": "jobject", "cpp_type": "jobject",
                   "python_type": "list", "conversion": "array_out",
                   "java_type": "array", "is_array": True, "array_dims": dims}
        else:
            ret = {"jni_type": "void", "cpp_type": "void",
                   "python_type": "None", "conversion": "none",
                   "java_type": "void", "is_array": False}

    return params, ret


# ── Helpers ────────────────────────────────────────────────────────────────────

def strip_generics(s: str) -> str:
    """Remove generic type parameters like <T, V extends Foo>."""
    result = ""
    depth = 0
    for ch in s:
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth -= 1
        elif depth == 0:
            result += ch
    return result


def extract_interfaces(tokens: list[str]) -> list[str]:
    """
    Extract implemented/extended interfaces from declaration tokens.
    Handles both 'implements X, Y' and 'extends X, Y' (for interfaces).
    """
    interfaces = []
    for kw in ("implements", "extends"):
        if kw in tokens:
            idx = tokens.index(kw)
            # Collect comma-separated names after keyword until '{' or next keyword
            i = idx + 1
            while i < len(tokens):
                t = tokens[i].rstrip(",{").strip()
                if t in ("implements", "extends", "throws", "{", ""):
                    break
                if t:
                    # Could be comma-joined e.g. "Foo,Bar"
                    for part in t.split(","):
                        part = part.strip().rstrip("{")
                        if part:
                            interfaces.append(part)
                i += 1
    return interfaces


def parse_throws(line: str) -> list[str]:
    """Extract thrown exception class names from a method declaration line."""
    m = re.search(r"throws\s+(.+?)(?:;|\{|$)", line)
    if not m:
        return []
    raw = m.group(1).strip()
    return [e.strip() for e in raw.split(",") if e.strip()]


def parse_field_value(line: str) -> str | None:
    """Try to extract ConstantValue from a javap field line."""
    m = re.search(r"ConstantValue:\s*(.+)", line)
    if m:
        return m.group(1).strip()
    # Some javap outputs embed it inline: "= 42;"
    m = re.search(r"=\s*(.+?);", line)
    if m:
        return m.group(1).strip()
    return None


# ── javap State Machine ────────────────────────────────────────────────────────

def parse_javap(text: str) -> dict:
    lines = text.splitlines()

    result = {
        # ── Identity ──────────────────────────────────────────
        "fqn":            "",          # fully-qualified name  e.g. android.view.View
        "jni_name":       "",          # JNI slash form        e.g. android/view/View
        "simple_name":    "",          # e.g. View
        "package":        "",          # e.g. android.view
        "source_file":    "",          # from "Compiled from" header

        # ── Class kind ────────────────────────────────────────
        "is_abstract":    False,
        "is_interface":   False,
        "is_annotation":  False,       # @interface
        "is_enum":        False,
        "is_final":       False,
        "is_inner_class": False,       # contains '$' in simple name

        # ── Hierarchy ─────────────────────────────────────────
        "parent_fqn":     "",
        "parent_jni":     "",
        "parent_simple":  "",
        "interfaces":     [],          # implemented/extended interfaces (fqn list)
        "interfaces_jni": [],          # same in JNI slash form

        # ── Members ───────────────────────────────────────────
        "methods":        [],
        "fields":         [],          # static final constants

        # ── Dependency graph hints ────────────────────────────
        "depends_on":     [],          # all referenced classes (for topological sort)
    }

    STATE_SEEKING_CLASS = 0
    STATE_IN_CLASS = 1
    STATE_IN_DESCRIPTOR = 2
    STATE_IN_FIELD = 3

    state = STATE_SEEKING_CLASS
    current_method = None
    current_field = None
    descriptor_accum = ""

    # ── collect all referenced jni class names for depends_on ──
    referenced: set[str] = set()

    def record_refs_from_params(params, ret):
        for p in params:
            jc = p.get("jni_class") or p.get("proxy_interface")
            if jc:
                referenced.add(jc)
        jc = ret.get("jni_class")
        if jc:
            referenced.add(jc)

    def finish_method(method, descriptor, throws=None):
        descriptor = descriptor.strip()
        params, ret = parse_descriptor(descriptor)

        method["jni_signature"]     = descriptor
        method["return_jni"]        = ret.get("jni_type", "void")
        method["return_cpp"]        = ret.get("cpp_type", "void")
        method["return_python"]     = ret.get("python_type", "None")
        method["return_java_type"]  = ret.get("java_type", "void")
        method["return_jni_class"]  = ret.get("jni_class", None)
        method["return_conversion"] = ret.get("conversion", "none")
        method["return_is_array"]   = ret.get("is_array", False)
        method["is_void"]           = ret.get("cpp_type") == "void"
        method["params"]            = params
        method["throws"]            = throws or []
        method["needs_proxy"]       = any(p.get("needs_proxy") for p in params)

        # PATCH 1: Set jni_new_sig and force void return for constructors.
        # Stage 05 enrich_constructor reads jni_new_sig directly.
        # Without this, constructors get wrong return types and missing jni_new_sig.
        if method.get("is_constructor"):
            method["jni_new_sig"]       = descriptor
            method["return_jni"]        = "void"
            method["return_cpp"]        = "void"
            method["return_python"]     = "None"
            method["return_java_type"]  = "void"
            method["return_conversion"] = "none"
            method["is_void"]           = True

        if method["needs_proxy"]:
            proxy_param = next(p for p in params if p.get("needs_proxy"))
            method["proxy_interface"] = proxy_param.get("proxy_interface", "")
            method["proxy_interface_jni"] = proxy_param.get("proxy_interface", "")
            method["proxy_method"] = (
                "on"
                + proxy_param["java_type"].split(".")[-1]
                .replace("Listener", "")
                .replace("Callback", "")
                .replace("Observer", "")
                .replace("Handler", "")
            )
        else:
            method["proxy_interface"]     = None
            method["proxy_interface_jni"] = None
            method["proxy_method"]        = None

        record_refs_from_params(params, ret)
        return method

    pending_throws = []

    for raw_line in lines:
        stripped = raw_line.strip()

        # ── Source file header ─────────────────────────────────────────────────
        if stripped.startswith("Compiled from"):
            m = re.search(r'"(.+?)"', stripped)
            if m:
                result["source_file"] = m.group(1)
            continue

        # ── SEEKING_CLASS ──────────────────────────────────────────────────────
        if state == STATE_SEEKING_CLASS:

            # Detect @interface (annotation type) FIRST — before generic interface check
            is_annotation_decl = "@interface" in stripped
            is_interface_decl = " interface " in stripped or stripped.startswith("interface ")
            is_class_decl = " class " in stripped or stripped.startswith("class ")
            is_enum_decl = " enum " in stripped or stripped.startswith("enum ")

            if not any([is_annotation_decl, is_interface_decl, is_class_decl, is_enum_decl]):
                continue

            # Must be a public/protected/package-level declaration
            if not any(kw in stripped for kw in ("public ", "protected ", "interface ", "class ", "enum ", "@interface")):
                continue

            result["is_abstract"]  = "abstract" in stripped
            result["is_final"]     = "final" in stripped
            result["is_enum"]      = is_enum_decl

            # Normalise @interface → treat as interface + annotation flag
            if is_annotation_decl:
                result["is_annotation"] = True
                result["is_interface"]  = True
                # Replace "@interface" with a placeholder so token splitting works
                decl_clean = stripped.replace("@interface", "§ANNOTATION§")
            elif is_interface_decl:
                result["is_interface"] = True
                decl_clean = stripped
            else:
                decl_clean = stripped

            # Strip generics to get clean tokens
            clean_decl = strip_generics(decl_clean)
            tokens = clean_decl.split()

            # Find the keyword position
            kw_found = None
            kw_idx = -1
            for kw in ("§ANNOTATION§", "class", "interface", "enum"):
                if kw in tokens:
                    kw_found = kw
                    kw_idx = tokens.index(kw)
                    break

            if kw_idx < 0 or kw_idx + 1 >= len(tokens):
                continue

            fqn = tokens[kw_idx + 1].rstrip("{,;").strip()
            # Remove any trailing brace remnants
            fqn = fqn.split("{")[0].strip()
            if not fqn:
                continue

            result["fqn"]         = fqn
            result["jni_name"]    = fqn.replace(".", "/")
            result["simple_name"] = fqn.split(".")[-1]
            result["package"]     = ".".join(fqn.split(".")[:-1])
            result["is_inner_class"] = "$" in result["simple_name"]

            # Parent class (only for 'extends' on a class, not interface)
            if not result["is_interface"] and "extends" in tokens:
                ext_idx = tokens.index("extends")
                if ext_idx + 1 < len(tokens):
                    parent = tokens[ext_idx + 1].rstrip("{,;").strip()
                    if parent not in ("implements", "{"):
                        result["parent_fqn"]    = parent
                        result["parent_jni"]    = parent.replace(".", "/")
                        result["parent_simple"] = parent.split(".")[-1]
                        referenced.add(parent.replace(".", "/"))

            # Collect interfaces (implements for classes; extends for interfaces)
            ifaces = extract_interfaces(tokens)
            # Annotation types implicitly extend java.lang.annotation.Annotation
            if result["is_annotation"]:
                ifaces = [i for i in ifaces if i != "java.lang.annotation.Annotation"]
            result["interfaces"] = ifaces
            result["interfaces_jni"] = [i.replace(".", "/") for i in ifaces]
            for i in result["interfaces_jni"]:
                referenced.add(i)

            state = STATE_IN_CLASS

        # ── IN_CLASS ───────────────────────────────────────────────────────────
        elif state == STATE_IN_CLASS:
            if stripped in ("}", ""):
                continue

            # ── Descriptor continuation ────────────────────────────────────────
            if stripped.startswith("descriptor:"):
                descriptor_accum = stripped[len("descriptor:"):].strip()
                if ")" in descriptor_accum:
                    if current_method is not None:
                        current_method = finish_method(current_method, descriptor_accum, pending_throws)
                        result["methods"].append(current_method)
                        current_method = None
                    elif current_field is not None:
                        # Field descriptor — record java type
                        current_field["descriptor"] = descriptor_accum
                        fd_params, fd_ret = parse_descriptor(f"(){descriptor_accum}")
                        current_field["jni_type"]    = fd_ret.get("jni_type", "jobject")
                        current_field["cpp_type"]    = fd_ret.get("cpp_type", "jobject")
                        current_field["python_type"] = fd_ret.get("python_type", "object")
                        result["fields"].append(current_field)
                        current_field = None
                    descriptor_accum = ""
                else:
                    state = STATE_IN_DESCRIPTOR
                pending_throws = []
                continue

            # ── ConstantValue ─────────────────────────────────────────────────
            if stripped.startswith("ConstantValue:") and current_field is not None:
                current_field["constant_value"] = stripped[len("ConstantValue:"):].strip()
                continue

            # ── Flags / attributes — skip ──────────────────────────────────────
            if stripped.startswith("flags:") or stripped.startswith("Code:") \
               or stripped.startswith("LineNumber") or stripped.startswith("LocalVariable") \
               or stripped.startswith("Exceptions:") or stripped.startswith("Signature:") \
               or stripped.startswith("AnnotationDefault:") or stripped.startswith("RuntimeVisible") \
               or stripped.startswith("StackMapTable"):
                continue

            # ── Static initialiser block ───────────────────────────────────────
            if re.match(r"static\s*\{", stripped):
                continue

            # ── Check if it's a member declaration ────────────────────────────
            has_access = any(kw in stripped for kw in ("public ", "protected ", "private "))
            if not has_access:
                # package-private or attribute line — skip
                continue

            # Throws on previous method line (sometimes javap puts throws here)
            if "throws" in stripped and current_method is not None:
                pending_throws = parse_throws(stripped)

            # ── Field: no parentheses ──────────────────────────────────────────
            if "(" not in stripped:
                # Only track static final constants (useful for JNI field IDs)
                if "static" in stripped and "final" in stripped:
                    # Parse: public static final int FLAG_SOMETHING = 1;
                    #        public static final java.lang.String SOME_KEY;
                    core = stripped
                    for mod in ("public", "protected", "private", "static", "final"):
                        core = core.replace(mod + " ", "")
                    core = core.strip().rstrip(";")
                    # Extract inline value
                    const_val = None
                    if "=" in core:
                        parts = core.split("=", 1)
                        core = parts[0].strip()
                        const_val = parts[1].strip().rstrip(";").strip()

                    parts = core.split()
                    if len(parts) >= 2:
                        field_type = parts[0]
                        field_name = parts[1]
                        current_field = {
                            "name":           field_name,
                            "java_type":      field_type,
                            "is_static":      True,
                            "is_final":       True,
                            "constant_value": const_val,
                            "descriptor":     "",
                            "jni_type":       "jobject",
                            "cpp_type":       "jobject",
                            "python_type":    "object",
                        }
                        # Descriptor comes on next line
                continue

            # ── Method / Constructor ───────────────────────────────────────────
            is_static = "static" in stripped
            is_abstract = "abstract" in stripped
            is_native = "native" in stripped

            # Throws on this line
            pending_throws = parse_throws(stripped)

            core = stripped
            for mod in ("public", "protected", "private", "static", "final",
                        "abstract", "synchronized", "native", "transient",
                        "volatile", "default"):
                core = core.replace(mod + " ", "")
            core = core.strip().rstrip(";").rstrip("{").strip()
            # Remove throws clause from core
            # Remove throws clause from core
            core = re.sub(r"\s+throws\s+.*$", "", core).strip()

            # --- PATCH: Strip generics before splitting so names don't get mangled ---
            core = strip_generics(core).strip()
            # -------------------------------------------------------------------------

            if not core:
                continue

            simple = result["simple_name"]
            fqn_check = result["fqn"]

            # Inner class constructors include outer class param: ClassName(OuterClass, ...)
            is_constructor = bool(
                re.match(rf"{re.escape(simple)}\s*\(", core)
                or re.match(rf"{re.escape(fqn_check)}\s*\(", core)
            )

            if is_constructor:
                method_name = "__init__"
                return_type_hint = None
            else:
                parts = core.split()
                if len(parts) < 2:
                    continue
                return_type_hint = parts[0]
                # method name may have generics: foo<T>(...)
                raw_name = parts[1].split("(")[0]
                method_name = strip_generics(raw_name)
                if not method_name:
                    continue

            current_method = {
                "name":            method_name,
                "is_static":       is_static,
                "is_constructor":  is_constructor,
                "is_abstract":     is_abstract,
                "is_native":       is_native,
                "return_hint":     return_type_hint,  # raw Java return type string
                "jni_signature":   "",
                "params":          [],
                "needs_proxy":     False,
                "overload_index":  0,  # filled in post-processing
            }

        # ── IN_DESCRIPTOR ─────────────────────────────────────────────────────
        elif state == STATE_IN_DESCRIPTOR:
            descriptor_accum += stripped
            if ")" in descriptor_accum:
                if current_method is not None:
                    current_method = finish_method(current_method, descriptor_accum, pending_throws)
                    result["methods"].append(current_method)
                    current_method = None
                elif current_field is not None:
                    current_field["descriptor"] = descriptor_accum
                    result["fields"].append(current_field)
                    current_field = None
                descriptor_accum = ""
                pending_throws = []
                state = STATE_IN_CLASS

    # ── Post-process: overload indices ────────────────────────────────────────
    name_count: dict[str, int] = {}
    for m in result["methods"]:
        n = m["name"]
        m["overload_index"] = name_count.get(n, 0)
        name_count[n] = name_count.get(n, 0) + 1

    # Mark overloaded
    overload_counts = {}
    for m in result["methods"]:
        overload_counts[m["name"]] = overload_counts.get(m["name"], 0) + 1
    for m in result["methods"]:
        m["is_overloaded"] = overload_counts[m["name"]] > 1

    # ── Build depends_on ──────────────────────────────────────────────────────
    own_jni = result["jni_name"]
    # Also pull class refs from interface list and parent
    if result["parent_jni"]:
        referenced.add(result["parent_jni"])
    for i in result["interfaces_jni"]:
        referenced.add(i)

    # Filter out self, primitives, java.lang basics we don't care about
    skip_prefixes = ("java/lang/Object", own_jni)
    result["depends_on"] = sorted(
        r for r in referenced
        if r and not any(r == s or r.startswith(s) for s in skip_prefixes)
    )

    # ── Annotation element summary (for @interface classes) ───────────────────
    if result["is_annotation"]:
        result["annotation_elements"] = [
            {
                "name":         m["name"],
                "return_java":  m.get("return_java_type", ""),
                "return_jni":   m.get("return_jni", ""),
                "has_default":  False,  # javap -p doesn't always expose defaults
            }
            for m in result["methods"]
            if not m["is_constructor"] and not m["is_static"]
        ]

    return result


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Stratum Stage 04 - Parse javap to JSON")
    parser.add_argument("--input",  required=True, help="Path to 03_javap/output/")
    parser.add_argument("--output", required=True, help="Path to 04_parse/output/")
    args = parser.parse_args()

    print_header("STRATUM PIPELINE - STAGE 04 (PARSE)")

    input_dir  = Path(args.input)
    output_dir = Path(args.output)

    if not input_dir.exists():
        print(f"ERROR: Input not found: {input_dir}")
        sys.exit(1)

    javap_files = sorted(input_dir.rglob("*.javap"))
    if not javap_files:
        print("ERROR: No .javap files found. Did Stage 03 succeed?")
        sys.exit(1)

    print(f"-> Found {len(javap_files)} .javap files")
    output_dir.mkdir(parents=True, exist_ok=True)

    total_methods     = 0
    total_proxy       = 0
    total_fields      = 0
    total_annotations = 0
    total_inner       = 0
    failed            = []
    successful        = []

    for i, javap_file in enumerate(javap_files, 1):
        try:
            text   = javap_file.read_text(encoding="utf-8")
            parsed = parse_javap(text)

            if not parsed["fqn"]:
                raise ValueError("Could not extract FQN from javap output")

            rel      = javap_file.relative_to(input_dir).with_suffix(".json")
            out_file = output_dir / rel
            out_file.parent.mkdir(parents=True, exist_ok=True)
            out_file.write_text(json.dumps(parsed, indent=2), encoding="utf-8")

            n_methods = len(parsed["methods"])
            n_proxy   = sum(1 for m in parsed["methods"] if m.get("needs_proxy"))
            n_fields  = len(parsed["fields"])
            total_methods += n_methods
            total_proxy   += n_proxy
            total_fields  += n_fields
            if parsed["is_annotation"]:
                total_annotations += 1
            if parsed["is_inner_class"]:
                total_inner += 1
            successful.append(parsed["fqn"])

            tags = []
            if parsed["is_annotation"]: tags.append("@ann")
            if parsed["is_interface"]:  tags.append("iface")
            if parsed["is_abstract"]:   tags.append("abs")
            if parsed["is_inner_class"]: tags.append("inner")
            tag_str = f"[{','.join(tags)}]" if tags else ""

            print(f"  [{i:4d}/{len(javap_files)}] OK  {parsed['fqn']} {tag_str}"
                  f"  ({n_methods} methods, {n_proxy} proxies, {n_fields} fields)")

        except Exception as e:
            failed.append({"file": str(javap_file), "error": str(e)})
            print(f"  [{i:4d}/{len(javap_files)}] FAIL {javap_file.name}  {e}")

    summary = {
        "total_parsed":         len(successful),
        "total_methods":        total_methods,
        "total_fields":         total_fields,
        "total_needing_proxy":  total_proxy,
        "total_annotations":    total_annotations,
        "total_inner_classes":  total_inner,
        "failed":               len(failed),
        "failed_files":         failed,
    }
    summary_file = output_dir / "parse_summary.json"
    summary_file.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print()
    print_header("STAGE 04 COMPLETE")
    print(f"-> Parsed      : {len(successful)}")
    print(f"-> Methods     : {total_methods}")
    print(f"-> Fields      : {total_fields}")
    print(f"-> Proxies     : {total_proxy}")
    print(f"-> Annotations : {total_annotations}")
    print(f"-> Inner cls   : {total_inner}")
    print(f"-> Failed      : {len(failed)}")
    print(f"-> Output      : {output_dir}")
    print(f"-> Summary     : {summary_file}")

    if failed:
        print()
        print("FAILED FILES:")
        for f in failed:
            print(f"  {f['file']}")
            print(f"    {f['error']}")
        print()
        print("Fix the issue and rerun Stage 04. Stages 01-03 are untouched.")
    else:
        print()
        print("All classes parsed. Proceed to Stage 05.")


if __name__ == "__main__":
    main()