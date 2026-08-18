"""Explicit tests for every confirmed ruling the new engine must reproduce.

These build surgical states (bypassing normal play) and invoke the specific
rules functions, verifying each documented ruling from the plan:
- cost reducers 02-006/04-065 forced-first and stacking
- 03-058/03-085 count ALL damage (battle+effect), self-remove at >=30, power-gated
- 04-032 self-removal can pre-empt its own resolution
- placement trigger matrix (agent-based vs location-based)
- 03-026 widens midnight +/-2 for all midnight conditions
- 04-095 keys on the battle loss itself, not damage
- attack: power gate > 02-007 force-day > 01-005 reversal for the starting
  value, then this turn's modifiers folded on in resolution order
"""

from __future__ import annotations

import random

from engine_alpha import cards
from engine_alpha.actions import P_EFFECT_ORDER
from engine_alpha.battle import (
    MIDNIGHT, base_attack, deal_damage, get_effective_attack,
    is_effectively_midnight, resolve_battle, total_power,
)
from engine_alpha.effects import interpreter
from engine_alpha.effects.removal import check_area_removal
from engine_alpha.effects.turn_end import process_end_of_turn_effects
from engine_alpha.events import (
    EVENT_BATTLE_RESULT, EVENT_EFFECT_SKIPPED_COST, EVENT_EFFECT_STARTED,
)
from engine_alpha.game import Game
from engine_alpha.state import (
    GF_MIDNIGHT_EXTENDED, PH_PROCESS_EFFECTS,
    PF_ABYSS_RECEIVED, PF_ATTACK_BONUS, PF_BATTLE_LOST,
    PF_CARD_TO_POWER, PF_CHAR_TO_POWER, PF_DAMAGE_REDUCTION, PF_DAMAGE_TAKEN,
    PF_DAY_NIGHT_REVERSED, PF_END_OF_TURN_DAMAGE, PF_OPP_CARD_TO_ABYSS,
    PF_POWER_BONUS,
    add_attack_modifier, set_attack_modifier,
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
    # 20 effect damage + 15 "battle" damage accumulated = 35 >= 30 -> removed.
    # The card text says すぐに, so the removal happens at the immediate
    # check_area_removal checkpoint rather than at turn end (Q&A No.16).
    deal_damage(state, 0, 20)
    state.players[0].flags[PF_DAMAGE_TAKEN] += 15  # as the battle resolver does
    check_area_removal(state)
    assert state.players[0].set_c == -1
    assert area in state.players[0].abyss


def test_03_058_heals_below_threshold():
    game = make_game()
    state = game.state
    _setup_area(game, 0, "03-058")
    state.players[0].hp = 50
    state.players[1].hp = 60
    deal_damage(state, 0, 20)  # below 30: stays and heals both players
    hp_0_before_heal = state.players[0].hp
    process_end_of_turn_effects(state)
    assert state.players[0].set_c != -1
    assert state.players[0].hp == hp_0_before_heal + 10
    assert state.players[1].hp == 70


def test_qa_26_each_03_058_copy_heals_independently():
    """Q&A No.26 is the duplicate-copies ruling: 「はい、２枚のカードの効果は重なります」.

    With both players holding a 03-058 there are two distinct area enchants, so the
    heal happens twice and each player gains 20 rather than 10. 「お互いの」 in the card
    text says who is healed, not how often. User-confirmed 2026-08-14, replacing an
    engine-only cap of once per window that had no source behind it.
    """
    game = make_game()
    state = game.state
    _setup_area(game, 0, "03-058")
    _setup_area(game, 1, "03-058")
    state.players[0].hp = 50
    state.players[1].hp = 50

    process_end_of_turn_effects(state)
    assert state.players[0].hp == 70, 'both copies heal player 0'
    assert state.players[1].hp == 70, 'both copies heal player 1'


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
    add_attack_modifier(state.players[1], 50)  # P1 wins
    resolve_battle(state)
    assert state.players[0].flags[PF_BATTLE_LOST] == 1
    assert state.players[0].hp == 100  # damage fully reduced
    check_area_removal(state)
    assert state.players[0].set_c == -1  # removed despite zero damage


# ---------------------------------------------------------------------------
# Attack computation: power gate > force-day > reversal > base, then this
# turn's modifiers folded on in resolution order
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
    add_attack_modifier(player, 25)
    assert get_effective_attack(state, player) == d.attack_night + 25
    # 5. A 04-099 set resolving after the bonus discards it (Q&A No.82: the set
    #    is point-in-time, not a lock).
    set_attack_modifier(player, 100)
    assert get_effective_attack(state, player) == 100
    # 6. A bonus resolving after the set is added to it, not swallowed.
    add_attack_modifier(player, 25)
    assert get_effective_attack(state, player) == 125
    # 7. Losing the power cost zeroes the whole fold, set included: Ground Rules
    #    2.3.6/7.1.2 and Q&A No.40/73 make an unpayable character unable to
    #    attack at all, and Q&A No.82 puts the set in the same modifier
    #    sequence as the bonuses.
    player.charger.clear()
    assert get_effective_attack(state, player) == 0


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
# _COND_KINDS is positional, so a retired condition keeps its slot rather than
# being deleted; those are declared in features._RETIRED_COND_KINDS.
# ---------------------------------------------------------------------------

def test_featurizer_covers_every_op_and_condition():
    from engine_alpha.effects import catalog, features

    assert set(interpreter.OP_TABLE) == set(features._OP_VERBS)
    condition_leaves = catalog._COND_NAMES - {"and", "or", "not"}
    featurized = set(features._COND_KINDS)
    assert condition_leaves <= featurized, "live condition missing from the featurizer"
    assert featurized - condition_leaves == set(features._RETIRED_COND_KINDS)


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


def run_process_effects(game: Game, answers=(), order_first: int = -1) -> list[tuple]:
    """Drive the real PROCESS_EFFECTS phase and return the events it emitted.

    Covers what run_effect / run_effect_auto skip: both players' batches in
    priority order, the P_EFFECT_ORDER prompt, and the per-effect power gate.
    `answers` is consumed in order; anything past it takes the first legal
    action. `order_first`, when given, answers the P_EFFECT_ORDER prompt that
    offers that instance by resolving it first.
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
    ordered_as_asked = False
    while state.phase == PH_PROCESS_EFFECTS and not game.is_terminal():
        request = game.decision_context()
        if request is None:
            break
        if (order_first != -1 and request.purpose == P_EFFECT_ORDER
                and order_first in request.candidates):
            action = request.candidates.index(order_first)
            ordered_as_asked = True
        elif queue:
            action = queue.pop(0)
        else:
            action = request.legal_actions()[0]
        game.apply(action)
    assert order_first == -1 or ordered_as_asked, "no ordering prompt offered it"
    return list(state.event_sink)


def started_definitions(events) -> set[int]:
    return {event[2] for event in events if event[0] == EVENT_EFFECT_STARTED}


def battle_attacks(events) -> tuple[int, int]:
    """The (player 0, player 1) attack values of the battle these effects fed.

    run_process_effects runs out of decisions inside the effects phase, so the
    driver carries on through BATTLE and END_TURN -- which clears attack_mods.
    Read the outcome off the event instead of the post-turn state.
    """
    for event in events:
        if event[0] == EVENT_BATTLE_RESULT:
            return event[1], event[2]
    raise AssertionError("no battle was resolved")


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


# ---------------------------------------------------------------------------
# Attack modifiers fold in resolution order (official Q&A No.40, No.54, No.60,
# No.68, No.82).
#
# Modifiers are neither summed into one total nor collapsed into a number when
# they resolve: they are kept in order and folded onto the live base at battle
# time, clamped to >=0 after every step. 04-099's "set the opponent's attack to
# 100" is one of those entries, so whether it wipes a bonus or is added to
# depends entirely on which side had priority.
# ---------------------------------------------------------------------------

SONG_NEKO_RESET = cards.SONG_NAMES.index("NEKO_RESET")
SONG_SHADE = cards.SONG_NAMES.index("SHADE")


def character_with_song(song_index: int, max_power_cost: int = 8) -> int:
    for d in cards.CARD_DB:
        if (d.card_type == cards.TYPE_CHARACTER and d.song == song_index
                and d.power_cost <= max_power_cost):
            return d.index
    raise AssertionError(f"no character of song {cards.SONG_NAMES[song_index]}")


def character_with_attack(state, attack: int, max_power_cost: int = 0) -> int:
    """A character printing exactly `attack` for the current half of the clock."""
    for d in cards.CARD_DB:
        if d.card_type != cards.TYPE_CHARACTER or d.power_cost > max_power_cost:
            continue
        if (d.attack_night if state.is_night else d.attack_day) == attack:
            return d.index
    raise AssertionError(f"no character attacking for {attack}")


def put_in_battle(game: Game, owner: int, def_index: int) -> int:
    player = game.state.players[owner]
    if player.battle != -1:
        player.abyss.append(player.battle)
    player.battle = spawn(game, def_index)
    return player.battle


def fill_charger_with_attribute(game: Game, owner: int, attribute: int,
                                count: int) -> None:
    """Replace `owner`'s charger with `count` SEND TO POWER 2 cards of one
    attribute, so the zone supplies both the attribute count and 2*count power."""
    player = game.state.players[owner]
    player.charger.clear()
    matching = [d.index for d in cards.CARD_DB
                if d.attribute == attribute and d.send_to_power == 2]
    assert len(matching) >= count, f"not enough attribute-{attribute} power cards"
    for def_index in matching[:count]:
        player.charger.append(spawn(game, def_index))


def _arm_enchant(game: Game, owner: int, effect_id: str, slot: str = "set_a") -> int:
    """Put a played-this-turn enchant carrying `effect_id` into `owner`'s slot."""
    state = game.state
    enchant = spawn(game, card_with_effect(effect_id))
    setattr(state.players[owner], slot, enchant)
    state.inst_played[enchant] = 1
    return enchant


def _arm_job_change_against_unigiri_kororin(game: Game, caster_index: int) -> None:
    """The Q&A No.82 board: `caster_index` sets 04-099 behind a (Neko Reset)
    character, and the opponent runs 02-064 over 3 electric cards (+60)."""
    state = game.state
    opponent_index = 1 - caster_index
    caster, opponent = state.players[caster_index], state.players[opponent_index]

    put_in_battle(game, caster_index, character_with_song(SONG_NEKO_RESET, 0))
    _arm_enchant(game, caster_index, "04-099")
    caster.set_b = caster.set_c = -1
    fill_charger_to(game, caster_index, 2)   # 4 power covers 04-099's cost 4

    put_in_battle(game, opponent_index, find_character(max_power_cost=0))
    opponent.set_a = opponent.set_b = -1
    opponent.set_c = spawn(game, card_with_effect("02-064"))
    fill_charger_with_attribute(game, opponent_index, cards.ATTR_ELECTRICITY, 3)


def test_qa_82_job_change_on_priority_is_buffed_on_top_of_the_100():
    """Q&A No.82, first half: the Job Change player has priority, so the 100
    lands first and the opponent's 02-064 is then added to it -> 160."""
    game = make_game()
    state = game.state
    give_priority(game, 0)
    _arm_job_change_against_unigiri_kororin(game, caster_index=0)

    events = run_process_effects(game)

    assert battle_attacks(events)[1] == 160


def test_qa_82_job_change_without_priority_overwrites_the_buff():
    """Q&A No.82, second half: same board, priority flipped. 02-064 resolves
    first and the 100 then overwrites it, however large the buff was."""
    game = make_game()
    state = game.state
    give_priority(game, 1)
    _arm_job_change_against_unigiri_kororin(game, caster_index=0)

    events = run_process_effects(game)

    assert battle_attacks(events)[1] == 100


def test_qa_54_minus_then_plus_clamps_after_each_step():
    """Q&A No.54 verbatim: the opponent's 30-attack character takes -40 from
    04-092 (clamped to 0, never below), and their own 03-028 then adds 80 on
    top of the 0 -> 80. Summing the modifiers first would give 70."""
    game = make_game()
    state = game.state
    give_priority(game, 0)

    put_in_battle(game, 0, character_with_song(SONG_SHADE, 1))
    _arm_enchant(game, 0, "04-092")
    state.players[0].set_b = state.players[0].set_c = -1
    fill_charger_to(game, 0, 1)              # 2 power covers 04-092's cost 2

    put_in_battle(game, 1, character_with_attack(state, 30))
    _arm_enchant(game, 1, "03-028")
    state.players[1].set_b = state.players[1].set_c = -1
    fill_charger_with_attribute(game, 1, cards.ATTR_FLAME, 3)  # all-flame, 6 power
    assert get_effective_attack(state, state.players[1]) == 30

    events = run_process_effects(game)

    assert battle_attacks(events)[1] == 80


def test_qa_40_unmet_power_cost_suppresses_attack_bonuses():
    """Q&A No.40: a battle character whose power cost is unmet cannot attack,
    so an attack+ effect does not apply to it either -- the answer is 0, not
    0 + the bonus."""
    game = make_game()
    state = game.state
    player = state.players[0]
    put_in_battle(game, 0, find_character(min_power_cost=1))
    player.charger.clear()

    add_attack_modifier(player, 20)

    assert get_effective_attack(state, player) == 0


def test_qa_73_unmet_power_cost_zeroes_the_attack_including_a_set():
    """An unmet power cost zeroes the final attack even when 04-099 set it.

    The authority is Ground Rules 2.3.6 ("パワーコストが足りないキャラクターの攻撃力は
    ０になり") and 5.1.3.2 ("攻撃力は０として扱われます"), with Q&A No.73 as the worked
    example -- the cost is lost after effects resolved and the attack is still 0 --
    and No.40/No.55 restating it.

    Deliberately NOT cited: GR 7.1.2, which is scoped to attack that was *added*
    ("攻撃力＋〇〇" effects) while 04-099 sets; and Q&A No.82, which settles
    resolution order and never mentions power cost. The counter-argument, GR 1.3.1
    (card text outranks the rules), was considered and rejected when the user
    confirmed this ruling on 2026-08-13.
    """
    game = make_game()
    state = game.state
    player = state.players[0]
    put_in_battle(game, 0, find_character(min_power_cost=1))
    player.charger.clear()

    add_attack_modifier(player, 20)
    set_attack_modifier(player, 100)
    add_attack_modifier(player, 20)
    assert get_effective_attack(state, player) == 0, "cost unmet: nothing folds"

    # Pay the cost and the very same modifier list folds normally: the set
    # discards the bonus before it, the bonus after it lands on top.
    fill_charger_to(game, 0, 4)
    assert get_effective_attack(state, player) == 120


def test_qa_60_enemy_atk_eq0_covers_every_way_of_not_attacking():
    """Q&A No.60: 'the opponent's character's attack is 0' covers no character
    set and an unmet power cost, not just a printed 0. All four cards with that
    text share the condition, and a 04-099 set takes the enemy out of it."""
    from engine_alpha.effects.conditions import eval_cond

    game = make_game()
    state = game.state
    enemy = state.players[1]

    enemy.battle = -1
    assert eval_cond(state, 0, ("enemy_atk_eq0",)) is True

    put_in_battle(game, 1, find_character(min_power_cost=1))
    enemy.charger.clear()
    assert eval_cond(state, 0, ("enemy_atk_eq0",)) is True, "unmet cost counts"

    # A 04-099 set does NOT rescue an unpayable character (Ground Rules 2.3.6 /
    # 7.1.2, Q&A No.40/73): the enemy still cannot attack, so the condition
    # still holds. Once the cost is paid the set applies and it no longer does.
    set_attack_modifier(enemy, 100)
    assert eval_cond(state, 0, ("enemy_atk_eq0",)) is True, "gate outranks the set"
    fill_charger_to(game, 1, 4)
    assert eval_cond(state, 0, ("enemy_atk_eq0",)) is False, "paid: the set lands"


def test_04_099_and_04_101_ordering_within_one_batch():
    """Both cards belong to the same player, so the P_EFFECT_ORDER prompt --
    not priority -- decides. 04-101 reads the enemy's attack live: ahead of the
    set it sees the unpayable character's 0 and buffs; behind it, it sees 100.
    """
    for job_change_first, expected_bonus in ((True, 0), (False, 20)):
        game = make_game()
        state = game.state
        give_priority(game, 0)

        put_in_battle(game, 0, character_with_song(SONG_NEKO_RESET, 0))
        job_change = _arm_enchant(game, 0, "04-099", "set_a")
        ice_cream = _arm_enchant(game, 0, "04-101", "set_b")
        state.players[0].set_c = -1
        fill_charger_to(game, 0, 2)   # 4 power for 04-099; 04-101 is free

        # The enemy's character has a printed night attack of 0 and its cost is
        # paid, so their attack reads 0 until the set lands -- which is what
        # makes the two orderings differ. (It has to be a printed 0 rather than
        # an unpayable character: an unmet cost now zeroes the set as well,
        # Ground Rules 2.3.6/7.1.2.)
        put_in_battle(game, 1, find_character(attack_night=0))
        state.chronos = MIDNIGHT
        fill_charger_to(game, 1, 4)
        state.players[1].set_a = state.players[1].set_b = state.players[1].set_c = -1
        caster_base = base_attack(state, state.players[0])

        events = run_process_effects(
            game, order_first=job_change if job_change_first else ice_cream)

        caster_attack, enemy_attack = battle_attacks(events)
        assert enemy_attack == 100
        assert caster_attack == caster_base + expected_bonus


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


# ---------------------------------------------------------------------------
# 2026-08-13 rules-compliance audit against the official Q&A (104 entries) and
# Ground Rules ver 1.0.1 (2026-08-08). One test per confirmed divergence.
# ---------------------------------------------------------------------------

def test_qa_18_family_d_fires_when_the_clock_wraps_within_one_turn():
    """Q&A No.18: the clock ran night -> day -> night inside one turn, and a
    "when day changes to night" card still activates, because the crossing did
    happen. Q&A No.17 makes the same point for a change that is later reverted.
    The old engine compared the turn-start period against the current one, so a
    full wrap (which ends where it started) fired nothing.
    """
    from engine_alpha.battle import advance_chronos_by
    from engine_alpha.state import GF_DAY_TO_NIGHT, GF_NIGHT_TO_DAY

    game = make_game()
    state = game.state
    state.chronos = 0
    state.chronos_at_turn_start = 0
    state.gflags[GF_DAY_TO_NIGHT] = 0
    state.gflags[GF_NIGHT_TO_DAY] = 0
    advance_chronos_by(state, 18)          # one full lap: both crossings occur
    assert state.chronos == 0 and state.is_night, "ends in the period it started"

    player = state.players[0]
    for effect_id, amount in (("01-061", 30), ("01-090", 20), ("01-096", 10),
                              ("01-084", 30), ("01-097", 20)):
        before = player.flags[PF_ATTACK_BONUS]
        run_effect(game, 0, spawn(game, card_with_effect(effect_id)))
        assert player.flags[PF_ATTACK_BONUS] - before == amount, effect_id


def test_qa_4_a_player_holding_cards_cannot_set_zero():
    """Ground Rules 5.2.1.5 and Q&A No.4: passing slot A is not a legal choice
    while the hand has cards. Slot B stays passable, because a player allowed
    two cards may choose to set only one."""
    from engine_alpha.actions import P_SET_SLOT_A
    from engine_alpha.state import PH_SET_CARDS

    game = make_game()
    while game.state.phase != PH_SET_CARDS and game.state.winner == -1:
        game.apply(game.legal_actions()[0])
    request = game.decision_context()
    assert request is not None and request.purpose == P_SET_SLOT_A
    assert game.state.players[game.state.acting].hand, "precondition: cards in hand"
    assert request.allow_pass is False
    assert len(game.legal_actions()) == len(request.candidates)


def test_qa_41_game_ends_the_instant_hp_reaches_zero():
    """Q&A No.41: a player on 03-058 takes lethal damage, and the turn-end heal
    does NOT bring them back. Ground Rules 1.2.3/5.4.1 end the game at that
    instant, and no later card effect is processed."""
    game = make_game()
    state = game.state
    _setup_area(game, 0, "03-058")
    state.players[0].hp = 20

    deal_damage(state, 0, 20)
    assert state.players[0].hp == 0
    assert state.winner == 1, "the winner is decided the moment HP hits 0"

    process_end_of_turn_effects(state)
    assert state.players[0].hp == 0, "no post-mortem heal"


def test_first_player_to_reach_zero_hp_loses():
    """Both players ending on 0 HP is not a draw: whoever got there first lost
    (user ruling 2026-08-13). record_hp_zero fixes the winner on the first
    crossing, so later damage cannot flip it."""
    game = make_game()
    state = game.state
    state.players[0].hp = 10
    state.players[1].hp = 10

    deal_damage(state, 1, 10)        # player 1 dies first
    assert state.winner == 0
    deal_damage(state, 0, 10)        # player 0 dies afterwards
    assert state.players[0].hp == 0 and state.players[1].hp == 0
    assert state.winner == 0, "the first to reach 0 is still the loser"


def test_qa_70_deck_shortfall_from_an_effect_loses_the_game():
    """Q&A No.70 and Ground Rules 8.2.2: when a mill cannot process the number
    of cards it names because the deck is short, the player who could not
    complete the processing loses. 04-057 mills 2 into a 1-card deck."""
    game = make_game()
    state = game.state
    owner, victim = state.players[0], state.players[1]
    while len(owner.abyss) < 3:                       # 04-057's own condition
        owner.abyss.append(spawn(game, cards.CARD_DB[0].index))
    victim.deck.clear()
    victim.deck.append(spawn(game, cards.CARD_DB[1].index))

    run_effect(game, 0, spawn(game, card_with_effect("04-057")))
    assert len(victim.deck) == 0
    assert state.winner == 0, "the milled player loses"


def test_grand_rule_5_4_3_1_double_deck_out_is_a_draw():
    """Ground Rules 5.4.3: a player who cannot make the mandatory end-of-turn
    draw loses -- and 5.4.3.1 makes it a draw when neither player can. The old
    code wrote a winner per player, so the second write silently won."""
    from engine_alpha.game import _end_turn_for

    game = make_game()
    state = game.state
    for player in state.players:
        player.deck.clear()
        player.cards_played = 1
    assert [_end_turn_for(state, p) for p in state.players] == [True, True]

    other = make_game()
    other_state = other.state
    other_state.players[0].deck.clear()
    other_state.players[0].cards_played = 1
    other_state.players[1].cards_played = 0
    assert _end_turn_for(other_state, other_state.players[0]) is True
    assert _end_turn_for(other_state, other_state.players[1]) is False


def test_grand_rule_5_4_3_1_winner_arithmetic_through_the_phase_driver():
    """The test above calls _end_turn_for directly, which leaves _ph_end_turn's
    own `2 if all(decked_out) else 1 - decked_out.index(True)` untested -- and that
    expression, not the bool, is what actually decides the game. Drive the phase.
    """
    for empty, expected in ((None, 2), (0, 1), (1, 0)):
        game = make_game()
        state = game.state
        for index, player in enumerate(state.players):
            player.cards_played = 1
            if empty is None or index == empty:
                player.deck.clear()
            else:
                player.deck.append(spawn(game, find_character()))
        state.phase_ctx = []

        game._ph_end_turn(state, None, None)
        assert state.winner == expected, (
            f"empty={empty}: expected {expected}, got {state.winner}")


def test_qa_79_public_zone_selection_requires_at_least_one_card():
    """Q&A No.79 names 04-002 directly: with valid targets visible in the power
    charger (a public zone, Ground Rules 1.3.5.1) the player must pick 1 to 2,
    not 0 to 2. Hidden-zone picks keep their 0 minimum -- see Q&A No.90."""
    game = make_game()
    state = game.state
    owner = state.players[0]
    shade = next(d.index for d in cards.CARD_DB
                 if d.card_type == cards.TYPE_CHARACTER and d.song == SONG_SHADE
                 and cards.EFFECT_T[d.index] != cards.NO_EFFECT)
    owner.charger.append(spawn(game, shade))
    owner.charger.append(spawn(game, shade))

    source = spawn(game, card_with_effect("04-002"))
    interpreter.start_effect(state, 0, source, cards.EFFECT_T[state.inst_def[source]])
    request = interpreter.resume(state, None, None)
    assert (request.lo, request.hi) == (1, 2)


def test_qa_90_hidden_zone_selection_may_still_choose_zero():
    """The other half of the choice-bound rule: 04-001 reveals from HAND, which
    is hidden information, so Ground Rules 1.3.5.2 and Q&A No.90 allow 0."""
    game = make_game()
    state = game.state
    owner = state.players[0]
    owner.hand.clear()
    taidada = next(d.index for d in cards.CARD_DB
                   if d.card_type == cards.TYPE_CHARACTER
                   and cards.SONG_NAMES[d.song] == "TAIDADA")
    owner.hand.append(spawn(game, taidada))

    source = spawn(game, card_with_effect("04-001"))
    interpreter.start_effect(state, 0, source, cards.EFFECT_T[state.inst_def[source]])
    request = interpreter.resume(state, None, None)
    assert request.lo == 0


def test_qa_89_hand_reveal_is_mandatory_even_when_the_bonus_misses():
    """Q&A No.89: the reveal cannot be declined. The JP text of 04-032/04-008/
    04-097 makes only the attack bonus conditional -- the reveal itself is not,
    so with the power cost met the hand is shown even when it holds fewer than
    the required number of attributes."""
    from engine_alpha.events import EVENT_CARDS_REVEALED

    for effect_id in ("04-032", "04-008", "04-097"):
        game = make_game()
        state = game.state
        owner = state.players[0]
        events = []
        state.event_sink = events
        owner.hand.clear()
        mono = next(d.index for d in cards.CARD_DB if d.attribute == 0)
        owner.hand.extend(spawn(game, mono) for _ in range(3))   # one attribute

        instance = spawn(game, card_with_effect(effect_id))
        before = owner.flags[PF_ATTACK_BONUS]
        run_effect(game, 0, instance)

        assert any(e[0] == EVENT_CARDS_REVEALED for e in events), effect_id
        assert owner.flags[PF_ATTACK_BONUS] == before, f"{effect_id}: bonus must miss"


def test_qa_16_03_058_and_03_085_self_remove_immediately():
    """Q&A No.16: the text says 「すぐに」, so the card reaches the abyss between
    taking the damage and the turn-end processing, and its turn-end block never
    runs.

    This drives the damage through `deal_damage` and then runs the REAL turn-end
    phase, rather than hand-calling check_area_removal. That distinction matters:
    an earlier version of this fix only removed the card at a phase boundary after
    the turn-end window, so the heal and the clock advance still fired and a
    hand-called checkpoint hid it.
    """
    from engine_alpha.state import PH_TURN_END_EFFECTS

    for effect_id in ("03-058", "03-085"):
        game = make_game()
        state = game.state
        area = _setup_area(game, 0, effect_id)
        state.players[0].hp = 100
        state.chronos = 13                  # daytime: 03-085 would advance the clock
        chronos_before = state.chronos

        deal_damage(state, 0, 30)
        assert state.players[0].set_c == -1, f"{effect_id}: not removed at damage time"
        assert area in state.players[0].abyss, effect_id

        state.phase = PH_TURN_END_EFFECTS
        state.phase_ctx = []
        game._ph_turn_end_effects(state, None, None)
        assert state.players[0].hp == 70, f"{effect_id}: 03-058 must not heal"
        assert state.chronos == chronos_before, f"{effect_id}: 03-085 must not advance"


def test_qa_80_04_091_leaves_play_as_soon_as_hp_drops_to_50():
    """Q&A No.80: 「バトルのダメージによってHP50以下になった場合は、HPの処理を終えたら
    すぐにパワーチャージャーに置きます」 -- immediately, not at the next phase."""
    game = make_game()
    state = game.state
    _setup_area(game, 0, "04-091")
    state.players[0].hp = 60

    deal_damage(state, 0, 20)
    assert state.players[0].hp == 40
    assert state.players[0].set_c == -1, "04-091 must leave play at once"


def test_damage_triggered_removal_does_not_run_once_the_game_is_over():
    """Q&A No.41: no further processing happens after a player's HP reaches 0,
    so the immediate-removal hook must not fire in a finished game."""
    game = make_game()
    state = game.state
    area = _setup_area(game, 0, "03-058")
    state.players[0].hp = 20

    deal_damage(state, 0, 20)
    assert state.winner == 1
    assert state.players[0].set_c == area, "board untouched after the game ended"


def test_qa_96_priority_player_resolves_their_turn_end_batch_first():
    """Q&A No.96 and Ground Rules 5.2.10.2: turn-end effects resolve from the
    priority player.

    Player 1 OWNS 03-027, so the pending damage flag sits on player 1 and the
    damage lands on player 0 (Q&A No.25 attributes the damage to the card, so it
    is its caster's turn-end effect and resolves in the caster's batch). With
    priority, player 1's damage lands before player 0's 03-058 heal.

    The HP cap is what makes the order observable: starting player 0 at 95, taking
    the damage first leaves room for the whole heal (95 - 20 = 75, then +10 = 85),
    whereas healing first wastes most of it against the cap (95 -> 100, then
    -20 = 80). The damage stays under 30 so it does not trip 03-058's own
    self-removal, which Q&A No.16 covers separately.
    """
    game = make_game()
    state = game.state
    give_priority(game, 1)
    _setup_area(game, 0, "03-058")
    state.players[1].flags[PF_END_OF_TURN_DAMAGE] = 20   # player 1 owns 03-027
    state.players[0].hp = 95
    state.players[1].hp = 100

    process_end_of_turn_effects(state)
    assert state.players[0].hp == 85, "damage resolved before the heal"
    assert state.players[1].hp == 100


def test_qa_25_03_027_damage_belongs_to_its_caster():
    """Q&A No.25 treats the turn-end 50 as 03-027's own effect, so it is the
    CASTER's turn-end item: it resolves in the caster's priority batch and the
    caster orders it among their own. Recording it on the victim also left the
    ordering prompt with an unshowable instance, since the card sits in the
    caster's set zone."""
    from engine_alpha.effects.turn_end import collect_turn_end_items

    game = make_game()
    state = game.state
    caster, victim = state.players[0], state.players[1]
    instance = spawn(game, card_with_effect("03-027"))
    caster.set_a = instance
    state.inst_played[instance] = 1
    run_effect(game, 0, instance)

    assert caster.flags[PF_END_OF_TURN_DAMAGE] == 50
    assert victim.flags[PF_END_OF_TURN_DAMAGE] == 0
    caster_items = collect_turn_end_items(state, caster)
    assert collect_turn_end_items(state, victim) == []
    assert [i for _, i in caster_items] == [instance], "resolves to the caster's card"

    victim.hp = 100
    process_end_of_turn_effects(state)
    assert victim.hp == 50, "the damage still lands on the opponent"


def test_qa_96_player_orders_their_own_turn_end_effects():
    """Q&A No.96 also lets a player choose the order among their own turn-end
    effects, so the window collects them as separate items and prompts with
    P_EFFECT_ORDER when a player holds more than one."""
    from engine_alpha.effects.turn_end import collect_turn_end_items

    game = make_game()
    state = game.state
    _setup_area(game, 0, "03-058")
    state.players[0].flags[PF_END_OF_TURN_DAMAGE] = 10
    items = collect_turn_end_items(state, state.players[0])
    assert len(items) == 2, "two turn-end effects -> the player picks the order"


def test_qa_33_03_064_reads_hp_at_attack_determination():
    """Q&A No.33: 03-064 adds each side's remaining HP at attack determination,
    not when the area enchant resolved, so damage taken afterwards shrinks the
    bonus."""
    game = make_game()
    state = game.state
    owner = state.players[0]
    put_in_battle(game, 0, find_character(min_power_cost=0, max_power_cost=0))
    area = _setup_area(game, 0, "03-064")
    owner.hp = 100
    base = base_attack(state, owner)

    run_effect(game, 0, area)
    assert get_effective_attack(state, owner) == base + 100

    deal_damage(state, 0, 30)          # HP 100 -> 70 after the effect resolved
    assert get_effective_attack(state, owner) == base + 70, "live HP, not a snapshot"


def test_qa_79_83_shade_chain_terminates_without_blocking_legal_nesting():
    """04-002 must not be able to re-enter its own resolution.

    Q&A No.79 forbids choosing zero when valid targets are visible, which removed
    the escape hatch: 04-002 is itself a SHADE character with an effect, so it
    re-selected itself out of the charger and the only legal action re-entered the
    effect forever -- a hung Discord match and an unbounded frame stack in search.
    Q&A No.83 still permits chaining to OTHER cards (「さらに２枚を指定することが可能です」),
    and its worked example totals three, so the nesting is meant to terminate.
    """
    ball = card_with_effect("04-002")

    for copies in (1, 2, 3):
        game = make_game()
        state = game.state
        owner = state.players[0]
        for _ in range(copies):
            owner.charger.append(spawn(game, ball))
        source = spawn(game, ball)
        interpreter.start_effect(state, 0, source,
                                 cards.EFFECT_T[state.inst_def[source]])
        request = interpreter.resume(state, None, None)
        steps = 0
        while request is not None and steps < 200:
            request = interpreter.resume(
                state, request, request.lo if request.kind == 2 else 0)
            steps += 1
        assert request is None, f"{copies} copies: chain never terminated"
        assert state.frame_stack == [], f"{copies} copies: frames left behind"
        assert len(state.frame_stack) <= 24

    # A 04-002 may still designate a DIFFERENT 04-002 and further distinct SHADE
    # characters -- Q&A No.83's case must keep working.
    game = make_game()
    state = game.state
    owner = state.players[0]
    others = [d.index for d in cards.CARD_DB
              if d.card_type == cards.TYPE_CHARACTER
              and cards.SONG_NAMES[d.song] == "SHADE"
              and cards.EFFECT_T[d.index] != cards.NO_EFFECT and d.index != ball]
    owner.charger.append(spawn(game, ball))
    for def_index in others[:3]:
        owner.charger.append(spawn(game, def_index))
    source = spawn(game, ball)
    interpreter.start_effect(state, 0, source, cards.EFFECT_T[state.inst_def[source]])
    request = interpreter.resume(state, None, None)
    card_picks, steps = 0, 0
    while request is not None and steps < 200:
        if request.kind != 2:
            card_picks += 1
        request = interpreter.resume(
            state, request, request.hi if request.kind == 2 else 0)
        steps += 1
    assert request is None
    assert card_picks >= 2, "nesting to distinct cards must still be possible"


def test_qa_45_03_097_returns_the_revealed_card_and_moves_itself():
    """Q&A No.45: 「公開した相手のデッキの一番上のカードは、再び相手のデッキの一番上に
    戻します。公開したカードのパワーコストが★6以上の場合、すぐに「厳戒態勢」を
    パワーチャージャーに置きます」.

    The revealed card is only looked at -- it stays on top of the opponent's deck --
    and it is 03-097 ITSELF that goes to the owner's charger. The engine used to move
    the revealed card onto the opponent's charger instead, which both took a card off
    their deck and put the wrong one in play.
    """
    game = make_game()
    state = game.state
    owner, opponent = state.players[0], state.players[1]
    area = _setup_area(game, 0, "03-097")

    expensive = next(d.index for d in cards.CARD_DB if d.power_cost >= 6)
    top = spawn(game, expensive)
    opponent.deck.insert(0, top)
    deck_length = len(opponent.deck)
    charger_before = list(opponent.charger)

    run_effect(game, 0, area)

    assert opponent.deck[0] == top, "the revealed card stays on top of the deck"
    assert len(opponent.deck) == deck_length, "no card is taken from the deck"
    assert opponent.charger == charger_before, "nothing is added to their charger"
    assert owner.set_c == -1 and area in owner.charger, "03-097 itself moves"
    # Q&A No.46: it sits there with SEND TO POWER 0, so it adds no power.
    assert cards.SEND_TO_POWER_T[state.inst_def[area]] == 0


def test_qa_45_03_097_stays_when_the_revealed_card_is_cheap():
    game = make_game()
    state = game.state
    owner, opponent = state.players[0], state.players[1]
    area = _setup_area(game, 0, "03-097")
    cheap = next(d.index for d in cards.CARD_DB if d.power_cost < 6)
    top = spawn(game, cheap)
    opponent.deck.insert(0, top)

    run_effect(game, 0, area)
    assert opponent.deck[0] == top
    assert owner.set_c == area, "below the threshold nothing moves"


def test_qa_28_all_three_03_055_block_terminations_hold():
    """Q&A No.28 lists three ways the area block ends: the owner sets another area
    enchant, the end of a turn in which the opponent put a card in the abyss, and the
    opponent activating an effect that interferes with area enchants.

    The third is satisfied structurally rather than by a dedicated branch: every
    interfering effect removes 03-055, and each of those paths fires
    on_area_enchant_leaves_play, which clears the block.
    """
    from engine_alpha.effects.removal import on_area_enchant_leaves_play

    def blocked_game():
        game = make_game()
        area = _setup_area(game, 0, "03-055")
        game.state.players[1].area_blocked = True
        return game, game.state, area

    game, state, area = blocked_game()
    state.players[0].set_c = -1
    on_area_enchant_leaves_play(state, area, 0)
    assert state.players[1].area_blocked is False, "condition 1"

    game, state, area = blocked_game()
    state.players[0].flags[PF_OPP_CARD_TO_ABYSS] = 1
    check_area_removal(state, end_of_turn=True)
    assert state.players[1].area_blocked is False, "condition 2"

    for interferer in ("03-014", "03-021", "04-107"):
        game, state, area = blocked_game()
        instance = spawn(game, card_with_effect(interferer))
        state.players[1].set_a = instance
        state.inst_played[instance] = 1
        run_effect(game, 1, instance)
        assert state.players[0].set_c != area, f"{interferer} should remove 03-055"
        assert state.players[1].area_blocked is False, f"condition 3 via {interferer}"


def test_02_015_places_the_used_enchant_even_if_it_ends_the_game():
    """The enchant 02-015 plays is used the moment it is chosen, so it must leave
    hand before its effect resolves.

    Deferring the placement until after resolution loses it whenever the nested
    effect ends the game: the interpreter drops every pending frame once a winner is
    set (Q&A No.41), so the card would still show in hand on the final board. Here
    01-104 mills an empty deck, which ends the game inside 02-015's own resolution.
    """
    game = make_game()
    state = game.state
    owner, opponent = state.players[0], state.players[1]
    dark = next(d.index for d in cards.CARD_DB
                if d.card_type == cards.TYPE_CHARACTER and d.attribute == 0)
    owner.prev_battle_def = dark        # 02-015's gate: prev character dark, and day
    state.chronos = 13
    owner.hand.clear()
    used = spawn(game, card_with_effect("01-104"))
    owner.hand.append(used)
    opponent.deck.clear()

    instance = spawn(game, card_with_effect("02-015"))
    owner.set_a = instance
    state.inst_played[instance] = 1
    interpreter.start_effect(state, 0, instance,
                             cards.EFFECT_T[state.inst_def[instance]])
    request = interpreter.resume(state, None, None)
    steps = 0
    while request is not None and steps < 20:
        request = interpreter.resume(state, request, 0)
        steps += 1

    assert state.winner == 0, 'the mill shortfall should end the game'
    assert used not in owner.hand, 'the used enchant must not linger in hand'
    assert used in owner.abyss or used in owner.charger, 'it must be placed'


def test_gr_8_2_1_a_draw_that_cannot_be_made_loses_for_every_card():
    """Ground Rules 8.2.1: a player who cannot draw the number an effect names loses
    at that moment.

    01-092, 04-089 and 02-015's trailing draw each used to opt out -- the first two
    behind a `deck_ge` gate with no basis in the card text, the third behind an
    `if owner.deck` in its handler -- so an impossible draw was a silent no-op while
    the identically-worded 03-031 lost the game.
    """
    for effect_id in ("01-092", "03-031"):
        game = make_game()
        state = game.state
        owner = state.players[0]
        owner.deck.clear()
        owner.hand.clear()
        owner.hand.append(spawn(game, cards.CARD_DB[0].index))  # 03-031 needs a pick
        instance = spawn(game, card_with_effect(effect_id))
        owner.set_a = instance
        state.inst_played[instance] = 1
        run_effect(game, 0, instance, answers=(0,))
        assert state.winner == 1, f"{effect_id}: an impossible draw must lose"


def test_qa_92_a_deck_reaching_zero_without_a_shortfall_is_not_a_loss():
    """Q&A No.92 marks the boundary: emptying a deck is not itself a defeat --
    that waits for the end-of-turn draw. Only a shortfall is immediate."""
    game = make_game()
    state = game.state
    owner = state.players[0]
    owner.deck.clear()
    owner.deck.append(spawn(game, cards.CARD_DB[0].index))   # exactly enough
    instance = spawn(game, card_with_effect("01-092"))
    owner.set_a = instance
    state.inst_played[instance] = 1

    run_effect(game, 0, instance)
    assert len(owner.deck) == 0
    assert state.winner == -1, "the deck hit 0 but nothing was short"


# ---------------------------------------------------------------------------
# 02-041 / deck_top_route: the last op that clamped a deck shortfall silently
# ---------------------------------------------------------------------------

def _arm_02_041(game: Game, owner: int, *, meet_gate: bool = True) -> int:
    """Put a dispatchable 02-041 in `owner`'s battle zone, gate met by default.

    02-041 reads 「前のターンで使用したキャラクターカードの属性が闇なら」, so the gate is
    the PREVIOUS turn's character being darkness -- nothing to do with the deck.
    Note the caller still owes the 2 power its cost needs when driving the real
    phase; `run_effect` bypasses the cost check.
    """
    state = game.state
    player = state.players[owner]
    dark = next(d.index for d in cards.CARD_DB
                if d.card_type == cards.TYPE_CHARACTER
                and d.attribute == cards.ATTR_DARKNESS)
    player.prev_battle_def = dark if meet_gate else -1
    if player.battle != -1:          # don't orphan whatever make_game left there
        to_power_or_abyss(state, player.battle, owner)
    instance = spawn(game, card_with_effect("02-041"))
    player.battle = instance
    state.inst_played[instance] = 1
    return instance


def test_gr_8_2_1_02_041_cannot_route_from_an_empty_deck_and_loses():
    """Ground Rules 8.2.1/8.2.2: 02-041 names one card out of a deck
    (「デッキの一番上のカードを…置く」) and cannot supply it, so its owner loses.

    01-104 is the same instruction aimed at the opponent
    (「相手のデッキの一番上のカードを…アビスに置く」) and has always lost the game,
    because it compiles to `mill`. `deck_top_route` used to no-op instead, so the
    identical instruction lost or fizzled purely by which op it compiled to.
    """
    game = make_game()
    state = game.state
    owner = state.players[0]
    owner.deck.clear()

    run_effect(game, 0, _arm_02_041(game, 0))
    assert state.winner == 1, "the owner cannot process its own card and loses"


def test_qa_92_02_041_routes_its_last_card_without_losing():
    """The boundary case for the test above: with exactly one card the
    instruction completes, and a deck reaching 0 is not itself a defeat."""
    game = make_game()
    state = game.state
    owner = state.players[0]
    owner.deck.clear()
    last = spawn(game, cards.CARD_DB[0].index)
    owner.deck.append(last)

    run_effect(game, 0, _arm_02_041(game, 0))
    assert len(owner.deck) == 0
    # CARD_DB[0] has SEND TO POWER 0, so the destination is deterministic --
    # asserting a disjunction here could not catch a routing regression.
    assert cards.SEND_TO_POWER_T[state.inst_def[last]] == 0
    assert last in owner.abyss, "the card was routed to the abyss"
    assert state.winner == -1, "the deck hit 0 but nothing was short"


def test_02_041_on_an_empty_deck_does_not_lose_when_it_never_resolves():
    """The other side of the fix, and the more dangerous direction to regress: an
    effect that does not resolve cannot deck anyone out.

    Three ways 02-041 never resolves, all with an empty deck behind it:
    an unmet power cost (it costs 2, and an unpaid effect is skipped like any
    other), an unmet gate (the previous character was not darkness), and a
    negated instance. If any of these started losing the game, every player
    holding 02-041 with an empty deck would die on sight.
    """
    from engine_alpha.effects.dispatch import HANDLED_EFFECTS
    from engine_alpha.game import _collect_eligible

    # 1. Power cost unmet: dispatched, but skipped before start_effect.
    game = make_game()
    state = game.state
    give_priority(game, 0)
    state.players[0].deck.clear()
    source = _arm_02_041(game, 0)
    state.players[0].charger.clear()          # 0 power against a cost of 2
    state.players[0].set_a = state.players[0].set_b = state.players[0].set_c = -1
    events = run_process_effects(game)
    assert state.inst_def[source] in cost_skipped_definitions(events), (
        "the cost gate is what stopped it")
    assert state.inst_def[source] not in started_definitions(events)
    assert state.winner == -1, "an unaffordable effect cannot deck its owner out"

    # 2. Gate unmet: dispatched and paid for, but start_effect pushes no frame.
    game = make_game()
    state = game.state
    state.players[0].deck.clear()
    source = _arm_02_041(game, 0, meet_gate=False)
    run_effect(game, 0, source)
    assert state.winner == -1, "the gate is what decides whether it processes"

    # 3. Negated: never collected in the first place.
    game = make_game()
    state = game.state
    state.players[0].deck.clear()
    source = _arm_02_041(game, 0)
    state.inst_neg[source] = 1
    assert cards.EFFECT_T[state.inst_def[source]] in HANDLED_EFFECTS
    assert source not in _collect_eligible(state, state.players[0])
    assert state.winner == -1


def _set_up_02_041_battle_preempt(game: Game) -> None:
    """Owner holds a lethal attack over the opponent, and 02-041 with no deck."""
    state = game.state
    give_priority(game, 0)
    owner, opponent = state.players
    _arm_02_041(game, 0)
    fill_charger_to(game, 0, 1)          # 2 power: covers 02-041's cost
    opponent.hp = 10
    opponent.battle = spawn(game, find_character())
    opponent.set_c = -1


def test_02_041_deck_out_pre_empts_a_battle_its_owner_would_have_won():
    """Accepted consequence 1 of making 02-041 lose: the shortfall lands in
    PROCESS_EFFECTS, which runs before BATTLE, so a battle the owner would have
    won never happens.

    The control arm is what makes this a statement about the shortfall rather
    than about the board: the identical position with ONE card in the deck routes
    it, reaches BATTLE, and the owner wins. Both arms also assert the premise --
    the owner really does out-attack the opponent lethally -- because otherwise
    the setup could stop being a winning position without any test noticing.
    """
    from engine_alpha.battle import get_effective_attack

    # Control: one card in the deck, everything else identical.
    control = make_game()
    _set_up_02_041_battle_preempt(control)
    control_owner, control_opponent = control.state.players
    control_owner.deck[:] = [spawn(control, cards.CARD_DB[0].index)]
    owner_attack = get_effective_attack(control.state, control_owner)
    opponent_attack = get_effective_attack(control.state, control_opponent)
    assert owner_attack > opponent_attack, "premise: the owner wins the battle"
    assert owner_attack - opponent_attack >= control_opponent.hp, "and lethally"

    control_events = run_process_effects(control)
    assert any(event[0] == EVENT_BATTLE_RESULT for event in control_events), (
        "control: BATTLE resolves when the deck can supply the card")
    assert control.state.winner == 0, "control: the owner wins that battle"

    # The real case: same position, empty deck.
    game = make_game()
    _set_up_02_041_battle_preempt(game)
    state = game.state
    owner, opponent = state.players
    owner.deck.clear()
    assert get_effective_attack(state, owner) > get_effective_attack(state, opponent)

    events = run_process_effects(game)

    assert state.winner == 1, "the owner loses in PROCESS_EFFECTS"
    assert opponent.hp == 10, "the opponent never took battle damage"
    assert not any(event[0] == EVENT_BATTLE_RESULT for event in events), (
        "the game ended before BATTLE could resolve")


def test_02_041_deck_out_takes_the_loss_alone_where_both_would_have_decked_out():
    """Accepted consequence 2: this replaces a reachable GR 5.4.3.1 draw.

    Both decks empty and both players owing an end-of-turn draw is a winner = 2
    draw. The 02-041 shortfall fires first, in PROCESS_EFFECTS, so its owner loses
    alone -- the first writer of `winner` wins, as it does for a double knock-out.

    This has to be driven through the phase machinery, and both players must owe
    a draw. `run_effect` calls start_effect directly, so _ph_end_turn would never
    run; and with cards_played == 0 the mandatory draw is zero cards, which an
    empty deck satisfies -- either way there would be no draw to displace. The
    control arm proves the draw is really there to lose.
    """
    def build() -> Game:
        game = make_game()
        state = game.state
        give_priority(game, 0)
        _arm_02_041(game, 0)
        fill_charger_to(game, 0, 1)      # 2 power: covers 02-041's cost
        for player in state.players:
            player.deck.clear()
            player.cards_played = 1      # both owe a draw they cannot make
            player.set_a = player.set_b = player.set_c = -1
        state.players[1].battle = spawn(game, find_character())
        return game

    # Control: the same position with 02-041's gate unmet, so it never resolves
    # and the turn reaches _ph_end_turn with both players short.
    control = build()
    control.state.players[0].prev_battle_def = -1
    run_process_effects(control)
    assert control.state.winner == 2, (
        "control: without the shortfall this position is a GR 5.4.3.1 draw")

    game = build()
    run_process_effects(game)
    assert game.state.winner == 1, "not a draw: the owner's shortfall came first"


def test_top_card_reveals_survive_an_empty_deck():
    """Ground Rules 8.2.1 turns on cards LEAVING the deck zone, not on a count
    being named. 03-097 and 03-103 only look at the top card -- Q&A No.45 puts it
    straight back -- so they cannot fall short.

    They MUST fizzle rather than lose: both are area enchants, and
    _collect_eligible re-queues set_c every turn with no inst_played check, so a
    loss here would kill a player every turn once their deck reached 0.
    """
    for effect_id in ("03-097", "03-103"):
        game = make_game()
        state = game.state
        state.event_sink = []
        state.players[1].deck.clear()
        area = spawn(game, card_with_effect(effect_id))
        state.players[0].set_c = area
        run_effect(game, 0, area)
        # Guard against the assertion going vacuous if a future gate stops the
        # effect starting: winner == -1 is also the untouched default.
        assert state.inst_def[area] in started_definitions(state.event_sink), (
            f"{effect_id}: the effect must actually have resolved")
        assert state.winner == -1, f"{effect_id}: a reveal cannot deck anyone out"


def test_04_088_clamps_a_named_count_it_only_reorders():
    """04-088 names 3 but never removes a card -- it looks at the opponent's top
    cards and puts them back (opponent.deck[:view_count] = reordered). Nothing
    leaves the deck zone, so a short deck is a clamp, not a shortfall.

    Two arms: a 2-card deck actually reaches the reorder line (an empty deck
    returns early at view_count <= 1 and would never exercise it).
    """
    for deck_size in (2, 0):
        game = make_game()
        state = game.state
        state.event_sink = []
        stock_abyss(game, 0, 1)          # 04-088 self-defeats on an empty abyss
        opponent = state.players[1]
        opponent.deck[:] = [spawn(game, find_character()) for _ in range(deck_size)]

        source = spawn(game, card_with_effect("04-088"))
        run_effect_auto(game, 0, source)

        assert state.inst_def[source] in started_definitions(state.event_sink)
        assert len(opponent.deck) == deck_size, "the deck is reordered, not drained"
        assert state.winner == -1, (
            f"deck of {deck_size}: looking at fewer than 3 is not a shortfall")


# ---------------------------------------------------------------------------
# The scenarios the deck-out audit was raised for
# ---------------------------------------------------------------------------

def test_qa_70_04_027_mills_past_the_opponents_deck_and_wins():
    """04-027 banks N from its own abyss and then mills the opponent by the same
    N. Banking 7 against a 6-card deck leaves the mill one card short, so the
    milled player loses (Q&A No.70, Ground Rules 8.2.2).

    pick_number answers are the literal number; card picks are indices -- hence
    the leading 7 followed by first-candidate picks.
    """
    game = make_game()
    state = game.state
    owner, opponent = state.players
    stock_abyss(game, 0, 7)
    opponent.deck.clear()
    opponent.abyss.clear()
    for _ in range(6):
        opponent.deck.append(spawn(game, find_character()))
    deck_before = len(owner.deck)

    run_effect(game, 0, spawn(game, card_with_effect("04-027")),
               answers=(7, 0, 0, 0, 0, 0, 0, 0))

    assert len(owner.deck) == deck_before + 7, "all 7 were banked to the deck"
    assert len(owner.abyss) == 0, "and none was left in the abyss"
    assert len(opponent.deck) == 0, "the mill took every card it could"
    assert len(opponent.abyss) == 6, "six cards were milled before it ran short"
    assert state.winner == 0, "the milled player loses"


def test_cumulative_mills_across_two_effects_deck_the_opponent_out():
    """Two effects in one turn add up: 04-027 milling 3 then 04-057 milling 2 is
    5 against a 4-card deck, and it is the SECOND effect that ends the game.

    04-027's bank removes the chosen cards from its owner's abyss before 04-057's
    own gate (「アビスに3枚以上のカードがあるなら」) is evaluated, so the owner needs 6
    abyss cards to bank 3 and still meet it -- otherwise 04-057 never starts and
    the test would pass for the wrong reason.
    """
    game = make_game()
    state = game.state
    owner, opponent = state.players
    stock_abyss(game, 0, 6)
    opponent.deck.clear()
    opponent.abyss.clear()
    for _ in range(4):
        opponent.deck.append(spawn(game, find_character()))

    run_effect(game, 0, spawn(game, card_with_effect("04-027")),
               answers=(3, 0, 0, 0))
    assert len(opponent.deck) == 1, "04-027 milled 3 of the 4"
    assert state.winner == -1, "three mills into a four-card deck is not short"
    assert len(owner.abyss) == 3, "04-057's gate still needs three abyss cards"

    run_effect(game, 0, spawn(game, card_with_effect("04-057")))
    assert len(opponent.deck) == 0
    assert state.winner == 0, "the second mill ran short and ended it"


def test_qa_17_a_rewind_does_not_manufacture_a_day_night_crossing():
    """Q&A No.17 treats a rewind as undoing a change, not making one. 01-008 (raw
    revert) and 01-026 (rewind by the opponent's clock) must therefore agree: neither
    records a crossing, so neither hands family D a bonus that never happened."""
    from engine_alpha.battle import advance_chronos_by
    from engine_alpha.state import (
        GF_DAY_TO_NIGHT, GF_NIGHT_TO_DAY, PF_CHRONOS_ADVANCED)

    game = make_game()
    state = game.state
    state.chronos = 17                      # day
    state.chronos_at_turn_start = 17
    state.gflags[GF_DAY_TO_NIGHT] = 0
    state.gflags[GF_NIGHT_TO_DAY] = 0
    advance_chronos_by(state, 2)            # 17 -> 1: a real day->night crossing
    assert state.gflags[GF_DAY_TO_NIGHT] == 1
    assert state.gflags[GF_NIGHT_TO_DAY] == 0

    state.players[1].flags[PF_CHRONOS_ADVANCED] = 2
    instance = spawn(game, card_with_effect("01-026"))
    state.players[0].set_a = instance
    state.inst_played[instance] = 1
    run_effect(game, 0, instance)

    assert state.gflags[GF_NIGHT_TO_DAY] == 0, "rewinding must not invent a crossing"
    assert state.gflags[GF_DAY_TO_NIGHT] == 1, "the crossing that did happen stands"
