"""Tests for the screenshot processing pipeline.

Run with: python3 -m pytest tests/test_process_screenshots.py -v

These tests run the actual pipeline against the Model Y deep_blue_metallic
submission and validate every output image meets quality requirements.
"""
import json
import subprocess
import shutil
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SUBMISSION_DIR = Path("submissions/Y/Y.1/deep_blue_metallic")
SIDE_VIEW_SIZE = (417, 262)
CONTROLS_SIZE = (545, 859)
CLIMATE_SIZE = (551, 950)


def _find_submission_dir():
    """Find any available submission directory for testing."""
    # Check the expected location first
    base = Path(__file__).resolve().parent.parent
    candidate = base / SUBMISSION_DIR
    if candidate.exists():
        return candidate
    # Also check /tmp clone from CI review
    tmp = Path("/tmp/pr13_review") / SUBMISSION_DIR
    if tmp.exists():
        return tmp
    return None


@pytest.fixture(scope="module")
def processed_offcharge(tmp_path_factory):
    """Run offcharge pipeline once for the entire test module."""
    src = _find_submission_dir()
    if src is None:
        pytest.skip("No submission directory found")
    out = tmp_path_factory.mktemp("offcharge")
    result = subprocess.run(
        ["python3", "scripts/process_screenshots.py",
         "--input-dir", str(src),
         "--output-dir", str(out),
         "--mode", "offcharge",
         "--generate-overlays",
         "--verbose"],
        capture_output=True, text=True, cwd=str(Path(__file__).resolve().parent.parent),
    )
    assert result.returncode == 0, f"Pipeline failed:\n{result.stderr}\n{result.stdout}"
    return out


@pytest.fixture(scope="module")
def processed_oncharge(tmp_path_factory):
    """Run oncharge pipeline once for the entire test module."""
    src = _find_submission_dir()
    if src is None:
        pytest.skip("No submission directory found")
    out = tmp_path_factory.mktemp("oncharge")
    result = subprocess.run(
        ["python3", "scripts/process_screenshots.py",
         "--input-dir", str(src),
         "--output-dir", str(out),
         "--mode", "oncharge",
         "--generate-overlays",
         "--verbose"],
        capture_output=True, text=True, cwd=str(Path(__file__).resolve().parent.parent),
    )
    assert result.returncode == 0, f"Pipeline failed:\n{result.stderr}\n{result.stdout}"
    return out


def _load_rgba(path):
    return np.array(Image.open(str(path)).convert("RGBA"))


# ---------------------------------------------------------------------------
# Required files
# ---------------------------------------------------------------------------

OFFCHARGE_OVERLAY_FILES = [
    "base.png",
    "trunk-open.png",
    "trunk-overlay.png",
    "chargeport-overlay.png",
    "frunk-overlay.png",
    "nf-overlay.png",
    "nr-overlay.png",
    "ff-overlay.png",
    "nf-nr-combined-overlay.png",
    "all-doors-overlay.png",
]

ONCHARGE_OVERLAY_FILES = [
    "oncharge-base.png",
    "oncharge-trunk-open.png",
    "oncharge-trunk-overlay.png",
    "oncharge-frunk-overlay.png",
    "oncharge-nf-overlay.png",
    "oncharge-nr-overlay.png",
    "oncharge-nf-nr-combined-overlay.png",
    "oncharge-all-doors-overlay.png",
]

# Cable overlay is optional — the charging cable may not survive LANCZOS
# downscale from full-res to 417x262 if it's very thin.
ONCHARGE_OPTIONAL_FILES = [
    "oncharge-cable-overlay.png",
]

PANEL_FILES_OFFCHARGE = ["controls-bg.png", "climate-bg.png"]
PANEL_FILES_ONCHARGE = ["controls-bg-charging.png", "climate-bg-charging.png"]


class TestOffchargeFilePresence:
    """Every required overlay and panel file must exist."""

    @pytest.mark.parametrize("filename", OFFCHARGE_OVERLAY_FILES)
    def test_overlay_exists(self, processed_offcharge, filename):
        path = processed_offcharge / "overlays" / filename
        assert path.exists(), f"Missing overlay: {filename}"

    @pytest.mark.parametrize("filename", PANEL_FILES_OFFCHARGE)
    def test_panel_exists(self, processed_offcharge, filename):
        path = processed_offcharge / filename
        assert path.exists(), f"Missing panel: {filename}"


