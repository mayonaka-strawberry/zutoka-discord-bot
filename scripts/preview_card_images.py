"""
Render the images the bot uploads, to disk, so they can be inspected by eye.

The test suite asserts that every upload is a JPEG in RGB mode with chroma subsampling off,
but no assertion tells you whether the result actually *looks* right. This writes the real
thing: each file here is the exact byte stream the production renderer hands to Discord,
pulled straight out of the returned discord.File rather than re-encoded, so what you open is
what a player would see.

Outputs land in scripts/ as preview_*.jpg and are gitignored -- regenerate them whenever the
render path changes.

The crop sheet is the one that answers the quality question. A 3540x4930 grid is downscaled
four or five times over by any image viewer, which hides every compression artifact there is,
so judging quality from the full-size grid really means judging your viewer's downscaler.
preview_detail_crops.jpg assembles unscaled 1:1 regions, decoded back out of the saved JPEG,
of the four things worth checking.

Usage: python scripts/preview_card_images.py
"""

from __future__ import annotations

import io
import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from engine_alpha.game import Game  # noqa: E402
from zutomayo.data.card_loader import load_cards  # noqa: E402
from zutomayo.enums.chronos import Chronos  # noqa: E402
from zutomayo.match.state_view import project_board_view  # noqa: E402
from zutomayo.ui.board_renderer import render_board_image, render_zone_strip  # noqa: E402
from zutomayo.ui.embeds import create_deck_grid_image  # noqa: E402

# Reused rather than reimplemented: the same helper tests/ui/test_view_embeds_smoke.py uses
# to stand up a real playable board. It is the reason this dev script imports from tests/.
from tests.match.support import random_full_pool_decks  # noqa: E402

OUTPUT_DIRECTORY = PROJECT_ROOT / 'scripts'

# Fixed so two runs are byte-comparable and a real change is distinguishable from a
# different random sample of cards.
CARD_SAMPLE_SEED = 7
BOARD_SEED = 14
BOARD_PLIES = 40

CARD_WIDTH, CARD_HEIGHT = 700, 978
GRID_PADDING = 10

PLAYER_NAMES = {0: 'Alpha', 1: 'Beta'}

# Cards chosen for what each one proves, not for looks.
REGRESSION_CARD = '2-036'   # shipped with a 2 px radius and a visible white ring
EXEMPT_CARD = '4-105'       # placeholder, must still have square corners
DENSE_TEXT_CARD = '4-093'   # small Japanese effect text: what 4:4:4 chroma is for
ROUNDED_CARD = '1-032'      # pack 1, the largest corner radius at 24 px


def _label_font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        # Pillow older than 10.1 cannot size the default font.
        return ImageFont.load_default()


def _cards_by_identity() -> dict[str, object]:
    return {f'{card.pack}-{card.id:03d}': card for card in load_cards()}


def _jpeg_byte_size(card) -> int:
    """How large a single card encodes to, used to rank the catalog by compressibility."""
    buffer = io.BytesIO()
    with Image.open(PROJECT_ROOT / card.image) as opened:
        opened.convert('RGB').save(buffer, 'JPEG', quality=95, subsampling=0)
    return buffer.tell()


def _write(discord_file, name: str) -> tuple[Path, int]:
    """Write the exact bytes the renderer produced. No re-encode."""
    path = OUTPUT_DIRECTORY / name
    data = discord_file.fp.getvalue()
    path.write_bytes(data)
    return path, len(data)


def _board_view():
    game = Game(seed=BOARD_SEED, mode='fixed_decks', decks=random_full_pool_decks(BOARD_SEED))
    rng = random.Random(BOARD_SEED)
    for _ in range(BOARD_PLIES):
        if game.is_terminal():
            break
        game.apply(rng.choice(game.legal_actions()))
    return project_board_view(game, PLAYER_NAMES)


class _FakeCardHolder:
    """The zone strip renderer reads .face_up and .card; nothing else."""

    def __init__(self, card):
        self.face_up = True
        self.card = card


LABEL_HEIGHT = 30
PANEL_BACKGROUND = (20, 20, 20)
SHEET_BACKGROUND = (12, 12, 12)


def _crop_panel(source: Image.Image, box: tuple[int, int, int, int], caption: str,
                font: ImageFont.ImageFont, scale: int = 1) -> Image.Image:
    """One labelled panel holding a region, optionally integer-magnified.

    Magnification uses NEAREST so it shows the actual stored pixels rather than inventing
    smooth ones -- the point is to see compression blocking, not to hide it.
    """
    region = source.crop(box)
    if scale != 1:
        region = region.resize((region.width * scale, region.height * scale), Image.NEAREST)

    # Widen to fit the caption so labels are never clipped.
    caption_width = font.getbbox(caption)[2] + 12
    panel_width = max(region.width, caption_width)

    panel = Image.new('RGB', (panel_width, region.height + LABEL_HEIGHT), PANEL_BACKGROUND)
    panel.paste(region, (0, LABEL_HEIGHT))
    ImageDraw.Draw(panel).text((6, 7), caption, fill=(240, 240, 240), font=font)
    return panel


