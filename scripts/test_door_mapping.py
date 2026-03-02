#!/usr/bin/env python3
"""Test that door mappings produce correct near-side / far-side results.

Validates that:
1. nf-open shows change on the near side of the car (close to camera)
2. ff-open shows change on the far side of the car (away from camera)
3. nr-open shows change on the near side, rear area
4. fr-open shows change on the far side, rear area
5. Combined images are closest to their constituent individual sources
6. Combo states with same-side doors use the combined image

Near side = close to camera.
  - Offcharge (front 3/4 from right): near = RIGHT of image
  - Oncharge  (rear 3/4 from left):  near = LEFT of image
"""

import sys
from pathlib import Path

import numpy as np
from PIL import Image


def load_rgb(path):
    img = Image.open(str(path)).convert("RGB")
    return np.array(img, dtype=np.float64)


def diff_mask(arr_a, arr_b, threshold=18):
    """Boolean mask where Euclidean RGB distance exceeds threshold."""
    dist = np.sqrt(np.sum((arr_a - arr_b) ** 2, axis=2))
    return dist > threshold


def side_bias(mask):
    """Fraction of changed pixels in left vs right half.

    Returns (left_frac, right_frac).
    """
    h, w = mask.shape
    mid = w // 2
    total = np.sum(mask)
    if total == 0:
        return 0.0, 0.0
    return np.sum(mask[:, :mid]) / total, np.sum(mask[:, mid:]) / total


def mae_changed_only(arr_a, arr_b, arr_base, threshold=18):
    """MAE between two images, evaluated only at pixels where EITHER
    differs from base.  This ignores the vast majority of unchanged pixels
    that would dominate a full-image MAE comparison."""
    mask_a = diff_mask(arr_a, arr_base, threshold)
    mask_b = diff_mask(arr_b, arr_base, threshold)
    union = mask_a | mask_b
    if np.sum(union) == 0:
        return 0.0
    diff = np.abs(arr_a - arr_b)
    return np.mean(diff[union])


# ─────────────────────────────────────────────────────────────────────────────

def test_single_door_bias(processed_dir, mode="offcharge"):
    """Test that each single-door overlay changes the expected side.

    Near side of the image depends on camera angle:
      - offcharge (front 3/4 from right): near = RIGHT of image
      - oncharge  (rear 3/4 from left):  near = LEFT of image
    """
    p = Path(processed_dir)
    base = load_rgb(p / "base.png")
    results = []

    if mode == "oncharge":
        # Rear 3/4 from left: near side is LEFT of image
        near_side, far_side = "left", "right"
    else:
        # Front 3/4 from right: near side is RIGHT of image
        near_side, far_side = "right", "left"

    tests = [
        ("nf-open.png", near_side, "near front"),
        ("nr-open.png", near_side, "near rear"),
        ("ff-open.png", far_side,  "far front"),
        ("fr-open.png", far_side,  "far rear"),
    ]

    for fname, expected, label in tests:
        path = p / fname
        if not path.exists():
            results.append((label, "SKIP", f"{fname} not found"))
            continue

        img = load_rgb(path)
        mask = diff_mask(img, base)
        n_changed = int(np.sum(mask))
        left_frac, right_frac = side_bias(mask)

        ok = (left_frac > right_frac) if expected == "left" else (right_frac > left_frac)
        status = "PASS" if ok else "FAIL"
        detail = (f"{n_changed:,} changed px — "
                  f"left {left_frac:.1%} / right {right_frac:.1%} "
                  f"(expected {expected})")
        results.append((label, status, detail))

    return results


