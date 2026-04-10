from __future__ import annotations
import io
import json
import logging
import math
import os
import random
from pathlib import Path
import discord
from zutomayo.data.rank_songs_data import STUDIO_RECORDINGS


log = logging.getLogger(__name__)


CHECKPOINT_DIRECTORY = Path('zutomayo/progress')

def get_checkpoint_path(user_id: int) -> Path:
    return CHECKPOINT_DIRECTORY / f'checkpoint_{user_id}.json'

INITIAL_RATING = 2000
K_FACTOR = 32
DEFAULT_NUMBER_OF_ROUNDS = 1 # math.ceil(math.sqrt(len(STUDIO_RECORDINGS))) + 7
SONGS_PER_PAGE = 15
MAX_ITERATIONS = 100
CONVERGENCE_THRESHOLD = 0.01

SCORE_PREFER_A = 1.0
SCORE_SLIGHTLY_PREFER_A = 0.75
SCORE_SLIGHTLY_PREFER_B = 0.25
SCORE_PREFER_B = 0.0


def _generate_matchup_list(number_of_rounds: int) -> list[tuple[int, int]]:
    """
    Return a shuffled list of (song_index_a, song_index_b) pairs.

    For partial mode, the first number_of_rounds * (len(STUDIO_RECORDINGS) // 2)
    pairs are returned, giving each song approximately number_of_rounds
    appearances. Works correctly for any N (even or odd).
    """
    n = len(STUDIO_RECORDINGS)
    all_pairs: list[tuple[int, int]] = [
        (i, j) for i in range(n) for j in range(i + 1, n)
    ]
    random.shuffle(all_pairs)
    return all_pairs[:number_of_rounds * (n // 2)]


class RankSongsView(discord.ui.View):
    def __init__(self, number_of_rounds: int = DEFAULT_NUMBER_OF_ROUNDS, user_id: int = 0) -> None:
        super().__init__(timeout=None)
        self._user_id: int = user_id
        self._number_of_rounds: int = number_of_rounds
        self._matchup_list: list[tuple[int, int]] = _generate_matchup_list(number_of_rounds)
        self._recorded_votes: list[tuple[int, int, float]] = []
        self._current_matchup_index: int = 0
        self._ranking_pages: list[list[str]] = []
        self._current_ranking_page: int = 0
        self._vote_history: list[dict] = []

    def _get_checkpoint_path(self) -> Path:
        return get_checkpoint_path(self._user_id)

    @classmethod
    def from_checkpoint(cls, data: dict) -> RankSongsView:
        instance = cls(number_of_rounds=data['number_of_rounds'], user_id=data['discord_user_id'])
        instance._matchup_list = [tuple(pair) for pair in data['matchup_list']]
        instance._current_matchup_index = data['current_matchup_index']
        instance._recorded_votes = [tuple(vote) for vote in data['recorded_votes']]
        return instance

    def _save_checkpoint(self) -> None:
        os.makedirs(CHECKPOINT_DIRECTORY, exist_ok=True)
        data = {
            'discord_user_id': self._user_id,
            'number_of_rounds': self._number_of_rounds,
            'matchup_list': [list(pair) for pair in self._matchup_list],
            'current_matchup_index': self._current_matchup_index,
            'recorded_votes': [list(vote) for vote in self._recorded_votes],
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

    @discord.ui.button(label='Go Back', style=discord.ButtonStyle.secondary, row=1, disabled=True)
    async def go_back(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if not self._vote_history:
            await interaction.response.defer()
            return

        snapshot = self._vote_history.pop()

        self._recorded_votes = snapshot['recorded_votes']
        self._current_matchup_index = snapshot['current_matchup_index']

        self.go_back.disabled = len(self._vote_history) == 0

        self._rebuild_normal_voting_buttons()

        await interaction.response.edit_message(content=self._build_matchup_content(), view=self)

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

    def _snapshot_state(self) -> dict:
        return {
            'recorded_votes': list(self._recorded_votes),
            'current_matchup_index': self._current_matchup_index,
        }

    def _rebuild_normal_voting_buttons(self) -> None:
        self.clear_items()
        self.add_item(self.prefer_a)
        self.add_item(self.slightly_prefer_a)
        self.add_item(self.slightly_prefer_b)
        self.add_item(self.prefer_b)
        self.add_item(self.go_back)
        self.add_item(self.save_progress)
        self.add_item(self.quit_session)

    def _build_matchup_content(self) -> str:
        song_index_a, song_index_b = self._matchup_list[self._current_matchup_index]
        total_matchups = len(self._matchup_list)
        current_number = self._current_matchup_index + 1
        percentage = round(self._current_matchup_index / total_matchups * 100)
        song_name_a = STUDIO_RECORDINGS[song_index_a]
        song_name_b = STUDIO_RECORDINGS[song_index_b]
        return f'Progress: {percentage}% ({current_number}/{total_matchups})\n\nA: {song_name_a}\nB: {song_name_b}'

    def _compute_iterated_elo_ratings(self) -> list[float]:
        ratings = [float(INITIAL_RATING)] * len(STUDIO_RECORDINGS)
        for _ in range(MAX_ITERATIONS):
            new_ratings = [float(INITIAL_RATING)] * len(STUDIO_RECORDINGS)
            for song_index_a, song_index_b, score_for_a in self._recorded_votes:
                expected_a = 1.0 / (1.0 + 10.0 ** ((ratings[song_index_b] - ratings[song_index_a]) / 400.0))
                new_ratings[song_index_a] += K_FACTOR * (score_for_a - expected_a)
                new_ratings[song_index_b] += K_FACTOR * ((1.0 - score_for_a) - (1.0 - expected_a))
            if max(abs(new_ratings[i] - ratings[i]) for i in range(len(ratings))) < CONVERGENCE_THRESHOLD:
                ratings = new_ratings
                break
            ratings = new_ratings
        return ratings

    async def _process_vote_and_advance(
        self, interaction: discord.Interaction, score_for_a: float
    ) -> None:
        self._vote_history.append(self._snapshot_state())
        self.go_back.disabled = False

        song_index_a, song_index_b = self._matchup_list[self._current_matchup_index]
        self._recorded_votes.append((song_index_a, song_index_b, score_for_a))
        self._current_matchup_index += 1

        if self._current_matchup_index % 50 == 0:
            self._save_checkpoint()

        if self._current_matchup_index < len(self._matchup_list):
            await interaction.response.edit_message(
                content=self._build_matchup_content(), view=self
            )
        else:
            await self._show_final_ranking(interaction)

    def _build_ranking_pages(self, ratings: list[float]) -> list[list[str]]:
        indexed_ratings = sorted(
            enumerate(ratings),
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
        log.info('User %s completed their song ranking', self._user_id)
        self._delete_checkpoint()
        ratings = self._compute_iterated_elo_ratings()
        self._ranking_pages = self._build_ranking_pages(ratings)
        self._current_ranking_page = 0
        self._rebuild_pagination_buttons()
        await interaction.response.edit_message(
            content='Ranking complete (songs at the top and bottom of the ranking will be more accurately placed than in the middle).',
            embed=self._build_ranking_embed(),
            view=self,
        )
        await interaction.followup.send(file=self._build_rankings_file())


class CheckpointChoiceView(discord.ui.View):
    def __init__(self, user_id: int, number_of_rounds: int) -> None:
        super().__init__(timeout=None)
        self._user_id = user_id
        self._number_of_rounds = number_of_rounds

    @discord.ui.button(label='Resume Ranking', style=discord.ButtonStyle.primary, row=0)
    async def resume_ranking(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        checkpoint_path = get_checkpoint_path(self._user_id)
        try:
            data = json.loads(checkpoint_path.read_text(encoding='utf-8'))
            view = RankSongsView.from_checkpoint(data)
        except (KeyError, json.JSONDecodeError):
            checkpoint_path.unlink(missing_ok=True)
            view = RankSongsView(number_of_rounds=self._number_of_rounds, user_id=self._user_id)
            await interaction.response.edit_message(
                content='Saved checkpoint was incompatible and has been cleared. Starting a new ranking.',
                view=view,
            )
            return
        await interaction.response.edit_message(content=view._build_matchup_content(), view=view)

    @discord.ui.button(label='Delete Checkpoint & Start New Ranking', style=discord.ButtonStyle.danger, row=0)
    async def restart_new_ranking(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        get_checkpoint_path(self._user_id).unlink(missing_ok=True)
        view = RankSongsView(number_of_rounds=self._number_of_rounds, user_id=self._user_id)
        await interaction.response.edit_message(content=view._build_matchup_content(), view=view)
