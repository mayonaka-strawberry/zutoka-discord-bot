"""Characterization tests for TurnManager and GameController."""

from __future__ import annotations

import asyncio
import random
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

import pytest  # noqa: E402

from zutomayo.enums.chronos import Chronos  # noqa: E402
from zutomayo.enums.result import Result  # noqa: E402
from zutomayo.enums.zone import Zone  # noqa: E402

from tests.support.game_state_builder import GameStateBuilder, card_by_identity  # noqa: E402
from tests.support.effect_harness import EffectHarness, ScriptedAnswer, card_identities  # noqa: E402

DARKNESS_CHARACTER = '01-001'   # clock? cost 5, STP 0
WIND_CHARACTER = '01-004'       # cost 3
LOW_COST_CHARACTER = '01-009'   # cost 0, STP 1, night attack 50
ONE_COST_CHARACTER = '01-010'   # cost 1, STP 1, night attack 60
STP_TWO_CARD = '01-013'
FLAT_BONUS_ENCHANT = '01-030'   # ENCHANT

NIGHT_CHRONOS = 4


def _harness(state) -> EffectHarness:
    return EffectHarness(state)


class TestAdvanceChronos:
    def test_advances_by_played_card_clocks_and_tracks_transitions(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, LOW_COST_CHARACTER, played_this_turn=True)
                 .with_chronos(7)
                 .build())
        harness = _harness(state)
        clock = state.players[0].battle_zone.card.clock
        advanced = harness.turn_manager.advance_chronos(state.players[0])
        assert advanced == clock
        assert state.chronos == (7 + clock) % 18
        assert harness.engine.turn_state.chronos_advanced[0] == clock
        if state.chronos > 8:
            assert harness.engine.turn_state.night_to_day_occurred is True

    def test_02_005_disables_character_clocks(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, LOW_COST_CHARACTER, played_this_turn=True)
                 .with_single_card(1, 'set_zone_c', '02-005')
                 .with_power_charger(1, [STP_TWO_CARD] * 3)   # affords cost 5
                 .with_chronos(0)
                 .build())
        harness = _harness(state)
        advanced = harness.turn_manager.advance_chronos(state.players[0])
        assert advanced == 0, "opponent's 02-005 disables this player's character clocks"
        assert state.chronos == 0

    def test_03_061_treats_all_clocks_as_one(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, LOW_COST_CHARACTER, played_this_turn=True)
                 .with_single_card(0, 'set_zone_c', '03-061')
                 .with_power_charger(0, [STP_TWO_CARD])
                 .with_chronos(0)
                 .build())
        harness = _harness(state)
        advanced = harness.turn_manager.advance_chronos(state.players[0])
        assert advanced == 1

    def test_cards_not_played_this_turn_do_not_count(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, LOW_COST_CHARACTER, played_this_turn=False)
                 .with_chronos(0)
                 .build())
        harness = _harness(state)
        assert harness.turn_manager.advance_chronos(state.players[0]) == 0


