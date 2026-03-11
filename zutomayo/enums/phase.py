from enum import auto, Enum


class Phase(Enum):
    SETUP = auto()
    SET_CARDS = auto()
    REVEAL = auto()
    ADVANCE_CHRONOS = auto()
    CHARACTER_SWAP = auto()
    AREA_ENCHANT_SWAP = auto()
    PROCESS_EFFECTS = auto()
    BATTLE = auto()
    TURN_END_EFFECTS = auto()
    END_TURN = auto()
