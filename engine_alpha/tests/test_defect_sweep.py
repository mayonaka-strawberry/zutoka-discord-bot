"""Deck-forced sweep for the defect classes the rest of the suite cannot see.

Three things make this different from `test_coverage_playouts`, and each of them
was needed to catch a real bug that shipped green:

1. **Decks are forced, not random.** Unbiased full-pool decks draw 10 of 425
   definitions, so any one card gets ~2.4% exposure per deck. 150 unbiased games
   found none of the defects below; the same count with forced decks finds them
   immediately.
2. **The observation is encoded at every decision.** Nothing else in the repo does
   this in a loop, which is exactly why a `KeyError` in the encoder survived a full
   green suite and a 24-game replay baseline.
3. **A phase-boundary hook checks area-enchant end conditions.** Some rules bugs are
   behavioural rather than crashes and are invisible at decision points, because the
   end-of-turn cleanup tidies up before the next prompt.

Caps make a hang a failure instead of a hang: without them a runaway effect chain
never returns and the test never reports.

What each group is and is not evidence for, measured rather than assumed:

- `immediate_removal` genuinely gates the area-removal timing rule. With the
  damage-triggered hook disabled it fails 65/150; with it enabled, 0/150.
- `shade_nesting` does **not** reproduce the 04-002 chain and must not be cited as
  its gate. 04-002 has SEND TO POWER 0, so it only reaches a charger via 03-097
  revealing a cost-6+ card while 03-097 is in play -- a setup random play does not
  produce (0/150 games here). The real gate for that defect is the deterministic
  `test_qa_79_83_shade_chain_terminates_without_blocking_legal_nesting`. The group
  and the frame-depth cap remain useful as a backstop for *unknown* runaway chains.
- `turn_end_owner` and `baseline` exist for breadth: encoder coverage, invariants and
  clean termination across many states.
"""

from __future__ import annotations

import random

import pytest

from engine_alpha import cards
from engine_alpha.battle import effective_power_cost, total_power
from engine_alpha.cards import EFFECT_T, EFFECT_TO_CARD, EFFECT_TO_INDEX
from engine_alpha.encoding import observation
from engine_alpha.events import EVENT_PHASE_CHANGED
from engine_alpha.game import Game
from engine_alpha.state import PF_DAMAGE_TAKEN, PH_END_TURN, PH_GAME_OVER
from .test_coverage_playouts import _deck_containing
from .test_invariants import check_invariants

GAMES_PER_GROUP = 150
MAX_FRAME_DEPTH = 24
MAX_DECISIONS = 3000

FX_03_058 = EFFECT_TO_INDEX['03-058']
FX_03_085 = EFFECT_TO_INDEX['03-085']
FX_04_091 = EFFECT_TO_INDEX['04-091']

#: One group per defect cluster. `shade_nesting` needs all three cards: 04-002 has
#: SEND TO POWER 0 so it never reaches a charger by leaving play, 03-097 is the only
#: practical way to seed one there, and 04-094 resolves it without a power gate --
#: measured individually, no pair reproduces the chain.
#: Each group carries a fixed deck-seed base. It must be a literal, not `hash(group)`:
#: string hashing is randomised per process, which would make this sweep
#: non-deterministic and a failure impossible to reproduce from the printed seed.
GROUPS = {
    'turn_end_owner': (10000, ('03-027', '04-100', '03-058')),
    'immediate_removal': (20000, ('03-058', '03-085', '04-091')),
    'shade_nesting': (30000, ('04-002', '04-094', '03-097')),
    'deck_shortfall': (40000, ('01-092', '04-089', '02-015', '04-057')),
    'baseline': (50000, ()),
}


class _AreaEndConditionSink(list):
    """Event sink that audits area-enchant end conditions as the turn-end window
    closes.

    Cards whose text says 「すぐに」 must already be gone by then: Q&A No.16 for
    03-058/03-085 at 30+ damage taken, Q&A No.80 for 04-091 at HP 50 or less. This
    has to run on the phase transition rather than at a decision point, because
    `check_area_removal(end_of_turn=True)` clears them up before the next prompt --
    a decision-point assertion sees nothing.
    """

    def __init__(self, state) -> None:
        super().__init__()
        self.state = state

    def append(self, event) -> None:
        super().append(event)
        if event[0] != EVENT_PHASE_CHANGED or event[1] != PH_END_TURN:
            return
        for player in self.state.players:
            area = player.set_c
            if area == -1:
                continue
            # An area enchant whose power cost is unmet is never removed
            # (GR 6.1.3.4), so it is allowed to still be here.
            if total_power(self.state, player) < effective_power_cost(self.state, area):
                continue
            effect = EFFECT_T[self.state.inst_def[area]]
            if effect in (FX_03_058, FX_03_085):
                assert player.flags[PF_DAMAGE_TAKEN] < 30, (
                    'a 30+ damage area enchant survived into the turn-end window')
            if effect == FX_04_091:
                assert player.hp > 50, (
                    '04-091 survived past HP 50 into the turn-end window')


def _forced_decks(effect_ids, deck_rng):
    forced = [EFFECT_TO_CARD[EFFECT_TO_INDEX[effect_id]] for effect_id in effect_ids]
    return (_deck_containing(list(forced), deck_rng),
            _deck_containing(list(forced), deck_rng))


def _play_one(group: str, seed: int) -> None:
    deck_base, effect_ids = GROUPS[group]
    deck_rng = random.Random(deck_base + seed)
    decks = _forced_decks(effect_ids, deck_rng)
    game = Game(seed=seed, mode='fixed_decks', decks=decks)
    game.state.event_sink = _AreaEndConditionSink(game.state)

    policy = random.Random(seed ^ 0x5EED)
    decisions = 0
    while game.state.winner == -1:
        decisions += 1
        if decisions > MAX_DECISIONS:
            raise AssertionError(
                f'{group} seed {seed}: exceeded {MAX_DECISIONS} decisions '
                '(non-terminating game)')
        depth = len(game.state.frame_stack)
        if depth > MAX_FRAME_DEPTH:
            raise AssertionError(
                f'{group} seed {seed}: frame stack reached {depth} '
                '(runaway effect chain)')
        # The encoder is the only thing that catches an instance id the
        # observation cannot map.
        observation.encode(game)
        check_invariants(game)
        legal = game.legal_actions()
        assert legal, f'{group} seed {seed}: a pending decision with no legal action'
        game.apply(policy.choice(legal))

    assert game.state.winner in (0, 1, 2)
    assert game.state.frame_stack == [], f'{group} seed {seed}: orphaned frames'
    assert game.state.pending is None, f'{group} seed {seed}: pending on a finished game'
    assert game.state.phase == PH_GAME_OVER


@pytest.mark.parametrize('group', sorted(GROUPS))
def test_defect_sweep(group: str) -> None:
    for seed in range(GAMES_PER_GROUP):
        _play_one(group, seed)
