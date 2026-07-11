"""
Shared table specifications for the JSON database export/import scripts.

The export file is plain JSON, so every non-JSON column type needs an explicit
serializer: TIMESTAMPTZ and DATE become ISO strings, NUMERIC (the 64-bit
games.random_seed) becomes a string. JSONB columns round-trip as-is through
the pool's JSONB codec. Tables are listed in foreign-key-safe insert order
(parents before children).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class TableSpecification:
    name: str
    primary_key: tuple[str, ...]
    columns: tuple[str, ...]
    timestamp_columns: frozenset[str] = field(default_factory=frozenset)
    date_columns: frozenset[str] = field(default_factory=frozenset)
    numeric_columns: frozenset[str] = field(default_factory=frozenset)


TABLE_SPECIFICATIONS: tuple[TableSpecification, ...] = (
    TableSpecification(
        name='schema_metadata',
        primary_key=('key',),
        columns=('key', 'value'),
    ),
    TableSpecification(
        name='player_profiles',
        primary_key=('user_id',),
        columns=(
            'user_id', 'last_updated',
            'elo', 'elo_peak', 'elo_games', 'tcg_elo', 'tcg_elo_peak', 'tcg_elo_games',
            'stats', 'deck_stats', 'opponent_stats',
        ),
        timestamp_columns=frozenset({'last_updated'}),
    ),
    TableSpecification(
        name='display_names',
        primary_key=('user_id',),
        columns=('user_id', 'name', 'custom', 'updated_at'),
        timestamp_columns=frozenset({'updated_at'}),
    ),
    TableSpecification(
        name='decks',
        primary_key=('user_id', 'name'),
        columns=('user_id', 'name', 'cards', 'created_at', 'updated_at'),
        timestamp_columns=frozenset({'created_at', 'updated_at'}),
    ),
    TableSpecification(
        name='decks_tcg',
        primary_key=('user_id', 'name'),
        columns=('user_id', 'name', 'main_deck', 'side_deck', 'created_at', 'updated_at'),
        timestamp_columns=frozenset({'created_at', 'updated_at'}),
    ),
    TableSpecification(
        name='daily_game_counters',
        primary_key=('day',),
        columns=('day', 'next_counter'),
        date_columns=frozenset({'day'}),
    ),
    TableSpecification(
        name='games',
        primary_key=('game_id',),
        columns=(
            'game_id', 'schema_version', 'status', 'mode', 'channel_id',
            'is_solo', 'solo_difficulty', 'is_tcg', 'best_of',
            'random_seed', 'manifest', 'winner_index', 'result_summary',
            'created_at', 'saved_at', 'ended_at',
        ),
        timestamp_columns=frozenset({'created_at', 'saved_at', 'ended_at'}),
        numeric_columns=frozenset({'random_seed'}),
    ),
    TableSpecification(
        name='game_players',
        primary_key=('game_id', 'player_index'),
        columns=('game_id', 'player_index', 'discord_id', 'deck_name'),
    ),
    TableSpecification(
        name='game_decisions',
        primary_key=('game_id', 'sequence_number'),
        columns=('game_id', 'sequence_number', 'fingerprint', 'payload_type', 'payload', 'recorded_at'),
        timestamp_columns=frozenset({'recorded_at'}),
    ),
    TableSpecification(
        name='game_events',
        primary_key=('game_id', 'event_index'),
        columns=(
            'game_id', 'event_index', 'match_number', 'turn', 'phase',
            'event_type', 'payload', 'recorded_at',
        ),
        timestamp_columns=frozenset({'recorded_at'}),
    ),
    TableSpecification(
        name='elo_history',
        primary_key=('game_id', 'user_id', 'ladder'),
        columns=('game_id', 'user_id', 'ladder', 'elo_before', 'elo_after', 'recorded_at'),
        timestamp_columns=frozenset({'recorded_at'}),
    ),
)


def serialize_value(specification: TableSpecification, column: str, value: Any) -> Any:
    if value is None:
        return None
    if column in specification.timestamp_columns:
        assert isinstance(value, datetime), (specification.name, column, value)
        return value.isoformat()
    if column in specification.date_columns:
        assert isinstance(value, date), (specification.name, column, value)
        return value.isoformat()
    if column in specification.numeric_columns:
        assert isinstance(value, (Decimal, int)), (specification.name, column, value)
        return str(value)
    return value


def deserialize_value(specification: TableSpecification, column: str, value: Any) -> Any:
    if value is None:
        return None
    if column in specification.timestamp_columns:
        return datetime.fromisoformat(value)
    if column in specification.date_columns:
        return date.fromisoformat(value)
    if column in specification.numeric_columns:
        return int(value)
    return value


def serialize_row(specification: TableSpecification, row: Any) -> dict[str, Any]:
    return {
        column: serialize_value(specification, column, row[column])
        for column in specification.columns
    }


def deserialize_row(specification: TableSpecification, row: dict[str, Any]) -> list[Any]:
    return [
        deserialize_value(specification, column, row.get(column))
        for column in specification.columns
    ]


def build_upsert_sql(specification: TableSpecification) -> str:
    columns = specification.columns
    placeholders = ', '.join(f'${position}' for position in range(1, len(columns) + 1))
    non_key_columns = [column for column in columns if column not in specification.primary_key]
    conflict_action = (
        'DO UPDATE SET ' + ', '.join(f'{column} = EXCLUDED.{column}' for column in non_key_columns)
        if non_key_columns else 'DO NOTHING'
    )
    return (
        f'INSERT INTO {specification.name} ({", ".join(columns)}) '
        f'VALUES ({placeholders}) '
        f'ON CONFLICT ({", ".join(specification.primary_key)}) {conflict_action}'
    )
