"""Shared `.env` override machinery for the model stacks.

Both `alpha_zero` and `ppo_transformer` keep their hyperparameters in a tree of
dataclasses whose defaults are the tracked, reproducible baseline. This module
layers per-machine overrides on top of that tree:

    <PREFIX>_<SECTION>_<FIELD>   -> Config.<section>.<field>
    <PREFIX>_<NAME>              -> run-level setting (workers, iterations, ...)

Precedence is CLI flag > process environment > the stack's `.env` file >
dataclass default. Values already present in the process environment always win
over the file, so a one-off `ALPHA_WORKERS=4 python -m ...` works without
editing anything.

`format_env_template` renders the whole surface back out from the dataclasses,
which is what `python -m alpha_zero.config` prints. Generating it from the
fields rather than maintaining a checked-in template is deliberate: the previous
hand-written `.env` had drifted to document a key that no longer existed.

Stdlib only, and no torch — the live Discord bot imports this path through
`alpha_zero.inference` on machines that carry no training code.
"""

from __future__ import annotations

import inspect
import os
from dataclasses import fields
from pathlib import Path
from typing import Any, Iterable, Sequence

# Run-level settings are described as (name, default, comment) triples.
RunSetting = tuple[str, Any, str]


def load_env_file(env_file: Path) -> None:
    """Loads `env_file` into os.environ. Existing variables are never replaced.

    Uses python-dotenv when importable; otherwise a minimal KEY=VALUE parser so
    dotenv stays a soft dependency. A missing file is not an error — production
    has no per-stack `.env` and relies on the process environment alone.
    """
    if not env_file.exists():
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        pass
    else:
        load_dotenv(env_file, override=False)
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.split("#", 1)[0].strip()
        if key and key not in os.environ:
            os.environ[key] = value


def coerce(raw: str, target_type: type, env_key: str = "value") -> Any:
    """Converts a raw env string to `target_type`.

    `env_key` is only used to name the offending variable in the error, which
    is the difference between a usable message and a bare ValueError from deep
    inside config loading.
    """
    if target_type is bool:
        text = raw.strip().lower()
        if text in ("1", "true", "yes", "on"):
            return True
        if text in ("0", "false", "no", "off", ""):
            return False
        raise ValueError(
            f"{env_key}={raw!r} is not a valid boolean "
            f"(use one of: 1/true/yes/on, 0/false/no/off)")
    try:
        return target_type(raw)
    except (TypeError, ValueError) as error:
        raise ValueError(
            f"{env_key}={raw!r} is not a valid {target_type.__name__}") from error


def apply_env_overrides(config: Any, prefix: str, sections: Sequence[str],
                        env_file: Path) -> Any:
    """Applies `<PREFIX>_<SECTION>_<FIELD>` overrides onto `config` in place.

    The target type comes from the field's current value, so a field defaulting
    to `''` reads as a string and one defaulting to `0` reads as an int. An
    empty override is treated as unset.
    """
    load_env_file(env_file)
    for section_name in sections:
        section = getattr(config, section_name)
        for field in fields(section):
            env_key = f"{prefix}_{section_name.upper()}_{field.name.upper()}"
            raw = os.environ.get(env_key)
            if raw is None or raw == "":
                continue
            current = getattr(section, field.name)
            setattr(section, field.name, coerce(raw, type(current), env_key))
    return config


def env_setting(name: str, default: Any, prefix: str, env_file: Path,
                target_type: type | None = None) -> Any:
    """Reads a run-level `<PREFIX>_<NAME>` setting, falling back to `default`."""
    load_env_file(env_file)
    env_key = f"{prefix}_{name.upper()}"
    raw = os.environ.get(env_key)
    if raw is None or raw == "":
        return default
    resolved = target_type or (type(default) if default is not None else str)
    return coerce(raw, resolved, env_key)


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def check_probabilities_sum(values: dict[str, float], label: str,
                            tolerance: float = 1e-6) -> None:
    """Raises unless `values` sums to 1.0. Used for opponent-sampling mixes,
    where a silent desync just quietly retunes training."""
    for name, value in values.items():
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"{label}: {name}={value} is outside [0, 1]")
    total = sum(values.values())
    if abs(total - 1.0) > tolerance:
        breakdown = ", ".join(f"{name}={value}" for name, value in values.items())
        raise ValueError(
            f"{label}: probabilities must sum to 1.0, got {total:.6f} ({breakdown})")


# ---------------------------------------------------------------------------
# Template rendering
# ---------------------------------------------------------------------------

def _field_comments(section_class: type) -> dict[str, list[str]]:
    """Field name -> its documentation lines, recovered from the source.

    Picks up both the comment block above a field and any trailing comment on
    its own line, so the notes already written in config.py become the notes in
    the generated template. Degrades to no comments if the source is
    unavailable (frozen builds, exec'd modules).
    """
    try:
        source_lines = inspect.getsource(section_class).splitlines()
    except (OSError, TypeError):
        return {}
    field_names = {field.name for field in fields(section_class)}
    comments: dict[str, list[str]] = {}
    pending: list[str] = []
    for line in source_lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            pending.append(stripped.lstrip("#").strip())
            continue
        name = stripped.split(":", 1)[0].strip()
        if name in field_names:
            collected = list(pending)
            if "#" in line:
                trailing = line.split("#", 1)[1].strip()
                if trailing:
                    collected.append(trailing)
            comments[name] = collected
        pending = []
    return comments


def _render(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def format_env_template(config: Any, prefix: str, sections: Sequence[str],
                        header: str, module_name: str,
                        run_settings: Iterable[RunSetting] = (),
                        section_notes: dict[str, str] | None = None) -> str:
    """Renders every override key with its current value, as a `.env` body.

    Every line is commented out, so redirecting the output to the stack's
    `.env` produces a file that documents the full surface while changing
    nothing. Uncomment what you want to override.

    `module_name` is passed in rather than read off the config class, which
    reports `__main__` when the module is run directly.
    """
    section_notes = section_notes or {}
    env_path = module_name.split(".")[0] + "/.env"
    rule = "# " + "=" * 75
    lines = [rule, f"# {header}", rule,
             "# Generated from the dataclass fields — regenerate with:",
             f"#   python -m {module_name} > {env_path}",
             "#",
             "# Naming:",
             f"#   {prefix}_<SECTION>_<FIELD>  -> Config.<section>.<field>",
             f"#   {prefix}_<NAME>             -> run-level setting",
             "# Precedence: CLI flag > process environment > this file > default.",
             ""]

    run_settings = list(run_settings)
    if run_settings:
        lines.append("# --- Run settings (CLI flags override these) " + "-" * 30)
        for name, default, comment in run_settings:
            suffix = f"  # {comment}" if comment else ""
            rendered = "" if default is None else _render(default)
            lines.append(f"# {prefix}_{name.upper()}={rendered}{suffix}")
        lines.append("")

    for section_name in sections:
        section = getattr(config, section_name)
        lines.append(f"# --- {section_name} " + "-" * (60 - len(section_name)))
        note = section_notes.get(section_name)
        if note:
            for note_line in note.splitlines():
                lines.append(f"# {note_line}")
        comments = _field_comments(type(section))
        for field in fields(section):
            for comment_line in comments.get(field.name, []):
                lines.append(f"#   {comment_line}")
            key = f"{prefix}_{section_name.upper()}_{field.name.upper()}"
            lines.append(f"# {key}={_render(getattr(section, field.name))}")
        lines.append("")

    return "\n".join(lines)
