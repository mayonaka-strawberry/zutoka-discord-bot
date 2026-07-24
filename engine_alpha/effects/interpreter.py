"""Micro-step IR interpreter.

Effect resolution is an explicit machine: a Frame holds (effect, source
card, owner, program counter, registers, compound-op sub-state). Ops
execute sequentially; choice ops return a DecisionRequest, pausing the
frame with all sub-state stored ON the frame (never on the Python stack),
so a paused game clones perfectly.

Answer routing: Game._advance() calls resume() with the (request, answer)
pair whenever frames are active; the paused op re-runs, consumes the
answer, and either finishes or pauses again (compound ops like multiselect
issue several requests in sequence).

Prompt-sequence fidelity: choice ops reproduce the legacy engine's exact
prompt sequences (e.g. multiselect = one number prompt then k single-card
prompts, matching its _prompt_card_multiselect), a contract established by
the now-retired cross-engine equivalence harness and still relied on by the
recorded decision logs and transcript baselines.
"""

from __future__ import annotations

from ..actions import (
    DecisionRequest,
    P_EFFECT_TARGET, P_EFFECT_NUMBER, P_NAME_GUESS, P_CHRONOS_VALUE,
    select_card, select_identity, select_number,
)
from ..cards import CARD_TYPE_T, NUM_CARDS, SONG_T, TYPE_CHARACTER
from ..events import EVENT_EFFECT_STARTED, EVENT_HP_CHANGED
from ..state import (
    Frame, GameState,
    PF_ATTACK_BONUS, PF_DAMAGE_REDUCTION, PF_DAY_NIGHT_REVERSED,
    PF_POWER_BONUS, PF_END_OF_TURN_DAMAGE, PF_ATTACK_OVERRIDE,
    PF_DAMAGE_NOT_REDUCIBLE, PF_REFLECT_REDUCTION,
    GF_MIDNIGHT_EXTENDED,
)
from ..rng import shuffled
from ..zones import place_in_abyss, place_in_charger, draw_cards
from .conditions import eval_cond
from .ir import Sel
from .selectors import eval_selector

CHRONOS_SIZE = 18

# Populated by catalog.py at import: effect_index -> (cond, ops, custom_name)
EFFECT_PROGRAMS: dict[int, tuple] = {}
CUSTOM_HANDLERS: dict[str, callable] = {}


# ---------------------------------------------------------------------------
# Expressions
# ---------------------------------------------------------------------------

def eval_expr(state: GameState, frame: Frame, expr) -> int:
    if isinstance(expr, int):
        return expr
    kind = expr[0]
    if kind == "reg":
        value = frame.regs[expr[1]]
        return value if isinstance(value, int) else len(value)
    if kind == "reg_len":
        value = frame.regs[expr[1]]
        return len(value) if isinstance(value, list) else 0
    if kind == "mul":
        return eval_expr(state, frame, expr[1]) * expr[2]
    if kind == "count":
        return len(eval_selector(state, frame.owner, expr[1]))
    if kind == "hand_len":
        return len(_side_player(state, frame, expr[1]).hand)
    if kind == "charger_len":
        return len(_side_player(state, frame, expr[1]).charger)
    if kind == "abyss_len":
        return len(_side_player(state, frame, expr[1]).abyss)
    if kind == "hp":
        return _side_player(state, frame, expr[1]).hp
    raise ValueError(f"unknown expr {expr!r}")


def _side_player(state: GameState, frame: Frame, side: int):
    return state.players[frame.owner if side == 0 else 1 - frame.owner]


def _ensure_regs(frame: Frame, reg: int) -> None:
    while len(frame.regs) <= reg:
        frame.regs.append(None)


def remove_from_current_zone(state: GameState, instance_id: int) -> int:
    """Detach an instance from whatever container holds it. Returns the
    holding player's index. Raises if the card is nowhere (a bug)."""
    for player in state.players:
        for zone in (player.hand, player.charger, player.abyss, player.deck):
            if instance_id in zone:
                zone.remove(instance_id)
                return player.index
        if player.battle == instance_id:
            player.battle = -1
            return player.index
        if player.set_a == instance_id:
            player.set_a = -1
            return player.index
        if player.set_b == instance_id:
            player.set_b = -1
            return player.index
        if player.set_c == instance_id:
            player.set_c = -1
            return player.index
    raise AssertionError(f"instance {instance_id} not found in any zone")


