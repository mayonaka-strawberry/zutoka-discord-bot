"""Characterization tests for pack 03 card effects (see test_pack_01 header)."""

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

ATTRIBUTE_CARD = {
    'DARKNESS': DARKNESS_CHARACTER,
    'FLAME': FLAME_CHARACTER,
    'ELECTRICITY': ELECTRICITY_CHARACTER,
    'WIND': WIND_CHARACTER,
}

MIDNIGHT_CHRONOS = 4
NOON_CHRONOS = 13


@pytest.mark.parametrize('effect_id, bonus', [
    ('03-001', 100),
    ('03-009', 50),
    ('03-025', 70),
])
def test_midnight_attack_bonuses(effect_id, bonus):
    state = GameStateBuilder().with_battle_card(0, effect_id).with_chronos(MIDNIGHT_CHRONOS).build()
    assert run_effect(state, effect_id, 0).engine.turn_state.attack_bonus[0] == bonus

    state = GameStateBuilder().with_battle_card(0, effect_id).with_chronos(5).build()
    assert run_effect(state, effect_id, 0).engine.turn_state.attack_bonus[0] == 0


@pytest.mark.parametrize('effect_id, target_chronos', [
    ('03-005', MIDNIGHT_CHRONOS),
    ('03-007', NOON_CHRONOS),
])
def test_clock_set_when_behind_on_hp(effect_id, target_chronos):
    state = (GameStateBuilder()
             .with_battle_card(0, effect_id)
             .with_hp(0, 40)
             .with_chronos(7)
             .build())
    run_effect(state, effect_id, 0)
    assert state.chronos == target_chronos

    state = (GameStateBuilder()
             .with_battle_card(0, effect_id)
             .with_hp(0, 100)
             .with_chronos(7)
             .build())
    run_effect(state, effect_id, 0)
    assert state.chronos == 7, 'equal HP: clock unchanged'


@pytest.mark.parametrize('effect_id, bonus', [
    ('03-006', 100), ('03-042', 50), ('03-087', 40), ('03-106', 100),
])
def test_bonus_against_full_hp_opponent(effect_id, bonus):
    state = GameStateBuilder().with_battle_card(0, effect_id).build()
    assert run_effect(state, effect_id, 0).engine.turn_state.attack_bonus[0] == bonus

    state = GameStateBuilder().with_battle_card(0, effect_id).with_hp(1, 99).build()
    assert run_effect(state, effect_id, 0).engine.turn_state.attack_bonus[0] == 0


GUESS_GAME_EFFECTS = [('03-047', 50), ('03-059', 100), ('03-094', 40), ('03-105', 100)]


def _guess_game_builder(effect_id: str) -> GameStateBuilder:
    return (GameStateBuilder()
            .with_battle_card(0, effect_id)
            .with_hand(1, [DARKNESS_CHARACTER, FLAME_CHARACTER]))


@pytest.mark.parametrize('effect_id, bonus', GUESS_GAME_EFFECTS)
def test_guess_game_match_grants_bonus(effect_id, bonus):
    state = _guess_game_builder(effect_id).build()
    result = run_effect(state, effect_id, 0, scripted_answers=[
        ScriptedAnswer.text('01-002'),
        ScriptedAnswer.number(2),   # 1-based blind pick: second card
    ])
    assert result.engine.turn_state.attack_bonus[0] == bonus
    assert any(f'Match! Attack +{bonus}!' in text for text in result.message_texts())


@pytest.mark.parametrize('effect_id, bonus', GUESS_GAME_EFFECTS)
def test_guess_game_wrong_guess_grants_nothing(effect_id, bonus):
    state = _guess_game_builder(effect_id).build()
    result = run_effect(state, effect_id, 0, scripted_answers=[
        ScriptedAnswer.text('01-002'),
        ScriptedAnswer.number(1),   # reveals 01-001 instead
    ])
    assert result.engine.turn_state.attack_bonus[0] == 0
    assert any('No match. No bonus.' in text for text in result.message_texts())


