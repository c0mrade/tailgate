"""tailgate core: job sources, shared state, and the progress tick.

Plain Python with no Hermes imports. Hermes internals are not part of the plugin contract, and the
tick runs outside the plugin (a no-agent cron script), so both sides share this module and one
locked JSON file in the plugin's data directory.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator

JOB_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,62}$")
TOPIC_CHARS = re.compile(r"[^a-z0-9]+")
NUMBER = re.compile(r"^#?(\d{1,6})$")
# Filler dropped from tool topics so ids stay short enough to read (and type) on a phone.
STOPWORDS = frozenset("a an and as at by doc docs for from in into of on or pr the to with add adds "
                      "create creates make new".split())
STEM_WORDS, STEM_CHARS = 3, 24
STATES = ("queued", "running", "done", "incomplete", "failed")
ACTIVE = frozenset({"queued", "running"})
FINISHED = frozenset({"done", "incomplete", "failed"})
ICONS = {"queued": "⏳", "running": "⏳", "done": "✅", "incomplete": "⚠️", "failed": "❌"}
TEXT_LIMIT = 120
SOURCE_TIMEOUT_S = 30
FORGET_AFTER_S = 14 * 24 * 3600  # drop state for jobs no source has reported for two weeks
CONTROL = re.compile(r"[\x00-\x1f\x7f]")

# Appended to rounds that contain progress lines, the only time muting is relevant.
HINT = "\n/tg mute <number> to silence one · /tg for all jobs"

STATE_FILE = "jobs.json"
SETTINGS_FILE = "settings.json"


# --- Jobs as reported by sources -------------------------------------------------------------


@dataclass(frozen=True)
class Job:
    id: str
    state: str
    source: str
    elapsed_s: int | None = None
    progress: str = ""
    last: str = ""


def clean_text(value: Any) -> str:
    """Text from a job source may come from a machine running untrusted code. It is only ever shown
    to the user, never given to the model; still strip control characters and cap the length."""
    if not isinstance(value, str):
        return ""
    text = CONTROL.sub(" ", value).strip()
    return text if len(text) <= TEXT_LIMIT else text[: TEXT_LIMIT - 1] + "…"


def parse_source_output(source: str, output: str) -> tuple[list[Job], list[str]]:
    """One JSON object per line: {"id", "state", "elapsed_s"?, "progress"?, "last"?}.
    Returns the valid jobs and a list of problems (bad lines are skipped, not fatal)."""
    jobs: list[Job] = []
    problems: list[str] = []
    for n, line in enumerate(output.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            problems.append(f"{source}: line {n} is not JSON")
            continue
        if not isinstance(obj, dict):
            problems.append(f"{source}: line {n} is not an object")
            continue
        job_id, state = obj.get("id"), obj.get("state")
        if not isinstance(job_id, str) or not JOB_ID.match(job_id):
            problems.append(f"{source}: line {n} has an invalid id")
            continue
        if state not in STATES:
            problems.append(f"{source}: {job_id} has unknown state {str(state)[:20]!r}")
            continue
        elapsed = obj.get("elapsed_s")
        elapsed = int(elapsed) if isinstance(elapsed, (int, float)) and elapsed >= 0 else None
        jobs.append(Job(job_id, state, source, elapsed, clean_text(obj.get("progress")),
                        clean_text(obj.get("last"))))
    return jobs, problems


def validate_sources(raw: Any) -> list[dict]:
    """Sources come from `plugins.entries.tailgate.settings.sources`: a list of
    {"name": str, "command": [argv...]}. The command is run without a shell."""
    sources = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        name, command = item.get("name"), item.get("command")
        if (isinstance(name, str) and JOB_ID.match(name) and isinstance(command, list) and command
                and all(isinstance(part, str) and part for part in command)):
            sources.append({"name": name, "command": list(command)})
    return sources


def run_source(source: dict, timeout: float = SOURCE_TIMEOUT_S) -> tuple[list[Job], list[str]]:
    try:
        result = subprocess.run(source["command"], capture_output=True, text=True,
                                timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return [], [f"{source['name']}: no answer within {timeout:.0f} s"]
    except OSError as e:
        return [], [f"{source['name']}: cannot run ({e.strerror or e})"]
    if result.returncode != 0:
        detail = clean_text(result.stderr) or f"exit {result.returncode}"
        return [], [f"{source['name']}: failed ({detail})"]
    return parse_source_output(source["name"], result.stdout)


def collect(sources: Iterable[dict]) -> tuple[list[Job], list[str]]:
    jobs: list[Job] = []
    problems: list[str] = []
    for source in sources:
        found, issues = run_source(source)
        jobs.extend(found)
        problems.extend(issues)
    return jobs, problems


# --- Shared state ----------------------------------------------------------------------------


def empty_state() -> dict:
    # jobs: id -> {"source", "state", "muted", "announced", "reserved", "first_seen", "last_seen"}
    # baselined: the first round has run (see merge); counters: last number handed out per topic
    return {"version": 1, "jobs": {}}


class Store:
    """jobs.json in the plugin data dir, read-modify-write under an exclusive file lock, written
    atomically. Shared by the gateway (slash commands, tool) and the cron tick (another process)."""

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
            # Never crash the gateway over a damaged state file; start over, keep the evidence.
            with contextlib.suppress(OSError):
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


def save_settings(data_dir: Path | str, sources: list[dict]) -> None:
    """Snapshot of the plugin settings for the cron tick, which cannot read plugin config itself."""
    path = Path(data_dir)
    path.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path, prefix=".settings.", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump({"sources": sources}, handle, indent=1)
    os.replace(tmp, path / SETTINGS_FILE)


def load_settings(data_dir: Path | str) -> list[dict]:
    try:
        raw = json.loads((Path(data_dir) / SETTINGS_FILE).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return validate_sources(raw.get("sources") if isinstance(raw, dict) else None)


# --- Operations ------------------------------------------------------------------------------


def duration(seconds: int | None) -> str:
    if seconds is None:
        return ""
    minutes = max(seconds, 0) // 60
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m" if hours else f"{minutes}m"


def number(state: dict, job_id: str) -> int:
    """The job's short number (#11): handed out once, never reused, typed instead of the id."""
    entry = state["jobs"].setdefault(job_id, {"muted": False, "reserved": False, "announced": False})
    if "n" not in entry:
        entry["n"] = state["next_n"] = state.get("next_n", 0) + 1
    return entry["n"]


