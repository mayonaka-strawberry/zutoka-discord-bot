"""Characterization tests for pack 04 card effects, part two (see test_pack_01
header for the characterization philosophy; test_pack_04.py holds the pilot
tests for 04-006 and 04-097)."""

from __future__ import annotations

import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

import pytest  # noqa: E402

from tests.support.game_state_builder import GameStateBuilder  # noqa: E402
from tests.support.effect_harness import ScriptedAnswer, card_identities, run_effect  # noqa: E402

DARKNESS_CHARACTER = '01-001'
FLAME_CHARACTER = '01-002'
ELECTRICITY_CHARACTER = '01-003'
WIND_CHARACTER = '01-004'
STP_ONE_CARD = '01-009'

ATTRIBUTE_CARD = {
    'DARKNESS': DARKNESS_CHARACTER,
    'FLAME': FLAME_CHARACTER,
    'ELECTRICITY': ELECTRICITY_CHARACTER,
    'WIND': WIND_CHARACTER,
}

TAIDADA_CHARACTER = '04-067'          # effectless TAIDADA character
SECOND_TAIDADA_CHARACTER = '04-068'
SHADE_CHARACTER = '04-003'            # effectless SHADE character


class TestEffect04001:
    """Reveal chosen TAIDADA characters from hand for +30 each."""

    def test_revealing_two_characters_grants_sixty(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '04-001')
                 .with_hand(0, [TAIDADA_CHARACTER, SECOND_TAIDADA_CHARACTER, DARKNESS_CHARACTER])
                 .build())
        result = run_effect(state, '04-001', 0, scripted_answers=[
            ScriptedAnswer.number(2),
            ScriptedAnswer.card_indices([0]),
            ScriptedAnswer.card_indices([0]),
        ])
        assert result.engine.turn_state.attack_bonus[0] == 60
        assert any('Attack +60!' in text for text in result.message_texts())

    def test_no_taidada_characters_fizzles(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '04-001')
                 .with_hand(0, [DARKNESS_CHARACTER])
                 .build())
        result = run_effect(state, '04-001', 0)
        assert any('No TAIDADA characters in hand. No effect.' in text
                   for text in result.message_texts())

    def test_choosing_zero_fizzles(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '04-001')
                 .with_hand(0, [TAIDADA_CHARACTER])
                 .build())
        result = run_effect(state, '04-001', 0, scripted_answers=[
            ScriptedAnswer.number(0),
        ])
        assert result.engine.turn_state.attack_bonus[0] == 0
        assert any('No effect.' in text for text in result.message_texts())


class TestEffect04002:
    """Use the effects of up to two SHADE characters from the Power Charger."""

    def test_uses_selected_shade_effect(self):
        # 04-073 is a SHADE character with a registered effect; asserting the
        # activation announcement proves the dispatch went through.
        state = (GameStateBuilder()
                 .with_battle_card(0, '04-002')
                 .with_power_charger(0, ['04-073', SHADE_CHARACTER])
                 .build())
        result = run_effect(state, '04-002', 0, scripted_answers=[
            ScriptedAnswer.number(1),
            ScriptedAnswer.card_indices([0]),
        ])
        assert any('Activating effect of' in text for text in result.message_texts())

    def test_no_shade_characters_fizzles(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '04-002')
                 .with_power_charger(0, [DARKNESS_CHARACTER, SHADE_CHARACTER])
                 .build())
        result = run_effect(state, '04-002', 0)
        assert any('No SHADE characters with effects in Power Charger. No effect.' in text
                   for text in result.message_texts())

    def test_choosing_zero_fizzles(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '04-002')
                 .with_power_charger(0, ['04-073'])
                 .build())
        result = run_effect(state, '04-002', 0, scripted_answers=[ScriptedAnswer.number(0)])
        assert any('No effect.' in text for text in result.message_texts())


TAIDADA_REVEAL_EFFECTS = [('04-007', 20), ('04-010', 20)]


@pytest.mark.parametrize('effect_id, per_card_bonus', TAIDADA_REVEAL_EFFECTS)
def test_taidada_reveal_twins(effect_id, per_card_bonus):
    state = (GameStateBuilder()
             .with_battle_card(0, effect_id)
             .with_hand(0, [TAIDADA_CHARACTER, SECOND_TAIDADA_CHARACTER])
             .build())
    result = run_effect(state, effect_id, 0, scripted_answers=[
        ScriptedAnswer.number(2),
        ScriptedAnswer.card_indices([0]),
        ScriptedAnswer.card_indices([0]),
    ])
    assert result.engine.turn_state.attack_bonus[0] == per_card_bonus * 2

    state = (GameStateBuilder()
             .with_battle_card(0, effect_id)
             .with_hand(0, [DARKNESS_CHARACTER])
             .build())
    result = run_effect(state, effect_id, 0)
    assert any('No TAIDADA characters in hand. No effect.' in text
               for text in result.message_texts())


class TestEffect04008:
    """Reveal your hand; +80 with four or more attributes."""

    def test_four_attributes_grant_bonus(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '04-008')
                 .with_hand(0, [DARKNESS_CHARACTER, FLAME_CHARACTER, ELECTRICITY_CHARACTER, WIND_CHARACTER])
                 .build())
        result = run_effect(state, '04-008', 0)
        assert result.engine.turn_state.attack_bonus[0] == 80
        bonus_messages = [text for text in result.message_texts() if 'Attack +80!' in text]
        assert bonus_messages and '(DARKNESS, ELECTRICITY, FLAME, WIND)' in bonus_messages[0]

    def test_three_attributes_grant_nothing(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '04-008')
                 .with_hand(0, [DARKNESS_CHARACTER, FLAME_CHARACTER, ELECTRICITY_CHARACTER])
                 .build())
        result = run_effect(state, '04-008', 0)
        assert result.engine.turn_state.attack_bonus[0] == 0
        assert any('Need 4+. No bonus.' in text for text in result.message_texts())

    def test_empty_hand_fizzles(self):
        state = GameStateBuilder().with_battle_card(0, '04-008').build()
        result = run_effect(state, '04-008', 0)
        assert any('Hand is empty. No effect.' in text for text in result.message_texts())


def test_04_009_abyss_count_weakens_opponent():
    state = (GameStateBuilder()
             .with_battle_card(0, '04-009')
             .with_abyss(0, [DARKNESS_CHARACTER, FLAME_CHARACTER, WIND_CHARACTER])
             .build())
    result = run_effect(state, '04-009', 0)
    assert result.engine.turn_state.attack_bonus[1] == -30
    assert result.engine.turn_state.attack_bonus[0] == 0

    state = (GameStateBuilder()
             .with_battle_card(0, '04-009')
             .with_abyss(0, [DARKNESS_CHARACTER])
             .build())
    result = run_effect(state, '04-009', 0)
    assert result.engine.turn_state.attack_bonus[1] == 0
    assert any('Need 3+. No effect.' in text for text in result.message_texts())


def test_04_011_empty_opponent_abyss_bonus():
    state = GameStateBuilder().with_battle_card(0, '04-011').build()
    assert run_effect(state, '04-011', 0).engine.turn_state.attack_bonus[0] == 50

    state = (GameStateBuilder()
             .with_battle_card(0, '04-011')
             .with_abyss(1, [DARKNESS_CHARACTER])
             .build())
    result = run_effect(state, '04-011', 0)
    assert result.engine.turn_state.attack_bonus[0] == 0
    assert any('No effect.' in text for text in result.message_texts())