@pytest.mark.parametrize('effect_id, bonus', GUESS_GAME_EFFECTS)
def test_guess_game_empty_hand_and_timeouts_fizzle(effect_id, bonus):
    state = GameStateBuilder().with_battle_card(0, effect_id).build()
    result = run_effect(state, effect_id, 0)
    assert any('No effect.' in text for text in result.message_texts())

    state = _guess_game_builder(effect_id).build()
    result = run_effect(state, effect_id, 0, scripted_answers=[
        ScriptedAnswer.timeout('effect_text_input'),
    ])
    assert result.engine.turn_state.attack_bonus[0] == 0

    state = _guess_game_builder(effect_id).build()
    result = run_effect(state, effect_id, 0, scripted_answers=[
        ScriptedAnswer.text('01-001'),
        ScriptedAnswer.timeout('effect_number_select'),
    ])
    assert result.engine.turn_state.attack_bonus[0] == 0


class TestEffect03097:
    """Reveal the opponent's top deck card; cost >= 6 sends it (and this card)
    to the respective Power Chargers."""

    def test_expensive_card_moves_to_power_charger(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, DARKNESS_CHARACTER)
                 .with_single_card(0, 'set_zone_c', '03-097')
                 .with_deck(1, [FLAME_CHARACTER])   # cost 7
                 .build())
        area_enchant = state.players[0].set_zone_c
        result = run_effect(state, '03-097', 0, card_instance=area_enchant)
        assert card_identities(state.players[1].power_charger) == [FLAME_CHARACTER]
        assert state.players[1].deck == []
        assert state.players[0].set_zone_c is None, 'the enchant sends itself away too'
        assert area_enchant in state.players[0].power_charger
        assert any('>= 6' in text for text in result.message_texts())

    def test_cheap_card_stays_on_deck(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, DARKNESS_CHARACTER)
                 .with_single_card(0, 'set_zone_c', '03-097')
                 .with_deck(1, [WIND_CHARACTER])   # cost 3
                 .build())
        result = run_effect(state, '03-097', 0, card_instance=state.players[0].set_zone_c)
        assert card_identities(state.players[1].deck) == [WIND_CHARACTER]
        assert state.players[0].set_zone_c is not None
        assert any('< 6. Card stays on deck.' in text for text in result.message_texts())

    def test_empty_opponent_deck_fizzles(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, DARKNESS_CHARACTER)
                 .with_single_card(0, 'set_zone_c', '03-097')
                 .build())
        result = run_effect(state, '03-097', 0, card_instance=state.players[0].set_zone_c)
        assert any("Opponent's deck is empty. No effect." in text for text in result.message_texts())


def test_03_098_bonus_per_electric_abyss_card():
    state = (GameStateBuilder()
             .with_battle_card(0, '03-098')
             .with_abyss(0, [ELECTRICITY_CHARACTER, ELECTRICITY_CHARACTER, WIND_CHARACTER])
             .build())
    assert run_effect(state, '03-098', 0).engine.turn_state.attack_bonus[0] == 20


def test_03_101_two_abyss_cards_bonus():
    state = (GameStateBuilder()
             .with_battle_card(0, '03-101')
             .with_abyss(0, [WIND_CHARACTER, FLAME_CHARACTER])
             .build())
    assert run_effect(state, '03-101', 0).engine.turn_state.attack_bonus[0] == 40

    state = (GameStateBuilder()
             .with_battle_card(0, '03-101')
             .with_abyss(0, [WIND_CHARACTER])
             .build())
    assert run_effect(state, '03-101', 0).engine.turn_state.attack_bonus[0] == 0


