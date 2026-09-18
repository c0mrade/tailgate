import asyncio
import inspect
import json
import subprocess
import sys

import tailgate_core as core

RUNNING = {"id": "intraday-1", "state": "running", "elapsed_s": 600}


def test_registers_commands_tool_and_cli(plugin, make_ctx, source):
    ctx = make_ctx([source(RUNNING)])
    plugin.register(ctx)
    assert set(ctx.commands) == {"tailgate", "tg"}
    assert set(ctx.tools) == {"tailgate_job_id"}
    assert set(ctx.cli) == {"tailgate"}
    assert "tailgate_job_id" in ctx.prompt_sections["tailgate.job-ids"]
    # Blocking handlers on the gateway loop can kill the gateway (hermes-agent#105279).
    assert all(inspect.iscoroutinefunction(h) for h in ctx.commands.values())


def test_slash_commands(plugin, make_ctx, source):
    ctx = make_ctx([source(RUNNING)])
    plugin.register(ctx)
    run = lambda args: asyncio.run(ctx.commands["tailgate"](args))
    assert run("") == "⏳ id: intraday-1 · running 10m · 🔔 following"
    assert run("mute intraday-1").startswith("🔕 intraday-1 muted")
    assert run("list") == "⏳ id: intraday-1 · running 10m · 🔕 muted"
    assert run("follow intraday-1") == "🔔 Following intraday-1."
    assert run("help") == plugin.USAGE
    assert asyncio.run(ctx.commands["tg"]("")) == run("")


def test_no_sources_configured(plugin, make_ctx):
    ctx = make_ctx([])
    plugin.register(ctx)
    assert "No job sources configured" in asyncio.run(ctx.commands["tailgate"](""))


def test_tool_reserves_unique_ids(plugin, make_ctx, source):
    ctx = make_ctx([source(RUNNING)])
    plugin.register(ctx)
    _, handler = ctx.tools["tailgate_job_id"]
    assert json.loads(handler({"topic": "intraday"})) == {"job_id": "intraday-2"}
    assert json.loads(handler({"topic": "intraday"})) == {"job_id": "intraday-3"}


def test_register_snapshots_settings_for_the_tick(plugin, make_ctx, source):
    src = source(RUNNING)
    ctx = make_ctx([src, {"name": "bad", "command": "not a list"}])
    plugin.register(ctx)
    assert core.load_settings(ctx.state.data_dir) == [src]


def test_generated_tick_script_runs_standalone(plugin, make_ctx, source, tmp_path):
    ctx = make_ctx([source(RUNNING)])
    plugin.register(ctx)
    script = tmp_path / "tailgate-tick.py"
    script.write_text(plugin._tick_script(ctx.state.data_dir))
    result = subprocess.run([sys.executable, str(script)], capture_output=True, text=True,
                            env={"PATH": "/usr/bin:/bin"})  # stripped env, as in hermes-agent#114209
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "⏳ id: intraday-1 · running 10m\n" + core.HINT


def _cli(ctx, *argv):
    import argparse
    setup_fn, handler = ctx.cli["tailgate"]
    parser = argparse.ArgumentParser()
    setup_fn(parser)
    return handler(parser.parse_args(list(argv)))


def test_cli_mute_follow_status(plugin, make_ctx, source, capsys):
    ctx = make_ctx([source(RUNNING)])
    plugin.register(ctx)
    assert _cli(ctx, "mute", "intraday-1") == 0
    assert "muted" in capsys.readouterr().out
    assert _cli(ctx, "status") == 0
    assert "🔕 muted" in capsys.readouterr().out
    assert _cli(ctx, "tick") == 0
    assert capsys.readouterr().out.strip() == "(nothing to report)"
    assert _cli(ctx, "follow", "intraday-1") == 0
    assert _cli(ctx, "tick", "--dry-run") == 0
    assert "⏳ id: intraday-1" in capsys.readouterr().out


def test_setup_passes_the_schedule_through(plugin, make_ctx, source, monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(plugin.subprocess, "run", lambda cmd, **kw: calls.append(cmd) or
                        plugin.subprocess.CompletedProcess(cmd, 0, "ok", ""))
    ctx = make_ctx([source(RUNNING)])
    plugin.register(ctx)
    assert _cli(ctx, "setup") == 0
    cron = lambda: [c for c in calls if "cron" in c]
    assert cron()[0][-7:] == ["cron", "edit", "tailgate", "--schedule", "*/5 * * * *",
                              "--deliver", "origin"]
    assert _cli(ctx, "setup", "--schedule", "*/10 * * * *") == 0
    assert "*/10 * * * *" in cron()[-1]
    count = len(calls)
    assert _cli(ctx, "setup", "--schedule", "every 5m") == 2  # intervals drift: refused
    assert len(calls) == count  # nothing sent to Hermes
