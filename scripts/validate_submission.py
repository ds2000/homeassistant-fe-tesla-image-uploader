#!/usr/bin/env python3
"""Validate submission: file presence, PNG signature, and RGBA checks."""

import argparse
import json
import os
import struct
import sys

# Must match the output names from process_screenshots.py
REQUIRED_FILES = [
    "base.png",
    "chargeport-open.png",
    "frunk-open.png",
    "trunk-open.png",
    "df-open.png",
    "dr-open.png",
    "pf-open.png",
    "pr-open.png",
    "controls-bg.png",
    "climate-bg.png",
]

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

# PNG IHDR color type constants
COLOR_TYPE_NAMES = {
    0: "Greyscale",
    2: "RGB (no alpha)",
    3: "Indexed-colour (palette)",
    4: "Greyscale with alpha",
    6: "RGBA",
}


def validate_png(filepath):
    """Validate a single PNG file. Returns a result dict."""
    result = {
        "file": os.path.basename(filepath),
        "exists": True,
        "valid_png": False,
        "rgba": False,
        "width": None,
        "height": None,
        "bit_depth": None,
        "color_type": None,
        "color_type_name": None,
        "error": None,
    }

    try:
        with open(filepath, "rb") as f:
            sig = f.read(8)
            if sig != PNG_SIGNATURE:
                result["error"] = "Invalid PNG signature (not a PNG file)"
                return result
            result["valid_png"] = True

            # Read IHDR chunk: 4 bytes length + 4 bytes type + 13 bytes data
            chunk_len_bytes = f.read(4)
            chunk_type = f.read(4)

            if chunk_type != b"IHDR":
                result["error"] = "First chunk is not IHDR (malformed PNG)"
                return result

            ihdr_data = f.read(13)
            if len(ihdr_data) < 13:
                result["error"] = "IHDR chunk too short (malformed PNG)"
                return result

            width, height = struct.unpack(">II", ihdr_data[0:8])
            bit_depth = ihdr_data[8]
            color_type = ihdr_data[9]

            result["width"] = width
            result["height"] = height
            result["bit_depth"] = bit_depth
            result["color_type"] = color_type
            result["color_type_name"] = COLOR_TYPE_NAMES.get(
                color_type, f"Unknown ({color_type})"
            )
            result["rgba"] = color_type == 6

            if color_type != 6:
                result["error"] = (
                    f"Color type is {result['color_type_name']} — must be RGBA (type 6)"
                )

    except OSError as e:
        result["error"] = f"Cannot read file: {e}"

    return result


def validate_submission(submission_dir):
    """Validate all files in a submission directory. Returns a report dict."""
    results = []
    all_pass = True

    for filename in REQUIRED_FILES:
        filepath = os.path.join(submission_dir, filename)

        if not os.path.isfile(filepath):
            results.append(
                {
                    "file": filename,
                    "exists": False,
                    "valid_png": False,
                    "rgba": False,
                    "width": None,
                    "height": None,
                    "bit_depth": None,
                    "color_type": None,
                    "color_type_name": None,
                    "error": "File is missing",
                }
            )
            all_pass = False
        else:
            result = validate_png(filepath)
            results.append(result)
            if result["error"]:
                all_pass = False

    # Check for extra unexpected files
    expected = set(REQUIRED_FILES)
    actual = set()
    try:
        actual = set(os.listdir(submission_dir))
    except OSError:
        pass
    extra = sorted(actual - expected)

    return {
        "submission_dir": submission_dir,
        "required_count": len(REQUIRED_FILES),
        "passed": sum(1 for r in results if not r["error"]),
        "failed": sum(1 for r in results if r["error"]),
        "all_pass": all_pass,
        "files": results,
        "extra_files": extra,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Validate a Tesla card image submission"
    )
    parser.add_argument(
        "--submission-dir",
        required=True,
        help="Path to the submission directory containing PNG files",
    )
    args = parser.parse_args()

    if not os.path.isdir(args.submission_dir):
        print(
            json.dumps(
                {"error": f"Directory not found: {args.submission_dir}"}, indent=2
            )
        )
        sys.exit(1)

    report = validate_submission(args.submission_dir)
    print(json.dumps(report, indent=2))

    sys.exit(0 if report["all_pass"] else 1)


if __name__ == "__main__":
    main()
