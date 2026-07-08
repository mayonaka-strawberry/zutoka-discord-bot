"""
Fluent builder for game states in effect and engine tests.

Builds a real GameState with real Card objects from the catalog (never
hand-built stubs) so tests exercise exactly the data the live game uses.
Cards are referenced by their pack-id string, for example '02-005'.
"""

from __future__ import annotations

import random
from typing import Optional

from constants import MIDNIGHT
from zutomayo.data.deck_validator import get_card_index
from zutomayo.enums.chronos import Chronos
from zutomayo.enums.phase import Phase
from zutomayo.enums.zone import Zone
from zutomayo.models.card import Card
from zutomayo.models.card_instance import CardInstance
from zutomayo.models.game_state import GameState
from zutomayo.models.player import Player

_ZONE_BY_FIELD = {
    'deck': Zone.DECK,
    'hand': Zone.HAND,
    'power_charger': Zone.POWER_CHARGER,
    'abyss': Zone.ABYSS,
    'battle_zone': Zone.BATTLE_ZONE,
    'set_zone_a': Zone.SET_ZONE_A,
    'set_zone_b': Zone.SET_ZONE_B,
    'set_zone_c': Zone.SET_ZONE_C,
}


def card_by_identity(card_identity: str) -> Card:
    """Look up a Card by its 'PP-III' pack-id string."""
    _, card_index = get_card_index()
    pack_text, id_text = card_identity.split('-')
    card = card_index.get((int(pack_text), int(id_text)))
    if card is None:
        raise KeyError(f'No card {card_identity} in the catalog')
    return card


class GameStateBuilder:
    def __init__(self) -> None:
        self._players: list[Player] = [
            Player(name='player_zero', index=0, side=Chronos.DAY),
            Player(name='player_one', index=1, side=Chronos.NIGHT),
        ]
        self._chronos = MIDNIGHT
        self._turn = 2
        self._phase = Phase.PROCESS_EFFECTS
        self._last_battle_winner: Optional[str] = None

    def _instance(self, player_index: int, card_identity: str, zone_field: str, face_up: bool) -> CardInstance:
        return CardInstance(
            card=card_by_identity(card_identity),
            owner=self._players[player_index].name,
            zone=_ZONE_BY_FIELD[zone_field],
            face_up=face_up,
        )

    def with_cards(self, player_index: int, zone_field: str, card_identities: list[str], face_up: bool = True) -> 'GameStateBuilder':
        """Fill a list zone ('deck', 'hand', 'power_charger', 'abyss')."""
        instances = [
            self._instance(player_index, identity, zone_field, face_up)
            for identity in card_identities
        ]
        getattr(self._players[player_index], zone_field).extend(instances)
        return self

    def with_single_card(self, player_index: int, zone_field: str, card_identity: Optional[str], face_up: bool = True, played_this_turn: bool = False) -> 'GameStateBuilder':
        """Set a single-card zone ('battle_zone', 'set_zone_a'/'b'/'c')."""
        if card_identity is None:
            setattr(self._players[player_index], zone_field, None)
            return self
        instance = self._instance(player_index, card_identity, zone_field, face_up)
        instance.played_this_turn = played_this_turn
        setattr(self._players[player_index], zone_field, instance)
        return self

    def with_battle_card(self, player_index: int, card_identity: str, played_this_turn: bool = False) -> 'GameStateBuilder':
        return self.with_single_card(player_index, 'battle_zone', card_identity, played_this_turn=played_this_turn)

    def with_hand(self, player_index: int, card_identities: list[str]) -> 'GameStateBuilder':
        return self.with_cards(player_index, 'hand', card_identities, face_up=False)

    def with_deck(self, player_index: int, card_identities: list[str]) -> 'GameStateBuilder':
        return self.with_cards(player_index, 'deck', card_identities, face_up=False)

    def with_abyss(self, player_index: int, card_identities: list[str]) -> 'GameStateBuilder':
        return self.with_cards(player_index, 'abyss', card_identities)

    def with_power_charger(self, player_index: int, card_identities: list[str]) -> 'GameStateBuilder':
        return self.with_cards(player_index, 'power_charger', card_identities)

    def with_hp(self, player_index: int, hp: int) -> 'GameStateBuilder':
        self._players[player_index].hp = hp
        return self

    def with_chronos(self, position: int) -> 'GameStateBuilder':
        self._chronos = position
        return self

    def with_turn(self, turn: int) -> 'GameStateBuilder':
        self._turn = turn
        return self

    def with_phase(self, phase: Phase) -> 'GameStateBuilder':
        self._phase = phase
        return self

    def with_sides(self, player_zero_side: Chronos) -> 'GameStateBuilder':
        self._players[0].side = player_zero_side
        self._players[1].side = Chronos.NIGHT if player_zero_side == Chronos.DAY else Chronos.DAY
        return self

    def with_last_battle_winner(self, player_index: Optional[int]) -> 'GameStateBuilder':
        self._last_battle_winner = None if player_index is None else self._players[player_index].name
        return self

    def build(self) -> GameState:
        state = GameState(
            players=self._players,
            chronos=self._chronos,
            chronos_at_turn_start=self._chronos,
        )
        state.turn = self._turn
        state.current_phase = self._phase
        state.last_battle_winner = self._last_battle_winner
        return state