# ---------------------------------------------------------------------------
# Frame execution
# ---------------------------------------------------------------------------

def start_effect(state: GameState, owner_index: int, instance_id: int, effect_index: int) -> None:
    """Push a frame if the effect's gate condition holds at resolution time.
    (Custom entries usually carry cond=None and evaluate conditions inside
    the handler; a non-None cond gates customs too, e.g. 02-015.)"""
    cond, ops, custom = EFFECT_PROGRAMS[effect_index]
    if not eval_cond(state, owner_index, cond):
        return
    state.frame_stack.append(Frame(effect_index, instance_id, owner_index))
    if state.event_sink is not None:
        state.event_sink.append(
            (EVENT_EFFECT_STARTED, owner_index, state.inst_def[instance_id], effect_index))


def resume(state: GameState, request: DecisionRequest | None, answer: int | None) -> DecisionRequest | None:
    """Run frames until all drain (returns None) or a choice pauses
    execution (returns the DecisionRequest; state.acting is set)."""
    while state.frame_stack:
        frame = state.frame_stack[-1]
        cond, ops, custom = EFFECT_PROGRAMS[frame.effect_index]
        if custom is not None:
            result = CUSTOM_HANDLERS[custom](state, frame, request, answer)
        else:
            result = _exec_ops(state, frame, ops, request, answer)
        request = None
        answer = None
        if result is not None:
            state.acting = frame.owner
            return result
        if state.frame_stack and state.frame_stack[-1] is frame:
            state.frame_stack.pop()
    return None


def _exec_ops(state: GameState, frame: Frame, ops: tuple, request, answer):
    while frame.pc < len(ops):
        op = ops[frame.pc]
        result = OP_TABLE[op[0]](state, frame, op, request, answer)
        request = None
        answer = None
        if result is not None:
            return result
    return None


# ---------------------------------------------------------------------------
# Op handlers. Signature: (state, frame, op, request, answer)
# -> DecisionRequest to pause, or None (op done; handler updated frame.pc).
# ---------------------------------------------------------------------------

def _op_end(state, frame, op, request, answer):
    frame.pc = 10**6
    return None


def _op_jump(state, frame, op, request, answer):
    frame.pc = op[1]
    return None


def _op_if_not(state, frame, op, request, answer):
    if eval_cond(state, frame.owner, op[1]):
        frame.pc += 1
    else:
        frame.pc = op[2]
    return None


def _op_if_reg_empty(state, frame, op, request, answer):
    value = frame.regs[op[1]]
    empty = (value is None) or (isinstance(value, list) and not value) or value == 0
    frame.pc = op[2] if empty else frame.pc + 1
    return None


def _op_if_reg_le(state, frame, op, request, answer):
    value = frame.regs[op[1]]
    count = value if isinstance(value, int) else len(value)
    frame.pc = op[3] if count <= op[2] else frame.pc + 1
    return None


def _store_pick(frame, reg, request, answer):
    """Common tail for pick ops: store the chosen instance and advance."""
    frame.regs[reg] = request.candidates[answer]
    frame.pc += 1


def _op_pick_card(state, frame, op, request, answer):
    _, reg, sel = op
    _ensure_regs(frame, reg)
    if answer is not None:
        _store_pick(frame, reg, request, answer)
        return None
    candidates = eval_selector(state, frame.owner, sel)
    if not candidates:
        # Old engine: prompt on empty candidates returns None -> effect aborts.
        frame.pc = 10**6
        return None
    return select_card(P_EFFECT_TARGET, candidates)


def _op_pick_card_opt(state, frame, op, request, answer):
    """Declinable single-card pick ('may' effects). Emits a SelectCard with
    allow_pass; PASS (or no candidates) jumps to skip_target so dependent ops
    are skipped. On a real pick, stores the instance and falls through."""
    _, reg, sel, skip_target = op
    _ensure_regs(frame, reg)
    if answer is not None:
        if request.is_pass(answer):
            frame.pc = skip_target
        else:
            _store_pick(frame, reg, request, answer)
        return None
    candidates = eval_selector(state, frame.owner, sel)
    if not candidates:
        frame.pc = skip_target
        return None
    return select_card(P_EFFECT_TARGET, candidates, allow_pass=True)


