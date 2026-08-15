"""Game state structs: __slots__, integers everywhere, fast hand-rolled clone.

A card *definition* is an int index into cards.CARD_DB (immutable, shared).
A card *instance* is an int index into the per-game parallel instance arrays
held on GameState. Zone containers hold instance ids. No copy.deepcopy is
used anywhere: fast_clone() hand-copies every mutable slot.

Per-turn effect-modifier state (the old engine's TurnEffectState) lives in
fixed-slot int arrays: one per player (PF_*) and one shared (GF_*), plus a
couple of Python-int bitmask slots. All of it is real game state and is
cloned and observed.
"""

from __future__ import annotations

from array import array

# ---------------------------------------------------------------------------
# Phases
# ---------------------------------------------------------------------------
PH_DRAFT = 0
PH_MULLIGAN = 1
PH_INITIAL_SET = 2
PH_INITIAL_REVEAL = 3
PH_SET_CARDS = 4
PH_REVEAL = 5
PH_ADVANCE_CHRONOS = 6
PH_CHARACTER_SWAP = 7
PH_AREA_SWAP = 8
PH_PROCESS_EFFECTS = 9
PH_BATTLE = 10
PH_TURN_END_EFFECTS = 11
PH_END_TURN = 12
PH_GAME_OVER = 13

PHASE_NAMES = (
    "DRAFT", "MULLIGAN", "INITIAL_SET", "INITIAL_REVEAL", "SET_CARDS",
    "REVEAL", "ADVANCE_CHRONOS", "CHARACTER_SWAP", "AREA_SWAP",
    "PROCESS_EFFECTS", "BATTLE", "TURN_END_EFFECTS", "END_TURN", "GAME_OVER",
)

# ---------------------------------------------------------------------------
# Per-player turn-effect flags (PF_*): indices into PlayerState.flags
# ---------------------------------------------------------------------------
PF_ATTACK_BONUS = 0            # net attack bonus; derived summary, see attack_mods
PF_DAMAGE_REDUCTION = 1        # summed damage reduction owned by this player
PF_DAY_NIGHT_REVERSED = 2      # 01-005 applied against this player
PF_POWER_BONUS = 3             # 02-058 style extra power (chars/enchants only)
PF_CHAR_TO_POWER = 4           # owner placed own CHARACTER on own charger
PF_END_OF_TURN_DAMAGE = 5      # 03-027 pending end-of-turn damage
PF_OPP_CARD_TO_ABYSS = 6       # this player's opponent performed an abyss placement
PF_ABYSS_RECEIVED = 7          # this player's abyss received a card (any actor)
PF_BATTLE_DAMAGE = 8           # battle damage taken this turn
PF_DAMAGE_TAKEN = 9            # total damage taken this turn (battle + effect)
PF_BATTLE_LOST = 10            # lost the battle this turn (even at 0 damage)
PF_DAMAGE_NOT_REDUCIBLE = 11   # 04-024: this player's battle damage can't be reduced
PF_CARD_TO_POWER = 12          # owner placed any card on own charger
PF_ATTACK_OVERRIDE = 13        # last 04-099 set value, -1 = none; derived summary
PF_REFLECT_REDUCTION = 14      # 04-100 active for this player
PF_DAMAGE_REDUCED = 15         # how much battle damage was reduced this turn
PF_CHRONOS_ADVANCED = 16       # this player's clock contribution this turn
N_PLAYER_FLAGS = 17

# ---------------------------------------------------------------------------
# Shared turn-effect flags (GF_*): indices into GameState.gflags
# ---------------------------------------------------------------------------
GF_MIDNIGHT_EXTENDED = 0       # 03-026 active this turn
GF_DAY_TO_NIGHT = 1            # a day->night transition occurred this turn
GF_NIGHT_TO_DAY = 2            # a night->day transition occurred this turn
N_GLOBAL_FLAGS = 3


def _fresh_player_flags() -> array:
    flags = array("h", bytes(2 * N_PLAYER_FLAGS))
    flags[PF_ATTACK_OVERRIDE] = -1
    return flags


# ---------------------------------------------------------------------------
# Attack modifiers (PlayerState.attack_mods)
# ---------------------------------------------------------------------------
# Official Q&A No.54 and No.68: attack modifiers are neither collapsed into a
# number when they resolve nor summed into one total. They are kept in
# resolution order and folded onto the live base at battle time, clamped to >=0
# after each step. 04-099's "set the opponent's attack to 100" is an
# ATTACK_MOD_SET entry in the same list (Q&A No.82), so whether it wipes a bonus
# or is added to depends purely on resolution order.
ATTACK_MOD_ADD = 0
ATTACK_MOD_SET = 1
# 03-064 adds each side's REMAINING HP, read at attack determination rather than
# when the area enchant resolves (Q&A No.33: 攻撃力の決定時). Stored as a deferred
# entry so it keeps its place in the fold order; the amount is resolved in
# battle.get_effective_attack. This list is runtime state only -- it is not
# featurized, so the extra kind does not change the observation layout.
ATTACK_MOD_ADD_OWN_HP = 2


