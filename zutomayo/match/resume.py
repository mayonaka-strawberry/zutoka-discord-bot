"""
Rebuild and replay engine_alpha matches from their persisted records.

The core primitive: reconstruct the Game from the manifest (seed + decks),
put the broker in replay mode with the loaded decision log, and run the
normal driver with the transport muted. Deterministic replay reproduces the
exact state; when the log is exhausted the broker goes live and play
continues. The full startup/resume orchestration (session rebuilding,
Discord announcements) is wired by the match flow.
"""

from __future__ import annotations

import logging
from typing import Any

from zutomayo.match.persistence import definition_indices_for_card_keys

log = logging.getLogger(__name__)


def rebuild_game_from_manifest(manifest: dict[str, Any]) -> Any:
    """Reconstruct the engine game exactly as it was first created."""
    from engine_alpha.game import Game

    decks = (
        definition_indices_for_card_keys(manifest['deck_0']),
        definition_indices_for_card_keys(manifest['deck_1']),
    )
    return Game(seed=manifest['random_seed'], mode='fixed_decks', decks=decks)


async def load_replay_state(broker: Any, game_id: str) -> None:
    """Load the persisted decision log into a broker and enter replay mode."""
    from zutomayo.match.persistence import load_match_decision_log

    broker.replay_log = await load_match_decision_log(game_id)
    broker.replaying = bool(broker.replay_log)


RESUME_ANNOUNCEMENT = '**Game resumed after bot restart.**'
DIVERGENCE_ANNOUNCEMENT = 'This game could not be resumed after the update and has been ended.'


async def resume_all(bot: Any) -> None:
    from zutomayo.engine.game_persistence import list_game_ids_with_status

    game_ids = await list_game_ids_with_status('active')
    if not game_ids:
        return
    log.info('Resuming %d in-flight game(s)', len(game_ids))
    for game_id in game_ids:
        try:
            await resume_game(bot, game_id)
        except Exception:
            log.exception('Failed to resume game %s', game_id)
            await _mark_divergence_failed(game_id)


async def resume_game(
    bot: Any,
    game_id: str,
    *,
    channel_id_override: int | None = None,
    announcement: str = RESUME_ANNOUNCEMENT,
) -> Any:
    """Rebuild and replay one persisted game, dispatching by manifest schema
    version: engine_alpha games run here; legacy records go to the legacy
    resume manager (which exists until the legacy engine is deleted)."""
    from zutomayo.engine.game_persistence import load_manifest
    from zutomayo.match.persistence import SCHEMA_VERSION_ENGINE_ALPHA

    manifest = await load_manifest(game_id)
    if manifest is None:
        raise ValueError(f'No game record found for {game_id}.')
    if manifest.get('schema_version', 1) < SCHEMA_VERSION_ENGINE_ALPHA:
        from zutomayo.engine import resume_manager as legacy_resume_manager

        return await legacy_resume_manager.resume_game(
            bot, game_id,
            channel_id_override=channel_id_override, announcement=announcement,
        )
    if channel_id_override is not None:
        manifest['channel_id'] = channel_id_override

    import asyncio

    from zutomayo.engine.game_persistence import next_event_index
    from zutomayo.engine.game_session import session_manager
    from zutomayo.match.match_flow import SingleMatchFlow
    from zutomayo.match.persistence import MatchRecordStore

    session = _rebuild_session(manifest)
    session_manager.active_games[session.game_id] = session
    for discord_id, player_index in manifest['player_discord_ids']:
        if discord_id != 0:
            session_manager.player_to_game[discord_id] = session.game_id

    from zutomayo.data.deck_validator import get_card_index
    from zutomayo.engine.game_persistence import resolve_card_keys

    _, card_index = get_card_index()

    if manifest.get('is_tcg'):
        from zutomayo.match.series_flow import TcgSeriesFlow

        series_flow = TcgSeriesFlow(bot, manifest['best_of'])
        series_flow.match_flow._ensure_decision_runtime(session)
        resumed_decks = (
            resolve_card_keys(manifest['deck_0'], card_index),
            resolve_card_keys(manifest['side_0'], card_index),
            resolve_card_keys(manifest['deck_1'], card_index),
            resolve_card_keys(manifest['side_1'], card_index),
        )
        entry_coroutine = series_flow.run_tcg(session, resumed_decks=resumed_decks)
    else:
        flow = SingleMatchFlow(bot)
        flow._ensure_decision_runtime(session)
        deck_0 = resolve_card_keys(manifest['deck_0'], card_index)
        deck_1 = resolve_card_keys(manifest['deck_1'], card_index)
        entry_coroutine = _run_single_resumed_match(flow, session, deck_0, deck_1)

    session.persistence = MatchRecordStore.attach_for_resume(game_id, session)
    session.persistence.next_event_index = await next_event_index(game_id)
    session.broker.persistence = session.persistence
    await load_replay_state(session.broker, game_id)
    session.transport.muted = True
    session.broker.on_go_live = _make_go_live_callback(session, announcement)

    session.game_task = asyncio.get_running_loop().create_task(
        _run_resumed_game(session, entry_coroutine)
    )
    log.info(
        'Resuming game %s (%s, %d logged decisions)',
        session.game_id, manifest['mode'], len(session.broker.replay_log),
    )
    return session


