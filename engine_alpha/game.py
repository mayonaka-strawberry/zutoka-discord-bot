"""The Game facade and resumable phase driver.

The engine is an explicit state machine: _advance() executes deterministic
work (phase transitions, chance events via the state RNG, effect frames)
until it either needs a player decision — it then sets state.pending and
returns — or the game ends. apply(action) answers the pending request and
re-enters _advance(). No coroutines or generators anywhere, so a Game can
be cloned at any decision point and every branch explored.

Phase flow (mirrors the old engine's play_full_game/_do_headless_turn):
  DRAFT -> MULLIGAN -> INITIAL_SET -> INITIAL_REVEAL ->
  turn 1: ADVANCE_CHRONOS -> PROCESS_EFFECTS -> BATTLE -> END_TURN
  turn 2+: SET_CARDS -> REVEAL -> ADVANCE_CHRONOS -> CHARACTER_SWAP ->
           AREA_SWAP -> PROCESS_EFFECTS -> BATTLE -> TURN_END_EFFECTS -> END_TURN

Set-card commitment is sequential (previous-battle loser first; tie or
turn 1 -> NIGHT-side player first): sequentialization yields a plain
alternating tree that PUCT can search.
"""

from __future__ import annotations

from .actions import (
    DecisionRequest,
    P_DRAFT_PICK, P_MULLIGAN, P_INITIAL_CARD, P_SET_SLOT_A, P_SET_SLOT_B,
    P_EFFECT_ORDER, P_SKIP_SWAP,
    select_card, select_identity, binary,
)
from .battle import (
    MIDNIGHT, all_clocks_one, advance_chronos_by, check_win,
    effective_power_cost, get_effective_attack, opponent_clock_disabled,
    resolve_battle, total_power,
)
from .cards import (
    CARD_TYPE_T, CLOCK_T, EFFECT_T, SONG_T,
    TYPE_CHARACTER, TYPE_ENCHANT, TYPE_AREA_ENCHANT,
    EFFECT_TO_INDEX, NO_EFFECT,
)
from .draft import DECK_SIZE, legal_picks, validate_deck
from .effects import dispatch
from .events import (
    EVENT_AREA_PLACED, EVENT_CHARACTER_SWAP, EVENT_EFFECT_SKIPPED_COST,
    EVENT_GAME_OVER, EVENT_MULLIGAN_DONE, EVENT_PHASE_CHANGED,
)
from .effects.removal import check_area_removal, on_area_enchant_leaves_play
from .effects.turn_end import process_end_of_turn_effects
from .rng import random_below, shuffled
from .state import (
    GameState, PlayerState, DraftState, _fresh_player_flags,
    PH_DRAFT, PH_MULLIGAN, PH_INITIAL_SET, PH_INITIAL_REVEAL, PH_SET_CARDS,
    PH_REVEAL, PH_ADVANCE_CHRONOS, PH_CHARACTER_SWAP, PH_AREA_SWAP,
    PH_PROCESS_EFFECTS, PH_BATTLE, PH_TURN_END_EFFECTS, PH_END_TURN,
    PH_GAME_OVER,
    PF_POWER_BONUS, PF_CHRONOS_ADVANCED,
    N_GLOBAL_FLAGS,
)
from array import array

FX_02_062 = EFFECT_TO_INDEX["02-062"]

OPENING_HAND = 5
STARTING_HP = 100


