from __future__ import annotations
import asyncio
from pathlib import Path
from typing import Optional
import discord
from PIL import Image
from engine_alpha.battle import CHRONOS_SIZE
from zutomayo.ui.card_art import GRID_BACKGROUND, card_back_image, load_card_image
from zutomayo.ui.image_utils import save_image_for_discord
from zutomayo.enums.chronos import Chronos

# Type-hint aliases: the renderer reads duck-typed views (zutomayo.match.state_view).
CardInstance = GameState = Player = object


_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

BOARD_NATIVE = 1500
BOARD_RENDER = 4500
SCALE = BOARD_RENDER // BOARD_NATIVE  # 3

# --- Zone coordinates: native 1500x1500 scale, (left, top, right, bottom); DAY is the bottom half ---


# The box each card is fitted and centred in; card art varies from 700x975 to 700x978.
CARD_WIDTH = 196
CARD_HEIGHT = 274


# The art's measured centre of 180-degree symmetry is (751.1, 748.8), not (750, 750).
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


# Printed slot centres, measured from zutomayo/images/board.png. 'battle' has no printed
# outline: it sits in the centre circle with its top edge 10 px below the midline.
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


# Printed slot outlines, measured from the art. Only scripts/calibrate_board.py and
# scripts/calibrate_full.py read them.
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


# --- Chronos ring: the 18 glyph positions the coin marker sits on ---


# Slots 0..17 run clockwise: night (0-8) on the top half with midnight (4) at top centre,
# day (9-17) on the bottom with noon (13) at bottom centre. Measured from the art by
# scripts/measure_chronos_centers.py (rerun it to check). Day suns: disc centroids.
DAY_CHRONOS_CENTERS = {
    9:  (1104, 812),
    10: (1061, 933),
    11: (981, 1027),
    12: (873, 1090),
    13: (750, 1111),
    14: (627, 1090),
    15: (520, 1027),
    16: (440, 933),
    17: (398, 812),
}


# Night moons: disc centres fitted to each moon's outer limb (new moons 0 and 8: a circle
# through their dashes). Not the day centres reflected: the rings differ by up to 10.7 px.
NIGHT_CHRONOS_CENTERS = {
    0: (399, 677),
    1: (442, 573),
    2: (517, 476),
    3: (624, 413),
    4: (752, 390),
    5: (880, 413),
    6: (986, 476),
    7: (1062, 573),
    8: (1105, 677),
}

CHRONOS_CENTERS = {**DAY_CHRONOS_CENTERS, **NIGHT_CHRONOS_CENTERS}


# Coin diameter at native scale, just under the night moon discs (107 px).
COIN_DIAMETER = 96

# coin.png is a 284 px canvas around a 245 px coin (the margin holds the shadow), so this
# ratio makes COIN_DIAMETER the coin itself.
COIN_CANVAS_RATIO = 284 / 245


# --- Cached assets ---


_board_base: Optional[Image.Image] = None
_coin_img: Optional[Image.Image] = None


def _get_board_base() -> Image.Image:
    global _board_base
    if _board_base is None:
        _board_base = (
            Image.open(_PROJECT_ROOT / 'zutomayo/images/board.png')
            .convert('RGBA')
            .resize((BOARD_RENDER, BOARD_RENDER), Image.LANCZOS)
        )
    return _board_base.copy()


def _get_coin() -> Image.Image:
    """The chronos marker, resized once so the coin itself is COIN_DIAMETER."""
    global _coin_img
    if _coin_img is None:
        size = round(COIN_DIAMETER * SCALE * COIN_CANVAS_RATIO)
        _coin_img = (
            Image.open(_PROJECT_ROOT / 'zutomayo/images/coin.png')
            .convert('RGBA')
            .resize((size, size), Image.LANCZOS)
        )
    return _coin_img


# --- Helpers ---


