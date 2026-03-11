import json
from pathlib import Path
from zutomayo.enums.attribute import Attribute
from zutomayo.enums.card_type import CardType
from zutomayo.enums.rarity import Rarity
from zutomayo.enums.song import Song
from zutomayo.models.card import Card


def load_cards() -> list[Card]:
    cards_path = Path(__file__).parent / 'cards.json'
    with open(cards_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    cards = []
    for entry in data['cards']:
        card = Card(
            pack=entry['pack'],
            id=entry['id'],
            name=entry['name'],
            name_jp=entry['name_jp'],
            image=entry['image'],
            clock=entry['clock'],
            card_type=CardType[entry['card_type']],
            attribute=Attribute[entry['attribute']],
            song=Song[entry['song']],
            rarity=Rarity[entry['rarity']],
            attack_day=entry.get('attack_day', 0),
            attack_night=entry.get('attack_night', 0),
            power_cost=entry.get('power_cost', 0),
            send_to_power=entry.get('send_to_power', 0),
            effect=entry.get('effect', ''),
            effect_text=entry.get('effect_text', ''),
            effect_text_jp=entry.get('effect_text_jp', ''),
        )
        cards.append(card)

    return cards