def _op_pick_number(state, frame, op, request, answer):
    _, reg, lo, hi = op
    _ensure_regs(frame, reg)
    if answer is not None:
        frame.regs[reg] = answer
        frame.pc += 1
        return None
    hi_value = eval_expr(state, frame, hi)
    return select_number(P_EFFECT_NUMBER, lo, hi_value)


def _op_multiselect(state, frame, op, request, answer):
    """Old _prompt_card_multiselect: SelectNumber(min..len(candidates)),
    then that many sequential SelectCards. Result list lands in `reg`.
    Sub-state: frame.data = [stage, remaining_candidates, picked]."""
    _, reg, sel, min_cards = op
    _ensure_regs(frame, reg)
    if frame.step == 0:
        candidates = eval_selector(state, frame.owner, sel)
        if not candidates:
            frame.regs[reg] = []
            frame.pc += 1
            return None
        frame.step = 1
        frame.data = [0, list(candidates), []]  # [want, remaining, picked]
        return select_number(P_EFFECT_NUMBER, min_cards, len(candidates))
    want, remaining, picked = frame.data
    if frame.step == 1:  # number answered
        frame.data[0] = answer
        frame.step = 2
        if answer <= 0:
            return _finish_multiselect(frame, reg)
        return select_card(P_EFFECT_TARGET, remaining)
    # step 2: a card pick answered
    picked.append(request.candidates[answer])
    remaining.remove(request.candidates[answer])
    if len(picked) < frame.data[0] and remaining:
        return select_card(P_EFFECT_TARGET, remaining)
    return _finish_multiselect(frame, reg)


def _finish_multiselect(frame: Frame, reg: int):
    frame.regs[reg] = list(frame.data[2])
    frame.step = 0
    frame.data = []
    frame.pc += 1
    return None


def _op_picks_exact(state, frame, op, request, answer):
    """`count_expr` sequential single-card picks (no number prompt)."""
    _, reg, sel, count_expr = op
    _ensure_regs(frame, reg)
    if frame.step == 0:
        candidates = eval_selector(state, frame.owner, sel)
        count = min(eval_expr(state, frame, count_expr), len(candidates))
        if count <= 0:
            frame.regs[reg] = []
            frame.pc += 1
            return None
        frame.step = 1
        frame.data = [count, list(candidates), []]
        return select_card(P_EFFECT_TARGET, candidates)
    want, remaining, picked = frame.data
    picked.append(request.candidates[answer])
    remaining.remove(request.candidates[answer])
    if len(picked) < want and remaining:
        return select_card(P_EFFECT_TARGET, remaining)
    frame.regs[reg] = list(picked)
    frame.step = 0
    frame.data = []
    frame.pc += 1
    return None


def _op_name_guess(state, frame, op, request, answer):
    _, reg = op
    _ensure_regs(frame, reg)
    if answer is not None:
        frame.regs[reg] = answer
        frame.pc += 1
        return None
    return select_identity(P_NAME_GUESS, list(range(NUM_CARDS)))


def _op_atk_bonus(state, frame, op, request, answer):
    _side_player(state, frame, op[1]).flags[PF_ATTACK_BONUS] += eval_expr(state, frame, op[2])
    frame.pc += 1
    return None


def _op_dmg_reduce(state, frame, op, request, answer):
    _side_player(state, frame, op[1]).flags[PF_DAMAGE_REDUCTION] += eval_expr(state, frame, op[2])
    frame.pc += 1
    return None


def _op_atk_override(state, frame, op, request, answer):
    _side_player(state, frame, op[1]).flags[PF_ATTACK_OVERRIDE] = eval_expr(state, frame, op[2])
    frame.pc += 1
    return None


def _op_not_reducible(state, frame, op, request, answer):
    _side_player(state, frame, op[1]).flags[PF_DAMAGE_NOT_REDUCIBLE] = 1
    frame.pc += 1
    return None


def _op_reverse_day_night(state, frame, op, request, answer):
    _side_player(state, frame, op[1]).flags[PF_DAY_NIGHT_REVERSED] = 1
    frame.pc += 1
    return None


def _op_power_bonus(state, frame, op, request, answer):
    _side_player(state, frame, op[1]).flags[PF_POWER_BONUS] += eval_expr(state, frame, op[2])
    frame.pc += 1
    return None


def _op_heal(state, frame, op, request, answer):
    player = _side_player(state, frame, op[1])
    old_hp = player.hp
    player.hp = min(100, player.hp + eval_expr(state, frame, op[2]))
    if state.event_sink is not None and player.hp != old_hp:
        state.event_sink.append(
            (EVENT_HP_CHANGED, player.index, player.hp - old_hp, player.hp))
    frame.pc += 1
    return None


