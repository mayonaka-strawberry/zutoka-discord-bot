from dataclasses import dataclass, field
from typing import Optional
from uuid import uuid4
from zutomayo.enums.attribute import Attribute
from zutomayo.enums.zone import Zone
from zutomayo.models.card import Card


@dataclass
class CardInstance:
    card: Card
    owner: str = ''
    unique_id: str = field(default_factory = lambda: str(uuid4()))
    zone: Zone = Zone.DECK
    face_up: bool = False
    played_this_turn: bool = False
    effects_disabled: bool = False
    # Transient per-turn reduction applied by effect 02-006
    power_cost_reduction: int = 0
    # Persistent attribute override applied by effect 02-084; cleared when card leaves battle zone
    attribute_override: Optional[Attribute] = None

    @property
    def effective_attribute(self) -> Attribute:
        """Return the overridden attribute if set, otherwise the card's intrinsic attribute."""
        if self.attribute_override is not None:
            return self.attribute_override
        return self.card.attribute
