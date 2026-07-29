"""
Calibration script: every coordinate the renderer uses, on one bare board -- the union of
the two focused calibration overlays.

scripts/calibrate_board.py checks the card rectangles and scripts/calibrate_chronos.py checks
the 18 chronos ring positions, each on its own image. This draws both at once, which is the
only view that shows whether a card rectangle and a coin position crowd each other.

Nothing is rendered into the slots: no card art, no card backs, no coin discs. A card fills
and hides the very rectangle its alignment is being judged against, and an opaque coin hides
the moon or sun glyph it is meant to sit concentric with, so the board is left bare and only
outlines are drawn.

Layers drawn:
  white   printed card slot outlines measured from board.png (the reference)
  green   DAY card rectangles, with a centre tick and zone label
  red     NIGHT card rectangles, with a centre tick and zone label
  cyan    each chronos coin's rim, a crosshair at its exact centre, and the slot index just
          outside the ring
  yellow  the board art's rotational centre crosshair, and the ring the 18 centres lie on

The overlay is drawn by importing the helpers the two focused scripts already use, so this
image cannot drift from the images it summarises.

Run from project root:
python scripts/calibrate_full.py

Output:
scripts/calibration_output_full.png
"""

import sys
from pathlib import Path


# Add project root and this directory to path: the project root for the zutomayo and
# engine_alpha packages, and scripts/ for the sibling calibration modules whose overlay
# helpers are reused below.
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
    out_path = PROJECT_ROOT / 'scripts' / 'calibration_output_full.png'
    board.save(out_path)

    print()
    print(f'Saved calibration image to {out_path}')
    print(f'Image size: {board.size}')


if __name__ == '__main__':
    main()
