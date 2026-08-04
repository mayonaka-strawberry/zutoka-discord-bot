"""PpoPolicyNetwork: transformer over card tokens with pointer / identity /
number policy heads and an unbounded value head, for PPO training on
engine_alpha observations.

Structure mirrors the engine's observation design (summed categorical
embeddings, IR-derived effect features fused with a learned effect
embedding, CLS carrying the global features) with actor-critic heads:
every decision becomes one masked categorical over the legal actions, and
the value head estimates the acting player's terminal return without a
tanh bound (value clipping happens in the loss).

Identity- and effect-indexed tables are allocated at configured capacities
above the current catalog size; the catalog's null indices are remapped to
the last row so future catalog rows use the reserved space without shape
changes.
"""

from __future__ import annotations

import torch
import torch.nn as nn

from engine_alpha.actions import SELECT_CARD, SELECT_IDENTITY
from engine_alpha.cards import NUM_CARDS, NUM_EFFECTS, NUM_SONGS
from engine_alpha.encoding.observation import (
    N_FLOAT_FEATURES, N_GLOBALS, N_NUMBER_ACTIONS, N_ZONE_SLOTS,
)
from engine_alpha.effects.features import EFFECT_FEATURES, FEATURE_DIM

from ..config import NetConfig

HEAD_POINTER = 0
HEAD_IDENTITY = 1
HEAD_NUMBER = 2


class PpoPolicyNetwork(nn.Module):
    def __init__(self, cfg: NetConfig | None = None) -> None:
        super().__init__()
        cfg = cfg or NetConfig()
        if cfg.identity_capacity < NUM_CARDS:
            raise ValueError(
                f'identity_capacity {cfg.identity_capacity} is below the card count '
                f'{NUM_CARDS}; migrate the checkpoint (model_common/migrate_checkpoint.py)'
            )
        if cfg.effect_capacity < NUM_EFFECTS:
            raise ValueError(
                f'effect_capacity {cfg.effect_capacity} is below the effect count '
                f'{NUM_EFFECTS}; migrate the checkpoint (model_common/migrate_checkpoint.py)'
            )
        self.cfg = cfg
        d = cfg.embed_dim

        self.identity_embedding = nn.Embedding(cfg.identity_capacity + 1, cfg.identity_embedding_dim)
        self.attribute_embedding = nn.Embedding(6, 16)
        self.type_embedding = nn.Embedding(4, 8)
        self.song_embedding = nn.Embedding(NUM_SONGS + 1, 16)
        self.rarity_embedding = nn.Embedding(6, 8)
        self.effect_embedding = nn.Embedding(cfg.effect_capacity + 1, cfg.effect_embedding_dim)
        self.zone_embedding = nn.Embedding(N_ZONE_SLOTS, 16)

        self.register_buffer("effect_features",
                             torch.from_numpy(EFFECT_FEATURES.copy()), persistent=False)
        self.effect_feature_projection = nn.Linear(FEATURE_DIM, cfg.effect_feature_projection_dim)

        concat_dim = (cfg.identity_embedding_dim + 16 + 8 + 16 + 8 + 16
                      + cfg.effect_embedding_dim + cfg.effect_feature_projection_dim
                      + N_FLOAT_FEATURES)
        self.token_input = nn.Sequential(
            nn.Linear(concat_dim, d), nn.LayerNorm(d))
        self.global_mlp = nn.Sequential(
            nn.Linear(N_GLOBALS, d), nn.GELU(), nn.Linear(d, d))

        layer = nn.TransformerEncoderLayer(
            d_model=d, nhead=cfg.num_heads, dim_feedforward=cfg.feedforward_dim,
            dropout=cfg.dropout, activation="gelu", batch_first=True,
            norm_first=True)
        self.encoder = nn.TransformerEncoder(layer, num_layers=cfg.num_layers)

        self.value_head = nn.Sequential(
            nn.Linear(d, cfg.value_hidden_dim), nn.GELU(),
            nn.Linear(cfg.value_hidden_dim, 1))
        self.pointer_query = nn.Linear(d, cfg.pointer_dim)
        self.pointer_key = nn.Linear(d, cfg.pointer_dim)
        self.identity_projection = nn.Linear(d, cfg.identity_embedding_dim)
        self.identity_bias = nn.Parameter(torch.zeros(cfg.identity_capacity))
        self.number_head = nn.Linear(d, N_NUMBER_ACTIONS)

    def forward(self, tok_int: torch.Tensor, tok_float: torch.Tensor,
                token_mask: torch.Tensor, global_features: torch.Tensor):
        """
        tok_int   [B, T, 7] long   tok_float [B, T, F] float
        token_mask[B, T] bool (True = real token)
        global_features [B, G] float
        Returns: value [B], pointer_scores [B, T],
                 identity_logits [B, identity_capacity],
                 number_logits [B, N_NUMBER_ACTIONS]
        """
        identity_index = tok_int[..., 0]
        identity_index = torch.where(
            identity_index == NUM_CARDS,
            torch.full_like(identity_index, self.cfg.identity_capacity),
            identity_index)
        effect_index = tok_int[..., 5]
        effect_embedding_index = torch.where(
            effect_index == NUM_EFFECTS,
            torch.full_like(effect_index, self.cfg.effect_capacity),
            effect_index)
        parts = torch.cat([
            self.identity_embedding(identity_index),
            self.attribute_embedding(tok_int[..., 1]),
            self.type_embedding(tok_int[..., 2]),
            self.song_embedding(tok_int[..., 3]),
            self.rarity_embedding(tok_int[..., 4]),
            self.zone_embedding(tok_int[..., 6]),
            self.effect_embedding(effect_embedding_index),
            self.effect_feature_projection(self.effect_features[effect_index]),
            tok_float,
        ], dim=-1)
        tokens = self.token_input(parts)
        cls_addition = self.global_mlp(global_features).unsqueeze(1)
        tokens = torch.cat([tokens[:, :1] + cls_addition, tokens[:, 1:]], dim=1)

        encoded = self.encoder(tokens, src_key_padding_mask=~token_mask)
        cls = encoded[:, 0]

        value = self.value_head(cls).squeeze(-1)
        query = self.pointer_query(cls).unsqueeze(1)
        keys = self.pointer_key(encoded)
        pointer_scores = (keys @ query.transpose(1, 2)).squeeze(-1)
        pointer_scores = pointer_scores / (self.cfg.pointer_dim ** 0.5)
        identity_logits = (self.identity_projection(cls)
                           @ self.identity_embedding.weight[:self.cfg.identity_capacity].T
                           + self.identity_bias)
        number_logits = self.number_head(cls)
        return value, pointer_scores, identity_logits, number_logits

    def parameter_count(self) -> int:
        return sum(p.numel() for p in self.parameters())


