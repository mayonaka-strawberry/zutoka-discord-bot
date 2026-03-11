from random import randint, shuffle
from constants import MIDNIGHT
from zutomayo.effects.effect_engine import EffectEngine
from zutomayo.enums.chronos import Chronos
from zutomayo.models.card_instance import CardInstance
from zutomayo.models.game_state import GameState
from zutomayo.models.player import Player


class GameController:
    def __init__(
            self,
            name_1: str,
            name_2: str,
            deck_1: list[CardInstance],
            deck_2: list[CardInstance],
            effect_engine: EffectEngine
    ) -> None:
        self.effect_engine = effect_engine

        coin_flip = randint(0, 1)
        side_1 = Chronos.DAY
        side_2 = Chronos.NIGHT
        if coin_flip == 0:
            side_1 = Chronos.NIGHT
            side_2 = Chronos.DAY

        player_1 = Player(
            name = name_1,
            index = 0,
            side = side_1,
            deck = deck_1
        )
        player_2 = Player(
            name = name_2,
            index = 1,
            side = side_2,
            deck = deck_2
        )

        self.game_state = GameState(
            players = [player_1, player_2],
            chronos = MIDNIGHT,
            chronos_at_turn_start = MIDNIGHT
        )
