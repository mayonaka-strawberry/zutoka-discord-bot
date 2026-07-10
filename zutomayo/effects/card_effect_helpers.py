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
from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING, Callable

from constants import NIGHT_END
from zutomayo.enums.attribute import Attribute
from zutomayo.enums.card_type import CardType
from zutomayo.enums.chronos import Chronos

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.enums.song import Song
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


class PlacementDestination(Enum):
    ABYSS = auto()
    DECK_BOTTOM = auto()


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


async def add_attack_bonus_if_zone_cards_all_attribute(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
    *, zone_attribute_name: str, attribute: Attribute, bonus: int, use_opponent_zone: bool = False,
) -> None:
    """
    Attack +bonus if the named zone ('power_charger' or 'abyss') is
    non-empty and contains only cards of the attribute. Zone scans read
    the printed card.attribute (the override only applies in battle).
    """
    if use_opponent_zone:
        zone_owner = engine.opponent_of(game_state, player_index)
        owner_description = 'opponent'
    else:
        zone_owner = game_state.players[player_index]
        owner_description = 'own'
    zone_cards = getattr(zone_owner, zone_attribute_name)
    if zone_cards and all(zone_card.card.attribute == attribute for zone_card in zone_cards):
        engine.turn_state.attack_bonus[player_index] += bonus
        log.debug(
            '[%s] %s: all %s %s cards are %s, +%d attack bonus (now %d)',
            card_instance.card.effect, engine.player_label(player_index),
            owner_description, zone_attribute_name, attribute.name,
            bonus, engine.turn_state.attack_bonus[player_index],
        )
    else:
        log.debug(
            '[%s] %s: %s %s not all %s (count=%d), no bonus',
            card_instance.card.effect, engine.player_label(player_index),
            owner_description, zone_attribute_name, attribute.name, len(zone_cards),
        )


async def add_attack_bonus_if_any_attribute_card_in_zone(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
    *, zone_attribute_name: str, attribute: Attribute, bonus: int,
) -> None:
    """Attack +bonus if any card of the attribute is in your named zone."""
    player = game_state.players[player_index]
    zone_cards = getattr(player, zone_attribute_name)
    if any(zone_card.card.attribute == attribute for zone_card in zone_cards):
        engine.turn_state.attack_bonus[player_index] += bonus
        log.debug(
            '[%s] %s: %s card found in %s, +%d attack bonus (now %d)',
            card_instance.card.effect, engine.player_label(player_index),
            attribute.name, zone_attribute_name,
            bonus, engine.turn_state.attack_bonus[player_index],
        )
    else:
        log.debug(
            '[%s] %s: no %s card in %s, no bonus',
            card_instance.card.effect, engine.player_label(player_index),
            attribute.name, zone_attribute_name,
        )


async def add_attack_bonus_if_zone_attribute_count_at_least(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
    *, zone_attribute_name: str, attribute: Attribute, minimum_count: int, bonus: int,
) -> None:
    """Attack +bonus if your named zone holds at least minimum_count cards of the attribute."""
    player = game_state.players[player_index]
    zone_cards = getattr(player, zone_attribute_name)
    matching_count = sum(1 for zone_card in zone_cards if zone_card.card.attribute == attribute)
    if matching_count >= minimum_count:
        engine.turn_state.attack_bonus[player_index] += bonus
        log.debug(
            '[%s] %s: %d %s cards in %s (need >= %d), +%d attack bonus (now %d)',
            card_instance.card.effect, engine.player_label(player_index),
            matching_count, attribute.name, zone_attribute_name, minimum_count,
            bonus, engine.turn_state.attack_bonus[player_index],
        )
    else:
        log.debug(
            '[%s] %s: only %d %s cards in %s (need >= %d), no bonus',
            card_instance.card.effect, engine.player_label(player_index),
            matching_count, attribute.name, zone_attribute_name, minimum_count,
        )