@pytest.mark.parametrize('effect_id, attribute_name, bonus', [
    ('04-014', 'DARKNESS', 60),
    ('04-017', 'FLAME', 50),
])
def test_exclusive_power_charger_bonuses(effect_id, attribute_name, bonus):
    matching = ATTRIBUTE_CARD[attribute_name]
    state = (GameStateBuilder()
             .with_battle_card(0, effect_id)
             .with_power_charger(0, [matching, matching])
             .build())
    assert run_effect(state, effect_id, 0).engine.turn_state.attack_bonus[0] == bonus

    state = (GameStateBuilder()
             .with_battle_card(0, effect_id)
             .with_power_charger(0, [matching, WIND_CHARACTER])
             .build())
    result = run_effect(state, effect_id, 0)
    assert result.engine.turn_state.attack_bonus[0] == 0
    assert any('No effect.' in text for text in result.message_texts())


def test_04_020_bonus_per_electric_abyss_card():
    state = (GameStateBuilder()
             .with_battle_card(0, '04-020')
             .with_abyss(0, [ELECTRICITY_CHARACTER, ELECTRICITY_CHARACTER])
             .build())
    assert run_effect(state, '04-020', 0).engine.turn_state.attack_bonus[0] == 40

    state = GameStateBuilder().with_battle_card(0, '04-020').build()
    result = run_effect(state, '04-020', 0)
    assert result.engine.turn_state.attack_bonus[0] == 0
    assert any('No electric cards in Abyss. No effect.' in text for text in result.message_texts())


class TestStudyMeSwapBonuses:
    """04-023 (+100) and 04-024 (+110, damage never reducible)."""

    def _run_with_swap(self, effect_id: str, swapped: bool):
        from zutomayo.enums.song import Song
        from tests.support.effect_harness import EffectHarness

        state = GameStateBuilder().with_battle_card(0, effect_id).build()
        harness = EffectHarness(state)
        if swapped:
            harness.engine.turn_state.swapped_from_songs[0].add(Song.STUDY_ME)
        return harness.run_effect(effect_id, 0)

    def test_04_023_bonus_requires_study_me_swap(self):
        result = self._run_with_swap('04-023', swapped=True)
        assert result.engine.turn_state.attack_bonus[0] == 100

        result = self._run_with_swap('04-023', swapped=False)
        assert result.engine.turn_state.attack_bonus[0] == 0

    def test_04_024_bonus_and_unconditional_damage_clause(self):
        result = self._run_with_swap('04-024', swapped=True)
        assert result.engine.turn_state.attack_bonus[0] == 110
        assert result.engine.turn_state.damage_not_reducible[0] is True

        result = self._run_with_swap('04-024', swapped=False)
        assert result.engine.turn_state.attack_bonus[0] == 0
        assert result.engine.turn_state.damage_not_reducible[0] is True, \
            'the no-reduction clause applies even without the swap'


class TestEffect04027:
    """Return N chosen Abyss cards to the deck bottom or lose; the opponent
    mills the same number from their deck top."""

    def test_happy_path(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '04-027')
                 .with_abyss(0, [DARKNESS_CHARACTER, FLAME_CHARACTER])
                 .with_deck(1, [WIND_CHARACTER, ELECTRICITY_CHARACTER, STP_ONE_CARD])
                 .build())
        result = run_effect(state, '04-027', 0, scripted_answers=[
            ScriptedAnswer.number(2),
            ScriptedAnswer.card_indices([0]),
            ScriptedAnswer.card_indices([0]),
        ])
        player = state.players[0]
        opponent = state.players[1]
        assert player.abyss == [] and len(player.deck) == 2
        assert len(opponent.deck) == 1
        assert len(opponent.abyss) + len(opponent.power_charger) == 2, \
            'two milled cards routed by their SEND TO POWER'

    def test_empty_abyss_loses_the_game(self):
        state = GameStateBuilder().with_battle_card(0, '04-027').build()
        result = run_effect(state, '04-027', 0)
        assert state.players[0].hp == 0
        assert any('You lose the game!' in text for text in result.message_texts())

    def test_number_timeout_loses_the_game(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '04-027')
                 .with_abyss(0, [DARKNESS_CHARACTER])
                 .build())
        run_effect(state, '04-027', 0,
                   scripted_answers=[ScriptedAnswer.timeout('effect_number_select')])
        assert state.players[0].hp == 0

    def test_card_timeout_loses_the_game(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '04-027')
                 .with_abyss(0, [DARKNESS_CHARACTER])
                 .build())
        run_effect(state, '04-027', 0, scripted_answers=[
            ScriptedAnswer.number(1),
            ScriptedAnswer.timeout('effect_card_select'),
        ])
        assert state.players[0].hp == 0

    def test_empty_opponent_deck_moves_nothing(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '04-027')
                 .with_abyss(0, [DARKNESS_CHARACTER])
                 .build())
        result = run_effect(state, '04-027', 0, scripted_answers=[
            ScriptedAnswer.number(1),
            ScriptedAnswer.card_indices([0]),
        ])
        assert any('No cards moved to Abyss.' in text for text in result.message_texts())


class TestEffect04028:
    """Return six Abyss cards to the deck bottom or lose; then pick any Chronos."""

    def _builder(self):
        return (GameStateBuilder()
                .with_battle_card(0, '04-028')
                .with_abyss(0, [DARKNESS_CHARACTER, FLAME_CHARACTER, ELECTRICITY_CHARACTER,
                                WIND_CHARACTER, STP_ONE_CARD, '01-010'])
                .with_chronos(4))

    def test_happy_path_sets_chosen_chronos(self):
        state = self._builder().build()
        answers = [ScriptedAnswer.card_indices([0]) for _ in range(6)]
        answers.append(ScriptedAnswer.number(15))
        run_effect(state, '04-028', 0, scripted_answers=answers)
        assert state.players[0].abyss == [] and len(state.players[0].deck) == 6
        assert state.chronos == 15

    def test_fewer_than_six_abyss_cards_loses(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '04-028')
                 .with_abyss(0, [DARKNESS_CHARACTER])
                 .build())
        run_effect(state, '04-028', 0)
        assert state.players[0].hp == 0

    def test_selection_timeout_loses(self):
        state = self._builder().build()
        run_effect(state, '04-028', 0,
                   scripted_answers=[ScriptedAnswer.timeout('effect_card_select')])
        assert state.players[0].hp == 0

    def test_chronos_timeout_keeps_time(self):
        state = self._builder().build()
        answers = [ScriptedAnswer.card_indices([0]) for _ in range(6)]
        answers.append(ScriptedAnswer.timeout('effect_number_select'))
        result = run_effect(state, '04-028', 0, scripted_answers=answers)
        assert state.chronos == 4
        assert any('Chronos unchanged.' in text for text in result.message_texts())


STUDY_ME_CHARACTER = '01-001'   # effectless STUDY ME character, STP 0
STP_TWO_CARD = '01-013'


def test_04_059_three_power_charger_attributes_heal():
    state = (GameStateBuilder()
             .with_battle_card(0, '04-059')
             .with_power_charger(0, [DARKNESS_CHARACTER, FLAME_CHARACTER, WIND_CHARACTER])
             .with_hp(0, 40)
             .build())
    result = run_effect(state, '04-059', 0)
    assert state.players[0].hp == 90
    heal_messages = [text for text in result.message_texts() if 'HP +50!' in text]
    assert heal_messages and '(DARKNESS, FLAME, WIND)' in heal_messages[0]

    state = (GameStateBuilder()
             .with_battle_card(0, '04-059')
             .with_power_charger(0, [DARKNESS_CHARACTER, FLAME_CHARACTER])
             .with_hp(0, 40)
             .build())
    result = run_effect(state, '04-059', 0)
    assert state.players[0].hp == 40
    assert any('Need 3+. No effect.' in text for text in result.message_texts())