class TestEffect03103:
    """Reveal the opponent's top deck card: no SEND TO POWER grants +30,
    otherwise this area enchant sends itself to the Power Charger."""

    def test_starless_reveal_grants_bonus(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, DARKNESS_CHARACTER)
                 .with_single_card(0, 'set_zone_c', '03-103')
                 .with_deck(1, [FLAME_CHARACTER])   # STP 0
                 .build())
        result = run_effect(state, '03-103', 0, card_instance=state.players[0].set_zone_c)
        assert result.engine.turn_state.attack_bonus[0] == 30
        assert state.players[0].set_zone_c is not None
        assert card_identities(state.players[1].deck) == [FLAME_CHARACTER], 'card stays on deck'

    def test_starred_reveal_self_destructs(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, DARKNESS_CHARACTER)
                 .with_single_card(0, 'set_zone_c', '03-103')
                 .with_deck(1, ['01-009'])   # STP 1
                 .build())
        area_enchant = state.players[0].set_zone_c
        result = run_effect(state, '03-103', 0, card_instance=area_enchant)
        assert result.engine.turn_state.attack_bonus[0] == 0
        assert state.players[0].set_zone_c is None
        assert area_enchant in state.players[0].power_charger

    def test_empty_opponent_deck_fizzles(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, DARKNESS_CHARACTER)
                 .with_single_card(0, 'set_zone_c', '03-103')
                 .build())
        result = run_effect(state, '03-103', 0, card_instance=state.players[0].set_zone_c)
        assert any("Opponent's deck is empty. No effect." in text for text in result.message_texts())


def test_03_104_bonus_per_wind_abyss_card():
    state = (GameStateBuilder()
             .with_battle_card(0, '03-104')
             .with_abyss(0, [WIND_CHARACTER, WIND_CHARACTER, DARKNESS_CHARACTER])
             .build())
    assert run_effect(state, '03-104', 0).engine.turn_state.attack_bonus[0] == 20


@pytest.mark.parametrize('effect_id, attribute_name, bonus', [
    ('03-063', 'ELECTRICITY', 60),
    ('03-084', 'FLAME', 70),
    ('03-088', 'DARKNESS', 30),
])
def test_more_exclusive_abyss_bonuses(effect_id, attribute_name, bonus):
    matching = ATTRIBUTE_CARD[attribute_name]
    state = (GameStateBuilder()
             .with_battle_card(0, effect_id)
             .with_abyss(0, [matching, matching])
             .build())
    assert run_effect(state, effect_id, 0).engine.turn_state.attack_bonus[0] == bonus

    state = (GameStateBuilder()
             .with_battle_card(0, effect_id)
             .with_abyss(0, [matching, DARKNESS_CHARACTER if attribute_name != 'DARKNESS' else FLAME_CHARACTER])
             .build())
    assert run_effect(state, effect_id, 0).engine.turn_state.attack_bonus[0] == 0


def test_03_064_mutual_hp_attack_bonus():
    state = (GameStateBuilder()
             .with_battle_card(0, '03-064')
             .with_hp(0, 70)
             .with_hp(1, 45)
             .build())
    result = run_effect(state, '03-064', 0)
    assert result.engine.turn_state.attack_bonus[0] == 70
    assert result.engine.turn_state.attack_bonus[1] == 45


def test_03_082_exclusive_darkness_power_charger_bonus():
    state = (GameStateBuilder()
             .with_battle_card(0, '03-082')
             .with_power_charger(0, [DARKNESS_CHARACTER, '01-009'])
             .build())
    assert run_effect(state, '03-082', 0).engine.turn_state.attack_bonus[0] == 40

    state = (GameStateBuilder()
             .with_battle_card(0, '03-082')
             .with_power_charger(0, [DARKNESS_CHARACTER, WIND_CHARACTER])
             .build())
    assert run_effect(state, '03-082', 0).engine.turn_state.attack_bonus[0] == 0


def test_03_083_four_darkness_abyss_cards_bonus():
    state = (GameStateBuilder()
             .with_battle_card(0, '03-083')
             .with_abyss(0, [DARKNESS_CHARACTER] * 4)
             .build())
    assert run_effect(state, '03-083', 0).engine.turn_state.attack_bonus[0] == 60

    state = (GameStateBuilder()
             .with_battle_card(0, '03-083')
             .with_abyss(0, [DARKNESS_CHARACTER] * 3)
             .build())
    assert run_effect(state, '03-083', 0).engine.turn_state.attack_bonus[0] == 0


def test_03_085_persistent_area_enchant_handler_is_no_op():
    state = GameStateBuilder().with_battle_card(0, '03-085').with_chronos(13).build()
    result = run_effect(state, '03-085', 0)
    assert state.chronos == 13
    assert result.engine.turn_state.attack_bonus == {0: 0, 1: 0}


