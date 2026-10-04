"""Custom handlers for effects that don't fit the linear IR.

Each is a step machine over Frame.step and Frame.data:
handler(state, frame, request, answer) -> DecisionRequest | None. Returning None
after pushing a frame runs that frame first; the handler then resumes at its saved
step. The entry's `cond` gates a handler like any other effect.
"""

from __future__ import annotations

from ..actions import select_card, P_EFFECT_TARGET
from ..cards import (
    CARD_TYPE_T, EFFECT_T, NO_EFFECT, POWER_COST_T, SEND_TO_POWER_T, SONG_T,
    SONG_NAMES, TYPE_CHARACTER, TYPE_ENCHANT,
)
from ..state import PF_POWER_BONUS, add_attack_modifier
from ..zones import place_in_abyss, place_in_charger
from .interpreter import (
    CUSTOM_HANDLERS, EFFECT_PROGRAMS, apply_self_defeat, _emit_reveal,
    remove_from_current_zone, start_effect,
)

SONG_SHADE = SONG_NAMES.index("SHADE")


def _register(name):
    def wrap(fn):
        CUSTOM_HANDLERS[name] = fn
        return fn
    return wrap


@_register("use_abyss_enchant")
def use_abyss_enchant(state, frame, request, answer):
    """01-006: resolve an enchant from your abyss (not another 01-006). The borrowed
    effect skips the power-cost check but keeps its own condition."""
    if frame.step == 0:
        player = state.players[frame.owner]
        candidates = [
            i for i in player.abyss
            if CARD_TYPE_T[state.inst_def[i]] == TYPE_ENCHANT
            and EFFECT_T[state.inst_def[i]] not in (NO_EFFECT, frame.effect_index)
        ]
        if not candidates:
            return None
        frame.step = 1
        return select_card(P_EFFECT_TARGET, candidates)
    if frame.step == 1:
        chosen = request.candidates[answer]
        frame.step = 2
        effect_index = EFFECT_T[state.inst_def[chosen]]
        if effect_index in EFFECT_PROGRAMS:
            start_effect(state, frame.owner, chosen, effect_index)
        return None
    return None


@_register("reveal_top_03_097")
def reveal_top_03_097(state, frame, request, answer):
    """03-097 (area): reveal the opponent's top card, which stays on top. At power cost
    6 or more, this card moves to the owner's charger (Q&A No.45), where it adds no
    power but still counts as a card (Q&A No.46)."""
    opponent = state.players[1 - frame.owner]
    # An empty deck fizzles, with no shortfall loss: no card leaves the deck (Q&A No.45),
    # and area enchants re-queue every turn.
    if not opponent.deck:
        return None
    top = opponent.deck[0]
    # Revealed to both players (Ground Rules 10.2.1); the card stays on top
    # (Ground Rules 10.3.2).
    _emit_reveal(state, frame, opponent.index, (top,))
    if POWER_COST_T[state.inst_def[top]] >= 6:
        owner = state.players[frame.owner]
        if owner.set_c == frame.source:
            owner.set_c = -1
            place_in_charger(state, frame.source, owner.index, frame.owner)
    return None


@_register("reveal_top_03_103")
def reveal_top_03_103(state, frame, request, answer):
    """03-103 (area): reveal the opponent's top card (it stays). Without SEND TO POWER,
    attack +30; otherwise this card moves to the owner's charger."""
    opponent = state.players[1 - frame.owner]
    # Fizzles on an empty deck, as in reveal_top_03_097.
    if not opponent.deck:
        return None
    top = opponent.deck[0]
    owner = state.players[frame.owner]
    # Revealed to both players (Ground Rules 10.2.1).
    _emit_reveal(state, frame, opponent.index, (top,))
    if SEND_TO_POWER_T[state.inst_def[top]] == 0:
        add_attack_modifier(owner, 30)
    elif owner.set_c == frame.source:
        # Move this card only while it is still the area in play; otherwise one
        # instance would end up in two zones.
        owner.set_c = -1
        place_in_charger(state, frame.source, owner.index, frame.owner)
    return None


