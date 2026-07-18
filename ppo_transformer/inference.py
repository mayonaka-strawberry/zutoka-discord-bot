"""
Deployment inference for the PPO opponent: one forward pass per decision,
masked to the legal actions, on the runtime-selected device (CUDA, then
Apple Silicon MPS, then CPU).

Checkpoint discovery looks for ``ppo_transformer/deploy/model.pt`` first,
then the newest training checkpoint under ``ppo_transformer/runs``.
Self-contained over the tracked modules; it must keep working on a clone
that carries no training code.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

PACKAGE_ROOT = Path(__file__).resolve().parent
DEPLOY_PATH = PACKAGE_ROOT / 'deploy' / 'model.pt'
CHECKPOINT_DIRECTORY = PACKAGE_ROOT / 'runs' / 'checkpoints'
LATEST_WEIGHTS = PACKAGE_ROOT / 'runs' / 'latest_weights.pt'


def find_checkpoint() -> Optional[Path]:
    """The checkpoint the bot would play with, or None when untrained."""
    if DEPLOY_PATH.exists():
        return DEPLOY_PATH
    if LATEST_WEIGHTS.exists():
        return LATEST_WEIGHTS
    if CHECKPOINT_DIRECTORY.exists():
        checkpoints = sorted(CHECKPOINT_DIRECTORY.glob('iteration_*.pt'))
        if checkpoints:
            return checkpoints[-1]
    return None


class PpoAgent:
    """act(game) -> engine action int. Loads lazily so importing this module
    never requires torch or a checkpoint."""

    def __init__(self) -> None:
        self._net = None
        self._device = None

    def _ensure_loaded(self) -> None:
        if self._net is not None:
            return
        import torch

        from model_common.device import bound_inference_threads, select_device
        from .config import NetConfig
        from .net.model import PpoPolicyNetwork

        checkpoint_path = find_checkpoint()
        if checkpoint_path is None:
            raise ValueError('No PPO checkpoint is deployed.')
        payload = torch.load(checkpoint_path, map_location='cpu', weights_only=False)
        if isinstance(payload, dict) and 'model_state_dict' in payload:
            state_dict = payload['model_state_dict']
            net_config = NetConfig(**payload['config']['net']) if 'config' in payload else NetConfig()
        else:
            state_dict = payload
            net_config = NetConfig()

        net = PpoPolicyNetwork(net_config)
        net.load_state_dict(state_dict)
        device = select_device()
        if device.type == 'cpu':
            bound_inference_threads()
        net = net.to(device).eval()
        for parameter in net.parameters():
            parameter.requires_grad_(False)
        self._net = net
        self._device = device
        log.info('PPO agent loaded from %s on %s', checkpoint_path, device)

    def reset(self) -> None:
        return None

    def act(self, game) -> int:
        self._ensure_loaded()
        import numpy as np
        import torch

        from engine_alpha.encoding.observation import encode
        from .net.model import head_for_request, legal_action_slots, logits_for_slots

        tokens_int, tokens_float, globals_row, candidate_positions = encode(game)
        tok_int = torch.from_numpy(tokens_int.astype(np.int64)).unsqueeze(0).to(self._device)
        tok_float = torch.from_numpy(tokens_float).unsqueeze(0).to(self._device)
        token_mask = torch.ones(1, tokens_int.shape[0], dtype=torch.bool, device=self._device)
        global_features = torch.from_numpy(globals_row).unsqueeze(0).to(self._device)
        with torch.no_grad():
            _, pointer_scores, identity_logits, number_logits = self._net(
                tok_int, tok_float, token_mask, global_features)

        request = game.decision_context()
        slots = legal_action_slots(request, candidate_positions)
        slots_tensor = torch.tensor([slots], dtype=torch.long, device=self._device)
        head_tensor = torch.tensor([head_for_request(request)], dtype=torch.long,
                                   device=self._device)
        legal_logits = logits_for_slots(
            head_tensor, slots_tensor, pointer_scores, identity_logits, number_logits)
        return game.legal_actions()[int(legal_logits[0].argmax())]
