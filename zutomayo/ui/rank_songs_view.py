from __future__ import annotations

import random

import discord

from zutomayo.data.rank_songs_data import STUDIO_RECORDINGS

INITIAL_RATING = 1000
K_FACTOR = 32
NUMBER_OF_ROUNDS = 15
SONGS_PER_PAGE = 15

SCORE_PREFER_A = 1.0
SCORE_SLIGHTLY_PREFER_A = 0.75
SCORE_SLIGHTLY_PREFER_B = 0.25
SCORE_PREFER_B = 0.0


def _generate_matchup_list() -> list[tuple[int, int]]:
    """
    Return a shuffled list of (song_index_a, song_index_b) pairs using the
    standard round-robin rotation algorithm for NUMBER_OF_ROUNDS rounds.

    A dummy None entry is added to make the total count even (75 -> 76),
    so one song sits out (receives a bye) each round. Over 15 rounds this
    produces 15 x 37 = 555 unique matchups with every song appearing in
    exactly 14 or 15 matchups.
    """
    song_indices: list[int | None] = list(range(len(STUDIO_RECORDINGS))) + [None]
    fixed = song_indices[0]
    rotating: list[int | None] = song_indices[1:]
    half = len(song_indices) // 2

    matchup_pairs: list[tuple[int, int]] = []

    for _ in range(NUMBER_OF_ROUNDS):
        for i in range(half):
            song_index_a = fixed if i == 0 else rotating[i - 1]
            song_index_b = rotating[len(rotating) - i - 1]
            if song_index_a is not None and song_index_b is not None:
                matchup_pairs.append((song_index_a, song_index_b))
        rotating = [rotating[-1]] + rotating[:-1]

    random.shuffle(matchup_pairs)
    return matchup_pairs


class RankSongsView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)
        self._matchup_list: list[tuple[int, int]] = _generate_matchup_list()
        self._elo_ratings: list[float] = [float(INITIAL_RATING)] * len(STUDIO_RECORDINGS)
        self._current_matchup_index: int = 0
        self._ranking_pages: list[list[str]] = []
        self._current_ranking_page: int = 0

    # --- Voting buttons ---

    @discord.ui.button(label='Prefer A', style=discord.ButtonStyle.primary, row=0)
    async def prefer_a(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._process_vote_and_advance(interaction, SCORE_PREFER_A)

    @discord.ui.button(label='Slightly Prefer A', style=discord.ButtonStyle.primary, row=0)
    async def slightly_prefer_a(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._process_vote_and_advance(interaction, SCORE_SLIGHTLY_PREFER_A)

    @discord.ui.button(label='Slightly Prefer B', style=discord.ButtonStyle.secondary, row=0)
    async def slightly_prefer_b(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._process_vote_and_advance(interaction, SCORE_SLIGHTLY_PREFER_B)

    @discord.ui.button(label='Prefer B', style=discord.ButtonStyle.secondary, row=0)
    async def prefer_b(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._process_vote_and_advance(interaction, SCORE_PREFER_B)

    # --- Private helpers ---

    def _build_matchup_content(self) -> str:
        song_index_a, song_index_b = self._matchup_list[self._current_matchup_index]
        total_matchups = len(self._matchup_list)
        percentage = round(self._current_matchup_index / total_matchups * 100)
        song_name_a = STUDIO_RECORDINGS[song_index_a]
        song_name_b = STUDIO_RECORDINGS[song_index_b]
        return f'Progress: {percentage}%\n\nA: {song_name_a}\nB: {song_name_b}'

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

        if self._current_matchup_index < len(self._matchup_list):
            await interaction.response.edit_message(
                content=self._build_matchup_content(), view=self
            )
        else:
            await self._show_final_ranking(interaction)

    def _build_ranking_pages(self) -> list[list[str]]:
        songs_with_ratings = [
            (STUDIO_RECORDINGS[index], self._elo_ratings[index])
            for index in range(len(STUDIO_RECORDINGS))
        ]
        songs_with_ratings.sort(key=lambda pair: pair[1], reverse=True)
        lines = [
            f'{rank}. {song_name}'
            for rank, (song_name, rating) in enumerate(songs_with_ratings, start=1)
        ]
        pages = []
        for page_start in range(0, len(lines), SONGS_PER_PAGE):
            pages.append(lines[page_start:page_start + SONGS_PER_PAGE])
        return pages

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
        self._ranking_pages = self._build_ranking_pages()
        self._current_ranking_page = 0
        self._rebuild_pagination_buttons()
        await interaction.response.edit_message(
            content='Ranking complete.',
            embed=self._build_ranking_embed(),
            view=self,
        )
