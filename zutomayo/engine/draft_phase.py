"""Compatibility shim: the draft phase moved to zutomayo.match.draft_flow.
This module keeps the legacy flows importable until they are deleted."""

from zutomayo.match.draft_flow import run_standard_draft_phase, run_tcg_draft_phase

__all__ = ['run_standard_draft_phase', 'run_tcg_draft_phase']
