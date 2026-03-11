from __future__ import annotations
import asyncio
import logging
from typing import TYPE_CHECKING
import discord
from constants import CHRONOS_SIZE
from zutomayo.enums.chronos import Chronos
from zutomayo.enums.phase import Phase
from zutomayo.enums.result import Result
from zutomayo.enums.zone import Zone
from zutomayo.ui.embeds import (
    build_battle_result_embed,
    build_effect_resolution_embed,
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


class GameFlow:
    def __init__(self, bot: discord.Client) -> None:
        self.bot = bot

    def _player_names(self, session: GameSession) -> dict[int, str]:
        names = {}
        for discord_id, index in session.player_discord_ids.items():
            user = self.bot.get_user(discord_id)
            names[index] = user.display_name if user else str(discord_id)
        return names

    async def _get_dm_channel(self, discord_id: int) -> discord.DMChannel:
        user = self.bot.get_user(discord_id)
        if user is None:
            user = await self.bot.fetch_user(discord_id)
        return await user.create_dm()

    async def _send_to_player(self, session: GameSession, player_index: int, **kwargs) -> discord.Message:
        discord_id = session.get_discord_id(player_index)
        dm_channel = await self._get_dm_channel(discord_id)
        return await dm_channel.send(**kwargs)

    async def _send_to_both(self, session: GameSession, **kwargs) -> None:
        for index in range(2):
            await self._send_to_player(session, index, **kwargs)

    async def _send_to_channel(self, session: GameSession, **kwargs) -> None:
        channel = self.bot.get_channel(session.channel_id)
        if channel:
            await channel.send(**kwargs)

    @staticmethod
    def _take_zone_snapshots(game_state: GameState) -> dict[str, frozenset[str]]:
        """Capture current card identity sets for all 4 tracked zones."""
        snapshots: dict[str, frozenset[str]] = {}
        for index in range(2):
            player = game_state.players[index]
            snapshots[f'p{index}_abyss'] = frozenset(
                ci.unique_id for ci in player.abyss
            )
            snapshots[f'p{index}_power_charger'] = frozenset(
                ci.unique_id for ci in player.power_charger
            )
        return snapshots

    @staticmethod
    def _get_changed_zone_indices(
        old_snapshots: dict[str, frozenset[str]],
        new_snapshots: dict[str, frozenset[str]],
    ) -> set[int]:
        """Return indices (0-3) of zone messages that have changed.

        Index mapping matches generate_zone_messages() output order:
          0 = P0 Abyss, 1 = P0 Power Charger,
          2 = P1 Abyss, 3 = P1 Power Charger.
        """
        zone_keys = ['p0_abyss', 'p0_power_charger', 'p1_abyss', 'p1_power_charger']
        changed: set[int] = set()
        for i, key in enumerate(zone_keys):
            old = old_snapshots.get(key)
            new = new_snapshots[key]
            if old is None or old != new:
                changed.add(i)
        return changed

    async def _send_zone_messages(
        self,
        session: GameSession,
        names: dict[int, str],
        *,
        to_channel: bool = False,
        player_index: int | None = None,
        changed_indices: set[int] | None = None,
    ) -> None:
        """Send Abyss/Power Charger zone images as separate messages.

        If changed_indices is provided, only send messages for those indices.
        """
        zone_msgs = generate_zone_messages(session.game_state, names)
        for i, (label, strip_file) in enumerate(zone_msgs):
            if changed_indices is not None and i not in changed_indices:
                continue
            if strip_file:
                kwargs = {'content': label, 'files': [strip_file]}
            else:
                kwargs = {'content': f'{label} Empty'}
            if to_channel:
                await self._send_to_channel(session, **kwargs)
            else:
                await self._send_to_player(session, player_index, **kwargs)

    async def _broadcast_effect_resolution(
        self,
        session: GameSession,
        player_index: int,
        names: dict[int, str],
        result: EffectResolutionResult,
    ) -> None:
        """Broadcast a player's effect resolution results to the channel and both DMs.

        Skips broadcasting if the player had no eligible effects.
        """
        if not result.resolved and not result.skipped_cost:
            return

        embed = build_effect_resolution_embed(
            player_name=names[player_index],
            resolved=result.resolved,
            skipped_cost=result.skipped_cost,
        )
        await self._send_to_channel(session, embed=embed)
        await self._send_to_both(session, embed=embed)

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
        """Post game state without waiting for player readiness."""
        game_state = session.game_state
        session.clear_pending()

        # Determine which zones changed since last send
        new_snapshots = self._take_zone_snapshots(game_state)
        if force_zones:
            changed_indices: set[int] | None = None  # None = send all
        else:
            changed_indices = self._get_changed_zone_indices(
                session._zone_snapshots, new_snapshots,
            )
        session._zone_snapshots = new_snapshots

        field_embed = build_field_embed(game_state, names)
        embeds = list(extra_embeds) if extra_embeds else []
        embeds.append(field_embed)

        content = f'**{phase_description}**'

        # Channel: zone messages (only changed) then DAY perspective board
        if changed_indices is None or changed_indices:
            await self._send_zone_messages(session, names, to_channel=True, changed_indices=changed_indices)
        board_file = render_board_image(game_state, Chronos.DAY)
        await self._send_to_channel(session, content=content, embeds=embeds, files=[board_file])

        # Each player DM: zone messages (only changed) then their perspective board (no button)
        for index in range(2):
            player = game_state.players[index]
            if changed_indices is None or changed_indices:
                await self._send_zone_messages(session, names, player_index=index, changed_indices=changed_indices)
            board_file = render_board_image(game_state, player.side)
            await self._send_to_player(
                session, index, content=content, embeds=embeds, files=[board_file],
            )

        if delay > 0:
            await asyncio.sleep(delay)

    async def _do_deck_building_phase(
        self, session: GameSession,
    ) -> tuple[list[Card] | None, list[Card] | None]:
        """Run the deck-building phase for both players simultaneously.

        Returns (player_0_cards, player_1_cards). None for a player means
        they timed out and should receive a random deck.
        """
        from zutomayo.data.card_loader import load_cards
        from zutomayo.data.deck_validator import build_card_index

        all_cards = load_cards()
        card_index = build_card_index(all_cards)
        session.clear_pending()
        names = self._player_names(session)

        for index in range(2):
            view = DeckSourceView(
                session=session,
                player_index=index,
                all_cards=all_cards,
                card_index=card_index,
                opponent_name=names[1 - index],
            )
            await self._send_to_player(
                session, index,
                content=(
                    '**Deck Building [デッキ構築]**\n'
                    'Choose how to build your deck:\n'
                    '**Build a Deck** - Enter cards manually or get a random deck\n'
                    '**Select a Deck** - Use one of your saved decks'
                ),
                view=view,
            )

        await session.wait_for_both_players(timeout=600.0)

        deck_0 = session.pending_actions.get(0)
        deck_1 = session.pending_actions.get(1)
        return deck_0, deck_1

    async def run_game(self, session: GameSession) -> None:
        try:
            deck_1_cards, deck_2_cards = await self._do_deck_building_phase(session)
            await self.run_single_match(session, deck_1_cards, deck_2_cards)

            from zutomayo.engine.game_session import session_manager
            session_manager.remove_game(session.game_id)

        except Exception:
            log.exception('Error in game flow')
            await self._send_to_channel(session, content='An error occurred. Game ended.')
            from zutomayo.engine.game_session import session_manager
            session_manager.remove_game(session.game_id)

    async def run_single_match(
        self,
        session: GameSession,
        deck_1_cards: list[Card] | None,
        deck_2_cards: list[Card] | None,
    ) -> int | None:
        """Run a single match from initialization through game end.

        Returns the winner player index (0 or 1), or None for a draw.
        Does NOT remove the session from the session manager.
        """
        session.initialize_game(
            deck_1_cards=deck_1_cards,
            deck_2_cards=deck_2_cards,
        )
        game_state = session.game_state
        turn_manager = session.turn_manager
        names = self._player_names(session)

        # Bind effect engine to session/bot for interactive effects
        turn_manager.effect_engine.bind(session, self.bot)

        # --- SETUP PHASE ---
        game_state.current_phase = Phase.SETUP
        game_state.turn = 0

        # Shuffle decks
        import random
        for player in game_state.players:
            random.shuffle(player.deck)

        # Draw initial 5 cards
        for i, player in enumerate(game_state.players):
            player.draw(5)
            await turn_manager.effect_engine.notify_draw(game_state, i, 5)

        # Send hands to both players
        for index in range(2):
            player = game_state.players[index]
            embed = build_hand_embed(player)
            await self._send_to_player(session, index, embed=embed)
            hand_file = create_hand_image(player.hand)
            if hand_file:
                await self._send_to_player(session, index, files=[hand_file])

        # Redraw phase
        await self._do_redraw_phase(session)

        # Choose initial battle card
        await self._do_initial_battle_card(session)

        # Reveal initial cards
        for player in game_state.players:
            turn_manager.reveal_initial_card(player)

        # Send field state and board image to channel and both players
        field_embed = build_field_embed(game_state, names)
        start_content = f'**ゲームスタート: {names[0]} vs. {names[1]}**'

        await self._send_zone_messages(session, names, to_channel=True)
        board_file = render_board_image(game_state, Chronos.DAY)
        await self._send_to_channel(session, content=start_content, embed=field_embed, files=[board_file])
        for index in range(2):
            player = game_state.players[index]
            await self._send_zone_messages(session, names, player_index=index)
            board_file = render_board_image(game_state, player.side)
            await self._send_to_player(session, index, content=start_content, embed=field_embed, files=[board_file])

        log.info('Game %s started: %s vs %s', session.game_id, names[0], names[1])

        # Establish initial zone snapshot after game-start messages
        session._zone_snapshots = self._take_zone_snapshots(game_state)

        # --- TURN 1 ---
        game_state.turn = 1
        game_state.chronos_at_turn_start = game_state.chronos

        # Advance chronos for both players (from initial cards)
        game_state.current_phase = Phase.ADVANCE_CHRONOS
        for player in game_state.players:
            turn_manager.advance_chronos(player)

        # GATE: Chronos advanced
        chronos_delta = (game_state.chronos - game_state.chronos_at_turn_start) % CHRONOS_SIZE
        await self._phase_announce(session, names, f'1 \u2014 Advance Chronos [時間を進める] ({chronos_delta})')

        # Process effects (priority player first)
        game_state.current_phase = Phase.PROCESS_EFFECTS
        priority = game_state.priority_player
        other = 1 - priority

        priority_result = await turn_manager.effect_engine.process_effects(game_state, priority)
        await self._broadcast_effect_resolution(session, priority, names, priority_result)

        # Q&A rule: game ends immediately when HP reaches 0 — skip other player's effects
        turn_manager.check_win_condition()
        if game_state.result != Result.IN_PROGRESS:
            await self._end_game(session, names, remove_session=False)
            return self._winner_index(game_state)

        other_result = await turn_manager.effect_engine.process_effects(game_state, other)
        await self._broadcast_effect_resolution(session, other, names, other_result)

        # GATE: Effects resolved
        await self._phase_announce(session, names, '1 \u2014 Character/Enchant/Area Enchant Effects [キャラクター/エンチャント/エリアエンチャントの効果の処理]')

        # Check win condition after effects
        turn_manager.check_win_condition()
        if game_state.result != Result.IN_PROGRESS:
            await self._end_game(session, names, remove_session=False)
            return self._winner_index(game_state)

        game_state.current_phase = Phase.BATTLE
        battle_result = turn_manager.resolve_battle()
        battle_embed = build_battle_result_embed(battle_result, game_state, names)

        # GATE: Battle results
        await self._phase_announce(
            session, names,
            '1 \u2014 Battle Damage Calculation [バトル：ダメージの計算]',
            extra_embeds=[battle_embed],
        )

        # Check win condition
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
        turn_manager.effect_engine.check_area_enchant_removal(game_state, turn_manager, end_of_turn=True)
        turn_manager.check_deck_loss()
        if game_state.result != Result.IN_PROGRESS:
            await self._end_game(session, names, remove_session=False)
            return self._winner_index(game_state)

        turn_manager.effect_engine.save_battle_characters(game_state)
        turn_manager.reset_turn_flags()

        # GATE: Turn 1 complete
        await self._phase_announce(session, names, 'Turn 1 complete. Preparing next turn...', force_zones=True)

        # --- TURNS 2+ ---
        while game_state.result == Result.IN_PROGRESS:
            game_state.turn += 1
            game_state.chronos_at_turn_start = game_state.chronos
            await self._do_turn(session, names)

            if game_state.result != Result.IN_PROGRESS:
                await self._end_game(session, names, remove_session=False)
                return self._winner_index(game_state)

        return None

    @staticmethod
    def _winner_index(game_state) -> int | None:
        """Return the winner player index from a finished game state, or None for draw."""
        if game_state.result == Result.PLAYER_1_WIN:
            return 0
        elif game_state.result == Result.PLAYER_2_WIN:
            return 1
        return None

    async def _do_redraw_phase(self, session: GameSession) -> None:
        game_state = session.game_state
        session.clear_pending()

        names = self._player_names(session)
        for index in range(2):
            player = game_state.players[index]
            view = RedrawView(session, index, player.hand[:], opponent_name=names[1 - index])
            await self._send_to_player(
                session, index,
                content='Select the cards you want to redraw [最初の手札を引いたとき、一度だけ引き直しができます。]',
                view=view,
            )

        await session.wait_for_both_players()

        # Process redraws
        for index in range(2):
            player = game_state.players[index]
            action = session.pending_actions.get(index, {'redraw': []})
            cards_to_redraw = action.get('redraw', [])

            if cards_to_redraw:
                count = len(cards_to_redraw)
                # Remove selected cards from hand
                for card_instance in cards_to_redraw:
                    if card_instance in player.hand:
                        player.hand.remove(card_instance)

                # Draw new cards
                drawn = player.draw(count)
                await session.turn_manager.effect_engine.notify_draw(game_state, index, len(drawn))

                # Return old cards to deck and shuffle
                for card_instance in cards_to_redraw:
                    card_instance.zone = Zone.DECK
                    player.deck.append(card_instance)
                import random
                random.shuffle(player.deck)

                # Send updated hand
                embed = build_hand_embed(player)
                await self._send_to_player(session, index, content='New hand', embed=embed)
                hand_file = create_hand_image(player.hand)
                if hand_file:
                    await self._send_to_player(session, index, files=[hand_file])
            else:
                await self._send_to_player(session, index, content='No change')

    async def _do_initial_battle_card(self, session: GameSession) -> None:
        game_state = session.game_state
        turn_manager = session.turn_manager
        session.clear_pending()
        names = self._player_names(session)

        for index in range(2):
            player = game_state.players[index]
            view = CardSelectView(
                session, index, player.hand[:],
                min_cards=1, max_cards=1,
                placeholder='Choose a card for the Battle Zone...',
                opponent_name=names[1 - index],
            )
            await self._send_to_player(
                session, index,
                content='Choose one card to place face-down in the Battle Zone [手札からカードを１枚選びバトルゾーンに裏向きにして置きます。]',
                view=view,
            )

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
                await self._send_to_player(session, index, content='You have no cards to set this turn.')
                continue

            embed = build_hand_embed(player)

            if max_cards > 1:
                view = TwoStepCardSelectView(
                    session, index, player.hand[:],
                    embed=embed,
                    opponent_name=names[1 - index],
                )
                status = f'Last Turn Result: LOSE | Set up to {max_cards} cards'
            else:
                view = CardSelectView(
                    session, index, player.hand[:],
                    min_cards=1, max_cards=1,
                    placeholder='Select a card to set...',
                    embed=embed,
                    opponent_name=names[1 - index],
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
        await self._phase_announce(session, names, f'{game_state.turn} \u2014 Set cards [カードのセット]')

        # Phase 2: Reveal
        game_state.current_phase = Phase.REVEAL
        for player in game_state.players:
            if player.set_zone_a:
                player.set_zone_a.face_up = True
            if player.set_zone_b:
                player.set_zone_b.face_up = True

        # Cards revealed (auto-advance, no ready required)
        await self._phase_announce(session, names, f'{game_state.turn} \u2014 Reveal set cards [セットしたカードを公開する]')

        # Phase 3: Advance chronos
        game_state.current_phase = Phase.ADVANCE_CHRONOS
        for player in game_state.players:
            turn_manager.advance_chronos(player)

        # GATE: Chronos advanced
        chronos_delta = (game_state.chronos - game_state.chronos_at_turn_start) % CHRONOS_SIZE
        await self._phase_announce(session, names, f'{game_state.turn} \u2014 Advance Chronos [時間を進める] ({chronos_delta})')

        # Phase 4: Character swap
        game_state.current_phase = Phase.CHARACTER_SWAP
        for player in game_state.players:
            turn_manager.do_character_swap(player)

        # Immediate area enchant removal check after character swap
        # (e.g. 晩餐会 removed when opponent character costs 4+)
        turn_manager.effect_engine.check_area_enchant_removal(game_state, turn_manager)

        # GATE: Characters swapped
        await self._phase_announce(session, names, f'{game_state.turn} \u2014 Character Swap [キャラクターの入れ替え]')

        # Phase 5: Area enchant swap
        game_state.current_phase = Phase.AREA_ENCHANT_SWAP
        for player in game_state.players:
            turn_manager.do_area_enchant_swap(player)

        # Immediate area enchant removal check after area enchant swap
        # (e.g. 02-007 removed when opponent plays area enchant)
        turn_manager.effect_engine.check_area_enchant_removal(game_state, turn_manager)

        # GATE: Area enchants updated
        await self._phase_announce(session, names, f'{game_state.turn} \u2014 Area Enchant Swap [エリアエンチャントの入れ替え]')

        # Phase 6: Process effects (priority player first)
        game_state.current_phase = Phase.PROCESS_EFFECTS
        priority = game_state.priority_player
        other = 1 - priority

        priority_result = await turn_manager.effect_engine.process_effects(game_state, priority)
        await self._broadcast_effect_resolution(session, priority, names, priority_result)

        # Q&A rule: game ends immediately when HP reaches 0 — skip other player's effects
        turn_manager.check_win_condition()
        if game_state.result != Result.IN_PROGRESS:
            return

        other_result = await turn_manager.effect_engine.process_effects(game_state, other)
        await self._broadcast_effect_resolution(session, other, names, other_result)

        # GATE: Effects resolved
        await self._phase_announce(session, names, f'{game_state.turn} \u2014 Character/Enchant/Area Enchant Effects [キャラクター/エンチャント/エリアエンチャントの効果の処理]')

        # Check win condition after effects (e.g. 襟頭の方 reducing HP to 0)
        turn_manager.check_win_condition()
        if game_state.result != Result.IN_PROGRESS:
            return

        # Phase 7-8: Battle
        game_state.current_phase = Phase.BATTLE
        battle_result = turn_manager.resolve_battle()
        battle_embed = build_battle_result_embed(battle_result, game_state, names)

        # GATE: Battle results (includes battle embed alongside field embed)
        await self._phase_announce(
            session, names,
            f'{game_state.turn} \u2014 Battle Damage Calculation [バトル：ダメージの計算]',
            extra_embeds=[battle_embed],
        )

        # Check win condition after battle (game ends immediately at HP 0)
        turn_manager.check_win_condition()
        if game_state.result != Result.IN_PROGRESS:
            return

        # Turn-end effects (e.g. 03-027 delayed damage)
        game_state.current_phase = Phase.TURN_END_EFFECTS
        turn_manager.effect_engine.process_end_of_turn_effects(game_state)

        # Check win condition
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
        turn_manager.effect_engine.check_area_enchant_removal(game_state, turn_manager, end_of_turn=True)

        turn_manager.check_deck_loss()
        if game_state.result != Result.IN_PROGRESS:
            return

        turn_manager.effect_engine.save_battle_characters(game_state)
        turn_manager.reset_turn_flags()

        log.info('Game %s turn %d complete', session.game_id, game_state.turn)

        # GATE: Turn complete
        await self._phase_announce(session, names, f'Turn {game_state.turn} complete. Preparing next turn...', force_zones=True)

    async def _end_game(self, session: GameSession, names: dict[int, str], *, remove_session: bool = True) -> None:
        game_state = session.game_state
        log.info('Game %s ended: %s', session.game_id, game_state.result.name)
        embed = build_game_over_embed(game_state, names)
        await self._send_to_channel(session, embed=embed)
        await self._send_to_both(session, embed=embed)

        if remove_session:
            from zutomayo.engine.game_session import session_manager
            session_manager.remove_game(session.game_id)
