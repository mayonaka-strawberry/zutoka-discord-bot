"""Card-name search for the name-guess prompt: one entry per full printed name
(Q&A No.50), found by English or Japanese name, song or card number."""

from __future__ import annotations

import dataclasses

import pytest

from engine_alpha import cards
from engine_alpha.effects import interpreter
from engine_alpha.game import Game
from zutomayo.match.card_names import (
    OPTION_TEXT_LIMIT,
    card_name_description,
    card_name_entries,
    card_name_label,
    entry_for_definition,
    search_card_names,
)
from tests.match.support import random_full_pool_decks

NO_RESULTS = ((), 0, False, None)


def entry(key: str):
    return entry_for_definition(cards.KEY_TO_INDEX[key])


def result_keys(query: str, limit: int = 25) -> list[str]:
    """The results' representative card keys, best first."""
    return [cards.CARD_DB[result_entry.representative].key
            for result_entry in search_card_names(query, limit).entries]


def name_guess_legal_actions() -> set[int]:
    """The engine's own legal set for a 03-047 name guess."""
    game = Game(seed=0, mode='fixed_decks', decks=random_full_pool_decks(0))
    while game.state.turn == 0:
        game.apply(game.legal_actions()[-1])
    state = game.state
    definition_index = cards.KEY_TO_INDEX['03-047']
    interpreter.start_effect(state, 0, state.new_instance(definition_index),
                             cards.EFFECT_T[definition_index])
    return set(interpreter.resume(state, None, None).legal_actions())


def test_one_entry_per_name_covers_every_printing():
    entries = card_name_entries()
    assert len(entries) == cards.NUM_CARD_NAMES
    representatives = [name_entry.representative for name_entry in entries]
    assert representatives == sorted(set(representatives))
    printed_keys = sorted(key for name_entry in entries for key in name_entry.card_keys)
    assert printed_keys == sorted(definition.key for definition in cards.CARD_DB)
    for definition in cards.CARD_DB:
        name_entry = entry_for_definition(definition.index)
        assert name_entry.representative == cards.NAME_REPRESENTATIVE_T[definition.index]
        assert definition.key in name_entry.card_keys
        assert list(name_entry.card_keys) == sorted(name_entry.card_keys)
        assert name_entry.card_keys[0] == cards.CARD_DB[name_entry.representative].key


def test_entry_fields():
    humanoid = entry('03-047')
    assert humanoid.representative == cards.KEY_TO_INDEX['01-022']
    assert humanoid.english_name == 'NIRA-CHAN (Humanoid)'
    assert humanoid.japanese_name == 'にらちゃん（ヒューマノイド）'
    assert humanoid.type_label == 'Character'
    assert humanoid.card_keys == ('01-022', '02-079', '03-047')
    assert entry('03-059').type_label == 'Enchant'
    assert entry('04-030').type_label == 'Area Enchant'


def test_english_queries():
    # HUMANOID (Humanoid) starts with the query, so it outranks the word starts.
    assert result_keys('hum') == ['02-076', '01-022', '01-005']
    assert result_keys('nira humanoid') == ['01-022']
    assert result_keys('humanoid nira') == ['01-022']
    assert result_keys('taidada') == ['04-001', '04-067', '04-068', '04-070']
    assert search_card_names('humanoid taidada') == NO_RESULTS, 'every word must match one name'
    # Word starts (Red, RED), then names merely containing it.
    assert result_keys('red') == ['01-088', '03-014', '01-085', '04-060', '03-030', '04-013']
    # Ties go by English name ignoring case: Uniguri Smash sits among the UNIGURI names.
    assert result_keys('uniguri')[:7] == [
        '02-030', '01-021', '01-057', '03-048', '03-063', '01-037', '03-032']


