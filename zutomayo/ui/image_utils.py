"""Utilities for saving images within Discord's upload size limit."""

import io
import logging
from pathlib import PurePosixPath

import discord
from PIL import Image

logger = logging.getLogger(__name__)

DISCORD_UPLOAD_BYTE_LIMIT = 24 * 1024 * 1024  # 24 MB with safety margin


def _initial_quality_for_format(image_format: str) -> int:
    """Return the starting quality value for a given image format."""
    if image_format == "JPEG":
        return 95
    return 100


def save_image_for_discord(
    image: Image.Image,
    filename: str,
    byte_limit: int = DISCORD_UPLOAD_BYTE_LIMIT,
) -> discord.File:
    """Save an image at the highest quality that fits under *byte_limit*.

    The format is inferred from *filename*:
      - ``.webp`` → lossy WebP (supports RGBA transparency)
      - ``.jpg`` / ``.jpeg`` → JPEG

    The function starts at the maximum quality for the format and, if the
    resulting file exceeds *byte_limit*, uses binary search to find the
    highest quality that fits.
    """
    extension = PurePosixPath(filename).suffix.lower()
    format_map = {
        ".webp": "WEBP",
        ".jpg": "JPEG",
        ".jpeg": "JPEG",
    }
    image_format = format_map.get(extension, "WEBP")

    quality = _initial_quality_for_format(image_format)
    buffer = _save_to_buffer(image, image_format, quality)

    if buffer.tell() <= byte_limit:
        buffer.seek(0)
        return discord.File(buffer, filename=filename)

    # Binary search for the highest quality that stays under the limit.
    low = 1
    high = quality - 1
    best_buffer = buffer  # fallback to the initial save

    while low <= high:
        mid = (low + high) // 2
        candidate = _save_to_buffer(image, image_format, mid)
        if candidate.tell() <= byte_limit:
            best_buffer = candidate
            low = mid + 1  # try higher quality
        else:
            high = mid - 1  # need lower quality

    file_size_megabytes = best_buffer.tell() / 1024 / 1024
    logger.info(
        "Saved %s as %s quality=%d (%.2f MB)",
        filename,
        image_format,
        low - 1 if best_buffer is not buffer else quality,
        file_size_megabytes,
    )

    best_buffer.seek(0)
    return discord.File(best_buffer, filename=filename)


def _save_to_buffer(
    image: Image.Image,
    image_format: str,
    quality: int,
) -> io.BytesIO:
    """Save *image* to a BytesIO buffer and return it (position at end)."""
    buffer = io.BytesIO()
    image.save(buffer, format=image_format, quality=quality)
    return buffer
