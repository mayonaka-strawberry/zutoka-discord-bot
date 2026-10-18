"""Device selection for every model stack and the bot: CUDA, then Apple Silicon MPS,
then CPU. MPS work is serialized across threads.
"""

from __future__ import annotations

import contextlib
import threading

import torch

# Below the 45 s solo-game watchdog (zutomayo/match/agents/agent_adapter.py), so a
# waiting thread gives up and the adapter plays its fallback move.
MPS_LOCK_TIMEOUT_SECONDS = 40.0

# Two threads issuing MPS ops at once crash the process (PyTorch issue #197805).
_MPS_LOCK = threading.RLock()


def select_device() -> torch.device:
    if torch.cuda.is_available():
        return torch.device("cuda")
    mps_backend = getattr(torch.backends, "mps", None)
    if mps_backend is not None and mps_backend.is_available():
        return torch.device("mps")
    return torch.device("cpu")


@contextlib.contextmanager
def serialized_on_mps(device: torch.device | str):
    """Hold the process-wide MPS lock while `device` is MPS; elsewhere a no-op.
    Raises TimeoutError when the lock is not free within MPS_LOCK_TIMEOUT_SECONDS."""
    if torch.device(device).type != "mps":
        yield
        return
    if not _MPS_LOCK.acquire(timeout=MPS_LOCK_TIMEOUT_SECONDS):
        raise TimeoutError(
            f"MPS lock not acquired within {MPS_LOCK_TIMEOUT_SECONDS} s")
    try:
        yield
    finally:
        _MPS_LOCK.release()


def bound_inference_threads(maximum_threads: int = 4) -> None:
    """Cap intra-op threads so CPU inference never starves the Discord event loop."""
    torch.set_num_threads(min(maximum_threads, torch.get_num_threads()))


def inference_optimizations(model: torch.nn.Module, device: torch.device) -> torch.nn.Module:
    """Prepare a model for inference: eval mode and no gradients; on CPU, bounded threads
    and int8 dynamic quantization of Linear layers; torch.compile where supported."""
    model = model.to(device).eval()
    for parameter in model.parameters():
        parameter.requires_grad_(False)
    if device.type == "cpu":
        bound_inference_threads()
        try:
            model = torch.quantization.quantize_dynamic(
                model, {torch.nn.Linear}, dtype=torch.qint8)
        except Exception:
            pass
    try:
        model = torch.compile(model, mode="reduce-overhead")
    except Exception:
        pass
    return model
