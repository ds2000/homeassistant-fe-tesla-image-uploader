#!/usr/bin/env python3
"""Validate submission: file presence, PNG signature, RGBA, and climate-active checks."""

import argparse
import json
import os
import struct
import sys

import cv2
import numpy as np

# Must match the upload filenames from the web app (docs/submit.js LAYERS).
REQUIRED_OFFCHARGE = [
    "closed.png",
    "cp.png",
    "cp_ft.png",
    "rt.png",
    "front_doors.png",
    "rear_doors.png",
    "all_doors.png",
    "top_controls.png",
    "top_climate.png",
]

REQUIRED_ONCHARGE = [
    "oc_closed.png",
    "oc_cp_ft.png",
    "oc_rt.png",
    "oc_front_doors.png",
    "oc_rear_doors.png",
    "oc_all_doors.png",
]

REQUIRED_FILES = REQUIRED_OFFCHARGE + REQUIRED_ONCHARGE

# Side-view screenshots that show the windscreen (checked for climate-active).
# Excludes panel screenshots (top_controls.png, top_climate.png).
SIDE_VIEW_FILES = [
    "closed.png",
    "cp.png",
    "cp_ft.png",
    "rt.png",
    "front_doors.png",
    "rear_doors.png",
    "all_doors.png",
    "oc_closed.png",
    "oc_cp_ft.png",
    "oc_rt.png",
    "oc_front_doors.png",
    "oc_rear_doors.png",
    "oc_all_doors.png",
]

# Bright-achromatic ratio above this threshold flags a single file.
# Individual files can exceed this due to normal variation (e.g. oncharge angle),
# so we require multiple flagged files to reject a submission.
CLIMATE_ACTIVE_THRESHOLD = 0.17
# Minimum number of flagged files to reject the submission.
CLIMATE_ACTIVE_MIN_FLAGS = 4

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

# PNG IHDR color type constants
COLOR_TYPE_NAMES = {
    0: "Greyscale",
    2: "RGB (no alpha)",
    3: "Indexed-colour (palette)",
    4: "Greyscale with alpha",
    6: "RGBA",
}


def check_climate_active(filepath):
    """Check whether a side-view screenshot has climate-active air flow graphics.

    Detects bright achromatic pixels inside the windscreen region.  Climate-off
    cars typically score ~9-11 %; climate-on cars score ~17 %+.  A threshold of
    17 % is used to flag likely climate-active screenshots.

    Returns a dict with ``climate_warning`` (bool) and ``bright_ratio`` (float),
    or ``None`` if the image could not be analysed.
    """
    img = cv2.imread(filepath, cv2.IMREAD_COLOR)
    if img is None:
        return None

    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB).astype(np.float64)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float64)
    h, w = img.shape[:2]

    # --- find car bounds via Euclidean distance from background color ---
    # Sample background from top-left corner (always app chrome/bg).
    bg_color = rgb[:10, :10].mean(axis=(0, 1))
    bg_dist = np.sqrt(np.sum((rgb - bg_color) ** 2, axis=2))
    non_bg = bg_dist > 25
    cols = np.where(non_bg.any(axis=0))[0]
    rows = np.where(non_bg.any(axis=1))[0]
    if len(cols) == 0 or len(rows) == 0:
        return None

    x0, x1 = int(cols[0]), int(cols[-1])
    y0, y1 = int(rows[0]), int(rows[-1])
    car_h = y1 - y0

    if car_h < 50:
        return None  # too small to be meaningful

    # --- isolate windscreen region (upper 40 % of car, full width) ---
    ws_y0 = y0
    ws_y1 = y0 + int(car_h * 0.4)
    ws_region = img[ws_y0:ws_y1, x0:x1]
    ws_gray = gray[ws_y0:ws_y1, x0:x1]

    # Restrict to actual car pixels (not background)
    ws_car = non_bg[ws_y0:ws_y1, x0:x1]

    # Build windscreen shape via morphological closing on dark pixels.
    # Dark pixels (gray 20-80) within the car bounds approximate the glass.
    dark_mask = (ws_car & (ws_gray >= 20) & (ws_gray <= 80)).astype(np.uint8) * 255
    kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (51, 51)
    )
    windscreen_mask = cv2.morphologyEx(dark_mask, cv2.MORPH_CLOSE, kernel)

    ws_area = int(windscreen_mask.sum() // 255)
    if ws_area < 100:
        return {"climate_warning": False, "bright_ratio": 0.0}

    # Count bright achromatic pixels inside windscreen shape.
    # Bright = gray > 80, achromatic = max channel - min channel < 30.
    b_ch, g_ch, r_ch = cv2.split(ws_region)
    ch_max = np.maximum(np.maximum(b_ch, g_ch), r_ch)
    ch_min = np.minimum(np.minimum(b_ch, g_ch), r_ch)
    ch_range = ch_max.astype(np.int16) - ch_min.astype(np.int16)

    bright_achromatic = (
        (ws_gray > 80) & (ch_range < 30) & (windscreen_mask > 0)
    )
    bright_count = int(bright_achromatic.sum())
    ratio = bright_count / ws_area

    return {
        "climate_warning": ratio > CLIMATE_ACTIVE_THRESHOLD,
        "bright_ratio": round(ratio, 4),
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

    # Climate-active detection on side-view screenshots.
    # Any flagged file is a hard failure — contributor must retake with climate OFF.
    climate_warnings = []
    for filename in SIDE_VIEW_FILES:
        filepath = os.path.join(submission_dir, filename)
        if not os.path.isfile(filepath):
            continue
        result = check_climate_active(filepath)
        if result and result["climate_warning"]:
            climate_warnings.append(
                {
                    "file": filename,
                    "bright_ratio": result["bright_ratio"],
                    "message": (
                        f"Climate-active screenshot detected "
                        f"(bright ratio {result['bright_ratio']:.1%} "
                        f"> {CLIMATE_ACTIVE_THRESHOLD:.0%} threshold). "
                        f"Please retake with climate control OFF."
                    ),
                }
            )

    if len(climate_warnings) >= CLIMATE_ACTIVE_MIN_FLAGS:
        all_pass = False

    return {
        "submission_dir": submission_dir,
        "required_count": len(REQUIRED_FILES),
        "passed": sum(1 for r in results if not r["error"]),
        "failed": sum(1 for r in results if r["error"]),
        "all_pass": all_pass,
        "files": results,
        "extra_files": extra,
        "climate_warnings": climate_warnings,
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

    # Print climate errors to stderr for visibility in CI logs.
    if report.get("climate_warnings"):
        n = len(report["climate_warnings"])
        if not report["all_pass"] and n >= CLIMATE_ACTIVE_MIN_FLAGS:
            print(f"\n--- CLIMATE CHECK FAILED ({n} files flagged) ---", file=sys.stderr)
            for w in report["climate_warnings"]:
                print(f"  REJECT: {w['file']}: {w['message']}", file=sys.stderr)
            print(
                "  Submission rejected — retake all screenshots with climate OFF.",
                file=sys.stderr,
            )
        else:
            print(f"\n--- CLIMATE CHECK: {n} file(s) borderline (< {CLIMATE_ACTIVE_MIN_FLAGS} required to reject) ---", file=sys.stderr)
            for w in report["climate_warnings"]:
                print(f"  NOTE: {w['file']}: {w['message']}", file=sys.stderr)

    sys.exit(0 if report["all_pass"] else 1)


if __name__ == "__main__":
    main()
