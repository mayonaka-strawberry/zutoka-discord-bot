"""Per-iteration metrics log.

A training run is hours to days long, so the two properties that matter are
that a record survives the round trip and that a bad write never takes the run
down with it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from model_common.metrics_log import append_metrics, read_metrics  # noqa: E402


def test_appends_one_record_per_call(tmp_path):
    path = tmp_path / 'metrics.jsonl'
    append_metrics(path, 1, {'loss/total': 0.5})
    append_metrics(path, 2, {'loss/total': 0.25})

    records = read_metrics(path)
    assert [record['iteration'] for record in records] == [1, 2]
    assert [record['loss/total'] for record in records] == [0.5, 0.25]
    assert len(path.read_text(encoding='utf-8').splitlines()) == 2


def test_slash_separated_scalar_names_survive_the_round_trip(tmp_path):
    """The trainer's keys are namespaced ('value/explained_variance'), which
    JSON handles but a naive CSV column scheme would not."""
    path = tmp_path / 'metrics.jsonl'
    scalars = {'value/explained_variance': 0.56, 'policy/approx_kl': 0.0071,
               'gate/promoted': 1.0, 'system/vram_allocated_gb': 4.1}
    append_metrics(path, 42, scalars)

    record = read_metrics(path)[0]
    assert {name: record[name] for name in scalars} == scalars


def test_non_serializable_values_are_stringified_not_dropped(tmp_path):
    path = tmp_path / 'metrics.jsonl'
    append_metrics(path, 1, {'device': Path('runs/checkpoints')})

    record = read_metrics(path)[0]
    assert 'device' in record
    assert isinstance(record['device'], str)


def test_unwritable_path_warns_instead_of_raising(tmp_path, capsys):
    """Losing a metrics line must never kill a run that is days in."""
    directory = tmp_path / 'not_a_file'
    directory.mkdir()

    append_metrics(directory, 1, {'loss/total': 0.5})

    assert 'could not append metrics' in capsys.readouterr().out


def test_read_skips_a_truncated_trailing_line(tmp_path):
    """An interrupt mid-write leaves a partial line; the rest must stay
    readable."""
    path = tmp_path / 'metrics.jsonl'
    append_metrics(path, 1, {'loss/total': 0.5})
    with path.open('a', encoding='utf-8') as handle:
        handle.write('{"iteration": 2, "loss/total": 0.2')

    records = read_metrics(path)
    assert len(records) == 1
    assert records[0]['iteration'] == 1