@pytest.mark.parametrize('effect_id, bonus', [('04-060', 20), ('04-066', 30)])
def test_opponent_send_to_power_two_bonuses(effect_id, bonus):
    state = (GameStateBuilder()
             .with_battle_card(0, effect_id)
             .with_battle_card(1, STP_TWO_CARD)
             .build())
    assert run_effect(state, effect_id, 0).engine.turn_state.attack_bonus[0] == bonus

    state = (GameStateBuilder()
             .with_battle_card(0, effect_id)
             .with_battle_card(1, STP_ONE_CARD)
             .build())
    result = run_effect(state, effect_id, 0)
    assert result.engine.turn_state.attack_bonus[0] == 0
    assert any('No effect.' in text for text in result.message_texts())

    state = GameStateBuilder().with_battle_card(0, effect_id).build()
    assert run_effect(state, effect_id, 0).engine.turn_state.attack_bonus[0] == 0


class TestEffect04061:
    """Bottom-deck any number of chosen hand cards, then draw that many."""

    def test_happy_path(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '04-061')
                 .with_hand(0, [DARKNESS_CHARACTER, FLAME_CHARACTER, WIND_CHARACTER])
                 .with_deck(0, [ELECTRICITY_CHARACTER, STP_ONE_CARD])
                 .build())
        result = run_effect(state, '04-061', 0, scripted_answers=[
            ScriptedAnswer.number(2),
            ScriptedAnswer.card_indices([0]),
            ScriptedAnswer.card_indices([0]),
        ])
        player = state.players[0]
        assert card_identities(player.hand) == [WIND_CHARACTER, ELECTRICITY_CHARACTER, STP_ONE_CARD]
        assert card_identities(player.deck) == [DARKNESS_CHARACTER, FLAME_CHARACTER]
        assert any('drew **2** cards.' in text for text in result.message_texts())

    def test_partial_selection_on_timeout_moves_partial(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '04-061')
                 .with_hand(0, [DARKNESS_CHARACTER, FLAME_CHARACTER])
                 .with_deck(0, [ELECTRICITY_CHARACTER])
                 .build())
        run_effect(state, '04-061', 0, scripted_answers=[
            ScriptedAnswer.number(2),
            ScriptedAnswer.card_indices([0]),
            ScriptedAnswer.timeout('effect_card_select'),
        ])
        player = state.players[0]
        assert card_identities(player.deck) == [DARKNESS_CHARACTER]
        assert len(player.hand) == 2, 'one bottomed, one drawn'

    def test_zero_selection_and_empty_hand_fizzle(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '04-061')
                 .with_hand(0, [DARKNESS_CHARACTER])
                 .build())
        result = run_effect(state, '04-061', 0, scripted_answers=[ScriptedAnswer.number(0)])
        assert any('No effect.' in text for text in result.message_texts())

        state = GameStateBuilder().with_battle_card(0, '04-061').build()
        result = run_effect(state, '04-061', 0)
        assert any('Hand is empty. No effect.' in text for text in result.message_texts())


ATTRIBUTE_MASS_DISCARD_EFFECTS = [
    ('04-062', 'DARKNESS'),
    ('04-063', 'FLAME'),
]


@pytest.mark.parametrize('effect_id, attribute_name', ATTRIBUTE_MASS_DISCARD_EFFECTS)
def test_attribute_mass_discard_draw_twins(effect_id, attribute_name):
    matching = ATTRIBUTE_CARD[attribute_name]
    state = (GameStateBuilder()
             .with_battle_card(0, effect_id)
             .with_hand(0, [matching, matching, WIND_CHARACTER if attribute_name != 'WIND' else DARKNESS_CHARACTER])
             .with_deck(0, [ELECTRICITY_CHARACTER, STP_ONE_CARD])
             .build())
    result = run_effect(state, effect_id, 0, scripted_answers=[
        ScriptedAnswer.number(2),
        ScriptedAnswer.card_indices([0]),
        ScriptedAnswer.card_indices([0]),
    ])
    player = state.players[0]
    assert card_identities(player.abyss) == [matching, matching]
    assert len(player.hand) == 3, 'one non-matching kept, two drawn'
    assert any('drew **2** cards.' in text for text in result.message_texts())

    state = (GameStateBuilder()
             .with_battle_card(0, effect_id)
             .with_hand(0, [WIND_CHARACTER if attribute_name != 'WIND' else DARKNESS_CHARACTER])
             .build())
    result = run_effect(state, effect_id, 0)
    assert any('No effect.' in text for text in result.message_texts())


def test_04_064_exclusive_wind_abyss_bonus():
    state = (GameStateBuilder()
             .with_battle_card(0, '04-064')
             .with_abyss(0, [WIND_CHARACTER, WIND_CHARACTER])
             .build())
    assert run_effect(state, '04-064', 0).engine.turn_state.attack_bonus[0] == 60

    state = (GameStateBuilder()
             .with_battle_card(0, '04-064')
             .with_abyss(0, [WIND_CHARACTER, DARKNESS_CHARACTER])
             .build())
    assert run_effect(state, '04-064', 0).engine.turn_state.attack_bonus[0] == 0

    state = GameStateBuilder().with_battle_card(0, '04-064').build()
    result = run_effect(state, '04-064', 0)
    assert result.engine.turn_state.attack_bonus[0] == 0
    assert any('Abyss is empty. No effect.' in text for text in result.message_texts())


def test_04_065_study_me_cost_reduction():
    state = (GameStateBuilder()
             .with_battle_card(0, STUDY_ME_CHARACTER)
             .with_single_card(0, 'set_zone_c', '04-065')
             .build())
    run_effect(state, '04-065', 0, card_instance=state.players[0].set_zone_c)
    assert state.players[0].battle_zone.power_cost_reduction == 2

    state = (GameStateBuilder()
             .with_battle_card(0, TAIDADA_CHARACTER)
             .with_single_card(0, 'set_zone_c', '04-065')
             .build())
    result = run_effect(state, '04-065', 0, card_instance=state.players[0].set_zone_c)
    assert state.players[0].battle_zone.power_cost_reduction == 0
    assert any('not STUDY ME. No Power Cost reduction.' in text for text in result.message_texts())


class TestEffect04053:
    """Optionally move a STUDY ME character from hand to the Power Charger, then draw."""

    def test_happy_path(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '04-053')
                 .with_hand(0, [STUDY_ME_CHARACTER, WIND_CHARACTER])
                 .with_deck(0, [FLAME_CHARACTER])
                 .build())
        result = run_effect(state, '04-053', 0,
                            scripted_answers=[ScriptedAnswer.card_indices([0])])
        player = state.players[0]
        assert card_identities(player.power_charger) == [STUDY_ME_CHARACTER]
        assert card_identities(player.hand) == [WIND_CHARACTER, FLAME_CHARACTER]
        assert any('drew **1** card.' in text for text in result.message_texts())

    def test_no_study_me_characters_fizzles(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '04-053')
                 .with_hand(0, [WIND_CHARACTER])
                 .build())
        result = run_effect(state, '04-053', 0)
        assert any('No STUDY ME characters in hand. No effect.' in text
                   for text in result.message_texts())

    def test_timeout_skips(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '04-053')
                 .with_hand(0, [STUDY_ME_CHARACTER])
                 .build())
        result = run_effect(state, '04-053', 0,
                            scripted_answers=[ScriptedAnswer.timeout('effect_card_select')])
        assert len(state.players[0].hand) == 1
        assert any('No card selected. No effect.' in text for text in result.message_texts())


