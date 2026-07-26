"""CHAOS self-defeat reward shaping across both training stacks.

A CHAOS card played without the Abyss cards its effect requires loses the
game on the spot. Both trainers single out that termination: PPO replaces
the terminal reward, AlphaZero reweights the value loss. Everything else --
battle-HP losses, deck-outs, draws -- keeps the engine's plain +/-1.

The shared predicate is tracked, so its tests run unconditionally; the
trainer-side tests import gitignored training modules via importorskip.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from model_common.termination import chaos_self_defeat_loser  # noqa: E402


class _TerminalState:
    """The two fields the predicate reads off a finished GameState."""

    def __init__(self, winner: int, self_defeat_player: int = -1) -> None:
        self.winner = winner
        self.self_defeat_player = self_defeat_player


# -- the shared predicate --------------------------------------------------


def test_predicate_ignores_ordinary_terminations():
    """A battle-HP loss or a deck-out leaves self_defeat_player unset."""
    assert chaos_self_defeat_loser(_TerminalState(winner=0)) == -1
    assert chaos_self_defeat_loser(_TerminalState(winner=1)) == -1
    assert chaos_self_defeat_loser(_TerminalState(winner=2)) == -1


def test_predicate_reports_the_seat_that_self_defeated():
    assert chaos_self_defeat_loser(
        _TerminalState(winner=1, self_defeat_player=0)) == 0
    assert chaos_self_defeat_loser(
        _TerminalState(winner=0, self_defeat_player=1)) == 1


def test_predicate_skips_a_self_defeater_who_still_won():
    """check_win awards the win on higher HP when both players are at or
    below zero, so the self-defeating player can end up the winner. Nothing
    is shaped there -- the opponent did not receive a free win."""
    assert chaos_self_defeat_loser(
        _TerminalState(winner=0, self_defeat_player=0)) == -1


def test_predicate_skips_a_draw_after_a_self_defeat():
    assert chaos_self_defeat_loser(
        _TerminalState(winner=2, self_defeat_player=0)) == -1


# -- against the real engine ------------------------------------------------


def test_predicate_fires_on_every_chaos_bomb_short_of_abyss():
    from engine_alpha.battle import check_win
    from engine_alpha.tests.test_rulings import (
        CHAOS_BOMBS, card_with_effect, make_game, run_effect_auto, spawn,
        stock_abyss)

    for effect_id, abyss_count in CHAOS_BOMBS:
        game = make_game()
        game.state.turn = 1
        stock_abyss(game, 0, abyss_count)
        run_effect_auto(game, 0, spawn(game, card_with_effect(effect_id)))
        check_win(game.state)

        assert game.state.winner == 1, effect_id
        assert chaos_self_defeat_loser(game.state) == 0, effect_id


def test_predicate_silent_when_the_chaos_requirement_is_met():
    """Playing a CHAOS card successfully is an ordinary game -- the shaping
    keys off the shortfall, not off the card type."""
    from engine_alpha.tests.test_rulings import (
        CHAOS_BOMBS_SATISFIED, card_with_effect, make_game, run_effect_auto,
        spawn, stock_abyss)

    for effect_id, abyss_count in CHAOS_BOMBS_SATISFIED:
        game = make_game()
        state = game.state
        stock_abyss(game, 0, abyss_count)
        state.players[1].battle = -1
        del state.players[1].deck[1:]
        run_effect_auto(game, 0, spawn(game, card_with_effect(effect_id)))

        assert state.self_defeat_player == -1, effect_id
        assert chaos_self_defeat_loser(state) == -1, effect_id


# -- PPO: shaped terminal reward -------------------------------------------


def _decision_sample(sample_class, value: float):
    """A sample carrying only the fields the advantage pass touches."""
    return sample_class(tok_int=None, tok_float=None, global_features=None,
                        head=0, slots=[0], chosen_position=0, log_prob=0.0,
                        value=value)


class _StubGame:
    def __init__(self, returns, state) -> None:
        self._returns = returns
        self.state = state

    def returns(self):
        return self._returns


def _terminal_value_targets(returns, state):
    """Runs the PPO advantage pass over one decision per seat and returns
    {seat: value_target}. With value 0.0 the target is the terminal reward."""
    rollout = pytest.importorskip('ppo_transformer.train.rollout')
    from ppo_transformer.config import TrainConfig

    slot = rollout.GameSlot(game=_StubGame(returns, state), mode='test',
                            learner_seats=(0, 1))
    slot.samples_by_seat = {
        seat: [_decision_sample(rollout.DecisionSample, 0.0)]
        for seat in (0, 1)}
    rollout._assign_gae(slot, TrainConfig())
    return {seat: samples[0].value_target
            for seat, samples in slot.samples_by_seat.items()}


def test_ppo_shapes_both_seats_on_a_self_defeat():
    from ppo_transformer.config import TrainConfig

    cfg = TrainConfig()
    targets = _terminal_value_targets(
        (-1.0, 1.0), _TerminalState(winner=1, self_defeat_player=0))

    assert targets[0] == pytest.approx(cfg.self_defeat_loss_reward)
    assert targets[1] == pytest.approx(cfg.self_defeat_win_reward)
    assert cfg.self_defeat_loss_reward < -1.0
    assert cfg.self_defeat_win_reward < 1.0


def test_ppo_leaves_ordinary_outcomes_at_plus_minus_one():
    for returns, winner in (((1.0, -1.0), 0), ((-1.0, 1.0), 1)):
        targets = _terminal_value_targets(returns, _TerminalState(winner=winner))
        assert targets[0] == pytest.approx(returns[0])
        assert targets[1] == pytest.approx(returns[1])


def test_ppo_leaves_a_self_defeater_who_won_unshaped():
    targets = _terminal_value_targets(
        (1.0, -1.0), _TerminalState(winner=0, self_defeat_player=0))
    assert targets[0] == pytest.approx(1.0)
    assert targets[1] == pytest.approx(-1.0)


def test_ppo_leaves_a_draw_unshaped():
    targets = _terminal_value_targets(
        (0.0, 0.0), _TerminalState(winner=2, self_defeat_player=0))
    assert targets[0] == pytest.approx(0.0)
    assert targets[1] == pytest.approx(0.0)


# -- AlphaZero: value-loss sample weights ----------------------------------


def _record_with_one_sample_per_seat():
    game_record = pytest.importorskip('alpha_zero.selfplay.game_record')
    import numpy as np

    record = game_record.GameRecord()
    for acting in (0, 1):
        record.samples.append(game_record.Sample(
            tok_int=np.zeros((1, 1), dtype=np.int16),
            tok_float=np.zeros((1, 1), dtype=np.float16),
            n_tokens=1,
            global_features=np.zeros(1, dtype=np.float32),
            head=game_record.HEAD_POINTER,
            target_slots=np.zeros(1, dtype=np.int16),
            target_probs=np.ones(1, dtype=np.float16),
            acting=acting))
    return record


def test_alpha_zero_backfill_weights_a_self_defeat_asymmetrically():
    record = _record_with_one_sample_per_seat()
    record.backfill(1, self_defeat_loser=0, loss_sample_weight=2.0,
                    win_sample_weight=0.25)

    by_seat = {s.acting: s for s in record.samples}
    # The outcome itself is untouched: it really was a loss and a win.
    assert by_seat[0].z == -1.0
    assert by_seat[1].z == 1.0
    assert by_seat[0].value_loss_sample_weight == 2.0
    assert by_seat[1].value_loss_sample_weight == 0.25


def test_alpha_zero_backfill_leaves_ordinary_games_at_weight_one():
    record = _record_with_one_sample_per_seat()
    record.backfill(1)

    assert [s.z for s in record.samples] == [-1.0, 1.0]
    assert all(s.value_loss_sample_weight == 1.0 for s in record.samples)


def test_alpha_zero_stack_samples_carries_the_weight():
    from alpha_zero.selfplay.game_record import stack_samples

    record = _record_with_one_sample_per_seat()
    record.backfill(1, self_defeat_loser=0, loss_sample_weight=2.0,
                    win_sample_weight=0.25)
    arrays = stack_samples(record.samples)

    assert list(arrays['value_loss_sample_weight']) == [2.0, 0.25]


def test_alpha_zero_weighted_value_loss_matches_the_manual_computation():
    torch = pytest.importorskip('torch')
    from alpha_zero.train.losses import compute_loss, wdl_class_targets

    outputs, batch = _loss_fixture(torch)
    weights = torch.tensor([2.0, 0.25])
    batch['value_loss_sample_weight'] = weights

    _, scalars = compute_loss(outputs, batch)
    per_sample = torch.nn.functional.cross_entropy(
        outputs[0], wdl_class_targets(batch['z']), reduction='none')

    assert scalars['loss/value'] == pytest.approx(
        float((per_sample * weights).mean()), rel=1e-6)


def test_alpha_zero_unit_weights_reproduce_the_unweighted_loss():
    torch = pytest.importorskip('torch')
    from alpha_zero.train.losses import compute_loss

    outputs, batch = _loss_fixture(torch)
    _, without_key = compute_loss(outputs, batch)

    batch['value_loss_sample_weight'] = torch.ones(2)
    _, with_ones = compute_loss(outputs, batch)

    assert with_ones['loss/value'] == pytest.approx(without_key['loss/value'],
                                                    rel=1e-6)


def _loss_fixture(torch):
    """(net_outputs, batch) for a two-sample pointer-head batch."""
    from alpha_zero.selfplay.game_record import HEAD_POINTER

    torch.manual_seed(0)
    value_logits = torch.randn(2, 3)
    pointer_scores = torch.randn(2, 4)
    identity_logits = torch.randn(2, 5)
    number_logits = torch.randn(2, 3)
    batch = {
        'head': torch.tensor([HEAD_POINTER, HEAD_POINTER]),
        'target_slots': torch.tensor([[0, -1], [1, -1]]),
        'target_probs': torch.tensor([[1.0, 0.0], [1.0, 0.0]]),
        'z': torch.tensor([-1.0, 1.0]),
        'token_mask': torch.ones(2, 4, dtype=torch.bool),
    }
    return (value_logits, pointer_scores, identity_logits, number_logits), batch


# -- AlphaZero: shards written before the weight existed --------------------


def test_replay_buffer_defaults_the_weight_for_pre_existing_shards(tmp_path):
    """An in-progress run must resume against its old shards."""
    np = pytest.importorskip('numpy')
    pytest.importorskip('alpha_zero.selfplay.replay_buffer')
    from alpha_zero.selfplay.replay_buffer import ReplayBuffer

    old_shard = {
        'tok_int': np.zeros((2, 1, 1), dtype=np.int16),
        'tok_float': np.zeros((2, 1, 1), dtype=np.float16),
        'n_tokens': np.ones(2, dtype=np.int16),
        'global_features': np.zeros((2, 1), dtype=np.float32),
        'head': np.zeros(2, dtype=np.int8),
        'target_slots': np.zeros((2, 1), dtype=np.int16),
        'target_probs': np.ones((2, 1), dtype=np.float16),
        'z': np.array([-1, 1], dtype=np.int8),
    }
    np.savez_compressed(tmp_path / 'shard_00000000.npz', **old_shard)
    with open(tmp_path / 'index.json', 'w', encoding='utf-8') as handle:
        json.dump({'shards': [{'name': 'shard_00000000.npz', 'size': 2}],
                   'next_shard_id': 1}, handle)

    buffer = ReplayBuffer(tmp_path)
    batch = buffer.sample_batch(4, np.random.default_rng(0))

    assert batch['value_loss_sample_weight'].shape == (4,)
    assert (batch['value_loss_sample_weight'] == 1.0).all()