class TestCharacterSwap:
    def test_set_zone_a_character_swaps_in_and_old_character_leaves(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, LOW_COST_CHARACTER)     # STP 1: leaves to power charger
                 .with_single_card(0, 'set_zone_a', DARKNESS_CHARACTER, played_this_turn=True)
                 .build())
        harness = _harness(state)
        old_character = state.players[0].battle_zone
        asyncio.run(harness.turn_manager.do_character_swap(state.players[0]))
        player = state.players[0]
        assert card_identities([player.battle_zone]) == [DARKNESS_CHARACTER]
        assert player.battle_zone.face_up and player.battle_zone.zone == Zone.BATTLE_ZONE
        assert player.set_zone_a is None
        assert old_character in player.power_charger
        assert card_by_identity(LOW_COST_CHARACTER).song in harness.engine.turn_state.swapped_from_songs[0]

    def test_starless_old_character_goes_to_abyss(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, DARKNESS_CHARACTER)     # STP 0: leaves to abyss
                 .with_single_card(0, 'set_zone_a', WIND_CHARACTER, played_this_turn=True)
                 .build())
        harness = _harness(state)
        old_character = state.players[0].battle_zone
        asyncio.run(harness.turn_manager.do_character_swap(state.players[0]))
        assert old_character in state.players[0].abyss

    def test_non_character_set_cards_do_not_swap(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, DARKNESS_CHARACTER)
                 .with_single_card(0, 'set_zone_a', FLAT_BONUS_ENCHANT, played_this_turn=True)
                 .build())
        harness = _harness(state)
        asyncio.run(harness.turn_manager.do_character_swap(state.players[0]))
        assert card_identities([state.players[0].battle_zone]) == [DARKNESS_CHARACTER]

    def test_02_062_lets_the_player_skip_the_swap(self):
        def build_state():
            return (GameStateBuilder()
                    .with_battle_card(0, DARKNESS_CHARACTER)
                    .with_single_card(0, 'set_zone_a', '02-062', played_this_turn=True)
                    .with_single_card(0, 'set_zone_b', WIND_CHARACTER, played_this_turn=True)
                    .with_power_charger(0, [STP_TWO_CARD] * 4)
                    .build())

        # Choosing 1 skips the swap.
        state = build_state()
        harness = _harness(state)
        harness.adapter.answers.append(ScriptedAnswer.number(1))
        asyncio.run(harness.turn_manager.do_character_swap(state.players[0]))
        assert card_identities([state.players[0].battle_zone]) == [DARKNESS_CHARACTER]

        # Choosing 0 swaps normally.
        state = build_state()
        harness = _harness(state)
        harness.adapter.answers.append(ScriptedAnswer.number(0))
        asyncio.run(harness.turn_manager.do_character_swap(state.players[0]))
        assert card_identities([state.players[0].battle_zone]) == [WIND_CHARACTER]

        # Timeout keeps the current character (the likely intent of playing 02-062).
        state = build_state()
        harness = _harness(state)
        harness.adapter.answers.append(ScriptedAnswer.timeout('effect_number_select'))
        asyncio.run(harness.turn_manager.do_character_swap(state.players[0]))
        assert card_identities([state.players[0].battle_zone]) == [DARKNESS_CHARACTER]


class TestAreaEnchantSwap:
    def test_new_area_enchant_replaces_old_one(self):
        state = (GameStateBuilder()
                 .with_single_card(0, 'set_zone_c', '02-086')   # STP 1: leaves to power charger
                 .with_single_card(0, 'set_zone_a', '03-061', played_this_turn=True)
                 .build())
        harness = _harness(state)
        old_enchant = state.players[0].set_zone_c
        harness.turn_manager.do_area_enchant_swap(state.players[0])
        player = state.players[0]
        assert card_identities([player.set_zone_c]) == ['03-061']
        assert player.set_zone_a is None
        assert old_enchant in player.power_charger

    def test_blocked_player_loses_the_new_enchant_immediately(self):
        state = (GameStateBuilder()
                 .with_single_card(0, 'set_zone_a', '03-061', played_this_turn=True)
                 .build())
        state.players[0].area_enchant_blocked = True
        harness = _harness(state)
        new_enchant = state.players[0].set_zone_a
        harness.turn_manager.do_area_enchant_swap(state.players[0])
        player = state.players[0]
        assert player.set_zone_c is None and player.set_zone_a is None
        assert new_enchant in player.power_charger, 'STP 1 routes to the power charger'


