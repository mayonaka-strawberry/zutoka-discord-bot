from __future__ import annotations
import logging
import importlib
import pkgutil
import random
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING, Any, Callable, Coroutine, Optional
from constants import CHRONOS_SIZE, MIDNIGHT, NIGHT_END
from zutomayo.data.name_storage import resolve_display_name
from zutomayo.engine.decisions import (
    KIND_EFFECT_CARD_SELECT,
    KIND_EFFECT_NUMBER_SELECT,
    KIND_EFFECT_TEXT_INPUT,
    PAYLOAD_INDICES,
    PAYLOAD_NUMBER,
    PAYLOAD_TEXT,
    PURPOSE_EFFECT_ORDER,
    DecisionRequest,
    build_card_options,
)
from zutomayo.enums.card_type import CardType
from zutomayo.enums.chronos import Chronos
from zutomayo.enums.song import Song
from zutomayo.models.card_instance import CardInstance

if TYPE_CHECKING:
    import discord
    from zutomayo.engine.game_session import GameSession
    from zutomayo.models.game_state import GameState
    from zutomayo.models.player import Player


log = logging.getLogger(__name__)

EffectHandler = Callable[
    ['EffectEngine', 'GameState', int, CardInstance],
    Coroutine[Any, Any, None],
]

# Effects whose dispatch sets CardInstance.power_cost_reduction. They must
# resolve before any other effect, otherwise the cost check for the
# character/enchant they reduce will see the un-reduced cost and skip.
_COST_REDUCING_EFFECTS = frozenset({"02-006", "04-065"})


@dataclass
class TurnEffectState:
    """Per-turn modifier state, reset at the end of each turn."""
    attack_bonus: dict[int, int] = field(default_factory=lambda: {0: 0, 1: 0})
    damage_reduction: dict[int, int] = field(default_factory=lambda: {0: 0, 1: 0})
    day_night_reversed: dict[int, bool] = field(default_factory=lambda: {0: False, 1: False})
    power_bonus: dict[int, int] = field(default_factory=lambda: {0: 0, 1: 0})
    # Keyed by the charger owner: the owner placed a character card on their
    # own Power Charger this turn (02-058 removal). Like card_to_power_this_turn
    # this is an agent-based trigger (JP: 置いた, active voice), so placements
    # caused by the opponent's effects do not set it. Set via
    # EffectEngine.place_in_power_charger.
    character_to_power_this_turn: dict[int, bool] = field(default_factory=lambda: {0: False, 1: False})
    midnight_extended: bool = False
    end_of_turn_damage: dict[int, int] = field(default_factory=lambda: {0: 0, 1: 0})
    opponent_card_to_abyss: dict[int, bool] = field(default_factory=lambda: {0: False, 1: False})
    # Keyed by the abyss owner: that player's abyss received a card this turn,
    # regardless of who caused the placement (location-based trigger, 04-030).
    # opponent_card_to_abyss is the agent-based counterpart (03-055, 03-091):
    # keyed by the watcher, true when the watcher's opponent performed a placement.
    abyss_received_card: dict[int, bool] = field(default_factory=lambda: {0: False, 1: False})
    battle_damage: dict[int, int] = field(default_factory=lambda: {0: 0, 1: 0})
    # Keyed by the damaged player: total damage taken this turn from all
    # sources (battle + effect), used by 03-058/03-085's ">=30 damage taken"
    # self-removal. battle_damage tracks combat only; this accumulates every
    # damage application via EffectEngine.deal_damage and the battle resolver.
    damage_taken_this_turn: dict[int, int] = field(default_factory=lambda: {0: 0, 1: 0})
    # Keyed by the loser: lost the battle this turn even if damage reduction
    # brought the battle damage to 0 (04-095 removal).
    battle_lost: dict[int, bool] = field(default_factory=lambda: {0: False, 1: False})
    swapped_from_songs: dict[int, set] = field(default_factory=lambda: {0: set(), 1: set()})
    damage_not_reducible: dict[int, bool] = field(default_factory=lambda: {0: False, 1: False})
    # Keyed by the charger owner: the owner placed a card on their own Power
    # Charger this turn (04-033 removal). Agent-based trigger (JP: 置いた,
    # active voice). Set via EffectEngine.place_in_power_charger.
    card_to_power_this_turn: dict[int, bool] = field(default_factory=lambda: {0: False, 1: False})
    attack_override: dict[int, int | None] = field(default_factory=lambda: {0: None, 1: None})
    reflect_reduction: dict[int, bool] = field(default_factory=lambda: {0: False, 1: False})
    damage_reduced_this_turn: dict[int, int] = field(default_factory=lambda: {0: 0, 1: 0})
    # Chronos transition tracking: whether a day→night or night→day transition
    # occurred at any point during this turn (even if later reverted).
    day_to_night_occurred: bool = False
    night_to_day_occurred: bool = False
    # Keyed by player: how much that player's cards advanced the chronos during
    # the Advance Chronos phase (after 02-005/03-061 adjustments). Snapshot for
    # effects that reference "the opponent's clock this turn" (01-026), which
    # resolve after played cards have moved between zones.
    chronos_advanced: dict[int, int] = field(default_factory=lambda: {0: 0, 1: 0})


@dataclass
class EffectResolutionResult:
    """Summary of which effects were processed for a player."""
    resolved: list[CardInstance] = field(default_factory=list)
    skipped_cost: list[CardInstance] = field(default_factory=list)


# ======================================================================
# Area-enchant removal rules
# ======================================================================
#
# Each area enchant with a printed removal condition has one entry in
# _AREA_ENCHANT_REMOVAL_RULES. check_area_enchant_removal evaluates the
# table per player (player 0 first), gating on power cost BEFORE the
# condition (Q&A rule: an unaffordable enchant is never removed even when
# its condition is met).


class AreaEnchantRemovalDestination(Enum):
    # Default routing: TurnManager.move_to_power_or_abyss decides by the
    # card's SEND TO POWER value.
    POWER_OR_ABYSS = auto()
    # 04-030 only: the card text sends it to the Abyss despite its
    # SEND TO POWER star, bypassing the send_to_power routing.
    ABYSS = auto()


