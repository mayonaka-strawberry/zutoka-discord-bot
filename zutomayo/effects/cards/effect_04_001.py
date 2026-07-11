from __future__ import annotations
from typing import TYPE_CHECKING
from zutomayo.effects.card_effect_helpers import reveal_song_characters_for_attack_bonus
from zutomayo.enums.song import Song

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


async def effect_04_001(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
) -> None:
    """Reveal any number of (TAIDADA) characters from your hand, Attack +30 for each."""
    await reveal_song_characters_for_attack_bonus(
        engine, game_state, player_index, card_instance,
        song=Song.TAIDADA, song_label='TAIDADA', bonus_per_card=30,
    )
