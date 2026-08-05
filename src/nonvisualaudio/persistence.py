"""Helpers for safely persisting user-owned JSON files."""

from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any

_PRESERVE_TARGET_MODE = os.name == "posix"


def atomic_write_json(path: Path, data: Any) -> None:
    """Write *data* as JSON without exposing a partially written target."""
    path.parent.mkdir(parents=True, exist_ok=True)
    target_mode: int | None = None
    if _PRESERVE_TARGET_MODE:
        try:
            target_mode = stat.S_IMODE(path.stat().st_mode)
        except FileNotFoundError:
            pass
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as fh:
            temp_path = Path(fh.name)
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
            if target_mode is not None:
                temp_path.chmod(target_mode)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temp_path, path)
        temp_path = None
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass
