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
    """03-097 (area): the opponent reveals their deck top. If its power cost is 6
    or more, THIS card goes to the owner's charger.

    Q&A No.45 is explicit about both halves: 「公開した相手のデッキの一番上のカードは、
    再び相手のデッキの一番上に戻します。公開したカードのパワーコストが★6以上の場合、
    すぐに「厳戒態勢」をパワーチャージャーに置きます」 — the revealed card stays on top of
    the deck (it is only looked at), and it is 03-097 itself that is placed. The engine
    used to move the revealed card onto the opponent's charger instead, which both
    stole a card and put the wrong one in play.

    Q&A No.46: once in the charger this card contributes no power (SEND TO POWER 0)
    but still counts for effects that count cards or attributes there — which falls out
    of placing the real instance.
    """
    opponent = state.players[1 - frame.owner]
    # Deliberate fizzle, NOT the deck-shortfall loss the draw/mill/deck_top_route
    # ops record. The discriminator is whether cards LEAVE the deck zone: this one
    # only looks (Q&A No.45 puts the card straight back), so no processing fails.
    # It also has to fizzle: an area enchant is re-queued every turn with no
    # inst_played check, so a loss here would kill a player every turn once their
    # deck hits 0 and gut the Q&A No.92 boundary.
    if not opponent.deck:
        return None
    top = opponent.deck[0]
    # 「公開する」: both players see it (GR 10.2.1/10.4.1), so announce it. The card
    # itself does not move -- Q&A No.45 puts it straight back on top of the deck.
    _emit_reveal(state, frame, opponent.index, (top,))
    if POWER_COST_T[state.inst_def[top]] >= 6:
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
    # Deliberate fizzle for the same two reasons as reveal_top_03_097: nothing
    # leaves the deck zone, and an area enchant re-queues every turn.
    if not opponent.deck:
        return None
    top = opponent.deck[0]
    owner = state.players[frame.owner]
    # 「デッキの一番上を公開する」: both players see it (GR 10.2.1/10.4.1).
    _emit_reveal(state, frame, opponent.index, (top,))
    if SEND_TO_POWER_T[state.inst_def[top]] == 0:
        add_attack_modifier(owner, 30)
    elif owner.set_c == frame.source:
        # Same guard as reveal_top_03_097: only move this card if it is still the
        # area enchant in play. Without it, a card removed earlier in the turn would
        # be placed a second time while clearing whatever now occupies set_c --
        # putting one instance in two zones.
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
        # Move the used enchant out of hand BEFORE resolving it. The card is used at
        # this moment, and the nested effect can end the game (04-027/04-028/04-105
        # can self-defeat, and mill/draw can deck a player out); once a winner is set
        # the interpreter drops every pending frame, so anything deferred to step 2
        # would silently never happen and the enchant would still show in hand.
        if chosen in owner.hand:
            owner.hand.remove(chosen)
        if SEND_TO_POWER_T[state.inst_def[chosen]] > 0:
            place_in_charger(state, chosen, owner.index, frame.owner)
        else:
            place_in_abyss(state, chosen, owner.index, frame.owner)
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
        # fall through to step 2 (the draw) in this call.
    if frame.step == 2:
        frame.step = 3
        return _draw_one_02_015(state, frame)
    return None


def _draw_one_02_015(state, frame):
    """02-015's trailing 「その後、カードを1枚引く」.

    An empty deck is a loss, not a no-op: Ground Rules 8.2.1 says a player who cannot
    draw the number an effect names loses at that moment. This used to be guarded by
    `if owner.deck`, which made it the one draw in the engine that could fail
    silently.
    """
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
        # This clamp is deliberate, and is NOT the silent-clamp defect that
        # draw/mill/deck_top_route record as a loss. 04-088 names a count (3),
        # but it only looks at and reorders cards WITHIN the deck -- nothing
        # leaves the deck zone (see opponent.deck[:view_count] = reordered
        # below), so there is no processing that can fail short. Cards leaving
        # the zone, not a named count, is what Ground Rules 8.2.1 turns on.
        # (Scoped to this line: the empty-abyss branch above is a real
        # bank-or-lose self-defeat, not a fizzle.)
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
    """SHADE characters with an effect in `owner`'s charger, excluding any card
    already resolving further up this chain.

    Q&A No.83 allows a 04-002 to designate another 04-002 and keep going
    (「さらに２枚を指定することが可能です」), and its worked example totals three cards, so
    the nesting is meant to terminate. Nothing in the rules says how, and without a
    bound it does not: 04-002 is itself a SHADE character with an effect, so it
    re-selects itself (or an A -> B -> A pair) forever -- and since Q&A No.79 forbids
    choosing zero, the player has no escape. Excluding the frames already on the stack
    is the narrowest rule that terminates: a card may not re-enter its own resolution,
    while distinct cards can still be chained as No.83 intends.

    (`Frame.source` is the resolving card's instance and is preserved by fast_clone,
    so this needs no extra frame state and stays correct across search clones.)
    """
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
        # Q&A No.79 (which names this card) and Ground Rules 1.3.5.1: the power
        # charger is public, so when valid targets are visible the player must
        # pick at least one -- "up to 2" means 1 to 2, not 0 to 2.
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