ATTRIBUTE_DISCARD_DRAW_EFFECTS = [
    ('04-054', 'ELECTRICITY'),
    ('04-058', 'WIND'),
]


@pytest.mark.parametrize('effect_id, attribute_name', ATTRIBUTE_DISCARD_DRAW_EFFECTS)
def test_attribute_discard_draw_twins(effect_id, attribute_name):
    matching = ATTRIBUTE_CARD[attribute_name]
    state = (GameStateBuilder()
             .with_battle_card(0, effect_id)
             .with_hand(0, [matching, DARKNESS_CHARACTER])
             .with_deck(0, [FLAME_CHARACTER])
             .build())
    result = run_effect(state, effect_id, 0,
                        scripted_answers=[ScriptedAnswer.card_indices([0])])
    player = state.players[0]
    assert card_identities(player.abyss) == [matching]
    assert any('drew **1** card.' in text for text in result.message_texts())

    state = (GameStateBuilder()
             .with_battle_card(0, effect_id)
             .with_hand(0, [DARKNESS_CHARACTER if attribute_name != 'DARKNESS' else FLAME_CHARACTER])
             .build())
    result = run_effect(state, effect_id, 0)
    assert any('No effect.' in text for text in result.message_texts())


def test_04_055_taidada_battle_character_heals():
    state = (GameStateBuilder()
             .with_battle_card(0, TAIDADA_CHARACTER)
             .with_single_card(0, 'set_zone_c', '04-055')
             .with_hp(0, 70)
             .build())
    run_effect(state, '04-055', 0, card_instance=state.players[0].set_zone_c)
    assert state.players[0].hp == 90

    state = (GameStateBuilder()
             .with_battle_card(0, WIND_CHARACTER)
             .with_single_card(0, 'set_zone_c', '04-055')
             .with_hp(0, 70)
             .build())
    run_effect(state, '04-055', 0, card_instance=state.players[0].set_zone_c)
    assert state.players[0].hp == 70


class TestEffect04057:
    """Three or more own Abyss cards: mill the opponent's top two."""

    def test_mills_two_cards(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '04-057')
                 .with_abyss(0, [DARKNESS_CHARACTER, FLAME_CHARACTER, WIND_CHARACTER])
                 .with_deck(1, [ELECTRICITY_CHARACTER, STP_ONE_CARD, WIND_CHARACTER])
                 .build())
        run_effect(state, '04-057', 0)
        opponent = state.players[1]
        assert card_identities(opponent.deck) == [WIND_CHARACTER]
        assert len(opponent.abyss) + len(opponent.power_charger) == 2

    def test_too_few_abyss_cards_fizzles(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '04-057')
                 .with_abyss(0, [DARKNESS_CHARACTER])
                 .with_deck(1, [ELECTRICITY_CHARACTER])
                 .build())
        result = run_effect(state, '04-057', 0)
        assert len(state.players[1].deck) == 1
        assert any('Need 3+. No effect.' in text for text in result.message_texts())

    def test_empty_opponent_deck_fizzles(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '04-057')
                 .with_abyss(0, [DARKNESS_CHARACTER, FLAME_CHARACTER, WIND_CHARACTER])
                 .build())
        result = run_effect(state, '04-057', 0)
        assert any("Opponent's deck is empty. No cards moved." in text
                   for text in result.message_texts())


@pytest.mark.parametrize('effect_id, bonus', [('04-029', 100), ('04-030', 40), ('04-056', 50)])
def test_empty_opponent_abyss_bonuses(effect_id, bonus):
    state = GameStateBuilder().with_battle_card(0, effect_id).build()
    assert run_effect(state, effect_id, 0).engine.turn_state.attack_bonus[0] == bonus

    state = (GameStateBuilder()
             .with_battle_card(0, effect_id)
             .with_abyss(1, [DARKNESS_CHARACTER])
             .build())
    assert run_effect(state, effect_id, 0).engine.turn_state.attack_bonus[0] == 0


class TestEffect04031:
    """Attack +100 with four or more attributes in the Power Charger."""

    def test_four_attributes_grant_bonus(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '04-031')
                 .with_power_charger(0, [DARKNESS_CHARACTER, FLAME_CHARACTER,
                                         ELECTRICITY_CHARACTER, WIND_CHARACTER])
                 .build())
        result = run_effect(state, '04-031', 0)
        assert result.engine.turn_state.attack_bonus[0] == 100
        bonus_messages = [text for text in result.message_texts() if 'Attack +100!' in text]
        assert bonus_messages and '(DARKNESS, ELECTRICITY, FLAME, WIND)' in bonus_messages[0]

    def test_three_attributes_grant_nothing(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '04-031')
                 .with_power_charger(0, [DARKNESS_CHARACTER, FLAME_CHARACTER, ELECTRICITY_CHARACTER])
                 .build())
        result = run_effect(state, '04-031', 0)
        assert result.engine.turn_state.attack_bonus[0] == 0
        assert any('Need 4+. No effect.' in text for text in result.message_texts())

    def test_empty_power_charger_fizzles(self):
        state = GameStateBuilder().with_battle_card(0, '04-031').build()
        result = run_effect(state, '04-031', 0)
        assert any('Power Charger is empty. No effect.' in text for text in result.message_texts())


class TestEffect04032:
    """Reveal your hand; +50 with four or more attributes (self-removal on
    opponent area enchant lives in check_area_enchant_removal)."""

    def test_four_attributes_grant_bonus(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, DARKNESS_CHARACTER)
                 .with_single_card(0, 'set_zone_c', '04-032')
                 .with_hand(0, [DARKNESS_CHARACTER, FLAME_CHARACTER, ELECTRICITY_CHARACTER, WIND_CHARACTER])
                 .build())
        result = run_effect(state, '04-032', 0, card_instance=state.players[0].set_zone_c)
        assert result.engine.turn_state.attack_bonus[0] == 50

    def test_fewer_attributes_grant_nothing(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, DARKNESS_CHARACTER)
                 .with_single_card(0, 'set_zone_c', '04-032')
                 .with_hand(0, [DARKNESS_CHARACTER, FLAME_CHARACTER])
                 .build())
        result = run_effect(state, '04-032', 0, card_instance=state.players[0].set_zone_c)
        assert result.engine.turn_state.attack_bonus[0] == 0
        assert any('Need 4+. No bonus.' in text for text in result.message_texts())

    def test_empty_hand_skips_reveal(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, DARKNESS_CHARACTER)
                 .with_single_card(0, 'set_zone_c', '04-032')
                 .build())
        result = run_effect(state, '04-032', 0, card_instance=state.players[0].set_zone_c)
        assert any('Hand is empty. No attribute bonus.' in text for text in result.message_texts())