def add_attack_modifier(player: "PlayerState", amount: int) -> None:
    """Record an attack +/-N at this point in the resolution order."""
    player.attack_mods.append(ATTACK_MOD_ADD)
    player.attack_mods.append(amount)
    player.flags[PF_ATTACK_BONUS] += amount


def add_own_hp_attack_modifier(player: "PlayerState") -> None:
    """Record 03-064's "+= your remaining HP", resolved at battle time.

    Deliberately does NOT touch PF_ATTACK_BONUS: the amount is only known at attack
    determination (Q&A No.33), so anything written here goes stale the moment HP
    changes. That flag is a live neural-network input (observation.py encodes every
    player flag), and battle.get_effective_attack — which is also encoded — already
    reports the true total, so a stale snapshot would be both wrong and redundant.
    """
    player.attack_mods.append(ATTACK_MOD_ADD_OWN_HP)
    player.attack_mods.append(0)


def set_attack_modifier(player: "PlayerState", value: int) -> None:
    """Record an attack set-to-N (04-099) at this point in the resolution order."""
    player.attack_mods.append(ATTACK_MOD_SET)
    player.attack_mods.append(value)
    player.flags[PF_ATTACK_OVERRIDE] = value


class PlayerState:
    __slots__ = (
        "index", "side_is_night", "hp",
        "deck", "hand", "charger", "abyss",
        "battle", "set_a", "set_b", "set_c",
        "cards_played", "area_blocked",
        "hand_bonus", "pending_hand_bonus",
        "prev_battle_def",
        "swapped_from_songs",   # bitmask over song indices
        "flags",                # array('h', N_PLAYER_FLAGS)
        "attack_mods",          # array('h') of flat (kind, value) pairs, in order
    )

    def __init__(self, index: int, side_is_night: bool, hp: int) -> None:
        self.index = index
        self.side_is_night = side_is_night
        self.hp = hp
        self.deck: list[int] = []      # ordered, deck[0] = top
        self.hand: list[int] = []
        self.charger: list[int] = []
        self.abyss: list[int] = []
        self.battle = -1
        self.set_a = -1
        self.set_b = -1
        self.set_c = -1
        self.cards_played = 0
        self.area_blocked = False
        self.hand_bonus = 0
        self.pending_hand_bonus = 0
        self.prev_battle_def = -1
        self.swapped_from_songs = 0
        self.flags = _fresh_player_flags()
        # This turn's attack modifiers in resolution order. Authoritative for
        # battle.get_effective_attack; PF_ATTACK_BONUS/PF_ATTACK_OVERRIDE are
        # derived summaries kept only for the network observation.
        self.attack_mods = array("h")

    def fast_clone(self) -> "PlayerState":
        clone = PlayerState.__new__(PlayerState)
        clone.index = self.index
        clone.side_is_night = self.side_is_night
        clone.hp = self.hp
        clone.deck = list(self.deck)
        clone.hand = list(self.hand)
        clone.charger = list(self.charger)
        clone.abyss = list(self.abyss)
        clone.battle = self.battle
        clone.set_a = self.set_a
        clone.set_b = self.set_b
        clone.set_c = self.set_c
        clone.cards_played = self.cards_played
        clone.area_blocked = self.area_blocked
        clone.hand_bonus = self.hand_bonus
        clone.pending_hand_bonus = self.pending_hand_bonus
        clone.prev_battle_def = self.prev_battle_def
        clone.swapped_from_songs = self.swapped_from_songs
        clone.flags = array("h", self.flags)
        clone.attack_mods = array("h", self.attack_mods)
        return clone


class Frame:
    """A paused effect resolution (explicit continuation, cloneable).

    Interpreted effects run ops from EFFECT_TABLE[effect_index] starting at
    `pc`; `regs` holds choice results (ints or int lists). Custom-coded
    effects keep their continuation in `step`/`data` instead.
    """

    __slots__ = ("effect_index", "source", "owner", "pc", "regs", "step", "data")

    def __init__(self, effect_index: int, source: int, owner: int) -> None:
        self.effect_index = effect_index
        self.source = source      # instance id of the card whose effect runs
        self.owner = owner        # player index resolving the effect
        self.pc = 0
        self.regs: list = []
        self.step = 0
        self.data: list = []

    def fast_clone(self) -> "Frame":
        clone = Frame.__new__(Frame)
        clone.effect_index = self.effect_index
        clone.source = self.source
        clone.owner = self.owner
        clone.pc = self.pc
        clone.regs = [list(r) if isinstance(r, list) else r for r in self.regs]
        clone.step = self.step
        clone.data = [list(d) if isinstance(d, list) else d for d in self.data]
        return clone


