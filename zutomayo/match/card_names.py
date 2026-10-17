"""
Card-name search for the name-guess prompt (03-047 family). A card name is the full
printed name, parenthesized part included (Q&A No.50), so one entry stands for every
printing of a name. Search reads only the query, never game state. No discord imports.
"""

from __future__ import annotations

import difflib
import functools
import re
import unicodedata
from dataclasses import dataclass, field
from typing import NamedTuple, Optional

from engine_alpha.cards import CARD_DB, KEY_TO_INDEX, NAME_REPRESENTATIVE_T
from zutomayo.enums.card_type import CardType
from zutomayo.enums.song import Song
from zutomayo.match.state_view import definition_index_to_card

# Local copy: zutomayo.ui.embeds imports discord.
CARD_TYPE_LABELS = {
    CardType.CHARACTER: 'Character',
    CardType.ENCHANT: 'Enchant',
    CardType.AREA_ENCHANT: 'Area Enchant',
}

DEFAULT_SEARCH_LIMIT = 25    # a Discord select menu holds at most 25 options
OPTION_TEXT_LIMIT = 100      # Discord's select option label and description limit
FUZZY_CUTOFF = 0.75
FUZZY_WORDS_PER_TOKEN = 3

# Ranking tiers, best first.
TIER_VERBATIM, TIER_EXACT, TIER_PREFIX, TIER_WORD_START, TIER_SUBSTRING = range(5)

# English and Japanese names lead each entry's search forms; the song follows.
NAME_FORM_COUNT = 2

# A normalized card number; dashes, slashes, dots and # are spaces by then: 03 047, 3 47, 03ー047.
_CARD_NUMBER_PATTERN = re.compile(r'([0-9]{1,2})(?: ?ー ?| )([0-9]{1,3})')
_U_RUN_PATTERN = re.compile(r'u{2,}')


@dataclass(frozen=True)
class CardNameEntry:
    """One card name and its printings."""
    representative: int              # lowest definition index of the name; the action to submit
    english_name: str
    japanese_name: str
    type_label: str
    card_keys: tuple[str, ...]       # printings, ascending
    # Precomputed search forms.
    verbatim_forms: tuple[str, ...] = field(repr=False)      # names up to width, case, spacing
    search_forms: tuple[str, ...] = field(repr=False)        # normalized names, then the song
    loose_search_forms: tuple[str, ...] = field(repr=False)
    search_words: tuple[str, ...] = field(repr=False)
    loose_search_words: tuple[str, ...] = field(repr=False)


class CardNameSearchResult(NamedTuple):
    entries: tuple[CardNameEntry, ...]    # best first, at most `limit`
    total: int                            # every match, before the limit
    is_fuzzy: bool                        # the entries match the closest words to a misspelling
    exact_entry: Optional[CardNameEntry]  # set for a card number or a verbatim full name


_NO_RESULTS = CardNameSearchResult((), 0, False, None)


# --- Normalization (query and index alike) ---

def _verbatim_form(text: str) -> str:
    return ' '.join(unicodedata.normalize('NFKC', text).casefold().split())


def _normalize(text: str) -> str:
    """NFKC and casefold; strip Latin diacritics only, so ば stays ば; katakana to
    hiragana; punctuation, symbols and separators to single spaces."""
    decomposed = unicodedata.normalize('NFD', unicodedata.normalize('NFKC', text).casefold())
    kept = []
    base_is_latin = False
    for character in decomposed:
        if unicodedata.category(character).startswith('M'):
            if base_is_latin:
                continue
        else:
            base_is_latin = unicodedata.name(character, '').startswith('LATIN ')
        kept.append(character)
    folded = []
    for character in unicodedata.normalize('NFC', ''.join(kept)):
        code_point = ord(character)
        if 0x30A1 <= code_point <= 0x30F6 or 0x30FD <= code_point <= 0x30FE:
            folded.append(chr(code_point - 0x60))
        elif unicodedata.category(character)[0] in 'PSZC':
            folded.append(' ')
        else:
            folded.append(character)
    return ' '.join(''.join(folded).split())