def test_04_033_exclusive_wind_abyss_bonus():
    state = (GameStateBuilder()
             .with_battle_card(0, '04-033')
             .with_abyss(0, [WIND_CHARACTER, WIND_CHARACTER])
             .build())
    assert run_effect(state, '04-033', 0).engine.turn_state.attack_bonus[0] == 20

    state = (GameStateBuilder()
             .with_battle_card(0, '04-033')
             .with_abyss(0, [WIND_CHARACTER, DARKNESS_CHARACTER])
             .build())
    result = run_effect(state, '04-033', 0)
    assert result.engine.turn_state.attack_bonus[0] == 0
    assert any('non-wind cards. No effect.' in text for text in result.message_texts())

    state = GameStateBuilder().with_battle_card(0, '04-033').build()
    result = run_effect(state, '04-033', 0)
    assert result.engine.turn_state.attack_bonus[0] == 0
    assert any('Abyss is empty. No effect.' in text for text in result.message_texts())


@pytest.mark.parametrize('effect_id, bonus', [('04-034', 30), ('04-039', 40)])
def test_bonus_when_opponent_attack_is_zero(effect_id, bonus):
    # Opponent character costs 5 with an empty Power Charger: the power-cost
    # gate makes its effective attack 0.
    state = (GameStateBuilder()
             .with_battle_card(0, effect_id)
             .with_battle_card(1, DARKNESS_CHARACTER)
             .build())
    assert run_effect(state, effect_id, 0).engine.turn_state.attack_bonus[0] == bonus

    # Zero-cost opponent character attacks at full value: no bonus.
    state = (GameStateBuilder()
             .with_battle_card(0, effect_id)
             .with_battle_card(1, STP_ONE_CARD)
             .build())
    result = run_effect(state, effect_id, 0)
    assert result.engine.turn_state.attack_bonus[0] == 0
    assert any('No effect.' in text for text in result.message_texts())


class TestEffect04035:
    """Reveal chosen TAIDADA characters for +10 each; a mid-sequence timeout
    keeps the partial reveal."""

    def test_partial_reveal_on_timeout_still_counts(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '04-035')
                 .with_hand(0, [TAIDADA_CHARACTER, SECOND_TAIDADA_CHARACTER])
                 .build())
        result = run_effect(state, '04-035', 0, scripted_answers=[
            ScriptedAnswer.number(2),
            ScriptedAnswer.card_indices([0]),
            ScriptedAnswer.timeout('effect_card_select'),
        ])
        assert result.engine.turn_state.attack_bonus[0] == 10

    def test_full_reveal(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '04-035')
                 .with_hand(0, [TAIDADA_CHARACTER, SECOND_TAIDADA_CHARACTER])
                 .build())
        result = run_effect(state, '04-035', 0, scripted_answers=[
            ScriptedAnswer.number(2),
            ScriptedAnswer.card_indices([0]),
            ScriptedAnswer.card_indices([0]),
        ])
        assert result.engine.turn_state.attack_bonus[0] == 20

    def test_zero_selection_fizzles(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '04-035')
                 .with_hand(0, [TAIDADA_CHARACTER])
                 .build())
        result = run_effect(state, '04-035', 0, scripted_answers=[ScriptedAnswer.number(0)])
        assert result.engine.turn_state.attack_bonus[0] == 0


class TestEffect04041:
    """SHADE swap: negate the opponent's enchants played this turn."""

    def _run(self, swapped: bool, opponent_enchant_played_this_turn: bool):
        from zutomayo.enums.song import Song
        from tests.support.effect_harness import EffectHarness

        builder = GameStateBuilder().with_battle_card(0, '04-041')
        if opponent_enchant_played_this_turn is not None:
            builder = builder.with_single_card(
                1, 'set_zone_a', '01-030', played_this_turn=opponent_enchant_played_this_turn,
            )
        state = builder.build()
        harness = EffectHarness(state)
        if swapped:
            harness.engine.turn_state.swapped_from_songs[0].add(Song.SHADE)
        return state, harness.run_effect('04-041', 0)

    def test_disables_played_enchants_after_shade_swap(self):
        state, result = self._run(swapped=True, opponent_enchant_played_this_turn=True)
        assert state.players[1].set_zone_a.effects_disabled is True
        assert any('Disabled opponent enchant(s)' in text for text in result.message_texts())

    def test_ignores_enchants_from_earlier_turns(self):
        state, result = self._run(swapped=True, opponent_enchant_played_this_turn=False)
        assert state.players[1].set_zone_a.effects_disabled is False
        assert any('no enchants to disable' in text for text in result.message_texts())

    def test_no_shade_swap_fizzles(self):
        state, result = self._run(swapped=False, opponent_enchant_played_this_turn=True)
        assert state.players[1].set_zone_a.effects_disabled is False
        assert any('Did not swap with a SHADE character this turn. No effect.' in text
                   for text in result.message_texts())


class TestShadeSwapRewards:
    """04-073 heal, 04-074 opponent attack malus, 04-075 opponent damage."""

    def _run(self, effect_id: str, swapped: bool, own_hp: int = 100):
        from zutomayo.enums.song import Song
        from tests.support.effect_harness import EffectHarness

        state = GameStateBuilder().with_battle_card(0, effect_id).with_hp(0, own_hp).build()
        harness = EffectHarness(state)
        if swapped:
            harness.engine.turn_state.swapped_from_songs[0].add(Song.SHADE)
        return state, harness.run_effect(effect_id, 0)

    def test_04_073_heals_after_shade_swap(self):
        state, result = self._run('04-073', swapped=True, own_hp=70)
        assert state.players[0].hp == 90
        state, result = self._run('04-073', swapped=False, own_hp=70)
        assert state.players[0].hp == 70

    def test_04_074_weakens_opponent_after_shade_swap(self):
        state, result = self._run('04-074', swapped=True)
        assert result.engine.turn_state.attack_bonus[1] == -30
        state, result = self._run('04-074', swapped=False)
        assert result.engine.turn_state.attack_bonus[1] == 0

    def test_04_075_damages_opponent_after_shade_swap(self):
        state, result = self._run('04-075', swapped=True)
        assert state.players[1].hp == 80
        assert result.engine.turn_state.damage_taken_this_turn[1] == 20
        state, result = self._run('04-075', swapped=False)
        assert state.players[1].hp == 100


def test_04_084_bonus_when_opponent_attack_is_zero():
    state = (GameStateBuilder()
             .with_battle_card(0, '04-084')
             .with_battle_card(1, DARKNESS_CHARACTER)   # cost 5, no power: gated to 0
             .build())
    assert run_effect(state, '04-084', 0).engine.turn_state.attack_bonus[0] == 50

    state = (GameStateBuilder()
             .with_battle_card(0, '04-084')
             .with_battle_card(1, STP_ONE_CARD)         # cost 0: attacks at full value
             .build())
    result = run_effect(state, '04-084', 0)
    assert result.engine.turn_state.attack_bonus[0] == 0
    assert any('No effect.' in text for text in result.message_texts())

    state = GameStateBuilder().with_battle_card(0, '04-084').build()
    assert run_effect(state, '04-084', 0).engine.turn_state.attack_bonus[0] == 50, \
        'empty battle zone counts as attack 0'


def test_04_087_study_me_swap_bonus():
    from zutomayo.enums.song import Song
    from tests.support.effect_harness import EffectHarness

    state = GameStateBuilder().with_battle_card(0, '04-087').build()
    harness = EffectHarness(state)
    harness.engine.turn_state.swapped_from_songs[0].add(Song.STUDY_ME)
    assert harness.run_effect('04-087', 0).engine.turn_state.attack_bonus[0] == 50

    state = GameStateBuilder().with_battle_card(0, '04-087').build()
    assert run_effect(state, '04-087', 0).engine.turn_state.attack_bonus[0] == 0