def _rebuild_session(manifest: dict[str, Any]) -> Any:
    from zutomayo.engine.game_session import GameSession

    ordered_ids = manifest['player_discord_ids']
    creator_id = ordered_ids[0][0]
    session = GameSession(
        game_id=manifest['game_id'],
        channel_id=manifest['channel_id'],
        creator_id=creator_id,
    )
    for discord_id, player_index in ordered_ids[1:]:
        session.add_player(discord_id)
    session.is_solo = manifest.get('is_solo', False)
    session.solo_difficulty = manifest.get('solo_difficulty', 'normal')
    session.is_tcg = manifest.get('is_tcg', False)
    session.best_of = manifest.get('best_of', 0)
    session.player_deck_names = {
        int(index): name for index, name in manifest.get('player_deck_names', {}).items()
    }
    session.random_seed = manifest['random_seed']
    return session


async def _run_single_resumed_match(flow: Any, session: Any, deck_0: list, deck_1: list) -> None:
    """Mirror run_game without the pre-persistence deck-building phase."""
    from zutomayo.engine.game_session import session_manager

    await flow.run_single_match(session, deck_0, deck_1)
    await flow.finalize_completed_game(session)
    session_manager.remove_game(session.game_id)


async def _run_resumed_game(session: Any, entry_coroutine: Any) -> None:
    import asyncio

    from zutomayo.engine.game_session import session_manager
    from zutomayo.match.broker import MatchResumeDivergenceError

    try:
        await entry_coroutine
    except MatchResumeDivergenceError:
        log.warning('Replay divergence for game %s; ending it without a result', session.game_id)
        await _announce_divergence(session)
        await _mark_divergence_failed(session.game_id)
        session_manager.remove_game(session.game_id)
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception('Resumed game %s failed', session.game_id)
        await _announce_divergence(session)
        await _mark_divergence_failed(session.game_id)
        session_manager.remove_game(session.game_id)


async def _mark_divergence_failed(game_id: str) -> None:
    from zutomayo.engine.game_persistence import STATUS_DIVERGENCE_FAILED, GameRecordStore

    try:
        await GameRecordStore.attach_for_resume(game_id).set_status(STATUS_DIVERGENCE_FAILED)
    except Exception:
        log.exception('Failed to mark game %s divergence_failed', game_id)


async def _announce_divergence(session: Any) -> None:
    try:
        if session.transport is None:
            return
        session.transport.muted = False
        await session.transport.send_to_channel(session, content=DIVERGENCE_ANNOUNCEMENT)
        for player_index in range(2):
            await session.transport.send_to_player(session, player_index, content=DIVERGENCE_ANNOUNCEMENT)
    except Exception:
        log.exception('Failed to announce resume failure for game %s', session.game_id)


def _make_go_live_callback(session: Any, announcement: str = RESUME_ANNOUNCEMENT):
    async def go_live() -> None:
        from zutomayo.engine.game_events import EVENT_GAME_RESUMED
        from zutomayo.enums.chronos import Chronos
        from zutomayo.match.state_view import project_board_view
        from zutomayo.ui.board_renderer import render_board_image_off_thread

        session.transport.muted = False
        if session.persistence is not None:
            session.persistence.emit_event(EVENT_GAME_RESUMED, {'channel_id': session.channel_id})
        try:
            if session.game is not None:
                names = {}
                for discord_id, index in session.player_discord_ids.items():
                    names[index] = session.transport.display_name(session, index) or f'Player {index + 1}'
                board_view = project_board_view(session.game, names)
                board_file = await render_board_image_off_thread(board_view, Chronos.DAY)
                await session.transport.send_to_channel(
                    session, content=announcement, files=[board_file],
                )
                for player_index in range(2):
                    player_view = board_view.players[player_index]
                    board_file = await render_board_image_off_thread(board_view, player_view.side)
                    await session.transport.send_to_player(
                        session, player_index,
                        content=announcement, files=[board_file],
                    )
            else:
                await session.transport.send_to_channel(session, content=announcement)
        except Exception:
            log.exception('Failed to announce resume for game %s', session.game_id)

    return go_live
