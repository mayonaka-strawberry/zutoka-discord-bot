"""
One-off, already run: convert the three pack-4 placeholder cards (105-107) from png to
jpg, the only cards never shipped as jpg. Saved above the render quality, since every
render re-encodes them. Touches only those three files.
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
