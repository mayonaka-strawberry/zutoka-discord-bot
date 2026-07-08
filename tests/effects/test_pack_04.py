"""Characterization tests for pack 04 card effects (see test_pack_01 header)."""

from __future__ import annotations

import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from zutomayo.enums.zone import Zone  # noqa: E402

from tests.support.game_state_builder import GameStateBuilder  # noqa: E402
from tests.support.effect_harness import ScriptedAnswer, card_identities, run_effect  # noqa: E402

DARKNESS_CHARACTER = '01-001'
FLAME_CHARACTER = '01-002'
ELECTRICITY_CHARACTER = '01-003'
WIND_CHARACTER = '01-004'
SECOND_DARKNESS_CHARACTER = '01-009'
THIRD_DARKNESS_CHARACTER = '01-010'


class TestEffect04006:
    """Return 4 Abyss cards to deck bottom or lose; then swap the opponent's
    battle character with one from their Power Charger (effects disabled)."""

    def _base_builder(self) -> GameStateBuilder:
        return (GameStateBuilder()
                .with_battle_card(0, '04-006')
                .with_abyss(0, [DARKNESS_CHARACTER, FLAME_CHARACTER, ELECTRICITY_CHARACTER, WIND_CHARACTER])
                .with_deck(0, [SECOND_DARKNESS_CHARACTER]))

    def test_fewer_than_four_abyss_cards_loses_the_game(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '04-006')
                 .with_abyss(0, [DARKNESS_CHARACTER, FLAME_CHARACTER])
                 .build())
        result = run_effect(state, '04-006', owner_index=0)
        assert state.players[0].hp == 0
        assert any('You lose the game!' in text for text in result.message_texts())
        assert result.prompts_seen == []

    def test_happy_path_returns_four_cards_and_swaps_opponent_character(self):
        state = (self._base_builder()
                 .with_battle_card(1, DARKNESS_CHARACTER)
                 .with_power_charger(1, [FLAME_CHARACTER])
                 .build())
        old_opponent_battle_card = state.players[1].battle_zone
        result = run_effect(state, '04-006', owner_index=0, scripted_answers=[
            ScriptedAnswer.card_indices([0]),
            ScriptedAnswer.card_indices([0]),
            ScriptedAnswer.card_indices([0]),
            ScriptedAnswer.card_indices([0]),
            ScriptedAnswer.card_indices([0]),  # swap target in opponent power charger
        ])

        player = state.players[0]
        assert player.abyss == []
        assert len(player.deck) == 5, 'four cards shuffled onto the deck bottom'
        assert all(card_instance.zone == Zone.DECK and not card_instance.face_up
                   for card_instance in player.deck[1:])

        opponent = state.players[1]
        assert opponent.battle_zone is not None
        assert opponent.battle_zone.card.effect == '' and card_identities([opponent.battle_zone]) == [FLAME_CHARACTER]
        assert opponent.battle_zone.effects_disabled is True
        assert old_opponent_battle_card in opponent.power_charger
        assert result.prompts_seen == ['effect_card_select'] * 5

    def test_timeout_during_abyss_selection_loses_the_game(self):
        state = self._base_builder().build()
        result = run_effect(state, '04-006', owner_index=0, scripted_answers=[
            ScriptedAnswer.card_indices([0]),
            ScriptedAnswer.timeout('effect_card_select'),
        ])
        assert state.players[0].hp == 0
        assert any('Failed to select 4 cards' in text for text in result.message_texts())

    def test_no_opponent_power_characters_fizzles_swap(self):
        state = (self._base_builder()
                 .with_battle_card(1, DARKNESS_CHARACTER)
                 .build())
        result = run_effect(state, '04-006', owner_index=0, scripted_answers=[
            ScriptedAnswer.card_indices([0]),
            ScriptedAnswer.card_indices([0]),
            ScriptedAnswer.card_indices([0]),
            ScriptedAnswer.card_indices([0]),
        ])
        assert any('Swap fizzles' in text for text in result.message_texts())
        assert len(state.players[0].deck) == 5

    def test_empty_opponent_battle_zone_fizzles_swap(self):
        state = (self._base_builder()
                 .with_power_charger(1, [FLAME_CHARACTER])
                 .build())
        result = run_effect(state, '04-006', owner_index=0, scripted_answers=[
            ScriptedAnswer.card_indices([0]),
            ScriptedAnswer.card_indices([0]),
            ScriptedAnswer.card_indices([0]),
            ScriptedAnswer.card_indices([0]),
        ])
        assert any('no character in Battle Zone' in text for text in result.message_texts())

    def test_swap_prompt_timeout_performs_no_swap(self):
        state = (self._base_builder()
                 .with_battle_card(1, DARKNESS_CHARACTER)
                 .with_power_charger(1, [FLAME_CHARACTER])
                 .build())
        result = run_effect(state, '04-006', owner_index=0, scripted_answers=[
            ScriptedAnswer.card_indices([0]),
            ScriptedAnswer.card_indices([0]),
            ScriptedAnswer.card_indices([0]),
            ScriptedAnswer.card_indices([0]),
            ScriptedAnswer.timeout('effect_card_select'),
        ])
        assert any('No swap performed.' in text for text in result.message_texts())
        assert card_identities([state.players[1].battle_zone]) == [DARKNESS_CHARACTER]


class TestEffect04097:
    """Reveal your hand; attack +50 if it holds 3 or more attributes."""

    def test_three_attributes_grant_attack_bonus_with_sorted_names(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '04-097')
                 .with_hand(0, [WIND_CHARACTER, ELECTRICITY_CHARACTER, FLAME_CHARACTER])
                 .build())
        result = run_effect(state, '04-097', owner_index=0)
        assert result.engine.turn_state.attack_bonus[0] == 50
        bonus_messages = [text for text in result.message_texts() if 'Attack +50!' in text]
        assert bonus_messages and '(ELECTRICITY, FLAME, WIND)' in bonus_messages[0]

    def test_two_attributes_grant_no_bonus(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '04-097')
                 .with_hand(0, [DARKNESS_CHARACTER, SECOND_DARKNESS_CHARACTER, FLAME_CHARACTER])
                 .build())
        result = run_effect(state, '04-097', owner_index=0)
        assert result.engine.turn_state.attack_bonus[0] == 0
        assert any('Need 3+. No bonus.' in text for text in result.message_texts())

    def test_empty_hand_skips_reveal(self):
        state = GameStateBuilder().with_battle_card(0, '04-097').build()
        result = run_effect(state, '04-097', owner_index=0)
        assert any('Hand is empty. No attribute bonus.' in text for text in result.message_texts())
        assert result.engine.turn_state.attack_bonus[0] == 0
