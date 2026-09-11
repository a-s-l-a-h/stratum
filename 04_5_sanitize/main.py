#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Stratum Pipeline — Stage 04.5: JNI Safety Sanitizer (optional, additive)
=========================================================================
LOCATION: 04_5_sanitize/main.py

This stage is a PURE FILTER: same JSON shape in (04_parse/output/) as out
(04_5_sanitize/output/). It is entirely optional — 05_resolve works fine
reading 04_parse/output/ directly, exactly like before this stage existed.
Run this stage only if you want the extra safety net below, and simply
point 05_resolve's Pass-1 --input at this stage's --output instead.

ALWAYS ON (not a flag — this is what makes the build crash-proof):
    - Strips javap -p leaked `private` methods/fields (never legally
      callable through JNI from outside the class).
    - Strips methods/fields whose hiddenapi status == 'blocked' (ART
      throws NoSuchMethodError on real hardware, unconditionally).
    - NEVER deletes a class file, NEVER drops a member for having a high
      sdk_since (Build.VERSION.SDK_INT guards keep working).

OPTIONAL (off by default):
    --strict     also drop hidden_status in {restricted, unsupported, conditional}
    --min-sdk N  also drop members permanently removed at/before API N

WHERE api-versions.xml / hiddenapi-flags.csv COME FROM (both optional)
    third_party/api_versions/<api>/api-versions.xml
    third_party/hiddenapi/<api>/hiddenapi-flags.csv
    No network access is used. If a file isn't found, this stage prints
    exactly where to place it and continues (private/blocked stripping
    still runs; sdk_* / hidden_status annotations are just left as None).
