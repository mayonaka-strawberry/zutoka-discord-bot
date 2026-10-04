"""
Card art loading, with corners rounded in memory at render time. The jpg scans keep
their white corner dead space, and the files are never rewritten. The radius is
per pack, because per-image detection proved unreliable. The board and all card
grids load art through here.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Optional

from PIL import Image, ImageDraw


_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

CARD_BACK_PATH = 'zutomayo/images/card_back.jpg'

# The radii below are for art this wide and scale with width.
REFERENCE_CARD_WIDTH = 700

# Measured per pack; each pack's scans share one radius.
CORNER_RADIUS_BY_PACK_DIRECTORY = {'1': 24, '2': 15, '3': 15, '4': 14}
DEFAULT_CORNER_RADIUS = 15

# The card back has no dead space; it is rounded only to match the fronts. 0 = square.
CARD_BACK_CORNER_RADIUS = 15

# The mask is drawn at this multiple and BOX-downsampled, anti-aliasing the arc by area
# (LANCZOS rings at the hard edge).
MASK_SUPERSAMPLE = 4
MASK_DOWNSAMPLE_FILTER = Image.BOX

# JPEG cannot store transparency, so grid images composite onto this before saving. The
# board composites onto black instead, in board_renderer, because its cards sit on board art.
GRID_BACKGROUND = (255, 255, 255)

# About 2.6 MB per card (700x978 RGBA), so at most ~167 MB: enough for any single render.
CARD_IMAGE_CACHE_SIZE = 64


@lru_cache(maxsize=16)
def _rounded_corner_mask(width: int, height: int, radius: int) -> Image.Image:
    """L-mode mask, opaque inside the rounded rectangle. Cached: there are few distinct
    sizes, and building one costs far more than applying it."""
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
    base_radius = CORNER_RADIUS_BY_PACK_DIRECTORY.get(
        pure_path.parent.name,
        DEFAULT_CORNER_RADIUS,
    )
    return round(base_radius * width / REFERENCE_CARD_WIDTH)


def round_corners(image: Image.Image, radius: int) -> Image.Image:
    """A copy of `image` with rounded corners. Copies because masks and cached art are shared."""
    rounded = image.copy()
    rounded.putalpha(_rounded_corner_mask(image.width, image.height, radius))
    return rounded


@lru_cache(maxsize=CARD_IMAGE_CACHE_SIZE)
def load_card_image(path: str) -> Image.Image:
    """A card image as RGBA, corners rounded. Shared: never mutate it (resize returns a copy)."""
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