class TestEffect04088:
    """Return an Abyss card to the deck bottom or lose; then rearrange the
    opponent's top three deck cards."""

    def test_happy_path_rearranges_opponent_deck(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '04-088')
                 .with_abyss(0, [DARKNESS_CHARACTER])
                 .with_deck(1, [FLAME_CHARACTER, WIND_CHARACTER, ELECTRICITY_CHARACTER, STP_ONE_CARD])
                 .build())
        run_effect(state, '04-088', 0, scripted_answers=[
            ScriptedAnswer.card_indices([0]),   # abyss card to deck bottom
            ScriptedAnswer.card_indices([2]),   # position 1: electricity
            ScriptedAnswer.card_indices([1]),   # position 2: wind
        ])
        player = state.players[0]
        assert player.abyss == [] and card_identities(player.deck) == [DARKNESS_CHARACTER]
        assert card_identities(state.players[1].deck) == [
            ELECTRICITY_CHARACTER, WIND_CHARACTER, FLAME_CHARACTER, STP_ONE_CARD,
        ]

    def test_empty_abyss_loses(self):
        state = GameStateBuilder().with_battle_card(0, '04-088').build()
        run_effect(state, '04-088', 0)
        assert state.players[0].hp == 0

    def test_selection_timeout_loses(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '04-088')
                 .with_abyss(0, [DARKNESS_CHARACTER])
                 .build())
        run_effect(state, '04-088', 0,
                   scripted_answers=[ScriptedAnswer.timeout('effect_card_select')])
        assert state.players[0].hp == 0

    def test_rearrange_timeout_keeps_current_order(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '04-088')
                 .with_abyss(0, [DARKNESS_CHARACTER])
                 .with_deck(1, [FLAME_CHARACTER, WIND_CHARACTER, ELECTRICITY_CHARACTER])
                 .build())
        run_effect(state, '04-088', 0, scripted_answers=[
            ScriptedAnswer.card_indices([0]),
            ScriptedAnswer.timeout('effect_card_select'),
        ])
        assert card_identities(state.players[1].deck) == [
            FLAME_CHARACTER, WIND_CHARACTER, ELECTRICITY_CHARACTER,
        ]

    def test_single_opponent_card_needs_no_rearranging(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '04-088')
                 .with_abyss(0, [DARKNESS_CHARACTER])
                 .with_deck(1, [FLAME_CHARACTER])
                 .build())
        result = run_effect(state, '04-088', 0,
                            scripted_answers=[ScriptedAnswer.card_indices([0])])
        assert any('no rearranging needed' in text for text in result.message_texts())

    def test_empty_opponent_deck_skips_rearranging(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '04-088')
                 .with_abyss(0, [DARKNESS_CHARACTER])
                 .build())
        result = run_effect(state, '04-088', 0,
                            scripted_answers=[ScriptedAnswer.card_indices([0])])
        assert any('Cannot rearrange.' in text for text in result.message_texts())


NEKO_RESET_CHARACTER = '01-012'   # effectless NEKO_RESET character


class TestEffect04089:
    """TAIDADA battle character: draw one and raise the hand limit."""

    def test_draws_and_raises_hand_limit(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, TAIDADA_CHARACTER)
                 .with_single_card(0, 'set_zone_c', '04-089')
                 .with_deck(0, [WIND_CHARACTER])
                 .build())
        result = run_effect(state, '04-089', 0, card_instance=state.players[0].set_zone_c)
        player = state.players[0]
        assert len(player.hand) == 1 and player.pending_hand_size_bonus == 1
        assert any('Hand size permanently increased by 1!' in text for text in result.message_texts())

    def test_non_taidada_character_fizzles(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, WIND_CHARACTER)
                 .with_single_card(0, 'set_zone_c', '04-089')
                 .with_deck(0, [WIND_CHARACTER])
                 .build())
        result = run_effect(state, '04-089', 0, card_instance=state.players[0].set_zone_c)
        assert state.players[0].pending_hand_size_bonus == 0
        assert any('not TAIDADA. No effect.' in text for text in result.message_texts())

    def test_empty_deck_cannot_draw(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, TAIDADA_CHARACTER)
                 .with_single_card(0, 'set_zone_c', '04-089')
                 .build())
        result = run_effect(state, '04-089', 0, card_instance=state.players[0].set_zone_c)
        assert state.players[0].pending_hand_size_bonus == 0
        assert any('deck is empty. Cannot draw.' in text for text in result.message_texts())


class TestEffect04090:
    """Bottom-deck a chosen card from the opponent's Abyss."""

    def test_happy_path(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '04-090')
                 .with_abyss(1, [FLAME_CHARACTER, WIND_CHARACTER])
                 .with_deck(1, [DARKNESS_CHARACTER])
                 .build())
        run_effect(state, '04-090', 0, scripted_answers=[ScriptedAnswer.card_indices([1])])
        opponent = state.players[1]
        assert card_identities(opponent.abyss) == [FLAME_CHARACTER]
        assert card_identities(opponent.deck) == [DARKNESS_CHARACTER, WIND_CHARACTER]

    def test_empty_abyss_and_timeout_fizzle(self):
        state = GameStateBuilder().with_battle_card(0, '04-090').build()
        result = run_effect(state, '04-090', 0)
        assert any("Opponent's Abyss is empty. No effect." in text for text in result.message_texts())

        state = (GameStateBuilder()
                 .with_battle_card(0, '04-090')
                 .with_abyss(1, [FLAME_CHARACTER])
                 .build())
        run_effect(state, '04-090', 0,
                   scripted_answers=[ScriptedAnswer.timeout('effect_card_select')])
        assert len(state.players[1].abyss) == 1


def test_04_091_taidada_reveal_ten_per_card():
    state = (GameStateBuilder()
             .with_battle_card(0, '04-091')
             .with_hand(0, [TAIDADA_CHARACTER, SECOND_TAIDADA_CHARACTER])
             .build())
    result = run_effect(state, '04-091', 0, scripted_answers=[
        ScriptedAnswer.number(2),
        ScriptedAnswer.card_indices([0]),
        ScriptedAnswer.card_indices([0]),
    ])
    assert result.engine.turn_state.attack_bonus[0] == 20

    state = (GameStateBuilder()
             .with_battle_card(0, '04-091')
             .with_hand(0, [DARKNESS_CHARACTER])
             .build())
    result = run_effect(state, '04-091', 0)
    assert any('No TAIDADA characters in hand. No effect.' in text for text in result.message_texts())


def test_04_092_shade_battle_character_weakens_opponent():
    state = (GameStateBuilder()
             .with_battle_card(0, SHADE_CHARACTER)
             .with_single_card(0, 'set_zone_c', '04-092')
             .build())
    result = run_effect(state, '04-092', 0, card_instance=state.players[0].set_zone_c)
    assert result.engine.turn_state.attack_bonus[1] == -40

    state = (GameStateBuilder()
             .with_battle_card(0, WIND_CHARACTER)
             .with_single_card(0, 'set_zone_c', '04-092')
             .build())
    result = run_effect(state, '04-092', 0, card_instance=state.players[0].set_zone_c)
    assert result.engine.turn_state.attack_bonus[1] == 0


