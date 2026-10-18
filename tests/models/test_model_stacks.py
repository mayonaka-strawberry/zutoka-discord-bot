"""Model-stack correctness: forward shapes, legal-action masking, capacity
padding, checkpoint save/load and migration, solo-opponent discovery, and MPS
serialization.

Tests over tracked files (model definitions, configs, inference discovery)
run unconditionally; tests touching gitignored training modules use
importorskip so a fresh clone still passes.
"""

from __future__ import annotations

import contextlib
import os
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


def _encoded_padded_batch(games):
    """Several positions collated the way the evaluators do it: padded to the
    longest token sequence, with a mask marking the real tokens."""
    import numpy as np

    from engine_alpha.encoding.observation import encode

    encoded = [encode(game) for game in games]
    longest = max(entry[0].shape[0] for entry in encoded)
    tok_int = torch.zeros(len(games), longest, encoded[0][0].shape[1], dtype=torch.long)
    tok_float = torch.zeros(len(games), longest, encoded[0][1].shape[1])
    token_mask = torch.zeros(len(games), longest, dtype=torch.bool)
    global_features = torch.zeros(len(games), encoded[0][2].shape[0])
    for row, (tokens_int, tokens_float, globals_row, _) in enumerate(encoded):
        token_count = tokens_int.shape[0]
        tok_int[row, :token_count] = torch.from_numpy(tokens_int.astype(np.int64))
        tok_float[row, :token_count] = torch.from_numpy(tokens_float)
        token_mask[row, :token_count] = True
        global_features[row] = torch.from_numpy(globals_row)
    return tok_int, tok_float, token_mask, global_features


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


def _opening(seed: int = 5):
    """A fresh game's first decision: fewer tokens than _mid_game, so batches pad."""
    return Game(seed=seed, mode='fixed_decks', decks=random_full_pool_decks(seed))


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


def test_alpha_zero_hp_margin_is_a_fifth_output_on_request():
    """Callers unpacking four outputs keep working; the margin changes no other output."""
    from alpha_zero.config import NetConfig
    from alpha_zero.net.model import UniguriNet

    net = UniguriNet(NetConfig(**TINY_ALPHA_ZERO_NET)).eval()
    batch = _encoded_padded_batch([_mid_game(3), _opening()])
    with torch.no_grad():
        default = net(*batch)
        with_margin = net(*batch, return_hp_margin=True)
        with_both = net(*batch, return_value_logits=True, return_hp_margin=True)

    assert len(default) == 4
    assert len(with_margin) == len(with_both) == 5
    assert with_margin[4].shape == (2,)
    assert with_both[0].shape == (2, 3) and with_both[4].shape == (2,)
    assert all(torch.equal(plain, margin) for plain, margin in zip(default, with_margin))
    assert torch.equal(with_margin[4], with_both[4])


def test_alpha_zero_gradient_checkpointing_matches_the_plain_forward():
    """Both encoder paths end in the final LayerNorm, so at dropout 0 the
    recomputing path must reproduce every plain output, the HP margin included,
    to within 1e-5 (padding included)."""
    from alpha_zero.config import NetConfig
    from alpha_zero.net.model import UniguriNet

    net = UniguriNet(NetConfig(**TINY_ALPHA_ZERO_NET)).train()
    batch = _encoded_padded_batch([_mid_game(3), _opening()])
    assert not batch[2].all(), 'one row is padded'

    net.gradient_checkpointing = False
    plain = net(*batch, return_value_logits=True, return_hp_margin=True)
    net.gradient_checkpointing = True
    checkpointed = net(*batch, return_value_logits=True, return_hp_margin=True)

    assert len(plain) == len(checkpointed) == 5
    for plain_output, checkpointed_output in zip(plain, checkpointed):
        assert torch.allclose(plain_output, checkpointed_output, atol=1e-5)


