"""
Calibration script: draws coloured rectangles and circles on the board
to verify zone positions against marked_board.png.

Run from project root:
python scripts/calibrate_board.py

Output:
scripts/calibration_output.png
"""

import sys
from pathlib import Path


# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


from PIL import Image, ImageDraw, ImageFont

from zutomayo.ui.board_renderer import (
    BOARD_RENDER,
    DAY_ZONES,
    NIGHT_ZONES,
    SCALE,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    board = (
        Image.open(PROJECT_ROOT / 'zutomayo/images/board.png')
        .convert('RGBA')
        .resize((BOARD_RENDER, BOARD_RENDER), Image.LANCZOS)
    )
    overlay = Image.new('RGBA', board.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # DAY zones in green
    for name, rect in DAY_ZONES.items():
        l, t, r, b = [v * SCALE for v in rect]
        draw.rectangle([l, t, r, b], outline=(0, 255, 0, 200), width=4)
        draw.text((l + 5, t + 5), f'DAY {name}', fill=(0, 255, 0, 255))

    # NIGHT zones in red
    for name, rect in NIGHT_ZONES.items():
        l, t, r, b = [v * SCALE for v in rect]
        draw.rectangle([l, t, r, b], outline=(255, 0, 0, 200), width=4)
        draw.text((l + 5, t + 5), f'NIGHT {name}', fill=(255, 0, 0, 255))

    board = Image.alpha_composite(board, overlay)

    out_path = PROJECT_ROOT / 'scripts' / 'calibration_output.png'
    board.save(out_path)
    print(f'Saved calibration image to {out_path}')
    print(f'Image size: {board.size}')


if __name__ == '__main__':
    main()
