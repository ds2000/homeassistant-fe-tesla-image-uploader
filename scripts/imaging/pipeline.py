"""Main processing pipeline: file mapping, multi-pass processing, CLI entry point."""

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

from .constants import (
    ALL_DOORS_PATTERN_OFFCHARGE,
    ALL_DOORS_PATTERN_ONCHARGE,
    COMBINED_DOOR_PATTERNS_OFFCHARGE,
    COMBINED_DOOR_PATTERNS_ONCHARGE,
    COMBINED_PATTERNS_OFFCHARGE,
    COMBINED_PATTERNS_ONCHARGE,
    PANEL_PATTERNS,
    PANEL_PATTERNS_ONCHARGE,
    SIDE_VIEW_PATTERNS_COMMON,
    SIDE_VIEW_PATTERNS_COMMON_ONCHARGE,
    SIDE_VIEW_PATTERNS_OFFCHARGE,
    SIDE_VIEW_PATTERNS_ONCHARGE,
)
from .detection import (
    align_to_reference,
    compute_crop_frame_from_union,
    create_non_bg_mask,
    detect_background_color,
    detect_battery_bottom,
    find_car_bounds_sideview,
)
from .overlays import compute_overlay, generate_combo_states, generate_overlays, split_combined_doors
from .panels import process_climate_panel, process_controls_panel
from .previews import create_comparison_preview, create_debug_visualization, create_overview_grid
from .sideview import process_sideview


