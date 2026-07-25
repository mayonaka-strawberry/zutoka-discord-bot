"""Explicit tests for every confirmed ruling the new engine must reproduce.

These build surgical states (bypassing normal play) and invoke the specific
rules functions, verifying each documented ruling from the plan:
- cost reducers 02-006/04-065 forced-first and stacking
- 03-058/03-085 count ALL damage (battle+effect), self-remove at >=30, power-gated
- 04-032 self-removal can pre-empt its own resolution
- placement trigger matrix (agent-based vs location-based)
- 03-026 widens midnight +/-2 for all midnight conditions
- 04-095 keys on the battle loss itself, not damage
- attack precedence: 04-099 override > power gate > 02-007 force-day > 01-005 reversal
"""

from __future__ import annotations

import random

from engine_alpha import cards
from engine_alpha.battle import (
    MIDNIGHT, deal_damage, get_effective_attack, is_effectively_midnight,
    resolve_battle, total_power,
)
from engine_alpha.effects import interpreter
from engine_alpha.effects.removal import check_area_removal
from engine_alpha.effects.turn_end import process_end_of_turn_effects
from engine_alpha.events import EVENT_EFFECT_SKIPPED_COST, EVENT_EFFECT_STARTED
from engine_alpha.game import Game
from engine_alpha.state import (
    GF_MIDNIGHT_EXTENDED, PH_PROCESS_EFFECTS,
    PF_ABYSS_RECEIVED, PF_ATTACK_BONUS, PF_ATTACK_OVERRIDE, PF_BATTLE_LOST,
    PF_CARD_TO_POWER, PF_CHAR_TO_POWER, PF_DAMAGE_REDUCTION, PF_DAMAGE_TAKEN,
    PF_DAY_NIGHT_REVERSED, PF_OPP_CARD_TO_ABYSS, PF_POWER_BONUS,
)
from engine_alpha.zones import place_in_abyss, place_in_charger, to_power_or_abyss
from .conftest import random_vanilla_deck


def fx(effect_id: str) -> int:
    return cards.EFFECT_TO_INDEX[effect_id]


def card_with_effect(effect_id: str) -> int:
    return cards.EFFECT_TO_CARD[fx(effect_id)]


def make_game() -> Game:
    rng = random.Random(0)
    game = Game(seed=123, mode="fixed_decks",
                decks=(random_vanilla_deck(rng), random_vanilla_deck(rng)))
    # Drive past setup decisions with a fixed policy to reach a live game.
    while game.state.turn == 0:
        game.apply(game.legal_actions()[-1])  # PASS mulligans, last-card picks
    return game


def spawn(game: Game, def_index: int) -> int:
    """Create a fresh instance outside any zone (tests place it manually)."""
    return game.state.new_instance(def_index)


def run_effect(game: Game, owner: int, instance_id: int, answers=()) -> None:
    """Resolve one effect to completion, feeding scripted answers."""
    state = game.state
    interpreter.start_effect(state, owner, instance_id,
                             cards.EFFECT_T[state.inst_def[instance_id]])
    request = interpreter.resume(state, None, None)
    answer_queue = list(answers)
    while request is not None:
        request = interpreter.resume(state, request, answer_queue.pop(0))
    assert not state.frame_stack


def find_character(min_power_cost=0, max_power_cost=8, attack_night=None) -> int:
    for d in cards.CARD_DB:
        if d.card_type != cards.TYPE_CHARACTER:
            continue
        if not min_power_cost <= d.power_cost <= max_power_cost:
            continue
        if attack_night is not None and d.attack_night != attack_night:
            continue
        return d.index
    raise AssertionError("no matching character")


# ---------------------------------------------------------------------------
# Cost reducers: 02-006 / 04-065 stack, and are forced first
# ---------------------------------------------------------------------------

def test_cost_reducers_stack():
    game = make_game()
    state = game.state
    player = state.players[0]
    study_me = next(d.index for d in cards.CARD_DB
                    if d.card_type == cards.TYPE_CHARACTER
                    and cards.SONG_NAMES[d.song] == "STUDY_ME" and d.power_cost >= 4)
    battle_instance = spawn(game, study_me)
    if player.battle != -1:
        player.abyss.append(player.battle)
    player.battle = battle_instance
    state.inst_played[battle_instance] = 1

    reducer_a = spawn(game, card_with_effect("02-006"))
    reducer_b = spawn(game, card_with_effect("04-065"))
    run_effect(game, 0, reducer_a)
    assert state.inst_cost_red[battle_instance] == 2
    run_effect(game, 0, reducer_b)
    assert state.inst_cost_red[battle_instance] == 4  # they stack

    from engine_alpha.effects.dispatch import COST_REDUCING
    assert fx("02-006") in COST_REDUCING and fx("04-065") in COST_REDUCING


# ---------------------------------------------------------------------------
# 03-058 / 03-085: >=30 total damage (battle + effect) self-removal, power gate
# ---------------------------------------------------------------------------

def _setup_area(game, owner_index, effect_id, *, power=True):
    state = game.state
    player = state.players[owner_index]
    area = spawn(game, card_with_effect(effect_id))
    if player.set_c != -1:
        player.abyss.append(player.set_c)
    player.set_c = area
    if power:
        # Fill the charger with enough SEND TO POWER to satisfy any cost.
        stp2 = [d.index for d in cards.CARD_DB if d.send_to_power == 2][:5]
        for def_index in stp2:
            player.charger.append(spawn(game, def_index))
    return area


def test_03_058_counts_battle_plus_effect_damage():
    game = make_game()
    state = game.state
    area = _setup_area(game, 0, "03-058")
    # 20 effect damage + 15 "battle" damage accumulated = 35 >= 30 -> removed
    deal_damage(state, 0, 20)
    state.players[0].flags[PF_DAMAGE_TAKEN] += 15  # as the battle resolver does
    process_end_of_turn_effects(state)
    assert state.players[0].set_c == -1
    assert area in state.players[0].abyss


def test_03_058_heals_once_below_threshold():
    game = make_game()
    state = game.state
    _setup_area(game, 0, "03-058")
    state.players[0].hp = 50
    state.players[1].hp = 60
    deal_damage(state, 0, 20)  # below 30: stays, heals both once
    hp_0_before_heal = state.players[0].hp
    process_end_of_turn_effects(state)
    assert state.players[0].set_c != -1
    assert state.players[0].hp == hp_0_before_heal + 10
    assert state.players[1].hp == 70


def test_03_085_power_gated():
    game = make_game()
    state = game.state
    area = _setup_area(game, 0, "03-085", power=False)  # cost unmet
    deal_damage(state, 0, 40)
    process_end_of_turn_effects(state)
    assert state.players[0].set_c == area  # not removed: power gate


# ---------------------------------------------------------------------------
# 04-032: self-removal (to abyss? no - via send_to_power routing) when the
# opponent has an area enchant, checked at every removal checkpoint
# ---------------------------------------------------------------------------

