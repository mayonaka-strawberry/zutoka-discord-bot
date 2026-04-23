"""
V2 headless game environment and observation builder for RL training.

Standalone - no dependency on headless_game_env.py or the deleted
effect_features.py. Key differences from v1:
  - Omniscient observation: bot sees opponent hand and both original decks.
  - GLOBAL_FEATURES_V2 = 79 (adds opponent hand_size_bonus + pending variant).
  - Effect semantic features auto-detected from effect source files at import.
  - Reward: no tempo reward; margin bonus and deck-out penalty on terminal.
  - Smaller HP-delta intermediate scale (0.001 vs 0.01).
"""

from __future__ import annotations
import logging
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from constants import CHRONOS_SIZE
from zutomayo.data.card_loader import load_cards
from zutomayo.data.deck_validator import build_card_index
from zutomayo.effects.effect_engine import EffectEngine, _EFFECT_HANDLERS
from zutomayo.engine.deck_builder import build_deck_from_cards
from zutomayo.engine.game_controller import GameController
from zutomayo.enums.attribute import Attribute
from zutomayo.enums.card_type import CardType
from zutomayo.enums.chronos import Chronos
from zutomayo.enums.phase import Phase
from zutomayo.enums.result import Result
from zutomayo.enums.song import Song
from zutomayo.enums.zone import Zone
from zutomayo.models.card import Card
from zutomayo.models.card_instance import CardInstance
from zutomayo.models.game_state import GameState


log = logging.getLogger(__name__)


# ============================================================
# Effect / attribute / song index maps (same pattern as v1)
# ============================================================

_SORTED_EFFECT_KEYS = sorted(_EFFECT_HANDLERS.keys())
EFFECT_TO_INDEX: dict[str, int] = {
    key: idx + 1 for idx, key in enumerate(_SORTED_EFFECT_KEYS)
}
NUM_EFFECT_TYPES = len(EFFECT_TO_INDEX) + 1  # 0 = no effect / padding

_SORTED_ATTRIBUTES = sorted(Attribute, key=lambda a: a.value)
ATTRIBUTE_TO_INDEX: dict[Attribute, int] = {
    attr: idx + 1 for idx, attr in enumerate(_SORTED_ATTRIBUTES)
}
NUM_ATTRIBUTE_TYPES = len(ATTRIBUTE_TO_INDEX) + 1

_SORTED_SONGS = sorted(Song, key=lambda s: s.value)
SONG_TO_INDEX: dict[Song, int] = {
    song: idx + 1 for idx, song in enumerate(_SORTED_SONGS)
}
NUM_SONG_TYPES = len(SONG_TO_INDEX) + 1

_PHASE_LIST = list(Phase)
NUM_PHASES = len(_PHASE_LIST)


# ============================================================
# Effect semantic features (auto-detected from source files)
# ============================================================

EFFECT_SEMANTIC_FEATURE_SIZE = 10


def _build_effect_semantic_features() -> dict[str, list[float]]:
    """Build binary semantic feature vectors by inspecting effect source files.

    Feature dimensions (10):
      0: boosts attack       (turn_state.attack_bonus used)
      1: heals HP            (player.hp raised via min())
      2: manipulates clock   (game_state.chronos assigned or MIDNIGHT/NOON referenced)
      3: overrides attribute (attribute_override set)
      4: area enchant        (area_enchant referenced)
      5: power charger cond. (power_charger referenced)
      6: abyss conditional   (.abyss referenced)
      7: time conditional    (day_night / Chronos.NIGHT / Chronos.DAY checked)
      8: attribute cond.     (Attribute. enum or effective_attribute referenced)
      9: HP conditional      (.hp compared with < > == >= <=)
    """
    effects_dir = Path(__file__).resolve().parent.parent / 'effects' / 'cards'
    result: dict[str, list[float]] = {}
    for effect_file in sorted(effects_dir.glob('effect_*.py')):
        parts = effect_file.stem.split('_')
        if len(parts) != 3:
            continue
        key = f'{parts[1]}-{parts[2]}'
        try:
            source = effect_file.read_text(encoding='utf-8')
        except OSError:
            continue
        result[key] = [
            float('attack_bonus' in source),
            float('player.hp = min(' in source or '.hp = min(' in source),
            float('game_state.chronos' in source or 'MIDNIGHT' in source or 'NOON' in source),
            float('attribute_override' in source),
            float('area_enchant' in source),
            float('power_charger' in source),
            float('.abyss' in source),
            float('Chronos.NIGHT' in source or 'Chronos.DAY' in source or 'day_night' in source),
            float('Attribute.' in source or 'effective_attribute' in source),
            float('.hp' in source and any(op in source for op in (' < ', ' > ', ' == ', '>=', '<='))),
        ]
    return result


