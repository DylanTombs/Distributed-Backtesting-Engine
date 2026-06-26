"""
config_io.py — read and write backtest_config.yaml for the dashboard.

Deliberately avoids pyyaml's round-trip parser so that hand-written comments
in the config file are not destroyed on save.  Instead we do a best-effort
key-by-key replacement on the raw text.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml


def load_config(path: str = "backtest_config.yaml") -> dict[str, Any]:
    """Parse the YAML config file and return a flat dict of key → value."""
    with open(path) as f:
        data = yaml.safe_load(f)
    return data or {}


def save_config(config: dict[str, Any], path: str = "backtest_config.yaml") -> None:
    """Write config dict back to YAML.

    Performs a comment-preserving in-place update: for each key that exists
    on a line by itself (``key: value``), replaces only the value portion.
    Keys not found via that pattern fall back to yaml.dump at the end of file.
    """
    config_path = Path(path)
    original_text = config_path.read_text() if config_path.exists() else ""

    updated = original_text
    unmatched: dict[str, Any] = {}

    for key, value in config.items():
        yaml_val = _to_yaml_scalar(value)
        # Replace: key: <anything> (possibly with trailing comment)
        pattern = rf"^({re.escape(key)}:\s*)([^\n#]*)(.*)$"
        new_line = rf"\g<1>{yaml_val}\g<3>"
        new_text, count = re.subn(pattern, new_line, updated, count=1, flags=re.MULTILINE)
        if count:
            updated = new_text
        else:
            unmatched[key] = value

    if unmatched:
        extra = yaml.dump(unmatched, default_flow_style=False)
        updated = updated.rstrip() + "\n\n# Added by dashboard\n" + extra

    config_path.write_text(updated)


def diff_configs(old: dict[str, Any], new: dict[str, Any]) -> list[tuple[str, Any, Any]]:
    """Return (field, old_value, new_value) for every changed field.

    Covers changed values, keys added in `new`, and keys removed from `old`.
    """
    all_keys = set(old) | set(new)
    changes = []
    for key in sorted(all_keys):
        old_val = old.get(key)
        new_val = new.get(key)
        if old_val != new_val:
            changes.append((key, old_val, new_val))
    return changes


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _to_yaml_scalar(value: Any) -> str:
    """Convert a Python value to an inline YAML scalar string."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return str(value)
    if isinstance(value, int):
        return str(value)
    return str(value)