def test_04_032_self_removes_when_opponent_has_area():
    game = make_game()
    state = game.state
    area = _setup_area(game, 0, "04-032")
    _setup_area(game, 1, "03-064")  # opponent area enchant present
    check_area_removal(state)
    assert state.players[0].set_c == -1  # removed before its own resolution


# ---------------------------------------------------------------------------
# Placement trigger matrix
# ---------------------------------------------------------------------------

def test_abyss_triggers_agent_vs_location():
    game = make_game()
    state = game.state
    card = spawn(game, find_character())
    # Opponent (P1) mills P0's card into P0's abyss: location trigger fires for
    # abyss owner P0; agent trigger fires for P0 (P0's opponent acted).
    place_in_abyss(state, card, owner_index=0, actor_index=1)
    assert state.players[0].flags[PF_ABYSS_RECEIVED] == 1
    assert state.players[1].flags[PF_ABYSS_RECEIVED] == 0
    assert state.players[0].flags[PF_OPP_CARD_TO_ABYSS] == 1  # P1 acted -> watcher P0
    assert state.players[1].flags[PF_OPP_CARD_TO_ABYSS] == 0


def test_charger_triggers_owner_only():
    game = make_game()
    state = game.state
    character = spawn(game, find_character())
    # Owner places own character: both flags fire.
    place_in_charger(state, character, owner_index=0, actor_index=0)
    assert state.players[0].flags[PF_CARD_TO_POWER] == 1
    assert state.players[0].flags[PF_CHAR_TO_POWER] == 1
    # Opponent-forced placement (04-006 / 03-097 style): flags do NOT fire.
    character_2 = spawn(game, find_character())
    place_in_charger(state, character_2, owner_index=1, actor_index=0)
    assert state.players[1].flags[PF_CARD_TO_POWER] == 0
    assert state.players[1].flags[PF_CHAR_TO_POWER] == 0


# ---------------------------------------------------------------------------
# 03-026: midnight widened to +/-2 for all midnight conditions
# ---------------------------------------------------------------------------

def test_midnight_widening_propagates():
    game = make_game()
    state = game.state
    state.chronos = MIDNIGHT + 2
    assert not is_effectively_midnight(state)
    state.gflags[GF_MIDNIGHT_EXTENDED] = 1
    assert is_effectively_midnight(state)
    state.chronos = MIDNIGHT + 3
    assert not is_effectively_midnight(state)

    # A midnight-conditional buff (03-001) inherits the widening.
    state.chronos = MIDNIGHT - 2
    buff_card = spawn(game, card_with_effect("03-001"))
    before = state.players[0].flags[PF_ATTACK_BONUS]
    run_effect(game, 0, buff_card)
    assert state.players[0].flags[PF_ATTACK_BONUS] == before + 100


# ---------------------------------------------------------------------------
# 04-095: keyed on the battle loss, not the damage
# ---------------------------------------------------------------------------

def test_04_095_removes_on_zero_damage_loss():
    game = make_game()
    state = game.state
    area = _setup_area(game, 0, "04-095")
    # P0 loses the battle but damage is fully reduced to 0.
    state.players[0].flags[PF_DAMAGE_REDUCTION] = 200
    strong = find_character(min_power_cost=0, max_power_cost=0)
    for player_index, def_index in ((0, strong), (1, strong)):
        instance = spawn(game, def_index)
        player = state.players[player_index]
        if player.battle != -1:
            player.abyss.append(player.battle)
        player.battle = instance
    state.players[1].flags[PF_ATTACK_BONUS] = 50  # P1 wins
    resolve_battle(state)
    assert state.players[0].flags[PF_BATTLE_LOST] == 1
    assert state.players[0].hp == 100  # damage fully reduced
    check_area_removal(state)
    assert state.players[0].set_c == -1  # removed despite zero damage


# ---------------------------------------------------------------------------
# Attack precedence: override > power gate > force-day > reversal > base
# ---------------------------------------------------------------------------

def test_attack_precedence_chain():
    game = make_game()
    state = game.state
    player = state.players[0]
    # A character with different day/night attacks and a real power cost.
    def_index = next(d.index for d in cards.CARD_DB
                     if d.card_type == cards.TYPE_CHARACTER
                     and d.attack_day != d.attack_night and d.power_cost >= 3)
    d = cards.CARD_DB[def_index]
    instance = spawn(game, def_index)
    if player.battle != -1:
        player.abyss.append(player.battle)
    player.battle = instance
    player.charger.clear()

    state.chronos = 4  # night
    # 1. Power cost unmet -> attack 0
    assert get_effective_attack(state, player) == 0
    # 2. Meet the cost -> night attack
    for stp2 in [x.index for x in cards.CARD_DB if x.send_to_power == 2][:5]:
        player.charger.append(spawn(game, stp2))
    assert get_effective_attack(state, player) == d.attack_night
    # 3. 01-005 reversal -> day attack at night
    player.flags[PF_DAY_NIGHT_REVERSED] = 1
    assert get_effective_attack(state, player) == d.attack_day
    player.flags[PF_DAY_NIGHT_REVERSED] = 0
    # 4. Attack bonus applies on top of base
    player.flags[PF_ATTACK_BONUS] = 25
    assert get_effective_attack(state, player) == d.attack_night + 25
    # 5. 04-099 override beats everything, even the power gate
    player.flags[PF_ATTACK_OVERRIDE] = 100
    player.charger.clear()
    assert get_effective_attack(state, player) == 100


def test_power_gate_uses_effective_cost():
    game = make_game()
    state = game.state
    player = state.players[0]
    def_index = find_character(min_power_cost=2, max_power_cost=2)
    instance = spawn(game, def_index)
    if player.battle != -1:
        player.abyss.append(player.battle)
    player.battle = instance
    player.charger.clear()
    state.chronos = 4
    assert get_effective_attack(state, player) == 0
    state.inst_cost_red[instance] = 2  # 02-006-style reduction lifts the gate
    assert get_effective_attack(state, player) == cards.CARD_DB[def_index].attack_night


# ---------------------------------------------------------------------------
# to_power_or_abyss routing
# ---------------------------------------------------------------------------

def test_send_to_power_routing():
    game = make_game()
    state = game.state
    starred = next(d.index for d in cards.CARD_DB if d.send_to_power > 0)
    unstarred = next(d.index for d in cards.CARD_DB if d.send_to_power == 0)
    a = spawn(game, starred)
    b = spawn(game, unstarred)
    to_power_or_abyss(state, a, 0)
    to_power_or_abyss(state, b, 0)
    assert a in state.players[0].charger
    assert b in state.players[0].abyss


# ---------------------------------------------------------------------------
# 04-053: 'may' place a STUDY_ME character from hand onto the power charger,
# then draw 1. The pick is declinable (Discord shows a "Skip" row); declining
# skips both the placement and the draw ("if you do, draw 1").
# ---------------------------------------------------------------------------

def _study_me_character() -> int:
    return next(d.index for d in cards.CARD_DB
               if d.card_type == cards.TYPE_CHARACTER
               and cards.SONG_NAMES[d.song] == "STUDY_ME")