async def add_attack_bonus_per_matching_card_in_zone(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
    *, card_matcher: 'Callable[[CardInstance], bool]', matched_description: str,
    zone_attribute_name: str, bonus_per_card: int,
) -> None:
    """Attack +bonus_per_card for each card in your named zone the matcher accepts."""
    player = game_state.players[player_index]
    zone_cards = getattr(player, zone_attribute_name)
    matching_count = sum(1 for zone_card in zone_cards if card_matcher(zone_card))
    if matching_count > 0:
        attack_bonus = bonus_per_card * matching_count
        engine.turn_state.attack_bonus[player_index] += attack_bonus
        log.debug(
            '[%s] %s: %d %s cards in %s, +%d attack bonus (now %d)',
            card_instance.card.effect, engine.player_label(player_index),
            matching_count, matched_description, zone_attribute_name,
            attack_bonus, engine.turn_state.attack_bonus[player_index],
        )
    else:
        log.debug(
            '[%s] %s: no %s cards in %s, no bonus',
            card_instance.card.effect, engine.player_label(player_index),
            matched_description, zone_attribute_name,
        )


async def add_attack_bonus_if_abyss_has_all_attributes(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
    *, required_attributes: frozenset[Attribute], bonus: int,
) -> None:
    """Attack +bonus if your Abyss holds cards of every required attribute."""
    player = game_state.players[player_index]
    abyss_attributes = {abyss_card.card.attribute for abyss_card in player.abyss}
    log.debug(
        '[%s] %s: abyss attributes found: %s',
        card_instance.card.effect, engine.player_label(player_index), abyss_attributes,
    )
    if required_attributes.issubset(abyss_attributes):
        engine.turn_state.attack_bonus[player_index] += bonus
        log.debug(
            '[%s] %s: all required attributes present, +%d attack bonus (now %d)',
            card_instance.card.effect, engine.player_label(player_index),
            bonus, engine.turn_state.attack_bonus[player_index],
        )
    else:
        log.debug(
            '[%s] %s: missing required attributes, no bonus',
            card_instance.card.effect, engine.player_label(player_index),
        )


async def reveal_song_characters_for_attack_bonus(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
    *, song: Song, song_label: str, bonus_per_card: int,
) -> None:
    """
    Reveal any number of characters of the song from your hand (the card
    itself excluded); attack +bonus_per_card for each revealed card, then
    broadcast the revealed cards to both players and the channel.
    """
    effect_identifier = card_instance.card.effect
    player = game_state.players[player_index]

    matching_characters = [
        hand_card for hand_card in player.hand
        if hand_card.card.song == song
        and hand_card.card.card_type == CardType.CHARACTER
        and hand_card.unique_id != card_instance.unique_id
    ]

    if not matching_characters:
        await engine._send_dm(player_index, content=f'**Effect ({effect_identifier}):** No {song_label} characters in hand. No effect.')
        log.debug('[%s] %s: no %s characters in hand, no effect', effect_identifier, engine.player_label(player_index), song_label)
        return

    # Let the player choose WHICH characters to reveal, not just how many —
    # the revealed identities are shown to the opponent, so the choice matters.
    revealed_cards = await engine._prompt_card_multiselect(
        player_index,
        matching_characters,
        prompt_text=f'**Effect ({effect_identifier}):** You have {len(matching_characters)} {song_label} character(s) in hand. Choose which to reveal (Attack +{bonus_per_card} each).',
        placeholder=f'Select {song_label} characters to reveal...',
    )
    log.debug('[%s] %s: reveal selection result: %s', effect_identifier, engine.player_label(player_index), revealed_cards)

    if not revealed_cards:
        await engine._send_dm(player_index, content=f'**Effect ({effect_identifier}):** No effect.')
        log.debug('[%s] %s: nothing revealed, no effect', effect_identifier, engine.player_label(player_index))
        return

    reveal_count = len(revealed_cards)
    attack_bonus = bonus_per_card * reveal_count
    engine.turn_state.attack_bonus[player_index] += attack_bonus
    log.debug('[%s] %s: attack bonus +%d (now %d)', effect_identifier, engine.player_label(player_index), attack_bonus, engine.turn_state.attack_bonus[player_index])
    revealed_names = ', '.join(hand_card.card.name for hand_card in revealed_cards)
    owner_message = f'**Effect ({effect_identifier}):** Revealed {reveal_count} {song_label} character(s): {revealed_names}. Attack +{attack_bonus}!'
    opponent_message = f'**Effect ({effect_identifier}):** Opponent revealed {reveal_count} {song_label} character(s): {revealed_names}.'
    await engine.broadcast_reveal(player_index, revealed_cards, owner_message, opponent_message, columns=reveal_count)