def test_alpha_zero_untrained_identity_prior_is_not_near_one_hot():
    """The tied identity logits are scaled by 1/sqrt(identity_embedding_dim);
    unscaled, their init std is about 5.7 and the prior piles onto one random card."""
    from alpha_zero.config import NetConfig
    from alpha_zero.net.model import UniguriNet

    torch.manual_seed(0)
    net = UniguriNet(NetConfig(**TINY_ALPHA_ZERO_NET)).eval()
    tok_int, tok_float, token_mask, global_features, _ = _encoded_batch(_mid_game())
    with torch.no_grad():
        _, _, identity, _ = net(tok_int, tok_float, token_mask, global_features)

    assert float(identity[0, :NUM_CARDS].softmax(dim=-1).max()) < 0.2


def test_playing_state_dict_prefers_the_ema_weights():
    from alpha_zero.net.model import playing_state_dict

    raw = {'weight': torch.zeros(1)}
    ema = {'weight': torch.ones(1)}
    assert playing_state_dict({'model_state_dict': raw, 'ema_state_dict': ema}) is ema
    assert playing_state_dict({'model_state_dict': raw, 'ema_state_dict': None}) is raw
    assert playing_state_dict(raw) is raw, 'a bare state dict is returned unchanged'


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


def test_unavailable_opponent_error_names_the_model_by_letter(monkeypatch):
    """The message reaches the player verbatim through the resume handler, so it
    speaks in letters rather than naming the stack."""
    from zutomayo.match import solo_flow
    from zutomayo.match import agents

    monkeypatch.setattr(agents, 'available_solo_opponents', lambda: [])
    with pytest.raises(ValueError, match='Model A is not available'):
        solo_flow.create_model_adapter(object(), 'alphazero')
    with pytest.raises(ValueError, match='Model B is not available'):
        solo_flow.create_model_adapter(object(), 'ppo')


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


# --- Live AlphaZero agent (alpha_zero/inference.py) ---

@pytest.fixture
def live_alpha_zero(monkeypatch, tmp_path):
    """alpha_zero.inference with an isolated config, an empty network cache,
    CPU inference, and a tiny deployed checkpoint that carries its config."""
    from dataclasses import asdict

    import alpha_zero.config as alpha_config
    import alpha_zero.inference as inference
    import model_common.device as device_module
    from alpha_zero.config import NetConfig
    from alpha_zero.net.model import UniguriNet

    monkeypatch.setattr(alpha_config, 'ENV_FILE', tmp_path / 'absent.env')
    for name in list(os.environ):
        if name.startswith('ALPHA_'):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(inference, '_LOADED_MODELS', {})
    monkeypatch.setattr(device_module, 'select_device', lambda: torch.device('cpu'))
    monkeypatch.setattr(device_module, 'bound_inference_threads',
                        lambda maximum_threads=4: None)

    net_config = NetConfig(**TINY_ALPHA_ZERO_NET)
    net = UniguriNet(net_config)
    checkpoint_path = tmp_path / 'alpha_zero.pt'
    torch.save({'model_state_dict': net.state_dict(),
                'ema_state_dict': net.state_dict(),
                'config': {'net': asdict(net_config)}}, checkpoint_path)
    monkeypatch.setattr(inference, 'find_checkpoint', lambda: checkpoint_path)
    return inference, checkpoint_path


def test_live_agent_never_searches_from_a_stale_tree(live_alpha_zero, monkeypatch):
    """The bot's match driver applies the human's moves without calling
    `observe`. Replaying that call pattern, every search must start from a
    fresh root on a clone of the game, and every move must be legal."""
    inference, _ = live_alpha_zero
    from alpha_zero.mcts import mcts

    real_search = mcts.run_search_batched
    search_calls = []

    def recording_search(game, *arguments, root=None, **keyword_arguments):
        search_calls.append((game, root))
        return real_search(game, *arguments, root=root, **keyword_arguments)

    monkeypatch.setattr(mcts, 'run_search_batched', recording_search)
    agent = inference.AlphaZeroAgent(mode='search', simulations_live=6)
    live_positions = []
    for seed in (4, 8):
        game = Game(seed=seed, mode='fixed_decks', decks=random_full_pool_decks(seed))
        human = random.Random(seed)
        while not game.is_terminal():
            legal = game.legal_actions()
            if game.current_player() == 1:
                live_positions.append(game)
                action = agent.act(game)
                assert action in legal
            else:
                action = human.choice(legal)
            game.apply(action)

    assert len(search_calls) == len(live_positions) > 0
    assert all(root is None for _, root in search_calls)
    assert all(searched is not live
               for (searched, _), live in zip(search_calls, live_positions))


