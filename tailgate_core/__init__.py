"""tailgate's engine: plain Python with no Hermes imports, usable from the plugin and the cron tick."""

from .models import Job, JobState
from .sources import Source, collect
from .store import Store, load_settings, save_settings
from .tracker import Tracker

__all__ = ["Job", "JobState", "Source", "Store", "Tracker", "collect", "load_settings",
           "save_settings", "tick"]


def tick(store: Store, sources: list[Source]) -> str:
    """Kept for tick scripts generated before 0.2; new ones call Tracker(...).round()."""
    return Tracker(store, sources).round()
