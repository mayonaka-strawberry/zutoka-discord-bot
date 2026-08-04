"""
Calibration script: overlays the card rectangles the renderer actually uses onto the board
image, together with the printed card slot outlines they are meant to be centred in, so any
drift between the two is visible at a glance.

Layers drawn:
  white   printed card slot outlines measured from board.png (the reference)
  green   DAY card rectangles, with a centre tick
  red     NIGHT card rectangles, with a centre tick
  yellow  the board art's rotational centre crosshair and horizontal midline

The two battle rectangles should sit centred on the vertical crosshair line, one on each
side of the midline, and every card rectangle should sit centred inside its white slot.

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
    CARD_HEIGHT,
    CARD_WIDTH,
    DAY_PRINTED_SLOTS,
    DAY_ZONES,
    MIRROR_X_SUM,
    MIRROR_Y_SUM,
    NIGHT_PRINTED_SLOTS,
    NIGHT_ZONES,
    SCALE,
    _get_board_base,
)
from zutomayo.ui.image_utils import save_jpeg_file

PROJECT_ROOT = Path(__file__).resolve().parent.parent

SLOT_COLOUR = (255, 255, 255, 170)
DAY_COLOUR = (0, 255, 0, 230)
NIGHT_COLOUR = (255, 40, 40, 230)
GUIDE_COLOUR = (255, 220, 0, 200)
LABEL_STROKE_COLOUR = (0, 0, 0, 230)


def _load_label_font() -> ImageFont.ImageFont:
    """A font large enough to read on a 4500x4500 canvas."""
    try:
        return ImageFont.load_default(size=48)
    except TypeError:
        # Pillow older than 10.1 cannot size the default font.
        return ImageFont.load_default()


def _scaled(rect: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    left, top, right, bottom = rect
    return (left * SCALE, top * SCALE, right * SCALE, bottom * SCALE)


def _draw_slots(
    draw: ImageDraw.ImageDraw,
    slots: dict[str, tuple[int, int, int, int]],
) -> None:
    """Outline the printed slots the card rectangles are centred in."""
    for slot in slots.values():
        draw.rectangle(_scaled(slot), outline=SLOT_COLOUR, width=3)


def _draw_card_rects(
    draw: ImageDraw.ImageDraw,
    zones: dict[str, tuple[int, int, int, int]],
    colour: tuple[int, int, int, int],
    side: str,
    font: ImageFont.ImageFont,
) -> None:
    """Outline each card rectangle and mark its centre."""
    for zone_name, rect in zones.items():
        left, top, right, bottom = _scaled(rect)
        draw.rectangle([left, top, right, bottom], outline=colour, width=5)

        center_x = (left + right) // 2
        center_y = (top + bottom) // 2
        tick = 22
        draw.line([center_x - tick, center_y, center_x + tick, center_y], fill=colour, width=4)
        draw.line([center_x, center_y - tick, center_x, center_y + tick], fill=colour, width=4)

        # Outlined, so the label stays readable wherever it lands on the board art.
        draw.text(
            (left + 12, top + 8),
            f'{side} {zone_name}',
            fill=colour,
            font=font,
            stroke_width=3,
            stroke_fill=LABEL_STROKE_COLOUR,
        )


def _draw_centre_guides(draw: ImageDraw.ImageDraw, size: int) -> None:
    """Draw the rotational centre crosshair and the horizontal midline."""
    centre_x = round(MIRROR_X_SUM / 2 * SCALE)
    centre_y = round(MIRROR_Y_SUM / 2 * SCALE)
    draw.line([centre_x, 0, centre_x, size], fill=GUIDE_COLOUR, width=3)
    draw.line([0, centre_y, size, centre_y], fill=GUIDE_COLOUR, width=3)


def main() -> None:
    board = _get_board_base()
    overlay = Image.new('RGBA', board.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font = _load_label_font()

    _draw_slots(draw, DAY_PRINTED_SLOTS)
    _draw_slots(draw, NIGHT_PRINTED_SLOTS)
    _draw_centre_guides(draw, board.size[0])
    _draw_card_rects(draw, DAY_ZONES, DAY_COLOUR, 'DAY', font)
    _draw_card_rects(draw, NIGHT_ZONES, NIGHT_COLOUR, 'NIGHT', font)

    board = Image.alpha_composite(board, overlay)

    out_path = PROJECT_ROOT / 'scripts' / 'calibration_output.jpg'
    save_jpeg_file(board, out_path)

    print(f'Card size: {CARD_WIDTH}x{CARD_HEIGHT} native')
    print(f'Rotational centre: x={MIRROR_X_SUM / 2}, y={MIRROR_Y_SUM / 2} native')
    print()
    print(f"{'zone':<14} {'DAY rect':<26} {'centre':<14} {'NIGHT rect':<26} centre")
    for zone_name in DAY_ZONES:
        day = DAY_ZONES[zone_name]
        night = NIGHT_ZONES[zone_name]
        day_centre = ((day[0] + day[2]) // 2, (day[1] + day[3]) // 2)
        night_centre = ((night[0] + night[2]) // 2, (night[1] + night[3]) // 2)
        print(
            f'{zone_name:<14} {str(day):<26} {str(day_centre):<14} '
            f'{str(night):<26} {night_centre}'
        )

    print()
    print(f'Saved calibration image to {out_path}')
    print(f'Image size: {board.size}')


if __name__ == '__main__':
    main()