def test_04_093_two_power_charger_attributes_heal():
    state = (GameStateBuilder()
             .with_battle_card(0, '04-093')
             .with_power_charger(0, [DARKNESS_CHARACTER, FLAME_CHARACTER])
             .with_hp(0, 60)
             .build())
    run_effect(state, '04-093', 0)
    assert state.players[0].hp == 90

    state = (GameStateBuilder()
             .with_battle_card(0, '04-093')
             .with_power_charger(0, [DARKNESS_CHARACTER, '01-009'])
             .with_hp(0, 60)
             .build())
    result = run_effect(state, '04-093', 0)
    assert state.players[0].hp == 60
    assert any('fewer than 2 attributes. No effect.' in text for text in result.message_texts())


class TestEffect04094:
    """Use one SHADE character's effect from the Power Charger."""

    def test_dispatches_selected_shade_character(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '04-094')
                 .with_power_charger(0, ['04-073'])
                 .build())
        result = run_effect(state, '04-094', 0,
                            scripted_answers=[ScriptedAnswer.card_indices([0])])
        assert any('Activating effect of' in text for text in result.message_texts())

    def test_no_shade_characters_fizzles(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '04-094')
                 .with_power_charger(0, [SHADE_CHARACTER])
                 .build())
        result = run_effect(state, '04-094', 0)
        assert any('No SHADE characters with effects in Power Charger. No effect.' in text
                   for text in result.message_texts())

    def test_timeout_fizzles(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '04-094')
                 .with_power_charger(0, ['04-073'])
                 .build())
        result = run_effect(state, '04-094', 0,
                            scripted_answers=[ScriptedAnswer.timeout('effect_card_select')])
        assert any('No effect.' in text for text in result.message_texts())


def test_04_095_four_power_charger_attributes_bonus():
    state = (GameStateBuilder()
             .with_battle_card(0, '04-095')
             .with_power_charger(0, [DARKNESS_CHARACTER, FLAME_CHARACTER,
                                     ELECTRICITY_CHARACTER, WIND_CHARACTER])
             .build())
    assert run_effect(state, '04-095', 0).engine.turn_state.attack_bonus[0] == 50

    state = (GameStateBuilder()
             .with_battle_card(0, '04-095')
             .with_power_charger(0, [DARKNESS_CHARACTER])
             .build())
    result = run_effect(state, '04-095', 0)
    assert result.engine.turn_state.attack_bonus[0] == 0
    assert any('Need 4+. No bonus.' in text for text in result.message_texts())

    state = GameStateBuilder().with_battle_card(0, '04-095').build()
    result = run_effect(state, '04-095', 0)
    assert any('(none)' in text for text in result.message_texts())


@pytest.mark.parametrize('effect_id, reduction', [('04-096', 50), ('04-098', 100)])
def test_neko_reset_damage_reductions(effect_id, reduction):
    state = (GameStateBuilder()
             .with_battle_card(0, NEKO_RESET_CHARACTER)
             .with_single_card(0, 'set_zone_c', effect_id)
             .build())
    result = run_effect(state, effect_id, 0, card_instance=state.players[0].set_zone_c)
    assert result.engine.turn_state.damage_reduction[0] == reduction

    state = (GameStateBuilder()
             .with_battle_card(0, WIND_CHARACTER)
             .with_single_card(0, 'set_zone_c', effect_id)
             .build())
    result = run_effect(state, effect_id, 0, card_instance=state.players[0].set_zone_c)
    assert result.engine.turn_state.damage_reduction[0] == 0


def test_04_099_neko_reset_sets_opponent_attack_override():
    state = (GameStateBuilder()
             .with_battle_card(0, NEKO_RESET_CHARACTER)
             .with_single_card(0, 'set_zone_c', '04-099')
             .build())
    result = run_effect(state, '04-099', 0, card_instance=state.players[0].set_zone_c)
    assert result.engine.turn_state.attack_override[1] == 100

    state = (GameStateBuilder()
             .with_battle_card(0, WIND_CHARACTER)
             .with_single_card(0, 'set_zone_c', '04-099')
             .build())
    result = run_effect(state, '04-099', 0, card_instance=state.players[0].set_zone_c)
    assert result.engine.turn_state.attack_override.get(1) is None


def test_04_100_neko_reset_reflects_reduction():
    state = (GameStateBuilder()
             .with_battle_card(0, NEKO_RESET_CHARACTER)
             .with_single_card(0, 'set_zone_c', '04-100')
             .build())
    result = run_effect(state, '04-100', 0, card_instance=state.players[0].set_zone_c)
    assert result.engine.turn_state.reflect_reduction[0] is True

    state = (GameStateBuilder()
             .with_battle_card(0, WIND_CHARACTER)
             .with_single_card(0, 'set_zone_c', '04-100')
             .build())
    result = run_effect(state, '04-100', 0, card_instance=state.players[0].set_zone_c)
    assert result.engine.turn_state.reflect_reduction[0] is False


def test_04_101_bonus_when_opponent_attack_is_zero():
    state = (GameStateBuilder()
             .with_battle_card(0, '04-101')
             .with_battle_card(1, DARKNESS_CHARACTER)   # gated to 0 without power
             .build())
    assert run_effect(state, '04-101', 0).engine.turn_state.attack_bonus[0] == 20

    state = (GameStateBuilder()
             .with_battle_card(0, '04-101')
             .with_battle_card(1, STP_ONE_CARD)
             .build())
    assert run_effect(state, '04-101', 0).engine.turn_state.attack_bonus[0] == 0

    state = GameStateBuilder().with_battle_card(0, '04-101').build()
    assert run_effect(state, '04-101', 0).engine.turn_state.attack_bonus[0] == 20, \
        'empty battle zone counts as attack 0'


@pytest.mark.parametrize('effect_id, per_card_bonus', [('04-102', 10), ('04-104', 20)])
def test_study_me_power_charger_count_bonuses(effect_id, per_card_bonus):
    state = (GameStateBuilder()
             .with_battle_card(0, effect_id)
             .with_power_charger(0, [STUDY_ME_CHARACTER, STUDY_ME_CHARACTER, TAIDADA_CHARACTER])
             .build())
    assert run_effect(state, effect_id, 0).engine.turn_state.attack_bonus[0] == per_card_bonus * 2

    state = (GameStateBuilder()
             .with_battle_card(0, effect_id)
             .with_power_charger(0, [TAIDADA_CHARACTER])
             .build())
    result = run_effect(state, effect_id, 0)
    assert result.engine.turn_state.attack_bonus[0] == 0
    assert any('No STUDY ME cards in Power Charger. No effect.' in text
               for text in result.message_texts())


def test_04_103_exclusive_wind_abyss_bonus():
    state = (GameStateBuilder()
             .with_battle_card(0, '04-103')
             .with_abyss(0, [WIND_CHARACTER, WIND_CHARACTER])
             .build())
    assert run_effect(state, '04-103', 0).engine.turn_state.attack_bonus[0] == 40

    state = (GameStateBuilder()
             .with_battle_card(0, '04-103')
             .with_abyss(0, [WIND_CHARACTER, DARKNESS_CHARACTER])
             .build())
    result = run_effect(state, '04-103', 0)
    assert result.engine.turn_state.attack_bonus[0] == 0
    assert any('Not all cards in Abyss are wind attribute. No effect.' in text
               for text in result.message_texts())

    state = GameStateBuilder().with_battle_card(0, '04-103').build()
    result = run_effect(state, '04-103', 0)
    assert any('Abyss is empty. No effect.' in text for text in result.message_texts())


