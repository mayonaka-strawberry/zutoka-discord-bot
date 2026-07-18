"""
Deployment inference for the AlphaZero opponent.

Loads the newest deployable checkpoint, selects the runtime device (CUDA,
then Apple Silicon MPS, then CPU), and answers one decision at a time:

- ``policy`` mode (default): a single forward pass, masked to the legal
  actions - effectively instant on CPU.
- ``search`` mode: batched-leaf MCTS with a reduced live simulation budget.

Checkpoint discovery looks for ``alpha_zero/deploy/model.pt`` first (the
deliberate deployment location), then falls back to the newest training
checkpoint under ``alpha_zero/runs/checkpoints/``.

Self-contained over the tracked modules (net/model.py, mcts/, config.py and
the engine); it must keep working on a clone that carries no training code.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

PACKAGE_ROOT = Path(__file__).resolve().parent
DEPLOY_PATH = PACKAGE_ROOT / 'deploy' / 'model.pt'
CHECKPOINT_DIRECTORY = PACKAGE_ROOT / 'runs' / 'checkpoints'

MODE_POLICY = 'policy'
MODE_SEARCH = 'search'
LIVE_SIMULATIONS = 64


def find_checkpoint() -> Optional[Path]:
    """The checkpoint the bot would play with, or None when untrained."""
    if DEPLOY_PATH.exists():
        return DEPLOY_PATH
    if CHECKPOINT_DIRECTORY.exists():
        checkpoints = sorted(CHECKPOINT_DIRECTORY.glob('step_*.pt'))
        if checkpoints:
            return checkpoints[-1]
    return None


class _Evaluator:
    """Evaluates games for MCTS and policy play: (value, priors) per game."""

    def __init__(self, net, device) -> None:
        self.net = net
        self.device = device

    def batch(self, games: list) -> list:
        import numpy as np
        import torch

        from engine_alpha.encoding.observation import encode
        from .net.model import priors_for_request

        encoded = [encode(game) for game in games]
        max_tokens = max(entry[0].shape[0] for entry in encoded)
        batch_size = len(games)
        tok_int = torch.zeros(batch_size, max_tokens, encoded[0][0].shape[1], dtype=torch.long)
        tok_float = torch.zeros(batch_size, max_tokens, encoded[0][1].shape[1])
        token_mask = torch.zeros(batch_size, max_tokens, dtype=torch.bool)
        global_features = torch.zeros(batch_size, encoded[0][2].shape[0])
        for row, (tokens_int, tokens_float, globals_row, _) in enumerate(encoded):
            token_count = tokens_int.shape[0]
            tok_int[row, :token_count] = torch.from_numpy(tokens_int.astype(np.int64))
            tok_float[row, :token_count] = torch.from_numpy(tokens_float)
            token_mask[row, :token_count] = True
            global_features[row] = torch.from_numpy(globals_row)
        with torch.no_grad():
            value, pointer, identity, number = self.net(
                tok_int.to(self.device), tok_float.to(self.device),
                token_mask.to(self.device), global_features.to(self.device))
        results = []
        for row, game in enumerate(games):
            priors = priors_for_request(
                game.decision_context(), encoded[row][3],
                pointer[row].float().cpu(), identity[row].float().cpu(),
                number[row].float().cpu())
            results.append((float(value[row]), priors.numpy()))
        return results

    def __call__(self, game):
        return self.batch([game])[0]


class AlphaZeroAgent:
    """act(game) -> engine action int. Loads lazily so importing this module
    never requires torch or a checkpoint."""

    def __init__(self, mode: str = MODE_POLICY,
                 simulations_live: int = LIVE_SIMULATIONS) -> None:
        self.mode = mode
        self.simulations_live = simulations_live
        self._evaluator: Optional[_Evaluator] = None
        self._mcts_config = None
        self._search_root = None

    def _ensure_loaded(self) -> None:
        if self._evaluator is not None:
            return
        import torch

        from model_common.device import bound_inference_threads, select_device
        from .config import MCTSConfig, NetConfig
        from .net.model import UniguriNet

        checkpoint_path = find_checkpoint()
        if checkpoint_path is None:
            raise ValueError('No AlphaZero checkpoint is deployed.')
        payload = torch.load(checkpoint_path, map_location='cpu', weights_only=False)
        if isinstance(payload, dict) and 'model_state_dict' in payload:
            state_dict = payload.get('ema_state_dict') or payload['model_state_dict']
            net_config = NetConfig(**payload['config']['net']) if 'config' in payload else NetConfig()
        else:
            state_dict = payload
            net_config = NetConfig()

        net = UniguriNet(net_config)
        net.load_state_dict(state_dict)
        device = select_device()
        if device.type == 'cpu':
            bound_inference_threads()
        net = net.to(device).eval()
        for parameter in net.parameters():
            parameter.requires_grad_(False)

        self._evaluator = _Evaluator(net, device)
        self._mcts_config = MCTSConfig()
        log.info('AlphaZero agent loaded from %s on %s (%s mode)',
                 checkpoint_path, device, self.mode)

    def reset(self) -> None:
        self._search_root = None

    def observe(self, action: int) -> None:
        if self.mode == MODE_SEARCH and self._search_root is not None:
            from .mcts.mcts import reuse_subtree

            self._search_root = reuse_subtree(self._search_root, action)

    def act(self, game) -> int:
        self._ensure_loaded()
        if self.mode == MODE_SEARCH:
            import random

            from .mcts.mcts import reuse_subtree, run_search_batched, select_action

            root = run_search_batched(
                game, self._evaluator.batch, self._mcts_config,
                self.simulations_live, root=self._search_root, noise_rng=None)
            action = select_action(root, temperature=0.0, rng=random.Random(0))
            self._search_root = reuse_subtree(root, action)
            return action

        _, priors = self._evaluator(game)
        legal = game.legal_actions()
        return legal[int(priors.argmax())]