def test_03_086_bonus_per_darkness_abyss_card():
    state = (GameStateBuilder()
             .with_battle_card(0, '03-086')
             .with_abyss(0, [DARKNESS_CHARACTER, '01-009', FLAME_CHARACTER])
             .build())
    assert run_effect(state, '03-086', 0).engine.turn_state.attack_bonus[0] == 20

    state = GameStateBuilder().with_battle_card(0, '03-086').build()
    assert run_effect(state, '03-086', 0).engine.turn_state.attack_bonus[0] == 0


PACK_THREE_OWN_ATTRIBUTE_BONUSES = [
    ('03-054', 'DARKNESS', 60),
    ('03-081', 'FLAME', 20),
    ('03-089', 'DARKNESS', 50),
    ('03-090', 'FLAME', 30),
    ('03-093', 'WIND', 30),
    ('03-096', 'ELECTRICITY', 50),
    ('03-099', 'WIND', 30),
    ('03-100', 'ELECTRICITY', 40),
]


@pytest.mark.parametrize('effect_id, attribute_name, bonus', PACK_THREE_OWN_ATTRIBUTE_BONUSES)
def test_pack_three_own_attribute_bonuses(effect_id, attribute_name, bonus):
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


def test_03_091_own_cost_threshold_bonus():
    state = (GameStateBuilder()
             .with_battle_card(0, DARKNESS_CHARACTER)   # cost 5
             .with_single_card(0, 'set_zone_c', '03-091')
             .build())
    result = run_effect(state, '03-091', 0, card_instance=state.players[0].set_zone_c)
    assert result.engine.turn_state.attack_bonus[0] == 20

    state = (GameStateBuilder()
             .with_battle_card(0, '01-010')             # cost 1
             .with_single_card(0, 'set_zone_c', '03-091')
             .build())
    result = run_effect(state, '03-091', 0, card_instance=state.players[0].set_zone_c)
    assert result.engine.turn_state.attack_bonus[0] == 0


def test_03_092_bonus_per_flame_abyss_card():
    state = (GameStateBuilder()
             .with_battle_card(0, '03-092')
             .with_abyss(0, [FLAME_CHARACTER, FLAME_CHARACTER, WIND_CHARACTER])
             .build())
    assert run_effect(state, '03-092', 0).engine.turn_state.attack_bonus[0] == 20


class TestEffect03055:
    """Bottom-deck the opponent's area enchant and block their future ones."""

    def test_bounces_and_blocks(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '03-055')
                 .with_single_card(1, 'set_zone_c', '02-053')
                 .with_deck(1, [WIND_CHARACTER])
                 .build())
        run_effect(state, '03-055', 0)
        opponent = state.players[1]
        assert opponent.set_zone_c is None
        assert card_identities(opponent.deck) == [WIND_CHARACTER, '02-053']
        assert opponent.area_enchant_blocked is True

    def test_blocks_even_without_current_area_enchant(self):
        state = GameStateBuilder().with_battle_card(0, '03-055').build()
        run_effect(state, '03-055', 0)
        assert state.players[1].area_enchant_blocked is True


def test_03_056_small_power_charger_bonus():
    state = (GameStateBuilder()
             .with_battle_card(0, '03-056')
             .with_power_charger(0, [WIND_CHARACTER, WIND_CHARACTER, WIND_CHARACTER])
             .build())
    assert run_effect(state, '03-056', 0).engine.turn_state.attack_bonus[0] == 50

    state = (GameStateBuilder()
             .with_battle_card(0, '03-056')
             .with_power_charger(0, [WIND_CHARACTER] * 4)
             .build())
    assert run_effect(state, '03-056', 0).engine.turn_state.attack_bonus[0] == 0


def test_03_057_four_flame_abyss_cards_bonus():
    state = (GameStateBuilder()
             .with_battle_card(0, '03-057')
             .with_abyss(0, [FLAME_CHARACTER] * 4)
             .build())
    assert run_effect(state, '03-057', 0).engine.turn_state.attack_bonus[0] == 70

    state = (GameStateBuilder()
             .with_battle_card(0, '03-057')
             .with_abyss(0, [FLAME_CHARACTER] * 3)
             .build())
    assert run_effect(state, '03-057', 0).engine.turn_state.attack_bonus[0] == 0


