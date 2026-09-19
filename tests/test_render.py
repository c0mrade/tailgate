from tailgate_core import render
from tailgate_core.models import Job, JobState


def test_queued_lines_hide_progress_and_last():
    job = Job("new-1", JobState.QUEUED, "src", 30, "0 events", "nothing yet")
    assert render.progress_line(job) == "⏳ name: new-1 · queued 0m"


def test_duration():
    assert render.duration(None) == ""
    assert render.duration(59) == "0m"
    assert render.duration(3900) == "1h 05m"


def test_job_list_marks_following_and_muted():
    jobs = [Job("a-1", JobState.RUNNING, "s", 60), Job("b-1", JobState.RUNNING, "s", 60),
            Job("old-1", JobState.DONE, "s", 60)]
    text = render.job_list(jobs, {"a-1": 1, "b-1": 2, "old-1": 3}, muted={"b-1"})
    assert text.splitlines() == ["⏳ #1 name: a-1 · running 1m · 🔔 following",
                                 "⏳ #2 name: b-1 · running 1m · 🔕 muted",
                                 "✅ #3 name: old-1 · done 1m"]
