import argparse
import asyncio
import inspect
import json
import subprocess
import sys

from tailgate_core import render

RUNNING = {"id": "intraday-1", "state": "running", "elapsed_s": 600}


def _cli(ctx, *argv):
    setup_fn, handler = ctx.cli["tailgate"]
    parser = argparse.ArgumentParser()
    setup_fn(parser)
    return handler(parser.parse_args(list(argv)))


def test_registers_commands_tool_prompt_and_cli(plugin, make_ctx, source):
    ctx = make_ctx([source(RUNNING)])
    plugin.register(ctx)
    assert set(ctx.commands) == {"tailgate", "tg"}
    assert set(ctx.tools) == {"tailgate_job_id"}
    assert set(ctx.cli) == {"tailgate"}
    assert "tailgate_job_id" in ctx.prompt_sections["tailgate.job-ids"]
    # A blocking handler on the gateway loop can kill the gateway (hermes-agent#105279).
    assert all(inspect.iscoroutinefunction(h) for h in ctx.commands.values())


def test_slash_commands(plugin, make_ctx, source):
    ctx = make_ctx([source(RUNNING)])
    plugin.register(ctx)
    run = lambda args: asyncio.run(ctx.commands["tailgate"](args))
    assert run("") == "⏳ #1 name: intraday-1 · running 10m · 🔔 following"
    assert run("mute 1").startswith("🔕 #1 intraday-1 muted")
    assert run("list") == "⏳ #1 name: intraday-1 · running 10m · 🔕 muted"
    assert run("follow intraday-1") == "🔔 Following #1 intraday-1."
    assert run("help") == render.USAGE
    assert asyncio.run(ctx.commands["tg"]("")) == run("")


def test_tool_hands_out_unique_names(plugin, make_ctx, source):
    ctx = make_ctx([source(RUNNING)])
    plugin.register(ctx)
    _, handler = ctx.tools["tailgate_job_id"]
    assert json.loads(handler({"topic": "intraday"})) == {"job_id": "intraday-2"}
    assert json.loads(handler({"topic": "intraday"})) == {"job_id": "intraday-3"}


def test_cli_mute_follow_status_tick(plugin, make_ctx, source, capsys):
    ctx = make_ctx([source(RUNNING)])
    plugin.register(ctx)
    assert _cli(ctx, "mute", "1") == 0 and "muted" in capsys.readouterr().out
    assert _cli(ctx, "status") == 0 and "🔕 muted" in capsys.readouterr().out
    assert _cli(ctx, "tick") == 0 and capsys.readouterr().out.strip() == "(nothing to report)"
    assert _cli(ctx, "follow", "1") == 0
    assert _cli(ctx, "tick", "--dry-run") == 0 and "⏳ #1 name: intraday-1" in capsys.readouterr().out


def test_setup_passes_the_schedule_through_and_refuses_intervals(plugin, make_ctx, source, monkeypatch):
    calls = []
    schedule = sys.modules["tailgate_plugin.hermes_adapter.schedule"]
    monkeypatch.setattr(schedule.subprocess, "run", lambda cmd, **kw: calls.append(cmd) or
                        subprocess.CompletedProcess(cmd, 0, "ok", ""))
    ctx = make_ctx([source(RUNNING)])
    plugin.register(ctx)
    cron = lambda: [c for c in calls if "cron" in c]
    assert _cli(ctx, "setup") == 0
    assert cron()[0][-7:] == ["cron", "edit", "tailgate", "--schedule", "*/5 * * * *", "--deliver", "origin"]
    assert _cli(ctx, "setup", "--schedule", "*/10 * * * *") == 0 and "*/10 * * * *" in cron()[-1]
    count = len(calls)
    assert _cli(ctx, "setup", "--schedule", "every 5m") == 2  # intervals drift: refused
    assert len(calls) == count


def test_generated_tick_script_runs_standalone(plugin, make_ctx, source, tmp_path):
    ctx = make_ctx([source(RUNNING)])
    plugin.register(ctx)
    schedule = sys.modules["tailgate_plugin.hermes_adapter.schedule"]
    script = tmp_path / "tailgate-tick.py"
    script.write_text(schedule.tick_script(ctx.state.data_dir))
    result = subprocess.run([sys.executable, str(script)], capture_output=True, text=True,
                            env={"PATH": "/usr/bin:/bin"})  # stripped env, as in hermes-agent#114209
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "⏳ #1 name: intraday-1 · running 10m\n" + render.HINT


def test_old_tick_scripts_keep_working(make_ctx, source, tmp_path):
    """Scripts generated before 0.2 call tailgate_core.tick(); it must still exist."""
    import tailgate_core
    from tailgate_core import Store
    assert tailgate_core.tick(Store(tmp_path / "d"), [source(RUNNING)]).startswith("⏳ #1")
