"""tailgate's engine: plain Python with no Hermes imports, usable from the plugin and the cron tick."""

import sys

MIN_PYTHON = (3, 11)  # the same floor as Hermes, and StrEnum needs 3.11
if sys.version_info < MIN_PYTHON:
    raise RuntimeError(f"tailgate needs Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer "
                       f"(the same as Hermes), this is {sys.version.split()[0]}")

from .models import Job, JobState  # noqa: E402 (after the version check)
from .sources import Source, collect  # noqa: E402
from .store import Store, load_settings, save_settings  # noqa: E402
from .tracker import Tracker  # noqa: E402

__all__ = ["Job", "JobState", "Source", "Store", "Tracker", "collect", "load_settings",
           "save_settings", "tick"]


def tick(store: Store, sources: list[Source]) -> str:
    """Kept for tick scripts generated before 0.2; new ones call Tracker(...).round()."""
    return Tracker(store, sources).round()