class TestResolveBattle:
    def _battle_state(self):
        # 01-010 (night 60) vs 01-009 (night 50), both affordable, at night.
        return (GameStateBuilder()
                .with_battle_card(0, ONE_COST_CHARACTER)
                .with_power_charger(0, [STP_TWO_CARD])
                .with_battle_card(1, LOW_COST_CHARACTER)
                .with_chronos(NIGHT_CHRONOS)
                .build())

    def test_higher_attack_wins_and_deals_the_difference(self):
        state = self._battle_state()
        harness = _harness(state)
        result = harness.turn_manager.resolve_battle()
        assert result['winner'] == 0
        assert result['damage_to_1'] == 10 and result['damage_to_0'] == 0
        assert state.players[1].hp == 90
        assert state.last_battle_winner == state.players[0].name
        assert harness.engine.turn_state.battle_lost[1] is True
        assert harness.engine.turn_state.damage_taken_this_turn[1] == 10

    def test_damage_reduction_applies_and_is_tracked(self):
        state = self._battle_state()
        harness = _harness(state)
        harness.engine.turn_state.damage_reduction[1] = 40
        result = harness.turn_manager.resolve_battle()
        assert result['winner'] == 0 and result['damage_to_1'] == 0
        assert state.players[1].hp == 100
        assert harness.engine.turn_state.damage_reduced_this_turn[1] == 10
        assert harness.engine.turn_state.battle_lost[1] is True, \
            'the battle is still lost even when reduction absorbs the damage'

    def test_damage_not_reducible_ignores_reduction(self):
        state = self._battle_state()
        harness = _harness(state)
        harness.engine.turn_state.damage_reduction[1] = 40
        harness.engine.turn_state.damage_not_reducible[0] = True
        result = harness.turn_manager.resolve_battle()
        assert result['damage_to_1'] == 10

    def test_tie_deals_nothing_and_clears_last_winner(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, LOW_COST_CHARACTER)
                 .with_battle_card(1, LOW_COST_CHARACTER)
                 .with_chronos(NIGHT_CHRONOS)
                 .with_last_battle_winner(0)
                 .build())
        harness = _harness(state)
        result = harness.turn_manager.resolve_battle()
        assert result['winner'] is None
        assert state.last_battle_winner is None
        assert state.players[0].hp == 100 and state.players[1].hp == 100


class TestEndTurnAndWinConditions:
    def test_end_turn_moves_leftover_set_cards_and_draws(self):
        state = (GameStateBuilder()
                 .with_single_card(0, 'set_zone_a', LOW_COST_CHARACTER, played_this_turn=True)
                 .with_deck(0, [WIND_CHARACTER, DARKNESS_CHARACTER])
                 .build())
        state.players[0].cards_played_this_turn = 1
        harness = _harness(state)
        drawn = harness.turn_manager.end_turn(state.players[0])
        player = state.players[0]
        assert drawn == 1 and len(player.hand) == 1
        assert player.set_zone_a is None
        assert len(player.power_charger) == 1, 'leftover STP-1 set card goes to the charger'

    def test_end_turn_deck_out_loses_the_game(self):
        state = GameStateBuilder().with_deck(0, [WIND_CHARACTER]).build()
        state.players[0].cards_played_this_turn = 2
        harness = _harness(state)
        drawn = harness.turn_manager.end_turn(state.players[0])
        assert drawn == 1, 'draws whatever remains'
        assert state.result == Result.PLAYER_2_WIN

    def test_playing_nothing_never_deck_outs(self):
        state = GameStateBuilder().build()
        harness = _harness(state)
        assert harness.turn_manager.end_turn(state.players[0]) == 0
        assert state.result == Result.IN_PROGRESS

    def test_check_win_condition_covers_all_branches(self):
        state = GameStateBuilder().with_hp(0, 0).build()
        harness = _harness(state)
        harness.turn_manager.check_win_condition()
        assert state.result == Result.PLAYER_2_WIN

        state = GameStateBuilder().with_hp(1, 0).build()
        _harness(state).turn_manager.check_win_condition()
        assert state.result == Result.PLAYER_1_WIN

        state = GameStateBuilder().with_hp(0, 0).with_hp(1, 0).build()
        _harness(state).turn_manager.check_win_condition()
        assert state.result == Result.PLAYER_1_WIN, 'simultaneous equal knockout goes to player 1'

        state = GameStateBuilder().build()
        _harness(state).turn_manager.check_win_condition()
        assert state.result == Result.IN_PROGRESS

    def test_get_max_cards_to_set(self):
        state = GameStateBuilder().with_last_battle_winner(None).build()
        turn_manager = _harness(state).turn_manager
        assert turn_manager.get_max_cards_to_set(state.players[0]) == 1

        state = GameStateBuilder().with_last_battle_winner(0).build()
        turn_manager = _harness(state).turn_manager
        assert turn_manager.get_max_cards_to_set(state.players[0]) == 1
        assert turn_manager.get_max_cards_to_set(state.players[1]) == 2


