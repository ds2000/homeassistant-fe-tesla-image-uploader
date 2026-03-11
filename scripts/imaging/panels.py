"""Panel processing: controls and climate panel inpainting, crop, resize."""

import cv2
import numpy as np
from PIL import Image

from .constants import CLIMATE_SIZE, CONTROLS_SIZE
from .detection import (
    create_non_bg_mask,
    detect_background_color,
    find_contiguous_runs,
)


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
    car_mask = cv2.bitwise_or(car_mask, cv2.bitwise_not(filled))

    # Close small gaps in the mask
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15))
    car_mask = cv2.morphologyEx(car_mask, cv2.MORPH_CLOSE, kernel)

    return car_mask


def inpaint_controls_ui(car_img, car_mask, car_x0, car_y0, car_w, car_h):
    """Detect and inpaint UI overlays on a controls panel image.

    Three-layer detection:
    - Very bright text on dark surfaces (gray > 100): catches "Open" text
    - White overlay on body panels (min_ch > 140): catches hood "Open"
    - Small achromatic icons on glass (padlock): isolated gray blobs
    """
    gray = cv2.cvtColor(car_img, cv2.COLOR_BGR2GRAY)
    b_ch, g_ch, r_ch = cv2.split(car_img)
    min_ch = np.minimum(np.minimum(r_ch, g_ch), b_ch)
    max_ch = np.maximum(np.maximum(r_ch, g_ch), b_ch)
    ch_range = max_ch.astype(int) - min_ch.astype(int)
    local_mean = cv2.blur(gray.astype(np.float64), (121, 121))

    # Layer 1: Very bright achromatic elements on dark surfaces
    dark_zone = local_mean < 55
    bright_text = ((gray.astype(np.float64) > 100) & dark_zone & (ch_range < 60)).astype(np.uint8) * 255

    # Layer 2: White overlay on body panels
    white_on_body = ((min_ch > 140) & (local_mean >= 55)).astype(np.uint8) * 255

    # Layer 3: Small achromatic icons on dark surfaces (padlock)
    achromatic_on_dark = (
        (gray > 55) & (gray < 100) &
        (ch_range < 10) &
        (dark_zone)
    ).astype(np.uint8) * 255

    # --- Pre-filter white_on_body: skip body-sized CCs ---
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
        cx_rel = (cc_cents[i][0] - car_x0) / car_w
        cy_rel = (cc_cents[i][1] - car_y0) / car_h
        cw = cc_stats[i, cv2.CC_STAT_WIDTH]
        ch = cc_stats[i, cv2.CC_STAT_HEIGHT]
        aspect = max(cw, ch) / (min(cw, ch) + 1)
        if cy_rel < 0.20 and (cx_rel < 0.35 or cx_rel > 0.65):
            continue
        if area > size_thresh * 2 and (cx_rel < 0.10 or cx_rel > 0.90) and aspect > 4:
            continue
        text_final[cc_labels == i] = 255

    # --- Achromatic icon layer ---
    k_close_sm = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    k_dilate_sm = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
    icon_mask = cv2.morphologyEx(achromatic_on_dark, cv2.MORPH_CLOSE, k_close_sm)
    icon_mask = cv2.dilate(icon_mask, k_dilate_sm, iterations=1)
    icon_mask = cv2.bitwise_and(icon_mask, car_mask)

    n_cc2, cc_labels2, cc_stats2, cc_cents2 = cv2.connectedComponentsWithStats(icon_mask, 8)
    icon_final = np.zeros_like(icon_mask)
    for i in range(1, n_cc2):
        area = cc_stats2[i, cv2.CC_STAT_AREA]
        if area < 15:
            continue
        if area > 5000:
            continue
        cx_rel = (cc_cents2[i][0] - car_x0) / car_w
        cy_rel = (cc_cents2[i][1] - car_y0) / car_h
        if cy_rel < 0.20 and (cx_rel < 0.35 or cx_rel > 0.65):
            continue
        icon_final[cc_labels2 == i] = 255

    final_mask = cv2.bitwise_or(text_final, icon_final)
    return cv2.inpaint(car_img, final_mask, inpaintRadius=12, flags=cv2.INPAINT_TELEA)


