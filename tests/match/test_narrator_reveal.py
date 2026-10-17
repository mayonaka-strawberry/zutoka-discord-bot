"""MatchNarrator reveal broadcasts: the three legs (owner DM, opponent DM, channel) and
their text. Image rendering is left unwired except where it is under test.
"""

from __future__ import annotations

import asyncio

import pytest

from engine_alpha import cards
from engine_alpha.actions import P_EFFECT_NUMBER, P_NAME_GUESS
from engine_alpha.events import EVENT_CARDS_REVEALED, EVENT_NAME_GUESS_RESOLVED
from tests.match.support import FakeSession, RecordingTransport, ScriptedActionAdapter
from zutomayo.match.agents import BOT_NAME
from zutomayo.match.card_names import card_name_label, search_card_names
from zutomayo.match.decisions import PAYLOAD_ACTION
from zutomayo.match.narrator import MatchNarrator
from zutomayo.match.state_view import definition_index_to_card

TAIDADA_CHARACTER_DEFINITIONS = [
    definition.index for definition in cards.CARD_DB
    if definition.song == cards.SONG_NAMES.index('TAIDADA')
    and definition.card_type == cards.TYPE_CHARACTER
]

# Three printings of one card name, NIRA-CHAN (Humanoid) (Q&A No.50).
NIRA_CHAN_HUMANOID_DEFINITIONS = [cards.KEY_TO_INDEX[key] for key in ('01-022', '02-079', '03-047')]


def carrier_definition_index(effect_id: str) -> int:
    """The definition index of the card carrying `effect_id`."""
    return cards.EFFECT_TO_CARD[cards.EFFECT_TO_INDEX[effect_id]]


def build_narrator(
        transport: RecordingTransport | None = None) -> tuple[MatchNarrator, RecordingTransport]:
    session = FakeSession()
    transport = transport or RecordingTransport()
    session.transport = transport
    return MatchNarrator(session, transport), transport


def revealed_names(definition_indices) -> str:
    return ', '.join(definition_index_to_card(i).name for i in definition_indices)


def reveal_event(owner_index, revealed_owner_index, effect_id, revealed) -> tuple:
    return (EVENT_CARDS_REVEALED, owner_index, revealed_owner_index,
            carrier_definition_index(effect_id), *revealed)


def name_guess_event(owner_index, effect_id, named_definition, attack_bonus) -> tuple:
    return (EVENT_NAME_GUESS_RESOLVED, owner_index, carrier_definition_index(effect_id),
            named_definition, attack_bonus)


def publish_events(narrator, events) -> None:
    # board_view is untouched by the reveal branch; only redraw events read it.
    asyncio.run(narrator.publish(list(events), None))


def publish_reveal(narrator, owner_index, revealed_owner_index, effect_id, revealed):
    publish_events(narrator, [reveal_event(owner_index, revealed_owner_index, effect_id, revealed)])


def contents(messages) -> list[str]:
    return [str(message.get('content') or '') for message in messages]


def test_taidada_reveal_reaches_both_players_and_the_channel():
    narrator, transport = build_narrator()
    revealed = TAIDADA_CHARACTER_DEFINITIONS[:2]
    publish_reveal(narrator, 0, 0, '04-001', revealed)

    names = revealed_names(revealed)
    assert len(transport.player_messages[0]) == 1
    assert len(transport.player_messages[1]) == 1
    assert len(transport.channel_messages) == 1

    assert transport.player_messages[0][0]['content'] == (
        f'**Effect (04-001):** Revealed 2 TAIDADA character(s): {names}. Attack +60!')
    assert transport.player_messages[1][0]['content'] == (
        f'**Effect (04-001):** Opponent revealed 2 TAIDADA character(s): {names}.')
    # The channel names the player: "Opponent" is ambiguous with both watching.
    assert transport.channel_messages[0]['content'] == (
        f'**Effect (04-001):** Player 1 revealed 2 TAIDADA character(s): {names}.')
    assert 'files' not in transport.channel_messages[0]