def _fit_card_into_rect(
    card_img: Image.Image,
    rect: tuple[int, int, int, int],
) -> tuple[Image.Image, tuple[int, int]]:
    """Scale a card to fit `rect` (native scale) without distortion, centred."""
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
            card_img = load_card_image(card_instance.card.image)
        except Exception:
            card_img = card_back_image()
    else:
        card_img = card_back_image()

    fitted, offset = _fit_card_into_rect(card_img, rect)
    board.paste(fitted, offset, fitted)


def _paste_card_back(
    board: Image.Image,
    rect: tuple[int, int, int, int],
) -> None:
    """Paste a card back image centred in rect (native scale)."""
    fitted, offset = _fit_card_into_rect(card_back_image(), rect)
    board.paste(fitted, offset, fitted)


def paste_chronos_coin(
    board: Image.Image,
    chronos: int,
    opacity: int = 255,
) -> None:
    """Paste the coin centred on a chronos slot's glyph. `board` is at render scale;
    `opacity` below 255 is for the calibration script."""
    center_x, center_y = CHRONOS_CENTERS[chronos % CHRONOS_SIZE]
    coin = _get_coin()

    if opacity < 255:
        coin = coin.copy()
        coin.putalpha(coin.getchannel('A').point(lambda value: value * opacity // 255))

    offset = (
        center_x * SCALE - coin.width // 2,
        center_y * SCALE - coin.height // 2,
    )
    board.paste(coin, offset, coin)


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


# --- Main board rendering ---


def compose_board_image(
    game_state: GameState,
    perspective: Chronos,
) -> Image.Image:
    """Compose the full game board as a 4500x4500 RGB image."""
    board = _get_board_base()

    # Drawn before the perspective rotation, so the coin turns with the ring. A board
    # without `chronos` renders no marker.
    chronos = getattr(game_state, 'chronos', None)
    if chronos is not None:
        paste_chronos_coin(board, chronos)

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


# --- Zone strip images (Abyss / Power Charger) ---


def render_zone_strip(
    cards: list[CardInstance],
    label: str,
) -> Optional[discord.File]:
    """Render all cards in a zone as one image, up to 10 per row."""
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
                card_img = load_card_image(card_instance.card.image)
            except Exception:
                card_img = card_back_image()
        else:
            card_img = card_back_image()

        resized = card_img.resize((card_w, card_h), Image.LANCZOS)
        grid.paste(resized, (x, y), resized)

    safe_label = label.replace(' ', '_').lower()
    return save_image_for_discord(grid, f'{safe_label}.jpg', background=GRID_BACKGROUND)


# --- Zone messages ---


def generate_zone_messages(
    game_state: GameState,
    player_names: dict[int, str],
    indices: Optional[set[int]] = None,
) -> list[tuple[str, Optional[discord.File]]]:
    """(label, file or None) for P0 Abyss, P0 Power Charger, P1 Abyss, P1 Power Charger.
    ``indices`` limits which are rendered (None: all). Call once per destination: a
    discord.File is consumed on send."""
    messages: list[tuple[str, Optional[discord.File]]] = []
    for index in range(2):
        player = game_state.players[index]
        name = player_names.get(index, f'Player {index + 1}')

        for position, (label, cards) in enumerate((
            (f'{name} Abyss', player.abyss),
            (f'{name} Power Charger', player.power_charger),
        )):
            if indices is not None and index * 2 + position not in indices:
                continue
            messages.append((label, render_zone_strip(cards, label)))

    return messages


# --- Off-thread wrappers ---


async def render_board_image_off_thread(
    game_state: GameState,
    perspective: Chronos,
) -> discord.File:
    """Run render_board_image in a worker thread so the event loop stays responsive."""
    return await asyncio.to_thread(render_board_image, game_state, perspective)


async def generate_zone_messages_off_thread(
    game_state: GameState,
    player_names: dict[int, str],
    indices: Optional[set[int]] = None,
) -> list[tuple[str, Optional[discord.File]]]:
    """Run generate_zone_messages in a worker thread so the event loop stays responsive."""
    return await asyncio.to_thread(generate_zone_messages, game_state, player_names, indices)