def test_04_053_skip_declines_place_and_draw():
    game = make_game()
    state = game.state
    player = state.players[0]
    player.hand[:] = [spawn(game, _study_me_character())]
    candidate = player.hand[0]
    player.deck.append(spawn(game, _study_me_character()))  # a draw would be possible
    charger_before = list(player.charger)
    deck_before = len(player.deck)
    run_effect(game, 0, spawn(game, card_with_effect("04-053")), answers=(1,))  # PASS
    assert candidate in player.hand           # not placed
    assert player.charger == charger_before   # charger untouched
    assert len(player.deck) == deck_before    # no draw


def test_04_053_select_places_on_charger_and_draws():
    game = make_game()
    state = game.state
    player = state.players[0]
    player.hand[:] = [spawn(game, _study_me_character())]
    candidate = player.hand[0]
    player.deck.append(spawn(game, _study_me_character()))
    deck_before = len(player.deck)
    run_effect(game, 0, spawn(game, card_with_effect("04-053")), answers=(0,))  # pick 0
    assert candidate not in player.hand
    assert candidate in player.charger          # placed on the power charger
    assert len(player.deck) == deck_before - 1  # drew exactly 1


# ---------------------------------------------------------------------------
# 02-015: 'may' use an additional enchant from hand, then draw 1. Declinable.
# Ruling: the enchant is playable even without the power to pay its cost; when
# unaffordable its effect does not trigger, but it is still placed and the draw
# still happens.
# ---------------------------------------------------------------------------

def _unconditional_self_buff_enchant():
    """An ENCHANT whose effect is an unconditional SELF attack buff by a
    constant amount (no gate, no decisions, deck-neutral), so triggering is
    directly observable via PF_ATTACK_BONUS. Returns (def_index, bonus)."""
    from engine_alpha.effects.catalog import CATALOG
    for effect_id, ir in CATALOG.items():
        if ir.cond is not None or ir.custom is not None or not ir.ops:
            continue
        if not all(op[0] == "atk_bonus" and op[1] == 0 and isinstance(op[2], int)
                   for op in ir.ops):
            continue
        effect_index = cards.EFFECT_TO_INDEX[effect_id]
        for d in cards.CARD_DB:
            if (d.card_type == cards.TYPE_ENCHANT and d.power_cost >= 1
                    and cards.EFFECT_T[d.index] == effect_index):
                return d.index, sum(op[2] for op in ir.ops)
    raise AssertionError("no unconditional self-buff enchant available")


def _enable_02_015_gate(game, owner_index=0):
    """Satisfy 02-015's gate: previous battle character was DARK, and it is day."""
    state = game.state
    dark_def = next(d.index for d in cards.CARD_DB
                    if cards.ATTRIBUTE_T[d.index] == cards.ATTR_DARKNESS)
    state.players[owner_index].prev_battle_def = dark_def
    state.chronos = 12  # day (chronos > 8)


def _give_power(game, player, amount):
    stp2 = next(d.index for d in cards.CARD_DB if d.send_to_power == 2)
    while total_power(game.state, player) < amount:
        player.charger.append(spawn(game, stp2))


def test_02_015_skip_declines_enchant_but_still_draws():
    game = make_game()
    player = game.state.players[0]
    _enable_02_015_gate(game)
    enchant_def, _ = _unconditional_self_buff_enchant()
    player.hand[:] = [spawn(game, enchant_def)]
    enchant = player.hand[0]
    player.deck.append(spawn(game, enchant_def))
    _give_power(game, player, 8)
    bonus_before = player.flags[PF_ATTACK_BONUS]
    deck_before = len(player.deck)
    run_effect(game, 0, spawn(game, card_with_effect("02-015")), answers=(1,))  # PASS
    assert enchant in player.hand                          # not played
    assert player.flags[PF_ATTACK_BONUS] == bonus_before   # effect not triggered
    assert len(player.deck) == deck_before - 1             # still drew 1


def test_02_015_select_plays_enchant_triggers_effect_and_draws():
    game = make_game()
    player = game.state.players[0]
    _enable_02_015_gate(game)
    enchant_def, bonus = _unconditional_self_buff_enchant()
    player.hand[:] = [spawn(game, enchant_def)]
    enchant = player.hand[0]
    player.deck.append(spawn(game, enchant_def))
    _give_power(game, player, 8)
    bonus_before = player.flags[PF_ATTACK_BONUS]
    deck_before = len(player.deck)
    run_effect(game, 0, spawn(game, card_with_effect("02-015")), answers=(0,))
    assert enchant not in player.hand                             # played
    assert player.flags[PF_ATTACK_BONUS] == bonus_before + bonus  # effect triggered
    assert len(player.deck) == deck_before - 1                    # drew 1


def test_02_015_unaffordable_enchant_is_played_without_effect():
    game = make_game()
    player = game.state.players[0]
    _enable_02_015_gate(game)
    enchant_def, _ = _unconditional_self_buff_enchant()
    player.hand[:] = [spawn(game, enchant_def)]
    enchant = player.hand[0]
    player.deck.append(spawn(game, enchant_def))
    player.charger.clear()               # no power to pay the enchant's cost
    player.flags[PF_POWER_BONUS] = 0
    bonus_before = player.flags[PF_ATTACK_BONUS]
    deck_before = len(player.deck)
    run_effect(game, 0, spawn(game, card_with_effect("02-015")), answers=(0,))
    assert enchant not in player.hand                     # still played
    assert player.flags[PF_ATTACK_BONUS] == bonus_before  # effect did NOT trigger
    assert len(player.deck) == deck_before - 1            # and still drew 1


# ---------------------------------------------------------------------------
# CHAOS bank-or-lose bombs (04-006 / 04-027 / 04-028 / 04-088): the engine
# records who self-defeated and on which turn, so the bot layer can refuse to
# pay Elo for a deliberately thrown game. Recording is informational only --
# the winner and Game.returns() are unaffected.
# ---------------------------------------------------------------------------

# (effect id, number of abyss cards that is one short of the requirement)
CHAOS_BOMBS = (("04-006", 3), ("04-027", 0), ("04-028", 5), ("04-088", 0),
               ("04-105", 7))
# The same cards with their requirement exactly satisfied.
CHAOS_BOMBS_SATISFIED = (("04-006", 4), ("04-027", 1), ("04-028", 6),
                         ("04-088", 1), ("04-105", 8))


def stock_abyss(game: Game, owner: int, count: int) -> None:
    """Put `count` throwaway cards in a player's abyss."""
    player = game.state.players[owner]
    player.abyss.clear()
    for _ in range(count):
        player.abyss.append(spawn(game, find_character()))


def stock_charger(game: Game, owner: int, count: int) -> list[int]:
    """Replace a player's power charger with `count` throwaway cards."""
    player = game.state.players[owner]
    player.charger.clear()
    for _ in range(count):
        player.charger.append(spawn(game, find_character()))
    return list(player.charger)