"""

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def print_header(title: str) -> None:
    print("=" * 70)
    print(f"  {title}")
    print("=" * 70)


def _int_or_none(v):
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def locate_api_versions_xml(api_version: str):
    path = Path("third_party") / "api_versions" / str(api_version) / "api-versions.xml"
    if path.exists():
        return str(path.resolve()), f"Found at {path}"
    return None, (
        f"Not found at {path} (optional). To enable SDK version tags, copy "
        f"<Android-SDK>/platforms/android-{api_version}/data/api-versions.xml there."
    )


def locate_hiddenapi_csv(api_version: str):
    path = Path("third_party") / "hiddenapi" / str(api_version) / "hiddenapi-flags.csv"
    if path.exists():
        return str(path.resolve()), f"Found at {path}"
    return None, (
        f"Not found at {path} (optional). To enable hidden-API stripping beyond "
        f"private members, place AOSP's hiddenapi-flags.csv for API {api_version} there."
    )


def build_api_versions_index(xml_path):
    index = {}
    if not xml_path or not Path(xml_path).exists():
        return index
    try:
        root = ET.parse(xml_path).getroot()
        for cls_el in root.findall("class"):
            cname = cls_el.get("name")
            if not cname:
                continue
            jni_name = cname.replace(".", "/")
            entry = {
                "class_since": _int_or_none(cls_el.get("since")),
                "class_deprecated": _int_or_none(cls_el.get("deprecated")),
                "class_removed": _int_or_none(cls_el.get("removed")),
                "methods": {}, "fields": {},
            }
            for m_el in cls_el.findall("method"):
                mname_full = m_el.get("name")
                if not mname_full:
                    continue
                meta = {
                    "since": _int_or_none(m_el.get("since")),
                    "deprecated": _int_or_none(m_el.get("deprecated")),
                    "removed": _int_or_none(m_el.get("removed")),
                }
                entry["methods"][mname_full] = meta
                entry["methods"].setdefault(mname_full.split("(")[0], meta)
            for f_el in cls_el.findall("field"):
                fname = f_el.get("name")
                if fname:
                    entry["fields"][fname] = {
                        "since": _int_or_none(f_el.get("since")),
                        "deprecated": _int_or_none(f_el.get("deprecated")),
                        "removed": _int_or_none(f_el.get("removed")),
                    }
            index[jni_name] = entry
    except Exception as e:
        print(f"  [WARN] Failed to parse api-versions.xml ({xml_path}): {e}")
        return {}
    return index


def build_hiddenapi_index(csv_path):
    index = {}
    if not csv_path or not Path(csv_path).exists():
        return index
    try:
        with open(csv_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split(",")
                sig = parts[0].strip()
                flags = {p.strip() for p in parts[1:] if p.strip()}
                if "blocked" in flags:
                    index[sig] = "blocked"
                elif "unsupported" in flags:
                    index[sig] = "unsupported"
                elif any(fl.startswith("max-target-") for fl in flags):
                    index[sig] = "conditional"
                elif "public-api" in flags or "sdk" in flags:
                    index[sig] = "public"
                else:
                    index[sig] = "restricted"
    except Exception as e:
        print(f"  [WARN] Failed to parse hiddenapi-flags.csv ({csv_path}): {e}")
        return {}
    return index


def sanitize_class(data: dict, xml_index: dict, hidden_index: dict, strict: bool, min_sdk) -> dict:
    jni_name = data.get("jni_name", "")
    xml_entry = xml_index.get(jni_name, {})

    data["sdk_since"] = xml_entry.get("class_since")
    data["sdk_deprecated"] = xml_entry.get("class_deprecated")
    data["sdk_removed"] = xml_entry.get("class_removed")
    data["hidden_status"] = hidden_index.get(f"L{jni_name};")

    def extra_strict_drop(status):
        return strict and status in ("restricted", "unsupported", "conditional")

    def removed_by_min_sdk(removed):
        return min_sdk is not None and removed is not None and removed <= min_sdk

    safe_methods = []
    for m in data.get("methods", []):
        if m.get("is_private", False):
            continue
        mname = "<init>" if m.get("is_constructor") else m.get("name", "")
        sig = m.get("jni_signature", "")
        h_status = hidden_index.get(f"L{jni_name};->{mname}{sig}")
        m["hidden_status"] = h_status
        if h_status == "blocked" or extra_strict_drop(h_status):
            continue
        xm = xml_entry.get("methods", {}).get(f"{mname}{sig}") or xml_entry.get("methods", {}).get(m.get("name", "")) or {}
        m["sdk_since"] = xm.get("since")
        m["sdk_deprecated"] = xm.get("deprecated")
        m["sdk_removed"] = xm.get("removed")
        if removed_by_min_sdk(m["sdk_removed"]):
            continue
        safe_methods.append(m)
    data["methods"] = safe_methods

    safe_fields = []
    for f in data.get("fields", []):
        if f.get("is_private", False):
            continue
        fname = f.get("name", "")
        fdesc = f.get("jni_signature") or f.get("descriptor", "")
        h_status = hidden_index.get(f"L{jni_name};->{fname}:{fdesc}")
        f["hidden_status"] = h_status
        if h_status == "blocked" or extra_strict_drop(h_status):
            continue
        xf = xml_entry.get("fields", {}).get(fname, {})
        f["sdk_since"] = xf.get("since")
        f["sdk_deprecated"] = xf.get("deprecated")
        f["sdk_removed"] = xf.get("removed")
        if removed_by_min_sdk(f["sdk_removed"]):
            continue
        safe_fields.append(f)
    data["fields"] = safe_fields

    data["has_accessible_constructor"] = any(m.get("is_constructor") for m in safe_methods)
    return data


def main():
    ap = argparse.ArgumentParser(description="Stratum Stage 04.5 - JNI Safety Sanitizer (optional)")
    ap.add_argument("--input", required=True, help="Path to 04_parse/output/")
    ap.add_argument("--output", required=True, help="Path to 04_5_sanitize/output/")
    ap.add_argument("--api-version", required=True, help="Target Android API level (e.g. 35)")
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--min-sdk", type=int, default=None)
    args = ap.parse_args()

    print_header("STRATUM PIPELINE — STAGE 04.5 (JNI SAFETY SANITIZER, OPTIONAL)")

    input_dir = Path(args.input)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    xml_path, xml_msg = locate_api_versions_xml(args.api_version)
    print(f"[{'OK' if xml_path else 'INFO'}] api-versions.xml   : {xml_msg}")
    csv_path, csv_msg = locate_hiddenapi_csv(args.api_version)
    print(f"[{'OK' if csv_path else 'INFO'}] hiddenapi-flags.csv: {csv_msg}")

    xml_index = build_api_versions_index(xml_path)
    hidden_index = build_hiddenapi_index(csv_path)
    if xml_index:
        print(f"-> Indexed {len(xml_index):,} classes from api-versions.xml")
    if hidden_index:
        print(f"-> Indexed {len(hidden_index):,} signatures from hiddenapi-flags.csv")

    json_files = sorted(f for f in input_dir.rglob("*.json") if f.name != "parse_summary.json")
    if not json_files:
        print("ERROR: No class JSON files found. Did Stage 04 succeed?")
        sys.exit(1)

    processed = total_m_dropped = total_f_dropped = 0
    for jf in json_files:
        data = json.loads(jf.read_text(encoding="utf-8"))
        orig_m, orig_f = len(data.get("methods", [])), len(data.get("fields", []))
        sanitized = sanitize_class(data, xml_index, hidden_index, args.strict, args.min_sdk)
        total_m_dropped += orig_m - len(sanitized["methods"])
        total_f_dropped += orig_f - len(sanitized["fields"])
        rel = jf.relative_to(input_dir)
        out_file = output_dir / rel
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_text(json.dumps(sanitized, indent=2), encoding="utf-8")
        processed += 1

    src_summary = input_dir / "parse_summary.json"
    if src_summary.exists():
        (output_dir / "parse_summary.json").write_text(src_summary.read_text(encoding="utf-8"), encoding="utf-8")

    summary = {
        "stage": "04_5_sanitize", "classes_processed": processed,
        "methods_dropped_private_or_blocked": total_m_dropped,
        "fields_dropped_private_or_blocked": total_f_dropped,
        "xml_classes_indexed": len(xml_index), "hiddenapi_signatures_indexed": len(hidden_index),
    }
    (output_dir / "sanitize_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print()
    print_header("STAGE 04.5 COMPLETE")
    print(f"-> Classes processed            : {processed:,}")
    print(f"-> Private/blocked mths dropped : {total_m_dropped:,}")
    print(f"-> Private/blocked flds dropped : {total_f_dropped:,}")
    print(f"-> Output                       : {output_dir}")


if __name__ == "__main__":
    main()