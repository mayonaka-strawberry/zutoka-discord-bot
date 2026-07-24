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
from engine_alpha.game import Game
from engine_alpha.state import (
    GF_MIDNIGHT_EXTENDED,
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
CHAOS_BOMBS = (("04-006", 3), ("04-027", 0), ("04-028", 5), ("04-088", 0))
# The same cards with their requirement exactly satisfied.
CHAOS_BOMBS_SATISFIED = (("04-006", 4), ("04-027", 1), ("04-028", 6), ("04-088", 1))


def stock_abyss(game: Game, owner: int, count: int) -> None:
    """Put `count` throwaway cards in a player's abyss."""
    player = game.state.players[owner]
    player.abyss.clear()
    for _ in range(count):
        player.abyss.append(spawn(game, find_character()))


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
