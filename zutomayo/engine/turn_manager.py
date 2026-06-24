from constants import CHRONOS_SIZE, NIGHT_END
from zutomayo.effects.effect_engine import EffectEngine
from zutomayo.enums.card_type import CardType
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
        if card_instance.card.send_to_power > 0:
            self.effect_engine.place_in_power_charger(card_instance, player, player.index)
        else:
            self.effect_engine.place_in_abyss(card_instance, player, player.index)

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
        # Snapshot this player's contribution for effects that reference
        # "the opponent's clock this turn" after cards have changed zones (01-026)
        self.effect_engine.turn_state.chronos_advanced[player.index] = total_clock
        return total_clock

    def get_cards_played_this_turn(self, player: Player) -> list[CardInstance]:
        cards = []
        if player.set_zone_a and player.set_zone_a.played_this_turn:
            cards.append(player.set_zone_a)
        if player.set_zone_b and player.set_zone_b.played_this_turn:
            cards.append(player.set_zone_b)
        if player.battle_zone and player.battle_zone.played_this_turn:
            cards.append(player.battle_zone)
        for card_instance in player.power_charger:
            if card_instance.played_this_turn:
                cards.append(card_instance)
        for card_instance in player.abyss:
            if card_instance.played_this_turn:
                cards.append(card_instance)
        return cards

    def get_attack_power(self, player: Player) -> int:
        # Delegates to the single source of truth on the effect engine, which
        # honors 04-099's attack_override, the power-cost gate, and day/night
        # modifiers. Effects (04-034, 04-039) use the same method.
        return self.effect_engine.get_effective_attack(self.game_state, player)

    async def do_character_swap(self, player: Player) -> None:
        new_character = None
        # Set zone A has priority for character swap
        if player.set_zone_a and player.set_zone_a.card.card_type == CardType.CHARACTER:
            new_character = player.set_zone_a
        elif player.set_zone_b and player.set_zone_b.card.card_type == CardType.CHARACTER:
            new_character = player.set_zone_b

        if new_character is None:
            return

        # 02-062 grants a permission (変えなくてもよい): the owner may skip the
        # character swap, but does not have to. Ask the player; timeout keeps
        # the current battle character (the likely intent of playing the card).
        for zone_attr in ('set_zone_a', 'set_zone_b'):
            card_instance = getattr(player, zone_attr)
            if card_instance is not None and card_instance.card.effect == '02-062' and card_instance.played_this_turn:
                effective_cost = self.effect_engine.get_effective_power_cost(card_instance, player)
                if player.total_power >= effective_cost:
                    selection = await self.effect_engine._prompt_number_selection(
                        player.index, 0, 1,
                        prompt_text=(
                            f'**Effect (02-062):** You may skip swapping {new_character.card.name} '
                            f'into the Battle Zone. 1 = skip the swap, 0 = swap normally.'
                        ),
                        placeholder='Skip the character swap?',
                    )
                    if selection is None or selection == 1:
                        return
                break

        if player.battle_zone is not None:
            old_character = player.battle_zone
            # Track the song of the swapped-out character (for effects 04-023, 04-024)
            self.effect_engine.turn_state.swapped_from_songs[player.index].add(old_character.card.song)
            self.move_to_power_or_abyss(old_character, player)
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
            self.effect_engine.on_area_enchant_leaves_play(self.game_state, old_area_enchant, player.index)

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
        # Also accumulate into total damage taken this turn (battle + effect),
        # used by 03-058/03-085's ">=30 damage taken" self-removal threshold.
        self.effect_engine.turn_state.damage_taken_this_turn[0] += result['damage_to_0']
        self.effect_engine.turn_state.damage_taken_this_turn[1] += result['damage_to_1']
        # Track the loss itself (for effect 04-095 removal): damage reduction
        # can bring battle damage to 0 even though the battle was lost.
        if result['winner'] is not None:
            self.effect_engine.turn_state.battle_lost[1 - result['winner']] = True

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
            for card_instance in player.power_charger:
                card_instance.played_this_turn = False
                card_instance.power_cost_reduction = 0
            for card_instance in player.abyss:
                card_instance.played_this_turn = False
                card_instance.power_cost_reduction = 0
        self.effect_engine.reset_turn_state()
