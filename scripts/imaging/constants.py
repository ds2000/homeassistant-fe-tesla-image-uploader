"""Constants and pattern dictionaries for the Tesla image processing pipeline."""

# Output image sizes
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

# Overlay diff threshold
DIFF_THRESHOLD = 18  # Euclidean RGB distance to count as changed pixel

# Z-order from bottom to top (furthest -> nearest to camera).
# Elements applied later overwrite earlier ones where they overlap.
# Offcharge is a front 3/4 view: raised frunk covers far-side doors,
# near-side doors are nearest to camera.
OFFCHARGE_OVERLAYS = ["chargeport", "fr", "ff", "frunk", "nr", "nf"]
# Oncharge is a rear 3/4 view: rear doors are nearest to camera.
ONCHARGE_OVERLAYS = ["frunk", "ff", "fr", "nf", "nr"]

# ---------------------------------------------------------------------------
# File-name pattern mappings
# ---------------------------------------------------------------------------

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

# Offcharge: front 3/4 from the right -> RIGHT of image = near side
#   cp_df/cp_dr are on the RIGHT -> near side
#   pf/pr are on the LEFT -> far side
SIDE_VIEW_PATTERNS_OFFCHARGE = {
    "nf-open": ["cp_df", "df"],         # near-front (right of image)
    "nr-open": ["cp_dr", "dr"],         # near-rear
    "ff-open": ["pf"],                  # far-front (left of image)
    "fr-open": ["pr"],                  # far-rear
}

# Oncharge: rear 3/4 from the left -> LEFT of image = near side
#   pf/pr are on the LEFT -> near side
#   cp_df/cp_dr are on the RIGHT -> far side
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
# shadows, interior visibility).  Map output name -> (input stems, constituents).
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
# Split into near/far halves -> nf-nr-combined and ff-fr-combined.
ALL_DOORS_PATTERN_OFFCHARGE = ["all_doors", "all_4_doors"]
ALL_DOORS_PATTERN_ONCHARGE = ["oc_all_doors", "all_doors", "all_4_doors"]