@pytest.mark.parametrize('effect_id', ['03-058', '03-061'])
def test_persistent_area_enchant_handlers_are_no_ops(effect_id):
    """The per-turn logic of these area enchants lives in
    process_end_of_turn_effects / should_override_all_clocks /
    check_area_enchant_removal (covered by the engine-core tests); the
    dispatched handler itself changes nothing."""
    state = GameStateBuilder().with_battle_card(0, effect_id).build()
    result = run_effect(state, effect_id, 0)
    assert result.engine.turn_state.attack_bonus == {0: 0, 1: 0}
    assert state.players[0].hp == 100 and state.players[1].hp == 100


def test_03_060_exclusive_wind_abyss_bonus():
    state = (GameStateBuilder()
             .with_battle_card(0, '03-060')
             .with_abyss(0, [WIND_CHARACTER, WIND_CHARACTER])
             .build())
    assert run_effect(state, '03-060', 0).engine.turn_state.attack_bonus[0] == 70

    state = (GameStateBuilder()
             .with_battle_card(0, '03-060')
             .with_abyss(0, [WIND_CHARACTER, FLAME_CHARACTER])
             .build())
    assert run_effect(state, '03-060', 0).engine.turn_state.attack_bonus[0] == 0


def test_03_062_three_distinct_abyss_attributes_bonus():
    state = (GameStateBuilder()
             .with_battle_card(0, '03-062')
             .with_abyss(0, [DARKNESS_CHARACTER, FLAME_CHARACTER, ELECTRICITY_CHARACTER])
             .build())
    assert run_effect(state, '03-062', 0).engine.turn_state.attack_bonus[0] == 50

    state = (GameStateBuilder()
             .with_battle_card(0, '03-062')
             .with_abyss(0, [DARKNESS_CHARACTER, DARKNESS_CHARACTER])
             .build())
    assert run_effect(state, '03-062', 0).engine.turn_state.attack_bonus[0] == 0


def test_03_051_three_distinct_abyss_attributes_bonus():
    state = (GameStateBuilder()
             .with_battle_card(0, '03-051')
             .with_abyss(0, [DARKNESS_CHARACTER, FLAME_CHARACTER, WIND_CHARACTER])
             .build())
    assert run_effect(state, '03-051', 0).engine.turn_state.attack_bonus[0] == 40

    state = (GameStateBuilder()
             .with_battle_card(0, '03-051')
             .with_abyss(0, [DARKNESS_CHARACTER, FLAME_CHARACTER])
             .build())
    assert run_effect(state, '03-051', 0).engine.turn_state.attack_bonus[0] == 0


def test_03_053_night_bonus():
    state = GameStateBuilder().with_battle_card(0, '03-053').with_chronos(4).build()
    assert run_effect(state, '03-053', 0).engine.turn_state.attack_bonus[0] == 40
    state = GameStateBuilder().with_battle_card(0, '03-053').with_chronos(13).build()
    assert run_effect(state, '03-053', 0).engine.turn_state.attack_bonus[0] == 0


@pytest.mark.parametrize('effect_id, bonus', [('03-008', 100), ('03-022', 40)])
def test_four_attribute_abyss_bonuses(effect_id, bonus):
    state = (GameStateBuilder()
             .with_battle_card(0, effect_id)
             .with_abyss(0, [DARKNESS_CHARACTER, FLAME_CHARACTER, ELECTRICITY_CHARACTER, WIND_CHARACTER])
             .build())
    assert run_effect(state, effect_id, 0).engine.turn_state.attack_bonus[0] == bonus

    state = (GameStateBuilder()
             .with_battle_card(0, effect_id)
             .with_abyss(0, [DARKNESS_CHARACTER, FLAME_CHARACTER, ELECTRICITY_CHARACTER])
             .build())
    assert run_effect(state, effect_id, 0).engine.turn_state.attack_bonus[0] == 0


