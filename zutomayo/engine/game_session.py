import asyncio
import random
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from zutomayo.engine.game_persistence import GameRecordStore
    from zutomayo.match.broker import MatchDecisionBroker
    from zutomayo.match.transport import MatchTransport


class GameSession:
    def __init__(self, game_id: str, channel_id: int, creator_id: int) -> None:
        self.game_id = game_id
        self.channel_id = channel_id
        self.player_discord_ids: dict[int, int] = {}  # discord user ID -> player index

        # Engine seed: Game(seed=...) consumes it directly, and it is persisted
        # in the game manifest so a logged game replays deterministically.
        self.random_seed: int = random.SystemRandom().getrandbits(64)

        # Decision-broker runtime, installed by the flow that runs this game.
        self.broker: Optional['MatchDecisionBroker'] = None
        self.transport: Optional['MatchTransport'] = None

        # engine_alpha game, set by the match flow.
        self.game: Any = None

        # Permanent game record store, created when the match is initialized.
        # Records are never deleted; game lifecycle is tracked by games.status.
        self.persistence: Optional['GameRecordStore'] = None

        # Track both players' Discord IDs
        self.player_discord_ids[creator_id] = 0

        # Synchronization for pre-match flows (deck building, draft) whose
        # views answer through session.submit_action.
        self.pending_actions: dict[int, Any] = {}  # player index -> action
        self.player_events: dict[int, asyncio.Event] = {
            0: asyncio.Event(),
            1: asyncio.Event(),
        }

        # Game flow task
        self.game_task: Optional[asyncio.Task] = None

        # TCG mode attributes
        self.is_tcg: bool = False
        self.best_of: int = 0

        # Solo mode (player vs bot)
        self.is_solo: bool = False
        self.solo_difficulty: str = 'normal'  # 'normal' | 'easy', only meaningful when is_solo

        # Draft mode: each player opens gacha boxes and builds a deck only from
        # the cards they open. These flags are consumed by the draft phase,
        # which runs before persistence exists, so nothing in resume needs them.
        self.is_draft: bool = False
        self.draft_boxes: int = 0
        self.draft_visibility: str = 'public'  # 'public' | 'private'

        # Deck name chosen by each player (populated by deck-selection views).
        # None means the player used a random/manual/no-saved-deck path.
        self.player_deck_names: dict[int, str | None] = {0: None, 1: None}

    def add_player(self, discord_id: int) -> int:
        player_index = 1
        self.player_discord_ids[discord_id] = player_index
        return player_index

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

    async def wait_for_both_players(self, timeout: float | None = 300.0) -> bool:
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

    # Sentinel Discord ID used for the bot player in solo mode.
    BOT_DISCORD_ID = 0

    async def create_game(self, channel_id: int, creator_id: int) -> GameSession:
        if creator_id in self.player_to_game:
            raise ValueError('You are already in a game.')

        from zutomayo.data.game_id_allocator import allocate_game_id

        game_id = await allocate_game_id()
        session = GameSession(game_id, channel_id, creator_id)
        self.active_games[game_id] = session
        self.player_to_game[creator_id] = game_id
        return session

    async def create_solo_game(self, channel_id: int, creator_id: int) -> GameSession:
        """
        Create a solo game where the creator plays against the bot.

        The bot is added as player 1 with a sentinel Discord ID (0).
        """
        if creator_id in self.player_to_game:
            raise ValueError('You are already in a game.')

        from zutomayo.data.game_id_allocator import allocate_game_id

        game_id = await allocate_game_id()
        session = GameSession(game_id, channel_id, creator_id)
        session.is_solo = True
        session.add_player(self.BOT_DISCORD_ID)
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

    def detach_game(self, game_id: str) -> None:
        """
        Drop the session from the in-memory maps, freeing its players for new
        games. The permanent game record is untouched; status transitions are
        the responsibility of the caller (game end, save, quit, abandon).
        """
        session = self.active_games.pop(game_id, None)
        if session:
            for discord_id in session.player_discord_ids:
                self.player_to_game.pop(discord_id, None)

    def remove_game(self, game_id: str) -> None:
        self.detach_game(game_id)


# Singleton instance
session_manager = GameSessionManager()
