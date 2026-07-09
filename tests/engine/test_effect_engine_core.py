"""
Characterization tests for the EffectEngine core: area-enchant removal rules,
end-of-turn processing, the power-cost gate, effect collection and ordering,
and the shared damage/placement/attack helpers.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

import pytest  # noqa: E402

from zutomayo.enums.chronos import Chronos  # noqa: E402
from zutomayo.enums.song import Song  # noqa: E402
from zutomayo.enums.zone import Zone  # noqa: E402

from tests.support.game_state_builder import GameStateBuilder  # noqa: E402
from tests.support.effect_harness import EffectHarness, ScriptedAnswer, card_identities  # noqa: E402

DARKNESS_CHARACTER = '01-001'   # cost 5, STUDY_ME song
FLAME_CHARACTER = '01-002'      # cost 7
WIND_CHARACTER = '01-004'       # cost 3
LOW_COST_CHARACTER = '01-009'   # cost 0, STP 1
STP_TWO_CARD = '01-013'
TAIDADA_CHARACTER = '04-067'

NIGHT_CHRONOS = 4
DAY_CHRONOS = 13


def _powered_builder(enchant_identity: str, owner_index: int = 0) -> GameStateBuilder:
    """Owner holds the area enchant with 8 power (covers every tested cost)."""
    return (GameStateBuilder()
            .with_single_card(owner_index, 'set_zone_c', enchant_identity)
            .with_power_charger(owner_index, [STP_TWO_CARD] * 4))


def _run_removal(state, *, end_of_turn: bool, prepare=None) -> EffectHarness:
    harness = EffectHarness(state)
    if prepare is not None:
        prepare(harness)
    harness.engine.check_area_enchant_removal(state, harness.turn_manager, end_of_turn=end_of_turn)
    return harness


def _assert_removed(state, owner_index: int = 0) -> None:
    player = state.players[owner_index]
    assert player.set_zone_c is None, 'the enchant must have been removed'


def _assert_kept(state, owner_index: int = 0) -> None:
    assert state.players[owner_index].set_zone_c is not None, 'the enchant must stay in play'


class TestAreaEnchantRemoval:
    def test_unmet_power_cost_prevents_removal(self):
        # 04-091 removes at HP <= 50, but with no power the enchant (cost 1)
        # is not even considered.
        state = (GameStateBuilder()
                 .with_single_card(0, 'set_zone_c', '04-091')
                 .with_hp(0, 40)
                 .build())
        _run_removal(state, end_of_turn=False)
        _assert_kept(state)

    def test_02_005_removes_on_transition_at_end_of_turn_only(self):
        def transition(harness):
            harness.engine.turn_state.day_to_night_occurred = True

        state = _powered_builder('02-005').build()
        _run_removal(state, end_of_turn=True, prepare=transition)
        _assert_removed(state)

        state = _powered_builder('02-005').build()
        _run_removal(state, end_of_turn=False, prepare=transition)
        _assert_kept(state)

        state = _powered_builder('02-005').build()
        _run_removal(state, end_of_turn=True)
        _assert_kept(state)

    def test_02_007_removes_when_opponent_played_area_enchant(self):
        state = (_powered_builder('02-007')
                 .with_single_card(1, 'set_zone_c', '03-061', played_this_turn=True)
                 .build())
        _run_removal(state, end_of_turn=True)
        _assert_removed(state)

        state = (_powered_builder('02-007')
                 .with_single_card(1, 'set_zone_c', '03-061', played_this_turn=False)
                 .build())
        _run_removal(state, end_of_turn=True)
        _assert_kept(state)

    def test_02_058_removes_when_own_character_reached_power_charger(self):
        def flag(harness):
            harness.engine.turn_state.character_to_power_this_turn[0] = True

        state = _powered_builder('02-058').build()
        _run_removal(state, end_of_turn=True, prepare=flag)
        _assert_removed(state)

        state = _powered_builder('02-058').build()
        _run_removal(state, end_of_turn=False, prepare=flag)
        _assert_kept(state)

    @pytest.mark.parametrize('effect_id, threshold', [('02-064', 30), ('03-064', 40)])
    def test_opponent_hp_threshold_removals(self, effect_id, threshold):
        state = _powered_builder(effect_id).with_hp(1, threshold).build()
        _run_removal(state, end_of_turn=True)
        _assert_removed(state)

        state = _powered_builder(effect_id).with_hp(1, threshold + 1).build()
        _run_removal(state, end_of_turn=True)
        _assert_kept(state)

        state = _powered_builder(effect_id).with_hp(1, threshold).build()
        _run_removal(state, end_of_turn=False)
        _assert_kept(state, 0)

    @pytest.mark.parametrize('effect_id, keeping_chronos, removing_chronos', [
        ('02-086', NIGHT_CHRONOS, DAY_CHRONOS),   # removed when not night
        ('02-098', DAY_CHRONOS, NIGHT_CHRONOS),   # removed when not day
    ])
    def test_time_of_day_removals(self, effect_id, keeping_chronos, removing_chronos):
        state = _powered_builder(effect_id).with_chronos(removing_chronos).build()
        _run_removal(state, end_of_turn=False)
        _assert_removed(state)

        state = _powered_builder(effect_id).with_chronos(keeping_chronos).build()
        _run_removal(state, end_of_turn=False)
        _assert_kept(state)

    @pytest.mark.parametrize('effect_id, battle_owner', [
        ('02-092', 1),   # opponent character costs 4+
        ('02-104', 0),   # own character costs 4+
    ])
    def test_battle_character_cost_removals(self, effect_id, battle_owner):
        state = (_powered_builder(effect_id)
                 .with_battle_card(battle_owner, DARKNESS_CHARACTER)   # cost 5
                 .build())
        _run_removal(state, end_of_turn=False)
        _assert_removed(state)

        state = (_powered_builder(effect_id)
                 .with_battle_card(battle_owner, WIND_CHARACTER)       # cost 3
                 .build())
        _run_removal(state, end_of_turn=False)
        _assert_kept(state)

    @pytest.mark.parametrize('effect_id', ['03-055', '03-091'])
    def test_opponent_abyss_placement_removals(self, effect_id):
        def flag(harness):
            harness.engine.turn_state.opponent_card_to_abyss[0] = True

        state = _powered_builder(effect_id).build()
        _run_removal(state, end_of_turn=True, prepare=flag)
        _assert_removed(state)

        state = _powered_builder(effect_id).build()
        _run_removal(state, end_of_turn=True)
        _assert_kept(state)

    def test_03_055_removal_unblocks_the_opponent(self):
        def flag(harness):
            harness.engine.turn_state.opponent_card_to_abyss[0] = True

        state = _powered_builder('03-055').build()
        state.players[1].area_enchant_blocked = True
        _run_removal(state, end_of_turn=True, prepare=flag)
        _assert_removed(state)
        assert state.players[1].area_enchant_blocked is False

    def test_03_061_removes_when_opponent_fields_area_enchant(self):
        state = (_powered_builder('03-061')
                 .with_single_card(1, 'set_zone_c', '02-086')
                 .build())
        _run_removal(state, end_of_turn=True)
        _assert_removed(state)

        state = _powered_builder('03-061').build()
        _run_removal(state, end_of_turn=True)
        _assert_kept(state)

    @pytest.mark.parametrize('effect_id', ['03-086', '03-092', '03-098', '03-104'])
    def test_four_abyss_cards_removals(self, effect_id):
        state = (_powered_builder(effect_id)
                 .with_abyss(0, [WIND_CHARACTER] * 4)
                 .build())
        _run_removal(state, end_of_turn=True)
        _assert_removed(state)

        state = (_powered_builder(effect_id)
                 .with_abyss(0, [WIND_CHARACTER] * 3)
                 .build())
        _run_removal(state, end_of_turn=True)
        _assert_kept(state)

        state = (_powered_builder(effect_id)
                 .with_abyss(0, [WIND_CHARACTER] * 4)
                 .build())
        _run_removal(state, end_of_turn=False)
        _assert_kept(state)

    def test_04_030_removes_to_abyss_despite_send_to_power(self):
        def flag(harness):
            harness.engine.turn_state.abyss_received_card[1] = True

        state = _powered_builder('04-030').build()
        area_enchant = state.players[0].set_zone_c
        _run_removal(state, end_of_turn=False, prepare=flag)
        _assert_removed(state)
        assert area_enchant in state.players[0].abyss, \
            '04-030 has a SEND TO POWER star but its text sends it to the Abyss'

    def test_04_032_removes_when_opponent_fields_area_enchant(self):
        state = (_powered_builder('04-032')
                 .with_single_card(1, 'set_zone_c', '02-086')
                 .build())
        _run_removal(state, end_of_turn=False)
        _assert_removed(state)

    def test_04_033_removes_on_own_power_charger_placement(self):
        def flag(harness):
            harness.engine.turn_state.card_to_power_this_turn[0] = True

        state = _powered_builder('04-033').build()
        _run_removal(state, end_of_turn=False, prepare=flag)
        _assert_removed(state)

    def test_04_065_removes_after_swap_to_non_study_me(self):
        def swapped(harness):
            harness.engine.turn_state.swapped_from_songs[0].add(Song.SHADE)

        state = (_powered_builder('04-065')
                 .with_battle_card(0, TAIDADA_CHARACTER)
                 .build())
        _run_removal(state, end_of_turn=False, prepare=swapped)
        _assert_removed(state)

        # Swapped INTO a STUDY ME character: stays.
        state = (_powered_builder('04-065')
                 .with_battle_card(0, DARKNESS_CHARACTER)   # STUDY_ME song
                 .build())
        _run_removal(state, end_of_turn=False, prepare=swapped)
        _assert_kept(state)

        # No swap at all: stays.
        state = (_powered_builder('04-065')
                 .with_battle_card(0, TAIDADA_CHARACTER)
                 .build())
        _run_removal(state, end_of_turn=False)
        _assert_kept(state)

    def test_04_091_removes_at_low_hp(self):
        state = _powered_builder('04-091').with_hp(0, 50).build()
        area_enchant = state.players[0].set_zone_c
        _run_removal(state, end_of_turn=False)
        _assert_removed(state)
        assert area_enchant in state.players[0].power_charger, 'STP 1 routes to the Power Charger'

        state = _powered_builder('04-091').with_hp(0, 51).build()
        _run_removal(state, end_of_turn=False)
        _assert_kept(state)

    def test_04_094_removes_with_five_power_charger_cards(self):
        state = (GameStateBuilder()
                 .with_single_card(0, 'set_zone_c', '04-094')
                 .with_power_charger(0, [STP_TWO_CARD] * 5)
                 .build())
        _run_removal(state, end_of_turn=False)
        _assert_removed(state)

        state = _powered_builder('04-094').build()   # 4 cards
        _run_removal(state, end_of_turn=False)
        _assert_kept(state)

    def test_04_095_removes_on_battle_loss(self):
        def lost(harness):
            harness.engine.turn_state.battle_lost[0] = True

        state = _powered_builder('04-095').build()
        _run_removal(state, end_of_turn=False, prepare=lost)
        _assert_removed(state)

        state = _powered_builder('04-095').build()
        _run_removal(state, end_of_turn=False)
        _assert_kept(state)


class TestEndOfTurnEffects:
    def test_03_027_end_damage_applies(self):
        state = GameStateBuilder().build()
        harness = EffectHarness(state)
        harness.engine.turn_state.end_of_turn_damage[1] = 50
        harness.engine.process_end_of_turn_effects(state)
        assert state.players[1].hp == 50
        assert harness.engine.turn_state.damage_taken_this_turn[1] == 50

    def test_04_100_reflects_reduced_damage(self):
        state = GameStateBuilder().build()
        harness = EffectHarness(state)
        harness.engine.turn_state.reflect_reduction[0] = True
        harness.engine.turn_state.damage_reduced_this_turn[0] = 30
        harness.engine.process_end_of_turn_effects(state)
        assert state.players[1].hp == 70

    def test_03_085_removes_after_thirty_damage_or_advances_clock(self):
        state = _powered_builder('03-085').with_chronos(DAY_CHRONOS).build()
        harness = EffectHarness(state)
        harness.engine.turn_state.damage_taken_this_turn[0] = 30
        harness.engine.process_end_of_turn_effects(state)
        assert state.players[0].set_zone_c is None
        assert state.chronos == DAY_CHRONOS, 'removed enchants do not advance the clock'

        state = _powered_builder('03-085').with_chronos(DAY_CHRONOS).build()
        harness = EffectHarness(state)
        harness.engine.process_end_of_turn_effects(state)
        assert state.players[0].set_zone_c is not None
        assert state.chronos == DAY_CHRONOS + 2

        state = _powered_builder('03-085').with_chronos(NIGHT_CHRONOS).build()
        EffectHarness(state).engine.process_end_of_turn_effects(state)
        assert state.chronos == NIGHT_CHRONOS, 'no clock advance at night'

    def test_03_058_removes_after_thirty_damage_or_heals_both(self):
        state = (_powered_builder('03-058')
                 .with_hp(0, 60)
                 .with_hp(1, 70)
                 .build())
        harness = EffectHarness(state)
        harness.engine.process_end_of_turn_effects(state)
        assert state.players[0].hp == 70 and state.players[1].hp == 80

        state = _powered_builder('03-058').with_hp(0, 60).build()
        harness = EffectHarness(state)
        harness.engine.turn_state.damage_taken_this_turn[0] = 35
        harness.engine.process_end_of_turn_effects(state)
        assert state.players[0].set_zone_c is None
        assert state.players[0].hp == 60, 'a removed 03-058 does not heal'


class TestEngineHelpers:
    def test_deal_damage_floors_at_zero_and_records(self):
        state = GameStateBuilder().with_hp(1, 10).build()
        harness = EffectHarness(state)
        harness.engine.deal_damage(state, 1, 25, source='test')
        assert state.players[1].hp == 0
        assert harness.engine.turn_state.damage_taken_this_turn[1] == 25

        harness.engine.deal_damage(state, 0, 0)
        assert state.players[0].hp == 100, 'zero damage is a no-op'

    def test_place_in_abyss_resets_card_state_and_sets_triggers(self):
        from zutomayo.enums.attribute import Attribute
        from tests.support.game_state_builder import card_by_identity
        from zutomayo.models.card_instance import CardInstance

        state = GameStateBuilder().build()
        harness = EffectHarness(state)
        card_instance = CardInstance(card=card_by_identity(WIND_CHARACTER))
        card_instance.attribute_override = Attribute.DARKNESS
        card_instance.effects_disabled = True

        harness.engine.place_in_abyss(card_instance, state.players[1], actor_index=0)
        assert card_instance.zone == Zone.ABYSS and card_instance.face_up
        assert card_instance.attribute_override is None and not card_instance.effects_disabled
        assert harness.engine.turn_state.abyss_received_card[1] is True
        assert harness.engine.turn_state.opponent_card_to_abyss[1] is True, \
            'actor 0 placed into abyss: the flag keys the placement victim side'

    def test_place_in_power_charger_flags_only_own_placements(self):
        from tests.support.game_state_builder import card_by_identity
        from zutomayo.models.card_instance import CardInstance

        state = GameStateBuilder().build()
        harness = EffectHarness(state)

        own_card = CardInstance(card=card_by_identity(DARKNESS_CHARACTER))
        harness.engine.place_in_power_charger(own_card, state.players[0], actor_index=0)
        assert harness.engine.turn_state.card_to_power_this_turn[0] is True
        assert harness.engine.turn_state.character_to_power_this_turn[0] is True

        forced_card = CardInstance(card=card_by_identity(DARKNESS_CHARACTER))
        harness.engine.place_in_power_charger(forced_card, state.players[1], actor_index=0)
        assert harness.engine.turn_state.card_to_power_this_turn[1] is False, \
            'placements forced by the opponent do not count as own placements'

    def test_is_effectively_midnight_with_extension(self):
        state = GameStateBuilder().with_chronos(6).build()
        harness = EffectHarness(state)
        assert harness.engine.is_effectively_midnight(state) is False
        harness.engine.turn_state.midnight_extended = True
        assert harness.engine.is_effectively_midnight(state) is True
        state.chronos = 7
        assert harness.engine.is_effectively_midnight(state) is False

    def test_get_effective_attack_override_gate_and_floor(self):
        # Attack override (04-099) wins over everything.
        state = GameStateBuilder().with_battle_card(0, LOW_COST_CHARACTER).with_chronos(NIGHT_CHRONOS).build()
        harness = EffectHarness(state)
        harness.engine.turn_state.attack_override[0] = 100
        assert harness.engine.get_effective_attack(state, state.players[0]) == 100

        # Power-cost gate: cost 5 with no power means 0.
        state = GameStateBuilder().with_battle_card(0, DARKNESS_CHARACTER).build()
        harness = EffectHarness(state)
        assert harness.engine.get_effective_attack(state, state.players[0]) == 0

        # Negative modifiers floor at 0.
        state = GameStateBuilder().with_battle_card(0, LOW_COST_CHARACTER).with_chronos(NIGHT_CHRONOS).build()
        harness = EffectHarness(state)
        harness.engine.turn_state.attack_bonus[0] = -200
        assert harness.engine.get_effective_attack(state, state.players[0]) == 0

        # Base case: 01-009 at night attacks with attack_night (50).
        state = GameStateBuilder().with_battle_card(0, LOW_COST_CHARACTER).with_chronos(NIGHT_CHRONOS).build()
        harness = EffectHarness(state)
        assert harness.engine.get_effective_attack(state, state.players[0]) == 50

    def test_get_effective_power_cost_floors_at_zero(self):
        state = GameStateBuilder().with_battle_card(0, WIND_CHARACTER).build()
        harness = EffectHarness(state)
        battle_card = state.players[0].battle_zone
        battle_card.power_cost_reduction = 5   # cost 3 - 5
        assert harness.engine.get_effective_power_cost(battle_card, state.players[0]) == 0


class TestEffectCollectionAndCostGate:
    def test_collection_order_and_filters(self):
        state = (GameStateBuilder()
                 .with_single_card(0, 'set_zone_c', '02-086')                      # area enchant, always eligible
                 .with_single_card(0, 'set_zone_a', '01-030', played_this_turn=True)   # enchant played this turn
                 .with_single_card(0, 'set_zone_b', '01-030', played_this_turn=False)  # too old
                 .with_battle_card(0, '03-045', played_this_turn=True)             # character played this turn
                 .build())
        harness = EffectHarness(state)
        eligible = harness.engine._collect_eligible_effects(state, 0)
        assert [ci.card.effect for ci in eligible] == ['02-086', '01-030', '03-045']

    def test_disabled_effects_are_not_collected(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '03-045', played_this_turn=True)
                 .build())
        state.players[0].battle_zone.effects_disabled = True
        harness = EffectHarness(state)
        assert harness.engine._collect_eligible_effects(state, 0) == []

    def test_cost_gate_skips_unaffordable_effects(self):
        # 02-064 is an area enchant costing 2; with no power it is collected
        # but skipped at dispatch time.
        state = (GameStateBuilder()
                 .with_single_card(0, 'set_zone_c', '02-064')
                 .build())
        harness = EffectHarness(state)
        result = asyncio.run(harness.engine.process_effects(state, 0))
        assert result.resolved == []
        assert [ci.card.effect for ci in result.skipped_cost] == ['02-064']

    def test_power_bonus_counts_for_non_area_effects_only(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, '03-045', played_this_turn=True)   # character, cost?
                 .build())
        harness = EffectHarness(state)
        cost = harness.engine.get_effective_power_cost(state.players[0].battle_zone, state.players[0])
        harness.engine.turn_state.power_bonus[0] = cost   # bonus alone affords it
        result = asyncio.run(harness.engine.process_effects(state, 0))
        assert [ci.card.effect for ci in result.resolved] == ['03-045']

    def test_two_effects_prompt_for_order(self):
        state = (GameStateBuilder()
                 .with_single_card(0, 'set_zone_a', '01-030', played_this_turn=True)
                 .with_single_card(0, 'set_zone_b', '01-089', played_this_turn=True)
                 .with_power_charger(0, [STP_TWO_CARD] * 4)   # affords both enchants
                 .build())
        harness = EffectHarness(state)
        harness.adapter.answers.extend([ScriptedAnswer.card_indices([1])])
        result = asyncio.run(harness.engine.process_effects(state, 0))
        assert [ci.card.effect for ci in result.resolved] == ['01-089', '01-030']
        assert harness.engine.turn_state.attack_bonus[0] == 40

    def test_cost_reducers_are_forced_first(self):
        from zutomayo.effects.effect_engine import _COST_REDUCING_EFFECTS
        from tests.support.game_state_builder import card_by_identity
        from zutomayo.models.card_instance import CardInstance

        assert _COST_REDUCING_EFFECTS == frozenset({'02-006', '04-065'})
        cost_reducer = CardInstance(card=card_by_identity('02-006'))
        plain = CardInstance(card=card_by_identity('01-030'))
        state = GameStateBuilder().build()
        harness = EffectHarness(state)
        forced, selectable = harness.engine._partition_forced_first([plain, cost_reducer])
        assert forced == [cost_reducer] and selectable == [plain]