@pytest.mark.parametrize('effect_id, zone_owner_index, attribute_name, bonus', [
    ('03-011', 1, 'WIND', 40),
    ('03-017', 1, 'DARKNESS', 40),
    ('03-023', 1, 'FLAME', 40),
    ('03-019', 0, 'ELECTRICITY', 30),
    ('03-036', 0, 'DARKNESS', 30),
    ('03-039', 1, 'ELECTRICITY', 50),
    ('03-040', 0, 'FLAME', 20),
    ('03-052', 0, 'WIND', 40),
    ('03-095', 0, 'ELECTRICITY', 50),
    ('03-102', 0, 'WIND', 40),
])
def test_exclusive_power_charger_bonuses(effect_id, zone_owner_index, attribute_name, bonus):
    matching = ATTRIBUTE_CARD[attribute_name]
    other = ATTRIBUTE_CARD['WIND' if attribute_name != 'WIND' else 'FLAME']

    state = (GameStateBuilder()
             .with_battle_card(0, effect_id)
             .with_power_charger(zone_owner_index, [matching, matching])
             .build())
    assert run_effect(state, effect_id, 0).engine.turn_state.attack_bonus[0] == bonus

    state = (GameStateBuilder()
             .with_battle_card(0, effect_id)
             .with_power_charger(zone_owner_index, [matching, other])
             .build())
    assert run_effect(state, effect_id, 0).engine.turn_state.attack_bonus[0] == 0

    state = GameStateBuilder().with_battle_card(0, effect_id).build()
    assert run_effect(state, effect_id, 0).engine.turn_state.attack_bonus[0] == 0


def test_03_026_extends_midnight():
    state = GameStateBuilder().with_battle_card(0, '03-026').build()
    assert run_effect(state, '03-026', 0).engine.turn_state.midnight_extended is True


def test_03_027_heals_opponent_now_and_damages_at_turn_end():
    state = (GameStateBuilder()
             .with_battle_card(0, '03-027')
             .with_hp(1, 40)
             .build())
    result = run_effect(state, '03-027', 0)
    assert state.players[1].hp == 90
    assert result.engine.turn_state.end_of_turn_damage[1] == 50

    state = GameStateBuilder().with_battle_card(0, '03-027').with_hp(1, 80).build()
    run_effect(state, '03-027', 0)
    assert state.players[1].hp == 100, 'heal caps at 100'


def test_03_028_exclusive_flame_power_charger_bonus():
    state = (GameStateBuilder()
             .with_battle_card(0, '03-028')
             .with_power_charger(0, [FLAME_CHARACTER, FLAME_CHARACTER])
             .build())
    assert run_effect(state, '03-028', 0).engine.turn_state.attack_bonus[0] == 80

    state = (GameStateBuilder()
             .with_battle_card(0, '03-028')
             .with_power_charger(0, [FLAME_CHARACTER, WIND_CHARACTER])
             .build())
    assert run_effect(state, '03-028', 0).engine.turn_state.attack_bonus[0] == 0


def test_03_029_day_bonus():
    state = GameStateBuilder().with_battle_card(0, '03-029').with_chronos(13).build()
    assert run_effect(state, '03-029', 0).engine.turn_state.attack_bonus[0] == 50
    state = GameStateBuilder().with_battle_card(0, '03-029').with_chronos(4).build()
    assert run_effect(state, '03-029', 0).engine.turn_state.attack_bonus[0] == 0


@pytest.mark.parametrize('effect_id, attribute_name, bonus', [
    ('03-030', 'ELECTRICITY', 70),
    ('03-032', 'WIND', 80),
])
def test_three_of_attribute_abyss_bonuses(effect_id, attribute_name, bonus):
    matching = ATTRIBUTE_CARD[attribute_name]
    state = (GameStateBuilder()
             .with_battle_card(0, effect_id)
             .with_abyss(0, [matching, matching, matching])
             .build())
    assert run_effect(state, effect_id, 0).engine.turn_state.attack_bonus[0] == bonus

    state = (GameStateBuilder()
             .with_battle_card(0, effect_id)
             .with_abyss(0, [matching, matching])
             .build())
    assert run_effect(state, effect_id, 0).engine.turn_state.attack_bonus[0] == 0


