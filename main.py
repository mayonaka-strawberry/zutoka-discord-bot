import asyncio
import logging
import os
import discord
from dotenv import load_dotenv
from discord.ext import commands


load_dotenv()

logging.basicConfig(level=logging.INFO)

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix='!', intents=intents)


@bot.event
async def on_ready():
    logging.info(f'Logged in as {bot.user} (ID: {bot.user.id})')
    try:
        synced = await bot.tree.sync()
        logging.info(f'Synced {len(synced)} slash commands')
    except Exception as e:
        logging.error(f'Failed to sync commands: {e}')


async def main():
    async with bot:
        await bot.load_extension('zutomayo.cogs.game_cog')
        await bot.start(os.environ['DISCORD_TOKEN'])


if __name__ == '__main__':
    asyncio.run(main())
