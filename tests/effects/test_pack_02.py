"""Characterization tests for pack 02 card effects (see test_pack_01 header).

02-005, 02-007, and 02-062 have no registered handlers (their logic lives in
check_area_enchant_removal / should_force_day_attack / TurnManager.do_character_swap)
and are covered by the engine-core tests instead.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

import pytest  # noqa: E402

from tests.support.game_state_builder import GameStateBuilder  # noqa: E402
from tests.support.effect_harness import ScriptedAnswer, card_identities, run_effect  # noqa: E402

DARKNESS_CHARACTER = '01-001'       # STP 0, cost 5
FLAME_CHARACTER = '01-002'          # STP 0, cost 7
ELECTRICITY_CHARACTER = '01-003'    # STP 0, cost 5
WIND_CHARACTER = '01-004'           # STP 0, cost 3
STP_ONE_CARD = '01-009'             # STP 1
SECOND_STP_ONE_CARD = '01-010'      # STP 1
STP_TWO_CARD = '01-013'             # STP 2
SECOND_STP_TWO_CARD = '01-014'      # STP 2

ATTRIBUTE_CARD = {
    'DARKNESS': DARKNESS_CHARACTER,
    'FLAME': FLAME_CHARACTER,
    'ELECTRICITY': ELECTRICITY_CHARACTER,
    'WIND': WIND_CHARACTER,
}


class TestEffect02008:
    """Electric character: bottom-deck an STP=2 card from the opponent's Power Charger."""

    def test_moves_selected_card_to_opponent_deck_bottom(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, ELECTRICITY_CHARACTER)
                 .with_single_card(0, 'set_zone_c', '02-008')
                 .with_power_charger(1, [STP_TWO_CARD, STP_ONE_CARD])
                 .with_deck(1, [DARKNESS_CHARACTER])
                 .build())
        result = run_effect(state, '02-008', 0, card_instance=state.players[0].set_zone_c,
                            scripted_answers=[ScriptedAnswer.card_indices([0])])
        opponent = state.players[1]
        assert card_identities(opponent.power_charger) == [STP_ONE_CARD]
        assert card_identities(opponent.deck) == [DARKNESS_CHARACTER, STP_TWO_CARD]
        assert not opponent.deck[-1].face_up

    def test_fizzles_without_electric_character(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, FLAME_CHARACTER)
                 .with_single_card(0, 'set_zone_c', '02-008')
                 .with_power_charger(1, [STP_TWO_CARD])
                 .build())
        result = run_effect(state, '02-008', 0, card_instance=state.players[0].set_zone_c)
        assert any('not Electric. Effect fizzles.' in text for text in result.message_texts())
        assert card_identities(state.players[1].power_charger) == [STP_TWO_CARD]

    def test_fizzles_without_stp_two_targets(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, ELECTRICITY_CHARACTER)
                 .with_single_card(0, 'set_zone_c', '02-008')
                 .with_power_charger(1, [STP_ONE_CARD])
                 .build())
        result = run_effect(state, '02-008', 0, card_instance=state.players[0].set_zone_c)
        assert any('No eligible cards (STP=2)' in text for text in result.message_texts())

    def test_prompt_timeout_leaves_everything_in_place(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, ELECTRICITY_CHARACTER)
                 .with_single_card(0, 'set_zone_c', '02-008')
                 .with_power_charger(1, [STP_TWO_CARD])
                 .build())
        result = run_effect(state, '02-008', 0, card_instance=state.players[0].set_zone_c,
                            scripted_answers=[ScriptedAnswer.timeout('effect_card_select')])
        assert any('No effect.' in text for text in result.message_texts())
        assert card_identities(state.players[1].power_charger) == [STP_TWO_CARD]


class TestEffect02010:
    """Attack +20 if the own previous-turn character was Flame."""

    def test_previous_flame_character_grants_bonus(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '02-010')
                 .with_previous_battle_character(0, FLAME_CHARACTER)
                 .build())
        result = run_effect(state, '02-010', 0)
        assert result.engine.turn_state.attack_bonus[0] == 20

    def test_other_or_missing_previous_character_grants_nothing(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '02-010')
                 .with_previous_battle_character(0, WIND_CHARACTER)
                 .build())
        assert run_effect(state, '02-010', 0).engine.turn_state.attack_bonus[0] == 0

        state = GameStateBuilder().with_battle_card(0, '02-010').build()
        assert run_effect(state, '02-010', 0).engine.turn_state.attack_bonus[0] == 0


class TestEffect02011:
    """Previous Flame character: advance the clock 0-5 at the player's choice."""

    def test_advances_chronos_by_selected_amount(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '02-011')
                 .with_previous_battle_character(0, FLAME_CHARACTER)
                 .with_chronos(6)
                 .build())
        result = run_effect(state, '02-011', 0, scripted_answers=[ScriptedAnswer.number(4)])
        assert state.chronos == 10
        assert result.engine.turn_state.day_to_night_occurred is False
        assert result.engine.turn_state.night_to_day_occurred is True

    def test_zero_selection_keeps_chronos(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '02-011')
                 .with_previous_battle_character(0, FLAME_CHARACTER)
                 .with_chronos(6)
                 .build())
        run_effect(state, '02-011', 0, scripted_answers=[ScriptedAnswer.number(0)])
        assert state.chronos == 6

    def test_fizzles_without_previous_flame(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '02-011')
                 .with_chronos(6)
                 .build())
        result = run_effect(state, '02-011', 0)
        assert any('was not Flame. Effect fizzles.' in text for text in result.message_texts())
        assert state.chronos == 6

    def test_timeout_keeps_chronos(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '02-011')
                 .with_previous_battle_character(0, FLAME_CHARACTER)
                 .with_chronos(6)
                 .build())
        result = run_effect(state, '02-011', 0,
                            scripted_answers=[ScriptedAnswer.timeout('effect_number_select')])
        assert state.chronos == 6
        assert any('No effect.' in text for text in result.message_texts())


class TestEffect02014:
    """Recover 20 HP if the Abyss holds two or more darkness cards."""

    def test_two_darkness_cards_heal(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '02-014')
                 .with_abyss(0, [DARKNESS_CHARACTER, '01-009'])
                 .with_hp(0, 70)
                 .build())
        run_effect(state, '02-014', 0)
        assert state.players[0].hp == 90

    def test_heal_caps_at_one_hundred(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '02-014')
                 .with_abyss(0, [DARKNESS_CHARACTER, '01-009'])
                 .with_hp(0, 95)
                 .build())
        run_effect(state, '02-014', 0)
        assert state.players[0].hp == 100

    def test_single_darkness_card_heals_nothing(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '02-014')
                 .with_abyss(0, [DARKNESS_CHARACTER, FLAME_CHARACTER])
                 .with_hp(0, 70)
                 .build())
        run_effect(state, '02-014', 0)
        assert state.players[0].hp == 70