def test_names_containing_the_query_outrank_loose_matches():
    """The loose form (no spaces, ou to o) only adds matches after the real ones."""
    assert result_keys('tour')[0] == '02-088', 'Factory tour, not TORI-NIRA-CHAN'
    assert result_keys('count') == ['04-091', '02-094', '01-094', '03-060']
    assert result_keys('nou')[:4] == ['01-053', '03-080', '01-049', '03-014'], 'Nouriueno'
    assert result_keys('sou')[:2] == ['01-071', '01-073'], 'Konnakoto Soudou'
    really_containing = {name_entry for name_entry in card_name_entries()
                         if any('ing' in form for form in name_entry.search_forms)}
    every_match = search_card_names('ing', limit=cards.NUM_CARD_NAMES).entries
    assert set(every_match[:len(really_containing)]) == really_containing


def test_japanese_queries_fold_katakana_and_character_width():
    assert result_keys('にらちゃん ヒューマノイド') == ['01-022']
    assert result_keys('にらちゃんヒューマノイド') == ['01-022']
    nira = search_card_names('ニラ')
    assert nira.total == 60
    assert nira == search_card_names('にら') == search_card_names('ﾆﾗ')
    assert nira.entries[0] == entry('03-044'), 'every にら name is a prefix match; NIRA-BAND sorts first'
    second_hand = result_keys('秒針')
    assert second_hand[0] == '03-006', 'the card named 秒針を噛む'
    assert all('秒針' in entry(key).japanese_name for key in second_hand)
    shogast = ['03-049', '03-073', '04-066', '04-046']    # exact, prefix, prefix, substring
    assert result_keys('しょがすと') == shogast
    assert result_keys('しょ がすと') == shogast, 'the loose form ranks a split query alike'
    assert result_keys('シャクマ 男') == ['01-036', '01-005'], 'しゃくま男, then しゃくま男（…）'
    assert search_card_names('ずとまよあーかいゔ') == ((entry('04-052'),), 1, False, None), 'ヴ'
    assert result_keys('トリート') == ['03-026'], 'ー is part of the word, not とり + と'


def test_romaji_long_vowels_and_diacritics_fold():
    assert result_keys('byoshin') == result_keys('byoushin') == result_keys('byōshin')
    assert '01-016' in result_keys('byoshin'), 'NIRA-CHAN (Byoushinwo Kamu)'
    assert result_keys('sauteed') == result_keys('sautéed') == ['01-059']
    assert search_card_names('uchuujin') == ((entry('01-068'), entry('04-067')), 2, False, None)


@pytest.mark.parametrize('query', [
    '03-047', '3-47', '０３－０４７', '03ー047', '03ｰ047', '03 ー 047', '03−047', '03–047',
    '03—047', '03‐047', '03‑047', '03―047', '03/047', '03.047', '#03-047', ' 03 047 ',
])
def test_a_card_number_gives_that_printings_name(query):
    assert search_card_names(query) == ((entry('01-022'),), 1, False, entry('01-022'))


def test_an_unknown_card_number_is_searched_as_text():
    assert search_card_names('09-001') == NO_RESULTS
    assert result_keys('6 30') == ['03-096'], '6:30am, not card 06-030'
    assert result_keys('14.4') == ['02-096'], '14.4 light-years away, not card 14-004'


def test_every_name_and_card_number_finds_its_own_entry_first():
    for name_entry in card_name_entries():
        for name in (name_entry.english_name, name_entry.japanese_name):
            result = search_card_names(name)
            assert result.exact_entry == result.entries[0] == name_entry, name
    for definition in cards.CARD_DB:
        assert search_card_names(definition.key).exact_entry == entry_for_definition(definition.index)


def test_kana_voicing_marks_survive_normalization():
    """Diacritics come off Latin letters only: ダンス is not タンス, パン is not ハン."""
    assert '03-029' in result_keys('ダンス'), 'stored as half-width ﾀﾞﾝｽ'
    assert search_card_names('タンス') == NO_RESULTS
    assert result_keys('パン') == ['02-042', '01-057']


def test_the_song_is_indexed_unless_none():
    """Song NONE would make "no" and "on" hit every song-less card."""
    assert entry('04-041').search_forms == ('inc neko shade', 'いんく猫 しぇーどの埃は延長', 'shade')
    assert entry('03-094').search_forms == ('shoot', '撃つ')
    for query in ('no', 'on'):
        assert '03-094' not in result_keys(query, limit=cards.NUM_CARD_NAMES), query


