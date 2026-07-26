"""Cooperative stop for long training runs.

A training run is expected to be interrupted — it is a multi-day process that
someone eventually wants to pause, move to another machine, or stop to change a
hyperparameter. Raising an exception wherever the interrupt happens to land is
the wrong shape for that: it can hit the middle of `torch.save`, or leave a
worker pool half-drained. Instead this sets a flag, and the trainer checks it at
boundaries where all state is consistent, then saves and exits cleanly.

Two channels set the flag:

- SIGINT (Ctrl+C) and SIGTERM, for a run attached to a terminal or killed by a
  process manager.
- A `STOP` sentinel file in the runs directory, for a detached run — a job under
  `nohup`, in another terminal, or over SSH cannot be sent Ctrl+C, and this is
  the only safe way to ask it to wind down.

Pressing Ctrl+C a second time restores Python's default handler, so a run that
is genuinely wedged can still be killed immediately.

Stdlib only, and safe to construct in a process that never installs handlers
(`install=False`) — worker subprocesses want the flag semantics without
stealing signal handling from the parent.
"""

from __future__ import annotations

import signal
from pathlib import Path

SENTINEL_NAME = "STOP"


class StopSignal:
    """Cooperative stop flag. Check `.requested` at safe boundaries."""

    def __init__(self, runs_directory: str | Path | None = None,
                 install: bool = True) -> None:
        self.sentinel = (Path(runs_directory) / SENTINEL_NAME
                         if runs_directory is not None else None)
        self._flag = False
        self._reason = ""
        self._previous: dict[int, object] = {}
        if install:
            self._install()

    # -- signal handling ----------------------------------------------------

    def _install(self) -> None:
        for signal_number in (signal.SIGINT, signal.SIGTERM):
            try:
                self._previous[signal_number] = signal.signal(
                    signal_number, self._handle)
            except (ValueError, OSError, AttributeError):
                # Not on the main thread, or the platform lacks this signal.
                continue

    def _handle(self, signal_number, frame) -> None:
        if self._flag:
            # Already winding down and the user asked again — give them the
            # default behaviour so a wedged run is still killable.
            handler = self._previous.get(signal_number, signal.SIG_DFL)
            signal.signal(signal_number, handler)
            raise KeyboardInterrupt
        name = signal.Signals(signal_number).name
        self.request(f"received {name}")
        print(f"\n{name} received: finishing the current step, then saving. "
              f"Press Ctrl+C again to abort immediately.", flush=True)

    def restore(self) -> None:
        """Puts the previous handlers back. Safe to call more than once."""
        for signal_number, handler in list(self._previous.items()):
            try:
                signal.signal(signal_number, handler)
            except (ValueError, OSError, TypeError):
                pass
        self._previous.clear()

    # -- state --------------------------------------------------------------

    def request(self, reason: str = "requested") -> None:
        if not self._flag:
            self._flag = True
            self._reason = reason

    @property
    def reason(self) -> str:
        return self._reason

    @property
    def requested(self) -> bool:
        """True once a stop has been asked for through any channel.

        The sentinel file is consumed on the first read so that the next run
        does not immediately stop again.
        """
        if not self._flag and self.sentinel is not None and self.sentinel.exists():
            try:
                self.sentinel.unlink()
            except OSError:
                pass
            self.request(f"{SENTINEL_NAME} file")
            print(f"\n{SENTINEL_NAME} file found: finishing the current step, "
                  f"then saving.", flush=True)
        return self._flag

    def __enter__(self) -> "StopSignal":
        return self

    def __exit__(self, *exception) -> None:
        self.restore()