EFFECT_SEMANTIC_FEATURES: dict[str, list[float]] = _build_effect_semantic_features()


# ============================================================
# Card feature size constants
# ============================================================

# 14 numeric + 3 categorical indices + 10 semantic = 27
CARD_FEATURE_SIZE = 17 + EFFECT_SEMANTIC_FEATURE_SIZE

MAX_HAND_SIZE = 10
MAX_DECK_SIZE = 20
ZONE_CARDS = 10  # my_battle, opp_battle, my_a/b/c, opp_a/b/c, prev_battle×2
MAX_ACTION_SIZE = 20
MAX_CANDIDATES = 10

DECISION_CARD_SELECTION = 0
DECISION_REDRAW = 1
DECISION_EFFECT_CARD = 2
DECISION_EFFECT_NUMBER = 3
DECISION_EFFECT_ORDER = 4
NUM_DECISION_TYPES = 5

# Global feature count: 79 (v1 had 77; v2 adds 2 opponent bonus fields)
GLOBAL_FEATURES_V2 = 79

DECISION_CONTEXT_SIZE_V2 = NUM_DECISION_TYPES + (MAX_CANDIDATES * CARD_FEATURE_SIZE) + 3
DECK_OBSERVATION_SIZE_V2 = MAX_DECK_SIZE * CARD_FEATURE_SIZE

BASE_OBSERVATION_SIZE_V2 = (
    GLOBAL_FEATURES_V2
    + (ZONE_CARDS * CARD_FEATURE_SIZE)
    + (MAX_HAND_SIZE * CARD_FEATURE_SIZE)   # player hand
    + (MAX_HAND_SIZE * CARD_FEATURE_SIZE)   # opponent hand (omniscient)
    + 2 * DECK_OBSERVATION_SIZE_V2          # both original decks (omniscient)
)
OBSERVATION_SIZE_V2 = BASE_OBSERVATION_SIZE_V2 + DECISION_CONTEXT_SIZE_V2


# ============================================================
# Feature encoding helpers
# ============================================================

def _encode_attribute_counts(cards: list[CardInstance]) -> list[float]:
    counts = {attr: 0 for attr in _SORTED_ATTRIBUTES}
    for card in cards:
        attr = card.effective_attribute
        if attr in counts:
            counts[attr] += 1
    return [counts[attr] / 20.0 for attr in _SORTED_ATTRIBUTES]


def _encode_card_type_counts(cards: list[CardInstance]) -> list[float]:
    char_count = enchant_count = area_count = 0
    for card in cards:
        t = card.card.card_type
        if t == CardType.CHARACTER:
            char_count += 1
        elif t == CardType.ENCHANT:
            enchant_count += 1
        elif t == CardType.AREA_ENCHANT:
            area_count += 1
    return [char_count / 20.0, enchant_count / 20.0, area_count / 20.0]


def card_instance_to_features_v2(
    card_instance: Optional[CardInstance],
    is_night: bool = False,
) -> list[float]:
    """Convert a CardInstance to CARD_FEATURE_SIZE floats (v2 with semantic features)."""
    if card_instance is None:
        return [0.0] * CARD_FEATURE_SIZE

    card = card_instance.card
    card_type_one_hot = [0.0, 0.0, 0.0]
    if card.card_type == CardType.CHARACTER:
        card_type_one_hot[0] = 1.0
    elif card.card_type == CardType.ENCHANT:
        card_type_one_hot[1] = 1.0
    elif card.card_type == CardType.AREA_ENCHANT:
        card_type_one_hot[2] = 1.0

    effective_attack = card.attack_night if is_night else card.attack_day
    semantic = EFFECT_SEMANTIC_FEATURES.get(card.effect, [0.0] * EFFECT_SEMANTIC_FEATURE_SIZE)

    return [
        card.attack_day / 130.0,
        card.attack_night / 130.0,
        card.clock / 7.0,
        card.power_cost / 7.0,
        card.send_to_power / 10.0,
    ] + card_type_one_hot + [
        1.0 if card_instance.played_this_turn else 0.0,
        1.0 if card_instance.effects_disabled else 0.0,
        card_instance.power_cost_reduction / 7.0,
        1.0 if card_instance.face_up else 0.0,
        effective_attack / 130.0,
        1.0 if card_instance.attribute_override is not None else 0.0,
        float(EFFECT_TO_INDEX.get(card.effect, 0)),
        float(ATTRIBUTE_TO_INDEX.get(card_instance.effective_attribute, 0)),
        float(SONG_TO_INDEX.get(card.song, 0)),
    ] + semantic


