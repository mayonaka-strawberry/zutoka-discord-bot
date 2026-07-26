"""Config/env override layer for both model stacks.

These tests cover tracked, torch-free modules (`model_common.env_config`, the
two `config.py` files), so they run on a fresh clone that carries no training
code and no checkpoints.

Every test that touches `load_config()` monkeypatches the stack's `ENV_FILE` to
a temp path. Without that, results would depend on whatever the developer
happens to have in their real `alpha_zero/.env`.
"""

from __future__ import annotations

import sys
from dataclasses import fields
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from model_common import env_config  # noqa: E402
from alpha_zero import config as alpha_config  # noqa: E402
from ppo_transformer import config as ppo_config  # noqa: E402

STACKS = pytest.mark.parametrize('stack', [alpha_config, ppo_config],
                                 ids=['alpha_zero', 'ppo_transformer'])


@pytest.fixture
def isolated_env(monkeypatch, tmp_path):
    """Points both stacks at an empty env file and clears their variables, so
    a test sees only what it sets."""
    for stack in (alpha_config, ppo_config):
        monkeypatch.setattr(stack, 'ENV_FILE', tmp_path / f'{stack.PREFIX}.env')
    for name in list(__import__('os').environ):
        if name.startswith(('ALPHA_', 'PPO_')):
            monkeypatch.delenv(name, raising=False)
    return tmp_path


# ---------------------------------------------------------------------------
# coerce
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('raw, target, expected', [
    ('42', int, 42),
    ('0.25', float, 0.25),
    ('alpha_zero/runs', str, 'alpha_zero/runs'),
    ('true', bool, True), ('1', bool, True), ('YES', bool, True), ('on', bool, True),
    ('false', bool, False), ('0', bool, False), ('no', bool, False), ('', bool, False),
])
def test_coerce_converts_supported_types(raw, target, expected):
    assert env_config.coerce(raw, target) == expected


def test_coerce_error_names_the_offending_variable():
    """A bad override should say which key was wrong — the whole point of
    threading env_key through."""
    with pytest.raises(ValueError, match=r'ALPHA_TRAIN_BATCH_SIZE=.not a number.'):
        env_config.coerce('not a number', int, 'ALPHA_TRAIN_BATCH_SIZE')


def test_coerce_rejects_ambiguous_boolean():
    with pytest.raises(ValueError, match='ALPHA_TRAIN_GRADIENT_CHECKPOINTING'):
        env_config.coerce('maybe', bool, 'ALPHA_TRAIN_GRADIENT_CHECKPOINTING')


# ---------------------------------------------------------------------------
# env file loading and precedence
# ---------------------------------------------------------------------------

def test_env_file_is_parsed_with_comments_stripped(isolated_env, monkeypatch):
    env_file = isolated_env / 'ALPHA.env'
    env_file.write_text(
        '# a comment line\n'
        '\n'
        'ALPHA_TRAIN_BATCH_SIZE=256   # trailing comment\n'
        'ALPHA_LEAGUE_DECK_POOL_PATH=some/decks.json\n',
        encoding='utf-8')
    config = alpha_config.load_config()
    assert config.train.batch_size == 256
    assert config.league.deck_pool_path == 'some/decks.json'


def test_process_environment_wins_over_the_env_file(isolated_env, monkeypatch):
    (isolated_env / 'ALPHA.env').write_text(
        'ALPHA_TRAIN_BATCH_SIZE=256\n', encoding='utf-8')
    monkeypatch.setenv('ALPHA_TRAIN_BATCH_SIZE', '999')
    assert alpha_config.load_config().train.batch_size == 999


def test_missing_env_file_is_not_an_error(isolated_env):
    """Production carries no per-stack .env and must still start."""
    assert not (isolated_env / 'ALPHA.env').exists()
    assert alpha_config.load_config().train.batch_size == \
        alpha_config.TrainConfig().batch_size


def test_empty_override_is_treated_as_unset(isolated_env, monkeypatch):
    monkeypatch.setenv('ALPHA_TRAIN_BATCH_SIZE', '')
    assert alpha_config.load_config().train.batch_size == \
        alpha_config.TrainConfig().batch_size


@STACKS
def test_env_setting_reads_run_level_values(isolated_env, monkeypatch, stack):
    monkeypatch.setenv(f'{stack.PREFIX}_ITERATIONS', '77')
    assert stack.env_setting('iterations', 1) == 77
    assert stack.env_setting('not_set_anywhere', 'fallback') == 'fallback'


def test_env_setting_infers_type_from_default(isolated_env, monkeypatch):
    monkeypatch.setenv('ALPHA_WORKERS', '12')
    assert alpha_config.env_setting('workers', 0) == 12
    monkeypatch.setenv('ALPHA_GATING_GAMES', '50')
    # default None carries no type, so the caller passes one explicitly
    assert alpha_config.env_setting('gating_games', None, int) == 50


# ---------------------------------------------------------------------------
# Every key round-trips
# ---------------------------------------------------------------------------

def _distinct_value(current):
    """A value guaranteed to differ from `current`, of the same type."""
    if isinstance(current, bool):
        return not current
    if isinstance(current, int):
        return current + 7
    if isinstance(current, float):
        return round(current + 0.125, 6)
    return (current or 'x') + '_changed'


