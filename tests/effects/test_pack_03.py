"""Characterization tests for pack 03 card effects (see test_pack_01 header)."""

from __future__ import annotations

import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from tests.support.game_state_builder import GameStateBuilder  # noqa: E402
from tests.support.effect_harness import card_identities, run_effect  # noqa: E402

DARKNESS_CHARACTER = '01-001'
FLAME_CHARACTER = '01-002'
ELECTRICITY_CHARACTER = '01-003'
WIND_CHARACTER = '01-004'


class TestEffect03045:
    """Reveal the opponent's hand (then shuffle it against position tracking)."""

    def test_reveals_and_shuffles_opponent_hand(self):
        original_hand = [DARKNESS_CHARACTER, FLAME_CHARACTER, ELECTRICITY_CHARACTER, WIND_CHARACTER]
        state = (GameStateBuilder()
                 .with_battle_card(0, '03-045')
                 .with_hand(1, original_hand)
                 .build())
        result = run_effect(state, '03-045', owner_index=0)

        assert result.prompts_seen == []
        # Same cards, order shuffled by the seeded session generator.
        assert sorted(card_identities(state.players[1].hand)) == sorted(original_hand)
        reveal_messages = [text for text in result.message_texts() if 'hand revealed' in text.lower()]
        assert reveal_messages, 'the reveal must be announced'

    def test_empty_opponent_hand_skips_reveal(self):
        state = GameStateBuilder().with_battle_card(0, '03-045').build()
        result = run_effect(state, '03-045', owner_index=0)
        assert any("Opponent's hand is empty." in text for text in result.message_texts())
