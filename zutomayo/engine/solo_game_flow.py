"""
Single-player game flow for playing against BOT うにぐり.

Subclasses GameFlow to handle the bot player automatically while
the human player uses normal Discord UI interactions.
"""

from __future__ import annotations
import asyncio
import logging
import random
from typing import TYPE_CHECKING
import discord
from constants import CHRONOS_SIZE
from zutomayo.enums.chronos import Chronos
from zutomayo.enums.phase import Phase
from zutomayo.enums.result import Result
from zutomayo.enums.zone import Zone
from zutomayo.engine.bot_agent import BOT_NAME, ModelBotAgent, create_bot_agent, load_random_best_deck, load_random_saved_deck
from zutomayo.engine.bot_effect_engine import BotEffectEngine, BOT_PLAYER_INDEX
from zutomayo.engine.game_flow import GameFlow
from zutomayo.ui.embeds import (
    build_battle_result_embed,
    build_field_embed,
    build_game_over_embed,
    build_hand_embed,
    create_hand_image,
)
from zutomayo.ui.board_renderer import generate_zone_messages, render_board_image
from zutomayo.ui.deck_management_views import DeckSourceView
from zutomayo.ui.views import CardSelectView, RedrawView, TwoStepCardSelectView

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectResolutionResult
    from zutomayo.engine.game_session import GameSession
    from zutomayo.models.card import Card


log = logging.getLogger(__name__)

HUMAN_PLAYER_INDEX = 0


