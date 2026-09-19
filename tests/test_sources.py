import json

from conftest import RUNNING

from tailgate_core import Source, collect
from tailgate_core.models import JobState
from tailgate_core.sources import TEXT_LIMIT


def test_parse_skips_bad_lines_and_reports_them():
    raw = "\n".join([
        json.dumps(RUNNING),
        "not json",
        json.dumps(["not", "an", "object"]),
        json.dumps({"id": "../evil", "state": "running"}),
        json.dumps({"id": "ok-2", "state": "exploded"}),
        json.dumps({"id": "ok-3", "state": "done", "elapsed_s": -5}),
    ])
    jobs, problems = Source("src", ("x",)).parse(raw)
    assert [j.name for j in jobs] == ["intraday-1", "ok-3"]
    assert jobs[0].state is JobState.RUNNING
    assert jobs[1].elapsed_s is None
    assert len(problems) == 4


def test_untrusted_text_is_cleaned():
    jobs, _ = Source("src", ("x",)).parse(json.dumps(
        {"id": "a-1", "state": "running", "last": "evil\x1b[31m\nnext" + "x" * 500}))
    assert "\x1b" not in jobs[0].last and "\n" not in jobs[0].last
    assert len(jobs[0].last) == TEXT_LIMIT


def test_from_config_rejects_shell_strings_and_bad_names():
    assert Source.from_config([
        {"name": "ok", "command": ["echo", "hi"]},
        {"name": "shell", "command": "echo hi; echo bye"},
        {"name": "Bad Name", "command": ["echo"]},
        {"name": "empty", "command": []},
        "nonsense",
    ]) == [Source("ok", ("echo", "hi"))]
    assert Source.from_config(None) == []


def test_failing_and_missing_sources_become_problems(tmp_path):
    jobs, problems = collect([Source("missing", (str(tmp_path / "nope"),)), Source("fails", ("false",))])
    assert jobs == [] and len(problems) == 2