def card_to_features_v2(
    card: Optional[Card],
    is_night: bool = False,
) -> list[float]:
    """Convert a bare Card (no instance state) to CARD_FEATURE_SIZE floats."""
    if card is None:
        return [0.0] * CARD_FEATURE_SIZE

    card_type_one_hot = [0.0, 0.0, 0.0]
    if card.card_type == CardType.CHARACTER:
        card_type_one_hot[0] = 1.0
    elif card.card_type == CardType.ENCHANT:
        card_type_one_hot[1] = 1.0
    elif card.card_type == CardType.AREA_ENCHANT:
        card_type_one_hot[2] = 1.0

    effective_attack = card.attack_night if is_night else card.attack_day
    semantic = EFFECT_SEMANTIC_FEATURES.get(card.effect, [0.0] * EFFECT_SEMANTIC_FEATURE_SIZE)

    return [
        card.attack_day / 130.0,
        card.attack_night / 130.0,
        card.clock / 7.0,
        card.power_cost / 7.0,
        card.send_to_power / 10.0,
    ] + card_type_one_hot + [
        0.0, 0.0, 0.0,  # played_this_turn, effects_disabled, power_cost_reduction
        0.0,            # face_up
        effective_attack / 130.0,
        0.0,            # has_attribute_override
        float(EFFECT_TO_INDEX.get(card.effect, 0)),
        float(ATTRIBUTE_TO_INDEX.get(card.attribute, 0)),
        float(SONG_TO_INDEX.get(card.song, 0)),
    ] + semantic


def build_decision_context_v2(
    decision_type: int,
    candidates: Optional[list[CardInstance]] = None,
    number_min: Optional[int] = None,
    number_max: Optional[int] = None,
    is_night: bool = False,
) -> list[float]:
    """Build a v2 decision context vector using CARD_FEATURE_SIZE_V2."""
    decision_one_hot = [0.0] * NUM_DECISION_TYPES
    if 0 <= decision_type < NUM_DECISION_TYPES:
        decision_one_hot[decision_type] = 1.0

    candidate_features: list[float] = []
    if candidates:
        for i in range(MAX_CANDIDATES):
            if i < len(candidates):
                candidate_features.extend(
                    card_instance_to_features_v2(candidates[i], is_night=is_night),
                )
            else:
                candidate_features.extend([0.0] * CARD_FEATURE_SIZE)
    else:
        candidate_features = [0.0] * (MAX_CANDIDATES * CARD_FEATURE_SIZE)

    if number_min is not None and number_max is not None:
        range_size = number_max - number_min + 1
        number_range = [number_min / 20.0, number_max / 20.0, range_size / 20.0]
    else:
        number_range = [0.0, 0.0, 0.0]

    return decision_one_hot + candidate_features + number_range


