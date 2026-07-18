"""
Round-trip tests for the database export/import scripts.

The JSON export/import tests need ZUTOKA_TEST_DATABASE_URL (integration
tier); the binary-locator tests are pure units. The pg_dump round trip
additionally self-skips when no pg_dump binary can be located.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))
SCRIPTS_DIRECTORY = REPOSITORY_ROOT / 'scripts'
if str(SCRIPTS_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIRECTORY))

import pytest

from tests.support.database_support import run_with_database, truncate_data_tables
from zutomayo.data import database
from zutomayo.data.player_storage import record_match_result
from zutomayo.engine.game_persistence import GameRecordStore
from zutomayo.engine.game_session import GameSession


def _load_script_module(script_name: str):
    script_path = SCRIPTS_DIRECTORY / f'{script_name}.py'
    specification = importlib.util.spec_from_file_location(script_name, script_path)
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


async def _seed_representative_data() -> None:
    """One finished game with decisions, events, elo history, plus decks and names."""
    from engine_alpha.actions import select_number, P_EFFECT_NUMBER
    from zutomayo.match.decisions import (
        KIND_NUMBER_CHOICE, PAYLOAD_ACTION, MatchDecisionRequest, MatchDecisionResponse,
    )
    from zutomayo.match.persistence import MatchRecordStore

    session = GameSession(game_id='20260710-00000', channel_id=7, creator_id=111)
    session.add_player(222)
    session.random_seed = 2 ** 63 + 99  # exercises the NUMERIC path
    store = await MatchRecordStore.create_for_match(
        session, 'standard', engine_seed=session.random_seed, deck_card_keys={},
    )

    request = MatchDecisionRequest(
        kind=KIND_NUMBER_CHOICE, player_index=0, prompt_text='number',
        engine_request=select_number(P_EFFECT_NUMBER, 0, 3),
        purpose=P_EFFECT_NUMBER,
        minimum_value=0, maximum_value=3,
    )
    request.sequence_number = 0
    await store.append_decision(request, MatchDecisionResponse(0, PAYLOAD_ACTION, 2))
    store.emit_event('phase_entered', {'chronos': 3, 'day_night': 'DAY'}, turn=1, phase='SETUP')
    await store.set_status('completed', winner_index=0,
                           result_summary={'result': 'PLAYER_1_WIN', 'turns': 4})
    await record_match_result(111, 222, 'Alpha Deck', None, 0,
                              mode='standard', is_solo=False, game_id='20260710-00000')

    async with database.get_pool().acquire() as connection:
        await connection.execute(
            'INSERT INTO decks (user_id, name, cards) VALUES ($1, $2, $3)',
            111, 'Alpha Deck', [{'pack': 1, 'id': 13}],
        )
        await connection.execute(
            'INSERT INTO display_names (user_id, name, custom) VALUES ($1, $2, $3)',
            111, 'Alpha', True,
        )
        await connection.execute(
            "INSERT INTO daily_game_counters (day, next_counter) VALUES ('2026-07-10', 1)",
        )


async def _table_counts() -> dict[str, int]:
    from database_transfer import TABLE_SPECIFICATIONS

    async with database.get_pool().acquire() as connection:
        return {
            specification.name: await connection.fetchval(
                f'SELECT count(*) FROM {specification.name}',
            )
            for specification in TABLE_SPECIFICATIONS
        }


def test_json_export_import_round_trip(integration_database_url, tmp_path, monkeypatch):
    import zutomayo.engine.game_persistence as game_persistence_module

    monkeypatch.setattr(
        game_persistence_module, 'backend', game_persistence_module.PostgresGameRecordBackend(),
    )
    monkeypatch.setattr(
        sys.modules['zutomayo.data.player_storage'], 'backend',
        sys.modules['zutomayo.data.player_storage'].PostgresProfileBackend(),
    )
    export_module = _load_script_module('export_database')
    import_module = _load_script_module('import_database')
    export_path = tmp_path / 'export.json'

    async def round_trip():
        await _seed_representative_data()
        original_counts = await _table_counts()

        async with database.get_pool().acquire() as connection:
            original_game = dict(await connection.fetchrow(
                'SELECT * FROM games WHERE game_id = $1', '20260710-00000',
            ))

        await database.close_pool()
        await export_module.export(integration_database_url, export_path)

        # Wipe and restore.
        await database.initialize_pool(dsn=integration_database_url)
        await truncate_data_tables()
        await database.close_pool()
        await import_module.import_export_file(
            export_path, integration_database_url, replace=False, dry_run=False,
        )

        # Import a second time: idempotent.
        await import_module.import_export_file(
            export_path, integration_database_url, replace=False, dry_run=False,
        )

        await database.initialize_pool(dsn=integration_database_url)
        restored_counts = await _table_counts()
        async with database.get_pool().acquire() as connection:
            restored_game = dict(await connection.fetchrow(
                'SELECT * FROM games WHERE game_id = $1', '20260710-00000',
            ))
            elo_rows = await connection.fetch('SELECT * FROM elo_history ORDER BY user_id')
        return original_counts, restored_counts, original_game, restored_game, elo_rows

    original_counts, restored_counts, original_game, restored_game, elo_rows = run_with_database(
        integration_database_url, round_trip,
    )

    assert restored_counts == original_counts, 'every table restores to the same row count'
    assert original_counts['games'] == 1 and original_counts['elo_history'] == 2
    assert restored_game == original_game, 'timestamps, NUMERIC seed, and JSONB survive the round trip'
    assert int(restored_game['random_seed']) == 2 ** 63 + 99
    assert [row['user_id'] for row in elo_rows] == [111, 222]


def test_json_import_replace_and_dry_run(integration_database_url, tmp_path, monkeypatch):
    import zutomayo.engine.game_persistence as game_persistence_module

    monkeypatch.setattr(
        game_persistence_module, 'backend', game_persistence_module.PostgresGameRecordBackend(),
    )
    monkeypatch.setattr(
        sys.modules['zutomayo.data.player_storage'], 'backend',
        sys.modules['zutomayo.data.player_storage'].PostgresProfileBackend(),
    )
    export_module = _load_script_module('export_database')
    import_module = _load_script_module('import_database')
    export_path = tmp_path / 'export.json'

    async def exercise():
        await _seed_representative_data()
        await database.close_pool()
        await export_module.export(integration_database_url, export_path)

        # Dry run against an empty database writes nothing.
        await database.initialize_pool(dsn=integration_database_url)
        await truncate_data_tables()
        await database.close_pool()
        await import_module.import_export_file(
            export_path, integration_database_url, replace=False, dry_run=True,
        )
        await database.initialize_pool(dsn=integration_database_url)
        dry_run_counts = await _table_counts()

        # --replace removes rows that are not in the export.
        async with database.get_pool().acquire() as connection:
            await connection.execute(
                'INSERT INTO display_names (user_id, name, custom) VALUES ($1, $2, $3)',
                999, 'Straggler', False,
            )
        await database.close_pool()
        await import_module.import_export_file(
            export_path, integration_database_url, replace=True, dry_run=False,
        )
        await database.initialize_pool(dsn=integration_database_url)
        async with database.get_pool().acquire() as connection:
            straggler = await connection.fetchval(
                'SELECT count(*) FROM display_names WHERE user_id = 999',
            )
            restored_names = await connection.fetchval('SELECT count(*) FROM display_names')
        return dry_run_counts, straggler, restored_names

    dry_run_counts, straggler, restored_names = run_with_database(
        integration_database_url, exercise,
    )
    data_table_counts = {
        table: count for table, count in dry_run_counts.items() if table != 'schema_metadata'
    }
    # apply_schema() legitimately seeds schema_metadata outside the transaction.
    assert all(count == 0 for count in data_table_counts.values()), 'dry run writes no data'
    assert straggler == 0, '--replace removes rows absent from the export'
    assert restored_names == 1


class TestBinaryLocator:
    def test_pgbin_takes_precedence(self, tmp_path, monkeypatch):
        from postgresql_tools import locate_postgresql_binary

        executable_name = 'pg_dump.exe' if sys.platform == 'win32' else 'pg_dump'
        (tmp_path / executable_name).write_bytes(b'')
        monkeypatch.setenv('PGBIN', str(tmp_path))
        assert locate_postgresql_binary('pg_dump') == str(tmp_path / executable_name)

    def test_missing_pgbin_binary_raises_with_hint(self, tmp_path, monkeypatch):
        from postgresql_tools import locate_postgresql_binary

        monkeypatch.setenv('PGBIN', str(tmp_path))
        with pytest.raises(FileNotFoundError, match='PGBIN'):
            locate_postgresql_binary('pg_dump')

    def test_path_lookup_is_used_without_pgbin(self, monkeypatch):
        from postgresql_tools import locate_postgresql_binary

        monkeypatch.delenv('PGBIN', raising=False)
        monkeypatch.setattr('shutil.which', lambda name: '/somewhere/pg_dump')
        assert locate_postgresql_binary('pg_dump') == '/somewhere/pg_dump'


def test_pg_dump_round_trip(integration_database_url, tmp_path, monkeypatch):
    """Full pg_dump -> pg_restore cycle; skipped when no binaries are available."""
    from postgresql_tools import locate_postgresql_binary

    try:
        pg_dump_path = locate_postgresql_binary('pg_dump')
        pg_restore_path = locate_postgresql_binary('pg_restore')
    except FileNotFoundError:
        pytest.skip('pg_dump / pg_restore not available on this machine')

    import zutomayo.engine.game_persistence as game_persistence_module

    monkeypatch.setattr(
        game_persistence_module, 'backend', game_persistence_module.PostgresGameRecordBackend(),
    )
    monkeypatch.setattr(
        sys.modules['zutomayo.data.player_storage'], 'backend',
        sys.modules['zutomayo.data.player_storage'].PostgresProfileBackend(),
    )
    dump_path = tmp_path / 'round-trip.dump'

    async def seed_and_count():
        await _seed_representative_data()
        return await _table_counts()

    original_counts = run_with_database(integration_database_url, seed_and_count)
    # run_with_database truncates on entry, so dump before it runs again:
    # re-seed, dump, truncate, restore, count.

    async def seed_only():
        await _seed_representative_data()

    run_with_database(integration_database_url, seed_only)
    subprocess.run(
        [pg_dump_path, '--format=custom', '--file', str(dump_path), integration_database_url],
        check=True,
    )

    async def wipe():
        await truncate_data_tables()

    run_with_database(integration_database_url, wipe)
    subprocess.run(
        [pg_restore_path, '--clean', '--if-exists', '--no-owner',
         '--dbname', integration_database_url, str(dump_path)],
        check=True,
    )

    async def count_without_truncating():
        async with database.get_pool().acquire() as connection:
            from database_transfer import TABLE_SPECIFICATIONS

            return {
                specification.name: await connection.fetchval(
                    f'SELECT count(*) FROM {specification.name}',
                )
                for specification in TABLE_SPECIFICATIONS
            }

    async def restored_counts_runner():
        await database.initialize_pool(dsn=integration_database_url)
        try:
            return await count_without_truncating()
        finally:
            await database.close_pool()

    import asyncio

    restored_counts = asyncio.run(restored_counts_runner())
    assert restored_counts == original_counts
