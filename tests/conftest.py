"""Load tailgate the way Hermes does: as a package from the repo directory."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))  # for `import tailgate_core`, as the cron tick script does


def load_plugin():
    spec = importlib.util.spec_from_file_location(
        "tailgate_plugin", REPO / "__init__.py", submodule_search_locations=[str(REPO)])
    module = importlib.util.module_from_spec(spec)
    sys.modules["tailgate_plugin"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def source(tmp_path):
    """A job source whose output the test controls: write jobs, get a source dict."""
    out = tmp_path / "source-output.jsonl"
    script = tmp_path / "source.py"
    script.write_text(f"import sys; sys.stdout.write(open({str(out)!r}).read())\n")

    def make(*jobs, name="fake", raw=None):
        out.write_text(raw if raw is not None else "".join(json.dumps(j) + "\n" for j in jobs))
        return {"name": name, "command": [sys.executable, str(script)]}

    return make


class FakeState:
    def __init__(self, data_dir):
        self.data_dir = data_dir


class FakeCtx:
    """The parts of Hermes's PluginContext tailgate uses."""

    def __init__(self, data_dir, sources):
        self.state = FakeState(data_dir)
        self._config = {"sources": sources}
        self.commands, self.tools, self.cli = {}, {}, {}

    def get_config(self, key, default=None):
        return self._config.get(key, default)

    def register_command(self, name, handler, description="", args_hint=""):
        self.commands[name] = handler

    def register_tool(self, name, toolset, schema, handler, **kwargs):
        self.tools[name] = (schema, handler)

    def register_cli_command(self, name, help, setup_fn, handler_fn=None, description=""):
        self.cli[name] = (setup_fn, handler_fn)


@pytest.fixture
def plugin():
    return load_plugin()


@pytest.fixture
def make_ctx(tmp_path):
    def make(sources):
        return FakeCtx(tmp_path / "plugin-data" / "tailgate", sources)
    return make
