from __future__ import annotations
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional
import discord
from PIL import Image
from zutomayo.ui.image_utils import save_image_for_discord
from zutomayo.enums.chronos import Chronos
from zutomayo.enums.zone import Zone
from zutomayo.models.card_instance import CardInstance
from zutomayo.models.game_state import GameState
from zutomayo.models.player import Player


_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

BOARD_NATIVE = 1500
BOARD_RENDER = 4500
SCALE = BOARD_RENDER // BOARD_NATIVE  # 3

# ---------------------------------------------------------------------------
# Zone coordinates at NATIVE 1500x1500 scale.
# Each is (left, top, right, bottom).
# DAY player occupies the bottom half of the board.
# ---------------------------------------------------------------------------


DAY_ZONES = {
    'battle':         (654, 760, 844, 1036),    # 190x276
    'set_a':          (452, 1189, 642, 1465),   # 190x276
    'set_b':          (669, 1189, 859, 1465),   # 190x276
    'set_c':          (886, 1189, 1076, 1465),  # 190x276
    'power_charger':  (104, 1182, 294, 1458),   # 190x276
    'deck':           (1145, 1189, 1335, 1465), # 190x276
    'abyss':          (34, 856, 224, 1132),     # 190x276
}


def _mirror_rect(rect: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """Mirror a rectangle 180 degrees around the center of the board."""
    l, t, r, b = rect
    return (BOARD_NATIVE - r, BOARD_NATIVE - b, BOARD_NATIVE - l, BOARD_NATIVE - t)


NIGHT_ZONES = {k: _mirror_rect(v) for k, v in DAY_ZONES.items()}

# Map Zone enum → zone coordinate key for board-visible zones.
_ZONE_TO_KEY: dict[Zone, str] = {
    Zone.BATTLE_ZONE: 'battle',
    Zone.SET_ZONE_A: 'set_a',
    Zone.SET_ZONE_B: 'set_b',
    Zone.SET_ZONE_C: 'set_c',
    Zone.POWER_CHARGER: 'power_charger',
    Zone.ABYSS: 'abyss',
}


# ---------------------------------------------------------------------------
# Board state diffing
# ---------------------------------------------------------------------------

def _get_visible_cards(
    player: Player,
) -> dict[str, tuple[CardInstance, Zone]]:
    """Return ``{unique_id: (card_instance, zone)}`` for cards visible on the board.

    Only includes cards that are actually rendered: single-slot zones
    plus the top card of stacked zones (power_charger, abyss).
    """
    visible: dict[str, tuple[CardInstance, Zone]] = {}

    for attr, zone in (
        ('battle_zone', Zone.BATTLE_ZONE),
        ('set_zone_a', Zone.SET_ZONE_A),
        ('set_zone_b', Zone.SET_ZONE_B),
        ('set_zone_c', Zone.SET_ZONE_C),
    ):
        ci: Optional[CardInstance] = getattr(player, attr)
        if ci is not None:
            visible[ci.unique_id] = (ci, zone)

    if player.power_charger:
        ci = player.power_charger[-1]
        visible[ci.unique_id] = (ci, Zone.POWER_CHARGER)

    if player.abyss:
        ci = player.abyss[-1]
        visible[ci.unique_id] = (ci, Zone.ABYSS)

    return visible


@dataclass
class CardTransition:
    """Describes a single card's visual change between two board states."""

    card_instance: CardInstance
    """Current CardInstance (or prev instance for cards that left the board)."""

    from_zone: Optional[Zone]
    """Previous zone, or ``None`` if the card appeared from off-board."""

    to_zone: Optional[Zone]
    """Current zone, or ``None`` if the card left the board."""

    flipped: bool
    """Whether ``face_up`` changed between states."""

    from_rect: Optional[tuple[int, int, int, int]]
    """Native-scale board rect for the source position, or ``None``."""

    to_rect: Optional[tuple[int, int, int, int]]
    """Native-scale board rect for the destination position, or ``None``."""


def diff_board_states(
    prev_state: GameState,
    curr_state: GameState,
    perspective: Chronos,
) -> list[CardTransition]:
    """Compare two game states and return visual transitions for animation.

    Detects three kinds of changes:
      - **Moved**: card changed zones (slide animation).
      - **Flipped**: card's ``face_up`` toggled (rotate-Y animation).
      - **Appeared / Disappeared**: card entered or left visible board
        zones (opacity fade).
    """
    transitions: list[CardTransition] = []

    for player_idx in range(2):
        prev_player = prev_state.players[player_idx]
        curr_player = curr_state.players[player_idx]
        zones = _get_zones_for_player(curr_player.side, perspective)

        prev_visible = _get_visible_cards(prev_player)
        curr_visible = _get_visible_cards(curr_player)

        all_ids = set(prev_visible) | set(curr_visible)

        for uid in all_ids:
            prev_entry = prev_visible.get(uid)
            curr_entry = curr_visible.get(uid)

            if prev_entry and curr_entry:
                # Card visible in both states
                prev_ci, prev_zone = prev_entry
                curr_ci, curr_zone = curr_entry

                moved = prev_zone != curr_zone
                flipped = prev_ci.face_up != curr_ci.face_up

                if not moved and not flipped:
                    continue

                from_key = _ZONE_TO_KEY.get(prev_zone)
                to_key = _ZONE_TO_KEY.get(curr_zone)

                transitions.append(CardTransition(
                    card_instance=curr_ci,
                    from_zone=prev_zone,
                    to_zone=curr_zone,
                    flipped=flipped,
                    from_rect=zones[from_key] if from_key else None,
                    to_rect=zones[to_key] if to_key else None,
                ))

            elif curr_entry:
                # Card appeared on board (fade in)
                curr_ci, curr_zone = curr_entry
                to_key = _ZONE_TO_KEY.get(curr_zone)

                transitions.append(CardTransition(
                    card_instance=curr_ci,
                    from_zone=None,
                    to_zone=curr_zone,
                    flipped=False,
                    from_rect=None,
                    to_rect=zones[to_key] if to_key else None,
                ))

            else:
                # Card left the board.  Only animate single-slot zones
                # (battle / set zones).  Stacked zones (power_charger,
                # abyss) just get covered by the new top card.
                prev_ci, prev_zone = prev_entry
                if prev_zone not in { Zone.BATTLE_ZONE, Zone.SET_ZONE_A, Zone.SET_ZONE_B, Zone.SET_ZONE_C }:
                    continue
                from_key = _ZONE_TO_KEY.get(prev_zone)

                transitions.append(CardTransition(
                    card_instance=prev_ci,
                    from_zone=prev_zone,
                    to_zone=None,
                    flipped=False,
                    from_rect=zones[from_key] if from_key else None,
                    to_rect=None,
                ))

    return transitions


# ---------------------------------------------------------------------------
# Animation helpers
# ---------------------------------------------------------------------------


def _get_card_front(ci: CardInstance) -> Image.Image:
    """Get the front (art) image of a card, falling back to card back."""
    if ci.card.image:
        try:
            return _load_card_image(ci.card.image).copy()
        except Exception:
            pass
    return _get_card_back().copy()


# ---------------------------------------------------------------------------
# Cached assets
# ---------------------------------------------------------------------------


_board_base: Optional[Image.Image] = None
_card_back_img: Optional[Image.Image] = None


def _get_board_base() -> Image.Image:
    global _board_base
    if _board_base is None:
        _board_base = (
            Image.open(_PROJECT_ROOT / 'zutomayo/images/board.png')
            .convert('RGBA')
            .resize((BOARD_RENDER, BOARD_RENDER), Image.LANCZOS)
        )
    return _board_base.copy()


def _get_card_back() -> Image.Image:
    global _card_back_img
    if _card_back_img is None:
        _card_back_img = Image.open(_PROJECT_ROOT / 'zutomayo/images/card_back.jpg').convert('RGBA')
    return _card_back_img


@lru_cache(maxsize=128)
def _load_card_image(path: str) -> Image.Image:
    return Image.open(_PROJECT_ROOT / path).convert('RGBA')


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_zones_for_player(
    player_side: Chronos,
    perspective: Chronos,
) -> dict[str, tuple[int, int, int, int]]:
    """Return the zone coordinate dict for a player based on perspective."""
    if perspective == Chronos.NIGHT:
        return NIGHT_ZONES if player_side == Chronos.DAY else DAY_ZONES
    else:
        return DAY_ZONES if player_side == Chronos.DAY else NIGHT_ZONES


def _paste_card(
    board: Image.Image,
    card_instance: Optional[CardInstance],
    rect: tuple[int, int, int, int],
) -> None:
    """Paste a card image onto the board. rect is at native 1500 scale."""
    if card_instance is None:
        return

    l, t, r, b = [v * SCALE for v in rect]
    w, h = r - l, b - t

    if card_instance.face_up and card_instance.card.image:
        try:
            card_img = _load_card_image(card_instance.card.image).copy()
        except Exception:
            card_img = _get_card_back().copy()
    else:
        card_img = _get_card_back().copy()

    card_img = card_img.resize((w, h), Image.LANCZOS)
    board.paste(card_img, (l, t), card_img)


def _paste_card_back(
    board: Image.Image,
    rect: tuple[int, int, int, int],
) -> None:
    """Paste a card back image at rect (native scale)."""
    l, t, r, b = [v * SCALE for v in rect]
    w, h = r - l, b - t
    card_img = _get_card_back().copy().resize((w, h), Image.LANCZOS)
    board.paste(card_img, (l, t), card_img)


def _render_player_zones(
    board: Image.Image,
    player: Player,
    zones: dict[str, tuple[int, int, int, int]],
) -> None:
    """Paste all cards for a single player's zones."""
    _paste_card(board, player.battle_zone, zones['battle'])
    _paste_card(board, player.set_zone_a, zones['set_a'])
    _paste_card(board, player.set_zone_b, zones['set_b'])
    _paste_card(board, player.set_zone_c, zones['set_c'])

    # Power charger: top card only
    if player.power_charger:
        _paste_card(board, player.power_charger[-1], zones['power_charger'])

    # Deck: show card back if non-empty
    if player.deck:
        _paste_card_back(board, zones['deck'])

    # Abyss: top card only
    if player.abyss:
        _paste_card(board, player.abyss[-1], zones['abyss'])


# ---------------------------------------------------------------------------
# Animated board rendering
# ---------------------------------------------------------------------------


def _render_board_animation(
    game_state: GameState,
    prev_game_state: GameState,
    perspective: Chronos,
) -> Optional[discord.File]:
    """Build an animated GIF showing card transitions between two board states.

    Returns ``None`` if there are no visual changes to animate.
    Renders at BOARD_NATIVE (1500x1500) to keep GIF sizes Discord-friendly.
    """
    from zutomayo.ui.animation import Scene

    transitions = diff_board_states(prev_game_state, game_state, perspective)
    
    if not transitions:
        return None

    scene = Scene(
        width=BOARD_NATIVE,
        height=BOARD_NATIVE,
        fps=16,
        duration=1,
        background=(0, 0, 0, 255),
        render_scale=0.5,
    )

    # Board background
    board_bg = _get_board_base().resize(
        (BOARD_NATIVE, BOARD_NATIVE), Image.LANCZOS,
    )
    if perspective == Chronos.NIGHT:
        board_bg = board_bg.rotate(180, resample=Image.LANCZOS)

    for p in game_state.players:
        zones = _get_zones_for_player(p.side, perspective)
        if p.deck:
            l, t, r, b = zones['deck']
            w, h = r - l, b - t
            card_img = _get_card_back().copy().resize((w, h), Image.LANCZOS)
            board_bg.paste(card_img, (l, t), card_img)

    scene.add(board_bg, position=(BOARD_NATIVE / 2, BOARD_NATIVE / 2))

    # Static cards
    transitioning_ids = {tr.card_instance.unique_id for tr in transitions}

    for p in game_state.players:
        zones = _get_zones_for_player(p.side, perspective)
        for uid, (ci, zone) in _get_visible_cards(p).items():
            if uid in transitioning_ids:
                continue
            card_img = _get_card_front(ci) if ci.face_up else _get_card_back().copy()
            scene.add(card_img, rect=zones[_ZONE_TO_KEY[zone]])

    # Animated cards
    for tr in transitions:
        ci = tr.card_instance

        if tr.flipped:
            if ci.face_up:
                source_img = _get_card_back().copy()
                back_img = _get_card_front(ci)
            else:
                source_img = _get_card_front(ci)
                back_img = _get_card_back().copy()
        else:
            source_img = _get_card_front(ci) if ci.face_up else _get_card_back().copy()
            back_img = None

        obj = scene.add(source_img, back_image=back_img)

        if tr.from_rect and tr.to_rect:
            obj.slide(tr.from_rect, tr.to_rect, flip=tr.flipped)
        elif tr.to_rect:
            obj.fade_in(rect=tr.to_rect)
        elif tr.from_rect:
            obj.fade_out(rect=tr.from_rect)

    return scene.render_to_file('board.gif')


# ---------------------------------------------------------------------------
# Main board rendering
# ---------------------------------------------------------------------------


def _render_board_static(
    game_state: GameState,
    perspective: Chronos,
) -> discord.File:
    """Render the board as a static 4500x4500 JPEG."""
    board = _get_board_base()

    day_player: Optional[Player] = None
    night_player: Optional[Player] = None
    for p in game_state.players:
        if p.side == Chronos.DAY:
            day_player = p
        else:
            night_player = p

    if perspective == Chronos.NIGHT:
        board = board.rotate(180, resample=Image.LANCZOS)
        if day_player:
            _render_player_zones(board, day_player, NIGHT_ZONES)
        if night_player:
            _render_player_zones(board, night_player, DAY_ZONES)
    else:
        if day_player:
            _render_player_zones(board, day_player, DAY_ZONES)
        if night_player:
            _render_player_zones(board, night_player, NIGHT_ZONES)

    rgb_board = Image.new('RGB', board.size, (0, 0, 0))
    rgb_board.paste(board, mask=board.split()[3])

    return save_image_for_discord(rgb_board, 'board.jpg')


def render_board_image(
    game_state: GameState,
    perspective: Chronos,
    prev_game_state: Optional[GameState] = None,
) -> discord.File:
    """Render the game board, animated if a previous state is provided."""
    if prev_game_state is not None:
        animated = _render_board_animation(game_state, prev_game_state, perspective)
        if animated is not None:
            return animated

    return _render_board_static(game_state, perspective)


# ---------------------------------------------------------------------------
# Zone strip images (Abyss / Power Charger)
# ---------------------------------------------------------------------------


def render_zone_strip(
    cards: list[CardInstance],
    label: str,
) -> Optional[discord.File]:
    """Render all cards in a zone as a horizontal strip image."""
    if not cards:
        return None

    card_w, card_h = 700, 978
    padding = 10
    columns = min(len(cards), 10)
    rows = -(-len(cards) // columns)

    grid_w = columns * card_w + (columns - 1) * padding
    grid_h = rows * card_h + (rows - 1) * padding
    grid = Image.new('RGBA', (grid_w, grid_h), (0, 0, 0, 0))

    for idx, card_instance in enumerate(cards):
        col = idx % columns
        row = idx // columns
        x = col * (card_w + padding)
        y = row * (card_h + padding)

        if card_instance.face_up and card_instance.card.image:
            try:
                with Image.open(_PROJECT_ROOT / card_instance.card.image) as card_img:
                    card_img = card_img.resize((card_w, card_h))
                    grid.paste(card_img, (x, y))
            except Exception:
                with Image.open(_PROJECT_ROOT / 'zutomayo/images/card_back.jpg') as back:
                    back = back.resize((card_w, card_h))
                    grid.paste(back, (x, y))
        else:
            with Image.open(_PROJECT_ROOT / 'zutomayo/images/card_back.jpg') as back:
                back = back.resize((card_w, card_h))
                grid.paste(back, (x, y))

    safe_label = label.replace(' ', '_').lower()
    return save_image_for_discord(grid, f'{safe_label}.webp')


# ---------------------------------------------------------------------------
# Zone message data for separate Discord messages
# ---------------------------------------------------------------------------


def generate_zone_messages(
    game_state: GameState,
    player_names: dict[int, str],
) -> list[tuple[str, Optional[discord.File]]]:
    """
    Generate (label, file_or_None) tuples for Abyss and Power Charger zones.

    Returns entries in order:
      Player 0 Abyss, Player 0 Power Charger,
      Player 1 Abyss, Player 1 Power Charger.

    Must be called once per destination because discord.File is consumed on send.
    """
    messages: list[tuple[str, Optional[discord.File]]] = []
    for index in range(2):
        player = game_state.players[index]
        name = player_names.get(index, f'Player {index + 1}')

        abyss_strip = render_zone_strip(player.abyss, f'{name} Abyss')
        messages.append((f'{name} Abyss', abyss_strip))

        pc_strip = render_zone_strip(player.power_charger, f'{name} Power Charger')
        messages.append((f'{name} Power Charger', pc_strip))

    return messages
