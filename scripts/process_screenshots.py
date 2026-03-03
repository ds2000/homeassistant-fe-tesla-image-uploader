#!/usr/bin/env python3
"""
Device-agnostic Tesla screenshot processing pipeline.

Takes raw Tesla app screenshots from ANY phone/device and produces
card-ready images with consistent framing and UI removal.

Output images:
  Side views (417x262):
    base.png, chargeport-open.png, frunk-open.png, trunk-open.png,
    nf-open.png, nr-open.png, ff-open.png, fr-open.png
    (doors split from combined front-doors / rear-doors inputs)
  Panels:
    controls-bg.png (545x859), climate-bg.png (551x950)

Usage:
    python scripts/process_screenshots.py \\
        --input-dir screenshots/Tesla/offcharge/ \\
        --output-dir output/ \\
        --reference-dir path/to/red/images/ \\
        --manifest manifest.json \\
        --verbose
"""

import argparse
import json
import sys
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter
import cv2
import numpy as np


# ═══════════════════════════════════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════════════════════════════════

SIDE_VIEW_SIZE = (417, 262)
CONTROLS_SIZE = (545, 859)
CLIMATE_SIZE = (551, 950)

# Background detection
CORNER_SAMPLE_SIZE = 20
BG_DISTANCE_THRESHOLD = 10

# Side-view car detection (proportional to image height)
SEARCH_ZONE_TOP = 0.15
SEARCH_ZONE_BOTTOM = 0.55
EDGE_MARGIN_PCT = 0.08
ROW_COVERAGE_THRESHOLD = 0.03
GAP_BRIDGE_PCT = 0.01
COL_COVERAGE_THRESHOLD = 0.008

# Union crop frame padding (fraction of the max union dimension)
UNION_PADDING_PCT = 0.05

# Auto-detect: maps output names to possible input filename stems (exact match).
#
# Near/far is relative to the camera:
#   nf = near-side front door (close to camera, many visible pixels)
#   nr = near-side rear door
#   ff = far-side front door (away from camera, few visible pixels)
#   fr = far-side rear door
#
# The source files pf/pr always show the LEFT side of the image, and
# cp_df/cp_dr always show the RIGHT side.  Which side is "near" depends
# on camera angle, so mappings differ by mode.

SIDE_VIEW_PATTERNS_COMMON = {
    "base": ["closed", "base", "all_closed"],
    "chargeport-open": ["cp", "chargeport", "charge_port"],
    "frunk-open": ["ft", "frunk", "cp_ft"],
    "trunk-open": ["rt", "trunk", "rear_trunk"],
}

# Individual door patterns — retained for backwards compatibility with
# submissions that still include per-door screenshots.  When these are NOT
# present the pipeline falls back to splitting combined-door screenshots
# (see COMBINED_DOOR_PATTERNS_* below).
#
# Offcharge: front 3/4 from the right → RIGHT of image = near side
#   cp_df/cp_dr are on the RIGHT → near side
#   pf/pr are on the LEFT → far side
SIDE_VIEW_PATTERNS_OFFCHARGE = {
    "nf-open": ["cp_df", "df"],         # near-front (right of image)
    "nr-open": ["cp_dr", "dr"],         # near-rear
    "ff-open": ["pf"],                  # far-front (left of image)
    "fr-open": ["pr"],                  # far-rear
}

# Oncharge: rear 3/4 from the left → LEFT of image = near side
#   pf/pr are on the LEFT → near side
#   cp_df/cp_dr are on the RIGHT → far side
SIDE_VIEW_PATTERNS_ONCHARGE = {
    "nf-open": ["pf"],                  # near-front (left of image)
    "nr-open": ["pr"],                  # near-rear
    "ff-open": ["cp_df", "df"],         # far-front (right of image)
    "fr-open": ["cp_dr", "dr"],         # far-rear (right of image)
}

# Combined-door input patterns: contributor supplies a screenshot with BOTH
# front doors open (or both rear doors open).  The pipeline splits these into
# individual near-side / far-side overlays via spatial analysis.
# Each entry: output-key -> (input stems, near-door output, far-door output)
#
# Offcharge: near side = RIGHT of image
COMBINED_DOOR_PATTERNS_OFFCHARGE = {
    "front-doors": (["front_doors", "both_front", "cp_df_pf"], "nf", "ff"),
    "rear-doors":  (["rear_doors", "both_rear", "cp_dr_pr"], "nr", "fr"),
}

# Oncharge: near side = LEFT of image
COMBINED_DOOR_PATTERNS_ONCHARGE = {
    "front-doors": (["oc_front_doors", "oc_both_front"], "nf", "ff"),
    "rear-doors":  (["oc_rear_doors", "oc_both_rear", "oc_cp_dr_pr"], "nr", "fr"),
}

PANEL_PATTERNS = {
    "controls-bg": ["top_controls", "controls"],
    "climate-bg": ["top_climate", "climate"],
}

# Oncharge variants for flat-directory submissions where all 13 files live in
# one directory.  oc_-prefixed stems are tried first (matching the web-app
# upload filenames), with unprefixed fallbacks for separate oncharge dirs.
SIDE_VIEW_PATTERNS_COMMON_ONCHARGE = {
    "base": ["oc_closed", "oc_base", "oc_all_closed"] + ["closed", "base", "all_closed"],
    "frunk-open": ["oc_ft", "oc_frunk", "oc_cp_ft"] + ["ft", "frunk", "cp_ft"],
    "trunk-open": ["oc_rt", "oc_trunk", "oc_rear_trunk"] + ["rt", "trunk", "rear_trunk"],
    # No chargeport-open for oncharge
}

PANEL_PATTERNS_ONCHARGE = {
    "controls-bg": ["oc_top_controls", "oc_controls"] + ["top_controls", "controls"],
    "climate-bg": ["oc_top_climate", "oc_climate"] + ["top_climate", "climate"],
}

# Combined-state screenshots: when multiple doors are open simultaneously the
# appearance differs from compositing individual door overlays (different
# shadows, interior visibility).  Map output name → (input stems, constituents).
# Constituents are the overlay names that this combined image replaces.
# Source mapping also depends on camera angle.
COMBINED_PATTERNS_OFFCHARGE = {
    "nf-nr-combined": (["cp_df_dr", "df_dr", "dfdr"], ["nf", "nr"]),  # both near-side doors
    "ff-fr-combined": (["pf_pr", "pfpr"], ["ff", "fr"]),              # both far-side doors
}

COMBINED_PATTERNS_ONCHARGE = {
    "nf-nr-combined": (["pf_pr", "pfpr"], ["nf", "nr"]),              # both near-side doors
    "ff-fr-combined": (["cp_df_dr", "dfdr", "df_dr"], ["ff", "fr"]),  # both far-side doors
}

# All-doors pattern: single screenshot with all 4 doors open.
# Split into near/far halves → nf-nr-combined and ff-fr-combined.
ALL_DOORS_PATTERN_OFFCHARGE = ["all_doors", "all_4_doors"]
ALL_DOORS_PATTERN_ONCHARGE = ["oc_all_doors", "all_doors", "all_4_doors"]

# Panel processing uses OpenCV inpainting (see inpaint_ui_overlays)
# instead of zone-based cleanup — no hardcoded UI zone coordinates needed.


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def find_contiguous_runs(mask, gap_bridge=0):
    """Find contiguous runs of True values in a 1D boolean array.

    Args:
        mask: 1D boolean numpy array.
        gap_bridge: Bridge gaps of up to this many False values between runs.

    Returns:
        List of (start, end) tuples (end is exclusive).
    """
    if gap_bridge > 0:
        mask = mask.copy()
        n = len(mask)
        for i in range(n):
            if not mask[i]:
                left = np.any(mask[max(0, i - gap_bridge):i])
                right = np.any(mask[i + 1:min(n, i + gap_bridge + 1)])
                if left and right:
                    mask[i] = True

    runs = []
    start = None
    for i, v in enumerate(mask):
        if v and start is None:
            start = i
        elif not v and start is not None:
            runs.append((start, i))
            start = None
    if start is not None:
        runs.append((start, len(mask)))
    return runs


# ═══════════════════════════════════════════════════════════════════════════════
# Background Detection
# ═══════════════════════════════════════════════════════════════════════════════

def detect_background_color(arr):
    """Detect background color by sampling the 4 corners of the image."""
    s = CORNER_SAMPLE_SIZE
    h, w = arr.shape[:2]
    # Use only RGB channels (images may be RGBA)
    rgb = arr[:, :, :3]
    corners = [
        rgb[:s, :s],
        rgb[:s, w - s:],
        rgb[h - s:, :s],
        rgb[h - s:, w - s:],
    ]
    all_pixels = np.concatenate([c.reshape(-1, 3) for c in corners], axis=0)
    return np.median(all_pixels, axis=0).astype(np.float64)


def create_non_bg_mask(arr, bg_color, threshold=BG_DISTANCE_THRESHOLD):
    """Create a boolean mask where True = pixel is NOT background."""
    rgb = arr[:, :, :3]
    diff = rgb.astype(np.float64) - bg_color
    dist = np.sqrt(np.sum(diff ** 2, axis=2))
    return dist > threshold


def detect_battery_bottom(arr):
    """Find the bottom edge of all Tesla app header UI content.

    Scans the top portion of a side-view screenshot (above the car
    search zone) for UI text and icons: status bar, vehicle name, range,
    battery icon, and charging status text. Returns the y-coordinate
    of the last row containing UI content, or 0 if none found.

    The scan is limited to the top SEARCH_ZONE_TOP fraction of the
    image, which is always above the car. This avoids false positives
    from car body highlights and chrome reflections.

    Works for both off-charge (yellow battery) and on-charge (green
    lightning bolt, white range/status text) screenshots.
    """
    h, w = arr.shape[:2]
    scan_limit = int(h * SEARCH_ZONE_TOP)

    r_ch = arr[:scan_limit, :, 0].astype(int)
    g_ch = arr[:scan_limit, :, 1].astype(int)
    b_ch = arr[:scan_limit, :, 2].astype(int)

    # Detect UI text/icons: achromatic bright pixels (white/gray text),
    # yellow battery icon, and green charging indicators.
    min_ch = np.minimum(np.minimum(r_ch, g_ch), b_ch)
    max_ch = np.maximum(np.maximum(r_ch, g_ch), b_ch)
    ch_range = max_ch - min_ch
    white_text = (min_ch > 120) & (ch_range < 40)
    yellow = (r_ch > 130) & (g_ch > 80) & (b_ch < 80)
    green = (g_ch > 100) & (r_ch < 80) & (b_ch < 80)

    ui_pixels = white_text | yellow | green
    row_counts = np.sum(ui_pixels, axis=1)

    # Find the last row with significant UI content (>10 pixels)
    ui_rows = np.where(row_counts > 10)[0]
    return int(ui_rows[-1]) if len(ui_rows) > 0 else 0


# ═══════════════════════════════════════════════════════════════════════════════
# Side-View Car Detection
# ═══════════════════════════════════════════════════════════════════════════════

def find_car_bounds_sideview(non_bg_mask):
    """Find the car bounding box in a side-view screenshot.

    Returns:
        (bounds, confidence) where bounds is (x0, y0, x1, y1) or None.
    """
    h, w = non_bg_mask.shape
    margin = int(w * EDGE_MARGIN_PCT)
    analysis = non_bg_mask[:, margin:w - margin]
    row_coverage = np.mean(analysis, axis=1)

    zone_top = int(h * SEARCH_ZONE_TOP)
    zone_bot = int(h * SEARCH_ZONE_BOTTOM)
    zone_coverage = row_coverage[zone_top:zone_bot]

    has_car = zone_coverage > ROW_COVERAGE_THRESHOLD
    gap_bridge = max(1, int(h * GAP_BRIDGE_PCT))
    runs = find_contiguous_runs(has_car, gap_bridge=gap_bridge)

    if not runs:
        return None, 0.0

    best = max(runs, key=lambda r: r[1] - r[0])
    car_y0 = zone_top + best[0]
    car_y1 = zone_top + best[1]

    car_band = non_bg_mask[car_y0:car_y1, :]
    col_coverage = np.mean(car_band, axis=0)
    car_cols = np.where(col_coverage > COL_COVERAGE_THRESHOLD)[0]

    if len(car_cols) == 0:
        return None, 0.0

    car_x0 = int(car_cols[0])
    car_x1 = int(car_cols[-1])

    # Confidence scoring
    car_w = car_x1 - car_x0
    car_h = car_y1 - car_y0
    center_y = (car_y0 + car_y1) / 2 / h
    width_ratio = car_w / w
    aspect = car_w / max(car_h, 1)

    size_score = max(0.0, 1.0 - abs(width_ratio - 0.75) / 0.35)
    pos_score = max(0.0, 1.0 - abs(center_y - 0.30) / 0.20)
    aspect_score = 1.0 if 1.5 < aspect < 4.5 else 0.5
    contiguity = float(np.mean(non_bg_mask[car_y0:car_y1, car_x0:car_x1]))

    confidence = (size_score * 0.25 + pos_score * 0.25 +
                  aspect_score * 0.25 + contiguity * 0.25)

    return (car_x0, car_y0, car_x1, car_y1), round(confidence, 3)


