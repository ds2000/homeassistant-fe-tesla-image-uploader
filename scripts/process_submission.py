#!/usr/bin/env python3
"""Local test wrapper: run the processing pipeline on a flat submission directory.

Takes a flat directory of 13 upload files (8 offcharge + 5 oncharge) and runs
process_screenshots.py twice — once per mode — producing overlays for both.

Usage:
    python3 scripts/process_submission.py \
        --submission-dir submissions/test/ \
        --output-dir output/

Output structure:
    output/
      offcharge/          (processed images + overlays/)
      oncharge/           (processed images + overlays/)
"""

import argparse
import subprocess
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
PROCESS_SCRIPT = SCRIPT_DIR / "process_screenshots.py"


def run_pipeline(submission_dir, output_dir, mode, verbose=False):
    """Run process_screenshots.py for one mode."""
    mode_output = Path(output_dir) / mode
    cmd = [
        sys.executable, str(PROCESS_SCRIPT),
        "--input-dir", str(submission_dir),
        "--output-dir", str(mode_output),
        "--mode", mode,
        "--generate-overlays",
    ]
    if verbose:
        cmd.append("--verbose")

    print(f"\n{'=' * 60}")
    print(f"Processing: {mode}")
    print(f"  Input:  {submission_dir}")
    print(f"  Output: {mode_output}")
    print(f"{'=' * 60}\n")

    result = subprocess.run(cmd)
    if result.returncode != 0:
        print(f"\nERROR: {mode} processing failed (exit code {result.returncode})")
        return False
    return True


def main():
    parser = argparse.ArgumentParser(
        description="Process a flat submission directory for both offcharge and oncharge modes",
    )
    parser.add_argument("--submission-dir", required=True,
                        help="Directory containing all 13 uploaded PNG files")
    parser.add_argument("--output-dir", required=True,
                        help="Output directory (offcharge/ and oncharge/ subdirs created)")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Pass --verbose to process_screenshots.py")
    args = parser.parse_args()

    submission_dir = Path(args.submission_dir)
    if not submission_dir.is_dir():
        print(f"ERROR: Submission directory not found: {submission_dir}")
        sys.exit(1)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    ok = True
    for mode in ("offcharge", "oncharge"):
        if not run_pipeline(submission_dir, output_dir, mode, verbose=args.verbose):
            ok = False

    if ok:
        print(f"\nDone. Results in {output_dir}/")
        print(f"  offcharge: {output_dir / 'offcharge'}")
        print(f"  oncharge:  {output_dir / 'oncharge'}")
    else:
        print("\nOne or more modes failed — check output above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
