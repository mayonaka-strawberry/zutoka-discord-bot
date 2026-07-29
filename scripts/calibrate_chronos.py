"""
Calibration script: draws the chronos coin marker on all 18 slots at once, so every
position on the printed chronos ring can be checked in a single image.

The live board only ever shows one coin. Here all 18 are drawn semi-transparent, which
keeps the moon or sun underneath visible -- an opaque coin would hide the very glyph its
alignment is being judged against.

Layers drawn:
  coin    the real marker at COIN_DIAMETER, at 45 percent alpha
  cyan    each coin's rim, a crosshair at its exact centre, and the slot index just
          outside the ring
  yellow  the board art's rotational centre crosshair, and the ring the 18 centres lie on

Each coin should sit concentric with its glyph: slot 4 on the four-pointed-star full moon
at top centre, slot 13 on the eight-pointed sun at bottom centre, night on the top half and
day on the bottom half.

The night centres are reflections of the measured day centres, so they carry a known error
of up to 9 px against the printed art (see the NIGHT_CHRONOS_CENTERS comment in the
renderer). Slots 0 and 8 are where that shows most.

Run from project root:
python scripts/calibrate_chronos.py

Output:
scripts/calibration_output_chronos.png
"""

import sys
from pathlib import Path


# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


from PIL import Image, ImageDraw, ImageFont

from engine_alpha.battle import CHRONOS_SIZE, MIDNIGHT, NIGHT_END, NOON
from zutomayo.ui.board_renderer import (
    CHRONOS_CENTERS,
    COIN_CANVAS_RATIO,
    COIN_DIAMETER,
    DAY_CHRONOS_CENTERS,
    MIRROR_X_SUM,
    MIRROR_Y_SUM,
    SCALE,
    _get_board_base,
    paste_chronos_coin,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

COIN_OPACITY = 115  # 45 percent
MARKER_COLOUR = (0, 255, 255, 255)
RIM_COLOUR = (0, 255, 255, 190)
GUIDE_COLOUR = (255, 220, 0, 160)
LABEL_STROKE_COLOUR = (0, 0, 0, 230)

LABEL_OFFSET = 92  # native px further out along the ring than the slot centre


def _load_label_font() -> ImageFont.ImageFont:
    """A font large enough to read on a 4500x4500 canvas."""
    try:
        return ImageFont.load_default(size=64)
    except TypeError:
        # Pillow older than 10.1 cannot size the default font.
        return ImageFont.load_default()


def _draw_guides(draw: ImageDraw.ImageDraw, size: int) -> None:
    """Rotational centre crosshair, plus the circle the 18 slot centres lie on."""
    centre_x = MIRROR_X_SUM / 2 * SCALE
    centre_y = MIRROR_Y_SUM / 2 * SCALE
    draw.line([centre_x, 0, centre_x, size], fill=GUIDE_COLOUR, width=3)
    draw.line([0, centre_y, size, centre_y], fill=GUIDE_COLOUR, width=3)

    radius = sum(
        ((x - MIRROR_X_SUM / 2) ** 2 + (y - MIRROR_Y_SUM / 2) ** 2) ** 0.5
        for x, y in DAY_CHRONOS_CENTERS.values()
    ) / len(DAY_CHRONOS_CENTERS) * SCALE
    draw.ellipse(
        [centre_x - radius, centre_y - radius, centre_x + radius, centre_y + radius],
        outline=GUIDE_COLOUR,
        width=3,
    )


def _draw_slot_markers(draw: ImageDraw.ImageDraw, font: ImageFont.ImageFont) -> None:
    """A centre crosshair and an index label for every chronos slot."""
    centre_x = MIRROR_X_SUM / 2
    centre_y = MIRROR_Y_SUM / 2

    for slot in range(CHRONOS_SIZE):
        native_x, native_y = CHRONOS_CENTERS[slot]
        x, y = native_x * SCALE, native_y * SCALE

        # Trace the coin's own rim, so whether it is concentric with the glyph reads off
        # the two outlines rather than off the translucent coin art.
        rim = COIN_DIAMETER * SCALE / 2  # the coin itself, not its padded canvas
        draw.ellipse([x - rim, y - rim, x + rim, y + rim], outline=RIM_COLOUR, width=4)

        tick = 26
        draw.line([x - tick, y, x + tick, y], fill=MARKER_COLOUR, width=4)
        draw.line([x, y - tick, x, y + tick], fill=MARKER_COLOUR, width=4)

        # Push the label outward along the ring so it clears the coin and the glyph.
        away_x = native_x - centre_x
        away_y = native_y - centre_y
        length = (away_x ** 2 + away_y ** 2) ** 0.5
        label_x = (native_x + away_x / length * LABEL_OFFSET) * SCALE
        label_y = (native_y + away_y / length * LABEL_OFFSET) * SCALE

        label = str(slot)
        if slot == MIDNIGHT:
            label = f'{slot} midnight'
        elif slot == NOON:
            label = f'{slot} noon'
        # Outlined, so the label stays readable where it falls on card art rather than on
        # the bare mat (scripts/calibrate_full.py draws these over a populated board).
        draw.text(
            (label_x, label_y),
            label,
            fill=MARKER_COLOUR,
            font=font,
            anchor='mm',
            stroke_width=4,
            stroke_fill=LABEL_STROKE_COLOUR,
        )


def main() -> None:
    board = _get_board_base()

    for slot in range(CHRONOS_SIZE):
        paste_chronos_coin(board, slot, opacity=COIN_OPACITY)

    overlay = Image.new('RGBA', board.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    _draw_guides(draw, board.size[0])
    _draw_slot_markers(draw, _load_label_font())
    board = Image.alpha_composite(board, overlay)

    out_path = PROJECT_ROOT / 'scripts' / 'calibration_output_chronos.png'
    board.save(out_path)

    print(f'Coin diameter: {COIN_DIAMETER} native, {COIN_DIAMETER * SCALE} rendered '
          f'(canvas {round(COIN_DIAMETER * SCALE * COIN_CANVAS_RATIO)} rendered)')
    print(f'Rotational centre: x={MIRROR_X_SUM / 2}, y={MIRROR_Y_SUM / 2} native')
    print()
    print(f"{'slot':<6} {'half':<7} {'native centre':<18} {'rendered centre':<18} note")
    for slot in range(CHRONOS_SIZE):
        native_x, native_y = CHRONOS_CENTERS[slot]
        half = 'night' if slot <= NIGHT_END else 'day'
        if slot in DAY_CHRONOS_CENTERS:
            note = 'measured'
        else:
            note = f'reflected from slot {17 - slot}'
        if slot == MIDNIGHT:
            note += ', midnight'
        elif slot == NOON:
            note += ', noon'
        print(
            f'{slot:<6} {half:<7} {str((native_x, native_y)):<18} '
            f'{str((native_x * SCALE, native_y * SCALE)):<18} {note}'
        )

    print()
    print(f'Saved calibration image to {out_path}')
    print(f'Image size: {board.size}')


if __name__ == '__main__':
    main()
