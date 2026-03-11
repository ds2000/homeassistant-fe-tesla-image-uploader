"""Preview and debug visualization generation."""

from pathlib import Path

from PIL import Image, ImageDraw

from .constants import SIDE_VIEW_SIZE


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