class TestOnchargeFilePresence:
    @pytest.mark.parametrize("filename", ONCHARGE_OVERLAY_FILES)
    def test_overlay_exists(self, processed_oncharge, filename):
        path = processed_oncharge / "overlays" / filename
        assert path.exists(), f"Missing overlay: {filename}"

    @pytest.mark.parametrize("filename", PANEL_FILES_ONCHARGE)
    def test_panel_exists(self, processed_oncharge, filename):
        path = processed_oncharge / filename
        assert path.exists(), f"Missing panel: {filename}"


# ---------------------------------------------------------------------------
# Dimensions
# ---------------------------------------------------------------------------

class TestDimensions:
    """All images must be the exact expected pixel dimensions."""

    def test_base_size(self, processed_offcharge):
        arr = _load_rgba(processed_offcharge / "overlays" / "base.png")
        assert (arr.shape[1], arr.shape[0]) == SIDE_VIEW_SIZE

    @pytest.mark.parametrize("name", [
        "trunk-overlay.png", "chargeport-overlay.png", "frunk-overlay.png",
        "nf-overlay.png", "nr-overlay.png", "ff-overlay.png",
        "nf-nr-combined-overlay.png", "all-doors-overlay.png",
    ])
    def test_overlay_size(self, processed_offcharge, name):
        arr = _load_rgba(processed_offcharge / "overlays" / name)
        assert (arr.shape[1], arr.shape[0]) == SIDE_VIEW_SIZE, \
            f"{name}: {arr.shape[1]}x{arr.shape[0]} != {SIDE_VIEW_SIZE}"

    def test_controls_size(self, processed_offcharge):
        arr = _load_rgba(processed_offcharge / "controls-bg.png")
        assert (arr.shape[1], arr.shape[0]) == CONTROLS_SIZE

    def test_climate_size(self, processed_offcharge):
        arr = _load_rgba(processed_offcharge / "climate-bg.png")
        assert (arr.shape[1], arr.shape[0]) == CLIMATE_SIZE


# ---------------------------------------------------------------------------
# Base background uniformity
# ---------------------------------------------------------------------------

class TestBaseBackground:
    """The base image background must be a single uniform colour.

    The Tesla app renders a solid dark background behind the car.
    After cropping, the background must not contain visible bands,
    stripes, or multiple distinct shades.
    """

    def test_no_horizontal_band(self, processed_offcharge):
        """Background brightness must not jump by >3 between adjacent rows."""
        arr = _load_rgba(processed_offcharge / "overlays" / "base.png")
        # Sample background from the leftmost 15 columns (always background)
        bg_strip = arr[:, :15, :3].astype(float)
        row_means = np.mean(bg_strip, axis=(1, 2))
        # Check for sudden jumps between adjacent rows
        diffs = np.abs(np.diff(row_means))
        max_jump = np.max(diffs)
        jump_row = np.argmax(diffs)
        assert max_jump <= 3.0, (
            f"Background has a visible band: brightness jumps by {max_jump:.1f} "
            f"at row {jump_row} (values {row_means[jump_row]:.1f} → "
            f"{row_means[jump_row+1]:.1f})"
        )

    def test_background_single_shade(self, processed_offcharge):
        """Background edges must be a single uniform colour.

        Only sample known-background regions (left/right 10% strips and
        top 15 rows) to avoid dark car paint pixels being mistaken for bg.
        """
        arr = _load_rgba(processed_offcharge / "overlays" / "base.png")
        h, w = arr.shape[:2]
        rgb = arr[:, :, :3].astype(float)
        # Sample guaranteed-background strips: left 10%, right 10%, top 15 rows
        left_strip = rgb[:, :int(w * 0.10)]
        right_strip = rgb[:, int(w * 0.90):]
        top_strip = rgb[:15, :]
        bg_pixels = np.concatenate([
            left_strip.reshape(-1, 3),
            right_strip.reshape(-1, 3),
            top_strip.reshape(-1, 3),
        ], axis=0)
        median_rgb = np.median(bg_pixels, axis=0)
        distances = np.max(np.abs(bg_pixels - median_rgb), axis=1)
        p99 = np.percentile(distances, 99)
        assert p99 <= 8.0, (
            f"Background is not uniform: 99th percentile deviation from "
            f"median RGB {median_rgb.astype(int)} is {p99:.1f} (limit 8)"
        )