async def place_one_hand_card_then_draw_one(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
    *, candidate_matcher: Callable[[CardInstance], bool], candidate_label: str, candidate_description: str,
) -> None:
    """
    Place one matching card from your hand into the Abyss; if you do,
    draw 1 card (guarded by can_draw, as the original modules were).
    candidate_label appears in player-facing messages ('electric');
    candidate_description appears in prompt text ('an electric card').
    """
    effect_identifier = card_instance.card.effect
    player = game_state.players[player_index]
    opponent_index = 1 - player_index

    candidate_cards = [hand_card for hand_card in player.hand if candidate_matcher(hand_card)]

    if not candidate_cards:
        await engine._send_dm(player_index, content=f'**Effect ({effect_identifier}):** No {candidate_label} cards in hand. No effect.')
        log.debug('[%s] %s: no %s cards in hand, no effect', effect_identifier, engine.player_label(player_index), candidate_label)
        return

    selected_card = await engine._prompt_card_selection(
        player_index,
        candidate_cards,
        prompt_text=f'**Effect ({effect_identifier}):** Choose {candidate_description} from your hand to place into the Abyss.',
        placeholder=f'Select {candidate_description}...',
    )
    log.debug('[%s] %s: card selection result: %s', effect_identifier, engine.player_label(player_index), selected_card.card.name if selected_card else None)

    if selected_card is None:
        await engine._send_dm(player_index, content=f'**Effect ({effect_identifier}):** No card selected. No effect.')
        return

    player.hand.remove(selected_card)
    engine.place_in_abyss(selected_card, player, player_index)

    await engine._send_dm(player_index, content=f'**Effect ({effect_identifier}):** Placed {selected_card.card.name} into the Abyss.')
    await engine._send_dm(opponent_index, content=f'**Effect ({effect_identifier}):** Opponent placed {selected_card.card.name} into their Abyss.')

    if player.can_draw(1):
        log.debug('[%s] %s: drawing 1 card', effect_identifier, engine.player_label(player_index))
        player.draw(1)
        await engine.notify_draw(game_state, player_index, 1)


