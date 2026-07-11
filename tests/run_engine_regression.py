"""
Tier A engine regression harness.

Plays seeded headless games through HeadlessGameEnvV2 (the same environment the
V2 training stack uses) with deterministic scripted agents and fixed decks,
recording a full transcript of prompts, effect dispatches, messages, and state
digests per game. Baselines live in tests/baselines/engine/.

Usage (from the repository root):
    python tests/run_engine_regression.py write               # regenerate baselines
    python tests/run_engine_regression.py compare             # rerun and diff against baselines
    python tests/run_engine_regression.py compare --smoke     # quick 5-game diff
    python tests/run_engine_regression.py write --limit 20

Determinism contract: before each game the module random state is seeded with
the game's seed. Scripted agents consume no module random state, so the module
stream feeds exactly the coin flip, deck shuffles, and the four shuffling
effects — the same consumers before and after the Stage 3 RNG-ownership move.
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import sys
import time
import traceback
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

import random  # noqa: E402

import zutomayo.effects.effect_engine as effect_engine_module  # noqa: E402
import zutomayo.engine.turn_manager as turn_manager_module  # noqa: E402
import zutomayo.engine.uniguri_env_v2 as environment_module  # noqa: E402
import zutomayo.ui.embeds as embeds_module  # noqa: E402
from zutomayo.data.card_loader import load_cards  # noqa: E402
from zutomayo.data.deck_validator import build_card_index  # noqa: E402

from tests.scripted_agents import ScriptedVarietyAgent  # noqa: E402
from tests.transcript import TranscriptRecorder, card_identity  # noqa: E402

DEFAULT_BASELINE_DIRECTORY = REPOSITORY_ROOT / 'tests' / 'baselines' / 'engine'
DECK_SOURCE_FILES = (
    ('default_decks.json', 'default'),
    ('best_decks_v2.json', 'best'),
)

# The recorder for the game currently being played. The recording engine and
# turn manager classes are instantiated deep inside HeadlessGameEnvV2.reset(),
# so they reach the recorder through this module-level slot.
_ACTIVE_RECORDER: TranscriptRecorder | None = None

_ORIGINAL_TURN_MANAGER = turn_manager_module.TurnManager
_ORIGINAL_HEADLESS_EFFECT_ENGINE = environment_module.HeadlessEffectEngineV2


class RecordingHeadlessEffectEngine(_ORIGINAL_HEADLESS_EFFECT_ENGINE):
    """HeadlessEffectEngineV2 that records every prompt, dispatch, and message."""

    async def _prompt_effect_order(self, player_index, eligible):
        ordered = await super()._prompt_effect_order(player_index, eligible)
        if _ACTIVE_RECORDER is not None:
            _ACTIVE_RECORDER.record_prompt(
                'effect_order', player_index,
                [card_identity(card_instance) for card_instance in eligible],
                'indices',
                [eligible.index(card_instance) for card_instance in ordered],
            )
        return ordered

    async def _prompt_card_selection(self, player_index, cards, prompt_text, placeholder='', *, purpose=''):
        chosen = await super()._prompt_card_selection(player_index, cards, prompt_text, placeholder, purpose=purpose)
        if _ACTIVE_RECORDER is not None:
            _ACTIVE_RECORDER.record_prompt(
                'effect_card_select', player_index,
                [card_identity(card_instance) for card_instance in cards],
                'indices',
                None if chosen is None else [cards.index(chosen)],
            )
        return chosen

    async def _prompt_number_selection(
        self, player_index, min_value, max_value, prompt_text, placeholder='', label_prefix=None,
    ):
        value = await super()._prompt_number_selection(
            player_index, min_value, max_value, prompt_text, placeholder, label_prefix,
        )
        if _ACTIVE_RECORDER is not None:
            _ACTIVE_RECORDER.record_prompt(
                'effect_number_select', player_index,
                [f'{min_value}..{max_value}'],
                'number', value,
            )
        return value

    async def _prompt_text_input(
        self, player_index, prompt_text, modal_title='', button_label='', input_label=None,
        input_placeholder=None, validator=None,
    ):
        value = await super()._prompt_text_input(
            player_index, prompt_text, modal_title, button_label, input_label,
            input_placeholder, validator,
        )
        if _ACTIVE_RECORDER is not None:
            _ACTIVE_RECORDER.record_prompt('effect_text_input', player_index, [], 'text', value)
        return value

    async def _send_dm(self, player_index, **kwargs):
        if _ACTIVE_RECORDER is not None:
            embed = kwargs.get('embed')
            _ACTIVE_RECORDER.record_message(
                f'dm_{player_index}',
                kwargs.get('content'),
                embed.title if embed is not None else None,
            )
        return await super()._send_dm(player_index, **kwargs)

    async def _send_to_channel(self, **kwargs):
        if _ACTIVE_RECORDER is not None:
            embed = kwargs.get('embed')
            _ACTIVE_RECORDER.record_message(
                'channel',
                kwargs.get('content'),
                embed.title if embed is not None else None,
            )
        return await super()._send_to_channel(**kwargs)

    async def _dispatch(self, game_state, player_index, card_instance):
        effect_id = card_instance.card.effect
        if effect_id in effect_engine_module._EFFECT_HANDLERS:
            _DISPATCHED_EFFECT_IDS.add(effect_id)
            if _ACTIVE_RECORDER is not None:
                _ACTIVE_RECORDER.record_effect_dispatch(effect_id, player_index)
        return await super()._dispatch(game_state, player_index, card_instance)

    async def process_effects(self, game_state, player_index):
        result = await super().process_effects(game_state, player_index)
        if _ACTIVE_RECORDER is not None:
            _ACTIVE_RECORDER.record_state_digest(game_state, f'process_effects_player_{player_index}')
        return result


class RecordingTurnManager(_ORIGINAL_TURN_MANAGER):
    """TurnManager that records a state digest after battle and turn end."""

    def resolve_battle(self, *args, **kwargs):
        result = super().resolve_battle(*args, **kwargs)
        if _ACTIVE_RECORDER is not None:
            _ACTIVE_RECORDER.record_state_digest(self.game_state, 'resolve_battle')
        return result

    def end_turn(self, player, *args, **kwargs):
        result = super().end_turn(player, *args, **kwargs)
        if _ACTIVE_RECORDER is not None:
            _ACTIVE_RECORDER.record_state_digest(self.game_state, f'end_turn_player_{player.index}')
        return result


class RecordingEnvironment(environment_module.HeadlessGameEnvV2):
    """Environment that records a setup digest right after reset."""

    def reset(self):
        observation = super().reset()
        if _ACTIVE_RECORDER is not None:
            _ACTIVE_RECORDER.record_state_digest(self.game_state, 'setup')
        return observation


_DISPATCHED_EFFECT_IDS: set[str] = set()


def _stub_deck_grid_rendering() -> None:
    """
    Replace create_deck_grid_image with a cheap recording stub.

    The real function composites card art with PIL purely for Discord output;
    headless engines discard the result (_send_dm returns None), so stubbing it
    changes no game state and no recorded prompt/message content, while making
    the run orders of magnitude faster. Effect modules bound the name at import
    time, so the stub is installed on every already-imported effects module too.
    """

    def recording_stub(cards, *args, **kwargs):
        if _ACTIVE_RECORDER is not None:
            _ACTIVE_RECORDER.record_render(
                'create_deck_grid_image',
                [card_identity(card_instance) for card_instance in cards],
            )
        return None

    async def recording_stub_off_thread(cards, *args, **kwargs):
        # Same recorded label as the sync stub so baselines are unaffected by
        # the Stage 1 move to off-thread rendering wrappers.
        return recording_stub(cards, *args, **kwargs)

    embeds_module.create_deck_grid_image = recording_stub
    if hasattr(embeds_module, 'create_deck_grid_image_off_thread'):
        embeds_module.create_deck_grid_image_off_thread = recording_stub_off_thread
    for module_name, module in list(sys.modules.items()):
        if not module_name.startswith('zutomayo.effects.cards.'):
            continue
        if hasattr(module, 'create_deck_grid_image'):
            module.create_deck_grid_image = recording_stub
        if hasattr(module, 'create_deck_grid_image_off_thread'):
            module.create_deck_grid_image_off_thread = recording_stub_off_thread


def _install_recording_classes() -> None:
    environment_module.HeadlessEffectEngineV2 = RecordingHeadlessEffectEngine
    turn_manager_module.TurnManager = RecordingTurnManager
    _stub_deck_grid_rendering()


def load_deck_definitions() -> list[tuple[str, list[dict]]]:
    """Return [(slug, [{'pack': int, 'id': int}, ...20 entries]), ...]."""
    definitions = []
    for file_name, prefix in DECK_SOURCE_FILES:
        raw = json.loads((REPOSITORY_ROOT / 'zutomayo' / file_name).read_text(encoding='utf-8'))
        for deck_number, deck in enumerate(raw['decks']):
            definitions.append((f'{prefix}{deck_number:02d}', deck['cards']))
    return definitions


def build_synthetic_deck_definitions() -> list[tuple[str, list[dict]]]:
    """
    Chunk the entire card catalog into valid 20-card decks (10 distinct cards,
    2 copies each) so that every card — and therefore as many effects as the
    game state allows — gets exercised by the regression games. Sorted by
    (pack, id) so the chunking is stable across runs.
    """
    all_cards = sorted(load_cards(), key=lambda card: (card.pack, card.id))
    definitions = []
    for chunk_start in range(0, len(all_cards) - len(all_cards) % 10, 10):
        chunk = all_cards[chunk_start:chunk_start + 10]
        entries = []
        for card in chunk:
            entries.append({'pack': card.pack, 'id': card.id})
            entries.append({'pack': card.pack, 'id': card.id})
        definitions.append((f'synthetic{chunk_start // 10:02d}', entries))
    # Fold any remainder cards into one final deck padded from the catalog start.
    remainder = len(all_cards) % 10
    if remainder:
        chunk = all_cards[-remainder:] + all_cards[:10 - remainder]
        entries = []
        for card in chunk:
            entries.append({'pack': card.pack, 'id': card.id})
            entries.append({'pack': card.pack, 'id': card.id})
        definitions.append((f'synthetic{len(all_cards) // 10:02d}', entries))
    return definitions


def build_game_matrix(seeds_per_pair: int) -> list[tuple[str, str, str, int]]:
    """Return [(file_stem, slug_0, slug_1, seed), ...] for every deck pair."""
    definitions = load_deck_definitions()
    matrix = []
    pair_index = 0
    for first in range(len(definitions)):
        for second in range(first, len(definitions)):
            slug_0 = definitions[first][0]
            slug_1 = definitions[second][0]
            base_seed = 20_000 + pair_index * 101
            for seed_number in range(seeds_per_pair):
                seed = base_seed + seed_number
                matrix.append((f'{slug_0}_vs_{slug_1}_seed{seed}', slug_0, slug_1, seed))
            pair_index += 1

    # Synthetic coverage games: chain and mirror pairings with several seeds so
    # every catalog card is in play repeatedly under different setups.
    synthetic = build_synthetic_deck_definitions()
    for index, (slug, _) in enumerate(synthetic):
        partner_slug = synthetic[(index + 1) % len(synthetic)][0]
        base_seed = 90_000 + index * 211
        for seed_number in range(12):
            seed = base_seed + seed_number
            matrix.append((f'{slug}_vs_{slug}_seed{seed}', slug, slug, seed))
            matrix.append((f'{slug}_vs_{partner_slug}_seed{seed}', slug, partner_slug, seed))
    return matrix


def play_recorded_game(deck_entries_0, deck_entries_1, seed: int, card_index) -> TranscriptRecorder:
    global _ACTIVE_RECORDER
    recorder = TranscriptRecorder()
    _ACTIVE_RECORDER = recorder
    try:
        random.seed(seed)
        deck_cards_0 = [card_index[(entry['pack'], entry['id'])] for entry in deck_entries_0]
        deck_cards_1 = [card_index[(entry['pack'], entry['id'])] for entry in deck_entries_1]
        agent_0 = ScriptedVarietyAgent(seed_offset=seed * 2 + 1)
        agent_1 = ScriptedVarietyAgent(seed_offset=seed * 3 + 2)
        environment = RecordingEnvironment(
            agent_0=agent_0,
            agent_1=agent_1,
            deck_cards_for_player_0=deck_cards_0,
            deck_cards_for_player_1=deck_cards_1,
        )
        try:
            result = asyncio.run(environment.play_full_game())
            recorder.record_game_result(
                result.winner, result.turns,
                result.player_0_final_hp, result.player_1_final_hp,
            )
        except Exception as error:
            traceback.print_exc()
            recorder.record({
                'event': 'game_error',
                'error_type': type(error).__name__,
                'error_message': str(error),
            })
    finally:
        _ACTIVE_RECORDER = None
    return recorder


def build_coverage_report() -> str:
    registered = sorted(effect_engine_module._EFFECT_HANDLERS.keys())
    dispatched = sorted(_DISPATCHED_EFFECT_IDS)
    not_dispatched = sorted(set(registered) - _DISPATCHED_EFFECT_IDS)
    report = {
        'registered_count': len(registered),
        'dispatched_count': len(dispatched),
        'dispatched': dispatched,
        'registered_not_dispatched': not_dispatched,
    }
    return json.dumps(report, sort_keys=True, indent=2) + '\n'


def run(mode: str, baseline_directory: Path, limit: int | None, seeds_per_pair: int) -> int:
    _install_recording_classes()

    all_cards = load_cards()
    card_index = build_card_index(all_cards)
    deck_entries_by_slug = dict(load_deck_definitions() + build_synthetic_deck_definitions())
    matrix = build_game_matrix(seeds_per_pair)
    if limit is not None:
        matrix = matrix[:limit]

    print(f'{mode}: {len(matrix)} games against {baseline_directory}')
    start_time = time.monotonic()

    mismatched: list[str] = []
    missing: list[str] = []
    generated_names: set[str] = set()

    if mode == 'write':
        baseline_directory.mkdir(parents=True, exist_ok=True)
        for stale in baseline_directory.glob('*.jsonl'):
            stale.unlink()
        for stale in baseline_directory.glob('*.jsonl.gz'):
            stale.unlink()

    for game_number, (file_stem, slug_0, slug_1, seed) in enumerate(matrix, start=1):
        recorder = play_recorded_game(
            deck_entries_by_slug[slug_0], deck_entries_by_slug[slug_1], seed, card_index,
        )
        transcript_text = recorder.to_jsonl()
        file_name = f'{file_stem}.jsonl.gz'
        generated_names.add(file_name)
        baseline_path = baseline_directory / file_name

        if mode == 'write':
            # mtime=0 keeps the gzip header byte-stable across runs.
            baseline_path.write_bytes(
                gzip.compress(transcript_text.encode('utf-8'), mtime=0)
            )
        else:
            if not baseline_path.exists():
                missing.append(file_name)
            elif gzip.decompress(baseline_path.read_bytes()).decode('utf-8') != transcript_text:
                mismatched.append(file_name)

        if game_number % 50 == 0 or game_number == len(matrix):
            elapsed = time.monotonic() - start_time
            print(f'  {game_number}/{len(matrix)} games, {elapsed:.1f}s elapsed')

    coverage_text = build_coverage_report()
    coverage_path = baseline_directory / 'coverage.json'
    if mode == 'write':
        with open(coverage_path, 'w', encoding='utf-8', newline='\n') as coverage_file:
            coverage_file.write(coverage_text)
        coverage = json.loads(coverage_text)
        print(f'Baselines written: {len(matrix)} transcripts.')
        print(f'Effect coverage: {coverage["dispatched_count"]}/{coverage["registered_count"]} registered effects dispatched.')
        return 0

    # Compare mode: also flag baseline transcripts the current matrix no longer produces.
    if limit is None:
        stale = sorted(
            path.name for path in baseline_directory.glob('*.jsonl.gz')
            if path.name not in generated_names
        )
    else:
        stale = []
    coverage_matches = True
    if limit is None and coverage_path.exists():
        coverage_matches = coverage_path.read_text(encoding='utf-8') == coverage_text

    failed = bool(mismatched or missing or stale) or not coverage_matches
    if mismatched:
        print(f'MISMATCHED ({len(mismatched)}):')
        for name in mismatched[:20]:
            print(f'  {name}')
        if len(mismatched) > 20:
            print(f'  ... and {len(mismatched) - 20} more')
    if missing:
        print(f'MISSING BASELINES ({len(missing)}): {missing[:10]}')
    if stale:
        print(f'STALE BASELINES ({len(stale)}): {stale[:10]}')
    if not coverage_matches:
        print('COVERAGE REPORT differs from baseline coverage.json')
    print('RESULT: ' + ('FAIL' if failed else 'PASS'))
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description='Tier A engine regression harness.')
    parser.add_argument('mode', choices=('write', 'compare'))
    parser.add_argument('--baseline-directory', type=Path, default=DEFAULT_BASELINE_DIRECTORY)
    parser.add_argument('--limit', type=int, default=None, help='Only run the first N games of the matrix.')
    parser.add_argument('--smoke', action='store_true', help='Shortcut for --limit 5.')
    parser.add_argument('--seeds-per-pair', type=int, default=1)
    arguments = parser.parse_args()
    limit = 5 if arguments.smoke else arguments.limit
    return run(arguments.mode, arguments.baseline_directory, limit, arguments.seeds_per_pair)


if __name__ == '__main__':
    raise SystemExit(main())
