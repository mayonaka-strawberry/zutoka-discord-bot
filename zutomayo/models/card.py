from dataclasses import dataclass
from zutomayo.enums.attribute import Attribute
from zutomayo.enums.card_type import CardType
from zutomayo.enums.rarity import Rarity
from zutomayo.enums.song import Song


@dataclass(frozen=True)
class Card:
    pack: int
    id: int
    name: str
    name_jp: str
    image: str
    clock: int
    card_type: CardType
    attribute: Attribute
    song: Song
    rarity: Rarity
    attack_day: int = 0
    attack_night: int = 0
    power_cost: int = 0
    send_to_power: int = 0
    effect: str = ''
    effect_text: str = ''
    effect_text_jp: str = ''
