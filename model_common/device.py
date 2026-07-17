"""Runtime device selection and CPU inference optimizations.

Shared by every model stack and by the bot's inference paths so device
preference is decided in exactly one place: CUDA when available, then Apple
Silicon MPS, then CPU.
"""

from __future__ import annotations

import torch


def select_device() -> torch.device:
    if torch.cuda.is_available():
        return torch.device("cuda")
    mps_backend = getattr(torch.backends, "mps", None)
    if mps_backend is not None and mps_backend.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def bound_inference_threads(maximum_threads: int = 4) -> None:
    """Caps intra-op threads so CPU inference never starves the host process
    (the Discord event loop shares the machine with inference)."""
    torch.set_num_threads(min(maximum_threads, torch.get_num_threads()))


def inference_optimizations(model: torch.nn.Module, device: torch.device) -> torch.nn.Module:
    """Prepares a model for low-latency inference on the given device.

    On CPU: dynamic int8 quantization of Linear layers plus bounded threads.
    On all devices: eval mode and gradient-free parameters. torch.compile is
    attempted and silently skipped where unsupported.
    """
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
