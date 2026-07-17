"""Decision requests: the complete typed action surface of the game.

Every point where a player chooses anything is expressed as one of four
request kinds. Requests are treated as immutable once issued (clones share
them). An *action* is always a small int whose meaning depends on the
pending request:

  SELECT_CARD:     index into `candidates`; `len(candidates)` means PASS
                   (only legal when allow_pass)
  SELECT_IDENTITY: card definition index (0..NUM_CARDS-1) from `legal`
  SELECT_NUMBER:   the number itself (lo..hi inclusive)
  BINARY:          0 or 1
"""

from __future__ import annotations

SELECT_CARD = 0
SELECT_IDENTITY = 1
SELECT_NUMBER = 2
BINARY = 3

# Purpose tags: what the decision is for (encoded into observations so the
# network knows the question, and used by drivers/tests for readability).
P_DRAFT_PICK = 0
P_MULLIGAN = 1          # iterative mark-for-redraw; PASS finishes
P_INITIAL_CARD = 2
P_SET_SLOT_A = 3
P_SET_SLOT_B = 4
P_EFFECT_ORDER = 5
P_EFFECT_TARGET = 6
P_EFFECT_NUMBER = 7
P_NAME_GUESS = 8
P_OPTIONAL_ACTIVATION = 9
P_SKIP_SWAP = 10        # 02-062: 1 = keep current battle character
P_CHRONOS_VALUE = 11

PURPOSE_NAMES = (
    "DRAFT_PICK", "MULLIGAN", "INITIAL_CARD", "SET_SLOT_A", "SET_SLOT_B",
    "EFFECT_ORDER", "EFFECT_TARGET", "EFFECT_NUMBER", "NAME_GUESS",
    "OPTIONAL_ACTIVATION", "SKIP_SWAP", "CHRONOS_VALUE",
)
N_PURPOSES = len(PURPOSE_NAMES)


class DecisionRequest:
    __slots__ = ("kind", "purpose", "candidates", "allow_pass", "legal", "lo", "hi")

    def __init__(self, kind: int, purpose: int, *, candidates: tuple[int, ...] = (),
                 allow_pass: bool = False, legal: tuple[int, ...] = (),
                 lo: int = 0, hi: int = 0) -> None:
        self.kind = kind
        self.purpose = purpose
        self.candidates = candidates    # SELECT_CARD: instance ids
        self.allow_pass = allow_pass
        self.legal = legal              # SELECT_IDENTITY: def indices
        self.lo = lo                    # SELECT_NUMBER bounds, inclusive
        self.hi = hi

    def legal_actions(self) -> list[int]:
        if self.kind == SELECT_CARD:
            actions = list(range(len(self.candidates)))
            if self.allow_pass:
                actions.append(len(self.candidates))
            return actions
        if self.kind == SELECT_IDENTITY:
            return list(self.legal)
        if self.kind == SELECT_NUMBER:
            return list(range(self.lo, self.hi + 1))
        return [0, 1]

    def is_legal(self, action: int) -> bool:
        if self.kind == SELECT_CARD:
            limit = len(self.candidates) + (1 if self.allow_pass else 0)
            return 0 <= action < limit
        if self.kind == SELECT_IDENTITY:
            return action in self.legal
        if self.kind == SELECT_NUMBER:
            return self.lo <= action <= self.hi
        return action in (0, 1)

    def is_pass(self, action: int) -> bool:
        return self.kind == SELECT_CARD and action == len(self.candidates)

    def __repr__(self) -> str:
        kind_name = ("SELECT_CARD", "SELECT_IDENTITY", "SELECT_NUMBER", "BINARY")[self.kind]
        return f"<{kind_name} {PURPOSE_NAMES[self.purpose]} n={len(self.legal_actions())}>"


def select_card(purpose: int, candidates: list[int], *, allow_pass: bool = False) -> DecisionRequest:
    return DecisionRequest(SELECT_CARD, purpose, candidates=tuple(candidates), allow_pass=allow_pass)


def select_identity(purpose: int, legal: list[int]) -> DecisionRequest:
    return DecisionRequest(SELECT_IDENTITY, purpose, legal=tuple(legal))


def select_number(purpose: int, lo: int, hi: int) -> DecisionRequest:
    return DecisionRequest(SELECT_NUMBER, purpose, lo=lo, hi=hi)


def binary(purpose: int) -> DecisionRequest:
    return DecisionRequest(BINARY, purpose)
