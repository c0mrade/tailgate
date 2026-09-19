"""Shared fixtures: the plugin loaded as Hermes loads it, fake job sources and a fake Hermes context."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))  # `import tailgate_core`, as the cron tick script does

from tailgate_core import Source, Store, Tracker  # noqa: E402

RUNNING = {"id": "intraday-1", "state": "running", "elapsed_s": 3900, "progress": "83 events",
           "last": "bundle exec rspec"}


@pytest.fixture
def plugin():
    spec = importlib.util.spec_from_file_location(
        "tailgate_plugin", REPO / "__init__.py", submodule_search_locations=[str(REPO)])
    module = importlib.util.module_from_spec(spec)
    sys.modules["tailgate_plugin"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def source(tmp_path):
    """A job source whose output the test controls: source(job, job, ...) -> Source."""
    out = tmp_path / "source-output.jsonl"
    script = tmp_path / "source.py"
    script.write_text(f"import sys; sys.stdout.write(open({str(out)!r}).read())\n")

    def make(*jobs, name="fake", raw=None):
        out.write_text(raw if raw is not None else "".join(json.dumps(j) + "\n" for j in jobs))
        return Source(name, (sys.executable, str(script)))

    return make


@pytest.fixture
def tracker(tmp_path):
    """tracker(sources, now=...) -> Tracker on a fresh store with a fixed clock."""
    def make(sources, now=1000.0):
        return Tracker(Store(tmp_path / "data"), sources, clock=lambda: now)
    return make


class FakeState:
    def __init__(self, data_dir):
        self.data_dir = data_dir


class FakeCtx:
    """The parts of Hermes's PluginContext tailgate uses."""

    def __init__(self, data_dir, sources):
        self.state = FakeState(data_dir)
        self._config = {"sources": sources}
        self.commands, self.tools, self.cli, self.prompt_sections = {}, {}, {}, {}

    def get_config(self, key, default=None):
        return self._config.get(key, default)

    def register_command(self, name, handler, description="", args_hint=""):
        self.commands[name] = handler

    def register_tool(self, name, toolset, schema, handler, **kwargs):
        self.tools[name] = (schema, handler)

    def register_system_prompt_section(self, id, content, *, position="after_memory", max_chars=4000):
        assert len(content) <= max_chars
        self.prompt_sections[id] = content

    def register_cli_command(self, name, help, setup_fn, handler_fn=None, description=""):
        self.cli[name] = (setup_fn, handler_fn)


@pytest.fixture
def make_ctx(tmp_path):
    def make(sources):
        return FakeCtx(tmp_path / "plugin-data" / "tailgate", [s.to_config() for s in sources])
    return make