class Game:
    __slots__ = ("state", "max_turns", "_answer", "_answered_request")

    def __init__(self, seed: int, mode: str = "draft",
                 decks: tuple[list[int], list[int]] | None = None,
                 max_turns: int = 200,
                 night_player: int | None = None) -> None:
        self.max_turns = max_turns
        self._answer = None
        self._answered_request = None

        state = GameState(rng_key=seed)
        self.state = state

        # Coin flip: which player index sits on the NIGHT side. The draw is
        # consumed unconditionally so the RNG stream is identical whether or
        # not the caller overrides the result (TCG series after game 1, where
        # the previous game's loser picks their side).
        flipped_night_player = random_below(2, state.rng_key, state.rng_ctr)
        state.rng_ctr += 1
        if night_player is None:
            night_player = flipped_night_player
        elif night_player not in (0, 1):
            raise ValueError(f"night_player must be 0, 1 or None, got {night_player!r}")
        state.players = (
            PlayerState(0, side_is_night=(night_player == 0), hp=STARTING_HP),
            PlayerState(1, side_is_night=(night_player == 1), hp=STARTING_HP),
        )
        state.chronos = MIDNIGHT
        state.chronos_at_turn_start = MIDNIGHT

        if mode == "draft":
            state.draft = DraftState()
            state.phase = PH_DRAFT
        elif mode == "fixed_decks":
            if decks is None:
                raise ValueError("fixed_decks mode requires decks")
            validate_deck(decks[0])
            validate_deck(decks[1])
            self._setup_match(state, (list(decks[0]), list(decks[1])))
        else:
            raise ValueError(f"unknown mode {mode!r}")

        self._advance()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def current_player(self) -> int:
        return self.state.acting

    def decision_context(self) -> DecisionRequest:
        return self.state.pending

    def legal_actions(self) -> list[int]:
        return self.state.pending.legal_actions()

    def apply(self, action: int) -> None:
        state = self.state
        request = state.pending
        if request is None:
            raise RuntimeError("no pending decision")
        if not request.is_legal(action):
            raise ValueError(f"illegal action {action} for {request!r}")
        state.pending = None
        self._answer = action
        self._answered_request = request
        self._advance()

    def clone(self) -> "Game":
        clone = Game.__new__(Game)
        clone.state = self.state.fast_clone()
        clone.max_turns = self.max_turns
        clone._answer = None
        clone._answered_request = None
        return clone

    def is_terminal(self) -> bool:
        return self.state.winner != -1

    def returns(self) -> tuple[float, float]:
        winner = self.state.winner
        if winner == 0:
            return (1.0, -1.0)
        if winner == 1:
            return (-1.0, 1.0)
        return (0.0, 0.0)

    # ------------------------------------------------------------------
    # Driver
    # ------------------------------------------------------------------

    def _advance(self) -> None:
        state = self.state
        handlers = _PHASE_HANDLERS
        while state.winner == -1:
            answer = self._answer
            request = self._answered_request
            self._answer = None
            self._answered_request = None
            if state.frame_stack:
                # Active effect resolution owns the decision stream.
                from .effects import interpreter
                pending = interpreter.resume(state, request, answer)
                if pending is not None:
                    state.pending = pending
                    return
                continue  # frames drained; phase handler resumes from its ctx
            phase_before = state.phase
            pending = handlers[state.phase](self, state, request, answer)
            if state.event_sink is not None and state.phase != phase_before:
                state.event_sink.append((EVENT_PHASE_CHANGED, state.phase, state.turn))
            if pending is not None:
                state.pending = pending
                return
        state.phase = PH_GAME_OVER
        state.pending = None
        state.acting = -1
        if state.event_sink is not None:
            state.event_sink.append((EVENT_GAME_OVER, state.winner))

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def _setup_match(self, state: GameState, deck_defs: tuple[list[int], list[int]]) -> None:
        for player_index in (0, 1):
            player = state.players[player_index]
            instances = [state.new_instance(d) for d in deck_defs[player_index]]
            player.deck = shuffled(instances, state.rng_key, state.rng_ctr)
            state.rng_ctr += 1
        for player_index in (0, 1):
            _draw(state, player_index, OPENING_HAND)
        state.phase = PH_MULLIGAN
        state.phase_ctx = [0, []]

    # ------------------------------------------------------------------
    # Phase handlers. Each returns a DecisionRequest to pause, or None
    # after advancing state.phase. `request`/`answer` echo the decision
    # this handler previously issued (None on fresh entry).
    # ------------------------------------------------------------------

    def _ph_draft(self, state: GameState, request, answer):
        draft = state.draft
        night_first = 0 if state.players[0].side_is_night else 1
        if answer is not None:
            picker = night_first if draft.pick_number % 2 == 0 else 1 - night_first
            draft.decks[picker].append(answer)
            draft.pick_number += 1
        if draft.pick_number >= 2 * DECK_SIZE:
            decks = (list(draft.decks[0]), list(draft.decks[1]))
            state.draft = None
            self._setup_match(state, decks)
            return None
        picker = night_first if draft.pick_number % 2 == 0 else 1 - night_first
        state.acting = picker
        return select_identity(P_DRAFT_PICK, legal_picks(draft.decks[picker]))

    def _ph_mulligan(self, state: GameState, request, answer):
        ctx = state.phase_ctx  # [player_pos, marked list]
        if answer is not None:
            player = state.players[ctx[0]]
            if request.is_pass(answer):
                marked = ctx[1]
                if marked:
                    # Old-engine order: remove marked, draw replacements,
                    # return marked to the deck bottom, shuffle.
                    for instance_id in marked:
                        player.hand.remove(instance_id)
                    _draw(state, player.index, len(marked))
                    player.deck.extend(marked)
                    player.deck = shuffled(player.deck, state.rng_key, state.rng_ctr)
                    state.rng_ctr += 1
                if state.event_sink is not None:
                    state.event_sink.append(
                        (EVENT_MULLIGAN_DONE, player.index, len(marked)))
                ctx[0] += 1
                ctx[1] = []
            else:
                ctx[1].append(request.candidates[answer])
        while ctx[0] < 2:
            player = state.players[ctx[0]]
            marked = ctx[1]
            candidates = [i for i in player.hand if i not in marked]
            state.acting = player.index
            return select_card(P_MULLIGAN, candidates, allow_pass=True)
        state.phase = PH_INITIAL_SET
        night_first = 0 if state.players[0].side_is_night else 1
        state.phase_ctx = [[night_first, 1 - night_first], 0]
        return None

    def _ph_initial_set(self, state: GameState, request, answer):
        ctx = state.phase_ctx  # [order, pos]
        order = ctx[0]
        if answer is not None:
            player = state.players[order[ctx[1]]]
            instance_id = request.candidates[answer]
            player.hand.remove(instance_id)
            state.inst_face_up[instance_id] = 0
            state.inst_played[instance_id] = 1
            player.battle = instance_id
            player.cards_played += 1
            ctx[1] += 1
        while ctx[1] < 2:
            player = state.players[order[ctx[1]]]
            state.acting = player.index
            return select_card(P_INITIAL_CARD, list(player.hand))
        state.phase = PH_INITIAL_REVEAL
        state.phase_ctx = []
        return None

    def _ph_initial_reveal(self, state: GameState, request, answer):
        for player in state.players:
            if player.battle == -1:
                continue
            state.inst_face_up[player.battle] = 1
            if CARD_TYPE_T[state.inst_def[player.battle]] != TYPE_CHARACTER:
                instance_id = player.battle
                player.battle = -1
                _to_power_or_abyss(state, instance_id, player.index)
        state.turn = 1
        state.chronos_at_turn_start = state.chronos
        state.phase = PH_ADVANCE_CHRONOS
        state.phase_ctx = []
        return None

    def _ph_set_cards(self, state: GameState, request, answer):
        ctx = state.phase_ctx  # [order, pos, slot]
        order = ctx[0]
        if answer is not None:
            player = state.players[order[ctx[1]]]
            if request.is_pass(answer):
                ctx[1] += 1
                ctx[2] = 0
            else:
                instance_id = request.candidates[answer]
                _set_card(state, player, instance_id, slot_b=(ctx[2] == 1))
                if ctx[2] == 0 and _max_cards_to_set(state, player) >= 2 and player.hand:
                    ctx[2] = 1
                else:
                    ctx[1] += 1
                    ctx[2] = 0
        while ctx[1] < 2:
            player = state.players[order[ctx[1]]]
            if ctx[2] == 0:
                if min(_max_cards_to_set(state, player), len(player.hand)) == 0:
                    ctx[1] += 1
                    continue
                state.acting = player.index
                return select_card(P_SET_SLOT_A, list(player.hand), allow_pass=True)
            state.acting = player.index
            return select_card(P_SET_SLOT_B, list(player.hand), allow_pass=True)
        state.phase = PH_REVEAL
        state.phase_ctx = []
        return None

    def _ph_reveal(self, state: GameState, request, answer):
        for player in state.players:
            if player.set_a != -1:
                state.inst_face_up[player.set_a] = 1
            if player.set_b != -1:
                state.inst_face_up[player.set_b] = 1
        state.phase = PH_ADVANCE_CHRONOS
        state.phase_ctx = []
        return None

    def _ph_advance_chronos(self, state: GameState, request, answer):
        clocks_one = all_clocks_one(state)
        for player in state.players:
            clock_disabled = opponent_clock_disabled(state, player.index)
            total_clock = 0
            for instance_id in _cards_played_this_turn(state, player):
                if clock_disabled and CARD_TYPE_T[state.inst_def[instance_id]] == TYPE_CHARACTER:
                    continue
                total_clock += 1 if clocks_one else CLOCK_T[state.inst_def[instance_id]]
            advance_chronos_by(state, total_clock)
            player.flags[PF_CHRONOS_ADVANCED] = total_clock
        if state.turn == 1:
            state.phase = PH_PROCESS_EFFECTS
            state.phase_ctx = [0, state.priority_player, 0, [], [], 0, 0]
        else:
            state.phase = PH_CHARACTER_SWAP
            state.phase_ctx = [0, -1]
        return None

    def _ph_character_swap(self, state: GameState, request, answer):
        ctx = state.phase_ctx  # [pos, pending_new_char]
        if answer is not None:
            player = state.players[ctx[0]]
            if answer == 0:  # swap normally
                _perform_character_swap(state, player, ctx[1])
            ctx[0] += 1
            ctx[1] = -1
        while ctx[0] < 2:
            player = state.players[ctx[0]]
            new_character = _incoming_of_type(state, player, TYPE_CHARACTER)
            if new_character == -1:
                ctx[0] += 1
                continue
            skip_prompt = _skip_swap_prompt_needed(state, player)
            if skip_prompt:
                ctx[1] = new_character
                state.acting = player.index
                return binary(P_SKIP_SWAP)
            _perform_character_swap(state, player, new_character)
            ctx[0] += 1
        check_area_removal(state)
        state.phase = PH_AREA_SWAP
        state.phase_ctx = []
        return None

    def _ph_area_swap(self, state: GameState, request, answer):
        for player in state.players:
            new_area = _incoming_of_type(state, player, TYPE_AREA_ENCHANT)
            if new_area == -1:
                continue
            if player.area_blocked:
                _clear_set_slot(player, new_area)
                _to_power_or_abyss(state, new_area, player.index)
                continue
            old_area_definition = state.inst_def[player.set_c] if player.set_c != -1 else -1
            if player.set_c != -1:
                old_area = player.set_c
                player.set_c = -1
                _to_power_or_abyss(state, old_area, player.index)
                on_area_enchant_leaves_play(state, old_area, player.index)
            state.inst_face_up[new_area] = 1
            player.set_c = new_area
            _clear_set_slot(player, new_area)
            if state.event_sink is not None:
                state.event_sink.append(
                    (EVENT_AREA_PLACED, player.index, old_area_definition,
                     state.inst_def[new_area]))
        check_area_removal(state)
        state.phase = PH_PROCESS_EFFECTS
        state.phase_ctx = [0, state.priority_player, 0, [], [], 0, 0]
        return None

    def _ph_process_effects(self, state: GameState, request, answer):
        # ctx: [round (0/1), priority, stage, remaining, ordered, pos, multi]
        # stage 0 = collect, 1 = ordering picks, 2 = dispatch loop, 3 = post-batch.
        # Effect-choice answers never reach here (frames intercept them in
        # _advance); only ordering answers do.
        ctx = state.phase_ctx

        if answer is not None and request is not None and request.purpose == P_EFFECT_ORDER:
            chosen = request.candidates[answer]
            ctx[4].append(chosen)
            ctx[3].remove(chosen)

        while True:
            round_index, priority, stage = ctx[0], ctx[1], ctx[2]
            if round_index >= 2:
                break
            player = state.players[priority if round_index == 0 else 1 - priority]

            if stage == 0:
                eligible = _collect_eligible(state, player)
                if not eligible:
                    ctx[2] = 3
                    continue
                if len(eligible) == 1:
                    ctx[3], ctx[4], ctx[5], ctx[6] = [], eligible, 0, 0
                    ctx[2] = 2
                    continue
                forced_first = [i for i in eligible if EFFECT_T[state.inst_def[i]] in dispatch.COST_REDUCING]
                selectable = [i for i in eligible if EFFECT_T[state.inst_def[i]] not in dispatch.COST_REDUCING]
                ctx[3], ctx[4], ctx[5], ctx[6] = selectable, list(forced_first), 0, 1
                ctx[2] = 1
                continue

            if stage == 1:
                if len(ctx[3]) > 1:
                    state.acting = player.index
                    return select_card(P_EFFECT_ORDER, list(ctx[3]))
                ctx[4].extend(ctx[3])
                ctx[3] = []
                ctx[2] = 2
                ctx[5] = 0
                continue

            if stage == 2:
                # Dispatch in chosen order. The multi-effect loop (old engine)
                # stops when any HP hits 0; a single effect dispatches without
                # that pre-check. After a dispatch pushes frames, yield to
                # _advance so the frames (and their decisions) run first.
                ordered = ctx[4]
                position = ctx[5]
                if position < len(ordered):
                    if ctx[6] and any(p.hp <= 0 for p in state.players):
                        ctx[2] = 3
                        continue
                    ctx[5] = position + 1
                    _dispatch_with_cost_check(state, player, ordered[position])
                    if state.frame_stack:
                        return None
                    continue
                ctx[2] = 3
                continue

            # stage 3: end of this player's batch
            check_win(state)
            if state.winner != -1:
                return None
            ctx[0] += 1
            ctx[2] = 0
            ctx[3], ctx[4], ctx[5], ctx[6] = [], [], 0, 0

        state.phase = PH_BATTLE
        state.phase_ctx = []
        return None

    def _ph_battle(self, state: GameState, request, answer):
        resolve_battle(state)
        check_win(state)
        if state.winner != -1:
            return None
        state.phase = PH_END_TURN if state.turn == 1 else PH_TURN_END_EFFECTS
        state.phase_ctx = []
        return None

    def _ph_turn_end_effects(self, state: GameState, request, answer):
        process_end_of_turn_effects(state)
        check_win(state)
        if state.winner != -1:
            return None
        state.phase = PH_END_TURN
        state.phase_ctx = []
        return None

    def _ph_end_turn(self, state: GameState, request, answer):
        for player in state.players:
            _end_turn_for(state, player)
        for player in state.players:
            player.hand_bonus += player.pending_hand_bonus
            player.pending_hand_bonus = 0
        check_area_removal(state, end_of_turn=True)
        if state.winner != -1:
            return None
        for player in state.players:
            player.prev_battle_def = state.inst_def[player.battle] if player.battle != -1 else -1
        _reset_turn_flags(state)
        state.turn += 1
        if state.turn > self.max_turns:
            state.winner = 2
            return None
        state.chronos_at_turn_start = state.chronos
        loser_first = _set_commit_order(state)
        state.phase = PH_SET_CARDS
        state.phase_ctx = [loser_first, 0, 0]
        return None

    def _ph_game_over(self, state: GameState, request, answer):
        return None