def clear_placement_flags(game: Game) -> None:
    """Zero the placement triggers that game setup already fired, so a test
    can attribute them to the effect under test."""
    for player in game.state.players:
        for flag in (PF_ABYSS_RECEIVED, PF_OPP_CARD_TO_ABYSS,
                     PF_CARD_TO_POWER, PF_CHAR_TO_POWER):
            player.flags[flag] = 0


def run_effect_auto(game: Game, owner: int, instance_id: int) -> None:
    """Resolve an effect, answering every prompt with its first legal action."""
    state = game.state
    interpreter.start_effect(state, owner, instance_id,
                             cards.EFFECT_T[state.inst_def[instance_id]])
    request = interpreter.resume(state, None, None)
    while request is not None:
        request = interpreter.resume(state, request, request.legal_actions()[0])
    assert not state.frame_stack


def test_chaos_bomb_short_abyss_records_self_defeat():
    from engine_alpha.battle import check_win

    for effect_id, abyss_count in CHAOS_BOMBS:
        game = make_game()
        state = game.state
        state.turn = 1
        stock_abyss(game, 0, abyss_count)
        run_effect_auto(game, 0, spawn(game, card_with_effect(effect_id)))

        assert state.players[0].hp == 0, effect_id
        assert state.self_defeat_player == 0, effect_id
        assert state.self_defeat_turn == 1, effect_id
        check_win(state)
        assert state.winner == 1, effect_id
        assert game.returns() == (-1.0, 1.0), effect_id


def test_chaos_bomb_satisfied_condition_records_nothing():
    for effect_id, abyss_count in CHAOS_BOMBS_SATISFIED:
        game = make_game()
        state = game.state
        stock_abyss(game, 0, abyss_count)
        # 04-006 and 04-088 branch on the opponent's board after banking; an
        # empty battle zone and a short deck end them right after the bank.
        state.players[1].battle = -1
        del state.players[1].deck[1:]
        hp_before = state.players[0].hp
        run_effect_auto(game, 0, spawn(game, card_with_effect(effect_id)))

        assert state.players[0].hp == hp_before, effect_id
        assert state.self_defeat_player == -1, effect_id
        assert state.self_defeat_turn == -1, effect_id
        assert state.winner == -1, effect_id


def test_self_defeat_fields_default_to_unset():
    state = make_game().state
    assert state.self_defeat_player == -1
    assert state.self_defeat_turn == -1


def test_self_defeat_fields_survive_fast_clone():
    game = make_game()
    unset = game.state.fast_clone()
    assert (unset.self_defeat_player, unset.self_defeat_turn) == (-1, -1)

    run_effect_auto(game, 1, spawn(game, card_with_effect("04-027")))
    clone = game.state.fast_clone()
    assert clone.self_defeat_player == 1
    assert clone.self_defeat_turn == game.state.turn


def test_self_defeat_records_the_turn_it_happened_on():
    """The engine reports the real turn; the turn-1 Elo policy lives in the bot
    layer, so a later self-defeat must NOT be relabelled as turn 1."""
    game = make_game()
    game.state.turn = 7
    run_effect_auto(game, 0, spawn(game, card_with_effect("04-027")))
    assert game.state.self_defeat_player == 0
    assert game.state.self_defeat_turn == 7


def test_first_self_defeat_is_not_overwritten():
    game = make_game()
    game.state.turn = 1
    run_effect_auto(game, 0, spawn(game, card_with_effect("04-027")))
    game.state.turn = 9
    run_effect_auto(game, 1, spawn(game, card_with_effect("04-027")))
    assert game.state.self_defeat_player == 0
    assert game.state.self_defeat_turn == 1


def test_self_defeat_does_not_reach_the_observation():
    """Guards the trained PPO / AlphaZero checkpoints: the new state fields are
    bot-layer bookkeeping and must never change the NN input."""
    import numpy as np

    from engine_alpha.encoding.observation import encode

    game = make_game()
    before = encode(game)
    game.state.self_defeat_player = 1
    game.state.self_defeat_turn = 1
    after = encode(game)

    for array_before, array_after in zip(before[:3], after[:3]):
        assert array_before.shape == array_after.shape
        assert np.array_equal(array_before, array_after)
    assert before[3] == after[3]


# ---------------------------------------------------------------------------
# 04-105: bank 8 own-abyss cards face down to the deck bottom, then BOTH power
# chargers empty into their own owners' abysses. Falling short of 8 is an
# immediate self-defeat and nothing else resolves (confirmed ruling).
# ---------------------------------------------------------------------------

def _play_04_105(game: Game, owner: int = 0):
    """Resolve 04-105 for `owner`, always picking the first remaining card."""
    run_effect(game, owner, spawn(game, card_with_effect("04-105")),
               answers=(0,) * 8)


def test_04_105_banks_eight_and_wipes_both_chargers():
    game = make_game()
    state = game.state
    stock_abyss(game, 0, 10)
    own_charger = stock_charger(game, 0, 3)
    opponent_charger = stock_charger(game, 1, 4)
    state.players[1].abyss.clear()
    abyss_before = list(state.players[0].abyss)
    deck_size_before = len(state.players[0].deck)
    hp_before = state.players[0].hp

    _play_04_105(game)

    banked = state.players[0].deck[-8:]
    assert len(state.players[0].deck) == deck_size_before + 8
    assert set(banked) == set(abyss_before[:8]), "the 8 picked cards, in shuffled order"
    # Own abyss keeps the 2 unpicked cards and gains only its OWN charger.
    assert state.players[0].abyss == abyss_before[8:] + own_charger
    # The opponent's charger goes to the OPPONENT's abyss, not the caster's.
    assert state.players[1].abyss == opponent_charger
    assert state.players[0].charger == [] and state.players[1].charger == []
    # No self-defeat on the success branch.
    assert state.players[0].hp == hp_before
    assert state.self_defeat_player == -1


def test_04_105_exactly_eight_abyss_cards_is_enough():
    """Boundary of the abyss_count_ge gate: 8 is a success, 7 is a loss."""
    game = make_game()
    state = game.state
    stock_abyss(game, 0, 8)
    stock_charger(game, 0, 0)
    stock_charger(game, 1, 0)
    banked_from = list(state.players[0].abyss)

    _play_04_105(game)

    assert state.players[0].abyss == []
    assert set(state.players[0].deck[-8:]) == set(banked_from)
    assert state.self_defeat_player == -1


def test_04_105_banked_cards_are_face_down():
    game = make_game()
    state = game.state
    stock_abyss(game, 0, 8)
    for instance_id in state.players[0].abyss:
        state.inst_face_up[instance_id] = 1  # abyss cards are face up

    _play_04_105(game)

    assert all(state.inst_face_up[i] == 0 for i in state.players[0].deck[-8:])


def test_04_105_wiped_charger_cards_land_face_up():
    game = make_game()
    state = game.state
    stock_abyss(game, 0, 8)
    charger = stock_charger(game, 0, 3)
    stock_charger(game, 1, 0)

    _play_04_105(game)

    assert all(state.inst_face_up[i] == 1 for i in charger)


