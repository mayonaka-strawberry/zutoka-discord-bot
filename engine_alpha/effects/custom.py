"""Custom-coded effect handlers: effects whose semantics don't fit the
linear IR. Each is an explicit step machine over Frame.step / Frame.data /
Frame.regs (same pause protocol as interpreter ops), registered into
interpreter.CUSTOM_HANDLERS by name.

Signature: handler(state, frame, request, answer) -> DecisionRequest | None.
Returning None with a NEW frame pushed on top hands control to that frame;
the custom frame resumes (at its saved step) after the nested frame pops.
Handlers evaluate their own conditions (start_effect pushes custom frames
unconditionally).
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
    CUSTOM_HANDLERS, EFFECT_PROGRAMS, apply_self_defeat,
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
    """01-006: choose an enchant card in your own abyss (excluding copies of
    this effect) and resolve its effect this turn. The borrowed effect is
    dispatched WITHOUT a power-cost check (old code calls _dispatch directly);
    its own gate condition still applies."""
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
    """03-097 (area): reveal opponent's deck top. If its power cost >= 6,
    move it onto THEIR charger (actor = effect owner, so no owner-placement
    flags), and send this area enchant to the OWNER's charger (actor = owner,
    so the owner's placement flags DO fire — old code)."""
    opponent = state.players[1 - frame.owner]
    if not opponent.deck:
        return None
    top = opponent.deck[0]
    if POWER_COST_T[state.inst_def[top]] >= 6:
        opponent.deck.pop(0)
        place_in_charger(state, top, opponent.index, frame.owner)
        owner = state.players[frame.owner]
        if owner.set_c == frame.source:
            owner.set_c = -1
            place_in_charger(state, frame.source, owner.index, frame.owner)
    return None


@_register("reveal_top_03_103")
def reveal_top_03_103(state, frame, request, answer):
    """03-103 (area): reveal opponent's deck top (it stays). No SEND TO
    POWER -> attack +30; otherwise this area enchant moves to the owner's
    charger (owner-actor placement flags fire — old code)."""
    opponent = state.players[1 - frame.owner]
    if not opponent.deck:
        return None
    top = opponent.deck[0]
    owner = state.players[frame.owner]
    if SEND_TO_POWER_T[state.inst_def[top]] == 0:
        add_attack_modifier(owner, 30)
    else:
        owner.set_c = -1
        place_in_charger(state, frame.source, owner.index, frame.owner)
    return None


@_register("additional_enchant_02_015")
def additional_enchant_02_015(state, frame, request, answer):
    """02-015: (condition prev-dark + day is on the IR entry) you may use an
    additional enchant from your hand. Candidates are the effect-bearing
    enchants in hand; power does NOT gate whether the enchant can be played,
    only whether its effect triggers (an unaffordable enchant is still placed
    and the draw still happens). The used enchant then goes to charger/abyss;
    finally draw 1 if the deck is non-empty. The pick is declinable (card text:
    "may use"); skipping goes straight to the draw. The draw happens even when
    no enchant was available, used, or declined (top-level draw)."""
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
        # The enchant is played regardless of power; its effect only triggers
        # when the owner can pay the enchant's power cost. Otherwise it is
        # placed with no effect (and the top-level draw still happens).
        if (effect_index in EFFECT_PROGRAMS
                and available_power >= effective_power_cost(state, chosen)):
            depth_before = len(state.frame_stack)
            start_effect(state, frame.owner, chosen, effect_index)
            if len(state.frame_stack) > depth_before:
                return None  # nested frame runs first; we resume at step 2
        # Effect did not trigger (unaffordable, or the gate pushed no frame):
        # fall through to step 2 (move the used enchant, draw) in this call.
    if frame.step == 2:
        chosen = frame.data[0]
        if chosen in owner.hand:
            owner.hand.remove(chosen)
        if SEND_TO_POWER_T[state.inst_def[chosen]] > 0:
            place_in_charger(state, chosen, owner.index, frame.owner)
        else:
            place_in_abyss(state, chosen, owner.index, frame.owner)
        frame.step = 3
        return _draw_one_02_015(state, frame)
    return None


def _draw_one_02_015(state, frame):
    from ..zones import draw_cards
    owner = state.players[frame.owner]
    if owner.deck:
        draw_cards(state, owner.index, 1)
    return None


@_register("chaos_04_006")
def chaos_04_006(state, frame, request, answer):
    """04-006: pick 4 abyss cards (sequential, no number prompt) -> shuffled
    to deck bottom, else lose (HP 0). Then, if the opponent has a charger
    character AND a battle character: pick one charger character; the old
    battle character goes to their charger (forced placement, actor = effect
    owner), the picked one enters their battle zone with effects negated."""
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
    """04-088: pick 1 abyss card -> deck bottom (no shuffle), else lose.
    Then look at the opponent's top-3 and reorder via sequential position
    picks (auto-placed when one remains; skipped entirely at <=1 card)."""
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
    return [
        i for i in owner.charger
        if SONG_T[state.inst_def[i]] == SONG_SHADE
        and CARD_TYPE_T[state.inst_def[i]] == TYPE_CHARACTER
        and EFFECT_T[state.inst_def[i]] != NO_EFFECT
    ]


@_register("shade_use_one")
def shade_use_one(state, frame, request, answer):
    """04-094 (area): pick one SHADE character in your charger and resolve
    its effect (dispatched without cost check — old _dispatch)."""
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
    """04-002: number prompt (0..min(2, candidates)) then that many picks;
    each picked SHADE character's effect resolves in pick order (nested,
    without cost check)."""
    from ..actions import select_number, P_EFFECT_NUMBER
    owner = state.players[frame.owner]
    if frame.step == 0:
        candidates = _shade_charger_candidates(state, owner)
        if not candidates:
            return None
        frame.data = [0, list(candidates), []]  # [want, remaining, picked]
        frame.step = 1
        return select_number(P_EFFECT_NUMBER, 0, min(2, len(candidates)))
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
    # step 3: dispatch picked effects one at a time (each nested frame
    # completes before the next is pushed). start_effect may push nothing
    # when the borrowed effect's gate condition fails — continue then.
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