class TestEffect02019:
    """Previous Wind character: bottom-deck an STP=1 card from the opponent's Power Charger."""

    def test_moves_selected_card(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '02-019')
                 .with_previous_battle_character(0, WIND_CHARACTER)
                 .with_power_charger(1, [STP_ONE_CARD, STP_TWO_CARD])
                 .build())
        run_effect(state, '02-019', 0, scripted_answers=[ScriptedAnswer.card_indices([0])])
        opponent = state.players[1]
        assert card_identities(opponent.power_charger) == [STP_TWO_CARD]
        assert card_identities(opponent.deck) == [STP_ONE_CARD]

    def test_fizzles_without_previous_wind(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '02-019')
                 .with_power_charger(1, [STP_ONE_CARD])
                 .build())
        result = run_effect(state, '02-019', 0)
        assert any('was not Wind. Effect fizzles.' in text for text in result.message_texts())

    def test_fizzles_without_stp_one_targets(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '02-019')
                 .with_previous_battle_character(0, WIND_CHARACTER)
                 .with_power_charger(1, [STP_TWO_CARD])
                 .build())
        result = run_effect(state, '02-019', 0)
        assert any('No eligible cards (STP=1)' in text for text in result.message_texts())


# Simple attribute template shared with pack 01 families:

@pytest.mark.parametrize('effect_id, attribute_name, bonus', [
    ('02-009', 'WIND', 20),
])
def test_opponent_attribute_bonus(effect_id, attribute_name, bonus):
    state = (GameStateBuilder()
             .with_battle_card(0, effect_id)
             .with_battle_card(1, ATTRIBUTE_CARD[attribute_name])
             .build())
    assert run_effect(state, effect_id, 0).engine.turn_state.attack_bonus[0] == bonus

    state = GameStateBuilder().with_battle_card(0, effect_id).build()
    assert run_effect(state, effect_id, 0).engine.turn_state.attack_bonus[0] == 0


@pytest.mark.parametrize('effect_id, attribute_name, bonus', [
    ('02-018', 'WIND', 20),
])
def test_previous_character_attribute_bonus(effect_id, attribute_name, bonus):
    state = (GameStateBuilder()
             .with_battle_card(0, effect_id)
             .with_previous_battle_character(0, ATTRIBUTE_CARD[attribute_name])
             .build())
    assert run_effect(state, effect_id, 0).engine.turn_state.attack_bonus[0] == bonus

    state = (GameStateBuilder()
             .with_battle_card(0, effect_id)
             .with_previous_battle_character(0, DARKNESS_CHARACTER)
             .build())
    assert run_effect(state, effect_id, 0).engine.turn_state.attack_bonus[0] == 0


class TestEffect02023:
    """Attack +40 with previous Electric character against Flame or Darkness opponent."""

    def test_bonus_applies_for_flame_and_darkness_opponents(self):
        for opponent_card in (FLAME_CHARACTER, DARKNESS_CHARACTER):
            state = (GameStateBuilder()
                     .with_battle_card(0, '02-023')
                     .with_previous_battle_character(0, ELECTRICITY_CHARACTER)
                     .with_battle_card(1, opponent_card)
                     .build())
            assert run_effect(state, '02-023', 0).engine.turn_state.attack_bonus[0] == 40

    def test_no_bonus_without_previous_electric(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '02-023')
                 .with_previous_battle_character(0, WIND_CHARACTER)
                 .with_battle_card(1, FLAME_CHARACTER)
                 .build())
        assert run_effect(state, '02-023', 0).engine.turn_state.attack_bonus[0] == 0

    def test_no_bonus_against_other_attributes(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '02-023')
                 .with_previous_battle_character(0, ELECTRICITY_CHARACTER)
                 .with_battle_card(1, WIND_CHARACTER)
                 .build())
        assert run_effect(state, '02-023', 0).engine.turn_state.attack_bonus[0] == 0


class TestEffect02024:
    """Previous Electric character at night: bottom-deck an opponent STP=1 card."""

    def _builder(self):
        return (GameStateBuilder()
                .with_battle_card(0, '02-024')
                .with_previous_battle_character(0, ELECTRICITY_CHARACTER)
                .with_power_charger(1, [STP_ONE_CARD])
                .with_chronos(4))

    def test_happy_path(self):
        state = self._builder().build()
        run_effect(state, '02-024', 0, scripted_answers=[ScriptedAnswer.card_indices([0])])
        assert card_identities(state.players[1].deck) == [STP_ONE_CARD]
        assert state.players[1].power_charger == []

    def test_fizzles_without_previous_electric(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '02-024')
                 .with_power_charger(1, [STP_ONE_CARD])
                 .with_chronos(4)
                 .build())
        result = run_effect(state, '02-024', 0)
        assert any('was not Electric. Effect fizzles.' in text for text in result.message_texts())

    def test_fizzles_during_daytime(self):
        state = self._builder().with_chronos(13).build()
        result = run_effect(state, '02-024', 0)
        assert any('It is not night. Effect fizzles.' in text for text in result.message_texts())

    def test_fizzles_without_targets(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '02-024')
                 .with_previous_battle_character(0, ELECTRICITY_CHARACTER)
                 .with_power_charger(1, [STP_TWO_CARD])
                 .with_chronos(4)
                 .build())
        result = run_effect(state, '02-024', 0)
        assert any('No eligible cards (STP=1)' in text for text in result.message_texts())


class TestEffect02026:
    """At night, +30 if the own battle character was played this turn."""

    def test_bonus_for_character_played_this_turn_at_night(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '02-026')
                 .with_chronos(4)
                 .build())
        state.players[0].battle_zone.played_this_turn = True
        assert run_effect(state, '02-026', 0).engine.turn_state.attack_bonus[0] == 30

    def test_no_bonus_for_older_character(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '02-026')
                 .with_chronos(4)
                 .build())
        assert run_effect(state, '02-026', 0).engine.turn_state.attack_bonus[0] == 0

    def test_no_bonus_during_daytime(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '02-026')
                 .with_chronos(13)
                 .build())
        state.players[0].battle_zone.played_this_turn = True
        assert run_effect(state, '02-026', 0).engine.turn_state.attack_bonus[0] == 0