class DraftState:
    __slots__ = ("pick_number", "decks")  # decks: per player, list of def indices

    def __init__(self) -> None:
        self.pick_number = 0               # 0..39; even -> first picker
        self.decks: tuple[list[int], list[int]] = ([], [])

    def fast_clone(self) -> "DraftState":
        clone = DraftState.__new__(DraftState)
        clone.pick_number = self.pick_number
        clone.decks = (list(self.decks[0]), list(self.decks[1]))
        return clone


class GameState:
    __slots__ = (
        "phase", "turn", "chronos", "chronos_at_turn_start",
        "players",
        "last_battle_winner",   # -1 none/draw, else winning player index
        "winner",               # -1 in progress, 0/1 winner index, 2 draw
        # CHAOS bank-or-lose bookkeeping. Informational only: the win check and
        # returns are unaffected, and these never enter the NN observation. The
        # bot layer reads them to rate a thrown game differently.
        "self_defeat_player",   # -1 none, else the player who self-defeated
        "self_defeat_turn",     # -1 none, else the turn it happened on
        # Parallel per-instance arrays (index = instance id, append-only)
        "inst_def",             # def index
        "inst_played",          # played_this_turn 0/1
        "inst_neg",             # effects_disabled 0/1
        "inst_cost_red",        # power_cost_reduction
        "inst_attr_ovr",        # attribute override, -1 = none
        "inst_face_up",         # 0/1
        # Decision plumbing
        "pending",              # DecisionRequest or None
        "acting",               # player index owning the pending decision
        "phase_ctx",            # phase-driver continuation (list of small values)
        "frame_stack",          # list[Frame]
        # Shared turn-effect flags
        "gflags",               # array('h', N_GLOBAL_FLAGS)
        # Chance
        "rng_key", "rng_ctr",
        "draft",                # DraftState or None
        "event_sink",           # list for engine events, or None (detached)
    )

    def __init__(self, rng_key: int) -> None:
        self.phase = PH_DRAFT
        self.turn = 0
        self.chronos = 0
        self.chronos_at_turn_start = 0
        self.players: tuple[PlayerState, PlayerState] = (None, None)  # set by Game
        self.last_battle_winner = -1
        self.winner = -1
        self.self_defeat_player = -1
        self.self_defeat_turn = -1
        self.inst_def: list[int] = []
        self.inst_played: list[int] = []
        self.inst_neg: list[int] = []
        self.inst_cost_red: list[int] = []
        self.inst_attr_ovr: list[int] = []
        self.inst_face_up: list[int] = []
        self.pending = None
        self.acting = -1
        self.phase_ctx: list = []
        self.frame_stack: list[Frame] = []
        self.gflags = array("h", bytes(2 * N_GLOBAL_FLAGS))
        self.rng_key = rng_key
        self.rng_ctr = 0
        self.draft = None
        self.event_sink = None

    def fast_clone(self) -> "GameState":
        clone = GameState.__new__(GameState)
        clone.phase = self.phase
        clone.turn = self.turn
        clone.chronos = self.chronos
        clone.chronos_at_turn_start = self.chronos_at_turn_start
        clone.players = (self.players[0].fast_clone(), self.players[1].fast_clone())
        clone.last_battle_winner = self.last_battle_winner
        clone.winner = self.winner
        clone.self_defeat_player = self.self_defeat_player
        clone.self_defeat_turn = self.self_defeat_turn
        clone.inst_def = list(self.inst_def)
        clone.inst_played = list(self.inst_played)
        clone.inst_neg = list(self.inst_neg)
        clone.inst_cost_red = list(self.inst_cost_red)
        clone.inst_attr_ovr = list(self.inst_attr_ovr)
        clone.inst_face_up = list(self.inst_face_up)
        clone.pending = self.pending  # DecisionRequests are immutable once issued
        clone.acting = self.acting
        clone.phase_ctx = [list(c) if isinstance(c, list) else c for c in self.phase_ctx]
        clone.frame_stack = [f.fast_clone() for f in self.frame_stack]
        clone.gflags = array("h", self.gflags)
        clone.rng_key = self.rng_key
        clone.rng_ctr = self.rng_ctr
        clone.draft = self.draft.fast_clone() if self.draft is not None else None
        clone.event_sink = None
        return clone

    # -- instance management ------------------------------------------------

    def new_instance(self, def_index: int) -> int:
        instance_id = len(self.inst_def)
        self.inst_def.append(def_index)
        self.inst_played.append(0)
        self.inst_neg.append(0)
        self.inst_cost_red.append(0)
        self.inst_attr_ovr.append(-1)
        self.inst_face_up.append(0)
        return instance_id

    # -- derived rules helpers ----------------------------------------------

    @property
    def is_night(self) -> bool:
        """NIGHT is chronos 0..NIGHT_END inclusive (0..8), DAY is 9..17."""
        return self.chronos <= 8

    @property
    def priority_player(self) -> int:
        night_now = self.is_night
        return 0 if self.players[0].side_is_night == night_now else 1

    def day_night_matches(self, player: PlayerState) -> bool:
        return player.side_is_night == self.is_night