_PHASE_HANDLERS = {
    PH_DRAFT: Game._ph_draft,
    PH_MULLIGAN: Game._ph_mulligan,
    PH_INITIAL_SET: Game._ph_initial_set,
    PH_INITIAL_REVEAL: Game._ph_initial_reveal,
    PH_SET_CARDS: Game._ph_set_cards,
    PH_REVEAL: Game._ph_reveal,
    PH_ADVANCE_CHRONOS: Game._ph_advance_chronos,
    PH_CHARACTER_SWAP: Game._ph_character_swap,
    PH_AREA_SWAP: Game._ph_area_swap,
    PH_PROCESS_EFFECTS: Game._ph_process_effects,
    PH_BATTLE: Game._ph_battle,
    PH_TURN_END_EFFECTS: Game._ph_turn_end_effects,
    PH_END_TURN: Game._ph_end_turn,
    PH_GAME_OVER: Game._ph_game_over,
}


# ---------------------------------------------------------------------------
# Rules helpers (module-level for speed; operate on state directly)
# ---------------------------------------------------------------------------

def _draw(state: GameState, player_index: int, count: int) -> int:
    from .zones import draw_cards
    return draw_cards(state, player_index, count)


def _to_power_or_abyss(state: GameState, instance_id: int, owner_index: int) -> None:
    from .zones import to_power_or_abyss
    to_power_or_abyss(state, instance_id, owner_index)