@STACKS
def test_every_field_is_reachable_from_the_environment(isolated_env, monkeypatch,
                                                       stack):
    """The regression test for config drift.

    A field with no working env key, or a key whose name no longer matches its
    field, is exactly the failure that let `ALPHA_LEAGUE_P_RANDOM_DECKS` sit in
    the env file doing nothing. Asserting the override *takes effect* catches
    both directions.
    """
    baseline = stack.Config()
    expected = {}
    for section_name in stack.SECTIONS:
        section = getattr(baseline, section_name)
        for field in fields(section):
            key = f'{stack.PREFIX}_{section_name.upper()}_{field.name.upper()}'
            value = _distinct_value(getattr(section, field.name))
            monkeypatch.setenv(key, 'true' if value is True else
                               'false' if value is False else str(value))
            expected[(section_name, field.name)] = value

    # validate() would reject this deliberately nonsensical config, so go
    # through the override step alone.
    loaded = env_config.apply_env_overrides(
        stack.Config(), stack.PREFIX, stack.SECTIONS, stack.ENV_FILE)

    for (section_name, field_name), value in expected.items():
        actual = getattr(getattr(loaded, section_name), field_name)
        assert actual == pytest.approx(value) if isinstance(value, float) \
            else actual == value, f'{stack.PREFIX}_{section_name}_{field_name}'


@STACKS
def test_generated_template_covers_every_field(stack):
    """The `python -m <stack>.config` dump is the documented key reference, so
    it must not omit anything."""
    template = stack.format_template()
    for section_name in stack.SECTIONS:
        for field in fields(getattr(stack.Config(), section_name)):
            key = f'{stack.PREFIX}_{section_name.upper()}_{field.name.upper()}'
            assert f'# {key}=' in template, f'{key} missing from the template'
    for name, _default, _comment in stack.RUN_SETTINGS:
        assert f'{stack.PREFIX}_{name.upper()}=' in template


@STACKS
def test_generated_template_is_inert(isolated_env, stack):
    """Redirecting the dump into `.env` must reproduce the defaults, not
    change them — every line is commented."""
    env_file = isolated_env / f'{stack.PREFIX}.env'
    env_file.write_text(stack.format_template(), encoding='utf-8')
    assert stack.load_config().to_dict() == stack.Config().to_dict()


# ---------------------------------------------------------------------------
# validate()
# ---------------------------------------------------------------------------

@STACKS
def test_default_config_is_valid(stack):
    stack.Config().validate()


def test_alpha_rejects_opponent_probabilities_that_do_not_sum(isolated_env,
                                                              monkeypatch):
    monkeypatch.setenv('ALPHA_LEAGUE_P_POOL_DECKS', '0.9')
    with pytest.raises(ValueError, match='must sum to 1.0'):
        alpha_config.load_config()


def test_ppo_rejects_opponent_probabilities_that_do_not_sum(isolated_env,
                                                            monkeypatch):
    monkeypatch.setenv('PPO_TRAIN_P_VS_RANDOM', '0.4')
    with pytest.raises(ValueError, match='must sum to 1.0'):
        ppo_config.load_config()


@STACKS
def test_embed_dim_must_divide_by_heads(isolated_env, monkeypatch, stack):
    monkeypatch.setenv(f'{stack.PREFIX}_NET_NUM_HEADS', '7')
    with pytest.raises(ValueError, match='divisible by'):
        stack.load_config()


@STACKS
def test_capacity_below_catalog_size_is_rejected(isolated_env, monkeypatch, stack):
    monkeypatch.setenv(f'{stack.PREFIX}_NET_IDENTITY_CAPACITY', '4')
    with pytest.raises(ValueError, match='below the card count'):
        stack.load_config()


def test_alpha_rejects_warmup_beyond_the_decay_horizon(isolated_env, monkeypatch):
    monkeypatch.setenv('ALPHA_TRAIN_WARMUP_STEPS', '999999')
    with pytest.raises(ValueError, match='WARMUP_STEPS'):
        alpha_config.load_config()


def test_ppo_rejects_minibatch_larger_than_the_rollout(isolated_env, monkeypatch):
    monkeypatch.setenv('PPO_TRAIN_MINIBATCH_SIZE', '999999')
    with pytest.raises(ValueError, match='exceeds'):
        ppo_config.load_config()


def test_probability_group_helper_reports_the_breakdown():
    with pytest.raises(ValueError, match=r'a=0\.5'):
        env_config.check_probabilities_sum({'a': 0.5, 'b': 0.2}, 'label')


# ---------------------------------------------------------------------------
# Fields removed on purpose
# ---------------------------------------------------------------------------

def test_unimplemented_and_dead_fields_are_gone():
    """`gumbel_root_top_k` was never implemented and the baseline-eval fields
    had no reader; keeping them would advertise knobs that do nothing."""
    assert not hasattr(alpha_config.MCTSConfig(), 'gumbel_root_top_k')
    assert not hasattr(alpha_config.TrainConfig(), 'baseline_eval_interval_steps')
    assert not hasattr(alpha_config.TrainConfig(), 'baseline_eval_games')