# ---------------------------------------------------------------------------
# Trunk fully visible
# ---------------------------------------------------------------------------

class TestTrunk:
    """The open trunk must be fully contained within the crop frame."""

    def test_trunk_not_clipped_at_top(self, processed_offcharge):
        """Trunk overlay must not have opaque pixels in the top 2 rows
        (which would indicate the trunk is cut off by the crop boundary)."""
        arr = _load_rgba(processed_offcharge / "overlays" / "trunk-overlay.png")
        top_opaque = np.sum(arr[:2, :, 3] > 0)
        assert top_opaque == 0, (
            f"Trunk overlay has {top_opaque} opaque pixels in top 2 rows — "
            f"trunk is clipped by crop frame"
        )

    def test_trunk_overlay_has_pixels(self, processed_offcharge):
        """Trunk overlay must have a meaningful number of pixels."""
        arr = _load_rgba(processed_offcharge / "overlays" / "trunk-overlay.png")
        n = np.sum(arr[:, :, 3] > 0)
        total = arr.shape[0] * arr.shape[1]
        assert n > total * 0.01, (
            f"Trunk overlay too small: {n} px ({100*n/total:.2f}%), "
            f"expected >1% of frame"
        )

    def test_trunk_open_shows_full_hatch(self, processed_offcharge):
        """trunk-open.png with base shows the hatch extending above the
        closed car position — verify by checking the trunk-overlay
        bounding box top is above the car's midline."""
        arr = _load_rgba(processed_offcharge / "overlays" / "trunk-overlay.png")
        opaque = arr[:, :, 3] > 0
        ys = np.where(opaque)[0]
        assert len(ys) > 0, "Trunk overlay is empty"
        # Trunk top should be in the upper third of the frame
        top_y = ys.min()
        assert top_y < arr.shape[0] * 0.33, (
            f"Trunk overlay starts too low at y={top_y} "
            f"(should be in top third, i.e. < {arr.shape[0] * 0.33:.0f})"
        )


# ---------------------------------------------------------------------------
# Chargeport
# ---------------------------------------------------------------------------

class TestChargeport:
    """The chargeport overlay must detect the open charge port."""

    def test_chargeport_has_pixels(self, processed_offcharge):
        arr = _load_rgba(processed_offcharge / "overlays" / "chargeport-overlay.png")
        n = np.sum(arr[:, :, 3] > 0)
        assert n >= 10, (
            f"Chargeport overlay has only {n} opaque pixels — "
            f"charge port not detected (need min_cc_area=10 for small ports)"
        )

    def test_chargeport_on_correct_side(self, processed_offcharge):
        """Chargeport is on the left rear fender — in offcharge front-right
        3/4 view, it appears on the FAR side (right half of image near top)."""
        arr = _load_rgba(processed_offcharge / "overlays" / "chargeport-overlay.png")
        opaque = arr[:, :, 3] > 0
        if np.sum(opaque) == 0:
            pytest.skip("No chargeport pixels")
        xs = np.where(opaque)[1]
        w = arr.shape[1]
        # Chargeport should be in right 60% of image (far side in offcharge)
        assert xs.min() > w * 0.4, (
            f"Chargeport pixels start at x={xs.min()} — "
            f"expected right side (x > {w * 0.4:.0f})"
        )


# ---------------------------------------------------------------------------
# Frunk
# ---------------------------------------------------------------------------

