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

DECKS_DIR = Path(__file__).resolve().parent.parent / 'decks'
BOT_DECKS_FILE = Path(__file__).resolve().parent.parent / 'bot_decks.json'
BEST_DECKS_FILE = Path(__file__).resolve().parent.parent / 'best_decks.json'

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

        # Check if the model supports the extended observation/action space
        from zutomayo.engine.headless_game_env import OBSERVATION_SIZE, MAX_ACTION_SIZE
        self.supports_extended_decisions = (
            model.observation_size >= OBSERVATION_SIZE
            and model.action_size >= MAX_ACTION_SIZE
        )

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
        """Build an observation tensor from the current game state."""
        import torch
        from zutomayo.engine.headless_game_env import game_state_to_observation

        observation = game_state_to_observation(
            self.current_game_state,
            self.player_index,
            decision_context=self.current_decision_context,
        )
        observation_tensor = torch.tensor(
            observation, dtype=torch.float32, device=self.device,
        ).unsqueeze(0)
        return observation, observation_tensor

    def _build_valid_action_mask(self, valid_count: int):
        """
        Build a boolean mask for valid action indices.

        Args:
            valid_count: Number of valid actions (first N indices are valid).

        Returns:
            Tuple of (mask_values, mask_tensor).
        """
        import torch
        from zutomayo.engine.headless_game_env import MAX_ACTION_SIZE

        mask_size = MAX_ACTION_SIZE if self.supports_extended_decisions else valid_count
        mask_values = [
            index < valid_count for index in range(mask_size)
        ]
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
        """Record a trajectory step for PPO training."""
        from zutomayo.engine.rl_model import TrajectoryStep

        step = TrajectoryStep(
            observation=observation,
            action_index=action_index,
            action_log_probability=action_log_probability,
            value_estimate=value_estimate,
            valid_action_mask=valid_mask,
        )
        self.trajectory_buffer.append(step)

    def choose_redraw(self, hand: list['CardInstance']) -> list['CardInstance']:
        """
        Choose which cards to redraw using the neural network policy.

        Uses sequential "pick or stop" decisions (up to 3 rounds).
        The stop action is represented as index == len(remaining_hand),
        which maps to the pointer network's stop token.
        """
        if not self.supports_extended_decisions or self.current_game_state is None or not hand:
            return super().choose_redraw(hand)

        from zutomayo.engine.headless_game_env import (
            DECISION_REDRAW,
            build_decision_context,
        )

        selected_cards: list['CardInstance'] = []
        remaining_hand = list(hand)
        max_redraws = min(3, len(remaining_hand))

        for _ in range(max_redraws):
            if not remaining_hand:
                break

            self.current_decision_context = build_decision_context(
                decision_type=DECISION_REDRAW,
                candidates=remaining_hand,
                is_night=self._is_night,
            )
            observation, observation_tensor = self._build_observation_tensor()

            # Valid actions: indices 0..len(remaining_hand)-1 for cards,
            # plus index len(remaining_hand) for "stop redrawing"
            stop_index = len(remaining_hand)
            valid_mask, mask_tensor = self._build_valid_action_mask(stop_index + 1)

            action_index, action_log_probability, value_estimate, _ = (
                self.model.select_action(observation_tensor, mask_tensor)
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
            from zutomayo.engine.headless_game_env import (
                DECISION_CARD_SELECTION,
                build_decision_context,
            )
            self.current_decision_context = build_decision_context(
                decision_type=DECISION_CARD_SELECTION,
                candidates=hand,
                is_night=self._is_night,
            )

        observation, observation_tensor = self._build_observation_tensor()
        valid_mask, mask_tensor = self._build_valid_action_mask(len(hand))

        action_index, action_log_probability, value_estimate, _ = (
            self.model.select_action(observation_tensor, mask_tensor)
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
                from zutomayo.engine.headless_game_env import (
                    DECISION_CARD_SELECTION,
                    build_decision_context,
                )
                self.current_decision_context = build_decision_context(
                    decision_type=DECISION_CARD_SELECTION,
                    candidates=remaining_hand,
                    is_night=self._is_night,
                )

            observation, observation_tensor = self._build_observation_tensor()
            valid_mask, mask_tensor = self._build_valid_action_mask(
                len(remaining_hand),
            )

            action_index, action_log_probability, value_estimate, _ = (
                self.model.select_action(observation_tensor, mask_tensor)
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

        from zutomayo.engine.headless_game_env import (
            DECISION_EFFECT_ORDER,
            build_decision_context,
        )

        ordered: list['CardInstance'] = []
        remaining = list(eligible)

        while len(remaining) > 1:
            self.current_decision_context = build_decision_context(
                decision_type=DECISION_EFFECT_ORDER,
                candidates=remaining,
                is_night=self._is_night,
            )
            observation, observation_tensor = self._build_observation_tensor()
            valid_mask, mask_tensor = self._build_valid_action_mask(len(remaining))

            action_index, action_log_probability, value_estimate, _ = (
                self.model.select_action(observation_tensor, mask_tensor)
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

        from zutomayo.engine.headless_game_env import (
            DECISION_EFFECT_CARD,
            MAX_CANDIDATES,
            build_decision_context,
        )

        # Truncate to MAX_CANDIDATES if needed
        selectable_cards = cards[:MAX_CANDIDATES]

        self.current_decision_context = build_decision_context(
            decision_type=DECISION_EFFECT_CARD,
            candidates=selectable_cards,
            is_night=self._is_night,
        )
        observation, observation_tensor = self._build_observation_tensor()
        valid_mask, mask_tensor = self._build_valid_action_mask(len(selectable_cards))

        action_index, action_log_probability, value_estimate, _ = (
            self.model.select_action(observation_tensor, mask_tensor)
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

        from zutomayo.engine.headless_game_env import (
            DECISION_EFFECT_NUMBER,
            MAX_ACTION_SIZE,
            build_decision_context,
        )

        range_size = max_value - min_value + 1
        clamped_range_size = min(range_size, MAX_ACTION_SIZE)

        self.current_decision_context = build_decision_context(
            decision_type=DECISION_EFFECT_NUMBER,
            number_min=min_value,
            number_max=max_value,
            is_night=self._is_night,
        )
        observation, observation_tensor = self._build_observation_tensor()
        valid_mask, mask_tensor = self._build_valid_action_mask(clamped_range_size)

        action_index, action_log_probability, value_estimate, _ = (
            self.model.select_action(observation_tensor, mask_tensor)
        )

        action_index = min(action_index, clamped_range_size - 1)

        self._record_trajectory_step(
            observation, action_index, action_log_probability,
            value_estimate, valid_mask,
        )

        self.current_decision_context = None
        return min_value + action_index


BOT_PLAYER_INDEX = 1


def create_bot_agent() -> BotAgent:
    """
    Create the best available bot agent for live gameplay.

    Attempts to load the latest trained checkpoint from models_trained/.
    If a checkpoint is found and PyTorch is available, returns a
    ModelBotAgent using the trained policy on CPU. Otherwise falls
    back to a random BotAgent.
    """
    try:
        from zutomayo.engine.rl_model import (
            MODELS_DIR,
            create_policy_network,
            load_checkpoint,
        )
    except ImportError:
        log.info('PyTorch not installed — UNIGURI will use random decisions.')
        return BotAgent()

    checkpoints = sorted(MODELS_DIR.glob('checkpoint_*.pt'))
    if not checkpoints:
        log.info(
            'No trained checkpoints found in %s — UNIGURI will use random decisions.',
            MODELS_DIR,
        )
        return BotAgent()

    try:
        import torch

        device = torch.device('cpu')
        latest_checkpoint_path = str(checkpoints[-1])

        # Load checkpoint to get model dimensions
        checkpoint_data = torch.load(
            latest_checkpoint_path, weights_only=False, map_location=device,
        )
        observation_size = checkpoint_data['observation_size']
        action_size = checkpoint_data['action_size']

        model = create_policy_network(
            observation_size, action_size, device=device,
        )
        load_checkpoint(model, checkpoint_path=latest_checkpoint_path, device=device)
        model.eval()

        agent = ModelBotAgent(model, device, player_index=BOT_PLAYER_INDEX)
        log.info(
            'UNIGURI loaded trained model from %s (episode %d)',
            latest_checkpoint_path,
            checkpoint_data.get('episode', 0),
        )
        return agent

    except Exception as error:
        log.warning(
            'Failed to load trained model: %s — falling back to random decisions.',
            error,
        )
        return BotAgent()


def collect_and_save_bot_decks() -> int:
    """
    Scan all user deck files and save unique decks to bot_decks.json.

    Reads every deck from zutomayo/decks/*.json, deduplicates by card
    composition (ignoring deck name), assigns each unique deck a random
    GUID, and writes the result to bot_decks.json.

    Returns the number of unique decks saved.
    """
    deck_files = list(DECKS_DIR.glob('*.json'))
    if not deck_files:
        raise ValueError('No saved deck files found in decks folder.')

    all_decks: list[dict] = []
    for deck_file in deck_files:
        try:
            with open(deck_file, 'r', encoding='utf-8') as file_handle:
                data = json.load(file_handle)
            decks = data.get('decks', [])
            all_decks.extend(decks)
        except (json.JSONDecodeError, KeyError):
            log.warning('Skipping invalid deck file: %s', deck_file)
            continue

    if not all_decks:
        raise ValueError('No valid decks found in any deck file.')

    # Deduplicate by card composition (sorted tuples of pack/id)
    seen_signatures: set[tuple[tuple[int, int], ...]] = set()
    unique_decks: list[dict] = []
    for deck in all_decks:
        cards = deck.get('cards', [])
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
        'Saved %d unique bot decks to %s (from %d total decks).',
        len(unique_decks),
        BOT_DECKS_FILE,
        len(all_decks),
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


def load_random_best_deck(
    card_index: dict[tuple[int, int], 'Card'],
) -> list['Card']:
    """
    Load a random deck from best_decks.json, falling back to bot_decks.json.

    Tries best_decks.json first (produced by post-training evaluation).
    If it doesn't exist or is empty, falls back to bot_decks.json.
    """
    from zutomayo.data.deck_storage import resolve_deck_cards

    if BEST_DECKS_FILE.exists():
        with open(BEST_DECKS_FILE, 'r', encoding='utf-8') as file_handle:
            data = json.load(file_handle)
        all_decks = data.get('decks', [])
        if all_decks:
            chosen_deck = random.choice(all_decks)
            log.info(
                'UNIGURI selected best deck: %s',
                chosen_deck.get('guid', 'unknown'),
            )
            return resolve_deck_cards(chosen_deck, card_index)

    log.info('best_decks.json not available, falling back to bot_decks.json')
    return load_random_saved_deck(card_index)
