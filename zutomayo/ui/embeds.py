from pathlib import Path
import discord
from PIL import Image
from zutomayo.ui.image_utils import save_image_for_discord
from constants import CHRONOS_SIZE, NIGHT_END
from zutomayo.enums.card_type import CardType
from zutomayo.enums.chronos import Chronos
from zutomayo.enums.phase import Phase
from zutomayo.models.card import Card
from zutomayo.models.card_instance import CardInstance
from zutomayo.models.game_state import GameState
from zutomayo.models.player import Player


CARD_TYPE_LABEL = {
    CardType.CHARACTER: 'Character',
    CardType.ENCHANT: 'Enchant',
    CardType.AREA_ENCHANT: 'Area Enchant',
}

ATTRIBUTE_EN = {
    'DARKNESS': 'Darkness',
    'FLAME': 'Flame',
    'ELECTRICITY': 'Electricity',
    'WIND': 'Wind',
    'CHAOS': 'Chaos',
}

ATTRIBUTE_JP = {
    'DARKNESS': '闇',
    'FLAME': '炎',
    'ELECTRICITY': '電気',
    'WIND': '風',
    'CHAOS': 'カオス',
}

CARD_TYPE_JP = {
    CardType.CHARACTER: 'キャラクター',
    CardType.ENCHANT: 'エンチャント',
    CardType.AREA_ENCHANT: 'エリアエンチャント',
}

PACK_NAME = {
    1: 'THE WORLD IS CHANGING',
    2: 'ALL ALONG THE WATCHTOWER',
    3: 'OFF MINOR',
    4: 'FANTASY IS REALITY',
}


def card_short_description(card_instance: CardInstance) -> str:
    card = card_instance.card
    attr_en = ATTRIBUTE_EN.get(card.attribute.value, card.attribute.value)
    attr_jp = ATTRIBUTE_JP.get(card.attribute.value, '')
    type_en = CARD_TYPE_LABEL.get(card.card_type, '')
    type_jp = CARD_TYPE_JP.get(card.card_type, '')
    pack_name = PACK_NAME.get(card.pack, '')

    parts = [
        f'**{card.name}** [{card.name_jp}]',
        card.rarity.value,
        f'{type_en} [{type_jp}]',
        f'{attr_en} [{attr_jp}]',
        f'Clock [時計]: {card.clock}',
    ]
    if card.card_type == CardType.CHARACTER:
        parts.append(f'NIGHT: {card.attack_night}')
        parts.append(f'DAY: {card.attack_day}')
    if card.effect_text_jp:
        parts.append(f'<Effect [効果]: {card.effect_text_jp}>')
    parts.append(f'Cost: {_stars(card.power_cost)}')
    parts.append(f'STP: {_stars(card.send_to_power)}')
    parts.append(f'{pack_name} {card.id:03d}/104')

    return ' | '.join(parts)


def _stars(count: int) -> str:
    return '★' * count if count > 0 else '-'


def card_detail_description(card_instance: CardInstance) -> str:
    card = card_instance.card
    attr_en = ATTRIBUTE_EN.get(card.attribute.value, card.attribute.value)
    attr_jp = ATTRIBUTE_JP.get(card.attribute.value, '')
    type_en = CARD_TYPE_LABEL.get(card.card_type, '')
    type_jp = CARD_TYPE_JP.get(card.card_type, '')
    pack = PACK_NAME.get(card.pack, str(card.pack))

    rarity = card.rarity.value

    lines = [
        f'**{card.name} [{card.name_jp}]**',
        f'**Rarity [レアリティ]:** {rarity}',
        f'**Attribute [属性]:** {attr_en} [{attr_jp}]',
        f'**Type [種類]:** {type_en} [{type_jp}]',
        f'**Clock [時計]:** {card.clock}',
    ]
    if card.attack_day:
        lines.append(f'**Day:** {card.attack_day}')
    if card.attack_night:
        lines.append(f'**Night:** {card.attack_night}')
    lines.append(f'**Power Cost [パワーコスト]:** {_stars(card.power_cost)}')
    lines.append(f'**Send to Power [センドトゥパワー]:** {_stars(card.send_to_power)}')
    if card.effect_text:
        lines.append(f'**Effect [効果]:** {card.effect_text} [{card.effect_text_jp}]')
    lines.append(f'**Pack:** {pack}')
    lines.append(f'**Number:** {card.id:03d}')
    return '\n'.join(lines)


def build_hand_embed(player: Player) -> discord.Embed:
    embed = discord.Embed(
        title='Your Hand',
        color=discord.Color.blue(),
    )
    if not player.hand:
        embed.description = 'Your hand is empty.'
        return embed

    lines = []
    for card_instance in player.hand:
        lines.append(card_detail_description(card_instance))
    embed.description = '\n\n'.join(lines)
    return embed