def inpaint_climate_ui(img_bgr, car_mask, cx0, cy0, cx1, cy1):
    """Detect and inpaint UI overlays on a climate panel image.

    Targeted approach using distance-transform-based deep interior detection.
    """
    h, w = img_bgr.shape[:2]
    car_h = cy1 - cy0
    car_w = cx1 - cx0

    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    b_ch, g_ch, r_ch = cv2.split(img_bgr)
    min_ch = np.minimum(np.minimum(r_ch, g_ch), b_ch)
    max_ch = np.maximum(np.maximum(r_ch, g_ch), b_ch)
    ch_range = max_ch.astype(int) - min_ch.astype(int)

    # Geometry-based deep interior detection (color-independent)
    dist_from_edge = cv2.distanceTransform(
        (car_mask // 255).astype(np.uint8), cv2.DIST_L2, 5
    )
    depth_threshold = max(20, int(min(car_w, car_h) * 0.04))
    deep_interior = ((dist_from_edge > depth_threshold) & (car_mask > 0)).astype(np.uint8) * 255

    # --- Layer A: Seat heater icons in interior ---
    bright_interior = (
        (gray > 80) & (ch_range < 20) & (deep_interior > 0)
    ).astype(np.uint8) * 255

    white_interior = (
        (min_ch > 100) & (deep_interior > 0)
    ).astype(np.uint8) * 255

    seat_ui = cv2.bitwise_or(bright_interior, white_interior)

    k_close = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    k_dilate = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
    seat_mask = cv2.morphologyEx(seat_ui, cv2.MORPH_CLOSE, k_close)
    seat_mask = cv2.dilate(seat_mask, k_dilate, iterations=1)
    seat_mask = cv2.bitwise_and(seat_mask, car_mask)

    n_cc, labels, stats, cents = cv2.connectedComponentsWithStats(seat_mask, 8)
    seat_final = np.zeros_like(seat_mask)
    for i in range(1, n_cc):
        area = stats[i, cv2.CC_STAT_AREA]
        if area < 50:
            continue
        if area > 15000:
            continue
        cw = stats[i, cv2.CC_STAT_WIDTH]
        ch = stats[i, cv2.CC_STAT_HEIGHT]
        aspect = max(cw, ch) / (min(cw, ch) + 1)
        if aspect > 8:
            continue
        cy_rel = (cents[i][1] - cy0) / car_h
        if cy_rel < 0.20:
            continue
        if cy_rel > 0.75:
            continue
        seat_final[labels == i] = 255

    # --- Layer B: Status bar + back button in top zone ---
    top_zone_end = cy0 + int(car_h * 0.10)
    top_zone_mask = np.zeros_like(gray, dtype=np.uint8)
    top_zone_mask[max(0, cy0 - 30):top_zone_end, :] = 255

    status_ui = (
        (gray > 100) & (ch_range < 15) & (top_zone_mask > 0)
    ).astype(np.uint8) * 255

    btn_zone = np.zeros_like(gray, dtype=np.uint8)
    btn_zone[max(0, cy0 - 20):cy0 + int(car_h * 0.05), :int(w * 0.15)] = 255
    back_btn = (
        (gray > 120) & (ch_range < 15) & (btn_zone > 0)
    ).astype(np.uint8) * 255
    status_ui = cv2.bitwise_or(status_ui, back_btn)

    status_mask = cv2.dilate(
        status_ui, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
    )

    final_mask = cv2.bitwise_or(seat_final, status_mask)
    return cv2.inpaint(img_bgr, final_mask, inpaintRadius=12, flags=cv2.INPAINT_TELEA)


def process_controls_panel(img_path, target_size=CONTROLS_SIZE, verbose=False,
                           full_res=False):
    """Process the controls panel screenshot using OpenCV inpainting."""
    img = Image.open(str(img_path))
    arr = np.array(img)
    iw, ih = img.size
    bg_color = detect_background_color(arr)

    info = {
        "input_file": str(img_path),
        "input_size": [iw, ih],
        "car_bounds": None,
        "warnings": [],
        "success": False,
    }

    img_bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
    bg_uint8 = np.clip(bg_color, 0, 255).astype(np.uint8)
    car_mask = get_car_mask_filled(img_bgr, bg_uint8, threshold=12)

    status_bar_cutoff = int(ih * 0.05)
    bounds_mask = car_mask.copy()
    bounds_mask[:status_bar_cutoff, :] = 0
    mask_ys, mask_xs = np.where(bounds_mask > 0)
    if len(mask_ys) == 0:
        info["warnings"].append("Controls car detection failed")
        return None, info

    cx0, cy0 = int(mask_xs.min()), int(mask_ys.min())
    cx1, cy1 = int(mask_xs.max()), int(mask_ys.max())
    info["car_bounds"] = [cx0, cy0, cx1, cy1]

    if verbose:
        print(f"    Car bounds: ({cx0},{cy0})-({cx1},{cy1}) "
              f"= {cx1-cx0}x{cy1-cy0}"
              f"  (status bar cutoff: y<{status_bar_cutoff})")

    car_w = cx1 - cx0
    car_h = cy1 - cy0
    inpainted = inpaint_controls_ui(img_bgr, car_mask, cx0, cy0, car_w, car_h)

    padding_top = max(0, int(car_h * 0.02))
    padding_bot = max(0, int(car_h * 0.02))
    crop_y0 = max(0, cy0 - padding_top)
    crop_y1 = min(ih, cy1 + padding_bot)
    cropped_bgr = inpainted[crop_y0:crop_y1, :, :]

    crop_mask = car_mask[crop_y0:crop_y1, :]
    bg_bgr = bg_uint8[::-1]
    bg_float = bg_bgr.reshape(1, 1, 3).astype(np.float64)
    outside_diff = np.sqrt(
        np.sum((cropped_bgr.astype(np.float64) - bg_float) ** 2, axis=2)
    )
    replace_outside = (outside_diff > 10) & (crop_mask == 0)
    for c in range(3):
        cropped_bgr[:, :, c] = np.where(replace_outside,
                                         int(bg_bgr[c]),
                                         cropped_bgr[:, :, c])

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


def process_climate_panel(img_path, target_size=CLIMATE_SIZE, verbose=False,
                          full_res=False):
    """Process the climate panel screenshot using OpenCV inpainting."""
    img = Image.open(str(img_path))
    arr = np.array(img)
    iw, ih = img.size
    bg_color = detect_background_color(arr)

    info = {
        "input_file": str(img_path),
        "input_size": [iw, ih],
        "car_bounds": None,
        "warnings": [],
        "success": False,
    }

    bg_uint8 = np.clip(bg_color, 0, 255).astype(np.uint8)
    img_bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
    car_mask = get_car_mask_filled(img_bgr, bg_uint8, threshold=12)

    # -- Car bottom correction --
    bg_bgr = bg_uint8[::-1]
    raw_diff = np.sqrt(np.sum(
        (img_bgr.astype(np.float64) - bg_bgr.reshape(1, 1, 3).astype(np.float64)) ** 2,
        axis=2))
    raw_non_bg = (raw_diff > 12).astype(np.uint8)
    raw_row_cov = np.sum(raw_non_bg > 0, axis=1).astype(float) / iw

    car_bottom_cutoff = None
    mid_y = ih // 2
    for y in range(mid_y, ih - 5):
        if raw_row_cov[y] < 0.50 and raw_row_cov[y + 1] > 0.90:
            car_bottom_cutoff = y
            break
        if (raw_row_cov[y] < 0.50
                and y + 10 < ih
                and np.max(raw_row_cov[y + 1:y + 11]) > 0.90):
            car_bottom_cutoff = y
            break

    if car_bottom_cutoff is not None:
        car_mask[car_bottom_cutoff:, :] = 0
        if verbose:
            print(f"    Car bottom correction: zeroed mask below y={car_bottom_cutoff}")

    status_bar_cutoff = int(ih * 0.05)
    bounds_mask = car_mask.copy()
    bounds_mask[:status_bar_cutoff, :] = 0
    mask_ys, mask_xs = np.where(bounds_mask > 0)
    if len(mask_ys) == 0:
        info["warnings"].append("Climate car detection failed")
        return None, info

    cx0, cy0 = int(mask_xs.min()), int(mask_ys.min())
    cx1, cy1 = int(mask_xs.max()), int(mask_ys.max())
    info["car_bounds"] = [cx0, cy0, cx1, cy1]
    car_h = cy1 - cy0
    car_w = cx1 - cx0

    if verbose:
        print(f"    Car bounds: ({cx0},{cy0})-({cx1},{cy1}) "
              f"= {car_w}x{car_h}"
              f"  (status bar cutoff: y<{status_bar_cutoff})")

    inpainted = inpaint_climate_ui(img_bgr, car_mask, cx0, cy0, cx1, cy1)

    crop_start_y = cy0 + int(car_h * 0.03)
    car_bottom_pad = int(car_h * 0.02)
    crop_end_y = min(ih, cy1 + car_bottom_pad)

    cropped_bgr = inpainted[crop_start_y:crop_end_y, :, :]
    crop_h = crop_end_y - crop_start_y

    bg_bgr = bg_uint8[::-1]

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

    car_cx = (cx0 + cx1) / 2
    canvas_x0 = int(car_cx - canvas_w / 2)
    canvas_x1 = canvas_x0 + canvas_w
    if canvas_x0 < 0:
        canvas_x0 = 0
        canvas_x1 = min(iw, canvas_w)
    if canvas_x1 > iw:
        canvas_x1 = iw
        canvas_x0 = max(0, iw - canvas_w)

    actual_w = canvas_x1 - canvas_x0
    canvas = np.full((canvas_h, actual_w, 3), bg_bgr, dtype=np.uint8)
    paste_h = min(crop_h, canvas_h)
    region = cropped_bgr[:paste_h, canvas_x0:canvas_x1, :]
    canvas[:paste_h, :region.shape[1], :] = region

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
