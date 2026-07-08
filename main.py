import asyncio
import logging
import os
import discord
from dotenv import load_dotenv
from discord.ext import commands


load_dotenv()

logging.basicConfig(level=logging.INFO)

intents = discord.Intents.default()

bot = commands.Bot(command_prefix='!', intents=intents)


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
    async with bot:
        await bot.load_extension('zutomayo.cogs.game_cog')
        await bot.start(os.environ['DISCORD_TOKEN'])


if __name__ == '__main__':
    asyncio.run(main())
