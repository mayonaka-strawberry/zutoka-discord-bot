"""
Phase gates: the board/zone/embed bundle posted at every phase boundary.

The engine runs a whole chain of phases inside one ``Game.apply`` (set cards ->
reveal -> chronos -> swaps -> effects -> battle -> end turn), so a driver that
only projects the board after ``apply`` returns can never show the intermediate
states - by then the set cards have been revealed and moved out of their set
slots. ``SnapshottingEventSink`` solves that without touching the engine: it is
the plain list the engine already appends events to, and it materializes a
BoardView at the instant each phase-change event is appended. Because the
engine emits that event after the handler that set the new phase returns but
before the next handler runs, the snapshot taken when PH_REVEAL is entered
still shows both players' cards face-down in their set slots.

GatePresenter turns those snapshots into the message bundle the pre-port flow
posted at every gate: the abyss / power charger strips that changed, the phase
header, the field embed, and one board image per perspective. Nothing here can
fire between the two players' set-card answers, because committing a set card
never changes the phase.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from engine_alpha.battle import CHRONOS_SIZE
from engine_alpha.events import EVENT_GAME_OVER, EVENT_PHASE_CHANGED
from engine_alpha.state import (
    PH_ADVANCE_CHRONOS, PH_AREA_SWAP, PH_BATTLE, PH_CHARACTER_SWAP, PH_END_TURN,
    PH_INITIAL_REVEAL, PH_PROCESS_EFFECTS, PH_REVEAL, PH_SET_CARDS,
    PH_TURN_END_EFFECTS,
)
from zutomayo.match.state_view import BoardView

log = logging.getLogger(__name__)

GATE_DELAY_SECONDS = 2.0

# Index order must match generate_zone_messages: P0 abyss, P0 charger, P1 abyss, P1 charger.
ZONE_KEYS = ('p0_abyss', 'p0_power_charger', 'p1_abyss', 'p1_power_charger')

BATTLE_GATE = '— Battle Damage Calculation [バトル：ダメージの計算]'


class SnapshottingEventSink(list):
    """The engine's ``state.event_sink``, with a BoardView captured at every
    phase boundary. Snapshots are keyed by the index of the event that
    triggered them, so a batch drained after ``apply`` stays aligned."""

    def __init__(self, snapshot_provider: Callable[[], BoardView]) -> None:
        super().__init__()
        self._snapshot_provider = snapshot_provider
        self.snapshots: dict[int, BoardView] = {}

    def append(self, event: tuple) -> None:
        if event[0] in (EVENT_PHASE_CHANGED, EVENT_GAME_OVER):
            self.snapshots[len(self)] = self._snapshot_provider()
        super().append(event)

    def clear(self) -> None:
        super().clear()
        self.snapshots.clear()


@dataclass(frozen=True)
class _Gate:
    header: str
    force_zones: bool = False
    delay: bool = True
    carries_battle_result: bool = False


def zone_instance_ids(board_view: BoardView) -> dict[str, frozenset]:
    ids: dict[str, frozenset] = {}
    for index, player in enumerate(board_view.players):
        ids[f'p{index}_abyss'] = frozenset(view.instance_id for view in player.abyss)
        ids[f'p{index}_power_charger'] = frozenset(
            view.instance_id for view in player.power_charger)
    return ids


class GatePresenter:
    """Posts one phase-gate bundle per engine phase boundary.

    ``board_image_provider`` and ``zone_messages_provider`` are injected by the
    Discord flow; left None (headless tests, replay) the gate still posts its
    header and embeds so message sequencing stays under test, it just renders
    no images."""

    def __init__(
        self,
        session: Any,
        transport: Any,
        *,
        board_image_provider: Optional[Callable[..., Awaitable[Any]]] = None,
        zone_messages_provider: Optional[Callable[..., Awaitable[list]]] = None,
        gate_delay_seconds: float = GATE_DELAY_SECONDS,
    ) -> None:
        self.session = session
        self.transport = transport
        self.board_image_provider = board_image_provider
        self.zone_messages_provider = zone_messages_provider
        self.gate_delay_seconds = gate_delay_seconds
        self.previous_phase: Optional[int] = None
        self.previous_zone_ids: dict[str, frozenset] = {}
        self.pending_battle_result: Optional[dict] = None

    # -- engine callbacks ----------------------------------------------------

    def note_battle_result(self, battle_result: dict) -> None:
        """Held until the gate that follows the battle, which is where the
        result embed belongs (its HP numbers are the post-damage ones)."""
        self.pending_battle_result = battle_result

    async def on_phase_entered(self, new_phase: int, turn: int, snapshot: BoardView) -> None:
        previous_phase, self.previous_phase = self.previous_phase, new_phase
        gate = self._gate_for(new_phase, previous_phase, turn, snapshot)
        if gate is None:
            return
        extra_embeds = None
        if gate.carries_battle_result:
            extra_embeds = self._battle_embeds(snapshot)
        await self._emit_gate(
            gate.header, snapshot,
            extra_embeds=extra_embeds,
            force_zones=gate.force_zones,
            delay=gate.delay,
        )
        if new_phase == PH_SET_CARDS:
            await self._warn_players_without_cards(snapshot)

    async def on_game_over(self, snapshot: BoardView) -> None:
        """A game that ends on battle damage never reaches another phase, so
        the battle gate it would have been attached to has to fire here."""
        if self.pending_battle_result is None:
            return
        await self._emit_gate(
            f'{snapshot.turn} {BATTLE_GATE}', snapshot,
            extra_embeds=self._battle_embeds(snapshot),
            delay=False,
        )

    # -- gate table ----------------------------------------------------------

    def _gate_for(
        self, new_phase: int, previous_phase: Optional[int], turn: int, snapshot: BoardView,
    ) -> Optional[_Gate]:
        if new_phase == PH_REVEAL:
            # Snapshot taken before _ph_reveal runs: both set slots still face-down.
            return _Gate(f'{turn} — Set cards [カードのセット]')

        if new_phase == PH_ADVANCE_CHRONOS:
            if previous_phase == PH_INITIAL_REVEAL:
                return _Gate(
                    f'ゲームスタート: {self._player_name(0)} vs. {self._player_name(1)}',
                    force_zones=True, delay=False,
                )
            return _Gate(f'{turn} — Reveal set cards [セットしたカードを公開する]')

        if new_phase == PH_CHARACTER_SWAP:
            return _Gate(self._advance_chronos_header(turn, snapshot))

        if new_phase == PH_AREA_SWAP:
            return _Gate(f'{turn} — Character Swap [キャラクターの入れ替え]')

        if new_phase == PH_PROCESS_EFFECTS:
            # Turn 1 has no swap phases, so chronos hands straight over.
            if turn == 1:
                return _Gate(self._advance_chronos_header(turn, snapshot))
            return _Gate(f'{turn} — Area Enchant Swap [エリアエンチャントの入れ替え]')

        if new_phase == PH_BATTLE:
            return _Gate(
                f'{turn} — Character/Enchant/Area Enchant Effects '
                '[キャラクター/エンチャント/エリアエンチャントの効果の処理]')

        if new_phase == PH_TURN_END_EFFECTS:
            return _Gate(f'{turn} {BATTLE_GATE}', carries_battle_result=True)

        if new_phase == PH_END_TURN:
            # Turn 1 skips the turn-end effect phase, so the battle gate lands here.
            if previous_phase == PH_BATTLE:
                return _Gate(f'{turn} {BATTLE_GATE}', carries_battle_result=True)
            return None

        if new_phase == PH_SET_CARDS:
            # The engine increments the turn counter before entering set cards.
            return _Gate(
                f'Turn {turn - 1} complete. Preparing next turn...', force_zones=True)

        return None

    def _advance_chronos_header(self, turn: int, snapshot: BoardView) -> str:
        delta = (snapshot.chronos - snapshot.chronos_at_turn_start) % CHRONOS_SIZE
        return f'{turn} — Advance Chronos [時間を進める] ({delta})'

    def _battle_embeds(self, snapshot: BoardView) -> Optional[list]:
        battle_result, self.pending_battle_result = self.pending_battle_result, None
        if battle_result is None:
            return None
        from zutomayo.ui.embeds import build_battle_result_embed_from_board_view

        return [build_battle_result_embed_from_board_view(battle_result, snapshot)]

    # -- sending -------------------------------------------------------------

    async def _emit_gate(
        self,
        header: str,
        snapshot: BoardView,
        *,
        extra_embeds: Optional[list] = None,
        force_zones: bool = False,
        delay: bool = True,
    ) -> None:
        new_zone_ids = zone_instance_ids(snapshot)
        if force_zones:
            changed_indices: Optional[set[int]] = None  # None = send every zone
        else:
            changed_indices = {
                index for index, key in enumerate(ZONE_KEYS)
                if self.previous_zone_ids.get(key) != new_zone_ids[key]
            }
        # Bookkeeping happens even while muted so a resumed game diffs against
        # the same baseline the original run did.
        self.previous_zone_ids = new_zone_ids
        if getattr(self.transport, 'muted', False):
            return

        from zutomayo.enums.chronos import Chronos
        from zutomayo.ui.embeds import build_field_embed_from_board_view

        embeds = list(extra_embeds) if extra_embeds else []
        embeds.append(build_field_embed_from_board_view(snapshot))
        content = f'**{header}**'

        if changed_indices is None or changed_indices:
            await self._send_zone_messages(snapshot, changed_indices, player_index=None)
        await self.transport.send_to_channel(
            self.session, content=content, embeds=embeds,
            **await self._board_attachment(snapshot, Chronos.DAY),
        )

        for index in range(2):
            if not self.transport.delivers_to_player(self.session, index):
                continue
            if changed_indices is None or changed_indices:
                await self._send_zone_messages(snapshot, changed_indices, player_index=index)
            await self.transport.send_to_player(
                self.session, index, content=content, embeds=embeds,
                **await self._board_attachment(snapshot, snapshot.players[index].side),
            )

        if delay and self.gate_delay_seconds > 0 and not getattr(
                self.transport, 'suppress_phase_delays', False):
            await asyncio.sleep(self.gate_delay_seconds)

    async def _board_attachment(self, snapshot: BoardView, perspective: Any) -> dict:
        if self.board_image_provider is None:
            return {}
        board_file = await self.board_image_provider(snapshot, perspective)
        return {'files': [board_file]} if board_file is not None else {}

    async def _send_zone_messages(
        self, snapshot: BoardView, changed_indices: Optional[set[int]], player_index: Optional[int],
    ) -> None:
        """One message per zone that changed. Rendered once per destination
        because a discord.File is consumed when it is sent."""
        if self.zone_messages_provider is None:
            return
        names = {0: self._player_name(0), 1: self._player_name(1)}
        messages = await self.zone_messages_provider(snapshot, names, changed_indices)
        for label, strip_file in messages:
            if strip_file is not None:
                kwargs = {'content': label, 'files': [strip_file]}
            else:
                kwargs = {'content': f'{label} Empty'}
            if player_index is None:
                await self.transport.send_to_channel(self.session, **kwargs)
            else:
                await self.transport.send_to_player(self.session, player_index, **kwargs)

    async def _warn_players_without_cards(self, snapshot: BoardView) -> None:
        for index in range(2):
            if snapshot.players[index].hand:
                continue
            if not self.transport.delivers_to_player(self.session, index):
                continue
            await self.transport.send_to_player(
                self.session, index, content='You have no cards to set this turn.')

    def _player_name(self, player_index: int) -> str:
        name = self.transport.display_name(self.session, player_index)
        return name or f'Player {player_index + 1}'