# ═══════════════════════════════════════════════════════════════════════════════
# Crop Frame (union-based)
# ═══════════════════════════════════════════════════════════════════════════════

def compute_crop_frame_from_union(union_bounds, img_shape,
                                  target_size=SIDE_VIEW_SIZE,
                                  padding_pct=UNION_PADDING_PCT,
                                  min_y0=0):
    """Compute crop frame from the union of all car bounding boxes.

    Adds padding around the union and adjusts to maintain the target
    aspect ratio. This ensures ALL side-view states (including trunk-open
    which extends higher) fit within the same frame.

    Args:
        min_y0: Minimum allowed y-coordinate for the crop top. Used to
            ensure the crop starts below UI elements (battery bar, etc.).
            The crop frame is shifted down if needed.
    """
    ux0, uy0, ux1, uy1 = union_bounds
    ih, iw = img_shape[:2]

    uw = ux1 - ux0
    uh = uy1 - uy0

    # Add proportional padding
    pad = int(max(uw, uh) * padding_pct)
    px0 = ux0 - pad
    py0 = uy0 - pad
    px1 = ux1 + pad
    py1 = uy1 + pad

    pw = px1 - px0
    ph = py1 - py0

    # Adjust to target aspect ratio (expand the smaller dimension)
    target_aspect = target_size[0] / target_size[1]  # width / height
    current_aspect = pw / ph

    cx = (px0 + px1) / 2
    cy = (py0 + py1) / 2

    if current_aspect > target_aspect:
        # Too wide — expand height
        new_h = pw / target_aspect
        py0 = int(cy - new_h / 2)
        py1 = int(cy + new_h / 2)
    else:
        # Too tall — expand width
        new_w = ph * target_aspect
        px0 = int(cx - new_w / 2)
        px1 = int(cx + new_w / 2)

    # Ensure crop starts below UI content (battery bar, etc.) but
    # preserve enough padding above the car for states that extend
    # higher (trunk-open). Any residual UI text in the padding gets
    # cleaned by remove_sideview_ui.
    # Allow the crop to start at most halfway between py0 and uy0 —
    # this keeps at least half the padding above the car intact.
    min_padded_y0 = (py0 + uy0) // 2
    effective_min_y0 = min(min_y0, min_padded_y0)
    if py0 < effective_min_y0:
        shift = effective_min_y0 - py0
        py0 += shift
        py1 += shift

    # Clamp to image bounds
    px0 = max(0, px0)
    py0 = max(0, py0)
    px1 = min(iw, px1)
    py1 = min(ih, py1)

    return (px0, py0, px1, py1)


# ═══════════════════════════════════════════════════════════════════════════════
# Vectorized Paint-Over
# ═══════════════════════════════════════════════════════════════════════════════

def paint_over_region(img, y0, y1, x0, x1, margin=15, blur_radius=8):
    """Paint over a UI region with a vertical gradient sampled from edges.

    Vectorized: uses np.linspace instead of nested per-pixel loops.
    Modifies img in-place.
    """
    w_full, h_full = img.size
    y0 = max(0, y0)
    y1 = min(h_full, y1)
    x0 = max(0, x0)
    x1 = min(w_full, x1)
    rh = y1 - y0
    rw = x1 - x0
    if rh < 10 or rw < 10:
        return

    arr = np.array(img)

    top_strip = arr[y0:min(y0 + margin, y1), x0:x1]
    bot_strip = arr[max(y1 - margin, y0):y1, x0:x1]

    top_color = np.mean(top_strip.reshape(-1, 3), axis=0)
    bot_color = np.mean(bot_strip.reshape(-1, 3), axis=0)

    # Vectorized vertical gradient
    gradient = np.linspace(top_color, bot_color, rh)
    fill = np.broadcast_to(gradient[:, np.newaxis, :], (rh, rw, 3)).copy()
    fill = np.clip(fill, 0, 255).astype(np.uint8)

    fill_img = Image.fromarray(fill)
    img.paste(fill_img, (x0, y0))

    # Blur to blend edges
    bx0 = max(0, x0 - blur_radius)
    by0 = max(0, y0 - blur_radius)
    bx1 = min(w_full, x1 + blur_radius)
    by1 = min(h_full, y1 + blur_radius)
    blend = img.crop((bx0, by0, bx1, by1))
    blurred = blend.filter(ImageFilter.GaussianBlur(radius=blur_radius))
    img.paste(blurred, (bx0, by0))


def paint_over_gray_icons(img, y0, y1, x0, x1, threshold=70, blur_radius=3,
                          max_ch_range=30):
    """Remove gray/white icons by replacing bright gray pixels with background.

    Vectorized: uses numpy mask operations instead of per-pixel loops.
    Modifies img in-place.
    """
    w_full, h_full = img.size
    y0 = max(0, y0)
    y1 = min(h_full, y1)
    x0 = max(0, x0)
    x1 = min(w_full, x1)
    if y1 - y0 < 3 or x1 - x0 < 3:
        return

    arr = np.array(img)
    region = arr[y0:y1, x0:x1].copy()

    max_ch = np.max(region, axis=2)
    min_ch = np.min(region, axis=2)
    ch_range = max_ch - min_ch
    gray_mask = (ch_range < max_ch_range) & (max_ch > threshold)

    if not np.any(gray_mask):
        return

    # Background color from dark pixels in the region
    flat = region.reshape(-1, 3)
    dark = np.max(flat, axis=1) < 40
    if np.sum(dark) > 10:
        bg_color = np.median(flat[dark], axis=0).astype(np.uint8)
    else:
        bg_color = np.array([15, 15, 15], dtype=np.uint8)

    for c in range(3):
        region[:, :, c] = np.where(gray_mask, bg_color[c], region[:, :, c])

    region_img = Image.fromarray(region)
    img.paste(region_img, (x0, y0))

    bx0 = max(0, x0 - 2)
    by0 = max(0, y0 - 2)
    bx1 = min(w_full, x1 + 2)
    by1 = min(h_full, y1 + 2)
    blend = img.crop((bx0, by0, bx1, by1))
    blurred = blend.filter(ImageFilter.GaussianBlur(radius=blur_radius))
    img.paste(blurred, (bx0, by0))


def remove_text_overlay(img, y0, y1, x0, x1, min_ch_threshold=80, blur_radius=2):
    """Remove semi-transparent white text overlaid on a colored surface.

    Detects text by elevated minimum channel (white overlay raises all RGB
    channels), then replaces each text pixel with the average color of
    non-text pixels in its row — giving a clean, natural-looking result
    without rectangular gradient artifacts.
    """
    w_full, h_full = img.size
    y0, y1 = max(0, y0), min(h_full, y1)
    x0, x1 = max(0, x0), min(w_full, x1)
    if y1 - y0 < 5 or x1 - x0 < 5:
        return

    arr = np.array(img)
    region = arr[y0:y1, x0:x1].copy()
    min_ch = np.min(region, axis=2)
    text_mask = min_ch > min_ch_threshold

    if not np.any(text_mask):
        return

    rh, rw = region.shape[:2]

    # Replace text pixels with per-row average of non-text pixels
    for row_idx in range(rh):
        row_mask = text_mask[row_idx]
        if not np.any(row_mask):
            continue
        non_text = region[row_idx][~row_mask]
        if len(non_text) > 10:
            row_avg = np.mean(non_text, axis=0).astype(np.uint8)
        else:
            row_avg = np.array([0, 0, 0], dtype=np.uint8)
        region[row_idx][row_mask] = row_avg

    region_img = Image.fromarray(region)
    img.paste(region_img, (x0, y0))

    # Light blur to blend replaced pixel edges
    bx0 = max(0, x0 - blur_radius)
    by0 = max(0, y0 - blur_radius)
    bx1 = min(w_full, x1 + blur_radius)
    by1 = min(h_full, y1 + blur_radius)
    blend = img.crop((bx0, by0, bx1, by1))
    blurred = blend.filter(ImageFilter.GaussianBlur(radius=blur_radius))
    img.paste(blurred, (bx0, by0))


def apply_ui_zones(img, zones, region_y0, region_h, region_x0, region_w):
    """Apply proportional UI zone cleanup to an image.

    Each zone is (y_pct_start, y_pct_end, x_pct_start, x_pct_end, method, kwargs).
    Coordinates are fractions of the region dimensions.
    """
    for y_start, y_end, x_start, x_end, method, kwargs in zones:
        abs_y0 = region_y0 + int(region_h * y_start)
        abs_y1 = region_y0 + int(region_h * min(y_end, 1.0))
        abs_x0 = region_x0 + int(region_w * x_start)
        abs_x1 = region_x0 + int(region_w * x_end)

        if method == "region":
            paint_over_region(img, abs_y0, abs_y1, abs_x0, abs_x1, **kwargs)
        elif method == "gray_icons":
            paint_over_gray_icons(img, abs_y0, abs_y1, abs_x0, abs_x1, **kwargs)
        elif method == "text_overlay":
            remove_text_overlay(img, abs_y0, abs_y1, abs_x0, abs_x1, **kwargs)


def remove_sideview_ui(cropped_img, bg_color):
    """Remove Tesla UI text/icons from the top of a cropped side-view image.

    Strategy: seed a car mask from chromatic (colored) pixels, then flood-fill
    into adjacent non-bg pixels to grow the car region. Anything NOT connected
    to the car body is UI (text, tab icons, battery indicators) → replaced
    with pure bg. No blur is used, so no noise halos are created.

    This correctly handles:
    - White "Parked"/"Low Power Mode" text (bright but not connected to car)
    - Tab bar icons (achromatic gray, not connected to car)
    - Trunk-open images where car fills the scan zone (chromatic seeds present)

    Args:
        cropped_img: PIL Image (cropped to the car region, before resize).
        bg_color: numpy array of background RGB color.
    """
    w, h = cropped_img.size
    scan_h = int(h * 0.15)

    arr = np.array(cropped_img)
    bg_uint8 = np.clip(bg_color, 0, 255).astype(np.uint8)

    # Work on extended region: scan zone + buffer below so car can enter from bottom
    ext_h = min(scan_h + 50, h)
    region = arr[:ext_h, :, :3].copy()

    bg_dist = np.sqrt(np.sum(
        (region.astype(np.float64) - bg_uint8.astype(np.float64)) ** 2,
        axis=2))
    ch_range = (np.max(region, axis=2).astype(int) -
                np.min(region, axis=2).astype(int))

    # Growable region: any non-bg pixel
    growable = (bg_dist > 15).astype(np.uint8) * 255

    # Seed from TWO sources:
    # 1. Bottom of extended region — car body enters from below
    # 2. Strongly chromatic pixels anywhere — UI text is always achromatic
    #    (white/gray), so high-saturation pixels (ch_range > 30) are safely
    #    car body. This preserves trunk-open where the lid extends upward
    #    and is disconnected from the main body by a thin hinge gap.
    seed_zone = ext_h - 10
    car_seed = np.zeros_like(growable)
    bottom_dist = bg_dist[seed_zone:ext_h, :]
    car_seed[seed_zone:ext_h, :] = (bottom_dist > 30).astype(np.uint8) * 255
    # Chromatic seed: colored pixels (car paint, trunk lid, etc.)
    chromatic_seed = ((ch_range > 30) & (bg_dist > 15)).astype(np.uint8) * 255
    car_seed = np.maximum(car_seed, chromatic_seed)

    # Flood-fill upward from bottom seeds into growable region
    # Use morphological reconstruction: iteratively dilate within growable bounds
    car_mask = car_seed.copy()
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    for _ in range(40):  # enough iterations to fill tall scan zones
        grown = cv2.dilate(car_mask, k, iterations=1)
        grown = cv2.bitwise_and(grown, growable)
        if np.array_equal(grown, car_mask):
            break
        car_mask = grown

    # Dilate car mask slightly to include antialiased edges
    k_edge = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    car_mask = cv2.dilate(car_mask, k_edge, iterations=1)

    # Only apply cleanup to the scan zone (not the extended region)
    scan_mask = car_mask[:scan_h, :]
    scan_region = arr[:scan_h, :, :3].copy()

    not_car = scan_mask == 0
    for c in range(3):
        scan_region[:, :, c] = np.where(not_car, bg_uint8[c], scan_region[:, :, c])

    scan_img = Image.fromarray(scan_region)
    cropped_img.paste(scan_img, (0, 0))


