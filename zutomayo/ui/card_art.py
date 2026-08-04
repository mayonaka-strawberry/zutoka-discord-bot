"""
Card art loading, with rounded corners applied on demand.

The printed cards are round-cornered, so the jpg scans carry white dead space in all four
corners. That dead space is masked away here, at render time, in memory: the jpg files are
pristine sources and are never rewritten, and no rounded copy is ever written to disk.

The corner radius is a property of the scan batch, not the individual card, so it is a
per-pack constant rather than something detected per image. Detection was tried first (see
the retired scripts/remove_corners.py) and is unreliable in both directions: cards whose art
runs near-white to the edge report a radius several times too large, while two pack-2 cards
whose dead space sits one luminance point below the threshold reported almost no radius at
all and shipped with a visible off-white ring.

Both board_renderer and embeds load card art through here, so every surface gets the same
corners and the same cache.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Optional

from PIL import Image, ImageDraw


_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

CARD_BACK_PATH = 'zutomayo/images/card_back.jpg'

# The radii below are expressed against this width and scaled proportionally, so they stay
# correct if card art is ever rescanned at another resolution.
REFERENCE_CARD_WIDTH = 700

# Measured from the corner-stripped png derivatives these replace: the leading fully
# transparent run on row 0 is exactly the radius, because for a circle centred at (r, r)
# only x = r is inside the circle at y = 0. Distributions were tight within each pack
# (pack 1: 24-25, pack 2: 15-16, pack 3: 14-15, pack 4: 13-14), so these reproduce the
# previous look to within a pixel.
CORNER_RADIUS_BY_PACK_DIRECTORY = {'1': 24, '2': 15, '3': 15, '4': 14}
DEFAULT_CORNER_RADIUS = 15

# The card back is full-bleed art with no dead space to remove, so this rounds it purely so
# face-down cards match the shape of face-up ones. Set to 0 for square backs.
CARD_BACK_CORNER_RADIUS = 15

# The mask is drawn at this multiple and downsampled, which anti-aliases the arc by area
# rather than by the hand-rolled one-pixel alpha ramp the old offline script used.
# Image.BOX is the filter that makes that literally true: on an integer downsample it
# averages each block, so an output alpha is exactly the fraction of the pixel the shape
# covers. LANCZOS measures no more accurately here and rings against the hard mask edge,
# leaving stray non-zero alpha out in the corner that should be fully clear.
MASK_SUPERSAMPLE = 4
MASK_DOWNSAMPLE_FILTER = Image.BOX

# JPEG cannot store transparency, so grid images composite onto this before saving. The
# board composites onto black instead, in board_renderer, because its cards sit on board art.
GRID_BACKGROUND = (255, 255, 255)

# Pack-4 105/106/107 are synthetic dark-background text placeholders rather than scans. They
# have no white dead space, so rounding them would clip real pixels for no gain.
SQUARE_CORNER_IMAGE_STEMS = frozenset({
    'zutomayocard_4th_105',
    'zutomayocard_4th_106',
    'zutomayocard_4th_107',
})

# 700x978 RGBA is ~2.6 MB, so this caps resident card art at roughly 167 MB. It comfortably
# covers every single render unit (a 20-card deck, a 25-card gacha or draft-box half, a
# 25-card draft page, a board's filled zones), which is what matters -- the full 425-card
# catalog would be ~1.1 GB and is never needed at once.
CARD_IMAGE_CACHE_SIZE = 64


@lru_cache(maxsize=16)
def _rounded_corner_mask(width: int, height: int, radius: int) -> Image.Image:
    """An L-mode mask that is opaque inside a rounded rectangle and clear outside it.

    Cached because building one costs far more than applying it (tens of milliseconds
    versus well under one) and the whole catalog only produces a handful of distinct
    (width, height, radius) combinations.
    """
    if radius <= 0:
        return Image.new('L', (width, height), 255)

    supersampled = Image.new(
        'L',
        (width * MASK_SUPERSAMPLE, height * MASK_SUPERSAMPLE),
        0,
    )
    ImageDraw.Draw(supersampled).rounded_rectangle(
        (0, 0, supersampled.width - 1, supersampled.height - 1),
        radius=radius * MASK_SUPERSAMPLE,
        fill=255,
    )
    return supersampled.resize((width, height), MASK_DOWNSAMPLE_FILTER)


def corner_radius_for(path: str, width: int) -> int:
    """The corner radius for a card image, scaled to the width it was loaded at."""
    pure_path = PurePosixPath(str(path).replace('\\', '/'))
    if pure_path.stem in SQUARE_CORNER_IMAGE_STEMS:
        return 0

    base_radius = CORNER_RADIUS_BY_PACK_DIRECTORY.get(
        pure_path.parent.name,
        DEFAULT_CORNER_RADIUS,
    )
    return round(base_radius * width / REFERENCE_CARD_WIDTH)


def round_corners(image: Image.Image, radius: int) -> Image.Image:
    """Return a copy of image with its corners rounded off to radius.

    Copies rather than mutating: both the mask and, for cached card art, the image itself
    are shared objects.
    """
    rounded = image.copy()
    rounded.putalpha(_rounded_corner_mask(image.width, image.height, radius))
    return rounded


@lru_cache(maxsize=CARD_IMAGE_CACHE_SIZE)
def load_card_image(path: str) -> Image.Image:
    """Load a card image as RGBA with its corners already rounded.

    The returned image is shared between all callers and must never be mutated. Resizing
    is safe because Image.resize returns a new image.
    """
    with Image.open(_PROJECT_ROOT / path) as opened:
        card_image = opened.convert('RGBA')
    return round_corners(card_image, corner_radius_for(path, card_image.width))


_card_back_image: Optional[Image.Image] = None


def card_back_image() -> Image.Image:
    """The shared card back, corners rounded to match the fronts. Must not be mutated."""
    global _card_back_image
    if _card_back_image is None:
        with Image.open(_PROJECT_ROOT / CARD_BACK_PATH) as opened:
            back_image = opened.convert('RGBA')
        radius = round(CARD_BACK_CORNER_RADIUS * back_image.width / REFERENCE_CARD_WIDTH)
        _card_back_image = round_corners(back_image, radius)
    return _card_back_image