def merge(state: dict, jobs: list[Job], now: float) -> None:
    """Record what the sources reported. The first round ever is the baseline: jobs already
    finished then ended before tailgate existed and are not announced. After that, every job
    tailgate has not seen before is new and gets its finish line, even if it is already finished
    when first seen (a short job between two rounds). No tool call needed for that."""
    known = state["jobs"]
    baseline = not state.get("baselined")
    for job in jobs:
        entry = known.get(job.id)
        if entry is None:
            entry = known[job.id] = {"muted": False, "reserved": False, "first_seen": now,
                                     "announced": baseline and job.state in FINISHED}
        entry.update(source=job.source, state=job.state, last_seen=now)
        number(state, job.id)
    state["baselined"] = True
    for job_id in [k for k, v in known.items()
                   if now - v.get("last_seen", v.get("first_seen", now)) > FORGET_AFTER_S]:
        del known[job_id]


def reserve_id(state: dict, topic: str, taken: Iterable[str], now: float) -> str:
    """Unique `<topic>-<n>`, above every number any source reports or tailgate has handed out."""
    words = [w for w in TOPIC_CHARS.split(re.sub(r"\(.*?\)", " ", topic.lower()))
             if w and w not in STOPWORDS][:STEM_WORDS]
    stem = "-".join(words)[:STEM_CHARS].strip("-") or "job"
    # Numbers only go up, so an id is never handed out twice, even after its job is forgotten.
    numbered = re.compile(rf"^{re.escape(stem)}-(\d+)$")
    used = [int(m.group(1)) for k in [*state["jobs"], *taken] if (m := numbered.match(k))]
    n = max(used, default=0) + 1
    counters = state.setdefault("counters", {})
    n = max(n, counters.get(stem, 0) + 1)
    counters[stem] = n
    job_id = f"{stem}-{n}"
    state["jobs"][job_id] = {"muted": False, "reserved": True, "announced": False,
                             "first_seen": now, "last_seen": now}
    number(state, job_id)
    return job_id


def label(job: Job, n: int | None) -> str:
    return f"{ICONS[job.state]} #{n} name: {job.id}" if n else f"{ICONS[job.state]} name: {job.id}"


