"""
Bot agent for メカうにぐり — makes gameplay decisions.

In random mode (default), all decisions are made randomly from valid options.
When a trained PyTorch model is loaded, decisions use the neural network policy.
"""

from __future__ import annotations
import json
import logging
import random
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from zutomayo.models.card import Card
    from zutomayo.models.card_instance import CardInstance


log = logging.getLogger(__name__)

# Decision type constants — same values used in uniguri_env_v2.
# Defined here so choose_* methods don't need to import from there.
_DECISION_CARD_SELECTION = 0
_DECISION_REDRAW = 1
_DECISION_EFFECT_CARD = 2
_DECISION_EFFECT_NUMBER = 3
_DECISION_EFFECT_ORDER = 4
_MAX_CANDIDATES = 10
_MAX_ACTION_SIZE = 20
_MODEL_INFERENCE_MAX_ATTEMPTS = 3

BOT_DECKS_FILE = Path(__file__).resolve().parent.parent / 'bot_decks.json'
BEST_DECKS_V2_FILE = Path(__file__).resolve().parent.parent / 'best_decks_v2.json'
BEST_DECKS_V2_EASY_FILE = Path(__file__).resolve().parent.parent / 'best_decks_v2_easy.json'
MODELS_DIR_V2_EASY = Path(__file__).resolve().parent.parent / 'models_trained_v2_easy'

BOT_NAME = 'メカうにぐり'


class BotAgent:
    """
    Decision-making agent for the UNIGURI bot.

    Currently operates in random mode. When a trained model is loaded
    via `load_model()`, decisions will use the neural network policy.
    """

    def __init__(self) -> None:
        self.model = None  # Reserved for future PyTorch model

    def choose_redraw(self, hand: list[CardInstance]) -> list[CardInstance]:
        """
        Choose which cards to redraw from the initial hand.

        Randomly decides whether to redraw, and if so, picks a random
        subset of 1-3 cards to discard and redraw.
        """
        if not hand:
            return []

        # 50% chance to keep the hand as-is
        if random.random() < 0.5:
            return []

        # Redraw 1 to min(3, hand_size) cards
        max_redraw = min(3, len(hand))
        count = random.randint(1, max_redraw)
        return random.sample(hand, count)

    def choose_initial_battle_card(self, hand: list[CardInstance]) -> CardInstance:
        """Choose one card from hand to place in the Battle Zone."""
        return random.choice(hand)

    def choose_cards_to_set(
        self, hand: list[CardInstance], max_cards: int,
    ) -> list[CardInstance]:
        """Choose 1 to max_cards cards from hand to set face-down."""
        if not hand:
            return []
        count = min(max_cards, len(hand))
        if count <= 0:
            return []
        # Always set the maximum allowed number of cards
        return random.sample(hand, count)

    def choose_effect_order(
        self, eligible: list[CardInstance],
    ) -> list[CardInstance]:
        """Choose the order to resolve multiple effects."""
        shuffled = list(eligible)
        random.shuffle(shuffled)
        return shuffled

    def choose_effect_card(
        self, cards: list[CardInstance],
    ) -> Optional[CardInstance]:
        """Choose a card for an interactive effect prompt."""
        if not cards:
            return None
        return random.choice(cards)

    def choose_effect_number(self, min_value: int, max_value: int) -> int:
        """Choose a number for an interactive effect prompt."""
        return random.randint(min_value, max_value)

    def choose_effect_text(self) -> Optional[str]:
        """Choose text for an interactive effect prompt.

        Returns None to indicate no text input (timeout behavior).
        """
        return None