def _op_damage(state, frame, op, request, answer):
    from ..battle import deal_damage
    player = _side_player(state, frame, op[1])
    deal_damage(state, player.index, eval_expr(state, frame, op[2]))
    frame.pc += 1
    return None


def _op_eot_damage(state, frame, op, request, answer):
    _side_player(state, frame, op[1]).flags[PF_END_OF_TURN_DAMAGE] += eval_expr(state, frame, op[2])
    frame.pc += 1
    return None


def _op_reflect(state, frame, op, request, answer):
    _side_player(state, frame, op[1]).flags[PF_REFLECT_REDUCTION] = 1
    frame.pc += 1
    return None


def _op_adv_chronos(state, frame, op, request, answer):
    """Effect clock changes use single-compare transition tracking
    (old engine.set_chronos), not the step-by-step turn-phase variant."""
    from ..battle import set_chronos
    steps = eval_expr(state, frame, op[1])
    if steps:
        set_chronos(state, (state.chronos + steps) % CHRONOS_SIZE)
    frame.pc += 1
    return None


def _op_set_chronos_to(state, frame, op, request, answer):
    from ..battle import set_chronos
    set_chronos(state, eval_expr(state, frame, op[1]) % CHRONOS_SIZE)
    frame.pc += 1
    return None


def _op_midnight_extend(state, frame, op, request, answer):
    state.gflags[GF_MIDNIGHT_EXTENDED] = 1
    frame.pc += 1
    return None


def _op_draw(state, frame, op, request, answer):
    player = _side_player(state, frame, op[1])
    count = min(eval_expr(state, frame, op[2]), len(player.deck))
    if count > 0:
        draw_cards(state, player.index, count)
    frame.pc += 1
    return None


def _op_move_reg(state, frame, op, request, answer):
    """Move the card(s) in `reg` to (dst_zone, dst_side, order). The actor
    for placement triggers is always the effect owner."""
    _, reg, dst_zone, dst_side, order = op
    value = frame.regs[reg]
    instance_ids = value if isinstance(value, list) else [value]
    destination = _side_player(state, frame, dst_side)
    for instance_id in instance_ids:
        remove_from_current_zone(state, instance_id)
        if dst_zone == "abyss":
            place_in_abyss(state, instance_id, destination.index, frame.owner)
        elif dst_zone == "charger":
            place_in_charger(state, instance_id, destination.index, frame.owner)
        elif dst_zone == "deck":
            state.inst_face_up[instance_id] = 0
            state.inst_neg[instance_id] = 0
            state.inst_attr_ovr[instance_id] = -1
            if order == "top":
                destination.deck.insert(0, instance_id)
            else:
                destination.deck.append(instance_id)
        elif dst_zone == "hand":
            state.inst_face_up[instance_id] = 0
            state.inst_neg[instance_id] = 0
            state.inst_attr_ovr[instance_id] = -1
            destination.hand.append(instance_id)
        else:
            raise ValueError(f"move_reg: bad dst_zone {dst_zone!r}")
    frame.pc += 1
    return None


def _op_draw_exact(state, frame, op, request, answer):
    """Old can_draw-gated draw: all-or-nothing (draw n only if deck >= n)."""
    player = _side_player(state, frame, op[1])
    count = eval_expr(state, frame, op[2])
    if count > 0 and len(player.deck) >= count:
        draw_cards(state, player.index, count)
    frame.pc += 1
    return None


def _op_chronos_revert_turn_start(state, frame, op, request, answer):
    """01-008: raw assignment back to the turn-start time; the old code does
    NOT route through set_chronos, so no transition flags are recorded."""
    state.chronos = state.chronos_at_turn_start
    frame.pc += 1
    return None


def _op_chronos_back_opp_clock(state, frame, op, request, answer):
    """01-026: rewind to (turn_start - opponent's chronos contribution),
    via set_chronos (single-compare transition tracking), only if the
    opponent advanced the clock this turn."""
    from ..battle import set_chronos
    from ..state import PF_CHRONOS_ADVANCED
    opponent = state.players[1 - frame.owner]
    advanced = opponent.flags[PF_CHRONOS_ADVANCED]
    if advanced > 0:
        set_chronos(state, (state.chronos_at_turn_start - advanced) % CHRONOS_SIZE)
    frame.pc += 1
    return None


