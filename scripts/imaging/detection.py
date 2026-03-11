"""Low-level image analysis helpers: background detection, car bounds, alignment."""

import cv2
import numpy as np
from PIL import Image

from .constants import (
    BG_DISTANCE_THRESHOLD,
    COL_COVERAGE_THRESHOLD,
    CORNER_SAMPLE_SIZE,
    EDGE_MARGIN_PCT,
    GAP_BRIDGE_PCT,
    ROW_COVERAGE_THRESHOLD,
    SEARCH_ZONE_BOTTOM,
    SEARCH_ZONE_TOP,
    UNION_PADDING_PCT,
)


def find_contiguous_runs(mask, gap_bridge=0):
    """Find contiguous runs of True values in a 1D boolean array.

    If gap_bridge > 0, gaps of that many False values are bridged before
    scanning.  This handles cars where a thin horizontal band (e.g. a
    window or door seam) falls below the coverage threshold but should
    be included in the bounding box.

    Returns:
        List of (start, end) tuples for each run (end exclusive).
    """
    if len(mask) == 0:
        return []

    if gap_bridge > 0:
        mask = mask.copy()
        in_gap = 0
        gap_start = -1
        for i in range(len(mask)):
            if mask[i]:
                if 0 < in_gap <= gap_bridge:
                    mask[gap_start:i] = True
                in_gap = 0
            else:
                if in_gap == 0:
                    gap_start = i
                in_gap += 1

    runs = []
    start = None
    for i, val in enumerate(mask):
        if val and start is None:
            start = i
        elif not val and start is not None:
            runs.append((start, i))
            start = None
    if start is not None:
        runs.append((start, len(mask)))

    return runs


def detect_background_color(arr):
    """Detect the Tesla app background color from corner samples.

    The Tesla app uses a two-tone background (lighter top ~22, darker
    bottom ~10).  We sample only the TOP corners to get a consistent
    reference for downstream processing.
    """
    s = CORNER_SAMPLE_SIZE
    h, w = arr.shape[:2]
    corners = [
        arr[:s, :s, :3],           # top-left
        arr[:s, w - s:, :3],       # top-right
    ]
    samples = np.concatenate([c.reshape(-1, 3) for c in corners], axis=0)
    return np.median(samples, axis=0).astype(np.float64)


def create_non_bg_mask(arr, bg_color, threshold=BG_DISTANCE_THRESHOLD):
    """Create a binary mask of non-background pixels using Euclidean distance."""
    rgb = arr[:, :, :3].astype(np.float64)
    dist = np.sqrt(np.sum((rgb - bg_color) ** 2, axis=2))
    return (dist > threshold).astype(np.uint8)


def detect_battery_bottom(arr):
    """Detect the bottom edge of the battery/status bar UI header.

    Scans downward from the top of the image looking for the first row
    where battery/signal icons (bright achromatic pixels) disappear.
    Returns the y-coordinate just below the last header row, or 0 if
    no header detected.
    """
    h, w = arr.shape[:2]
    scan_h = int(h * 0.12)
    rgb = arr[:scan_h, :, :3]
    gray = np.mean(rgb, axis=2)
    ch_range = np.max(rgb, axis=2).astype(int) - np.min(rgb, axis=2).astype(int)

    # Battery/signal icons: bright (gray > 150) and achromatic (ch_range < 30)
    icon_mask = (gray > 150) & (ch_range < 30)
    row_density = np.mean(icon_mask, axis=1)

    # Find the last row with significant icon density (> 0.5% of width)
    header_rows = np.where(row_density > 0.005)[0]
    if len(header_rows) == 0:
        return 0

    return int(header_rows[-1]) + 1


def find_car_bounds_sideview(non_bg_mask):
    """Detect the car bounding box in a side-view screenshot.

    Uses row/column coverage analysis with gap bridging.  Returns the
    bounding box as (x0, y0, x1, y1) and a confidence score.
    """
    h, w = non_bg_mask.shape

    top = int(h * SEARCH_ZONE_TOP)
    bottom = int(h * SEARCH_ZONE_BOTTOM)
    margin = int(w * EDGE_MARGIN_PCT)

    analysis_zone = non_bg_mask[top:bottom, margin:w - margin]
    ah = analysis_zone.shape[0]

    row_coverage = np.mean(analysis_zone, axis=1)
    gap_bridge = max(2, int(ah * GAP_BRIDGE_PCT))
    runs = find_contiguous_runs(
        row_coverage > ROW_COVERAGE_THRESHOLD, gap_bridge=gap_bridge)

    if not runs:
        return None, 0.0

    best = max(runs, key=lambda r: r[1] - r[0])
    car_y0 = top + best[0]
    car_y1 = top + best[1]

    car_band = non_bg_mask[car_y0:car_y1, :]
    col_coverage = np.mean(car_band, axis=0)
    car_cols = np.where(col_coverage > COL_COVERAGE_THRESHOLD)[0]

    if len(car_cols) == 0:
        return None, 0.0

    car_x0 = int(car_cols[0])
    car_x1 = int(car_cols[-1])

    # Extend downward: include wheels/shadow below the analysis zone
    extended_y1 = car_y1
    for y in range(car_y1, min(h, car_y1 + int(h * 0.35))):
        row = non_bg_mask[y, car_x0:car_x1]
        if np.mean(row) < 0.005:
            break
        extended_y1 = y + 1
    car_y1 = extended_y1

    # Confidence: how much of the bounding box is filled
    car_region = non_bg_mask[car_y0:car_y1, car_x0:car_x1]
    fill_ratio = np.mean(car_region) if car_region.size > 0 else 0
    confidence = min(1.0, fill_ratio * 2)

    return (car_x0, car_y0, car_x1, car_y1), confidence


