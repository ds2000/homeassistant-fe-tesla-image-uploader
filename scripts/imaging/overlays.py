"""Overlay generation: diff computation, door splitting, validation, combo states."""

from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from .constants import (
    COMBINED_PATTERNS_OFFCHARGE,
    COMBINED_PATTERNS_ONCHARGE,
    DIFF_THRESHOLD,
    OFFCHARGE_OVERLAYS,
    ONCHARGE_OVERLAYS,
)
from .detection import align_to_reference

# Directory containing pre-built overlay masks per model
_ASSETS_DIR = Path(__file__).parent / "assets"


def _load_overlay_mask(name: str, model: str, target_size: tuple[int, int],
                       mode: str = "offcharge") -> np.ndarray | None:
    """Load and scale a pre-built overlay mask for a given model.

    *name* is the overlay name: "frunk", "nf", "nr", "ff", "fr", "trunk",
    "all_doors", etc.  Tries ``{name}_mask_{model}_{mode}.png`` first,
    then ``{name}_mask_{model}.png``.

    Returns a uint8 alpha mask at *target_size* (w, h), or None if no
    mask exists for this model/mode combination.
    """
    if not model:
        return None
    for suffix in (f"_{mode}", ""):
        mask_path = _ASSETS_DIR / f"{name}_mask_{model}{suffix}.png"
        if mask_path.exists():
            mask_img = Image.open(str(mask_path)).convert("RGBA")
            mask_scaled = mask_img.resize(target_size, Image.Resampling.LANCZOS)
            alpha = np.array(mask_scaled)[:, :, 3]
            return (alpha > 128).astype(np.uint8) * 255
    return None


def _load_overlay_rgba(name: str, model: str, target_size: tuple[int, int],
                       mode: str = "offcharge") -> np.ndarray | None:
    """Load a pre-built overlay as full RGBA (pixels + mask).

    Like _load_overlay_mask but returns the complete RGBA array so that
    both the source pixels and the alpha shape are used from the asset.
    """
    if not model:
        return None
    for suffix in (f"_{mode}", ""):
        mask_path = _ASSETS_DIR / f"{name}_mask_{model}{suffix}.png"
        if mask_path.exists():
            mask_img = Image.open(str(mask_path)).convert("RGBA")
            mask_scaled = mask_img.resize(target_size, Image.Resampling.LANCZOS)
            arr = np.array(mask_scaled)
            # Binarize alpha
            arr[:, :, 3] = ((arr[:, :, 3] > 128).astype(np.uint8) * 255)
            return arr
    return None


def _load_frunk_mask(model, target_size, mode="offcharge"):
    """Load and scale the pre-built frunk mask for a given model.

    Backward-compatible wrapper around _load_overlay_mask.
    """
    return _load_overlay_mask("frunk", model, target_size, mode)


def _detect_cable_mask(state_f, base_f, kernel):
    """Detect green/teal charging cable pixels in either image."""
    def _is_green(rgb_f):
        return ((rgb_f[:, :, 1] > 80) &
                (rgb_f[:, :, 1] > rgb_f[:, :, 0] + 30) &
                (rgb_f[:, :, 1] > rgb_f[:, :, 2] + 15))

    cable = (_is_green(state_f) | _is_green(base_f)).astype(np.uint8) * 255
    return cv2.dilate(cable, kernel, iterations=3)


def compute_overlay(state_img, base_img, threshold=DIFF_THRESHOLD,
                    min_cc_area=80, remove_cable=False):
    """Compute an RGBA overlay from a state image vs base.

    Returns an RGBA PIL Image where changed pixels keep their color with
    alpha=255 and unchanged pixels are fully transparent.
    """
    s = np.array(state_img.convert("RGB")).astype(np.float64)
    b = np.array(base_img.convert("RGB")).astype(np.float64)
    diff = np.sqrt(np.sum((s - b) ** 2, axis=2))
    mask = (diff > threshold).astype(np.uint8) * 255

    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)

    if remove_cable:
        cable = _detect_cable_mask(s, b, kernel)
        mask[cable > 0] = 0

    n_cc, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    for i in range(1, n_cc):
        if stats[i, cv2.CC_STAT_AREA] < min_cc_area:
            mask[labels == i] = 0

    # Remove UI text artifacts from oncharge overlays BEFORE hull fill
    text_like = None
    text_region = None
    if remove_cable:
        h_img = mask.shape[0]
        top_cutoff = int(h_img * 0.35)
        state_u8 = s.astype(np.uint8)
        ch_range = (np.max(state_u8, axis=2).astype(int) -
                    np.min(state_u8, axis=2).astype(int))
        brightness = np.mean(state_u8, axis=2)
        text_like = (ch_range < 25) & (brightness > 40)
        text_region = np.zeros_like(mask)
        text_region[:top_cutoff] = 255
        mask[(text_like & (text_region > 0) & (mask > 0))] = 0

    # Convex-hull gap fill
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_SIMPLE)
    hull_mask = np.zeros_like(mask)
    for c in contours:
        if cv2.contourArea(c) < min_cc_area:
            continue
        hull = cv2.convexHull(c)
        cv2.drawContours(hull_mask, [hull], 0, 255, -1)
    soft_change = (diff > 1).astype(np.uint8) * 255
    if remove_cable and text_like is not None:
        soft_change[(text_like & (text_region > 0))] = 0
    hull_filled = hull_mask & soft_change
    mask = np.maximum(mask, hull_filled)

    rgba = np.array(state_img.convert("RGBA"))
    rgba[:, :, 3] = mask
    return Image.fromarray(rgba)


# Keep backwards-compatible name used in pipeline.py
_compute_overlay = compute_overlay


