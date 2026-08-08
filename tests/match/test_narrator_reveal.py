"""MatchNarrator reveal broadcasts.

Revealing is only meaningful if the opponent actually sees what was revealed,
so these pin the three legs of the broadcast (owner DM, opponent DM, channel)
and the text of each. Image rendering is left unwired except where it is the
subject, so headless runs never touch card art.
"""

from __future__ import annotations

import asyncio

from engine_alpha import cards
from engine_alpha.events import EVENT_CARDS_REVEALED
from tests.match.support import FakeSession, RecordingTransport
from zutomayo.match.narrator import MatchNarrator
from zutomayo.match.state_view import definition_index_to_card

TAIDADA_CHARACTER_DEFS = [
    d.index for d in cards.CARD_DB
    if d.song == cards.SONG_NAMES.index('TAIDADA') and d.card_type == cards.TYPE_CHARACTER
]


def carrier_definition_index(effect_id: str) -> int:
    """The definition index of the card carrying `effect_id`."""
    return cards.EFFECT_TO_CARD[cards.EFFECT_TO_INDEX[effect_id]]


def build_narrator() -> tuple[MatchNarrator, RecordingTransport]:
    session = FakeSession()
    transport = RecordingTransport()
    session.transport = transport
    return MatchNarrator(session, transport), transport


def revealed_names(definition_indices) -> str:
    return ', '.join(definition_index_to_card(i).name for i in definition_indices)


def publish_reveal(narrator, owner_index, revealed_owner_index, effect_id, revealed):
    event = (EVENT_CARDS_REVEALED, owner_index, revealed_owner_index,
             carrier_definition_index(effect_id), *revealed)
    # board_view is untouched by the reveal branch; only phase/redraw events read it.
    asyncio.run(narrator.publish([event], None))


def test_taidada_reveal_reaches_both_players_and_the_channel():
    narrator, transport = build_narrator()
    revealed = TAIDADA_CHARACTER_DEFS[:2]
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
    publish_reveal(narrator, 0, 0, '04-035', TAIDADA_CHARACTER_DEFS[:3])

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
    revealed = TAIDADA_CHARACTER_DEFS[:3]
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
    publish_reveal(narrator, 0, 0, '04-001', TAIDADA_CHARACTER_DEFS[:2])

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
    publish_reveal(narrator, 0, 0, '04-001', TAIDADA_CHARACTER_DEFS[:2])

    assert transport.player_messages == {0: [], 1: []}
    assert transport.channel_messages == []


# ---------------------------------------------------------------------------
# End to end: a real engine event, through the real driver, out to Discord.
# The regression this fixes lived in the seam between those layers, and the
# standing match-regression corpus never draws a reveal card, so nothing else
# covers the whole path.
# ---------------------------------------------------------------------------

def taidada_reveal_deck() -> list[int]:
    """20 cards (10 distinct x 2) stacked so reveal effects actually resolve:
    all 7 TAIDADA characters, three of which carry the reveal effect."""
    filler = [
        d.index for d in cards.CARD_DB
        if d.effect_index == cards.NO_EFFECT and d.index not in TAIDADA_CHARACTER_DEFS
    ][:3]
    deck = []
    for definition_index in TAIDADA_CHARACTER_DEFS + filler:
        deck.extend((definition_index, definition_index))
    return deck


def run_headless_game(seed: int, deck: list[int]) -> RecordingTransport:
    from engine_alpha.game import Game
    from tests.match.support import MemoryRecordStore, ScriptedActionAdapter
    from zutomayo.match.broker import MatchDecisionBroker
    from zutomayo.match.match_driver import EngineMatchDriver

    session = FakeSession(game_id=f'REVEAL-{seed:05d}')
    transport = RecordingTransport()
    store = MemoryRecordStore(session.game_id, session)
    adapter = ScriptedActionAdapter(lambda: session.broker, seed=seed)
    session.broker = MatchDecisionBroker(session, {0: adapter, 1: adapter}, store)
    session.transport = transport
    session.persistence = store
    session.game = Game(seed=seed, mode='fixed_decks', decks=(list(deck), list(deck)))
    narrator = MatchNarrator(session, transport)
    driver = EngineMatchDriver(
        session, session.game, session.broker, narrator,
        {0: 'Player 1', 1: 'Player 2'},
    )
    asyncio.run(driver.run_to_completion())
    return transport


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
        transport = run_headless_game(seed, deck)
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
