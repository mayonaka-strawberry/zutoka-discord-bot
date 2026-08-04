"""State -> network observation.

Everything is relative to the acting player ("self" = the player owning the
pending decision). The game state is encoded as a token sequence.

Output (numpy, variable length T <= MAX_TOKENS):
  tok_int   [T, N_INT_FEATURES] int16   categorical indices (embedded by net)
  tok_float [T, N_FLOAT_FEATURES] float32
  globals   [N_GLOBALS] float32
  candidate_positions: for SELECT_CARD requests, token row of each candidate
    (action i -> candidate_positions[i]); the PASS action maps to the PASS
    token row appended at the end of the list.

Token order: CLS, PASS, prev-battle-def x2, then per side (self, opp):
battle, set_a, set_b, set_c, hand..., charger..., abyss..., deck...
"""

from __future__ import annotations

import numpy as np

from ..actions import (
    SELECT_CARD, SELECT_IDENTITY, SELECT_NUMBER, BINARY, N_PURPOSES,
)
from ..battle import get_effective_attack, total_power
from ..cards import (
    ATK_DAY_T, ATK_NIGHT_T, ATTRIBUTE_T, CARD_TYPE_T, CLOCK_T, EFFECT_T,
    NUM_CARDS, NUM_EFFECTS, NUM_SONGS, POWER_COST_T, RARITY, SEND_TO_POWER_T,
    SONG_T,
)
from ..state import (
    GameState, N_PLAYER_FLAGS, N_GLOBAL_FLAGS,
    PF_ATTACK_OVERRIDE, PH_MULLIGAN,
)

# Zone-slot vocabulary (relative to acting player)
ZS_CLS = 0
ZS_PASS = 1
ZS_PREV_SELF = 2
ZS_PREV_OPP = 3
_ZONE_BASE = 4  # + zone_kind * 2 + (0 self / 1 opp)
_ZK_BATTLE, _ZK_SET_A, _ZK_SET_B, _ZK_SET_C, _ZK_HAND, _ZK_CHARGER, _ZK_ABYSS, _ZK_DECK = range(8)
N_ZONE_SLOTS = _ZONE_BASE + 16

N_INT_FEATURES = 7   # identity, attribute, card_type, song, rarity, effect, zone_slot
N_FLOAT_FEATURES = 14
MAX_TOKENS = 4 + 2 * (4 + 20 + 20 + 20 + 20)  # 172

# Categorical paddings (index = vocabulary size means "none")
PAD_IDENTITY = NUM_CARDS       # 425
PAD_ATTR = 5
PAD_TYPE = 3
PAD_SONG = NUM_SONGS
PAD_RARITY = 5
PAD_EFFECT = NUM_EFFECTS       # 253

N_NUMBER_ACTIONS = 21          # numeric answers 0..20 (chronos <= 17, counts <= 20)

_RARITY_T = tuple(int(x) for x in RARITY)

# Globals layout
N_GLOBALS = (18 + 2) + 2 + 8 + 1 + 14 + N_PURPOSES + 4 + 2 + 3 + 2 + 2 + 4 \
    + 2 * N_PLAYER_FLAGS + N_GLOBAL_FLAGS + 4 + 2


def _gather_chosen(state: GameState) -> set[int]:
    """Instance ids already marked/picked within the pending multi-part
    decision (mulligan marks, multiselect picks)."""
    chosen: set[int] = set()
    if state.phase == PH_MULLIGAN and len(state.phase_ctx) == 2:
        chosen.update(state.phase_ctx[1])
    for frame in state.frame_stack:
        if len(frame.data) == 3 and isinstance(frame.data[2], list):
            chosen.update(i for i in frame.data[2] if isinstance(i, int))
    return chosen


