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
    EVENT_CARDS_REVEALED,
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

#: Reveals that show the top of the opponent's DECK rather than anything in hand.
DECK_TOP_REVEAL_EFFECTS = frozenset({'03-097', '03-103'})


def describe_card(card: Any) -> dict[str, Any]:
    return {'card': [card.pack, card.id], 'name': card.name, 'effect': card.effect}


def reveal_effect_details(effect_id: str) -> tuple[Optional[str], Optional[int]]:
    """(song label, per-card attack bonus) read out of the effect's own IR.

    The TAIDADA reveal line quotes both, and reading them from the catalog
    rather than hard-coding them keeps the message from drifting if
    catalog_data.py changes a bonus or reuses the family for another song.
    """
    from engine_alpha.cards import SONG_NAMES
    from engine_alpha.effects.catalog import CATALOG
    from engine_alpha.effects.ir import Sel

    entry = CATALOG.get(effect_id)
    if entry is None:
        return None, None
    song_label: Optional[str] = None
    per_card_bonus: Optional[int] = None
    for op in entry.ops:
        for argument in op[1:]:
            if isinstance(argument, Sel) and argument.song != -1:
                song_label = SONG_NAMES[argument.song]
        if op[0] == 'atk_bonus' and isinstance(op[2], tuple) and op[2][0] == 'mul':
            per_card_bonus = op[2][2]
    return song_label, per_card_bonus


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
        self.reveal_image_provider: Optional[Any] = None
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

            elif event_type == EVENT_CARDS_REVEALED:
                # The reveal is the point of these effects, so it goes out on
                # its own rather than waiting for the batched effect embed.
                await self._send_lines(lines)
                lines = []
                await self._broadcast_reveal(event)

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

    async def _broadcast_reveal(self, event: tuple) -> None:
        """Show revealed cards to both players and the channel.

        Keeps the pre-port send order - owner DM, opponent DM, channel - so a
        reveal reads the same way it always has. Two shapes: the TAIDADA
        family reveals the owner's own picks, 03-045 reveals the opponent's
        whole hand, and `revealed_owner_index` is what tells them apart.
        """
        owner_index, revealed_owner_index = event[1], event[2]
        source_card = definition_index_to_card(event[3])
        effect_id = f'{source_card.pack:02d}-{source_card.id:03d}'
        revealed = [definition_index_to_card(index) for index in event[4:]]

        if not revealed:
            # Owner only, as the pre-port announce_effect_fizzle was: a reveal
            # that did not happen tells the opponent nothing.
            await self.transport.send_to_player(
                self.session, owner_index,
                content=f'**Effect ({effect_id}):** Nothing revealed. No effect.',
            )
            return

        names = ', '.join(card.name for card in revealed)
        owner_embed = None

        if revealed_owner_index == owner_index:
            song_label, per_card_bonus = reveal_effect_details(effect_id)
            noun = f'{song_label} character(s)' if song_label else 'card(s)'
            owner_message = (
                f'**Effect ({effect_id}):** Revealed {len(revealed)} {noun}: {names}.')
            if per_card_bonus:
                owner_message += f' Attack +{len(revealed) * per_card_bonus}!'
            other_message = (
                f'**Effect ({effect_id}):** Opponent revealed '
                f'{len(revealed)} {noun}: {names}.')
            channel_message = (
                f'**Effect ({effect_id}):** {self._player_name(owner_index)} revealed '
                f'{len(revealed)} {noun}: {names}.')
        elif effect_id in DECK_TOP_REVEAL_EFFECTS:
            # 03-097 / 03-103 show the top of the opponent's DECK, which stays
            # where it is (Q&A No.45). Calling that a hand reveal would be wrong
            # twice over: wrong zone, and it implies the whole hand.
            owner_message = (
                f'**Effect ({effect_id}):** Opponent\'s top deck card revealed: {names}.')
            other_message = (
                f'**Effect ({effect_id}):** Your top deck card was revealed: {names}.')
            channel_message = (
                f'**Effect ({effect_id}):** '
                f"{self._player_name(revealed_owner_index)}'s top deck card "
                f'revealed: {names}.')
        elif len(revealed) == 1:
            # The name-guess family picks ONE card out of the opponent's hand and
            # shows it; the rest of the hand stays hidden.
            owner_message = (
                f'**Effect ({effect_id}):** Revealed from opponent\'s hand: {names}.')
            other_message = (
                f'**Effect ({effect_id}):** A card was revealed from your hand: {names}.')
            channel_message = (
                f'**Effect ({effect_id}):** '
                f"a card from {self._player_name(revealed_owner_index)}'s hand "
                f'revealed: {names}.')
        else:
            from types import SimpleNamespace

            from zutomayo.ui.embeds import build_hand_embed

            # Built from the event, not the BoardView: 03-045 shuffles the hand
            # immediately after revealing it, so the board already holds the
            # post-shuffle order by the time this batch is narrated.
            owner_embed = build_hand_embed(
                SimpleNamespace(hand=[_CardHolder(card) for card in revealed]))
            owner_embed.title = "Opponent's Hand [相手の手札]"
            owner_message = f"**Effect ({effect_id}):** Opponent's hand revealed!"
            other_message = (
                f'**Effect ({effect_id}):** Your hand has been revealed: {names}.')
            channel_message = (
                f'**Effect ({effect_id}):** '
                f"{self._player_name(revealed_owner_index)}'s hand revealed: {names}.")

        await self._send_reveal(owner_index, owner_message, revealed, embed=owner_embed)
        await self._send_reveal(1 - owner_index, other_message, revealed)
        await self._send_reveal(None, channel_message, revealed)

    async def _send_reveal(
        self,
        player_index: Optional[int],
        content: str,
        revealed: list,
        embed: Any = None,
    ) -> None:
        """One leg of a reveal broadcast; `player_index` None means the channel.

        The image is rendered here rather than once by the caller because a
        discord.File is consumed on send, so each leg needs its own.
        """
        if player_index is not None and not self.transport.delivers_to_player(
                self.session, player_index):
            return
        kwargs: dict[str, Any] = {'content': content}
        if embed is not None:
            kwargs['embed'] = embed
        if self.reveal_image_provider is not None:
            reveal_image = await self.reveal_image_provider(revealed, columns=len(revealed))
            if reveal_image:
                kwargs['files'] = [reveal_image]
        if player_index is None:
            await self.transport.send_to_channel(self.session, **kwargs)
        else:
            await self.transport.send_to_player(self.session, player_index, **kwargs)

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
