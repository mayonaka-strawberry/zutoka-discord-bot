"""Tests for card_art: per-pack corner radii, the mask, and the shared image cache."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from zutomayo.ui.card_art import (  # noqa: E402
    CORNER_RADIUS_BY_PACK_DIRECTORY,
    DEFAULT_CORNER_RADIUS,
    REFERENCE_CARD_WIDTH,
    _rounded_corner_mask,
    card_back_image,
    corner_radius_for,
    load_card_image,
)

# One real card per pack. Packs 1-3 use unpadded ids, pack 4 zero-pads to three digits.
CARD_PER_PACK = {
    '1': 'zutomayo/images/1/zutomayocard_1st_32.jpg',
    '2': 'zutomayo/images/2/zutomayocard_2nd_25.jpg',
    '3': 'zutomayo/images/3/zutomayocard_3rd_40.jpg',
    '4': 'zutomayo/images/4/zutomayocard_4th_093.jpg',
}


@pytest.mark.parametrize('pack_directory,image_path', sorted(CARD_PER_PACK.items()))
def test_radius_matches_the_pack_constant(pack_directory, image_path):
    """The radius is a property of the scan batch, so it is looked up per pack."""
    assert corner_radius_for(image_path, REFERENCE_CARD_WIDTH) == (
        CORNER_RADIUS_BY_PACK_DIRECTORY[pack_directory]
    )


def test_radius_scales_with_image_width():
    """Constants are expressed against REFERENCE_CARD_WIDTH so a rescan stays correct."""
    path = CARD_PER_PACK['1']
    base = CORNER_RADIUS_BY_PACK_DIRECTORY['1']
    assert corner_radius_for(path, REFERENCE_CARD_WIDTH * 2) == base * 2
    assert corner_radius_for(path, REFERENCE_CARD_WIDTH // 2) == round(base / 2)


def test_unknown_pack_directory_falls_back_to_the_default_radius():
    assert corner_radius_for('zutomayo/images/9/zutomayocard_9th_1.jpg', 700) == (
        DEFAULT_CORNER_RADIUS
    )


def test_windows_style_paths_resolve_the_same_pack():
    assert corner_radius_for(r'zutomayo\images\1\zutomayocard_1st_32.jpg', 700) == (
        CORNER_RADIUS_BY_PACK_DIRECTORY['1']
    )


@pytest.mark.parametrize('image_path', sorted(CARD_PER_PACK.values()))
def test_loaded_card_is_transparent_at_the_corners_and_opaque_inside(image_path):
    card = load_card_image(image_path)
    alpha = card.getchannel('A')

    for corner in [
        (0, 0),
        (card.width - 1, 0),
        (0, card.height - 1),
        (card.width - 1, card.height - 1),
    ]:
        assert alpha.getpixel(corner) == 0, f'{image_path} corner {corner} should be clear'

    assert alpha.getpixel((card.width // 2, card.height // 2)) == 255


@pytest.mark.parametrize('image_path', sorted(CARD_PER_PACK.values()))
def test_corner_arc_is_anti_aliased(image_path):
    """The mask is area-sampled, so the arc carries partial alpha rather than a hard step."""
    card = load_card_image(image_path)
    radius = corner_radius_for(image_path, card.width)
    alpha = card.getchannel('A')

    partial = [
        alpha.getpixel((x, y))
        for x in range(radius + 2)
        for y in range(radius + 2)
        if 0 < alpha.getpixel((x, y)) < 255
    ]
    assert partial, 'expected partially transparent pixels along the corner arc'


@pytest.mark.parametrize('stem', [
    'zutomayocard_4th_105',
    'zutomayocard_4th_106',
    'zutomayocard_4th_107',
])
def test_set_4_se_cards_round_like_the_rest_of_the_pack(stem):
    """Regression: these shipped as square-cornered synthetic placeholders and were later
    replaced by real scans, which carry the same white dead space as every pack-4 card.
    The exemption that survived that swap left them with a visible white corner fringe.
    """
    image_path = f'zutomayo/images/4/{stem}.jpg'
    assert corner_radius_for(image_path, 700) == CORNER_RADIUS_BY_PACK_DIRECTORY['4']

    card = load_card_image(image_path)
    alpha = card.getchannel('A')
    for corner in [
        (0, 0),
        (card.width - 1, 0),
        (0, card.height - 1),
        (card.width - 1, card.height - 1),
    ]:
        assert alpha.getpixel(corner) == 0, f'{image_path} corner {corner} should be clear'


@pytest.mark.parametrize('stem', ['zutomayocard_2nd_36', 'zutomayocard_2nd_37'])
def test_threshold_bug_cards_now_get_the_full_pack_radius(stem):
    """Regression: these two shipped with a ~2 px radius and a visible off-white ring.

    Their dead space sits at luminance ~219, one point under the retired offline script's
    threshold of 220, so its detection walk stopped immediately. Per-pack constants are
    immune to that.
    """
    image_path = f'zutomayo/images/2/{stem}.jpg'
    assert corner_radius_for(image_path, 700) == CORNER_RADIUS_BY_PACK_DIRECTORY['2']
    assert load_card_image(image_path).getchannel('A').getpixel((0, 0)) == 0


def test_mask_is_cached_and_shared_across_cards_of_the_same_shape():
    """Building a mask costs far more than applying one, so the cache is load-bearing."""
    first = _rounded_corner_mask(700, 978, 15)
    second = _rounded_corner_mask(700, 978, 15)
    assert first is second


def test_zero_radius_mask_is_fully_opaque():
    mask = _rounded_corner_mask(20, 30, 0)
    assert mask.getextrema() == (255, 255)


def test_loaded_card_images_are_shared_and_survive_caller_resizing():
    path = CARD_PER_PACK['1']
    first = load_card_image(path)
    assert load_card_image(path) is first

    corner_alpha_before = first.getchannel('A').getpixel((0, 0))
    first.resize((100, 140))  # callers resize; it must not mutate the cached original
    assert load_card_image(path) is first
    assert first.getchannel('A').getpixel((0, 0)) == corner_alpha_before


def test_card_back_is_rounded_to_match_the_fronts():
    back = card_back_image()
    alpha = back.getchannel('A')
    assert alpha.getpixel((0, 0)) == 0
    assert alpha.getpixel((back.width // 2, back.height // 2)) == 255
    assert card_back_image() is back