def test_combined_source_affinity(processed_dir, source_dir):
    """Test that each combined image is closest to its constituent sources.

    Uses raw source screenshots (not processed), comparing in the source
    pixel space to avoid crop/resize artifacts.

    nf-nr-combined comes from source pf_pr (near side):
      → should be closest to pf + pr sources
    ff-fr-combined comes from source cp_df_dr (far side):
      → should be closest to cp_df + cp_dr sources
    """
    src = Path(source_dir)
    results = []

    closed_path = src / "closed.png"
    if not closed_path.exists():
        return [("source affinity", "SKIP", "closed.png not found")]
    closed = load_rgb(closed_path)

    # (combined_source_stems, expected_closest_stems, label)
    tests = [
        (["pf_pr"],     ["pf", "pr"],     "nf-nr-combined (from pf_pr) close to pf+pr"),
        (["cp_df_dr", "df_dr", "dfdr"], ["cp_df", "df", "cp_dr", "dr"],
         "ff-fr-combined (from cp_df_dr) close to cp_df+cp_dr"),
    ]

    for combo_stems, constituent_stems, label in tests:
        # Find the combined source
        combo_path = None
        for stem in combo_stems:
            p = src / f"{stem}.png"
            if p.exists():
                combo_path = p
                break
        if combo_path is None:
            results.append((label, "SKIP", "combined source not found"))
            continue

        combo = load_rgb(combo_path)

        # Find constituent sources
        constituent_paths = []
        for stem in constituent_stems:
            p = src / f"{stem}.png"
            if p.exists():
                constituent_paths.append(p)

        if not constituent_paths:
            results.append((label, "SKIP", "constituent sources not found"))
            continue

        # MAE of combined vs each constituent
        constituent_maes = []
        for cp in constituent_paths:
            c_img = load_rgb(cp)
            mae = np.mean(np.abs(combo - c_img))
            constituent_maes.append((cp.stem, mae))

        # Compare with the OTHER side's sources
        all_stems = ["cp_df", "df", "cp_dr", "dr", "pf", "pr"]
        other_stems = [s for s in all_stems if s not in constituent_stems]
        other_maes = []
        for stem in other_stems:
            p = src / f"{stem}.png"
            if p.exists():
                o_img = load_rgb(p)
                mae = np.mean(np.abs(combo - o_img))
                other_maes.append((stem, mae))

        avg_constituent = np.mean([m for _, m in constituent_maes])
        avg_other = np.mean([m for _, m in other_maes]) if other_maes else float('inf')

        ok = avg_constituent < avg_other
        status = "PASS" if ok else "FAIL"
        const_detail = ", ".join(f"{n}={m:.2f}" for n, m in constituent_maes)
        other_detail = ", ".join(f"{n}={m:.2f}" for n, m in other_maes)
        detail = (f"constituents avg={avg_constituent:.2f} ({const_detail}) "
                  f"vs others avg={avg_other:.2f} ({other_detail})")
        results.append((label, status, detail))

    return results


def test_combo_states_use_combined(processed_dir):
    """Test that combo states with same-side doors use the combined overlay.

    Compares the actual state against the combined source image and against
    a naive composite, using only changed pixels (not full-image MAE which
    is dominated by unchanged pixels).
    """
    p = Path(processed_dir)
    states_dir = p / "states"
    results = []

    base = load_rgb(p / "base.png")

    # State filenames follow z-order, so check both orderings
    combos = [
        (["nr-nf.png", "nf-nr.png"], "nf-nr-combined.png",
         ["nf-open.png", "nr-open.png"], "nf+nr state uses combined overlay"),
        (["fr-ff.png", "ff-fr.png"], "ff-fr-combined.png",
         ["ff-open.png", "fr-open.png"], "ff+fr state uses combined overlay"),
    ]

    for state_fnames, combined_fname, individual_fnames, label in combos:
        combined_path = p / combined_fname
        if not combined_path.exists():
            results.append((label, "SKIP", f"{combined_fname} not found"))
            continue

        # Find the state file (try both orderings)
        state_path = None
        for sf in state_fnames:
            sp = states_dir / sf
            if sp.exists():
                state_path = sp
                break
        if state_path is None:
            results.append((label, "SKIP",
                           f"state file not found (tried {state_fnames})"))
            continue

        individuals_exist = all((p / f).exists() for f in individual_fnames)
        if not individuals_exist:
            results.append((label, "SKIP", "individual overlays not found"))
            continue

        actual = load_rgb(state_path)
        combined = load_rgb(combined_path)

        # Build naive composite
        naive = base.copy()
        for ifname in individual_fnames:
            overlay = load_rgb(p / ifname)
            mask = diff_mask(overlay, base)
            # Stamp overlay pixels onto naive canvas
            naive[mask] = overlay[mask]

        # Compare using only changed pixels
        score_combined = mae_changed_only(actual, combined, base)
        score_naive = mae_changed_only(actual, naive, base)

        ok = score_combined <= score_naive
        status = "PASS" if ok else "FAIL"
        detail = (f"vs combined: {score_combined:.2f}, "
                  f"vs naive: {score_naive:.2f} "
                  f"(state={state_path.name})")
        results.append((label, status, detail))

    return results


