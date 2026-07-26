"""
Calibration script: renders the board with a single real card filling every slot, so the
zone geometry can be checked against actual card art instead of coloured outlines.

Unlike scripts/calibrate_board.py this draws no overlay at all -- what you see is what the
bot would send, so any misalignment shows up as a card sitting crooked in its printed slot.

The deck slot normally renders a card back (a deck is hidden), so it is deliberately
overwritten with the card face here to make all seven slots checkable.

Run from project root:
python scripts/calibrate_board_cards.py

Output:
scripts/calibration_output_zanki.png        DAY perspective, board upright
scripts/calibration_output_zanki_night.png  NIGHT perspective, board rotated 180 degrees
"""

import sys
from pathlib import Path


# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


from engine_alpha.cards import KEY_TO_INDEX
from engine_alpha.state import PH_BATTLE

from zutomayo.data.card_loader import load_cards
from zutomayo.enums.chronos import Chronos
from zutomayo.match.state_view import BoardView, CardView, PlayerView
from zutomayo.models.card import Card
from zutomayo.ui.board_renderer import (
    DAY_ZONES,
    NIGHT_ZONES,
    compose_board_image,
    _paste_card,
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
        is_night=False,
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
    print(f'  definition index {card_view.definition_index}')
    print()

    renders = (
        (Chronos.DAY, 'calibration_output_zanki.png'),
        (Chronos.NIGHT, 'calibration_output_zanki_night.png'),
    )
    for perspective, filename in renders:
        board = compose_board_image(board_view, perspective)

        # The deck slot renders a card back by design. Overwrite both deck rects with the
        # card face so every slot is checkable. Which player owns which rect depends on the
        # perspective, but the same card goes in both, so no branching is needed.
        _paste_card(board, card_view, DAY_ZONES['deck'])
        _paste_card(board, card_view, NIGHT_ZONES['deck'])

        out_path = PROJECT_ROOT / 'scripts' / filename
        board.save(out_path)
        print(f'{perspective.name:<6} -> {out_path}  {board.size}')


if __name__ == '__main__':
    main()
