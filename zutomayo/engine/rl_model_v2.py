"""
V2 policy network for the UNIGURI bot.

Same transformer + pointer architecture as rl_model.py, but:
  - Imports observation constants from uniguri_env_v2 (not broken headless_game_env).
  - global_encoder input is GLOBAL_FEATURES_V2 = 79 (adds 2 opponent bonus fields).
  - Checkpoints saved to models_trained_v2/ via save_checkpoint_v2 / load_checkpoint_v2.
  - No average_strategy_head - PPO only.
"""

from __future__ import annotations
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


log = logging.getLogger(__name__)

MODELS_DIR_V2 = Path(__file__).resolve().parent.parent / 'models_trained_v2'

# Lazy torch imports to avoid requiring PyTorch at bot startup.
_torch = None
_nn = None
_functional = None


def _ensure_torch():
    global _torch, _nn, _functional
    if _torch is None:
        try:
            import torch
            import torch.nn as nn
            import torch.nn.functional as functional
            _torch = torch
            _nn = nn
            _functional = functional
        except ImportError:
            raise ImportError(
                'PyTorch is required for RL training. '
                'Install it with: pip install torch'
            )
    return _torch, _nn, _functional


def detect_training_device():
    """Return the best available torch.device (CUDA if present, else CPU)."""
    torch, _, _ = _ensure_torch()
    if torch.cuda.is_available():
        device = torch.device('cuda')
        gpu_name = torch.cuda.get_device_name(0)
        gpu_memory_gigabytes = torch.cuda.get_device_properties(0).total_memory / (1024 ** 3)
        log.info('Using GPU: %s (%.1f GB VRAM)', gpu_name, gpu_memory_gigabytes)
    else:
        device = torch.device('cpu')
        log.info('No CUDA GPU detected, using CPU for training.')
    return device


@dataclass
class TrajectoryStepV2:
    """A single step in a game trajectory for PPO training."""
    observation: list[float]
    action_index: int
    action_log_probability: float
    value_estimate: float
    reward: float = 0.0
    done: bool = False
    valid_action_mask: list[bool] = field(default_factory=list)


# Lazy class cache
_UniguriPolicyNetworkV2 = None


