"""Card database loader.

Loads zutomayo/data/cards.json (read purely as data) into immutable CardDef
NamedTuples plus flat numpy arrays for hot-path lookups. A card *definition*
is identified everywhere in engine_alpha by its dense index 0..NUM_CARDS-1.

Categorical vocabularies (song, rarity, effect id) are derived from the data
so a new card set extends them automatically.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import NamedTuple

import numpy as np

_CARDS_JSON_PATH = Path(__file__).resolve().parent.parent / "zutomayo" / "data" / "cards.json"

# Card types
TYPE_CHARACTER = 0
TYPE_ENCHANT = 1
TYPE_AREA_ENCHANT = 2
CARD_TYPE_NAMES = ("CHARACTER", "ENCHANT", "AREA_ENCHANT")
CARD_TYPE_TO_INDEX = {name: i for i, name in enumerate(CARD_TYPE_NAMES)}

# Attributes
ATTR_DARKNESS = 0
ATTR_FLAME = 1
ATTR_ELECTRICITY = 2
ATTR_WIND = 3
ATTR_CHAOS = 4
ATTRIBUTE_NAMES = ("DARKNESS", "FLAME", "ELECTRICITY", "WIND", "CHAOS")
ATTRIBUTE_TO_INDEX = {name: i for i, name in enumerate(ATTRIBUTE_NAMES)}

RARITY_NAMES = ("N", "R", "SR", "UR", "SE")
RARITY_TO_INDEX = {name: i for i, name in enumerate(RARITY_NAMES)}

# Normalization maxima (verified against the card DB; asserted at load).
MAX_CLOCK = 6
MAX_POWER_COST = 8
MAX_ATTACK = 200
MAX_SEND_TO_POWER = 2

NO_EFFECT = -1


class CardDef(NamedTuple):
    index: int
    pack: int
    number: int
    name: str
    card_type: int
    attribute: int
    song: int
    clock: int
    attack_day: int
    attack_night: int
    power_cost: int
    send_to_power: int
    effect_index: int  # dense index into EFFECT_IDS, or NO_EFFECT
    rarity: int

    @property
    def key(self) -> str:
        """Canonical "XX-YYY" token, e.g. "03-045"."""
        return f"{self.pack:02d}-{self.number:03d}"

    @property
    def effect_id(self) -> str:
        return EFFECT_IDS[self.effect_index] if self.effect_index != NO_EFFECT else ""


def _load() -> tuple:
    with open(_CARDS_JSON_PATH, encoding="utf-8") as handle:
        raw_cards = json.load(handle)["cards"]

    raw_cards.sort(key=lambda c: (c["pack"], c["id"]))

    songs = sorted({c["song"] for c in raw_cards})
    song_to_index = {name: i for i, name in enumerate(songs)}
    effect_ids = sorted({c["effect"] for c in raw_cards if c["effect"]})
    effect_to_index = {eid: i for i, eid in enumerate(effect_ids)}

    definitions = []
    for i, c in enumerate(raw_cards):
        assert c["clock"] <= MAX_CLOCK, c
        assert c["power_cost"] <= MAX_POWER_COST, c
        assert max(c["attack_day"], c["attack_night"]) <= MAX_ATTACK, c
        assert c["send_to_power"] <= MAX_SEND_TO_POWER, c
        definitions.append(CardDef(
            index=i,
            pack=c["pack"],
            number=c["id"],
            name=c["name"],
            card_type=CARD_TYPE_TO_INDEX[c["card_type"]],
            attribute=ATTRIBUTE_TO_INDEX[c["attribute"]],
            song=song_to_index[c["song"]],
            clock=c["clock"],
            attack_day=c["attack_day"],
            attack_night=c["attack_night"],
            power_cost=c["power_cost"],
            send_to_power=c["send_to_power"],
            effect_index=effect_to_index.get(c["effect"], NO_EFFECT),
            rarity=RARITY_TO_INDEX[c["rarity"]],
        ))
    return tuple(definitions), tuple(songs), tuple(effect_ids)


CARD_DB, SONG_NAMES, EFFECT_IDS = _load()
NUM_CARDS = len(CARD_DB)
NUM_SONGS = len(SONG_NAMES)
NUM_EFFECTS = len(EFFECT_IDS)

CARD_INDEX = {(d.pack, d.number): d.index for d in CARD_DB}
KEY_TO_INDEX = {d.key: d.index for d in CARD_DB}
EFFECT_TO_INDEX = {eid: i for i, eid in enumerate(EFFECT_IDS)}
# Card def index that carries each effect (effects are unique per card).
EFFECT_TO_CARD = {d.effect_index: d.index for d in CARD_DB if d.effect_index != NO_EFFECT}

# Flat arrays for hot-path lookups: X[def_index].
CLOCK = np.array([d.clock for d in CARD_DB], dtype=np.int16)
ATK_DAY = np.array([d.attack_day for d in CARD_DB], dtype=np.int16)
ATK_NIGHT = np.array([d.attack_night for d in CARD_DB], dtype=np.int16)
POWER_COST = np.array([d.power_cost for d in CARD_DB], dtype=np.int16)
SEND_TO_POWER = np.array([d.send_to_power for d in CARD_DB], dtype=np.int16)
CARD_TYPE = np.array([d.card_type for d in CARD_DB], dtype=np.int16)
ATTRIBUTE = np.array([d.attribute for d in CARD_DB], dtype=np.int16)
SONG = np.array([d.song for d in CARD_DB], dtype=np.int16)
RARITY = np.array([d.rarity for d in CARD_DB], dtype=np.int16)
EFFECT = np.array([d.effect_index for d in CARD_DB], dtype=np.int16)

# Plain-int tuples: faster than numpy scalar extraction for single lookups
# in the pure-Python hot path (numpy scalars carry heavy overhead).
CLOCK_T = tuple(int(x) for x in CLOCK)
ATK_DAY_T = tuple(int(x) for x in ATK_DAY)
ATK_NIGHT_T = tuple(int(x) for x in ATK_NIGHT)
POWER_COST_T = tuple(int(x) for x in POWER_COST)
SEND_TO_POWER_T = tuple(int(x) for x in SEND_TO_POWER)
CARD_TYPE_T = tuple(int(x) for x in CARD_TYPE)
ATTRIBUTE_T = tuple(int(x) for x in ATTRIBUTE)
SONG_T = tuple(int(x) for x in SONG)
EFFECT_T = tuple(int(x) for x in EFFECT)
