"""
Characterization tests for pack 01 card effects.

These tests assert CURRENT behavior (verified identical to the pre-refactor
engine by the transcript baselines); they are not a rules re-derivation.
Do not "fix" behavior here — known intended rulings are documented in the
project notes.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

import pytest  # noqa: E402

from zutomayo.enums.chronos import Chronos  # noqa: E402

from tests.support.game_state_builder import GameStateBuilder  # noqa: E402
from tests.support.effect_harness import EffectHarness, ScriptedAnswer, run_effect  # noqa: E402

# Effectless fixture characters, one per attribute:
DARKNESS_CHARACTER = '01-001'       # power cost 5
FLAME_CHARACTER = '01-002'          # power cost 7
ELECTRICITY_CHARACTER = '01-003'    # power cost 5
WIND_CHARACTER = '01-004'           # power cost 3
SECOND_DARKNESS_CHARACTER = '01-009'  # power cost 0
LOW_COST_CHARACTER = '01-009'       # power cost 0
ONE_COST_CHARACTER = '01-010'       # power cost 1
HIGH_COST_CHARACTER = '01-002'      # power cost 7

ATTRIBUTE_CARD = {
    'DARKNESS': DARKNESS_CHARACTER,
    'FLAME': FLAME_CHARACTER,
    'ELECTRICITY': ELECTRICITY_CHARACTER,
    'WIND': WIND_CHARACTER,
}

NIGHT_CHRONOS = 4
DAY_CHRONOS = 13


def run_battle_effect(builder: GameStateBuilder, effect_id: str, owner_index: int = 0, **kwargs):
    state = builder.build()
    return state, run_effect(state, effect_id, owner_index, **kwargs)


# ---------------------------------------------------------------------------
# Family: attack bonus keyed on the OPPONENT battle character's attribute
# ---------------------------------------------------------------------------

OPPONENT_ATTRIBUTE_BONUSES = [
    ('01-025', ('WIND',), 50),
    ('01-027', ('ELECTRICITY',), 50),
    ('01-029', ('DARKNESS',), 50),
    ('01-031', ('FLAME',), 50),
    ('01-054', ('WIND',), 30),
    ('01-056', ('ELECTRICITY',), 30),
    ('01-060', ('DARKNESS',), 30),
    ('01-062', ('FLAME',), 30),
    ('01-091', ('FLAME', 'WIND'), 20),
    ('01-095', ('DARKNESS', 'ELECTRICITY'), 20),
]


@pytest.mark.parametrize('effect_id, matching_attributes, bonus', OPPONENT_ATTRIBUTE_BONUSES)
def test_opponent_attribute_bonus_applies(effect_id, matching_attributes, bonus):
    for attribute_name in matching_attributes:
        state, result = run_battle_effect(
            GameStateBuilder()
            .with_battle_card(0, effect_id)
            .with_battle_card(1, ATTRIBUTE_CARD[attribute_name]),
            effect_id,
        )
        assert result.engine.turn_state.attack_bonus[0] == bonus, attribute_name


@pytest.mark.parametrize('effect_id, matching_attributes, bonus', OPPONENT_ATTRIBUTE_BONUSES)
def test_opponent_attribute_bonus_skips_other_attribute_and_empty_zone(effect_id, matching_attributes, bonus):
    non_matching = next(name for name in ATTRIBUTE_CARD if name not in matching_attributes)
    state, result = run_battle_effect(
        GameStateBuilder()
        .with_battle_card(0, effect_id)
        .with_battle_card(1, ATTRIBUTE_CARD[non_matching]),
        effect_id,
    )
    assert result.engine.turn_state.attack_bonus[0] == 0

    state, result = run_battle_effect(
        GameStateBuilder().with_battle_card(0, effect_id),
        effect_id,
    )
    assert result.engine.turn_state.attack_bonus[0] == 0


# ---------------------------------------------------------------------------
# Family: attack bonus keyed on the OWN battle character's attribute
# ---------------------------------------------------------------------------

OWN_ATTRIBUTE_BONUSES = [
    ('01-082', 'DARKNESS', 20),
    ('01-088', 'FLAME', 20),
    ('01-094', 'ELECTRICITY', 20),
    ('01-100', 'WIND', 20),
]


@pytest.mark.parametrize('effect_id, attribute_name, bonus', OWN_ATTRIBUTE_BONUSES)
def test_own_attribute_bonus(effect_id, attribute_name, bonus):
    # These are enchant effects; the character sits in the battle zone and the
    # enchant is dispatched from set zone C.
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


# ---------------------------------------------------------------------------
# Family: heal 10 HP (capped at 100) keyed on own attribute
# ---------------------------------------------------------------------------

OWN_ATTRIBUTE_HEALS = [
    ('01-081', 'DARKNESS'),
    ('01-087', 'FLAME'),
    ('01-093', 'ELECTRICITY'),
    ('01-099', 'WIND'),
]


@pytest.mark.parametrize('effect_id, attribute_name', OWN_ATTRIBUTE_HEALS)
def test_own_attribute_heal(effect_id, attribute_name):
    state = (GameStateBuilder()
             .with_battle_card(0, ATTRIBUTE_CARD[attribute_name])
             .with_single_card(0, 'set_zone_c', effect_id)
             .with_hp(0, 85)
             .build())
    run_effect(state, effect_id, 0, card_instance=state.players[0].set_zone_c)
    assert state.players[0].hp == 95

    state = (GameStateBuilder()
             .with_battle_card(0, ATTRIBUTE_CARD[attribute_name])
             .with_single_card(0, 'set_zone_c', effect_id)
             .with_hp(0, 95)
             .build())
    run_effect(state, effect_id, 0, card_instance=state.players[0].set_zone_c)
    assert state.players[0].hp == 100, 'healing must cap at 100'

    other = next(name for name in ATTRIBUTE_CARD if name != attribute_name)
    state = (GameStateBuilder()
             .with_battle_card(0, ATTRIBUTE_CARD[other])
             .with_single_card(0, 'set_zone_c', effect_id)
             .with_hp(0, 85)
             .build())
    run_effect(state, effect_id, 0, card_instance=state.players[0].set_zone_c)
    assert state.players[0].hp == 85


# ---------------------------------------------------------------------------
# Family: unconditional bonuses and reductions
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('effect_id, bonus', [('01-030', 30), ('01-089', 10)])
def test_flat_attack_bonus(effect_id, bonus):
    state, result = run_battle_effect(GameStateBuilder().with_battle_card(0, effect_id), effect_id)
    assert result.engine.turn_state.attack_bonus[0] == bonus


@pytest.mark.parametrize('effect_id, reduction', [('01-028', 40), ('01-057', 30), ('01-085', 20)])
def test_flat_damage_reduction(effect_id, reduction):
    state, result = run_battle_effect(GameStateBuilder().with_battle_card(0, effect_id), effect_id)
    assert result.engine.turn_state.damage_reduction[0] == reduction
    assert result.engine.turn_state.damage_reduction[1] == 0


# ---------------------------------------------------------------------------
# Family: day / night conditions and transitions
# ---------------------------------------------------------------------------

DAY_NIGHT_BONUSES = [
    ('01-053', Chronos.NIGHT, 30),
    ('01-083', Chronos.NIGHT, 20),
    ('01-059', Chronos.DAY, 30),
    ('01-102', Chronos.DAY, 20),
]


@pytest.mark.parametrize('effect_id, required_time, bonus', DAY_NIGHT_BONUSES)
def test_day_night_bonus(effect_id, required_time, bonus):
    matching = NIGHT_CHRONOS if required_time == Chronos.NIGHT else DAY_CHRONOS
    other = DAY_CHRONOS if required_time == Chronos.NIGHT else NIGHT_CHRONOS
    time_word = required_time.name.lower()

    state, result = run_battle_effect(
        GameStateBuilder().with_battle_card(0, effect_id).with_chronos(matching), effect_id,
    )
    assert result.engine.turn_state.attack_bonus[0] == bonus
    # Unified narration: both players are told the outcome.
    assert any(f'It is {time_word}. Attack +{bonus}!' in text for text in result.message_texts())

    state, result = run_battle_effect(
        GameStateBuilder().with_battle_card(0, effect_id).with_chronos(other), effect_id,
    )
    assert result.engine.turn_state.attack_bonus[0] == 0
    assert any(f'It is not {time_word}. No effect.' in text for text in result.message_texts())


TRANSITION_BONUSES = [
    ('01-061', Chronos.DAY, Chronos.NIGHT, 30),
    ('01-090', Chronos.DAY, Chronos.NIGHT, 20),
    ('01-096', Chronos.DAY, Chronos.NIGHT, 10),
    ('01-084', Chronos.NIGHT, Chronos.DAY, 30),
    ('01-097', Chronos.NIGHT, Chronos.DAY, 20),
]


@pytest.mark.parametrize('effect_id, from_time, to_time, bonus', TRANSITION_BONUSES)
def test_day_night_transition_bonus(effect_id, from_time, to_time, bonus):
    start = NIGHT_CHRONOS if from_time == Chronos.NIGHT else DAY_CHRONOS
    current = NIGHT_CHRONOS if to_time == Chronos.NIGHT else DAY_CHRONOS

    state = GameStateBuilder().with_battle_card(0, effect_id).with_chronos(start).build()
    state.chronos = current  # chronos moved this turn; chronos_at_turn_start keeps the start
    result = run_effect(state, effect_id, 0)
    assert result.engine.turn_state.attack_bonus[0] == bonus

    # No transition: stayed at the starting time.
    state = GameStateBuilder().with_battle_card(0, effect_id).with_chronos(start).build()
    result = run_effect(state, effect_id, 0)
    assert result.engine.turn_state.attack_bonus[0] == 0


# ---------------------------------------------------------------------------
# Family: own HP thresholds and opponent power-cost thresholds
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('effect_id, threshold, bonus', [('01-032', 30, 50), ('01-055', 50, 20)])
def test_own_hp_threshold_bonus(effect_id, threshold, bonus):
    state, result = run_battle_effect(
        GameStateBuilder().with_battle_card(0, effect_id).with_hp(0, threshold), effect_id,
    )
    assert result.engine.turn_state.attack_bonus[0] == bonus

    state, result = run_battle_effect(
        GameStateBuilder().with_battle_card(0, effect_id).with_hp(0, threshold + 1), effect_id,
    )
    assert result.engine.turn_state.attack_bonus[0] == 0


OPPONENT_COST_BONUSES = [
    ('01-058', 'high', 20),
    ('01-064', 'high', 30),
    ('01-098', 'low', 30),
    ('01-101', 'low', 20),
]


@pytest.mark.parametrize('effect_id, cost_band, bonus', OPPONENT_COST_BONUSES)
def test_opponent_power_cost_bonus(effect_id, cost_band, bonus):
    matching = HIGH_COST_CHARACTER if cost_band == 'high' else ONE_COST_CHARACTER
    non_matching = ONE_COST_CHARACTER if cost_band == 'high' else HIGH_COST_CHARACTER

    state, result = run_battle_effect(
        GameStateBuilder().with_battle_card(0, effect_id).with_battle_card(1, matching), effect_id,
    )
    assert result.engine.turn_state.attack_bonus[0] == bonus

    state, result = run_battle_effect(
        GameStateBuilder().with_battle_card(0, effect_id).with_battle_card(1, non_matching), effect_id,
    )
    assert result.engine.turn_state.attack_bonus[0] == 0

    state, result = run_battle_effect(
        GameStateBuilder().with_battle_card(0, effect_id), effect_id,
    )
    assert result.engine.turn_state.attack_bonus[0] == 0


# ---------------------------------------------------------------------------
# Singletons: day/night reversal, chronos reset, bonus draw
# ---------------------------------------------------------------------------

def test_01_005_reverses_opponent_day_night():
    state, result = run_battle_effect(GameStateBuilder().with_battle_card(0, '01-005'), '01-005')
    assert result.engine.turn_state.day_night_reversed == {0: False, 1: True}


def test_01_063_reverses_own_day_night():
    state, result = run_battle_effect(GameStateBuilder().with_battle_card(0, '01-063'), '01-063')
    assert result.engine.turn_state.day_night_reversed == {0: True, 1: False}


def test_01_008_resets_chronos_to_turn_start():
    state = GameStateBuilder().with_battle_card(0, '01-008').with_chronos(6).build()
    state.chronos = 11
    run_effect(state, '01-008', 0)
    assert state.chronos == 6


class TestEffect01006:
    """Use one enchant from the own Abyss this turn (it stays in the Abyss)."""

    def test_dispatches_selected_abyss_enchant(self):
        # 01-030 is an enchant granting a flat +30; using it via 01-006 must
        # apply its effect while the card stays in the Abyss.
        state = (GameStateBuilder()
                 .with_battle_card(0, '01-006')
                 .with_abyss(0, ['01-030'])
                 .build())
        result = run_effect(state, '01-006', 0, scripted_answers=[
            ScriptedAnswer.card_indices([0]),
        ])
        assert result.engine.turn_state.attack_bonus[0] == 30
        assert [ci.card.effect for ci in state.players[0].abyss] == ['01-030']

    def test_fizzles_without_abyss_enchants(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '01-006')
                 .with_abyss(0, [DARKNESS_CHARACTER])
                 .build())
        result = run_effect(state, '01-006', 0)
        assert any('No enchantment cards in your Abyss. Effect fizzles.' in text
                   for text in result.message_texts())

    def test_timeout_does_nothing(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '01-006')
                 .with_abyss(0, ['01-030'])
                 .build())
        result = run_effect(state, '01-006', 0, scripted_answers=[
            ScriptedAnswer.timeout('effect_card_select'),
        ])
        assert result.engine.turn_state.attack_bonus[0] == 0
        assert any('No effect.' in text for text in result.message_texts())


def test_01_026_rewinds_by_opponent_clock():
    state = GameStateBuilder().with_battle_card(0, '01-026').with_chronos(6).build()
    state.chronos = 10
    harness = EffectHarness(state)
    harness.engine.turn_state.chronos_advanced[1] = 3
    harness.run_effect('01-026', 0)
    assert state.chronos == 3, 'turn-start chronos 6 minus opponent clock 3'

    state = GameStateBuilder().with_battle_card(0, '01-026').with_chronos(6).build()
    harness = EffectHarness(state)
    harness.run_effect('01-026', 0)
    assert state.chronos == 6, 'no opponent clock recorded: nothing happens'


class TestEffect01086:
    """Swap a hand card with an Abyss card of choice."""

    def test_happy_path_swaps(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '01-086')
                 .with_hand(0, [DARKNESS_CHARACTER])
                 .with_abyss(0, [FLAME_CHARACTER])
                 .build())
        run_effect(state, '01-086', 0, scripted_answers=[
            ScriptedAnswer.card_indices([0]),
            ScriptedAnswer.card_indices([0]),
        ])
        player = state.players[0]
        assert [ci.card.pack * 1000 + ci.card.id for ci in player.hand] == [1002]
        assert [ci.card.pack * 1000 + ci.card.id for ci in player.abyss] == [1001]
        assert not player.hand[0].face_up

    def test_fizzles_without_hand_or_abyss(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '01-086')
                 .with_hand(0, [DARKNESS_CHARACTER])
                 .build())
        result = run_effect(state, '01-086', 0)
        assert any('Effect fizzles.' in text for text in result.message_texts())

    def test_timeout_on_either_step_changes_nothing(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '01-086')
                 .with_hand(0, [DARKNESS_CHARACTER])
                 .with_abyss(0, [FLAME_CHARACTER])
                 .build())
        run_effect(state, '01-086', 0, scripted_answers=[
            ScriptedAnswer.timeout('effect_card_select'),
        ])
        assert len(state.players[0].hand) == 1 and len(state.players[0].abyss) == 1

        state = (GameStateBuilder()
                 .with_battle_card(0, '01-086')
                 .with_hand(0, [DARKNESS_CHARACTER])
                 .with_abyss(0, [FLAME_CHARACTER])
                 .build())
        run_effect(state, '01-086', 0, scripted_answers=[
            ScriptedAnswer.card_indices([0]),
            ScriptedAnswer.timeout('effect_card_select'),
        ])
        assert len(state.players[0].hand) == 1 and len(state.players[0].abyss) == 1


class TestEffect01103:
    """Bottom-deck a chosen card from the opponent's Abyss."""

    def test_happy_path(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '01-103')
                 .with_abyss(1, [FLAME_CHARACTER, WIND_CHARACTER])
                 .with_deck(1, [DARKNESS_CHARACTER])
                 .build())
        run_effect(state, '01-103', 0, scripted_answers=[ScriptedAnswer.card_indices([1])])
        opponent = state.players[1]
        assert [ci.card.id for ci in opponent.abyss] == [2]
        assert [ci.card.id for ci in opponent.deck] == [1, 4]

    def test_fizzles_on_empty_abyss(self):
        state = GameStateBuilder().with_battle_card(0, '01-103').build()
        result = run_effect(state, '01-103', 0)
        assert any("Opponent's Abyss is empty. No effect." in text
                   for text in result.message_texts())

    def test_timeout_changes_nothing(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '01-103')
                 .with_abyss(1, [FLAME_CHARACTER])
                 .build())
        run_effect(state, '01-103', 0,
                   scripted_answers=[ScriptedAnswer.timeout('effect_card_select')])
        assert len(state.players[1].abyss) == 1


