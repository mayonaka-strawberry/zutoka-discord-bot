import asyncio
import logging
import os
import discord
from dotenv import load_dotenv
from discord.ext import commands

from zutomayo.data import database
from zutomayo.data import name_storage


load_dotenv()

logging.basicConfig(level=logging.INFO)

intents = discord.Intents.default()


class ZutokaBot(commands.Bot):
    async def setup_hook(self) -> None:
        # Fail fast if PostgreSQL is unreachable: every storage module
        # depends on the pool, so starting without it would only defer
        # the failure to the first command.
        await database.initialize_pool()
        await database.apply_schema()
        await name_storage.load_display_name_cache()
        await self.load_extension('zutomayo.cogs.game_cog')


bot = ZutokaBot(command_prefix='!', intents=intents)


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    raise error


resumed_games_once = False


@bot.event
async def on_ready():
    logging.info(f'Logged in as {bot.user} (ID: {bot.user.id})')
    try:
        synced = await bot.tree.sync()
        logging.info(f'Synced {len(synced)} slash commands')
    except Exception as e:
        logging.error(f'Failed to sync commands: {e}')

    # Resume in-flight games exactly once (on_ready can fire again on
    # reconnects, but replay must not restart running games).
    global resumed_games_once
    if not resumed_games_once:
        resumed_games_once = True
        from zutomayo.engine.resume_manager import resume_all
        try:
            await resume_all(bot)
        except Exception:
            logging.exception('Game resume failed')


async def main():
    try:
        async with bot:
            await bot.start(os.environ['DISCORD_TOKEN'])
    finally:
        await database.close_pool()


if __name__ == '__main__':
    asyncio.run(main())
