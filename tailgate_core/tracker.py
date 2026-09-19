"""The Tracker: what tailgate knows about jobs and what it tells the user."""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterable

from . import render
from .models import NAME, NUMBER, Job
from .sources import Source, collect
from .store import Store

FORGET_AFTER_S = 14 * 24 * 3600  # drop jobs no source has reported for two weeks
TOPIC_CHARS = re.compile(r"[^a-z0-9]+")
# Filler dropped from topics so names stay short enough to read (and type) on a phone.
STOPWORDS = frozenset("a an and as at by doc docs for from in into of on or pr the to with add adds "
                      "create creates make new".split())
NAME_WORDS, NAME_CHARS = 3, 24


class Tracker:
    """Single entry point for rounds, /tg, mute, follow and new job names. Every change goes
    through the store's lock, so the gateway and the cron tick can use it at the same time."""

    def __init__(self, store: Store, sources: list[Source], clock: Callable[[], float] = time.time):
        self.store = store
        self.sources = sources
        self.clock = clock

    def round(self, commit: bool = True) -> str:
        """One progress round: a line per followed active job, one finish line per job.
        Empty means nothing to say. commit=False previews without recording."""
        jobs, issues = collect(self.sources)
        if commit:
            with self.store.edit() as state:
                lines = advance(state, jobs, self.clock())
        else:
            lines = advance(self.store.read(), jobs, self.clock())
        if any(not line.startswith(("✅", "⚠️", "❌")) for line in lines):
            lines.append(render.HINT)
        return "\n".join(lines + render.problems(issues, "⚠️ tailgate: "))

    def take_baseline(self) -> int:
        """Record today's jobs as history so they are never announced. Returns jobs known."""
        jobs, _ = collect(self.sources)
        with self.store.edit() as state:
            merge(state, jobs, self.clock())
            return len(state["jobs"])

    def list(self) -> str:
        if not self.sources:
            return render.NO_SOURCES
        jobs, issues = collect(self.sources)
        with self.store.edit() as state:
            merge(state, jobs, self.clock())
            numbers = {name: e["n"] for name, e in state["jobs"].items() if "n" in e}
            muted = {name for name, e in state["jobs"].items() if e.get("muted")}
        return "\n".join([render.job_list(jobs, numbers, muted)] + render.problems(issues))

    def mute(self, target: str) -> str:
        return self._set_muted(target, True)

    def follow(self, target: str) -> str:
        return self._set_muted(target, False)

    def new_name(self, topic: str) -> str:
        """A fresh, never-reused job name for the agent to use before it hands a job off."""
        jobs, _ = collect(self.sources)
        with self.store.edit() as state:
            return reserve_name(state, topic, [j.name for j in jobs], self.clock())

    def _set_muted(self, target: str, muted: bool) -> str:
        jobs, issues = collect(self.sources)
        with self.store.edit() as state:
            merge(state, jobs, self.clock())
            reply = set_muted(state, jobs, target, muted)
        return "\n".join([reply] + render.problems(issues))


def number(state: dict, name: str) -> int:
    """The job's short number (#12): handed out once, never reused."""
    entry = state["jobs"].setdefault(name, {"muted": False, "reserved": False, "announced": False})
    if "n" not in entry:
        entry["n"] = state["next_n"] = state.get("next_n", 0) + 1
    return entry["n"]


def merge(state: dict, jobs: list[Job], now: float) -> None:
    """Record a round of reports. The first round ever is the baseline: jobs already finished
    then are history and never announced. Every later new job gets its finish line, even one
    that starts and ends between two rounds."""
    known = state["jobs"]
    baseline = not state.get("baselined")
    for job in jobs:
        entry = known.get(job.name)
        if entry is None:
            entry = known[job.name] = {"muted": False, "reserved": False, "first_seen": now,
                                       "announced": baseline and job.state.finished}
        entry.update(source=job.source, state=job.state.value, last_seen=now)
        number(state, job.name)
    state["baselined"] = True
    for name in [k for k, v in known.items()
                 if now - v.get("last_seen", v.get("first_seen", now)) > FORGET_AFTER_S]:
        del known[name]


def advance(state: dict, jobs: list[Job], now: float) -> list[str]:
    """Record a round and return its lines."""
    merge(state, jobs, now)
    lines: list[str] = []
    for job in sorted(jobs, key=lambda j: j.name):
        entry = state["jobs"][job.name]
        if job.state.finished:
            if not entry.get("announced"):
                entry["announced"] = True
                lines.append(render.finish_line(job, entry.get("n")))
        elif not entry.get("muted"):
            lines.append(render.progress_line(job, entry.get("n")))
    return lines


def reserve_name(state: dict, topic: str, taken: Iterable[str], now: float) -> str:
    """`<up to three words of the topic>-<n>`, numbered above anything seen or handed out."""
    words = [w for w in TOPIC_CHARS.split(re.sub(r"\(.*?\)", " ", topic.lower()))
             if w and w not in STOPWORDS][:NAME_WORDS]
    stem = "-".join(words)[:NAME_CHARS].strip("-") or "job"
    numbered = re.compile(rf"^{re.escape(stem)}-(\d+)$")
    used = [int(m.group(1)) for k in [*state["jobs"], *taken] if (m := numbered.match(k))]
    counters = state.setdefault("counters", {})
    n = max(max(used, default=0), counters.get(stem, 0)) + 1
    counters[stem] = n
    name = f"{stem}-{n}"
    state["jobs"][name] = {"muted": False, "reserved": True, "announced": False,
                           "first_seen": now, "last_seen": now}
    number(state, name)
    return name


def set_muted(state: dict, jobs: list[Job], target: str, muted: bool) -> str:
    """target: a number (12 or #12), a name, or nothing when exactly one job is running."""
    verb = "mute" if muted else "follow"
    active = [j for j in jobs if j.state.active]
    target = target.strip()
    if not target:
        if len(active) != 1:
            return render.usage(verb, [(number(state, j.name), j.name) for j in active])
        job = active[0]
    elif m := NUMBER.match(target):
        by_number = {state["jobs"].get(j.name, {}).get("n"): j for j in jobs}
        job = by_number.get(int(m.group(1)))
        if job is None:
            return render.no_number(m.group(1))
    elif NAME.match(target):
        job = next((j for j in jobs if j.name == target), None)
        if job is None:
            return render.no_name(target)
    else:
        return render.not_a_target(target)
    n = number(state, job.name)
    if job.state.finished:
        return render.already_finished(n, job, verb)
    state["jobs"][job.name]["muted"] = muted
    return render.muted(n, job.name) if muted else render.following(n, job.name)
