from dataclasses import dataclass, field
from typing import Optional
from zutomayo.enums.chronos import Chronos
from zutomayo.enums.zone import Zone
from zutomayo.models.card_instance import CardInstance


@dataclass
class Player:
    name: str
    index: int
    side: Chronos
    hp: int = 100
    deck: list[CardInstance] = field(default_factory=list)
    hand: list[CardInstance] = field(default_factory=list)
    power_charger: list[CardInstance] = field(default_factory=list)
    abyss: list[CardInstance] = field(default_factory=list)
    battle_zone: Optional[CardInstance] = None
    set_zone_a: Optional[CardInstance] = None
    set_zone_b: Optional[CardInstance] = None
    set_zone_c: Optional[CardInstance] = None
    cards_played_this_turn: int = 0
    area_enchant_blocked: bool = False
    hand_size_bonus: int = 0
    pending_hand_size_bonus: int = 0

    @property
    def total_power(self) -> int:
        return sum(card_instance.card.send_to_power for card_instance in self.power_charger)

    def can_draw(self, count: int) -> bool:
        return len(self.deck) >= count

    def draw(self, count: int) -> list[CardInstance]:
        draw = self.deck[:count]
        self.deck = self.deck[count:]
        for card_instance in draw:
            card_instance.zone = Zone.HAND
            card_instance.face_up = False
        self.hand.extend(draw)
        return draw
