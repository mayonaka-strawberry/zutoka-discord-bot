"""Observation-only engine events.

A GameState may carry an `event_sink` (a plain list, default None). When the
sink is attached, rule functions append small int tuples describing what
happened so an external driver can narrate the game. Emissions never read the
RNG and never mutate game state, and `fast_clone` always detaches the sink,
so cloned states (search, training) emit nothing.

Tuple layouts (all ints):
  (EVENT_PHASE_CHANGED, new_phase, turn)
  (EVENT_DRAW, player_index, count)
  (EVENT_PLACED_IN_ABYSS, owner_index, actor_index, definition_index)
  (EVENT_PLACED_IN_CHARGER, owner_index, actor_index, definition_index)
  (EVENT_CHRONOS_ADVANCED, steps, resulting_chronos)
  (EVENT_CHRONOS_SET, resulting_chronos)
  (EVENT_CHARACTER_SWAP, player_index, old_definition_index_or_minus_1, new_definition_index)
  (EVENT_AREA_PLACED, player_index, old_definition_index_or_minus_1, new_definition_index)
  (EVENT_EFFECT_STARTED, owner_index, definition_index, effect_index)
  (EVENT_EFFECT_SKIPPED_COST, owner_index, definition_index)
  (EVENT_HP_CHANGED, player_index, delta, new_hp)
  (EVENT_BATTLE_RESULT, attack_0, attack_1, winner_index_or_minus_1, damage)
  (EVENT_MULLIGAN_DONE, player_index, redraw_count)
  (EVENT_GAME_OVER, winner_index)
"""

(
    EVENT_PHASE_CHANGED,
    EVENT_DRAW,
    EVENT_PLACED_IN_ABYSS,
    EVENT_PLACED_IN_CHARGER,
    EVENT_CHRONOS_ADVANCED,
    EVENT_CHRONOS_SET,
    EVENT_CHARACTER_SWAP,
    EVENT_AREA_PLACED,
    EVENT_EFFECT_STARTED,
    EVENT_EFFECT_SKIPPED_COST,
    EVENT_HP_CHANGED,
    EVENT_BATTLE_RESULT,
    EVENT_MULLIGAN_DONE,
    EVENT_GAME_OVER,
) = range(14)

EVENT_NAMES = {
    EVENT_PHASE_CHANGED: "phase_changed",
    EVENT_DRAW: "draw",
    EVENT_PLACED_IN_ABYSS: "placed_in_abyss",
    EVENT_PLACED_IN_CHARGER: "placed_in_charger",
    EVENT_CHRONOS_ADVANCED: "chronos_advanced",
    EVENT_CHRONOS_SET: "chronos_set",
    EVENT_CHARACTER_SWAP: "character_swap",
    EVENT_AREA_PLACED: "area_placed",
    EVENT_EFFECT_STARTED: "effect_started",
    EVENT_EFFECT_SKIPPED_COST: "effect_skipped_cost",
    EVENT_HP_CHANGED: "hp_changed",
    EVENT_BATTLE_RESULT: "battle_result",
    EVENT_MULLIGAN_DONE: "mulligan_done",
    EVENT_GAME_OVER: "game_over",
}
