"""
Calibration: every renderer coordinate on one bare board (calibrate_board.py and
calibrate_chronos.py combined), to show whether card rectangles and coin positions
crowd each other. Only outlines are drawn, since art would hide what is being
checked; the two scripts' own helpers draw them, so the views cannot drift.

Run from the project root: python scripts/calibrate_full.py
Output: scripts/calibration_output_full.jpg
"""

import sys
from pathlib import Path


# The project root (packages) and scripts/ (the sibling calibration modules).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))


from PIL import Image, ImageDraw

from engine_alpha.battle import CHRONOS_SIZE, NIGHT_END

from zutomayo.ui.board_renderer import (
    CHRONOS_CENTERS,
    DAY_PRINTED_SLOTS,
    DAY_ZONES,
    NIGHT_PRINTED_SLOTS,
    NIGHT_ZONES,
    _get_board_base,
)
from zutomayo.ui.image_utils import save_jpeg_file

# Overlay helpers, reused rather than reimplemented, from the two focused scripts.
from calibrate_board import (
    DAY_COLOUR,
    NIGHT_COLOUR,
    _draw_card_rects,
    _draw_slots,
    _load_label_font as _load_card_font,
)
from calibrate_chronos import (
    _draw_guides,
    _draw_slot_markers,
    _load_label_font as _load_slot_font,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _positions_only() -> Image.Image:
    """Every card rectangle and chronos slot marked on a bare board."""
    board = _get_board_base()
    overlay = Image.new('RGBA', board.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # The printed slot outlines are the reference the card rectangles are judged against.
    _draw_slots(draw, DAY_PRINTED_SLOTS)
    _draw_slots(draw, NIGHT_PRINTED_SLOTS)

    # calibrate_chronos._draw_guides covers the rotational centre crosshair as well as the
    # chronos ring, so calibrate_board._draw_centre_guides is deliberately not called too.
    _draw_guides(draw, board.size[0])

    card_font = _load_card_font()
    _draw_card_rects(draw, DAY_ZONES, DAY_COLOUR, 'DAY', card_font)
    _draw_card_rects(draw, NIGHT_ZONES, NIGHT_COLOUR, 'NIGHT', card_font)
    _draw_slot_markers(draw, _load_slot_font())

    return Image.alpha_composite(board, overlay)


def main() -> None:
    print(f"{'zone':<14} {'DAY centre':<14} NIGHT centre")
    for zone_name in DAY_ZONES:
        day = DAY_ZONES[zone_name]
        night = NIGHT_ZONES[zone_name]
        day_centre = ((day[0] + day[2]) // 2, (day[1] + day[3]) // 2)
        night_centre = ((night[0] + night[2]) // 2, (night[1] + night[3]) // 2)
        print(f'{zone_name:<14} {str(day_centre):<14} {night_centre}')

    print()
    print(f"{'slot':<6} {'half':<7} centre")
    for slot in range(CHRONOS_SIZE):
        half = 'night' if slot <= NIGHT_END else 'day'
        print(f'{slot:<6} {half:<7} {CHRONOS_CENTERS[slot]}')

    board = _positions_only()
    out_path = PROJECT_ROOT / 'scripts' / 'calibration_output_full.jpg'
    save_jpeg_file(board, out_path)

    print()
    print(f'Saved calibration image to {out_path}')
    print(f'Image size: {board.size}')


if __name__ == '__main__':
    main()
