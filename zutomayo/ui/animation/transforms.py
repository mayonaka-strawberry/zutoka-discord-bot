"""PIL image transform pipeline.

Applies resolved properties to a source image in the correct order:
    1. Visibility check
    2. Scale
    3. Rotate Y (fake 3D flip via horizontal squeeze)
    4. Rotate Z (simple 2D rotation)
    5. Opacity (alpha multiply)
    6. Position (center-anchored)
"""

from __future__ import annotations

import math

from PIL import Image

from zutomayo.ui.animation.keyframe import ResolvedProperties


def apply_transforms(
    source: Image.Image,
    props: ResolvedProperties,
) -> tuple[Image.Image, tuple[int, int]] | None:
    """Apply all transforms to a source image.

    Returns ``(transformed_image, (paste_x, paste_y))`` or ``None`` if
    the object is invisible and should be skipped entirely.
    """
    if not props.visible:
        return None

    img = source.copy().convert('RGBA')

    # Explicit width/height (from rect-based keyframes) takes priority
    if props.width > 0 and props.height > 0:
        new_w = max(1, round(props.width * props.scale))
        new_h = max(1, round(props.height * props.scale))
        img = img.resize((new_w, new_h))
    elif props.scale != 1.0:
        # Relative scale (legacy path)
        new_w = max(1, round(img.width * props.scale))
        new_h = max(1, round(img.height * props.scale))
        img = img.resize((new_w, new_h))

    # Rotate Y (fake 3D flip via horizontal squeeze)
    if props.rotate_y != 0.0:
        squeeze = abs(math.cos(math.radians(props.rotate_y)))
        new_w = max(1, round(img.width * squeeze))
        img = img.resize((new_w, img.height))

    # Rotate Z (clockwise positive, like CSS)
    if props.rotate_z != 0.0:
        img = img.rotate(-props.rotate_z, expand=True)

    # Opacity
    if props.opacity < 1.0:
        r, g, b, a = img.split()
        a = a.point(lambda p: round(p * props.opacity))
        img = Image.merge('RGBA', (r, g, b, a))

    # Position (x, y is the center of the object)
    paste_x = round(props.x - img.width / 2)
    paste_y = round(props.y - img.height / 2)

    return img, (paste_x, paste_y)
