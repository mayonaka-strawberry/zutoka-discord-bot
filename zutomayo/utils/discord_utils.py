from __future__ import annotations

import asyncio
import logging
from typing import Callable, Coroutine, Any, TypeVar

import discord

log = logging.getLogger(__name__)

MAX_SEND_ATTEMPTS = 5

# Discord answers an oversized attachment with 413 Payload Too Large.
UPLOAD_TOO_LARGE_STATUS = 413

T = TypeVar('T')


async def send_with_retry(
    coroutine_factory: Callable[[], Coroutine[Any, Any, T]],
    label: str = 'send',
    attachment_kwargs: dict[str, Any] | None = None,
    byte_limit: int | None = None,
) -> T:
    """Call coroutine_factory() and retry up to MAX_SEND_ATTEMPTS times on 5xx errors.

    If *attachment_kwargs* is given, it must be the very dict the factory unpacks into its
    send call. A 413 (attachment too large, meaning this guild or DM has a lower upload
    ceiling than we assumed) then re-encodes the images in that dict to *byte_limit* and
    retries once, so the message goes out at reduced quality instead of failing outright.
    Shrinking happens at most once: if the smaller image is rejected too, the error is
    raised rather than shrinking repeatedly toward nothing.
    """
    shrunk_attachments = False

    for attempt in range(MAX_SEND_ATTEMPTS):
        try:
            return await coroutine_factory()
        except (discord.errors.DiscordServerError, discord.errors.HTTPException) as error:
            if (
                attachment_kwargs is not None
                and not shrunk_attachments
                and isinstance(error, discord.errors.HTTPException)
                and error.status == UPLOAD_TOO_LARGE_STATUS
                and attempt < MAX_SEND_ATTEMPTS - 1
            ):
                from zutomayo.ui.image_utils import (
                    FALLBACK_UPLOAD_BYTE_LIMIT,
                    shrink_attachments_in_place,
                )

                shrink_limit = (
                    FALLBACK_UPLOAD_BYTE_LIMIT if byte_limit is None else byte_limit
                )
                if shrink_attachments_in_place(attachment_kwargs, shrink_limit):
                    shrunk_attachments = True
                    log.warning(
                        '%s rejected as too large (attempt %d/%d), retrying with '
                        'smaller images: %s',
                        label, attempt + 1, MAX_SEND_ATTEMPTS, error,
                    )
                    continue

            is_retryable = (
                isinstance(error, discord.errors.DiscordServerError)
                or (isinstance(error, discord.errors.HTTPException) and error.status >= 500)
            )
            if is_retryable and attempt < MAX_SEND_ATTEMPTS - 1:
                delay = 2 ** attempt  # 1s, 2s, 4s, 8s
                log.warning(
                    '%s failed (attempt %d/%d), retrying in %ds: %s',
                    label, attempt + 1, MAX_SEND_ATTEMPTS, delay, error,
                )
                await asyncio.sleep(delay)
            else:
                raise


async def send_images_with_retry(
    send_callable: Callable[..., Coroutine[Any, Any, T]],
    label: str = 'send',
    byte_limit: int | None = None,
    **send_kwargs: Any,
) -> T:
    """Send a message carrying rendered images, shrinking them if Discord rejects the size.

    Wraps the send sites that talk to an interaction or channel directly, rather than
    through a match transport. Everything in *send_kwargs* is forwarded verbatim to
    *send_callable*, and that same dict is handed to send_with_retry so the 413 path can
    rewrite the attachments in place and retry the identical call. ``byte_limit`` is named
    explicitly so it is consumed here rather than forwarded to Discord.
    """
    return await send_with_retry(
        lambda: send_callable(**send_kwargs),
        label=label,
        attachment_kwargs=send_kwargs,
        byte_limit=byte_limit,
    )
