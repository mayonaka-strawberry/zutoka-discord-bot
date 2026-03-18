"""Scene graph and rendering loop.

A ``Scene`` holds ``SceneObject`` instances, each with a source image
and a list of keyframes.  Calling ``scene.render()`` produces a list of
PIL frames by:

    for each frame:
        pct = frame_index / (total_frames - 1) * 100
        for each object (sorted by z_index):
            resolve properties at pct
            apply transforms to source image
            composite onto canvas
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import discord
from PIL import Image
import io
from dataclasses import replace

from zutomayo.ui.animation.easing import Easing
from zutomayo.ui.animation.interpolation import KeyframeResolver
from zutomayo.ui.animation.keyframe import Keyframe, Rect, ResolvedProperties
from zutomayo.ui.animation.transforms import apply_transforms


class SceneObject:
    """A single animatable element in the scene."""

    def __init__(
        self,
        image: Image.Image,
        z_index: int = 0,
        back_image: Image.Image | None = None,
    ) -> None:
        self.source_image = image.convert('RGBA')
        self.back_image = back_image.convert('RGBA') if back_image else None
        self.resolver: KeyframeResolver | None = None
        self.z_index = z_index

    def animate(self, keyframes: list[Keyframe]) -> SceneObject:
        """Set keyframe animation.  Returns self for chaining."""
        self.resolver = KeyframeResolver(keyframes)
        return self

    # ------------------------------------------------------------------
    # Animation presets
    # ------------------------------------------------------------------

    def place(
        self,
        rect: Rect | None = None,
        *,
        x: float | None = None,
        y: float | None = None,
    ) -> SceneObject:
        """Place the object statically (single keyframe at 0%).

        Accepts either a *rect* or explicit *x*/*y* coordinates.
        """
        return self.animate([
            Keyframe(
                pct=0, 
                rect=rect, 
                x=x, 
                y=y
                )
            ])

    def slide(
        self,
        from_rect: Rect | None = None,
        to_rect: Rect | None = None,
        *,
        from_pos: tuple[float, float] | None = None,
        to_pos: tuple[float, float] | None = None,
        flip: bool = False,
        easing: Easing = 'ease_in_out',
    ) -> SceneObject:
        """Slide from one position to another, optionally with a card flip.

        Accepts either rects or ``(x, y)`` tuples for each endpoint.
        """
        from_x, from_y = from_pos if from_pos is not None else (None, None)
        to_x, to_y = to_pos if to_pos is not None else (None, None)

        return self.animate([
            Keyframe(
                pct=0, 
                rect=from_rect, 
                x=from_x, 
                y=from_y, 
                rotate_y=0
                ),
            Keyframe(
                pct=100,
                rect=to_rect,
                x=to_x,
                y=to_y,
                rotate_y=180 if flip else None,
                easing=easing,
            )
        ])

    def fade_in(
        self,
        rect: Rect | None = None,
        *,
        x: float | None = None,
        y: float | None = None,
        easing: Easing = 'ease_out',
    ) -> SceneObject:
        """Fade in at a position (opacity 0 → 1)."""
        return self.animate([
            Keyframe(
                pct=0, 
                rect=rect, 
                x=x, 
                y=y, 
                opacity=0
            ),
            Keyframe(
                pct=100, 
                opacity=1, 
                easing=easing
            ),
        ])

    def fade_out(
        self,
        rect: Rect | None = None,
        *,
        x: float | None = None,
        y: float | None = None,
        easing: Easing = 'ease_in',
    ) -> SceneObject:
        """Fade out at a position (opacity 1 → 0)."""
        return self.animate([
            Keyframe(
                pct=0, 
                rect=rect, 
                x=x, 
                y=y, 
                opacity=1
            ),
            Keyframe(
                pct=100, 
                opacity=0, 
                easing=easing
            ),
        ])


class Scene:
    """Container for animated objects. Renders frame-by-frame."""

    def __init__(
        self,
        width: int,
        height: int,
        fps: int = 20,
        duration: float = 2.0,
        background: tuple[int, int, int, int] = (0, 0, 0, 0),
        render_scale: float = 1.0,
    ) -> None:
        self.width = width
        self.height = height
        self.fps = fps
        self.duration = duration
        self.background = background
        self.render_scale = render_scale
        self._objects: list[SceneObject] = []
        self._next_z = 0

    def add(
        self,
        image: Image.Image,
        z_index: int | None = None,
        back_image: Image.Image | None = None,
        position: tuple[float, float] | None = None,
        rect: Rect | None = None,
    ) -> SceneObject:
        """Add an image to the scene.  Returns the ``SceneObject``.

        *position* ``(x, y)`` or *rect* ``(l, t, r, b)`` automatically
        places the object with a single static keyframe so the caller
        doesn't need to call ``.animate()`` or ``.place()`` manually.
        """
        if z_index is None:
            z_index = self._next_z
            self._next_z += 1
        obj = SceneObject(image, z_index, back_image=back_image)
        self._objects.append(obj)

        if rect is not None:
            obj.place(rect=rect)
        elif position is not None:
            obj.place(x=position[0], y=position[1])

        return obj

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def render(self) -> list[Image.Image]:
        """Render every frame of the animation.

        Frames are independent of each other, so we render them in
        parallel using threads.  Pillow releases the GIL during its
        C-level image operations (resize, rotate, paste), so threads
        get real concurrency for the heavy work.
        """
        total_frames = max(1, round(self.fps * self.duration))
        sorted_objects = sorted(self._objects, key=lambda o: o.z_index)

        pcts = [
            (idx / max(1, total_frames - 1)) * 100.0
            for idx in range(total_frames)
        ]

        with ThreadPoolExecutor() as pool:
            frames = list(pool.map(
                lambda pct: self._render_frame(sorted_objects, pct),
                pcts,
            ))
        return frames

    def _render_frame(
        self,
        objects: list[SceneObject],
        pct: float,
    ) -> Image.Image:
        """Composite one frame at percentage *pct*."""
        s = self.render_scale
        canvas_w = max(1, round(self.width * s))
        canvas_h = max(1, round(self.height * s))
        canvas = Image.new('RGBA', (canvas_w, canvas_h), self.background)

        for obj in objects:
            if obj.resolver is None:
                continue

            props = obj.resolver.at(pct)

            # Apply render_scale to position and size.
            # When width/height are explicit (rect-based), they already
            # define the target size — don't also scale `scale`, or the
            # image shrinks twice.
            if s != 1.0:
                has_explicit_size = props.width > 0 and props.height > 0
                props = replace(
                    props,
                    x=props.x * s,
                    y=props.y * s,
                    width=props.width * s,
                    height=props.height * s,
                    scale=props.scale if has_explicit_size else props.scale * s,
                )

            # Pick front or back image based on Y rotation.
            # Past 90° (mod 360) the card is facing away.
            if obj.back_image is not None:
                angle = props.rotate_y % 360
                showing_back = 90 < angle < 270
                source = obj.back_image if showing_back else obj.source_image
            else:
                source = obj.source_image

            result = apply_transforms(source, props)

            if result is None:
                continue

            transformed, (paste_x, paste_y) = result
            canvas.paste(transformed, (paste_x, paste_y), transformed)

        return canvas
    
    def render_to_file(
        self,
        filename: str = 'animation.gif',
    ) -> discord.File:
        """Render and encode as an animated GIF for Discord. Plays once."""
        frames = self.render()

        rgb_frames = []
        for frame in frames:
            bg = Image.new("RGB", frame.size, (0,0,0))
            bg.paste(frame, mask=frame.split()[3])
            rgb_frames.append(bg)

        buf = io.BytesIO()
        rgb_frames[0].save(
            buf,
            format = "GIF",
            append_images=rgb_frames[1:],
            duration=round(100 / self.fps) * 10,
        )
        raw_gif = buf.getvalue()
        buf = io.BytesIO(raw_gif)

        return discord.File(buf, filename=filename)