def head_for_request(request) -> int:
    if request.kind == SELECT_CARD:
        return HEAD_POINTER
    if request.kind == SELECT_IDENTITY:
        return HEAD_IDENTITY
    return HEAD_NUMBER


def legal_action_slots(request, candidate_positions) -> list[int]:
    """Head-native slot index for every legal action, in legal_actions()
    order: pointer -> token rows (candidate_positions covers the PASS token),
    identity -> card definition indices, number/binary -> numeric values."""
    if request.kind == SELECT_CARD:
        return list(candidate_positions)
    if request.kind == SELECT_IDENTITY:
        return list(request.legal)
    return request.legal_actions()


def logits_for_slots(head: torch.Tensor, slots: torch.Tensor,
                     pointer_scores: torch.Tensor, identity_logits: torch.Tensor,
                     number_logits: torch.Tensor) -> torch.Tensor:
    """Gather per-sample legal-action logits into [B, S] (-inf padded).

    head [B], slots [B, S] (-1 padded); the head tensors are batch-first.
    """
    batch_size, slot_width = slots.shape
    gathered = torch.full((batch_size, slot_width), float('-inf'),
                          device=pointer_scores.device)
    safe_slots = slots.clamp(min=0)
    for head_id, logits in ((HEAD_POINTER, pointer_scores),
                            (HEAD_IDENTITY, identity_logits),
                            (HEAD_NUMBER, number_logits)):
        selector = head == head_id
        if selector.any():
            gathered[selector] = logits[selector].gather(1, safe_slots[selector])
    return gathered.masked_fill(slots < 0, float('-inf'))