class SoloGameFlow(GameFlow):
    """
    Game flow for single-player mode against the UNIGURI bot.

    The human is always player index 0, the bot is always player index 1.
    Bot decisions are made via BotAgent (random by default).
    DMs and interactive views are only sent to the human player.
    """

    def __init__(self, bot: discord.Client) -> None:
        super().__init__(bot)
        self.bot_agent = create_bot_agent()

    def _update_bot_game_state(self, session: GameSession) -> None:
        """Set the current game state on the bot agent if it is model-driven."""
        if isinstance(self.bot_agent, ModelBotAgent):
            self.bot_agent.current_game_state = session.game_state

    def _player_names(self, session: GameSession) -> dict[int, str]:
        names = {}
        # Human player
        human_discord_id = session.get_discord_id(HUMAN_PLAYER_INDEX)
        if human_discord_id is not None:
            user = self.bot.get_user(human_discord_id)
            names[HUMAN_PLAYER_INDEX] = user.display_name if user else str(human_discord_id)
        # Bot player
        names[BOT_PLAYER_INDEX] = BOT_NAME
        return names

    async def _send_to_player(
        self, session: GameSession, player_index: int, **kwargs,
    ) -> discord.Message | None:
        """Send DM only to the human player; skip bot."""
        if player_index == BOT_PLAYER_INDEX:
            return None
        return await super()._send_to_player(session, player_index, **kwargs)

    async def _send_to_both(self, session: GameSession, **kwargs) -> None:
        """Send DM only to the human player."""
        await self._send_to_player(session, HUMAN_PLAYER_INDEX, **kwargs)

    async def _send_zone_messages(
        self,
        session: GameSession,
        names: dict[int, str],
        *,
        to_channel: bool = False,
        player_index: int | None = None,
        changed_indices: set[int] | None = None,
    ) -> None:
        """Send zone messages, but skip DMs to bot player."""
        if not to_channel and player_index == BOT_PLAYER_INDEX:
            return
        await super()._send_zone_messages(
            session, names,
            to_channel=to_channel,
            player_index=player_index,
            changed_indices=changed_indices,
        )

    async def _phase_announce(
        self,
        session: GameSession,
        names: dict[int, str],
        phase_description: str,
        *,
        extra_embeds: list[discord.Embed] | None = None,
        force_zones: bool = False,
        delay: float = 2.0,
    ) -> None:
        """Post game state to channel and human DM only (skip bot DM)."""
        game_state = session.game_state
        session.clear_pending()

        # Determine which zones changed since last send
        new_snapshots = self._take_zone_snapshots(game_state)
        if force_zones:
            changed_indices: set[int] | None = None
        else:
            changed_indices = self._get_changed_zone_indices(
                session._zone_snapshots, new_snapshots,
            )
        session._zone_snapshots = new_snapshots

        field_embed = build_field_embed(game_state, names)
        embeds = list(extra_embeds) if extra_embeds else []
        embeds.append(field_embed)

        content = f'**{phase_description}**'

        # Channel: zone messages then DAY perspective board
        if changed_indices is None or changed_indices:
            await self._send_zone_messages(
                session, names, to_channel=True, changed_indices=changed_indices,
            )
        board_file = render_board_image(game_state, Chronos.DAY)
        await self._send_to_channel(session, content=content, embeds=embeds, files=[board_file])

        # Human player DM only
        human_player = game_state.players[HUMAN_PLAYER_INDEX]
        if changed_indices is None or changed_indices:
            await self._send_zone_messages(
                session, names, player_index=HUMAN_PLAYER_INDEX,
                changed_indices=changed_indices,
            )
        board_file = render_board_image(game_state, human_player.side)
        await self._send_to_player(
            session, HUMAN_PLAYER_INDEX,
            content=content, embeds=embeds, files=[board_file],
        )

        if delay > 0:
            await asyncio.sleep(delay)

    async def _do_deck_building_phase(
        self, session: GameSession,
    ) -> tuple[list[Card] | None, list[Card] | None]:
        """Human selects deck normally; bot picks a random saved deck."""
        from zutomayo.data.card_loader import load_cards
        from zutomayo.data.deck_validator import build_card_index

        all_cards = load_cards()
        card_index = build_card_index(all_cards)
        session.clear_pending()
        names = self._player_names(session)

        # Send deck source view to human player only
        view = DeckSourceView(
            session=session,
            player_index=HUMAN_PLAYER_INDEX,
            all_cards=all_cards,
            card_index=card_index,
            opponent_name=names[BOT_PLAYER_INDEX],
        )
        await self._send_to_player(
            session, HUMAN_PLAYER_INDEX,
            content=(
                '**Deck Building [デッキ構築]**\n'
                'Choose how to build your deck:\n'
                '**Build a Deck** - Enter cards manually or get a random deck\n'
                '**Select a Deck** - Use one of your saved decks'
            ),
            view=view,
        )

        # Bot selects a deck from the best evaluated decks (or falls back to any saved deck)
        try:
            bot_deck_cards = load_random_best_deck(card_index)
        except ValueError:
            log.warning('No saved decks found, using random deck for bot')
            bot_deck_cards = None

        session.submit_action(BOT_PLAYER_INDEX, bot_deck_cards)

        # Wait for human player only
        await session.wait_for_both_players(timeout=600.0)

        human_deck = session.pending_actions.get(HUMAN_PLAYER_INDEX)
        bot_deck = session.pending_actions.get(BOT_PLAYER_INDEX)
        return human_deck, bot_deck

    async def _do_redraw_phase(self, session: GameSession) -> None:
        game_state = session.game_state
        session.clear_pending()
        names = self._player_names(session)

        # Send redraw view to human
        human_player = game_state.players[HUMAN_PLAYER_INDEX]
        view = RedrawView(
            session, HUMAN_PLAYER_INDEX, human_player.hand[:],
            opponent_name=names[BOT_PLAYER_INDEX],
        )
        await self._send_to_player(
            session, HUMAN_PLAYER_INDEX,
            content='Select the cards you want to redraw [最初の手札を引いたとき、一度だけ引き直しができます。]',
            view=view,
        )

        # Bot decides redraw immediately
        self._update_bot_game_state(session)
        bot_player = game_state.players[BOT_PLAYER_INDEX]
        bot_redraw = self.bot_agent.choose_redraw(bot_player.hand[:])
        session.submit_action(BOT_PLAYER_INDEX, {'redraw': bot_redraw})

        await session.wait_for_both_players()

        # Process redraws for both players
        for index in range(2):
            player = game_state.players[index]
            action = session.pending_actions.get(index, {'redraw': []})
            cards_to_redraw = action.get('redraw', [])

            if cards_to_redraw:
                count = len(cards_to_redraw)
                for card_instance in cards_to_redraw:
                    if card_instance in player.hand:
                        player.hand.remove(card_instance)

                drawn = player.draw(count)
                await session.turn_manager.effect_engine.notify_draw(game_state, index, len(drawn))

                for card_instance in cards_to_redraw:
                    card_instance.zone = Zone.DECK
                    player.deck.append(card_instance)
                random.shuffle(player.deck)

                if index == HUMAN_PLAYER_INDEX:
                    embed = build_hand_embed(player)
                    await self._send_to_player(session, index, content='New hand', embed=embed)
                    hand_file = create_hand_image(player.hand)
                    if hand_file:
                        await self._send_to_player(session, index, files=[hand_file])
            else:
                if index == HUMAN_PLAYER_INDEX:
                    await self._send_to_player(session, index, content='No change')

    async def _do_initial_battle_card(self, session: GameSession) -> None:
        game_state = session.game_state
        turn_manager = session.turn_manager
        session.clear_pending()
        names = self._player_names(session)

        # Send card select view to human
        human_player = game_state.players[HUMAN_PLAYER_INDEX]
        view = CardSelectView(
            session, HUMAN_PLAYER_INDEX, human_player.hand[:],
            min_cards=1, max_cards=1,
            placeholder='Choose a card for the Battle Zone...',
            opponent_name=names[BOT_PLAYER_INDEX],
        )
        await self._send_to_player(
            session, HUMAN_PLAYER_INDEX,
            content='Choose one card to place face-down in the Battle Zone [手札からカードを１枚選びバトルゾーンに裏向きにして置きます。]',
            view=view,
        )

        # Bot chooses immediately
        self._update_bot_game_state(session)
        bot_player = game_state.players[BOT_PLAYER_INDEX]
        bot_choice = self.bot_agent.choose_initial_battle_card(bot_player.hand[:])
        session.submit_action(BOT_PLAYER_INDEX, [bot_choice])

        await session.wait_for_both_players()

        for index in range(2):
            player = game_state.players[index]
            selected = session.pending_actions.get(index, [])
            if selected:
                turn_manager.set_initial_battle_card(player, selected[0])

    async def _do_turn(self, session: GameSession, names: dict[int, str]) -> None:
        game_state = session.game_state
        turn_manager = session.turn_manager

        # Phase 1: Set cards
        game_state.current_phase = Phase.SET_CARDS
        session.clear_pending()

        for index in range(2):
            player = game_state.players[index]
            max_cards = turn_manager.get_max_cards_to_set(player)
            max_cards = min(max_cards, len(player.hand))

            if max_cards == 0:
                session.submit_action(index, [])
                if index == HUMAN_PLAYER_INDEX:
                    await self._send_to_player(
                        session, index, content='You have no cards to set this turn.',
                    )
                continue

            if index == BOT_PLAYER_INDEX:
                # Bot chooses cards immediately
                self._update_bot_game_state(session)
                bot_selected = self.bot_agent.choose_cards_to_set(
                    player.hand[:], max_cards,
                )
                session.submit_action(BOT_PLAYER_INDEX, bot_selected)
                continue

            # Human player gets the interactive view
            embed = build_hand_embed(player)

            if max_cards > 1:
                view = TwoStepCardSelectView(
                    session, index, player.hand[:],
                    embed=embed,
                    opponent_name=names[BOT_PLAYER_INDEX],
                )
                status = f'Last Turn Result: LOSE | Set up to {max_cards} cards'
            else:
                view = CardSelectView(
                    session, index, player.hand[:],
                    min_cards=1, max_cards=1,
                    placeholder='Select a card to set...',
                    embed=embed,
                    opponent_name=names[BOT_PLAYER_INDEX],
                )
                if game_state.last_battle_winner == player.name:
                    status = 'Last Turn Result: WIN | Set 1 card'
                elif game_state.last_battle_winner is None:
                    status = 'Last Turn Result: DRAW | Set 1 card'
                else:
                    status = 'Set 1 card'

            await self._send_to_player(session, index, content=status, embed=embed)
            hand_file = create_hand_image(player.hand)
            if hand_file:
                await self._send_to_player(session, index, files=[hand_file])
            await self._send_to_player(session, index, content='Select your cards:', view=view)

        await session.wait_for_both_players()

        # Place selected cards in set zones
        for index in range(2):
            player = game_state.players[index]
            selected = session.pending_actions.get(index, [])
            if len(selected) >= 1:
                turn_manager.set_card(player, selected[0], Zone.SET_ZONE_A)
            if len(selected) >= 2:
                turn_manager.set_card(player, selected[1], Zone.SET_ZONE_B)

        # GATE: Cards set face-down
        await self._phase_announce(
            session, names, f'{game_state.turn} \u2014 Set cards [カードのセット]',
        )

        # Phase 2: Reveal
        game_state.current_phase = Phase.REVEAL
        for player in game_state.players:
            if player.set_zone_a:
                player.set_zone_a.face_up = True
            if player.set_zone_b:
                player.set_zone_b.face_up = True

        await self._phase_announce(
            session, names,
            f'{game_state.turn} \u2014 Reveal set cards [セットしたカードを公開する]',
        )

        # Phase 3: Advance chronos
        game_state.current_phase = Phase.ADVANCE_CHRONOS
        for player in game_state.players:
            turn_manager.advance_chronos(player)

        chronos_delta = (game_state.chronos - game_state.chronos_at_turn_start) % CHRONOS_SIZE
        await self._phase_announce(
            session, names,
            f'{game_state.turn} \u2014 Advance Chronos [時間を進める] ({chronos_delta})',
        )

        # Phase 4: Character swap
        game_state.current_phase = Phase.CHARACTER_SWAP
        for player in game_state.players:
            turn_manager.do_character_swap(player)

        turn_manager.effect_engine.check_area_enchant_removal(game_state, turn_manager)

        await self._phase_announce(
            session, names,
            f'{game_state.turn} \u2014 Character Swap [キャラクターの入れ替え]',
        )

        # Phase 5: Area enchant swap
        game_state.current_phase = Phase.AREA_ENCHANT_SWAP
        for player in game_state.players:
            turn_manager.do_area_enchant_swap(player)

        turn_manager.effect_engine.check_area_enchant_removal(game_state, turn_manager)

        await self._phase_announce(
            session, names,
            f'{game_state.turn} \u2014 Area Enchant Swap [エリアエンチャントの入れ替え]',
        )

        # Phase 6: Process effects (priority player first)
        game_state.current_phase = Phase.PROCESS_EFFECTS
        priority = game_state.priority_player
        other = 1 - priority

        priority_result = await turn_manager.effect_engine.process_effects(game_state, priority)
        await self._broadcast_effect_resolution(session, priority, names, priority_result)

        turn_manager.check_win_condition()
        if game_state.result != Result.IN_PROGRESS:
            return

        other_result = await turn_manager.effect_engine.process_effects(game_state, other)
        await self._broadcast_effect_resolution(session, other, names, other_result)

        await self._phase_announce(
            session, names,
            f'{game_state.turn} \u2014 Character/Enchant/Area Enchant Effects [キャラクター/エンチャント/エリアエンチャントの効果の処理]',
        )

        turn_manager.check_win_condition()
        if game_state.result != Result.IN_PROGRESS:
            return

        # Phase 7-8: Battle
        game_state.current_phase = Phase.BATTLE
        battle_result = turn_manager.resolve_battle()
        battle_embed = build_battle_result_embed(battle_result, game_state, names)

        await self._phase_announce(
            session, names,
            f'{game_state.turn} \u2014 Battle Damage Calculation [バトル：ダメージの計算]',
            extra_embeds=[battle_embed],
        )

        turn_manager.check_win_condition()
        if game_state.result != Result.IN_PROGRESS:
            return

        # Turn-end effects
        game_state.current_phase = Phase.TURN_END_EFFECTS
        turn_manager.effect_engine.process_end_of_turn_effects(game_state)

        turn_manager.check_win_condition()
        if game_state.result != Result.IN_PROGRESS:
            return

        # Phase 9-10: End turn
        game_state.current_phase = Phase.END_TURN
        for player in game_state.players:
            drawn_count = turn_manager.end_turn(player)
            await turn_manager.effect_engine.notify_draw(game_state, player.index, drawn_count)
        for player in game_state.players:
            player.hand_size_bonus += player.pending_hand_size_bonus
            player.pending_hand_size_bonus = 0
        turn_manager.effect_engine.check_area_enchant_removal(
            game_state, turn_manager, end_of_turn=True,
        )

        turn_manager.check_deck_loss()
        if game_state.result != Result.IN_PROGRESS:
            return

        turn_manager.effect_engine.save_battle_characters(game_state)
        turn_manager.reset_turn_flags()

        log.info('Game %s turn %d complete', session.game_id, game_state.turn)

        await self._phase_announce(
            session, names,
            f'Turn {game_state.turn} complete. Preparing next turn...',
            force_zones=True,
        )

    async def run_solo_game(self, session: GameSession) -> None:
        """Entry point for a single-player game against UNIGURI."""
        try:
            # Replace the session's effect engine with bot-aware version
            bot_effect_engine = BotEffectEngine(self.bot_agent)
            session.effect_engine = bot_effect_engine

            deck_1_cards, deck_2_cards = await self._do_deck_building_phase(session)
            await self._run_solo_match(session, deck_1_cards, deck_2_cards)

            from zutomayo.engine.game_session import session_manager
            session_manager.remove_game(session.game_id)

        except Exception:
            log.exception('Error in solo game flow')
            await self._send_to_channel(session, content='An error occurred. Game ended.')
            await self._send_to_player(session, HUMAN_PLAYER_INDEX, content='An error occurred. Game ended.')
            from zutomayo.engine.game_session import session_manager
            session_manager.remove_game(session.game_id)

    async def _run_solo_match(
        self,
        session: GameSession,
        deck_1_cards: list[Card] | None,
        deck_2_cards: list[Card] | None,
    ) -> int | None:
        """Run a single solo match. Mirrors run_single_match but uses solo overrides."""
        session.initialize_game(
            deck_1_cards=deck_1_cards,
            deck_2_cards=deck_2_cards,
        )
        game_state = session.game_state
        turn_manager = session.turn_manager

        # Bind bot-aware effect engine
        turn_manager.effect_engine = session.effect_engine
        turn_manager.effect_engine.bind(session, self.bot)

        names = self._player_names(session)

        # --- SETUP PHASE ---
        game_state.current_phase = Phase.SETUP
        game_state.turn = 0

        # Shuffle decks
        for player in game_state.players:
            random.shuffle(player.deck)

        # Draw initial 5 cards
        for player_index, player in enumerate(game_state.players):
            player.draw(5)
            await turn_manager.effect_engine.notify_draw(game_state, player_index, 5)

        # Send hand to human player only
        human_player = game_state.players[HUMAN_PLAYER_INDEX]
        embed = build_hand_embed(human_player)
        await self._send_to_player(session, HUMAN_PLAYER_INDEX, embed=embed)
        hand_file = create_hand_image(human_player.hand)
        if hand_file:
            await self._send_to_player(session, HUMAN_PLAYER_INDEX, files=[hand_file])

        # Redraw phase
        await self._do_redraw_phase(session)

        # Choose initial battle card
        await self._do_initial_battle_card(session)

        # Reveal initial cards
        for player in game_state.players:
            turn_manager.reveal_initial_card(player)

        # Send field state to channel and human player
        field_embed = build_field_embed(game_state, names)
        start_content = f'**ゲームスタート: {names[HUMAN_PLAYER_INDEX]} vs. {names[BOT_PLAYER_INDEX]}**'

        await self._send_zone_messages(session, names, to_channel=True)
        board_file = render_board_image(game_state, Chronos.DAY)
        await self._send_to_channel(
            session, content=start_content, embed=field_embed, files=[board_file],
        )

        # Human player DM only
        await self._send_zone_messages(
            session, names, player_index=HUMAN_PLAYER_INDEX,
        )
        board_file = render_board_image(game_state, human_player.side)
        await self._send_to_player(
            session, HUMAN_PLAYER_INDEX,
            content=start_content, embed=field_embed, files=[board_file],
        )

        log.info(
            'Solo game %s started: %s vs %s',
            session.game_id, names[HUMAN_PLAYER_INDEX], names[BOT_PLAYER_INDEX],
        )

        # Establish initial zone snapshot
        session._zone_snapshots = self._take_zone_snapshots(game_state)

        # --- TURN 1 ---
        game_state.turn = 1
        game_state.chronos_at_turn_start = game_state.chronos

        # Advance chronos for both players (from initial cards)
        game_state.current_phase = Phase.ADVANCE_CHRONOS
        for player in game_state.players:
            turn_manager.advance_chronos(player)

        chronos_delta = (game_state.chronos - game_state.chronos_at_turn_start) % CHRONOS_SIZE
        await self._phase_announce(
            session, names,
            f'1 \u2014 Advance Chronos [時間を進める] ({chronos_delta})',
        )

        # Process effects (priority player first)
        game_state.current_phase = Phase.PROCESS_EFFECTS
        priority = game_state.priority_player
        other = 1 - priority

        priority_result = await turn_manager.effect_engine.process_effects(game_state, priority)
        await self._broadcast_effect_resolution(session, priority, names, priority_result)

        turn_manager.check_win_condition()
        if game_state.result != Result.IN_PROGRESS:
            await self._end_game(session, names, remove_session=False)
            return self._winner_index(game_state)

        other_result = await turn_manager.effect_engine.process_effects(game_state, other)
        await self._broadcast_effect_resolution(session, other, names, other_result)

        await self._phase_announce(
            session, names,
            '1 \u2014 Character/Enchant/Area Enchant Effects [キャラクター/エンチャント/エリアエンチャントの効果の処理]',
        )

        turn_manager.check_win_condition()
        if game_state.result != Result.IN_PROGRESS:
            await self._end_game(session, names, remove_session=False)
            return self._winner_index(game_state)

        game_state.current_phase = Phase.BATTLE
        battle_result = turn_manager.resolve_battle()
        battle_embed = build_battle_result_embed(battle_result, game_state, names)

        await self._phase_announce(
            session, names,
            '1 \u2014 Battle Damage Calculation [バトル：ダメージの計算]',
            extra_embeds=[battle_embed],
        )

        turn_manager.check_win_condition()
        if game_state.result != Result.IN_PROGRESS:
            await self._end_game(session, names, remove_session=False)
            return self._winner_index(game_state)

        # End turn 1: draw cards
        game_state.current_phase = Phase.END_TURN
        for player in game_state.players:
            drawn_count = turn_manager.end_turn(player)
            await turn_manager.effect_engine.notify_draw(game_state, player.index, drawn_count)
        for player in game_state.players:
            player.hand_size_bonus += player.pending_hand_size_bonus
            player.pending_hand_size_bonus = 0
        turn_manager.effect_engine.check_area_enchant_removal(
            game_state, turn_manager, end_of_turn=True,
        )
        turn_manager.check_deck_loss()
        if game_state.result != Result.IN_PROGRESS:
            await self._end_game(session, names, remove_session=False)
            return self._winner_index(game_state)

        turn_manager.effect_engine.save_battle_characters(game_state)
        turn_manager.reset_turn_flags()

        await self._phase_announce(
            session, names,
            'Turn 1 complete. Preparing next turn...',
            force_zones=True,
        )

        # --- TURNS 2+ ---
        while game_state.result == Result.IN_PROGRESS:
            game_state.turn += 1
            game_state.chronos_at_turn_start = game_state.chronos
            await self._do_turn(session, names)

            if game_state.result != Result.IN_PROGRESS:
                await self._end_game(session, names, remove_session=False)
                return self._winner_index(game_state)

        return None