class ModelBotAgent(BotAgent):
    """Decision-making agent that uses a trained RL model."""

    def __init__(self, model, device, player_index: int) -> None:
        super().__init__()
        self.model = model
        self.device = device
        self.player_index = player_index
        self.trajectory_buffer: list = []
        self.current_game_state = None
        self.current_decision_context: Optional[list[float]] = None

        self.supports_extended_decisions = False

    def clear_trajectory(self) -> None:
        """Clear the trajectory buffer for a new game."""
        self.trajectory_buffer.clear()

    @property
    def _is_night(self) -> bool:
        """Whether the current game state is night."""
        if self.current_game_state is None:
            return False
        from zutomayo.enums.chronos import Chronos
        return self.current_game_state.day_night == Chronos.NIGHT

    def _build_observation_tensor(self):
        raise NotImplementedError

    def _build_valid_action_mask(self, valid_count: int):
        raise NotImplementedError

    def _make_decision_context(
        self,
        decision_type: int,
        candidates: Optional[list] = None,
        number_min: Optional[int] = None,
        number_max: Optional[int] = None,
    ) -> list[float]:
        raise NotImplementedError

    def _record_trajectory_step(
        self,
        observation: list[float],
        action_index: int,
        action_log_probability: float,
        value_estimate: float,
        valid_mask: list[bool],
    ) -> None:
        raise NotImplementedError

    def _call_model_with_retry(
        self,
        observation_tensor,
        mask_tensor,
        valid_count: int,
    ) -> tuple[int, float, float, float]:
        """Call model.select_action() with retries, falling back to a random valid action on total failure."""
        for attempt in range(_MODEL_INFERENCE_MAX_ATTEMPTS):
            try:
                return self.model.select_action(observation_tensor, mask_tensor)
            except Exception as error:
                log.warning(
                    'Model inference failed (attempt %d/%d): %s',
                    attempt + 1, _MODEL_INFERENCE_MAX_ATTEMPTS, error,
                )
        log.warning(
            'All %d model inference attempts failed; using random fallback action',
            _MODEL_INFERENCE_MAX_ATTEMPTS,
        )
        return random.randrange(valid_count), 0.0, 0.0, 0.0

    def choose_redraw(self, hand: list['CardInstance']) -> list['CardInstance']:
        """
        Choose which cards to redraw using the neural network policy.

        Uses sequential "pick or stop" decisions (up to 3 rounds).
        The stop action is represented as index == len(remaining_hand),
        which maps to the pointer network's stop token.
        """
        if not self.supports_extended_decisions or self.current_game_state is None or not hand:
            return super().choose_redraw(hand)

        selected_cards: list['CardInstance'] = []
        remaining_hand = list(hand)
        max_redraws = min(3, len(remaining_hand))

        for _ in range(max_redraws):
            if not remaining_hand:
                break

            self.current_decision_context = self._make_decision_context(
                _DECISION_REDRAW, candidates=remaining_hand,
            )
            observation, observation_tensor = self._build_observation_tensor()

            # Valid actions: indices 0..len(remaining_hand)-1 for cards,
            # plus index len(remaining_hand) for "stop redrawing"
            stop_index = len(remaining_hand)
            valid_mask, mask_tensor = self._build_valid_action_mask(stop_index + 1)

            action_index, action_log_probability, value_estimate, _ = (
                self._call_model_with_retry(observation_tensor, mask_tensor, stop_index + 1)
            )

            action_index = min(action_index, stop_index)

            self._record_trajectory_step(
                observation, action_index, action_log_probability,
                value_estimate, valid_mask,
            )

            if action_index == stop_index:
                break

            selected_cards.append(remaining_hand[action_index])
            remaining_hand.pop(action_index)

        self.current_decision_context = None
        return selected_cards

    def choose_initial_battle_card(self, hand: list['CardInstance']) -> 'CardInstance':
        """Choose a card from hand using the neural network policy."""
        if self.current_game_state is None or not hand:
            return super().choose_initial_battle_card(hand)

        if self.supports_extended_decisions:
            self.current_decision_context = self._make_decision_context(
                _DECISION_CARD_SELECTION, candidates=hand,
            )

        observation, observation_tensor = self._build_observation_tensor()
        valid_mask, mask_tensor = self._build_valid_action_mask(len(hand))

        action_index, action_log_probability, value_estimate, _ = (
            self._call_model_with_retry(observation_tensor, mask_tensor, len(hand))
        )

        action_index = min(action_index, len(hand) - 1)

        self._record_trajectory_step(
            observation, action_index, action_log_probability,
            value_estimate, valid_mask,
        )

        self.current_decision_context = None
        return hand[action_index]

    def choose_cards_to_set(
        self, hand: list['CardInstance'], max_cards: int,
    ) -> list['CardInstance']:
        """
        Choose cards to set using the neural network policy.

        Makes sequential selections, each recorded as a separate
        trajectory step.
        """
        if self.current_game_state is None or not hand:
            return super().choose_cards_to_set(hand, max_cards)

        selected_cards: list['CardInstance'] = []
        remaining_hand = list(hand)

        for _ in range(min(max_cards, len(remaining_hand))):
            if not remaining_hand:
                break

            if self.supports_extended_decisions:
                self.current_decision_context = self._make_decision_context(
                    _DECISION_CARD_SELECTION, candidates=remaining_hand,
                )

            observation, observation_tensor = self._build_observation_tensor()
            valid_mask, mask_tensor = self._build_valid_action_mask(
                len(remaining_hand),
            )

            action_index, action_log_probability, value_estimate, _ = (
                self._call_model_with_retry(observation_tensor, mask_tensor, len(remaining_hand))
            )

            action_index = min(action_index, len(remaining_hand) - 1)

            self._record_trajectory_step(
                observation, action_index, action_log_probability,
                value_estimate, valid_mask,
            )

            selected_cards.append(remaining_hand[action_index])
            remaining_hand.pop(action_index)

        self.current_decision_context = None
        return selected_cards

    def choose_effect_order(
        self, eligible: list['CardInstance'],
    ) -> list['CardInstance']:
        """
        Choose effect resolution order using the neural network policy.

        Sequential selection from a shrinking candidate list.
        """
        if not self.supports_extended_decisions or self.current_game_state is None:
            return super().choose_effect_order(eligible)

        ordered: list['CardInstance'] = []
        remaining = list(eligible)

        while len(remaining) > 1:
            self.current_decision_context = self._make_decision_context(
                _DECISION_EFFECT_ORDER, candidates=remaining,
            )
            observation, observation_tensor = self._build_observation_tensor()
            valid_mask, mask_tensor = self._build_valid_action_mask(len(remaining))

            action_index, action_log_probability, value_estimate, _ = (
                self._call_model_with_retry(observation_tensor, mask_tensor, len(remaining))
            )

            action_index = min(action_index, len(remaining) - 1)

            self._record_trajectory_step(
                observation, action_index, action_log_probability,
                value_estimate, valid_mask,
            )

            ordered.append(remaining[action_index])
            remaining.pop(action_index)

        if remaining:
            ordered.append(remaining[0])

        self.current_decision_context = None
        return ordered

    def choose_effect_card(
        self, cards: list['CardInstance'],
    ) -> Optional['CardInstance']:
        """Choose a card for an effect prompt using the neural network policy."""
        if not cards:
            return None
        if not self.supports_extended_decisions or self.current_game_state is None:
            return super().choose_effect_card(cards)

        selectable_cards = cards[:_MAX_CANDIDATES]

        self.current_decision_context = self._make_decision_context(
            _DECISION_EFFECT_CARD, candidates=selectable_cards,
        )
        observation, observation_tensor = self._build_observation_tensor()
        valid_mask, mask_tensor = self._build_valid_action_mask(len(selectable_cards))

        action_index, action_log_probability, value_estimate, _ = (
            self._call_model_with_retry(observation_tensor, mask_tensor, len(selectable_cards))
        )

        action_index = min(action_index, len(selectable_cards) - 1)

        self._record_trajectory_step(
            observation, action_index, action_log_probability,
            value_estimate, valid_mask,
        )

        self.current_decision_context = None
        return selectable_cards[action_index]

    def choose_effect_number(self, min_value: int, max_value: int) -> int:
        """Choose a number for an effect prompt using the neural network policy."""
        if not self.supports_extended_decisions or self.current_game_state is None:
            return super().choose_effect_number(min_value, max_value)

        range_size = max_value - min_value + 1
        clamped_range_size = min(range_size, _MAX_ACTION_SIZE)

        self.current_decision_context = self._make_decision_context(
            _DECISION_EFFECT_NUMBER,
            number_min=min_value,
            number_max=max_value,
        )
        observation, observation_tensor = self._build_observation_tensor()
        valid_mask, mask_tensor = self._build_valid_action_mask(clamped_range_size)

        action_index, action_log_probability, value_estimate, _ = (
            self._call_model_with_retry(observation_tensor, mask_tensor, clamped_range_size)
        )

        action_index = min(action_index, clamped_range_size - 1)

        self._record_trajectory_step(
            observation, action_index, action_log_probability,
            value_estimate, valid_mask,
        )

        self.current_decision_context = None
        return min_value + action_index


