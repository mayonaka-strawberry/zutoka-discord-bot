"""
Shared templates for card effect handlers.

Each template implements one recurring effect shape from the card pool;
the per-card modules in zutomayo/effects/cards/ stay as thin wrappers
that pass that card's parameters. This keeps registry discovery (one
module per card, discovered by module name) and the per-card
characterization tests unchanged while the behavior lives in one place.

Conventions the templates preserve from the original hand-written
modules:
- Battle-zone attribute checks read effective_attribute (honors
  attribute_override); zone scans elsewhere read the printed
  card.attribute — the override only applies to the battle character.
- Power-cost conditions on printed card text read card.power_cost, not
  the effective cost.
- A missing battle character (battle_zone is None) means the condition
  is not met.
- Bonuses accumulate with += into engine.turn_state so stacking order
  matches dispatch order.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from constants import NIGHT_END
from zutomayo.enums.attribute import Attribute
from zutomayo.enums.chronos import Chronos

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


def _attribute_names(attributes: tuple[Attribute, ...]) -> str:
    return ' or '.join(attribute.name for attribute in attributes)


def _day_night_at_turn_start(game_state: GameState) -> Chronos:
    return Chronos.NIGHT if 0 <= game_state.chronos_at_turn_start <= NIGHT_END else Chronos.DAY


async def add_attack_bonus_if_opponent_character_attribute(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
    *, attributes: tuple[Attribute, ...], bonus: int,
) -> None:
    """Attack +bonus if the opponent's battle character has one of the attributes."""
    opponent = engine.opponent_of(game_state, player_index)
    if opponent.battle_zone is not None and opponent.battle_zone.effective_attribute in attributes:
        engine.turn_state.attack_bonus[player_index] += bonus
        log.debug(
            '[%s] %s: opponent attribute is %s (%s), +%d attack bonus (now %d)',
            card_instance.card.effect, engine.player_label(player_index),
            opponent.battle_zone.effective_attribute, _attribute_names(attributes),
            bonus, engine.turn_state.attack_bonus[player_index],
        )
    else:
        log.debug(
            '[%s] %s: opponent attribute is not %s (battle_zone=%s), no bonus',
            card_instance.card.effect, engine.player_label(player_index),
            _attribute_names(attributes),
            opponent.battle_zone.effective_attribute if opponent.battle_zone else None,
        )


async def add_attack_bonus_if_own_character_attribute(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
    *, attributes: tuple[Attribute, ...], bonus: int,
) -> None:
    """Attack +bonus if your own battle character has one of the attributes."""
    player = game_state.players[player_index]
    if player.battle_zone is not None and player.battle_zone.effective_attribute in attributes:
        engine.turn_state.attack_bonus[player_index] += bonus
        log.debug(
            '[%s] %s: own attribute is %s (%s), +%d attack bonus (now %d)',
            card_instance.card.effect, engine.player_label(player_index),
            player.battle_zone.effective_attribute, _attribute_names(attributes),
            bonus, engine.turn_state.attack_bonus[player_index],
        )
    else:
        log.debug(
            '[%s] %s: own attribute is not %s (battle_zone=%s), no bonus',
            card_instance.card.effect, engine.player_label(player_index),
            _attribute_names(attributes),
            player.battle_zone.effective_attribute if player.battle_zone else None,
        )


async def heal_if_own_character_attribute(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
    *, attributes: tuple[Attribute, ...], amount: int,
) -> None:
    """Recover HP (clamped at 100) if your own battle character has one of the attributes."""
    player = game_state.players[player_index]
    if player.battle_zone is not None and player.battle_zone.effective_attribute in attributes:
        engine.heal(game_state, player_index, amount, source=card_instance.card.effect)
    else:
        log.debug(
            '[%s] %s: own attribute is not %s (battle_zone=%s), no recovery',
            card_instance.card.effect, engine.player_label(player_index),
            _attribute_names(attributes),
            player.battle_zone.effective_attribute if player.battle_zone else None,
        )


