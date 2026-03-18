"""Property resolution engine — the heart of the CSS-keyframe system.

CSS resolves each property **independently**.  For a given percentage
``pct``, the engine:

1. Filters keyframes to only those where the property is specified.
2. Finds the surrounding pair (last <= pct, first >= pct).
3. Computes local progress within that segment.
4. Applies the *destination* keyframe's easing (per-segment easing).
5. Lerps (continuous) or snaps (discrete) the value.

The ``KeyframeResolver`` pre-indexes keyframes by property on
construction so that per-frame lookups avoid redundant filtering.
"""

from __future__ import annotations

from zutomayo.ui.animation.easing import Easing, apply_easing
from zutomayo.ui.animation.keyframe import (
    ALL_PROPS,
    DISCRETE_PROPS,
    Keyframe,
    ResolvedProperties,
)

class KeyframeResolver:
    """Resolves animated properties at arbitrary percentages.
    """

    def __init__(self, keyframes: list[Keyframe]) -> None:
        """Expects at least one keyframe, IN ORDER"""
        
        if not keyframes:
            raise ValueError('At least one keyframe is required')

        # Three parallel lists per property:
        #   pcts -> pcts: [0, 50, 100]
        #   values -> [0.3, 1.0, 1.0]
        #   easings -> ['linear', 'ease_out', 'linear']
        self._pcts: dict[str, list[float]] = {}
        self._values: dict[str, list[float | bool]] = {}
        self._easings: dict[str, list[Easing]] = {}

        for prop in ALL_PROPS:
            pcts: list[float] = []
            values: list[float | bool] = []
            easings: list[Easing] = []

            for kf in keyframes:
                val = getattr(kf, prop)
                if val is not None:
                    pcts.append(kf.pct)
                    values.append(val)
                    easings.append(kf.easing)
            
            self._pcts[prop] = pcts
            self._values[prop] = values
            self._easings[prop] = easings

    def at(self, pct: float) -> ResolvedProperties:
        """Resolve all properties at a given percentage."""

        resolved = {}

        defaults = ResolvedProperties()
        for prop in ALL_PROPS:
            resolved[prop] = getattr(defaults, prop)

        for prop in ALL_PROPS:
            pcts = self._pcts[prop]
            values = self._values[prop]
            easings = self._easings[prop]

            if len(pcts) <= 1:
                if pcts:
                    resolved[prop] = values[0]
                continue

            # Find surrounding keyframe pair
            left_i = None
            right_i = None
            found_right = False
            for idx, p in enumerate(pcts):
                if p <= pct:
                    left_i = idx
                if p >= pct and found_right is False:
                    right_i = idx
                    found_right = True
            
            # clip to the nearest keyframe if the point is in
            # before any keyframes or after all keyframes 
            if left_i is None:
                resolved[prop] = values[0]
                continue
            if right_i is None:
                resolved[prop] = values[-1]
                continue
            
            # Local progress within this segment (0.0 to 1.0)
            # divide by zero guard
            if pcts[left_i] == pcts[right_i]:
                resolved[prop] = values[left_i]
                continue
            progress = (pct - pcts[left_i]) / (pcts[right_i] - pcts[left_i])

            # Apply the destination keyframe's easing
            eased_t = apply_easing(progress, easings[right_i])

            if prop in DISCRETE_PROPS:
                resolved[prop] = values[left_i] if eased_t < 1.0 else values[right_i]
            else:
                resolved[prop] = values[left_i] + (values[right_i] - values[left_i]) * eased_t

        return ResolvedProperties(
            x=resolved["x"],
            y=resolved["y"],
            width=resolved["width"],
            height=resolved["height"],
            scale=resolved["scale"],
            rotate_z=resolved["rotate_z"],
            rotate_y=resolved["rotate_y"],
            opacity=resolved["opacity"],
            visible=resolved["visible"],
        )
