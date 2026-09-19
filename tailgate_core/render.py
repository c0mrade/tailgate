"""Every sentence a user sees, in one place."""

from __future__ import annotations

from .models import Job, JobState

ICONS = {JobState.QUEUED: "⏳", JobState.RUNNING: "⏳", JobState.DONE: "✅",
         JobState.INCOMPLETE: "⚠️", JobState.FAILED: "❌"}
HINT = "\n/tg mute <number> to silence one · /tg for all jobs"
USAGE = (
    "/tg (or /tailgate): list jobs\n"
    "/tg mute <number>: stop progress updates for one job (no number: the only running job)\n"
    "/tg follow <number>: resume them"
)
NO_SOURCES = "No job sources configured. See the tailgate README."


def duration(seconds: int | None) -> str:
    if seconds is None:
        return ""
    minutes = max(seconds, 0) // 60
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m" if hours else f"{minutes}m"


def label(job: Job, n: int | None) -> str:
    icon = ICONS[job.state]
    return f"{icon} #{n} name: {job.name}" if n else f"{icon} name: {job.name}"


def progress_line(job: Job, n: int | None = None) -> str:
    parts = [label(job, n), f"{job.state.value} {duration(job.elapsed_s)}".strip()]
    if job.state is not JobState.QUEUED:  # nothing has happened yet: no "0 events", no last step
        if job.progress:
            parts.append(job.progress)
        if job.last:
            parts.append(f"last: {job.last}")
    return " · ".join(parts)


def finish_line(job: Job, n: int | None = None) -> str:
    took = f" after {duration(job.elapsed_s)}" if job.elapsed_s is not None else ""
    what = {JobState.DONE: f"done{took}", JobState.INCOMPLETE: f"stopped without finishing{took}",
            JobState.FAILED: f"failed{took}"}[job.state]
    parts = [label(job, n), what]
    if job.progress:
        parts.append(job.progress)
    if job.state is JobState.DONE:
        parts.append(f"ask Hermes about {job.name}")
    return " · ".join(parts)


def job_list(jobs: list[Job], numbers: dict[str, int], muted: set[str]) -> str:
    if not jobs:
        return "No jobs."
    lines = []
    for job in sorted(jobs, key=lambda j: (not j.state.active, j.name)):
        line = f"{label(job, numbers.get(job.name))} · {job.state.value} {duration(job.elapsed_s)}".rstrip()
        if job.state.active:
            line += " · 🔕 muted" if job.name in muted else " · 🔔 following"
        lines.append(line)
    return "\n".join(lines)


def problems(items: list[str], prefix: str = "⚠️ ") -> list[str]:
    return [f"{prefix}{p}" for p in items]


def usage(verb: str, running: list[tuple[int, str]]) -> str:
    listed = ", ".join(f"#{n} {name}" for n, name in running) or "none"
    return f"Usage: /tg {verb} <number>. Running: {listed}"


def no_number(number: str) -> str:
    return f"No job #{number}. See /tg."


def no_name(name: str) -> str:
    return f"No job called {name}. See /tg."


def not_a_target(target: str) -> str:
    return f"'{target[:64]}' is not a job number or name."


def already_finished(n: int, job: Job, verb: str) -> str:
    return f"#{n} {job.name} has already finished ({job.state.value}); nothing to {verb}."


def muted(n: int, name: str) -> str:
    return (f"🔕 #{n} {name} muted: no more progress updates. You will still hear when it "
            f"finishes. /tg follow {n} to undo.")


def following(n: int, name: str) -> str:
    return f"🔔 Following #{n} {name}."
