from __future__ import annotations
import logging
import importlib
import pkgutil
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Coroutine, Optional
from constants import CHRONOS_SIZE, MIDNIGHT, NIGHT_END
from zutomayo.enums.card_type import CardType
from zutomayo.enums.chronos import Chronos
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


@dataclass
class TurnEffectState:
    """Per-turn modifier state, reset at the end of each turn."""
    attack_bonus: dict[int, int] = field(default_factory=lambda: {0: 0, 1: 0})
    damage_reduction: dict[int, int] = field(default_factory=lambda: {0: 0, 1: 0})
    day_night_reversed: dict[int, bool] = field(default_factory=lambda: {0: False, 1: False})
    power_bonus: dict[int, int] = field(default_factory=lambda: {0: 0, 1: 0})
    character_to_power_this_turn: dict[int, bool] = field(default_factory=lambda: {0: False, 1: False})
    midnight_extended: bool = False
    end_of_turn_damage: dict[int, int] = field(default_factory=lambda: {0: 0, 1: 0})
    opponent_card_to_abyss: dict[int, bool] = field(default_factory=lambda: {0: False, 1: False})
    battle_damage: dict[int, int] = field(default_factory=lambda: {0: 0, 1: 0})
    swapped_from_songs: dict[int, set] = field(default_factory=lambda: {0: set(), 1: set()})
    damage_not_reducible: dict[int, bool] = field(default_factory=lambda: {0: False, 1: False})
    card_to_power_this_turn: dict[int, bool] = field(default_factory=lambda: {0: False, 1: False})
    attack_override: dict[int, int | None] = field(default_factory=lambda: {0: None, 1: None})
    reflect_reduction: dict[int, bool] = field(default_factory=lambda: {0: False, 1: False})
    damage_reduced_this_turn: dict[int, int] = field(default_factory=lambda: {0: 0, 1: 0})
    # Chronos transition tracking: whether a day→night or night→day transition
    # occurred at any point during this turn (even if later reverted).
    day_to_night_occurred: bool = False
    night_to_day_occurred: bool = False


@dataclass
class EffectResolutionResult:
    """Summary of which effects were processed for a player."""
    resolved: list[CardInstance] = field(default_factory=list)
    skipped_cost: list[CardInstance] = field(default_factory=list)


