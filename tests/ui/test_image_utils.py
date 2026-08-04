"""Tests for save_image_for_discord: format selection, alpha flattening, size search."""

from __future__ import annotations

import io
import random
import sys
from pathlib import Path

import pytest
from PIL import Image, JpegImagePlugin

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from zutomayo.ui.image_utils import (  # noqa: E402
    JPEG_QUALITY,
    save_image_for_discord,
    shrink_attachments_in_place,
    shrink_file_for_upload,
)


def _reopen(discord_file) -> Image.Image:
    """Decode the bytes a discord.File is carrying, leaving it sendable afterwards."""
    discord_file.fp.seek(0)
    image = Image.open(io.BytesIO(discord_file.fp.getvalue()))
    image.load()
    discord_file.fp.seek(0)
    return image


def _noisy_image(width: int = 900, height: int = 900) -> Image.Image:
    """An image that compresses badly, so a small byte limit forces the quality search."""
    rng = random.Random(0)
    image = Image.new('RGB', (width, height))
    image.putdata([
        (rng.randrange(256), rng.randrange(256), rng.randrange(256))
        for _ in range(width * height)
    ])
    return image


@pytest.mark.parametrize('filename', ['board.jpg', 'deck.jpeg', 'DECK.JPG'])
def test_jpeg_extensions_produce_jpeg(filename):
    result = save_image_for_discord(Image.new('RGB', (32, 32), (10, 20, 30)), filename)
    assert result.filename == filename
    assert _reopen(result).format == 'JPEG'


@pytest.mark.parametrize('filename', ['deck.webp', 'board.png', 'deck', 'deck.gif'])
def test_non_jpeg_extension_raises(filename):
    """A filename missed during the webp rename must fail loudly, not ship wrong bytes."""
    with pytest.raises(ValueError):
        save_image_for_discord(Image.new('RGB', (8, 8)), filename)


def test_rgba_image_is_flattened_rather_than_raising():
    """Pillow cannot write RGBA as JPEG, so the caller relies on this compositing."""
    transparent = Image.new('RGBA', (16, 16), (255, 0, 0, 0))
    reopened = _reopen(save_image_for_discord(transparent, 'grid.jpg'))
    assert reopened.format == 'JPEG'
    assert reopened.mode == 'RGB'


@pytest.mark.parametrize(
    'background,expected',
    [((255, 255, 255), 255), ((0, 0, 0), 0)],
)
def test_background_fills_transparent_pixels(background, expected):
    transparent = Image.new('RGBA', (16, 16), (0, 0, 0, 0))
    reopened = _reopen(save_image_for_discord(transparent, 'grid.jpg', background=background))
    for channel_value in reopened.getpixel((0, 0)):
        assert abs(channel_value - expected) <= 2


def test_palette_image_is_flattened():
    """board.png loads as mode P, so the P branch is a real production path."""
    palette_image = Image.new('RGBA', (16, 16), (7, 8, 9, 255)).convert('P')
    assert _reopen(save_image_for_discord(palette_image, 'grid.jpg')).mode == 'RGB'


def test_chroma_subsampling_is_disabled():
    """The load-bearing assertion of the quality story.

    Pillow silently defaults JPEG to 4:2:0 at every quality, which halves colour
    resolution and smears the small text and thin coloured outlines on card art. If the
    subsampling argument is ever dropped, nothing else in the suite would notice.
    """
    result = save_image_for_discord(_noisy_image(64, 64), 'board.jpg')
    assert JpegImagePlugin.get_sampling(_reopen(result)) == 0


def test_default_quality_is_unchanged():
    """Guards against the default drifting back up and blowing the size budget."""
    assert JPEG_QUALITY == 90


def test_quality_search_reduces_size_to_fit_limit():
    image = _noisy_image()
    unconstrained = save_image_for_discord(image, 'board.jpg')
    full_size = len(unconstrained.fp.getvalue())

    byte_limit = full_size // 2
    constrained = save_image_for_discord(image, 'board.jpg', byte_limit=byte_limit)
    assert len(constrained.fp.getvalue()) <= byte_limit


def test_quality_search_never_changes_dimensions():
    """Resolution is deliberately not a size lever: grids are read for their card text."""
    image = _noisy_image()
    full_size = len(save_image_for_discord(image, 'board.jpg').fp.getvalue())

    constrained = save_image_for_discord(image, 'board.jpg', byte_limit=full_size // 4)
    assert _reopen(constrained).size == image.size


def test_unsatisfiable_limit_still_returns_a_file():
    """Even a limit nothing can meet returns the smallest attempt rather than raising."""
    result = save_image_for_discord(_noisy_image(), 'board.jpg', byte_limit=1)
    assert len(result.fp.getvalue()) > 0


def test_shrink_file_for_upload_reencodes_over_limit():
    original = save_image_for_discord(_noisy_image(), 'board.jpg')
    original_size = len(original.fp.getvalue())

    smaller = shrink_file_for_upload(original, byte_limit=original_size // 2)
    assert smaller is not None
    assert len(smaller.fp.getvalue()) <= original_size // 2
    assert smaller.filename == 'board.jpg'
    assert _reopen(smaller).size == _reopen(original).size


def test_shrink_file_for_upload_returns_none_when_already_small_enough():
    already_fine = save_image_for_discord(Image.new('RGB', (16, 16)), 'board.jpg')
    assert shrink_file_for_upload(already_fine, byte_limit=10 * 1024 * 1024) is None


def test_shrink_attachments_rewrites_single_file_in_place():
    payload = {'content': 'hi', 'file': save_image_for_discord(_noisy_image(), 'board.jpg')}
    limit = len(payload['file'].fp.getvalue()) // 2

    assert shrink_attachments_in_place(payload, byte_limit=limit) is True
    assert len(payload['file'].fp.getvalue()) <= limit
    assert payload['content'] == 'hi'


def test_shrink_attachments_rewrites_file_list_in_place():
    payload = {
        'files': [
            save_image_for_discord(_noisy_image(), 'draft_box_1_1.jpg'),
            save_image_for_discord(_noisy_image(), 'draft_box_1_2.jpg'),
        ],
    }
    limit = min(len(f.fp.getvalue()) for f in payload['files']) // 2

    assert shrink_attachments_in_place(payload, byte_limit=limit) is True
    assert all(len(f.fp.getvalue()) <= limit for f in payload['files'])
    assert [f.filename for f in payload['files']] == ['draft_box_1_1.jpg', 'draft_box_1_2.jpg']


def test_shrink_attachments_reports_no_change_when_nothing_oversized():
    payload = {'file': save_image_for_discord(Image.new('RGB', (16, 16)), 'board.jpg')}
    assert shrink_attachments_in_place(payload, byte_limit=10 * 1024 * 1024) is False
