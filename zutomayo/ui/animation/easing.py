"""Easing functions built on cubic-bezier curves, matching CSS spec values.

A CSS ``cubic-bezier(x1, y1, x2, y2)`` defines a parametric Bezier curve
with four control points: P0=(0,0), P1=(x1,y1), P2=(x2,y2), P3=(1,1).

Both axes are parameterized by ``s`` (0-1):
    X(s) = 3(1-s)^2 * s * x1  +  3(1-s) * s^2 * x2  +  s^3
    Y(s) = 3(1-s)^2 * s * y1  +  3(1-s) * s^2 * y2  +  s^3

Given an input time ``t`` (0-1), the easing function must:
    1. Find the parameter ``s`` where X(s) = t  (Newton-Raphson)
    2. Return Y(s) as the eased progress

This is exactly how browsers evaluate CSS transitions.
"""

class CubicBezier:
    """A cubic-bezier easing curve, callable as ``curve(t) -> eased_t``."""

    def __init__(self, x1: float, y1: float, x2: float, y2: float) -> None:
        self.x1 = x1
        self.y1 = y1
        self.x2 = x2
        self.y2 = y2

    def _sample_x(self, s: float) -> float:
        return 3.0 * (1.0 - s) ** 2 * s * self.x1 + 3.0 * (1.0 - s) * s ** 2 * self.x2 + s ** 3

    def _sample_y(self, s: float) -> float:
        return 3.0 * (1.0 - s) ** 2 * s * self.y1 + 3.0 * (1.0 - s) * s ** 2 * self.y2 + s ** 3

    def _dx_ds(self, s: float) -> float:
        """Derivative dX/ds, needed for Newton-Raphson."""
        return (
            3.0 * self.x1 * (1.0 - s) ** 2
            + 6.0 * (self.x2 - self.x1) * (1.0 - s) * s
            + 3.0 * (1.0 - self.x2) * s ** 2
        )

    def __call__(self, t: float) -> float:
        if t <= 0.0:
            return 0.0
        if t >= 1.0:
            return 1.0

        # Newton-Raphson: find s where X(s) = t
        s = t
        for _ in range(8):
            x = self._sample_x(s) - t
            dx = self._dx_ds(s)
            if abs(dx) < 1e-12:
                break
            s -= x / dx
            s = max(0.0, min(1.0, s))

        return self._sample_y(s)

    def __repr__(self) -> str:
        return f'CubicBezier({self.x1}, {self.y1}, {self.x2}, {self.y2})'




# ---------------------------------------------------------------------------
# Presets matching CSS named timing functions
# ---------------------------------------------------------------------------

Easing = str | CubicBezier

EASING_PRESETS: dict[str, CubicBezier] = {
    'linear': CubicBezier(0.0, 0.0, 1.0, 1.0),
    'ease': CubicBezier(0.25, 0.1, 0.25, 1.0),
    'ease_in': CubicBezier(0.42, 0.0, 1.0, 1.0),
    'ease_out': CubicBezier(0.0, 0.0, 0.58, 1.0),
    'ease_in_out': CubicBezier(0.42, 0.0, 0.58, 1.0),
}


def apply_easing(t: float, easing: Easing) -> float:
    """Apply an easing function by name or CubicBezier instance."""
    if isinstance(easing, CubicBezier):
        return easing(t)
    fn = EASING_PRESETS.get(easing)
    if fn is None:
        raise ValueError(f'Unknown easing: {easing!r}')
    return fn(t)