def test_04_105_shuffle_is_one_deterministic_rng_event():
    def bank(seed_state_setup) -> tuple[list[int], int, int]:
        game = make_game()
        state = game.state
        stock_abyss(game, 0, 8)
        stock_charger(game, 0, 0)
        stock_charger(game, 1, 0)
        counter_before = state.rng_ctr
        _play_04_105(game)
        return list(state.players[0].deck[-8:]), counter_before, state.rng_ctr

    first, counter_before, counter_after = bank(None)
    second, _, _ = bank(None)
    assert counter_after == counter_before + 1, "exactly one shuffle chance event"
    assert first == second, "same seed and counter reproduce the same bottom order"


def test_04_105_short_abyss_loses_and_leaves_both_chargers_alone():
    from engine_alpha.battle import check_win

    game = make_game()
    state = game.state
    state.turn = 1
    stock_abyss(game, 0, 7)
    own_charger = stock_charger(game, 0, 3)
    opponent_charger = stock_charger(game, 1, 4)
    deck_size_before = len(state.players[0].deck)

    run_effect_auto(game, 0, spawn(game, card_with_effect("04-105")))

    assert state.players[0].hp == 0
    assert state.self_defeat_player == 0 and state.self_defeat_turn == 1
    # Confirmed ruling: the loss ends it; the charger wipe does not happen.
    assert state.players[0].charger == own_charger
    assert state.players[1].charger == opponent_charger
    assert len(state.players[0].abyss) == 7
    assert len(state.players[0].deck) == deck_size_before
    check_win(state)
    assert state.winner == 1
    assert game.returns() == (-1.0, 1.0)


def test_04_105_empty_abyss_loses_without_prompting():
    game = make_game()
    state = game.state
    stock_abyss(game, 0, 0)
    charger = stock_charger(game, 0, 2)

    # No answers supplied: a prompt would raise IndexError popping the queue.
    run_effect(game, 0, spawn(game, card_with_effect("04-105")))

    assert state.players[0].hp == 0
    assert state.players[0].charger == charger


def test_04_105_empty_chargers_resolve_cleanly():
    game = make_game()
    state = game.state
    stock_abyss(game, 0, 8)
    stock_charger(game, 0, 0)
    stock_charger(game, 1, 0)

    _play_04_105(game)

    assert state.players[0].charger == [] and state.players[1].charger == []
    assert state.self_defeat_player == -1


def test_04_105_banks_onto_an_empty_deck():
    game = make_game()
    state = game.state
    stock_abyss(game, 0, 8)
    banked_from = list(state.players[0].abyss)
    state.players[0].deck.clear()

    _play_04_105(game)

    assert sorted(state.players[0].deck) == sorted(banked_from)


def test_04_105_charger_wipe_fires_the_right_placement_flags():
    """place_in_abyss with actor = the caster: PF_ABYSS_RECEIVED is
    location-based (both owners), PF_OPP_CARD_TO_ABYSS is agent-based (only
    the side whose opponent acted), and nothing reaches the charger."""
    game = make_game()
    state = game.state
    stock_abyss(game, 0, 8)
    stock_charger(game, 0, 2)
    stock_charger(game, 1, 2)
    clear_placement_flags(game)

    _play_04_105(game)

    assert state.players[0].flags[PF_ABYSS_RECEIVED] == 1
    assert state.players[1].flags[PF_ABYSS_RECEIVED] == 1
    assert state.players[1].flags[PF_OPP_CARD_TO_ABYSS] == 1
    assert state.players[0].flags[PF_OPP_CARD_TO_ABYSS] == 0
    assert state.players[0].flags[PF_CARD_TO_POWER] == 0
    assert state.players[1].flags[PF_CARD_TO_POWER] == 0
    assert state.players[0].flags[PF_CHAR_TO_POWER] == 0


def test_04_105_leaves_both_players_at_zero_power():
    game = make_game()
    state = game.state
    stock_abyss(game, 0, 8)
    stock_charger(game, 0, 3)
    stock_charger(game, 1, 3)

    _play_04_105(game)

    assert total_power(state, state.players[0]) == 0
    assert total_power(state, state.players[1]) == 0


def test_04_105_wipe_protects_an_opponent_area_from_its_own_removal():
    """Emergent but deliberate: 04-030 self-removes once the opponent's abyss
    receives a card, and the wipe does exactly that -- but the same wipe drops
    its owner to 0 power, and check_area_removal never removes an area whose
    power cost is unmet. Restoring power removes it."""
    game = make_game()
    state = game.state
    area = _setup_area(game, 1, "04-030")
    stock_abyss(game, 0, 8)
    # 04-030 keys on ITS opponent's abyss receiving a card, so the caster needs
    # a charger of their own for the wipe to feed that abyss at all.
    stock_charger(game, 0, 2)
    clear_placement_flags(game)

    _play_04_105(game)

    assert state.players[0].flags[PF_ABYSS_RECEIVED] == 1, "removal condition is met"
    check_area_removal(state)
    assert state.players[1].set_c == area, "unmet power cost blocks the removal"

    stock_charger(game, 1, 0)
    for def_index in [d.index for d in cards.CARD_DB if d.send_to_power == 2][:5]:
        state.players[1].charger.append(spawn(game, def_index))
    check_area_removal(state)
    assert state.players[1].set_c == -1, "with power restored it removes itself"


def test_04_105_pause_encodes_every_candidate_for_the_pointer_head():
    """observation.encode truncates each zone at 20 cards and then looks every
    SELECT_CARD candidate up in that token map, so a candidate past the cut
    would be a KeyError mid-self-play. A player only ever owns deck_size (20)
    instances -- and the wipe keeps each card in its own owner's abyss -- so
    the abyss can never exceed the window. This pins that."""
    from engine_alpha.encoding.observation import encode

    game = make_game()
    state = game.state
    stock_abyss(game, 0, 20)  # the real worst case: a player's whole card pool
    stock_charger(game, 0, 0)

    interpreter.start_effect(state, 0, spawn(game, card_with_effect("04-105")),
                             fx("04-105"))
    request = interpreter.resume(state, None, None)
    assert request is not None and len(request.candidates) == 20
    state.pending = request

    _, _, _, candidate_positions = encode(game)
    assert len(candidate_positions) == len(request.candidates)

    # Drain the effect and confirm neither abyss outgrows the encoder window.
    for _ in range(8):
        request = interpreter.resume(state, request, 0)
    state.pending = None
    assert all(len(player.abyss) <= 20 for player in state.players)