class ModelBotAgentV2(ModelBotAgent):
    """V2 model agent — uses v2 observation and decision context.

    Inherits all choose_* logic from ModelBotAgent. Only the observation
    builder, decision context builder, and trajectory recording differ.
    """

    def __init__(self, model, device, player_index: int) -> None:
        # Bypass ModelBotAgent.__init__ which checks v1 OBSERVATION_SIZE
        BotAgent.__init__(self)
        self.model = model
        self.device = device
        self.player_index = player_index
        self.trajectory_buffer: list = []
        self.current_game_state = None
        self.current_decision_context: Optional[list[float]] = None
        self.supports_extended_decisions = True  # always True for v2

    def _make_decision_context(
        self,
        decision_type: int,
        candidates: Optional[list] = None,
        number_min: Optional[int] = None,
        number_max: Optional[int] = None,
    ) -> list[float]:
        from zutomayo.engine.uniguri_env_v2 import build_decision_context_v2

        return build_decision_context_v2(
            decision_type=decision_type,
            candidates=candidates,
            number_min=number_min,
            number_max=number_max,
            is_night=self._is_night,
        )

    def _build_observation_tensor(self):
        import torch
        from zutomayo.engine.uniguri_env_v2 import build_observation_v2

        observation = build_observation_v2(
            self.current_game_state,
            self.player_index,
            decision_context=self.current_decision_context,
        )
        observation_tensor = torch.tensor(
            observation, dtype=torch.float32, device=self.device,
        ).unsqueeze(0)
        return observation, observation_tensor

    def _build_valid_action_mask(self, valid_count: int):
        import torch
        from zutomayo.engine.uniguri_env_v2 import MAX_ACTION_SIZE

        mask_values = [index < valid_count for index in range(MAX_ACTION_SIZE)]
        mask_tensor = torch.tensor(
            mask_values, dtype=torch.bool, device=self.device,
        ).unsqueeze(0)
        return mask_values, mask_tensor

    def _record_trajectory_step(
        self,
        observation: list[float],
        action_index: int,
        action_log_probability: float,
        value_estimate: float,
        valid_mask: list[bool],
    ) -> None:
        from zutomayo.engine.rl_model_v2 import TrajectoryStepV2

        step = TrajectoryStepV2(
            observation=observation,
            action_index=action_index,
            action_log_probability=action_log_probability,
            value_estimate=value_estimate,
            valid_action_mask=valid_mask,
        )
        self.trajectory_buffer.append(step)