def _op_bounce_opp_area(state, frame, op, request, answer):
    """Move the opponent's area enchant to the top/bottom of their deck.
    `cleanup` mirrors the old code: 02-055/03-014/03-021 fire the leave-play
    cleanup (03-055 unblock); 03-055's own bounce does NOT."""
    from .removal import on_area_enchant_leaves_play
    _, order, cleanup = op
    opponent = state.players[1 - frame.owner]
    if opponent.set_c != -1:
        area = opponent.set_c
        opponent.set_c = -1
        state.inst_face_up[area] = 0
        if order == "top":
            opponent.deck.insert(0, area)
        else:
            opponent.deck.append(area)
        if cleanup:
            on_area_enchant_leaves_play(state, area, opponent.index)
    frame.pc += 1
    return None


def _op_name_guess_bonus(state, frame, op, request, answer):
    """03-047 family: +N attack when the guessed definition matches the
    opponent's hand card at the 1-based index chosen earlier."""
    _, guess_reg, index_reg, amount = op
    opponent = state.players[1 - frame.owner]
    revealed = opponent.hand[frame.regs[index_reg] - 1]
    if state.inst_def[revealed] == frame.regs[guess_reg]:
        state.players[frame.owner].flags[PF_ATTACK_BONUS] += amount
    frame.pc += 1
    return None


def _op_negate_opp_set_enchants(state, frame, op, request, answer):
    """04-041: negate the opponent's set-zone A/B ENCHANTs played this turn
    that are not already negated."""
    from ..cards import TYPE_ENCHANT
    opponent = state.players[1 - frame.owner]
    for slot in (opponent.set_a, opponent.set_b):
        if (slot != -1 and CARD_TYPE_T[state.inst_def[slot]] == TYPE_ENCHANT
                and state.inst_played[slot] and not state.inst_neg[slot]):
            state.inst_neg[slot] = 1
    frame.pc += 1
    return None


def _op_shuffle_reg(state, frame, op, request, answer):
    """Shuffle the list register in place (chance event, counter-based)."""
    frame.regs[op[1]] = shuffled(frame.regs[op[1]], state.rng_key, state.rng_ctr)
    state.rng_ctr += 1
    frame.pc += 1
    return None


def apply_self_defeat(state, player_index):
    """CHAOS bomb failure: the old code sets the player's HP to 0.

    Also records who did it and on which turn. That record is purely
    informational — the HP win check, the winner, and Game.returns() are
    identical with or without it, and it never enters the NN observation. The
    bot layer reads it to rate a deliberately thrown game differently.
    """
    player = state.players[player_index]
    old_hp = player.hp
    player.hp = 0
    if state.self_defeat_player == -1:
        state.self_defeat_player = player_index
        state.self_defeat_turn = state.turn
    if state.event_sink is not None and old_hp != 0:
        state.event_sink.append((EVENT_HP_CHANGED, player_index, -old_hp, 0))


def _op_lose_game(state, frame, op, request, answer):
    """CHAOS bomb failure: the owner loses the game."""
    apply_self_defeat(state, frame.owner)
    frame.pc += 1
    return None


def _op_deck_top_route(state, frame, op, request, answer):
    """Top card of side's deck -> their charger (if SEND TO POWER) else
    their abyss; the actor is the effect owner (02-041, self-placement)."""
    from ..zones import to_power_or_abyss
    player = _side_player(state, frame, op[1])
    if player.deck:
        instance_id = player.deck.pop(0)
        to_power_or_abyss(state, instance_id, player.index, frame.owner)
    frame.pc += 1
    return None


def _op_mill(state, frame, op, request, answer):
    player = _side_player(state, frame, op[1])
    count = min(eval_expr(state, frame, op[2]), len(player.deck))
    for _ in range(count):
        instance_id = player.deck.pop(0)
        place_in_abyss(state, instance_id, player.index, frame.owner)
    frame.pc += 1
    return None


def _op_reveal_reg(state, frame, op, request, answer):
    # Reveals are informational: they do not mutate any zone contents.
    frame.pc += 1
    return None


def _op_reveal_hand(state, frame, op, request, answer):
    frame.pc += 1
    return None


def _op_shuffle_hand(state, frame, op, request, answer):
    player = _side_player(state, frame, op[1])
    player.hand = shuffled(player.hand, state.rng_key, state.rng_ctr)
    state.rng_ctr += 1
    frame.pc += 1
    return None