def test_01_104_mills_opponent_top_card_to_abyss():
    state = (GameStateBuilder()
             .with_battle_card(0, '01-104')
             .with_deck(1, [FLAME_CHARACTER, WIND_CHARACTER])
             .build())
    run_effect(state, '01-104', 0)
    opponent = state.players[1]
    assert [ci.card.id for ci in opponent.abyss] == [2]
    assert [ci.card.id for ci in opponent.deck] == [4]

    state = GameStateBuilder().with_battle_card(0, '01-104').build()
    run_effect(state, '01-104', 0)
    assert state.players[1].abyss == []


def test_01_092_draws_a_card_and_raises_hand_size():
    state = (GameStateBuilder()
             .with_battle_card(0, '01-092')
             .with_deck(0, [DARKNESS_CHARACTER, FLAME_CHARACTER])
             .build())
    result = run_effect(state, '01-092', 0)
    player = state.players[0]
    assert len(player.hand) == 1 and len(player.deck) == 1
    assert player.pending_hand_size_bonus == 1
    assert any('drew **1** card.' in text for text in result.message_texts())


def test_01_092_with_empty_deck_does_nothing():
    state = GameStateBuilder().with_battle_card(0, '01-092').build()
    result = run_effect(state, '01-092', 0)
    player = state.players[0]
    assert player.hand == [] and player.pending_hand_size_bonus == 0
    assert result.message_texts() == []


class TestEffect01007:
    """Attack +50 if the Abyss's cards have four different attributes."""

    def test_four_attributes_grant_attack_bonus(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '01-007')
                 .with_abyss(0, [DARKNESS_CHARACTER, FLAME_CHARACTER, ELECTRICITY_CHARACTER, WIND_CHARACTER])
                 .build())
        result = run_effect(state, '01-007', owner_index=0)
        assert result.engine.turn_state.attack_bonus[0] == 50
        assert result.engine.turn_state.attack_bonus[1] == 0
        assert result.prompts_seen == []

    def test_duplicate_attributes_do_not_count_twice(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '01-007')
                 .with_abyss(0, [DARKNESS_CHARACTER, SECOND_DARKNESS_CHARACTER, FLAME_CHARACTER, ELECTRICITY_CHARACTER])
                 .build())
        result = run_effect(state, '01-007', owner_index=0)
        assert result.engine.turn_state.attack_bonus[0] == 0

    def test_empty_abyss_grants_nothing(self):
        state = GameStateBuilder().with_battle_card(0, '01-007').build()
        result = run_effect(state, '01-007', owner_index=0)
        assert result.engine.turn_state.attack_bonus[0] == 0