@dataclass(frozen=True)
class AreaEnchantRemovalRule:
    # Some removal conditions (HP thresholds, placement flags) only apply
    # at end of turn per Q&A rules; others are checked at every removal
    # window.
    end_of_turn_only: bool
    condition: Callable[['EffectEngine', 'GameState', 'Player'], bool]
    destination: AreaEnchantRemovalDestination = AreaEnchantRemovalDestination.POWER_OR_ABYSS


def _any_day_night_transition_occurred(engine: EffectEngine, game_state: GameState, player: Player) -> bool:
    return engine.turn_state.day_to_night_occurred or engine.turn_state.night_to_day_occurred


def _opponent_played_area_enchant(engine: EffectEngine, game_state: GameState, player: Player) -> bool:
    opponent_area_enchant = game_state.players[1 - player.index].set_zone_c
    return opponent_area_enchant is not None and opponent_area_enchant.played_this_turn


def _own_character_reached_power_charger(engine: EffectEngine, game_state: GameState, player: Player) -> bool:
    return engine.turn_state.character_to_power_this_turn.get(player.index, False)


def _opponent_hp_at_most(threshold: int) -> Callable[['EffectEngine', 'GameState', 'Player'], bool]:
    def condition(engine: EffectEngine, game_state: GameState, player: Player) -> bool:
        return game_state.players[1 - player.index].hp <= threshold
    return condition


def _not_night_now(engine: EffectEngine, game_state: GameState, player: Player) -> bool:
    # Either a night-to-day transition occurred or it was already day.
    return engine.turn_state.night_to_day_occurred or game_state.day_night != Chronos.NIGHT


def _not_day_now(engine: EffectEngine, game_state: GameState, player: Player) -> bool:
    # Either a day-to-night transition occurred or it was already night.
    return engine.turn_state.day_to_night_occurred or game_state.day_night != Chronos.DAY


def _battle_character_power_cost_at_least_four(
    *, use_opponent: bool,
) -> Callable[['EffectEngine', 'GameState', 'Player'], bool]:
    def condition(engine: EffectEngine, game_state: GameState, player: Player) -> bool:
        target = game_state.players[1 - player.index] if use_opponent else player
        return target.battle_zone is not None and target.battle_zone.card.power_cost >= 4
    return condition


def _opponent_placed_card_in_abyss(engine: EffectEngine, game_state: GameState, player: Player) -> bool:
    # Agent-based trigger (03-055, 03-091): keyed by the watcher, true when
    # the watcher's opponent performed an Abyss placement this turn.
    return engine.turn_state.opponent_card_to_abyss.get(player.index, False)


def _opponent_fields_area_enchant(engine: EffectEngine, game_state: GameState, player: Player) -> bool:
    return game_state.players[1 - player.index].set_zone_c is not None


def _abyss_has_four_or_more_cards(engine: EffectEngine, game_state: GameState, player: Player) -> bool:
    return len(player.abyss) >= 4


def _opponent_abyss_received_card(engine: EffectEngine, game_state: GameState, player: Player) -> bool:
    # Location-based trigger (04-030, JP: 置かれた, passive): fires no
    # matter who caused the placement into the opponent's Abyss.
    return engine.turn_state.abyss_received_card.get(1 - player.index, False)


def _own_card_reached_power_charger(engine: EffectEngine, game_state: GameState, player: Player) -> bool:
    return engine.turn_state.card_to_power_this_turn.get(player.index, False)


def _swapped_to_non_study_me_character(engine: EffectEngine, game_state: GameState, player: Player) -> bool:
    swapped_songs = engine.turn_state.swapped_from_songs.get(player.index, set())
    if not swapped_songs:   # No swap happened this turn
        return False
    return (player.battle_zone is None
            or player.battle_zone.card.song != Song.STUDY_ME)


def _own_hp_at_most(threshold: int) -> Callable[['EffectEngine', 'GameState', 'Player'], bool]:
    def condition(engine: EffectEngine, game_state: GameState, player: Player) -> bool:
        return player.hp <= threshold
    return condition


def _power_charger_has_five_or_more_cards(engine: EffectEngine, game_state: GameState, player: Player) -> bool:
    return len(player.power_charger) >= 5


def _lost_battle_this_turn(engine: EffectEngine, game_state: GameState, player: Player) -> bool:
    # Keyed on the loss itself, not battle damage: damage reduction can
    # bring the damage to 0 while the battle is still lost (04-095).
    return engine.turn_state.battle_lost.get(player.index, False)


