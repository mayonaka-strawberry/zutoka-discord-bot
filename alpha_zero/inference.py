"""
Deployment inference for the AlphaZero opponent (model A).

Loads the deployed checkpoint (model/alpha_zero, then alpha_zero/deploy/model.pt,
then the newest runs/checkpoints/step_*.pt) on CUDA, MPS or CPU, and answers one
decision at a time:
- ``search`` (default): batched-leaf MCTS at the live budget. Training and gating
  select for strength with search, so raw policy play is much weaker.
- ``policy``: one masked forward pass, for when latency matters more.
ALPHA_LIVE_MODE and ALPHA_LIVE_SIMULATIONS override the default mode and budget.

Each decision searches a fresh tree on a clone: the driver never reports the
human's moves (it never calls ``observe``), so a kept tree would be stale. The
network is cached per process, keyed by path, modification time and size, so a new
file in model/ is used by the next game.

Must run on a clone without training code: imports only net/model.py, mcts/,
config.py, tracked model_common modules and the engine.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Optional

from model_common.deployed_model import resolve_deployed_checkpoint

log = logging.getLogger(__name__)

DEPLOYED_MODEL_NAME = 'alpha_zero'
PACKAGE_ROOT = Path(__file__).resolve().parent
DEPLOY_PATH = PACKAGE_ROOT / 'deploy' / 'model.pt'
CHECKPOINT_DIRECTORY = PACKAGE_ROOT / 'runs' / 'checkpoints'

MODE_POLICY = 'policy'
MODE_SEARCH = 'search'
LIVE_SIMULATIONS = 64

# Deployed model name -> (checkpoint fingerprint, evaluator); a changed checkpoint
# replaces its entry, so the cache cannot grow without bound.
_LOADED_MODELS: dict[str, tuple[tuple, '_Evaluator']] = {}
_LOADED_MODELS_LOCK = threading.Lock()


def find_checkpoint() -> Optional[Path]:
    """The checkpoint the bot would play with, or None when untrained."""
    deployed = resolve_deployed_checkpoint(DEPLOYED_MODEL_NAME)
    if deployed is not None:
        return deployed
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


def _checkpoint_fingerprint(checkpoint_path: Path) -> tuple:
    status = checkpoint_path.stat()
    return (str(checkpoint_path.resolve()), status.st_mtime_ns, status.st_size)


def _build_evaluator(checkpoint_path: Path) -> _Evaluator:
    import torch

    from model_common.device import bound_inference_threads, select_device
    from .config import NetConfig
    from .net.model import UniguriNet, playing_state_dict

    payload = torch.load(checkpoint_path, map_location='cpu', weights_only=True)
    carries_config = isinstance(payload, dict) and 'config' in payload
    net_config = NetConfig(**payload['config']['net']) if carries_config else NetConfig()
    net = UniguriNet(net_config)
    try:
        net.load_state_dict(playing_state_dict(payload))
    except RuntimeError as error:
        if carries_config:
            raise
        raise ValueError(
            f'{checkpoint_path} holds bare weights that do not fit the default network '
            'shape. Deploy a training checkpoint, or any file that carries its "config", '
            'so the network is built at the size it was trained at.') from error
    device = select_device()
    if device.type == 'cpu':
        bound_inference_threads()
    net = net.to(device).eval()
    for parameter in net.parameters():
        parameter.requires_grad_(False)
    return _Evaluator(net, device)


def _load_evaluator(checkpoint_path: Path) -> _Evaluator:
    """The shared evaluator for `checkpoint_path`: loaded on first use, and
    again only when the file on disk has changed since it was cached."""
    fingerprint = _checkpoint_fingerprint(checkpoint_path)
    with _LOADED_MODELS_LOCK:
        cached = _LOADED_MODELS.get(DEPLOYED_MODEL_NAME)
        if cached is not None and cached[0] == fingerprint:
            return cached[1]
        evaluator = _build_evaluator(checkpoint_path)
        _LOADED_MODELS[DEPLOYED_MODEL_NAME] = (fingerprint, evaluator)
        log.info('AlphaZero network loaded from %s on %s', checkpoint_path, evaluator.device)
        return evaluator


def _live_search_config():
    """MCTS settings for live play. A training `.env` that fails validation
    must not take live games down with it, so it degrades to the defaults."""
    from .config import MCTSConfig, load_config

    try:
        return load_config().mcts
    except ValueError as error:
        log.warning('alpha_zero config did not validate (%s); live search uses the '
                    'default MCTS settings', error)
        return MCTSConfig()


class AlphaZeroAgent:
    """act(game) -> engine action int. Loads lazily so importing this module
    never requires torch or a checkpoint. Holds no per-game search state, so
    any position can be asked about in any order."""

    def __init__(self, mode: Optional[str] = None,
                 simulations_live: Optional[int] = None) -> None:
        from .config import env_setting

        # env_setting is stdlib-only and tolerates a missing .env, so this is
        # safe on a production host that carries no training config.
        self.mode = mode or env_setting('live_mode', MODE_SEARCH, str)
        self.simulations_live = (
            simulations_live
            if simulations_live is not None
            else env_setting('live_simulations', LIVE_SIMULATIONS, int))
        self._evaluator: Optional[_Evaluator] = None
        self._mcts_config = None

    def _ensure_loaded(self) -> None:
        if self._evaluator is not None:
            return
        checkpoint_path = find_checkpoint()
        if checkpoint_path is None:
            raise ValueError('No AlphaZero checkpoint is deployed.')
        self._evaluator = _load_evaluator(checkpoint_path)
        self._mcts_config = _live_search_config()
        log.info('AlphaZero agent ready (%s mode, %d simulations)',
                 self.mode, self.simulations_live)

    def reset(self) -> None:
        """Nothing to clear between games; kept for the arena agent protocol."""

    def observe(self, action: int) -> None:
        """No tree is kept between decisions, so there is nothing to advance;
        kept for the arena agent protocol."""

    def act(self, game) -> int:
        self._ensure_loaded()
        position = game.clone()
        if self.mode == MODE_SEARCH:
            import random

            from .mcts.mcts import run_search_batched, select_action

            root = run_search_batched(
                position, self._evaluator.batch, self._mcts_config,
                self.simulations_live, root=None, noise_rng=None)
            return select_action(root, temperature=0.0, rng=random.Random(0),
                                 use_gumbel=self._mcts_config.use_gumbel_root)

        _, priors = self._evaluator(position)
        legal = position.legal_actions()
        return legal[int(priors.argmax())]
