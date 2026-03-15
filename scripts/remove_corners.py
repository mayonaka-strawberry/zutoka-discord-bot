"""
Remove white corners from card images by fitting a rounded mask.

For each corner, we walk along the edges to find where non-white pixels begin,
use those points to estimate the corner radius. We take the minimum of the 4 corner radii
estimates, then zero out everything outside the rounded rectangle.
"""
from pathlib import Path
from PIL import Image

THRESHOLD = 220
FEATHER = 2  # pixels of anti-aliasing along the rounded edge


def main():
    PROJECT_ROOT = Path(__file__).resolve().parent.parent
    IMAGES_DIR = PROJECT_ROOT / "zutomayo" / "images"
    card_dirs = [d for d in IMAGES_DIR.iterdir() if d.is_dir()]

    for card_dir in card_dirs:
        jpg_paths = [f for f in card_dir.iterdir() if f.suffix == ".jpg"]
        for jpg_path in jpg_paths:
            png_path = jpg_path.with_suffix(".png")
            process_card(jpg_path, png_path)

def process_card(src, dst):
    img = Image.open(src)
    img = img.convert("RGBA")
    pixels = img.load()
    w, h = img.size

    corners = [
        # top-left
        (0, 0, 1, 1),

        # top-right
        (w - 1, 0, -1, 1),

        # bottom-left
        (0, h - 1, 1, -1),
        
        # bottom-right
        (w - 1, h - 1, -1, -1)
    ]

    def find_radius(pixels, corner_x, corner_y, dx, dy, limit):
        """
        Given corner_x and corner_y, walk in the dx and dy 
        direction until we hit a non-white pixel
        """
        
        def is_white(pixel):
            r, g, b = pixel[0], pixel[1], pixel[2]
            lum = 0.299 * r + 0.587 * g + 0.114 * b # luminance formula
            return lum >= THRESHOLD

        x, y = corner_x, corner_y
        dist = 0
        while (0 <= x < limit[0]) and (0 <= y < limit[1]) and is_white(pixels[x, y]):
            x += dx
            y += dy
            dist += 1

        # from testing, adding magic number 2 here helped
        return dist + 2
        
    radii = []
    for cx, cy, sx, sy in corners:
        radii.append(find_radius(pixels, cx, cy, sx, 0, (w, h)))
        radii.append(find_radius(pixels, cx, cy, 0, sy, (w, h)))
    r = min(radii)

    # Place a circle of radius r into each corner.
    # The circle center sits r pixels inward from the corner on both axes.
    # Any pixel outside that circle is part of the rounded-off corner, so we clear it.
    if r > 0:
        for cx, cy, sx, sy in corners:
            center_x = cx + sx * r
            center_y = cy + sy * r

            for ix in range(r):
                for iy in range(r):
                    px = cx + sx * ix
                    py = cy + sy * iy

                    dist_from_center = ((px - center_x) ** 2 + (py - center_y) ** 2) ** 0.5
                    inside_circle = dist_from_center <= r

                    if not inside_circle:
                        pixels[px, py] = (0, 0, 0, 0)
                    
                    elif dist_from_center > r - FEATHER:
                        alpha = int(255 * (r - dist_from_center) / FEATHER)
                        cr, cg, cb, _ = pixels[px, py]
                        pixels[px, py] = (cr, cg, cb, alpha)

    img.save(dst, "PNG")


if __name__ == "__main__":
    main()
