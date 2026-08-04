import json
import logging
import discord
from discord import app_commands
from discord.ext import commands
from zutomayo.data.name_storage import (
    MAXIMUM_CUSTOM_NAME_LENGTH,
    clear_custom_name,
    ensure_display_names,
    remember_user,
    set_custom_name,
)
from zutomayo.data.player_storage import (
    load_profile,
    list_ranked_profiles,
)
from zutomayo.engine.game_session import session_manager
from zutomayo.ui.player_embeds import (
    build_leaderboard_embed,
    build_profile_embed,
)
from zutomayo.ui.leaderboard_view import LeaderboardView
from zutomayo.ui.views import GameLobbyView
from zutomayo.ui.rank_songs_view import (
    CheckpointChoiceView,
    DEFAULT_NUMBER_OF_ROUNDS,
    RankSongsView,
    get_checkpoint_path,
)
from zutomayo.utils.discord_utils import send_images_with_retry


log = logging.getLogger(__name__)


LEADERBOARD_MINIMUM_GAMES = 1

# Players choose a solo opponent by letter; these are the identifiers
# zutomayo.match.agents.SOLO_OPPONENT_MODULES keys the model stacks by.
SOLO_MODEL_ALPHA_ZERO = 'alphazero'
SOLO_MODEL_PPO = 'ppo'
SOLO_MODEL_LABELS = {SOLO_MODEL_ALPHA_ZERO: 'A', SOLO_MODEL_PPO: 'B'}


class GameCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    group = app_commands.Group(name='zutomayo', description='ZUTOMAYO CARD game commands')

    @commands.Cog.listener('on_interaction')
    async def capture_interaction_user_name(self, interaction: discord.Interaction) -> None:
        """
        Record the acting user's name on every interaction. Interaction payloads
        carry the user regardless of gateway intents, so this replaces the member
        cache that the (removed) privileged members intent used to fill.
        """
        if interaction.user is None or interaction.user.bot:
            return
        remember_user(interaction.user.id, interaction.user.global_name or interaction.user.name)

    @group.command(name='create', description='Create a ZUTOMAYO CARD game against another player')
    @app_commands.guild_only()
    @app_commands.describe(
        game_format='Standard single game or TCG best-of-N series (default: standard)',
        best_of='TCG only: best of 3 or 5 (default: 3)',
    )
    @app_commands.rename(game_format='format')
    @app_commands.choices(
        game_format=[
            app_commands.Choice(name='Standard', value='standard'),
            app_commands.Choice(name='TCG', value='tcg'),
        ],
        best_of=[
            app_commands.Choice(name='3', value=3),
            app_commands.Choice(name='5', value=5),
        ],
    )
    async def create_game(
        self,
        interaction: discord.Interaction,
        game_format: str = 'standard',
        best_of: int | None = None,
    ):
        if game_format != 'tcg' and best_of is not None:
            await interaction.response.send_message(
                'best_of only applies to TCG games (format: TCG).', ephemeral=True,
            )
            return

        await self._create_two_player_game(
            interaction, is_tcg=game_format == 'tcg', best_of=best_of,
        )

    @group.command(
        name='createdraft',
        description='Create a draft game: both players build a deck from gacha boxes they open',
    )
    @app_commands.guild_only()
    @app_commands.describe(
        boxes='Gacha boxes each player opens (1-5; default 2, TCG 3)',
        visibility='Whether opened boxes are shown in the channel (default: public)',
        game_format='Standard single game or TCG best-of-N series (default: standard)',
        best_of='TCG only: best of 3 or 5 (default: 3)',
    )
    @app_commands.rename(game_format='format')
    @app_commands.choices(
        visibility=[
            app_commands.Choice(name='Public', value='public'),
            app_commands.Choice(name='Private', value='private'),
        ],
        game_format=[
            app_commands.Choice(name='Standard', value='standard'),
            app_commands.Choice(name='TCG', value='tcg'),
        ],
        best_of=[
            app_commands.Choice(name='3', value=3),
            app_commands.Choice(name='5', value=5),
        ],
    )
    async def create_draft_game(
        self,
        interaction: discord.Interaction,
        boxes: app_commands.Range[int, 1, 5] | None = None,
        visibility: str | None = None,
        game_format: str = 'standard',
        best_of: int | None = None,
    ):
        if game_format != 'tcg' and best_of is not None:
            await interaction.response.send_message(
                'best_of only applies to TCG games (format: TCG).', ephemeral=True,
            )
            return

        is_tcg = game_format == 'tcg'
        await self._create_two_player_game(
            interaction,
            is_tcg=is_tcg,
            best_of=best_of,
            draft_boxes=boxes if boxes is not None else (3 if is_tcg else 2),
            draft_visibility=visibility or 'public',
        )

    async def _create_two_player_game(
        self,
        interaction: discord.Interaction,
        is_tcg: bool,
        best_of: int | None = None,
        draft_boxes: int | None = None,
        draft_visibility: str | None = None,
    ) -> None:
        """Open a lobby for a two-player game. A `draft_boxes` count makes it a
        draft; leaving it None keeps the saved/built deck path."""
        try:
            session = await session_manager.create_game(interaction.channel_id, interaction.user.id)
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return

        title_parts = ['**ZUTOMAYO CARD']
        if is_tcg:
            session.is_tcg = True
            session.best_of = best_of or 3
            title_parts.append(' TCG')
        if draft_boxes is not None:
            session.is_draft = True
            session.draft_boxes = draft_boxes
            session.draft_visibility = draft_visibility or 'public'
            title_parts.append(' DRAFT')
        title_parts.append('**')
        details = []
        if session.is_tcg:
            details.append(f'Best of {session.best_of}')
        if session.is_draft:
            details.append(f'{session.draft_boxes}-box ({session.draft_visibility}) draft')
        detail_text = f' - {", ".join(details)}' if details else ''

        view = GameLobbyView(session.game_id)
        await interaction.response.send_message(
            f'{"".join(title_parts)}{detail_text} created by **{interaction.user.display_name}**!\n'
            f'Game ID: `{session.game_id}`\n'
            f'Click the button below or use `/zutomayo join {session.game_id}` to join.',
            view=view,
        )

    @group.command(
        name='playuniguri',
        description='Play a solo game against a trained model opponent (DMs only)',
    )
    @app_commands.describe(model='A (AlphaZero) or B (PPO transformer)')
    @app_commands.choices(model=[
        app_commands.Choice(name='A', value=SOLO_MODEL_ALPHA_ZERO),
        app_commands.Choice(name='B', value=SOLO_MODEL_PPO),
    ])
    async def play_uniguri(self, interaction: discord.Interaction, model: str) -> None:
        from zutomayo.match.agents import available_solo_opponents

        if interaction.guild is not None:
            await interaction.response.send_message(
                'Solo games run in DMs - use this command in a DM with the bot.', ephemeral=True,
            )
            return

        # Checked ahead of availability so model A gives its own message rather
        # than the generic one, and starts working with no code change the
        # moment a checkpoint is dropped into model/.
        if model == SOLO_MODEL_ALPHA_ZERO:
            from alpha_zero.inference import find_checkpoint

            if find_checkpoint() is None:
                await interaction.response.send_message(
                    'Model A has not yet been trained. Play against model B', ephemeral=True,
                )
                return

        if model not in available_solo_opponents():
            await interaction.response.send_message(
                f'Model {SOLO_MODEL_LABELS.get(model, model)} is not available yet.',
                ephemeral=True,
            )
            return

        await self._start_solo_game(interaction, model)

    async def _start_solo_game(self, interaction: discord.Interaction, opponent: str) -> None:
        try:
            session = await session_manager.create_solo_game(0, interaction.user.id)
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return

        session.solo_difficulty = opponent

        await interaction.response.send_message(
            f'**ãƒ¡ã‚«ã†ã«ãã‚Š** has accepted **{interaction.user.display_name}**\'s challenge!\n'
            f'Game ID: `{session.game_id}`\n'
            f'Starting solo game...'
        )

        from zutomayo.match.solo_flow import run_solo_game

        session.game_task = self.bot.loop.create_task(run_solo_game(self.bot, session, opponent))

    # Disabled feature; re-enable by uncommenting (the command tree has free
    # slots). The supporting code (RankSongsView, CheckpointChoiceView,
    # get_checkpoint_path) is intact.
    # @group.command(name='ranksongs', description='Rank your favourite ZUTOMAYO songs')
    # @app_commands.dm_only()
    # async def rank_songs(self, interaction: discord.Interaction):
    #     checkpoint_path = get_checkpoint_path(interaction.user.id)
    #     if checkpoint_path.exists():
    #         progress_info = ''
    #         try:
    #             data = json.loads(checkpoint_path.read_text(encoding='utf-8'))
    #             votes_done = len(data['recorded_votes'])
    #             total = data['total_matchups']
    #             percentage = round(votes_done / total * 100)
    #             progress_info = f' You are {percentage}% complete ({votes_done}/{total} matchups done).'
    #         except (KeyError, json.JSONDecodeError):
    #             pass
    #         view = CheckpointChoiceView(user_id=interaction.user.id, number_of_rounds=DEFAULT_NUMBER_OF_ROUNDS)
    #         await interaction.response.send_message(
    #             f'A saved checkpoint was found.{progress_info} Choose to resume or start from scratch.',
    #             view=view,
    #         )
    #     else:
    #         log.info('User %s started a new song ranking', interaction.user.id)
    #         view = RankSongsView(number_of_rounds=DEFAULT_NUMBER_OF_ROUNDS, user_id=interaction.user.id)
    #         await interaction.response.send_message(content=view._build_matchup_content(), view=view)

    @group.command(name='join', description='Join an existing ZUTOMAYO CARD game')
    @app_commands.guild_only()
    @app_commands.describe(game_id='The game ID to join')
    async def join_game(self, interaction: discord.Interaction, game_id: str):
        try:
            session = session_manager.join_game(game_id, interaction.user.id)
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return

        await interaction.response.send_message(
            f'**{interaction.user.display_name}** joined the game! Starting...'
        )

        if session.is_tcg:
            from zutomayo.match.series_flow import TcgSeriesFlow
            flow = TcgSeriesFlow(self.bot, session.best_of)
            session.game_task = self.bot.loop.create_task(
                flow.run_tcg(session)
            )
        else:
            from zutomayo.match.match_flow import SingleMatchFlow
            game_flow = SingleMatchFlow(self.bot)
            session.game_task = self.bot.loop.create_task(
                game_flow.run_game(session)
            )

    @group.command(name='end', description='End a live game, or abandon one of your saved games')
    @app_commands.guild_only()
    @app_commands.describe(game_id='The game ID to end or abandon')
    async def end_game(self, interaction: discord.Interaction, game_id: str):
        session = session_manager.active_games.get(game_id)
        if session is None:
            await self._abandon_saved_game(interaction, game_id)
            return

        if interaction.user.id not in session.player_discord_ids:
            await interaction.response.send_message('You are not a player in that game.', ephemeral=True)
            return

        if session.game_task and not session.game_task.done():
            session.game_task.cancel()

        await self._record_forfeit_for_session(session, interaction.user.id)
        await self._mark_session_quit(session)
        session_manager.remove_game(game_id)
        log.info('Game %s ended by %s (end command)', game_id, interaction.user)
        await interaction.response.send_message(
            f'**{interaction.user.display_name}** ended game `{game_id}`.'
        )

    @end_game.autocomplete('game_id')
    async def end_game_autocomplete(
        self, interaction: discord.Interaction, current: str,
    ) -> list[app_commands.Choice[str]]:
        try:
            from zutomayo.engine.game_persistence import list_saved_games_for_player

            choices: list[app_commands.Choice[str]] = []
            live_session = session_manager.get_session_by_player(interaction.user.id)
            if live_session is not None and live_session.game_id.startswith(current):
                choices.append(app_commands.Choice(
                    name=f'{live_session.game_id} (live game)'[:100],
                    value=live_session.game_id,
                ))
            for row in await list_saved_games_for_player(interaction.user.id, current):
                saved_date = row['saved_at'].date().isoformat() if row['saved_at'] else 'unknown date'
                choices.append(app_commands.Choice(
                    name=f'{row["game_id"]} ({row["mode"]}, saved {saved_date})'[:100],
                    value=row['game_id'],
                ))
            return choices[:25]
        except Exception:
            log.exception('end autocomplete failed')
            return []

    async def _abandon_saved_game(self, interaction: discord.Interaction, game_id: str) -> None:
        """End a saved game for good: status abandoned, forfeit for the abandoner."""
        from zutomayo.data.player_storage import BOT_DISCORD_ID, record_forfeit
        from zutomayo.engine.game_events import EVENT_FORFEIT
        from zutomayo.engine.game_persistence import (
            STATUS_ABANDONED,
            STATUS_SAVED,
            GameRecordStore,
            get_game_row,
        )

        row = await get_game_row(game_id)
        if row is None or row['status'] != STATUS_SAVED:
            await interaction.response.send_message(f'Game `{game_id}` not found.', ephemeral=True)
            return

        manifest = row['manifest']
        player_ids = [pair[0] for pair in manifest.get('player_discord_ids', [])]
        if interaction.user.id not in player_ids:
            await interaction.response.send_message('You are not a player in that game.', ephemeral=True)
            return

        opponent_id = next(
            (player_id for player_id in player_ids
             if player_id != interaction.user.id and player_id != BOT_DISCORD_ID),
            None,
        )
        try:
            await record_forfeit(interaction.user.id, opponent_id)
        except Exception:
            log.exception('Failed to record forfeit for abandoned game %s', game_id)

        store = GameRecordStore.attach_for_resume(game_id)
        player_index = next(
            (index for player_id, index in manifest.get('player_discord_ids', [])
             if player_id == interaction.user.id),
            None,
        )
        store.next_event_index = await self._next_event_index_safe(game_id)
        store.emit_event(EVENT_FORFEIT, {
            'player_index': player_index,
            'discord_id': interaction.user.id,
        })
        await store.set_status(STATUS_ABANDONED)
        log.info('Saved game %s abandoned by %s', game_id, interaction.user)
        await interaction.response.send_message(
            f'**{interaction.user.display_name}** abandoned saved game `{game_id}`. '
            'It can no longer be resumed.'
        )

    @staticmethod
    async def _next_event_index_safe(game_id: str) -> int:
        from zutomayo.engine.game_persistence import next_event_index

        try:
            return await next_event_index(game_id)
        except Exception:
            log.exception('Failed to read next event index for game %s', game_id)
            return 0

    async def _start_make_deck(
        self,
        interaction: discord.Interaction,
        name: str,
        load_deck_names,
        duplicate_message: str,
        modal_class,
    ) -> None:
        """Shared body of the deck make command (standard and TCG formats)."""
        from zutomayo.data.deck_validator import get_card_index

        if len(name) > 50:
            await interaction.response.send_message(
                'Deck name must be 50 characters or fewer.', ephemeral=True,
            )
            return

        existing_names = await load_deck_names(interaction.user.id)
        if name in existing_names:
            await interaction.response.send_message(duplicate_message, ephemeral=True)
            return

        _, card_index = get_card_index()
        modal = modal_class(deck_name=name, user_id=interaction.user.id, card_index=card_index)
        await interaction.response.send_modal(modal)

    deck_group = app_commands.Group(
        name='deck', description='Create, view, and manage your decks', parent=group,
    )

    @deck_group.command(name='make', description='Create and save a new deck')
    @app_commands.describe(
        name='A unique name for this deck (max 50 characters)',
        deck_format='Standard (20 cards) or TCG (20 main + 8 side); default: standard',
    )
    @app_commands.rename(deck_format='format')
    @app_commands.choices(deck_format=[
        app_commands.Choice(name='Standard', value='standard'),
        app_commands.Choice(name='TCG', value='tcg'),
    ])
    async def deck_make(self, interaction: discord.Interaction, name: str, deck_format: str = 'standard'):
        if deck_format == 'tcg':
            from zutomayo.data.deck_storage_tcg import get_tcg_deck_names
            from zutomayo.ui.deck_management_views_tcg import MakeDeckTcgModal

            await self._start_make_deck(
                interaction, name, get_tcg_deck_names,
                f'A TCG deck named **{name}** already exists. Please choose a different name.',
                MakeDeckTcgModal,
            )
            return
        from zutomayo.data.deck_storage import get_deck_names
        from zutomayo.ui.deck_management_views import MakeDeckModal

        await self._start_make_deck(
            interaction, name, get_deck_names,
            f'A deck named **{name}** already exists. Please choose a different name.',
            MakeDeckModal,
        )

    async def _load_deck_or_report(self, interaction: discord.Interaction, deck: str):
        """Resolve a standard deck by name; sends the not-found reply itself."""
        from zutomayo.data.deck_storage import get_deck_by_name, resolve_deck_cards
        from zutomayo.data.deck_validator import get_card_index

        deck_data = await get_deck_by_name(interaction.user.id, deck)
        if deck_data is None:
            await interaction.response.send_message(
                f'You have no deck named **{deck}**. Start typing to search your '
                'saved decks, or use `/zutomayo deck make` to create one.',
                ephemeral=True,
            )
            return None, None
        _, card_index = get_card_index()
        return resolve_deck_cards(deck_data, card_index), card_index

    async def _deck_name_autocomplete(self, interaction: discord.Interaction, current: str, tcg: bool):
        try:
            if tcg:
                from zutomayo.data.deck_storage_tcg import search_tcg_deck_names as search
            else:
                from zutomayo.data.deck_storage import search_deck_names as search
            names = await search(interaction.user.id, current)
            return [app_commands.Choice(name=name[:100], value=name[:100]) for name in names[:25]]
        except Exception:
            log.exception('deck name autocomplete failed')
            return []

    @deck_group.command(name='view', description='View one of your saved decks')
    @app_commands.describe(
        deck='The deck to view (search by name)',
        deck_format='Standard or TCG deck list; default: standard',
    )
    @app_commands.rename(deck_format='format')
    @app_commands.choices(deck_format=[
        app_commands.Choice(name='Standard', value='standard'),
        app_commands.Choice(name='TCG', value='tcg'),
    ])
    async def deck_view(self, interaction: discord.Interaction, deck: str, deck_format: str = 'standard'):
        if deck_format == 'tcg':
            main_cards, side_cards, _ = await self._load_tcg_deck_or_report(interaction, deck)
            if main_cards is None:
                return
            await interaction.response.send_message(
                embeds=self._build_tcg_deck_embeds(deck, main_cards, side_cards),
                ephemeral=True,
            )
            await self._send_tcg_deck_grids(interaction, main_cards, side_cards)
            return

        from zutomayo.ui.deck_management_views import format_card_ids_line
        from zutomayo.ui.embeds import build_deck_list_embed, create_deck_grid_image_off_thread

        cards, _ = await self._load_deck_or_report(interaction, deck)
        if cards is None:
            return

        embed = build_deck_list_embed(deck, cards)
        embed.description += f'\n\n{format_card_ids_line(cards)}'
        await interaction.response.send_message(embed=embed, ephemeral=True)
        grid = await create_deck_grid_image_off_thread(cards)
        if grid:
            await send_images_with_retry(
                interaction.followup.send,
                label='image followup',
                file=grid,
                ephemeral=True,
            )

    @deck_view.autocomplete('deck')
    async def deck_view_autocomplete(self, interaction: discord.Interaction, current: str):
        selected_format = getattr(interaction.namespace, 'format', None) or 'standard'
        return await self._deck_name_autocomplete(interaction, current, tcg=(selected_format == 'tcg'))

    @deck_group.command(name='manage', description='Edit or delete one of your saved decks')
    @app_commands.describe(
        deck='The deck to manage (search by name)',
        deck_format='Standard or TCG deck list; default: standard',
    )
    @app_commands.rename(deck_format='format')
    @app_commands.choices(deck_format=[
        app_commands.Choice(name='Standard', value='standard'),
        app_commands.Choice(name='TCG', value='tcg'),
    ])
    async def deck_manage(self, interaction: discord.Interaction, deck: str, deck_format: str = 'standard'):
        if deck_format == 'tcg':
            from zutomayo.ui.deck_management_views_tcg import ManageDeckTcgActionsView

            main_cards, side_cards, card_index = await self._load_tcg_deck_or_report(interaction, deck)
            if main_cards is None:
                return
            view = ManageDeckTcgActionsView(
                user_id=interaction.user.id,
                deck_name=deck,
                card_index=card_index,
            )
            await interaction.response.send_message(
                'Choose an action:',
                embeds=self._build_tcg_deck_embeds(deck, main_cards, side_cards),
                view=view,
                ephemeral=True,
            )
            await self._send_tcg_deck_grids(interaction, main_cards, side_cards)
            return

        from zutomayo.ui.deck_management_views import ManageDeckActionsView, format_card_ids_line
        from zutomayo.ui.embeds import build_deck_list_embed, create_deck_grid_image_off_thread

        cards, card_index = await self._load_deck_or_report(interaction, deck)
        if cards is None:
            return

        embed = build_deck_list_embed(deck, cards)
        embed.description += f'\n\n{format_card_ids_line(cards)}'
        view = ManageDeckActionsView(
            user_id=interaction.user.id,
            deck_name=deck,
            card_index=card_index,
        )
        await interaction.response.send_message(
            'Choose an action:', embed=embed, view=view, ephemeral=True,
        )
        grid = await create_deck_grid_image_off_thread(cards)
        if grid:
            await send_images_with_retry(
                interaction.followup.send,
                label='image followup',
                file=grid,
                ephemeral=True,
            )

    @deck_manage.autocomplete('deck')
    async def deck_manage_autocomplete(self, interaction: discord.Interaction, current: str):
        selected_format = getattr(interaction.namespace, 'format', None) or 'standard'
        return await self._deck_name_autocomplete(interaction, current, tcg=(selected_format == 'tcg'))

    async def _load_tcg_deck_or_report(self, interaction: discord.Interaction, deck: str):
        """Resolve a TCG deck by name; sends the not-found reply itself."""
        from zutomayo.data.deck_storage_tcg import get_tcg_deck_by_name, resolve_tcg_deck_cards
        from zutomayo.data.deck_validator import get_card_index

        deck_data = await get_tcg_deck_by_name(interaction.user.id, deck)
        if deck_data is None:
            await interaction.response.send_message(
                f'You have no TCG deck named **{deck}**. Start typing to search your '
                'saved TCG decks, or use `/zutomayo deck make format:TCG` to create one.',
                ephemeral=True,
            )
            return None, None, None
        _, card_index = get_card_index()
        main_cards, side_cards = resolve_tcg_deck_cards(deck_data, card_index)
        return main_cards, side_cards, card_index

    def _build_tcg_deck_embeds(self, deck: str, main_cards, side_cards) -> list[discord.Embed]:
        from zutomayo.ui.deck_management_views import format_card_ids_line
        from zutomayo.ui.embeds import build_deck_list_embed

        main_embed = build_deck_list_embed(f'{deck} - Main Deck', main_cards)
        main_embed.description += f'\n\n{format_card_ids_line(main_cards)}'
        side_embed = build_deck_list_embed(f'{deck} - Side Deck', side_cards)
        side_embed.description += f'\n\n{format_card_ids_line(side_cards)}'
        return [main_embed, side_embed]

    async def _send_tcg_deck_grids(self, interaction: discord.Interaction, main_cards, side_cards) -> None:
        from zutomayo.ui.embeds import create_deck_grid_image_off_thread

        grid = await create_deck_grid_image_off_thread(main_cards)
        if grid:
            await send_images_with_retry(
                interaction.followup.send,
                label='image followup',
                content='**Main Deck:**',
                file=grid,
                ephemeral=True,
            )
        side_grid = await create_deck_grid_image_off_thread(side_cards, columns=4)
        if side_grid:
            await send_images_with_retry(
                interaction.followup.send,
                label='image followup',
                content='**Side Deck:**',
                file=side_grid,
                ephemeral=True,
            )

    @group.command(name='gacha', description='Open a card pack (5 cards)')
    @app_commands.describe(pack='Pack number (1-4)')
    async def gacha(self, interaction: discord.Interaction, pack: app_commands.Range[int, 1, 4]):
        from zutomayo.data.card_loader import load_cards
        from zutomayo.data.gacha import draw_gacha
        from zutomayo.ui.embeds import create_deck_grid_image_off_thread

        all_cards = load_cards()
        drawn = draw_gacha(pack, all_cards)
        image = await create_deck_grid_image_off_thread(drawn, columns=5, filename='gacha.jpg')
        if image:
            await send_images_with_retry(
                interaction.response.send_message,
                label='image response',
                file=image,
            )
        else:
            await interaction.response.send_message(
                'Something went wrong generating the gacha image.',
                ephemeral=True,
            )

    @group.command(name='gachabox', description='Open a card box (10 packs)')
    @app_commands.describe(pack='Pack number (1-4)')
    async def gachabox(self, interaction: discord.Interaction, pack: app_commands.Range[int, 1, 4]):
        from zutomayo.data.card_loader import load_cards
        from zutomayo.data.gacha import draw_gachabox
        from zutomayo.ui.embeds import create_deck_grid_image_off_thread

        await interaction.response.defer()
        drawn = draw_gachabox(pack, load_cards())
        half = len(drawn) // 2
        image1 = await create_deck_grid_image_off_thread(drawn[:half], columns=5, filename='gachabox_1.jpg')
        image2 = await create_deck_grid_image_off_thread(drawn[half:], columns=5, filename='gachabox_2.jpg')
        files = [f for f in (image1, image2) if f]
        if files:
            await send_images_with_retry(
                interaction.followup.send,
                label='image followup',
                files=files,
            )
        else:
            await interaction.followup.send(
                'Something went wrong generating the gacha box image.',
            )

    @group.command(name='quit', description='Quit your current game, optionally saving it to resume later')
    @app_commands.describe(save='Save the game so it can be resumed later (default: no, forfeit)')
    async def quit_game(self, interaction: discord.Interaction, save: bool = False):
        session = session_manager.get_session_by_player(interaction.user.id)
        if session is None:
            await interaction.response.send_message('You are not in a game.', ephemeral=True)
            return

        if save:
            await self._save_and_quit(interaction, session)
            return

        if session.game_task and not session.game_task.done():
            session.game_task.cancel()

        await self._record_forfeit_for_session(session, interaction.user.id)
        await self._mark_session_quit(session)
        session_manager.remove_game(session.game_id)
        log.info('Game %s ended by %s (quit command)', session.game_id, interaction.user)
        await interaction.response.send_message(
            f'**{interaction.user.display_name}** quit the game. Game `{session.game_id}` has been removed.'
        )

    async def _save_and_quit(self, interaction: discord.Interaction, session) -> None:
        from zutomayo.engine.game_events import EVENT_GAME_SAVED
        from zutomayo.engine.game_persistence import STATUS_SAVED

        if session.persistence is None:
            await interaction.response.send_message(
                'This game has not started yet, so there is nothing to save. '
                'Use `/zutomayo quit` without the save option instead.',
                ephemeral=True,
            )
            return
        if session.broker is not None and session.broker.replaying:
            await interaction.response.send_message(
                'This game is still being restored. Try again in a moment.', ephemeral=True,
            )
            return

        if session.game_task and not session.game_task.done():
            session.game_task.cancel()

        session.persistence.emit_event(EVENT_GAME_SAVED, {
            'by_discord_id': interaction.user.id,
            'channel_id': session.channel_id,
        })
        await session.persistence.set_status(STATUS_SAVED)
        session_manager.detach_game(session.game_id)
        log.info('Game %s saved by %s', session.game_id, interaction.user)
        await interaction.response.send_message(
            f'Game `{session.game_id}` has been saved. Resume it any time with '
            f'`/zutomayo resume {session.game_id}`.\n'
            'Note: saved games are restored by replaying the game log, so they '
            'may not survive bot updates.'
        )

    @group.command(name='resume', description='Resume one of your saved games')
    @app_commands.describe(game_id='The saved game to resume')
    async def resume_saved_game(self, interaction: discord.Interaction, game_id: str) -> None:
        """Resume from a DM or a server channel. Every game already plays out in
        DMs; the channel only carries public narration, so where the command is
        used decides how a two-player confirmation is delivered, not whether the
        game can run."""
        from zutomayo.match.resume import load_saved_game_for_resume

        try:
            row = await load_saved_game_for_resume(game_id, interaction.user.id)
        except ValueError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return

        if row['is_solo']:
            await self._resume_solo_game(interaction, game_id, row)
        else:
            await self._request_two_player_resume(interaction, game_id, row)

    async def _resume_solo_game(
        self, interaction: discord.Interaction, game_id: str, row: dict,
    ) -> None:
        """Solo games are played entirely over DM, so they resume from anywhere
        and keep their recorded channel (0 - nothing is posted publicly)."""
        from zutomayo.engine.game_persistence import (
            STATUS_ACTIVE,
            STATUS_SAVED,
            GameRecordStore,
        )
        from zutomayo.match.resume import resume_game

        store = GameRecordStore.attach_for_resume(game_id)
        await store.set_status(STATUS_ACTIVE)
        await interaction.response.send_message(f'Resuming game `{game_id}`...')
        try:
            await resume_game(
                self.bot, game_id,
                channel_id_override=row['channel_id'],
                announcement='**Game resumed.**',
            )
        except ValueError as error:
            # Usually the solo opponent's checkpoint is no longer deployed.
            # Leave the game saved rather than starting a match whose bot seat
            # nobody can answer for.
            log.warning('Solo resume of game %s failed: %s', game_id, error)
            await store.set_status(STATUS_SAVED)
            await interaction.followup.send(
                f'Game `{game_id}` could not be resumed: {error} It remains saved.',
                ephemeral=True,
            )

    async def _request_two_player_resume(
        self, interaction: discord.Interaction, game_id: str, row: dict,
    ) -> None:
        """Ask the opponent to confirm. In a server channel both players can see
        the request, so it goes there; from a DM it is delivered to the
        opponent's DM instead."""
        from zutomayo.data.name_storage import resolve_display_name
        from zutomayo.engine.game_persistence import STATUS_ACTIVE, GameRecordStore
        from zutomayo.match.resume import load_saved_game_for_resume, resume_game
        from zutomayo.match.transport import open_dm_channel
        from zutomayo.ui.resume_views import ResumeConfirmationView

        manifest = row['manifest']
        opponent_id = next(
            player_id for player_id, _ in manifest['player_discord_ids']
            if player_id != interaction.user.id
        )
        in_guild = interaction.guild is not None
        invoking_channel_id = interaction.channel_id
        invoker_id = interaction.user.id
        bot = self.bot

        async def start_resume(button_interaction: discord.Interaction) -> None:
            try:
                current_row = await load_saved_game_for_resume(game_id, invoker_id)
            except ValueError as error:
                await button_interaction.response.edit_message(content=str(error), view=None)
                return
            # Resuming from a server channel moves the game there; resuming from
            # a DM leaves it wherever it already was. The row is re-read because
            # minutes can pass between the request and the answer.
            channel_id = invoking_channel_id if in_guild else current_row['channel_id']
            await GameRecordStore.attach_for_resume(game_id).set_status(
                STATUS_ACTIVE, channel_id=channel_id,
            )
            await button_interaction.response.edit_message(
                content=f'Both players agreed - resuming game `{game_id}`...', view=None,
            )
            await resume_game(
                bot, game_id,
                channel_id_override=channel_id,
                announcement='**Game resumed.**',
            )

        mode_label = f'TCG best of {row["best_of"]}' if row['is_tcg'] else 'standard'
        saved_date = row['saved_at'].date().isoformat() if row['saved_at'] else 'unknown date'

        if in_guild:
            view = ResumeConfirmationView(
                game_id=game_id,
                invoker_id=invoker_id,
                opponent_id=opponent_id,
                on_accept=start_resume,
            )
            await interaction.response.send_message(
                f'<@{opponent_id}> - **{interaction.user.display_name}** wants to resume '
                f'saved game `{game_id}` ({mode_label}, saved {saved_date}). '
                'Both players must agree before the game continues.',
                view=view,
                allowed_mentions=discord.AllowedMentions(users=[discord.Object(id=opponent_id)]),
            )
            view.message = await interaction.original_response()
            return

        # Opening the opponent's DM costs extra round trips, so answer the
        # interaction first. Cancel is dropped: the request lives in the
        # opponent's DM, where the invoker can never press it.
        await interaction.response.defer()
        view = ResumeConfirmationView(
            game_id=game_id,
            invoker_id=invoker_id,
            opponent_id=opponent_id,
            on_accept=start_resume,
            allow_cancel=False,
        )
        try:
            dm_channel = await open_dm_channel(bot, opponent_id)
            view.message = await dm_channel.send(
                content=(
                    f'**{interaction.user.display_name}** wants to resume saved game `{game_id}` '
                    f'({mode_label}, saved {saved_date}). '
                    'Both players must agree before the game continues.'
                ),
                view=view,
            )
        except discord.HTTPException:
            log.warning(
                'Could not DM the resume request for game %s to %s', game_id, opponent_id,
            )
            await interaction.followup.send(
                'I could not send the resume request to your opponent - they may have '
                'DMs turned off. Ask them to open their DMs, or run `/zutomayo resume` '
                'in a server channel you both share.',
                ephemeral=True,
            )
            return

        opponent_name = resolve_display_name(bot, opponent_id)
        await interaction.followup.send(
            f'Resume request for game `{game_id}` sent to **{opponent_name}** in a DM. '
            'It expires in 5 minutes if they do not respond.'
        )

    @resume_saved_game.autocomplete('game_id')
    async def resume_autocomplete(
        self, interaction: discord.Interaction, current: str,
    ) -> list[app_commands.Choice[str]]:
        try:
            from zutomayo.engine.game_persistence import list_saved_games_for_player

            choices = []
            for row in await list_saved_games_for_player(interaction.user.id, current):
                saved_date = row['saved_at'].date().isoformat() if row['saved_at'] else 'unknown date'
                mode_label = f'TCG best of {row["best_of"]}' if row['is_tcg'] else row['mode']
                choices.append(app_commands.Choice(
                    name=f'{row["game_id"]} ({mode_label}, saved {saved_date})'[:100],
                    value=row['game_id'],
                ))
            return choices[:25]
        except Exception:
            log.exception('resume autocomplete failed')
            return []

    @group.command(
        name='editname',
        description='Set the name shown in games and leaderboards (leave empty to revert to your Discord name)',
    )
    @app_commands.describe(name=f'Your new display name (max {MAXIMUM_CUSTOM_NAME_LENGTH} characters)')
    async def edit_name(self, interaction: discord.Interaction, name: str | None = None) -> None:
        if name is None or not name.strip():
            await clear_custom_name(interaction.user.id)
            remember_user(interaction.user.id, interaction.user.global_name or interaction.user.name)
            await interaction.response.send_message(
                'Your display name now follows your Discord name again.', ephemeral=True,
            )
            return

        name = name.strip()
        if len(name) > MAXIMUM_CUSTOM_NAME_LENGTH:
            await interaction.response.send_message(
                f'Display name must be {MAXIMUM_CUSTOM_NAME_LENGTH} characters or fewer.', ephemeral=True,
            )
            return

        await set_custom_name(interaction.user.id, name)
        await interaction.response.send_message(
            f'Your display name is now **{name}**.', ephemeral=True,
        )

    @group.command(
        name='summary',
        description='Show a full summary of a finished game: moves, phases, hands, and results',
    )
    @app_commands.describe(game_id='The finished game to summarize (search by game ID)')
    async def game_summary(self, interaction: discord.Interaction, game_id: str) -> None:
        from zutomayo.data.deck_validator import get_card_index
        from zutomayo.engine.game_persistence import (
            SUMMARY_ELIGIBLE_STATUSES,
            get_game_row,
            load_events,
        )
        from zutomayo.ui.game_summary_view import GameSummaryView, build_game_summary

        row = await get_game_row(game_id)
        if row is None or row['status'] not in SUMMARY_ELIGIBLE_STATUSES:
            await interaction.response.send_message(
                f'Game `{game_id}` was not found or is not finished yet. '
                'Summaries are available for completed, quit, or abandoned games.',
                ephemeral=True,
            )
            return

        await interaction.response.defer()

        manifest = row.get('manifest') or {}
        player_ids = manifest.get('player_discord_ids', [])
        human_ids = [player_id for player_id, _ in player_ids if player_id != 0]
        await ensure_display_names(self.bot, human_ids)

        from zutomayo.data.name_storage import resolve_display_name
        from zutomayo.match.agents import BOT_NAME

        player_names = {
            index: (BOT_NAME if player_id == 0 else resolve_display_name(self.bot, player_id))
            for player_id, index in player_ids
        }

        events = await load_events(game_id)
        _, card_index = get_card_index()
        summary = build_game_summary(row, player_names, events, card_index)
        if not summary.pages:
            await interaction.followup.send(
                f'No summary data is recorded for game `{game_id}`.',
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        view = GameSummaryView(game_id, summary)
        view.message = await interaction.followup.send(
            embed=view.build_embed(),
            view=view,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @game_summary.autocomplete('game_id')
    async def game_summary_autocomplete(
        self, interaction: discord.Interaction, current: str,
    ) -> list[app_commands.Choice[str]]:
        try:
            from zutomayo.engine.game_persistence import search_finished_games

            choices = []
            for row in await search_finished_games(current):
                if row['is_tcg']:
                    mode_label = f'TCG best of {row["best_of"]}'
                else:
                    mode_label = row['mode']
                played_date = row['created_at'].date().isoformat() if row['created_at'] else ''
                choices.append(app_commands.Choice(
                    name=f'{row["game_id"]} ({mode_label}, {row["status"]}, {played_date})'[:100],
                    value=row['game_id'],
                ))
            return choices[:25]
        except Exception:
            log.exception('summary autocomplete failed')
            return []

    async def _resolve_player_option(
        self, interaction: discord.Interaction, player: str | None,
    ) -> tuple[int, str, str | None, bool] | None:
        """
        Resolve the optional player search option to
        (user_id, display_name, avatar_url, viewing_own). The option value is
        a user id string from autocomplete, but a typed display name is also
        accepted. Sends the not-found reply itself and returns None on failure.
        """
        from zutomayo.data.name_storage import resolve_display_name, search_known_players

        if player is None or not player.strip():
            return (
                interaction.user.id,
                interaction.user.display_name,
                interaction.user.display_avatar.url,
                True,
            )

        player = player.strip()
        if player.isdigit():
            target_id = int(player)
        else:
            matches = await search_known_players(player, limit=2)
            exact = [pair for pair in matches if pair[1].lower() == player.lower()]
            if len(exact) == 1:
                target_id = exact[0][0]
            elif len(matches) == 1:
                target_id = matches[0][0]
            else:
                await interaction.response.send_message(
                    f'No player named **{player}** was found. Start typing to '
                    'search known players.',
                    ephemeral=True,
                )
                return None

        if target_id == interaction.user.id:
            return (
                interaction.user.id,
                interaction.user.display_name,
                interaction.user.display_avatar.url,
                True,
            )

        display_name = resolve_display_name(self.bot, target_id)
        user = self.bot.get_user(target_id)
        avatar_url = user.display_avatar.url if user is not None else None
        return target_id, display_name, avatar_url, False

    async def _player_search_autocomplete(
        self, interaction: discord.Interaction, current: str,
    ) -> list[app_commands.Choice[str]]:
        try:
            from zutomayo.data.name_storage import search_known_players

            matches = await search_known_players(current)
            return [
                app_commands.Choice(name=name[:100], value=str(user_id))
                for user_id, name in matches[:25]
            ]
        except Exception:
            log.exception('player search autocomplete failed')
            return []

    @group.command(
        name='profilestats',
        description='Show a player profile (Elo, win/loss, top decks, top rivals); defaults to your own',
    )
    @app_commands.describe(player='Another player to look up (search by name); leave empty for yourself')
    async def profile_stats(self, interaction: discord.Interaction, player: str | None = None) -> None:
        resolved = await self._resolve_player_option(interaction, player)
        if resolved is None:
            return
        target_id, display_name, avatar_url, viewing_own = resolved

        await interaction.response.defer()
        profile = await load_profile(target_id)
        rival_ids = [
            int(opponent_id_str)
            for opponent_id_str in profile.get('opponent_stats', {})
            if opponent_id_str.isdigit()
        ]
        await ensure_display_names(self.bot, rival_ids)
        embed = build_profile_embed(
            self.bot, profile,
            display_name=display_name,
            avatar_url=avatar_url,
            viewing_own=viewing_own,
        )
        await interaction.followup.send(
            embed=embed, allowed_mentions=discord.AllowedMentions.none(),
        )

    @profile_stats.autocomplete('player')
    async def profile_stats_autocomplete(self, interaction: discord.Interaction, current: str):
        return await self._player_search_autocomplete(interaction, current)

    @group.command(
        name='history',
        description='List recent finished games (yours or another playerâ€™s) with their game ids',
    )
    @app_commands.describe(player='Another player to look up (search by name); leave empty for yourself')
    async def game_history(self, interaction: discord.Interaction, player: str | None = None) -> None:
        from zutomayo.data.name_storage import resolve_display_name
        from zutomayo.match.agents import BOT_NAME
        from zutomayo.engine.game_persistence import list_recent_games_for_player

        resolved = await self._resolve_player_option(interaction, player)
        if resolved is None:
            return
        target_id, display_name, _, viewing_own = resolved

        await interaction.response.defer()
        recent_games = await list_recent_games_for_player(target_id, limit=15)
        if not recent_games:
            subject = 'You have' if viewing_own else f'**{display_name}** has'
            await interaction.followup.send(
                f'{subject} no finished games yet.',
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        opponent_ids = [
            row['opponent_discord_id'] for row in recent_games
            if row['opponent_discord_id'] not in (None, 0)
        ]
        await ensure_display_names(self.bot, opponent_ids)

        lines = []
        for row in recent_games:
            if row['opponent_discord_id'] in (None, 0):
                opponent_name = BOT_NAME
            else:
                opponent_name = resolve_display_name(self.bot, row['opponent_discord_id'])
            if row['status'] != 'completed':
                outcome = row['status']
            elif row['winner_index'] is None:
                outcome = 'draw'
            elif row['winner_index'] == row['player_index']:
                outcome = 'won'
            else:
                outcome = 'lost'
            mode_label = f'TCG bo{row["best_of"]}' if row['is_tcg'] else row['mode']
            played_date = row['created_at'].date().isoformat() if row['created_at'] else ''
            lines.append(
                f'`{row["game_id"]}` â€” {mode_label} vs {opponent_name} â€” {outcome} ({played_date})'
            )

        title = 'Your Recent Games' if viewing_own else f'Recent Games â€” {display_name}'
        embed = discord.Embed(
            title=title,
            description='\n'.join(lines),
            color=discord.Color.blurple(),
        )
        embed.set_footer(text='Use /zutomayo summary <game id> to replay any of these games.')
        await interaction.followup.send(
            embed=embed, allowed_mentions=discord.AllowedMentions.none(),
        )

    @game_history.autocomplete('player')
    async def game_history_autocomplete(self, interaction: discord.Interaction, current: str):
        return await self._player_search_autocomplete(interaction, current)

    async def _send_leaderboard(
        self,
        interaction: discord.Interaction,
        ranked_rows: list[dict],
        **embed_kwargs,
    ) -> None:
        """
        Render and send a leaderboard. Short leaderboards (<= one page) are sent as a
        plain embed; longer ones get a paginated LeaderboardView. The same renderer
        kwargs flow through to build_leaderboard_embed either way.
        """
        await ensure_display_names(
            self.bot,
            [row['user_id'] for row in ranked_rows[:LeaderboardView.PAGE_SIZE]]
            + [interaction.user.id],
        )

        if len(ranked_rows) <= LeaderboardView.PAGE_SIZE:
            embed = build_leaderboard_embed(
                self.bot,
                ranked_rows,
                interaction.user.id,
                **embed_kwargs,
            )
            await interaction.followup.send(embed=embed)
            return

        view = LeaderboardView(
            self.bot,
            ranked_rows,
            interaction.user.id,
            **embed_kwargs,
        )
        view.rebuild_buttons()
        view.message = await interaction.followup.send(embed=view.build_embed(), view=view)

    @group.command(
        name='leaderboard',
        description='Show the server leaderboard ranked by Elo rating',
    )
    @app_commands.describe(board_format='Standard or TCG ladder; default: standard')
    @app_commands.rename(board_format='format')
    @app_commands.choices(board_format=[
        app_commands.Choice(name='Standard', value='standard'),
        app_commands.Choice(name='TCG', value='tcg'),
    ])
    async def leaderboard(self, interaction: discord.Interaction, board_format: str = 'standard') -> None:
        await interaction.response.defer()
        if board_format == 'tcg':
            ranked_rows = await list_ranked_profiles(
                rating_field='tcg_elo',
                games_field='tcg_elo_games',
                minimum_games=LEADERBOARD_MINIMUM_GAMES,
            )
            await self._send_leaderboard(
                interaction,
                ranked_rows,
                title='Zutoka TCG Leaderboard',
                elo_field='tcg_elo',
                elo_games_field='tcg_elo_games',
                record_stats_bucket='tcg_series',
                empty_message='No ranked players yet. Finish a TCG series with `/zutomayo create format:TCG` to appear here.',
            )
            return
        ranked_rows = await list_ranked_profiles(
            rating_field='elo',
            games_field='elo_games',
            minimum_games=LEADERBOARD_MINIMUM_GAMES,
        )
        await self._send_leaderboard(interaction, ranked_rows)

    @staticmethod
    async def _mark_session_quit(session) -> None:
        """Mark the game record quit; a game with no record yet (still in the
        lobby or deck building) has nothing to update."""
        from zutomayo.engine.game_persistence import STATUS_QUIT

        if session.persistence is None:
            return
        try:
            await session.persistence.set_status(STATUS_QUIT)
        except Exception:
            log.exception('Failed to mark game %s quit', session.game_id)

    @staticmethod
    async def _record_forfeit_for_session(session, quitter_id: int) -> None:
        """Record a forfeit_given for the quitter and forfeit_received for the human opponent (if any).
        Storage errors are logged and swallowed so the quit/end command stays responsive.
        """
        from zutomayo.data.player_storage import BOT_DISCORD_ID, record_forfeit

        try:
            opponent_id = None
            for discord_id in session.player_discord_ids:
                if discord_id != quitter_id and discord_id != BOT_DISCORD_ID:
                    opponent_id = discord_id
                    break
            await record_forfeit(quitter_id, opponent_id)

            if session.persistence is not None:
                from zutomayo.engine.game_events import EVENT_FORFEIT

                session.persistence.emit_event(EVENT_FORFEIT, {
                    'player_index': session.get_player_index(quitter_id),
                    'discord_id': quitter_id,
                })
        except Exception:
            log.exception('Failed to record forfeit for game %s', session.game_id)


async def setup(bot: commands.Bot):
    await bot.add_cog(GameCog(bot))