def build_observation_v2(
    game_state: GameState,
    player_index: int,
    decision_context: Optional[list[float]] = None,
    original_deck_cards: Optional[dict[int, list[Card]]] = None,
) -> list[float]:
    """Build an omniscient v2 observation from player_index's perspective.

    GLOBAL_FEATURES_V2 breakdown (79 total):
      12  original: hp×2, chronos, turn, is_night, side, power×2, deck_size×2, hand_size×2
      19  player additions: power_charger_count, power_charger_attrs(5), abyss_count,
                            abyss_attrs(5), abyss_types(3), area_enchant_blocked,
                            hand_size_bonus, pending_hand_size_bonus, cards_played_this_turn
      19  opponent additions (v2: same 19 as player, v1 only had 17 — missing 2 bonus fields)
      10  game state: phase_one_hot(10) [Note: only 10 phases but NUM_PHASES matches]
       2  last_battle_winner, chronos_at_turn_start
       8  player deck composition: attrs(5), types(3)
       8  opponent deck composition: attrs(5), types(3)
       1  priority flag
      --
      79  total
    """
    player = game_state.players[player_index]
    opponent = game_state.players[1 - player_index]
    is_night_bool = game_state.day_night == Chronos.NIGHT
    is_night_float = 1.0 if is_night_bool else 0.0

    # Original 12 global features
    global_features: list[float] = [
        player.hp / 100.0,
        opponent.hp / 100.0,
        game_state.chronos / float(CHRONOS_SIZE),
        min(game_state.turn, 20) / 20.0,
        is_night_float,
        1.0 if player.side == Chronos.NIGHT else 0.0,
        min(player.total_power, 50) / 50.0,
        len(player.deck) / 20.0,
        len(player.hand) / float(MAX_HAND_SIZE),
        min(opponent.total_power, 50) / 50.0,
        len(opponent.deck) / 20.0,
        len(opponent.hand) / float(MAX_HAND_SIZE),
    ]

    # Player additions (19)
    global_features.append(len(player.power_charger) / 20.0)
    global_features.extend(_encode_attribute_counts(player.power_charger))
    global_features.append(len(player.abyss) / 20.0)
    global_features.extend(_encode_attribute_counts(player.abyss))
    global_features.extend(_encode_card_type_counts(player.abyss))
    global_features.append(1.0 if player.area_enchant_blocked else 0.0)
    global_features.append(player.hand_size_bonus / 10.0)
    global_features.append(player.pending_hand_size_bonus / 10.0)
    global_features.append(player.cards_played_this_turn / 5.0)

    # Opponent additions (19 — v2 adds hand_size_bonus and pending_hand_size_bonus)
    global_features.append(len(opponent.power_charger) / 20.0)
    global_features.extend(_encode_attribute_counts(opponent.power_charger))
    global_features.append(len(opponent.abyss) / 20.0)
    global_features.extend(_encode_attribute_counts(opponent.abyss))
    global_features.extend(_encode_card_type_counts(opponent.abyss))
    global_features.append(1.0 if opponent.area_enchant_blocked else 0.0)
    global_features.append(opponent.hand_size_bonus / 10.0)           # v2 addition
    global_features.append(opponent.pending_hand_size_bonus / 10.0)   # v2 addition
    global_features.append(opponent.cards_played_this_turn / 5.0)

    # Game state (12)
    phase_one_hot = [0.0] * NUM_PHASES
    if game_state.current_phase in _PHASE_LIST:
        phase_one_hot[_PHASE_LIST.index(game_state.current_phase)] = 1.0
    global_features.extend(phase_one_hot)

    if game_state.last_battle_winner is None:
        global_features.append(0.0)
    elif game_state.last_battle_winner == player.name:
        global_features.append(1.0)
    else:
        global_features.append(-1.0)
    global_features.append(game_state.chronos_at_turn_start / float(CHRONOS_SIZE))

    # Deck composition (8 + 8)
    global_features.extend(_encode_attribute_counts(player.deck))
    global_features.extend(_encode_card_type_counts(player.deck))
    global_features.extend(_encode_attribute_counts(opponent.deck))
    global_features.extend(_encode_card_type_counts(opponent.deck))

    # Priority flag (1)
    global_features.append(1.0 if game_state.priority_player == player_index else 0.0)

    # Zone cards (10 × CARD_FEATURE_SIZE) — omniscient: all zones visible as-is
    zone_features = (
        card_instance_to_features_v2(player.battle_zone, is_night=is_night_bool)
        + card_instance_to_features_v2(opponent.battle_zone, is_night=is_night_bool)
        + card_instance_to_features_v2(player.set_zone_a, is_night=is_night_bool)
        + card_instance_to_features_v2(player.set_zone_b, is_night=is_night_bool)
        + card_instance_to_features_v2(player.set_zone_c, is_night=is_night_bool)
        + card_instance_to_features_v2(opponent.set_zone_a, is_night=is_night_bool)
        + card_instance_to_features_v2(opponent.set_zone_b, is_night=is_night_bool)
        + card_instance_to_features_v2(opponent.set_zone_c, is_night=is_night_bool)
        + card_to_features_v2(
            game_state.previous_battle_characters.get(player_index), is_night=is_night_bool,
        )
        + card_to_features_v2(
            game_state.previous_battle_characters.get(1 - player_index), is_night=is_night_bool,
        )
    )

    # Player hand (10 × CARD_FEATURE_SIZE)
    hand_features: list[float] = []
    for i in range(MAX_HAND_SIZE):
        if i < len(player.hand):
            hand_features.extend(
                card_instance_to_features_v2(player.hand[i], is_night=is_night_bool),
            )
        else:
            hand_features.extend([0.0] * CARD_FEATURE_SIZE)

    # Opponent hand (10 × CARD_FEATURE_SIZE) — omniscient
    opponent_hand_features: list[float] = []
    for i in range(MAX_HAND_SIZE):
        if i < len(opponent.hand):
            opponent_hand_features.extend(
                card_instance_to_features_v2(opponent.hand[i], is_night=is_night_bool),
            )
        else:
            opponent_hand_features.extend([0.0] * CARD_FEATURE_SIZE)

    # Original deck cards (20 × CARD_FEATURE_SIZE per player) — omniscient
    player_deck_features: list[float] = []
    opponent_deck_features: list[float] = []
    if original_deck_cards is not None:
        player_originals = original_deck_cards.get(player_index, [])
        opponent_originals = original_deck_cards.get(1 - player_index, [])
        for i in range(MAX_DECK_SIZE):
            card = player_originals[i] if i < len(player_originals) else None
            player_deck_features.extend(card_to_features_v2(card, is_night=is_night_bool))
        for i in range(MAX_DECK_SIZE):
            card = opponent_originals[i] if i < len(opponent_originals) else None
            opponent_deck_features.extend(card_to_features_v2(card, is_night=is_night_bool))
    else:
        player_deck_features = [0.0] * DECK_OBSERVATION_SIZE_V2
        opponent_deck_features = [0.0] * DECK_OBSERVATION_SIZE_V2

    if decision_context is None:
        decision_context = [0.0] * DECISION_CONTEXT_SIZE_V2

    return (
        global_features
        + zone_features
        + hand_features
        + opponent_hand_features
        + player_deck_features
        + opponent_deck_features
        + decision_context
    )