def test_cross_check_sides(processed_dir, mode="offcharge"):
    """Cross-check: near-side doors should produce more visible pixel change
    than far-side doors (the camera faces the near side)."""
    p = Path(processed_dir)
    base = load_rgb(p / "base.png")
    results = []

    changes = {}
    for name in ["nf-open.png", "nr-open.png", "ff-open.png", "fr-open.png"]:
        path = p / name
        if not path.exists():
            continue
        img = load_rgb(path)
        changes[name] = int(np.sum(diff_mask(img, base)))

    if len(changes) < 4:
        results.append(("cross-check", "SKIP", "not all 4 door images found"))
        return results

    near_total = changes["nf-open.png"] + changes["nr-open.png"]
    far_total = changes["ff-open.png"] + changes["fr-open.png"]

    ok = near_total > far_total
    status = "PASS" if ok else "FAIL"
    detail = (f"near side: {near_total:,} px "
              f"(nf={changes['nf-open.png']:,}+nr={changes['nr-open.png']:,}), "
              f"far side: {far_total:,} px "
              f"(ff={changes['ff-open.png']:,}+fr={changes['fr-open.png']:,})")
    results.append(("near-side doors have more visible change", status, detail))

    return results


# ─────────────────────────────────────────────────────────────────────────────

def run_tests(processed_dir, source_dir, mode="offcharge"):
    print(f"\n{'='*70}")
    print(f"Testing: {processed_dir} ({mode})")
    print(f"{'='*70}\n")

    all_results = []

    sections = [
        ("1. Single door side bias", test_single_door_bias(processed_dir, mode=mode)),
        ("2. Combined source affinity (raw sources)",
         test_combined_source_affinity(processed_dir, source_dir)),
        ("3. Combo states use combined overlay",
         test_combo_states_use_combined(processed_dir)),
        ("4. Near-side vs far-side visibility",
         test_cross_check_sides(processed_dir, mode=mode)),
    ]

    for title, test_results in sections:
        print(f"{title}:")
        for label, status, detail in test_results:
            icon = {"PASS": "  PASS", "FAIL": "**FAIL", "SKIP": "  SKIP"}[status]
            print(f"   {icon}  {label}")
            print(f"         {detail}")
            all_results.append(status)
        print()

    return all_results


def main():
    all_results = []

    # Offcharge (front 3/4 view from right: near side = right of image)
    all_results.extend(run_tests(
        "screenshots/processed_v12",
        "screenshots/Tesla/offcharge",
        mode="offcharge",
    ))

    # Oncharge (rear 3/4 view from left: near side = left of image)
    all_results.extend(run_tests(
        "screenshots/processed_v12_oncharge",
        "screenshots/Tesla/oncharge",
        mode="oncharge",
    ))

    n_pass = all_results.count("PASS")
    n_fail = all_results.count("FAIL")
    n_skip = all_results.count("SKIP")
    total = len(all_results)

    print(f"{'='*70}")
    print(f"Summary: {n_pass}/{total} passed, {n_fail} failed, {n_skip} skipped")
    print(f"{'='*70}")

    if n_fail > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
