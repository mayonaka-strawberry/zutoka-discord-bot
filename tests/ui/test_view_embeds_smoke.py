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


def _assert_sendable_jpeg(discord_file, label: str):
    """Every image the bot uploads must be a real JPEG named .jpg, and still sendable.

    Asserting the decoded format rather than just non-None is what catches a filename that
    was missed during a rename, and asserting mode RGB is what catches a paste that dropped
    its mask and left the rounded corners opaque.
    """
    import io

    from PIL import Image

    assert discord_file is not None, f'{label} should have rendered'
    assert discord_file.filename.endswith('.jpg'), (
        f'{label} attachment is named {discord_file.filename!r}'
    )

    discord_file.fp.seek(0)
    image = Image.open(io.BytesIO(discord_file.fp.getvalue()))
    image.load()
    assert image.format == 'JPEG', f'{label} is {image.format}, not JPEG'
    assert image.mode == 'RGB', f'{label} is mode {image.mode}, not RGB'
    discord_file.fp.seek(0)


def test_board_renderer_and_images_accept_views():
    from zutomayo.enums.chronos import Chronos
    from zutomayo.ui.board_renderer import generate_zone_messages, render_board_image
    from zutomayo.ui.embeds import create_deck_grid_image, create_hand_image

    board_view = _played_board_view()
    _assert_sendable_jpeg(render_board_image(board_view, Chronos.DAY), 'board (day)')
    _assert_sendable_jpeg(render_board_image(board_view, Chronos.NIGHT), 'board (night)')

    zone_messages = generate_zone_messages(board_view, PLAYER_NAMES)
    assert zone_messages, 'zone messages should list abyss and charger zones'
    assert [label for label, _ in zone_messages] == [
        'Alpha Abyss', 'Alpha Power Charger', 'Beta Abyss', 'Beta Power Charger',
    ]
    for label, zone_file in zone_messages:
        if zone_file is not None:  # an empty zone renders nothing
            _assert_sendable_jpeg(zone_file, label)

    # A caller re-sending only what changed never composes the other zones.
    selected = generate_zone_messages(board_view, PLAYER_NAMES, indices={0, 3})
    assert [label for label, _ in selected] == ['Alpha Abyss', 'Beta Power Charger']

    player_view = board_view.players[0]
    if player_view.hand:
        _assert_sendable_jpeg(create_hand_image(list(player_view.hand)), 'hand')
    _assert_sendable_jpeg(create_deck_grid_image(list(player_view.deck)[:10]), 'deck grid')


def test_effect_resolution_embed_accepts_card_views():
    from zutomayo.ui.embeds import build_effect_resolution_embed

    board_view = _played_board_view()
    holders = list(board_view.players[0].abyss)[:2]
    embed = build_effect_resolution_embed('Alpha', holders, holders[:1])
    assert 'Effect Resolution' in embed.title
