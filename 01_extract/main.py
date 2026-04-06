import argparse
import json
import zipfile
import sys
import time
from pathlib import Path
from datetime import datetime
from collections import defaultdict

def print_header(title):
    print("==================================================")
    print(f" {title}")
    print("==================================================")

def load_setup_report(setup_path):
    """Load paths from Stage 00 report."""
    setup_file = Path(setup_path)
    if not setup_file.exists():
        print(f"ERROR: Setup report not found at {setup_file}")
        print("Please run Stage 00 first.")
        sys.exit(1)
        
    with open(setup_file, 'r') as f:
        return json.load(f)

def extract_jar(jar_path, out_dir):
    """Extract all .class files from the Android JAR."""
    jar_file = Path(jar_path)
    if not jar_file.exists():
        print(f"ERROR: android.jar not found at {jar_file}")
        sys.exit(1)

    print(f"\n-> Opening {jar_file.name}...")
    
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    total_classes = 0
    package_counts = defaultdict(int)

    start_time = time.time()

    with zipfile.ZipFile(jar_file, 'r') as zf:
        # Get list of all files in the JAR
        all_files = zf.namelist()
        
        # Filter only .class files, skip META-INF and other metadata
        class_files = [f for f in all_files if f.endswith('.class') and not f.startswith('META-INF')]
        total_target = len(class_files)
        
        print(f"-> Found {total_target} .class files. Extracting...")

        for i, file_path in enumerate(class_files, 1):
            # Extract the file (this automatically creates the folder structure)
            zf.extract(file_path, path=out_path)
            total_classes += 1
            
            # Count the package (e.g., android/widget/Button.class -> android.widget)
            parts = file_path.split('/')
            if len(parts) > 1:
                pkg_name = '.'.join(parts[:-1])
                package_counts[pkg_name] += 1
            
            # Print progress every 2000 files to keep terminal clean
            if i % 2000 == 0 or i == total_target:
                print(f"   ... extracted {i}/{total_target} classes")

    elapsed = time.time() - start_time
    return total_classes, package_counts, elapsed

def main():
    parser = argparse.ArgumentParser(description="Stratum Stage 01 - Extract Android JAR")
    parser.add_argument("--setup", type=str, required=True, help="Path to setup_report.json from Stage 00")
    parser.add_argument("--output", type=str, required=True, help="Output directory for extracted .class files")
    parser.add_argument("--force", action="store_true", help="Force re-extraction even if already done")
    args = parser.parse_args()

    print_header("STRATUM PIPELINE - STAGE 01 (EXTRACT)")

    # 1. Read single source of truth from Stage 00
    setup_data = load_setup_report(args.setup)
    jar_path = setup_data.get("jar_path")
    api_version = setup_data.get("android_api", "Unknown")

    if not jar_path:
        print("ERROR: jar_path missing in setup_report.json.")
        sys.exit(1)

    out_dir = Path(args.output)
    summary_file = out_dir / "extract_summary.json"

    # 2. Check Cache (Optimization)
    if summary_file.exists() and not args.force:
        print(f"\n[CACHE HIT] Extraction already completed for API {api_version}.")
        print("Use --force to re-extract.")
        print(f"Output is safely stored at: {out_dir.resolve()}")
        sys.exit(0)

    # 3. Perform Extraction
    total_classes, package_counts, elapsed = extract_jar(jar_path, out_dir)

    # 4. Generate Summary Report
    # Sort packages by size (largest first) to make the JSON nice to read
    sorted_packages = dict(sorted(package_counts.items(), key=lambda item: item[1], reverse=True))

    summary = {
        "android_version": api_version,
        "total_classes": total_classes,
        "extraction_time_seconds": round(elapsed, 2),
        "packages": sorted_packages,
        "timestamp": datetime.now().isoformat()
    }

    with open(summary_file, "w") as f:
        json.dump(summary, f, indent=2)

    # 5. Print Results
    print("\n==================================================")
    print(f" EXTRACTION SUCCESS: {total_classes} classes extracted!")
    print(f" Time taken: {elapsed:.2f} seconds")
    print("==================================================")
    
    # Print a quick preview of top 5 largest packages
    print("\nTop 5 Largest Packages Extracted:")
    top_5 = list(sorted_packages.items())[:5]
    for pkg, count in top_5:
        print(f"  - {pkg}: {count} classes")

    print(f"\n-> Full mirror stored in: {out_dir / 'android'}")
    print(f"-> Summary written to: {summary_file}")

if __name__ == "__main__":
    main()