def test_live_agents_share_one_loaded_network(live_alpha_zero):
    """Concurrent games share the cached network, and replacing the deployed
    file is picked up by the next game without growing the cache."""
    inference, checkpoint_path = live_alpha_zero
    first = inference.AlphaZeroAgent(mode='search')
    second = inference.AlphaZeroAgent(mode='search')
    first._ensure_loaded()
    second._ensure_loaded()
    assert first._evaluator is second._evaluator

    status = checkpoint_path.stat()
    os.utime(checkpoint_path, ns=(status.st_atime_ns, status.st_mtime_ns + 1_000_000_000))
    third = inference.AlphaZeroAgent(mode='search')
    third._ensure_loaded()
    assert third._evaluator is not first._evaluator
    assert len(inference._LOADED_MODELS) == 1


def test_live_agent_refuses_bare_weights_of_a_non_default_network(
        live_alpha_zero, monkeypatch, tmp_path):
    inference, _ = live_alpha_zero
    from alpha_zero.config import NetConfig
    from alpha_zero.net.model import UniguriNet

    bare_path = tmp_path / 'bare.pt'
    torch.save(UniguriNet(NetConfig(**TINY_ALPHA_ZERO_NET)).state_dict(), bare_path)
    monkeypatch.setattr(inference, 'find_checkpoint', lambda: bare_path)
    with pytest.raises(ValueError, match='bare weights'):
        inference.AlphaZeroAgent(mode='search')._ensure_loaded()


def test_live_search_settings_survive_an_invalid_training_config(
        live_alpha_zero, monkeypatch, caplog):
    """A training `.env` that fails validation degrades live search to the
    default settings instead of failing every live decision."""
    inference, _ = live_alpha_zero
    import alpha_zero.config as alpha_config

    def invalid_config():
        raise ValueError('ALPHA_NET_EMBED_DIM (100) must be divisible by ALPHA_NET_NUM_HEADS (8)')

    monkeypatch.setattr(alpha_config, 'load_config', invalid_config)
    agent = inference.AlphaZeroAgent(mode='search', simulations_live=4)
    agent._ensure_loaded()
    assert agent._mcts_config == alpha_config.MCTSConfig()
    assert 'did not validate' in caplog.text


def test_live_evaluator_batch_rows_match_single_positions(live_alpha_zero):
    """A padded batch leaves the device as whole tensors; each row must still
    get its own value and priors."""
    import numpy as np

    inference, checkpoint_path = live_alpha_zero
    evaluator = inference._load_evaluator(checkpoint_path)
    games = [_mid_game(3), _opening(), _mid_game(9)]
    assert not _encoded_padded_batch(games)[2].all(), 'one row is padded'
    for game, (value, priors) in zip(games, evaluator.batch(games)):
        single_value, single_priors = evaluator(game)
        assert abs(value - single_value) < 1e-5
        assert priors.shape == single_priors.shape == (len(game.legal_actions()),)
        assert np.allclose(priors, single_priors, atol=1e-5)