def test_attack_bonus_in_the_reveal_line_comes_from_the_effect_ir():
    """04-035 pays 10 per card where 04-001 pays 30, and the line is read out
    of catalog_data.py rather than hard-coded, so the two must differ."""
    narrator, transport = build_narrator()
    publish_reveal(narrator, 0, 0, '04-035', TAIDADA_CHARACTER_DEFINITIONS[:3])

    assert transport.player_messages[0][0]['content'].endswith('Attack +30!')


def test_empty_reveal_is_announced_to_the_owner_only():
    narrator, transport = build_narrator()
    publish_reveal(narrator, 1, 1, '04-035', [])

    assert transport.player_messages[1] == [
        {'content': '**Effect (04-035):** Nothing revealed. No effect.'}]
    assert transport.player_messages[0] == []
    assert transport.channel_messages == []


def test_hand_reveal_names_the_exposed_player_and_details_the_cards():
    narrator, transport = build_narrator()
    revealed = TAIDADA_CHARACTER_DEFINITIONS[:3]
    publish_reveal(narrator, 0, 1, '03-045', revealed)

    names = revealed_names(revealed)
    owner_message = transport.player_messages[0][0]
    assert owner_message['content'] == "**Effect (03-045):** Opponent's hand revealed!"
    assert owner_message['embed'].title == "Opponent's Hand [相手の手札]"
    for definition_index in revealed:
        assert definition_index_to_card(definition_index).name in owner_message['embed'].description

    assert transport.player_messages[1][0]['content'] == (
        f'**Effect (03-045):** Your hand has been revealed: {names}.')
    assert transport.channel_messages[0]['content'] == (
        f"**Effect (03-045):** Player 2's hand revealed: {names}.")


def test_each_leg_gets_its_own_rendered_image():
    """A discord.File is consumed on send, so the three legs cannot share one."""
    narrator, transport = build_narrator()
    renders = []

    async def fake_provider(revealed, columns):
        renders.append(columns)
        return object()

    narrator.reveal_image_provider = fake_provider
    publish_reveal(narrator, 0, 0, '04-001', TAIDADA_CHARACTER_DEFINITIONS[:2])

    assert renders == [2, 2, 2], 'owner DM, opponent DM, channel'
    files = [
        transport.player_messages[0][0]['files'][0],
        transport.player_messages[1][0]['files'][0],
        transport.channel_messages[0]['files'][0],
    ]
    assert len({id(handle) for handle in files}) == 3


def test_muted_transport_reveals_nothing():
    """Replay re-runs every apply; a muted transport must not re-post reveals."""
    narrator, transport = build_narrator()
    transport.muted = True
    publish_reveal(narrator, 0, 0, '04-001', TAIDADA_CHARACTER_DEFINITIONS[:2])

    assert transport.player_messages == {0: [], 1: []}
    assert transport.channel_messages == []


# --- Name guess (03-047 family): the declaration joins its pick's reveal ---

def plain_hand_pick_channel_line(effect_id, revealed_owner_name, definition_index) -> str:
    """The channel line for one opponent hand card shown without a declaration."""
    return (f"**Effect ({effect_id}):** a card from {revealed_owner_name}'s hand revealed: "
            f'{definition_index_to_card(definition_index).name}.')


def test_name_guess_match_lines_for_owner_opponent_and_channel():
    """Any printing matches (Q&A No.50): 01-022 named, 02-079 revealed."""
    narrator, transport = build_narrator()
    renders = []

    async def fake_provider(revealed, columns):
        renders.append(columns)
        return object()

    narrator.reveal_image_provider = fake_provider
    named, revealed = NIRA_CHAN_HUMANOID_DEFINITIONS[0], NIRA_CHAN_HUMANOID_DEFINITIONS[1]
    publish_events(narrator, [
        name_guess_event(0, '03-047', named, 50),
        reveal_event(0, 1, '03-047', [revealed]),
    ])

    label = card_name_label(named)
    assert label == card_name_label(revealed)
    assert contents(transport.player_messages[0]) == [
        f"**Effect (03-047):** You named {label}. Revealed from opponent's hand: {label}. "
        'Same name: Attack +50.']
    assert contents(transport.player_messages[1]) == [
        f'**Effect (03-047):** Player 1 named {label}. '
        f'A card was revealed from your hand: {label}. Same name: Attack +50 for Player 1.']
    assert contents(transport.channel_messages) == [
        f"**Effect (03-047):** Player 1 named {label}. Revealed from Player 2's hand: {label}. "
        'Same name: Attack +50.']
    assert renders == [1, 1, 1], 'the revealed card image still goes to every leg'