class TestEffect02027:
    """Bottom-deck two chosen hand cards, then draw two."""

    def test_happy_path_moves_and_draws(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '02-027')
                 .with_hand(0, [DARKNESS_CHARACTER, FLAME_CHARACTER, WIND_CHARACTER])
                 .with_deck(0, [ELECTRICITY_CHARACTER, STP_ONE_CARD])
                 .build())
        result = run_effect(state, '02-027', 0, scripted_answers=[
            ScriptedAnswer.card_indices([0]),
            ScriptedAnswer.card_indices([0]),
        ])
        player = state.players[0]
        assert card_identities(player.hand) == [WIND_CHARACTER, ELECTRICITY_CHARACTER, STP_ONE_CARD]
        assert card_identities(player.deck) == [DARKNESS_CHARACTER, FLAME_CHARACTER]
        assert any('drew **2** cards.' in text for text in result.message_texts())

    def test_fizzles_with_fewer_than_two_hand_cards(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '02-027')
                 .with_hand(0, [DARKNESS_CHARACTER])
                 .build())
        result = run_effect(state, '02-027', 0)
        assert any('Not enough cards in hand. Effect fizzles.' in text for text in result.message_texts())

    def test_timeout_on_first_selection_changes_nothing(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '02-027')
                 .with_hand(0, [DARKNESS_CHARACTER, FLAME_CHARACTER])
                 .build())
        run_effect(state, '02-027', 0,
                   scripted_answers=[ScriptedAnswer.timeout('effect_card_select')])
        assert len(state.players[0].hand) == 2 and state.players[0].deck == []

    def test_timeout_on_second_selection_changes_nothing(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '02-027')
                 .with_hand(0, [DARKNESS_CHARACTER, FLAME_CHARACTER])
                 .build())
        run_effect(state, '02-027', 0, scripted_answers=[
            ScriptedAnswer.card_indices([0]),
            ScriptedAnswer.timeout('effect_card_select'),
        ])
        assert len(state.players[0].hand) == 2 and state.players[0].deck == []


class TestEffect02028:
    """Attack +40 when both battle characters share a power cost."""

    def test_equal_costs_grant_bonus(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, DARKNESS_CHARACTER)      # cost 5
                 .with_single_card(0, 'set_zone_c', '02-028')
                 .with_battle_card(1, ELECTRICITY_CHARACTER)   # cost 5
                 .build())
        result = run_effect(state, '02-028', 0, card_instance=state.players[0].set_zone_c)
        assert result.engine.turn_state.attack_bonus[0] == 40

    def test_different_costs_grant_nothing(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, DARKNESS_CHARACTER)      # cost 5
                 .with_single_card(0, 'set_zone_c', '02-028')
                 .with_battle_card(1, WIND_CHARACTER)          # cost 3
                 .build())
        result = run_effect(state, '02-028', 0, card_instance=state.players[0].set_zone_c)
        assert result.engine.turn_state.attack_bonus[0] == 0


def test_02_021_opponent_darkness_bonus():
    state = (GameStateBuilder()
             .with_battle_card(0, '02-021')
             .with_battle_card(1, DARKNESS_CHARACTER)
             .build())
    assert run_effect(state, '02-021', 0).engine.turn_state.attack_bonus[0] == 20
    state = (GameStateBuilder()
             .with_battle_card(0, '02-021')
             .with_battle_card(1, FLAME_CHARACTER)
             .build())
    assert run_effect(state, '02-021', 0).engine.turn_state.attack_bonus[0] == 0


def test_02_022_previous_electric_bonus():
    state = (GameStateBuilder()
             .with_battle_card(0, '02-022')
             .with_previous_battle_character(0, ELECTRICITY_CHARACTER)
             .build())
    assert run_effect(state, '02-022', 0).engine.turn_state.attack_bonus[0] == 20
    state = GameStateBuilder().with_battle_card(0, '02-022').build()
    assert run_effect(state, '02-022', 0).engine.turn_state.attack_bonus[0] == 0


def test_02_025_night_bonus():
    state = GameStateBuilder().with_battle_card(0, '02-025').with_chronos(4).build()
    assert run_effect(state, '02-025', 0).engine.turn_state.attack_bonus[0] == 50
    state = GameStateBuilder().with_battle_card(0, '02-025').with_chronos(13).build()
    assert run_effect(state, '02-025', 0).engine.turn_state.attack_bonus[0] == 0


def test_02_029_opponent_high_cost_bonus():
    state = (GameStateBuilder()
             .with_battle_card(0, '02-029')
             .with_battle_card(1, FLAME_CHARACTER)   # cost 7
             .build())
    assert run_effect(state, '02-029', 0).engine.turn_state.attack_bonus[0] == 50
    state = (GameStateBuilder()
             .with_battle_card(0, '02-029')
             .with_battle_card(1, DARKNESS_CHARACTER)  # cost 5
             .build())
    assert run_effect(state, '02-029', 0).engine.turn_state.attack_bonus[0] == 0


def test_02_030_day_bonus():
    state = GameStateBuilder().with_battle_card(0, '02-030').with_chronos(13).build()
    assert run_effect(state, '02-030', 0).engine.turn_state.attack_bonus[0] == 50
    state = GameStateBuilder().with_battle_card(0, '02-030').with_chronos(4).build()
    assert run_effect(state, '02-030', 0).engine.turn_state.attack_bonus[0] == 0


class TestEffect02031:
    """Bottom-deck two chosen hand cards, then draw two (twin of 02-027)."""

    def test_happy_path_moves_and_draws(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '02-031')
                 .with_hand(0, [DARKNESS_CHARACTER, FLAME_CHARACTER, WIND_CHARACTER])
                 .with_deck(0, [ELECTRICITY_CHARACTER, STP_ONE_CARD])
                 .build())
        result = run_effect(state, '02-031', 0, scripted_answers=[
            ScriptedAnswer.card_indices([1]),
            ScriptedAnswer.card_indices([1]),
        ])
        player = state.players[0]
        assert card_identities(player.hand) == [DARKNESS_CHARACTER, ELECTRICITY_CHARACTER, STP_ONE_CARD]
        assert card_identities(player.deck) == [FLAME_CHARACTER, WIND_CHARACTER]
        assert any('drew **2** cards.' in text for text in result.message_texts())

    def test_fizzles_with_short_hand(self):
        state = GameStateBuilder().with_battle_card(0, '02-031').with_hand(0, [DARKNESS_CHARACTER]).build()
        result = run_effect(state, '02-031', 0)
        assert any('Not enough cards in hand. Effect fizzles.' in text for text in result.message_texts())

    def test_timeout_changes_nothing(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '02-031')
                 .with_hand(0, [DARKNESS_CHARACTER, FLAME_CHARACTER])
                 .build())
        run_effect(state, '02-031', 0,
                   scripted_answers=[ScriptedAnswer.timeout('effect_card_select')])
        assert len(state.players[0].hand) == 2 and state.players[0].deck == []


