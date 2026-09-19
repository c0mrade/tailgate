import multiprocessing

from tailgate_core import Source, Store, load_settings, save_settings
from tailgate_core.store import STATE_FILE, empty_state


def test_corrupt_state_file_is_set_aside(tmp_path):
    (tmp_path / STATE_FILE).write_text("{not json")
    assert Store(tmp_path).read() == empty_state()
    assert (tmp_path / "jobs.json.corrupt").exists()


def _bump(path, n):
    store = Store(path)
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
    assert Store(tmp_path).read()["jobs"]["c-1"]["count"] == 200


def test_settings_snapshot_round_trip(tmp_path):
    save_settings(tmp_path, [Source("a", ("echo",))])
    assert load_settings(tmp_path) == [Source("a", ("echo",))]
    assert load_settings(tmp_path / "missing") == []
