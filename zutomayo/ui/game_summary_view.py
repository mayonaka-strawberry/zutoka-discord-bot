"""
Game summary renderer and paginated view for /zutomayo summary.

The renderer first groups the recorded game_events into an intermediate
per-match / per-turn structure, then renders embed pages from it:

- page 0: overview (players, decks, mode, result, duration)
- per match: an opening page (initial hands, redraws, initial battle cards)
  followed by one page per turn (set cards, chronos and day/night, effect
  priority, effect resolution order, battle outcome, HP after)
- TCG: side-deck-swap pages between matches and the series score

A Full Log button attaches the complete event log as a text file. Finished
games are public replays: both players' hands are shown by design.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

import discord

from zutomayo.match.agents import solo_opponent_label

log = logging.getLogger(__name__)

EMBED_DESCRIPTION_LIMIT = 4096
PAGE_CHUNK_LIMIT = 3800


def _card_label(card_key: Optional[list], card_index: dict) -> str:
    if card_key is None:
        return 'none'
    pack, card_id = card_key[0], card_key[1]
    identity = f'{pack:02d}-{card_id:03d}'
    card = card_index.get((pack, card_id))
    return f'{card.name} ({identity})' if card is not None else identity


def _cards_line(card_keys_list: list, card_index: dict) -> str:
    if not card_keys_list:
        return 'none'
    return ', '.join(_card_label(card_key, card_index) for card_key in card_keys_list)


@dataclass
class SummaryPage:
    title: str
    description: str


@dataclass
class GameSummary:
    pages: list[SummaryPage] = field(default_factory=list)
    full_log_lines: list[str] = field(default_factory=list)

    def full_log_text(self) -> str:
        return '\n'.join(self.full_log_lines) + '\n'


def _chunk_page(title: str, lines: list[str]) -> list[SummaryPage]:
    """Split one logical page into embed-sized pages."""
    pages: list[SummaryPage] = []
    current: list[str] = []
    current_length = 0
    for line in lines:
        if current and current_length + len(line) + 1 > PAGE_CHUNK_LIMIT:
            pages.append(SummaryPage(title if not pages else f'{title} (continued)', '\n'.join(current)))
            current, current_length = [], 0
        current.append(line)
        current_length += len(line) + 1
    if current:
        pages.append(SummaryPage(title if not pages else f'{title} (continued)', '\n'.join(current)))
    return pages


def _overview_lines(game_row: dict, player_names: dict[int, str]) -> list[str]:
    manifest = game_row.get('manifest') or {}
    deck_names = manifest.get('player_deck_names') or {}
    if game_row.get('is_tcg'):
        mode_label = f'TCG best of {game_row.get("best_of")}'
    elif game_row.get('is_solo'):
        solo_opponent = game_row.get('solo_difficulty', 'normal')
        mode_label = f'solo ({solo_opponent_label(solo_opponent)})'
    else:
        mode_label = 'standard'

    lines = [
        f'**Game:** `{game_row["game_id"]}`',
        f'**Mode:** {mode_label}',
        f'**Status:** {game_row["status"]}',
    ]
    for index in range(2):
        deck_name = deck_names.get(str(index)) or 'unnamed deck'
        lines.append(f'**Player {index + 1}:** {player_names.get(index, "?")} — deck: {deck_name}')

    winner_index = game_row.get('winner_index')
    if winner_index is not None:
        lines.append(f'**Winner:** {player_names.get(winner_index, "?")}')
    result_summary = game_row.get('result_summary') or {}
    if 'series_score' in result_summary:
        score = result_summary['series_score']
        lines.append(f'**Series score:** {score[0]} - {score[1]}')
    elif 'result' in result_summary:
        lines.append(f'**Result:** {result_summary["result"]} in {result_summary.get("turns", "?")} turn(s)')

    created_at = game_row.get('created_at')
    ended_at = game_row.get('ended_at')
    if created_at is not None:
        lines.append(f'**Started:** {created_at:%Y-%m-%d %H:%M} UTC')
    if created_at is not None and ended_at is not None:
        duration_minutes = max(0, int((ended_at - created_at).total_seconds() // 60))
        lines.append(f'**Duration:** {duration_minutes} minute(s)')
    return lines


def _describe_decision_line(payload: dict, player_names: dict[int, str]) -> str:
    player = player_names.get(payload.get('player_index'), '?')
    label = payload.get('purpose') or payload.get('kind') or 'decision'
    if payload.get('payload_type') == 'timeout':
        return f'{player} — {label}: timed out'
    if 'action' in payload or 'card_keys' in payload:
        # engine_alpha decision shape: one action, optionally labeled.
        if 'card_keys' in payload:
            keys = payload['card_keys'] or {}
            chosen_text = f'{len(keys.get("removed", []))} out, {len(keys.get("added", []))} in'
        elif payload.get('chosen_label'):
            description = payload.get('chosen_description')
            chosen_text = payload['chosen_label'] + (f' {description}' if description else '')
        else:
            chosen_text = str(payload['action'])
        suffix = ' (timed out)' if payload.get('timed_out') else ''
        return f'{player} — {label}: {chosen_text}{suffix}'
    chosen = payload.get('chosen') or []
    if chosen and isinstance(chosen[0], dict):
        # TCG switch payloads carry removed/added key lists.
        parts = []
        for entry in chosen:
            removed = entry.get('removed') or []
            added = entry.get('added') or []
            parts.append(f'{len(removed)} out, {len(added)} in')
        chosen_text = '; '.join(parts)
    else:
        chosen_text = ', '.join(str(choice) for choice in chosen) if chosen else 'no selection'
    return f'{player} — {label}: {chosen_text}'


def _match_opening_lines(
    match_events: list[dict], player_names: dict[int, str], card_index: dict,
) -> list[str]:
    lines: list[str] = []
    for event in match_events:
        payload = event['payload']
        if event['event_type'] == 'initial_hand':
            player = player_names.get(payload['player_index'], '?')
            lines.append(f'**{player} opening hand:** {_cards_line(payload["cards"], card_index)}')
        elif event['event_type'] == 'redraw':
            player = player_names.get(payload['player_index'], '?')
            if 'count' in payload:
                lines.append(f'**{player} redraw:** redrew {payload["count"]} card(s)')
            else:
                lines.append(
                    f'**{player} redraw:** returned {_cards_line(payload["discarded"], card_index)}; '
                    f'drew {_cards_line(payload["drawn"], card_index)}'
                )
        elif event['event_type'] == 'initial_battle_card':
            player = player_names.get(payload['player_index'], '?')
            lines.append(f'**{player} initial battle card:** {_card_label(payload["card"], card_index)}')
    return lines


def _turn_lines(
    turn_events: list[dict], player_names: dict[int, str], card_index: dict,
) -> list[str]:
    lines: list[str] = []
    for event in turn_events:
        event_type = event['event_type']
        payload = event['payload']
        if event_type == 'decision_made':
            lines.append(f'- {_describe_decision_line(payload, player_names)}')
        elif event_type == 'effect_priority_determined':
            player = player_names.get(payload['priority_player_index'], '?')
            lines.append(
                f'- Chronos {payload["chronos"]} ({payload["day_night"]}) — '
                f'effect priority: **{player}**'
            )
        elif event_type == 'effect_order_chosen':
            player = player_names.get(payload['player_index'], '?')
            ordered = ' -> '.join(
                f'{entry["name"]} ({entry["card"][0]:02d}-{entry["card"][1]:03d})'
                for entry in payload['ordered']
            )
            source = ' (single effect)' if payload.get('source') == 'single' else ''
            lines.append(f'- {player} effect order{source}: {ordered}')
        elif event_type == 'effect_resolved':
            player = player_names.get(payload['player_index'], '?')
            if 'order_index' in payload:
                prefix = f'  {payload["order_index"] + 1}.'
            else:
                prefix = '  -'
            lines.append(f'{prefix} {player} resolved {_card_label(payload["card"], card_index)}')
        elif event_type == 'effect_skipped_cost':
            player = player_names.get(payload['player_index'], '?')
            if 'order_index' in payload:
                prefix = f'  {payload["order_index"] + 1}.'
            else:
                prefix = '  -'
            lines.append(f'{prefix} {player} could not pay for {_card_label(payload["card"], card_index)}')
        elif event_type == 'battle_result':
            if 'player_0_attack' in payload:
                # engine_alpha shape: flat attacks, single damage value.
                winner_index = payload.get('winner')
                if winner_index in (None, -1):
                    outcome = 'draw'
                else:
                    outcome = (
                        f'**{player_names.get(winner_index, "?")}** wins, '
                        f'{payload.get("damage", 0)} damage'
                    )
                lines.append(
                    f'- Battle: {player_names.get(0, "?")} {payload["player_0_attack"]} vs '
                    f'{player_names.get(1, "?")} {payload["player_1_attack"]} — {outcome}'
                )
            else:
                attacks = payload['attacks']
                damage = payload['damage']
                hp_after = payload['hp_after']
                winner_index = payload.get('winner_index')
                if winner_index is None:
                    outcome = 'draw'
                else:
                    dealt = damage['1'] if winner_index == 0 else damage['0']
                    outcome = f'**{player_names.get(winner_index, "?")}** wins, {dealt} damage'
                lines.append(
                    f'- Battle: {player_names.get(0, "?")} {attacks["0"]} vs '
                    f'{player_names.get(1, "?")} {attacks["1"]} — {outcome}'
                )
                lines.append(
                    f'- HP after battle: {player_names.get(0, "?")} {hp_after["0"]} / '
                    f'{player_names.get(1, "?")} {hp_after["1"]}'
                )
        elif event_type == 'state_snapshot':
            players = payload['players']
            lines.append(
                f'- End of turn: HP {players[0]["hp"]} / {players[1]["hp"]}, '
                f'hands {len(players[0]["hand"])} / {len(players[1]["hand"])}, '
                f'decks {players[0]["deck_count"]} / {players[1]["deck_count"]}'
            )
        elif event_type == 'game_end':
            if 'winner' in payload:
                winner_index = payload['winner']
                winner = player_names.get(winner_index, 'draw') if winner_index in (0, 1) else 'draw'
                lines.append(f'- **Game end** (winner: {winner})')
            else:
                winner_index = payload.get('winner_index')
                winner = player_names.get(winner_index, 'draw') if winner_index is not None else 'draw'
                lines.append(f'- **Game end:** {payload["result"]} (winner: {winner})')
        elif event_type == 'forfeit':
            player = player_names.get(payload.get('player_index'), '?')
            lines.append(f'- **Forfeit** by {player}')
        elif event_type == 'game_saved':
            lines.append('- Game saved')
        elif event_type == 'game_resumed':
            lines.append('- Game resumed')
    return lines


def _full_log_lines(events: list[dict], player_names: dict[int, str]) -> list[str]:
    lines = []
    for event in events:
        context = f'match {event.get("match_number")}, turn {event.get("turn")}'
        phase = event.get('phase')
        if phase:
            context += f', {phase}'
        lines.append(f'[{event["event_index"]:04d}] ({context}) {event["event_type"]}: {event["payload"]}')
    header = [f'Player {index + 1}: {name}' for index, name in sorted(player_names.items())]
    return header + lines


def build_game_summary(
    game_row: dict,
    player_names: dict[int, str],
    events: list[dict],
    card_index: dict,
) -> GameSummary:
    summary = GameSummary()
    summary.pages.extend(_chunk_page(
        f'Game Summary — {game_row["game_id"]}',
        _overview_lines(game_row, player_names),
    ))

    is_tcg = bool(game_row.get('is_tcg'))

    # Group events by match, keeping stream order.
    events_by_match: dict[Any, list[dict]] = {}
    for event in events:
        events_by_match.setdefault(event.get('match_number'), []).append(event)

    for match_number in sorted(events_by_match, key=lambda value: (value is None, value)):
        match_events = events_by_match[match_number]
        match_prefix = f'Match {match_number} — ' if is_tcg else ''

        opening_types = ('initial_hand', 'redraw', 'initial_battle_card')
        opening_events = [event for event in match_events if event['event_type'] in opening_types]
        if opening_events:
            summary.pages.extend(_chunk_page(
                f'{match_prefix}Opening Hands',
                _match_opening_lines(opening_events, player_names, card_index),
            ))

        swap_lines = []
        for event in match_events:
            if event['event_type'] == 'side_deck_swap':
                payload = event['payload']
                player = player_names.get(payload['player_index'], '?')
                swap_lines.append(
                    f'**{player}** moved out {_cards_line(payload["removed"], card_index)}; '
                    f'moved in {_cards_line(payload["added"], card_index)}'
                )
            elif event['event_type'] == 'side_choice':
                from zutomayo.match.decisions import SIDE_LABEL_DAY, SIDE_LABEL_NIGHT

                payload = event['payload']
                chooser = player_names.get(payload['chooser_index'], '?')
                chosen_label = SIDE_LABEL_NIGHT if payload['chose_night'] else SIDE_LABEL_DAY
                night_player = player_names.get(payload['night_player'], '?')
                day_player = player_names.get(1 - payload['night_player'], '?')
                swap_lines.append(
                    f'**{chooser}** lost the match and chose {chosen_label} for the next one; '
                    f'**{night_player}** plays {SIDE_LABEL_NIGHT}, **{day_player}** plays {SIDE_LABEL_DAY}'
                )
            elif event['event_type'] == 'match_result':
                payload = event['payload']
                score = payload['series_score']
                swap_lines.insert(0, (
                    f'**{player_names.get(payload["winner_index"], "?")}** won match '
                    f'{payload["match_number"]} — series score {score[0]} - {score[1]}'
                ))

        turn_numbers = sorted({
            event['turn'] for event in match_events
            if event.get('turn') is not None and event['turn'] >= 1
        })
        for turn in turn_numbers:
            turn_events = [event for event in match_events if event.get('turn') == turn]
            lines = _turn_lines(turn_events, player_names, card_index)
            if lines:
                summary.pages.extend(_chunk_page(f'{match_prefix}Turn {turn}', lines))

        if swap_lines:
            title = f'{match_prefix}Result and Side Deck Swaps' if is_tcg else f'{match_prefix}Result'
            summary.pages.extend(_chunk_page(title, swap_lines))

    summary.full_log_lines = _full_log_lines(events, player_names)
    return summary


class GameSummaryView(discord.ui.View):
    """Previous/Next pagination over the summary pages plus a Full Log attachment."""

    def __init__(self, game_id: str, summary: GameSummary) -> None:
        super().__init__(timeout=750)
        self.game_id = game_id
        self.summary = summary
        self.page = 0
        self.message: Optional[discord.Message] = None
        self._rebuild_buttons()

    def build_embed(self) -> discord.Embed:
        page = self.summary.pages[self.page]
        embed = discord.Embed(
            title=page.title,
            description=page.description[:EMBED_DESCRIPTION_LIMIT],
            color=discord.Color.blurple(),
        )
        embed.set_footer(text=f'Page {self.page + 1}/{len(self.summary.pages)} — {self.game_id}')
        return embed

    def _rebuild_buttons(self) -> None:
        self.clear_items()
        if len(self.summary.pages) > 1:
            previous_button = discord.ui.Button(
                label='<< Previous', style=discord.ButtonStyle.grey, disabled=(self.page == 0),
            )
            previous_button.callback = self._previous_page
            self.add_item(previous_button)

            next_button = discord.ui.Button(
                label='Next >>', style=discord.ButtonStyle.grey,
                disabled=(self.page >= len(self.summary.pages) - 1),
            )
            next_button.callback = self._next_page
            self.add_item(next_button)

        full_log_button = discord.ui.Button(label='Full Log', style=discord.ButtonStyle.primary)
        full_log_button.callback = self._send_full_log
        self.add_item(full_log_button)

    async def _previous_page(self, interaction: discord.Interaction) -> None:
        self.page = max(0, self.page - 1)
        self._rebuild_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def _next_page(self, interaction: discord.Interaction) -> None:
        self.page = min(len(self.summary.pages) - 1, self.page + 1)
        self._rebuild_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def _send_full_log(self, interaction: discord.Interaction) -> None:
        log_file = discord.File(
            io.BytesIO(self.summary.full_log_text().encode('utf-8')),
            filename=f'{self.game_id}-full-log.txt',
        )
        await interaction.response.send_message(
            file=log_file, ephemeral=True, allowed_mentions=discord.AllowedMentions.none(),
        )