# ============================================================
# Headless effect engine (thin wrapper — no Discord I/O)
# ============================================================

class HeadlessEffectEngineV2(EffectEngine):
    """EffectEngine that routes all interactive prompts through BotAgent instances."""

    def __init__(self, get_agent: Callable[[int], Any]) -> None:
        super().__init__()
        self.get_agent = get_agent

    async def _prompt_effect_order(self, player_index, eligible):
        return self.get_agent(player_index).choose_effect_order(eligible)

    async def _prompt_card_selection(self, player_index, cards, prompt_text, placeholder=''):
        if not cards:
            return None
        return self.get_agent(player_index).choose_effect_card(cards)

    async def _prompt_number_selection(
        self, player_index, min_value, max_value, prompt_text, placeholder='', label_prefix=None,
    ):
        return self.get_agent(player_index).choose_effect_number(min_value, max_value)

    async def _prompt_text_input(
        self, player_index, prompt_text, modal_title='', button_label='', input_label=None,
        input_placeholder=None, validator=None,
    ):
        return self.get_agent(player_index).choose_effect_text()

    async def _send_dm(self, player_index, **kwargs):
        return None

    async def _send_to_channel(self, **kwargs):
        return None

    async def notify_draw(self, game_state, player_index, count):
        return None


# ============================================================
# Game result dataclass
# ============================================================

@dataclass
class GameResultV2:
    """Full game result with trajectories for PPO training."""
    winner: int          # 0, 1, or -1 for draw
    turns: int
    reward: float        # from player 0's perspective
    player_0_final_hp: int
    player_1_final_hp: int
    player_0_trajectory: list = field(default_factory=list)
    player_1_trajectory: list = field(default_factory=list)


# ============================================================
# Headless game environment v2
# ============================================================

