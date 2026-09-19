from conftest import RUNNING

from tailgate_core import render
from tailgate_core import tracker as tr
from tailgate_core.models import JobState
from tailgate_core.store import empty_state


def test_round_progress_then_single_finish_line(tracker, source):
    t = tracker([source(RUNNING)])
    line = "⏳ #1 name: intraday-1 · running 1h 05m · 83 events · last: bundle exec rspec"
    assert t.round() == line + "\n" + render.HINT
    t.sources = [source(dict(RUNNING, state="done", elapsed_s=7200))]
    assert t.round() == "✅ #1 name: intraday-1 · done after 2h 00m · 83 events · ask Hermes about intraday-1"
    assert t.round() == ""  # announced once


def test_first_round_is_the_baseline(tracker, source):
    assert tracker([source({"id": "old-1", "state": "done", "elapsed_s": 60})]).round() == ""


def test_short_job_after_the_baseline_is_announced_without_the_tool(tracker, source):
    old = {"id": "old-1", "state": "done", "elapsed_s": 60}
    t = tracker([source(old)])
    t.take_baseline()
    t.sources = [source(old, {"id": "quick-1", "state": "done", "elapsed_s": 90})]
    assert t.round() == "✅ #2 name: quick-1 · done after 1m · ask Hermes about quick-1"


def test_reserved_job_is_announced_even_if_first_seen_finished(tracker, source):
    t = tracker([source()])
    name = t.new_name("quick")
    t.sources = [source({"id": name, "state": "incomplete", "elapsed_s": 120})]
    assert t.round() == f"⚠️ #1 name: {name} · stopped without finishing after 2m"


def test_mute_silences_progress_but_not_the_finish_line(tracker, source):
    t = tracker([source(RUNNING)])
    assert t.mute("1").startswith("🔕 #1 intraday-1 muted")
    assert t.round() == ""
    t.sources = [source(dict(RUNNING, state="failed"))]
    assert t.round().startswith("❌ #1 name: intraday-1 · failed")


def test_mute_by_number_by_name_and_without_either(tracker, source):
    t = tracker([source(RUNNING)])
    assert t.mute("#1").startswith("🔕 #1 intraday-1 muted")
    assert t.follow("1") == "🔔 Following #1 intraday-1."
    assert t.mute("intraday-1").startswith("🔕 #1")
    assert t.follow("") == "🔔 Following #1 intraday-1."  # the only running job


def test_mute_refusals(tracker, source):
    t = tracker([source(RUNNING, {"id": "old-1", "state": "done"}, {"id": "other-1", "state": "running"})])
    assert t.mute("") == "Usage: /tg mute <number>. Running: #1 intraday-1, #3 other-1"
    assert "not a job number or name" in t.mute("../evil")
    assert "No job called" in t.mute("nope-1")
    assert t.mute("#9") == "No job #9. See /tg."
    assert "already finished" in t.mute("2")
    assert not any(e["muted"] for e in t.store.read()["jobs"].values())


def test_numbers_are_never_reused(tracker, source):
    t = tracker([source(RUNNING)], now=0)
    t.round()
    t.clock = lambda: tr.FORGET_AFTER_S + 1
    t.sources = [source()]
    t.round()  # intraday-1 forgotten
    t.sources = [source({"id": "next-1", "state": "running"})]
    t.round()
    assert t.store.read()["jobs"]["next-1"]["n"] == 2


def test_dry_run_does_not_record(tracker, source):
    t = tracker([source()])
    t.new_name("intraday")  # makes intraday-1 announceable
    t.sources = [source(dict(RUNNING, state="done"))]
    assert t.round(commit=False).startswith("✅")
    assert t.round().startswith("✅")  # still unannounced after the preview


def test_new_names_are_short_unique_and_never_reused():
    state = empty_state()
    assert tr.reserve_name(state, "Intraday App!", ["intraday-app-1"], now=0) == "intraday-app-2"
    assert tr.reserve_name(state, "Intraday App!", [], now=0) == "intraday-app-3"
    assert tr.reserve_name(state, "!!!", [], now=0) == "job-1"
    topic = "intraday: add minute-bars storage design doc (docs/designs/x.md) as a PR"
    assert tr.reserve_name(state, topic, [], now=0) == "intraday-minute-bars-1"
    assert len(tr.reserve_name(state, "x" * 80, [], now=0)) <= tr.NAME_CHARS + 3


def test_list_without_sources(tracker):
    assert tracker([]).list() == render.NO_SOURCES
    assert JobState("done").finished
