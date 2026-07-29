"""
Calibration script: the whole board with everything on it at once -- a real card in every
slot for both players, and the chronos coin on all 18 ring positions.

The two focused calibration scripts each check one half of the picture:
scripts/calibrate_board_cards.py fills the card slots but draws no coins, and
scripts/calibrate_chronos.py draws all 18 coins on a bare board. This combines them, which
is the only view that shows whether the cards and the coin ring crowd each other once the
board is fully populated.

Two images are written:

  calibration_output_full.png         no overlay at all -- what the bot would actually
                                      send if every zone were occupied
  calibration_output_full_marked.png  the same board under the full calibration overlay:
                                      green DAY and red NIGHT card rectangles with centre
                                      ticks, each coin's rim and centre crosshair with its
                                      slot index, and the rotational centre and chronos
                                      ring guides. Coins are drawn translucent here so the
                                      printed glyph stays visible underneath.

The overlay is drawn by importing the helpers the two focused scripts already use, so this
image cannot drift from the images it summarises.

The deck slot normally renders a card back (a deck is hidden), so it is deliberately
overwritten with the card face here, the same way calibrate_board_cards.py does, to make
all seven slots checkable.

Run from project root:
python scripts/calibrate_full.py

Output:
scripts/calibration_output_full.png
scripts/calibration_output_full_marked.png
"""

import sys
from pathlib import Path


# Add project root and this directory to path: the project root for the zutomayo and
# engine_alpha packages, and scripts/ for the sibling calibration modules whose overlay
# helpers are reused below.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))


from PIL import Image, ImageDraw

from engine_alpha.battle import CHRONOS_SIZE, NIGHT_END
from engine_alpha.cards import KEY_TO_INDEX
from engine_alpha.state import PH_BATTLE

from zutomayo.data.card_loader import load_cards
from zutomayo.enums.chronos import Chronos
from zutomayo.match.state_view import BoardView, CardView, PlayerView
from zutomayo.models.card import Card
from zutomayo.ui.board_renderer import (
    CHRONOS_CENTERS,
    COIN_DIAMETER,
    DAY_ZONES,
    NIGHT_ZONES,
    compose_board_image,
    paste_chronos_coin,
    _paste_card,
)

# Overlay helpers, reused rather than reimplemented, from the two focused scripts.
from calibrate_board import (
    DAY_COLOUR,
    NIGHT_COLOUR,
    _draw_card_rects,
    _load_label_font as _load_card_font,
)
from calibrate_chronos import (
    COIN_OPACITY,
    _draw_guides,
    _draw_slot_markers,
    _load_label_font as _load_slot_font,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

CARD_KEY = '02-001'


def _resolve_card(card_key: str) -> Card:
    """Look up a card by its 'PP-NNN' display code."""
    pack_text, _, number_text = card_key.partition('-')
    pack, number = int(pack_text), int(number_text)
    for card in load_cards():
        if card.pack == pack and card.id == number:
            return card
    raise SystemExit(f'Card {card_key} not found in the card database.')


def _filled_player_view(index: int, card_view: CardView, side_is_night: bool) -> PlayerView:
    """A player whose every rendered zone holds the same card, face up."""
    return PlayerView(
        index=index,
        name=f'Player {index + 1}',
        hp=100,
        total_power=0,
        side_is_night=side_is_night,
        hand=(card_view,),
        deck=(card_view,),
        power_charger=(card_view,),
        abyss=(card_view,),
        battle_zone=card_view,
        set_zone_a=card_view,
        set_zone_b=card_view,
        set_zone_c=card_view,
    )


def _compose_full_board(board_view: BoardView, card_view: CardView,
                        coin_opacity: int) -> Image.Image:
    """The board with every card slot filled and all 18 chronos coins on the ring."""
    board = compose_board_image(board_view, Chronos.DAY)

    # The deck slot renders a card back by design. Overwrite both deck rects with the card
    # face so every slot is checkable. Which player owns which rect depends on the
    # perspective, but the same card goes in both, so no branching is needed.
    _paste_card(board, card_view, DAY_ZONES['deck'])
    _paste_card(board, card_view, NIGHT_ZONES['deck'])

    # compose_board_image has already drawn the coin for board_view.chronos; re-pasting it
    # is idempotent, so the loop needs no special case. No coin overlaps a card rect, so
    # drawing them after the cards looks the same as drawing them before.
    for slot in range(CHRONOS_SIZE):
        paste_chronos_coin(board, slot, opacity=coin_opacity)

    return board


def _marked(board: Image.Image) -> Image.Image:
    """Composite the card and chronos calibration overlays onto a board."""
    overlay = Image.new('RGBA', board.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # calibrate_chronos._draw_guides covers the rotational centre crosshair as well as the
    # chronos ring, so calibrate_board._draw_centre_guides is deliberately not called too.
    _draw_guides(draw, board.size[0])
    _draw_card_rects(draw, DAY_ZONES, DAY_COLOUR, 'DAY', _load_card_font())
    _draw_card_rects(draw, NIGHT_ZONES, NIGHT_COLOUR, 'NIGHT', _load_card_font())
    _draw_slot_markers(draw, _load_slot_font())

    return Image.alpha_composite(board.convert('RGBA'), overlay).convert('RGB')


def main() -> None:
    card = _resolve_card(CARD_KEY)
    card_view = CardView(
        instance_id=0,
        definition_index=KEY_TO_INDEX[CARD_KEY],
        card=card,
        face_up=True,
    )

    board_view = BoardView(
        turn=1,
        chronos=0,
        is_night=True,
        phase=PH_BATTLE,
        phase_name='Battle',
        players=(
            _filled_player_view(0, card_view, side_is_night=False),
            _filled_player_view(1, card_view, side_is_night=True),
        ),
        winner=-1,
        last_battle_winner=-1,
    )

    print(f'Card {CARD_KEY}: {card.name}')
    print(f'  image {card.image}')
    print(f'Coin diameter: {COIN_DIAMETER} native')
    print()

    print(f"{'zone':<14} {'DAY centre':<14} NIGHT centre")
    for zone_name in DAY_ZONES:
        day = DAY_ZONES[zone_name]
        night = NIGHT_ZONES[zone_name]
        day_centre = ((day[0] + day[2]) // 2, (day[1] + day[3]) // 2)
        night_centre = ((night[0] + night[2]) // 2, (night[1] + night[3]) // 2)
        print(f'{zone_name:<14} {str(day_centre):<14} {night_centre}')

    print()
    print(f"{'slot':<6} {'half':<7} centre")
    for slot in range(CHRONOS_SIZE):
        half = 'night' if slot <= NIGHT_END else 'day'
        print(f'{slot:<6} {half:<7} {CHRONOS_CENTERS[slot]}')

    print()
    clean = _compose_full_board(board_view, card_view, coin_opacity=255)
    clean_path = PROJECT_ROOT / 'scripts' / 'calibration_output_full.png'
    clean.save(clean_path)
    print(f'clean  -> {clean_path}  {clean.size}')

    translucent = _compose_full_board(board_view, card_view, coin_opacity=COIN_OPACITY)
    marked_path = PROJECT_ROOT / 'scripts' / 'calibration_output_full_marked.png'
    _marked(translucent).save(marked_path)
    print(f'marked -> {marked_path}  {translucent.size}')


if __name__ == '__main__':
    main()