class TestSetupHelpers:
    def test_set_card_and_set_initial_battle_card(self):
        state = GameStateBuilder().with_hand(0, [WIND_CHARACTER, DARKNESS_CHARACTER]).build()
        harness = _harness(state)
        player = state.players[0]
        first, second = player.hand[0], player.hand[1]

        harness.turn_manager.set_card(player, first, Zone.SET_ZONE_A)
        assert player.set_zone_a is first and first.played_this_turn
        assert not first.face_up and player.cards_played_this_turn == 1

        harness.turn_manager.set_initial_battle_card(player, second)
        assert player.battle_zone is second and player.cards_played_this_turn == 2

    def test_reveal_initial_card_keeps_characters_and_discards_others(self):
        state = GameStateBuilder().with_battle_card(0, WIND_CHARACTER, played_this_turn=True).build()
        state.players[0].battle_zone.face_up = False
        harness = _harness(state)
        assert harness.turn_manager.reveal_initial_card(state.players[0]) is True
        assert state.players[0].battle_zone.face_up

        state = GameStateBuilder().with_battle_card(0, FLAT_BONUS_ENCHANT).build()
        harness = _harness(state)
        assert harness.turn_manager.reveal_initial_card(state.players[0]) is False
        assert state.players[0].battle_zone is None
        assert len(state.players[0].abyss) == 1, 'STP-0 non-character goes to the abyss'

        state = GameStateBuilder().build()
        assert _harness(state).turn_manager.reveal_initial_card(state.players[0]) is False

    def test_reset_turn_flags_clears_per_turn_state(self):
        state = (GameStateBuilder()
                 .with_battle_card(0, WIND_CHARACTER, played_this_turn=True)
                 .with_hand(0, [DARKNESS_CHARACTER])
                 .build())
        state.players[0].cards_played_this_turn = 2
        state.players[0].battle_zone.power_cost_reduction = 2
        harness = _harness(state)
        harness.engine.turn_state.attack_bonus[0] = 50
        harness.turn_manager.reset_turn_flags()
        assert state.players[0].cards_played_this_turn == 0
        assert state.players[0].battle_zone.played_this_turn is False
        assert state.players[0].battle_zone.power_cost_reduction == 0
        assert harness.engine.turn_state.attack_bonus[0] == 0, 'turn state is rebuilt'


class TestGameController:
    def test_seeded_generator_controls_the_coin_flip(self):
        from zutomayo.effects.effect_engine import EffectEngine
        from zutomayo.engine.game_controller import GameController
        from zutomayo.models.card_instance import CardInstance

        def build(seed=None):
            deck = [CardInstance(card=card_by_identity(WIND_CHARACTER)) for _ in range(3)]
            deck_2 = [CardInstance(card=card_by_identity(WIND_CHARACTER)) for _ in range(3)]
            generator = random.Random(seed) if seed is not None else None
            controller = GameController(
                name_1='one', name_2='two', deck_1=deck, deck_2=deck_2,
                effect_engine=EffectEngine(),
                random_generator=generator,
            )
            return controller.game_state

        state_a = build(seed=99)
        state_b = build(seed=99)
        assert state_a.players[0].side == state_b.players[0].side, 'same seed, same coin flip'
        assert state_a.players[0].side != state_a.players[1].side
        assert {state_a.players[0].side, state_a.players[1].side} == {Chronos.DAY, Chronos.NIGHT}

    def test_module_random_default_still_works(self):
        from zutomayo.effects.effect_engine import EffectEngine
        from zutomayo.engine.game_controller import GameController
        from zutomayo.models.card_instance import CardInstance

        random.seed(123)
        controller = GameController(
            name_1='one', name_2='two',
            deck_1=[CardInstance(card=card_by_identity(WIND_CHARACTER))],
            deck_2=[CardInstance(card=card_by_identity(WIND_CHARACTER))],
            effect_engine=EffectEngine(),
        )
        assert controller.game_state.players[0].side in (Chronos.DAY, Chronos.NIGHT)