BOT_PLAYER_INDEX = 1


def create_bot_agent() -> BotAgent:
    """
    Create the best available bot agent for live gameplay.

    Loads the latest checkpoint from models_trained_v2/. Falls back to a
    random BotAgent if PyTorch is unavailable or no checkpoint exists.
    """
    try:
        import torch
    except ImportError:
        log.info('PyTorch not installed — UNIGURI will use random decisions.')
        return BotAgent()

    try:
        from zutomayo.engine.rl_model_v2 import (
            MODELS_DIR_V2,
            create_policy_network_v2,
            load_checkpoint_v2,
        )

        checkpoints_v2 = sorted(MODELS_DIR_V2.glob('checkpoint_*.pt'))
        if not checkpoints_v2:
            log.info(
                'No trained checkpoints found in %s — UNIGURI will use random decisions.',
                MODELS_DIR_V2,
            )
            return BotAgent()

        device = torch.device('cpu')
        latest_v2 = str(checkpoints_v2[-1])
        checkpoint_data = torch.load(latest_v2, weights_only=False, map_location=device)
        observation_size = checkpoint_data['observation_size']
        action_size = checkpoint_data['action_size']

        model = create_policy_network_v2(observation_size, action_size, device=device)
        load_checkpoint_v2(model, checkpoint_path=latest_v2, device=device)
        model.eval()

        agent = ModelBotAgentV2(model, device, player_index=BOT_PLAYER_INDEX)
        log.info(
            'UNIGURI loaded v2 model from %s (episode %d)',
            latest_v2,
            checkpoint_data.get('episode', 0),
        )
        return agent

    except Exception as error:
        log.warning(
            'Failed to load trained model: %s — falling back to random decisions.',
            error,
        )
        return BotAgent()