def compute_crop_frame_from_union(union_bounds, img_shape, min_y0=0,
                                  closed_bounds=None):
    """Compute a standardized crop frame from the union of all car bounds.

    The crop frame is centered on the closed-car centroid (or union centroid)
    with consistent aspect ratio and padding.  It is clamped to image boundaries
    and shifted to contain all union pixels.

    Args:
        union_bounds: (x0, y0, x1, y1) union of all detected car bounds.
        img_shape: (height, width, channels) of the source image.
        min_y0: minimum allowed top edge (below battery bar).
        closed_bounds: optional (x0, y0, x1, y1) of the closed/base car.

    Returns:
        (cx0, cy0, cx1, cy1) crop frame coordinates.
    """
    ux0, uy0, ux1, uy1 = union_bounds
    img_h, img_w = img_shape[:2]

    uw = ux1 - ux0
    uh = uy1 - uy0
    max_dim = max(uw, uh)
    pad = int(max_dim * UNION_PADDING_PCT)

    frame_w = uw + 2 * pad
    frame_h = uh + 2 * pad

    target_aspect = 417 / 262
    if frame_w / frame_h > target_aspect:
        frame_h = int(frame_w / target_aspect)
    else:
        frame_w = int(frame_h * target_aspect)

    min_pad_ratio = 1.10
    if frame_w < uw * min_pad_ratio:
        frame_w = int(uw * min_pad_ratio)
        frame_h = int(frame_w / target_aspect)
    if frame_h < uh * min_pad_ratio:
        frame_h = int(uh * min_pad_ratio)
        frame_w = int(frame_h * target_aspect)

    # Center on closed car if available, otherwise union center
    if closed_bounds:
        cx = (closed_bounds[0] + closed_bounds[2]) / 2
        cy = (closed_bounds[1] + closed_bounds[3]) / 2
    else:
        cx = (ux0 + ux1) / 2
        cy = (uy0 + uy1) / 2

    fx0 = int(cx - frame_w / 2)
    fy0 = int(cy - frame_h / 2)
    fx1 = fx0 + frame_w
    fy1 = fy0 + frame_h

    # Shift to contain ALL union pixels (open trunk may extend above frame)
    if ux0 < fx0:
        shift = fx0 - ux0 + pad
        fx0 -= shift
        fx1 -= shift
    if ux1 > fx1:
        shift = ux1 - fx1 + pad
        fx0 += shift
        fx1 += shift
    if uy0 < fy0:
        shift = fy0 - uy0 + pad
        fy0 -= shift
        fy1 -= shift
    if uy1 > fy1:
        shift = uy1 - fy1 + pad
        fy0 += shift
        fy1 += shift

    # Enforce minimum y0 (below battery bar)
    if fy0 < min_y0:
        shift = min_y0 - fy0
        fy0 += shift
        fy1 += shift

    # Clamp to image boundaries
    if fx0 < 0:
        fx1 -= fx0
        fx0 = 0
    if fy0 < 0:
        fy1 -= fy0
        fy0 = 0
    if fx1 > img_w:
        fx0 -= (fx1 - img_w)
        fx1 = img_w
    if fy1 > img_h:
        fy0 -= (fy1 - img_h)
        fy1 = img_h
    fx0 = max(0, fx0)
    fy0 = max(0, fy0)

    return (fx0, fy0, fx1, fy1)


def align_to_reference(state_img, base_img, verbose=False):
    """Align a side-view image to a reference using phase correlation.

    Only the bottom 60% of the image (wheels, rocker panel, lower body)
    is used for correlation since the top portion changes between states
    (frunk, doors, trunk).

    Args:
        state_img: PIL Image to align.
        base_img: PIL Image reference.
        verbose: Print shift diagnostics.

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

    # Use bottom 60% — wheels, rocker panel, lower body are stable
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