def _op_hand_bonus(state, frame, op, request, answer):
    _side_player(state, frame, op[1]).pending_hand_bonus += 1
    frame.pc += 1
    return None


def _op_attr_override_enemy(state, frame, op, request, answer):
    enemy = state.players[1 - frame.owner]
    if enemy.battle != -1:
        state.inst_attr_ovr[enemy.battle] = op[1]
    frame.pc += 1
    return None


def _op_negate_reg(state, frame, op, request, answer):
    value = frame.regs[op[1]]
    for instance_id in (value if isinstance(value, list) else [value]):
        state.inst_neg[instance_id] = 1
    frame.pc += 1
    return None


def _op_block_area(state, frame, op, request, answer):
    _side_player(state, frame, op[1]).area_blocked = True
    frame.pc += 1
    return None


def _op_cost_reduce_set_chars(state, frame, op, request, answer):
    """02-006: reduce cost of own characters set this turn (A/B/battle)."""
    player = state.players[frame.owner]
    for slot in (player.set_a, player.set_b, player.battle):
        if (slot != -1 and state.inst_played[slot]
                and CARD_TYPE_T[state.inst_def[slot]] == TYPE_CHARACTER):
            state.inst_cost_red[slot] += op[1]
    frame.pc += 1
    return None


def _op_cost_reduce_battle_song(state, frame, op, request, answer):
    """04-065: reduce cost of own battle character of the given song."""
    player = state.players[frame.owner]
    if player.battle != -1 and SONG_T[state.inst_def[player.battle]] == op[1]:
        state.inst_cost_red[player.battle] += op[2]
    frame.pc += 1
    return None


def _op_pick_chronos(state, frame, op, request, answer):
    _, reg = op
    _ensure_regs(frame, reg)
    if answer is not None:
        frame.regs[reg] = answer
        frame.pc += 1
        return None
    return select_number(P_CHRONOS_VALUE, 0, CHRONOS_SIZE - 1)


OP_TABLE = {
    "end": _op_end,
    "jump": _op_jump,
    "if_not": _op_if_not,
    "if_reg_empty": _op_if_reg_empty,
    "if_reg_le": _op_if_reg_le,
    "pick_card": _op_pick_card,
    "pick_card_opt": _op_pick_card_opt,
    "pick_number": _op_pick_number,
    "multiselect": _op_multiselect,
    "picks_exact": _op_picks_exact,
    "name_guess": _op_name_guess,
    "pick_chronos": _op_pick_chronos,
    "atk_bonus": _op_atk_bonus,
    "dmg_reduce": _op_dmg_reduce,
    "atk_override": _op_atk_override,
    "not_reducible": _op_not_reducible,
    "reverse_day_night": _op_reverse_day_night,
    "power_bonus": _op_power_bonus,
    "heal": _op_heal,
    "damage": _op_damage,
    "eot_damage": _op_eot_damage,
    "reflect": _op_reflect,
    "adv_chronos": _op_adv_chronos,
    "set_chronos_to": _op_set_chronos_to,
    "midnight_extend": _op_midnight_extend,
    "draw": _op_draw,
    "draw_exact": _op_draw_exact,
    "move_reg": _op_move_reg,
    "mill": _op_mill,
    "deck_top_route": _op_deck_top_route,
    "chronos_revert_turn_start": _op_chronos_revert_turn_start,
    "chronos_back_opp_clock": _op_chronos_back_opp_clock,
    "bounce_opp_area": _op_bounce_opp_area,
    "name_guess_bonus": _op_name_guess_bonus,
    "negate_opp_set_enchants": _op_negate_opp_set_enchants,
    "shuffle_reg": _op_shuffle_reg,
    "lose_game": _op_lose_game,
    "reveal_reg": _op_reveal_reg,
    "reveal_hand": _op_reveal_hand,
    "shuffle_hand": _op_shuffle_hand,
    "hand_bonus": _op_hand_bonus,
    "attr_override_enemy": _op_attr_override_enemy,
    "negate_reg": _op_negate_reg,
    "block_area": _op_block_area,
    "cost_reduce_set_chars": _op_cost_reduce_set_chars,
    "cost_reduce_battle_song": _op_cost_reduce_battle_song,
}

OP_NAMES = frozenset(OP_TABLE)
