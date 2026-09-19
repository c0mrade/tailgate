"""On-disk state shared by the gateway and the cron tick, which run in different processes."""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

from .sources import Source

STATE_FILE = "jobs.json"
SETTINGS_FILE = "settings.json"


def empty_state() -> dict:
    """jobs: name -> {n, source, state, muted, announced, reserved, first_seen, last_seen}.
    next_n: last number handed out. counters: last name suffix per topic. baselined: first round ran."""
    return {"version": 1, "jobs": {}}


class Store:
    """jobs.json in the plugin data dir, edited under an exclusive file lock and written atomically."""

    def __init__(self, data_dir: Path | str):
        self.dir = Path(data_dir)
        self.path = self.dir / STATE_FILE

    @contextlib.contextmanager
    def edit(self) -> Iterator[dict]:
        self.dir.mkdir(parents=True, exist_ok=True)
        with open(self.dir / f".{STATE_FILE}.lock", "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            state = self._read()
            yield state
            self._write(state)

    def read(self) -> dict:
        self.dir.mkdir(parents=True, exist_ok=True)
        with open(self.dir / f".{STATE_FILE}.lock", "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_SH)
            return self._read()

    def _read(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return empty_state()
        except (OSError, json.JSONDecodeError):
            with contextlib.suppress(OSError):  # keep the damaged file for inspection
                self.path.replace(self.path.with_suffix(".json.corrupt"))
            return empty_state()
        if not isinstance(data, dict) or not isinstance(data.get("jobs"), dict):
            return empty_state()
        return data

    def _write(self, state: dict) -> None:
        fd, tmp = tempfile.mkstemp(dir=self.dir, prefix=".jobs.", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=1, sort_keys=True)
        os.replace(tmp, self.path)


def save_settings(data_dir: Path | str, sources: list[Source]) -> None:
    """Snapshot of the configured sources for the cron tick, which cannot read plugin config."""
    path = Path(data_dir)
    path.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path, prefix=".settings.", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump({"sources": [s.to_config() for s in sources]}, handle, indent=1)
    os.replace(tmp, path / SETTINGS_FILE)


def load_settings(data_dir: Path | str) -> list[Source]:
    try:
        raw = json.loads((Path(data_dir) / SETTINGS_FILE).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return Source.from_config(raw.get("sources") if isinstance(raw, dict) else None)