def test_02_032_flat_bonus():
    state = GameStateBuilder().with_battle_card(0, '02-032').build()
    assert run_effect(state, '02-032', 0).engine.turn_state.attack_bonus[0] == 30


@pytest.mark.parametrize('effect_id, attribute_name, bonus', [
    ('02-035', 'FLAME', 20),
    ('02-040', 'DARKNESS', 30),
])
def test_power_charger_attribute_bonus(effect_id, attribute_name, bonus):
    state = (GameStateBuilder()
             .with_battle_card(0, effect_id)
             .with_power_charger(0, [ATTRIBUTE_CARD[attribute_name], WIND_CHARACTER])
             .build())
    assert run_effect(state, effect_id, 0).engine.turn_state.attack_bonus[0] == bonus

    other = next(name for name in ATTRIBUTE_CARD if name != attribute_name and name != 'WIND')
    state = (GameStateBuilder()
             .with_battle_card(0, effect_id)
             .with_power_charger(0, [ATTRIBUTE_CARD[other]])
             .build())
    assert run_effect(state, effect_id, 0).engine.turn_state.attack_bonus[0] == 0


def test_02_036_two_flame_abyss_cards_set_midnight():
    state = (GameStateBuilder()
             .with_battle_card(0, '02-036')
             .with_abyss(0, [FLAME_CHARACTER, '01-016'])
             .with_chronos(13)
             .build())
    # Need two FLAME cards; 01-016 may not be flame, so use two known flame fixtures.
    state = (GameStateBuilder()
             .with_battle_card(0, '02-036')
             .with_abyss(0, [FLAME_CHARACTER, FLAME_CHARACTER])
             .with_chronos(13)
             .build())
    result = run_effect(state, '02-036', 0)
    assert state.chronos == 4
    assert result.engine.turn_state.day_to_night_occurred is True

    state = (GameStateBuilder()
             .with_battle_card(0, '02-036')
             .with_abyss(0, [FLAME_CHARACTER, DARKNESS_CHARACTER])
             .with_chronos(13)
             .build())
    run_effect(state, '02-036', 0)
    assert state.chronos == 13


ATTRIBUTE_OVERRIDE_EFFECTS = [
    ('02-084', 'ELECTRICITY'),
    ('02-088', 'DARKNESS'),
    ('02-096', 'WIND'),
    ('02-100', 'FLAME'),
]


@pytest.mark.parametrize('effect_id, override_attribute', ATTRIBUTE_OVERRIDE_EFFECTS)
def test_attribute_override_effects(effect_id, override_attribute):
    from zutomayo.enums.attribute import Attribute

    state = (GameStateBuilder()
             .with_battle_card(0, effect_id)
             .with_battle_card(1, ATTRIBUTE_CARD['WIND' if override_attribute != 'WIND' else 'FLAME'])
             .build())
    result = run_effect(state, effect_id, 0)
    assert result.engine.turn_state.attack_bonus[0] == 10
    assert state.players[1].battle_zone.effective_attribute == Attribute[override_attribute]

    # Empty opponent battle zone: bonus still applies, nothing to override.
    state = GameStateBuilder().with_battle_card(0, effect_id).build()
    result = run_effect(state, effect_id, 0)
    assert result.engine.turn_state.attack_bonus[0] == 10


PACK_TWO_HEALS = [
    ('02-085', 'DARKNESS'),
    ('02-091', 'FLAME'),
    ('02-097', 'ELECTRICITY'),
    ('02-103', 'WIND'),
]


@pytest.mark.parametrize('effect_id, attribute_name', PACK_TWO_HEALS)
def test_pack_two_own_attribute_heals(effect_id, attribute_name):
    state = (GameStateBuilder()
             .with_battle_card(0, ATTRIBUTE_CARD[attribute_name])
             .with_single_card(0, 'set_zone_c', effect_id)
             .with_hp(0, 95)
             .build())
    run_effect(state, effect_id, 0, card_instance=state.players[0].set_zone_c)
    assert state.players[0].hp == 100, 'heals 10 capped at 100'

    other = next(name for name in ATTRIBUTE_CARD if name != attribute_name)
    state = (GameStateBuilder()
             .with_battle_card(0, ATTRIBUTE_CARD[other])
             .with_single_card(0, 'set_zone_c', effect_id)
             .with_hp(0, 80)
             .build())
    run_effect(state, effect_id, 0, card_instance=state.players[0].set_zone_c)
    assert state.players[0].hp == 80


@pytest.mark.parametrize('effect_id, required_chronos, other_chronos, bonus', [
    ('02-086', 4, 13, 20),   # night
    ('02-098', 13, 4, 20),   # day
    ('02-095', 13, 4, 30),   # day
])
def test_pack_two_day_night_bonuses(effect_id, required_chronos, other_chronos, bonus):
    state = GameStateBuilder().with_battle_card(0, effect_id).with_chronos(required_chronos).build()
    assert run_effect(state, effect_id, 0).engine.turn_state.attack_bonus[0] == bonus
    state = GameStateBuilder().with_battle_card(0, effect_id).with_chronos(other_chronos).build()
    assert run_effect(state, effect_id, 0).engine.turn_state.attack_bonus[0] == 0


EXCLUSIVE_ABYSS_BONUSES = [
    ('02-087', 'FLAME', 20),
    ('02-093', 'ELECTRICITY', 20),
    ('02-099', 'WIND', 30),
]


@pytest.mark.parametrize('effect_id, attribute_name, bonus', EXCLUSIVE_ABYSS_BONUSES)
def test_exclusive_abyss_attribute_bonus(effect_id, attribute_name, bonus):
    matching = ATTRIBUTE_CARD[attribute_name]
    state = (GameStateBuilder()
             .with_battle_card(0, effect_id)
             .with_abyss(0, [matching, matching])
             .build())
    assert run_effect(state, effect_id, 0).engine.turn_state.attack_bonus[0] == bonus

    state = (GameStateBuilder()
             .with_battle_card(0, effect_id)
             .with_abyss(0, [matching, DARKNESS_CHARACTER])
             .build())
    assert run_effect(state, effect_id, 0).engine.turn_state.attack_bonus[0] == 0

    state = GameStateBuilder().with_battle_card(0, effect_id).build()
    assert run_effect(state, effect_id, 0).engine.turn_state.attack_bonus[0] == 0