class HeadlessGameEnvV2:
    """
    V2 headless game environment for PPO training.

    Reward design differences from v1:
      - No tempo reward.
      - intermediate_reward_scale defaults to 0.001 (was 0.01).
      - Terminal reward: win/loss ±1.0 + margin bonus ±0.1×(surviving_hp/100).
      - Deck-out penalty: extra −0.1 if loss by deck exhaustion.
    """

    def __init__(
        self,
        agent_0=None,
        agent_1=None,
        intermediate_reward_scale: float = 0.001,
        deck_cards_for_player_0: Optional[list[Card]] = None,
        deck_cards_for_player_1: Optional[list[Card]] = None,
    ) -> None:
        from zutomayo.engine.bot_agent import BotAgent

        self.all_cards = load_cards()
        self.card_index = build_card_index(self.all_cards)
        self.intermediate_reward_scale = intermediate_reward_scale
        self.deck_cards_for_player_0 = deck_cards_for_player_0
        self.deck_cards_for_player_1 = deck_cards_for_player_1
        self.agent_0 = agent_0 if agent_0 is not None else BotAgent()
        self.agent_1 = agent_1 if agent_1 is not None else BotAgent()
        self.game_state: Optional[GameState] = None
        self.effect_engine: Optional[EffectEngine] = None
        self.turn_manager = None
        self.original_deck_cards: Optional[dict[int, list[Card]]] = None

    def _get_agent(self, player_index: int):
        return self.agent_0 if player_index == 0 else self.agent_1

    def _update_agent_game_state(self) -> None:
        from zutomayo.engine.bot_agent import ModelBotAgent
        for agent in (self.agent_0, self.agent_1):
            if isinstance(agent, ModelBotAgent):
                agent.current_game_state = self.game_state
                agent.original_deck_cards = self.original_deck_cards

    def _build_deck(self, owner_name: str, deck_cards: Optional[list[Card]] = None):
        from zutomayo.engine.bot_agent import load_random_saved_deck
        if deck_cards is None:
            deck_cards = load_random_saved_deck(self.card_index)
        return build_deck_from_cards(deck_cards, owner_name)

    def _assign_intermediate_rewards(self, hp_before: dict[int, int]) -> None:
        if self.intermediate_reward_scale <= 0:
            return
        from zutomayo.engine.bot_agent import ModelBotAgent
        for player_index in range(2):
            agent = self._get_agent(player_index)
            if not isinstance(agent, ModelBotAgent):
                continue
            if not agent.trajectory_buffer:
                continue
            opponent_index = 1 - player_index
            player_hp_delta = (
                self.game_state.players[player_index].hp - hp_before[player_index]
            )
            opponent_hp_delta = (
                self.game_state.players[opponent_index].hp - hp_before[opponent_index]
            )
            intermediate_reward = self.intermediate_reward_scale * (
                -opponent_hp_delta + player_hp_delta
            )
            agent.trajectory_buffer[-1].reward += intermediate_reward

    def _assign_terminal_rewards(self, terminal_reward: float) -> None:
        """Assign terminal reward with margin bonus and deck-out penalty."""
        from zutomayo.engine.bot_agent import ModelBotAgent
        game_state = self.game_state

        for player_index, agent in enumerate((self.agent_0, self.agent_1)):
            if not isinstance(agent, ModelBotAgent):
                continue
            if not agent.trajectory_buffer:
                continue

            player_reward = terminal_reward if player_index == 0 else -terminal_reward

            # Margin bonus: larger reward for cleaner wins (up to ±0.1)
            if player_reward > 0:
                surviving_hp = game_state.players[player_index].hp
                player_reward += 0.1 * (surviving_hp / 100.0)
            elif player_reward < 0:
                surviving_hp = game_state.players[player_index].hp
                player_reward -= 0.1 * (surviving_hp / 100.0)

                # Deck-out penalty: extra −0.1 if this player lost with an empty deck
                if len(game_state.players[player_index].deck) == 0:
                    player_reward -= 0.1

            agent.trajectory_buffer[-1].reward += player_reward
            agent.trajectory_buffer[-1].done = True

    def _make_game_result(self) -> GameResultV2:
        from zutomayo.engine.bot_agent import ModelBotAgent
        game_state = self.game_state

        if game_state.result == Result.PLAYER_1_WIN:
            reward = 1.0
            winner = 0
        elif game_state.result == Result.PLAYER_2_WIN:
            reward = -1.0
            winner = 1
        else:
            reward = 0.0
            winner = -1

        self._assign_terminal_rewards(reward)

        player_0_trajectory = (
            list(self.agent_0.trajectory_buffer)
            if isinstance(self.agent_0, ModelBotAgent) else []
        )
        player_1_trajectory = (
            list(self.agent_1.trajectory_buffer)
            if isinstance(self.agent_1, ModelBotAgent) else []
        )

        return GameResultV2(
            winner=winner,
            turns=game_state.turn,
            reward=reward,
            player_0_final_hp=game_state.players[0].hp,
            player_1_final_hp=game_state.players[1].hp,
            player_0_trajectory=player_0_trajectory,
            player_1_trajectory=player_1_trajectory,
        )

    def reset(self) -> list[float]:
        """Reset the environment and start a new game."""
        from zutomayo.engine.bot_agent import ModelBotAgent
        from zutomayo.engine.turn_manager import TurnManager

        self.effect_engine = HeadlessEffectEngineV2(self._get_agent)

        for agent in (self.agent_0, self.agent_1):
            if isinstance(agent, ModelBotAgent):
                agent.clear_trajectory()

        deck_1 = self._build_deck('agent_0', self.deck_cards_for_player_0)
        deck_2 = self._build_deck('agent_1', self.deck_cards_for_player_1)

        self.original_deck_cards = {
            0: [card_instance.card for card_instance in deck_1],
            1: [card_instance.card for card_instance in deck_2],
        }

        controller = GameController(
            name_1='agent_0',
            name_2='agent_1',
            deck_1=deck_1,
            deck_2=deck_2,
            effect_engine=self.effect_engine,
        )
        self.game_state = controller.game_state
        self.turn_manager = TurnManager(self.game_state, self.effect_engine)

        self.game_state.current_phase = Phase.SETUP
        self.game_state.turn = 0

        for player in self.game_state.players:
            random.shuffle(player.deck)
            player.draw(5)

        self._update_agent_game_state()

        for player_index in range(2):
            player = self.game_state.players[player_index]
            agent = self._get_agent(player_index)
            cards_to_redraw = agent.choose_redraw(player.hand[:])
            if cards_to_redraw:
                count = len(cards_to_redraw)
                for card_instance in cards_to_redraw:
                    if card_instance in player.hand:
                        player.hand.remove(card_instance)
                player.draw(count)
                for card_instance in cards_to_redraw:
                    card_instance.zone = Zone.DECK
                    player.deck.append(card_instance)
                random.shuffle(player.deck)

        self._update_agent_game_state()

        for player_index in range(2):
            player = self.game_state.players[player_index]
            agent = self._get_agent(player_index)
            chosen_card = agent.choose_initial_battle_card(player.hand[:])
            self.turn_manager.set_initial_battle_card(player, chosen_card)

        for player in self.game_state.players:
            self.turn_manager.reveal_initial_card(player)

        return build_observation_v2(
            self.game_state, 0,
            original_deck_cards=self.original_deck_cards,
        )

    async def play_full_game(self) -> GameResultV2:
        """Play a complete game and return the result with trajectories."""
        self.reset()

        game_state = self.game_state
        turn_manager = self.turn_manager

        # Turn 1: advance chronos, effects, battle (no SET_CARDS phase)
        game_state.turn = 1
        game_state.chronos_at_turn_start = game_state.chronos

        game_state.current_phase = Phase.ADVANCE_CHRONOS
        for player in game_state.players:
            turn_manager.advance_chronos(player)

        game_state.current_phase = Phase.PROCESS_EFFECTS
        priority = game_state.priority_player
        await self.effect_engine.process_effects(game_state, priority)
        turn_manager.check_win_condition()
        if game_state.result != Result.IN_PROGRESS:
            return self._make_game_result()

        await self.effect_engine.process_effects(game_state, 1 - priority)
        turn_manager.check_win_condition()
        if game_state.result != Result.IN_PROGRESS:
            return self._make_game_result()

        hp_before_battle = {i: game_state.players[i].hp for i in range(2)}

        game_state.current_phase = Phase.BATTLE
        turn_manager.resolve_battle()
        turn_manager.check_win_condition()
        self._assign_intermediate_rewards(hp_before_battle)

        if game_state.result != Result.IN_PROGRESS:
            return self._make_game_result()

        game_state.current_phase = Phase.END_TURN
        for player in game_state.players:
            turn_manager.end_turn(player)
        for player in game_state.players:
            player.hand_size_bonus += player.pending_hand_size_bonus
            player.pending_hand_size_bonus = 0
        self.effect_engine.check_area_enchant_removal(game_state, turn_manager, end_of_turn=True)
        turn_manager.check_deck_loss()
        if game_state.result != Result.IN_PROGRESS:
            return self._make_game_result()

        self.effect_engine.save_battle_characters(game_state)
        turn_manager.reset_turn_flags()

        max_turns = 50
        while game_state.result == Result.IN_PROGRESS and game_state.turn < max_turns:
            game_state.turn += 1
            game_state.chronos_at_turn_start = game_state.chronos
            await self._do_headless_turn()
            if game_state.result != Result.IN_PROGRESS:
                return self._make_game_result()

        return self._make_game_result()

    async def _do_headless_turn(self) -> None:
        """Execute a single full turn (phases 1–9) without Discord interaction."""
        game_state = self.game_state
        turn_manager = self.turn_manager

        self._update_agent_game_state()

        # Phase 1: Set cards (no tempo reward)
        game_state.current_phase = Phase.SET_CARDS
        for player_index in range(2):
            player = game_state.players[player_index]
            agent = self._get_agent(player_index)
            max_cards = turn_manager.get_max_cards_to_set(player)
            max_cards = min(max_cards, len(player.hand))
            if max_cards == 0:
                continue
            selected = agent.choose_cards_to_set(player.hand[:], max_cards)
            if len(selected) >= 1:
                turn_manager.set_card(player, selected[0], Zone.SET_ZONE_A)
            if len(selected) >= 2:
                turn_manager.set_card(player, selected[1], Zone.SET_ZONE_B)

        # Phase 2: Reveal
        game_state.current_phase = Phase.REVEAL
        for player in game_state.players:
            if player.set_zone_a:
                player.set_zone_a.face_up = True
            if player.set_zone_b:
                player.set_zone_b.face_up = True

        # Phase 3: Advance chronos
        game_state.current_phase = Phase.ADVANCE_CHRONOS
        for player in game_state.players:
            turn_manager.advance_chronos(player)

        # Phase 4: Character swap
        game_state.current_phase = Phase.CHARACTER_SWAP
        for player in game_state.players:
            turn_manager.do_character_swap(player)
        self.effect_engine.check_area_enchant_removal(game_state, turn_manager)

        # Phase 5: Area enchant swap
        game_state.current_phase = Phase.AREA_ENCHANT_SWAP
        for player in game_state.players:
            turn_manager.do_area_enchant_swap(player)
        self.effect_engine.check_area_enchant_removal(game_state, turn_manager)

        # Phase 6: Process effects
        game_state.current_phase = Phase.PROCESS_EFFECTS
        priority = game_state.priority_player
        await self.effect_engine.process_effects(game_state, priority)
        turn_manager.check_win_condition()
        if game_state.result != Result.IN_PROGRESS:
            return

        await self.effect_engine.process_effects(game_state, 1 - priority)
        turn_manager.check_win_condition()
        if game_state.result != Result.IN_PROGRESS:
            return

        hp_before_battle = {i: game_state.players[i].hp for i in range(2)}

        # Phase 7: Battle
        game_state.current_phase = Phase.BATTLE
        turn_manager.resolve_battle()
        turn_manager.check_win_condition()
        self._assign_intermediate_rewards(hp_before_battle)

        if game_state.result != Result.IN_PROGRESS:
            return

        # Phase 8: Turn-end effects
        game_state.current_phase = Phase.TURN_END_EFFECTS
        self.effect_engine.process_end_of_turn_effects(game_state)
        turn_manager.check_win_condition()
        if game_state.result != Result.IN_PROGRESS:
            return

        # Phase 9: End turn
        game_state.current_phase = Phase.END_TURN
        for player in game_state.players:
            turn_manager.end_turn(player)
        for player in game_state.players:
            player.hand_size_bonus += player.pending_hand_size_bonus
            player.pending_hand_size_bonus = 0
        self.effect_engine.check_area_enchant_removal(game_state, turn_manager, end_of_turn=True)
        turn_manager.check_deck_loss()
        if game_state.result != Result.IN_PROGRESS:
            return

        self.effect_engine.save_battle_characters(game_state)
        turn_manager.reset_turn_flags()