def test_name_guess_miss_lines_for_owner_opponent_and_channel():
    """A shared prefix is not a match: NIRA-CHAN (STUDY ME) against NIRA-CHAN (TAIDADA)."""
    narrator, transport = build_narrator()
    named, revealed = cards.KEY_TO_INDEX['01-001'], cards.KEY_TO_INDEX['04-001']
    publish_events(narrator, [
        name_guess_event(1, '03-094', named, 0),
        reveal_event(1, 0, '03-094', [revealed]),
    ])

    named_label, revealed_label = card_name_label(named), card_name_label(revealed)
    assert contents(transport.player_messages[1]) == [
        f'**Effect (03-094):** You named {named_label}. '
        f"Revealed from opponent's hand: {revealed_label}. Different name: no bonus."]
    assert contents(transport.player_messages[0]) == [
        f'**Effect (03-094):** Player 2 named {named_label}. '
        f'A card was revealed from your hand: {revealed_label}. Different name: no bonus.']
    assert contents(transport.channel_messages) == [
        f'**Effect (03-094):** Player 2 named {named_label}. '
        f"Revealed from Player 1's hand: {revealed_label}. Different name: no bonus."]


@pytest.mark.parametrize('owner_index, revealed_owner_index, effect_id, revealed', [
    pytest.param(0, 0, '04-035', [], id='empty'),
    pytest.param(0, 0, '04-001', TAIDADA_CHARACTER_DEFINITIONS[:2], id='own_hand'),
    pytest.param(0, 1, '03-097', TAIDADA_CHARACTER_DEFINITIONS[:1], id='deck_top'),
    pytest.param(0, 1, '03-045', TAIDADA_CHARACTER_DEFINITIONS[:3], id='full_hand'),
    pytest.param(0, 1, '03-047', TAIDADA_CHARACTER_DEFINITIONS[:1], id='name_guess_pick'),
])
def test_every_reveal_branch_clears_the_declaration(
        owner_index, revealed_owner_index, effect_id, revealed):
    """A declaration reaches only the next reveal, whichever branch that takes."""
    narrator, transport = build_narrator()
    later = NIRA_CHAN_HUMANOID_DEFINITIONS[2]
    publish_events(narrator, [
        name_guess_event(0, '03-047', NIRA_CHAN_HUMANOID_DEFINITIONS[0], 50),
        reveal_event(owner_index, revealed_owner_index, effect_id, revealed),
        reveal_event(0, 1, '03-047', [later]),
    ])

    assert transport.channel_messages[-1]['content'] == (
        plain_hand_pick_channel_line('03-047', 'Player 2', later))
    merged = [line for line in contents(transport.channel_messages) if ' named ' in line]
    assert len(merged) == (1 if effect_id == '03-047' else 0)


@pytest.mark.parametrize('declaring_owner, declaring_effect', [
    pytest.param(0, '03-059', id='other_source'),
    pytest.param(1, '03-047', id='other_owner'),
])
def test_a_declaration_from_another_effect_is_ignored(declaring_owner, declaring_effect):
    narrator, transport = build_narrator()
    revealed = NIRA_CHAN_HUMANOID_DEFINITIONS[1]
    publish_events(narrator, [
        name_guess_event(declaring_owner, declaring_effect, NIRA_CHAN_HUMANOID_DEFINITIONS[0], 100),
        reveal_event(0, 1, '03-047', [revealed]),
    ])

    name = definition_index_to_card(revealed).name
    assert contents(transport.player_messages[0]) == [
        f"**Effect (03-047):** Revealed from opponent's hand: {name}."]
    assert contents(transport.player_messages[1]) == [
        f'**Effect (03-047):** A card was revealed from your hand: {name}.']
    assert contents(transport.channel_messages) == [
        plain_hand_pick_channel_line('03-047', 'Player 2', revealed)]