def _get_policy_network_class():
    """Get or create the UniguriPolicyNetworkV2 class (lazy so torch is not
    imported at bot startup)."""
    global _UniguriPolicyNetworkV2
    if _UniguriPolicyNetworkV2 is not None:
        return _UniguriPolicyNetworkV2

    torch, nn, functional = _ensure_torch()

    from zutomayo.engine.uniguri_env_v2 import (
        CARD_FEATURE_SIZE,
        DECISION_EFFECT_NUMBER,
        EFFECT_SEMANTIC_FEATURE_SIZE,
        GLOBAL_FEATURES_V2,
        MAX_ACTION_SIZE,
        MAX_CANDIDATES,
        MAX_DECK_SIZE,
        MAX_HAND_SIZE,
        NUM_ATTRIBUTE_TYPES,
        NUM_DECISION_TYPES,
        NUM_EFFECT_TYPES,
        NUM_SONG_TYPES,
        ZONE_CARDS,
    )

    # Card feature layout
    NUM_NUMERIC_CARD_FEATURES = 14
    NUM_CATEGORICAL_INDICES = 3   # effect_index, attribute_index, song_index
    SEMANTIC_FEATURES_START = NUM_NUMERIC_CARD_FEATURES + NUM_CATEGORICAL_INDICES

    # Token positions in the transformer sequence
    TOKEN_CLS = 0
    TOKEN_ZONE_START = 1
    TOKEN_ZONE_END = TOKEN_ZONE_START + ZONE_CARDS
    TOKEN_HAND_START = TOKEN_ZONE_END
    TOKEN_HAND_END = TOKEN_HAND_START + MAX_HAND_SIZE
    TOKEN_OPPONENT_HAND_START = TOKEN_HAND_END
    TOKEN_OPPONENT_HAND_END = TOKEN_OPPONENT_HAND_START + MAX_HAND_SIZE
    TOKEN_PLAYER_DECK = TOKEN_OPPONENT_HAND_END
    TOKEN_OPPONENT_DECK = TOKEN_PLAYER_DECK + 1
    TOKEN_CANDIDATE_START = TOKEN_OPPONENT_DECK + 1
    TOKEN_CANDIDATE_END = TOKEN_CANDIDATE_START + MAX_CANDIDATES
    TOKEN_STOP = TOKEN_CANDIDATE_END
    TOTAL_TOKENS = TOKEN_STOP + 1

    EMBED_DIM = 256
    NUM_HEADS = 8
    NUM_TRANSFORMER_LAYERS = 6
    FEEDFORWARD_DIM = 1024
    DROPOUT_RATE = 0.1
    NUM_POINTER_HEADS = 4
    POINTER_HEAD_DIM = EMBED_DIM // NUM_POINTER_HEADS

    class UniguriPolicyNetworkV2(nn.Module):
        """
        Transformer + pointer network for the v2 UNIGURI bot (PPO).

        Identical structure to UniguriTransformerNetwork in rl_model.py except:
          - global_encoder takes GLOBAL_FEATURES_V2 (79) inputs.
          - No average_strategy_head.
        """

        def __init__(self, observation_size: int, action_size: int) -> None:
            super().__init__()
            self.observation_size = observation_size
            self.action_size = action_size

            self.card_encoder = nn.Linear(NUM_NUMERIC_CARD_FEATURES, EMBED_DIM)
            self.effect_semantic_encoder = nn.Linear(EFFECT_SEMANTIC_FEATURE_SIZE, EMBED_DIM)
            self.effect_embedding = nn.Embedding(NUM_EFFECT_TYPES, EMBED_DIM)
            self.attribute_embedding = nn.Embedding(NUM_ATTRIBUTE_TYPES, EMBED_DIM)
            self.song_embedding = nn.Embedding(NUM_SONG_TYPES, EMBED_DIM)
            self.global_encoder = nn.Linear(GLOBAL_FEATURES_V2, EMBED_DIM)

            self.position_embedding = nn.Embedding(TOTAL_TOKENS, EMBED_DIM)
            self.decision_embedding = nn.Embedding(NUM_DECISION_TYPES, EMBED_DIM)

            self.stop_token_embedding = nn.Parameter(torch.randn(EMBED_DIM) * 0.02)

            self.token_layer_norm = nn.LayerNorm(EMBED_DIM)

            encoder_layer = nn.TransformerEncoderLayer(
                d_model=EMBED_DIM,
                nhead=NUM_HEADS,
                dim_feedforward=FEEDFORWARD_DIM,
                dropout=DROPOUT_RATE,
                activation='gelu',
                batch_first=True,
                norm_first=True,
            )
            self.transformer_encoder = nn.TransformerEncoder(
                encoder_layer,
                num_layers=NUM_TRANSFORMER_LAYERS,
                norm=nn.LayerNorm(EMBED_DIM),
            )

            self.effect_gate_projection = nn.Linear(2 * EMBED_DIM, EMBED_DIM)

            self.pointer_query_projection = nn.Linear(EMBED_DIM, EMBED_DIM)
            self.pointer_key_projection = nn.Linear(EMBED_DIM, EMBED_DIM)
            self.pointer_scale = 1.0 / math.sqrt(POINTER_HEAD_DIM)

            NUMBER_RANGE_FEATURES = 3
            self.number_head = nn.Sequential(
                nn.Linear(EMBED_DIM + NUMBER_RANGE_FEATURES, 128),
                nn.ReLU(),
                nn.Linear(128, action_size),
            )

            self.value_head = nn.Sequential(
                nn.Linear(2 * EMBED_DIM, EMBED_DIM),
                nn.GELU(),
                nn.LayerNorm(EMBED_DIM),
                nn.Linear(EMBED_DIM, 128),
                nn.GELU(),
                nn.Linear(128, 1),
            )

            self._initialize_weights()

        def _initialize_weights(self) -> None:
            for module in self.modules():
                if isinstance(module, nn.Linear):
                    nn.init.orthogonal_(module.weight, gain=1.0)
                    if module.bias is not None:
                        nn.init.zeros_(module.bias)

            nn.init.orthogonal_(self.number_head[-1].weight, gain=0.01)
            nn.init.orthogonal_(self.value_head[-1].weight, gain=1.0)
            nn.init.orthogonal_(self.pointer_query_projection.weight, gain=1.0)
            nn.init.orthogonal_(self.pointer_key_projection.weight, gain=0.5)
            nn.init.normal_(self.effect_embedding.weight, mean=0.0, std=0.01)
            nn.init.constant_(self.effect_gate_projection.bias, 1.0)

        def _parse_observation(self, observation_flat):
            """Split a flat observation vector into structured components.

            Args:
                observation_flat: Tensor (batch_size, observation_size).

            Returns:
                9-tuple of tensors matching the v2 observation layout.
            """
            batch_size = observation_flat.shape[0]
            offset = 0

            global_features = observation_flat[:, offset:offset + GLOBAL_FEATURES_V2]
            offset += GLOBAL_FEATURES_V2

            zone_card_features = observation_flat[:, offset:offset + ZONE_CARDS * CARD_FEATURE_SIZE].view(
                batch_size, ZONE_CARDS, CARD_FEATURE_SIZE)
            offset += ZONE_CARDS * CARD_FEATURE_SIZE

            hand_card_features = observation_flat[:, offset:offset + MAX_HAND_SIZE * CARD_FEATURE_SIZE].view(
                batch_size, MAX_HAND_SIZE, CARD_FEATURE_SIZE)
            offset += MAX_HAND_SIZE * CARD_FEATURE_SIZE

            opponent_hand_features = observation_flat[:, offset:offset + MAX_HAND_SIZE * CARD_FEATURE_SIZE].view(
                batch_size, MAX_HAND_SIZE, CARD_FEATURE_SIZE)
            offset += MAX_HAND_SIZE * CARD_FEATURE_SIZE

            player_deck_features = observation_flat[:, offset:offset + MAX_DECK_SIZE * CARD_FEATURE_SIZE].view(
                batch_size, MAX_DECK_SIZE, CARD_FEATURE_SIZE)
            offset += MAX_DECK_SIZE * CARD_FEATURE_SIZE

            opponent_deck_features = observation_flat[:, offset:offset + MAX_DECK_SIZE * CARD_FEATURE_SIZE].view(
                batch_size, MAX_DECK_SIZE, CARD_FEATURE_SIZE)
            offset += MAX_DECK_SIZE * CARD_FEATURE_SIZE

            decision_type_indices = observation_flat[:, offset:offset + NUM_DECISION_TYPES].argmax(dim=-1)
            offset += NUM_DECISION_TYPES

            candidate_card_features = observation_flat[:, offset:offset + MAX_CANDIDATES * CARD_FEATURE_SIZE].view(
                batch_size, MAX_CANDIDATES, CARD_FEATURE_SIZE)
            offset += MAX_CANDIDATES * CARD_FEATURE_SIZE

            number_range_features = observation_flat[:, offset:offset + 3]

            return (
                global_features,
                zone_card_features,
                hand_card_features,
                opponent_hand_features,
                player_deck_features,
                opponent_deck_features,
                decision_type_indices,
                candidate_card_features,
                number_range_features,
            )

        def _encode_card_tokens(self, card_features):
            """Encode (batch, num_cards, CARD_FEATURE_SIZE) → (batch, num_cards, EMBED_DIM)."""
            numeric = card_features[:, :, :NUM_NUMERIC_CARD_FEATURES]
            effect_indices = card_features[:, :, NUM_NUMERIC_CARD_FEATURES].long()
            attribute_indices = card_features[:, :, NUM_NUMERIC_CARD_FEATURES + 1].long()
            song_indices = card_features[:, :, NUM_NUMERIC_CARD_FEATURES + 2].long()
            effect_semantic = card_features[:, :, SEMANTIC_FEATURES_START:]

            base = (
                self.card_encoder(numeric)
                + self.attribute_embedding(attribute_indices)
                + self.song_embedding(song_indices)
            )
            effect_signal = (
                self.effect_semantic_encoder(effect_semantic)
                + self.effect_embedding(effect_indices)
            )
            gate = torch.sigmoid(
                self.effect_gate_projection(torch.cat([base, effect_signal], dim=-1))
            )
            return base + gate * effect_signal

        def _build_token_sequence(
            self,
            global_features,
            zone_card_features,
            hand_card_features,
            opponent_hand_features,
            player_deck_features,
            opponent_deck_features,
            decision_type_indices,
            candidate_card_features,
        ):
            """Assemble the transformer input sequence and attention mask."""
            batch_size = global_features.shape[0]
            device = global_features.device

            position_indices = torch.arange(TOTAL_TOKENS, device=device)
            position_embeddings = self.position_embedding(position_indices)

            cls_token = (
                self.global_encoder(global_features)
                + self.decision_embedding(decision_type_indices)
                + position_embeddings[TOKEN_CLS]
            )

            zone_tokens = self._encode_card_tokens(zone_card_features) + position_embeddings[TOKEN_ZONE_START:TOKEN_ZONE_END]
            hand_tokens = self._encode_card_tokens(hand_card_features) + position_embeddings[TOKEN_HAND_START:TOKEN_HAND_END]
            opponent_hand_tokens = self._encode_card_tokens(opponent_hand_features) + position_embeddings[TOKEN_OPPONENT_HAND_START:TOKEN_OPPONENT_HAND_END]
            candidate_tokens = self._encode_card_tokens(candidate_card_features) + position_embeddings[TOKEN_CANDIDATE_START:TOKEN_CANDIDATE_END]

            player_deck_summary = (
                self._encode_card_tokens(player_deck_features).mean(dim=1, keepdim=True)
                + position_embeddings[TOKEN_PLAYER_DECK]
            )
            opponent_deck_summary = (
                self._encode_card_tokens(opponent_deck_features).mean(dim=1, keepdim=True)
                + position_embeddings[TOKEN_OPPONENT_DECK]
            )

            stop_token = (
                self.stop_token_embedding + position_embeddings[TOKEN_STOP]
            ).unsqueeze(0).expand(batch_size, -1)

            token_embeddings = torch.cat([
                cls_token.unsqueeze(1),
                zone_tokens,
                hand_tokens,
                opponent_hand_tokens,
                player_deck_summary,
                opponent_deck_summary,
                candidate_tokens,
                stop_token.unsqueeze(1),
            ], dim=1)

            token_embeddings = self.token_layer_norm(token_embeddings)

            zone_is_padding = (zone_card_features.abs().sum(dim=-1) == 0)
            hand_is_padding = (hand_card_features.abs().sum(dim=-1) == 0)
            opponent_hand_is_padding = (opponent_hand_features.abs().sum(dim=-1) == 0)
            candidate_is_padding = (candidate_card_features.abs().sum(dim=-1) == 0)

            attention_mask = torch.cat([
                torch.zeros(batch_size, 1, dtype=torch.bool, device=device),
                zone_is_padding,
                hand_is_padding,
                opponent_hand_is_padding,
                torch.zeros(batch_size, 2, dtype=torch.bool, device=device),
                candidate_is_padding,
                torch.zeros(batch_size, 1, dtype=torch.bool, device=device),
            ], dim=1)

            return token_embeddings, attention_mask

        def forward(self, observation_flat, valid_action_mask=None):
            """
            Forward pass.

            Args:
                observation_flat: (batch_size, observation_size)
                valid_action_mask: optional bool (batch_size, action_size)

            Returns:
                (action_logits, state_value) — shapes (batch, action_size) and (batch, 1)
            """
            (
                global_features,
                zone_card_features,
                hand_card_features,
                opponent_hand_features,
                player_deck_features,
                opponent_deck_features,
                decision_type_indices,
                candidate_card_features,
                number_range_features,
            ) = self._parse_observation(observation_flat)

            token_embeddings, attention_mask = self._build_token_sequence(
                global_features,
                zone_card_features,
                hand_card_features,
                opponent_hand_features,
                player_deck_features,
                opponent_deck_features,
                decision_type_indices,
                candidate_card_features,
            )

            transformer_output = self.transformer_encoder(
                token_embeddings,
                src_key_padding_mask=attention_mask,
            )

            cls_output = transformer_output[:, TOKEN_CLS, :]

            candidate_outputs = transformer_output[:, TOKEN_CANDIDATE_START:TOKEN_CANDIDATE_END, :]
            candidate_not_padding = ~attention_mask[:, TOKEN_CANDIDATE_START:TOKEN_CANDIDATE_END]
            candidate_sum = (candidate_outputs * candidate_not_padding.unsqueeze(-1)).sum(dim=1)
            candidate_count = candidate_not_padding.sum(dim=1, keepdim=True).clamp(min=1)
            candidate_summary = candidate_sum / candidate_count
            state_value = self.value_head(torch.cat([cls_output, candidate_summary], dim=-1))

            batch_size = observation_flat.shape[0]
            num_pointer_tokens = MAX_CANDIDATES + 1
            pointer_query = self.pointer_query_projection(cls_output)
            candidate_and_stop = transformer_output[:, TOKEN_CANDIDATE_START:TOKEN_STOP + 1, :]
            pointer_keys = self.pointer_key_projection(candidate_and_stop)

            pointer_query = pointer_query.view(batch_size, NUM_POINTER_HEADS, POINTER_HEAD_DIM)
            pointer_keys = pointer_keys.view(batch_size, num_pointer_tokens, NUM_POINTER_HEADS, POINTER_HEAD_DIM)

            pointer_logits = torch.einsum('bhd,bnhd->bhn', pointer_query, pointer_keys) * self.pointer_scale
            pointer_logits = pointer_logits.mean(dim=1)

            if pointer_logits.shape[1] < self.action_size:
                padding = torch.full(
                    (batch_size, self.action_size - pointer_logits.shape[1]),
                    float('-inf'),
                    device=observation_flat.device,
                )
                pointer_logits = torch.cat([pointer_logits, padding], dim=1)

            number_logits = self.number_head(torch.cat([cls_output, number_range_features], dim=-1))

            is_number_decision = (decision_type_indices == DECISION_EFFECT_NUMBER).unsqueeze(1)
            action_logits = torch.where(is_number_decision, number_logits, pointer_logits)

            return action_logits, state_value

        @torch.no_grad()
        def select_action(self, observation_tensor, valid_action_mask=None):
            """
            Sample an action from the policy.

            Args:
                observation_tensor: (1, observation_size)
                valid_action_mask: optional bool (1, action_size)

            Returns:
                (action_index, log_probability, value_estimate, entropy)
            """
            action_logits, state_value = self.forward(observation_tensor, valid_action_mask)
            if valid_action_mask is not None:
                action_logits = action_logits.masked_fill(~valid_action_mask, float('-inf'))

            if torch.isnan(action_logits).any():
                raise ValueError('Model produced NaN logits; checkpoint may be corrupt')

            distribution = torch.distributions.Categorical(logits=action_logits)
            action = distribution.sample()
            return (
                action.item(),
                distribution.log_prob(action).item(),
                state_value.item(),
                distribution.entropy().item(),
            )

        def evaluate_actions(self, observation_batch, action_batch, valid_mask_batch=None):
            """
            Evaluate previously taken actions for PPO updates.

            Args:
                observation_batch: (batch_size, observation_size)
                action_batch: (batch_size,) int tensor
                valid_mask_batch: optional bool (batch_size, action_size)

            Returns:
                (action_log_probabilities, value_estimates, entropy)
            """
            action_logits, state_values = self.forward(observation_batch, valid_mask_batch)
            if valid_mask_batch is not None:
                action_logits = action_logits.masked_fill(~valid_mask_batch, float('-inf'))

            distribution = torch.distributions.Categorical(logits=action_logits)
            return (
                distribution.log_prob(action_batch),
                state_values.squeeze(-1),
                distribution.entropy(),
            )

    _UniguriPolicyNetworkV2 = UniguriPolicyNetworkV2
    return _UniguriPolicyNetworkV2


