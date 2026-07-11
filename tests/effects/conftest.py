"""Shared fixtures for the per-effect characterization tests."""

from __future__ import annotations

import sys

import pytest


@pytest.fixture(autouse=True)
def stub_reveal_rendering(monkeypatch):
    """
    Replace the deck-grid renderer with a no-op in every effect module.

    Reveal effects composite card art with PIL purely for Discord output; the
    tests assert on state and message text, and the real renderer would load
    hundreds of images per test run.
    """

    async def render_stub(*args, **kwargs):
        return None

    import zutomayo.effects.effect_engine  # noqa: F401  (imports all card modules)
    import zutomayo.ui.embeds as embeds_module

    for module_name, module in list(sys.modules.items()):
        if module_name.startswith('zutomayo.effects.cards.') and hasattr(module, 'create_deck_grid_image_off_thread'):
            monkeypatch.setattr(module, 'create_deck_grid_image_off_thread', render_stub)
    monkeypatch.setattr(embeds_module, 'create_deck_grid_image_off_thread', render_stub)