def _set_card(state: GameState, player: PlayerState, instance_id: int, *, slot_b: bool) -> None:
    player.hand.remove(instance_id)
    state.inst_face_up[instance_id] = 0
    state.inst_played[instance_id] = 1
    player.cards_played += 1
    if slot_b:
        player.set_b = instance_id
    else:
        player.set_a = instance_id


def _max_cards_to_set(state: GameState, player: PlayerState) -> int:
    if state.last_battle_winner == -1:
        return 1
    return 1 if state.last_battle_winner == player.index else 2


def _set_commit_order(state: GameState) -> list[int]:
    """Previous-battle loser commits first; tie -> NIGHT-side player first."""
    if state.last_battle_winner != -1:
        loser = 1 - state.last_battle_winner
        return [loser, 1 - loser]
    night = 0 if state.players[0].side_is_night else 1
    return [night, 1 - night]


def _cards_played_this_turn(state: GameState, player: PlayerState) -> list[int]:
    played = state.inst_played
    cards = []
    if player.set_a != -1 and played[player.set_a]:
        cards.append(player.set_a)
    if player.set_b != -1 and played[player.set_b]:
        cards.append(player.set_b)
    if player.battle != -1 and played[player.battle]:
        cards.append(player.battle)
    for instance_id in player.charger:
        if played[instance_id]:
            cards.append(instance_id)
    for instance_id in player.abyss:
        if played[instance_id]:
            cards.append(instance_id)
    return cards


