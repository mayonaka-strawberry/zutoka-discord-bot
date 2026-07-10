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


log = logging.getLogger(__name__)


LEADERBOARD_MINIMUM_GAMES = 1


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

    @group.command(name='create', description='Create a new ZUTOMAYO CARD game')
    @app_commands.guild_only()
    async def create_game(self, interaction: discord.Interaction):
        try:
            session = session_manager.create_game(interaction.channel_id, interaction.user.id)
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return

        view = GameLobbyView(session.game_id)
        await interaction.response.send_message(
            f'**ZUTOMAYO CARD** - Game created by **{interaction.user.display_name}**!\n'
            f'Game ID: `{session.game_id}`\n'
            f'Click the button below or use `/zutomayo join {session.game_id}` to join.',
            view=view,
        )

    @group.command(name='createtcg', description='Create a new ZUTOMAYO CARD game (Best of N TCG format)')
    @app_commands.guild_only()
    @app_commands.describe(best_of='Best of 3 or 5 (default: 3)')
    @app_commands.choices(best_of=[
        app_commands.Choice(name='Best of 3', value=3),
        app_commands.Choice(name='Best of 5', value=5),
    ])
    async def create_tcg_game(self, interaction: discord.Interaction, best_of: int = 3):
        if best_of not in (3, 5):
            await interaction.response.send_message(
                'best_of must be 3 or 5.', ephemeral=True,
            )
            return

        try:
            session = session_manager.create_game(interaction.channel_id, interaction.user.id)
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return

        session.is_tcg = True
        session.best_of = best_of

        view = GameLobbyView(session.game_id)
        await interaction.response.send_message(
            f'**ZUTOMAYO CARD TCG** - Best of {best_of} created by **{interaction.user.display_name}**!\n'
            f'Game ID: `{session.game_id}`\n'
            f'Click the button below or use `/zutomayo join {session.game_id}` to join.',
            view=view,
        )

    @group.command(name='playuniguri', description='Play a solo game against メカうにぐり')
    @app_commands.dm_only()
    async def play_uniguri(self, interaction: discord.Interaction):
        channel_id = 0
        try:
            session = session_manager.create_solo_game(channel_id, interaction.user.id)
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return

        session.solo_difficulty = 'normal'

        await interaction.response.send_message(
            f'**メカうにぐり** has accepted **{interaction.user.display_name}**\'s challenge!\n'
            f'Game ID: `{session.game_id}`\n'
            f'Starting solo game...'
        )

        from zutomayo.engine.solo_game_flow import SoloGameFlow
        solo_flow = SoloGameFlow(self.bot)
        session.game_task = self.bot.loop.create_task(
            solo_flow.run_solo_game(session)
        )

    @group.command(name='playunigurieasy', description='Play a solo game against an easier メカうにぐり')
    @app_commands.dm_only()
    async def play_uniguri_easy(self, interaction: discord.Interaction):
        channel_id = 0
        try:
            session = session_manager.create_solo_game(channel_id, interaction.user.id)
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return

        session.solo_difficulty = 'easy'

        await interaction.response.send_message(
            f'**メカうにぐり** has accepted **{interaction.user.display_name}**\'s challenge!\n'
            f'Game ID: `{session.game_id}`\n'
            f'Starting solo game...'
        )

        from zutomayo.engine.bot_agent import create_bot_agent_easy
        from zutomayo.engine.solo_game_flow import SoloGameFlow
        solo_flow = SoloGameFlow(self.bot, bot_agent=create_bot_agent_easy(), use_easy_decks=True)
        session.game_task = self.bot.loop.create_task(
            solo_flow.run_solo_game(session)
        )

    @group.command(name='ranksongs', description='Rank your favourite ZUTOMAYO songs')
    @app_commands.dm_only()
    async def rank_songs(self, interaction: discord.Interaction):
        checkpoint_path = get_checkpoint_path(interaction.user.id)
        if checkpoint_path.exists():
            progress_info = ''
            try:
                data = json.loads(checkpoint_path.read_text(encoding='utf-8'))
                votes_done = len(data['recorded_votes'])
                total = data['total_matchups']
                percentage = round(votes_done / total * 100)
                progress_info = f' You are {percentage}% complete ({votes_done}/{total} matchups done).'
            except (KeyError, json.JSONDecodeError):
                pass
            view = CheckpointChoiceView(user_id=interaction.user.id, number_of_rounds=DEFAULT_NUMBER_OF_ROUNDS)
            await interaction.response.send_message(
                f'A saved checkpoint was found.{progress_info} Choose to resume or start from scratch.',
                view=view,
            )
        else:
            log.info('User %s started a new song ranking', interaction.user.id)
            view = RankSongsView(number_of_rounds=DEFAULT_NUMBER_OF_ROUNDS, user_id=interaction.user.id)
            await interaction.response.send_message(content=view._build_matchup_content(), view=view)

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
            from zutomayo.engine.tcg_match_flow import TcgMatchFlow
            flow = TcgMatchFlow(self.bot, session.best_of)
            session.game_task = self.bot.loop.create_task(
                flow.run_tcg(session)
            )
        else:
            from zutomayo.engine.game_flow import GameFlow
            game_flow = GameFlow(self.bot)
            session.game_task = self.bot.loop.create_task(
                game_flow.run_game(session)
            )

    @group.command(name='end', description='End a specific game by ID')
    @app_commands.guild_only()
    @app_commands.describe(game_id='The game ID to end')
    async def end_game(self, interaction: discord.Interaction, game_id: str):
        session = session_manager.active_games.get(game_id)
        if session is None:
            await interaction.response.send_message(f'Game `{game_id}` not found.', ephemeral=True)
            return

        if interaction.user.id not in session.player_discord_ids:
            await interaction.response.send_message('You are not a player in that game.', ephemeral=True)
            return

        if session.game_task and not session.game_task.done():
            session.game_task.cancel()

        await self._record_forfeit_for_session(session, interaction.user.id)
        session_manager.remove_game(game_id)
        log.info('Game %s ended by %s (end command)', game_id, interaction.user)
        await interaction.response.send_message(
            f'**{interaction.user.display_name}** ended game `{game_id}`.'
        )

    async def _start_make_deck(
        self,
        interaction: discord.Interaction,
        name: str,
        load_deck_names,
        duplicate_message: str,
        modal_class,
    ) -> None:
        """Shared body of the makedeck / makedecktcg commands."""
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

    async def _open_manage_decks(
        self,
        interaction: discord.Interaction,
        load_deck_names,
        empty_message: str,
        view_class,
        prompt_text: str,
    ) -> None:
        """Shared body of the managedecks / managedeckstcg commands."""
        from zutomayo.data.deck_validator import get_card_index

        deck_names = await load_deck_names(interaction.user.id)
        if not deck_names:
            await interaction.response.send_message(empty_message, ephemeral=True)
            return

        _, card_index = get_card_index()
        view = view_class(
            user_id=interaction.user.id,
            deck_names=deck_names,
            card_index=card_index,
        )
        await interaction.response.send_message(prompt_text, view=view, ephemeral=True)

    @group.command(name='makedeck', description='Create and save a new deck')
    @app_commands.describe(name='A unique name for this deck (max 50 characters)')
    async def make_deck(self, interaction: discord.Interaction, name: str):
        from zutomayo.data.deck_storage import get_deck_names
        from zutomayo.ui.deck_management_views import MakeDeckModal

        await self._start_make_deck(
            interaction, name, get_deck_names,
            f'A deck named **{name}** already exists. Please choose a different name.',
            MakeDeckModal,
        )

    @group.command(name='managedecks', description='Edit or delete your saved decks')
    async def manage_decks(self, interaction: discord.Interaction):
        from zutomayo.data.deck_storage import get_deck_names
        from zutomayo.ui.deck_management_views import ManageDecksView

        await self._open_manage_decks(
            interaction, get_deck_names,
            'You have no saved decks. Use `/zutomayo makedeck` to create one.',
            ManageDecksView,
            'Select a deck to manage:',
        )

    @group.command(name='viewdeck', description='View your saved decks')
    async def view_deck(self, interaction: discord.Interaction):
        from zutomayo.data.deck_storage import load_user_decks
        from zutomayo.data.deck_validator import get_card_index

        decks = await load_user_decks(interaction.user.id)
        if not decks:
            await interaction.response.send_message(
                'You have no saved decks. Use `/zutomayo makedeck` to create one.',
                ephemeral=True,
            )
            return

        _, card_index = get_card_index()

        from zutomayo.ui.deck_management_views import ViewDeckView
        from zutomayo.ui.embeds import create_deck_grid_image_off_thread

        view = ViewDeckView(
            user_id=interaction.user.id,
            decks=decks,
            card_index=card_index,
        )
        await interaction.response.send_message(
            embed=view.current_embed(),
            view=view,
            ephemeral=True,
        )
        grid = await create_deck_grid_image_off_thread(view.current_cards())
        if grid:
            await interaction.followup.send(file=grid, ephemeral=True)

    @group.command(name='makedecktcg', description='Create and save a new TCG deck (20 main + 8 side)')
    @app_commands.describe(name='A unique name for this deck (max 50 characters)')
    async def make_deck_tcg(self, interaction: discord.Interaction, name: str):
        from zutomayo.data.deck_storage_tcg import get_tcg_deck_names
        from zutomayo.ui.deck_management_views_tcg import MakeDeckTcgModal

        await self._start_make_deck(
            interaction, name, get_tcg_deck_names,
            f'A TCG deck named **{name}** already exists. Please choose a different name.',
            MakeDeckTcgModal,
        )

    @group.command(name='viewdecktcg', description='View your saved TCG decks')
    async def view_deck_tcg(self, interaction: discord.Interaction):
        from zutomayo.data.deck_storage_tcg import load_user_tcg_decks
        from zutomayo.data.deck_validator import get_card_index

        decks = await load_user_tcg_decks(interaction.user.id)
        if not decks:
            await interaction.response.send_message(
                'You have no saved TCG decks. Use `/zutomayo makedecktcg` to create one.',
                ephemeral=True,
            )
            return

        _, card_index = get_card_index()

        from zutomayo.ui.deck_management_views_tcg import ViewDeckTcgView
        from zutomayo.ui.embeds import create_deck_grid_image_off_thread

        view = ViewDeckTcgView(
            user_id=interaction.user.id,
            decks=decks,
            card_index=card_index,
        )
        await interaction.response.send_message(
            embeds=view.current_embeds(),
            view=view,
            ephemeral=True,
        )
        main_cards, side_cards = view.current_cards()
        grid = await create_deck_grid_image_off_thread(main_cards)
        if grid:
            await interaction.followup.send(content='**Main Deck:**', file=grid, ephemeral=True)
        side_grid = await create_deck_grid_image_off_thread(side_cards, columns=4)
        if side_grid:
            await interaction.followup.send(content='**Side Deck:**', file=side_grid, ephemeral=True)

    @group.command(name='managedeckstcg', description='Edit or delete your saved TCG decks')
    async def manage_decks_tcg(self, interaction: discord.Interaction):
        from zutomayo.data.deck_storage_tcg import get_tcg_deck_names
        from zutomayo.ui.deck_management_views_tcg import ManageDecksTcgView

        await self._open_manage_decks(
            interaction, get_tcg_deck_names,
            'You have no saved TCG decks. Use `/zutomayo makedecktcg` to create one.',
            ManageDecksTcgView,
            'Select a TCG deck to manage:',
        )

    @group.command(name='gacha', description='Open a card pack and draw 5 cards')
    @app_commands.describe(pack='Pack number (1-4)')
    async def gacha(self, interaction: discord.Interaction, pack: int):
        if pack < 1 or pack > 4:
            await interaction.response.send_message(
                'Pack must be between 1 and 4.', ephemeral=True,
            )
            return

        from zutomayo.data.card_loader import load_cards
        from zutomayo.data.gacha import draw_gacha
        from zutomayo.ui.embeds import create_deck_grid_image_off_thread

        all_cards = load_cards()
        drawn = draw_gacha(pack, all_cards)
        image = await create_deck_grid_image_off_thread(drawn, columns=5, filename='gacha.webp')
        if image:
            await interaction.response.send_message(file=image)
        else:
            await interaction.response.send_message(
                'Something went wrong generating the gacha image.',
                ephemeral=True,
            )

    @group.command(name='gachabox', description='Open a gacha box: 10 packs of 5 cards')
    @app_commands.describe(pack='Pack number (1-4)')
    async def gachabox(self, interaction: discord.Interaction, pack: int):
        if pack < 1 or pack > 4:
            await interaction.response.send_message(
                'Pack must be between 1 and 4.', ephemeral=True,
            )
            return

        await interaction.response.defer()

        from zutomayo.data.card_loader import load_cards
        from zutomayo.data.gacha import draw_gachabox
        from zutomayo.ui.embeds import create_deck_grid_image_off_thread

        all_cards = load_cards()
        drawn = draw_gachabox(pack, all_cards)
        half = len(drawn) // 2
        image1 = await create_deck_grid_image_off_thread(drawn[:half], columns=5, filename='gachabox_1.webp')
        image2 = await create_deck_grid_image_off_thread(drawn[half:], columns=5, filename='gachabox_2.webp')
        files = [f for f in (image1, image2) if f]
        if files:
            await interaction.followup.send(files=files)
        else:
            await interaction.followup.send(
                'Something went wrong generating the gacha box image.',
            )

    @group.command(name='quit', description='Quit your current game')
    @app_commands.guild_only()
    async def quit_game(self, interaction: discord.Interaction):
        session = session_manager.get_session_by_player(interaction.user.id)
        if session is None:
            await interaction.response.send_message('You are not in a game.', ephemeral=True)
            return

        if session.game_task and not session.game_task.done():
            session.game_task.cancel()

        await self._record_forfeit_for_session(session, interaction.user.id)
        session_manager.remove_game(session.game_id)
        log.info('Game %s ended by %s (quit command)', session.game_id, interaction.user)
        await interaction.response.send_message(
            f'**{interaction.user.display_name}** quit the game. Game `{session.game_id}` has been removed.'
        )

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
        name='profilestats',
        description='Show your own ZUTOMAYO CARD player profile (Elo, win/loss, top decks, top rivals)',
    )
    async def profile_stats(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        profile = await load_profile(interaction.user.id)
        rival_ids = [
            int(opponent_id_str)
            for opponent_id_str in profile.get('opponent_stats', {})
            if opponent_id_str.isdigit()
        ]
        await ensure_display_names(self.bot, rival_ids)
        embed = build_profile_embed(self.bot, interaction.user, profile)
        await interaction.followup.send(embed=embed)

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
    async def leaderboard(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        ranked_rows = await list_ranked_profiles(
            rating_field='elo',
            games_field='elo_games',
            minimum_games=LEADERBOARD_MINIMUM_GAMES,
        )
        await self._send_leaderboard(interaction, ranked_rows)

    @group.command(
        name='leaderboardtcg',
        description='Show the server leaderboard ranked by TCG Elo rating',
    )
    async def leaderboard_tcg(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
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
            empty_message='No ranked players yet. Finish a TCG series with `/zutomayo createtcg` to appear here.',
        )

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
        except Exception:
            log.exception('Failed to record forfeit for game %s', session.game_id)


async def setup(bot: commands.Bot):
    await bot.add_cog(GameCog(bot))