def auto_detect_mapping(input_dir, mode="offcharge"):
    """Auto-detect input file -> output name mapping from filenames."""
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

    for output_name, (stems, _near, _far) in combined_door_patterns.items():
        for stem in stems:
            if stem.lower() in files_by_stem:
                mapping["combined_doors"][output_name] = files_by_stem[stem.lower()]
                break

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

    # -- Resolve file mapping --
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

    # === Side views: multi-pass approach ===
    side_views = mapping.get("side_views", {})
    combined_views = mapping.get("combined", {})
    combined_doors = mapping.get("combined_doors", {})
    all_doors_file = mapping.get("all_doors")
    all_bounds = {}
    first_img_shape = None
    max_battery_bottom = 0

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

    # Compute union crop frame
    crop_frame = None
    if all_bounds and first_img_shape is not None:
        union_x0 = min(b[0] for b in all_bounds.values())
        union_y0 = min(b[1] for b in all_bounds.values())
        union_x1 = max(b[2] for b in all_bounds.values())
        union_y1 = max(b[3] for b in all_bounds.values())
        union_bounds = (union_x0, union_y0, union_x1, union_y1)

        min_y0 = max_battery_bottom + int(first_img_shape[0] * 0.005) if max_battery_bottom > 0 else 0

        closed_bounds = all_bounds.get("base")
        crop_frame = compute_crop_frame_from_union(
            union_bounds, first_img_shape, min_y0=min_y0,
            closed_bounds=closed_bounds)

        if verbose:
            uw = union_x1 - union_x0
            uh = union_y1 - union_y0
            print(f"\n  Union: ({union_x0},{union_y0})-({union_x1},{union_y1}) = {uw}x{uh}")
            if max_battery_bottom > 0:
                print(f"  Battery bar bottom: y={max_battery_bottom} -> min crop y0={min_y0}")
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

        # Process combined-state images
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

        # Process combined-door inputs
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

        # Process all-doors input
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

    # === Align all side views to base ===
    base_for_align = processed_images.get("base")
    if base_for_align and len(processed_images) > 1:
        print("Aligning side views to base...")
        for name in list(processed_images.keys()):
            if name == "base" or name in ("controls-bg", "climate-bg"):
                continue
            original = processed_images[name]
            aligned = align_to_reference(original, base_for_align, verbose=verbose)
            if aligned is not original:
                processed_images[name] = aligned
                out_path = output_dir / f"{name}.png"
                aligned.save(str(out_path), "PNG")
                if verbose:
                    print(f"    Overwrote {name}.png (aligned)")
        print()

    # === Pass 3: Split combined-door images ===
    combined_door_patterns = (COMBINED_DOOR_PATTERNS_ONCHARGE if mode == "oncharge"
                              else COMBINED_DOOR_PATTERNS_OFFCHARGE)

    base_img = processed_images.get("base")
    if base_img and combined_doors:
        print("Pass 3: Splitting combined-door images into individual overlays...")
        for door_key, filename in combined_doors.items():
            if door_key not in processed_images:
                continue
            combined_img = processed_images[door_key]

            _stems, near_name, far_name = combined_door_patterns[door_key]
            near_out = f"{near_name}-open"
            far_out = f"{far_name}-open"

            if near_out in processed_images and far_out in processed_images:
                if verbose:
                    print(f"  Skipping {door_key}: individual doors already present")
                continue

            print(f"  Splitting {door_key} -> {near_out}.png + {far_out}.png")
            near_overlay, far_overlay = split_combined_doors(
                combined_img, base_img, mode=mode, verbose=verbose)

            if near_overlay is not None and near_out not in processed_images:
                out_path = output_dir / f"{near_out}.png"
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

        # Derive same-side combined overlays from all_doors split
        all_doors_img = processed_images.get("all-doors")
        if all_doors_img is not None:
            near_overlay, far_overlay = split_combined_doors(
                all_doors_img, base_img, mode=mode, verbose=verbose)

            near_combo, far_combo = "nf-nr-combined", "ff-fr-combined"

            if near_overlay is not None:
                combo_img = base_img.copy()
                combo_img.paste(near_overlay, (0, 0), near_overlay)
                out_path = output_dir / f"{near_combo}.png"
                combo_img.save(str(out_path), "PNG")
                processed_images[near_combo] = combo_img
                n = int(np.sum(np.array(near_overlay)[:, :, 3] > 0))
                print(f"  Split all-doors -> {near_combo}.png ({n} opaque px)")

            if far_overlay is not None:
                combo_img = base_img.copy()
                combo_img.paste(far_overlay, (0, 0), far_overlay)
                out_path = output_dir / f"{far_combo}.png"
                combo_img.save(str(out_path), "PNG")
                processed_images[far_combo] = combo_img
                n = int(np.sum(np.array(far_overlay)[:, :, 3] > 0))
                print(f"  Split all-doors -> {far_combo}.png ({n} opaque px)")
        else:
            # Fallback: composite from individual doors
            same_side_combos = (COMBINED_PATTERNS_ONCHARGE if mode == "oncharge"
                                else COMBINED_PATTERNS_OFFCHARGE)
            for combo_name, (_stems, constituents) in same_side_combos.items():
                parts = [f"{c}-open" for c in constituents]
                if all(p in processed_images for p in parts):
                    z_ordered = list(reversed(parts))
                    print(f"  Compositing {combo_name} from {' + '.join(z_ordered)}")
                    combo_img = base_img.copy()
                    for p in z_ordered:
                        door_img = processed_images[p]
                        overlay = compute_overlay(door_img, base_img,
                                                  remove_cable=(mode == "oncharge"))
                        combo_img.paste(overlay, (0, 0), overlay)
                    out_path = output_dir / f"{combo_name}.png"
                    combo_img.save(str(out_path), "PNG")
                    processed_images[combo_name] = combo_img
                    print(f"    Saved {combo_name}.png")

        print()

    # === Panel processing ===
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

    # -- Previews --
    if reference_dir:
        ref_dir = Path(reference_dir)
        preview_dir.mkdir(parents=True, exist_ok=True)
        for name, img in processed_images.items():
            ref_path = ref_dir / f"{name}.png"
            if ref_path.exists():
                create_comparison_preview(
                    img, str(ref_path),
                    str(preview_dir / f"compare_{name}.png"), name)

    # -- Overview --
    if processed_images:
        create_overview_grid(processed_images, str(output_dir / "overview.png"))

    # -- Summary --
    total = len(report["images"])
    succeeded = sum(1 for r in report["images"] if r.get("success"))
    report["summary"] = {
        "total": total,
        "succeeded": succeeded,
        "failed": total - succeeded,
    }
    report["success"] = (total - succeeded == 0) and total > 0

    return report


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
    parser.add_argument("--model", default=None,
                        help="Model ID (e.g. 'Y', '3') for model-specific "
                             "processing such as pre-built frunk masks")
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
    print(f"Done in {elapsed:.1f}s -- "
          f"{s['succeeded']}/{s['total']} images processed successfully")
    if report["errors"]:
        print(f"Errors ({len(report['errors'])}):")
        for err in report["errors"]:
            print(f"  - {err}")
    print(f"Report: {report_path}")

    if args.generate_states:
        states_dir = Path(args.output_dir) / "states"
        print(f"\nGenerating combo states...")
        generate_combo_states(
            args.output_dir, states_dir,
            mode=args.mode, verbose=args.verbose)

    if args.generate_overlays:
        overlays_dir = Path(args.output_dir) / "overlays"
        print(f"\nGenerating transparent overlays...")
        ref_overlays = None
        if args.reference_dir:
            ref_overlays = Path(args.reference_dir) / "overlays"
            if not ref_overlays.exists():
                ref_overlays = None
        generate_overlays(
            args.output_dir, overlays_dir,
            mode=args.mode, verbose=args.verbose,
            reference_dir=str(ref_overlays) if ref_overlays else None,
            model=args.model)

    sys.exit(0 if report.get("success") else 1)