def _incoming_of_type(state: GameState, player: PlayerState, card_type: int) -> int:
    """Set-zone A has priority over B for swaps (old engine rule)."""
    if player.set_a != -1 and CARD_TYPE_T[state.inst_def[player.set_a]] == card_type:
        return player.set_a
    if player.set_b != -1 and CARD_TYPE_T[state.inst_def[player.set_b]] == card_type:
        return player.set_b
    return -1


def _clear_set_slot(player: PlayerState, instance_id: int) -> None:
    if player.set_a == instance_id:
        player.set_a = -1
    elif player.set_b == instance_id:
        player.set_b = -1


def _skip_swap_prompt_needed(state: GameState, player: PlayerState) -> bool:
    """02-062 (played this turn, cost met) lets the owner skip the swap."""
    for slot in (player.set_a, player.set_b):
        if slot != -1 and EFFECT_T[state.inst_def[slot]] == FX_02_062 and state.inst_played[slot]:
            return total_power(state, player) >= effective_power_cost(state, slot)
    return False


def _perform_character_swap(state: GameState, player: PlayerState, new_character: int) -> None:
    old_definition = state.inst_def[player.battle] if player.battle != -1 else -1
    if player.battle != -1:
        old_character = player.battle
        player.swapped_from_songs |= 1 << SONG_T[state.inst_def[old_character]]
        player.battle = -1
        _to_power_or_abyss(state, old_character, player.index)
    state.inst_face_up[new_character] = 1
    player.battle = new_character
    _clear_set_slot(player, new_character)
    if state.event_sink is not None:
        state.event_sink.append(
            (EVENT_CHARACTER_SWAP, player.index, old_definition,
             state.inst_def[new_character]))