def build_deck_reveal_embed(player_name: str, deck: list[CardInstance]) -> discord.Embed:
    embed = discord.Embed(
        title=f'{player_name} \u2014 Deck Reveal [デッキ公開]',
        color=discord.Color.orange(),
    )
    lines = []
    for i, card_instance in enumerate(deck, 1):
        card = card_instance.card
        pack_name = PACK_NAME.get(card.pack, '')
        lines.append(f'{i}. [{card.rarity.value}] {card.name} [{card.name_jp}] | {pack_name} - {card.id:03d}/104')
    embed.description = '\n'.join(lines)
    return embed


def build_deck_list_embed(title: str, cards: list[Card]) -> discord.Embed:
    """Build a deck listing embed from Card objects (outside game context)."""
    embed = discord.Embed(
        title=title,
        color=discord.Color.orange(),
    )
    lines = []
    for i, card in enumerate(cards, 1):
        pack_name = PACK_NAME.get(card.pack, '')
        lines.append(f'{i}. [{card.rarity.value}] {card.name} [{card.name_jp}] | {pack_name} - {card.id:03d}/104')
    embed.description = '\n'.join(lines)
    return embed


def build_field_embed(game_state: GameState, player_names: dict[int, str] = None) -> discord.Embed:
    player_0 = game_state.players[0]
    player_1 = game_state.players[1]
    day_night = game_state.day_night
    day_night_label = 'NIGHT [夜]' if day_night == Chronos.NIGHT else 'DAY [昼]'

    name_0 = (player_names or {}).get(0, player_0.name)
    name_1 = (player_names or {}).get(1, player_1.name)

    embed = discord.Embed(
        title=f'ZUTOMAYO CARD \u2014 TURN {game_state.turn}',
        color=discord.Color.dark_purple() if day_night == Chronos.NIGHT else discord.Color.gold(),
    )

    # Chronos
    chronos_bar = _build_chronos_bar(game_state.chronos)
    embed.add_field(
        name=f'Chronos ({day_night_label})',
        value=chronos_bar,
        inline=False,
    )

    # Player 0
    player_0_side = '(Night [夜])' if player_0.side == Chronos.NIGHT else '(Day [昼])'
    embed.add_field(
        name=f'{player_0_side}: {name_0}',
        value=f'HP: {player_0.hp} | Power: {player_0.total_power} | Deck: {len(player_0.deck)} | Hand: {len(player_0.hand)}',
        inline=False,
    )

    # Player 1
    player_1_side = '(Night [夜])' if player_1.side == Chronos.NIGHT else '(Day [昼])'
    embed.add_field(
        name=f'{player_1_side}: {name_1}',
        value=f'HP: {player_1.hp} | Power: {player_1.total_power} | Deck: {len(player_1.deck)} | Hand: {len(player_1.hand)}',
        inline=False,
    )

    # Phase label
    phase_labels = {
        Phase.SETUP: 'Setup',
        Phase.SET_CARDS: 'Set Cards',
        Phase.REVEAL: 'Reveal',
        Phase.ADVANCE_CHRONOS: 'Advance Chronos',
        Phase.CHARACTER_SWAP: 'Character Swap',
        Phase.AREA_ENCHANT_SWAP: 'Area Enchant Swap',
        Phase.PROCESS_EFFECTS: 'Process Effects',
        Phase.BATTLE: 'Battle',
        Phase.TURN_END_EFFECTS: 'Turn End Effects',
        Phase.END_TURN: 'End Turn',
    }
    phase_name = phase_labels.get(game_state.current_phase, str(game_state.current_phase))
    embed.set_footer(text=f'Phase: {phase_name}')

    return embed


def _chronos_emoji(i: int, position: int) -> str:
    if i == position:
        return '\U0001f534'   # Red = current
    elif i <= NIGHT_END:
        return '\U0001f535'   # Blue = night
    else:
        return '\U0001f7e0'   # Orange = day


def _build_chronos_bar(position: int) -> str:
    chronos_emoji = _chronos_emoji
    # Top row: night (0-8), left to right
    top = ''.join(chronos_emoji(i, position) for i in range(NIGHT_END + 1))
    # Bottom row: day (9-17), right to left (counter-clockwise)
    bottom = ''.join(chronos_emoji(i, position) for i in range(CHRONOS_SIZE - 1, NIGHT_END, -1))
    return f'{top}\n{bottom}'


