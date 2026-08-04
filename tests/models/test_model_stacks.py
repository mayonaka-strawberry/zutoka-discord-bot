"""Model-stack correctness: forward shapes, legal-action masking, capacity
padding, checkpoint save/load and migration, and solo-opponent discovery.

Tests over tracked files (model definitions, configs, inference discovery)
run unconditionally; tests touching gitignored training modules use
importorskip so a fresh clone still passes.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

torch = pytest.importorskip('torch')

from engine_alpha.cards import NUM_CARDS  # noqa: E402
from engine_alpha.game import Game  # noqa: E402
from tests.match.support import random_full_pool_decks  # noqa: E402

TINY_ALPHA_ZERO_NET = dict(embed_dim=64, num_layers=2, num_heads=4,
                           feedforward_dim=128, value_hidden_dim=32)
TINY_PPO_NET = dict(embed_dim=64, num_layers=2, num_heads=4,
                    feedforward_dim=128, value_hidden_dim=32)


def _encoded_batch(game):
    import numpy as np

    from engine_alpha.encoding.observation import encode

    tokens_int, tokens_float, globals_row, candidate_positions = encode(game)
    tok_int = torch.from_numpy(tokens_int.astype(np.int64)).unsqueeze(0)
    tok_float = torch.from_numpy(tokens_float).unsqueeze(0)
    token_mask = torch.ones(1, tokens_int.shape[0], dtype=torch.bool)
    global_features = torch.from_numpy(globals_row).unsqueeze(0)
    return tok_int, tok_float, token_mask, global_features, candidate_positions


def _mid_game(seed: int = 3):
    """A game advanced part-way with a guaranteed pending decision."""
    for attempt_seed in range(seed, seed + 20):
        game = Game(seed=attempt_seed, mode='fixed_decks',
                    decks=random_full_pool_decks(attempt_seed))
        rng = random.Random(attempt_seed)
        for _ in range(30):
            if game.is_terminal():
                break
            game.apply(rng.choice(game.legal_actions()))
        if not game.is_terminal():
            return game
    raise AssertionError('no non-terminal mid-game position found')


def test_alpha_zero_forward_shapes_and_priors():
    from alpha_zero.config import NetConfig
    from alpha_zero.net.model import UniguriNet, priors_for_request

    cfg = NetConfig(**TINY_ALPHA_ZERO_NET)
    net = UniguriNet(cfg).eval()
    game = _mid_game()
    tok_int, tok_float, token_mask, global_features, candidate_positions = _encoded_batch(game)

    with torch.no_grad():
        value, pointer, identity, number = net(tok_int, tok_float, token_mask, global_features)
        value_logits, _, _, _ = net(tok_int, tok_float, token_mask, global_features,
                                    return_value_logits=True)

    assert value.shape == (1,)
    assert -1.0 <= float(value) <= 1.0, 'WDL expectation stays in [-1, 1]'
    assert value_logits.shape == (1, 3)
    assert identity.shape == (1, cfg.identity_capacity)

    priors = priors_for_request(
        game.decision_context(), candidate_positions,
        pointer[0], identity[0], number[0])
    legal = game.legal_actions()
    assert priors.shape[0] == len(legal)
    assert abs(float(priors.sum()) - 1.0) < 1e-4


def test_alpha_zero_capacity_below_card_count_is_rejected():
    from alpha_zero.config import NetConfig
    from alpha_zero.net.model import UniguriNet

    with pytest.raises(ValueError, match='identity_capacity'):
        UniguriNet(NetConfig(**TINY_ALPHA_ZERO_NET, identity_capacity=NUM_CARDS - 1))


def test_ppo_forward_shapes_and_legal_masking():
    from ppo_transformer.config import NetConfig
    from ppo_transformer.net.model import (
        PpoPolicyNetwork, head_for_request, legal_action_slots, logits_for_slots,
    )

    cfg = NetConfig(**TINY_PPO_NET)
    net = PpoPolicyNetwork(cfg).eval()
    game = _mid_game(seed=9)
    tok_int, tok_float, token_mask, global_features, candidate_positions = _encoded_batch(game)

    with torch.no_grad():
        value, pointer, identity, number = net(tok_int, tok_float, token_mask, global_features)

    assert value.shape == (1,)
    request = game.decision_context()
    slots = legal_action_slots(request, candidate_positions)
    assert len(slots) == len(game.legal_actions())

    slot_width = len(slots) + 3  # exercise the -1 padding path
    slots_tensor = torch.full((1, slot_width), -1, dtype=torch.long)
    slots_tensor[0, :len(slots)] = torch.tensor(slots)
    head_tensor = torch.tensor([head_for_request(request)])
    legal_logits = logits_for_slots(head_tensor, slots_tensor, pointer, identity, number)
    assert legal_logits.shape == (1, slot_width)
    assert torch.isfinite(legal_logits[0, :len(slots)]).all()
    assert (legal_logits[0, len(slots):] == float('-inf')).all()


def test_checkpoint_round_trip_both_stacks(tmp_path):
    from alpha_zero.config import NetConfig as AlphaZeroNetConfig
    from alpha_zero.net.model import UniguriNet
    from ppo_transformer.config import NetConfig as PpoNetConfig
    from ppo_transformer.net.model import PpoPolicyNetwork

    for name, net in (
        ('alpha_zero', UniguriNet(AlphaZeroNetConfig(**TINY_ALPHA_ZERO_NET))),
        ('ppo', PpoPolicyNetwork(PpoNetConfig(**TINY_PPO_NET))),
    ):
        path = tmp_path / f'{name}.pt'
        torch.save(net.state_dict(), path)
        loaded = torch.load(path, map_location='cpu', weights_only=True)
        net.load_state_dict(loaded)


def test_checkpoint_migration_grows_identity_capacity(tmp_path):
    migrate = pytest.importorskip('model_common.migrate_checkpoint')

    from alpha_zero.config import NetConfig
    from alpha_zero.net.model import UniguriNet

    small = NetConfig(**TINY_ALPHA_ZERO_NET, identity_capacity=NUM_CARDS + 8)
    net = UniguriNet(small)
    original = net.state_dict()
    null_row = original['identity_embedding.weight'][small.identity_capacity].clone()

    grown_capacity = NUM_CARDS + 40
    migrated = migrate.migrate_state_dict(
        original, identity_capacity=grown_capacity,
        trained_identity_rows=NUM_CARDS)

    grown = NetConfig(**TINY_ALPHA_ZERO_NET, identity_capacity=grown_capacity)
    grown_net = UniguriNet(grown)
    grown_net.load_state_dict(migrated)

    grown_weight = migrated['identity_embedding.weight']
    assert grown_weight.shape[0] == grown_capacity + 1
    assert torch.equal(grown_weight[:small.identity_capacity],
                       original['identity_embedding.weight'][:small.identity_capacity])
    assert torch.equal(grown_weight[grown_capacity], null_row)
    assert migrated['identity_bias'].shape[0] == grown_capacity
    with pytest.raises(ValueError, match='only grow'):
        migrate.migrate_state_dict(original, identity_capacity=NUM_CARDS)


def test_solo_opponents_track_deployed_checkpoints():
    """A stack is offered as a solo opponent exactly when its `find_checkpoint`
    locates one. Asserted as a contract rather than "nothing is deployed", so
    the test holds on a machine that has a checkpoint in `model/`."""
    from alpha_zero.inference import find_checkpoint as alpha_zero_checkpoint
    from ppo_transformer.inference import find_checkpoint as ppo_checkpoint
    from zutomayo.match.agents import available_solo_opponents

    available = available_solo_opponents()
    assert ('alphazero' in available) is (alpha_zero_checkpoint() is not None)
    assert ('ppo' in available) is (ppo_checkpoint() is not None)


def test_agent_adapter_submits_agent_action():
    import asyncio

    from engine_alpha.actions import select_card, P_EFFECT_TARGET
    from zutomayo.match.agents.agent_adapter import ModelDecisionAdapter
    from zutomayo.match.decisions import KIND_CARD_CHOICE, MatchDecisionRequest

    submissions = []

    class BrokerStub:
        def submit(self, sequence_number, payload_type, payload):
            submissions.append((sequence_number, payload_type, payload))

    class AgentStub:
        def act(self, game):
            return 1

    class SessionStub:
        game_id = 'TEST-00000'
        game = object()

    request = MatchDecisionRequest(
        kind=KIND_CARD_CHOICE, player_index=1, prompt_text='t',
        engine_request=select_card(P_EFFECT_TARGET, [10, 11, 12]),
        purpose=P_EFFECT_TARGET,
    )
    request.sequence_number = 7
    adapter = ModelDecisionAdapter(AgentStub(), lambda: BrokerStub())
    asyncio.run(adapter.present_decision(SessionStub(), request))
    assert submissions == [(7, 'action', 1)]


def test_agent_adapter_falls_back_on_agent_failure():
    import asyncio

    from engine_alpha.actions import select_card, P_EFFECT_TARGET
    from zutomayo.match.agents.agent_adapter import ModelDecisionAdapter
    from zutomayo.match.decisions import KIND_CARD_CHOICE, MatchDecisionRequest

    submissions = []

    class BrokerStub:
        def submit(self, sequence_number, payload_type, payload):
            submissions.append((sequence_number, payload_type, payload))

    class FailingAgent:
        def act(self, game):
            raise RuntimeError('model exploded')

    class SessionStub:
        game_id = 'TEST-00000'
        game = object()

    request = MatchDecisionRequest(
        kind=KIND_CARD_CHOICE, player_index=1, prompt_text='t',
        engine_request=select_card(P_EFFECT_TARGET, [10, 11], allow_pass=True),
        purpose=P_EFFECT_TARGET,
    )
    request.sequence_number = 3
    adapter = ModelDecisionAdapter(FailingAgent(), lambda: BrokerStub())
    asyncio.run(adapter.present_decision(SessionStub(), request))
    assert submissions == [(3, 'action', 2)], 'fallback is the PASS action'