class TestEffect03031:
    """Send a chosen hand card to the Abyss (power ignored), then draw."""

    def test_happy_path(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '03-031')
                 .with_hand(0, ['01-009'])   # STP 1: would normally go to power charger
                 .with_deck(0, [WIND_CHARACTER])
                 .build())
        result = run_effect(state, '03-031', 0,
                            scripted_answers=[ScriptedAnswer.card_indices([0])])
        player = state.players[0]
        assert card_identities(player.abyss) == ['01-009'], 'goes to Abyss regardless of STP'
        assert card_identities(player.hand) == [WIND_CHARACTER]
        assert any('drew **1** card.' in text for text in result.message_texts())

    def test_empty_hand_fizzles(self):
        state = GameStateBuilder().with_battle_card(0, '03-031').build()
        result = run_effect(state, '03-031', 0)
        assert any('No cards in hand. Effect fizzles.' in text for text in result.message_texts())

    def test_timeout_changes_nothing(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '03-031')
                 .with_hand(0, [DARKNESS_CHARACTER])
                 .build())
        run_effect(state, '03-031', 0,
                   scripted_answers=[ScriptedAnswer.timeout('effect_card_select')])
        assert len(state.players[0].hand) == 1 and state.players[0].abyss == []

    def test_empty_deck_skips_the_draw(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '03-031')
                 .with_hand(0, [DARKNESS_CHARACTER])
                 .build())
        run_effect(state, '03-031', 0,
                   scripted_answers=[ScriptedAnswer.card_indices([0])])
        player = state.players[0]
        assert card_identities(player.abyss) == [DARKNESS_CHARACTER]
        assert player.hand == [] and player.deck == []


def test_03_033_daytime_clock_advance():
    state = GameStateBuilder().with_battle_card(0, '03-033').with_chronos(13).build()
    run_effect(state, '03-033', 0)
    assert state.chronos == 15

    state = GameStateBuilder().with_battle_card(0, '03-033').with_chronos(4).build()
    run_effect(state, '03-033', 0)
    assert state.chronos == 4


@pytest.mark.parametrize('effect_id, deck_position', [
    ('03-014', 'bottom'),
    ('03-021', 'top'),
])
def test_area_enchant_bounce_effects(effect_id, deck_position):
    state = (GameStateBuilder()
             .with_battle_card(0, effect_id)
             .with_single_card(1, 'set_zone_c', '02-053')
             .with_deck(1, [WIND_CHARACTER])
             .build())
    run_effect(state, effect_id, 0)
    opponent = state.players[1]
    assert opponent.set_zone_c is None
    if deck_position == 'bottom':
        assert card_identities(opponent.deck) == [WIND_CHARACTER, '02-053']
    else:
        assert card_identities(opponent.deck) == ['02-053', WIND_CHARACTER]

    state = GameStateBuilder().with_battle_card(0, effect_id).build()
    run_effect(state, effect_id, 0)
    assert state.players[1].deck == []


class TestEffect03045:
    """Reveal the opponent's hand (then shuffle it against position tracking)."""

    def test_reveals_and_shuffles_opponent_hand(self):
        original_hand = [DARKNESS_CHARACTER, FLAME_CHARACTER, ELECTRICITY_CHARACTER, WIND_CHARACTER]
        state = (GameStateBuilder()
                 .with_battle_card(0, '03-045')
                 .with_hand(1, original_hand)
                 .build())
        result = run_effect(state, '03-045', owner_index=0)

        assert result.prompts_seen == []
        # Same cards, order shuffled by the seeded session generator.
        assert sorted(card_identities(state.players[1].hand)) == sorted(original_hand)
        reveal_messages = [text for text in result.message_texts() if 'hand revealed' in text.lower()]
        assert reveal_messages, 'the reveal must be announced'

    def test_empty_opponent_hand_skips_reveal(self):
        state = GameStateBuilder().with_battle_card(0, '03-045').build()
        result = run_effect(state, '03-045', owner_index=0)
        assert any("Opponent's hand is empty." in text for text in result.message_texts())
