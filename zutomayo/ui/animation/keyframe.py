"""Data structures for the keyframe animation system.

``Keyframe`` is the user-facing type — a snapshot of properties at a
percentage point (0-100).  ``None`` means "not specified here"; the
interpolation engine will look forward/backward for the nearest
keyframes that *do* specify it, exactly like CSS ``@keyframes``.

``ResolvedProperties`` is the engine's output — every field has a
concrete value, ready to feed into the transform pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass
from zutomayo.ui.animation import CubicBezier, Easing


Rect = tuple[float, float, float, float]
"""(left, top, right, bottom) rectangle in scene coordinates."""


def _rect_to_components(rect: Rect) -> tuple[float, float, float, float]:
    """Decompose a rect into (center_x, center_y, width, height)."""
    l, t, r, b = rect
    return (l + r) / 2, (t + b) / 2, r - l, b - t


@dataclass(frozen=True)
class Keyframe:
    """A property snapshot at a percentage point in the animation."""

    pct: float
    """Percentage (0-100), like CSS ``@keyframes`` percentages."""

    x: float | None = None
    y: float | None = None
    width: float | None = None
    height: float | None = None
    scale: float | None = None
    rotate_z: float | None = None
    rotate_y: float | None = None
    opacity: float | None = None
    visible: bool | None = None

    rect: Rect | None = None
    """Convenience: ``(left, top, right, bottom)`` — decomposes into
    ``x``, ``y``, ``width``, ``height`` automatically.  Explicit values
    for those fields take precedence over the rect."""

    easing: Easing = 'linear'
    """Easing for the transition *into* this keyframe (per-segment)."""

    def __post_init__(self) -> None:
        if self.rect is not None:
            cx, cy, w, h = _rect_to_components(self.rect)
            if self.x is None:
                object.__setattr__(self, 'x', cx)
            if self.y is None:
                object.__setattr__(self, 'y', cy)
            if self.width is None:
                object.__setattr__(self, 'width', w)
            if self.height is None:
                object.__setattr__(self, 'height', h)


# Properties that are linearly interpolated between keyframes.
CONTINUOUS_PROPS: tuple[str,...] = ('x', 'y', 'width', 'height', 'scale', 'rotate_z', 'rotate_y', 'opacity')

# Properties that snap discretely (no interpolation).
DISCRETE_PROPS: tuple[str,...] = ('visible',)

ALL_PROPS: tuple[str,...] = CONTINUOUS_PROPS + DISCRETE_PROPS


@dataclass
class ResolvedProperties:
    """All properties resolved to concrete values at a specific time."""

    x: float = 0.0
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0
    scale: float = 1.0
    rotate_z: float = 0.0
    rotate_y: float = 0.0
    opacity: float = 1.0
    visible: bool = True
