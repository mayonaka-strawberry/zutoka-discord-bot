"""
Event taxonomy for the permanent per-game event stream (game_events table).

Events are recorded live only (never during replay) and are observation-only:
recording reads game state but never mutates it, so the deterministic-replay
contract and the regression baselines are unaffected.

The stream drives /zutomayo summary. The MatchNarrator translates engine
events into these types; card references are [pack, id] pairs (the card_keys
convention).
"""

from __future__ import annotations

EVENT_SERIES_START = 'series_start'
EVENT_MATCH_START = 'match_start'
EVENT_INITIAL_HAND = 'initial_hand'
EVENT_REDRAW = 'redraw'
EVENT_INITIAL_BATTLE_CARD = 'initial_battle_card'
EVENT_PHASE_ENTERED = 'phase_entered'
EVENT_DECISION_MADE = 'decision_made'
EVENT_EFFECT_PRIORITY_DETERMINED = 'effect_priority_determined'
EVENT_EFFECT_ORDER_CHOSEN = 'effect_order_chosen'
EVENT_EFFECT_RESOLVED = 'effect_resolved'
EVENT_EFFECT_SKIPPED_COST = 'effect_skipped_cost'
EVENT_BATTLE_RESULT = 'battle_result'
EVENT_STATE_SNAPSHOT = 'state_snapshot'
EVENT_NARRATION = 'narration'
EVENT_SIDE_DECK_SWAP = 'side_deck_swap'
EVENT_MATCH_RESULT = 'match_result'
EVENT_SERIES_RESULT = 'series_result'
EVENT_GAME_SAVED = 'game_saved'
EVENT_GAME_RESUMED = 'game_resumed'
EVENT_GAME_END = 'game_end'
EVENT_FORFEIT = 'forfeit'