@_register("additional_enchant_02_015")
def additional_enchant_02_015(state, frame, request, answer):
    """02-015: you may use another enchant from your hand, then draw 1. An unaffordable
    enchant is still played, without its effect. The draw always happens."""
    from ..battle import total_power, effective_power_cost
    owner = state.players[frame.owner]
    if frame.step == 0:
        candidates = [
            i for i in owner.hand
            if CARD_TYPE_T[state.inst_def[i]] == TYPE_ENCHANT
            and EFFECT_T[state.inst_def[i]] != NO_EFFECT
        ]
        if not candidates:
            frame.step = 3
            return _draw_one_02_015(state, frame)
        frame.step = 1
        return select_card(P_EFFECT_TARGET, candidates, allow_pass=True)
    if frame.step == 1:
        if request.is_pass(answer):  # declined the enchant; still draw
            frame.step = 3
            return _draw_one_02_015(state, frame)
        chosen = request.candidates[answer]
        frame.data = [chosen]
        frame.step = 2
        effect_index = EFFECT_T[state.inst_def[chosen]]
        available_power = total_power(state, owner) + owner.flags[PF_POWER_BONUS]
        # Place the enchant before resolving it: its effect can end the game, which
        # drops all pending frames.
        if chosen in owner.hand:
            owner.hand.remove(chosen)
        if SEND_TO_POWER_T[state.inst_def[chosen]] > 0:
            place_in_charger(state, chosen, owner.index, frame.owner)
        else:
            place_in_abyss(state, chosen, owner.index, frame.owner)
        # The effect triggers only if the owner can pay the enchant's cost.
        if (effect_index in EFFECT_PROGRAMS
                and available_power >= effective_power_cost(state, chosen)):
            depth_before = len(state.frame_stack)
            start_effect(state, frame.owner, chosen, effect_index)
            if len(state.frame_stack) > depth_before:
                return None  # nested frame runs first; we resume at step 2
        # No effect frame: go straight to the draw.
    if frame.step == 2:
        frame.step = 3
        return _draw_one_02_015(state, frame)
    return None


def _draw_one_02_015(state, frame):
    """02-015's final draw. An empty deck loses the game (Ground Rules 8.2.1)."""
    from ..zones import draw_cards
    from .interpreter import _record_deck_shortfall
    owner = state.players[frame.owner]
    if owner.deck:
        draw_cards(state, owner.index, 1)
    else:
        _record_deck_shortfall(state, owner.index)
    return None


@_register("chaos_04_006")
def chaos_04_006(state, frame, request, answer):
    """04-006: bank 4 abyss cards (shuffled onto the deck bottom) or lose. Then, if the
    opponent has a battle character and a charger character, the picked charger character
    replaces their battle character, negated; the replaced one goes to their charger
    (forced, owner as actor)."""
    from ..rng import shuffled
    owner = state.players[frame.owner]
    opponent = state.players[1 - frame.owner]
    if frame.step == 0:
        if len(owner.abyss) < 4:
            apply_self_defeat(state, frame.owner)
            return None
        frame.data = [list(owner.abyss), []]  # [remaining, picked]
        frame.step = 1
        return select_card(P_EFFECT_TARGET, frame.data[0])
    if frame.step == 1:
        remaining, picked = frame.data
        picked.append(request.candidates[answer])
        remaining.remove(request.candidates[answer])
        if len(picked) < 4:
            return select_card(P_EFFECT_TARGET, remaining)
        for instance_id in picked:
            owner.abyss.remove(instance_id)
        picked = shuffled(picked, state.rng_key, state.rng_ctr)
        state.rng_ctr += 1
        for instance_id in picked:
            state.inst_face_up[instance_id] = 0
            state.inst_neg[instance_id] = 0
            owner.deck.append(instance_id)
        candidates = [i for i in opponent.charger
                      if CARD_TYPE_T[state.inst_def[i]] == TYPE_CHARACTER]
        if not candidates or opponent.battle == -1:
            return None
        frame.step = 2
        return select_card(P_EFFECT_TARGET, candidates)
    # step 2: swap the picked charger character into the opponent's battle zone
    chosen = request.candidates[answer]
    old_battle = opponent.battle
    place_in_charger(state, old_battle, opponent.index, frame.owner)
    opponent.charger.remove(chosen)
    state.inst_face_up[chosen] = 1
    state.inst_neg[chosen] = 1
    opponent.battle = chosen
    return None


