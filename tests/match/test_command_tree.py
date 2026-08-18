"""Command tree shape and the per-command validation the cog does itself."""

from __future__ import annotations

import asyncio
import re

from zutomayo.cogs.game_cog import GameCog
from zutomayo.match.agents import SOLO_OPPONENT_ALPHA_ZERO

DISCORD_SUBCOMMAND_LIMIT = 25

# Model stack names that must never reach a player-facing surface.
STACK_NAMES = ('alphazero', 'alpha zero', 'ppo', 'transformer')

EXPECTED_TOP_LEVEL = {
    'create', 'createdraft', 'playuniguri', 'deck', 'join', 'end', 'quit',
    'resume', 'gacha', 'gachabox', 'editname', 'summary', 'profilestats',
    'history', 'leaderboard',
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


def run_command(command, cog: GameCog, interaction: FakeInteraction, **kwargs) -> None:
    asyncio.run(command.callback(cog, interaction, **kwargs))


def build_cog() -> GameCog:
    return GameCog.__new__(GameCog)


def test_create_rejects_best_of_without_tcg():
    interaction = FakeInteraction()
    run_command(GameCog.create_game, build_cog(), interaction, best_of=3)
    assert 'best_of' in interaction.response.messages[0]['content']


def test_create_draft_rejects_best_of_without_tcg():
    interaction = FakeInteraction()
    run_command(GameCog.create_draft_game, build_cog(), interaction, boxes=2, best_of=5)
    assert 'best_of' in interaction.response.messages[0]['content']


def test_play_uniguri_rejects_a_guild_channel():
    interaction = FakeInteraction(in_guild=True)
    run_command(GameCog.play_uniguri, build_cog(), interaction, model=SOLO_OPPONENT_ALPHA_ZERO)
    assert 'DM' in interaction.response.messages[0]['content']


def test_play_uniguri_reports_model_a_untrained(monkeypatch):
    import alpha_zero.inference

    monkeypatch.setattr(alpha_zero.inference, 'find_checkpoint', lambda: None)
    interaction = FakeInteraction(in_guild=False)
    run_command(GameCog.play_uniguri, build_cog(), interaction, model=SOLO_OPPONENT_ALPHA_ZERO)
    assert interaction.response.messages[0]['content'] == (
        'Model A has not yet been trained. Play against model B'
    )


def test_play_uniguri_offers_only_the_letters():
    """Players pick a model by letter; the stack behind each letter is an
    implementation detail that must never reach the command picker."""
    command = next(
        command for command in GameCog.group.commands if command.name == 'playuniguri'
    )
    model_parameter = next(
        parameter for parameter in command.parameters if parameter.name == 'model'
    )

    assert [choice.name for choice in model_parameter.choices] == ['A', 'B']

    visible_text = ' '.join([
        command.description,
        model_parameter.description,
        *(choice.name for choice in model_parameter.choices),
    ])
    for stack_name in STACK_NAMES:
        # Whole words only: "opponent" legitimately contains "ppo".
        assert not re.search(rf'\b{stack_name}\b', visible_text, re.IGNORECASE), (
            f'{stack_name!r} is visible in the command picker'
        )