_AREA_ENCHANT_REMOVAL_RULES: dict[str, AreaEnchantRemovalRule] = {
    # Remove when any day/night transition occurred this turn.
    '02-005': AreaEnchantRemovalRule(end_of_turn_only=True, condition=_any_day_night_transition_occurred),
    # Remove when the opponent plays an area enchant this turn.
    '02-007': AreaEnchantRemovalRule(end_of_turn_only=True, condition=_opponent_played_area_enchant),
    # Remove when your character card is placed on your Power Charger.
    '02-058': AreaEnchantRemovalRule(end_of_turn_only=True, condition=_own_character_reached_power_charger),
    # Remove when the opponent's HP falls to the threshold or below
    # (end of turn only per Q&A).
    '02-064': AreaEnchantRemovalRule(end_of_turn_only=True, condition=_opponent_hp_at_most(30)),
    '03-064': AreaEnchantRemovalRule(end_of_turn_only=True, condition=_opponent_hp_at_most(40)),
    # Remove when it is not night / not day.
    '02-086': AreaEnchantRemovalRule(end_of_turn_only=False, condition=_not_night_now),
    '02-098': AreaEnchantRemovalRule(end_of_turn_only=False, condition=_not_day_now),
    # Remove when the opponent's / your own character card costs 4 or more.
    '02-092': AreaEnchantRemovalRule(end_of_turn_only=False, condition=_battle_character_power_cost_at_least_four(use_opponent=True)),
    '02-104': AreaEnchantRemovalRule(end_of_turn_only=False, condition=_battle_character_power_cost_at_least_four(use_opponent=False)),
    # Remove when the opponent places a card in the Abyss.
    '03-055': AreaEnchantRemovalRule(end_of_turn_only=True, condition=_opponent_placed_card_in_abyss),
    '03-091': AreaEnchantRemovalRule(end_of_turn_only=True, condition=_opponent_placed_card_in_abyss),
    # Remove (routed to own Power Charger via send_to_power) when the
    # opponent has any area enchant on the field at end of turn.
    '03-061': AreaEnchantRemovalRule(end_of_turn_only=True, condition=_opponent_fields_area_enchant),
    # Remove if 4 or more total cards are in the player's Abyss.
    '03-086': AreaEnchantRemovalRule(end_of_turn_only=True, condition=_abyss_has_four_or_more_cards),
    '03-092': AreaEnchantRemovalRule(end_of_turn_only=True, condition=_abyss_has_four_or_more_cards),
    '03-098': AreaEnchantRemovalRule(end_of_turn_only=True, condition=_abyss_has_four_or_more_cards),
    '03-104': AreaEnchantRemovalRule(end_of_turn_only=True, condition=_abyss_has_four_or_more_cards),
    # Remove when a card is placed in the opponent's Abyss; goes to the
    # Abyss despite its SEND TO POWER star (card text).
    '04-030': AreaEnchantRemovalRule(end_of_turn_only=False, condition=_opponent_abyss_received_card, destination=AreaEnchantRemovalDestination.ABYSS),
    # Remove immediately when the opponent has any area enchant fielded.
    '04-032': AreaEnchantRemovalRule(end_of_turn_only=False, condition=_opponent_fields_area_enchant),
    # Remove when a card is placed into your Power Charger.
    '04-033': AreaEnchantRemovalRule(end_of_turn_only=False, condition=_own_card_reached_power_charger),
    # Remove when the battle character is swapped to a non-(STUDY ME) one.
    '04-065': AreaEnchantRemovalRule(end_of_turn_only=False, condition=_swapped_to_non_study_me_character),
    # Remove when the player's own HP becomes 50 or less.
    '04-091': AreaEnchantRemovalRule(end_of_turn_only=False, condition=_own_hp_at_most(50)),
    # Remove when 5 or more cards are in the player's Power Charger.
    '04-094': AreaEnchantRemovalRule(end_of_turn_only=False, condition=_power_charger_has_five_or_more_cards),
    # Remove immediately when the player loses a battle.
    '04-095': AreaEnchantRemovalRule(end_of_turn_only=False, condition=_lost_battle_this_turn),
}