async def place_hand_cards_then_draw_same_count(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
    *, candidate_matcher: Callable[[CardInstance], bool] | None, candidate_label: str,
    no_candidates_message: str, success_owner_template: str, success_opponent_template: str,
    destination: PlacementDestination,
) -> None:
    """
    Place any number of matching hand cards (candidate_matcher None means
    the whole hand) into the Abyss or on the bottom of your deck; if you
    placed at least one, draw the same number (capped by deck size).

    The count-then-pick loop is deliberately hand-rolled instead of
    _prompt_card_multiselect: choosing 0 at the number prompt and timing
    out on every pick send different messages ('No effect.' versus
    'No cards selected. No effect.'), and the shared primitive returns []
    for both. The message templates take {count} and {names}.
    """
    effect_identifier = card_instance.card.effect
    player = game_state.players[player_index]
    opponent_index = 1 - player_index

    if candidate_matcher is None:
        candidate_cards = list(player.hand)
    else:
        candidate_cards = [hand_card for hand_card in player.hand if candidate_matcher(hand_card)]

    if not candidate_cards:
        await engine._send_dm(player_index, content=f'**Effect ({effect_identifier}):** {no_candidates_message}')
        log.debug('[%s] %s: no candidate cards in hand, no effect', effect_identifier, engine.player_label(player_index))
        return

    if destination is PlacementDestination.ABYSS:
        destination_phrase = 'into the Abyss'
    else:
        destination_phrase = 'at the bottom of your deck'

    selection_count = await engine._prompt_number_selection(
        player_index,
        min_value=0,
        max_value=len(candidate_cards),
        prompt_text=f'**Effect ({effect_identifier}):** You have {len(candidate_cards)} {candidate_label}(s) in hand. How many do you want to place {destination_phrase}?',
        placeholder='Select number of cards...',
    )
    log.debug('[%s] %s: number selection result: %s', effect_identifier, engine.player_label(player_index), selection_count)

    if not selection_count:
        await engine._send_dm(player_index, content=f'**Effect ({effect_identifier}):** No effect.')
        return

    remaining_candidates = list(candidate_cards)
    selected_cards: list[CardInstance] = []

    for selection_number in range(1, selection_count + 1):
        selected_card = await engine._prompt_card_selection(
            player_index,
            remaining_candidates,
            prompt_text=f'**Effect ({effect_identifier}):** Choose {candidate_label} #{selection_number} of {selection_count} to place {destination_phrase}.',
            placeholder=f'Select a {candidate_label}...',
        )
        log.debug('[%s] %s: card selection result: %s', effect_identifier, engine.player_label(player_index), selected_card.card.name if selected_card else None)

        if selected_card is None:
            break

        selected_cards.append(selected_card)
        remaining_candidates.remove(selected_card)

    if not selected_cards:
        await engine._send_dm(player_index, content=f'**Effect ({effect_identifier}):** No cards selected. No effect.')
        return

    for selected_card in selected_cards:
        player.hand.remove(selected_card)
        if destination is PlacementDestination.ABYSS:
            engine.place_in_abyss(selected_card, player, player_index)
        else:
            engine.return_to_deck_bottom(selected_card, player)

    placed_names = ', '.join(selected_card.card.name for selected_card in selected_cards)
    await engine._send_dm(player_index, content=success_owner_template.format(count=len(selected_cards), names=placed_names))
    await engine._send_dm(opponent_index, content=success_opponent_template.format(count=len(selected_cards)))

    draw_count = min(len(selected_cards), len(player.deck))
    if draw_count > 0:
        log.debug('[%s] %s: drawing %s card(s)', effect_identifier, engine.player_label(player_index), draw_count)
        player.draw(draw_count)
        await engine.notify_draw(game_state, player_index, draw_count)


@dataclass(frozen=True)
class ReturnFromAbyssMessages:
    """
    Player-facing strings for return_opponent_abyss_card_to_deck_bottom.
    The success templates take {name}; None means send nothing (01-103 is
    silent on success, 04-090 narrates).
    """
    empty_abyss: str
    prompt_text: str
    placeholder: str
    no_selection: str
    success_owner_template: str | None
    success_opponent_template: str | None


async def return_opponent_abyss_card_to_deck_bottom(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
    *, messages: ReturnFromAbyssMessages,
) -> None:
    """Choose a card from the opponent's Abyss and put it on the bottom of their deck."""
    effect_identifier = card_instance.card.effect
    opponent_index = 1 - player_index
    opponent = game_state.players[opponent_index]

    if not opponent.abyss:
        await engine._send_dm(player_index, content=messages.empty_abyss)
        log.debug('[%s] %s: opponent abyss empty, no effect', effect_identifier, engine.player_label(player_index))
        return

    selected_card = await engine._prompt_card_selection(
        player_index,
        opponent.abyss,
        prompt_text=messages.prompt_text,
        placeholder=messages.placeholder,
    )
    log.debug('[%s] %s: card selection result: %s', effect_identifier, engine.player_label(player_index), selected_card.card.name if selected_card else None)

    if selected_card is None:
        await engine._send_dm(player_index, content=messages.no_selection)
        return

    opponent.abyss.remove(selected_card)
    engine.return_to_deck_bottom(selected_card, opponent)
    log.debug('[%s] %s: moved opponent abyss card %s to the bottom of their deck', effect_identifier, engine.player_label(player_index), selected_card.card.effect)

    if messages.success_owner_template is not None:
        await engine._send_dm(player_index, content=messages.success_owner_template.format(name=selected_card.card.name))
    if messages.success_opponent_template is not None:
        await engine._send_dm(opponent_index, content=messages.success_opponent_template.format(name=selected_card.card.name))
