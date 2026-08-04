"""
Read-only projections of engine_alpha game state for the UI layer.

CardView duck-types the two attributes the board renderer and embed helpers
read from the old CardInstance (``face_up`` and ``card``), and PlayerView
exposes the old Player zone attribute names (``battle_zone``, ``set_zone_a``,
``set_zone_b``, ``set_zone_c``, ``power_charger``, ``abyss``, ``hand``,
``deck``), so the PIL renderer and embed builders work on projections
without changes to their drawing logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from engine_alpha import cards as engine_cards
from engine_alpha.battle import NIGHT_END, total_power
from engine_alpha.state import (
    PH_ADVANCE_CHRONOS, PH_AREA_SWAP, PH_BATTLE, PH_CHARACTER_SWAP, PH_DRAFT,
    PH_END_TURN, PH_GAME_OVER, PH_INITIAL_REVEAL, PH_INITIAL_SET, PH_MULLIGAN,
    PH_PROCESS_EFFECTS, PH_REVEAL, PH_SET_CARDS, PH_TURN_END_EFFECTS,
)
from zutomayo.models.card import Card

PHASE_NAMES = {
    PH_DRAFT: 'Draft',
    PH_MULLIGAN: 'Setup',
    PH_INITIAL_SET: 'Setup',
    PH_INITIAL_REVEAL: 'Setup',
    PH_SET_CARDS: 'Set Cards',
    PH_REVEAL: 'Reveal',
    PH_ADVANCE_CHRONOS: 'Advance Chronos',
    PH_CHARACTER_SWAP: 'Character Swap',
    PH_AREA_SWAP: 'Area Enchant Swap',
    PH_PROCESS_EFFECTS: 'Process Effects',
    PH_BATTLE: 'Battle',
    PH_TURN_END_EFFECTS: 'Turn End Effects',
    PH_END_TURN: 'End Turn',
    PH_GAME_OVER: 'Game Over',
}


def _build_definition_table() -> dict[int, Card]:
    from zutomayo.data.deck_validator import get_card_index

    _, card_index = get_card_index()
    table: dict[int, Card] = {}
    for key, definition_index in engine_cards.KEY_TO_INDEX.items():
        pack_text, _, number_text = key.partition('-')
        card = card_index.get((int(pack_text), int(number_text)))
        if card is None:
            raise RuntimeError(f'engine card {key} missing from the legacy card index')
        table[definition_index] = card
    if len(table) != len(engine_cards.CARD_DB):
        raise RuntimeError('card definition table does not cover the full card database')
    return table


_DEFINITION_TABLE: dict[int, Card] | None = None


def definition_index_to_card(definition_index: int) -> Card:
    global _DEFINITION_TABLE
    if _DEFINITION_TABLE is None:
        _DEFINITION_TABLE = _build_definition_table()
    return _DEFINITION_TABLE[definition_index]


@dataclass(frozen=True)
class CardView:
    instance_id: int
    definition_index: int
    card: Card
    face_up: bool


@dataclass(frozen=True)
class PlayerView:
    index: int
    name: str
    hp: int
    total_power: int
    side_is_night: bool
    hand: tuple[CardView, ...]
    deck: tuple[CardView, ...]
    power_charger: tuple[CardView, ...]
    abyss: tuple[CardView, ...]
    battle_zone: Optional[CardView]
    set_zone_a: Optional[CardView]
    set_zone_b: Optional[CardView]
    set_zone_c: Optional[CardView]

    @property
    def deck_count(self) -> int:
        return len(self.deck)

    @property
    def side(self):
        """Legacy Chronos side enum, read by the board renderer."""
        from zutomayo.enums.chronos import Chronos

        return Chronos.NIGHT if self.side_is_night else Chronos.DAY


@dataclass(frozen=True)
class BoardView:
    turn: int
    chronos: int
    chronos_at_turn_start: int
    is_night: bool
    phase: int
    phase_name: str
    players: tuple[PlayerView, PlayerView]
    winner: int                # -1 in progress, 0/1 winner index, 2 draw
    last_battle_winner: int    # -1 none/draw


def card_view(state, instance_id: int) -> CardView:
    return CardView(
        instance_id=instance_id,
        definition_index=state.inst_def[instance_id],
        card=definition_index_to_card(state.inst_def[instance_id]),
        face_up=bool(state.inst_face_up[instance_id]),
    )


def _single(state, instance_id: int) -> Optional[CardView]:
    return card_view(state, instance_id) if instance_id != -1 else None


def project_player_view(state, player_index: int, name: str) -> PlayerView:
    player = state.players[player_index]
    return PlayerView(
        index=player_index,
        name=name,
        hp=player.hp,
        total_power=total_power(state, player),
        side_is_night=player.side_is_night,
        hand=tuple(card_view(state, i) for i in player.hand),
        deck=tuple(card_view(state, i) for i in player.deck),
        power_charger=tuple(card_view(state, i) for i in player.charger),
        abyss=tuple(card_view(state, i) for i in player.abyss),
        battle_zone=_single(state, player.battle),
        set_zone_a=_single(state, player.set_a),
        set_zone_b=_single(state, player.set_b),
        set_zone_c=_single(state, player.set_c),
    )


def project_board_view(game, player_names: dict[int, str]) -> BoardView:
    state = game.state
    return BoardView(
        turn=state.turn,
        chronos=state.chronos,
        chronos_at_turn_start=state.chronos_at_turn_start,
        is_night=state.chronos <= NIGHT_END,
        phase=state.phase,
        phase_name=PHASE_NAMES.get(state.phase, str(state.phase)),
        players=(
            project_player_view(state, 0, player_names.get(0, 'Player 1')),
            project_player_view(state, 1, player_names.get(1, 'Player 2')),
        ),
        winner=state.winner,
        last_battle_winner=state.last_battle_winner,
    )
