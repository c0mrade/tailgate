"""Jobs as tailgate sees them: a name, a state and what a source said about progress."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,62}$")
NUMBER = re.compile(r"^#?(\d{1,6})$")


class JobState(StrEnum):
    """Where a job is. The first two are active, the last three finished."""

    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    INCOMPLETE = "incomplete"
    FAILED = "failed"

    @property
    def active(self) -> bool:
        return self in (JobState.QUEUED, JobState.RUNNING)

    @property
    def finished(self) -> bool:
        return not self.active


@dataclass(frozen=True)
class Job:
    """One job as a source reported it in the current round."""

    name: str
    state: JobState
    source: str
    elapsed_s: int | None = None
    progress: str = ""
    last: str = ""