def _collect_eligible(state: GameState, player: PlayerState) -> list[int]:
    """Old _collect_eligible_effects: area enchant (always), enchants set this
    turn (A then B), then the battle character if played this turn. Power cost
    is deferred to dispatch time."""
    eligible = []
    inst_def = state.inst_def
    area = player.set_c
    if area != -1 and EFFECT_T[inst_def[area]] != NO_EFFECT and not state.inst_neg[area]:
        if EFFECT_T[inst_def[area]] in dispatch.HANDLED_EFFECTS:
            eligible.append(area)
    for slot in (player.set_a, player.set_b):
        if slot == -1 or not state.inst_played[slot]:
            continue
        if CARD_TYPE_T[inst_def[slot]] != TYPE_ENCHANT:
            continue
        if EFFECT_T[inst_def[slot]] == NO_EFFECT or state.inst_neg[slot]:
            continue
        if EFFECT_T[inst_def[slot]] in dispatch.HANDLED_EFFECTS:
            eligible.append(slot)
    battle_instance = player.battle
    if battle_instance != -1 and state.inst_played[battle_instance]:
        if (CARD_TYPE_T[inst_def[battle_instance]] == TYPE_CHARACTER
                and EFFECT_T[inst_def[battle_instance]] != NO_EFFECT
                and not state.inst_neg[battle_instance]
                and EFFECT_T[inst_def[battle_instance]] in dispatch.HANDLED_EFFECTS):
            eligible.append(battle_instance)
    return eligible


