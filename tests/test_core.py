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
    assert out == "⏳ intraday-1 · running 1h 05m · 83 events · last: bundle exec rspec\n" + core.HINT
    done = dict(RUNNING, state="done", elapsed_s=7200)
    assert core.tick(store, [source(done)], now=1300) == "✅ intraday-1 · done after 2h 00m · ask Hermes about intraday-1"
    assert core.tick(store, [source(done)], now=1600) == ""  # announced once


def test_jobs_already_finished_when_first_seen_are_not_announced(tmp_path, source):
    store = core.Store(tmp_path)
    old = {"id": "old-1", "state": "done", "elapsed_s": 60}
    assert core.tick(store, [source(old)], now=1000) == ""


def test_reserved_job_is_announced_even_if_first_seen_finished(tmp_path, source):
    store = core.Store(tmp_path)
    with store.edit() as state:
        job_id = core.reserve_id(state, "quick", [], now=1000)
    finished = {"id": job_id, "state": "incomplete", "elapsed_s": 120}
    assert core.tick(store, [source(finished)], now=1100) == f"⚠️ {job_id} · stopped without finishing after 2m"


def test_mute_silences_progress_but_not_the_finish_line(tmp_path, source):
    store = core.Store(tmp_path)
    src = source(RUNNING)
    jobs, _ = core.collect([src])
    with store.edit() as state:
        assert "muted" in core.set_muted(state, jobs, "intraday-1", True)
    assert core.tick(store, [src], now=1000) == ""
    assert core.tick(store, [source(dict(RUNNING, state="failed"))], now=1300).startswith("❌ intraday-1 · failed")


def test_set_muted_refusals(tmp_path, source):
    jobs, _ = core.collect([source(RUNNING, {"id": "old-1", "state": "done"})])
    state = core.empty_state()
    assert core.set_muted(state, jobs, "", True) == "Usage: /tailgate mute <job-id>. Running: intraday-1"
    assert "not a valid job id" in core.set_muted(state, jobs, "../evil", True)
    assert "No job called" in core.set_muted(state, jobs, "nope-1", True)
    assert "already finished" in core.set_muted(state, jobs, "old-1", True)
    assert state["jobs"] == {}


def test_reserve_id_is_unique_against_sources_and_state():
    state = core.empty_state()
    assert core.reserve_id(state, "Intraday App!", ["intraday-app-1"], now=0) == "intraday-app-2"
    assert core.reserve_id(state, "Intraday App!", [], now=0) == "intraday-app-3"
    assert core.reserve_id(state, "!!!", [], now=0) == "job-1"


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
