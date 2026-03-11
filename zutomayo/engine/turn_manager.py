from constants import CHRONOS_SIZE, NIGHT_END
from zutomayo.effects.effect_engine import EffectEngine
from zutomayo.enums.card_type import CardType
from zutomayo.enums.chronos import Chronos
from zutomayo.enums.result import Result
from zutomayo.enums.zone import Zone
from zutomayo.models.card_instance import CardInstance
from zutomayo.models.game_state import GameState
from zutomayo.models.player import Player


class TurnManager:
    def __init__(self, game_state: GameState, effect_engine: EffectEngine) -> None:
        self.game_state = game_state
        self.effect_engine = effect_engine

    def move_to_power_or_abyss(self, card_instance: CardInstance, player: Player) -> None:
        card_instance.attribute_override = None
        if card_instance.card.send_to_power > 0:
            card_instance.zone = Zone.POWER_CHARGER
            card_instance.face_up = True
            player.power_charger.append(card_instance)
            # Track that a card went to this player's power charger (for effect 04-033 removal)
            self.effect_engine.turn_state.card_to_power_this_turn[player.index] = True
        else:
            card_instance.zone = Zone.ABYSS
            card_instance.face_up = True
            player.abyss.append(card_instance)
            # Track that this player sent a card to abyss (for effect 03-055 removal)
            self.effect_engine.turn_state.opponent_card_to_abyss[1 - player.index] = True

    def advance_chronos(self, player: Player) -> int:
        total_clock = 0
        clock_disabled = self.effect_engine.is_opponent_clock_disabled(self.game_state, player.index)
        all_clocks_one = self.effect_engine.should_override_all_clocks(self.game_state)
        for card_instance in self.get_cards_played_this_turn(player):
            # If opponent has 02-005 active, skip this player's CHARACTER card clock
            if clock_disabled and card_instance.card.card_type == CardType.CHARACTER:
                continue
            # If 03-061 is active, all cards' clocks are treated as 1
            total_clock += 1 if all_clocks_one else card_instance.card.clock

        # Track day/night transitions step-by-step as chronos advances
        old_chronos = self.game_state.chronos
        for _ in range(total_clock):
            old_is_night = 0 <= old_chronos <= NIGHT_END
            old_chronos = (old_chronos + 1) % CHRONOS_SIZE
            new_is_night = 0 <= old_chronos <= NIGHT_END
            if old_is_night and not new_is_night:
                self.effect_engine.turn_state.night_to_day_occurred = True
            elif not old_is_night and new_is_night:
                self.effect_engine.turn_state.day_to_night_occurred = True

        self.game_state.chronos = (self.game_state.chronos + total_clock) % CHRONOS_SIZE
        return total_clock

    def get_cards_played_this_turn(self, player: Player) -> list[CardInstance]:
        cards = []
        if player.set_zone_a and player.set_zone_a.played_this_turn:
            cards.append(player.set_zone_a)
        if player.set_zone_b and player.set_zone_b.played_this_turn:
            cards.append(player.set_zone_b)
        if player.battle_zone and player.battle_zone.played_this_turn:
            cards.append(player.battle_zone)
        return cards

    def get_attack_power(self, player: Player) -> int:
        if player.battle_zone is None:
            return 0

        # Effect 04-099: attack override takes precedence over all other calculations
        override = self.effect_engine.turn_state.attack_override.get(player.index)
        if override is not None:
            return override
        card = player.battle_zone.card
        effective_cost = self.effect_engine.get_effective_power_cost(player.battle_zone, player)
        total_power = player.total_power + self.effect_engine.turn_state.power_bonus.get(player.index, 0)
        if total_power < effective_cost:
            return 0

        # Determine which attack value to use
        force_day = self.effect_engine.should_force_day_attack(self.game_state, player.index)
        reversed_dn = self.effect_engine.should_reverse_day_night(self.game_state, player.index)

        if force_day:
            # 02-007: always use day attack
            base = card.attack_day
        elif reversed_dn:
            # 01-005: opponent reversed our day/night
            if self.game_state.day_night == Chronos.NIGHT:
                base = card.attack_day
            else:
                base = card.attack_night
        else:
            if self.game_state.day_night == Chronos.NIGHT:
                base = card.attack_night
            else:
                base = card.attack_day

        modifier = self.effect_engine.apply_attack_modifier(self.game_state, player.index)
        return max(0, base + modifier)

    def do_character_swap(self, player: Player) -> None:
        new_character = None
        # Set zone A has priority for character swap
        if player.set_zone_a and player.set_zone_a.card.card_type == CardType.CHARACTER:
            new_character = player.set_zone_a
        elif player.set_zone_b and player.set_zone_b.card.card_type == CardType.CHARACTER:
            new_character = player.set_zone_b

        if new_character is None:
            return

        # Check if 02-062 is active — skip character swap
        for zone_attr in ('set_zone_a', 'set_zone_b'):
            card_instance = getattr(player, zone_attr)
            if card_instance is not None and card_instance.card.effect == '02-062' and card_instance.played_this_turn:
                effective_cost = self.effect_engine.get_effective_power_cost(card_instance, player)
                if player.total_power >= effective_cost:
                    return

        if player.battle_zone is not None:
            old_character = player.battle_zone
            # Track the song of the swapped-out character (for effects 04-023, 04-024)
            self.effect_engine.turn_state.swapped_from_songs[player.index].add(old_character.card.song)
            self.move_to_power_or_abyss(old_character, player)
            # Track if a character was sent to power charger (for 02-058 removal)
            if old_character.card.send_to_power > 0:
                self.effect_engine.turn_state.character_to_power_this_turn[player.index] = True
            player.battle_zone = None

        new_character.zone = Zone.BATTLE_ZONE
        new_character.face_up = True
        player.battle_zone = new_character

        if new_character is player.set_zone_a:
            player.set_zone_a = None
        elif new_character is player.set_zone_b:
            player.set_zone_b = None

    def do_area_enchant_swap(self, player: Player) -> None:
        new_area_enchant = None
        # Set zone A has priority for area enchant swap
        if player.set_zone_a and player.set_zone_a.card.card_type == CardType.AREA_ENCHANT:
            new_area_enchant = player.set_zone_a
        elif player.set_zone_b and player.set_zone_b.card.card_type == CardType.AREA_ENCHANT:
            new_area_enchant = player.set_zone_b

        if new_area_enchant is None:
            return

        # Effect 03-055: opponent cannot set area enchants while the card is in play.
        # Q&A rule: the blocked AE is immediately sent to power/abyss after clock reference.
        if player.area_enchant_blocked:
            self.move_to_power_or_abyss(new_area_enchant, player)
            if new_area_enchant is player.set_zone_a:
                player.set_zone_a = None
            elif new_area_enchant is player.set_zone_b:
                player.set_zone_b = None
            return

        if player.set_zone_c is not None:
            old_area_enchant = player.set_zone_c
            self.move_to_power_or_abyss(old_area_enchant, player)
            player.set_zone_c = None

        new_area_enchant.zone = Zone.SET_ZONE_C
        new_area_enchant.face_up = True
        player.set_zone_c = new_area_enchant

        if new_area_enchant is player.set_zone_a:
            player.set_zone_a = None
        elif new_area_enchant is player.set_zone_b:
            player.set_zone_b = None

    def resolve_battle(self) -> dict:
        player_0 = self.game_state.players[0]
        player_1 = self.game_state.players[1]

        attack_0 = self.get_attack_power(player_0)
        attack_1 = self.get_attack_power(player_1)

        result = {
            'player_0_attack': attack_0,
            'player_1_attack': attack_1,
            'damage_to_0': 0,
            'damage_to_1': 0,
            'winner': None,
        }

        if attack_0 > attack_1:
            raw_damage = attack_0 - attack_1
            # Effect 04-024: damage dealt by this character cannot be reduced
            if self.effect_engine.turn_state.damage_not_reducible.get(0, False):
                reduction = 0
            else:
                reduction = self.effect_engine.apply_damage_reduction(self.game_state, 1)
            damage = max(0, raw_damage - reduction)
            # Effect 04-100: track how much damage was actually reduced
            self.effect_engine.turn_state.damage_reduced_this_turn[1] = raw_damage - damage
            player_1.hp = max(0, player_1.hp - damage)
            result['damage_to_1'] = damage
            result['winner'] = 0
            self.game_state.last_battle_winner = player_0.name
        elif attack_1 > attack_0:
            raw_damage = attack_1 - attack_0
            # Effect 04-024: damage dealt by this character cannot be reduced
            if self.effect_engine.turn_state.damage_not_reducible.get(1, False):
                reduction = 0
            else:
                reduction = self.effect_engine.apply_damage_reduction(self.game_state, 0)
            damage = max(0, raw_damage - reduction)
            # Effect 04-100: track how much damage was actually reduced
            self.effect_engine.turn_state.damage_reduced_this_turn[0] = raw_damage - damage
            player_0.hp = max(0, player_0.hp - damage)
            result['damage_to_0'] = damage
            result['winner'] = 1
            self.game_state.last_battle_winner = player_1.name
        else:
            self.game_state.last_battle_winner = None

        # Store battle damage in turn state (for effect 03-058 removal)
        self.effect_engine.turn_state.battle_damage[0] = result['damage_to_0']
        self.effect_engine.turn_state.battle_damage[1] = result['damage_to_1']

        return result

    def end_turn(self, player: Player) -> int:
        # Move cards remaining in set zones A/B to power charger/abyss
        for zone_attr in ('set_zone_a', 'set_zone_b'):
            card_instance = getattr(player, zone_attr)
            if card_instance is not None and card_instance.played_this_turn:
                self.move_to_power_or_abyss(card_instance, player)
                setattr(player, zone_attr, None)

        # Draw cards equal to number played this turn plus any hand size bonus
        draw_count = player.cards_played_this_turn
        if player.can_draw(draw_count):
            player.draw(draw_count)
            return draw_count
        else:
            remaining = len(player.deck)
            if remaining > 0:
                player.draw(remaining)
                return remaining
            return 0

    def check_win_condition(self) -> None:
        player_0 = self.game_state.players[0]
        player_1 = self.game_state.players[1]

        if player_0.hp <= 0 and player_1.hp <= 0:
            if player_0.hp > player_1.hp:
                self.game_state.result = Result.PLAYER_1_WIN
            elif player_1.hp > player_0.hp:
                self.game_state.result = Result.PLAYER_2_WIN
            else:
                # Both at same HP <= 0, tie goes to player 1
                self.game_state.result = Result.PLAYER_1_WIN
        elif player_0.hp <= 0:
            self.game_state.result = Result.PLAYER_2_WIN
        elif player_1.hp <= 0:
            self.game_state.result = Result.PLAYER_1_WIN

    def check_deck_loss(self) -> None:
        for i, player in enumerate(self.game_state.players):
            if player.cards_played_this_turn > 0 and not player.can_draw(player.cards_played_this_turn):
                if len(player.deck) == 0:
                    if i == 0:
                        self.game_state.result = Result.PLAYER_2_WIN
                    else:
                        self.game_state.result = Result.PLAYER_1_WIN

    def get_max_cards_to_set(self, player: Player) -> int:
        if self.game_state.last_battle_winner is None:
            return 1  # Draw: 1 card each
        if self.game_state.last_battle_winner == player.name:
            return 1  # Winner: 1 card
        else:
            return 2  # Loser: up to 2 cards

    def set_card(self, player: Player, card_instance: CardInstance, zone: Zone) -> None:
        if card_instance in player.hand:
            player.hand.remove(card_instance)
        card_instance.zone = zone
        card_instance.face_up = False
        card_instance.played_this_turn = True
        player.cards_played_this_turn += 1

        if zone == Zone.SET_ZONE_A:
            player.set_zone_a = card_instance
        elif zone == Zone.SET_ZONE_B:
            player.set_zone_b = card_instance

    def set_initial_battle_card(self, player: Player, card_instance: CardInstance) -> None:
        if card_instance in player.hand:
            player.hand.remove(card_instance)
        card_instance.zone = Zone.BATTLE_ZONE
        card_instance.face_up = False
        card_instance.played_this_turn = True
        player.cards_played_this_turn += 1
        player.battle_zone = card_instance

    def reveal_initial_card(self, player: Player) -> bool:
        """Reveal the initial battle card. Returns True if it's a character (stays), False if not."""
        if player.battle_zone is None:
            return False
        player.battle_zone.face_up = True
        if player.battle_zone.card.card_type != CardType.CHARACTER:
            card_instance = player.battle_zone
            player.battle_zone = None
            self.move_to_power_or_abyss(card_instance, player)
            return False
        return True

    def reset_turn_flags(self) -> None:
        for player in self.game_state.players:
            player.cards_played_this_turn = 0
            for card_instance in player.hand:
                card_instance.played_this_turn = False
                card_instance.power_cost_reduction = 0
            if player.battle_zone:
                player.battle_zone.played_this_turn = False
                player.battle_zone.power_cost_reduction = 0
            if player.set_zone_a:
                player.set_zone_a.played_this_turn = False
                player.set_zone_a.power_cost_reduction = 0
            if player.set_zone_b:
                player.set_zone_b.played_this_turn = False
                player.set_zone_b.power_cost_reduction = 0
            if player.set_zone_c:
                player.set_zone_c.played_this_turn = False
        self.effect_engine.reset_turn_state()