async def add_attack_bonus_if_day_night(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
    *, required_day_night: Chronos, bonus: int,
) -> None:
    """Attack +bonus if the chronos is currently in the required time of day."""
    if game_state.day_night == required_day_night:
        engine.turn_state.attack_bonus[player_index] += bonus
        log.debug(
            '[%s] %s: it is %s, +%d attack bonus (now %d)',
            card_instance.card.effect, engine.player_label(player_index),
            required_day_night, bonus, engine.turn_state.attack_bonus[player_index],
        )
    else:
        log.debug(
            '[%s] %s: it is not %s (day_night=%s), no bonus',
            card_instance.card.effect, engine.player_label(player_index),
            required_day_night, game_state.day_night,
        )


async def add_attack_bonus_on_day_night_transition(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
    *, from_day_night: Chronos, to_day_night: Chronos, bonus: int,
) -> None:
    """
    Attack +bonus when the time of day changed across the turn: derived
    from chronos_at_turn_start versus the current day_night, exactly as
    the original per-card modules did.
    """
    day_night_at_start = _day_night_at_turn_start(game_state)
    log.debug(
        '[%s] %s: turn started at %s, currently %s',
        card_instance.card.effect, engine.player_label(player_index),
        day_night_at_start, game_state.day_night,
    )
    if day_night_at_start == from_day_night and game_state.day_night == to_day_night:
        engine.turn_state.attack_bonus[player_index] += bonus
        log.debug(
            '[%s] %s: %s->%s transition detected, +%d attack bonus (now %d)',
            card_instance.card.effect, engine.player_label(player_index),
            from_day_night, to_day_night, bonus, engine.turn_state.attack_bonus[player_index],
        )
    else:
        log.debug(
            '[%s] %s: no %s->%s transition, no bonus',
            card_instance.card.effect, engine.player_label(player_index),
            from_day_night, to_day_night,
        )


async def add_attack_bonus_by_opponent_character_power_cost(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
    *, minimum_power_cost: int | None = None, maximum_power_cost: int | None = None, bonus: int,
) -> None:
    """
    Attack +bonus gated on the opponent's battle character's printed
    power cost (card.power_cost, not the effective cost). Exactly one of
    minimum_power_cost / maximum_power_cost is given.
    """
    opponent = engine.opponent_of(game_state, player_index)
    if opponent.battle_zone is None:
        log.debug(
            '[%s] %s: opponent has no battle character, no bonus',
            card_instance.card.effect, engine.player_label(player_index),
        )
        return
    power_cost = opponent.battle_zone.card.power_cost
    if minimum_power_cost is not None:
        condition_met = power_cost >= minimum_power_cost
        condition_text = f'>= {minimum_power_cost}'
    else:
        condition_met = power_cost <= maximum_power_cost
        condition_text = f'<= {maximum_power_cost}'
    if condition_met:
        engine.turn_state.attack_bonus[player_index] += bonus
        log.debug(
            '[%s] %s: opponent power_cost=%d %s, +%d attack bonus (now %d)',
            card_instance.card.effect, engine.player_label(player_index),
            power_cost, condition_text, bonus, engine.turn_state.attack_bonus[player_index],
        )
    else:
        log.debug(
            '[%s] %s: opponent power_cost=%d not %s, no bonus',
            card_instance.card.effect, engine.player_label(player_index),
            power_cost, condition_text,
        )


async def add_attack_bonus_if_own_hp_at_most(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
    *, hp_threshold: int, bonus: int,
) -> None:
    """Attack +bonus if your own HP is at or below the threshold."""
    player = game_state.players[player_index]
    if player.hp <= hp_threshold:
        engine.turn_state.attack_bonus[player_index] += bonus
        log.debug(
            '[%s] %s: HP %d <= %d, +%d attack bonus (now %d)',
            card_instance.card.effect, engine.player_label(player_index),
            player.hp, hp_threshold, bonus, engine.turn_state.attack_bonus[player_index],
        )
    else:
        log.debug(
            '[%s] %s: HP %d > %d, no bonus',
            card_instance.card.effect, engine.player_label(player_index),
            player.hp, hp_threshold,
        )


async def add_damage_reduction(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
    *, amount: int,
) -> None:
    """Unconditional damage reduction for this turn."""
    engine.turn_state.damage_reduction[player_index] += amount
    log.debug(
        '[%s] %s: +%d damage reduction (now %d)',
        card_instance.card.effect, engine.player_label(player_index),
        amount, engine.turn_state.damage_reduction[player_index],
    )
