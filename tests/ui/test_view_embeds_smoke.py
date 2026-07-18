"""Smoke tests for the view-based embed builders and the PIL board renderer,
driven by real BoardView projections from engine_alpha games."""

from __future__ import annotations

import random
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from engine_alpha.game import Game  # noqa: E402
from zutomayo.match.state_view import project_board_view  # noqa: E402
from tests.match.support import random_full_pool_decks  # noqa: E402

PLAYER_NAMES = {0: 'Alpha', 1: 'Beta'}


def _played_board_view(seed: int = 5, steps: int = 60):
    game = Game(seed=seed, mode='fixed_decks', decks=random_full_pool_decks(seed))
    rng = random.Random(seed)
    for _ in range(steps):
        if game.is_terminal():
            break
        game.apply(rng.choice(game.legal_actions()))
    return project_board_view(game, PLAYER_NAMES)


def test_field_embed_from_board_view():
    from zutomayo.ui.embeds import build_field_embed_from_board_view

    board_view = _played_board_view()
    embed = build_field_embed_from_board_view(board_view)
    assert f'TURN {board_view.turn}' in embed.title
    assert any('Alpha' in field.name for field in embed.fields)
    assert 'Phase:' in embed.footer.text


def test_hand_embed_and_card_descriptions():
    from zutomayo.ui.embeds import build_hand_embed, card_detail_description

    board_view = _played_board_view()
    player_view = board_view.players[0]
    embed = build_hand_embed(player_view)
    assert embed.title == 'Your Hand'
    if player_view.hand:
        first = card_detail_description(player_view.hand[0])
        assert player_view.hand[0].card.name in first


def test_battle_and_game_over_embeds():
    from zutomayo.ui.embeds import (
        build_battle_result_embed_from_board_view,
        build_game_over_embed_from_board_view,
    )

    board_view = _played_board_view()
    battle_embed = build_battle_result_embed_from_board_view(
        {'player_0_attack': 30, 'player_1_attack': 10, 'winner': 0, 'damage': 20},
        board_view,
    )
    assert 'WON' in battle_embed.title
    assert battle_embed.fields[0].name == 'Alpha'

    game_over = build_game_over_embed_from_board_view(board_view)
    assert 'GAME COMPLETE' in game_over.title


def test_board_renderer_and_images_accept_views():
    from zutomayo.enums.chronos import Chronos
    from zutomayo.ui.board_renderer import generate_zone_messages, render_board_image
    from zutomayo.ui.embeds import create_deck_grid_image, create_hand_image

    board_view = _played_board_view()
    board_file = render_board_image(board_view, Chronos.DAY)
    assert board_file is not None
    night_file = render_board_image(board_view, Chronos.NIGHT)
    assert night_file is not None

    zone_messages = generate_zone_messages(board_view, PLAYER_NAMES)
    assert zone_messages, 'zone messages should list abyss and charger zones'

    player_view = board_view.players[0]
    if player_view.hand:
        assert create_hand_image(list(player_view.hand)) is not None
    assert create_deck_grid_image(list(player_view.deck)[:10]) is not None


def test_effect_resolution_embed_accepts_card_views():
    from zutomayo.ui.embeds import build_effect_resolution_embed

    board_view = _played_board_view()
    holders = list(board_view.players[0].abyss)[:2]
    embed = build_effect_resolution_embed('Alpha', holders, holders[:1])
    assert 'Effect Resolution' in embed.title
