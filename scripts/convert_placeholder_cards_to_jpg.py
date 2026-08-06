"""
Convert the three pack-4 placeholder cards from png to jpg.

Cards 105, 106 and 107 are synthetic dark-background text placeholders rather than
scans, so unlike every other card they were never shipped as a jpg. The renderer now
loads card art exclusively from the jpg sources, so these three need a jpg of their own
before the png derivatives are removed.

Written at a higher quality than the render pipeline uses: these are source assets that
every render re-encodes downstream, so they sit above the output quality to avoid
stacking generation loss. The placeholders were exempt from corner rounding while they
shipped, because the placeholder art had no white dead space to remove; they have since
been replaced by real scans and now round with the normal pack-4 radius.

This is a one-off. It touches only the three named files and never reads or rewrites the
422 real card scans.
"""

from pathlib import Path

from PIL import Image


PROJECT_ROOT = Path(__file__).resolve().parent.parent
PACK_4_DIR = PROJECT_ROOT / "zutomayo" / "images" / "4"

PLACEHOLDER_STEMS = (
    "zutomayocard_4th_105",
    "zutomayocard_4th_106",
    "zutomayocard_4th_107",
)

SOURCE_QUALITY = 95


def convert_placeholder(stem: str) -> str:
    """Convert one placeholder png to jpg. Returns a one-line status message."""
    source_path = PACK_4_DIR / f"{stem}.png"
    destination_path = PACK_4_DIR / f"{stem}.jpg"

    if destination_path.is_file():
        return f"SKIP    {destination_path.name} (already exists)"
    if not source_path.is_file():
        return f"MISSING {source_path.name}"

    with Image.open(source_path) as image:
        # The placeholders are already opaque, so this drops a channel rather than
        # compositing anything away.
        image.convert("RGB").save(
            destination_path,
            "JPEG",
            quality=SOURCE_QUALITY,
            subsampling=0,
        )

    kilobytes = destination_path.stat().st_size / 1024
    return f"WROTE   {destination_path.name} ({kilobytes:.0f} KB)"


def main() -> None:
    for stem in PLACEHOLDER_STEMS:
        print(convert_placeholder(stem))

    jpg_count = len(list(PACK_4_DIR.glob("*.jpg")))
    print(f"\nPack 4 now has {jpg_count} jpg files (expected 107).")


if __name__ == "__main__":
    main()