@pytest.mark.parametrize('query', ['', '   ', '()', '（）', '・', '!?'])
def test_a_query_without_letters_or_digits_returns_nothing(query):
    assert search_card_names(query) == NO_RESULTS


def test_a_misspelled_word_falls_back_to_the_closest_words():
    misspelled = search_card_names('humaniod')
    assert misspelled.is_fuzzy
    assert misspelled.entries == search_card_names('humanoid').entries
    assert not search_card_names('humanoid').is_fuzzy
    assert result_keys('nira humaniod') == ['01-022']
    assert search_card_names('nira xyzzy') == NO_RESULTS


def test_fuzzy_results_put_the_closest_replacement_first():
    """hippocampal is closer to the typo than hippocampus, uniguri than unigiri."""
    assert result_keys('hipocampal') == ['04-006', '04-066', '04-107']
    misspelled = search_card_names('unguri', limit=cards.NUM_CARD_NAMES)
    assert misspelled.is_fuzzy
    assert misspelled.entries == (
        search_card_names('uniguri', limit=cards.NUM_CARD_NAMES).entries + (entry('02-064'),))


def test_a_typo_found_inside_another_name_retries_every_word():
    """"rest" occurs in Firestorm, so no name has both words until rest becomes reset."""
    assert '04-103' in result_keys('rest')
    misspelled = search_card_names('neko rest', limit=cards.NUM_CARD_NAMES)
    assert misspelled.is_fuzzy
    assert misspelled.entries == search_card_names('neko reset', limit=cards.NUM_CARD_NAMES).entries
    assert misspelled.total == 11


def test_results_are_capped_with_the_full_total():
    capped = search_card_names('nira chan')
    assert len(capped.entries) == 25
    assert capped.total == 53
    shorter = search_card_names('nira chan', limit=5)
    assert shorter.entries == capped.entries[:5]
    assert shorter.total == 53
    assert search_card_names('03-047', limit=0) == ((), 1, False, entry('01-022'))


def test_a_verbatim_full_name_ranks_first_and_is_the_exact_entry():
    """Both names normalize to "nira chan warmthaholic"; only a verbatim query picks one."""
    plain, dotted = entry('04-078'), entry('04-088')
    result = search_card_names('NIRA-CHAN (Warmthaholic)')
    assert result.entries == (plain, dotted)
    assert result.exact_entry == plain
    assert search_card_names('NIRA-CHAN...? (Warmthaholic)').entries == (dotted, plain)
    assert search_card_names('にらちゃん…？（微熱魔）').exact_entry == dotted
    assert search_card_names('にらちゃん(微熱魔)').exact_entry == plain, 'half-width parentheses'
    assert search_card_names('NIRA-CHAN  (Humanoid)').exact_entry == entry('01-022'), 'spacing'
    assert search_card_names('nira chan warmthaholic').exact_entry is None


def test_labels_and_descriptions_fit_discord_select_options():
    for name_entry in card_name_entries():
        assert len(card_name_label(name_entry.representative)) <= OPTION_TEXT_LIMIT
        assert len(card_name_description(name_entry)) <= OPTION_TEXT_LIMIT
    assert card_name_label(cards.KEY_TO_INDEX['03-047']) == \
        'NIRA-CHAN (Humanoid) [にらちゃん（ヒューマノイド）]'
    assert card_name_description(entry('03-047')) == 'Character | 01-022, 02-079, 03-047'
    crowded = dataclasses.replace(
        entry('03-047'), card_keys=tuple(f'01-{number:03d}' for number in range(1, 16)))
    assert card_name_description(crowded) == 'Character | 15 printings'


def test_order_is_deterministic_and_every_result_is_a_legal_name_guess():
    legal = name_guess_legal_actions()
    assert legal == set(range(cards.NUM_CARDS))
    for query in ('nira', 'ニラ', 'hum', 'byoshin', 'humaniod', 'NIRA-CHAN (Warmthaholic)', '03-047'):
        result = search_card_names(query, limit=cards.NUM_CARD_NAMES)
        assert result == search_card_names(query, limit=cards.NUM_CARD_NAMES), query
        assert all(result_entry.representative in legal for result_entry in result.entries)
