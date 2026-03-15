"""Populate the 'image' field in cards.json with paths to card images."""

import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CARDS_JSON = PROJECT_ROOT / "zutomayo" / "data" / "cards.json"
IMAGES_DIR = PROJECT_ROOT / "zutomayo" / "images"

ORDINAL_SUFFIX = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th"}


def get_image_path(pack: int, card_id: int) -> str:
    suffix = ORDINAL_SUFFIX[pack]
    if pack == 4:
        filename = f"zutomayocard_{suffix}_{card_id:03d}.png"
    else:
        filename = f"zutomayocard_{suffix}_{card_id}.png"
    return f"zutomayo/images/{pack}/{filename}"


def main():
    with open(CARDS_JSON, "r", encoding="utf-8") as f:
        data = json.load(f)

    matched = 0
    errors = []

    for card in data["cards"]:
        image_path = get_image_path(card["pack"], card["id"])
        full_path = PROJECT_ROOT / image_path
        if full_path.is_file():
            card["image"] = image_path
            matched += 1
        else:
            errors.append(
                f"MISSING: Pack {card['pack']} ID {card['id']} "
                f"({card['name']}) -> {image_path}"
            )

    with open(CARDS_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"Matched: {matched}/{len(data['cards'])}")
    if errors:
        print(f"\nERRORS ({len(errors)}):")
        for e in errors:
            print(f"  {e}")
    else:
        print("No errors - all cards matched to images.")


if __name__ == "__main__":
    main()
