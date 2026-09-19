"""Job sources: commands that print one JSON object per job, and what tailgate accepts from them."""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .models import NAME, Job, JobState

TEXT_LIMIT = 120
TIMEOUT_S = 30
CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def clean_text(value: Any) -> str:
    """Source text may come from a machine running untrusted code: it is only shown to the user,
    never to the model, and still gets control characters stripped and its length capped."""
    if not isinstance(value, str):
        return ""
    text = CONTROL.sub(" ", value).strip()
    return text if len(text) <= TEXT_LIMIT else text[: TEXT_LIMIT - 1] + "…"


@dataclass(frozen=True)
class Source:
    """A configured command (argv, run without a shell) that lists jobs."""

    name: str
    command: tuple[str, ...]

    @classmethod
    def from_config(cls, raw: Any) -> list[Source]:
        """Valid entries of `plugins.entries.tailgate.settings.sources`, invalid ones dropped."""
        sources = []
        for item in raw if isinstance(raw, list) else []:
            if not isinstance(item, dict):
                continue
            name, command = item.get("name"), item.get("command")
            if (isinstance(name, str) and NAME.match(name) and isinstance(command, list) and command
                    and all(isinstance(part, str) and part for part in command)):
                sources.append(cls(name, tuple(command)))
        return sources

    def to_config(self) -> dict:
        return {"name": self.name, "command": list(self.command)}

    def parse(self, output: str) -> tuple[list[Job], list[str]]:
        """Valid jobs and a list of problems; bad lines are skipped, not fatal."""
        jobs: list[Job] = []
        problems: list[str] = []
        for n, line in enumerate(output.splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                problems.append(f"{self.name}: line {n} is not JSON")
                continue
            if not isinstance(obj, dict):
                problems.append(f"{self.name}: line {n} is not an object")
                continue
            name, state = obj.get("id"), obj.get("state")
            if not isinstance(name, str) or not NAME.match(name):
                problems.append(f"{self.name}: line {n} has an invalid id")
                continue
            try:
                state = JobState(state)
            except ValueError:
                problems.append(f"{self.name}: {name} has unknown state {str(state)[:20]!r}")
                continue
            elapsed = obj.get("elapsed_s")
            elapsed = int(elapsed) if isinstance(elapsed, (int, float)) and elapsed >= 0 else None
            jobs.append(Job(name, state, self.name, elapsed, clean_text(obj.get("progress")),
                            clean_text(obj.get("last"))))
        return jobs, problems

    def fetch(self, timeout: float = TIMEOUT_S) -> tuple[list[Job], list[str]]:
        """Run the command once and parse what it printed."""
        try:
            result = subprocess.run(self.command, capture_output=True, text=True,
                                    timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            return [], [f"{self.name}: no answer within {timeout:.0f} s"]
        except OSError as e:
            return [], [f"{self.name}: cannot run ({e.strerror or e})"]
        if result.returncode != 0:
            detail = clean_text(result.stderr) or f"exit {result.returncode}"
            return [], [f"{self.name}: failed ({detail})"]
        return self.parse(result.stdout)


def collect(sources: Iterable[Source]) -> tuple[list[Job], list[str]]:
    """Jobs and problems from every source, one after another."""
    jobs: list[Job] = []
    problems: list[str] = []
    for source in sources:
        found, issues = source.fetch()
        jobs.extend(found)
        problems.extend(issues)
    return jobs, problems