def test_04_037_exclusive_darkness_power_charger_bonus():
    state = (GameStateBuilder()
             .with_battle_card(0, '04-037')
             .with_power_charger(0, [DARKNESS_CHARACTER, DARKNESS_CHARACTER])
             .build())
    assert run_effect(state, '04-037', 0).engine.turn_state.attack_bonus[0] == 20

    state = (GameStateBuilder()
             .with_battle_card(0, '04-037')
             .with_power_charger(0, [DARKNESS_CHARACTER, WIND_CHARACTER])
             .build())
    assert run_effect(state, '04-037', 0).engine.turn_state.attack_bonus[0] == 0


# --- branch gap fills -------------------------------------------------------

def test_04_002_selection_timeout_after_count_fizzles():
    state = (GameStateBuilder()
             .with_battle_card(0, '04-002')
             .with_power_charger(0, ['04-073'])
             .build())
    result = run_effect(state, '04-002', 0, scripted_answers=[
        ScriptedAnswer.number(1),
        ScriptedAnswer.timeout('effect_card_select'),
    ])
    assert any('No effect.' in text for text in result.message_texts())


@pytest.mark.parametrize('effect_id', ['04-007', '04-010', '04-091'])
def test_taidada_reveal_zero_count_fizzles(effect_id):
    state = (GameStateBuilder()
             .with_battle_card(0, effect_id)
             .with_hand(0, [TAIDADA_CHARACTER])
             .build())
    result = run_effect(state, effect_id, 0, scripted_answers=[ScriptedAnswer.number(0)])
    assert result.engine.turn_state.attack_bonus[0] == 0
    assert any('No effect.' in text for text in result.message_texts())


def test_04_035_no_taidada_and_immediate_timeout_fizzle():
    state = (GameStateBuilder()
             .with_battle_card(0, '04-035')
             .with_hand(0, [DARKNESS_CHARACTER])
             .build())
    result = run_effect(state, '04-035', 0)
    assert any('No TAIDADA characters in hand. No effect.' in text
               for text in result.message_texts())

    state = (GameStateBuilder()
             .with_battle_card(0, '04-035')
             .with_hand(0, [TAIDADA_CHARACTER])
             .build())
    result = run_effect(state, '04-035', 0, scripted_answers=[
        ScriptedAnswer.number(1),
        ScriptedAnswer.timeout('effect_card_select'),
    ])
    assert result.engine.turn_state.attack_bonus[0] == 0


@pytest.mark.parametrize('effect_id, attribute_name', ATTRIBUTE_DISCARD_DRAW_EFFECTS)
def test_attribute_discard_draw_timeouts(effect_id, attribute_name):
    state = (GameStateBuilder()
             .with_battle_card(0, effect_id)
             .with_hand(0, [ATTRIBUTE_CARD[attribute_name]])
             .build())
    result = run_effect(state, effect_id, 0,
                        scripted_answers=[ScriptedAnswer.timeout('effect_card_select')])
    assert len(state.players[0].hand) == 1
    assert any('No card selected. No effect.' in text for text in result.message_texts())


def test_04_061_immediate_timeout_moves_nothing():
    state = (GameStateBuilder()
             .with_battle_card(0, '04-061')
             .with_hand(0, [DARKNESS_CHARACTER])
             .build())
    result = run_effect(state, '04-061', 0, scripted_answers=[
        ScriptedAnswer.number(1),
        ScriptedAnswer.timeout('effect_card_select'),
    ])
    assert len(state.players[0].hand) == 1 and state.players[0].deck == []
    assert any('No cards selected. No effect.' in text for text in result.message_texts())


@pytest.mark.parametrize('effect_id, attribute_name', ATTRIBUTE_MASS_DISCARD_EFFECTS)
def test_attribute_mass_discard_zero_and_timeout_fizzle(effect_id, attribute_name):
    matching = ATTRIBUTE_CARD[attribute_name]
    state = (GameStateBuilder()
             .with_battle_card(0, effect_id)
             .with_hand(0, [matching])
             .build())
    result = run_effect(state, effect_id, 0, scripted_answers=[ScriptedAnswer.number(0)])
    assert any('No effect.' in text for text in result.message_texts())

    state = (GameStateBuilder()
             .with_battle_card(0, effect_id)
             .with_hand(0, [matching])
             .build())
    result = run_effect(state, effect_id, 0, scripted_answers=[
        ScriptedAnswer.number(1),
        ScriptedAnswer.timeout('effect_card_select'),
    ])
    assert len(state.players[0].hand) == 1
    assert any('No cards selected. No effect.' in text for text in result.message_texts())


@pytest.mark.parametrize('effect_id', ['04-084', '04-101'])
def test_zero_attack_checks_honor_day_night_reversal_and_force_day(effect_id):
    from tests.support.effect_harness import EffectHarness

    # Reversed day/night: opponent 01-009 at night uses attack_day (10) -> no bonus.
    state = (GameStateBuilder()
             .with_battle_card(0, effect_id)
             .with_battle_card(1, STP_ONE_CARD)
             .with_chronos(4)
             .build())
    harness = EffectHarness(state)
    harness.engine.turn_state.day_night_reversed[1] = True
    result = harness.run_effect(effect_id, 0)
    assert result.engine.turn_state.attack_bonus[0] == 0

    # Force-day via the opponent's affordable 02-007 area enchant (cost 3;
    # four STP-1 cards give the opponent power 4).
    state = (GameStateBuilder()
             .with_battle_card(0, effect_id)
             .with_battle_card(1, STP_ONE_CARD)
             .with_single_card(1, 'set_zone_c', '02-007')
             .with_power_charger(1, [STP_ONE_CARD, STP_ONE_CARD, STP_ONE_CARD, STP_ONE_CARD])
             .with_chronos(4)
             .build())
    result = run_effect(state, effect_id, 0)
    assert result.engine.turn_state.attack_bonus[0] == 0, \
        'attack_day 10 is nonzero, so no bonus under forced day attack'

    # Reversed at DAY uses attack_night; normal at DAY uses attack_day.
    state = (GameStateBuilder()
             .with_battle_card(0, effect_id)
             .with_battle_card(1, STP_ONE_CARD)
             .with_chronos(13)
             .build())
    harness = EffectHarness(state)
    harness.engine.turn_state.day_night_reversed[1] = True
    assert harness.run_effect(effect_id, 0).engine.turn_state.attack_bonus[0] == 0

    state = (GameStateBuilder()
             .with_battle_card(0, effect_id)
             .with_battle_card(1, STP_ONE_CARD)
             .with_chronos(13)
             .build())
    assert run_effect(state, effect_id, 0).engine.turn_state.attack_bonus[0] == 0


def test_04_005_bonus_per_wind_abyss_card():
    state = (GameStateBuilder()
             .with_battle_card(0, '04-005')
             .with_abyss(0, [WIND_CHARACTER, WIND_CHARACTER, DARKNESS_CHARACTER])
             .build())
    result = run_effect(state, '04-005', 0)
    assert result.engine.turn_state.attack_bonus[0] == 40
    assert any('Attack +40!' in text for text in result.message_texts())

    state = GameStateBuilder().with_battle_card(0, '04-005').build()
    result = run_effect(state, '04-005', 0)
    assert result.engine.turn_state.attack_bonus[0] == 0
    assert any('No wind cards in Abyss. No effect.' in text for text in result.message_texts())
