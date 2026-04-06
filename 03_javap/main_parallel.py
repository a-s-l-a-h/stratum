import argparse
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock


def print_header(title):
    print("==================================================")
    print(f" {title}")
    print("==================================================")


def auto_thread_count(num_classes: int) -> int:
    """
    Use half the CPU cores — conservative so javap subprocesses
    don't saturate the machine and cause failures.
    Always at least 1, never more than 8 or num_classes.
    """
    cpu_cores = os.cpu_count() or 2
    threads   = max(1, cpu_cores // 2)
    threads   = min(threads, 8, num_classes)
    return threads


def run_javap(javap_path: str, classpath: str, fqn: str) -> tuple[bool, str]:
    """
    Run javap on a single class. Returns (success, output_text).
    On first failure, retries ONCE silently then gives up.
    """
    cmd = [
        javap_path,
        "-s",
        "-p",
        "-classpath", classpath,
        fqn,
    ]
    last_err = ""
    for attempt in range(2):   # attempt 0 = first try, attempt 1 = one retry
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                return True, result.stdout
            last_err = result.stderr.strip()
        except subprocess.TimeoutExpired:
            last_err = "TIMEOUT after 30 seconds"
        except Exception as e:
            last_err = str(e)

    return False, last_err


def fqn_to_path(fqn: str) -> Path:
    return Path(*fqn.split("."))


def process_class(
    fqn: str,
    javap_path: str,
    classpath: str,
    output_dir: Path,
    print_lock: Lock,
    counter: list,
    total: int,
) -> tuple[bool, str, str]:
    """Process one class. Returns (ok, fqn, error_or_empty)."""
    rel_path = fqn_to_path(fqn)
    out_file = output_dir / rel_path.with_suffix(".javap")
    out_file.parent.mkdir(parents=True, exist_ok=True)

    ok, text = run_javap(javap_path, classpath, fqn)

    with print_lock:
        counter[0] += 1
        idx = counter[0]
        if ok:
            out_file.write_text(text, encoding="utf-8")
            print(f"  [{idx:4d}/{total}] OK      {fqn}")
            return True, fqn, ""
        else:
            print(f"  [{idx:4d}/{total}] FAILED  {fqn}")
            print(f"             {text[:120]}")
            return False, fqn, text


def main():
    parser = argparse.ArgumentParser(description="Stratum Stage 03 - Run javap")
    parser.add_argument("--input",   required=True, help="Path to 01_extract/output/")
    parser.add_argument("--targets", required=True, help="Path to 02_inspect/targets.json")
    parser.add_argument("--setup",   required=True, help="Path to 00_setup/output/setup_report.json")
    parser.add_argument("--output",  required=True, help="Path to 03_javap/output/")
    parser.add_argument("--threads", type=int, default=0,
                        help="Worker threads (0 = auto: half of CPU cores, max 8)")
    args = parser.parse_args()

    print_header("STRATUM PIPELINE - STAGE 03 (JAVAP)")

    # ── Resolve & validate paths ───────────────────────────────────────────────
    input_dir    = Path(args.input)
    targets_file = Path(args.targets)
    setup_file   = Path(args.setup)
    output_dir   = Path(args.output)

    for p, label in [
        (input_dir,    "01_extract/output/"),
        (targets_file, "targets.json"),
        (setup_file,   "setup_report.json"),
    ]:
        if not p.exists():
            print(f"ERROR: Not found: {p}  ({label})")
            sys.exit(1)

    # ── Read setup report ──────────────────────────────────────────────────────
    with open(setup_file, "r") as f:
        setup = json.load(f)

    javap_path = setup.get("javap_path", "javap")
    print(f"-> javap path : {javap_path}")

    # ── Read targets.json ──────────────────────────────────────────────────────
    with open(targets_file, "r") as f:
        targets_data = json.load(f)

    mode    = targets_data.get("mode", "manual")
    targets = targets_data.get("targets", [])
    print(f"-> Mode       : {mode}")

    # ── Decide which classes to process ───────────────────────────────────────
    if mode == "full":
        class_files = list(input_dir.rglob("*.class"))
        to_process  = []
        for cf in class_files:
            rel = cf.relative_to(input_dir).with_suffix("")
            fqn = str(rel).replace("\\", ".").replace("/", ".")
            to_process.append(fqn)
        to_process.sort()
        print(f"-> Full mode  : {len(to_process)} classes")
    else:
        to_process = [
            t["fqn"] for t in targets
            if t.get("enabled", False)
        ]
        print(f"-> Manual mode: {len(to_process)} enabled classes")

    if not to_process:
        print("ERROR: No classes to process. Check targets.json.")
        sys.exit(1)

    # ── Thread count ───────────────────────────────────────────────────────────
    num_threads = args.threads if args.threads > 0 else auto_thread_count(len(to_process))
    print(f"-> Threads    : {num_threads}  (auto={args.threads == 0})")

    output_dir.mkdir(parents=True, exist_ok=True)

    # ── Run in parallel ────────────────────────────────────────────────────────
    successful: list[str]  = []
    failed:     list[dict] = []
    print_lock = Lock()
    counter    = [0]

    print()
    with ThreadPoolExecutor(max_workers=num_threads) as pool:
        futures = {
            pool.submit(
                process_class,
                fqn, javap_path, str(input_dir),
                output_dir, print_lock, counter, len(to_process),
            ): fqn
            for fqn in to_process
        }

        for future in as_completed(futures):
            try:
                ok, fqn, err = future.result()
                if ok:
                    successful.append(fqn)
                else:
                    failed.append({"fqn": fqn, "error": err})
            except Exception as exc:
                fqn = futures[future]
                with print_lock:
                    print(f"  [THREAD CRASH] {fqn}: {exc}")
                failed.append({"fqn": fqn, "error": str(exc)})

    # ── Write summary ──────────────────────────────────────────────────────────
    summary = {
        "mode":            mode,
        "threads_used":    num_threads,
        "total_processed": len(to_process),
        "successful":      len(successful),
        "failed":          len(failed),
        "failed_classes":  failed,
    }
    summary_file = output_dir / "javap_summary.json"
    with open(summary_file, "w") as f:
        json.dump(summary, f, indent=2)

    # ── Final report ───────────────────────────────────────────────────────────
    print()
    print_header("STAGE 03 COMPLETE")
    print(f"-> Processed  : {len(to_process)}")
    print(f"-> Successful : {len(successful)}")
    print(f"-> Failed     : {len(failed)}")
    print(f"-> Threads    : {num_threads}")
    print(f"-> Output     : {output_dir}")
    print(f"-> Summary    : {summary_file}")

    if failed:
        print()
        print("FAILED CLASSES:")
        for item in failed:
            print(f"  {item['fqn']}")
            print(f"    {item['error'][:100]}")
        print()
        print("These classes were skipped. Check the FQN spelling in targets.json.")
        print("All other classes succeeded. You can proceed to Stage 04.")
    else:
        print()
        print("All classes succeeded. Proceed to Stage 04.")


if __name__ == "__main__":
    main()