def test_live_load_times_out_behind_a_held_cache_lock(live_alpha_zero, monkeypatch):
    """The cache lock's holder may be waiting out the MPS lock, so a queued
    loader gives up on its own instead of stacking its wait behind it."""
    import threading

    import model_common.device as device_module

    inference, checkpoint_path = live_alpha_zero
    monkeypatch.setattr(device_module, 'MPS_LOCK_TIMEOUT_SECONDS', 0.05)
    holding, release = threading.Event(), threading.Event()

    def hold_the_cache_lock():
        with inference._LOADED_MODELS_LOCK:
            holding.set()
            release.wait(timeout=10)

    holder = threading.Thread(target=hold_the_cache_lock)
    holder.start()
    try:
        assert holding.wait(timeout=10)
        with pytest.raises(TimeoutError):
            inference._load_evaluator(checkpoint_path)
        assert inference._LOADED_MODELS_LOCK.locked(), "the holder's lock is left alone"
    finally:
        release.set()
        holder.join(timeout=10)


# --- Live PPO agent (ppo_transformer/inference.py) ---

@pytest.fixture
def live_ppo(monkeypatch, tmp_path):
    """ppo_transformer.inference with CPU inference and a tiny deployed
    checkpoint that carries its config."""
    from dataclasses import asdict

    import model_common.device as device_module
    import ppo_transformer.inference as inference
    from ppo_transformer.config import NetConfig
    from ppo_transformer.net.model import PpoPolicyNetwork

    monkeypatch.setattr(device_module, 'select_device', lambda: torch.device('cpu'))
    monkeypatch.setattr(device_module, 'bound_inference_threads',
                        lambda maximum_threads=4: None)

    net_config = NetConfig(**TINY_PPO_NET)
    checkpoint_path = tmp_path / 'ppo.pt'
    torch.save({'model_state_dict': PpoPolicyNetwork(net_config).state_dict(),
                'config': {'net': asdict(net_config)}}, checkpoint_path)
    monkeypatch.setattr(inference, 'find_checkpoint', lambda: checkpoint_path)
    return inference, checkpoint_path


def test_live_ppo_agent_plays_legal_actions_with_the_deployed_weights(live_ppo):
    """Asked at every seat-1 decision of two seeded random games, which reach all
    three heads. The tiny checkpoint does not fit the default NetConfig, so loading
    must use the checkpoint's config."""
    from ppo_transformer.net.model import (
        HEAD_IDENTITY, HEAD_NUMBER, HEAD_POINTER, head_for_request,
    )

    inference, checkpoint_path = live_ppo
    agent = inference.PpoAgent()
    heads = set()
    for seed in (0, 21):
        game = Game(seed=seed, mode='fixed_decks', decks=random_full_pool_decks(seed))
        rng = random.Random(seed)
        while not game.is_terminal():
            legal = game.legal_actions()
            if game.current_player() == 1:
                heads.add(head_for_request(game.decision_context()))
                assert agent.act(game) in legal
            game.apply(rng.choice(legal))

    assert heads == {HEAD_POINTER, HEAD_IDENTITY, HEAD_NUMBER}
    deployed = torch.load(checkpoint_path, weights_only=True)['model_state_dict']
    assert all(torch.equal(tensor, deployed[name])
               for name, tensor in agent._net.state_dict().items())


# --- MPS serialization (model_common/device.py) ---

def _another_thread_enters(device) -> bool:
    """Whether another thread gets through serialized_on_mps(device) right now."""
    import threading

    from model_common.device import serialized_on_mps

    entered = []

    def enter():
        try:
            with serialized_on_mps(device):
                entered.append(True)
        except TimeoutError:
            entered.append(False)

    thread = threading.Thread(target=enter)
    thread.start()
    thread.join(timeout=10)
    return entered == [True]


