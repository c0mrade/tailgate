import json
import multiprocessing

import tailgate_core as core

RUNNING = {"id": "intraday-1", "state": "running", "elapsed_s": 3900, "progress": "83 events",
           "last": "bundle exec rspec"}


def test_parse_skips_bad_lines_and_reports_them():
    raw = "\n".join([
        json.dumps(RUNNING),
        "not json",
        json.dumps(["not", "an", "object"]),
        json.dumps({"id": "../evil", "state": "running"}),
        json.dumps({"id": "ok-2", "state": "exploded"}),
        json.dumps({"id": "ok-3", "state": "done", "elapsed_s": -5}),
    ])
    jobs, problems = core.parse_source_output("src", raw)
    assert [j.id for j in jobs] == ["intraday-1", "ok-3"]
    assert jobs[1].elapsed_s is None
    assert len(problems) == 4


def test_untrusted_text_is_cleaned():
    jobs, _ = core.parse_source_output("src", json.dumps(
        {"id": "a-1", "state": "running", "last": "evil\x1b[31m\nnext" + "x" * 500}))
    assert "\x1b" not in jobs[0].last and "\n" not in jobs[0].last
    assert len(jobs[0].last) == core.TEXT_LIMIT


def test_validate_sources_rejects_shell_strings_and_bad_names():
    assert core.validate_sources([
        {"name": "ok", "command": ["echo", "hi"]},
        {"name": "shell", "command": "echo hi; echo bye"},
        {"name": "Bad Name", "command": ["echo"]},
        {"name": "empty", "command": []},
        "nonsense",
    ]) == [{"name": "ok", "command": ["echo", "hi"]}]
    assert core.validate_sources(None) == []


def test_failing_and_missing_sources_become_problems(tmp_path):
    jobs, problems = core.collect([
        {"name": "missing", "command": [str(tmp_path / "does-not-exist")]},
        {"name": "fails", "command": ["false"]},
    ])
    assert jobs == [] and len(problems) == 2


def test_tick_progress_then_single_finish_line(tmp_path, source):
    store = core.Store(tmp_path)
    out = core.tick(store, [source(RUNNING)], now=1000)
    assert out == "⏳ #1 name: intraday-1 · running 1h 05m · 83 events · last: bundle exec rspec\n" + core.HINT
    done = dict(RUNNING, state="done", elapsed_s=7200)
    assert core.tick(store, [source(done)], now=1300) == "✅ #1 name: intraday-1 · done after 2h 00m · 83 events · ask Hermes about intraday-1"
    assert core.tick(store, [source(done)], now=1600) == ""  # announced once


def test_first_round_is_the_baseline(tmp_path, source):
    store = core.Store(tmp_path)
    old = {"id": "old-1", "state": "done", "elapsed_s": 60}
    assert core.tick(store, [source(old)], now=1000) == ""  # finished before tailgate existed


def test_short_job_after_the_baseline_is_announced_without_the_tool(tmp_path, source):
    store = core.Store(tmp_path)
    old = {"id": "old-1", "state": "done", "elapsed_s": 60}
    core.take_baseline(store, [source(old)], now=1000)
    quick = {"id": "quick-1", "state": "done", "elapsed_s": 90}  # started and ended between rounds
    assert core.tick(store, [source(old, quick)], now=1300) == "✅ #2 name: quick-1 · done after 1m · ask Hermes about quick-1"


def test_reserved_job_is_announced_even_if_first_seen_finished(tmp_path, source):
    store = core.Store(tmp_path)
    with store.edit() as state:
        job_id = core.reserve_id(state, "quick", [], now=1000)
    finished = {"id": job_id, "state": "incomplete", "elapsed_s": 120}
    assert core.tick(store, [source(finished)], now=1100) == f"⚠️ #1 name: {job_id} · stopped without finishing after 2m"


def test_mute_silences_progress_but_not_the_finish_line(tmp_path, source):
    store = core.Store(tmp_path)
    src = source(RUNNING)
    jobs, _ = core.collect([src])
    with store.edit() as state:
        assert "muted" in core.set_muted(state, jobs, "intraday-1", True)
    assert core.tick(store, [src], now=1000) == ""
    assert core.tick(store, [source(dict(RUNNING, state="failed"))], now=1300).startswith("❌ #1 name: intraday-1 · failed")


