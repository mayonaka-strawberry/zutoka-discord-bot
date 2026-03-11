import asyncio
from typing import Any, Optional
from uuid import uuid4
from zutomayo.data.card_loader import load_cards
from zutomayo.effects.effect_engine import EffectEngine
from zutomayo.engine.deck_builder import build_deck_from_cards, build_random_deck
from zutomayo.engine.game_controller import GameController
from zutomayo.engine.turn_manager import TurnManager
from zutomayo.models.card import Card
from zutomayo.models.game_state import GameState


class GameSession:
    def __init__(self, game_id: str, channel_id: int, creator_id: int) -> None:
        self.game_id = game_id
        self.channel_id = channel_id
        self.player_discord_ids: dict[int, int] = {}  # discord user ID -> player index
        self.game_state: Optional[GameState] = None
        self.turn_manager: Optional[TurnManager] = None
        self.effect_engine = EffectEngine()

        # Track both players' Discord IDs
        self.player_discord_ids[creator_id] = 0

        # Synchronization for simultaneous actions
        self.pending_actions: dict[int, Any] = {}  # player index -> action
        self.player_events: dict[int, asyncio.Event] = {
            0: asyncio.Event(),
            1: asyncio.Event(),
        }

        # Game flow task
        self.game_task: Optional[asyncio.Task] = None

        # Zone snapshot tracking: maps zone key -> frozenset of card unique_ids
        # Keys: "p0_abyss", "p0_power_charger", "p1_abyss", "p1_power_charger"
        self._zone_snapshots: dict[str, frozenset[str]] = {}

        # TCG mode attributes
        self.is_tcg: bool = False
        self.best_of: int = 0

    def add_player(self, discord_id: int) -> int:
        player_index = 1
        self.player_discord_ids[discord_id] = player_index
        return player_index

    def initialize_game(
        self,
        deck_1_cards: list[Card] | None = None,
        deck_2_cards: list[Card] | None = None,
    ) -> GameState:
        all_cards = load_cards()

        discord_ids = list(self.player_discord_ids.keys())
        name_1 = str(discord_ids[0])
        name_2 = str(discord_ids[1])

        if deck_1_cards is not None:
            deck_1 = build_deck_from_cards(deck_1_cards, name_1)
        else:
            deck_1 = build_random_deck(all_cards, name_1)

        if deck_2_cards is not None:
            deck_2 = build_deck_from_cards(deck_2_cards, name_2)
        else:
            deck_2 = build_random_deck(all_cards, name_2)

        controller = GameController(
            name_1=name_1,
            name_2=name_2,
            deck_1=deck_1,
            deck_2=deck_2,
            effect_engine=self.effect_engine,
        )

        self.game_state = controller.game_state
        self.game_state.game_id = self.game_id
        self.turn_manager = TurnManager(self.game_state, self.effect_engine)
        return self.game_state

    def get_player_index(self, discord_id: int) -> Optional[int]:
        return self.player_discord_ids.get(discord_id)

    def get_discord_id(self, player_index: int) -> Optional[int]:
        for discord_id, index in self.player_discord_ids.items():
            if index == player_index:
                return discord_id
        return None

    def submit_action(self, player_index: int, action: Any) -> None:
        self.pending_actions[player_index] = action
        self.player_events[player_index].set()

    async def wait_for_both_players(self, timeout: float = 300.0) -> bool:
        try:
            await asyncio.wait_for(
                asyncio.gather(
                    self.player_events[0].wait(),
                    self.player_events[1].wait(),
                ),
                timeout=timeout,
            )
            return True
        except asyncio.TimeoutError:
            return False

    async def wait_for_player(self, player_index: int, timeout: float = 300.0) -> bool:
        try:
            await asyncio.wait_for(
                self.player_events[player_index].wait(),
                timeout=timeout,
            )
            return True
        except asyncio.TimeoutError:
            return False

    def clear_pending(self) -> None:
        self.pending_actions.clear()
        self.player_events[0].clear()
        self.player_events[1].clear()

    def clear_pending_player(self, player_index: int) -> None:
        self.pending_actions.pop(player_index, None)
        self.player_events[player_index].clear()

    @property
    def is_full(self) -> bool:
        return len(self.player_discord_ids) == 2


class GameSessionManager:
    def __init__(self) -> None:
        self.active_games: dict[str, GameSession] = {}
        self.player_to_game: dict[int, str] = {}  # discord user ID -> game_id

    def create_game(self, channel_id: int, creator_id: int) -> GameSession:
        if creator_id in self.player_to_game:
            raise ValueError('You are already in a game.')

        game_id = str(uuid4())[:8]
        session = GameSession(game_id, channel_id, creator_id)
        self.active_games[game_id] = session
        self.player_to_game[creator_id] = game_id
        return session

    def join_game(self, game_id: str, joiner_id: int) -> GameSession:
        if joiner_id in self.player_to_game:
            raise ValueError('You are already in a game.')

        session = self.active_games.get(game_id)
        if session is None:
            raise ValueError(f'Game {game_id} not found.')
        if session.is_full:
            raise ValueError('Game is already full.')

        creator_id = list(session.player_discord_ids.keys())[0]
        if joiner_id == creator_id:
            raise ValueError('You cannot join your own game.')

        session.add_player(joiner_id)
        self.player_to_game[joiner_id] = game_id
        return session

    def get_session_by_player(self, discord_id: int) -> Optional[GameSession]:
        game_id = self.player_to_game.get(discord_id)
        if game_id is None:
            return None
        return self.active_games.get(game_id)

    def remove_game(self, game_id: str) -> None:
        session = self.active_games.pop(game_id, None)
        if session:
            for discord_id in session.player_discord_ids:
                self.player_to_game.pop(discord_id, None)


# Singleton instance
session_manager = GameSessionManager()