def encode(game) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[int]]:
    state: GameState = game.state
    acting = state.acting if state.acting != -1 else 0
    request = state.pending

    tok_int = np.zeros((MAX_TOKENS, N_INT_FEATURES), dtype=np.int16)
    tok_float = np.zeros((MAX_TOKENS, N_FLOAT_FEATURES), dtype=np.float32)

    candidates = set()
    candidate_order: dict[int, int] = {}
    if request is not None and request.kind == SELECT_CARD:
        candidates = set(request.candidates)
        candidate_order = {iid: i for i, iid in enumerate(request.candidates)}
    chosen = _gather_chosen(state)

    row = 0
    # CLS
    tok_int[row] = (PAD_IDENTITY, PAD_ATTR, PAD_TYPE, PAD_SONG, PAD_RARITY, PAD_EFFECT, ZS_CLS)
    row += 1
    # PASS
    pass_row = row
    tok_int[row] = (PAD_IDENTITY, PAD_ATTR, PAD_TYPE, PAD_SONG, PAD_RARITY, PAD_EFFECT, ZS_PASS)
    tok_float[row, 12] = 1.0 if (request is not None and request.kind == SELECT_CARD
                                 and request.allow_pass) else 0.0
    row += 1

    token_of_instance: dict[int, int] = {}

    def add_def_token(def_index: int, zone_slot: int) -> None:
        nonlocal row
        effect = EFFECT_T[def_index]
        tok_int[row] = (def_index, ATTRIBUTE_T[def_index], CARD_TYPE_T[def_index],
                        SONG_T[def_index], _RARITY_T[def_index],
                        effect if effect != -1 else PAD_EFFECT, zone_slot)
        tok_float[row, 0] = CLOCK_T[def_index] / 6.0
        tok_float[row, 1] = ATK_DAY_T[def_index] / 200.0
        tok_float[row, 2] = ATK_NIGHT_T[def_index] / 200.0
        tok_float[row, 3] = POWER_COST_T[def_index] / 8.0
        tok_float[row, 4] = SEND_TO_POWER_T[def_index] / 2.0
        row += 1

    def add_instance_token(instance_id: int, zone_slot: int, position: float = 0.0,
                           effective_attack: float = 0.0) -> None:
        nonlocal row
        def_index = state.inst_def[instance_id]
        attr_override = state.inst_attr_ovr[instance_id]
        effect = EFFECT_T[def_index]
        if effect == -1 or state.inst_neg[instance_id]:
            effect = PAD_EFFECT
        tok_int[row] = (
            def_index,
            attr_override if attr_override != -1 else ATTRIBUTE_T[def_index],
            CARD_TYPE_T[def_index], SONG_T[def_index], _RARITY_T[def_index],
            effect, zone_slot,
        )
        f = tok_float[row]
        f[0] = CLOCK_T[def_index] / 6.0
        f[1] = ATK_DAY_T[def_index] / 200.0
        f[2] = ATK_NIGHT_T[def_index] / 200.0
        f[3] = POWER_COST_T[def_index] / 8.0
        f[4] = SEND_TO_POWER_T[def_index] / 2.0
        f[5] = effective_attack
        f[6] = position
        f[7] = state.inst_cost_red[instance_id] / 8.0
        f[8] = float(state.inst_played[instance_id])
        f[9] = float(state.inst_neg[instance_id])
        f[10] = float(state.inst_face_up[instance_id])
        f[11] = 1.0 if attr_override != -1 else 0.0
        f[12] = 1.0 if instance_id in candidates else 0.0
        f[13] = 1.0 if instance_id in chosen else 0.0
        token_of_instance[instance_id] = row
        row += 1

    # Prev-battle definitions (family S context)
    for relative, player in ((0, state.players[acting]), (1, state.players[1 - acting])):
        if player.prev_battle_def != -1:
            add_def_token(player.prev_battle_def, ZS_PREV_SELF if relative == 0 else ZS_PREV_OPP)

    for relative in (0, 1):
        player = state.players[acting if relative == 0 else 1 - acting]
        eff_atk = get_effective_attack(state, player) / 200.0
        if player.battle != -1:
            add_instance_token(player.battle, _ZONE_BASE + _ZK_BATTLE * 2 + relative,
                               effective_attack=eff_atk)
        for zone_kind, single in ((_ZK_SET_A, player.set_a), (_ZK_SET_B, player.set_b),
                                  (_ZK_SET_C, player.set_c)):
            if single != -1:
                add_instance_token(single, _ZONE_BASE + zone_kind * 2 + relative)
        for instance_id in player.hand[:20]:
            add_instance_token(instance_id, _ZONE_BASE + _ZK_HAND * 2 + relative)
        for instance_id in player.charger[:20]:
            add_instance_token(instance_id, _ZONE_BASE + _ZK_CHARGER * 2 + relative)
        for instance_id in player.abyss[:20]:
            add_instance_token(instance_id, _ZONE_BASE + _ZK_ABYSS * 2 + relative)
        for depth, instance_id in enumerate(player.deck[:20]):
            add_instance_token(instance_id, _ZONE_BASE + _ZK_DECK * 2 + relative,
                               position=(depth + 1) / 20.0)

    # Draft: partial decks as definition tokens in the deck slots
    if state.draft is not None:
        for relative in (0, 1):
            deck_defs = state.draft.decks[acting if relative == 0 else 1 - acting]
            for def_index in deck_defs:
                add_def_token(def_index, _ZONE_BASE + _ZK_DECK * 2 + relative)

    tok_int = tok_int[:row]
    tok_float = tok_float[:row]

    # ---- globals ----
    g = np.zeros(N_GLOBALS, dtype=np.float32)
    o = 0
    g[o + state.chronos] = 1.0; o += 18
    g[o] = 1.0 if state.is_night else 0.0; o += 1
    g[o] = 1.0 if state.players[acting].side_is_night else 0.0; o += 1
    for relative in (0, 1):
        player = state.players[acting if relative == 0 else 1 - acting]
        g[o + relative] = player.hp / 100.0
    o += 2
    for relative in (0, 1):
        player = state.players[acting if relative == 0 else 1 - acting]
        base = o + relative * 4
        g[base] = len(player.deck) / 20.0
        g[base + 1] = len(player.hand) / 20.0
        g[base + 2] = len(player.charger) / 20.0
        g[base + 3] = len(player.abyss) / 20.0
    o += 8
    g[o] = min(state.turn, 50) / 50.0; o += 1
    g[o + state.phase] = 1.0; o += 14
    if request is not None:
        g[o + request.purpose] = 1.0
    o += N_PURPOSES
    if request is not None:
        g[o + request.kind] = 1.0
    o += 4
    if request is not None and request.kind in (SELECT_NUMBER,):
        g[o] = request.lo / 20.0
        g[o + 1] = request.hi / 20.0
    o += 2
    winner_slot = 0 if state.last_battle_winner == -1 else (
        1 if state.last_battle_winner == acting else 2)
    g[o + winner_slot] = 1.0; o += 3
    for relative in (0, 1):
        player = state.players[acting if relative == 0 else 1 - acting]
        g[o + relative] = player.cards_played / 2.0
    o += 2
    for relative in (0, 1):
        player = state.players[acting if relative == 0 else 1 - acting]
        g[o + relative] = total_power(state, player) / 16.0
    o += 2
    for relative in (0, 1):
        player = state.players[acting if relative == 0 else 1 - acting]
        g[o + relative * 2] = player.hand_bonus / 5.0
        g[o + relative * 2 + 1] = player.pending_hand_bonus / 5.0
    o += 4
    for relative in (0, 1):
        player = state.players[acting if relative == 0 else 1 - acting]
        flags = player.flags
        base = o + relative * N_PLAYER_FLAGS
        for flag_index in range(N_PLAYER_FLAGS):
            value = flags[flag_index]
            if flag_index == PF_ATTACK_OVERRIDE:
                g[base + flag_index] = 0.0 if value == -1 else (value + 1) / 200.0
            else:
                g[base + flag_index] = value / 100.0 if abs(value) > 1 else float(value)
    o += 2 * N_PLAYER_FLAGS
    for flag_index in range(N_GLOBAL_FLAGS):
        g[o + flag_index] = float(state.gflags[flag_index])
    o += N_GLOBAL_FLAGS
    if state.draft is not None:
        g[o] = 1.0
        g[o + 1] = state.draft.pick_number / 40.0
        g[o + 2] = len(state.draft.decks[acting]) / 20.0
        g[o + 3] = len(state.draft.decks[1 - acting]) / 20.0
    o += 4
    for relative in (0, 1):
        player = state.players[acting if relative == 0 else 1 - acting]
        g[o + relative] = 1.0 if player.area_blocked else 0.0
    o += 2
    assert o == N_GLOBALS

    # Candidate rows for the pointer head
    candidate_positions: list[int] = []
    if request is not None and request.kind == SELECT_CARD:
        for instance_id in request.candidates:
            candidate_positions.append(token_of_instance[instance_id])
        if request.allow_pass:
            candidate_positions.append(pass_row)

    return tok_int, tok_float, g, candidate_positions
