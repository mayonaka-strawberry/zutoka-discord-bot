import logging
import discord
from discord import app_commands
from discord.ext import commands
from zutomayo.engine.game_session import session_manager
from zutomayo.ui.views import GameLobbyView


log = logging.getLogger(__name__)


class GameCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    group = app_commands.Group(name='zutomayo', description='ZUTOMAYO CARD game commands')

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

        session_manager.remove_game(game_id)
        log.info('Game %s ended by %s (end command)', game_id, interaction.user)
        await interaction.response.send_message(
            f'**{interaction.user.display_name}** ended game `{game_id}`.'
        )

    @group.command(name='makedeck', description='Create and save a new deck')
    @app_commands.describe(name='A unique name for this deck (max 50 characters)')
    async def make_deck(self, interaction: discord.Interaction, name: str):
        if len(name) > 50:
            await interaction.response.send_message(
                'Deck name must be 50 characters or fewer.', ephemeral=True,
            )
            return

        from zutomayo.data.card_loader import load_cards
        from zutomayo.data.deck_storage import get_deck_names
        from zutomayo.data.deck_validator import build_card_index

        existing_names = get_deck_names(interaction.user.id)
        if name in existing_names:
            await interaction.response.send_message(
                f'A deck named **{name}** already exists. Please choose a different name.',
                ephemeral=True,
            )
            return

        all_cards = load_cards()
        card_index = build_card_index(all_cards)

        from zutomayo.ui.deck_management_views import MakeDeckModal
        modal = MakeDeckModal(deck_name=name, user_id=interaction.user.id, card_index=card_index)
        await interaction.response.send_modal(modal)

    @group.command(name='managedecks', description='Edit or delete your saved decks')
    async def manage_decks(self, interaction: discord.Interaction):
        from zutomayo.data.card_loader import load_cards
        from zutomayo.data.deck_storage import get_deck_names
        from zutomayo.data.deck_validator import build_card_index

        deck_names = get_deck_names(interaction.user.id)
        if not deck_names:
            await interaction.response.send_message(
                'You have no saved decks. Use `/zutomayo makedeck` to create one.',
                ephemeral=True,
            )
            return

        all_cards = load_cards()
        card_index = build_card_index(all_cards)

        from zutomayo.ui.deck_management_views import ManageDecksView
        view = ManageDecksView(
            user_id=interaction.user.id,
            deck_names=deck_names,
            card_index=card_index,
        )
        await interaction.response.send_message(
            'Select a deck to manage:',
            view=view,
            ephemeral=True,
        )

    @group.command(name='viewdeck', description='View your saved decks')
    async def view_deck(self, interaction: discord.Interaction):
        from zutomayo.data.card_loader import load_cards
        from zutomayo.data.deck_storage import load_user_decks
        from zutomayo.data.deck_validator import build_card_index

        decks = load_user_decks(interaction.user.id)
        if not decks:
            await interaction.response.send_message(
                'You have no saved decks. Use `/zutomayo makedeck` to create one.',
                ephemeral=True,
            )
            return

        all_cards = load_cards()
        card_index = build_card_index(all_cards)

        from zutomayo.ui.deck_management_views import ViewDeckView
        from zutomayo.ui.embeds import create_deck_grid_image

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
        grid = create_deck_grid_image(view.current_cards())
        if grid:
            await interaction.followup.send(file=grid, ephemeral=True)

    @group.command(name='makedecktcg', description='Create and save a new TCG deck (20 main + 8 side)')
    @app_commands.describe(name='A unique name for this deck (max 50 characters)')
    async def make_deck_tcg(self, interaction: discord.Interaction, name: str):
        if len(name) > 50:
            await interaction.response.send_message(
                'Deck name must be 50 characters or fewer.', ephemeral=True,
            )
            return

        from zutomayo.data.card_loader import load_cards
        from zutomayo.data.deck_storage_tcg import get_tcg_deck_names
        from zutomayo.data.deck_validator import build_card_index

        existing_names = get_tcg_deck_names(interaction.user.id)
        if name in existing_names:
            await interaction.response.send_message(
                f'A TCG deck named **{name}** already exists. Please choose a different name.',
                ephemeral=True,
            )
            return

        all_cards = load_cards()
        card_index = build_card_index(all_cards)

        from zutomayo.ui.deck_management_views_tcg import MakeDeckTcgModal
        modal = MakeDeckTcgModal(deck_name=name, user_id=interaction.user.id, card_index=card_index)
        await interaction.response.send_modal(modal)

    @group.command(name='viewdecktcg', description='View your saved TCG decks')
    async def view_deck_tcg(self, interaction: discord.Interaction):
        from zutomayo.data.card_loader import load_cards
        from zutomayo.data.deck_storage_tcg import load_user_tcg_decks
        from zutomayo.data.deck_validator import build_card_index

        decks = load_user_tcg_decks(interaction.user.id)
        if not decks:
            await interaction.response.send_message(
                'You have no saved TCG decks. Use `/zutomayo makedecktcg` to create one.',
                ephemeral=True,
            )
            return

        all_cards = load_cards()
        card_index = build_card_index(all_cards)

        from zutomayo.ui.deck_management_views_tcg import ViewDeckTcgView
        from zutomayo.ui.embeds import create_deck_grid_image

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
        grid = create_deck_grid_image(main_cards)
        if grid:
            await interaction.followup.send(content='**Main Deck:**', file=grid, ephemeral=True)
        side_grid = create_deck_grid_image(side_cards, columns=4)
        if side_grid:
            await interaction.followup.send(content='**Side Deck:**', file=side_grid, ephemeral=True)

    @group.command(name='managedeckstcg', description='Edit or delete your saved TCG decks')
    async def manage_decks_tcg(self, interaction: discord.Interaction):
        from zutomayo.data.card_loader import load_cards
        from zutomayo.data.deck_storage_tcg import get_tcg_deck_names
        from zutomayo.data.deck_validator import build_card_index

        deck_names = get_tcg_deck_names(interaction.user.id)
        if not deck_names:
            await interaction.response.send_message(
                'You have no saved TCG decks. Use `/zutomayo makedecktcg` to create one.',
                ephemeral=True,
            )
            return

        all_cards = load_cards()
        card_index = build_card_index(all_cards)

        from zutomayo.ui.deck_management_views_tcg import ManageDecksTcgView
        view = ManageDecksTcgView(
            user_id=interaction.user.id,
            deck_names=deck_names,
            card_index=card_index,
        )
        await interaction.response.send_message(
            'Select a TCG deck to manage:',
            view=view,
            ephemeral=True,
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
        from zutomayo.ui.embeds import create_deck_grid_image

        all_cards = load_cards()
        drawn = draw_gacha(pack, all_cards)
        image = create_deck_grid_image(drawn, columns=5, filename='gacha.jpg')
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
        from zutomayo.ui.embeds import create_deck_grid_image

        all_cards = load_cards()
        drawn = draw_gachabox(pack, all_cards)
        half = len(drawn) // 2
        image1 = create_deck_grid_image(drawn[:half], columns=5, filename='gachabox_1.jpg')
        image2 = create_deck_grid_image(drawn[half:], columns=5, filename='gachabox_2.jpg')
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

        session_manager.remove_game(session.game_id)
        log.info('Game %s ended by %s (quit command)', session.game_id, interaction.user)
        await interaction.response.send_message(
            f'**{interaction.user.display_name}** quit the game. Game `{session.game_id}` has been removed.'
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(GameCog(bot))
