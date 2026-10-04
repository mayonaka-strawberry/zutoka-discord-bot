"""Utilities for saving images within Discord's upload size limit."""

import io
import logging
from pathlib import Path, PurePosixPath
from typing import Optional

import discord
from PIL import Image

logger = logging.getLogger(__name__)

DISCORD_UPLOAD_BYTE_LIMIT = 24 * 1024 * 1024  # 24 MB with safety margin

# Retry budget after Discord rejects an upload: unboosted guilds and all DMs allow 10 MiB.
FALLBACK_UPLOAD_BYTE_LIMIT = int(9.5 * 1024 * 1024)

# Chroma subsampling off (4:4:4): Pillow's default 4:2:0, at any quality, smears the thin
# outlines and small text on card art.
JPEG_QUALITY = 90
JPEG_SUBSAMPLING = 0

JPEG_FORMAT = 'JPEG'
_FORMAT_BY_EXTENSION = {
    '.jpg': JPEG_FORMAT,
    '.jpeg': JPEG_FORMAT,
}


def save_image_for_discord(
    image: Image.Image,
    filename: str,
    byte_limit: int = DISCORD_UPLOAD_BYTE_LIMIT,
    background: tuple[int, int, int] = (255, 255, 255),
) -> discord.File:
    """Save as JPEG at the highest quality, at most JPEG_QUALITY, under *byte_limit*;
    dimensions are never reduced, so card text stays readable. *filename* must end in .jpg
    or .jpeg. Transparency is composited onto *background*."""
    extension = PurePosixPath(filename).suffix.lower()
    if extension not in _FORMAT_BY_EXTENSION:
        raise ValueError(
            f'unsupported image extension {extension!r} for {filename!r}: '
            f'expected one of {sorted(_FORMAT_BY_EXTENSION)}'
        )
    image_format = _FORMAT_BY_EXTENSION[extension]

    image = flatten_for_jpeg(image, background)

    quality = JPEG_QUALITY
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
        'Saved %s as %s quality=%d (%.2f MB)',
        filename,
        image_format,
        low - 1 if best_buffer is not buffer else quality,
        file_size_megabytes,
    )

    best_buffer.seek(0)
    return discord.File(best_buffer, filename=filename)


def shrink_file_for_upload(
    file: discord.File,
    byte_limit: int = FALLBACK_UPLOAD_BYTE_LIMIT,
) -> Optional[discord.File]:
    """Re-encode an attachment Discord rejected as too large, to fit *byte_limit*. None if
    it is not a re-encodable image or already fits. Works from the JPEG bytes, so the
    source image need not be kept around."""
    buffer = getattr(file, 'fp', None)
    if not isinstance(buffer, io.BytesIO):
        return None

    encoded = buffer.getvalue()
    if len(encoded) <= byte_limit:
        return None

    try:
        with Image.open(io.BytesIO(encoded)) as opened:
            decoded = opened.convert('RGB')
    except Exception:
        logger.warning('Could not re-encode %s to fit %d bytes', file.filename, byte_limit)
        return None

    # Already RGB, so the background argument is never consulted.
    return save_image_for_discord(decoded, file.filename, byte_limit=byte_limit)


def shrink_attachments_in_place(
    send_kwargs: dict,
    byte_limit: int = FALLBACK_UPLOAD_BYTE_LIMIT,
) -> bool:
    """Shrink any oversized images in a ``send(**kwargs)`` payload. True if anything changed.

    Rewrites ``file`` / ``files`` in the dict so a caller holding a closure over it can
    simply retry the same send.
    """
    changed = False

    single_file = send_kwargs.get('file')
    if isinstance(single_file, discord.File):
        smaller = shrink_file_for_upload(single_file, byte_limit)
        if smaller is not None:
            send_kwargs['file'] = smaller
            changed = True

    file_list = send_kwargs.get('files')
    if file_list:
        rebuilt = []
        for candidate in file_list:
            smaller = (
                shrink_file_for_upload(candidate, byte_limit)
                if isinstance(candidate, discord.File)
                else None
            )
            if smaller is not None:
                changed = True
                rebuilt.append(smaller)
            else:
                rebuilt.append(candidate)
        if changed:
            send_kwargs['files'] = rebuilt

    return changed


def flatten_for_jpeg(
    image: Image.Image,
    background: tuple[int, int, int] = (255, 255, 255),
) -> Image.Image:
    """Return `image` as RGB, compositing transparency onto `background` (JPEG has no alpha)."""
    if image.mode == 'RGB':
        return image

    if image.mode == 'P' or 'A' in image.getbands():
        source = image.convert('RGBA')
        flattened = Image.new('RGB', source.size, background)
        flattened.paste(source, mask=source.getchannel('A'))
        return flattened

    return image.convert('RGB')


def save_jpeg_file(
    image: Image.Image,
    path,
    background: tuple[int, int, int] = (0, 0, 0),
) -> int:
    """Write *image* to *path* with the upload JPEG settings; returns the byte size. For the
    developer scripts: no quality search, and a black default background like the board."""
    flatten_for_jpeg(image, background).save(
        path,
        JPEG_FORMAT,
        quality=JPEG_QUALITY,
        subsampling=JPEG_SUBSAMPLING,
    )
    return Path(path).stat().st_size


def _save_to_buffer(
    image: Image.Image,
    image_format: str,
    quality: int,
) -> io.BytesIO:
    """Save *image* to a BytesIO buffer and return it (position at end)."""
    buffer = io.BytesIO()
    image.save(
        buffer,
        format=image_format,
        quality=quality,
        subsampling=JPEG_SUBSAMPLING,
    )
    return buffer
