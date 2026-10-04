"""Device selection for every model stack and the bot: CUDA, then Apple Silicon MPS,
then CPU.
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
