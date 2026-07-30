"""Utilities for saving images within Discord's upload size limit."""

import io
import logging
from pathlib import Path, PurePosixPath
from typing import Optional

import discord
from PIL import Image

logger = logging.getLogger(__name__)

DISCORD_UPLOAD_BYTE_LIMIT = 24 * 1024 * 1024  # 24 MB with safety margin

# Discord's real per-attachment ceiling is 10 MiB unless the guild is Boost Level 2 or
# higher, and DMs are capped at 10 MiB whatever the guild's tier. The limit above assumes
# the boosted ceiling; this one is the retry budget used when Discord rejects an upload.
FALLBACK_UPLOAD_BYTE_LIMIT = int(9.5 * 1024 * 1024)

# Chroma subsampling, not the quality number, is what limits image fidelity here. Pillow
# defaults JPEG to 4:2:0 at every quality including 100, which halves colour resolution and
# smears the thin coloured glyph outlines and small text on this card art. Turning it off
# (subsampling=0, i.e. 4:4:4) is worth about 10 dB PSNR and drops max channel error from
# ~100 to ~30, for roughly 20% more bytes. q90 at 4:4:4 is both higher quality and smaller
# than the q95 4:2:0 this replaced.
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
    """Save an image at the highest quality that fits under *byte_limit*.

    The format is inferred from *filename*, which must end in ``.jpg`` or ``.jpeg``; any
    other extension raises. The pipeline is JPEG-only on purpose, so that a filename which
    was missed during a rename fails loudly instead of quietly shipping one format's bytes
    under another format's extension.

    JPEG cannot store transparency, so an image with an alpha channel is composited onto
    *background* first. Grid images pass white; the board composites onto black itself,
    before it reaches here, because its cards sit on board art.

    Quality is the only size-reduction lever: the function starts at ``JPEG_QUALITY`` and,
    if the result exceeds *byte_limit*, binary-searches for the highest quality that fits.
    Image dimensions are never reduced -- card grids are what players open to read effect
    text, so resolution is preserved even when quality is not.
    """
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
    """Re-encode an already-encoded attachment to fit under *byte_limit*.

    Used when Discord rejects an upload as too large, which means the guild (or DM) has a
    lower ceiling than ``DISCORD_UPLOAD_BYTE_LIMIT`` assumed. Returns ``None`` if the file
    is not a re-encodable image or already fits, so callers can tell "nothing to do" from
    "here is a smaller one".

    This re-encodes from the JPEG bytes rather than from the source image, which costs one
    extra generation of loss. That is the right trade: it fires only for an image that
    would otherwise not send at all, and the alternative is holding a decoded 50+ MB source
    image alive for every send that might fail.
    """
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
    """Return image as RGB, compositing any transparency onto background.

    Pillow raises ``OSError: cannot write mode RGBA as JPEG``, so this is required rather
    than cosmetic. It runs once, before the quality search, because the search may re-encode
    the same image several times.
    """
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
    """Write *image* to *path* as JPEG at the pipeline's quality. Returns the byte size.

    For local files written by the developer scripts (calibration overlays, render previews),
    so they use the same quality and chroma settings as everything the bot uploads and cannot
    drift from it.

    Deliberately has no quality search: a local file has no upload budget, and quietly
    degrading a diagnostic image to hit a byte target would defeat its purpose. The default
    background is black to match ``compose_board_image``, whose board art carries a small
    number of transparent pixels.
    """
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
