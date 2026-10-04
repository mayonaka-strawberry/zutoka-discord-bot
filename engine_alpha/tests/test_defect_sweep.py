"""Deck-forced sweep for defect classes the rest of the suite cannot see.

Unlike test_coverage_playouts, it:
1. forces decks per defect group (a random deck gives any one card about 2.4% exposure);
2. encodes the observation at every decision, catching encoder crashes;
3. audits area-enchant end conditions at the turn-end phase boundary, where the
   end-of-turn cleanup would otherwise hide a violation;
4. caps frame depth and decision count, so a runaway chain fails instead of hanging.

What each group proves:
- `immediate_removal` gates the area-removal timing rule (61/150 failures with the
  damage-triggered hook disabled).
- `shade_nesting` does NOT reproduce the 04-002 chain (0/150); its real gate is
  test_qa_79_83_shade_chain_terminates_without_blocking_legal_nesting. It backstops
  unknown runaway chains.
- `turn_end_owner` and `baseline` are for breadth.
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

#: One group per defect cluster; `shade_nesting` needs all three cards. Seed bases are
#: literals because hash() is randomized per process, which would make failures
#: unreproducible.
GROUPS = {
    'turn_end_owner': (10000, ('03-027', '04-100', '03-058')),
    'immediate_removal': (20000, ('03-058', '03-085', '04-091')),
    'shade_nesting': (30000, ('04-002', '04-094', '03-097')),
    'deck_shortfall': (40000, ('01-092', '04-089', '02-015', '04-057')),
    'baseline': (50000, ()),
    # Breadth only: it does NOT gate the 02-041 shortfall fix (the empty-deck branch
    # never fires here); test_rulings.py does. A separate group because adding cards
    # to `deck_shortfall` would re-roll that group's games.
    'deck_top_route': (60000, ('02-041', '01-006')),
}


class _AreaEndConditionSink(list):
    """Event sink auditing area-enchant end conditions as the turn-end window closes:
    03-058/03-085 at 30+ damage and 04-091 at HP <= 50 must already be gone (Q&A No.16,
    80). Runs on the phase transition because cleanup hides them by the next decision."""

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
            # An area with an unmet power cost is never removed (Ground Rules 6.1.3.4).
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
        # Only the encoder catches an instance id the observation cannot map.
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