@pytest.mark.parametrize('effect_id, reduction', [('02-089', 20), ('02-090', 30)])
def test_pack_two_damage_reductions(effect_id, reduction):
    state = GameStateBuilder().with_battle_card(0, effect_id).build()
    assert run_effect(state, effect_id, 0).engine.turn_state.damage_reduction[0] == reduction


@pytest.mark.parametrize('effect_id, matching_card, other_card, bonus', [
    ('02-092', DARKNESS_CHARACTER, STP_ONE_CARD, 20),   # own cost >= 2
    ('02-104', STP_ONE_CARD, DARKNESS_CHARACTER, 20),   # own cost <= 2
])
def test_own_cost_threshold_bonuses(effect_id, matching_card, other_card, bonus):
    state = (GameStateBuilder()
             .with_battle_card(0, matching_card)
             .with_single_card(0, 'set_zone_c', effect_id)
             .build())
    result = run_effect(state, effect_id, 0, card_instance=state.players[0].set_zone_c)
    assert result.engine.turn_state.attack_bonus[0] == bonus

    state = (GameStateBuilder()
             .with_battle_card(0, other_card)
             .with_single_card(0, 'set_zone_c', effect_id)
             .build())
    result = run_effect(state, effect_id, 0, card_instance=state.players[0].set_zone_c)
    assert result.engine.turn_state.attack_bonus[0] == 0


class TestEffect02094:
    """Bottom-deck one chosen hand card, then draw one (twin of 02-082)."""

    def test_happy_path(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '02-094')
                 .with_hand(0, [DARKNESS_CHARACTER, FLAME_CHARACTER])
                 .with_deck(0, [WIND_CHARACTER])
                 .build())
        run_effect(state, '02-094', 0, scripted_answers=[ScriptedAnswer.card_indices([1])])
        player = state.players[0]
        assert card_identities(player.hand) == [DARKNESS_CHARACTER, WIND_CHARACTER]
        assert card_identities(player.deck) == [FLAME_CHARACTER]

    def test_empty_hand_fizzles(self):
        state = GameStateBuilder().with_battle_card(0, '02-094').build()
        result = run_effect(state, '02-094', 0)
        assert any('No cards in hand. Effect fizzles.' in text for text in result.message_texts())

    def test_timeout_changes_nothing(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '02-094')
                 .with_hand(0, [DARKNESS_CHARACTER])
                 .build())
        run_effect(state, '02-094', 0,
                   scripted_answers=[ScriptedAnswer.timeout('effect_card_select')])
        assert len(state.players[0].hand) == 1 and state.players[0].deck == []


def test_02_101_flat_bonus():
    state = GameStateBuilder().with_battle_card(0, '02-101').build()
    assert run_effect(state, '02-101', 0).engine.turn_state.attack_bonus[0] == 20


def test_02_102_exclusive_wind_power_charger_bonus():
    state = (GameStateBuilder()
             .with_battle_card(0, '02-102')
             .with_power_charger(0, [WIND_CHARACTER, WIND_CHARACTER])
             .build())
    assert run_effect(state, '02-102', 0).engine.turn_state.attack_bonus[0] == 50

    state = (GameStateBuilder()
             .with_battle_card(0, '02-102')
             .with_power_charger(0, [WIND_CHARACTER, FLAME_CHARACTER])
             .build())
    assert run_effect(state, '02-102', 0).engine.turn_state.attack_bonus[0] == 0

    state = GameStateBuilder().with_battle_card(0, '02-102').build()
    assert run_effect(state, '02-102', 0).engine.turn_state.attack_bonus[0] == 0


def test_02_106_two_wind_abyss_cards_set_noon():
    state = (GameStateBuilder()
             .with_battle_card(0, '02-106')
             .with_abyss(0, [WIND_CHARACTER, WIND_CHARACTER])
             .with_chronos(4)
             .build())
    result = run_effect(state, '02-106', 0)
    assert state.chronos == 13
    assert result.engine.turn_state.night_to_day_occurred is True

    state = (GameStateBuilder()
             .with_battle_card(0, '02-106')
             .with_abyss(0, [WIND_CHARACTER, FLAME_CHARACTER])
             .with_chronos(4)
             .build())
    run_effect(state, '02-106', 0)
    assert state.chronos == 4


def test_02_063_own_electric_bonus():
    state = (GameStateBuilder()
             .with_battle_card(0, ELECTRICITY_CHARACTER)
             .with_single_card(0, 'set_zone_c', '02-063')
             .build())
    result = run_effect(state, '02-063', 0, card_instance=state.players[0].set_zone_c)
    assert result.engine.turn_state.attack_bonus[0] == 30

    state = (GameStateBuilder()
             .with_battle_card(0, WIND_CHARACTER)
             .with_single_card(0, 'set_zone_c', '02-063')
             .build())
    result = run_effect(state, '02-063', 0, card_instance=state.players[0].set_zone_c)
    assert result.engine.turn_state.attack_bonus[0] == 0


def test_02_064_bonus_per_electric_power_card():
    state = (GameStateBuilder()
             .with_battle_card(0, '02-064')
             .with_power_charger(0, [ELECTRICITY_CHARACTER, ELECTRICITY_CHARACTER, WIND_CHARACTER])
             .build())
    assert run_effect(state, '02-064', 0).engine.turn_state.attack_bonus[0] == 40

    state = GameStateBuilder().with_battle_card(0, '02-064').build()
    assert run_effect(state, '02-064', 0).engine.turn_state.attack_bonus[0] == 0


def test_02_068_previous_flame_at_night_bonus():
    state = (GameStateBuilder()
             .with_battle_card(0, '02-068')
             .with_previous_battle_character(0, FLAME_CHARACTER)
             .with_chronos(4)
             .build())
    assert run_effect(state, '02-068', 0).engine.turn_state.attack_bonus[0] == 30

    state = (GameStateBuilder()
             .with_battle_card(0, '02-068')
             .with_previous_battle_character(0, FLAME_CHARACTER)
             .with_chronos(13)
             .build())
    assert run_effect(state, '02-068', 0).engine.turn_state.attack_bonus[0] == 0

    state = (GameStateBuilder()
             .with_battle_card(0, '02-068')
             .with_chronos(4)
             .build())
    assert run_effect(state, '02-068', 0).engine.turn_state.attack_bonus[0] == 0


