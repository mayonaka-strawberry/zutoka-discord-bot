"""State projection: BoardView must mirror engine state exactly."""

from __future__ import annotations

import random

from engine_alpha import cards
from engine_alpha.battle import total_power
from engine_alpha.game import Game
from zutomayo.match.state_view import definition_index_to_card, project_board_view
from tests.match.support import random_full_pool_decks

PLAYER_NAMES = {0: 'Alpha', 1: 'Beta'}


def test_definition_table_covers_every_card():
    for definition in cards.CARD_DB:
        card = definition_index_to_card(definition.index)
        assert f'{card.pack:02d}-{card.id:03d}' == definition.key


def test_projection_matches_engine_state_over_random_games():
    for seed in range(5):
        decks = random_full_pool_decks(seed)
        game = Game(seed=seed, mode='fixed_decks', decks=decks)
        rng = random.Random(seed)
        steps = 0
        while not game.is_terminal() and steps < 400:
            board_view = project_board_view(game, PLAYER_NAMES)
            state = game.state
            assert board_view.turn == state.turn
            assert board_view.chronos == state.chronos
            assert board_view.winner == state.winner
            for player_index in (0, 1):
                player = state.players[player_index]
                view = board_view.players[player_index]
                assert view.name == PLAYER_NAMES[player_index]
                assert view.hp == player.hp
                assert view.total_power == total_power(state, player)
                assert [v.instance_id for v in view.hand] == list(player.hand)
                assert [v.instance_id for v in view.abyss] == list(player.abyss)
                assert [v.instance_id for v in view.power_charger] == list(player.charger)
                assert view.deck_count == len(player.deck)
                for zone_view, instance_id in (
                    (view.battle_zone, player.battle),
                    (view.set_zone_a, player.set_a),
                    (view.set_zone_b, player.set_b),
                    (view.set_zone_c, player.set_c),
                ):
                    if instance_id == -1:
                        assert zone_view is None
                    else:
                        assert zone_view.instance_id == instance_id
                        assert zone_view.face_up == bool(state.inst_face_up[instance_id])
                        expected = definition_index_to_card(state.inst_def[instance_id])
                        assert zone_view.card is expected
            game.apply(rng.choice(game.legal_actions()))
            steps += 1
