from __future__ import annotations
import asyncio
from functools import lru_cache
from pathlib import Path
from typing import Optional
import discord
from PIL import Image
from zutomayo.ui.image_utils import save_image_for_discord
from zutomayo.enums.chronos import Chronos

# The renderer reads duck-typed views: any board object with `.players`, any
# player object with the zone attributes (battle_zone, set_zone_a/b/c,
# power_charger, deck, abyss, side), and any card holder with `.face_up` and
# `.card` (BoardView / PlayerView / CardView from zutomayo.match.state_view).
CardInstance = GameState = Player = object


_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

BOARD_NATIVE = 1500
BOARD_RENDER = 4500
SCALE = BOARD_RENDER // BOARD_NATIVE  # 3

# ---------------------------------------------------------------------------
# Zone coordinates at NATIVE 1500x1500 scale.
# Each rectangle is (left, top, right, bottom).
# DAY player occupies the bottom half of the board.
# ---------------------------------------------------------------------------


# Card art is not a single resolution: 700x978, 700x977 and 700x975 all occur, spanning
# aspects 0.7157 to 0.7180. 196x274 is therefore the bounding box each card is scaled to fit
# and centred within (see _fit_card_into_rect), not a shape cards are stretched to. It is wide
# enough that even the squarest card fills the full 196 px width, and both halves are integers
# so centring needs no rounding.
CARD_WIDTH = 196
CARD_HEIGHT = 274


# The board art is 180-degree rotationally symmetric, but its rotational centre is
# (751.1, 748.8), not the geometric centre (750, 750). Derived by measuring each printed
# slot centre on both halves at subpixel precision: 2 * centre = day + night. Over the six
# slots below that gives 2*cx = 1502.21 (sd 0.16) and 2*cy = 1497.64 (sd 0.35); the y value
# is corroborated by the HP track circle pairs (median 1497.32). Mirroring about (750, 750)
# instead puts every NIGHT zone ~2 px off its printed slot.
MIRROR_X_SUM = 1502  # round(2 * 751.10)
MIRROR_Y_SUM = 1498  # round(2 * 748.82)


def _mirror_rect(rect: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """Mirror a rectangle 180 degrees about the board art's rotational centre."""
    left, top, right, bottom = rect
    return (
        MIRROR_X_SUM - right,
        MIRROR_Y_SUM - bottom,
        MIRROR_X_SUM - left,
        MIRROR_Y_SUM - top,
    )


# Printed card slot centres measured from zutomayo/images/board.png at native 1500x1500
# scale, by locating each slot border at subpixel precision and halving. Every card
# rectangle is centred on one of these, which lands it within 1.2 px of the measured slot
# centre on both halves of the board.
#
# 'battle' is the character card in play and has no printed outline: it sits inside the
# centre circle, horizontally centred on the board's rotational centre x, and offset below
# the midline so the two character cards stay on their own side. Its centre y of 896 puts
# its top edge 10 px below the midline, leaving a symmetric 20 px gap to the NIGHT card.
DAY_SLOT_CENTERS = {
    'battle':         (751, 896),
    'set_a':          (548, 1325),
    'set_b':          (765, 1325),
    'set_c':          (982, 1325),
    'power_charger':  (207, 1317),
    'deck':           (1242, 1325),
    'abyss':          (131, 991),
}


def _card_rect_at(center_x: int, center_y: int) -> tuple[int, int, int, int]:
    """Return a CARD_WIDTH x CARD_HEIGHT rectangle centred on a slot centre."""
    half_width = CARD_WIDTH // 2
    half_height = CARD_HEIGHT // 2
    return (
        center_x - half_width,
        center_y - half_height,
        center_x + half_width,
        center_y + half_height,
    )


DAY_ZONES = {name: _card_rect_at(*center) for name, center in DAY_SLOT_CENTERS.items()}
NIGHT_ZONES = {name: _mirror_rect(rect) for name, rect in DAY_ZONES.items()}


# Printed slot outlines measured from the board art, rounded to whole native pixels. These
# are the reference the card rectangles above are centred in; only the calibration script
# (scripts/calibrate_board.py) reads them. 'battle' is absent because it has no outline.
DAY_PRINTED_SLOTS = {
    'set_a':          (450, 1185, 646, 1466),
    'set_b':          (667, 1185, 863, 1466),
    'set_c':          (884, 1185, 1080, 1466),
    'power_charger':  (26, 1158, 387, 1476),
    'deck':           (1145, 1184, 1339, 1465),
    'abyss':          (34, 851, 228, 1131),
}
NIGHT_PRINTED_SLOTS = {
    name: _mirror_rect(slot) for name, slot in DAY_PRINTED_SLOTS.items()
}


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


def _fit_card_into_rect(
    card_img: Image.Image,
    rect: tuple[int, int, int, int],
) -> tuple[Image.Image, tuple[int, int]]:
    """Scale a card to fit inside rect without distorting it, then centre it there.

    Card art is not all one resolution (700x975, 700x977 and 700x978 all occur), so the card
    is contained within the slot rather than stretched to fill it, and the leftover space is
    split evenly. rect is at native 1500 scale.
    """
    left, top, right, bottom = [value * SCALE for value in rect]
    box_width, box_height = right - left, bottom - top

    scale = min(box_width / card_img.width, box_height / card_img.height)
    width = max(1, round(card_img.width * scale))
    height = max(1, round(card_img.height * scale))

    resized = card_img.resize((width, height), Image.LANCZOS)
    offset = (
        left + (box_width - width) // 2,
        top + (box_height - height) // 2,
    )
    return resized, offset


def _paste_card(
    board: Image.Image,
    card_instance: Optional[CardInstance],
    rect: tuple[int, int, int, int],
) -> None:
    """Paste a card image onto the board, centred in rect (native 1500 scale)."""
    if card_instance is None:
        return

    if card_instance.face_up and card_instance.card.image:
        try:
            card_img = _load_card_image(card_instance.card.image)
        except Exception:
            card_img = _get_card_back()
    else:
        card_img = _get_card_back()

    fitted, offset = _fit_card_into_rect(card_img, rect)
    board.paste(fitted, offset, fitted)


def _paste_card_back(
    board: Image.Image,
    rect: tuple[int, int, int, int],
) -> None:
    """Paste a card back image centred in rect (native scale)."""
    fitted, offset = _fit_card_into_rect(_get_card_back(), rect)
    board.paste(fitted, offset, fitted)


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


def compose_board_image(
    game_state: GameState,
    perspective: Chronos,
) -> Image.Image:
    """Compose the full game board as a 4500x4500 RGB image."""
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

    return rgb_board


def render_board_image(
    game_state: GameState,
    perspective: Chronos,
) -> discord.File:
    """Render the full game board as a 4500x4500 JPEG."""
    return save_image_for_discord(compose_board_image(game_state, perspective), 'board.jpg')


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


# ---------------------------------------------------------------------------
# Event-loop-friendly wrappers
# ---------------------------------------------------------------------------


async def render_board_image_off_thread(
    game_state: GameState,
    perspective: Chronos,
) -> discord.File:
    """Run render_board_image in a worker thread so the event loop stays responsive."""
    return await asyncio.to_thread(render_board_image, game_state, perspective)


async def generate_zone_messages_off_thread(
    game_state: GameState,
    player_names: dict[int, str],
) -> list[tuple[str, Optional[discord.File]]]:
    """Run generate_zone_messages in a worker thread so the event loop stays responsive."""
    return await asyncio.to_thread(generate_zone_messages, game_state, player_names)