@pytest.mark.parametrize('effect_id, attribute_name, bonus', [
    ('02-069', 'ELECTRICITY', 20),
    ('02-076', 'FLAME', 20),
])
def test_more_opponent_attribute_bonuses(effect_id, attribute_name, bonus):
    state = (GameStateBuilder()
             .with_battle_card(0, effect_id)
             .with_battle_card(1, ATTRIBUTE_CARD[attribute_name])
             .build())
    assert run_effect(state, effect_id, 0).engine.turn_state.attack_bonus[0] == bonus

    other = next(name for name in ATTRIBUTE_CARD if name != attribute_name)
    state = (GameStateBuilder()
             .with_battle_card(0, effect_id)
             .with_battle_card(1, ATTRIBUTE_CARD[other])
             .build())
    assert run_effect(state, effect_id, 0).engine.turn_state.attack_bonus[0] == 0


def test_02_081_all_darkness_abyss_bonus():
    state = (GameStateBuilder()
             .with_battle_card(0, '02-081')
             .with_abyss(0, [DARKNESS_CHARACTER, '01-009'])
             .build())
    assert run_effect(state, '02-081', 0).engine.turn_state.attack_bonus[0] == 20

    state = (GameStateBuilder()
             .with_battle_card(0, '02-081')
             .with_abyss(0, [DARKNESS_CHARACTER, FLAME_CHARACTER])
             .build())
    assert run_effect(state, '02-081', 0).engine.turn_state.attack_bonus[0] == 0

    state = GameStateBuilder().with_battle_card(0, '02-081').build()
    assert run_effect(state, '02-081', 0).engine.turn_state.attack_bonus[0] == 0


class TestEffect02082:
    """Bottom-deck one chosen hand card, then draw one."""

    def test_happy_path(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '02-082')
                 .with_hand(0, [DARKNESS_CHARACTER, FLAME_CHARACTER])
                 .with_deck(0, [WIND_CHARACTER])
                 .build())
        result = run_effect(state, '02-082', 0,
                            scripted_answers=[ScriptedAnswer.card_indices([0])])
        player = state.players[0]
        assert card_identities(player.hand) == [FLAME_CHARACTER, WIND_CHARACTER]
        assert card_identities(player.deck) == [DARKNESS_CHARACTER]
        assert any('drew **1** card.' in text for text in result.message_texts())

    def test_empty_hand_fizzles(self):
        state = GameStateBuilder().with_battle_card(0, '02-082').build()
        result = run_effect(state, '02-082', 0)
        assert any('no cards in hand. Effect fizzles.' in text for text in result.message_texts())

    def test_timeout_changes_nothing(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '02-082')
                 .with_hand(0, [DARKNESS_CHARACTER])
                 .build())
        run_effect(state, '02-082', 0,
                   scripted_answers=[ScriptedAnswer.timeout('effect_card_select')])
        assert len(state.players[0].hand) == 1 and state.players[0].deck == []


def test_02_083_night_bonus():
    state = GameStateBuilder().with_battle_card(0, '02-083').with_chronos(4).build()
    assert run_effect(state, '02-083', 0).engine.turn_state.attack_bonus[0] == 30
    state = GameStateBuilder().with_battle_card(0, '02-083').with_chronos(13).build()
    assert run_effect(state, '02-083', 0).engine.turn_state.attack_bonus[0] == 0


# ---------------------------------------------------------------------------
# Families: exclusive power-charger attribute, own-attribute bonuses,
# played-this-turn day bonus, power stars, area-enchant bounce
# ---------------------------------------------------------------------------

EXCLUSIVE_POWER_CHARGER_BONUSES = [
    ('02-053', 'DARKNESS', 40),
    ('02-056', 'FLAME', 30),
    ('02-061', 'ELECTRICITY', 40),
]


@pytest.mark.parametrize('effect_id, attribute_name, bonus', EXCLUSIVE_POWER_CHARGER_BONUSES)
def test_exclusive_power_charger_attribute_bonus(effect_id, attribute_name, bonus):
    matching = ATTRIBUTE_CARD[attribute_name]
    other = ATTRIBUTE_CARD['WIND' if attribute_name != 'WIND' else 'FLAME']

    state = (GameStateBuilder()
             .with_battle_card(0, effect_id)
             .with_power_charger(0, [matching, matching])
             .build())
    assert run_effect(state, effect_id, 0).engine.turn_state.attack_bonus[0] == bonus

    state = (GameStateBuilder()
             .with_battle_card(0, effect_id)
             .with_power_charger(0, [matching, other])
             .build())
    assert run_effect(state, effect_id, 0).engine.turn_state.attack_bonus[0] == 0

    state = GameStateBuilder().with_battle_card(0, effect_id).build()
    assert run_effect(state, effect_id, 0).engine.turn_state.attack_bonus[0] == 0, 'empty charger does not count'


PACK_TWO_OWN_ATTRIBUTE_BONUSES = [
    ('02-054', 'FLAME', 30),
    ('02-057', 'DARKNESS', 30),
    ('02-060', 'WIND', 30),
]


@pytest.mark.parametrize('effect_id, attribute_name, bonus', PACK_TWO_OWN_ATTRIBUTE_BONUSES)
def test_pack_two_own_attribute_bonus(effect_id, attribute_name, bonus):
    state = (GameStateBuilder()
             .with_battle_card(0, ATTRIBUTE_CARD[attribute_name])
             .with_single_card(0, 'set_zone_c', effect_id)
             .build())
    result = run_effect(state, effect_id, 0, card_instance=state.players[0].set_zone_c)
    assert result.engine.turn_state.attack_bonus[0] == bonus

    other = next(name for name in ATTRIBUTE_CARD if name != attribute_name)
    state = (GameStateBuilder()
             .with_battle_card(0, ATTRIBUTE_CARD[other])
             .with_single_card(0, 'set_zone_c', effect_id)
             .build())
    result = run_effect(state, effect_id, 0, card_instance=state.players[0].set_zone_c)
    assert result.engine.turn_state.attack_bonus[0] == 0