def test_04_105_paused_frame_survives_a_clone_mid_pick():
    """MCTS clones at every decision. 04-105 carries the catalog's deepest
    pick list (8), so the Frame's step/data deep-copy has to hold."""
    def drive(state, request, answers):
        for answer in answers:
            request = interpreter.resume(state, request, answer)
        assert request is None and not state.frame_stack
        return ([list(p.deck) for p in state.players],
                [list(p.abyss) for p in state.players],
                [list(p.charger) for p in state.players])

    game = make_game()
    state = game.state
    stock_abyss(game, 0, 12)
    stock_charger(game, 0, 2)
    stock_charger(game, 1, 2)

    interpreter.start_effect(state, 0, spawn(game, card_with_effect("04-105")),
                             fx("04-105"))
    request = interpreter.resume(state, None, None)
    request = interpreter.resume(state, request, 0)  # answer 3 of the 8 picks
    request = interpreter.resume(state, request, 0)
    request = interpreter.resume(state, request, 0)

    clone = state.fast_clone()
    assert clone.frame_stack[-1].data is not state.frame_stack[-1].data

    remaining = (0, 0, 0, 0, 0)
    assert drive(state, request, remaining) == drive(clone, request, remaining)


# ---------------------------------------------------------------------------
# 04-106: advance chronos by 9 -- exactly half of the 18-slot clock, so it
# always crosses day/night exactly once.
# ---------------------------------------------------------------------------

def test_04_106_advances_nine_from_every_start_and_flips_day_night():
    from engine_alpha.state import GF_DAY_TO_NIGHT, GF_NIGHT_TO_DAY

    for start in range(18):
        game = make_game()
        state = game.state
        state.chronos = start
        state.gflags[GF_DAY_TO_NIGHT] = 0
        state.gflags[GF_NIGHT_TO_DAY] = 0
        was_night = state.is_night

        run_effect(game, 0, spawn(game, card_with_effect("04-106")))

        assert state.chronos == (start + 9) % 18, start
        assert state.is_night != was_night, f"9 of 18 always flips at {start}"
        if was_night:
            assert state.gflags[GF_NIGHT_TO_DAY] == 1, start
            assert state.gflags[GF_DAY_TO_NIGHT] == 0, start
        else:
            assert state.gflags[GF_DAY_TO_NIGHT] == 1, start
            assert state.gflags[GF_NIGHT_TO_DAY] == 0, start


def test_04_106_wraps_past_midnight():
    game = make_game()
    state = game.state
    state.chronos = 17

    run_effect(game, 0, spawn(game, card_with_effect("04-106")))

    assert state.chronos == 8 and state.is_night


def test_04_106_does_not_touch_the_turn_phase_clock_bookkeeping():
    from engine_alpha.state import PF_CHRONOS_ADVANCED

    game = make_game()
    state = game.state
    state.chronos = 4
    state.chronos_at_turn_start = 4
    state.players[0].flags[PF_CHRONOS_ADVANCED] = 2

    run_effect(game, 0, spawn(game, card_with_effect("04-106")))

    assert state.chronos_at_turn_start == 4
    assert state.players[0].flags[PF_CHRONOS_ADVANCED] == 2


def test_04_106_stacks_on_top_of_the_turn_clock_advance():
    """PH_ADVANCE_CHRONOS runs before PH_PROCESS_EFFECTS, so the +9 lands on
    the clock the played cards already moved."""
    from engine_alpha.battle import advance_chronos_by
    from engine_alpha.state import PH_ADVANCE_CHRONOS, PH_PROCESS_EFFECTS

    assert PH_ADVANCE_CHRONOS < PH_PROCESS_EFFECTS

    game = make_game()
    state = game.state
    state.chronos = 0
    advance_chronos_by(state, 2)   # the turn phase's card clocks
    run_effect(game, 0, spawn(game, card_with_effect("04-106")))

    assert state.chronos == 11


def test_04_106_resolves_when_borrowed_from_the_abyss_by_01_006():
    """04-106 is an effect-bearing ENCHANT, so 01-006 can replay it out of the
    abyss -- which resolves it as a nested frame."""
    game = make_game()
    state = game.state
    state.chronos = 0
    stock_abyss(game, 0, 3)  # characters only, so the enchant is the sole pick
    borrowed = spawn(game, card_with_effect("04-106"))
    state.players[0].abyss.append(borrowed)

    run_effect(game, 0, spawn(game, card_with_effect("01-006")), answers=(0,))

    assert state.chronos == 9


# ---------------------------------------------------------------------------
# 04-107: the opponent's area enchant goes to THEIR abyss -- forced, even when
# the card has SEND TO POWER, and firing the leave-play cleanup.
# ---------------------------------------------------------------------------

def test_04_107_sends_a_send_to_power_area_to_the_abyss_anyway():
    game = make_game()
    state = game.state
    area = _setup_area(game, 1, "04-030")
    assert cards.SEND_TO_POWER_T[state.inst_def[area]] == 1, \
        "this card is only interesting because normal routing would send it to power"

    run_effect(game, 0, spawn(game, card_with_effect("04-107")))

    assert state.players[1].set_c == -1
    assert area in state.players[1].abyss
    assert area not in state.players[1].charger
    assert state.inst_face_up[area] == 1


def test_04_107_is_a_clean_no_op_without_an_opponent_area():
    game = make_game()
    state = game.state
    state.players[1].set_c = -1
    abyss_before = list(state.players[1].abyss)

    run_effect(game, 0, spawn(game, card_with_effect("04-107")))

    assert state.players[1].set_c == -1
    assert state.players[1].abyss == abyss_before


def test_04_107_clears_a_03_055_area_block():
    """The leave-play cleanup is the reason this needs its own op: moving the
    card out of set_c with move_reg would strand area_blocked forever."""
    game = make_game()
    state = game.state
    blocker = _setup_area(game, 1, "03-055")
    run_effect(game, 1, blocker)
    assert state.players[0].area_blocked is True

    run_effect(game, 0, spawn(game, card_with_effect("04-107")))

    assert state.players[1].set_c == -1
    assert state.players[0].area_blocked is False


def test_04_107_leaves_the_casters_own_area_alone():
    game = make_game()
    state = game.state
    own_area = _setup_area(game, 0, "04-033")
    opponent_area = _setup_area(game, 1, "04-030")

    run_effect(game, 0, spawn(game, card_with_effect("04-107")))

    assert state.players[0].set_c == own_area
    assert state.players[1].set_c == -1
    assert opponent_area in state.players[1].abyss


def test_04_107_fires_the_right_placement_flags():
    game = make_game()
    state = game.state
    _setup_area(game, 1, "04-030")
    clear_placement_flags(game)

    run_effect(game, 0, spawn(game, card_with_effect("04-107")))

    assert state.players[1].flags[PF_ABYSS_RECEIVED] == 1
    assert state.players[1].flags[PF_OPP_CARD_TO_ABYSS] == 1
    assert state.players[0].flags[PF_ABYSS_RECEIVED] == 0
    assert state.players[0].flags[PF_OPP_CARD_TO_ABYSS] == 0


def test_04_107_second_copy_in_a_turn_is_a_no_op():
    game = make_game()
    state = game.state
    area = _setup_area(game, 1, "04-030")

    run_effect(game, 0, spawn(game, card_with_effect("04-107")))
    abyss_after_first = list(state.players[1].abyss)
    run_effect(game, 0, spawn(game, card_with_effect("04-107")))

    assert state.players[1].abyss == abyss_after_first
    assert abyss_after_first.count(area) == 1


