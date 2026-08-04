"""Tests for send_with_retry: 5xx backoff and the oversized-attachment fallback."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import discord
import pytest
from PIL import Image

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from zutomayo.ui.image_utils import save_image_for_discord  # noqa: E402
from zutomayo.utils import discord_utils  # noqa: E402
from zutomayo.utils.discord_utils import (  # noqa: E402
    send_images_with_retry,
    send_with_retry,
)


class _FakeResponse:
    """Minimal stand-in for the aiohttp response HTTPException reads."""

    def __init__(self, status: int):
        self.status = status
        self.reason = 'Payload Too Large' if status == 413 else 'Server Error'


def _http_error(status: int) -> discord.errors.HTTPException:
    return discord.errors.HTTPException(_FakeResponse(status), 'boom')


def _noisy_file(filename: str = 'grid.jpg', size: int = 700) -> discord.File:
    """A real encoded attachment, large enough that shrinking it measurably works."""
    import random

    rng = random.Random(0)
    image = Image.new('RGB', (size, size))
    image.putdata([
        (rng.randrange(256), rng.randrange(256), rng.randrange(256))
        for _ in range(size * size)
    ])
    return save_image_for_discord(image, filename)


@pytest.fixture(autouse=True)
def _no_backoff_sleeping(monkeypatch):
    """The 5xx path sleeps up to 15 seconds; the tests only care about the control flow."""
    async def instant_sleep(_seconds):
        return None

    monkeypatch.setattr(discord_utils.asyncio, 'sleep', instant_sleep)


def test_returns_the_first_successful_result():
    calls = []

    async def factory():
        calls.append(1)
        return 'sent'

    assert asyncio.run(send_with_retry(factory)) == 'sent'
    assert len(calls) == 1


def test_retries_then_succeeds_on_server_error():
    attempts = []

    async def factory():
        attempts.append(1)
        if len(attempts) < 3:
            raise _http_error(500)
        return 'sent'

    assert asyncio.run(send_with_retry(factory)) == 'sent'
    assert len(attempts) == 3


def test_client_errors_are_not_retried():
    attempts = []

    async def factory():
        attempts.append(1)
        raise _http_error(403)

    with pytest.raises(discord.errors.HTTPException):
        asyncio.run(send_with_retry(factory))
    assert len(attempts) == 1, 'a 403 is not transient and must not be retried'


def test_oversized_attachment_is_shrunk_and_resent():
    """A 413 means this guild or DM has a lower ceiling than we assumed."""
    original = _noisy_file()
    original_size = len(original.fp.getvalue())
    send_kwargs = {'content': 'box 1', 'file': original}
    sizes_attempted = []

    async def factory():
        sizes_attempted.append(len(send_kwargs['file'].fp.getvalue()))
        if len(sizes_attempted) == 1:
            raise _http_error(413)
        return 'sent'

    result = asyncio.run(send_with_retry(
        factory,
        label='DM send',
        attachment_kwargs=send_kwargs,
        byte_limit=original_size // 2,
    ))

    assert result == 'sent'
    assert len(sizes_attempted) == 2
    assert sizes_attempted[1] < sizes_attempted[0], 'the retry must send fewer bytes'
    assert send_kwargs['content'] == 'box 1', 'only the attachments should be rewritten'


def test_oversized_attachment_is_only_shrunk_once():
    """If a smaller image is still rejected, give up rather than loop down to nothing."""
    original = _noisy_file()
    send_kwargs = {'file': original}
    attempts = []

    async def factory():
        attempts.append(1)
        raise _http_error(413)

    with pytest.raises(discord.errors.HTTPException):
        asyncio.run(send_with_retry(
            factory,
            attachment_kwargs=send_kwargs,
            byte_limit=len(original.fp.getvalue()) // 2,
        ))
    assert len(attempts) == 2, 'one original attempt plus one shrunken retry'


def test_413_without_attachments_is_not_retried():
    attempts = []

    async def factory():
        attempts.append(1)
        raise _http_error(413)

    with pytest.raises(discord.errors.HTTPException):
        asyncio.run(send_with_retry(factory))
    assert len(attempts) == 1


def test_send_images_with_retry_passes_its_own_kwargs_through():
    """The helper must hand the same dict to the retry logic that the send unpacks."""
    original = _noisy_file()
    received = []
    sizes_attempted = []

    async def fake_send(**kwargs):
        sizes_attempted.append(len(kwargs['file'].fp.getvalue()))
        if len(sizes_attempted) == 1:
            raise _http_error(413)
        received.append(kwargs)
        return 'sent'

    result = asyncio.run(send_images_with_retry(
        fake_send,
        label='image followup',
        file=original,
        ephemeral=True,
        byte_limit=len(original.fp.getvalue()) // 2,
    ))

    assert result == 'sent'
    assert sizes_attempted[1] < sizes_attempted[0]
    assert received[0]['ephemeral'] is True
    assert 'byte_limit' not in received[0], 'byte_limit is ours, not a Discord kwarg'
