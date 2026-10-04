"""Shared .env overrides for the model stacks' dataclass configs:

    <PREFIX>_<SECTION>_<FIELD>   -> Config.<section>.<field>
    <PREFIX>_<NAME>              -> run-level setting (workers, iterations, ...)

Precedence: CLI flag > process environment > the stack's .env > dataclass default.
format_env_template renders the section keys from the dataclasses, so they cannot
drift. Stdlib only, so the live bot can import it without torch.
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
    """Load `env_file` into os.environ without replacing existing variables. Uses
    python-dotenv when available, else a minimal parser. A missing file is fine."""
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
    """Convert a raw env string to `target_type`; `env_key` names the variable in errors."""
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
    """Apply `<PREFIX>_<SECTION>_<FIELD>` overrides to `config` in place. Each value's
    type comes from the field's current value; an empty value counts as unset, but an
    empty process variable still hides the .env value."""
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


# --- Validation helpers ---

def check_probabilities_sum(values: dict[str, float], label: str,
                            tolerance: float = 1e-6) -> None:
    """Raise unless each value is in [0, 1] and they sum to 1.0 (opponent-sampling
    mixes)."""
    for name, value in values.items():
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"{label}: {name}={value} is outside [0, 1]")
    total = sum(values.values())
    if abs(total - 1.0) > tolerance:
        breakdown = ", ".join(f"{name}={value}" for name, value in values.items())
        raise ValueError(
            f"{label}: probabilities must sum to 1.0, got {total:.6f} ({breakdown})")


# --- Template rendering ---

def _field_comments(section_class: type) -> dict[str, list[str]]:
    """Field name -> its comment lines from the source: the block directly above the
    field (no blank line between) plus any trailing comment. Empty without source."""
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
    """Every override key with its current value, as a fully commented `.env` body.
    `module_name` is passed in because the class reports `__main__` when run directly."""
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