def test_04_107_resolves_when_borrowed_from_the_abyss_by_01_006():
    game = make_game()
    state = game.state
    area = _setup_area(game, 1, "04-030")
    stock_abyss(game, 0, 3)
    borrowed = spawn(game, card_with_effect("04-107"))
    state.players[0].abyss.append(borrowed)

    run_effect(game, 0, spawn(game, card_with_effect("01-006")), answers=(0,))

    assert state.players[1].set_c == -1
    assert area in state.players[1].abyss


# ---------------------------------------------------------------------------
# Featurizer completeness: a verb missing from features._OP_VERBS is dropped
# silently rather than raising, so the network just loses the signal. Assert
# full coverage instead of trusting the two lists to stay in sync by hand.
# ---------------------------------------------------------------------------

def test_featurizer_covers_every_op_and_condition():
    from engine_alpha.effects import catalog, features

    assert set(interpreter.OP_TABLE) == set(features._OP_VERBS)
    condition_leaves = catalog._COND_NAMES - {"and", "or", "not"}
    assert condition_leaves == set(features._COND_KINDS)


def test_new_effects_are_featurized():
    from engine_alpha.effects import features

    assert features.EFFECT_FEATURES.shape == (cards.NUM_EFFECTS + 1,
                                              features.FEATURE_DIM)
    assert not features.EFFECT_FEATURES[-1].any(), "the no-effect row stays zero"

    def verbs(effect_id: str) -> set[str]:
        row = features.EFFECT_FEATURES[fx(effect_id)]
        return {verb for index, verb in enumerate(features._OP_VERBS)
                if row[features.OP_VERB + index]}

    assert {"picks_exact", "charger_to_abyss", "lose_game"} <= verbs("04-105")
    assert verbs("04-106") == {"adv_chronos"}
    assert verbs("04-107") == {"opp_area_to_abyss"}

    row_105 = features.EFFECT_FEATURES[fx("04-105")]
    assert row_105[features.MAG_BANK_COUNT] == 1.0, "banks 8 of the 8-card scale"
    assert row_105[features.TARGET_MOVES_OPP] == 1.0
    assert features.EFFECT_FEATURES[fx("04-106")][features.MAG_CHRONOS] == 0.5
    assert features.EFFECT_FEATURES[fx("04-107")][features.TARGET_MOVES_OPP] == 1.0


# ---------------------------------------------------------------------------
# 04-105 power starvation.
#
# The wipe empties both chargers during the effects phase. Because
# _ph_process_effects resolves the priority player's whole batch before it
# even calls _collect_eligible for the other side, and because
# _dispatch_with_cost_check reads total_power FRESH at each individual
# dispatch, a priority-side 04-105 leaves the opponent unable to pay for
# anything they had queued. Power is read live everywhere else too, so the
# same wipe zeroes an unaffordable battle character's attack and switches off
# power-gated area passives for the rest of the turn.
#
# These are the only tests that drive the real PROCESS_EFFECTS phase: every
# other ruling test calls interpreter.start_effect directly and so never
# reaches _collect_eligible or _dispatch_with_cost_check at all.
# ---------------------------------------------------------------------------

def give_priority(game: Game, player_index: int) -> None:
    """Move the clock to the half that gives `player_index` priority."""
    state = game.state
    wants_night = state.players[player_index].side_is_night
    state.chronos = MIDNIGHT if wants_night else 13
    assert state.priority_player == player_index


def run_process_effects(game: Game, answers=()) -> list[tuple]:
    """Drive the real PROCESS_EFFECTS phase and return the events it emitted.

    Covers what run_effect / run_effect_auto skip: both players' batches in
    priority order, the P_EFFECT_ORDER prompt, and the per-effect power gate.
    `answers` is consumed in order; anything past it takes the first legal
    action.
    """
    state = game.state
    state.event_sink = []
    state.phase = PH_PROCESS_EFFECTS
    state.phase_ctx = [0, state.priority_player, 0, [], [], 0, 0]
    # make_game leaves a pending setup decision behind; drop it so the first
    # scripted answer goes to this phase's own first prompt.
    state.pending = None
    game._answer = None
    game._answered_request = None
    game._advance()
    queue = list(answers)
    while state.phase == PH_PROCESS_EFFECTS and not game.is_terminal():
        request = game.decision_context()
        if request is None:
            break
        game.apply(queue.pop(0) if queue else request.legal_actions()[0])
    return list(state.event_sink)


def started_definitions(events) -> set[int]:
    return {event[2] for event in events if event[0] == EVENT_EFFECT_STARTED}


def cost_skipped_definitions(events) -> set[int]:
    return {event[2] for event in events if event[0] == EVENT_EFFECT_SKIPPED_COST}


def costed_enchant(power_cost: int = 4) -> int:
    """A dispatchable ENCHANT whose effect costs exactly `power_cost`."""
    from engine_alpha.effects.dispatch import HANDLED_EFFECTS
    for d in cards.CARD_DB:
        if (d.card_type == cards.TYPE_ENCHANT and d.power_cost == power_cost
                and d.effect_index in HANDLED_EFFECTS):
            return d.index
    raise AssertionError(f"no dispatchable enchant costing {power_cost}")


def fill_charger_to(game: Game, owner: int, send_to_power_cards: int) -> None:
    """Give a player `send_to_power_cards` x 2 power worth of charger."""
    player = game.state.players[owner]
    player.charger.clear()
    stp2 = [d.index for d in cards.CARD_DB if d.send_to_power == 2]
    for def_index in stp2[:send_to_power_cards]:
        player.charger.append(spawn(game, def_index))


def _arm_04_105(game: Game, owner: int) -> int:
    """Put a playable 04-105 in `owner`'s battle zone with a full abyss."""
    state = game.state
    player = state.players[owner]
    stock_abyss(game, owner, 8)
    bomb = spawn(game, card_with_effect("04-105"))
    player.battle = bomb
    state.inst_played[bomb] = 1
    return bomb


def _arm_costed_enchant(game: Game, owner: int, slot: str = "set_a") -> int:
    state = game.state
    player = state.players[owner]
    enchant = spawn(game, costed_enchant())
    setattr(player, slot, enchant)
    state.inst_played[enchant] = 1
    return enchant


def test_04_105_on_priority_starves_the_opponents_queued_effects():
    """The ruling: the priority player's wipe lands before the opponent's
    batch is even collected, so their costed effect can no longer be paid."""
    game = make_game()
    state = game.state
    give_priority(game, 0)
    bomb = _arm_04_105(game, 0)
    enchant = _arm_costed_enchant(game, 1)
    state.players[1].set_c = -1
    state.players[1].battle = -1
    fill_charger_to(game, 1, 4)   # 8 power, comfortably covers the cost 4
    assert total_power(state, state.players[1]) == 8

    events = run_process_effects(game)

    assert state.inst_def[bomb] in started_definitions(events)
    assert state.inst_def[enchant] in cost_skipped_definitions(events)
    assert state.inst_def[enchant] not in started_definitions(events)
    assert total_power(state, state.players[1]) == 0


