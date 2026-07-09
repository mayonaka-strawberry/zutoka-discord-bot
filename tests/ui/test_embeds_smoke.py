"""Content tests for embed builders and smoke tests for the image renderers.
Interaction callbacks are deliberately untested here (manual playtests own
that surface); these tests pin the embed text and prove the renderers run."""

from __future__ import annotations

import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

import discord  # noqa: E402

from zutomayo.enums.chronos import Chronos  # noqa: E402

from tests.support.game_state_builder import GameStateBuilder, card_by_identity  # noqa: E402

PLAYER_NAMES = {0: 'PlayerZero', 1: 'PlayerOne'}


def _state():
    return (GameStateBuilder()
            .with_battle_card(0, '01-009')
            .with_battle_card(1, '01-010')
            .with_hand(0, ['01-001', '01-002'])
            .with_abyss(1, ['01-004'])
            .with_chronos(4)
            .build())


class TestEmbedBuilders:
    def test_field_embed_contains_both_players(self):
        from zutomayo.ui.embeds import build_field_embed

        embed = build_field_embed(_state(), PLAYER_NAMES)
        assert isinstance(embed, discord.Embed)
        text = str(embed.to_dict())
        assert 'PlayerZero' in text and 'PlayerOne' in text

    def test_hand_embed_lists_the_cards(self):
        from zutomayo.ui.embeds import build_hand_embed

        state = _state()
        embed = build_hand_embed(state.players[0])
        text = str(embed.to_dict())
        assert card_by_identity('01-001').name in text

    def test_battle_result_embed(self):
        from zutomayo.ui.embeds import build_battle_result_embed

        battle_result = {
            'player_0_attack': 60, 'player_1_attack': 50,
            'damage_to_0': 0, 'damage_to_1': 10, 'winner': 0,
        }
        embed = build_battle_result_embed(battle_result, _state(), PLAYER_NAMES)
        assert isinstance(embed, discord.Embed)

    def test_game_over_embed(self):
        from zutomayo.enums.result import Result
        from zutomayo.ui.embeds import build_game_over_embed

        state = _state()
        state.result = Result.PLAYER_1_WIN
        embed = build_game_over_embed(state, PLAYER_NAMES)
        assert 'PlayerZero' in str(embed.to_dict())


class TestRendererSmoke:
    def test_render_board_image_produces_a_discord_file(self):
        from zutomayo.ui.board_renderer import render_board_image

        board_file = render_board_image(_state(), Chronos.DAY)
        assert isinstance(board_file, discord.File)

    def test_create_deck_grid_image(self):
        from zutomayo.ui.embeds import create_deck_grid_image

        grid = create_deck_grid_image([card_by_identity('01-001'), card_by_identity('01-002')])
        assert isinstance(grid, discord.File)
        assert create_deck_grid_image([]) is None

    def test_generate_zone_messages_covers_all_four_zones(self):
        from zutomayo.ui.board_renderer import generate_zone_messages

        messages = generate_zone_messages(_state(), PLAYER_NAMES)
        labels = [label for label, _ in messages]
        assert labels == [
            'PlayerZero Abyss', 'PlayerZero Power Charger',
            'PlayerOne Abyss', 'PlayerOne Power Charger',
        ]