# ═══════════════════════════════════════════════════════════════════════════════
# Panel Car Detection
# ═══════════════════════════════════════════════════════════════════════════════

def find_car_bounds_panel(non_bg_mask):
    """Find the car/interior bounding box in a panel (top-down) screenshot."""
    h, w = non_bg_mask.shape
    margin_y = int(h * 0.03)
    margin_x = int(w * 0.05)
    analysis = non_bg_mask[margin_y:h - margin_y, margin_x:w - margin_x]

    row_coverage = np.mean(analysis, axis=1)
    has_car = row_coverage > 0.05
    gap_bridge = max(2, int(h * 0.02))
    runs = find_contiguous_runs(has_car, gap_bridge=gap_bridge)

    if not runs:
        return None

    best = max(runs, key=lambda r: r[1] - r[0])
    car_y0 = margin_y + best[0]
    car_y1 = margin_y + best[1]

    car_band = non_bg_mask[car_y0:car_y1, :]
    col_coverage = np.mean(car_band, axis=0)
    car_cols = np.where(col_coverage > 0.01)[0]

    if len(car_cols) == 0:
        return None

    return (int(car_cols[0]), car_y0, int(car_cols[-1]), car_y1)


# ═══════════════════════════════════════════════════════════════════════════════
# OpenCV Panel Inpainting
# ═══════════════════════════════════════════════════════════════════════════════

def get_car_mask_filled(img_bgr, bg_color, threshold=12):
    """Get a filled car mask using flood fill to close holes in glass areas.

    Args:
        img_bgr: BGR numpy array (OpenCV format).
        bg_color: RGB background color as numpy array.
        threshold: Euclidean distance threshold for background detection.

    Returns:
        Binary mask (uint8, 0 or 255) where 255 = car region.
    """
    h, w = img_bgr.shape[:2]
    # Convert bg_color from RGB to BGR for comparison
    bg_bgr = bg_color[::-1].astype(np.float64)
    diff = np.sqrt(np.sum((img_bgr.astype(np.float64) - bg_bgr) ** 2, axis=2))
    non_bg = (diff > threshold).astype(np.uint8)

    # Find largest connected component (the car)
    n_labels, labels, stats, _ = cv2.connectedComponentsWithStats(non_bg, 8)
    if n_labels < 2:
        return np.zeros((h, w), dtype=np.uint8)
    car_label = np.argmax(stats[1:, cv2.CC_STAT_AREA]) + 1
    car_mask = (labels == car_label).astype(np.uint8) * 255

    # Fill holes (glass, interior) using flood fill from edges
    filled = car_mask.copy()
    flood_mask = np.zeros((h + 2, w + 2), np.uint8)
    cv2.floodFill(filled, flood_mask, (0, 0), 255)
    # Invert: what flood fill couldn't reach = holes inside the car
    car_mask = cv2.bitwise_or(car_mask, cv2.bitwise_not(filled))

    # Close small gaps in the mask
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15))
    car_mask = cv2.morphologyEx(car_mask, cv2.MORPH_CLOSE, kernel)

    return car_mask


def inpaint_controls_ui(car_img, car_mask, car_w, car_h):
    """Detect and inpaint UI overlays on a controls panel image.

    Targeted approach that preserves interior detail visible through glass:
    - Very bright text on dark surfaces (gray > 100): catches "Open" text
    - White overlay on body panels (min_ch > 140): catches hood "Open"
    - Small achromatic icons on glass (padlock): isolated gray blobs

    Preserves headlights, chrome trim, and interior reflections/detail.
    """
    gray = cv2.cvtColor(car_img, cv2.COLOR_BGR2GRAY)
    b_ch, g_ch, r_ch = cv2.split(car_img)
    min_ch = np.minimum(np.minimum(r_ch, g_ch), b_ch)
    max_ch = np.maximum(np.maximum(r_ch, g_ch), b_ch)
    ch_range = max_ch.astype(int) - min_ch.astype(int)
    local_mean = cv2.blur(gray.astype(np.float64), (121, 121))

    # Layer 1: Very bright achromatic elements on dark surfaces (catches "Open" text)
    # Threshold 100 preserves interior detail (gray 30-80) while catching text (gray 110+)
    # ch_range < 60 excludes colored elements like the green charging cable
    dark_zone = local_mean < 55
    bright_text = ((gray.astype(np.float64) > 100) & dark_zone & (ch_range < 60)).astype(np.uint8) * 255

    # Layer 2: White overlay on body panels (catches "Open" on hood)
    white_on_body = ((min_ch > 140) & (local_mean >= 55)).astype(np.uint8) * 255

    # Layer 3: Small achromatic icons on dark surfaces (padlock, small UI elements)
    # These are gray blobs (ch_range < 10, gray 55-100) in the glass area
    achromatic_on_dark = (
        (gray > 55) & (gray < 100) &
        (ch_range < 10) &
        (dark_zone)
    ).astype(np.uint8) * 255

    # Process each layer separately to avoid cross-contamination during CC filtering.
    # Bright text and white-on-body are inpainted unconditionally (only headlight protection).
    # Achromatic layer needs strict size filtering to avoid destroying interior detail.

    # --- Pre-filter white_on_body: skip body-sized CCs ---
    # On white/silver cars, min_ch > 140 matches the entire hood.
    # Real text overlays are small; the hood is huge. Filter by CC size.
    k_close_wb = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
    wb_closed = cv2.morphologyEx(white_on_body, cv2.MORPH_CLOSE, k_close_wb)
    wb_closed = cv2.bitwise_and(wb_closed, car_mask)
    max_text_area = car_w * car_h * 0.02
    n_wb, wb_labels, wb_stats, _ = cv2.connectedComponentsWithStats(wb_closed, 8)
    wb_filtered = np.zeros_like(wb_closed)
    for i in range(1, n_wb):
        if wb_stats[i, cv2.CC_STAT_AREA] > max_text_area:
            continue  # body panel, not text
        wb_filtered[wb_labels == i] = 255

    # --- Text layers (bright_text + filtered white_on_body) ---
    text_raw = cv2.bitwise_or(bright_text, wb_filtered)
    k_dilate_text = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (11, 11))
    text_mask = cv2.morphologyEx(text_raw, cv2.MORPH_CLOSE, k_close_wb)
    text_mask = cv2.dilate(text_mask, k_dilate_text, iterations=1)
    text_mask = cv2.bitwise_and(text_mask, car_mask)

    # Filter text CCs: only skip headlights
    n_cc, cc_labels, cc_stats, cc_cents = cv2.connectedComponentsWithStats(text_mask, 8)
    size_thresh = car_w * car_h * 0.003
    text_final = np.zeros_like(text_mask)
    for i in range(1, n_cc):
        area = cc_stats[i, cv2.CC_STAT_AREA]
        if area < 15:
            continue
        cx_rel = cc_cents[i][0] / car_w
        cy_rel = cc_cents[i][1] / car_h
        cw = cc_stats[i, cv2.CC_STAT_WIDTH]
        ch = cc_stats[i, cv2.CC_STAT_HEIGHT]
        aspect = max(cw, ch) / (min(cw, ch) + 1)
        # Skip headlights
        if area > size_thresh and cy_rel < 0.20 and (cx_rel < 0.35 or cx_rel > 0.65):
            continue
        # Skip side chrome/trim
        if area > size_thresh * 2 and (cx_rel < 0.10 or cx_rel > 0.90) and aspect > 4:
            continue
        text_final[cc_labels == i] = 255

    # --- Achromatic icon layer ---
    k_close_sm = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    k_dilate_sm = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
    icon_mask = cv2.morphologyEx(achromatic_on_dark, cv2.MORPH_CLOSE, k_close_sm)
    icon_mask = cv2.dilate(icon_mask, k_dilate_sm, iterations=1)
    icon_mask = cv2.bitwise_and(icon_mask, car_mask)

    # Filter icon CCs: skip large blobs (interior features) and headlights
    n_cc2, cc_labels2, cc_stats2, cc_cents2 = cv2.connectedComponentsWithStats(icon_mask, 8)
    icon_final = np.zeros_like(icon_mask)
    for i in range(1, n_cc2):
        area = cc_stats2[i, cv2.CC_STAT_AREA]
        if area < 15:
            continue
        # Skip large achromatic blobs — these are interior features, not icons
        if area > 5000:
            continue
        cx_rel = cc_cents2[i][0] / car_w
        cy_rel = cc_cents2[i][1] / car_h
        # Skip headlight zones
        if area > size_thresh and cy_rel < 0.20 and (cx_rel < 0.35 or cx_rel > 0.65):
            continue
        icon_final[cc_labels2 == i] = 255

    # Merge both masks
    final_mask = cv2.bitwise_or(text_final, icon_final)

    return cv2.inpaint(car_img, final_mask, inpaintRadius=12, flags=cv2.INPAINT_TELEA)


# ═══════════════════════════════════════════════════════════════════════════════
# Side-View Alignment
# ═══════════════════════════════════════════════════════════════════════════════

def _align_to_base(state_img, base_img, verbose=False):
    """Align a cropped side-view image to the base using phase correlation.

    The Tesla app may render the car at slightly different pixel positions
    between screenshots, especially across different phone resolutions.
    Even a 1–2 px shift causes the overlay diff to pick up the entire car
    body edge as changed pixels.

    Only the bottom 60% of the image (wheels, rocker panel, lower body) is
    used for correlation since the top portion changes between states
    (frunk, doors, trunk).

    Args:
        state_img: PIL Image (cropped side view to align).
        base_img:  PIL Image (base / all-closed reference).
        verbose:   Print shift diagnostics.

    Returns:
        Aligned PIL Image (same mode as input), or the original unchanged
        if alignment fails or the shift is negligible (< 0.3 px).
    """
    state_arr = np.array(state_img.convert("RGB"))
    base_arr = np.array(base_img.convert("RGB"))
    h, w = state_arr.shape[:2]

    if state_arr.shape != base_arr.shape:
        if verbose:
            print("    Alignment: size mismatch — skipped")
        return state_img

    # Use bottom 60% — wheels, rocker panel, lower body are stable across states
    y_start = int(h * 0.4)
    state_gray = cv2.cvtColor(state_arr[y_start:], cv2.COLOR_RGB2GRAY).astype(np.float64)
    base_gray = cv2.cvtColor(base_arr[y_start:], cv2.COLOR_RGB2GRAY).astype(np.float64)

    # Hann window reduces edge artefacts in the frequency domain
    hann = cv2.createHanningWindow(
        (state_gray.shape[1], state_gray.shape[0]), cv2.CV_64F)

    try:
        (dx, dy), response = cv2.phaseCorrelate(base_gray, state_gray, hann)
    except cv2.error:
        if verbose:
            print("    Alignment: phase correlation failed — skipped")
        return state_img

    # Cap shift to 5% of image dimension — larger = detection error
    max_dx = w * 0.05
    max_dy = h * 0.05
    if abs(dx) > max_dx or abs(dy) > max_dy:
        if verbose:
            print(f"    Alignment: shift ({dx:.2f}, {dy:.2f}) exceeds cap — skipped")
        return state_img

    # Skip negligible shifts
    if abs(dx) < 0.3 and abs(dy) < 0.3:
        if verbose:
            print(f"    Alignment: shift ({dx:.2f}, {dy:.2f}) negligible")
        return state_img

    if verbose:
        print(f"    Alignment: dx={dx:.2f}, dy={dy:.2f} (response={response:.4f})")

    # Build affine translation matrix and warp
    M = np.float32([[1, 0, -dx], [0, 1, -dy]])
    src_arr = np.array(state_img)

    if src_arr.ndim == 3 and src_arr.shape[2] == 4:
        # RGBA — warp each channel to preserve alpha correctly
        aligned = np.stack([
            cv2.warpAffine(src_arr[:, :, c], M, (w, h),
                           borderMode=cv2.BORDER_REPLICATE)
            for c in range(4)
        ], axis=2)
    else:
        aligned = cv2.warpAffine(src_arr, M, (w, h),
                                 borderMode=cv2.BORDER_REPLICATE)

    return Image.fromarray(aligned)