def split_combined_doors(combined_img, base_img, mode="offcharge",
                         threshold=DIFF_THRESHOLD, min_cc_area=80,
                         verbose=False):
    """Split a combined-door image into near-side and far-side overlays.

    Returns:
        (near_overlay, far_overlay) -- each a PIL RGBA Image or None.
    """
    s = np.array(combined_img.convert("RGB")).astype(np.float64)
    b = np.array(base_img.convert("RGB")).astype(np.float64)
    diff = np.sqrt(np.sum((s - b) ** 2, axis=2))
    mask = (diff > threshold).astype(np.uint8) * 255

    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)

    if mode == "oncharge":
        cable = _detect_cable_mask(s, b, kernel)
        mask[cable > 0] = 0

    n_cc, labels, stats, centroids = cv2.connectedComponentsWithStats(mask, 8)
    for i in range(1, n_cc):
        if stats[i, cv2.CC_STAT_AREA] < min_cc_area:
            mask[labels == i] = 0

    h, w = mask.shape

    # Find split x-coordinate via valley in x-projection
    col_sums = np.sum(mask > 0, axis=0).astype(np.float64)
    smooth_k = max(3, w // 50)
    if smooth_k % 2 == 0:
        smooth_k += 1
    col_smooth = np.convolve(col_sums, np.ones(smooth_k) / smooth_k, mode="same")

    active_cols = np.where(col_smooth > 0.5)[0]
    if len(active_cols) < 2:
        if verbose:
            print("    split_combined_doors: too few active columns")
        return None, None

    left_edge = int(active_cols[0])
    right_edge = int(active_cols[-1])

    search_start = left_edge + int((right_edge - left_edge) * 0.2)
    search_end = left_edge + int((right_edge - left_edge) * 0.8)
    search_zone = col_smooth[search_start:search_end]

    split_x = None
    if len(search_zone) > 0:
        valley_idx = int(np.argmin(search_zone))
        valley_val = search_zone[valley_idx]
        peak_val = np.max(col_smooth[left_edge:right_edge])
        if peak_val > 0 and valley_val < peak_val * 0.3:
            split_x = search_start + valley_idx
            if verbose:
                print(f"    Valley split at x={split_x} "
                      f"(valley={valley_val:.1f}, peak={peak_val:.1f})")

    # Fallback: CC centroid gap analysis
    if split_x is None:
        n_cc2, labels2, stats2, centroids2 = cv2.connectedComponentsWithStats(mask, 8)
        cx_list = []
        for i in range(1, n_cc2):
            if stats2[i, cv2.CC_STAT_AREA] >= min_cc_area:
                cx_list.append(centroids2[i][0])
        if len(cx_list) >= 2:
            cx_sorted = sorted(cx_list)
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
            split_x = w // 2
            if verbose:
                print(f"    Cannot find valley or CC gap -- "
                      f"splitting at midpoint x={split_x}")

    # Sanity check: outer quartile -> correct to center
    if split_x < w * 0.25 or split_x > w * 0.75:
        if verbose:
            print(f"    Split x={split_x} in outer quartile -- "
                  f"correcting to image center x={w // 2}")
        split_x = w // 2

    # Assign CCs to left/right by centroid
    n_cc3, labels3, stats3, centroids3 = cv2.connectedComponentsWithStats(mask, 8)
    left_mask = np.zeros_like(mask)
    right_mask = np.zeros_like(mask)
    for i in range(1, n_cc3):
        if stats3[i, cv2.CC_STAT_AREA] < min_cc_area:
            continue
        cc_cx = centroids3[i][0]
        if cc_cx < split_x:
            left_mask[labels3 == i] = 255
        else:
            right_mask[labels3 == i] = 255

    if mode == "oncharge":
        near_mask, far_mask = left_mask, right_mask
    else:
        near_mask, far_mask = right_mask, left_mask

    # Build side clips for hull containment
    left_side = np.zeros((h, w), dtype=np.uint8)
    left_side[:, :split_x] = 255
    right_side = np.zeros((h, w), dtype=np.uint8)
    right_side[:, split_x:] = 255
    if mode == "oncharge":
        near_side_clip, far_side_clip = left_side, right_side
    else:
        near_side_clip, far_side_clip = right_side, left_side

    results = []
    for half_mask, side_clip in [(near_mask, near_side_clip),
                                  (far_mask, far_side_clip)]:
        n_px = int(np.sum(half_mask > 0))
        if n_px < min_cc_area:
            results.append(None)
            continue

        contours, _ = cv2.findContours(half_mask, cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)
        hull_mask = np.zeros_like(half_mask)
        for c in contours:
            if cv2.contourArea(c) < min_cc_area:
                continue
            hull = cv2.convexHull(c)
            cv2.drawContours(hull_mask, [hull], 0, 255, -1)
        hull_mask = hull_mask & side_clip
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


def validate_overlays(output_dir, mode="offcharge"):
    """Validate overlay integrity: check for cross-contamination and layering."""
    output_dir = Path(output_dir)
    prefix = "oncharge-" if mode == "oncharge" else ""
    errors = []

    base_path = output_dir / f"{prefix}base.png"
    if not base_path.exists():
        return [f"Base image not found: {base_path}"]
    base = np.array(Image.open(str(base_path)).convert("RGBA"))

    door_names = ["nf", "nr", "ff", "fr"]
    door_overlays = {}
    for name in door_names:
        path = output_dir / f"{prefix}{name}-overlay.png"
        if path.exists():
            arr = np.array(Image.open(str(path)).convert("RGBA"))
            door_overlays[name] = arr[:, :, 3] > 0

    near_doors = ["nf", "nr"]
    far_doors = ["ff", "fr"]

    # 1. Cross-side contamination
    for nd in near_doors:
        for fd in far_doors:
            if nd in door_overlays and fd in door_overlays:
                overlap = door_overlays[nd] & door_overlays[fd]
                n = int(np.sum(overlap))
                if n > 0:
                    errors.append(
                        f"CROSS-SIDE: {nd}-overlay has {n} px overlapping "
                        f"with {fd}-overlay")

    # 2. Combined overlays
    combined_checks = {
        "nf-nr-combined": (near_doors, far_doors),
        "ff-fr-combined": (far_doors, near_doors),
    }
    for cname, (own_doors, other_doors) in combined_checks.items():
        cpath = output_dir / f"{prefix}{cname}-overlay.png"
        if not cpath.exists():
            continue
        carr = np.array(Image.open(str(cpath)).convert("RGBA"))
        cmask = carr[:, :, 3] > 0
        for od in other_doors:
            if od in door_overlays:
                overlap = cmask & door_overlays[od]
                n = int(np.sum(overlap))
                if n > 0:
                    errors.append(
                        f"LEAKAGE: {cname}-overlay has {n} px from "
                        f"{od}-overlay")

    # 3. Position checks
    h, w = base.shape[:2]
    mid_x = w // 2
    for name in door_names:
        if name not in door_overlays:
            continue
        mask = door_overlays[name]
        ys, xs = np.where(mask)
        if len(xs) == 0:
            continue
        cx = float(np.mean(xs))
        if mode == "offcharge":
            is_near = name in near_doors
            if is_near and cx < mid_x * 0.6:
                errors.append(
                    f"POSITION: {name}-overlay centroid x={cx:.0f} is on "
                    f"the far side (expected near/right, mid={mid_x})")
            elif not is_near and cx > mid_x * 1.4:
                errors.append(
                    f"POSITION: {name}-overlay centroid x={cx:.0f} is on "
                    f"the near side (expected far/left, mid={mid_x})")
        else:
            is_near = name in near_doors
            if is_near and cx > mid_x * 1.4:
                errors.append(
                    f"POSITION: {name}-overlay centroid x={cx:.0f} is on "
                    f"the far side (expected near/left, mid={mid_x})")
            elif not is_near and cx < mid_x * 0.6:
                errors.append(
                    f"POSITION: {name}-overlay centroid x={cx:.0f} is on "
                    f"the near side (expected far/right, mid={mid_x})")

    # 4. Non-door overlap checks
    # Frunk's bonnet expansion naturally overlaps with door areas (frunk
    # renders above doors in z-order), so use a higher threshold for it.
    overlap_thresholds = {"frunk": 5000, "chargeport": 1000}
    for extra in ["frunk", "chargeport"]:
        epath = output_dir / f"{prefix}{extra}-overlay.png"
        if not epath.exists():
            continue
        earr = np.array(Image.open(str(epath)).convert("RGBA"))
        emask = earr[:, :, 3] > 0
        thresh = overlap_thresholds[extra]
        for dname in door_names:
            if dname in door_overlays:
                overlap = emask & door_overlays[dname]
                n = int(np.sum(overlap))
                if n > thresh:
                    errors.append(
                        f"OVERLAP: {extra}-overlay has {n} px overlapping "
                        f"with {dname}-overlay")

    return errors


def generate_overlays(processed_dir, output_dir, mode="offcharge",
                      verbose=False, reference_dir=None, model=None):
    """Export transparent overlay PNGs for runtime compositing."""
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

    # Reference-based alignment
    _ref_shift = None
    if reference_dir:
        ref_base = Path(reference_dir) / f"{prefix}base.png"
        if ref_base.exists():
            ref_img = Image.open(str(ref_base)).convert("RGBA")
            new_arr = np.array(base_img.convert("RGB"))
            ref_arr = np.array(ref_img.convert("RGB"))
            rh, rw = ref_arr.shape[:2]
            nh, nw = new_arr.shape[:2]
            new_resized = cv2.resize(new_arr, (rw, rh)) if (nh, nw) != (rh, rw) else new_arr
            new_gray = cv2.cvtColor(new_resized, cv2.COLOR_RGB2GRAY).astype(np.float64)
            ref_gray = cv2.cvtColor(ref_arr, cv2.COLOR_RGB2GRAY).astype(np.float64)
            hann = cv2.createHanningWindow((rw, rh), cv2.CV_64F)
            try:
                (dx, dy), _ = cv2.phaseCorrelate(ref_gray, new_gray, hann)
                if abs(dx) < rw * 0.05 and abs(dy) < rh * 0.05:
                    if abs(dx) > 0.3 or abs(dy) > 0.3:
                        _ref_shift = (dx, dy)
                        if verbose:
                            print(f"  Reference alignment: dx={dx:.2f}, dy={dy:.2f}")
            except cv2.error:
                pass

    def _apply_ref_shift(img):
        if _ref_shift is None:
            return img
        dx, dy = _ref_shift
        arr = np.array(img)
        h, w = arr.shape[:2]
        M = np.float32([[1, 0, -dx], [0, 1, -dy]])
        if arr.ndim == 3 and arr.shape[2] == 4:
            aligned = np.stack([
                cv2.warpAffine(arr[:, :, c], M, (w, h),
                               borderMode=cv2.BORDER_REPLICATE)
                for c in range(4)
            ], axis=2)
        else:
            aligned = cv2.warpAffine(arr, M, (w, h),
                                     borderMode=cv2.BORDER_REPLICATE)
        return Image.fromarray(aligned)

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

    base_img = _apply_ref_shift(base_img)

    base_out = f"{prefix}base.png"
    base_img.save(str(output_dir / base_out), "PNG")
    print(f"  Saved {base_out}")

    # Trunk overlay
    trunk_out = f"{prefix}trunk-open.png"
    trunk_overlay_out = f"{prefix}trunk-overlay.png"
    if trunk_path.exists():
        trunk_img_raw = Image.open(str(trunk_path)).convert("RGBA")
        if mode == "oncharge":
            trunk_img_raw = _clean_stray_green(trunk_img_raw)
        trunk_img_raw = _apply_ref_shift(trunk_img_raw)
        trunk_img_raw.save(str(output_dir / trunk_out), "PNG")
        trunk_overlay = compute_overlay(trunk_img_raw, base_img,
                                        remove_cable=(mode == "oncharge"))
        trunk_overlay.save(str(output_dir / trunk_overlay_out), "PNG")
        print(f"  Saved {trunk_out} + {trunk_overlay_out}")

    # Cable overlay for oncharge.
    # Try pre-built RGBA mask first; fall back to green cable detection.
    if mode == "oncharge":
        base_arr = np.array(base_img)
        cable_saved = False
        if model:
            base_sz = (base_arr.shape[1], base_arr.shape[0])
            cable_rgba = _load_overlay_rgba("cable", model, base_sz,
                                            mode=mode)
            if cable_rgba is not None:
                Image.fromarray(cable_rgba).save(
                    str(output_dir / "oncharge-cable-overlay.png"), "PNG")
                print(f"  Saved oncharge-cable-overlay.png (from mask)")
                cable_saved = True
        if not cable_saved:
            rgb_f = base_arr[:, :, :3].astype(np.float64)
            g = rgb_f[:, :, 1]
            r = rgb_f[:, :, 0]
            b = rgb_f[:, :, 2]
            cable_mask = ((g > 80) & (g > r + 30) & (g > b + 15))
            if np.any(cable_mask):
                cable_rgba = np.zeros_like(base_arr)
                cable_rgba[cable_mask] = base_arr[cable_mask]
                cable_img = Image.fromarray(cable_rgba)
                cable_img.save(
                    str(output_dir / "oncharge-cable-overlay.png"), "PNG")
                print(f"  Saved oncharge-cable-overlay.png")

    overlays_list = (ONCHARGE_OVERLAYS if mode == "oncharge"
                     else OFFCHARGE_OVERLAYS)

    # Individual overlays
    count = 0
    for name in overlays_list:
        img_path = processed_dir / f"{name}-open.png"
        if not img_path.exists():
            if verbose:
                print(f"  Skipping {name}: {img_path} not found")
            continue
        state_img = _apply_ref_shift(Image.open(str(img_path)).convert("RGBA"))
        cc_area = 10 if name in ("chargeport", "frunk") else 80
        overlay = compute_overlay(state_img, base_img,
                                  min_cc_area=cc_area,
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

    # Apply pre-built overlay masks for doors and trunk (same pattern as
    # frunk mask — replaces diff-based alpha with the authoritative shape).
    # Track which overlays used masks so we skip heuristic cleanup for them.
    _masked_overlays = set()
    if model:
        _mask_names = {"nf", "nr", "ff", "fr", "chargeport"}
        for ov_name in overlays_list:
            if ov_name not in _mask_names:
                continue
            ov_path = output_dir / f"{prefix}{ov_name}-overlay.png"
            # FF/NF/FR use RGBA from asset (correct pre-rendered pixels).
            # Can create the overlay even if the diff pipeline didn't.
            if ov_name in ("ff", "nf", "fr"):
                # Need target size from base image
                base_path = output_dir / f"{prefix}base.png"
                if not base_path.exists():
                    base_path = output_dir / "base.png"
                if base_path.exists():
                    base_sz = Image.open(str(base_path)).size
                    ov_rgba = _load_overlay_rgba(
                        ov_name, model, base_sz, mode=mode)
                    if ov_rgba is not None:
                        Image.fromarray(ov_rgba).save(
                            str(ov_path), "PNG")
                        _masked_overlays.add(ov_name)
                        count += (0 if ov_path.exists() else 1)
                        if verbose:
                            n = int(np.sum(ov_rgba[:, :, 3] > 0))
                            print(f"  {prefix}{ov_name}-overlay.png: "
                                  f"applied {model} mask -> {n} opaque px")
                        continue
            if not ov_path.exists():
                continue
            src_path = processed_dir / f"{prefix}{ov_name}-open.png"
            if not src_path.exists():
                src_path = processed_dir / f"{ov_name}-open.png"
            if not src_path.exists():
                continue
            src_img = _apply_ref_shift(
                Image.open(str(src_path)).convert("RGBA"))
            h_o, w_o = np.array(src_img).shape[:2]
            ov_mask = _load_overlay_mask(ov_name, model, (w_o, h_o),
                                         mode=mode)
            if ov_mask is not None:
                ov_arr = np.array(src_img)
                ov_arr[:, :, 3] = ov_mask
                Image.fromarray(ov_arr).save(str(ov_path), "PNG")
                _masked_overlays.add(ov_name)
                if verbose:
                    n = int(np.sum(ov_mask > 0))
                    print(f"  {prefix}{ov_name}-overlay.png: applied "
                          f"{model} mask -> {n} opaque px")

        # Trunk mask — oncharge uses RGBA (full pre-rendered trunk);
        # offcharge uses alpha-only union with diff (lid mask + cavity fill).
        trunk_ov_path = output_dir / f"{prefix}trunk-overlay.png"
        if trunk_ov_path.exists():
            if mode == "oncharge":
                base_path2 = output_dir / f"{prefix}base.png"
                if not base_path2.exists():
                    base_path2 = output_dir / "base.png"
                if base_path2.exists():
                    base_sz2 = Image.open(str(base_path2)).size
                    t_rgba = _load_overlay_rgba("trunk", model, base_sz2,
                                                mode=mode)
                    if t_rgba is not None:
                        Image.fromarray(t_rgba).save(
                            str(trunk_ov_path), "PNG")
                        _masked_overlays.add("trunk")
                        if verbose:
                            n = int(np.sum(t_rgba[:, :, 3] > 0))
                            print(f"  {prefix}trunk-overlay.png: applied "
                                  f"{model} RGBA mask -> {n} opaque px")
            # Fall back to alpha-only union if no RGBA mask
            if "trunk" not in _masked_overlays:
                trunk_src = processed_dir / f"{prefix}trunk-open.png"
                if not trunk_src.exists():
                    trunk_src = processed_dir / "trunk-open.png"
                if trunk_src.exists():
                    t_src = _apply_ref_shift(
                        Image.open(str(trunk_src)).convert("RGBA"))
                    h_o, w_o = np.array(t_src).shape[:2]
                    t_mask = _load_overlay_mask("trunk", model,
                                                (w_o, h_o), mode=mode)
                    if t_mask is not None:
                        existing = np.array(
                            Image.open(str(trunk_ov_path)).convert("RGBA"))
                        t_arr = np.array(t_src)
                        t_arr[:, :, 3] = np.maximum(existing[:, :, 3], t_mask)
                        Image.fromarray(t_arr).save(str(trunk_ov_path), "PNG")
                        # Don't add to _masked_overlays — let scanline fill
                        # run to fill the trunk cavity gaps below the lid
                        if verbose:
                            n = int(np.sum(t_mask > 0))
                        print(f"  {prefix}trunk-overlay.png: applied "
                              f"{model} mask -> {n} opaque px")

    # Save pre-expansion trunk alpha for frunk cleanup (the expanded trunk
    # includes cavity pixels that overlap with frunk area; only the original
    # lid+diff area should be subtracted from frunk).
    _trunk_pre_expand_alpha = None
    trunk_overlay_path = output_dir / f"{prefix}trunk-overlay.png"
    if trunk_overlay_path.exists():
        _trunk_pre_expand_alpha = np.array(
            Image.open(str(trunk_overlay_path)).convert("RGBA"))[:, :, 3].copy()

    # Fill interior holes in trunk overlay (dark glass vs dark body → below
    # diff threshold, leaving holes in the alpha mask).
    # Skip when RGBA trunk mask was applied — it's authoritative.
    if trunk_overlay_path.exists() and "trunk" not in _masked_overlays:
        t_arr = np.array(Image.open(str(trunk_overlay_path)))
        t_alpha = t_arr[:, :, 3]
        if np.any(t_alpha > 0):
            h_t, w_t = t_alpha.shape
            # Compute low-threshold diff to capture the dark trunk cavity
            trunk_src_path = processed_dir / f"{prefix}trunk-open.png"
            base_src_path = processed_dir / f"{prefix}base.png"
            if not base_src_path.exists():
                base_src_path = processed_dir / "base.png"
            soft_diff = None
            if trunk_src_path.exists() and base_src_path.exists():
                t_rgb = np.array(Image.open(
                    str(trunk_src_path)).convert("RGB")).astype(float)
                b_rgb = np.array(Image.open(
                    str(base_src_path)).convert("RGB")).astype(float)
                pixel_diff = np.sqrt(np.sum((t_rgb - b_rgb) ** 2, axis=2))
                soft_diff = (pixel_diff > 1).astype(np.uint8) * 255

            # Bridge lid mask down to trunk cavity using soft diff.
            # The lid is at the top; the cavity (dark opening) is below.
            # Dilate the existing alpha vertically to bridge the gap,
            # then intersect with soft diff to keep only real changes.
            trunk_fill = t_alpha.copy()
            if soft_diff is not None:
                # Vertical dilation to bridge lid → cavity gap
                vk = cv2.getStructuringElement(
                    cv2.MORPH_RECT, (1, h_t // 2))
                bridged = cv2.dilate(t_alpha, vk)
                # Intersect with soft diff to keep only actual changes
                trunk_fill = np.maximum(trunk_fill, bridged & soft_diff)

            # Scanline fill remaining interior gaps
            for y_row in range(h_t):
                xs_row = np.where(trunk_fill[y_row, :] > 0)[0]
                if len(xs_row) >= 2:
                    trunk_fill[y_row, xs_row.min():xs_row.max() + 1] = 255
            for x_col in range(w_t):
                ys_col = np.where(trunk_fill[:, x_col] > 0)[0]
                if len(ys_col) >= 2:
                    trunk_fill[ys_col.min():ys_col.max() + 1, x_col] = 255

            fill_px = (trunk_fill > 0) & (t_alpha == 0)
            n_holes = int(np.sum(fill_px))
            if n_holes > 0:
                trunk_src = None
                if trunk_src_path.exists():
                    trunk_src = np.array(
                        Image.open(str(trunk_src_path)).convert("RGBA"))
                    t_arr[fill_px, :3] = trunk_src[fill_px, :3]
                t_arr[:, :, 3] = np.maximum(t_alpha, trunk_fill)
                Image.fromarray(t_arr).save(str(trunk_overlay_path), "PNG")
                if verbose:
                    total_t = int(np.sum(t_arr[:, :, 3] > 0))
                    print(f"  {prefix}trunk-overlay.png: filled {n_holes} "
                          f"glass gap px -> {total_t}")

    # Apply pre-built frunk mask if available for this model.
    # The mask defines the exact frunk shape (raised lid + bonnet panels),
    # replacing the diff-based alpha which misses low-contrast bonnet areas.
    frunk_overlay_path = output_dir / f"{prefix}frunk-overlay.png"
    if model and frunk_overlay_path.exists():
        frunk_src_path = processed_dir / f"{prefix}frunk-open.png"
        if not frunk_src_path.exists():
            frunk_src_path = processed_dir / "frunk-open.png"
        if frunk_src_path.exists():
            frunk_src = _apply_ref_shift(
                Image.open(str(frunk_src_path)).convert("RGBA"))
            h_o, w_o = np.array(frunk_src).shape[:2]
            frunk_mask = _load_frunk_mask(model, (w_o, h_o), mode=mode)
            if frunk_mask is not None:
                frunk_arr = np.array(frunk_src)
                frunk_arr[:, :, 3] = frunk_mask
                Image.fromarray(frunk_arr).save(str(frunk_overlay_path), "PNG")
                if verbose:
                    n = int(np.sum(frunk_mask > 0))
                    print(f"  {prefix}frunk-overlay.png: applied {model} "
                          f"frunk mask -> {n} opaque px")

    # Clean frunk: subtract trunk pixels (using pre-expansion alpha to
    # avoid the expanded cavity eating into frunk territory)
    if frunk_overlay_path.exists() and trunk_overlay_path.exists():
        frunk_arr = np.array(Image.open(str(frunk_overlay_path)))
        if _trunk_pre_expand_alpha is not None:
            trunk_opaque = _trunk_pre_expand_alpha > 0
        else:
            trunk_arr = np.array(Image.open(str(trunk_overlay_path)))
            trunk_opaque = trunk_arr[:, :, 3] > 0
        frunk_arr[trunk_opaque, 3] = 0
        Image.fromarray(frunk_arr).save(str(frunk_overlay_path), "PNG")
        n = int(np.sum(frunk_arr[:, :, 3] > 0))
        if verbose:
            print(f"  {prefix}frunk-overlay.png: cleaned trunk leak -> {n} opaque px")

    # Clean frunk overlay: remove noise, fill interior holes, subtract trunk.
    if frunk_overlay_path.exists():
        frunk_arr = np.array(Image.open(str(frunk_overlay_path)))
        frunk_alpha = frunk_arr[:, :, 3]
        if np.any(frunk_alpha > 0):
            before_clean = int(np.sum(frunk_alpha > 0))
            h_f, w_f = frunk_alpha.shape

            # 1. Remove small noise CCs (keep only components > 1% of
            #    the largest CC area).
            n_cc, labels, stats, _ = cv2.connectedComponentsWithStats(
                frunk_alpha, 8)
            if n_cc > 2:
                areas = stats[1:, cv2.CC_STAT_AREA]
                largest_area = int(areas.max())
                min_area = max(50, int(largest_area * 0.01))
                for i in range(1, n_cc):
                    if stats[i, cv2.CC_STAT_AREA] < min_area:
                        frunk_alpha[labels == i] = 0

            # 2. Fill interior holes via flood-fill from corners.
            #    Any transparent pixel not reachable from the border is
            #    an interior hole that should be opaque.
            border_fill = frunk_alpha.copy()
            flood_mask = np.zeros((h_f + 2, w_f + 2), np.uint8)
            cv2.floodFill(border_fill, flood_mask, (0, 0), 255)
            interior_holes = (border_fill == 0)
            n_holes = int(np.sum(interior_holes))
            if n_holes > 0:
                # Copy RGB from frunk-open source for hole pixels
                frunk_src_path = processed_dir / "frunk-open.png"
                if frunk_src_path.exists():
                    frunk_src = np.array(
                        Image.open(str(frunk_src_path)).convert("RGBA"))
                    frunk_arr[interior_holes, :3] = frunk_src[
                        interior_holes, :3]
                frunk_alpha[interior_holes] = 255

            # 3. Subtract trunk (may overlap at hinge area).
            # Use pre-expansion alpha to avoid cavity eating frunk.
            if _trunk_pre_expand_alpha is not None:
                frunk_alpha[_trunk_pre_expand_alpha > 0] = 0
            elif trunk_overlay_path.exists():
                trunk_a = np.array(
                    Image.open(str(trunk_overlay_path)))[:, :, 3]
                frunk_alpha[trunk_a > 0] = 0

            frunk_arr[:, :, 3] = frunk_alpha
            after_clean = int(np.sum(frunk_alpha > 0))
            if after_clean != before_clean:
                Image.fromarray(frunk_arr).save(
                    str(frunk_overlay_path), "PNG")
                if verbose:
                    print(f"  {prefix}frunk-overlay.png: cleaned "
                          f"({before_clean} -> {after_clean} px, "
                          f"{n_holes} hole px filled)")

    # Clean door overlays: subtract frunk and trunk from doors.
    # Frunk renders above doors in z-order, so overlapping pixels are
    # redundant and would double-render.  Trunk renders below doors,
    # but door overlays contain CLOSED-trunk pixels that would
    # overwrite the open trunk when both are active.
    if frunk_overlay_path.exists():
        frunk_mask = np.array(Image.open(str(frunk_overlay_path)))[:, :, 3] > 0
        for name in overlays_list:
            if name in ("chargeport", "frunk"):
                continue
            if name in _masked_overlays:
                continue
            door_path = output_dir / f"{prefix}{name}-overlay.png"
            if not door_path.exists():
                continue
            clip_mask = frunk_mask
            door_arr = np.array(Image.open(str(door_path)))
            before = int(np.sum(door_arr[:, :, 3] > 0))
            door_arr[clip_mask, 3] = 0
            after = int(np.sum(door_arr[:, :, 3] > 0))
            if before != after:
                Image.fromarray(door_arr).save(str(door_path), "PNG")
                if verbose:
                    print(f"  {prefix}{name}-overlay.png: cleaned frunk/trunk leak "
                          f"({before - after} px removed)")

    # NOTE: trunk-vs-door layering (trunk shows closed when doors are
    # open) must be handled by the card's z-order, not by clipping door
    # overlays — clipping destroys the individual door overlays.

    # Fill interior gaps in door overlays (same dark-glass issue as trunk).
    # Skip frunk/chargeport — their shapes are concave and scanline fill
    # would massively inflate them.
    # Skip overlays with pre-built masks — their shape is authoritative.
    for name in overlays_list:
        if name in ("frunk", "chargeport"):
            continue
        if name in _masked_overlays:
            continue
        door_path = output_dir / f"{prefix}{name}-overlay.png"
        if not door_path.exists():
            continue
        d_arr = np.array(Image.open(str(door_path)))
        d_alpha = d_arr[:, :, 3]
        if np.sum(d_alpha > 0) < 10:
            continue
        h_d, w_d = d_alpha.shape
        door_fill = d_alpha.copy()
        for y_row in range(h_d):
            xs_row = np.where(d_alpha[y_row, :] > 0)[0]
            if len(xs_row) >= 2:
                door_fill[y_row, xs_row.min():xs_row.max() + 1] = 255
        for x_col in range(w_d):
            ys_col = np.where(d_alpha[:, x_col] > 0)[0]
            if len(ys_col) >= 2:
                door_fill[ys_col.min():ys_col.max() + 1, x_col] = 255
        fill_px = (door_fill > 0) & (d_alpha == 0)
        n_fill = int(np.sum(fill_px))
        if n_fill > 0:
            # Copy RGB from the door-open source image
            src_path = processed_dir / f"{prefix}{name.replace('-overlay', '')}-open.png"
            if not src_path.exists():
                src_path = processed_dir / f"{name}-open.png"
            if src_path.exists():
                d_src = np.array(Image.open(str(src_path)).convert("RGBA"))
                d_arr[fill_px, :3] = d_src[fill_px, :3]
            d_arr[:, :, 3] = door_fill
            Image.fromarray(d_arr).save(str(door_path), "PNG")
            if verbose:
                total_d = int(np.sum(d_arr[:, :, 3] > 0))
                print(f"  {prefix}{name}-overlay.png: filled {n_fill} "
                      f"gap px -> {total_d}")

    # Clip frunk away from far-side door areas.  Frunk renders above
    # far-doors in z-order, so any overlap makes the door invisible.
    # Must run after gap-fill (which can re-inflate the frunk via
    # scanline fill into far-side door territory).
    # Skip when pre-built masks define the shapes — masks are
    # authoritative and the dilated door buffer would eat into the lid.
    _used_frunk_mask = ("frunk" in _masked_overlays or (model and any(
        (_ASSETS_DIR / f"frunk_mask_{model}{s}.png").exists()
        for s in (f"_{mode}", ""))))
    far_doors_clip = {"ff", "fr"}
    if frunk_overlay_path.exists() and not _used_frunk_mask:
        frunk_clip_arr = np.array(Image.open(str(frunk_overlay_path)))
        frunk_clip_alpha = frunk_clip_arr[:, :, 3]
        clipped_total = 0
        for fd_name in far_doors_clip:
            fd_path = output_dir / f"{prefix}{fd_name}-overlay.png"
            if not fd_path.exists():
                continue
            fd_alpha = np.array(Image.open(str(fd_path)))[:, :, 3]
            # Dilate door mask to create buffer so door edge stays visible
            buf_k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (11, 11))
            fd_buf = cv2.dilate(fd_alpha, buf_k)
            clipped = int(np.sum((frunk_clip_alpha > 0) & (fd_buf > 0)))
            frunk_clip_alpha[fd_buf > 0] = 0
            clipped_total += clipped
        if clipped_total > 0:
            frunk_clip_arr[:, :, 3] = frunk_clip_alpha
            Image.fromarray(frunk_clip_arr).save(
                str(frunk_overlay_path), "PNG")
            if verbose:
                print(f"  {prefix}frunk-overlay.png: clipped {clipped_total} "
                      f"px from far-door areas")

    # Combined overlays
    combined_patterns = (COMBINED_PATTERNS_ONCHARGE if mode == "oncharge"
                         else COMBINED_PATTERNS_OFFCHARGE)
    for cname, (_stems, constituents) in combined_patterns.items():
        cpath = processed_dir / f"{cname}.png"
        if not cpath.exists():
            continue
        cimg = _apply_ref_shift(Image.open(str(cpath)).convert("RGBA"))
        overlay = compute_overlay(cimg, base_img,
                                  remove_cable=(mode == "oncharge"))
        out_name = f"{prefix}{cname}-overlay.png"
        out_path = output_dir / out_name
        overlay.save(str(out_path), "PNG")
        count += 1
        if verbose:
            arr = np.array(overlay)
            n = int(np.sum(arr[:, :, 3] > 0))
            print(f"  {out_name}: {n} opaque px")

    # Fill concavities + remove cross-side leakage in combined overlays
    for cname, (_stems, constituents) in combined_patterns.items():
        c_overlay_path = output_dir / f"{prefix}{cname}-overlay.png"
        if not c_overlay_path.exists():
            continue
        c_arr = np.array(Image.open(str(c_overlay_path)))
        c_alpha = c_arr[:, :, 3]
        modified = False

        if np.any(c_alpha > 0):
            # Scanline fill interior gaps
            h_c, w_c = c_alpha.shape
            scan_filled = c_alpha.copy()
            for y_row in range(h_c):
                xs_row = np.where(c_alpha[y_row, :] > 0)[0]
                if len(xs_row) >= 2:
                    scan_filled[y_row, xs_row.min():xs_row.max() + 1] = 255
            # Morph close to bridge remaining gaps
            close_k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (35, 35))
            closed = cv2.morphologyEx(scan_filled, cv2.MORPH_CLOSE, close_k)
            new_alpha = cv2.GaussianBlur(closed, (7, 7), 0)
            new_alpha = (new_alpha > 128).astype(np.uint8) * 255
            before_fill = int(np.sum(c_alpha > 0))
            after_fill = int(np.sum(new_alpha > 0))
            if after_fill > before_fill:
                c_alpha = new_alpha
                modified = True
                if verbose:
                    print(f"  {prefix}{cname}-overlay.png: filled concavity "
                          f"(+{after_fill - before_fill} px -> {after_fill})")

        # Only subtract opposite-side DOOR overlays (not frunk/trunk/chargeport)
        door_names_set = {"nf", "nr", "ff", "fr"}
        opposite = [n for n in door_names_set if n not in constituents]
        for opp_name in opposite:
            opp_path = output_dir / f"{prefix}{opp_name}-overlay.png"
            if not opp_path.exists():
                continue
            opp_arr = np.array(Image.open(str(opp_path)))
            opp_mask = opp_arr[:, :, 3] > 0
            leaked = np.sum(c_alpha[opp_mask] > 0)
            if leaked > 0:
                c_alpha[opp_mask] = 0
                modified = True
                if verbose:
                    print(f"  {prefix}{cname}-overlay.png: removed {leaked} "
                          f"px from {opp_name}")

        # Side-based clip: combined overlays must stay on their own side.
        # nf-nr-combined = near-side, ff-fr-combined = far-side.
        # Offcharge: near=RIGHT (x >= w/2), far=LEFT (x < w/2)
        # Oncharge:  near=LEFT  (x < w/2), far=RIGHT (x >= w/2)
        h_c, w_c = c_alpha.shape
        mid_x = w_c // 2
        is_near = ("nf" in constituents)  # nf-nr = near side
        if mode == "offcharge":
            # near = right, far = left
            if is_near:
                clip_zone = np.s_[:, :mid_x]  # zero left side
            else:
                clip_zone = np.s_[:, mid_x:]  # zero right side
        else:
            # oncharge: near = left, far = right
            if is_near:
                clip_zone = np.s_[:, mid_x:]  # zero right side
            else:
                clip_zone = np.s_[:, :mid_x]  # zero left side
        clipped = int(np.sum(c_alpha[clip_zone] > 0))
        if clipped > 0:
            c_alpha[clip_zone] = 0
            modified = True
            if verbose:
                print(f"  {prefix}{cname}-overlay.png: side-clip removed "
                      f"{clipped} px from wrong side")

        if modified:
            c_arr[:, :, 3] = c_alpha
            Image.fromarray(c_arr).save(str(c_overlay_path), "PNG")

        # Apply pre-built combined overlay (RGBA) if available.
        if model:
            h_c2, w_c2 = c_arr.shape[:2]
            mask_name = cname.replace("-", "_")
            c_rgba = _load_overlay_rgba(mask_name, model,
                                        (w_c2, h_c2), mode=mode)
            if c_rgba is not None:
                Image.fromarray(c_rgba).save(
                    str(c_overlay_path), "PNG")
                if verbose:
                    n = int(np.sum(c_rgba[:, :, 3] > 0))
                    print(f"  {prefix}{cname}-overlay.png: applied "
                          f"{model} mask -> {n} opaque px")
            else:
                c_mask = _load_overlay_mask(mask_name, model,
                                            (w_c2, h_c2), mode=mode)
                if c_mask is not None:
                    c_src = np.array(Image.open(str(c_overlay_path)))
                    c_src[:, :, 3] = c_mask
                    Image.fromarray(c_src).save(
                        str(c_overlay_path), "PNG")
                    if verbose:
                        n = int(np.sum(c_mask > 0))
                        print(f"  {prefix}{cname}-overlay.png: applied "
                              f"{model} mask -> {n} opaque px")

    # All-doors overlay
    all_doors_path = processed_dir / "all-doors.png"
    if all_doors_path.exists():
        all_doors_img = _apply_ref_shift(
            Image.open(str(all_doors_path)).convert("RGBA"))
        overlay = compute_overlay(all_doors_img, base_img,
                                  remove_cable=(mode == "oncharge"))
        out_name = f"{prefix}all-doors-overlay.png"
        out_path = output_dir / out_name
        overlay.save(str(out_path), "PNG")
        count += 1
        # Apply pre-built all-doors mask if available
        if model:
            h_o, w_o = np.array(all_doors_img).shape[:2]
            ad_mask = _load_overlay_mask("all_doors", model, (w_o, h_o),
                                         mode=mode)
            if ad_mask is not None:
                ad_arr = np.array(all_doors_img)
                ad_arr[:, :, 3] = ad_mask
                Image.fromarray(ad_arr).save(str(out_path), "PNG")
                n = int(np.sum(ad_mask > 0))
                print(f"  {out_name}: applied {model} all-doors mask "
                      f"-> {n} opaque px")
            else:
                print(f"  Saved {out_name}")
        else:
            print(f"  Saved {out_name}")
    elif verbose:
        print(f"  Skipping all-doors overlay: {all_doors_path} not found")

    # Panel backgrounds
    for panel in ("controls-bg", "climate-bg"):
        panel_path = processed_dir / f"{panel}.png"
        if not panel_path.exists():
            continue
        if mode == "oncharge":
            panel_out = f"{panel}-charging.png"
        else:
            panel_out = f"{panel}.png"
        panel_dst = output_dir.parent / panel_out
        panel_img = Image.open(str(panel_path)).convert("RGBA")
        if reference_dir:
            ref_panel = Path(reference_dir).parent / panel_out
            if ref_panel.exists():
                panel_img = align_to_reference(panel_img, Image.open(str(ref_panel)),
                                               verbose=verbose)
        panel_img.save(str(panel_dst), "PNG")
        print(f"  Saved {panel_out}")

    print(f"  Generated {count} overlays in {output_dir}")

    validation_errors = validate_overlays(output_dir, mode=mode)
    if validation_errors:
        print(f"\n  OVERLAY VALIDATION FAILED ({len(validation_errors)} issues):")
        for err in validation_errors:
            print(f"    - {err}")
    else:
        print(f"  Overlay validation passed")


def generate_combo_states(processed_dir, output_dir, mode="offcharge",
                          verbose=False):
    """Generate all combo state images from processed side-view outputs."""
    processed_dir = Path(processed_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

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

    overlay_diffs = {}
    for name in overlays_list:
        img_path = processed_dir / f"{name}-open.png"
        if not img_path.exists():
            if verbose:
                print(f"  Skipping {name}: {img_path} not found")
            continue
        state_img = Image.open(str(img_path)).convert("RGBA")
        overlay_diffs[name] = compute_overlay(state_img, base_img,
                                              remove_cable=(mode == "oncharge"))
        if verbose:
            arr = np.array(overlay_diffs[name])
            n = np.sum(arr[:, :, 3] > 0)
            print(f"  {name} overlay: {n} px")

    combined_diffs = {}
    combined_patterns = (COMBINED_PATTERNS_ONCHARGE if mode == "oncharge"
                         else COMBINED_PATTERNS_OFFCHARGE)
    for cname, (_stems, constituents) in combined_patterns.items():
        cpath = processed_dir / f"{cname}.png"
        if not cpath.exists():
            continue
        cimg = Image.open(str(cpath)).convert("RGBA")
        key = frozenset(constituents)
        combined_diffs[key] = compute_overlay(cimg, base_img,
                                              remove_cable=(mode == "oncharge"))
        if verbose:
            arr = np.array(combined_diffs[key])
            n = np.sum(arr[:, :, 3] > 0)
            print(f"  combined {constituents} overlay: {n} px")

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

        if trunk_active and trunk_img is None:
            continue

        if not active:
            fname = "base.png"
        else:
            fname = "-".join(active) + ".png"

        if trunk_active:
            canvas = trunk_img.copy()
        else:
            canvas = base_img.copy()

        active_set = frozenset(overlay_active)
        used_combined = set()
        for combo_key in combined_diffs:
            if combo_key.issubset(active_set):
                used_combined.update(combo_key)

        for overlay_name in overlays_list:
            if overlay_name not in overlay_active:
                continue
            if overlay_name in used_combined:
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