def test_muted_transport_sends_no_name_guess_lines():
    """Replay re-runs every apply: the declaration is consumed silently, so it cannot
    reach the first live reveal."""
    narrator, transport = build_narrator()
    transport.muted = True
    publish_events(narrator, [
        name_guess_event(0, '03-047', NIRA_CHAN_HUMANOID_DEFINITIONS[0], 50),
        reveal_event(0, 1, '03-047', [NIRA_CHAN_HUMANOID_DEFINITIONS[1]]),
    ])

    assert transport.player_messages == {0: [], 1: []}
    assert transport.channel_messages == []

    transport.muted = False
    later = NIRA_CHAN_HUMANOID_DEFINITIONS[2]
    publish_reveal(narrator, 0, 1, '03-047', [later])
    assert contents(transport.channel_messages) == [
        plain_hand_pick_channel_line('03-047', 'Player 2', later)]


class SoloBotTransport(RecordingTransport):
    """Player `bot_index` is the solo bot, named and skipped as DiscordMatchTransport does."""

    def __init__(self, bot_index: int) -> None:
        super().__init__()
        self.bot_index = bot_index

    def display_name(self, session, player_index: int) -> str:
        if player_index == self.bot_index:
            return BOT_NAME
        return super().display_name(session, player_index)

    def delivers_to_player(self, session, player_index: int) -> bool:
        return player_index != self.bot_index and super().delivers_to_player(session, player_index)


def test_a_bot_declaration_skips_only_the_bot_dm():
    narrator, transport = build_narrator(SoloBotTransport(bot_index=1))
    renders = []

    async def fake_provider(revealed, columns):
        renders.append(columns)
        return object()

    narrator.reveal_image_provider = fake_provider
    named, revealed = NIRA_CHAN_HUMANOID_DEFINITIONS[2], NIRA_CHAN_HUMANOID_DEFINITIONS[0]
    publish_events(narrator, [
        name_guess_event(1, '03-059', named, 100),
        reveal_event(1, 0, '03-059', [revealed]),
    ])

    label = card_name_label(named)
    assert transport.player_messages[1] == [], 'no DM to the bot'
    assert renders == [1, 1], 'no image rendered for the bot'
    assert contents(transport.player_messages[0]) == [
        f'**Effect (03-059):** {BOT_NAME} named {label}. A card was revealed from your hand: '
        f'{label}. Same name: Attack +100 for {BOT_NAME}.']
    assert contents(transport.channel_messages) == [
        f"**Effect (03-059):** {BOT_NAME} named {label}. Revealed from Player 1's hand: "
        f'{label}. Same name: Attack +100.']
    assert narrator.pending_name_guess is None


# --- End to end: a real engine event through the real driver ---
# The match-regression corpus never resolves a TAIDADA reveal, so only this covers that path.

class EventRecordingNarrator(MatchNarrator):
    """Keeps every batch the driver publishes."""

    def __init__(self, session, transport) -> None:
        super().__init__(session, transport)
        self.batches: list[list[tuple]] = []

    async def publish(self, engine_events, board_view, snapshots=None) -> None:
        self.batches.append(list(engine_events))
        await super().publish(engine_events, board_view, snapshots)


def taidada_reveal_deck() -> list[int]:
    """20 cards (10 distinct x 2) stacked so reveal effects actually resolve:
    all 7 TAIDADA characters, three of which carry the reveal effect."""
    filler = [
        definition.index for definition in cards.CARD_DB
        if definition.effect_index == cards.NO_EFFECT
        and definition.index not in TAIDADA_CHARACTER_DEFINITIONS
    ][:3]
    deck = []
    for definition_index in TAIDADA_CHARACTER_DEFINITIONS + filler:
        deck.extend((definition_index, definition_index))
    return deck


