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
day on the bottom half. All 18 centres are measured from the art by
scripts/measure_chronos_centers.py, which is the script to run for how far off they are in
pixels rather than by eye.

Two images come out of this. The full-board composite is the honest view of what a player
sees, but it is a poor alignment check: the printed glyphs sit at brightness 75-79 against
a mat at 53, so at 45 percent coin opacity even a 20 px offset is hard to see, which is how
the night half stayed misaligned through earlier rounds of this script. The per-slot montage
exists for that job -- it stretches the board's narrow brightness band to full range and
draws the coin rim as an outline, so any offset reads off immediately.

Run from project root:
python scripts/calibrate_chronos.py

Outputs:
scripts/calibration_output_chronos.png         whole board, coins composited
scripts/calibration_output_chronos_glyphs.png  one high-contrast window per slot
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

# Per-slot montage. The window is wide enough to show a whole glyph plus a margin, but not
# so wide that the neighbouring slots crowd in.
MONTAGE_WINDOW = 85  # native px each side of the slot centre
MONTAGE_CELL = 340   # rendered px per cell
MONTAGE_COLUMNS = 6

# The mat sits at ~53 and the printed glyphs at ~75-79. Stretching just that band is what
# makes a misaligned coin obvious; at the board's own contrast it is not.
GLYPH_BAND = (56, 80)


def _load_label_font(size: int = 64) -> ImageFont.ImageFont:
    """A font large enough to read at the given canvas scale."""
    try:
        return ImageFont.load_default(size=size)
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
        for x, y in CHRONOS_CENTERS.values()
    ) / len(CHRONOS_CENTERS) * SCALE
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
        # Outlined, so the label stays readable where it falls on a bright glyph or on the
        # ring guide rather than on the bare mat.
        draw.text(
            (label_x, label_y),
            label,
            fill=MARKER_COLOUR,
            font=font,
            anchor='mm',
            stroke_width=4,
            stroke_fill=LABEL_STROKE_COLOUR,
        )


def _glyph_contrast_board() -> Image.Image:
    """The board art with only its glyph brightness band kept, stretched to full range."""
    low, high = GLYPH_BAND
    source = Image.open(PROJECT_ROOT / 'zutomayo/images/board.png').convert('L')
    # point() over 0..255 rather than numpy, so this script stays a Pillow-only tool.
    stretched = source.point(
        lambda value: 0 if value <= low else 255 if value >= high
        else round((value - low) * 255 / (high - low))
    )
    return stretched.convert('RGB')


def _draw_glyph_montage(font: ImageFont.ImageFont) -> Image.Image:
    """One high-contrast window per slot, with the coin's rim drawn over the glyph."""
    board = _glyph_contrast_board()
    rows = -(-CHRONOS_SIZE // MONTAGE_COLUMNS)
    sheet = Image.new(
        'RGB', (MONTAGE_COLUMNS * MONTAGE_CELL, rows * MONTAGE_CELL), (0, 0, 0)
    )

    zoom = MONTAGE_CELL / (2 * MONTAGE_WINDOW)
    for index, slot in enumerate(range(CHRONOS_SIZE)):
        native_x, native_y = CHRONOS_CENTERS[slot]
        cell = board.crop((
            native_x - MONTAGE_WINDOW, native_y - MONTAGE_WINDOW,
            native_x + MONTAGE_WINDOW, native_y + MONTAGE_WINDOW,
        )).resize((MONTAGE_CELL, MONTAGE_CELL), Image.LANCZOS)

        draw = ImageDraw.Draw(cell)
        middle = MONTAGE_CELL / 2
        rim = COIN_DIAMETER / 2 * zoom
        draw.ellipse(
            [middle - rim, middle - rim, middle + rim, middle + rim],
            outline=MARKER_COLOUR[:3],
            width=3,
        )
        tick = 16
        draw.line([middle - tick, middle, middle + tick, middle], fill=(255, 60, 60), width=3)
        draw.line([middle, middle - tick, middle, middle + tick], fill=(255, 60, 60), width=3)

        half = 'night' if slot <= NIGHT_END else 'day'
        label = f'{slot} {half} ({native_x},{native_y})'
        if slot == MIDNIGHT:
            label += ' midnight'
        elif slot == NOON:
            label += ' noon'
        draw.text((10, 10), label, fill=MARKER_COLOUR[:3], font=font)

        sheet.paste(
            cell,
            ((index % MONTAGE_COLUMNS) * MONTAGE_CELL,
             (index // MONTAGE_COLUMNS) * MONTAGE_CELL),
        )

    return sheet


def main() -> None:
    font = _load_label_font()
    board = _get_board_base()

    for slot in range(CHRONOS_SIZE):
        paste_chronos_coin(board, slot, opacity=COIN_OPACITY)

    overlay = Image.new('RGBA', board.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    _draw_guides(draw, board.size[0])
    _draw_slot_markers(draw, font)
    board = Image.alpha_composite(board, overlay)

    out_path = PROJECT_ROOT / 'scripts' / 'calibration_output_chronos.png'
    board.save(out_path)

    montage_path = PROJECT_ROOT / 'scripts' / 'calibration_output_chronos_glyphs.png'
    montage = _draw_glyph_montage(_load_label_font(22))
    montage.save(montage_path)

    print(f'Coin diameter: {COIN_DIAMETER} native, {COIN_DIAMETER * SCALE} rendered '
          f'(canvas {round(COIN_DIAMETER * SCALE * COIN_CANVAS_RATIO)} rendered)')
    print(f'Rotational centre: x={MIRROR_X_SUM / 2}, y={MIRROR_Y_SUM / 2} native')
    print()
    print(f"{'slot':<6} {'half':<7} {'native centre':<18} {'rendered centre':<18} note")
    for slot in range(CHRONOS_SIZE):
        native_x, native_y = CHRONOS_CENTERS[slot]
        half = 'night' if slot <= NIGHT_END else 'day'
        note = 'measured'
        if slot == MIDNIGHT:
            note += ', midnight'
        elif slot == NOON:
            note += ', noon'
        print(
            f'{slot:<6} {half:<7} {str((native_x, native_y)):<18} '
            f'{str((native_x * SCALE, native_y * SCALE)):<18} {note}'
        )

    print()
    print(f'Saved board composite to {out_path} ({board.size[0]}x{board.size[1]})')
    print(f'Saved glyph montage to {montage_path} ({montage.size[0]}x{montage.size[1]})')
    print('Run scripts/measure_chronos_centers.py for the offsets in pixels.')


if __name__ == '__main__':
    main()
