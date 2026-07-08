"""
Fake decision adapter and transport for the flow-level (Tier B) harness.

ScriptedDecisionAdapter reuses the real BotAgentDecisionAdapter routing (so the
production kind-to-agent mapping and effect-order permutation cache are
exercised) while recording every prompt and answer into the transcript.

RecordingTransport records every send instead of talking to Discord and
suppresses the cosmetic phase-announce delays so headless games run fast.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from zutomayo.engine.adapters.bot_agent_adapter import BotAgentDecisionAdapter

from tests.transcript import TranscriptRecorder

if TYPE_CHECKING:
    from zutomayo.engine.decisions import DecisionRequest


class ScriptedDecisionAdapter(BotAgentDecisionAdapter):
    """Answers every DecisionRequest from a deterministic scripted agent."""

    def __init__(self, scripted_agent: Any, recorder: TranscriptRecorder) -> None:
        super().__init__(scripted_agent)
        self.recorder = recorder

    async def _run_agent(self, function: Any, *arguments: Any) -> Any:
        # Synchronous instead of asyncio.to_thread: concurrent flow prompts
        # (both players prompted via gather) must record in deterministic
        # order, and worker-thread completion order is not deterministic.
        return function(*arguments)

    async def _decide(self, request: 'DecisionRequest') -> tuple[str, Any]:
        payload_type, payload = await super()._decide(request)
        self.recorder.record_prompt(
            request.kind,
            request.player_index,
            [option.label for option in request.options],
            payload_type,
            payload,
        )
        return payload_type, payload


class RecordingTransport:
    """MatchTransport that records sends into the transcript."""

    def __init__(self, recorder: TranscriptRecorder, player_names: tuple[str, str] = ('TestPlayerZero', 'TestPlayerOne')) -> None:
        self.recorder = recorder
        self.player_names = player_names
        self.muted = False
        self.suppress_phase_delays = True

    async def send_to_player(self, session: Any, player_index: int, **kwargs: Any) -> None:
        if self.muted:
            return None
        self._record(f'dm_{player_index}', kwargs)
        return None

    async def send_to_channel(self, session: Any, **kwargs: Any) -> None:
        if self.muted:
            return None
        self._record('channel', kwargs)
        return None

    def display_name(self, session: Any, player_index: int) -> Optional[str]:
        return self.player_names[player_index]

    def _record(self, target: str, kwargs: dict[str, Any]) -> None:
        embeds = kwargs.get('embeds')
        if embeds is None:
            embed = kwargs.get('embed')
            embeds = [embed] if embed is not None else []
        view = kwargs.get('view')
        self.recorder.record({
            'event': 'send',
            'target': target,
            'content': kwargs.get('content'),
            'embed_titles': [embed.title for embed in embeds],
            'has_files': bool(kwargs.get('files') or kwargs.get('file')),
            'view': type(view).__name__ if view is not None else None,
        })
