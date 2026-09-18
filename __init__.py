"""tailgate: follow the jobs Hermes hands off, without spending model turns on it.

Registers:
  * /tailgate (alias /tg): list jobs; `/tailgate mute <id>`, `/tailgate follow <id>`
  * tool `tailgate_job_id`: the model gets a unique id before it hands a job off
  * CLI `hermes tailgate setup|tick|status|mute|follow`: `setup` installs the progress tick as a no-agent
    cron job (script stdout goes straight to chat, empty output is silent, no model call)

Slash-command handlers are async and push blocking work to a thread: a handler that blocks runs on
the gateway event loop and can get the gateway killed (hermes-agent#105279).
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import tailgate_core as core

PLUGIN_DIR = Path(__file__).resolve().parent
CRON_NAME = "tailgate"
TICK_SCRIPT = "tailgate-tick.py"
# Cron expressions only, so rounds land on fixed clock times. Hermes also takes intervals
# (`every 5m`), but runs them five minutes after the previous run started, checked once a
# minute, so they drift; tailgate does not offer them.
DEFAULT_SCHEDULE = "*/5 * * * *"
USAGE = (
    "/tailgate (or /tg): list jobs\n"
    "/tailgate mute <job-id>: stop progress updates for one job\n"
    "/tailgate follow <job-id>: resume them"
)

PROMPT_SECTION = (
    "When you hand a long-running job to another tool (a coding agent, CI, a long script), first "
    "call the `tailgate_job_id` tool (find it with tool_search if it is not in your tool list) and "
    "use the id it returns as the job's name. tailgate reports the job's progress to the user; "
    "do not poll it yourself."
)

TOOL_SCHEMA = {
    "name": "tailgate_job_id",
    "description": (
        "Get a unique id for a job you are about to hand off to another tool (a coding agent, CI, a "
        "long script). Call this BEFORE starting the job and use the returned id as the job's name "
        "everywhere, including in what you tell the user. tailgate then sends the user progress "
        "updates for that job automatically; you do not need to poll it."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "topic": {
                "type": "string",
                "description": "One or two words naming the job, e.g. 'intraday' or 'rubocop fix'.",
            }
        },
        "required": ["topic"],
    },
}


def _data_dir(ctx) -> Path:
    try:
        return Path(ctx.state.data_dir)
    except Exception:  # older Hermes without ctx.state
        home = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
        return home / "plugin-data" / "tailgate"


def register(ctx):
    data_dir = _data_dir(ctx)
    sources = core.validate_sources(ctx.get_config("sources", default=[]))
    store = core.Store(data_dir)
    # The cron tick runs outside the plugin and cannot read plugin config; give it a snapshot.
    # Refreshed on every load, so a config change applies after a gateway restart.
    core.save_settings(data_dir, sources)

    async def collect():
        return await asyncio.to_thread(core.collect, sources)

    async def list_cmd() -> str:
        jobs, problems = await collect()
        state = await asyncio.to_thread(store.read)
        text = core.list_jobs(state, jobs) if sources else "No job sources configured. See the tailgate README."
        return "\n".join([text] + [f"⚠️ {p}" for p in problems])

    async def mute_cmd(job_id: str, muted: bool) -> str:
        jobs, problems = await collect()

        def apply() -> str:
            with store.edit() as state:
                return core.set_muted(state, jobs, job_id, muted)

        reply = await asyncio.to_thread(apply)
        return "\n".join([reply] + [f"⚠️ {p}" for p in problems])

    async def tailgate(raw_args: str) -> str:
        sub, _, rest = (raw_args or "").strip().partition(" ")
        sub, rest = sub.lower(), rest.strip()
        if sub in ("", "jobs", "list"):
            return await list_cmd()
        if sub in ("mute", "follow"):
            return await mute_cmd(rest, sub == "mute")
        return USAGE

    # Tool Search (on by default in Hermes) hides plugin tools behind a search step, and some
    # models never search. One sentence in every new session's prompt names the tool.
    ctx.register_system_prompt_section("tailgate.job-ids", PROMPT_SECTION, max_chars=600)

    for name in ("tailgate", "tg"):
        # A name taken by a built-in or another plugin is skipped by Hermes; /tailgate is the
        # canonical one, /tg only a shortcut.
        ctx.register_command(name, tailgate, description="Jobs: list, mute <id>, follow <id>",
                             args_hint="[mute|follow <job-id>]")

    def job_id_tool(params, **kwargs) -> str:
        topic = str((params or {}).get("topic") or "job")
        jobs, _ = core.collect(sources)
        with store.edit() as state:
            job_id = core.reserve_id(state, topic, [j.id for j in jobs], time.time())
        return json.dumps({"job_id": job_id})

    ctx.register_tool(name="tailgate_job_id", toolset="tailgate", schema=TOOL_SCHEMA,
                      handler=job_id_tool)

    def cli_setup(parser):
        sub = parser.add_subparsers(dest="tailgate_command")
        setup = sub.add_parser("setup", help="Install or update the progress cron job")
        setup.add_argument("--schedule", default=DEFAULT_SCHEDULE,
                           help=f"Cron expression (default {DEFAULT_SCHEDULE!r}, e.g. '*/10 * * * *')")
        setup.add_argument("--deliver", default="origin",
                           help="Hermes delivery target, e.g. telegram, all (default origin)")
        tick = sub.add_parser("tick", help="Run one progress round now and print it")
        tick.add_argument("--dry-run", action="store_true", help="Preview without recording")
        sub.add_parser("status", help="List jobs and configured sources")
        for verb, text in (("mute", "Stop progress updates for one job"), ("follow", "Resume them")):
            sub.add_parser(verb, help=text).add_argument("job_id")

    def cli_handler(args) -> int:
        command = getattr(args, "tailgate_command", None)
        if command == "setup":
            return _setup(data_dir, sources, args.schedule, args.deliver)
        if command == "tick":
            print(core.tick(store, sources, commit=not args.dry_run) or "(nothing to report)")
            return 0
        if command == "status":
            jobs, problems = core.collect(sources)
            print("Sources: " + (", ".join(s["name"] for s in sources) or "none configured"))
            print(core.list_jobs(store.read(), jobs))
            for p in problems:
                print(f"warning: {p}")
            return 0
        if command in ("mute", "follow"):
            jobs, problems = core.collect(sources)
            with store.edit() as state:
                print(core.set_muted(state, jobs, args.job_id, command == "mute"))
            for p in problems:
                print(f"warning: {p}")
            return 0
        print("usage: hermes tailgate setup|tick|status|mute <id>|follow <id>")
        return 2

    ctx.register_cli_command("tailgate", help="Follow handed-off jobs (tailgate plugin)",
                             setup_fn=cli_setup, handler_fn=cli_handler)


def _tick_script(data_dir: Path) -> str:
    return f'''# Generated by `hermes tailgate setup`; regenerate rather than edit.
# Runs as a no-agent cron job: stdout is delivered to chat verbatim, empty stdout is silent.
import os, pwd, sys
os.environ.setdefault("HOME", pwd.getpwuid(os.getuid()).pw_dir)  # hermes-agent#114209
sys.path.insert(0, {str(PLUGIN_DIR)!r})
import tailgate_core as core
DATA_DIR = {str(data_dir)!r}
out = core.tick(core.Store(DATA_DIR), core.load_settings(DATA_DIR))
if out:
    print(out)
'''


def _hermes_cmd() -> list[str]:
    exe = shutil.which("hermes")
    return [exe] if exe else [sys.executable, "-m", "hermes_cli.main"]


def _setup(data_dir: Path, sources: list[dict], schedule: str, deliver: str) -> int:
    if len(schedule.split()) != 5:
        print(f"tailgate: --schedule must be a cron expression with five fields, e.g. "
              f"{DEFAULT_SCHEDULE!r}; got {schedule!r}")
        return 2
    hermes_home = data_dir.parent.parent  # <hermes home>/plugin-data/<plugin>
    scripts = hermes_home / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / TICK_SCRIPT).write_text(_tick_script(data_dir), encoding="utf-8")
    core.save_settings(data_dir, sources)
    known = core.take_baseline(core.Store(data_dir), sources)
    print(f"tailgate: {known} existing job(s) recorded as history; only jobs from now on are announced.")
    # Update the job if it exists (cron verbs accept a job name), otherwise create it.
    result = subprocess.run(_hermes_cmd() + ["cron", "edit", CRON_NAME, "--schedule", schedule,
                                             "--deliver", deliver], capture_output=True, text=True)
    if result.returncode != 0:
        result = subprocess.run(_hermes_cmd() + ["cron", "create", schedule, "--no-agent",
                                                 "--script", TICK_SCRIPT, "--deliver", deliver,
                                                 "--name", CRON_NAME], capture_output=True, text=True)
    print(result.stdout.strip() or result.stderr.strip())
    if result.returncode != 0:
        return result.returncode
    print(f"tailgate: {len(sources)} source(s), schedule {schedule!r}, delivered to {deliver}.")
    if not sources:
        print("tailgate: no sources configured yet; see README (plugins.entries.tailgate.settings.sources).")
    return 0