# ======================================================================
# Factory and checkpoint utilities
# ======================================================================


def create_policy_network_v2(
    observation_size: int,
    action_size: int,
    device=None,
):
    """Create a new v2 policy network and optionally move it to a device."""
    network_class = _get_policy_network_class()
    model = network_class(observation_size, action_size)
    if device is not None:
        model = model.to(device)
    return model


def save_checkpoint_v2(
    model,
    optimizer,
    episode: int,
    save_path: Optional[str] = None,
    scheduler=None,
) -> str:
    """
    Save a v2 model checkpoint to models_trained_v2/.

    Returns the path where the checkpoint was saved.
    """
    torch, _, _ = _ensure_torch()
    MODELS_DIR_V2.mkdir(parents=True, exist_ok=True)

    if save_path is None:
        save_path = str(MODELS_DIR_V2 / f'checkpoint_{episode:06d}.pt')

    checkpoint_data = {
        'episode': episode,
        'model_state_dict': model.state_dict(),
        'optimizer_state_dict': optimizer.state_dict(),
        'observation_size': model.observation_size,
        'action_size': model.action_size,
        'version': 2,
    }
    if scheduler is not None:
        checkpoint_data['scheduler_state_dict'] = scheduler.state_dict()

    torch.save(checkpoint_data, save_path)
    log.info('Saved v2 checkpoint to %s', save_path)
    return save_path