class TestFrunk:
    """The frunk overlay must capture the full open frunk hood."""

    def test_frunk_has_adequate_coverage(self, processed_offcharge):
        """Frunk overlay should have at least 4000 opaque pixels."""
        arr = _load_rgba(processed_offcharge / "overlays" / "frunk-overlay.png")
        n = np.sum(arr[:, :, 3] > 0)
        assert n >= 4000, f"Frunk overlay too small: {n} px (expected ≥4000)"

    def test_frunk_fill_ratio(self, processed_offcharge):
        """Within its bounding box, the frunk should fill at least 40%."""
        arr = _load_rgba(processed_offcharge / "overlays" / "frunk-overlay.png")
        opaque = arr[:, :, 3] > 0
        ys, xs = np.where(opaque)
        if len(ys) == 0:
            pytest.fail("Frunk overlay is empty")
        bbox_area = (xs.max() - xs.min() + 1) * (ys.max() - ys.min() + 1)
        fill = np.sum(opaque) / bbox_area
        assert fill >= 0.40, (
            f"Frunk fill ratio {fill:.1%} too low (expected ≥40%). "
            f"Hull fill may not be capturing enough edge pixels."
        )

    def test_frunk_no_trunk_contamination(self, processed_offcharge):
        """Frunk overlay must not contain trunk pixels.

        Since cp_ft.png has both frunk and trunk open, the pipeline must
        subtract trunk-overlay pixels from frunk-overlay to avoid leakage.
        """
        frunk = _load_rgba(processed_offcharge / "overlays" / "frunk-overlay.png")
        trunk = _load_rgba(processed_offcharge / "overlays" / "trunk-overlay.png")
        # Any pixel opaque in both frunk and trunk is contamination
        overlap = (frunk[:, :, 3] > 0) & (trunk[:, :, 3] > 0)
        n = np.sum(overlap)
        assert n == 0, (
            f"Frunk overlay has {n} pixels overlapping with trunk overlay — "
            f"trunk contamination from cp_ft.png"
        )

    def test_door_overlays_dont_cut_frunk(self, processed_offcharge):
        """No door overlay should contain frunk pixels.

        Opening a door may reveal part of the hood that shifts slightly,
        creating false diff pixels.  The pipeline must subtract frunk/trunk
        pixels from door overlays to prevent visual 'cutting' of the frunk.
        """
        frunk = _load_rgba(processed_offcharge / "overlays" / "frunk-overlay.png")
        frunk_opaque = frunk[:, :, 3] > 0
        for name in ["nf", "nr", "ff", "fr"]:
            path = processed_offcharge / "overlays" / f"{name}-overlay.png"
            if not path.exists():
                continue
            door = _load_rgba(path)
            overlap = (door[:, :, 3] > 0) & frunk_opaque
            n = np.sum(overlap)
            assert n == 0, (
                f"{name}-overlay has {n} pixels overlapping with frunk — "
                f"door would visually cut the frunk"
            )


# ---------------------------------------------------------------------------
# Combined-door overlays (near/far side separation)
# ---------------------------------------------------------------------------

class TestCombinedDoors:
    """Combined-door overlays must be derived from all_doors.png split,
    and must not leak pixels from the opposite side."""

    def test_nf_nr_combined_no_far_side_leakage(self, processed_offcharge):
        """nf-nr-combined must not contain significant far-side door pixels.

        In offcharge (front-right 3/4), near side = RIGHT half of image.
        Small overlap (<200px) at the B-pillar boundary is acceptable since
        both overlays legitimately detect that shared area.
        """
        nfnr = _load_rgba(processed_offcharge / "overlays" / "nf-nr-combined-overlay.png")
        fr = _load_rgba(processed_offcharge / "overlays" / "fr-overlay.png")
        ff_path = processed_offcharge / "overlays" / "ff-overlay.png"

        nfnr_opaque = nfnr[:, :, 3] > 0
        # Check for fr-overlay pixel overlap (allow B-pillar boundary overlap)
        fr_opaque = fr[:, :, 3] > 0
        fr_leak = np.sum(nfnr_opaque & fr_opaque)
        assert fr_leak < 200, (
            f"nf-nr-combined has {fr_leak} leaked fr-overlay pixels (limit 200)"
        )
        # Check for ff-overlay pixel overlap
        if ff_path.exists():
            ff = _load_rgba(ff_path)
            ff_opaque = ff[:, :, 3] > 0
            ff_leak = np.sum(nfnr_opaque & ff_opaque)
            assert ff_leak < 200, (
                f"nf-nr-combined has {ff_leak} leaked ff-overlay pixels (limit 200)"
            )

    def test_ff_fr_combined_no_near_side_leakage(self, processed_offcharge):
        """ff-fr-combined must not contain near-side door pixels."""
        path = processed_offcharge / "overlays" / "ff-fr-combined-overlay.png"
        if not path.exists():
            pytest.skip("ff-fr-combined not generated (far side too small)")
        ffr = _load_rgba(path)
        nf = _load_rgba(processed_offcharge / "overlays" / "nf-overlay.png")
        nr = _load_rgba(processed_offcharge / "overlays" / "nr-overlay.png")

        ffr_opaque = ffr[:, :, 3] > 0
        nf_leak = np.sum(ffr_opaque & (nf[:, :, 3] > 0))
        nr_leak = np.sum(ffr_opaque & (nr[:, :, 3] > 0))
        assert nf_leak == 0, f"ff-fr-combined has {nf_leak} leaked nf-overlay pixels"
        assert nr_leak == 0, f"ff-fr-combined has {nr_leak} leaked nr-overlay pixels"

    def test_nf_nr_combined_has_both_doors(self, processed_offcharge):
        """The near-side combined overlay must contain pixels from BOTH
        the front and rear door regions (not just one)."""
        nfnr = _load_rgba(processed_offcharge / "overlays" / "nf-nr-combined-overlay.png")
        opaque = nfnr[:, :, 3] > 0
        n = np.sum(opaque)
        assert n > 5000, f"nf-nr-combined has only {n} px — expected both doors"

        # Should span a taller region than a single door
        ys = np.where(opaque)[0]
        height_span = ys.max() - ys.min()
        assert height_span > SIDE_VIEW_SIZE[1] * 0.3, (
            f"nf-nr-combined spans only {height_span}px vertically — "
            f"expected >30% of frame height for two doors"
        )

    def test_all_doors_is_superset(self, processed_offcharge):
        """all-doors-overlay must cover at least as many pixels as
        nf-nr-combined + ff-fr-combined individually."""
        all_doors = _load_rgba(processed_offcharge / "overlays" / "all-doors-overlay.png")
        nfnr = _load_rgba(processed_offcharge / "overlays" / "nf-nr-combined-overlay.png")
        all_n = np.sum(all_doors[:, :, 3] > 0)
        nfnr_n = np.sum(nfnr[:, :, 3] > 0)
        assert all_n >= nfnr_n, (
            f"all-doors-overlay ({all_n} px) smaller than "
            f"nf-nr-combined ({nfnr_n} px)"
        )


