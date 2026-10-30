"""Generate the small, reproducible LayerLoom desktop icon from vector primitives."""
from pathlib import Path


def write_icon(path: Path) -> None:
    from PIL import Image, ImageDraw
    image = Image.new("RGBA", (1024, 1024))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((32, 32, 992, 992), radius=210, fill="#153641")
    # Alternating filament strips form an L, matching the layer-weaving workflow.
    colors = ("#3AC8DC", "#C31996", "#FFDF00")
    for row in range(9):
        y = 195 + row * 72
        right = 795 if row >= 6 else 430
        draw.rounded_rectangle((222, y, right, y + 49), radius=14, fill=colors[row % 3])
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="ICNS")
