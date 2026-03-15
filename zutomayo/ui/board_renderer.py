from __future__ import annotations
from functools import lru_cache
from pathlib import Path
from typing import Optional
import discord
from PIL import Image
from zutomayo.ui.image_utils import save_image_for_discord
from zutomayo.enums.chronos import Chronos
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
# Main board rendering
# ---------------------------------------------------------------------------


def render_board_image(
    game_state: GameState,
    perspective: Chronos,
) -> discord.File:
    """Render the full game board as a 4500x4500 JPEG."""
    board = _get_board_base()

    day_player: Optional[Player] = None
    night_player: Optional[Player] = None
    for p in game_state.players:
        if p.side == Chronos.DAY:
            day_player = p
        else:
            night_player = p

    if perspective == Chronos.NIGHT:
        # Rotate the board background first, then paste cards upright.
        # After rotation DAY_ZONES coords map to the visual bottom (NIGHT side)
        # and NIGHT_ZONES coords map to the visual top (DAY side).
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
