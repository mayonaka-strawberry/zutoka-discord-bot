"""Behaviour tests for the choice views and their adapter wiring, with stub interactions."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

import discord  # noqa: E402
import pytest  # noqa: E402
from discord.ui.view import ViewStore  # noqa: E402

from engine_alpha.actions import (  # noqa: E402
    P_EFFECT_NUMBER,
    P_NAME_GUESS,
    select_identity,
    select_number,
)
from engine_alpha.cards import KEY_TO_INDEX, NUM_CARDS  # noqa: E402
from zutomayo.match import discord_adapter  # noqa: E402
from zutomayo.match.card_names import (  # noqa: E402
    DEFAULT_SEARCH_LIMIT,
    card_name_description,
    card_name_label,
    entry_for_definition,
    search_card_names,
)
from zutomayo.match.decisions import (  # noqa: E402
    KIND_IDENTITY_INPUT,
    KIND_NUMBER_CHOICE,
    SIDE_ACTION_DAY,
    SIDE_ACTION_NIGHT,
    SIDE_LABEL_DAY,
    SIDE_LABEL_NIGHT,
    MatchDecisionOption,
    MatchDecisionRequest,
)
from zutomayo.ui.views import (  # noqa: E402
    PROMPT_EXPIRED_TEXT,
    CardNameDeclarationView,
    CardNameSearchModal,
    ConfirmableChoiceView,
    EffectNumberSelectView,
)
from tests.match.support import FakeSession  # noqa: E402

PROMPT_TEXT = 'Choose your side.'

SIDE_OPTIONS = [
    MatchDecisionOption(SIDE_LABEL_DAY, 'opponent sets first', SIDE_ACTION_DAY),
    MatchDecisionOption(SIDE_LABEL_NIGHT, 'you set first', SIDE_ACTION_NIGHT),
]

NAME_PROMPT = '**Shoot** [撃つ] effect: name a card [カード名を1つ指定].'
NIRA_HUMANOID = KEY_TO_INDEX['01-022']    # representative of 01-022, 02-079 and 03-047
NIRA_HUMANOID_LABEL = 'NIRA-CHAN (Humanoid) [にらちゃん（ヒューマノイド）]'
HUMANOID = KEY_TO_INDEX['02-076']          # HUMANOID (Humanoid)


class StubResponse:
    def __init__(self) -> None:
        self.edits: list[dict] = []
        self.modals: list = []
        self.deferred = False

    async def edit_message(self, **kwargs) -> None:
        self.edits.append(kwargs)

    async def send_modal(self, modal) -> None:
        self.modals.append(modal)

    async def defer(self) -> None:
        self.deferred = True


class StubInteraction:
    def __init__(self, values: list[str] | None = None) -> None:
        self.response = StubResponse()
        self.data = {'values': values or []}


def _buttons(view) -> list:
    return [child for child in view.children if isinstance(child, discord.ui.Button)]


def _labels(view) -> list[str]:
    return [button.label for button in _buttons(view)]


def _button(view, label: str) -> discord.ui.Button:
    for button in _buttons(view):
        if button.label == label:
            return button
    raise AssertionError(f'no button labelled {label!r}; have {_labels(view)}')


def _select(view) -> discord.ui.Select:
    return next(child for child in view.children if isinstance(child, discord.ui.Select))


def _has_select(view) -> bool:
    return any(isinstance(child, discord.ui.Select) for child in view.children)


async def _press(view, label: str) -> StubInteraction:
    interaction = StubInteraction()
    await _button(view, label).callback(interaction)
    return interaction


async def _choose(view, value: str) -> StubInteraction:
    interaction = StubInteraction(values=[value])
    await _select(view).callback(interaction)
    return interaction


def _build_view(submitted: list) -> ConfirmableChoiceView:
    return ConfirmableChoiceView(
        session=None,
        player_index=0,
        options=SIDE_OPTIONS,
        prompt_text=PROMPT_TEXT,
        opponent_name='Beta',
        submit_callback=lambda payload_type, payload: submitted.append((payload_type, payload)),
    )


def _recording_callback(submitted: list, accepted: bool = True):
    def submit(payload_type: str, payload) -> bool:
        submitted.append((payload_type, payload))
        return accepted

    return submit


# Clicks through discord.py's real ViewStore. A stopped view's clicks are never
# dispatched, and Discord then shows "This interaction failed".
MESSAGE_ID = 1
ROUND_TRIP_SECONDS = 0.02


class RoundTripResponse(StubResponse):
    """Each edit takes an HTTP round trip, during which more clicks can land."""

    async def edit_message(self, **kwargs) -> None:
        await super().edit_message(**kwargs)
        await asyncio.sleep(ROUND_TRIP_SECONDS)


class DispatchedInteraction:
    """A component interaction as ViewStore.dispatch_view reads it."""

    def __init__(self, item, values: list[str] | None = None) -> None:
        self.response = RoundTripResponse()
        self.data = {'custom_id': item.custom_id, 'component_type': item.type.value,
                     'values': values or []}
        self.message = SimpleNamespace(id=MESSAGE_ID, interaction_metadata=None)


def _listening_store(view) -> ViewStore:
    """A store holding `view` for its sent message."""
    store = ViewStore(state=None)
    store.add_view(view, MESSAGE_ID)
    return store


def _dispatch(store: ViewStore, item, values: list[str] | None = None) -> DispatchedInteraction:
    """Deliver a click as the gateway does; a dispatched callback runs as a task."""
    interaction = DispatchedInteraction(item, values)
    store.dispatch_view(item.type.value, item.custom_id, interaction)
    return interaction


async def _until_edit_in_flight(interaction: DispatchedInteraction) -> None:
    for _ in range(100):
        if interaction.response.edits:
            return
        await asyncio.sleep(0)
    raise AssertionError('the click never edited its message')


async def _settle(view) -> None:
    """Wait for `view` to stop, then for the other dispatched callbacks."""
    await asyncio.wait_for(view.wait(), timeout=5)
    await asyncio.sleep(ROUND_TRIP_SECONDS * 2)


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


# -- CardNameDeclarationView ---------------------------------------------------
# Views are built inside the running loop, so is_finished() works.


def _declaration_view(submitted: list, accepted: bool = True,
                      is_pending=lambda: True) -> CardNameDeclarationView:
    return CardNameDeclarationView(
        session=None,
        player_index=0,
        prompt_text=NAME_PROMPT,
        submit_callback=_recording_callback(submitted, accepted),
        timeout=300.0,
        is_pending=is_pending,
    )


async def _open_search(view) -> CardNameSearchModal:
    label = 'Search again' if 'Search again' in _labels(view) else 'Search card names'
    interaction = await _press(view, label)
    return interaction.response.modals[-1]


async def _submit_search(modal: CardNameSearchModal, query: str) -> dict:
    """Submit the modal as Discord would; returns the message edit."""
    modal.query_input._refresh_state(None, {'value': query})
    interaction = StubInteraction()
    await modal.on_submit(interaction)
    assert len(interaction.response.edits) == 1
    return interaction.response.edits[0]


async def _search(view, query: str) -> dict:
    return await _submit_search(await _open_search(view), query)


def _default_values(view) -> list[str]:
    return [option.value for option in _select(view).options if option.default]


def test_opening_the_search_leaves_the_view_running():
    async def run():
        view = _declaration_view([])
        assert _labels(view) == ['Search card names']
        assert not _has_select(view)

        interaction = await _press(view, 'Search card names')
        modal = interaction.response.modals[0]
        assert isinstance(modal, CardNameSearchModal)
        assert modal.title == 'Name a card'
        assert modal.query_input.to_component_dict()['label'] == 'Card name or number'
        assert modal.query_input.placeholder == 'nira humanoid / にらちゃん / 03-047'
        assert modal.query_input.max_length == 100
        assert modal.query_input.default is None
        assert modal.timeout == view.timeout == 300.0
        assert interaction.response.edits == []
        assert not view.is_finished()

    asyncio.run(run())


def test_one_result_needs_a_pick_before_declare():
    async def run():
        view = _declaration_view([])
        edit = await _search(view, 'nira humanoid')

        assert edit['view'] is view
        assert edit['content'] == f'{NAME_PROMPT}\n\n1 name matches "nira humanoid".'
        assert _labels(view) == ['Declare', 'Search again']
        assert _button(view, 'Declare').disabled is True
        [option] = _select(view).options
        assert option.value == str(NIRA_HUMANOID)
        assert option.label == NIRA_HUMANOID_LABEL
        assert option.description == card_name_description(entry_for_definition(NIRA_HUMANOID))
        assert option.description == 'Character | 01-022, 02-079, 03-047'
        assert option.default is False

    asyncio.run(run())


def test_selecting_never_submits_and_declare_submits_the_representative():
    submitted: list = []

    async def run():
        view = _declaration_view(submitted)
        edit = await _search(view, 'hum')
        assert '3 names match "hum".' in edit['content']

        interaction = await _choose(view, str(NIRA_HUMANOID))
        assert submitted == [], 'picking must not submit on its own'
        assert _default_values(view) == [str(NIRA_HUMANOID)]
        assert _button(view, 'Declare').disabled is False
        content = interaction.response.edits[0]['content']
        assert content.endswith(f'\nDeclare **{NIRA_HUMANOID_LABEL}**?')
        assert interaction.response.edits[0]['view'] is view

        # Re-picking is free.
        interaction = await _choose(view, str(HUMANOID))
        assert _default_values(view) == [str(HUMANOID)]
        assert f'Declare **{card_name_label(HUMANOID)}**?' in interaction.response.edits[0]['content']
        assert submitted == []

        interaction = await _press(view, 'Declare')
        assert submitted == [('action', HUMANOID)]
        assert interaction.response.edits == [
            {'content': f'You named **{card_name_label(HUMANOID)}**.', 'view': None}]
        assert view.is_finished()

    asyncio.run(run())


def test_a_card_number_or_a_verbatim_name_is_preselected():
    submitted: list = []

    async def run():
        view = _declaration_view(submitted)
        edit = await _search(view, '02-079')
        assert _default_values(view) == [str(NIRA_HUMANOID)]
        assert _button(view, 'Declare').disabled is False
        assert edit['content'].endswith(
            f'1 name matches "02-079".\nDeclare **{NIRA_HUMANOID_LABEL}**?')

        await _search(view, 'shoot')
        shoot = KEY_TO_INDEX['03-094']
        assert _select(view).options[0].value == str(shoot)
        assert _default_values(view) == [str(shoot)]
        assert _button(view, 'Declare').disabled is False

        await _press(view, 'Declare')
        assert submitted == [('action', shoot)]

    asyncio.run(run())


def test_clicks_during_the_declare_edit_are_deferred_and_change_nothing():
    """A second Declare, Search again and a pick land while the first Declare's edit is
    in flight. Each must still be dispatched, and only deferred."""
    submitted: list = []

    async def run():
        view = _declaration_view(submitted)
        await _search(view, '03-047')
        store = _listening_store(view)
        first = _dispatch(store, _button(view, 'Declare'))
        await _until_edit_in_flight(first)

        late_clicks = {
            'Declare': _dispatch(store, _button(view, 'Declare')),
            'Search again': _dispatch(store, _button(view, 'Search again')),
            'pick': _dispatch(store, _select(view), [str(NIRA_HUMANOID)]),
        }
        await _settle(view)

        assert submitted == [('action', NIRA_HUMANOID)]
        assert first.response.edits == [
            {'content': f'You named **{NIRA_HUMANOID_LABEL}**.', 'view': None}]
        for name, late in late_clicks.items():
            assert late.response.deferred is True, f'{name} was never answered'
            assert late.response.edits == [] and late.response.modals == [], name

    asyncio.run(run())


def test_declare_shows_the_expired_text_when_the_answer_is_refused():
    submitted: list = []

    async def run():
        view = _declaration_view(submitted, accepted=False)
        await _search(view, '03-047')
        interaction = await _press(view, 'Declare')
        assert submitted == [('action', NIRA_HUMANOID)]
        assert interaction.response.edits == [{'content': PROMPT_EXPIRED_TEXT, 'view': None}]

    asyncio.run(run())


def test_search_again_is_prefilled_with_the_last_query():
    async def run():
        view = _declaration_view([])
        await _search(view, '  nira humanoid  ')
        modal = await _open_search(view)
        assert modal.query_input.default == 'nira humanoid'
        assert not view.is_finished()

        # A new search replaces the results and clears the pick.
        await _choose(view, str(NIRA_HUMANOID))
        edit = await _submit_search(modal, 'hum')
        assert '3 names match "hum".' in edit['content']
        assert 'Declare **' not in edit['content']
        assert _default_values(view) == []
        assert _button(view, 'Declare').disabled is True

    asyncio.run(run())


def test_zero_results_offer_only_a_new_search():
    async def run():
        view = _declaration_view([])
        edit = await _search(view, 'zzzzqqq')
        assert edit['content'] == (
            f'{NAME_PROMPT}\n\nNo card name matches "zzzzqqq". Try part of the English or '
            'Japanese name, the song, or a number like 03-047.')
        assert _labels(view) == ['Search again']
        assert not _has_select(view)

    asyncio.run(run())


def test_over_twenty_five_results_show_the_first_page_and_the_total():
    total = search_card_names('nira').total
    assert total > DEFAULT_SEARCH_LIMIT

    async def run():
        view = _declaration_view([])
        edit = await _search(view, 'nira')
        assert edit['content'].endswith(
            f'Showing 25 of {total} names for "nira". Add more of the name, such as the song.')
        assert len(_select(view).options) == DEFAULT_SEARCH_LIMIT

    asyncio.run(run())


def test_fuzzy_results_are_headed_closest_names():
    total = search_card_names('nirq').total
    assert search_card_names('nirq').is_fuzzy and total > DEFAULT_SEARCH_LIMIT

    async def run():
        view = _declaration_view([])
        edit = await _search(view, 'humaniod')
        assert edit['content'].endswith('\n\nClosest names to "humaniod":')
        values = [option.value for option in _select(view).options]
        assert str(NIRA_HUMANOID) in values and str(HUMANOID) in values

        edit = await _search(view, 'nirq')
        assert edit['content'].endswith(
            f'Closest names to "nirq": showing 25 of {total}. '
            'Add more of the name, such as the song.')

    asyncio.run(run())


def test_the_echoed_query_is_escaped_and_capped():
    async def run():
        view = _declaration_view([])
        edit = await _search(view, '*nira*')
        assert '"\\*nira\\*"' in edit['content']

        edit = await _search(view, 'q' * 150)
        assert f'"{"q" * 100}"' in edit['content']
        assert 'q' * 101 not in edit['content']

    asyncio.run(run())


def test_a_search_after_the_view_finished_shows_the_expired_text():
    async def run():
        view = _declaration_view([])
        modal = await _open_search(view)
        view.stop()  # as a timeout would
        edit = await _submit_search(modal, 'nira humanoid')
        assert edit == {'content': PROMPT_EXPIRED_TEXT, 'view': None}

    asyncio.run(run())


def test_a_stale_search_after_declaring_keeps_the_declaration():
    """A modal left open (another device) and submitted after Declare."""
    submitted: list = []

    async def run():
        view = _declaration_view(submitted)
        stale_modal = await _open_search(view)
        await _search(view, '03-047')
        await _press(view, 'Declare')

        edit = await _submit_search(stale_modal, 'hum')
        assert edit == {'content': f'You named **{NIRA_HUMANOID_LABEL}**.', 'view': None}
        assert submitted == [('action', NIRA_HUMANOID)]

    asyncio.run(run())


@pytest.mark.parametrize('interaction_kind', ['search button', 'modal submit', 'name pick'])
def test_once_the_broker_stops_waiting_the_next_interaction_closes_the_view(interaction_kind):
    """After the timeout fallback answered, no search or pick goes through: narration has
    already announced the fallback name."""
    broker_waiting = [True]

    async def run():
        view = _declaration_view([], is_pending=lambda: broker_waiting[0])
        modal = await _open_search(view)
        await _submit_search(modal, 'hum')
        broker_waiting[0] = False

        if interaction_kind == 'search button':
            interaction = await _press(view, 'Search again')
            assert interaction.response.modals == []
            edits = interaction.response.edits
        elif interaction_kind == 'modal submit':
            edits = [await _submit_search(modal, 'nira humanoid')]
        else:
            edits = (await _choose(view, str(NIRA_HUMANOID))).response.edits
        assert edits == [{'content': PROMPT_EXPIRED_TEXT, 'view': None}]
        assert view.is_finished()

    asyncio.run(run())


# -- EffectNumberSelectView ------------------------------------------------------


def test_hidden_hand_pick_offers_slots_and_submits_the_mapped_position():
    submitted: list = []

    async def run():
        view = EffectNumberSelectView(
            None, 0, 1, 4,
            submit_callback=_recording_callback(submitted),
            slot_positions=[3, 1, 4, 2],
            timeout=300.0,
        )
        options = _select(view).options
        assert [option.value for option in options] == ['1', '2', '3', '4']
        assert [option.label for option in options] == ['Card 1', 'Card 2', 'Card 3', 'Card 4']
        assert view.timeout == 300.0

        interaction = await _choose(view, '2')
        assert submitted == [('number', 1)]
        assert interaction.response.edits == [{'content': 'You chose Card 2.', 'view': None}]
        assert view.is_finished()

    asyncio.run(run())


def test_hidden_hand_pick_shows_the_expired_text_when_refused():
    async def run():
        view = EffectNumberSelectView(
            None, 0, 1, 2,
            submit_callback=_recording_callback([], accepted=False),
            slot_positions=[2, 1],
        )
        interaction = await _choose(view, '1')
        assert interaction.response.edits == [{'content': PROMPT_EXPIRED_TEXT, 'view': None}]

    asyncio.run(run())


def test_a_second_hidden_pick_during_the_edit_keeps_the_first_answer():
    """Dispatched while "You chose Card 1." is being shown: deferred, not refused and
    shown as expired."""
    submitted: list = []

    async def run():
        view = EffectNumberSelectView(
            None, 0, 1, 3,
            submit_callback=_recording_callback(submitted),
            slot_positions=[2, 3, 1],
            timeout=300.0,
        )
        store = _listening_store(view)
        first = _dispatch(store, _select(view), ['1'])
        await _until_edit_in_flight(first)
        second = _dispatch(store, _select(view), ['3'])
        await _settle(view)

        assert submitted == [('number', 2)]
        assert first.response.edits == [{'content': 'You chose Card 1.', 'view': None}]
        assert second.response.deferred is True
        assert second.response.edits == []

    asyncio.run(run())


def test_other_number_prompts_are_unchanged():
    submitted: list = []

    async def run():
        view = EffectNumberSelectView(None, 0, 0, 5, submit_callback=_recording_callback(submitted))
        select = _select(view)
        assert [option.value for option in select.options] == [str(n) for n in range(6)]
        assert [option.label for option in select.options] == [str(n) for n in range(6)]
        assert select.placeholder == 'Select a number...'

        interaction = await _choose(view, '3')
        assert submitted == [('number', 3)]
        assert interaction.response.edits == [{'content': 'You selected: **3**', 'view': None}]

    asyncio.run(run())


# -- Adapter wiring ----------------------------------------------------------------


class AnsweringBroker:
    """Records submissions and reports each as accepted or not; `pending` is what
    is_pending answers."""

    def __init__(self, accepted: bool = True) -> None:
        self.accepted = accepted
        self.pending = True
        self.submissions: list[tuple] = []
        self.pending_checks: list[int] = []

    def submit(self, sequence_number: int, payload_type: str, payload) -> bool:
        self.submissions.append((sequence_number, payload_type, payload))
        return self.accepted

    def is_pending(self, sequence_number: int) -> bool:
        self.pending_checks.append(sequence_number)
        return self.pending


def _adapter_view(request: MatchDecisionRequest, broker: AnsweringBroker):
    session = FakeSession()
    session.broker = broker
    return discord_adapter.DiscordMatchDecisionAdapter(transport=None)._build_simple_view(
        session, request)


def _hand_pick_request(hand_size: int) -> MatchDecisionRequest:
    request = MatchDecisionRequest(
        kind=KIND_NUMBER_CHOICE, player_index=0, prompt_text='pick',
        engine_request=select_number(P_EFFECT_NUMBER, 1, hand_size),
        purpose=P_EFFECT_NUMBER, minimum_value=1, maximum_value=hand_size,
        hidden_hand_pick=True, timeout_seconds=300.0,
    )
    request.sequence_number = 9
    return request


def test_adapter_shuffles_a_hidden_hand_pick_and_submits_the_real_position(monkeypatch):
    sampled: list = []

    class StubSystemRandom:
        def sample(self, population, count):
            sampled.append((list(population), count))
            return [3, 1, 2]

    monkeypatch.setattr(discord_adapter.random, 'SystemRandom', StubSystemRandom)
    broker = AnsweringBroker()

    async def run():
        view = _adapter_view(_hand_pick_request(3), broker)
        assert sampled == [([1, 2, 3], 3)]
        assert view.timeout == 300.0
        select = _select(view)
        assert select.placeholder == 'Select a card...'
        assert [option.value for option in select.options] == ['1', '2', '3']

        interaction = await _choose(view, '1')
        assert broker.submissions == [(9, 'action', 3)]
        assert interaction.response.edits[0]['content'] == 'You chose Card 1.'

    asyncio.run(run())


def test_adapter_hand_pick_order_is_a_permutation_of_the_positions():
    async def run():
        view = _adapter_view(_hand_pick_request(5), AnsweringBroker())
        assert sorted(view.slot_positions) == [1, 2, 3, 4, 5]

    asyncio.run(run())


def test_adapter_presents_a_plain_number_choice_unshuffled(monkeypatch):
    class RefusingSystemRandom:
        def __init__(self) -> None:
            raise AssertionError('only a hidden hand pick is shuffled')

    monkeypatch.setattr(discord_adapter.random, 'SystemRandom', RefusingSystemRandom)
    broker = AnsweringBroker()
    request = MatchDecisionRequest(
        kind=KIND_NUMBER_CHOICE, player_index=0, prompt_text='advance the clock',
        engine_request=select_number(P_EFFECT_NUMBER, 0, 5),
        purpose=P_EFFECT_NUMBER, minimum_value=0, maximum_value=5, timeout_seconds=240.0,
    )
    request.sequence_number = 6

    async def run():
        view = _adapter_view(request, broker)
        assert view.slot_positions is None
        assert view.timeout == 240.0
        select = _select(view)
        assert select.placeholder == 'Select a number...'
        assert [option.value for option in select.options] == [str(n) for n in range(6)]

        interaction = await _choose(view, '4')
        assert broker.submissions == [(6, 'action', 4)]
        assert interaction.response.edits == [{'content': 'You selected: **4**', 'view': None}]

    asyncio.run(run())


def _name_request() -> MatchDecisionRequest:
    request = MatchDecisionRequest(
        kind=KIND_IDENTITY_INPUT, player_index=1, prompt_text=NAME_PROMPT,
        engine_request=select_identity(P_NAME_GUESS, list(range(NUM_CARDS))),
        purpose=P_NAME_GUESS, timeout_seconds=300.0,
    )
    request.sequence_number = 4
    return request


def test_adapter_name_view_closes_once_the_broker_stops_waiting():
    broker = AnsweringBroker()

    async def run():
        view = _adapter_view(_name_request(), broker)
        await _search(view, '02-079')
        assert set(broker.pending_checks) == {4}, 'it asks about its own request'

        broker.pending = False
        interaction = await _press(view, 'Search again')
        assert interaction.response.edits == [{'content': PROMPT_EXPIRED_TEXT, 'view': None}]
        assert view.is_finished()
        assert broker.submissions == []

    asyncio.run(run())


def test_adapter_declares_a_card_name_through_the_broker():
    request = _name_request()

    async def run():
        for accepted, closing_text in ((True, f'You named **{NIRA_HUMANOID_LABEL}**.'),
                                       (False, PROMPT_EXPIRED_TEXT)):
            broker = AnsweringBroker(accepted)
            view = _adapter_view(request, broker)
            assert isinstance(view, CardNameDeclarationView)
            assert view.timeout == 300.0
            await _search(view, '02-079')
            interaction = await _press(view, 'Declare')
            assert broker.submissions == [(4, 'action', NIRA_HUMANOID)]
            assert interaction.response.edits[0]['content'] == closing_text

    asyncio.run(run())
