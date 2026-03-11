"""Side-view processing: UI removal, crop, resize, background normalization."""

import cv2
import numpy as np
from PIL import Image

from .constants import BG_DISTANCE_THRESHOLD, SIDE_VIEW_SIZE
from .detection import (
    create_non_bg_mask,
    detect_background_color,
    find_car_bounds_sideview,
)


def remove_sideview_ui(cropped_img, bg_color):
    """Remove Tesla UI text/icons from the top of a cropped side-view image.

    Strategy: seed a car mask from chromatic (colored) pixels, then flood-fill
    into adjacent non-bg pixels to grow the car region. Anything NOT connected
    to the car body is UI (text, tab icons, battery indicators) -> replaced
    with pure bg. No blur is used, so no noise halos are created.

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
    # 1. Bottom of extended region -- car body enters from below
    # 2. Strongly chromatic pixels anywhere -- UI text is always achromatic
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

    # Normalize background to a single uniform colour.
    res_arr = np.array(result)
    rh, rw = res_arr.shape[:2]
    edge_w = max(1, rw // 10)
    edge_pixels = np.concatenate([
        res_arr[:, :edge_w, :3].reshape(-1, 3),
        res_arr[:, rw - edge_w:, :3].reshape(-1, 3),
    ], axis=0).astype(np.float64)
    dominant_bg = np.median(edge_pixels, axis=0).astype(np.uint8)
    res_rgb = res_arr[:, :, :3]
    bg_dist = np.sqrt(np.sum(
        (res_rgb.astype(np.float64) - dominant_bg.astype(np.float64)) ** 2,
        axis=2))
    # Only normalize border-connected bg pixels (not dark interior)
    candidate = (bg_dist < BG_DISTANCE_THRESHOLD).astype(np.uint8) * 255
    seed = np.zeros_like(candidate)
    seed[0, :] = candidate[0, :]
    seed[-1, :] = candidate[-1, :]
    seed[:, 0] = candidate[:, 0]
    seed[:, -1] = candidate[:, -1]
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    bg_mask = seed.copy()
    for _ in range(max(rh, rw)):
        grown = cv2.dilate(bg_mask, k, iterations=1)
        grown = cv2.bitwise_and(grown, candidate)
        if np.array_equal(grown, bg_mask):
            break
        bg_mask = grown
    bg_px = bg_mask > 0
    for c in range(3):
        res_arr[:, :, c] = np.where(bg_px, dominant_bg[c], res_arr[:, :, c])
    result = Image.fromarray(res_arr)

    if verbose:
        print(f"    Crop frame: ({cx0},{cy0})-({cx1},{cy1}) = {cx1-cx0}x{cy1-cy0}")
        out_size = result.size
        print(f"    Output: {out_size[0]}x{out_size[1]}" +
              (" (full-res)" if full_res else ""))

    info["success"] = True
    return result, info
