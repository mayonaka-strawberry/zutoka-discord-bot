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
DEFAULT_NUMBER_OF_ROUNDS = math.ceil(math.sqrt(len(STUDIO_RECORDINGS))) + 7
SONGS_PER_PAGE = 15
MAX_ITERATIONS = 500
CONVERGENCE_THRESHOLD = 0.01
PSEUDOCOUNT = 0.5

SCORE_PREFER_A = 1.0
SCORE_SLIGHTLY_PREFER_A = 0.75
SCORE_SLIGHTLY_PREFER_B = 0.25
SCORE_PREFER_B = 0.0


class RankSongsView(discord.ui.View):
    def __init__(self, number_of_rounds: int = DEFAULT_NUMBER_OF_ROUNDS, user_id: int = 0) -> None:
        super().__init__(timeout=None)
        self._user_id: int = user_id
        self._number_of_rounds: int = number_of_rounds
        n = len(STUDIO_RECORDINGS)
        self._total_matchups: int = number_of_rounds * (n // 2)

        # All N*(N-1)/2 pairs, shuffled, form the adaptive candidate pool.
        all_pairs: list[tuple[int, int]] = [
            (i, j) for i in range(n) for j in range(i + 1, n)
        ]
        random.shuffle(all_pairs)
        self._candidate_pairs: list[tuple[int, int]] = all_pairs

        # Running ratings used only for adaptive pair selection, not for the final output.
        self._running_ratings: list[float] = [float(INITIAL_RATING)] * n

        self._recorded_votes: list[tuple[int, int, float]] = []
        self._ranking_pages: list[list[str]] = []
        self._current_ranking_page: int = 0
        self._vote_history: list[dict] = []

        self._current_matchup: tuple[int, int] = self._pick_next_matchup()

    def _get_checkpoint_path(self) -> Path:
        return get_checkpoint_path(self._user_id)

    @classmethod
    def from_checkpoint(cls, data: dict) -> RankSongsView:
        # KeyError on any missing key propagates to CheckpointChoiceView.resume_ranking,
        # which treats it as an incompatible checkpoint and starts fresh.
        instance = cls.__new__(cls)
        discord.ui.View.__init__(instance, timeout=None)
        instance._user_id = data['discord_user_id']
        instance._number_of_rounds = data['number_of_rounds']
        instance._total_matchups = data['total_matchups']
        instance._candidate_pairs = [tuple(pair) for pair in data['candidate_pairs']]
        instance._running_ratings = list(data['running_ratings'])
        instance._current_matchup = tuple(data['current_matchup'])
        instance._recorded_votes = [tuple(vote) for vote in data['recorded_votes']]
        instance._ranking_pages = []
        instance._current_ranking_page = 0
        instance._vote_history = []
        return instance

    def _save_checkpoint(self) -> None:
        os.makedirs(CHECKPOINT_DIRECTORY, exist_ok=True)
        data = {
            'discord_user_id': self._user_id,
            'number_of_rounds': self._number_of_rounds,
            'total_matchups': self._total_matchups,
            'candidate_pairs': [list(pair) for pair in self._candidate_pairs],
            'running_ratings': list(self._running_ratings),
            'current_matchup': list(self._current_matchup),
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
        self._running_ratings = snapshot['running_ratings']
        self._current_matchup = snapshot['current_matchup']
        self._candidate_pairs = snapshot['candidate_pairs']

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

    def _pick_next_matchup(self) -> tuple[int, int]:
        """Remove and return the candidate pair whose two songs have the closest running ratings."""
        best_index = min(
            range(len(self._candidate_pairs)),
            key=lambda k: abs(
                self._running_ratings[self._candidate_pairs[k][0]]
                - self._running_ratings[self._candidate_pairs[k][1]]
            ),
        )
        pair = self._candidate_pairs[best_index]
        # Swap-and-pop for O(1) removal; pool order doesn't matter since we always re-scan.
        self._candidate_pairs[best_index] = self._candidate_pairs[-1]
        self._candidate_pairs.pop()
        return pair

    def _update_running_ratings(
        self, song_index_a: int, song_index_b: int, score_for_a: float
    ) -> None:
        """Apply a single online ELO update to the running ratings used for adaptive selection."""
        expected_a = 1.0 / (
            1.0 + 10.0 ** ((self._running_ratings[song_index_b] - self._running_ratings[song_index_a]) / 400.0)
        )
        self._running_ratings[song_index_a] += K_FACTOR * (score_for_a - expected_a)
        self._running_ratings[song_index_b] += K_FACTOR * ((1.0 - score_for_a) - (1.0 - expected_a))

    def _snapshot_state(self) -> dict:
        return {
            'recorded_votes': list(self._recorded_votes),
            'running_ratings': list(self._running_ratings),
            'current_matchup': self._current_matchup,
            'candidate_pairs': list(self._candidate_pairs),
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
        song_index_a, song_index_b = self._current_matchup
        votes_done = len(self._recorded_votes)
        current_number = votes_done + 1
        percentage = round(votes_done / self._total_matchups * 100)
        song_name_a = STUDIO_RECORDINGS[song_index_a]
        song_name_b = STUDIO_RECORDINGS[song_index_b]
        return (
            f'Progress: {percentage}% ({current_number}/{self._total_matchups})\n\n'
            f'A: {song_name_a}\nB: {song_name_b}'
        )

    def _compute_bradley_terry_ratings(self) -> list[float]:
        """
        Compute final ratings using the Bradley-Terry MM algorithm with pseudocounts.

        The MM update per song i:
            gamma_i_new = (W_i + PSEUDOCOUNT)
                          / (PSEUDOCOUNT * 2 / (gamma_i + 1)
                             + sum_{j facing i} n_ij / (gamma_i + gamma_j))

        Ratings are then: INITIAL_RATING + 400 * log10(gamma_i).
        """
        n = len(STUDIO_RECORDINGS)

        wins: list[float] = [0.0] * n
        comparisons: dict[tuple[int, int], int] = {}

        for song_index_a, song_index_b, score_for_a in self._recorded_votes:
            wins[song_index_a] += score_for_a
            wins[song_index_b] += 1.0 - score_for_a
            key = (min(song_index_a, song_index_b), max(song_index_a, song_index_b))
            comparisons[key] = comparisons.get(key, 0) + 1

        opponents: list[list[tuple[int, int]]] = [[] for _ in range(n)]
        for (i, j), n_ij in comparisons.items():
            opponents[i].append((j, n_ij))
            opponents[j].append((i, n_ij))

        gammas: list[float] = [1.0] * n

        for _ in range(MAX_ITERATIONS):
            new_gammas: list[float] = []
            for i in range(n):
                numerator = wins[i] + PSEUDOCOUNT
                denominator = PSEUDOCOUNT * 2.0 / (gammas[i] + 1.0) + sum(
                    n_ij / (gammas[i] + gammas[j]) for j, n_ij in opponents[i]
                )
                new_gammas.append(numerator / denominator if denominator > 0.0 else gammas[i])

            # Normalise by geometric mean to keep gammas well-scaled across iterations.
            log_mean = sum(math.log(g) for g in new_gammas) / n
            scale = math.exp(log_mean)
            new_gammas = [g / scale for g in new_gammas]

            if max(abs(new_gammas[i] - gammas[i]) for i in range(n)) < CONVERGENCE_THRESHOLD:
                gammas = new_gammas
                break
            gammas = new_gammas

        return [INITIAL_RATING + 400.0 * math.log10(g) for g in gammas]

    async def _process_vote_and_advance(
        self, interaction: discord.Interaction, score_for_a: float
    ) -> None:
        self._vote_history.append(self._snapshot_state())
        self.go_back.disabled = False

        song_index_a, song_index_b = self._current_matchup
        self._recorded_votes.append((song_index_a, song_index_b, score_for_a))
        self._update_running_ratings(song_index_a, song_index_b, score_for_a)

        if len(self._recorded_votes) % (len(STUDIO_RECORDINGS) // 2) == 0:
            self._save_checkpoint()

        if len(self._recorded_votes) < self._total_matchups:
            self._current_matchup = self._pick_next_matchup()
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
        ratings = self._compute_bradley_terry_ratings()
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