class TestEffect02055:
    """Flame character: put the opponent's area enchant on top of their deck."""

    def test_bounces_area_enchant_to_deck_top(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, FLAME_CHARACTER)
                 .with_single_card(0, 'set_zone_c', '02-055')
                 .with_single_card(1, 'set_zone_c', '02-053')
                 .with_deck(1, [WIND_CHARACTER])
                 .build())
        run_effect(state, '02-055', 0, card_instance=state.players[0].set_zone_c)
        opponent = state.players[1]
        assert opponent.set_zone_c is None
        assert card_identities(opponent.deck) == ['02-053', WIND_CHARACTER]
        assert not opponent.deck[0].face_up

    def test_requires_flame_character(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, WIND_CHARACTER)
                 .with_single_card(0, 'set_zone_c', '02-055')
                 .with_single_card(1, 'set_zone_c', '02-053')
                 .build())
        run_effect(state, '02-055', 0, card_instance=state.players[0].set_zone_c)
        assert state.players[1].set_zone_c is not None

    def test_no_opponent_area_enchant_does_nothing(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, FLAME_CHARACTER)
                 .with_single_card(0, 'set_zone_c', '02-055')
                 .build())
        run_effect(state, '02-055', 0, card_instance=state.players[0].set_zone_c)
        assert state.players[1].deck == []


def test_02_058_power_stars_per_darkness_abyss_card():
    state = (GameStateBuilder()
             .with_battle_card(0, '02-058')
             .with_abyss(0, [DARKNESS_CHARACTER, '01-009', FLAME_CHARACTER])
             .build())
    assert run_effect(state, '02-058', 0).engine.turn_state.power_bonus[0] == 2

    state = GameStateBuilder().with_battle_card(0, '02-058').build()
    assert run_effect(state, '02-058', 0).engine.turn_state.power_bonus[0] == 0


def test_02_059_daytime_played_this_turn_bonus():
    state = GameStateBuilder().with_battle_card(0, '02-059').with_chronos(13).build()
    state.players[0].battle_zone.played_this_turn = True
    assert run_effect(state, '02-059', 0).engine.turn_state.attack_bonus[0] == 30

    state = GameStateBuilder().with_battle_card(0, '02-059').with_chronos(13).build()
    assert run_effect(state, '02-059', 0).engine.turn_state.attack_bonus[0] == 0

    state = GameStateBuilder().with_battle_card(0, '02-059').with_chronos(4).build()
    state.players[0].battle_zone.played_this_turn = True
    assert run_effect(state, '02-059', 0).engine.turn_state.attack_bonus[0] == 0


def _affordable_effect_enchant_identity() -> str:
    """First zero-cost enchant with a registered effect handler, from the catalog."""
    from zutomayo.data.card_loader import load_cards
    from zutomayo.effects.effect_engine import _EFFECT_HANDLERS
    from zutomayo.enums.card_type import CardType

    for card in load_cards():
        if card.card_type == CardType.ENCHANT and card.effect in _EFFECT_HANDLERS and card.power_cost == 0:
            return f'{card.pack:02d}-{card.id:03d}'
    raise AssertionError('no zero-cost effect enchant in the catalog')


class TestEffect02006:
    """Reduce the power cost of simultaneously set character cards by 2."""

    def test_reduces_played_set_zone_character(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '02-006')
                 .with_single_card(0, 'set_zone_a', DARKNESS_CHARACTER, played_this_turn=True)
                 .build())
        run_effect(state, '02-006', 0)
        assert state.players[0].set_zone_a.power_cost_reduction == 2

    def test_ignores_characters_from_earlier_turns(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '02-006')
                 .with_single_card(0, 'set_zone_a', DARKNESS_CHARACTER, played_this_turn=False)
                 .build())
        run_effect(state, '02-006', 0)
        assert state.players[0].set_zone_a.power_cost_reduction == 0

    def test_reduces_battle_zone_character_played_this_turn(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, DARKNESS_CHARACTER, played_this_turn=True)
                 .with_single_card(0, 'set_zone_c', '02-006')
                 .build())
        run_effect(state, '02-006', 0, card_instance=state.players[0].set_zone_c)
        assert state.players[0].battle_zone.power_cost_reduction == 2


class TestEffect02015:
    """Previous darkness character during the day: optionally use an extra
    enchant from hand, then draw a card."""

    def _builder(self, enchant_identity: str) -> GameStateBuilder:
        return (GameStateBuilder()
                .with_battle_card(0, '02-015')
                .with_previous_battle_character(0, DARKNESS_CHARACTER)
                .with_chronos(13)
                .with_hand(0, [enchant_identity])
                .with_deck(0, [WIND_CHARACTER, FLAME_CHARACTER]))

    def test_uses_enchant_and_draws(self):
        enchant_identity = _affordable_effect_enchant_identity()
        state = self._builder(enchant_identity).build()
        result = run_effect(state, '02-015', 0,
                            scripted_answers=[ScriptedAnswer.card_indices([0])])
        player = state.players[0]
        assert enchant_identity not in card_identities(player.hand)
        assert enchant_identity in card_identities(player.power_charger + player.abyss)
        assert len(player.deck) == 1, 'one card drawn after the enchant use'
        assert any('drew **1** card.' in text for text in result.message_texts())

    def test_timeout_skips_enchant_but_still_draws(self):
        enchant_identity = _affordable_effect_enchant_identity()
        state = self._builder(enchant_identity).build()
        run_effect(state, '02-015', 0,
                   scripted_answers=[ScriptedAnswer.timeout('effect_card_select')])
        player = state.players[0]
        assert enchant_identity in card_identities(player.hand)
        assert len(player.deck) == 1

    def test_no_affordable_enchants_still_draws(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '02-015')
                 .with_previous_battle_character(0, DARKNESS_CHARACTER)
                 .with_chronos(13)
                 .with_hand(0, [DARKNESS_CHARACTER])
                 .with_deck(0, [WIND_CHARACTER])
                 .build())
        run_effect(state, '02-015', 0)
        assert len(state.players[0].deck) == 0 and len(state.players[0].hand) == 2

    def test_fizzles_without_previous_darkness_or_daytime(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '02-015')
                 .with_chronos(13)
                 .with_deck(0, [WIND_CHARACTER])
                 .build())
        run_effect(state, '02-015', 0)
        assert len(state.players[0].deck) == 1, 'no previous darkness: not even the draw happens'

        state = (GameStateBuilder()
                 .with_battle_card(0, '02-015')
                 .with_previous_battle_character(0, DARKNESS_CHARACTER)
                 .with_chronos(4)
                 .with_deck(0, [WIND_CHARACTER])
                 .build())
        run_effect(state, '02-015', 0)
        assert len(state.players[0].deck) == 1, 'at night: not even the draw happens'