class EffectEngine:
    def __init__(self) -> None:
        self.session: Optional[GameSession] = None
        self.bot: Optional[discord.Client] = None
        self.turn_state = TurnEffectState()
        self._player_name_cache: dict[int, str] = {}
        # Transient routing context stamped on card-selection requests issued
        # from composed prompts (currently only effect ordering); see
        # BotAgentDecisionAdapter for why the purpose matters.
        self._prompt_purpose: str = ''

    def bind(self, session: GameSession, bot: discord.Client) -> None:
        self.session = session
        self.bot = bot
        self.turn_state = TurnEffectState()
        self._player_name_cache.clear()

    @property
    def random_generator(self) -> Any:
        """
        The generator all effect randomness must draw from. Bound sessions
        provide a seeded per-game random.Random (replay determinism); unbound
        engines (headless V2 training, tests) fall back to the module random
        functions, preserving the historical stream for seeded harness runs.
        """
        if self.session is not None:
            generator = getattr(self.session, 'random_generator', None)
            if generator is not None:
                return generator
        return random

    def player_label(self, player_index: int) -> str:
        """Return 'P0 (DisplayName)' or just 'P0' if name unavailable."""
        if player_index in self._player_name_cache:
            return self._player_name_cache[player_index]
        label = f'P{player_index}'
        if self.session is not None and self.bot is not None:
            discord_id = self.session.get_discord_id(player_index)
            if discord_id:  # falsy 0 is the solo-mode bot sentinel
                label = f'P{player_index} ({resolve_display_name(self.bot, discord_id)})'
        self._player_name_cache[player_index] = label
        return label

    def opponent_of(self, game_state: GameState, player_index: int) -> Player:
        """Return the opponent of the given player."""
        return game_state.players[1 - player_index]

    def set_chronos(self, game_state: GameState, new_value: int) -> None:
        """Set chronos to a new value and track any day/night transition."""
        old_is_night = 0 <= game_state.chronos <= NIGHT_END
        new_is_night = 0 <= new_value <= NIGHT_END
        if old_is_night and not new_is_night:
            self.turn_state.night_to_day_occurred = True
        elif not old_is_night and new_is_night:
            self.turn_state.day_to_night_occurred = True
        game_state.chronos = new_value

    def deal_damage(self, game_state: GameState, player_index: int, amount: int, *, source: str = '') -> None:
        """
        Deal damage to a player's HP and record it for the turn.

        Every effect-damage application should go through here so the damage
        counts toward 03-058/03-085's ">=30 damage taken" self-removal
        (damage_taken_this_turn). Battle damage is recorded separately by the
        battle resolver, which also feeds damage_taken_this_turn.
        """
        if amount <= 0:
            return
        player = game_state.players[player_index]
        old_hp = player.hp
        player.hp = max(0, player.hp - amount)
        self.turn_state.damage_taken_this_turn[player_index] += amount
        log.debug(
            '%s%s: damage %d (HP %d -> %d)',
            f'[{source}] ' if source else '', self.player_label(player_index),
            amount, old_hp, player.hp,
        )

    def heal(self, game_state: GameState, player_index: int, amount: int, *, source: str = '') -> int:
        """
        Heal a player's HP, clamped to 100. Returns the amount actually healed.

        Healing is not damage: it never touches damage_taken_this_turn.
        """
        if amount <= 0:
            return 0
        player = game_state.players[player_index]
        old_hp = player.hp
        player.hp = min(100, player.hp + amount)
        healed_amount = player.hp - old_hp
        log.debug(
            '%s%s: healed %d (HP %d -> %d)',
            f'[{source}] ' if source else '', self.player_label(player_index),
            healed_amount, old_hp, player.hp,
        )
        return healed_amount

    def lose_game(self, game_state: GameState, player_index: int, *, source: str = '') -> None:
        """
        Make a player lose the game immediately by setting their HP to 0.

        Losing is not damage: it must not route through deal_damage, so it
        never counts toward 03-058/03-085's damage_taken_this_turn.
        """
        player = game_state.players[player_index]
        old_hp = player.hp
        player.hp = 0
        log.debug(
            '%s%s: loses the game (HP %d -> 0)',
            f'[{source}] ' if source else '', self.player_label(player_index), old_hp,
        )

    def place_in_abyss(self, card_instance: CardInstance, abyss_owner: Player, actor_index: int) -> None:
        """
        Move a card into a player's Abyss.

        Every Abyss placement must go through here so the placement triggers
        fire correctly: abyss_received_card records whose abyss got the card
        (04-030), opponent_card_to_abyss records who performed the placement
        (03-055, 03-091). actor_index is the player whose action caused the
        placement, which is not always the abyss owner (e.g. mill effects).
        """
        from zutomayo.enums.zone import Zone
        card_instance.attribute_override = None
        card_instance.effects_disabled = False
        card_instance.zone = Zone.ABYSS
        card_instance.face_up = True
        abyss_owner.abyss.append(card_instance)
        self.turn_state.abyss_received_card[abyss_owner.index] = True
        self.turn_state.opponent_card_to_abyss[1 - actor_index] = True

    def place_in_power_charger(self, card_instance: CardInstance, charger_owner: Player, actor_index: int) -> None:
        """
        Move a card onto a player's Power Charger.

        Every Power Charger placement must go through here so the placement
        triggers fire correctly: card_to_power_this_turn (04-033 removal) and
        character_to_power_this_turn (02-058 removal). Both cards use active
        voice (JP: 置いた, "you placed"), so the flags are only set when the
        actor is the charger owner — placements forced by the opponent's
        effects (e.g. 04-006, 03-097's mill) do not count. actor_index is the
        player whose action caused the placement.
        """
        from zutomayo.enums.zone import Zone
        card_instance.attribute_override = None
        card_instance.effects_disabled = False
        card_instance.zone = Zone.POWER_CHARGER
        card_instance.face_up = True
        charger_owner.power_charger.append(card_instance)
        if actor_index == charger_owner.index:
            self.turn_state.card_to_power_this_turn[charger_owner.index] = True
            if card_instance.card.card_type == CardType.CHARACTER:
                self.turn_state.character_to_power_this_turn[charger_owner.index] = True

    def return_to_deck_bottom(self, card_instance: CardInstance, deck_owner: Player) -> None:
        """
        Place a card face-down on the bottom of a player's deck (index 0 is
        the top). The caller removes the card from its source zone first,
        the same contract as place_in_abyss. Deliberately does not reset
        attribute_override/effects_disabled: Player.draw clears lingering
        negation when the card is drawn again.
        """
        from zutomayo.enums.zone import Zone
        card_instance.zone = Zone.DECK
        card_instance.face_up = False
        deck_owner.deck.append(card_instance)

    def return_to_deck_top(self, card_instance: CardInstance, deck_owner: Player) -> None:
        """
        Place a card face-down on top of a player's deck (index 0 is the
        top). Same caller contract as return_to_deck_bottom.
        """
        from zutomayo.enums.zone import Zone
        card_instance.zone = Zone.DECK
        card_instance.face_up = False
        deck_owner.deck.insert(0, card_instance)

    def mill_deck_top_to_abyss(self, deck_owner: Player, count: int, actor_index: int) -> list[CardInstance]:
        """
        Move up to count cards from the top of a player's deck into their
        own Abyss via place_in_abyss, so the placement triggers fire.
        Returns the moved cards in the order they were milled.
        """
        milled_cards: list[CardInstance] = []
        for _ in range(count):
            if not deck_owner.deck:
                break
            card_instance = deck_owner.deck.pop(0)
            self.place_in_abyss(card_instance, deck_owner, actor_index)
            milled_cards.append(card_instance)
        return milled_cards

    def on_area_enchant_leaves_play(self, game_state: GameState, area_enchant: CardInstance, owner_index: int) -> None:
        """
        Clean up persistent state tied to an area enchant when it leaves the
        field. Must be called on every removal path (printed removal
        condition, owner replacing it, or an opponent's effect removing it).
        """
        if area_enchant.card.effect == '03-055':
            # 03-055 blocks the opponent only while it is in play
            opponent = game_state.players[1 - owner_index]
            opponent.area_enchant_blocked = False
            log.debug('[03-055] left play — unblocking %s area enchant placement', self.player_label(opponent.index))


    # ------------------------------------------------------------------
    # Main entry points called from game_flow / turn_manager
    # ------------------------------------------------------------------


    async def process_effects(self, game_state: GameState, player_index: int) -> EffectResolutionResult:
        """
        Process all card effects for a player during the effect resolution phase.

        If the player has 2+ eligible effects, they are prompted via Discord DM
        to choose the resolution order.  If 0-1 eligible, processing happens
        automatically.  Power cost is checked at dispatch time, not at collection
        time, because earlier effects can grant power_bonus that makes later
        effects affordable.

        Default order (and timeout fallback):
            area enchant (set_zone_c) -> enchant A -> enchant B -> character.
        """
        result = EffectResolutionResult()
        eligible = self._collect_eligible_effects(game_state, player_index)

        if not eligible:
            log.debug('%s: no eligible effects this turn', self.player_label(player_index))
            return result

        log.debug(
            '%s: %d eligible effect(s): %s', self.player_label(player_index), len(eligible),
            ', '.join(f'{ci.card.effect} ({ci.card.name})' for ci in eligible),
        )

        if len(eligible) == 1:
            dispatched = await self._dispatch_with_cost_check(game_state, player_index, eligible[0])
            if dispatched:
                result.resolved.append(eligible[0])
            else:
                result.skipped_cost.append(eligible[0])
            return result

        # 2+ eligible effects — let the player choose order
        ordered = await self._prompt_effect_order(player_index, eligible)
        log.debug(
            '%s: resolution order: %s', self.player_label(player_index),
            ', '.join(f'{ci.card.effect} ({ci.card.name})' for ci in ordered),
        )

        for card_instance in ordered:
            # Q&A rule: game ends immediately when HP reaches 0
            if any(p.hp <= 0 for p in game_state.players):
                log.debug('%s: HP reached 0, stopping effect resolution', self.player_label(player_index))
                break
            dispatched = await self._dispatch_with_cost_check(game_state, player_index, card_instance)
            if dispatched:
                result.resolved.append(card_instance)
            else:
                result.skipped_cost.append(card_instance)

        return result

    def get_effective_attack(self, game_state: GameState, player: Player) -> int:
        """
        Compute a battle character's effective attack power.

        Single source of truth for attack: used by battle resolution
        (TurnManager.get_attack_power) and by effects that test the opponent's
        attack (04-034, 04-039). Honors 04-099's attack_override, the power-cost
        gate, and day/night modifiers (02-007 force-day, 01-005 reversal).
        Returns 0 when there is no battle character or the power cost is unmet.
        """
        if player.battle_zone is None:
            return 0

        # Effect 04-099: attack override takes precedence over all other calculations
        override = self.turn_state.attack_override.get(player.index)
        if override is not None:
            return override

        card = player.battle_zone.card
        if not self.is_effect_affordable(player.battle_zone, player):
            return 0

        force_day = self.should_force_day_attack(game_state, player.index)
        reversed_day_night = self.should_reverse_day_night(game_state, player.index)

        if force_day:
            # 02-007: always use day attack
            base = card.attack_day
        elif reversed_day_night:
            # 01-005: opponent reversed our day/night
            if game_state.day_night == Chronos.NIGHT:
                base = card.attack_day
            else:
                base = card.attack_night
        else:
            if game_state.day_night == Chronos.NIGHT:
                base = card.attack_night
            else:
                base = card.attack_day

        modifier = self.turn_state.attack_bonus.get(player.index, 0)
        return max(0, base + modifier)

    def apply_damage_reduction(self, game_state: GameState, player_index: int) -> int:
        return self.turn_state.damage_reduction.get(player_index, 0)

    def should_reverse_day_night(self, game_state: GameState, player_index: int) -> bool:
        return self.turn_state.day_night_reversed.get(player_index, False)

    def should_force_day_attack(self, game_state: GameState, player_index: int) -> bool:
        """Check if effect 02-007 (Collect the wind) is active for this player."""
        player = game_state.players[player_index]
        area_enchant = player.set_zone_c
        if area_enchant is None or area_enchant.card.effect != '02-007':
            return False
        return self.is_effect_affordable(area_enchant, player)

    def is_opponent_clock_disabled(self, game_state: GameState, player_index: int) -> bool:
        """
        Check if effect 02-005 (GREINU take a break) is disabling this player's character clock.

        The area enchant belongs to the OPPONENT and disables THIS player's character clock.
        """
        opponent_index = 1 - player_index
        opponent = game_state.players[opponent_index]
        area_enchant = opponent.set_zone_c
        if area_enchant is None or area_enchant.card.effect != '02-005':
            return False
        return self.is_effect_affordable(area_enchant, opponent)

    def is_effectively_midnight(self, game_state: GameState) -> bool:
        """
        Check if the current chronos counts as midnight.

        Normally only chronos == MIDNIGHT (4).  When midnight_extended is set
        (effect 03-026), chronos values within 2 of MIDNIGHT also count.
        """
        if game_state.chronos == MIDNIGHT:
            return True
        if self.turn_state.midnight_extended and abs(game_state.chronos - MIDNIGHT) <= 2:
            return True
        return False

    def should_override_all_clocks(self, game_state: GameState) -> bool:
        """
        Check if effect 03-061 (GAME CENTER TECHNO POOR) is active.

        When active, all cards' clocks are treated as 1 during chronos advancement.
        """
        for player in game_state.players:
            area_enchant = player.set_zone_c
            if area_enchant is not None and area_enchant.card.effect == '03-061':
                if self.is_effect_affordable(area_enchant, player):
                    return True
        return False

    def process_end_of_turn_effects(self, game_state: GameState) -> None:
        """Apply end-of-turn effects (e.g. 03-027 damage, 03-058 healing/removal)."""
        log.debug('Processing end-of-turn effects')

        # End-of-turn effect damage is applied FIRST so it counts toward the
        # 03-058/03-085 ">=30 damage taken" self-removal threshold (together
        # with the battle damage already accumulated during combat).
        self._apply_end_of_turn_damage(game_state)
        self._apply_reflected_reduction_damage(game_state)
        self._process_03_085_end_of_turn(game_state)
        self._process_03_058_end_of_turn(game_state)

    def _apply_end_of_turn_damage(self, game_state: GameState) -> None:
        """Effect 03-027: end-of-turn damage."""
        for player_index in (0, 1):
            damage = self.turn_state.end_of_turn_damage.get(player_index, 0)
            self.deal_damage(game_state, player_index, damage, source='03-027')

    def _apply_reflected_reduction_damage(self, game_state: GameState) -> None:
        """Effect 04-100: reflect this turn's reduced damage to the opponent."""
        for player_index in (0, 1):
            if self.turn_state.reflect_reduction.get(player_index, False):
                reflected_damage = self.turn_state.damage_reduced_this_turn.get(player_index, 0)
                self.deal_damage(game_state, 1 - player_index, reflected_damage, source='04-100')

    def _process_03_085_end_of_turn(self, game_state: GameState) -> None:
        """
        Effect 03-085: remove if >=30 total damage taken this turn (battle +
        effect), otherwise advance the clock by 2 if daytime. Gated on power
        cost like every other area enchant; removal routes through
        place_in_abyss so its placement triggers (04-030/03-055/03-091) fire.
        """
        for player in game_state.players:
            area_enchant = player.set_zone_c
            if area_enchant is None or area_enchant.card.effect != '03-085':
                continue
            if not self.is_effect_affordable(area_enchant, player):
                continue
            damage = self.turn_state.damage_taken_this_turn.get(player.index, 0)
            if damage >= 30:
                log.debug('[03-085] %s: removing — took %d damage (>= 30)', self.player_label(player.index), damage)
                player.set_zone_c = None
                self.place_in_abyss(area_enchant, player, player.index)
            elif game_state.day_night == Chronos.DAY:
                log.debug('[03-085] %s: daytime — advancing clock by 2', self.player_label(player.index))
                self.set_chronos(game_state, (game_state.chronos + 2) % CHRONOS_SIZE)

    def _process_03_058_end_of_turn(self, game_state: GameState) -> None:
        """
        Effect 03-058: remove if >=30 total damage taken this turn, otherwise
        (if still active) heal both players by 10. Gated on power cost;
        removal routes through place_in_abyss.
        """
        healed = False
        for player in game_state.players:
            area_enchant = player.set_zone_c
            if area_enchant is None or area_enchant.card.effect != '03-058':
                continue
            if not self.is_effect_affordable(area_enchant, player):
                continue
            damage = self.turn_state.damage_taken_this_turn.get(player.index, 0)
            if damage >= 30:
                log.debug('[03-058] %s: removing — took %d damage (>= 30)', self.player_label(player.index), damage)
                player.set_zone_c = None
                self.place_in_abyss(area_enchant, player, player.index)
            elif not healed:
                # Only heal once even if both players somehow have 03-058
                log.debug('[03-058] %s: still active — healing both players by 10', self.player_label(player.index))
                for heal_index in (0, 1):
                    self.heal(game_state, heal_index, 10, source='03-058')
                healed = True


    def get_effective_power_cost(self, card_instance: CardInstance, player: Player) -> int:
        cost = card_instance.card.power_cost - card_instance.power_cost_reduction
        return max(0, cost)

    def is_effect_affordable(self, card_instance: CardInstance, player: Player) -> bool:
        """
        Whether the player currently meets the card's power cost (checked at
        the moment the effect is processed, per the rule guide).

        Area enchants count Power Charger power only; enchants and characters
        also add turn_state.power_bonus.
        """
        effective_cost = self.get_effective_power_cost(card_instance, player)
        total_power = player.total_power
        if card_instance.card.card_type != CardType.AREA_ENCHANT:
            total_power += self.turn_state.power_bonus.get(player.index, 0)
        return total_power >= effective_cost

    def check_area_enchant_removal(
        self, game_state: GameState, turn_manager: Any, *, end_of_turn: bool = False,
    ) -> None:
        """
        Check if any area enchants should be removed.

        Some removal conditions (HP thresholds, battle damage) only apply at
        end of turn per Q&A rules.  Pass ``end_of_turn=True`` when calling
        from the END_TURN phase.
        """
        for player in game_state.players:
            area_enchant = player.set_zone_c
            if area_enchant is None:
                continue

            # Area enchants with insufficient power cost are not removed even
            # when their removal conditions are met (Q&A rule).
            if not self.is_effect_affordable(area_enchant, player):
                continue

            rule = _AREA_ENCHANT_REMOVAL_RULES.get(area_enchant.card.effect)
            if rule is None:
                continue
            if rule.end_of_turn_only and not end_of_turn:
                continue
            if not rule.condition(self, game_state, player):
                continue

            log.debug(
                '%s: removing area enchant %s (%s) — removal condition met',
                self.player_label(player.index), area_enchant.card.effect, area_enchant.card.name,
            )
            player.set_zone_c = None
            self.on_area_enchant_leaves_play(game_state, area_enchant, player.index)
            if rule.destination is AreaEnchantRemovalDestination.ABYSS:
                self.place_in_abyss(area_enchant, player, player.index)
            else:
                turn_manager.move_to_power_or_abyss(area_enchant, player)

    def save_battle_characters(self, game_state: GameState) -> None:
        """Snapshot current battle zone characters for next turn (used by effect 02-010)."""
        for player in game_state.players:
            if player.battle_zone is not None:
                game_state.previous_battle_characters[player.index] = player.battle_zone.card
            else:
                game_state.previous_battle_characters[player.index] = None

    def reset_turn_state(self) -> None:
        self.turn_state = TurnEffectState()


    # ------------------------------------------------------------------
    # Effect collection and ordering
    # ------------------------------------------------------------------


    def _collect_eligible_effects(
        self, game_state: GameState, player_index: int,
    ) -> list[CardInstance]:
        """
        Collect card instances that are candidates for effect processing.

        Power cost is NOT checked here — it is deferred to dispatch time
        because earlier effects can grant power_bonus that makes later
        effects affordable.

        Default collection order (used as timeout fallback):
            area enchant -> enchant A -> enchant B -> character.
        """
        player = game_state.players[player_index]
        eligible: list[CardInstance] = []

        # 1. Area enchant (set_zone_c) — no played_this_turn requirement
        area_enchant = player.set_zone_c
        if area_enchant is not None and area_enchant.card.effect and not area_enchant.effects_disabled:
            if _EFFECT_HANDLERS.get(area_enchant.card.effect) is not None:
                eligible.append(area_enchant)

        # 2. Enchants played this turn (set_zone_a then set_zone_b)
        for zone_attr in ('set_zone_a', 'set_zone_b'):
            card_instance: Optional[CardInstance] = getattr(player, zone_attr)
            if card_instance is None or not card_instance.played_this_turn:
                continue
            if card_instance.card.card_type != CardType.ENCHANT:
                continue
            if not card_instance.card.effect or card_instance.effects_disabled:
                continue
            if _EFFECT_HANDLERS.get(card_instance.card.effect) is not None:
                eligible.append(card_instance)

        # 3. Character in battle zone (played this turn only)
        battle_zone_card = player.battle_zone
        if battle_zone_card is not None and battle_zone_card.played_this_turn:
            if (battle_zone_card.card.card_type == CardType.CHARACTER
                    and battle_zone_card.card.effect
                    and not battle_zone_card.effects_disabled):
                if _EFFECT_HANDLERS.get(battle_zone_card.card.effect) is not None:
                    eligible.append(battle_zone_card)

        return eligible

    async def _dispatch_with_cost_check(
        self, game_state: GameState, player_index: int, card_instance: CardInstance,
    ) -> bool:
        """
        Dispatch an effect only if the player can currently afford its power cost.

        Area enchants use player.total_power only; enchants and characters
        also add turn_state.power_bonus.

        Returns True if the effect was dispatched, False if skipped due to cost.
        """
        player = game_state.players[player_index]

        if not self.is_effect_affordable(card_instance, player):
            effective_cost = self.get_effective_power_cost(card_instance, player)
            if card_instance.card.card_type == CardType.AREA_ENCHANT:
                log.debug(
                    '%s: skipping %s (%s) — insufficient power (have %d, need %d)',
                    self.player_label(player_index), card_instance.card.effect, card_instance.card.name,
                    player.total_power, effective_cost,
                )
            else:
                log.debug(
                    '%s: skipping %s (%s) — insufficient power (have %d+%d bonus, need %d)',
                    self.player_label(player_index), card_instance.card.effect, card_instance.card.name,
                    player.total_power, self.turn_state.power_bonus.get(player_index, 0),
                    effective_cost,
                )
            return False

        await self._dispatch(game_state, player_index, card_instance)
        return True

    @staticmethod
    def _partition_forced_first(
        eligible: list[CardInstance],
    ) -> tuple[list[CardInstance], list[CardInstance]]:
        """Split eligible effects into (forced-first, user-selectable).

        Cost-reducing effects must dispatch before any other effect so the
        cost check for the character/enchant they reduce sees the reduced
        cost.  Forced-first effects preserve their original collection order.
        """
        forced_first = [ci for ci in eligible if ci.card.effect in _COST_REDUCING_EFFECTS]
        selectable = [ci for ci in eligible if ci.card.effect not in _COST_REDUCING_EFFECTS]
        return forced_first, selectable

    async def _prompt_effect_order(
        self,
        player_index: int,
        eligible: list[CardInstance],
    ) -> list[CardInstance]:
        """
        Prompt a player to choose the order of their effect resolution.

        Uses sequential single-card selections.  On timeout at any step,
        remaining cards are appended in their default collection order.

        Cost-reducing effects (see ``_COST_REDUCING_EFFECTS``) are
        pre-positioned at the front of the resolution order and removed
        from the selectable pool — they must dispatch before any other
        effect so subsequent cost checks see the reduced cost.
        """
        forced_first, remaining = self._partition_forced_first(eligible)
        ordered: list[CardInstance] = list(forced_first)

        self._prompt_purpose = PURPOSE_EFFECT_ORDER
        try:
            while len(remaining) > 1:
                card_names = ', '.join(card_instance.card.name for card_instance in remaining)
                step = len(ordered) + 1
                total = len(eligible)
                prompt_text = (
                    f'**Choose effect order ({step}/{total})** '
                    f'[効果の処理順を選んでください]\n'
                    f'Remaining effects: {card_names}\n'
                    f'Select which effect to resolve next:'
                )

                selected = await self._prompt_card_selection(
                    player_index,
                    remaining,
                    prompt_text,
                    placeholder='Select next effect to resolve...',
                )

                if selected is None:
                    log.info(
                        '%s timed out during effect order selection; '
                        'using default order for remaining %d effects',
                        self.player_label(player_index), len(remaining),
                    )
                    ordered.extend(remaining)
                    return ordered

                ordered.append(selected)
                remaining.remove(selected)
        finally:
            self._prompt_purpose = ''

        # Last remaining card — no choice needed
        if remaining:
            ordered.append(remaining[0])

        return ordered


    # ------------------------------------------------------------------
    # Internal dispatch
    # ------------------------------------------------------------------


    async def _dispatch(self, game_state: GameState, player_index: int, card_instance: CardInstance) -> None:
        handler = _EFFECT_HANDLERS.get(card_instance.card.effect)
        if handler is None:
            return
        log.info(
            'Processing effect %s (%s) for %s',
            card_instance.card.effect, card_instance.card.name, self.player_label(player_index),
        )
        await handler(self, game_state, player_index, card_instance)


    # ------------------------------------------------------------------
    # Helper to send DM and wait for single-player response
    # ------------------------------------------------------------------


    async def _send_dm(self, player_index: int, **kwargs: Any) -> Optional[discord.Message]:
        if self.session is None or self.session.transport is None:
            return None
        return await self.session.transport.send_to_player(self.session, player_index, **kwargs)

    async def _send_to_channel(self, **kwargs: Any) -> Optional[discord.Message]:
        if self.session is None or self.session.transport is None:
            return None
        return await self.session.transport.send_to_channel(self.session, **kwargs)

    async def notify_draw(self, game_state: GameState, player_index: int, count: int) -> None:
        """Broadcast a draw notification to the channel and both player DMs."""
        if count <= 0:
            return
        player_name = game_state.players[player_index].name
        if self.session is not None:
            discord_id = self.session.get_discord_id(player_index)
            if discord_id:
                player_name = resolve_display_name(self.bot, discord_id)
            elif discord_id == 0 and self.session.transport is not None:
                # Solo-mode bot sentinel: the transport knows the bot's name.
                resolved_name = self.session.transport.display_name(self.session, player_index)
                if resolved_name:
                    player_name = resolved_name
        card_word = 'card' if count == 1 else 'cards'
        msg = f'**{player_name}** drew **{count}** {card_word}.'
        await self._send_to_channel(content=msg)
        await self._send_dm(player_index, content=f'You drew **{count}** {card_word}.')
        await self._send_dm(1 - player_index, content=msg)

    async def broadcast_reveal(
        self,
        player_index: int,
        revealed_cards: list[CardInstance],
        owner_message: str,
        opponent_message: str,
        *,
        owner_embed: Any = None,
        channel_message: str | None = None,
        columns: int | None = None,
    ) -> None:
        """
        Show revealed cards to both players and the channel, in that fixed
        order: owner DM, opponent DM, channel. channel_message defaults to
        opponent_message; columns defaults to the number of revealed cards.

        A fresh image is rendered before each send: discord.File is
        consumed on send, and the regression recorder logs one render
        event per call, so collapsing the three renders would change
        transcripts. Rendering resolves through the embeds module
        attribute at call time so test stubs installed on that module
        keep applying.
        """
        from zutomayo.ui import embeds
        effective_columns = len(revealed_cards) if columns is None else columns
        if channel_message is None:
            channel_message = opponent_message
        owner_kwargs: dict[str, Any] = {}
        if owner_embed is not None:
            owner_kwargs['embed'] = owner_embed

        reveal_image = await embeds.create_deck_grid_image_off_thread(revealed_cards, columns=effective_columns)
        await self._send_dm(player_index, content=owner_message, **owner_kwargs, file=reveal_image)

        reveal_image = await embeds.create_deck_grid_image_off_thread(revealed_cards, columns=effective_columns)
        await self._send_dm(1 - player_index, content=opponent_message, file=reveal_image)

        reveal_image = await embeds.create_deck_grid_image_off_thread(revealed_cards, columns=effective_columns)
        await self._send_to_channel(content=channel_message, file=reveal_image)

    async def _prompt_card_selection(
        self,
        player_index: int,
        cards: list[CardInstance],
        prompt_text: str,
        placeholder: str = 'Select a card...',
    ) -> Optional[CardInstance]:
        """Prompt a player to choose one card. Returns the selected CardInstance or None on timeout."""
        if not cards or self.session is None or self.session.broker is None:
            return None

        request = DecisionRequest(
            kind=KIND_EFFECT_CARD_SELECT,
            player_index=player_index,
            prompt_text=prompt_text,
            placeholder=placeholder,
            options=build_card_options(cards),
            minimum_selections=1,
            maximum_selections=1,
            timeout_seconds=300.0,
            purpose=self._prompt_purpose,
            live_objects=cards,
        )
        response = await self.session.broker.request(request)
        if response.payload_type != PAYLOAD_INDICES or not response.payload:
            return None
        return cards[response.payload[0]]

    async def _prompt_card_multiselect(
        self,
        player_index: int,
        cards: list[CardInstance],
        prompt_text: str,
        placeholder: str = 'Select cards...',
        min_cards: int = 0,
    ) -> Optional[list[CardInstance]]:
        """
        Let a player choose a subset of cards — exactly which ones, not just
        how many.

        Built on the single-card and number primitives (like
        ``_prompt_effect_order``) so every engine subclass that overrides those
        — Discord, bot, RL, headless — gets multi-select for free: first choose
        how many, then choose which. Returns the chosen list (possibly empty
        when ``min_cards`` is 0) or None on timeout.
        """
        if not cards:
            return [] if min_cards == 0 else None

        count = await self._prompt_number_selection(
            player_index,
            min_value=min_cards,
            max_value=len(cards),
            prompt_text=prompt_text,
            placeholder='Select how many...',
        )
        if count is None:
            return None
        if count <= 0:
            return []

        remaining = list(cards)
        chosen: list[CardInstance] = []
        for selection_number in range(count):
            pick_text = f'{prompt_text}\nChoose card {selection_number + 1} of {count}.'
            selected = await self._prompt_card_selection(
                player_index, remaining, pick_text, placeholder=placeholder,
            )
            if selected is None:
                # Timed out partway — keep whatever was chosen so far
                break
            chosen.append(selected)
            remaining.remove(selected)
        return chosen

    async def _prompt_number_selection(
        self,
        player_index: int,
        min_value: int,
        max_value: int,
        prompt_text: str,
        placeholder: str = 'Select a number...',
        label_prefix: str | None = None,
    ) -> int | None:
        """Prompt a player to choose a number. Returns the selected int or None on timeout."""
        if self.session is None or self.session.broker is None:
            return None

        request = DecisionRequest(
            kind=KIND_EFFECT_NUMBER_SELECT,
            player_index=player_index,
            prompt_text=prompt_text,
            placeholder=placeholder,
            minimum_value=min_value,
            maximum_value=max_value,
            label_prefix=label_prefix,
            timeout_seconds=300.0,
        )
        response = await self.session.broker.request(request)
        if response.payload_type != PAYLOAD_NUMBER or response.payload is None:
            return None
        return response.payload

    async def _prompt_text_input(
        self,
        player_index: int,
        prompt_text: str,
        modal_title: str = 'Specify a card',
        button_label: str = 'Enter Card ID',
        input_label: str | None = None,
        input_placeholder: str | None = None,
        validator: Callable[[str], str | None] | None = None,
    ) -> str | None:
        """Prompt a player to type a value. Returns the entered text or None on timeout."""
        if self.session is None or self.session.broker is None:
            return None

        request = DecisionRequest(
            kind=KIND_EFFECT_TEXT_INPUT,
            player_index=player_index,
            prompt_text=prompt_text,
            modal_title=modal_title,
            button_label=button_label,
            input_label=input_label,
            input_placeholder=input_placeholder,
            validator=validator,
            timeout_seconds=300.0,
        )
        response = await self.session.broker.request(request)
        if response.payload_type != PAYLOAD_TEXT or response.payload is None:
            return None
        return response.payload


# ======================================================================
# Handler registry
# ======================================================================

_EFFECT_HANDLERS: dict[str, EffectHandler] = {}
import zutomayo.effects.cards as cards_pkg

# no-op effects that originally weren't imported
_EXCLUDED_EFFECT_MODULES = {'effect_02_005', 'effect_02_007', 'effect_02_062'}

for module_info in pkgutil.iter_modules(cards_pkg.__path__):
    name = module_info.name

    if name.startswith("effect_"):
        if name in _EXCLUDED_EFFECT_MODULES:
            continue

        module = importlib.import_module(f"zutomayo.effects.cards.{name}")
        handler = getattr(module, name) # assumes the handler has the same name as the module
        
        _, set_num, card_num = name.split("_", 2)
        key = f"{set_num}-{card_num}"
        
        _EFFECT_HANDLERS[key] = handler
