import argparse
import json
import sys
from pathlib import Path
from collections import defaultdict

def print_header(title):
    print("==================================================")
    print(f" {title}")
    print("==================================================")

def generate_default_targets(api_version):
    """Generates the exact starter targets.json specified in your architecture."""
    return {
        "android_version": api_version,
        "mode": "manual",
        "targets": [
            {"fqn": "android.app.Activity", "enabled": True, "priority": 1, "notes": "core lifecycle"},
            {"fqn": "android.view.View", "enabled": True, "priority": 1, "notes": "base of everything"},
            {"fqn": "android.view.ViewGroup", "enabled": True, "priority": 1, "notes": "layout base"},
            {"fqn": "android.widget.Button", "enabled": True, "priority": 1, "notes": "core UI"},
            {"fqn": "android.widget.TextView", "enabled": True, "priority": 1, "notes": "text display"},
            {"fqn": "android.widget.EditText", "enabled": True, "priority": 1, "notes": "text input"},
            {"fqn": "android.widget.ImageView", "enabled": True, "priority": 1, "notes": "image display"},
            {"fqn": "android.widget.LinearLayout", "enabled": True, "priority": 1, "notes": "layout"},
            {"fqn": "android.widget.FrameLayout", "enabled": True, "priority": 1, "notes": "layout"},
            {"fqn": "android.content.Context", "enabled": True, "priority": 1, "notes": "Android context"},
            {"fqn": "android.content.Intent", "enabled": True, "priority": 1, "notes": "navigation"},
            {"fqn": "android.os.Bundle", "enabled": True, "priority": 1, "notes": "data passing"},
            {"fqn": "android.os.Handler", "enabled": True, "priority": 2, "notes": "thread posting"},
            {"fqn": "android.hardware.SensorManager", "enabled": True, "priority": 2, "notes": "sensors"},
            {"fqn": "android.hardware.camera2.CameraManager", "enabled": True, "priority": 1, "notes": "camera"},
            {"fqn": "android.hardware.camera2.CameraDevice", "enabled": True, "priority": 1, "notes": "camera"},
            {"fqn": "android.media.AudioRecord", "enabled": True, "priority": 2, "notes": "audio"},
            {"fqn": "android.graphics.Bitmap", "enabled": True, "priority": 1, "notes": "image data"},
            {"fqn": "android.location.LocationManager", "enabled": True, "priority": 2, "notes": "GPS"},
            {"fqn": "android.widget.DatePicker", "enabled": False, "priority": 3, "notes": "skip for now"}
        ]
    }

def main():
    parser = argparse.ArgumentParser(description="Stratum Stage 02 - Inspect Classes")
    parser.add_argument("--input", type=str, required=True, help="Path to 01_extract/output/")
    parser.add_argument("--output", type=str, required=True, help="Path to 02_inspect/output/")
    args = parser.parse_args()

    print_header("STRATUM PIPELINE - STAGE 02 (INSPECT)")

    input_dir = Path(args.input)
    if not input_dir.exists():
        print(f"ERROR: Input directory not found: {input_dir}")
        sys.exit(1)

    # 1. Read API Version from Stage 01 Summary
    summary_file = input_dir / "extract_summary.json"
    api_version = "Unknown"
    if summary_file.exists():
        with open(summary_file, 'r') as f:
            api_version = json.load(f).get("android_version", "Unknown")

    # 2. Scan all .class files
    print("-> Scanning extracted classes...")
    class_files = list(input_dir.rglob("*.class"))
    
    if not class_files:
        print("ERROR: No .class files found. Did Stage 01 succeed?")
        sys.exit(1)

    # 3. Process Fully Qualified Names (FQNs)
    # Strip the base path and .class extension
    # e.g., 01_extract/output/android/widget/Button.class -> android.widget.Button
    all_fqns = []
    package_map = defaultdict(list)

    for path in class_files:
        # Get relative path from the input directory
        rel_path = path.relative_to(input_dir)
        # Convert path separators to dots and remove .class
        fqn = str(rel_path.with_suffix('')).replace('\\', '.').replace('/', '.')
        all_fqns.append(fqn)

        # Split into package and class name
        parts = fqn.rsplit('.', 1)
        if len(parts) == 2:
            pkg, cls = parts
            package_map[pkg].append(cls)
        else:
            package_map["<default>"].append(fqn)

    # Sort everything alphabetically
    all_fqns.sort()
    for pkg in package_map:
        package_map[pkg].sort()
    
    sorted_packages = sorted(package_map.keys())

    # 4. Write Output Files
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    # A. Write flat list
    flat_file = out_dir / "available_classes.txt"
    with open(flat_file, "w", encoding="utf-8") as f:
        for fqn in all_fqns:
            f.write(fqn + "\n")

    # B. Write package grouped list
    tree_file = out_dir / "available_by_package.txt"
    with open(tree_file, "w", encoding="utf-8") as f:
        for pkg in sorted_packages:
            classes = package_map[pkg]
            f.write(f"{pkg} ({len(classes)})\n")
            for cls in classes:
                f.write(f"  {cls}\n")
            f.write("\n")

    # 5. Handle targets.json (The Developer Config)
    # Notice this is placed in 02_inspect/ DIRECTLY, not inside output/
    targets_file = Path("02_inspect/targets.json")
    
    if targets_file.exists():
        print(f"-> [SAFE] {targets_file} already exists. Skipping creation to preserve your edits.")
    else:
        print(f"-> Creating starter {targets_file}...")
        targets_data = generate_default_targets(api_version)
        with open(targets_file, "w", encoding="utf-8") as f:
            json.dump(targets_data, f, indent=2)

    # 6. Print Summary
    print("\n==================================================")
    print(f" INSPECT SUCCESS: {len(all_fqns)} classes mapped.")
    print("==================================================")
    print(f"-> Flat list      : {flat_file}")
    print(f"-> Grouped list   : {tree_file}")
    print(f"-> Target config  : {targets_file}")
    print("\nNext steps:")
    print(" 1. Review 02_inspect/targets.json")
    print(" 2. Add or disable any classes you want")
    print(" 3. Proceed to Stage 03 to generate .javap files")

if __name__ == "__main__":
    main()