def _build_crop_sheet(cards: dict) -> Image.Image:
    """A sheet of 1:1 crops taken from decoded JPEG bytes, not from the pre-encode image."""
    font = _label_font(18)

    selected = [cards[key] for key in (ROUNDED_CARD, REGRESSION_CARD, EXEMPT_CARD, DENSE_TEXT_CARD)]
    grid_file = create_deck_grid_image(selected, columns=4, filename='crop_source.jpg')
    # Decoding the saved JPEG is the whole point: crops must show real compression artifacts.
    decoded = Image.open(io.BytesIO(grid_file.fp.getvalue())).convert('RGB')

    def cell_origin(index: int) -> tuple[int, int]:
        return index * (CARD_WIDTH + GRID_PADDING), 0

    # A 24 px arc inside a 250 px crop is 10% of the edge and reads as a straight line, so
    # the corners are cropped tight and magnified. At 3x a 90 px window shows the whole arc,
    # its anti-aliasing, and the white background behind it.
    corner_span = 90
    corner_scale = 3
    corner_rows = []
    for index, (key, note) in enumerate([
        (ROUNDED_CARD, 'r=24 rounded'),
        (REGRESSION_CARD, 'r=15, was r=2 + ring'),
        (EXEMPT_CARD, 'exempt, square'),
    ]):
        x, y = cell_origin(index)
        corner_rows.append(_crop_panel(
            decoded, (x, y, x + corner_span, y + corner_span),
            f'{key} corner {corner_scale}x - {note}', font, scale=corner_scale,
        ))

    # Effect text sits in the lower third of the card face.
    text_x, text_y = cell_origin(3)
    text_rows = [
        _crop_panel(
            decoded, (text_x + 20, text_y + 690, text_x + 480, text_y + 940),
            f'{DENSE_TEXT_CARD} effect text 1:1', font,
        ),
        _crop_panel(
            decoded, (text_x + 30, text_y + 780, text_x + 260, text_y + 875),
            f'{DENSE_TEXT_CARD} effect text 2x nearest', font, scale=2,
        ),
    ]

    return _assemble_rows([corner_rows, text_rows])


def _assemble_rows(rows: list[list[Image.Image]], margin: int = 12) -> Image.Image:
    """Lay panels out row by row, each row packed left to right at its own height."""
    row_widths = [sum(panel.width for panel in row) + margin * (len(row) + 1) for row in rows]
    row_heights = [max(panel.height for panel in row) + margin for row in rows]

    sheet = Image.new('RGB', (max(row_widths), sum(row_heights) + margin), SHEET_BACKGROUND)
    y = margin
    for row, height in zip(rows, row_heights):
        x = margin
        for panel in row:
            sheet.paste(panel, (x, y))
            x += panel.width + margin
        y += height
    return sheet


def main() -> None:
    cards = _cards_by_identity()
    ordered = list(cards.values())

    sample_rng = random.Random(CARD_SAMPLE_SEED)
    typical = sample_rng.sample(ordered, 25)
    worst = sorted(ordered, key=_jpeg_byte_size, reverse=True)[:25]

    results: list[tuple[str, Path, int, tuple[int, int]]] = []

    def record(discord_file, name: str) -> None:
        if discord_file is None:
            print(f'  SKIP {name}: renderer returned nothing')
            return
        path, size = _write(discord_file, name)
        with Image.open(path) as opened:
            dimensions = opened.size
        results.append((name, path, size, dimensions))

    record(create_deck_grid_image(typical[:20], columns=5, filename='preview_deck_20.jpg'),
           'preview_deck_20.jpg')
    record(create_deck_grid_image(typical, columns=5, filename='preview_pack_25.jpg'),
           'preview_pack_25.jpg')
    record(create_deck_grid_image(worst, columns=5, filename='preview_pack_25_worst.jpg'),
           'preview_pack_25_worst.jpg')
    record(render_board_image(_board_view(), Chronos.DAY), 'preview_board.jpg')
    record(render_zone_strip([_FakeCardHolder(card) for card in typical[:10]], 'preview zone strip'),
           'preview_zone_strip.jpg')

    crop_sheet = _build_crop_sheet(cards)
    crop_path = OUTPUT_DIRECTORY / 'preview_detail_crops.jpg'
    from zutomayo.ui.image_utils import save_jpeg_file

    crop_size = save_jpeg_file(crop_sheet, crop_path)
    results.append(('preview_detail_crops.jpg', crop_path, crop_size, crop_sheet.size))

    from zutomayo.ui.image_utils import (
        DISCORD_UPLOAD_BYTE_LIMIT,
        FALLBACK_UPLOAD_BYTE_LIMIT,
        JPEG_QUALITY,
    )

    print(f'\nWrote {len(results)} previews to {OUTPUT_DIRECTORY} '
          f'(JPEG quality {JPEG_QUALITY}, 4:4:4, no resolution cap)\n')
    print(f"{'file':32s} {'dimensions':>13s} {'MP':>6s} {'MB':>7s}  quality")
    for name, _path, size, (width, height) in results:
        megabytes = size / 1024 / 1024
        if size > DISCORD_UPLOAD_BYTE_LIMIT:
            note = 'OVER 24 MB BUDGET'
        elif size > FALLBACK_UPLOAD_BYTE_LIMIT:
            note = f'q{JPEG_QUALITY}, would re-encode on a 413'
        else:
            note = f'q{JPEG_QUALITY}, fits every ceiling'
        print(f'{name:32s} {width:5d}x{height:<7d} {width * height / 1e6:6.1f} {megabytes:7.2f}  {note}')

    print('\nOpen preview_detail_crops.jpg first: the full grids are downscaled by any viewer,')
    print('so the 1:1 crops are the only place compression artifacts are actually visible.')


if __name__ == '__main__':
    main()
