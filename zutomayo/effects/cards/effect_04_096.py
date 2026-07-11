from __future__ import annotations
from typing import TYPE_CHECKING
from zutomayo.effects.card_effect_helpers import add_damage_reduction_if_battle_zone_song
from zutomayo.enums.song import Song

if TYPE_CHECKING:
    from zutomayo.effects.effect_engine import EffectEngine
    from zutomayo.models.card_instance import CardInstance
    from zutomayo.models.game_state import GameState


async def effect_04_096(
    engine: EffectEngine, game_state: GameState, player_index: int, card_instance: CardInstance,
) -> None:
    """If the card in the Battle Zone is a (Neko Reset) character, reduce damage by 50 when you take damage."""
    await add_damage_reduction_if_battle_zone_song(
        engine, game_state, player_index, card_instance,
        song=Song.NEKO_RESET, song_label='Neko Reset', amount=50,
    )