# ═══════════════════════════════════════════════════════════════════════════════
# Side-View Processing
# ═══════════════════════════════════════════════════════════════════════════════

def process_sideview(img_path, crop_frame, target_size=SIDE_VIEW_SIZE,
                     verbose=False, full_res=False):
    """Process a single side-view screenshot using a pre-computed crop frame.

    Returns:
        (result_image, info_dict)
    """
    img = Image.open(str(img_path))
    arr = np.array(img)
    bg_color = detect_background_color(arr)
    non_bg = create_non_bg_mask(arr, bg_color)

    info = {
        "input_file": str(img_path),
        "input_size": list(img.size),
        "bg_color": [int(c) for c in bg_color],
        "car_bounds": None,
        "crop_frame": list(crop_frame),
        "confidence": None,
        "warnings": [],
        "success": False,
    }

    # Detect car bounds for the report (even though we use the shared frame)
    bounds, confidence = find_car_bounds_sideview(non_bg)
    info["confidence"] = confidence
    if bounds:
        info["car_bounds"] = list(bounds)

    if verbose:
        if bounds:
            bw = bounds[2] - bounds[0]
            bh = bounds[3] - bounds[1]
            print(f"    Car bounds: ({bounds[0]},{bounds[1]})-({bounds[2]},{bounds[3]}) "
                  f"= {bw}x{bh}  confidence={confidence:.3f}")

    # Crop, remove UI text, and resize using the shared frame
    cx0, cy0, cx1, cy1 = crop_frame
    cropped = img.crop((cx0, cy0, cx1, cy1))
    remove_sideview_ui(cropped, bg_color)
    if full_res:
        result = cropped
    else:
        result = cropped.resize(target_size, Image.Resampling.LANCZOS)

    if verbose:
        print(f"    Crop frame: ({cx0},{cy0})-({cx1},{cy1}) = {cx1-cx0}x{cy1-cy0}")
        out_size = result.size
        print(f"    Output: {out_size[0]}x{out_size[1]}" +
              (" (full-res)" if full_res else ""))

    info["success"] = True
    return result, info


# ═══════════════════════════════════════════════════════════════════════════════
# Panel Processing
# ═══════════════════════════════════════════════════════════════════════════════

def process_controls_panel(img_path, target_size=CONTROLS_SIZE, verbose=False,
                           full_res=False):
    """Process the controls panel screenshot using OpenCV inpainting.

    Detects the car rendering, inpaints UI overlays (Open text, padlock,
    icons) while preserving headlights and badge, then crops and resizes.
    """
    img = Image.open(str(img_path))
    arr = np.array(img)
    iw, ih = img.size
    bg_color = detect_background_color(arr)
    non_bg = create_non_bg_mask(arr, bg_color)

    info = {
        "input_file": str(img_path),
        "input_size": [iw, ih],
        "car_bounds": None,
        "warnings": [],
        "success": False,
    }

    car_bounds = find_car_bounds_panel(non_bg)
    if car_bounds is None:
        info["warnings"].append("Controls car detection failed")
        return None, info

    cx0, cy0, cx1, cy1 = car_bounds
    info["car_bounds"] = list(car_bounds)

    if verbose:
        print(f"    Car bounds: ({cx0},{cy0})-({cx1},{cy1}) "
              f"= {cx1-cx0}x{cy1-cy0}")

    # Convert to BGR for OpenCV processing
    img_bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)

    # Get filled car mask (fills holes in glass areas)
    bg_uint8 = np.clip(bg_color, 0, 255).astype(np.uint8)
    car_mask = get_car_mask_filled(img_bgr, bg_uint8, threshold=12)

    # Inpaint UI overlays (targeted: preserves interior glass detail)
    car_w = cx1 - cx0
    car_h = cy1 - cy0
    inpainted = inpaint_controls_ui(img_bgr, car_mask, car_w, car_h)

    # Crop to car region with padding
    padding_top = max(0, int(car_h * 0.02))
    padding_bot = max(0, int(car_h * 0.02))
    crop_y0 = max(0, cy0 - padding_top)
    crop_y1 = min(ih, cy1 + padding_bot)
    cropped_bgr = inpainted[crop_y0:crop_y1, :, :]

    # Clean up non-car bright elements in the crop area (lightning bolt, nav icons)
    crop_mask = car_mask[crop_y0:crop_y1, :]
    gray_crop = cv2.cvtColor(cropped_bgr, cv2.COLOR_BGR2GRAY)
    bright_non_car = (gray_crop > 40) & (crop_mask == 0)
    for c in range(3):
        cropped_bgr[:, :, c] = np.where(bright_non_car,
                                         int(bg_uint8[2 - c]),  # BGR order
                                         cropped_bgr[:, :, c])

    # Convert back to RGB PIL Image
    cropped_rgb = cv2.cvtColor(cropped_bgr, cv2.COLOR_BGR2RGB)
    cropped_pil = Image.fromarray(cropped_rgb)
    if full_res:
        result = cropped_pil
    else:
        result = cropped_pil.resize(target_size, Image.Resampling.LANCZOS)

    if verbose:
        out_size = result.size
        print(f"    Inpainted UI overlays (OpenCV)")
        print(f"    Output: {out_size[0]}x{out_size[1]}" +
              (" (full-res)" if full_res else ""))

    info["success"] = True
    return result, info


def inpaint_climate_ui(img_bgr, car_mask, cx0, cy0, cx1, cy1):
    """Detect and inpaint UI overlays on a climate panel image.

    Targeted approach that preserves ALL interior detail:
    - Seat heater SSS wave icons and "Auto" text on seats
    - Status bar elements (time, battery, signal) near top of car
    - Back button chevron in top-left

    Preserves: rearview mirror, overhead console, seatbelt buckles,
    steering wheel, seats, center console, wood dashboard trim.
    """
    h, w = img_bgr.shape[:2]
    car_h = cy1 - cy0
    car_w = cx1 - cx0

    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    b_ch, g_ch, r_ch = cv2.split(img_bgr)
    min_ch = np.minimum(np.minimum(r_ch, g_ch), b_ch)
    max_ch = np.maximum(np.maximum(r_ch, g_ch), b_ch)
    ch_range = max_ch.astype(int) - min_ch.astype(int)

    # Geometry-based deep interior detection (color-independent).
    # Body panels are always the outer ring of the car mask. Interior (seats,
    # console, steering wheel) is deep inside. Distance transform measures
    # minimum distance from each pixel to the nearest mask edge.
    dist_from_edge = cv2.distanceTransform(
        (car_mask // 255).astype(np.uint8), cv2.DIST_L2, 5
    )
    depth_threshold = max(20, int(min(car_w, car_h) * 0.04))
    deep_interior = ((dist_from_edge > depth_threshold) & (car_mask > 0)).astype(np.uint8) * 255

    # --- Layer A: Seat heater icons in interior ---
    # SSS waves: gray 80-160, achromatic (ch_range < 20)
    # Threshold 80 preserves rearview mirror (~55-70) and overhead console (~40-60)
    bright_interior = (
        (gray > 80) & (ch_range < 20) & (deep_interior > 0)
    ).astype(np.uint8) * 255

    # White text ("Auto", etc.): min_ch > 100 in deep interior
    white_interior = (
        (min_ch > 100) & (deep_interior > 0)
    ).astype(np.uint8) * 255

    seat_ui = cv2.bitwise_or(bright_interior, white_interior)

    # Morphological cleanup for seat heater layer
    k_close = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    k_dilate = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
    seat_mask = cv2.morphologyEx(seat_ui, cv2.MORPH_CLOSE, k_close)
    seat_mask = cv2.dilate(seat_mask, k_dilate, iterations=1)
    seat_mask = cv2.bitwise_and(seat_mask, car_mask)

    # CC filter seat heater detections
    n_cc, labels, stats, cents = cv2.connectedComponentsWithStats(seat_mask, 8)
    seat_final = np.zeros_like(seat_mask)
    for i in range(1, n_cc):
        area = stats[i, cv2.CC_STAT_AREA]
        if area < 50:
            continue  # noise
        if area > 15000:
            continue  # glass frame edge (too large)
        cw = stats[i, cv2.CC_STAT_WIDTH]
        ch = stats[i, cv2.CC_STAT_HEIGHT]
        aspect = max(cw, ch) / (min(cw, ch) + 1)
        if aspect > 8:
            continue  # long edge reflection
        cy_rel = (cents[i][1] - cy0) / car_h
        if cy_rel < 0.25:
            continue  # preserve rearview mirror / overhead console
        if cy_rel > 0.75:
            continue  # avoid rear bumper area
        seat_final[labels == i] = 255

    # --- Layer B: Status bar + back button in top zone ---
    # These are bright achromatic elements above or just inside the car top
    top_zone_end = cy0 + int(car_h * 0.10)
    top_zone_mask = np.zeros_like(gray, dtype=np.uint8)
    top_zone_mask[max(0, cy0 - 30):top_zone_end, :] = 255

    status_ui = (
        (gray > 100) & (ch_range < 15) & (top_zone_mask > 0)
    ).astype(np.uint8) * 255

    # Also catch the back button chevron specifically (bright in top-left)
    btn_zone = np.zeros_like(gray, dtype=np.uint8)
    btn_zone[max(0, cy0 - 20):cy0 + int(car_h * 0.05), :int(w * 0.15)] = 255
    back_btn = (
        (gray > 120) & (ch_range < 15) & (btn_zone > 0)
    ).astype(np.uint8) * 255
    status_ui = cv2.bitwise_or(status_ui, back_btn)

    # Dilate status UI for clean inpainting
    status_mask = cv2.dilate(
        status_ui, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
    )

    # --- Combine all layers and inpaint ---
    final_mask = cv2.bitwise_or(seat_final, status_mask)
    return cv2.inpaint(img_bgr, final_mask, inpaintRadius=12, flags=cv2.INPAINT_TELEA)


def process_climate_panel(img_path, target_size=CLIMATE_SIZE, verbose=False,
                          full_res=False):
    """Process the climate panel screenshot using OpenCV inpainting.

    Uses targeted inpainting to remove seat heater icons and status bar
    while preserving all interior detail (seats, steering wheel, rearview
    mirror, seatbelt buckles, overhead console, wood dashboard).
    """
    img = Image.open(str(img_path))
    arr = np.array(img)
    iw, ih = img.size
    bg_color = detect_background_color(arr)
    non_bg = create_non_bg_mask(arr, bg_color)

    info = {
        "input_file": str(img_path),
        "input_size": [iw, ih],
        "car_bounds": None,
        "warnings": [],
        "success": False,
    }

    car_bounds = find_car_bounds_panel(non_bg)
    if car_bounds is None:
        info["warnings"].append("Climate car detection failed")
        return None, info

    cx0, cy0, cx1, cy1 = car_bounds
    info["car_bounds"] = list(car_bounds)
    car_h = cy1 - cy0
    car_w = cx1 - cx0

    if verbose:
        print(f"    Car bounds: ({cx0},{cy0})-({cx1},{cy1}) "
              f"= {car_w}x{car_h}")

    bg_uint8 = np.clip(bg_color, 0, 255).astype(np.uint8)

    # Convert to BGR for OpenCV processing
    img_bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)

    # Get filled car mask (fills holes in glass/interior areas)
    car_mask = get_car_mask_filled(img_bgr, bg_uint8, threshold=12)

    # Inpaint UI overlays (seat heater icons, status bar, back button)
    inpainted = inpaint_climate_ui(img_bgr, car_mask, cx0, cy0, cx1, cy1)

    # Crop: start 3% into car height to cleanly remove back button area.
    # This only loses the very tip of the front bumper — all interior detail
    # (windshield, dashboard, rearview mirror) is well below this.
    crop_start_y = cy0 + int(car_h * 0.03)
    car_bottom_pad = int(car_h * 0.02)
    crop_end_y = min(ih, cy1 + car_bottom_pad)

    cropped_bgr = inpainted[crop_start_y:crop_end_y, :, :]
    crop_h = crop_end_y - crop_start_y

    bg_bgr = bg_uint8[::-1]  # RGB to BGR

    # Clean non-car elements outside car mask using Euclidean distance from bg.
    # The back button rounded rect is RGB(34,35,36) — only ~20 from bg(22,23,24)
    # but clearly not bg. Using distance threshold 10 catches it reliably.
    crop_car_mask = car_mask[crop_start_y:crop_end_y, :]
    bg_float = bg_bgr.reshape(1, 1, 3).astype(np.float64)
    outside_diff = np.sqrt(
        np.sum((cropped_bgr.astype(np.float64) - bg_float) ** 2, axis=2)
    )
    replace_outside = (outside_diff > 10) & (crop_car_mask == 0)
    for c in range(3):
        cropped_bgr[:, :, c] = np.where(
            replace_outside, int(bg_bgr[c]), cropped_bgr[:, :, c]
        )

    # Build canvas with bg padding at bottom for correct aspect ratio
    target_aspect = target_size[0] / target_size[1]
    target_car_fill = 0.82
    canvas_h = int(crop_h / target_car_fill)
    canvas_w = int(canvas_h * target_aspect)

    # Center horizontally on the car
    car_cx = (cx0 + cx1) / 2
    canvas_x0 = int(car_cx - canvas_w / 2)
    canvas_x1 = canvas_x0 + canvas_w
    if canvas_x0 < 0:
        canvas_x0 = 0
        canvas_x1 = min(iw, canvas_w)
    if canvas_x1 > iw:
        canvas_x1 = iw
        canvas_x0 = max(0, iw - canvas_w)

    # Build final canvas
    actual_w = canvas_x1 - canvas_x0
    canvas = np.full((canvas_h, actual_w, 3), bg_bgr, dtype=np.uint8)
    paste_h = min(crop_h, canvas_h)
    region = cropped_bgr[:paste_h, canvas_x0:canvas_x1, :]
    canvas[:paste_h, :region.shape[1], :] = region

    # Convert back to RGB PIL Image
    canvas_rgb = cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB)
    result = Image.fromarray(canvas_rgb)
    if not full_res:
        result = result.resize(target_size, Image.Resampling.LANCZOS)

    if verbose:
        out_size = result.size
        print(f"    Inpainted UI overlays (OpenCV)")
        print(f"    Output: {out_size[0]}x{out_size[1]}" +
              (" (full-res)" if full_res else ""))

    info["ui_zones_applied"] = 1
    info["success"] = True
    return result, info


