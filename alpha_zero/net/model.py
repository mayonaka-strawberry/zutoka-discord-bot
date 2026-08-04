"""UniguriNet: transformer over card tokens with pointer / identity / number
policy heads and a win/draw/loss value head.

- Token embedding: summed categorical embeddings (identity, attribute, type,
  song, rarity, zone-slot) + Linear(float features) + effect block. The
  effect block fuses a fixed IR-derived feature vector (projected 128->64)
  with a learned per-effect embedding (32) — the IR supplies structure, the
  embedding absorbs whatever the IR misses.
- The CLS token receives the global-feature MLP output additively.
- Identity head weights are tied to the identity embedding matrix, so draft
  picks and name-guesses share card knowledge.
- Identity- and effect-indexed tables are allocated at configured capacities
  above the current catalog size; the catalog's null indices are remapped to
  the last row so future catalog rows slot into the reserved space without
  changing tensor shapes.
- Value is for the acting player (observations are acting-relative): a
  3-way win/draw/loss distribution whose expectation serves as the scalar
  value ([win, draw, loss] maps to [+1, 0, -1]).
"""

from __future__ import annotations

import torch
import torch.nn as nn

from engine_alpha.cards import NUM_CARDS, NUM_EFFECTS, NUM_SONGS
from ..config import NetConfig
from engine_alpha.encoding.observation import (
    N_FLOAT_FEATURES, N_GLOBALS, N_NUMBER_ACTIONS, N_ZONE_SLOTS,
)
from engine_alpha.effects.features import EFFECT_FEATURES, FEATURE_DIM

WDL_OUTCOME_VALUES = (1.0, 0.0, -1.0)


class UniguriNet(nn.Module):
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
        self.gradient_checkpointing = False
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
            nn.Linear(cfg.value_hidden_dim, 3))
        self.register_buffer(
            "wdl_values", torch.tensor(WDL_OUTCOME_VALUES), persistent=False)
        self.pointer_query = nn.Linear(d, cfg.pointer_dim)
        self.pointer_key = nn.Linear(d, cfg.pointer_dim)
        self.identity_projection = nn.Linear(d, cfg.identity_embedding_dim)
        self.identity_bias = nn.Parameter(torch.zeros(cfg.identity_capacity))
        self.number_head = nn.Linear(d, N_NUMBER_ACTIONS)

    def _encode(self, tokens: torch.Tensor, token_mask: torch.Tensor) -> torch.Tensor:
        if self.gradient_checkpointing and self.training:
            from torch.utils.checkpoint import checkpoint

            padding_mask = ~token_mask
            for layer in self.encoder.layers:
                tokens = checkpoint(
                    lambda layer_input, transformer_layer=layer: transformer_layer(
                        layer_input, src_key_padding_mask=padding_mask),
                    tokens, use_reentrant=False)
            return tokens
        return self.encoder(tokens, src_key_padding_mask=~token_mask)

    def forward(self, tok_int: torch.Tensor, tok_float: torch.Tensor,
                token_mask: torch.Tensor, global_features: torch.Tensor,
                return_value_logits: bool = False):
        """
        tok_int   [B, T, 7] long   tok_float [B, T, F] float
        token_mask[B, T] bool (True = real token)
        global_features [B, G] float
        Returns: value [B] (win/draw/loss expectation; the [B, 3] logits when
                 return_value_logits), pointer_scores [B, T],
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
        cls_addition = self.global_mlp(global_features).unsqueeze(1)   # [B,1,d]
        tokens = torch.cat([tokens[:, :1] + cls_addition, tokens[:, 1:]], dim=1)

        encoded = self._encode(tokens, token_mask)
        cls = encoded[:, 0]

        value_logits = self.value_head(cls)                            # [B,3]
        if return_value_logits:
            value = value_logits
        else:
            value = value_logits.softmax(dim=-1) @ self.wdl_values
        query = self.pointer_query(cls).unsqueeze(1)                   # [B,1,P]
        keys = self.pointer_key(encoded)                               # [B,T,P]
        pointer_scores = (keys @ query.transpose(1, 2)).squeeze(-1)    # [B,T]
        pointer_scores = pointer_scores / (self.cfg.pointer_dim ** 0.5)
        identity_logits = (self.identity_projection(cls)
                           @ self.identity_embedding.weight[:self.cfg.identity_capacity].T
                           + self.identity_bias)
        number_logits = self.number_head(cls)
        return value, pointer_scores, identity_logits, number_logits

    def parameter_count(self) -> int:
        return sum(p.numel() for p in self.parameters())


def priors_for_request(request, candidate_positions, pointer_scores,
                       identity_logits, number_logits):
    """Map head outputs to a prior distribution over request.legal_actions().

    Single-sample tensors (1-D). Returns a torch tensor of probabilities in
    legal_actions() order.
    """
    import torch.nn.functional as functional
    from engine_alpha.actions import SELECT_CARD, SELECT_IDENTITY

    if request.kind == SELECT_CARD:
        logits = pointer_scores[list(candidate_positions)]
    elif request.kind == SELECT_IDENTITY:
        logits = identity_logits[list(request.legal)]
    else:  # SELECT_NUMBER / BINARY: actions lo..hi (binary: 0..1)
        actions = request.legal_actions()
        logits = number_logits[actions]
    return functional.softmax(logits.float(), dim=-1)
