from __future__ import annotations
import io
import json
import os
import random
from pathlib import Path
import discord
from zutomayo.data.rank_songs_data import STUDIO_RECORDINGS


CHECKPOINT_DIRECTORY = Path('zutomayo/progress')

def get_checkpoint_path(user_id: int) -> Path:
    return CHECKPOINT_DIRECTORY / f'checkpoint_{user_id}.json'

def get_full_checkpoint_path(user_id: int) -> Path:
    return CHECKPOINT_DIRECTORY / f'full_checkpoint_{user_id}.json'

INITIAL_RATING = 2000
K_FACTOR = 32
DEFAULT_NUMBER_OF_ROUNDS = 15
MAX_NUMBER_OF_ROUNDS = len(STUDIO_RECORDINGS)
SONGS_PER_PAGE = 15
MAX_TIEBREAKER_ROUNDS = 10

SCORE_PREFER_A = 1.0
SCORE_SLIGHTLY_PREFER_A = 0.75
SCORE_SLIGHTLY_PREFER_B = 0.25
SCORE_PREFER_B = 0.0


def _generate_matchup_list(number_of_rounds: int) -> list[tuple[int, int]]:
    """
    Return a shuffled list of (song_index_a, song_index_b) pairs.

    For full mode (number_of_rounds >= len(STUDIO_RECORDINGS)), all
    C(N, 2) unique pairs are returned so every possible matchup is covered.

    For partial mode, the first number_of_rounds * (len(STUDIO_RECORDINGS) // 2)
    pairs are returned, giving each song approximately number_of_rounds
    appearances. Works correctly for any N (even or odd).
    """
    n = len(STUDIO_RECORDINGS)
    all_pairs: list[tuple[int, int]] = [
        (i, j) for i in range(n) for j in range(i + 1, n)
    ]
    random.shuffle(all_pairs)
    if number_of_rounds >= n:
        return all_pairs
    return all_pairs[:number_of_rounds * (n // 2)]


class RankSongsView(discord.ui.View):
    def __init__(self, number_of_rounds: int = DEFAULT_NUMBER_OF_ROUNDS, user_id: int = 0, checkpoint_prefix: str = 'checkpoint') -> None:
        super().__init__(timeout=None)
        self._user_id: int = user_id
        self._number_of_rounds: int = number_of_rounds
        self._checkpoint_prefix: str = checkpoint_prefix
        self._matchup_list: list[tuple[int, int]] = _generate_matchup_list(number_of_rounds)
        self._elo_ratings: list[float] = [float(INITIAL_RATING)] * len(STUDIO_RECORDINGS)
        self._current_matchup_index: int = 0
        self._ranking_pages: list[list[str]] = []
        self._current_ranking_page: int = 0
        self._tiebreaker_round: int = 0
        self._tiebreaker_champion: int | None = None
        self._tiebreaker_challenger_queue: list[int] = []
        self._tiebreaker_remaining_groups: list[list[int]] = []

    def _get_checkpoint_path(self) -> Path:
        return CHECKPOINT_DIRECTORY / f'{self._checkpoint_prefix}_{self._user_id}.json'

    @classmethod
    def from_checkpoint(cls, data: dict, checkpoint_prefix: str = 'checkpoint') -> RankSongsView:
        instance = cls(number_of_rounds=data['number_of_rounds'], user_id=data['discord_user_id'], checkpoint_prefix=checkpoint_prefix)
        instance._matchup_list = [tuple(pair) for pair in data['matchup_list']]
        instance._current_matchup_index = data['current_matchup_index']
        instance._elo_ratings = data['elo_ratings']
        instance._tiebreaker_round = data['tiebreaker_round']
        instance._tiebreaker_champion = data['tiebreaker_champion']
        instance._tiebreaker_challenger_queue = data['tiebreaker_challenger_queue']
        instance._tiebreaker_remaining_groups = [list(group) for group in data['tiebreaker_remaining_groups']]
        return instance

    def _save_checkpoint(self) -> None:
        os.makedirs(CHECKPOINT_DIRECTORY, exist_ok=True)
        data = {
            'discord_user_id': self._user_id,
            'number_of_rounds': self._number_of_rounds,
            'matchup_list': [list(pair) for pair in self._matchup_list],
            'current_matchup_index': self._current_matchup_index,
            'elo_ratings': self._elo_ratings,
            'tiebreaker_round': self._tiebreaker_round,
            'tiebreaker_champion': self._tiebreaker_champion,
            'tiebreaker_challenger_queue': self._tiebreaker_challenger_queue,
            'tiebreaker_remaining_groups': self._tiebreaker_remaining_groups,
        }
        self._get_checkpoint_path().write_text(json.dumps(data), encoding='utf-8')

    def _delete_checkpoint(self) -> None:
        self._get_checkpoint_path().unlink(missing_ok=True)

    # --- Voting buttons ---

    @discord.ui.button(label='Prefer A', style=discord.ButtonStyle.primary, row=0)
    async def prefer_a(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._process_vote_and_advance(interaction, SCORE_PREFER_A)

    @discord.ui.button(label='Slightly Prefer A', style=discord.ButtonStyle.secondary, row=0)
    async def slightly_prefer_a(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._process_vote_and_advance(interaction, SCORE_SLIGHTLY_PREFER_A)

    @discord.ui.button(label='Slightly Prefer B', style=discord.ButtonStyle.secondary, row=0)
    async def slightly_prefer_b(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._process_vote_and_advance(interaction, SCORE_SLIGHTLY_PREFER_B)

    @discord.ui.button(label='Prefer B', style=discord.ButtonStyle.primary, row=0)
    async def prefer_b(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._process_vote_and_advance(interaction, SCORE_PREFER_B)

    @discord.ui.button(label='Save Progress', style=discord.ButtonStyle.success, row=1)
    async def save_progress(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self._save_checkpoint()
        await interaction.response.send_message('Progress saved!', ephemeral=True)

    @discord.ui.button(label='Save and Quit', style=discord.ButtonStyle.danger, row=1)
    async def quit_session(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self._save_checkpoint()
        self.stop()
        await interaction.response.edit_message(
            content='Session ended. Progress has been saved.', view=None
        )

    # --- Private helpers ---

    def _build_matchup_content(self) -> str:
        song_index_a, song_index_b = self._matchup_list[self._current_matchup_index]
        total_matchups = len(self._matchup_list)
        percentage = round(self._current_matchup_index / total_matchups * 100)
        song_name_a = STUDIO_RECORDINGS[song_index_a]
        song_name_b = STUDIO_RECORDINGS[song_index_b]
        tiebreaker_line = '\nRunning tiebreakers...' if self._tiebreaker_round > 0 else ''
        return f'Progress: {percentage}%{tiebreaker_line}\n\nA: {song_name_a}\nB: {song_name_b}'

    def _update_elo_ratings(
        self, song_index_a: int, song_index_b: int, score_for_a: float
    ) -> None:
        rating_a = self._elo_ratings[song_index_a]
        rating_b = self._elo_ratings[song_index_b]
        expected_a = 1.0 / (1.0 + 10.0 ** ((rating_b - rating_a) / 400.0))
        expected_b = 1.0 - expected_a
        self._elo_ratings[song_index_a] = rating_a + K_FACTOR * (score_for_a - expected_a)
        self._elo_ratings[song_index_b] = rating_b + K_FACTOR * ((1.0 - score_for_a) - expected_b)

    async def _process_vote_and_advance(
        self, interaction: discord.Interaction, score_for_a: float
    ) -> None:
        song_index_a, song_index_b = self._matchup_list[self._current_matchup_index]
        self._update_elo_ratings(song_index_a, song_index_b, score_for_a)
        self._current_matchup_index += 1

        if self._tiebreaker_challenger_queue:
            challenger = self._tiebreaker_challenger_queue.pop(0)
            if score_for_a < 0.5:  # challenger (song B) beat the champion (song A)
                self._tiebreaker_champion = challenger

        if self._current_matchup_index < len(self._matchup_list):
            await interaction.response.edit_message(
                content=self._build_matchup_content(), view=self
            )
        else:
            await self._try_enter_tiebreaker_or_finish(interaction)

    def _find_tied_groups(self) -> list[list[int]]:
        indexed = sorted(
            range(len(self._elo_ratings)),
            key=lambda index: self._elo_ratings[index],
            reverse=True,
        )
        groups: list[list[int]] = []
        current_group: list[int] = [indexed[0]]
        for index in indexed[1:]:
            if self._elo_ratings[index] == self._elo_ratings[current_group[0]]:
                current_group.append(index)
            else:
                if len(current_group) >= 2:
                    groups.append(current_group)
                current_group = [index]
        if len(current_group) >= 2:
            groups.append(current_group)
        return groups

    async def _show_tiebreaker_matchup(self, interaction: discord.Interaction) -> None:
        self.clear_items()
        self.add_item(self.prefer_a)
        self.add_item(self.prefer_b)
        await interaction.response.edit_message(
            content=self._build_matchup_content(), view=self
        )

    async def _try_enter_tiebreaker_or_finish(
        self, interaction: discord.Interaction
    ) -> None:
        if self._tiebreaker_challenger_queue:
            self._matchup_list.append(
                (self._tiebreaker_champion, self._tiebreaker_challenger_queue[0])
            )
            await self._show_tiebreaker_matchup(interaction)
            return

        if self._tiebreaker_remaining_groups:
            group = self._tiebreaker_remaining_groups.pop(0)
            random.shuffle(group)
            self._tiebreaker_champion = group[0]
            self._tiebreaker_challenger_queue = group[1:]
            self._matchup_list.append(
                (self._tiebreaker_champion, self._tiebreaker_challenger_queue[0])
            )
            await self._show_tiebreaker_matchup(interaction)
            return

        tied_groups = self._find_tied_groups()
        if tied_groups and self._tiebreaker_round < MAX_TIEBREAKER_ROUNDS:
            self._tiebreaker_round += 1
            self._tiebreaker_remaining_groups = tied_groups[1:]
            first_group = tied_groups[0]
            random.shuffle(first_group)
            self._tiebreaker_champion = first_group[0]
            self._tiebreaker_challenger_queue = first_group[1:]
            self._matchup_list.append(
                (self._tiebreaker_champion, self._tiebreaker_challenger_queue[0])
            )
            await self._show_tiebreaker_matchup(interaction)
        else:
            await self._show_final_ranking(interaction)

    def _build_ranking_pages(self) -> list[list[str]]:
        indexed_ratings = sorted(
            enumerate(self._elo_ratings),
            key=lambda pair: (-pair[1], pair[0]),
        )
        lines = [
            f'{rank}. {STUDIO_RECORDINGS[index]}'
            for rank, (index, _rating) in enumerate(indexed_ratings, start=1)
        ]
        pages = []
        for page_start in range(0, len(lines), SONGS_PER_PAGE):
            pages.append(lines[page_start:page_start + SONGS_PER_PAGE])
        return pages

    def _build_rankings_file(self) -> discord.File:
        all_lines = [line for page in self._ranking_pages for line in page]
        content = '\n'.join(all_lines) + '\n'
        buffer = io.BytesIO(content.encode('utf-8'))
        buffer.seek(0)
        return discord.File(buffer, filename='rankings.txt')

    def _build_ranking_embed(self) -> discord.Embed:
        total_pages = len(self._ranking_pages)
        page_lines = self._ranking_pages[self._current_ranking_page]
        return discord.Embed(
            title=f'Your ZUTOMAYO Song Ranking (Page {self._current_ranking_page + 1} / {total_pages})',
            description='\n'.join(page_lines),
        )

    def _rebuild_pagination_buttons(self) -> None:
        self.clear_items()
        total_pages = len(self._ranking_pages)

        previous_button = discord.ui.Button(
            label='Previous',
            style=discord.ButtonStyle.secondary,
            disabled=(self._current_ranking_page == 0),
            row=0,
        )
        previous_button.callback = self._on_previous_page_pressed

        page_label_button = discord.ui.Button(
            label=f'Page {self._current_ranking_page + 1} / {total_pages}',
            style=discord.ButtonStyle.secondary,
            disabled=True,
            row=0,
        )

        next_button = discord.ui.Button(
            label='Next',
            style=discord.ButtonStyle.secondary,
            disabled=(self._current_ranking_page >= total_pages - 1),
            row=0,
        )
        next_button.callback = self._on_next_page_pressed

        self.add_item(previous_button)
        self.add_item(page_label_button)
        self.add_item(next_button)

    async def _on_previous_page_pressed(self, interaction: discord.Interaction) -> None:
        self._current_ranking_page -= 1
        self._rebuild_pagination_buttons()
        await interaction.response.edit_message(embed=self._build_ranking_embed(), view=self)

    async def _on_next_page_pressed(self, interaction: discord.Interaction) -> None:
        self._current_ranking_page += 1
        self._rebuild_pagination_buttons()
        await interaction.response.edit_message(embed=self._build_ranking_embed(), view=self)

    async def _show_final_ranking(self, interaction: discord.Interaction) -> None:
        self._delete_checkpoint()
        self._ranking_pages = self._build_ranking_pages()
        self._current_ranking_page = 0
        self._rebuild_pagination_buttons()
        await interaction.response.edit_message(
            content='Ranking complete.',
            embed=self._build_ranking_embed(),
            view=self,
        )
        await interaction.followup.send(file=self._build_rankings_file())


class CheckpointChoiceView(discord.ui.View):
    def __init__(self, user_id: int, number_of_rounds: int, checkpoint_prefix: str = 'checkpoint') -> None:
        super().__init__(timeout=None)
        self._user_id = user_id
        self._number_of_rounds = number_of_rounds
        self._checkpoint_prefix = checkpoint_prefix

    @discord.ui.button(label='Resume Ranking', style=discord.ButtonStyle.primary, row=0)
    async def resume_ranking(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        checkpoint_path = CHECKPOINT_DIRECTORY / f'{self._checkpoint_prefix}_{self._user_id}.json'
        data = json.loads(checkpoint_path.read_text(encoding='utf-8'))
        view = RankSongsView.from_checkpoint(data, checkpoint_prefix=self._checkpoint_prefix)
        await interaction.response.edit_message(content=view._build_matchup_content(), view=view)

    @discord.ui.button(label='Delete Checkpoint & Start New Ranking', style=discord.ButtonStyle.danger, row=0)
    async def restart_new_ranking(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        (CHECKPOINT_DIRECTORY / f'{self._checkpoint_prefix}_{self._user_id}.json').unlink(missing_ok=True)
        view = RankSongsView(number_of_rounds=self._number_of_rounds, user_id=self._user_id, checkpoint_prefix=self._checkpoint_prefix)
        await interaction.response.edit_message(content=view._build_matchup_content(), view=view)