class EffectEngine:
    def __init__(self) -> None:
        self.session: Optional[GameSession] = None
        self.bot: Optional[discord.Client] = None
        self.turn_state = TurnEffectState()
        self._player_name_cache: dict[int, str] = {}

    def bind(self, session: GameSession, bot: discord.Client) -> None:
        self.session = session
        self.bot = bot
        self.turn_state = TurnEffectState()
        self._player_name_cache.clear()

    def player_label(self, player_index: int) -> str:
        """Return 'P0 (DisplayName)' or just 'P0' if name unavailable."""
        if player_index in self._player_name_cache:
            return self._player_name_cache[player_index]
        label = f'P{player_index}'
        if self.session is not None and self.bot is not None:
            discord_id = self.session.get_discord_id(player_index)
            if discord_id is not None:
                user = self.bot.get_user(discord_id)
                if user is not None:
                    label = f'P{player_index} ({user.display_name})'
        self._player_name_cache[player_index] = label
        return label

    def set_chronos(self, game_state: GameState, new_value: int) -> None:
        """Set chronos to a new value and track any day/night transition."""
        old_is_night = 0 <= game_state.chronos <= NIGHT_END
        new_is_night = 0 <= new_value <= NIGHT_END
        if old_is_night and not new_is_night:
            self.turn_state.night_to_day_occurred = True
        elif not old_is_night and new_is_night:
            self.turn_state.day_to_night_occurred = True
        game_state.chronos = new_value


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

    def apply_attack_modifier(self, game_state: GameState, player_index: int) -> int:
        return self.turn_state.attack_bonus.get(player_index, 0)

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
        effective_cost = self.get_effective_power_cost(area_enchant, player)
        return player.total_power >= effective_cost

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
        effective_cost = self.get_effective_power_cost(area_enchant, opponent)
        return opponent.total_power >= effective_cost

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
                effective_cost = self.get_effective_power_cost(area_enchant, player)
                if player.total_power >= effective_cost:
                    return True
        return False

    def process_end_of_turn_effects(self, game_state: GameState) -> None:
        """Apply end-of-turn effects (e.g. 03-027 damage, 03-058 healing/removal)."""
        from zutomayo.enums.zone import Zone

        log.debug('Processing end-of-turn effects')

        # Effect 03-085: check removal (>=30 damage), then advance clock if daytime
        for player in game_state.players:
            area_enchant = player.set_zone_c
            if area_enchant is not None and area_enchant.card.effect == '03-085':
                damage = self.turn_state.battle_damage.get(player.index, 0)
                if damage >= 30:
                    log.debug('[03-085] %s: removing — took %d damage (>= 30)', self.player_label(player.index), damage)
                    player.set_zone_c = None
                    area_enchant.zone = Zone.ABYSS
                    area_enchant.face_up = True
                    area_enchant.attribute_override = None
                    player.abyss.append(area_enchant)
                elif game_state.day_night == Chronos.DAY:
                    log.debug('[03-085] %s: daytime — advancing clock by 2', self.player_label(player.index))
                    self.set_chronos(game_state, (game_state.chronos + 2) % CHRONOS_SIZE)

        # Effect 03-058: check removal first (>=30 damage), then heal if still active
        for player in game_state.players:
            area_enchant = player.set_zone_c
            if area_enchant is not None and area_enchant.card.effect == '03-058':
                damage = self.turn_state.battle_damage.get(player.index, 0)
                if damage >= 30:
                    log.debug('[03-058] %s: removing — took %d damage (>= 30)', self.player_label(player.index), damage)
                    # Remove to abyss (forced, regardless of send_to_power)
                    player.set_zone_c = None
                    area_enchant.zone = Zone.ABYSS
                    area_enchant.face_up = True
                    area_enchant.attribute_override = None
                    player.abyss.append(area_enchant)

        # Effect 03-058: heal both players by 10 if still active
        for player in game_state.players:
            area_enchant = player.set_zone_c
            if area_enchant is not None and area_enchant.card.effect == '03-058':
                log.debug('[03-058] %s: still active — healing both players by 10', self.player_label(player.index))
                for player_index in (0, 1):
                    old_hp = game_state.players[player_index].hp
                    game_state.players[player_index].hp = min(100, game_state.players[player_index].hp + 10)
                    log.debug('[03-058] %s: HP %d -> %d', self.player_label(player_index), old_hp, game_state.players[player_index].hp)
                break  # Only apply once even if both players somehow have 03-058

        # Effect 03-027: end-of-turn damage
        for player_index in (0, 1):
            damage = self.turn_state.end_of_turn_damage.get(player_index, 0)
            if damage > 0:
                old_hp = game_state.players[player_index].hp
                game_state.players[player_index].hp = max(0, game_state.players[player_index].hp - damage)
                log.debug('[03-027] %s: end-of-turn damage %d (HP %d -> %d)', self.player_label(player_index), damage, old_hp, game_state.players[player_index].hp)

        # Effect 04-100: reflect reduced damage to opponent
        for player_index in (0, 1):
            if self.turn_state.reflect_reduction.get(player_index, False):
                reflected_damage = self.turn_state.damage_reduced_this_turn.get(player_index, 0)
                if reflected_damage > 0:
                    opponent_index = 1 - player_index
                    old_hp = game_state.players[opponent_index].hp
                    game_state.players[opponent_index].hp = max(0, game_state.players[opponent_index].hp - reflected_damage)
                    log.debug('[04-100] %s: reflecting %d reduced damage to %s (HP %d -> %d)', self.player_label(player_index), reflected_damage, self.player_label(opponent_index), old_hp, game_state.players[opponent_index].hp)

        # Effect 03-061: move opponent's area enchant to their Power Charger
        for player in game_state.players:
            area_enchant = player.set_zone_c
            if area_enchant is not None and area_enchant.card.effect == '03-061':
                opponent = game_state.players[1 - player.index]
                opponent_area_enchant = opponent.set_zone_c
                if opponent_area_enchant is not None:
                    log.debug('[03-061] %s: moving opponent %s area enchant %s to Power Charger', self.player_label(player.index), self.player_label(opponent.index), opponent_area_enchant.card.name)
                    opponent.set_zone_c = None
                    opponent_area_enchant.zone = Zone.POWER_CHARGER
                    opponent_area_enchant.face_up = True
                    opponent_area_enchant.attribute_override = None
                    opponent.power_charger.append(opponent_area_enchant)

    def get_effective_power_cost(self, card_instance: CardInstance, player: Player) -> int:
        cost = card_instance.card.power_cost - card_instance.power_cost_reduction
        return max(0, cost)

    def check_area_enchant_removal(
        self, game_state: GameState, turn_manager: Any, *, end_of_turn: bool = False,
    ) -> None:
        """
        Check if any area enchants should be removed.

        Some removal conditions (HP thresholds, battle damage) only apply at
        end of turn per Q&A rules.  Pass ``end_of_turn=True`` when calling
        from the END_TURN phase.
        """
        day_night_at_start = Chronos.NIGHT if 0 <= game_state.chronos_at_turn_start <= NIGHT_END else Chronos.DAY
        day_night_now = game_state.day_night

        for player in game_state.players:
            area_enchant = player.set_zone_c
            if area_enchant is None:
                continue

            # Area enchants with insufficient power cost are not removed even
            # when their removal conditions are met (Q&A rule).
            effective_cost = self.get_effective_power_cost(area_enchant, player)
            if player.total_power < effective_cost:
                continue

            should_remove = False

            if area_enchant.card.effect == '02-005':
                # Remove when any day/night transition occurred this turn (end of turn only)
                if end_of_turn and (self.turn_state.day_to_night_occurred or self.turn_state.night_to_day_occurred):
                    should_remove = True

            elif area_enchant.card.effect == '02-007':
                # Remove when opponent plays an area enchant this turn (end of turn only)
                if end_of_turn:
                    opponent = game_state.players[1 - player.index]
                    opponent_area_enchant = opponent.set_zone_c
                    if opponent_area_enchant is not None and opponent_area_enchant.played_this_turn:
                        should_remove = True

            elif area_enchant.card.effect == '02-058':
                # Remove when your character card is placed on your Power charger (end of turn only)
                if end_of_turn:
                    if self.turn_state.character_to_power_this_turn.get(player.index, False):
                        should_remove = True

            elif area_enchant.card.effect == '02-064':
                # Remove when opponent's HP falls to 30 or below (end of turn only per Q&A)
                if end_of_turn:
                    opponent = game_state.players[1 - player.index]
                    if opponent.hp <= 30:
                        should_remove = True

            elif area_enchant.card.effect == '02-086':
                # Remove when it's not night (either a transition occurred or it was already day)
                if self.turn_state.night_to_day_occurred or game_state.day_night != Chronos.NIGHT:
                    should_remove = True

            elif area_enchant.card.effect == '02-092':
                # Remove when opponent's character card costs 4 or more
                opponent = game_state.players[1 - player.index]
                if opponent.battle_zone is not None and opponent.battle_zone.card.power_cost >= 4:
                    should_remove = True

            elif area_enchant.card.effect == '02-098':
                # Remove when it's not daytime (either a transition occurred or it was already night)
                if self.turn_state.day_to_night_occurred or game_state.day_night != Chronos.DAY:
                    should_remove = True

            elif area_enchant.card.effect == '02-104':
                # Remove when your character card costs 4 or more
                if player.battle_zone is not None and player.battle_zone.card.power_cost >= 4:
                    should_remove = True

            elif area_enchant.card.effect == '03-055':
                # Remove when opponent places a card in the Abyss (end of turn only)
                if end_of_turn and self.turn_state.opponent_card_to_abyss.get(player.index, False):
                    should_remove = True

            elif area_enchant.card.effect == '03-064':
                # Remove when opponent's HP falls to 40 or below (end of turn only)
                if end_of_turn:
                    opponent = game_state.players[1 - player.index]
                    if opponent.hp <= 40:
                        should_remove = True

            elif area_enchant.card.effect == '03-086':
                # Remove if 4 or more total cards in player's Abyss (end of turn only)
                if end_of_turn and len(player.abyss) >= 4:
                    should_remove = True

            elif area_enchant.card.effect == '03-091':
                # Remove when opponent places a card in the Abyss (end of turn only)
                if end_of_turn and self.turn_state.opponent_card_to_abyss.get(player.index, False):
                    should_remove = True

            elif area_enchant.card.effect == '03-092':
                # Remove if 4 or more total cards in player's Abyss (end of turn only)
                if end_of_turn and len(player.abyss) >= 4:
                    should_remove = True

            elif area_enchant.card.effect == '03-098':
                # Remove if 4 or more total cards in player's Abyss (end of turn only)
                if end_of_turn and len(player.abyss) >= 4:
                    should_remove = True

            elif area_enchant.card.effect == '03-104':
                # Remove if 4 or more total cards in player's Abyss (end of turn only)
                if end_of_turn and len(player.abyss) >= 4:
                    should_remove = True

            elif area_enchant.card.effect == '04-030':
                # Remove when a card is placed in the opponent's Abyss
                if self.turn_state.opponent_card_to_abyss.get(player.index, False):
                    should_remove = True

            elif area_enchant.card.effect == '04-033':
                # Remove when a card is placed into your Power Charger
                if self.turn_state.card_to_power_this_turn.get(player.index, False):
                    should_remove = True

            elif area_enchant.card.effect == '04-065':
                # Remove when battle zone character is swapped to a non-(STUDY ME) character
                from zutomayo.enums.song import Song
                swapped_songs = self.turn_state.swapped_from_songs.get(player.index, set())
                if swapped_songs:  # A swap happened this turn
                    if (player.battle_zone is None
                            or player.battle_zone.card.song != Song.STUDY_ME):
                        should_remove = True

            elif area_enchant.card.effect == '04-091':
                # Remove when player's HP becomes 50 or less
                if player.hp <= 50:
                    should_remove = True

            elif area_enchant.card.effect == '04-094':
                # Remove when 5 or more cards in player's Power Charger (goes to abyss)
                if len(player.power_charger) >= 5:
                    should_remove = True

            elif area_enchant.card.effect == '04-095':
                # Remove when player loses a battle (immediately)
                if self.turn_state.battle_damage.get(player.index, 0) > 0:
                    should_remove = True

            if should_remove:
                log.debug(
                    '%s: removing area enchant %s (%s) — removal condition met',
                    self.player_label(player.index), area_enchant.card.effect, area_enchant.card.name,
                )
                player.set_zone_c = None
                # Reset area enchant block if 03-055 is removed
                if area_enchant.card.effect == '03-055':
                    opponent = game_state.players[1 - player.index]
                    opponent.area_enchant_blocked = False
                    log.debug('%s: unblocking opponent %s area enchant placement', self.player_label(player.index), self.player_label(opponent.index))
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
        effective_cost = self.get_effective_power_cost(card_instance, player)

        if card_instance.card.card_type == CardType.AREA_ENCHANT:
            if player.total_power < effective_cost:
                log.debug(
                    '%s: skipping %s (%s) — insufficient power (have %d, need %d)',
                    self.player_label(player_index), card_instance.card.effect, card_instance.card.name,
                    player.total_power, effective_cost,
                )
                return False
        else:
            total_power = player.total_power + self.turn_state.power_bonus.get(player_index, 0)
            if total_power < effective_cost:
                log.debug(
                    '%s: skipping %s (%s) — insufficient power (have %d+%d bonus, need %d)',
                    self.player_label(player_index), card_instance.card.effect, card_instance.card.name,
                    player.total_power, self.turn_state.power_bonus.get(player_index, 0),
                    effective_cost,
                )
                return False

        await self._dispatch(game_state, player_index, card_instance)
        return True

    async def _prompt_effect_order(
        self,
        player_index: int,
        eligible: list[CardInstance],
    ) -> list[CardInstance]:
        """
        Prompt a player to choose the order of their effect resolution.

        Uses sequential single-card selections.  On timeout at any step,
        remaining cards are appended in their default collection order.

        NOTE: If a future card specifies a forced ordering constraint
        (e.g. "this effect must resolve first"), that logic should be
        inserted here before prompting, pre-positioning the constrained
        card and removing it from the selectable pool.
        """
        ordered: list[CardInstance] = []
        remaining = list(eligible)

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
        if self.session is None or self.bot is None:
            return None
        discord_id = self.session.get_discord_id(player_index)
        if discord_id is None:
            return None
        user = self.bot.get_user(discord_id)
        if user is None:
            user = await self.bot.fetch_user(discord_id)
        dm_channel = await user.create_dm()
        return await dm_channel.send(**kwargs)

    async def _send_to_channel(self, **kwargs: Any) -> Optional[discord.Message]:
        if self.session is None or self.bot is None:
            return None
        channel = self.bot.get_channel(self.session.channel_id)
        if channel is None:
            return None
        return await channel.send(**kwargs)

    async def notify_draw(self, game_state: GameState, player_index: int, count: int) -> None:
        """Broadcast a draw notification to the channel and both player DMs."""
        if count <= 0:
            return
        player_name = game_state.players[player_index].name
        if self.session is not None:
            discord_id = self.session.get_discord_id(player_index)
            if discord_id is not None and self.bot is not None:
                user = self.bot.get_user(discord_id)
                if user is not None:
                    player_name = user.display_name
        card_word = 'card' if count == 1 else 'cards'
        msg = f'**{player_name}** drew **{count}** {card_word}.'
        await self._send_to_channel(content=msg)
        await self._send_dm(player_index, content=f'You drew **{count}** {card_word}.')
        await self._send_dm(1 - player_index, content=msg)

    async def _prompt_card_selection(
        self,
        player_index: int,
        cards: list[CardInstance],
        prompt_text: str,
        placeholder: str = 'Select a card...',
    ) -> Optional[CardInstance]:
        """Send a dropdown to a player's DM and wait for selection. Returns selected CardInstance or None."""
        if not cards or self.session is None:
            return None

        from zutomayo.ui.views import EffectCardSelectView

        self.session.clear_pending_player(player_index)
        view = EffectCardSelectView(self.session, player_index, cards, placeholder=placeholder)
        await self._send_dm(player_index, content=prompt_text, view=view)

        received = await self.session.wait_for_player(player_index, timeout=120.0)
        if not received:
            return None

        return self.session.pending_actions.get(player_index)

    async def _prompt_number_selection(
        self,
        player_index: int,
        min_value: int,
        max_value: int,
        prompt_text: str,
        placeholder: str = 'Select a number...',
        label_prefix: str | None = None,
    ) -> int | None:
        """Send a numeric dropdown to a player's DM and wait for selection. Returns selected int or None."""
        if self.session is None:
            return None

        from zutomayo.ui.views import EffectNumberSelectView

        self.session.clear_pending_player(player_index)
        view = EffectNumberSelectView(
            self.session, player_index, min_value, max_value,
            placeholder=placeholder, label_prefix=label_prefix,
        )
        await self._send_dm(player_index, content=prompt_text, view=view)

        received = await self.session.wait_for_player(player_index, timeout=120.0)
        if not received:
            return None

        return self.session.pending_actions.get(player_index)

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
        """Send a button that opens a text input modal. Returns the entered text or None on timeout."""
        if self.session is None:
            return None

        from zutomayo.ui.views import EffectTextInputView

        self.session.clear_pending_player(player_index)
        view = EffectTextInputView(
            self.session, player_index,
            modal_title=modal_title,
            button_label=button_label,
            label=input_label,
            placeholder=input_placeholder,
            validator=validator,
            prompt_text=prompt_text,
        )
        await self._send_dm(player_index, content=prompt_text, view=view)

        received = await self.session.wait_for_player(player_index, timeout=120.0)
        if not received:
            return None

        return self.session.pending_actions.get(player_index)


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