def _loose(normalized: str) -> str:
    """Space-free, with romaji long vowels folded (uu to u, ou to o): byoshin finds byoushin."""
    return _U_RUN_PATTERN.sub('u', normalized.replace(' ', '')).replace('ou', 'o')


# --- Entries ---

def _build_entry(representative: int, card_keys: tuple[str, ...]) -> CardNameEntry:
    card = definition_index_to_card(representative)
    texts = [card.name, card.name_jp]
    # Song NONE would make "no" and "on" hit every song-less card.
    if card.song is not Song.NONE:
        texts.append(card.song.value)
    search_forms = tuple(_normalize(text) for text in texts)
    search_words = tuple(dict.fromkeys(word for form in search_forms for word in form.split()))
    return CardNameEntry(
        representative=representative,
        english_name=card.name,
        japanese_name=card.name_jp,
        type_label=CARD_TYPE_LABELS[card.card_type],
        card_keys=card_keys,
        verbatim_forms=(_verbatim_form(card.name), _verbatim_form(card.name_jp)),
        search_forms=search_forms,
        loose_search_forms=tuple(_loose(form) for form in search_forms),
        search_words=search_words,
        loose_search_words=tuple(_loose(word) for word in search_words),
    )


@functools.cache
def card_name_entries() -> tuple[CardNameEntry, ...]:
    """One entry per card name, ordered by representative."""
    keys_by_representative: dict[int, list[str]] = {}
    for definition in CARD_DB:
        keys_by_representative.setdefault(
            NAME_REPRESENTATIVE_T[definition.index], []).append(definition.key)
    return tuple(_build_entry(representative, tuple(keys))
                 for representative, keys in sorted(keys_by_representative.items()))


@functools.cache
def _entries_by_representative() -> dict[int, CardNameEntry]:
    return {entry.representative: entry for entry in card_name_entries()}


@functools.cache
def _search_vocabulary() -> tuple[str, ...]:
    return tuple(sorted({word for entry in card_name_entries() for word in entry.search_words}))


def entry_for_definition(definition_index: int) -> CardNameEntry:
    """The entry for any printing."""
    return _entries_by_representative()[NAME_REPRESENTATIVE_T[definition_index]]


# --- Search ---

class _Token(NamedTuple):
    """A query word, or an indexed word standing in for it."""
    normal: str
    loose: str
    distance: float    # 1 - difflib ratio to the typed word; 0 for the typed word


class _RankKey(NamedTuple):
    """Sort key; lower ranks first."""
    loose_only: bool       # some word matched only in the loose form
    distance: float        # summed over the words: the closest replacements lead
    tier: int
    english_name: str      # casefolded
    representative: int


@functools.lru_cache(maxsize=1024)
def _close_tokens(word: str) -> tuple[_Token, ...]:
    """The indexed words closest to a word, closest first, without the word itself."""
    return tuple(
        _Token(close, _loose(close), 1 - difflib.SequenceMatcher(None, close, word).ratio())
        for close in difflib.get_close_matches(
            word, _search_vocabulary(), n=FUZZY_WORDS_PER_TOKEN, cutoff=FUZZY_CUTOFF)
        if close != word)


def _contains_normalized(entry: CardNameEntry, token: _Token) -> bool:
    return any(token.normal in form for form in entry.search_forms)


def _contains_loose(entry: CardNameEntry, token: _Token) -> bool:
    return any(token.loose in form for form in entry.loose_search_forms)


def _starts_a_word(entry: CardNameEntry, token: _Token) -> bool:
    return (any(word.startswith(token.normal) for word in entry.search_words)
            or any(word.startswith(token.loose) for word in entry.loose_search_words))


def _match_tier(entry: CardNameEntry, verbatim_query: str, tokens: list[_Token]) -> int:
    if verbatim_query in entry.verbatim_forms:
        return TIER_VERBATIM
    phrase = ' '.join(token.normal for token in tokens)
    loose_phrase = _loose(phrase)
    names = entry.search_forms[:NAME_FORM_COUNT]
    loose_names = entry.loose_search_forms[:NAME_FORM_COUNT]
    if phrase in names or loose_phrase in loose_names:
        return TIER_EXACT
    if (any(name.startswith(phrase) for name in names)
            or any(name.startswith(loose_phrase) for name in loose_names)):
        return TIER_PREFIX
    if all(_starts_a_word(entry, token) for token in tokens):
        return TIER_WORD_START
    return TIER_SUBSTRING