def test_serialized_on_mps_locks_out_other_threads_on_mps_only(monkeypatch):
    """Two threads issuing MPS ops at once crash the process (PyTorch issue
    #197805). torch.device('mps') needs no MPS hardware to construct."""
    import model_common.device as device_module

    monkeypatch.setattr(device_module, 'MPS_LOCK_TIMEOUT_SECONDS', 0.05)
    with device_module.serialized_on_mps(torch.device('mps')):
        assert not _another_thread_enters(torch.device('mps'))
        assert _another_thread_enters('cpu'), 'CPU work never waits on the lock'
    with device_module.serialized_on_mps(torch.device('cpu')):
        assert _another_thread_enters('mps'), 'CPU work takes no lock'
    assert _another_thread_enters('mps'), 'the lock is released on exit'


def test_serialized_on_mps_times_out_below_the_solo_watchdog(monkeypatch):
    """A thread queued behind a stuck one gives up first, so the adapter plays its
    fallback move instead of threads piling up."""
    import threading

    import model_common.device as device_module
    from zutomayo.match.agents.agent_adapter import AGENT_TIMEOUT_SECONDS

    assert device_module.MPS_LOCK_TIMEOUT_SECONDS < AGENT_TIMEOUT_SECONDS
    monkeypatch.setattr(device_module, 'MPS_LOCK_TIMEOUT_SECONDS', 0.05)
    holding, release = threading.Event(), threading.Event()

    def hold_the_lock():
        with device_module.serialized_on_mps('mps'):
            holding.set()
            release.wait(timeout=10)

    holder = threading.Thread(target=hold_the_lock)
    holder.start()
    try:
        assert holding.wait(timeout=10)
        with pytest.raises(TimeoutError):
            with device_module.serialized_on_mps(torch.device('mps')):
                pass
    finally:
        release.set()
        holder.join(timeout=10)


def test_serialized_on_mps_releases_the_lock_when_the_work_raises(monkeypatch):
    """A failed decision must not keep the lock, or every later MPS decision
    would time out."""
    import model_common.device as device_module

    monkeypatch.setattr(device_module, 'MPS_LOCK_TIMEOUT_SECONDS', 0.05)
    with pytest.raises(RuntimeError, match='forward failed'):
        with device_module.serialized_on_mps(torch.device('mps')):
            raise RuntimeError('forward failed')
    assert _another_thread_enters(torch.device('mps'))


class _RecordingGuard:
    """Stands in for serialized_on_mps; records each wrapped call with whether a
    guard was open."""

    def __init__(self) -> None:
        self.open_guards = 0
        self.calls = []

    @contextlib.contextmanager
    def guard(self, device):
        self.open_guards += 1
        try:
            yield
        finally:
            self.open_guards -= 1

    def record(self, monkeypatch, owner, name: str) -> None:
        original = getattr(owner, name)

        def recording(*arguments, **keyword_arguments):
            self.calls.append((name, self.open_guards > 0))
            return original(*arguments, **keyword_arguments)

        monkeypatch.setattr(owner, name, recording)


@pytest.mark.parametrize('stack', ['alpha_zero', 'ppo'])
def test_live_agents_do_their_device_work_inside_the_mps_guard(stack, request, monkeypatch):
    """Loading moves the network, and every forward runs, inside serialized_on_mps."""
    import model_common.device as device_module

    if stack == 'alpha_zero':
        from alpha_zero.net.model import UniguriNet as network_class
        inference, _ = request.getfixturevalue('live_alpha_zero')
        agents = [inference.AlphaZeroAgent(mode='search', simulations_live=6),
                  inference.AlphaZeroAgent(mode='policy')]
    else:
        from ppo_transformer.net.model import PpoPolicyNetwork as network_class
        inference, _ = request.getfixturevalue('live_ppo')
        agents = [inference.PpoAgent()]
    recorder = _RecordingGuard()
    monkeypatch.setattr(device_module, 'serialized_on_mps', recorder.guard)
    for name in ('to', 'forward'):
        recorder.record(monkeypatch, network_class, name)

    for agent in agents:
        for game in (_mid_game(3), _opening()):
            assert agent.act(game) in game.legal_actions()

    assert {name for name, _ in recorder.calls} == {'to', 'forward'}
    assert [name for name, guarded in recorder.calls if not guarded] == []
