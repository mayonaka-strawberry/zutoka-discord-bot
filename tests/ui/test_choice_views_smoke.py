"""Behaviour tests for ConfirmableChoiceView, the buttons-plus-confirm view
behind the TCG day/night side choice: nothing is submitted until Confirm, and
Go Back returns to the original options."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from zutomayo.match.decisions import (  # noqa: E402
    SIDE_ACTION_DAY,
    SIDE_ACTION_NIGHT,
    SIDE_LABEL_DAY,
    SIDE_LABEL_NIGHT,
    MatchDecisionOption,
)
from zutomayo.ui.views import ConfirmableChoiceView  # noqa: E402

PROMPT_TEXT = 'Choose your side.'

SIDE_OPTIONS = [
    MatchDecisionOption(SIDE_LABEL_DAY, 'opponent sets first', SIDE_ACTION_DAY),
    MatchDecisionOption(SIDE_LABEL_NIGHT, 'you set first', SIDE_ACTION_NIGHT),
]


class StubResponse:
    def __init__(self) -> None:
        self.edits: list[dict] = []

    async def edit_message(self, **kwargs) -> None:
        self.edits.append(kwargs)


class StubInteraction:
    def __init__(self) -> None:
        self.response = StubResponse()


def _labels(view) -> list[str]:
    return [child.label for child in view.children]


async def _press(view, label: str) -> StubInteraction:
    interaction = StubInteraction()
    for child in view.children:
        if child.label == label:
            await child.callback(interaction)
            return interaction
    raise AssertionError(f'no button labelled {label!r}; have {_labels(view)}')


def _build_view(submitted: list) -> ConfirmableChoiceView:
    return ConfirmableChoiceView(
        session=None,
        player_index=0,
        options=SIDE_OPTIONS,
        prompt_text=PROMPT_TEXT,
        opponent_name='Beta',
        submit_callback=lambda payload_type, payload: submitted.append((payload_type, payload)),
    )


def test_choice_needs_confirmation_before_submitting():
    submitted: list = []
    view = _build_view(submitted)

    async def run():
        assert _labels(view) == [SIDE_LABEL_DAY, SIDE_LABEL_NIGHT]

        interaction = await _press(view, SIDE_LABEL_NIGHT)
        assert submitted == [], 'selecting must not submit on its own'
        assert _labels(view) == ['Confirm', 'Go Back']
        assert SIDE_LABEL_NIGHT in interaction.response.edits[0]['content']

        interaction = await _press(view, 'Confirm')
        assert submitted == [('action', SIDE_ACTION_NIGHT)]
        assert interaction.response.edits[0]['view'] is None
        assert 'Beta' in interaction.response.edits[0]['content']

    asyncio.run(run())


def test_go_back_restores_the_options_without_submitting():
    submitted: list = []
    view = _build_view(submitted)

    async def run():
        await _press(view, SIDE_LABEL_NIGHT)
        interaction = await _press(view, 'Go Back')
        assert submitted == []
        assert view.selected_option is None
        assert _labels(view) == [SIDE_LABEL_DAY, SIDE_LABEL_NIGHT]
        assert interaction.response.edits[0]['content'] == PROMPT_TEXT

        await _press(view, SIDE_LABEL_DAY)
        await _press(view, 'Confirm')
        assert submitted == [('action', SIDE_ACTION_DAY)]

    asyncio.run(run())
