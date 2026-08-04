"""
MatchNarrator: translates engine event tuples into player-facing messages and
the permanent game_events stream (same string event types as before, so the
summary renderer keeps working).

The driver drains ``state.event_sink`` after every ``Game.apply`` and hands the
batch here together with the projected BoardView and the per-phase BoardView
snapshots the sink captured mid-apply. Phase-change events are handed to the
GatePresenter, which posts the board/zone bundle for that boundary; the events
in between only add what a gate cannot show on its own (effect resolution
embeds, effect-driven HP swings, redraw results). Effect activity is aggregated
across applies and flushed as one embed per player batch, mirroring the
pre-port cadence. Everything is muted automatically while the transport is
muted (replay) because all sends go through the transport.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from engine_alpha.events import (
    EVENT_BATTLE_RESULT,
    EVENT_DRAW,
    EVENT_EFFECT_SKIPPED_COST,
    EVENT_EFFECT_STARTED,
    EVENT_GAME_OVER,
    EVENT_HP_CHANGED,
    EVENT_MULLIGAN_DONE,
    EVENT_PHASE_CHANGED,
)
from engine_alpha.state import PH_MULLIGAN, PH_PROCESS_EFFECTS, PH_TURN_END_EFFECTS
from zutomayo.match.gate_presenter import GatePresenter
from zutomayo.match.state_view import BoardView, definition_index_to_card

log = logging.getLogger(__name__)

# Optional bespoke narration per effect id ('XX-YYY' -> text shown when the
# effect starts). Effects without an entry get the systematic line.
EFFECT_NARRATION_OVERRIDES: dict[str, str] = {}


def describe_card(card: Any) -> dict[str, Any]:
    return {'card': [card.pack, card.id], 'name': card.name, 'effect': card.effect}


def _zone_key(view: Any) -> Optional[list[int]]:
    if view is None:
        return None
    return [view.card.pack, view.card.id]


def build_state_snapshot_from_board_view(board_view: BoardView) -> dict[str, Any]:
    """Same dict shape as the legacy build_state_snapshot, from a BoardView."""
    players = []
    for player in board_view.players:
        players.append({
            'hp': player.hp,
            'power': player.total_power,
            'hand': [[v.card.pack, v.card.id] for v in player.hand],
            'battle_zone': _zone_key(player.battle_zone),
            'set_zone_a': _zone_key(player.set_zone_a),
            'set_zone_b': _zone_key(player.set_zone_b),
            'set_zone_c': _zone_key(player.set_zone_c),
            'power_charger': [[v.card.pack, v.card.id] for v in player.power_charger],
            'abyss': [[v.card.pack, v.card.id] for v in player.abyss],
            'deck_count': player.deck_count,
        })
    return {
        'turn': board_view.turn,
        'chronos': board_view.chronos,
        'day_night': 'NIGHT' if board_view.is_night else 'DAY',
        'players': players,
    }


class MatchNarrator:
    def __init__(self, session: Any, transport: Any) -> None:
        self.session = session
        self.transport = transport
        self.gate_presenter = GatePresenter(session, transport)
        # Set by the Discord flow; headless runs narrate without images.
        self.hand_image_provider: Optional[Any] = None
        # Effect aggregation per player batch during process effects.
        self.resolved_effects: dict[int, list[Any]] = {0: [], 1: []}
        self.skipped_effects: dict[int, list[Any]] = {0: [], 1: []}
        self.current_phase: Optional[int] = None

    def _emit(self, event_type: str, payload: dict[str, Any], **context: Any) -> None:
        if self.session.persistence is not None:
            self.session.persistence.emit_event(event_type, payload, **context)

    def _player_name(self, player_index: int) -> str:
        name = self.transport.display_name(self.session, player_index)
        return name or f'Player {player_index + 1}'

    async def _send_lines(self, lines: list[str]) -> None:
        if lines:
            await self.transport.send_to_channel(self.session, content='\n'.join(lines))

    async def publish(
        self,
        engine_events: list[tuple],
        board_view: BoardView,
        snapshots: Optional[dict[int, BoardView]] = None,
    ) -> None:
        from zutomayo.engine import game_events as stream

        snapshots = snapshots or {}
        lines: list[str] = []
        for event_index, event in enumerate(engine_events):
            event_type = event[0]

            if event_type == EVENT_PHASE_CHANGED:
                await self._send_lines(lines)
                lines = []
                await self._flush_effect_embeds()
                self.current_phase = event[1]
                from zutomayo.match.state_view import PHASE_NAMES

                phase_name = PHASE_NAMES.get(event[1], str(event[1]))
                self._emit(stream.EVENT_PHASE_ENTERED, {'phase': phase_name},
                           turn=event[2], phase=phase_name)
                snapshot = snapshots.get(event_index)
                if snapshot is not None:
                    await self.gate_presenter.on_phase_entered(event[1], event[2], snapshot)

            elif event_type == EVENT_DRAW:
                player_index, count = event[1], event[2]
                if self.current_phase not in (None, PH_MULLIGAN):
                    await self.transport.send_to_player(
                        self.session, player_index,
                        content=f'You drew {count} card(s).',
                    )

            elif event_type == EVENT_MULLIGAN_DONE:
                player_index, count = event[1], event[2]
                self._emit(stream.EVENT_REDRAW, {'player_index': player_index, 'count': count})
                await self._send_redraw_result(player_index, count, board_view)

            elif event_type == EVENT_EFFECT_STARTED:
                owner_index, definition_index = event[1], event[2]
                # The engine resolves one player's whole batch before the other
                # collects, so the first event owned by the second player closes
                # out the first player's embed.
                await self._flush_effect_embeds(player_index=1 - owner_index)
                card = definition_index_to_card(definition_index)
                self.resolved_effects[owner_index].append(card)
                self._emit(stream.EVENT_EFFECT_RESOLVED,
                           {'player_index': owner_index, **describe_card(card)})
                override = EFFECT_NARRATION_OVERRIDES.get(f'{card.pack:02d}-{card.id:03d}')
                if override:
                    lines.append(override)

            elif event_type == EVENT_EFFECT_SKIPPED_COST:
                owner_index, definition_index = event[1], event[2]
                await self._flush_effect_embeds(player_index=1 - owner_index)
                card = definition_index_to_card(definition_index)
                self.skipped_effects[owner_index].append(card)
                self._emit(stream.EVENT_EFFECT_SKIPPED_COST,
                           {'player_index': owner_index, **describe_card(card)})

            elif event_type == EVENT_HP_CHANGED:
                # Battle damage is already spelled out by the battle result
                # embed at the following gate; only effect-driven swings, which
                # nothing else narrates, get a line.
                if self.current_phase in (PH_PROCESS_EFFECTS, PH_TURN_END_EFFECTS):
                    player_index, delta, new_hp = event[1], event[2], event[3]
                    if delta < 0:
                        lines.append(
                            f'{self._player_name(player_index)} took {-delta} damage (HP: {new_hp}).')
                    else:
                        lines.append(
                            f'{self._player_name(player_index)} healed {delta} (HP: {new_hp}).')

            elif event_type == EVENT_BATTLE_RESULT:
                attack_0, attack_1, winner_index, damage = event[1], event[2], event[3], event[4]
                battle_result = {
                    'player_0_attack': attack_0,
                    'player_1_attack': attack_1,
                    'winner': winner_index,
                    'damage': damage,
                }
                self._emit(stream.EVENT_BATTLE_RESULT, battle_result)
                self.gate_presenter.note_battle_result(battle_result)

            elif event_type == EVENT_GAME_OVER:
                await self._send_lines(lines)
                lines = []
                await self._flush_effect_embeds()
                self._emit(stream.EVENT_GAME_END, {'winner': event[1]})
                snapshot = snapshots.get(event_index)
                if snapshot is not None:
                    await self.gate_presenter.on_game_over(snapshot)

        await self._send_lines(lines)

    async def _send_redraw_result(
        self, player_index: int, count: int, board_view: BoardView,
    ) -> None:
        """DM only: the channel never learns how many cards an opponent swapped."""
        if not self.transport.delivers_to_player(self.session, player_index):
            return
        if not count:
            await self.transport.send_to_player(
                self.session, player_index, content='No change')
            return
        from zutomayo.ui.embeds import build_hand_embed

        player_view = board_view.players[player_index]
        await self.transport.send_to_player(
            self.session, player_index, content='New hand',
            embed=build_hand_embed(player_view),
        )
        if self.hand_image_provider is None:
            return
        hand_file = await self.hand_image_provider(list(player_view.hand))
        if hand_file:
            await self.transport.send_to_player(self.session, player_index, files=[hand_file])

    async def _flush_effect_embeds(self, player_index: Optional[int] = None) -> None:
        if self.current_phase not in (PH_PROCESS_EFFECTS, PH_TURN_END_EFFECTS):
            self.resolved_effects = {0: [], 1: []}
            self.skipped_effects = {0: [], 1: []}
            return
        from zutomayo.ui.embeds import build_effect_resolution_embed

        for index in (0, 1) if player_index is None else (player_index,):
            resolved = self.resolved_effects[index]
            skipped = self.skipped_effects[index]
            if not resolved and not skipped:
                continue
            embed = build_effect_resolution_embed(
                self._player_name(index),
                [_CardHolder(card) for card in resolved],
                [_CardHolder(card) for card in skipped],
            )
            await self.transport.send_to_channel(self.session, embed=embed)
            for recipient in range(2):
                if self.transport.delivers_to_player(self.session, recipient):
                    await self.transport.send_to_player(self.session, recipient, embed=embed)
            self.resolved_effects[index] = []
            self.skipped_effects[index] = []

    def snapshot(self, board_view: BoardView) -> None:
        from zutomayo.engine import game_events as stream

        self._emit(stream.EVENT_STATE_SNAPSHOT, build_state_snapshot_from_board_view(board_view),
                   turn=board_view.turn, phase=board_view.phase_name)


class _CardHolder:
    """Duck-typed CardInstance stand-in (.card) for embed builders."""

    def __init__(self, card: Any) -> None:
        self.card = card