def test_set_muted_refusals(tmp_path, source):
    jobs, _ = core.collect([source(RUNNING, {"id": "old-1", "state": "done"},
                                   {"id": "other-1", "state": "running"})])
    state = core.empty_state()
    core.merge(state, jobs, now=0)  # numbers: intraday-1 #1, old-1 #2, other-1 #3
    assert core.set_muted(state, jobs, "", True) == "Usage: /tg mute <number>. Running: #1 intraday-1, #3 other-1"
    assert "not a job number or name" in core.set_muted(state, jobs, "../evil", True)
    assert "No job called" in core.set_muted(state, jobs, "nope-1", True)
    assert core.set_muted(state, jobs, "#9", True) == "No job #9. See /tg."
    assert "already finished" in core.set_muted(state, jobs, "2", True)
    assert not any(e["muted"] for e in state["jobs"].values())


def test_mute_by_number_by_id_and_without_id(tmp_path, source):
    state = core.empty_state()
    jobs, _ = core.collect([source(RUNNING)])
    core.merge(state, jobs, now=0)
    assert core.set_muted(state, jobs, "#1", True).startswith("🔕 #1 intraday-1 muted")
    assert core.set_muted(state, jobs, "1", False) == "🔔 Following #1 intraday-1."
    assert core.set_muted(state, jobs, "intraday-1", True).startswith("🔕 #1")
    assert core.set_muted(state, jobs, "", False) == "🔔 Following #1 intraday-1."  # only one running


def test_numbers_are_never_reused(tmp_path, source):
    store = core.Store(tmp_path)
    core.tick(store, [source(RUNNING)], now=0)
    core.tick(store, [source()], now=core.FORGET_AFTER_S + 1)  # intraday-1 forgotten
    core.tick(store, [source({"id": "next-1", "state": "running"})], now=core.FORGET_AFTER_S + 2)
    assert store.read()["jobs"]["next-1"]["n"] == 2


def test_reserve_id_is_unique_against_sources_and_state():
    state = core.empty_state()
    assert core.reserve_id(state, "Intraday App!", ["intraday-app-1"], now=0) == "intraday-app-2"
    assert core.reserve_id(state, "Intraday App!", [], now=0) == "intraday-app-3"
    assert core.reserve_id(state, "!!!", [], now=0) == "job-1"


def test_reserve_id_turns_sentences_into_short_ids():
    state = core.empty_state()
    topic = "intraday: add minute-bars storage design doc (docs/designs/minute-bars-storage/design.md) as a PR"
    assert core.reserve_id(state, topic, [], now=0) == "intraday-minute-bars-1"
    assert len(core.reserve_id(state, "x" * 80, [], now=0)) <= core.STEM_CHARS + 3


def test_dry_run_does_not_record(tmp_path, source):
    store = core.Store(tmp_path)
    src = source(dict(RUNNING, state="done"))
    with store.edit() as state:
        core.reserve_id(state, "intraday", [], now=0)  # makes intraday-1 announceable
    assert core.tick(store, [src], now=10, commit=False).startswith("✅")
    assert core.tick(store, [src], now=20).startswith("✅")  # still unannounced after the preview


def test_forgotten_after_two_weeks(tmp_path, source):
    store = core.Store(tmp_path)
    core.tick(store, [source(RUNNING)], now=0)
    core.tick(store, [source()], now=core.FORGET_AFTER_S + 1)
    assert store.read()["jobs"] == {}


def test_corrupt_state_file_is_set_aside(tmp_path):
    (tmp_path / core.STATE_FILE).write_text("{not json")
    store = core.Store(tmp_path)
    assert store.read() == core.empty_state()
    assert (tmp_path / "jobs.json.corrupt").exists()


def _bump(path, n):
    store = core.Store(path)
    for _ in range(n):
        with store.edit() as state:
            state["jobs"].setdefault("c-1", {"count": 0})
            state["jobs"]["c-1"]["count"] += 1


def test_edits_from_several_processes_are_serialized(tmp_path):
    procs = [multiprocessing.Process(target=_bump, args=(tmp_path, 50)) for _ in range(4)]
    for p in procs:
        p.start()
    for p in procs:
        p.join()
    assert core.Store(tmp_path).read()["jobs"]["c-1"]["count"] == 200


def test_settings_snapshot_round_trip(tmp_path):
    core.save_settings(tmp_path, [{"name": "a", "command": ["echo"]}])
    assert core.load_settings(tmp_path) == [{"name": "a", "command": ["echo"]}]
    assert core.load_settings(tmp_path / "missing") == []


def test_queued_lines_hide_progress_and_last():
    job = core.Job("new-1", "queued", "src", 30, "0 events", "nothing yet")
    assert core.progress_line(job) == "⏳ name: new-1 · queued 0m"