@_register("chaos_04_088")
def chaos_04_088(state, frame, request, answer):
    """04-088: bank 1 abyss card to the deck bottom or lose. Then reorder the opponent's
    top 3 with position picks (the last is automatic; skipped at 1 card or fewer)."""
    owner = state.players[frame.owner]
    opponent = state.players[1 - frame.owner]
    if frame.step == 0:
        if not owner.abyss:
            apply_self_defeat(state, frame.owner)
            return None
        frame.step = 1
        return select_card(P_EFFECT_TARGET, list(owner.abyss))
    if frame.step == 1:
        chosen = request.candidates[answer]
        owner.abyss.remove(chosen)
        state.inst_face_up[chosen] = 0
        state.inst_neg[chosen] = 0
        owner.deck.append(chosen)
        # A clamp, not a shortfall loss: the cards are only reordered within the
        # deck, and Ground Rules 8.2.1 turns on cards leaving it.
        view_count = min(3, len(opponent.deck))
        if view_count <= 1:
            return None
        frame.data = [view_count, list(opponent.deck[:view_count]), []]
        frame.step = 2
        return select_card(P_EFFECT_TARGET, frame.data[1])
    # step 2: position picks
    view_count, remaining, reordered = frame.data
    reordered.append(request.candidates[answer])
    remaining.remove(request.candidates[answer])
    if len(remaining) > 1:
        return select_card(P_EFFECT_TARGET, remaining)
    reordered.extend(remaining)
    opponent.deck[:view_count] = reordered
    return None


def _shade_charger_candidates(state, owner) -> list[int]:
    """SHADE characters with an effect in `owner`'s charger, minus cards already
    resolving in this chain. Otherwise 04-002 could re-select itself forever, since it
    cannot choose zero (Q&A No.79); distinct cards still chain (Q&A No.83)."""
    resolving = {f.source for f in state.frame_stack}
    return [
        i for i in owner.charger
        if i not in resolving
        and SONG_T[state.inst_def[i]] == SONG_SHADE
        and CARD_TYPE_T[state.inst_def[i]] == TYPE_CHARACTER
        and EFFECT_T[state.inst_def[i]] != NO_EFFECT
    ]


@_register("shade_use_one")
def shade_use_one(state, frame, request, answer):
    """04-094 (area): resolve the effect of a SHADE character in your charger,
    without a cost check."""
    owner = state.players[frame.owner]
    if frame.step == 0:
        candidates = _shade_charger_candidates(state, owner)
        if not candidates:
            return None
        frame.step = 1
        return select_card(P_EFFECT_TARGET, candidates)
    if frame.step == 1:
        chosen = request.candidates[answer]
        frame.step = 2
        effect_index = EFFECT_T[state.inst_def[chosen]]
        if effect_index in EFFECT_PROGRAMS:
            start_effect(state, frame.owner, chosen, effect_index)
        return None
    return None


@_register("shade_use_two")
def shade_use_two(state, frame, request, answer):
    """04-002: pick 1 or 2 SHADE characters in your charger, then resolve their effects
    in pick order, without cost checks."""
    from ..actions import select_number, P_EFFECT_NUMBER
    owner = state.players[frame.owner]
    if frame.step == 0:
        candidates = _shade_charger_candidates(state, owner)
        if not candidates:
            return None
        frame.data = [0, list(candidates), []]  # [want, remaining, picked]
        frame.step = 1
        # The charger is public, so "up to 2" means 1 to 2 (Ground Rules 1.3.5.1;
        # Q&A No.79).
        return select_number(P_EFFECT_NUMBER, 1, min(2, len(candidates)))
    if frame.step == 1:
        frame.data[0] = answer
        if answer <= 0:
            return None
        frame.step = 2
        return select_card(P_EFFECT_TARGET, frame.data[1])
    if frame.step == 2:
        want, remaining, picked = frame.data
        picked.append(request.candidates[answer])
        remaining.remove(request.candidates[answer])
        if len(picked) < want and remaining:
            return select_card(P_EFFECT_TARGET, remaining)
        frame.step = 3
        # fall through to dispatch loop
    # step 3: dispatch each pick, one nested frame at a time. A failed condition
    # pushes nothing.
    picked = frame.data[2]
    while picked:
        next_card = picked.pop(0)
        effect_index = EFFECT_T[state.inst_def[next_card]]
        if effect_index in EFFECT_PROGRAMS:
            depth_before = len(state.frame_stack)
            start_effect(state, frame.owner, next_card, effect_index)
            if len(state.frame_stack) > depth_before:
                return None  # resume here after the nested frame pops
    return None