# ---------------------------------------------------------------------------
# Climate panel
# ---------------------------------------------------------------------------

class TestClimate:
    """The climate panel background must show the full car without
    UI artifacts or excessive cropping."""

    def test_no_ui_artifact_top_left(self, processed_offcharge):
        """The Tesla app back button (blue '<') must be inpainted out."""
        arr = _load_rgba(processed_offcharge / "climate-bg.png")
        # Check top-left 40x40 for blue pixels that don't belong
        tl = arr[:40, :40, :3].astype(float)
        blue_dominant = (tl[:, :, 2] > tl[:, :, 0] + 30) & (tl[:, :, 2] > 60)
        n_blue = np.sum(blue_dominant)
        assert n_blue == 0, (
            f"Climate-bg has {n_blue} blue UI artifact pixels in top-left 40x40"
        )

    def test_car_fills_adequate_height(self, processed_offcharge):
        """The car body should fill at least 70% of the image height."""
        arr = _load_rgba(processed_offcharge / "climate-bg.png")
        row_brightness = np.mean(arr[:, :, :3], axis=(1, 2))
        car_rows = np.where(row_brightness > 20)[0]
        assert len(car_rows) > 0, "Climate-bg appears empty"
        fill = (car_rows[-1] - car_rows[0] + 1) / arr.shape[0]
        assert fill >= 0.70, (
            f"Car fills only {fill:.1%} of climate-bg height (expected ≥70%)"
        )

    def test_no_hvac_controls_visible(self, processed_offcharge):
        """The bottom portion must not contain HVAC control UI elements.

        HVAC controls have bright text/icons — check that the bottom 10%
        of the image is dark (just background or car body shadow).
        """
        arr = _load_rgba(processed_offcharge / "climate-bg.png")
        h = arr.shape[0]
        bottom = arr[int(h * 0.9):, :, :3]
        mean_brightness = np.mean(bottom)
        assert mean_brightness < 50, (
            f"Bottom 10% of climate-bg has brightness {mean_brightness:.1f} — "
            f"possible HVAC controls leak (expected <50)"
        )


# ---------------------------------------------------------------------------
# Controls panel
# ---------------------------------------------------------------------------