def load_checkpoint_v2(
    model,
    optimizer=None,
    checkpoint_path: Optional[str] = None,
    device=None,
    scheduler=None,
) -> int:
    """
    Load a v2 model checkpoint.

    Returns the episode number from the checkpoint.
    """
    torch, _, _ = _ensure_torch()

    if checkpoint_path is None:
        checkpoints = sorted(MODELS_DIR_V2.glob('checkpoint_*.pt'))
        if not checkpoints:
            raise FileNotFoundError(f'No v2 checkpoints found in {MODELS_DIR_V2}')
        checkpoint_path = str(checkpoints[-1])

    map_location = device if device is not None else 'cpu'
    checkpoint = torch.load(checkpoint_path, weights_only=False, map_location=map_location)

    saved_observation_size = checkpoint.get('observation_size')
    if saved_observation_size is not None and saved_observation_size != model.observation_size:
        raise ValueError(
            f'Checkpoint {checkpoint_path} has observation_size={saved_observation_size}, '
            f'but model expects {model.observation_size}.'
        )

    model.load_state_dict(checkpoint['model_state_dict'])

    if optimizer is not None and 'optimizer_state_dict' in checkpoint:
        optimizer.load_state_dict(checkpoint['optimizer_state_dict'])
    if scheduler is not None and 'scheduler_state_dict' in checkpoint:
        scheduler.load_state_dict(checkpoint['scheduler_state_dict'])

    episode = checkpoint.get('episode', 0)
    log.info('Loaded v2 checkpoint from %s (episode %d)', checkpoint_path, episode)
    return episode