def run_headless_game(
    seed: int, deck: list[int], adapter_class=ScriptedActionAdapter,
) -> EventRecordingNarrator:
    from engine_alpha.game import Game
    from tests.match.support import MemoryRecordStore
    from zutomayo.match.broker import MatchDecisionBroker
    from zutomayo.match.match_driver import EngineMatchDriver

    session = FakeSession(game_id=f'REVEAL-{seed:05d}')
    transport = RecordingTransport()
    store = MemoryRecordStore(session.game_id, session)
    adapter = adapter_class(lambda: session.broker, seed=seed)
    session.broker = MatchDecisionBroker(session, {0: adapter, 1: adapter}, store)
    session.transport = transport
    session.persistence = store
    session.game = Game(seed=seed, mode='fixed_decks', decks=(list(deck), list(deck)))
    narrator = EventRecordingNarrator(session, transport)
    driver = EngineMatchDriver(
        session, session.game, session.broker, narrator,
        {0: 'Player 1', 1: 'Player 2'},
    )
    asyncio.run(driver.run_to_completion())
    return narrator


def reveal_lines(messages) -> list[str]:
    """Reveal broadcasts only; the phase gate posts its own unrelated
    'Reveal set cards' header, which is about set slots, not hands."""
    return [
        str(message.get('content', '')) for message in messages
        if 'character(s):' in str(message.get('content', ''))
    ]


def test_reveal_reaches_discord_through_the_real_driver():
    deck = taidada_reveal_deck()
    for seed in range(8):
        transport = run_headless_game(seed, deck).transport
        channel = reveal_lines(transport.channel_messages)
        if channel:
            break
    else:
        raise AssertionError('no reveal effect resolved in seeds 0-7')

    owner_dms = reveal_lines(transport.player_messages[0])
    opponent_dms = reveal_lines(transport.player_messages[1])
    # Every reveal fans out to exactly three places, so each side sees one
    # message per broadcast regardless of who revealed.
    assert len(owner_dms) == len(channel)
    assert len(opponent_dms) == len(channel)
    for line in channel:
        assert line.startswith('**Effect (04-')
        assert 'TAIDADA character(s):' in line
    assert any('Attack +' in line for line in owner_dms + opponent_dms)


NAME_GUESS_SOURCE_DEFINITIONS = frozenset(
    carrier_definition_index(effect_id) for effect_id in ('03-047', '03-059', '03-094', '03-105'))


class CardNamingAdapter(ScriptedActionAdapter):
    """Scripted, except a name guess names a card in the opponent's hand, searched by its
    number as the Discord picker does, so matches occur. The scripted hand pick then
    decides which card is revealed; in the name-guess deck every effect number is one."""

    def __init__(self, broker_getter, seed: int = 0) -> None:
        super().__init__(broker_getter, seed)
        self.hand_picks = 0

    async def present_decision(self, session, request) -> None:
        engine_request = request.engine_request
        if engine_request is not None and engine_request.purpose == P_EFFECT_NUMBER:
            assert request.hidden_hand_pick, request.prompt_text
            self.hand_picks += 1
        if engine_request is None or engine_request.purpose != P_NAME_GUESS:
            await super().present_decision(session, request)
            return
        state = session.game.state
        hand = state.players[1 - request.player_index].hand
        held_definition = state.inst_def[hand[request.sequence_number % len(hand)]]
        named = search_card_names(cards.CARD_DB[held_definition].key).exact_entry.representative
        assert named == cards.NAME_REPRESENTATIVE_T[held_definition]
        self.broker_getter().submit(request.sequence_number, PAYLOAD_ACTION, named)


def name_guess_deck() -> list[int]:
    """20 cards (10 distinct x 2): the four name-guess cards, 03-047's reprints
    01-022 and 02-079, and no-effect filler."""
    distinct = sorted(NAME_GUESS_SOURCE_DEFINITIONS) + NIRA_CHAN_HUMANOID_DEFINITIONS[:2]
    distinct += [
        definition.index for definition in cards.CARD_DB
        if definition.effect_index == cards.NO_EFFECT and definition.index not in distinct
    ][:10 - len(distinct)]
    return [definition_index for definition_index in distinct for _ in range(2)]


def effect_lines(messages) -> list[str]:
    """Effect lines only; in the name-guess deck those are the name-guess reveals."""
    return [line for line in contents(messages) if line.startswith('**Effect (')]


def expected_name_guess_lines(declaration: tuple, reveal: tuple) -> tuple[str, str, str]:
    """Owner DM, opponent DM and channel lines for one name guess."""
    _, owner_index, source_definition, named_definition, attack_bonus = declaration
    prefix = f'**Effect ({cards.CARD_DB[source_definition].key}):**'
    player, opponent = f'Player {owner_index + 1}', f'Player {2 - owner_index}'
    named, revealed = card_name_label(named_definition), card_name_label(reveal[4])
    outcome = f'Same name: Attack +{attack_bonus}' if attack_bonus else 'Different name: no bonus'
    opponent_outcome = f'{outcome} for {player}' if attack_bonus else outcome
    return (
        f"{prefix} You named {named}. Revealed from opponent's hand: {revealed}. {outcome}.",
        f'{prefix} {player} named {named}. '
        f'A card was revealed from your hand: {revealed}. {opponent_outcome}.',
        f"{prefix} {player} named {named}. Revealed from {opponent}'s hand: {revealed}. {outcome}.",
    )


def test_name_guess_reaches_discord_through_the_real_driver():
    """name_guess_resolved arrives right before its cards_revealed, with the same owner and
    source, the merged lines reach both DMs and the channel, and each pick is flagged as
    a hidden hand pick. Seeds run until a miss and a match on another printing resolve."""
    deck = name_guess_deck()
    outcomes = set()
    hand_picks = 0
    for seed in range(40):
        narrator = run_headless_game(seed, deck, CardNamingAdapter)
        hand_picks += narrator.session.broker.adapters[0].hand_picks
        expected: dict[int | None, list[str]] = {0: [], 1: [], None: []}
        for batch in narrator.batches:
            for position, event in enumerate(batch):
                if event[0] == EVENT_CARDS_REVEALED and event[3] in NAME_GUESS_SOURCE_DEFINITIONS:
                    previous_event = batch[position - 1] if position else (None,)
                    assert previous_event[0] == EVENT_NAME_GUESS_RESOLVED, batch
                if event[0] != EVENT_NAME_GUESS_RESOLVED:
                    continue
                reveal = batch[position + 1]
                owner_index, source_definition, named_definition, attack_bonus = event[1:]
                assert reveal[:4] == (
                    EVENT_CARDS_REVEALED, owner_index, 1 - owner_index, source_definition)
                assert len(reveal) == 5, 'exactly one card revealed'
                assert (attack_bonus > 0) == cards.same_card_name(named_definition, reveal[4])
                owner_line, opponent_line, channel_line = expected_name_guess_lines(event, reveal)
                expected[owner_index].append(owner_line)
                expected[1 - owner_index].append(opponent_line)
                expected[None].append(channel_line)
                if not attack_bonus:
                    outcomes.add('miss')
                elif named_definition != reveal[4]:
                    outcomes.add('match on another printing')

        transport = narrator.transport
        assert effect_lines(transport.channel_messages) == expected[None]
        assert effect_lines(transport.player_messages[0]) == expected[0]
        assert effect_lines(transport.player_messages[1]) == expected[1]
        if outcomes == {'miss', 'match on another printing'}:
            break
    else:
        raise AssertionError(f'seeds 0-39 resolved only: {sorted(outcomes)}')
    assert hand_picks, 'no hidden hand pick was presented'