# ═══════════════════════════════════════════════════════════════════════════════
# Preview Generation
# ═══════════════════════════════════════════════════════════════════════════════

def create_comparison_preview(processed_img, ref_img_path, output_path, label):
    """Create side-by-side comparison: reference (left) vs processed (right)."""
    ref = Image.open(str(ref_img_path))

    display_w = 500
    ref_ratio = ref.size[0] / ref.size[1]
    display_h = int(display_w / ref_ratio)

    ref_display = ref.resize((display_w, display_h), Image.Resampling.LANCZOS)
    proc_display = processed_img.resize((display_w, display_h), Image.Resampling.LANCZOS)

    gap = 20
    canvas_w = display_w * 2 + gap
    canvas_h = display_h + 60

    canvas = Image.new("RGB", (canvas_w, canvas_h), (13, 13, 13))
    canvas.paste(ref_display, (0, 40))
    canvas.paste(proc_display, (display_w + gap, 40))

    draw = ImageDraw.Draw(canvas)
    draw.text((display_w // 2, 10), "Reference", fill=(180, 180, 180), anchor="mt")
    draw.text((display_w + gap + display_w // 2, 10), "Processed",
              fill=(180, 180, 180), anchor="mt")
    draw.text((canvas_w // 2, canvas_h - 10), label, fill=(232, 33, 39), anchor="mb")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(str(output_path))


def create_overview_grid(images, output_path):
    """Create an overview grid showing all processed output images."""
    if not images:
        return

    side_names = [n for n in images if n not in ("controls-bg", "climate-bg")]
    panel_names = [n for n in images if n in ("controls-bg", "climate-bg")]

    cell_w = 250
    padding = 10
    label_h = 25

    cols = max(len(side_names), 1)
    side_cell_h = int(cell_w * SIDE_VIEW_SIZE[1] / SIDE_VIEW_SIZE[0])
    panel_cell_h = int(cell_w * 1.5) if panel_names else 0

    total_w = cols * (cell_w + padding) + padding
    total_h = (padding + label_h + side_cell_h + padding +
               (label_h + panel_cell_h + padding if panel_names else 0))

    canvas = Image.new("RGB", (total_w, total_h), (13, 13, 13))
    draw = ImageDraw.Draw(canvas)

    for i, name in enumerate(sorted(side_names)):
        x = padding + i * (cell_w + padding)
        y = padding + label_h
        thumb = images[name].resize((cell_w, side_cell_h), Image.Resampling.LANCZOS)
        canvas.paste(thumb, (x, y))
        draw.text((x + cell_w // 2, y - 5), name, fill=(180, 180, 180), anchor="mb")

    if panel_names:
        panel_y_base = padding + label_h + side_cell_h + padding + label_h
        for i, name in enumerate(sorted(panel_names)):
            x = padding + i * (cell_w + padding)
            ratio = images[name].size[0] / images[name].size[1]
            thumb_h = min(panel_cell_h, int(cell_w / ratio))
            thumb_w = int(thumb_h * ratio)
            thumb = images[name].resize((thumb_w, thumb_h), Image.Resampling.LANCZOS)
            canvas.paste(thumb, (x, panel_y_base))
            draw.text((x + thumb_w // 2, panel_y_base - 5), name,
                      fill=(180, 180, 180), anchor="mb")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(str(output_path))


def create_debug_visualization(img_path, car_bounds, crop_frame, output_path):
    """Draw car bounds (green) and crop frame (red) on the original screenshot."""
    img = Image.open(str(img_path)).copy()
    draw = ImageDraw.Draw(img)

    if car_bounds:
        x0, y0, x1, y1 = car_bounds
        for offset in range(3):
            draw.rectangle([x0 - offset, y0 - offset, x1 + offset, y1 + offset],
                           outline=(0, 255, 0))

    if crop_frame:
        cx0, cy0, cx1, cy1 = crop_frame
        for offset in range(3):
            draw.rectangle([cx0 - offset, cy0 - offset, cx1 + offset, cy1 + offset],
                           outline=(232, 33, 39))

    max_h = 1200
    if img.size[1] > max_h:
        ratio = max_h / img.size[1]
        img = img.resize((int(img.size[0] * ratio), max_h), Image.Resampling.LANCZOS)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(output_path))


# ═══════════════════════════════════════════════════════════════════════════════
# Auto-Detection & Manifest
# ═══════════════════════════════════════════════════════════════════════════════

def auto_detect_mapping(input_dir, mode="offcharge"):
    """Auto-detect input file -> output name mapping from filenames.

    Mode determines which source files map to near-side vs far-side:
      offcharge (front 3/4 from right): cp_df/cp_dr = near, pf/pr = far
      oncharge  (rear 3/4 from left):   pf/pr = near, cp_df/cp_dr = far
    """
    input_dir = Path(input_dir)
    files_by_stem = {}
    for f in input_dir.iterdir():
        if f.suffix.lower() == ".png" and f.is_file():
            files_by_stem[f.stem.lower()] = f.name

    common_patterns = (SIDE_VIEW_PATTERNS_COMMON_ONCHARGE if mode == "oncharge"
                       else SIDE_VIEW_PATTERNS_COMMON)
    door_patterns = (SIDE_VIEW_PATTERNS_ONCHARGE if mode == "oncharge"
                     else SIDE_VIEW_PATTERNS_OFFCHARGE)
    panel_patterns = (PANEL_PATTERNS_ONCHARGE if mode == "oncharge"
                      else PANEL_PATTERNS)
    combined_patterns = (COMBINED_PATTERNS_ONCHARGE if mode == "oncharge"
                         else COMBINED_PATTERNS_OFFCHARGE)
    combined_door_patterns = (COMBINED_DOOR_PATTERNS_ONCHARGE if mode == "oncharge"
                              else COMBINED_DOOR_PATTERNS_OFFCHARGE)

    mapping = {"side_views": {}, "panels": {}, "combined": {},
               "combined_doors": {}}
    for output_name, patterns in {**common_patterns, **door_patterns}.items():
        for pattern in patterns:
            if pattern.lower() in files_by_stem:
                mapping["side_views"][output_name] = files_by_stem[pattern.lower()]
                break

    for output_name, patterns in panel_patterns.items():
        for pattern in patterns:
            if pattern.lower() in files_by_stem:
                mapping["panels"][output_name] = files_by_stem[pattern.lower()]
                break

    for output_name, (stems, _constituents) in combined_patterns.items():
        for stem in stems:
            if stem.lower() in files_by_stem:
                mapping["combined"][output_name] = files_by_stem[stem.lower()]
                break

    # Combined-door inputs (both front doors open / both rear doors open)
    for output_name, (stems, _near, _far) in combined_door_patterns.items():
        for stem in stems:
            if stem.lower() in files_by_stem:
                mapping["combined_doors"][output_name] = files_by_stem[stem.lower()]
                break

    # All-doors input (all 4 doors open — split into near/far combined)
    all_doors_stems = (ALL_DOORS_PATTERN_ONCHARGE if mode == "oncharge"
                       else ALL_DOORS_PATTERN_OFFCHARGE)
    for stem in all_doors_stems:
        if stem.lower() in files_by_stem:
            mapping["all_doors"] = files_by_stem[stem.lower()]
            break

    return mapping


def load_manifest(manifest_path):
    """Load a manifest JSON file that maps output names to input filenames."""
    with open(manifest_path) as f:
        data = json.load(f)
    mapping = {"side_views": {}, "panels": {}}
    if "side_views" in data:
        mapping["side_views"] = dict(data["side_views"])
    if "panels" in data:
        mapping["panels"] = dict(data["panels"])
    return mapping


# ═══════════════════════════════════════════════════════════════════════════════
# Main Processing Pipeline
# ═══════════════════════════════════════════════════════════════════════════════

def process_all(input_dir, output_dir, reference_dir=None, manifest_path=None,
                mode="offcharge", verbose=False, full_res=False):
    """Run the full processing pipeline."""
    input_dir = Path(input_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    preview_dir = output_dir / "previews"
    debug_dir = output_dir / "debug"

    report = {
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "images": [],
        "errors": [],
        "summary": {"total": 0, "succeeded": 0, "failed": 0},
    }

    # ── Resolve file mapping ──
    if manifest_path:
        mapping = load_manifest(manifest_path)
        if verbose:
            print(f"Loaded manifest: {manifest_path}")
    else:
        mapping = auto_detect_mapping(input_dir, mode=mode)
        if verbose:
            print(f"Auto-detected file mapping (mode={mode})")

    all_mappings = {**mapping.get("side_views", {}), **mapping.get("panels", {}),
                     **mapping.get("combined", {}),
                     **mapping.get("combined_doors", {})}
    if verbose:
        print(f"  Found {len(all_mappings)} files:")
        for out_name, in_name in sorted(all_mappings.items()):
            print(f"    {out_name:25s} <- {in_name}")
        print()

    if not all_mappings:
        report["errors"].append("No input files found or matched")
        return report

    # ══════════════════════════════════════════════════════════════════════════
    # Side views: multi-pass approach
    # Pass 1: detect car bounds for ALL side views
    # Pass 2: process all using a union-based crop frame
    # Pass 3: split combined-door images into individual door overlays
    # ══════════════════════════════════════════════════════════════════════════
    side_views = mapping.get("side_views", {})
    combined_views = mapping.get("combined", {})
    combined_doors = mapping.get("combined_doors", {})
    all_doors_file = mapping.get("all_doors")
    all_bounds = {}
    first_img_shape = None
    max_battery_bottom = 0

    # Include combined-state, combined-door, and all-doors images in bounds detection
    all_side_inputs = {**side_views, **combined_views, **combined_doors}
    if all_doors_file:
        all_side_inputs["all-doors"] = all_doors_file

    if all_side_inputs:
        print("Pass 1: Detecting car bounds for all side views...")
        for name, filename in all_side_inputs.items():
            src_path = input_dir / filename
            if not src_path.exists():
                continue
            img = Image.open(str(src_path))
            arr = np.array(img)
            if first_img_shape is None:
                first_img_shape = arr.shape
            bg_color = detect_background_color(arr)
            non_bg = create_non_bg_mask(arr, bg_color)
            bounds, confidence = find_car_bounds_sideview(non_bg)

            # Detect battery bar bottom to constrain crop frame
            battery_bottom = detect_battery_bottom(arr)
            if battery_bottom > max_battery_bottom:
                max_battery_bottom = battery_bottom

            if bounds:
                all_bounds[name] = bounds
                if verbose:
                    bw = bounds[2] - bounds[0]
                    bh = bounds[3] - bounds[1]
                    print(f"  {name:25s} ({bounds[0]},{bounds[1]})-({bounds[2]},{bounds[3]}) "
                          f"= {bw}x{bh}  conf={confidence:.3f}")
            else:
                print(f"  {name:25s} DETECTION FAILED")

    # Compute union of all detected bounds
    crop_frame = None
    if all_bounds and first_img_shape is not None:
        union_x0 = min(b[0] for b in all_bounds.values())
        union_y0 = min(b[1] for b in all_bounds.values())
        union_x1 = max(b[2] for b in all_bounds.values())
        union_y1 = max(b[3] for b in all_bounds.values())
        union_bounds = (union_x0, union_y0, union_x1, union_y1)

        # Constrain crop to start below battery bar + safety margin
        min_y0 = max_battery_bottom + int(first_img_shape[0] * 0.005) if max_battery_bottom > 0 else 0

        crop_frame = compute_crop_frame_from_union(
            union_bounds, first_img_shape, min_y0=min_y0)

        if verbose:
            uw = union_x1 - union_x0
            uh = union_y1 - union_y0
            print(f"\n  Union: ({union_x0},{union_y0})-({union_x1},{union_y1}) = {uw}x{uh}")
            if max_battery_bottom > 0:
                print(f"  Battery bar bottom: y={max_battery_bottom} → min crop y0={min_y0}")
            cx0, cy0, cx1, cy1 = crop_frame
            print(f"  Crop frame: ({cx0},{cy0})-({cx1},{cy1}) = {cx1-cx0}x{cy1-cy0}")
        print()

    # Pass 2: process all side views with the shared crop frame
    processed_images = {}
    if crop_frame:
        print("Pass 2: Processing side views...")
        for name, filename in side_views.items():
            src_path = input_dir / filename
            if not src_path.exists():
                report["errors"].append(f"Source not found: {src_path}")
                continue

            print(f"  {filename} -> {name}.png")
            result, info = process_sideview(src_path, crop_frame, verbose=verbose,
                                            full_res=full_res)

            if result is not None:
                if result.mode != "RGBA":
                    result = result.convert("RGBA")
                out_path = output_dir / f"{name}.png"
                result.save(str(out_path), "PNG")
                processed_images[name] = result
            else:
                report["errors"].append(f"Failed to process {filename}")

            info["output_name"] = f"{name}.png"
            report["images"].append(info)

            if verbose and all_bounds.get(name):
                debug_dir.mkdir(parents=True, exist_ok=True)
                create_debug_visualization(
                    str(src_path), all_bounds[name], crop_frame,
                    str(debug_dir / f"debug_{name}.png"),
                )

        # Process combined-state images (e.g. pf_pr with both doors open)
        for name, filename in combined_views.items():
            src_path = input_dir / filename
            if not src_path.exists():
                continue

            print(f"  {filename} -> {name}.png (combined)")
            result, info = process_sideview(src_path, crop_frame, verbose=verbose,
                                            full_res=full_res)

            if result is not None:
                if result.mode != "RGBA":
                    result = result.convert("RGBA")
                out_path = output_dir / f"{name}.png"
                result.save(str(out_path), "PNG")
                processed_images[name] = result

            info["output_name"] = f"{name}.png"
            report["images"].append(info)

        # Process combined-door inputs (both front or both rear doors open)
        for name, filename in combined_doors.items():
            src_path = input_dir / filename
            if not src_path.exists():
                continue

            print(f"  {filename} -> {name}.png (combined-door input)")
            result, info = process_sideview(src_path, crop_frame, verbose=verbose,
                                            full_res=full_res)

            if result is not None:
                if result.mode != "RGBA":
                    result = result.convert("RGBA")
                out_path = output_dir / f"{name}.png"
                result.save(str(out_path), "PNG")
                processed_images[name] = result

            info["output_name"] = f"{name}.png"
            report["images"].append(info)

        # Process all-doors input (all 4 doors open)
        if all_doors_file:
            src_path = input_dir / all_doors_file
            if src_path.exists():
                print(f"  {all_doors_file} -> all-doors.png (all 4 doors)")
                result, info = process_sideview(src_path, crop_frame,
                                                verbose=verbose, full_res=full_res)
                if result is not None:
                    if result.mode != "RGBA":
                        result = result.convert("RGBA")
                    out_path = output_dir / "all-doors.png"
                    result.save(str(out_path), "PNG")
                    processed_images["all-doors"] = result

    elif side_views:
        report["errors"].append("Could not detect car in any side-view screenshot")

    # ══════════════════════════════════════════════════════════════════════════
    # Align all side views to base (corrects sub-pixel shifts between shots)
    # ══════════════════════════════════════════════════════════════════════════
    base_for_align = processed_images.get("base")
    if base_for_align and len(processed_images) > 1:
        print("Aligning side views to base...")
        for name in list(processed_images.keys()):
            if name == "base" or name in ("controls-bg", "climate-bg"):
                continue
            original = processed_images[name]
            aligned = _align_to_base(original, base_for_align, verbose=verbose)
            if aligned is not original:
                processed_images[name] = aligned
                out_path = output_dir / f"{name}.png"
                aligned.save(str(out_path), "PNG")
                if verbose:
                    print(f"    Overwrote {name}.png (aligned)")
        print()

    # ══════════════════════════════════════════════════════════════════════════
    # Pass 3: Split combined-door images into individual door overlays
    # ══════════════════════════════════════════════════════════════════════════
    combined_door_patterns = (COMBINED_DOOR_PATTERNS_ONCHARGE if mode == "oncharge"
                              else COMBINED_DOOR_PATTERNS_OFFCHARGE)

    base_img = processed_images.get("base")
    if base_img and combined_doors:
        print("Pass 3: Splitting combined-door images into individual overlays...")
        for door_key, filename in combined_doors.items():
            if door_key not in processed_images:
                continue
            combined_img = processed_images[door_key]

            # Look up near/far door output names from the pattern
            _stems, near_name, far_name = combined_door_patterns[door_key]
            near_out = f"{near_name}-open"
            far_out = f"{far_name}-open"

            # Skip splitting if we already have individual door images
            # (from SIDE_VIEW_PATTERNS_* — backwards compatibility)
            if near_out in processed_images and far_out in processed_images:
                if verbose:
                    print(f"  Skipping {door_key}: individual doors already present")
                continue

            print(f"  Splitting {door_key} -> {near_out}.png + {far_out}.png")
            near_overlay, far_overlay = split_combined_doors(
                combined_img, base_img, mode=mode, verbose=verbose)

            if near_overlay is not None and near_out not in processed_images:
                out_path = output_dir / f"{near_out}.png"
                near_overlay.save(str(out_path), "PNG")
                # Save as full RGBA image (door pixels + transparent bg) for
                # downstream overlay generation to diff against base
                # Composite onto base so generate_overlays sees a full image
                full_near = base_img.copy()
                full_near.paste(near_overlay, (0, 0), near_overlay)
                processed_images[near_out] = full_near
                full_near.save(str(out_path), "PNG")
                print(f"    Saved {near_out}.png")

            if far_overlay is not None and far_out not in processed_images:
                out_path = output_dir / f"{far_out}.png"
                full_far = base_img.copy()
                full_far.paste(far_overlay, (0, 0), far_overlay)
                processed_images[far_out] = full_far
                full_far.save(str(out_path), "PNG")
                print(f"    Saved {far_out}.png")
            elif far_overlay is None:
                if verbose:
                    print(f"    {far_out}: skipped (no significant pixels)")

        # Split all-doors image into near/far halves for same-side combined
        if "all-doors" in processed_images:
            all_doors_img = processed_images["all-doors"]
            print(f"  Splitting all-doors -> nf-nr-combined + ff-fr-combined")
            near_half, far_half = split_combined_doors(
                all_doors_img, base_img, mode=mode, verbose=verbose)

            for combo_name, half in [("nf-nr-combined", near_half),
                                     ("ff-fr-combined", far_half)]:
                if half is not None and combo_name not in processed_images:
                    full_img = base_img.copy()
                    full_img.paste(half, (0, 0), half)
                    out_path = output_dir / f"{combo_name}.png"
                    full_img.save(str(out_path), "PNG")
                    processed_images[combo_name] = full_img
                    print(f"    Saved {combo_name}.png")

        # Derive same-side combined overlays (nf-nr-combined, ff-fr-combined)
        # from individual door compositing as fallback when no all-doors screenshot.
        same_side_combos = (COMBINED_PATTERNS_ONCHARGE if mode == "oncharge"
                            else COMBINED_PATTERNS_OFFCHARGE)
        for combo_name, (_stems, constituents) in same_side_combos.items():
            if combo_name in processed_images:
                continue  # Already captured from a dedicated screenshot
            # Check if both constituent door images exist
            parts = [f"{c}-open" for c in constituents]
            if all(p in processed_images for p in parts):
                # Always composite rear door first, front door on top.
                # The door panels don't spatially overlap — only the gap/
                # interior regions do.  In the overlap zone, the front-door
                # overlay correctly shows the open-front-door interior that
                # would be visible through the rear door gap when both doors
                # are open.  This holds for both camera angles.
                z_ordered = list(reversed(parts))  # rear first, front on top
                print(f"  Compositing {combo_name} from {' + '.join(z_ordered)}")
                combo_img = base_img.copy()
                for p in z_ordered:
                    door_img = processed_images[p]
                    # Compute overlay vs base to get just the changed pixels
                    overlay = _compute_overlay(door_img, base_img,
                                               remove_cable=(mode == "oncharge"))
                    combo_img.paste(overlay, (0, 0), overlay)
                out_path = output_dir / f"{combo_name}.png"
                combo_img.save(str(out_path), "PNG")
                processed_images[combo_name] = combo_img
                print(f"    Saved {combo_name}.png")

        print()

    # ══════════════════════════════════════════════════════════════════════════
    # Panel processing
    # ══════════════════════════════════════════════════════════════════════════
    panels = mapping.get("panels", {})

    for name, filename in panels.items():
        src_path = input_dir / filename
        if not src_path.exists():
            report["errors"].append(f"Source not found: {src_path}")
            continue

        print(f"  {filename} -> {name}.png")

        if name == "controls-bg":
            result, info = process_controls_panel(src_path, verbose=verbose,
                                                  full_res=full_res)
        elif name == "climate-bg":
            result, info = process_climate_panel(src_path, verbose=verbose,
                                                 full_res=full_res)
        else:
            result, info = None, {"warnings": [f"Unknown panel type: {name}"],
                                  "success": False}

        if result is not None:
            if result.mode != "RGBA":
                result = result.convert("RGBA")
            out_path = output_dir / f"{name}.png"
            result.save(str(out_path), "PNG")
            processed_images[name] = result
            print(f"    Saved: {out_path}")
        else:
            report["errors"].append(f"Failed to process {filename}")

        info["output_name"] = f"{name}.png"
        report["images"].append(info)

    # ── Previews ──
    if reference_dir:
        ref_dir = Path(reference_dir)
        preview_dir.mkdir(parents=True, exist_ok=True)
        for name, img in processed_images.items():
            ref_path = ref_dir / f"{name}.png"
            if ref_path.exists():
                create_comparison_preview(
                    img, str(ref_path),
                    str(preview_dir / f"compare_{name}.png"), name)

    # ── Overview ──
    if processed_images:
        create_overview_grid(processed_images, str(output_dir / "overview.png"))

    # ── Summary ──
    total = len(report["images"])
    succeeded = sum(1 for r in report["images"] if r.get("success"))
    report["summary"] = {
        "total": total,
        "succeeded": succeeded,
        "failed": total - succeeded,
    }
    report["success"] = (total - succeeded == 0) and total > 0

    return report


# ═══════════════════════════════════════════════════════════════════════════════
# Combo State Generation
# ═══════════════════════════════════════════════════════════════════════════════

# Z-order from bottom to top (furthest → nearest to camera).
# Elements applied later overwrite earlier ones where they overlap.
# Offcharge is a front 3/4 view: front doors are nearest to camera.
OFFCHARGE_OVERLAYS = ["chargeport", "frunk", "fr", "ff", "nr", "nf"]
# Oncharge is a rear 3/4 view: rear doors are nearest to camera.
ONCHARGE_OVERLAYS  = ["frunk", "ff", "fr", "nf", "nr"]

DIFF_THRESHOLD = 18  # Euclidean RGB distance to count as changed pixel


def _detect_cable_mask(state_f, base_f, kernel):
    """Detect green/teal charging cable pixels in either image.

    The cable is distinctly green/teal (G >> R and G >> B).  No car body
    part, door interior, or background has this colour, so this is a safe
    filter for all paint colours.

    Args:
        state_f: float64 RGB array of the state image.
        base_f:  float64 RGB array of the base image.
        kernel:  morphological structuring element for dilation.

    Returns:
        uint8 mask (255 = cable pixel, 0 = not cable), dilated to cover
        anti-aliased edges and slight positional drift.
    """
    def _is_green(rgb_f):
        return ((rgb_f[:, :, 1] > 80) &
                (rgb_f[:, :, 1] > rgb_f[:, :, 0] + 30) &
                (rgb_f[:, :, 1] > rgb_f[:, :, 2] + 15))

    cable = (_is_green(state_f) | _is_green(base_f)).astype(np.uint8) * 255
    return cv2.dilate(cable, kernel, iterations=3)


def _compute_overlay(state_img, base_img, threshold=DIFF_THRESHOLD,
                     min_cc_area=80, remove_cable=False):
    """Compute an RGBA overlay from a state image vs base.

    Returns an RGBA PIL Image where changed pixels keep their color with
    alpha=255 and unchanged pixels are fully transparent.

    Cleanup:
      1. Morphological opening removes thin bleed artifacts (1-2px lines
         at door/body boundaries from lighting differences).
      2. (Optional) Cable removal strips green/teal charging cable pixels
         that shift position between on-charge screenshots.
      3. Connected-component filtering drops small blobs (ground reflections,
         shadow shifts) that aren't part of the main overlay element.
      4. Convex-hull fill recaptures subtle same-colour pixels (e.g. a red
         door panel shifting position) that fall below the RGB distance
         threshold but sit within the convex hull of the detected region.
    """
    s = np.array(state_img.convert("RGB")).astype(np.float64)
    b = np.array(base_img.convert("RGB")).astype(np.float64)
    diff = np.sqrt(np.sum((s - b) ** 2, axis=2))
    mask = (diff > threshold).astype(np.uint8) * 255

    # Morphological open removes thin lines and isolated noise
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)

    # Remove charging cable artifacts — cable shifts between screenshots
    if remove_cable:
        cable = _detect_cable_mask(s, b, kernel)
        mask[cable > 0] = 0

    # Drop small connected components (ground reflections, cable glow, etc.)
    n_cc, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    for i in range(1, n_cc):
        if stats[i, cv2.CC_STAT_AREA] < min_cc_area:
            mask[labels == i] = 0

    # Remove UI text artifacts from oncharge overlays BEFORE hull fill.
    # Text from the Tesla app (e.g. "remaining to charge limit") leaks into
    # the crop frame above the frunk hood.  remove_sideview_ui() can't strip
    # it because the text is pixel-adjacent to the frunk and flood fill
    # connects them.  Pixel-level filter: in the top portion, any achromatic
    # bright pixel is text (car body is always chromatic, shadows are dark).
    if remove_cable:
        h_img = mask.shape[0]
        top_cutoff = int(h_img * 0.35)
        state_u8 = s.astype(np.uint8)
        ch_range = (np.max(state_u8, axis=2).astype(int) -
                    np.min(state_u8, axis=2).astype(int))
        brightness = np.mean(state_u8, axis=2)
        # Text is achromatic (ch_range < 25) and brighter than background
        # (brightness > 40).  Car body is chromatic; shadows are dark.
        text_like = (ch_range < 25) & (brightness > 40)
        text_region = np.zeros_like(mask)
        text_region[:top_cutoff] = 255
        mask[(text_like & (text_region > 0) & (mask > 0))] = 0

    # Convex-hull gap fill: door panels shift position when open but keep
    # nearly the same colour, so per-pixel diff misses interior body pixels.
    # Compute the convex hull of each large connected component and include
    # any pixel within the hull that has *some* visible change (diff > 1).
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_SIMPLE)
    hull_mask = np.zeros_like(mask)
    for c in contours:
        if cv2.contourArea(c) < min_cc_area:
            continue
        hull = cv2.convexHull(c)
        cv2.drawContours(hull_mask, [hull], 0, 255, -1)
    soft_change = (diff > 1).astype(np.uint8) * 255
    # Exclude text-like pixels so hull fill can't re-add them
    if remove_cable:
        soft_change[(text_like & (text_region > 0))] = 0
    hull_filled = hull_mask & soft_change
    mask = np.maximum(mask, hull_filled)

    rgba = np.array(state_img.convert("RGBA"))
    rgba[:, :, 3] = mask
    return Image.fromarray(rgba)


def split_combined_doors(combined_img, base_img, mode="offcharge",
                         threshold=DIFF_THRESHOLD, min_cc_area=80,
                         verbose=False):
    """Split a combined-door image into near-side and far-side overlays.

    Takes a processed side-view image with BOTH front (or rear) doors open
    and the base (all-closed) image, then separates the diff mask into two
    halves — one per door — using the valley in the x-axis projection.

    Args:
        combined_img: PIL Image with both doors open (RGBA).
        base_img: PIL Image with all doors closed (RGBA).
        mode: "offcharge" or "oncharge" — determines which image side is near.
        threshold: RGB Euclidean distance for diff detection.
        min_cc_area: Minimum connected-component area in pixels.
        verbose: Print diagnostic info.

    Returns:
        (near_overlay, far_overlay) — each a PIL RGBA Image (or None if that
        side has too few changed pixels to constitute a door overlay).
    """
    s = np.array(combined_img.convert("RGB")).astype(np.float64)
    b = np.array(base_img.convert("RGB")).astype(np.float64)
    diff = np.sqrt(np.sum((s - b) ** 2, axis=2))
    mask = (diff > threshold).astype(np.uint8) * 255

    # Morphological open to clean noise (same as _compute_overlay)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)

    # Remove charging cable artifacts in oncharge mode
    if mode == "oncharge":
        cable = _detect_cable_mask(s, b, kernel)
        mask[cable > 0] = 0

    # Drop tiny connected components
    n_cc, labels, stats, centroids = cv2.connectedComponentsWithStats(mask, 8)
    for i in range(1, n_cc):
        if stats[i, cv2.CC_STAT_AREA] < min_cc_area:
            mask[labels == i] = 0

    h, w = mask.shape

    # ── Find split x-coordinate ──────────────────────────────────────────
    # Project mask onto x-axis: column sums give a 1D profile.
    col_sums = np.sum(mask > 0, axis=0).astype(np.float64)

    # Smooth the profile to avoid splitting on minor noise gaps
    smooth_k = max(3, w // 50)
    if smooth_k % 2 == 0:
        smooth_k += 1
    col_smooth = np.convolve(col_sums, np.ones(smooth_k) / smooth_k,
                             mode="same")

    # Identify active region (columns with meaningful content)
    active_cols = np.where(col_smooth > 0.5)[0]
    if len(active_cols) < 2:
        if verbose:
            print("    split_combined_doors: too few active columns")
        return None, None

    left_edge = int(active_cols[0])
    right_edge = int(active_cols[-1])

    # Search the middle 60% for the deepest valley
    search_start = left_edge + int((right_edge - left_edge) * 0.2)
    search_end = left_edge + int((right_edge - left_edge) * 0.8)
    search_zone = col_smooth[search_start:search_end]

    split_x = None
    if len(search_zone) > 0:
        valley_idx = int(np.argmin(search_zone))
        valley_val = search_zone[valley_idx]
        peak_val = np.max(col_smooth[left_edge:right_edge])

        # Valley must be substantially lower than peak to count
        if peak_val > 0 and valley_val < peak_val * 0.3:
            split_x = search_start + valley_idx
            if verbose:
                print(f"    Valley split at x={split_x} "
                      f"(valley={valley_val:.1f}, peak={peak_val:.1f})")

    # Fallback: connected-component centroid analysis
    if split_x is None:
        n_cc2, labels2, stats2, centroids2 = cv2.connectedComponentsWithStats(
            mask, 8)
        # Collect centroids of significant components
        cx_list = []
        for i in range(1, n_cc2):
            if stats2[i, cv2.CC_STAT_AREA] >= min_cc_area:
                cx_list.append(centroids2[i][0])

        if len(cx_list) >= 2:
            cx_sorted = sorted(cx_list)
            # Find the largest gap between adjacent centroids
            max_gap = 0
            best_split = w // 2
            for j in range(len(cx_sorted) - 1):
                gap = cx_sorted[j + 1] - cx_sorted[j]
                if gap > max_gap:
                    max_gap = gap
                    best_split = int((cx_sorted[j] + cx_sorted[j + 1]) / 2)
            split_x = best_split
            if verbose:
                print(f"    CC centroid fallback split at x={split_x} "
                      f"(gap={max_gap:.0f}px)")
        else:
            # Cannot split — single cluster or no content
            split_x = w // 2
            if verbose:
                print(f"    Cannot find valley or CC gap — "
                      f"splitting at midpoint x={split_x}")

    # ── Create left/right masks ──────────────────────────────────────────
    left_mask = mask.copy()
    left_mask[:, split_x:] = 0
    right_mask = mask.copy()
    right_mask[:, :split_x] = 0

    # Assign near/far based on camera mode
    # Offcharge (front 3/4 from right): near = RIGHT, far = LEFT
    # Oncharge (rear 3/4 from left):    near = LEFT,  far = RIGHT
    if mode == "oncharge":
        near_mask, far_mask = left_mask, right_mask
    else:
        near_mask, far_mask = right_mask, left_mask

    # ── Build overlay images with convex-hull fill (same as _compute_overlay) ──
    results = []
    for half_mask in (near_mask, far_mask):
        n_px = int(np.sum(half_mask > 0))
        if n_px < min_cc_area:
            results.append(None)
            continue

        # Convex-hull gap fill on this half
        contours, _ = cv2.findContours(half_mask, cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)
        hull_mask = np.zeros_like(half_mask)
        for c in contours:
            if cv2.contourArea(c) < min_cc_area:
                continue
            hull = cv2.convexHull(c)
            cv2.drawContours(hull_mask, [hull], 0, 255, -1)
        soft_change = (diff > 1).astype(np.uint8) * 255
        hull_filled = hull_mask & soft_change
        final_mask = np.maximum(half_mask, hull_filled)

        rgba = np.array(combined_img.convert("RGBA"))
        rgba[:, :, 3] = final_mask
        results.append(Image.fromarray(rgba))

    near_overlay, far_overlay = results

    if verbose:
        for label, ovl in [("near", near_overlay), ("far", far_overlay)]:
            if ovl is not None:
                arr = np.array(ovl)
                n = int(np.sum(arr[:, :, 3] > 0))
                print(f"    {label}: {n} opaque px")
            else:
                print(f"    {label}: skipped (below threshold)")

    return near_overlay, far_overlay


def generate_overlays(processed_dir, output_dir, mode="offcharge",
                      verbose=False):
    """Export transparent overlay PNGs for runtime compositing.

    Instead of pre-rendering every state combination (128 offcharge / 32
    oncharge), this saves individual overlay diffs that the card stacks
    at runtime via CSS absolute positioning.

    When mode="oncharge", output filenames are prefixed with ``oncharge-``
    so both sets coexist in the same ``overlays/`` directory.

    Offcharge output files:
      base.png, trunk-open.png,
      chargeport-overlay.png, frunk-overlay.png,
      nf-overlay.png, nr-overlay.png, ff-overlay.png, fr-overlay.png,
      nf-nr-combined-overlay.png, ff-fr-combined-overlay.png

    Oncharge output files:
      oncharge-base.png, oncharge-trunk-open.png,
      oncharge-frunk-overlay.png, oncharge-nf-overlay.png,
      oncharge-nr-overlay.png, oncharge-ff-overlay.png, oncharge-fr-overlay.png,
      oncharge-nf-nr-combined-overlay.png, oncharge-ff-fr-combined-overlay.png
    """
    processed_dir = Path(processed_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    prefix = "oncharge-" if mode == "oncharge" else ""

    base_path = processed_dir / "base.png"
    trunk_path = processed_dir / "trunk-open.png"

    if not base_path.exists():
        print(f"  ERROR: {base_path} not found")
        return

    base_img = Image.open(str(base_path)).convert("RGBA")

    # For oncharge, clean stray green cable pixels from base images.
    # The cable body is in the lower half; any green in the top rows is stray.
    def _clean_stray_green(img, top_rows=5):
        arr = np.array(img)
        rgb = arr[:top_rows, :, :3].astype(float)
        green = ((rgb[:, :, 1] > 60) &
                 (rgb[:, :, 1] > rgb[:, :, 0] + 20) &
                 (rgb[:, :, 1] > rgb[:, :, 2] + 10))
        if np.any(green):
            for c in range(3):
                arr[:top_rows, :, c] = np.where(green, 22, arr[:top_rows, :, c])
            return Image.fromarray(arr)
        return img

    if mode == "oncharge":
        base_img = _clean_stray_green(base_img)

    # Copy base images as-is (opaque)
    base_out = f"{prefix}base.png"
    trunk_out = f"{prefix}trunk-open.png"
    base_img.save(str(output_dir / base_out), "PNG")
    if trunk_path.exists():
        trunk_img_save = Image.open(str(trunk_path)).convert("RGBA")
        if mode == "oncharge":
            trunk_img_save = _clean_stray_green(trunk_img_save)
        trunk_img_save.save(str(output_dir / trunk_out), "PNG")
    print(f"  Saved {base_out}" +
          (f" + {trunk_out}" if trunk_path.exists() else ""))

    # Extract green charging cable into a dedicated transparent overlay.
    # The card renders this on top of the base and applies CSS glow animation
    # to just this element, so the glow halos around the cable only.
    if mode == "oncharge":
        base_arr = np.array(base_img)
        rgb_f = base_arr[:, :, :3].astype(np.float64)
        cable_mask = ((rgb_f[:, :, 1] > 80) &
                      (rgb_f[:, :, 1] > rgb_f[:, :, 0] + 30) &
                      (rgb_f[:, :, 1] > rgb_f[:, :, 2] + 15))
        if np.any(cable_mask):
            cable_rgba = np.zeros_like(base_arr)
            cable_rgba[cable_mask] = base_arr[cable_mask]
            cable_img = Image.fromarray(cable_rgba)
            cable_img.save(str(output_dir / "oncharge-cable-overlay.png"), "PNG")
            print(f"  Saved oncharge-cable-overlay.png")

    overlays_list = (ONCHARGE_OVERLAYS if mode == "oncharge"
                     else OFFCHARGE_OVERLAYS)

    # Generate individual overlays
    count = 0
    for name in overlays_list:
        img_path = processed_dir / f"{name}-open.png"
        if not img_path.exists():
            if verbose:
                print(f"  Skipping {name}: {img_path} not found")
            continue
        state_img = Image.open(str(img_path)).convert("RGBA")
        overlay = _compute_overlay(state_img, base_img,
                                   remove_cable=(mode == "oncharge"))
        out_name = f"{prefix}{name}-overlay.png"
        out_path = output_dir / out_name
        overlay.save(str(out_path), "PNG")
        count += 1
        if verbose:
            arr = np.array(overlay)
            n = int(np.sum(arr[:, :, 3] > 0))
            total = arr.shape[0] * arr.shape[1]
            print(f"  {out_name}: {n} opaque px "
                  f"({100*n/total:.1f}%)")

    # Generate combined overlays
    combined_patterns = (COMBINED_PATTERNS_ONCHARGE if mode == "oncharge"
                         else COMBINED_PATTERNS_OFFCHARGE)
    for cname, (_stems, constituents) in combined_patterns.items():
        cpath = processed_dir / f"{cname}.png"
        if not cpath.exists():
            continue
        cimg = Image.open(str(cpath)).convert("RGBA")
        overlay = _compute_overlay(cimg, base_img,
                                   remove_cable=(mode == "oncharge"))
        out_name = f"{prefix}{cname}-overlay.png"
        out_path = output_dir / out_name
        overlay.save(str(out_path), "PNG")
        count += 1
        if verbose:
            arr = np.array(overlay)
            n = int(np.sum(arr[:, :, 3] > 0))
            print(f"  {out_name}: {n} opaque px")

    # Copy panel backgrounds — oncharge gets '-charging' suffix for the card
    for panel in ("controls-bg", "climate-bg"):
        panel_path = processed_dir / f"{panel}.png"
        if not panel_path.exists():
            continue
        if mode == "oncharge":
            panel_out = f"{panel}-charging.png"
        else:
            panel_out = f"{panel}.png"
        # Panel backgrounds go to the PARENT of the overlays dir (colour root)
        panel_dst = output_dir.parent / panel_out
        Image.open(str(panel_path)).convert("RGBA").save(str(panel_dst), "PNG")
        print(f"  Saved {panel_out}")

    print(f"  Generated {count} overlays in {output_dir}")


def generate_combo_states(processed_dir, output_dir, mode="offcharge",
                          verbose=False):
    """Generate all combo state images from processed side-view outputs.

    For offcharge: 7 binary toggles (chargeport, trunk, frunk, fr, ff, nr, nf)
        → 128 combos.  Trunk swaps the base image entirely.
    For oncharge: 6 binary toggles (trunk, frunk, ff, fr, nr, nf)
        → 64 combos.  Base is the on-charge closed image.

    Trunk is handled by using trunk-open as the base image (not an overlay
    diff), because the trunk changes the car silhouette dramatically and a
    simple diff loses it.
    """
    processed_dir = Path(processed_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Load images (no prefix — offcharge and oncharge use separate dirs)
    base_path = processed_dir / "base.png"
    trunk_path = processed_dir / "trunk-open.png"

    if not base_path.exists():
        print(f"  ERROR: {base_path} not found")
        return

    base_img = Image.open(str(base_path)).convert("RGBA")
    trunk_img = None
    if trunk_path.exists():
        trunk_img = Image.open(str(trunk_path)).convert("RGBA")

    overlays_list = ONCHARGE_OVERLAYS if mode == "oncharge" else OFFCHARGE_OVERLAYS

    # Compute overlay diffs from base
    overlay_diffs = {}
    for name in overlays_list:
        img_path = processed_dir / f"{name}-open.png"
        if not img_path.exists():
            if verbose:
                print(f"  Skipping {name}: {img_path} not found")
            continue
        state_img = Image.open(str(img_path)).convert("RGBA")
        overlay_diffs[name] = _compute_overlay(state_img, base_img,
                                               remove_cable=(mode == "oncharge"))
        if verbose:
            arr = np.array(overlay_diffs[name])
            n = np.sum(arr[:, :, 3] > 0)
            print(f"  {name} overlay: {n} px")

    # NOTE: front/rear bleed removal was removed — the dilated rear-door
    # mask was destroying most of the front-door overlay because the doors
    # are spatially close in the 3/4 view.  When both same-side doors are
    # open, the combined image (nf-nr-combined / ff-fr-combined) already
    # handles the shared interior correctly, so individual overlays don't
    # need cross-door cleanup.

    # All overlay diffs are computed against BASE only.  Door screenshots
    # have trunk closed, so diffing against base (also trunk-closed) captures
    # only the door change.  For trunk combos the canvas starts as trunk-open
    # and door overlays are composited on top — since door overlays have
    # transparent pixels in the trunk area, the open trunk shows through.

    # Load combined-state images and compute their diffs vs base.
    # These replace individual overlays when all constituents are active.
    # Key = frozenset of constituent names, value = overlay RGBA image.
    combined_diffs = {}
    combined_patterns = (COMBINED_PATTERNS_ONCHARGE if mode == "oncharge"
                         else COMBINED_PATTERNS_OFFCHARGE)
    for cname, (_stems, constituents) in combined_patterns.items():
        cpath = processed_dir / f"{cname}.png"
        if not cpath.exists():
            continue
        cimg = Image.open(str(cpath)).convert("RGBA")
        key = frozenset(constituents)
        combined_diffs[key] = _compute_overlay(cimg, base_img,
                                               remove_cable=(mode == "oncharge"))
        if verbose:
            arr = np.array(combined_diffs[key])
            n = np.sum(arr[:, :, 3] > 0)
            print(f"  combined {constituents} overlay: {n} px")

    # Build all combinations
    # Toggles: trunk + each overlay in overlays_list
    toggle_names = ["trunk"] + list(overlays_list)
    n_toggles = len(toggle_names)
    total = 1 << n_toggles

    count = 0
    for bits in range(total):
        active = []
        for i, name in enumerate(toggle_names):
            if bits & (1 << i):
                active.append(name)

        trunk_active = "trunk" in active
        overlay_active = [n for n in active if n != "trunk"]

        # Skip trunk combos if no trunk image
        if trunk_active and trunk_img is None:
            continue

        # Build filename
        if not active:
            fname = "base.png"
        else:
            fname = "-".join(active) + ".png"

        # Pick base canvas (trunk-open or base); overlays are always base-relative
        if trunk_active:
            canvas = trunk_img.copy()
        else:
            canvas = base_img.copy()

        # Check which combined overlays apply to this combo
        active_set = frozenset(overlay_active)
        used_combined = set()  # overlay names handled by a combined image
        for combo_key in combined_diffs:
            if combo_key.issubset(active_set):
                used_combined.update(combo_key)

        # Apply overlays in z-order
        for overlay_name in overlays_list:
            if overlay_name not in overlay_active:
                continue
            if overlay_name in used_combined:
                # Apply the combined overlay once, at the z-position of its
                # last constituent in the overlay list
                combo_key = None
                for ck in combined_diffs:
                    if overlay_name in ck and ck.issubset(active_set):
                        combo_key = ck
                        break
                if combo_key is not None:
                    last_in_z = max(
                        overlays_list.index(c) for c in combo_key
                        if c in overlay_active
                    )
                    if overlays_list.index(overlay_name) == last_in_z:
                        canvas = Image.alpha_composite(
                            canvas, combined_diffs[combo_key])
                continue
            if overlay_name not in overlay_diffs:
                continue
            canvas = Image.alpha_composite(canvas, overlay_diffs[overlay_name])

        canvas.save(str(output_dir / fname), "PNG")
        count += 1

    print(f"  Generated {count} {mode} combo states in {output_dir}")


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Process Tesla app screenshots into card-ready images",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--input-dir", required=True,
                        help="Directory containing Tesla app screenshots")
    parser.add_argument("--output-dir", required=True,
                        help="Directory for processed output images")
    parser.add_argument("--reference-dir", default=None,
                        help="Reference images for preview comparison")
    parser.add_argument("--manifest", default=None,
                        help="JSON manifest mapping output names to input files")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Print detailed processing information")
    parser.add_argument("--mode", choices=["offcharge", "oncharge"],
                        default="offcharge",
                        help="Camera angle: offcharge (front 3/4 from right) "
                             "or oncharge (rear 3/4 from left)")
    parser.add_argument("--generate-states", action="store_true",
                        help="Generate all combo state images from processed output")
    parser.add_argument("--generate-overlays", action="store_true",
                        help="Generate transparent overlay PNGs for runtime compositing")
    parser.add_argument("--full-res", action="store_true",
                        help="Output at native crop resolution (skip final resize)")
    args = parser.parse_args()

    if not Path(args.input_dir).is_dir():
        print(f"ERROR: Input directory not found: {args.input_dir}", file=sys.stderr)
        sys.exit(1)

    print("=" * 70)
    print(f"Tesla Screenshot Processing Pipeline ({args.mode})")
    print("=" * 70)
    start = time.time()

    report = process_all(
        input_dir=args.input_dir,
        output_dir=args.output_dir,
        reference_dir=args.reference_dir,
        manifest_path=args.manifest,
        mode=args.mode,
        verbose=args.verbose,
        full_res=args.full_res,
    )

    elapsed = time.time() - start

    report_path = Path(args.output_dir) / "report.json"
    with open(str(report_path), "w") as f:
        json.dump(report, f, indent=2)

    s = report["summary"]
    print(f"\n{'=' * 70}")
    print(f"Done in {elapsed:.1f}s — "
          f"{s['succeeded']}/{s['total']} images processed successfully")
    if report["errors"]:
        print(f"Errors ({len(report['errors'])}):")
        for err in report["errors"]:
            print(f"  - {err}")
    print(f"Report: {report_path}")

    # Generate combo states if requested
    if args.generate_states:
        states_dir = Path(args.output_dir) / "states"
        print(f"\nGenerating combo states...")
        generate_combo_states(
            args.output_dir, states_dir,
            mode=args.mode, verbose=args.verbose)

    # Generate overlays if requested
    if args.generate_overlays:
        overlays_dir = Path(args.output_dir) / "overlays"
        print(f"\nGenerating transparent overlays...")
        generate_overlays(
            args.output_dir, overlays_dir,
            mode=args.mode, verbose=args.verbose)

    sys.exit(0 if report.get("success") else 1)


if __name__ == "__main__":
    main()
