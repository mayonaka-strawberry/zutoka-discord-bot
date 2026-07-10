"""
Resume manager: restores in-flight games after a bot restart.

For every game whose record status is 'active', the persisted manifest and
decision log are loaded, the GameSession is rebuilt, and the game coroutine is
re-run from move zero with the transport muted and the broker feeding logged
decisions back instantly (deterministic replay: the per-game RNG seed
regenerates the coin flip and every shuffle). When the log runs out, the
broker unmutes the transport, announces the resume, and presents the pending
decision live with a fresh timeout window.

Failure modes: a corrupt record or a replay divergence (the code changed the
prompt sequence since the log was written) ends that game with an apology
message, records no result, and marks the game divergence_failed. The record
is kept — the played portion still has a summary. One bad game never blocks
the others or startup.

resume_game is shared between startup resume-all and the /zutomayo resume
command (Stage 7), which resumes a 'saved' game on demand.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Optional

from zutomayo.engine.decision_broker import ResumeDivergenceError
from zutomayo.engine.game_persistence import (
    STATUS_DIVERGENCE_FAILED,
    GameRecordStore,
    list_game_ids_with_status,
    load_decision_log,
    load_manifest,
    resolve_card_keys,
)
from zutomayo.engine.game_session import GameSession, session_manager

if TYPE_CHECKING:
    import discord

log = logging.getLogger(__name__)

RESUME_ANNOUNCEMENT = '**Game resumed after bot restart.**'
DIVERGENCE_ANNOUNCEMENT = 'This game could not be resumed after the update and has been ended.'


async def resume_all(bot: 'discord.Client') -> None:
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
    bot: 'discord.Client',
    game_id: str,
    *,
    channel_id_override: Optional[int] = None,
    announcement: str = RESUME_ANNOUNCEMENT,
) -> GameSession:
    """
    Rebuild and replay one persisted game. Used at startup for 'active' games
    and by the resume command for 'saved' games (which sets the status back to
    'active' and may move the game to the invoking channel first).
    """
    manifest = await load_manifest(game_id)
    if manifest is None:
        raise ValueError(f'No game record found for {game_id}.')
    if channel_id_override is not None:
        manifest['channel_id'] = channel_id_override
    session = _rebuild_session(manifest)

    session_manager.active_games[session.game_id] = session
    for discord_id, player_index in manifest['player_discord_ids']:
        if discord_id != 0:
            session_manager.player_to_game[discord_id] = session.game_id

    flow, entry_coroutine = _build_flow_and_entry(bot, session, manifest)

    # The flow's runtime factory installed the right adapters; now switch the
    # broker into replay mode and mute all output until the log is exhausted.
    from zutomayo.engine.game_persistence import next_event_index

    session.persistence = GameRecordStore.attach_for_resume(game_id, session)
    session.persistence.next_event_index = await next_event_index(game_id)
    session.broker.persistence = session.persistence
    session.broker.replay_log = await load_decision_log(game_id)
    session.broker.replaying = True
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


async def _mark_divergence_failed(game_id: str) -> None:
    try:
        await GameRecordStore.attach_for_resume(game_id).set_status(STATUS_DIVERGENCE_FAILED)
    except Exception:
        log.exception('Failed to mark game %s divergence_failed', game_id)


def _rebuild_session(manifest: dict[str, Any]) -> GameSession:
    import random

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
    session.random_generator = random.Random(session.random_seed)
    return session


def _build_flow_and_entry(bot: 'discord.Client', session: GameSession, manifest: dict[str, Any]):
    """Create the mode-appropriate flow (installing its decision runtime) and
    the coroutine that re-enters the match with the manifest's decks."""
    from zutomayo.data.deck_validator import get_card_index

    _, card_index = get_card_index()
    mode = manifest['mode']

    if mode == 'tcg':
        from zutomayo.engine.tcg_match_flow import TcgMatchFlow

        flow = TcgMatchFlow(bot, manifest['best_of'])
        flow.game_flow._ensure_decision_runtime(session)
        resumed_decks = (
            resolve_card_keys(manifest['deck_0'], card_index),
            resolve_card_keys(manifest['side_0'], card_index),
            resolve_card_keys(manifest['deck_1'], card_index),
            resolve_card_keys(manifest['side_1'], card_index),
        )
        return flow, flow.run_tcg(session, resumed_decks=resumed_decks)

    deck_0 = resolve_card_keys(manifest['deck_0'], card_index)
    deck_1 = resolve_card_keys(manifest['deck_1'], card_index)

    if mode == 'solo':
        from zutomayo.engine.bot_agent import create_bot_agent, create_bot_agent_easy
        from zutomayo.engine.solo_game_flow import SoloGameFlow

        # Past bot decisions replay from the log; the agent is only consulted
        # for decisions after the game goes live again.
        if session.solo_difficulty == 'easy':
            flow = SoloGameFlow(bot, bot_agent=create_bot_agent_easy(), use_easy_decks=True)
        else:
            flow = SoloGameFlow(bot)
        flow._ensure_decision_runtime(session)
        return flow, _resume_single_match(flow, session, deck_0, deck_1)

    from zutomayo.engine.game_flow import GameFlow

    flow = GameFlow(bot)
    flow._ensure_decision_runtime(session)
    return flow, _resume_single_match(flow, session, deck_0, deck_1)


async def _resume_single_match(flow: Any, session: GameSession, deck_0, deck_1) -> None:
    """Mirror run_game without the pre-persistence deck-building phase."""
    await flow.run_single_match(session, deck_0, deck_1)
    await flow.finalize_completed_game(session)
    session_manager.remove_game(session.game_id)


async def _run_resumed_game(session: GameSession, entry_coroutine) -> None:
    try:
        await entry_coroutine
    except ResumeDivergenceError:
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


async def _announce_divergence(session: GameSession) -> None:
    try:
        if session.transport is None:
            return
        session.transport.muted = False
        await session.transport.send_to_channel(session, content=DIVERGENCE_ANNOUNCEMENT)
        for player_index in range(2):
            await session.transport.send_to_player(session, player_index, content=DIVERGENCE_ANNOUNCEMENT)
    except Exception:
        log.exception('Failed to announce resume failure for game %s', session.game_id)


def _make_go_live_callback(session: GameSession, announcement: str = RESUME_ANNOUNCEMENT):
    async def go_live() -> None:
        from zutomayo.engine.game_events import EVENT_GAME_RESUMED
        from zutomayo.enums.chronos import Chronos
        from zutomayo.ui.board_renderer import render_board_image_off_thread

        session.transport.muted = False
        if session.persistence is not None:
            session.persistence.emit_event(EVENT_GAME_RESUMED, {'channel_id': session.channel_id})
        try:
            game_state = session.game_state
            if game_state is not None:
                board_file = await render_board_image_off_thread(game_state, Chronos.DAY)
                await session.transport.send_to_channel(
                    session, content=announcement, files=[board_file],
                )
                for player_index in range(2):
                    player = game_state.players[player_index]
                    board_file = await render_board_image_off_thread(game_state, player.side)
                    await session.transport.send_to_player(
                        session, player_index,
                        content=announcement, files=[board_file],
                    )
            else:
                await session.transport.send_to_channel(session, content=announcement)
        except Exception:
            log.exception('Failed to announce resume for game %s', session.game_id)

    return go_live