class TestControls:
    def test_controls_dimensions(self, processed_offcharge):
        arr = _load_rgba(processed_offcharge / "controls-bg.png")
        assert (arr.shape[1], arr.shape[0]) == CONTROLS_SIZE

    def test_controls_has_car_content(self, processed_offcharge):
        """Controls-bg should have significant car body content."""
        arr = _load_rgba(processed_offcharge / "controls-bg.png")
        row_brightness = np.mean(arr[:, :, :3], axis=(1, 2))
        car_rows = np.where(row_brightness > 20)[0]
        fill = (car_rows[-1] - car_rows[0] + 1) / arr.shape[0]
        assert fill >= 0.80, (
            f"Car fills only {fill:.1%} of controls-bg height (expected ≥80%)"
        )


# ---------------------------------------------------------------------------
# Oncharge-specific tests
# ---------------------------------------------------------------------------

class TestOncharge:
    def test_oncharge_base_size(self, processed_oncharge):
        arr = _load_rgba(processed_oncharge / "overlays" / "oncharge-base.png")
        assert (arr.shape[1], arr.shape[0]) == SIDE_VIEW_SIZE

    def test_oncharge_cable_overlay_if_present(self, processed_oncharge):
        """If cable overlay exists, it should contain green pixels."""
        path = processed_oncharge / "overlays" / "oncharge-cable-overlay.png"
        if not path.exists():
            pytest.skip("Cable overlay not generated (cable too thin at output resolution)")
        arr = _load_rgba(path)
        opaque = arr[:, :, 3] > 0
        if np.sum(opaque) == 0:
            pytest.fail("Cable overlay exists but is empty")
        rgb = arr[opaque, :3].astype(float)
        green_dominant = (rgb[:, 1] > rgb[:, 0] + 20) & (rgb[:, 1] > rgb[:, 2] + 10)
        assert np.sum(green_dominant) > 10, "Cable overlay has no green pixels"

    def test_oncharge_trunk_not_clipped(self, processed_oncharge):
        arr = _load_rgba(processed_oncharge / "overlays" / "oncharge-trunk-overlay.png")
        top_opaque = np.sum(arr[:2, :, 3] > 0)
        assert top_opaque == 0, (
            f"Oncharge trunk has {top_opaque} pixels in top 2 rows — clipped"
        )

    def test_oncharge_nf_nr_no_leakage(self, processed_oncharge):
        """Oncharge nf-nr-combined must not leak far-side pixels."""
        nfnr = _load_rgba(
            processed_oncharge / "overlays" / "oncharge-nf-nr-combined-overlay.png")
        fr_path = processed_oncharge / "overlays" / "oncharge-fr-overlay.png"
        if not fr_path.exists():
            pytest.skip("No fr overlay to check against")
        fr = _load_rgba(fr_path)
        overlap = (nfnr[:, :, 3] > 0) & (fr[:, :, 3] > 0)
        assert np.sum(overlap) == 0, (
            f"Oncharge nf-nr-combined has {np.sum(overlap)} leaked fr pixels"
        )

    def test_oncharge_panels_exist(self, processed_oncharge):
        for name in PANEL_FILES_ONCHARGE:
            assert (processed_oncharge / name).exists(), f"Missing {name}"


# ---------------------------------------------------------------------------
# Cross-mode consistency
# ---------------------------------------------------------------------------

class TestCrossMode:
    """Offcharge and oncharge outputs must be internally consistent."""

    def test_same_side_view_dimensions(self, processed_offcharge, processed_oncharge):
        """All side-view overlays must be the same dimensions across modes."""
        off_base = _load_rgba(processed_offcharge / "overlays" / "base.png")
        on_base = _load_rgba(processed_oncharge / "overlays" / "oncharge-base.png")
        assert off_base.shape == on_base.shape, (
            f"Offcharge base {off_base.shape} != oncharge base {on_base.shape}"
        )

    def test_panels_same_dimensions_across_modes(self, processed_offcharge, processed_oncharge):
        off_ctrl = _load_rgba(processed_offcharge / "controls-bg.png")
        on_ctrl = _load_rgba(processed_oncharge / "controls-bg-charging.png")
        assert off_ctrl.shape == on_ctrl.shape

        off_clim = _load_rgba(processed_offcharge / "climate-bg.png")
        on_clim = _load_rgba(processed_oncharge / "climate-bg-charging.png")
        assert off_clim.shape == on_clim.shape