def _ranked_matches(verbatim_query: str, alternatives_per_word: list[tuple[_Token, ...]]
                    ) -> list[tuple[_RankKey, CardNameEntry]]:
    """Entries matching one alternative per query word, best first. Per word, the closest
    alternative found in the normalized form beats one found only in the loose form."""
    ranked = []
    for entry in card_name_entries():
        matched_tokens = []
        loose_only = False
        for alternatives in alternatives_per_word:
            matched = next(
                (token for token in alternatives if _contains_normalized(entry, token)), None)
            if matched is None:
                matched = next(
                    (token for token in alternatives if _contains_loose(entry, token)), None)
                if matched is None:
                    break
                loose_only = True
            matched_tokens.append(matched)
        else:
            key = _RankKey(loose_only, sum(token.distance for token in matched_tokens),
                           _match_tier(entry, verbatim_query, matched_tokens),
                           entry.english_name.casefold(), entry.representative)
            ranked.append((key, entry))
    ranked.sort(key=lambda item: item[0])
    return ranked


def _entry_for_card_number(normalized_query: str) -> Optional[CardNameEntry]:
    match = _CARD_NUMBER_PATTERN.fullmatch(normalized_query)
    if match is None:
        return None
    definition_index = KEY_TO_INDEX.get(f'{int(match.group(1)):02d}-{int(match.group(2)):03d}')
    return None if definition_index is None else entry_for_definition(definition_index)


def search_card_names(query: str, limit: int = DEFAULT_SEARCH_LIMIT) -> CardNameSearchResult:
    """Card names for a typed query, best first. A card number gives that card's name.
    Otherwise every word must appear in the English name, the Japanese name or the song.
    A word found nowhere takes its closest indexed words instead; if no name matches then,
    every word also accepts its closest words (both set is_fuzzy)."""
    normalized_query = _normalize(query)
    if not normalized_query:
        return _NO_RESULTS
    number_entry = _entry_for_card_number(normalized_query)
    if number_entry is not None:
        return CardNameSearchResult((number_entry,)[:max(limit, 0)], 1, False, number_entry)

    entries = card_name_entries()
    typed_tokens = [_Token(word, _loose(word), 0.0) for word in normalized_query.split()]
    found = [any(_contains_normalized(entry, token) or _contains_loose(entry, token)
                 for entry in entries) for token in typed_tokens]
    alternatives_per_word = [(token,) if token_found else _close_tokens(token.normal)
                             for token, token_found in zip(typed_tokens, found)]
    if not all(alternatives_per_word):
        return _NO_RESULTS
    verbatim_query = _verbatim_form(query)
    ranked = _ranked_matches(verbatim_query, alternatives_per_word)
    is_fuzzy = not all(found)
    if not ranked and any(found):
        # The typo may occur inside another name ("rest" in Firestorm).
        ranked = _ranked_matches(
            verbatim_query, [(token,) + _close_tokens(token.normal) for token in typed_tokens])
        is_fuzzy = True
    if not ranked:
        return _NO_RESULTS
    exact_entry = ranked[0][1] if ranked[0][0].tier == TIER_VERBATIM else None
    return CardNameSearchResult(tuple(entry for _, entry in ranked[:max(limit, 0)]),
                                len(ranked), is_fuzzy, exact_entry)


# --- Display ---

def card_name_label(definition_index: int) -> str:
    """'EN [JP]' for any printing of the name."""
    entry = entry_for_definition(definition_index)
    return f'{entry.english_name} [{entry.japanese_name}]'


def card_name_description(entry: CardNameEntry) -> str:
    """'Character | 01-022, 02-079, 03-047', or the printing count past Discord's limit."""
    description = f'{entry.type_label} | {", ".join(entry.card_keys)}'
    if len(description) > OPTION_TEXT_LIMIT:
        return f'{entry.type_label} | {len(entry.card_keys)} printings'
    return description
