"""Command tree shape and the merged create command's validation matrix."""

from __future__ import annotations

import asyncio

from zutomayo.cogs.game_cog import GameCog

DISCORD_SUBCOMMAND_LIMIT = 25

EXPECTED_TOP_LEVEL = {
    'create', 'deck', 'join', 'end', 'quit', 'resume', 'gacha',
    'editname', 'summary', 'profilestats', 'history', 'leaderboard',
}
EXPECTED_DECK_SUBCOMMANDS = {'make', 'view', 'manage'}


def test_top_level_command_tree_shape():
    names = {command.name for command in GameCog.group.commands}
    assert names == EXPECTED_TOP_LEVEL
    assert len(GameCog.group.commands) <= DISCORD_SUBCOMMAND_LIMIT


def test_deck_group_shape():
    deck_group = next(
        command for command in GameCog.group.commands if command.name == 'deck'
    )
    names = {command.name for command in deck_group.commands}
    assert names == EXPECTED_DECK_SUBCOMMANDS
    assert len(deck_group.commands) <= DISCORD_SUBCOMMAND_LIMIT


class FakeResponse:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_message(self, content=None, **kwargs) -> None:
        self.messages.append({'content': content, **kwargs})

    async def defer(self, **kwargs) -> None:
        return None


class FakeInteraction:
    def __init__(self, in_guild: bool = True) -> None:
        self.guild = object() if in_guild else None
        self.channel_id = 555
        self.response = FakeResponse()

        class User:
            id = 424242
            display_name = 'Tester'
            global_name = 'Tester'
            name = 'tester'
            bot = False

        self.user = User()


def run_create(cog: GameCog, interaction: FakeInteraction, **kwargs) -> None:
    asyncio.run(GameCog.create_game.callback(cog, interaction, **kwargs))


def build_cog() -> GameCog:
    return GameCog.__new__(GameCog)


def test_create_rejects_best_of_without_tcg():
    interaction = FakeInteraction()
    run_create(build_cog(), interaction, best_of=3)
    assert 'best_of' in interaction.response.messages[0]['content']


def test_create_rejects_draft_options_without_draft():
    interaction = FakeInteraction()
    run_create(build_cog(), interaction, boxes=2)
    assert 'draft' in interaction.response.messages[0]['content']

    interaction = FakeInteraction()
    run_create(build_cog(), interaction, visibility='public')
    assert 'draft' in interaction.response.messages[0]['content']


def test_create_rejects_two_player_game_in_direct_message():
    interaction = FakeInteraction(in_guild=False)
    run_create(build_cog(), interaction)
    assert 'server channel' in interaction.response.messages[0]['content']


def test_create_rejects_solo_in_guild_and_unavailable_opponents():
    interaction = FakeInteraction(in_guild=True)
    run_create(build_cog(), interaction, opponent='alphazero')
    assert 'DM' in interaction.response.messages[0]['content']

    interaction = FakeInteraction(in_guild=False)
    run_create(build_cog(), interaction, opponent='alphazero', game_format='tcg')
    assert 'TCG' in interaction.response.messages[0]['content']

    interaction = FakeInteraction(in_guild=False)
    run_create(build_cog(), interaction, opponent='alphazero', deck='draft')
    assert 'Draft' in interaction.response.messages[0]['content']

    # No trained checkpoints exist, so every model opponent is rejected.
    interaction = FakeInteraction(in_guild=False)
    run_create(build_cog(), interaction, opponent='alphazero')
    assert 'not available' in interaction.response.messages[0]['content']