def create_bot_agent_easy() -> BotAgent:
    """
    Load the easy-difficulty bot agent from models_trained_v2_easy/checkpoint_easy.pt.
    Falls back to random BotAgent if the checkpoint is missing or loading fails.
    """
    try:
        import torch
    except ImportError:
        log.info('PyTorch not installed — UNIGURI easy will use random decisions.')
        return BotAgent()

    try:
        from zutomayo.engine.rl_model_v2 import create_policy_network_v2, load_checkpoint_v2

        checkpoint_path = MODELS_DIR_V2_EASY / 'checkpoint_easy.pt'
        if not checkpoint_path.exists():
            log.info(
                'checkpoint_easy.pt not found in %s — UNIGURI easy will use random decisions.',
                MODELS_DIR_V2_EASY,
            )
            return BotAgent()

        device = torch.device('cpu')
        checkpoint_path_str = str(checkpoint_path)
        checkpoint_data = torch.load(checkpoint_path_str, weights_only=False, map_location=device)
        observation_size = checkpoint_data['observation_size']
        action_size = checkpoint_data['action_size']

        model = create_policy_network_v2(observation_size, action_size, device=device)
        load_checkpoint_v2(model, checkpoint_path=checkpoint_path_str, device=device)
        model.eval()

        agent = ModelBotAgentV2(model, device, player_index=BOT_PLAYER_INDEX)
        log.info(
            'UNIGURI easy loaded from %s (episode %d)',
            checkpoint_path_str,
            checkpoint_data.get('episode', 0),
        )
        return agent

    except Exception as error:
        log.warning('Failed to load easy model: %s — falling back to random decisions.', error)
        return BotAgent()


def collect_and_save_bot_decks() -> int:
    """
    Scan every user's saved decks and save unique ones to bot_decks.json.

    Reads all decks from the database, deduplicates by card composition
    (ignoring deck name), assigns each unique deck a random GUID, and writes
    the result to bot_decks.json. Decks containing any CHAOS attribute card
    are excluded.

    Maintenance utility for the training pipeline; runs its own event loop
    and database pool, so it keeps a synchronous signature for callers like
    train_uniguri_v2.py. Returns the number of unique decks saved.
    """
    import asyncio

    from zutomayo.data.card_loader import load_cards
    from zutomayo.enums.attribute import Attribute

    async def fetch_all_decks() -> list[dict]:
        from zutomayo.data import database, deck_repository

        await database.initialize_pool()
        try:
            return await deck_repository.STANDARD_DECK_REPOSITORY.list_all_decks()
        finally:
            await database.close_pool()

    card_list = load_cards()
    card_index = {(card.pack, card.id): card for card in card_list}

    all_decks = asyncio.run(fetch_all_decks())
    if not all_decks:
        raise ValueError('No saved decks found in the database.')

    # Deduplicate by card composition (sorted tuples of pack/id), excluding CHAOS decks
    seen_signatures: set[tuple[tuple[int, int], ...]] = set()
    unique_decks: list[dict] = []
    excluded_count = 0
    for deck in all_decks:
        cards = deck.get('cards', [])
        if any(
            (card['pack'], card['id']) in card_index
            and card_index[(card['pack'], card['id'])].attribute == Attribute.CHAOS
            for card in cards
        ):
            excluded_count += 1
            continue
        signature = tuple(sorted((card['pack'], card['id']) for card in cards))
        if signature not in seen_signatures:
            seen_signatures.add(signature)
            unique_decks.append({
                'guid': str(uuid.uuid4()),
                'cards': cards,
            })

    output = {'decks': unique_decks}
    with open(BOT_DECKS_FILE, 'w', encoding='utf-8') as file_handle:
        json.dump(output, file_handle, indent=2, ensure_ascii=False)

    log.info(
        'Saved %d unique bot decks to %s (from %d total decks, %d excluded for CHAOS cards).',
        len(unique_decks),
        BOT_DECKS_FILE,
        len(all_decks),
        excluded_count,
    )
    return len(unique_decks)