class TestEffect02041:
    """Previous darkness character: mill the top deck card to power charger or abyss."""

    def test_starred_top_card_goes_to_power_charger(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '02-041')
                 .with_previous_battle_character(0, DARKNESS_CHARACTER)
                 .with_deck(0, [STP_ONE_CARD, WIND_CHARACTER])
                 .build())
        run_effect(state, '02-041', 0)
        player = state.players[0]
        assert card_identities(player.power_charger) == [STP_ONE_CARD]
        assert card_identities(player.deck) == [WIND_CHARACTER]

    def test_starless_top_card_goes_to_abyss(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '02-041')
                 .with_previous_battle_character(0, DARKNESS_CHARACTER)
                 .with_deck(0, [WIND_CHARACTER])
                 .build())
        run_effect(state, '02-041', 0)
        assert card_identities(state.players[0].abyss) == [WIND_CHARACTER]

    def test_no_previous_darkness_or_empty_deck_do_nothing(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '02-041')
                 .with_deck(0, [WIND_CHARACTER])
                 .build())
        run_effect(state, '02-041', 0)
        assert len(state.players[0].deck) == 1

        state = (GameStateBuilder()
                 .with_battle_card(0, '02-041')
                 .with_previous_battle_character(0, DARKNESS_CHARACTER)
                 .build())
        run_effect(state, '02-041', 0)
        assert state.players[0].abyss == [] and state.players[0].power_charger == []


def test_02_047_previous_wind_daytime_bonus():
    state = (GameStateBuilder()
             .with_battle_card(0, '02-047')
             .with_previous_battle_character(0, WIND_CHARACTER)
             .with_chronos(13)
             .build())
    assert run_effect(state, '02-047', 0).engine.turn_state.attack_bonus[0] == 40

    state = (GameStateBuilder()
             .with_battle_card(0, '02-047')
             .with_previous_battle_character(0, WIND_CHARACTER)
             .with_chronos(4)
             .build())
    assert run_effect(state, '02-047', 0).engine.turn_state.attack_bonus[0] == 0

    state = (GameStateBuilder()
             .with_battle_card(0, '02-047')
             .with_previous_battle_character(0, FLAME_CHARACTER)
             .with_chronos(13)
             .build())
    assert run_effect(state, '02-047', 0).engine.turn_state.attack_bonus[0] == 0


@pytest.mark.parametrize('effect_id, attribute_name, bonus', [
    ('02-045', 'WIND', 20),
    ('02-049', 'ELECTRICITY', 20),
])
def test_more_power_charger_attribute_bonuses(effect_id, attribute_name, bonus):
    state = (GameStateBuilder()
             .with_battle_card(0, effect_id)
             .with_power_charger(0, [ATTRIBUTE_CARD[attribute_name]])
             .build())
    assert run_effect(state, effect_id, 0).engine.turn_state.attack_bonus[0] == bonus

    state = (GameStateBuilder()
             .with_battle_card(0, effect_id)
             .with_power_charger(0, [DARKNESS_CHARACTER if attribute_name != 'DARKNESS' else FLAME_CHARACTER])
             .build())
    assert run_effect(state, effect_id, 0).engine.turn_state.attack_bonus[0] == 0


def test_02_050_electric_abyss_card_damages_opponent():
    state = (GameStateBuilder()
             .with_battle_card(0, '02-050')
             .with_abyss(0, [ELECTRICITY_CHARACTER])
             .build())
    result = run_effect(state, '02-050', 0)
    assert state.players[1].hp == 80
    assert result.engine.turn_state.damage_taken_this_turn[1] == 20

    state = (GameStateBuilder()
             .with_battle_card(0, '02-050')
             .with_abyss(0, [FLAME_CHARACTER])
             .build())
    run_effect(state, '02-050', 0)
    assert state.players[1].hp == 100


def test_02_042_previous_darkness_bonus():
    state = (GameStateBuilder()
             .with_battle_card(0, '02-042')
             .with_previous_battle_character(0, DARKNESS_CHARACTER)
             .build())
    assert run_effect(state, '02-042', 0).engine.turn_state.attack_bonus[0] == 20
    state = (GameStateBuilder()
             .with_battle_card(0, '02-042')
             .with_previous_battle_character(0, FLAME_CHARACTER)
             .build())
    assert run_effect(state, '02-042', 0).engine.turn_state.attack_bonus[0] == 0


# --- branch gap fills -------------------------------------------------------

def test_02_015_enchant_use_with_empty_deck_skips_the_draw():
    enchant_identity = _affordable_effect_enchant_identity()
    state = (GameStateBuilder()
             .with_battle_card(0, '02-015')
             .with_previous_battle_character(0, DARKNESS_CHARACTER)
             .with_chronos(13)
             .with_hand(0, [enchant_identity])
             .build())
    run_effect(state, '02-015', 0, scripted_answers=[ScriptedAnswer.card_indices([0])])
    player = state.players[0]
    assert player.deck == [] and enchant_identity not in card_identities(player.hand)


@pytest.mark.parametrize('effect_id, setup', [
    ('02-019', 'previous_wind'),
    ('02-024', 'previous_electric_night'),
])
def test_bounce_effect_selection_timeouts(effect_id, setup):
    builder = (GameStateBuilder()
               .with_battle_card(0, effect_id)
               .with_power_charger(1, [STP_ONE_CARD]))
    if setup == 'previous_wind':
        builder = builder.with_previous_battle_character(0, WIND_CHARACTER)
    else:
        builder = builder.with_previous_battle_character(0, ELECTRICITY_CHARACTER).with_chronos(4)
    state = builder.build()
    result = run_effect(state, effect_id, 0,
                        scripted_answers=[ScriptedAnswer.timeout('effect_card_select')])
    assert card_identities(state.players[1].power_charger) == [STP_ONE_CARD]
    assert any('No effect.' in text for text in result.message_texts())


def test_02_031_timeout_on_second_selection_changes_nothing():
    state = (GameStateBuilder()
             .with_battle_card(0, '02-031')
             .with_hand(0, [DARKNESS_CHARACTER, FLAME_CHARACTER])
             .build())
    run_effect(state, '02-031', 0, scripted_answers=[
        ScriptedAnswer.card_indices([0]),
        ScriptedAnswer.timeout('effect_card_select'),
    ])
    assert len(state.players[0].hand) == 2 and state.players[0].deck == []