def test_04_105_without_priority_the_opponent_resolves_first_and_pays():
    """Control for the test above: same board, priority flipped. The opponent's
    batch now runs before the wipe, so their effect does fire -- which is what
    makes the previous test a statement about ordering rather than about the
    cost gate alone."""
    game = make_game()
    state = game.state
    give_priority(game, 1)
    bomb = _arm_04_105(game, 0)
    enchant = _arm_costed_enchant(game, 1)
    state.players[1].set_c = -1
    state.players[1].battle = -1
    fill_charger_to(game, 1, 4)

    events = run_process_effects(game)

    assert state.inst_def[enchant] in started_definitions(events)
    assert state.inst_def[enchant] not in cost_skipped_definitions(events)
    assert state.inst_def[bomb] in started_definitions(events)
    assert total_power(state, state.players[1]) == 0, "the wipe still lands, just later"


def test_04_105_starves_its_own_caster_when_ordered_first():
    """The wipe is symmetric: it empties the caster's charger too, so their own
    queued costed effect is skipped when 04-105 resolves first."""
    game = make_game()
    state = game.state
    give_priority(game, 0)
    bomb = _arm_04_105(game, 0)
    own_enchant = _arm_costed_enchant(game, 0)
    state.players[0].set_c = -1
    state.players[1].set_a = state.players[1].set_b = -1
    state.players[1].set_c = state.players[1].battle = -1
    fill_charger_to(game, 0, 4)

    # Two eligible effects -> a P_EFFECT_ORDER prompt. Its candidates are
    # [set_a enchant, battle character], so action 1 resolves 04-105 first.
    events = run_process_effects(game, answers=(1,))

    assert state.inst_def[bomb] in started_definitions(events)
    assert state.inst_def[own_enchant] in cost_skipped_definitions(events)
    assert state.inst_def[own_enchant] not in started_definitions(events)


def test_04_105_caster_can_order_their_own_effect_first_to_keep_it():
    """The same board with the other ordering answer: resolving the enchant
    before the bomb pays for it out of the charger that is about to vanish."""
    game = make_game()
    state = game.state
    give_priority(game, 0)
    bomb = _arm_04_105(game, 0)
    own_enchant = _arm_costed_enchant(game, 0)
    state.players[0].set_c = -1
    state.players[1].set_a = state.players[1].set_b = -1
    state.players[1].set_c = state.players[1].battle = -1
    fill_charger_to(game, 0, 4)

    events = run_process_effects(game, answers=(0,))

    started = started_definitions(events)
    assert state.inst_def[own_enchant] in started
    assert state.inst_def[bomb] in started
    assert not cost_skipped_definitions(events)


def test_04_105_wipe_zeroes_an_unaffordable_battle_characters_attack():
    """battle.get_effective_attack reads total_power live, so the wipe strips
    the opponent's attack for the battle that follows in the same turn."""
    game = make_game()
    state = game.state
    opponent = state.players[1]
    stock_abyss(game, 0, 8)   # so the bomb banks and wipes instead of losing
    costly = next(d for d in cards.CARD_DB
                  if d.card_type == cards.TYPE_CHARACTER and d.power_cost >= 4
                  and max(d.attack_day, d.attack_night) > 0)
    battle_instance = spawn(game, costly.index)
    opponent.battle = battle_instance
    state.inst_played[battle_instance] = 1
    fill_charger_to(game, 1, 4)
    assert get_effective_attack(state, opponent) > 0

    run_effect(game, 0, spawn(game, card_with_effect("04-105")), answers=(0,) * 8)

    assert total_power(state, opponent) == 0
    assert get_effective_attack(state, opponent) == 0


def test_04_105_wipe_switches_off_a_power_gated_area_passive():
    """area_enchant_active also re-reads power, so 02-007's force-day passive
    stops applying the moment the charger is emptied."""
    from engine_alpha.battle import force_day_active

    game = make_game()
    state = game.state
    stock_abyss(game, 0, 8)   # so the bomb banks and wipes instead of losing
    _setup_area(game, 1, "02-007")
    assert force_day_active(state, 1) is True

    run_effect(game, 0, spawn(game, card_with_effect("04-105")), answers=(0,) * 8)

    assert force_day_active(state, 1) is False


def test_power_bonus_survives_the_wipe_for_enchants_but_not_for_areas():
    """The one escape hatch that exists: PF_POWER_BONUS standing from earlier
    in the turn still pays for an ENCHANT at zero charger power, but never for
    an AREA_ENCHANT -- _dispatch_with_cost_check deliberately omits the bonus
    on the area branch, matching battle.area_enchant_active."""
    game = make_game()
    state = game.state
    give_priority(game, 0)
    _arm_04_105(game, 0)
    state.players[0].set_c = -1   # so 04-032 has no reason to self-remove
    opponent = state.players[1]
    enchant = _arm_costed_enchant(game, 1)
    area = _setup_area(game, 1, "04-032", power=False)
    opponent.battle = -1
    fill_charger_to(game, 1, 4)

    enchant_cost = cards.POWER_COST_T[state.inst_def[enchant]]
    area_cost = cards.POWER_COST_T[state.inst_def[area]]
    assert enchant_cost > 0 and area_cost > 0, \
        "both must actually cost power or this test proves nothing"
    opponent.flags[PF_POWER_BONUS] = max(enchant_cost, area_cost)

    events = run_process_effects(game)

    assert total_power(state, opponent) == 0
    assert state.inst_def[enchant] in started_definitions(events), \
        "the standing bonus pays for the enchant"
    assert state.inst_def[area] in cost_skipped_definitions(events), \
        "areas are gated on charger power alone and never get the bonus"
    assert state.inst_def[area] not in started_definitions(events)


def test_no_zero_cost_effect_can_refill_a_wiped_charger():
    """Tripwire for the 'unless something introduces power this turn' clause:
    every effect that adds charger power or a power bonus is itself power
    costed, so a full wipe cannot be undone inside the effects phase.

    If a future card breaks this, that is a design change to re-rule against
    04-105 -- not a bug to patch here.
    """
    from engine_alpha.effects.catalog import CATALOG
    from engine_alpha.effects.dispatch import HANDLED_EFFECTS

    offenders = []
    for effect_id, entry in CATALOG.items():
        adds_power = any(
            op[0] in ("deck_top_route", "power_bonus")
            or (op[0] == "move_reg" and op[2] == "charger")
            for op in entry.ops)
        if not adds_power:
            continue
        carrier = cards.CARD_DB[cards.EFFECT_TO_CARD[fx(effect_id)]]
        if carrier.power_cost == 0 and fx(effect_id) in HANDLED_EFFECTS:
            offenders.append(effect_id)

    assert offenders == [], (
        f"{offenders} add power at zero cost, so a wiped charger could refill "
        "mid-phase; the 04-105 starvation ruling needs revisiting")