def load_random_saved_deck(
    card_index: dict[tuple[int, int], 'Card'],
) -> list['Card']:
    """
    Load a random deck from bot_decks.json.

    Picks a random deck from the pre-built bot decks file and resolves
    the card references to Card objects.
    """
    from zutomayo.data.deck_storage import resolve_deck_cards

    if not BOT_DECKS_FILE.exists():
        raise ValueError(
            'bot_decks.json not found. Run training first to generate it.'
        )

    with open(BOT_DECKS_FILE, 'r', encoding='utf-8') as file_handle:
        data = json.load(file_handle)

    all_decks = data.get('decks', [])
    if not all_decks:
        raise ValueError('No decks found in bot_decks.json.')

    chosen_deck = random.choice(all_decks)
    log.info(
        'UNIGURI selected bot deck: %s',
        chosen_deck.get('guid', 'unknown'),
    )

    return resolve_deck_cards(chosen_deck, card_index)


def load_random_best_deck_v2(
    card_index: dict[tuple[int, int], 'Card'],
) -> list['Card']:
    """
    Load a random deck from best_decks_v2.json, falling back to bot_decks.json.

    Tries best_decks_v2.json first (produced by best_decks_v2.py evaluation with the v2 model).
    If it doesn't exist or is empty, falls back to load_random_saved_deck().
    """
    from zutomayo.data.deck_storage import resolve_deck_cards

    if BEST_DECKS_V2_FILE.exists():
        with open(BEST_DECKS_V2_FILE, 'r', encoding='utf-8') as file_handle:
            data = json.load(file_handle)
        all_decks = data.get('decks', [])
        if all_decks:
            chosen_deck = random.choice(all_decks)
            log.info(
                'UNIGURI selected best v2 deck: %s',
                chosen_deck.get('guid', 'unknown'),
            )
            return resolve_deck_cards(chosen_deck, card_index)

    log.info('best_decks_v2.json not available, falling back to bot_decks.json')
    return load_random_saved_deck(card_index)


def load_random_best_deck_v2_easy(
    card_index: dict[tuple[int, int], 'Card'],
) -> list['Card']:
    """
    Load a random deck from best_decks_v2_easy.json, falling back to best_decks_v2.json.

    Tries best_decks_v2_easy.json first (produced by best_decks_uniguri_v2_easy.py).
    If it doesn't exist or is empty, falls back to load_random_best_deck_v2().
    """
    from zutomayo.data.deck_storage import resolve_deck_cards

    if BEST_DECKS_V2_EASY_FILE.exists():
        with open(BEST_DECKS_V2_EASY_FILE, 'r', encoding='utf-8') as file_handle:
            data = json.load(file_handle)
        all_decks = data.get('decks', [])
        if all_decks:
            chosen_deck = random.choice(all_decks)
            log.info(
                'UNIGURI easy selected best v2 easy deck: %s',
                chosen_deck.get('guid', 'unknown'),
            )
            return resolve_deck_cards(chosen_deck, card_index)

    log.info('best_decks_v2_easy.json not available, falling back to best_decks_v2.json')
    return load_random_best_deck_v2(card_index)