def build_battle_result_embed(
    battle_result: dict,
    game_state: GameState,
    player_names: dict[int, str] = None,
) -> discord.Embed:
    player_0 = game_state.players[0]
    player_1 = game_state.players[1]
    name_0 = (player_names or {}).get(0, player_0.name)
    name_1 = (player_names or {}).get(1, player_1.name)

    winner = battle_result['winner']
    if winner == 0:
        title = f'Last Round Result: {name_0} WON'
        color = discord.Color.green()
    elif winner == 1:
        title = f'Last Round Result: {name_1} WON'
        color = discord.Color.green()
    else:
        title = 'Last Round Result: DRAW'
        color = discord.Color.greyple()

    embed = discord.Embed(title=title, color=color)

    embed.add_field(
        name=name_0,
        value=f'ATK: {battle_result["player_0_attack"]}\nDamage taken: {battle_result["damage_to_0"]}\nHP: {player_0.hp}',
        inline=True,
    )
    embed.add_field(
        name=name_1,
        value=f'ATK: {battle_result["player_1_attack"]}\nDamage taken: {battle_result["damage_to_1"]}\nHP: {player_1.hp}',
        inline=True,
    )

    return embed


def build_game_over_embed(game_state: GameState, player_names: dict[int, str] = None) -> discord.Embed:
    from zutomayo.enums.result import Result

    name_0 = (player_names or {}).get(0, game_state.players[0].name)
    name_1 = (player_names or {}).get(1, game_state.players[1].name)

    if game_state.result == Result.PLAYER_1_WIN:
        winner_name = name_0
    elif game_state.result == Result.PLAYER_2_WIN:
        winner_name = name_1
    else:
        winner_name = 'Nobody'

    embed = discord.Embed(
        title=f'GAME COMPLETE \u2014 {winner_name} WINS!',
    )
    embed.add_field(name=name_0, value=f'HP: {game_state.players[0].hp}', inline=True)
    embed.add_field(name=name_1, value=f'HP: {game_state.players[1].hp}', inline=True)
    return embed


def build_effect_resolution_embed(
    player_name: str,
    resolved: list[CardInstance],
    skipped_cost: list[CardInstance],
) -> discord.Embed:
    """Build an embed summarizing a player's effect resolution results."""
    embed = discord.Embed(
        title=f'{player_name} \u2014 Effect Resolution [効果の処理結果]',
        color=discord.Color.teal(),
    )

    if resolved:
        lines = []
        for i, card_instance in enumerate(resolved, 1):
            card = card_instance.card
            type_label = CARD_TYPE_LABEL.get(card.card_type, '')
            lines.append(f'{i}. **{card.name}** [{card.name_jp}] ({type_label})\n\u00a0\u00a0\u00a0Effect [効果]: {card.effect_text} [{card.effect_text_jp}]')
        embed.add_field(
            name='Resolved Effects [処理された効果]',
            value='\n'.join(lines),
            inline=False,
        )
    else:
        embed.add_field(
            name='Resolved Effects [処理された効果]',
            value='*None*',
            inline=False,
        )

    if skipped_cost:
        lines = []
        for card_instance in skipped_cost:
            card = card_instance.card
            type_label = CARD_TYPE_LABEL.get(card.card_type, '')
            lines.append(f'- **{card.name}** [{card.name_jp}] ({type_label}) \u2014 insufficient power')
        embed.add_field(
            name='Skipped (Insufficient Power) [パワー不足でスキップ]',
            value='\n'.join(lines),
            inline=False,
        )

    return embed


# ---------------------------------------------------------------------------
# Deck card image helpers
# ---------------------------------------------------------------------------