def progress_line(job: Job, n: int | None = None) -> str:
    parts = [label(job, n), f"{job.state} {duration(job.elapsed_s)}".strip()]
    if job.state != "queued":  # nothing has happened yet: no "0 events", no last step
        if job.progress:
            parts.append(job.progress)
        if job.last:
            parts.append(f"last: {job.last}")
    return " · ".join(parts)


def finish_line(job: Job, n: int | None = None) -> str:
    took = f" after {duration(job.elapsed_s)}" if job.elapsed_s is not None else ""
    what = {"done": f"done{took}", "incomplete": f"stopped without finishing{took}",
            "failed": f"failed{took}"}[job.state]
    parts = [label(job, n), what]
    if job.progress:
        parts.append(job.progress)
    if job.state == "done":
        parts.append(f"ask Hermes about {job.id}")
    return " · ".join(parts)


def advance(state: dict, jobs: list[Job], now: float) -> list[str]:
    """Record a round of reports and return what to tell the user: a line per followed active job,
    a finish line once per job."""
    merge(state, jobs, now)
    lines: list[str] = []
    for job in sorted(jobs, key=lambda j: j.id):
        entry = state["jobs"][job.id]
        if job.state in FINISHED:
            if not entry.get("announced"):
                entry["announced"] = True
                lines.append(finish_line(job, entry.get("n")))
        elif not entry.get("muted"):
            lines.append(progress_line(job, entry.get("n")))
    return lines


def take_baseline(store: Store, sources: list[dict], now: float | None = None) -> int:
    """Record the jobs that exist right now as history, so they are never announced. Run by
    `hermes tailgate setup`; returns how many jobs were already known or recorded."""
    jobs, _ = collect(sources)
    with store.edit() as state:
        merge(state, jobs, time.time() if now is None else now)
        return len(state["jobs"])


def tick(store: Store, sources: list[dict], now: float | None = None, commit: bool = True) -> str:
    """One progress round. Empty string means nothing to say (Hermes treats empty stdout as a silent
    tick). commit=False previews the round without recording it."""
    now = time.time() if now is None else now
    jobs, problems = collect(sources)
    if commit:
        with store.edit() as state:
            lines = advance(state, jobs, now)
    else:
        lines = advance(store.read(), jobs, now)
    if any(not line.startswith(("✅", "⚠️", "❌")) for line in lines):
        lines.append(HINT)
    lines.extend(f"⚠️ tailgate: {p}" for p in problems)
    return "\n".join(lines)


def list_jobs(state: dict, jobs: list[Job]) -> str:
    if not jobs:
        return "No jobs."
    lines = []
    for job in sorted(jobs, key=lambda j: (j.state not in ACTIVE, j.id)):
        n = state["jobs"].get(job.id, {}).get("n")
        line = f"{label(job, n)} · {job.state} {duration(job.elapsed_s)}".rstrip()
        if job.state in ACTIVE:
            muted = state["jobs"].get(job.id, {}).get("muted")
            line += " · 🔕 muted" if muted else " · 🔔 following"
        lines.append(line)
    return "\n".join(lines)


def set_muted(state: dict, jobs: list[Job], target: str, muted: bool) -> str:
    """target: a job's number (11 or #11), its id, or nothing when exactly one job is running."""
    verb = "mute" if muted else "follow"
    active = [j for j in jobs if j.state in ACTIVE]
    target = target.strip()
    if not target:
        if len(active) != 1:
            running = ", ".join(f"#{number(state, j.id)} {j.id}" for j in active) or "none"
            return f"Usage: /tg {verb} <number>. Running: {running}"
        job = active[0]
    elif m := NUMBER.match(target):
        by_number = {state["jobs"].get(j.id, {}).get("n"): j for j in jobs}
        job = by_number.get(int(m.group(1)))
        if job is None:
            return f"No job #{m.group(1)}. See /tg."
    elif JOB_ID.match(target):
        job = next((j for j in jobs if j.id == target), None)
        if job is None:
            return f"No job called {target}. See /tg."
    else:
        return f"'{target[:64]}' is not a job number or name."
    n = number(state, job.id)
    if job.state in FINISHED:
        return f"#{n} {job.id} has already finished ({job.state}); nothing to {verb}."
    state["jobs"][job.id]["muted"] = muted
    if muted:
        return (f"🔕 #{n} {job.id} muted: no more progress updates. You will still hear when it "
                f"finishes. /tg follow {n} to undo.")
    return f"🔔 Following #{n} {job.id}."