def _dispatch_with_cost_check(state: GameState, player: PlayerState, instance_id: int) -> bool:
    """Power cost is checked at dispatch time. Area enchants use total_power
    only; enchants/characters add the power bonus."""
    cost = effective_power_cost(state, instance_id)
    if CARD_TYPE_T[state.inst_def[instance_id]] == TYPE_AREA_ENCHANT:
        if total_power(state, player) < cost:
            if state.event_sink is not None:
                state.event_sink.append(
                    (EVENT_EFFECT_SKIPPED_COST, player.index, state.inst_def[instance_id]))
            return False
    else:
        if total_power(state, player) + player.flags[PF_POWER_BONUS] < cost:
            if state.event_sink is not None:
                state.event_sink.append(
                    (EVENT_EFFECT_SKIPPED_COST, player.index, state.inst_def[instance_id]))
            return False
    dispatch.start_effect(state, player.index, instance_id)
    return True


def _end_turn_for(state: GameState, player: PlayerState) -> None:
    for slot_is_b in (False, True):
        instance_id = player.set_b if slot_is_b else player.set_a
        if instance_id != -1 and state.inst_played[instance_id]:
            if slot_is_b:
                player.set_b = -1
            else:
                player.set_a = -1
            _to_power_or_abyss(state, instance_id, player.index)

    draw_count = player.cards_played
    if len(player.deck) >= draw_count:
        _draw(state, player.index, draw_count)
        return
    # Deck cannot supply the mandatory draw: this player loses. If both
    # players deck out, the later call overwrites (old-engine behavior).
    _draw(state, player.index, len(player.deck))
    state.winner = 1 - player.index


def _reset_turn_flags(state: GameState) -> None:
    """Old reset_turn_flags: per-instance transients in hand/battle/set/charger/
    abyss (set-zone C keeps its cost reduction), player counters, per-turn
    effect flags, and the shared flags."""
    played = state.inst_played
    cost_red = state.inst_cost_red
    for player in state.players:
        player.cards_played = 0
        for instance_id in player.hand:
            played[instance_id] = 0
            cost_red[instance_id] = 0
        for slot in (player.battle, player.set_a, player.set_b):
            if slot != -1:
                played[slot] = 0
                cost_red[slot] = 0
        if player.set_c != -1:
            played[player.set_c] = 0
        for instance_id in player.charger:
            played[instance_id] = 0
            cost_red[instance_id] = 0
        for instance_id in player.abyss:
            played[instance_id] = 0
            cost_red[instance_id] = 0
        player.swapped_from_songs = 0
        player.flags = _fresh_player_flags()
    state.gflags = array("h", bytes(2 * N_GLOBAL_FLAGS))