_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def create_deck_grid_image(
    cards: list,
    columns: int = 5,
    padding: int = 10,
    filename: str = 'deck.webp',
) -> discord.File | None:
    """
    Combine card images into a single grid image.

    Arranges cards in rows of *columns* (default 5) with *padding* pixels
    between them. For a 20-card deck this produces a 4x5 grid.

    Returns a single ``discord.File`` ready to send, or ``None`` if no
    valid card images were found. Each call creates a fresh File object.
    """
    image_paths: list[Path] = []
    for item in cards:
        card = item.card if isinstance(item, CardInstance) else item
        if not card.image:
            continue
        image_path = _PROJECT_ROOT / card.image
        if image_path.is_file():
            image_paths.append(image_path)

    if not image_paths:
        return None
    
    sample = Image.open(image_paths[0])
    card_w, card_h = sample.size
    sample.close()

    rows = -(-len(image_paths) // columns)  # ceil division
    grid_w = columns * card_w + (columns - 1) * padding
    grid_h = rows * card_h + (rows - 1) * padding
    grid = Image.new('RGBA', (grid_w, grid_h), (0, 0, 0, 0))

    for idx, path in enumerate(image_paths):
        col = idx % columns
        row = idx // columns
        x = col * (card_w + padding)
        y = row * (card_h + padding)
        with Image.open(path) as card_img:
            card_img = card_img.resize((card_w, card_h))
            grid.paste(card_img, (x, y))
    
    return save_image_for_discord(grid, filename)


def create_gacha_grid_image(
    cards: list,
    filename: str = 'gacha.webp',
) -> discord.File | None:
    """Animated gacha reveal — single card slides up into view."""
    from zutomayo.ui.animation import Keyframe, Scene

    image_paths: list[Path] = []
    for item in cards:
        card = item.card if isinstance(item, CardInstance) else item
        if not card.image:
            continue
        image_path = _PROJECT_ROOT / card.image
        if image_path.is_file():
            image_paths.append(image_path)

    if not image_paths:
        return None

    first_card = cards[0].card if isinstance(cards[0], CardInstance) else cards[0]
    pack_path = _PROJECT_ROOT / f'zutomayo/images/pack-{first_card.pack}.png'

    card_images: list[Image.Image] = []
    for path in image_paths:
        with Image.open(path) as img:
            card_images.append(img.copy())

    card_w, card_h = card_images[0].size
    num_cards = len(card_images)
    
    pack_img = Image.open(pack_path).convert('RGBA')
    pack_aspect = pack_img.width / pack_img.height
    pack_w = round(card_h * pack_aspect)

    row_padding = 20
    row_width = num_cards * card_w + (num_cards - 1) * row_padding

    scene_w = row_width + 50
    scene_h = max(card_w, card_h) + 200

    cx = scene_w // 2
    cy = scene_h // 2

    # Timeline:
    #   0-15%:  Phase 0 — pack slides up into view
    #   15-25%: Phase 0 — pack slides back down, revealing card stack behind it
    #   25-75%: Phase 1 — cards peel off the stack one by one
    #   75-100%: Phase 2 — cards slide up in a row one by one
    pack_up_end = 15
    pack_down_end = 25
    peel_duration = 10
    peel_start_pct = pack_down_end
    peel_end_pct = peel_start_pct + num_cards * peel_duration
    reveal_duration = (100 - peel_end_pct) / num_cards

    scene = Scene(
        width=scene_w, height=scene_h,
        fps=12, duration=5.0,
        background=(0x1d, 0x1d, 0x22, 255),
        render_scale=0.5,
    )

    # Pack image
    pack_obj = scene.add(pack_img, z_index=100)
    pack_obj.animate([
        Keyframe(
            pct=0,
            x=cx, y=cy + scene_h,
            width=pack_w, height=card_h,
            visible=True,
        ),
        Keyframe(
            pack_up_end,
            x=cx, y=cy,
            width=round(pack_w * 2.5), height=round(card_h * 2.5),
            easing='ease_out',
        ),
        Keyframe(
            pack_down_end,
            x=cx, y=cy + scene_h,
            visible=False,
            easing='ease_in',
        ),
    ])

    # Peel off cards
    for i, img in enumerate(card_images):
        peel_order = num_cards - 1 - i 
        card_peel_start = peel_start_pct + peel_order * peel_duration
        card_peel_end = card_peel_start + peel_duration

        obj = scene.add(img, z_index=i)
        obj.animate([
            Keyframe(
                pct=0, 
                x=cx, 
                y=cy, 
                rotate_z=0, 
                visible=False
            ),
            Keyframe(
                pct=pack_up_end, 
                x=cx, 
                y=cy, 
                rotate_z=0, 
                visible=True
            ),
            Keyframe(
                pct=card_peel_start, 
                x=cx, 
                y=cy, 
                rotate_z=0
            ),
            Keyframe(
                pct=card_peel_end,
                x=cx + scene_w, y=cy + scene_h // 2,
                rotate_z=45, easing='ease_in',
            ),
            Keyframe(
                pct=peel_end_pct,
                x=cx + scene_w, y=cy + scene_h,
                visible=False,
            ),
        ])

    # Reveal cards in a row
    row_left = (scene_w - row_width) // 2

    for i, img in enumerate(card_images):
        target_x = row_left + i * (card_w + row_padding) + card_w // 2
        target_y = cy

        reveal_start = peel_end_pct + i * reveal_duration
        reveal_end = min(reveal_start + reveal_duration, 99.9)

        obj = scene.add(img, z_index=num_cards + i)
        obj.animate([
            Keyframe(
                pct=reveal_start,
                visible=False,
                x=target_x, y=target_y + scene_h,
                opacity=0.2,
            ),
            Keyframe(
                pct=reveal_start + 0.1,
                visible=True,
                x=target_x, y=target_y + scene_h,
                opacity=1,
            ),
            Keyframe(
                reveal_end,
                x=target_x, y=target_y,
                easing='ease_out',
            ),
        ])

    return scene.render_to_file(filename='gacha.gif')


def create_hand_image(hand: list[CardInstance]) -> discord.File | None:
    """Create a single-row image of the cards in a player's hand."""
    if not hand:
        return None
    return create_deck_grid_image(hand, columns=len(hand))
