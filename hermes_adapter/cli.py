"""`hermes tailgate setup|tick|status|mute|follow`."""

from __future__ import annotations

from pathlib import Path

from ..tailgate_core import Source, Tracker
from . import setup


def register(ctx, tracker: Tracker, data_dir: Path, sources: list[Source]) -> None:
    def build(parser):
        sub = parser.add_subparsers(dest="tailgate_command")
        p = sub.add_parser("setup", help="Install or update the progress cron job")
        p.add_argument("--schedule", default=setup.DEFAULT_SCHEDULE,
                       help=f"Cron expression (default {setup.DEFAULT_SCHEDULE!r}, e.g. '*/10 * * * *')")
        p.add_argument("--deliver", default="origin",
                       help="Hermes delivery target, e.g. telegram, all (default origin)")
        p = sub.add_parser("tick", help="Run one progress round now and print it")
        p.add_argument("--dry-run", action="store_true", help="Preview without recording")
        sub.add_parser("status", help="List jobs and configured sources")
        for verb, text in (("mute", "Stop progress updates for one job"), ("follow", "Resume them")):
            sub.add_parser(verb, help=text).add_argument("job", nargs="?", default="",
                                                       help="Job number (#12) or name")

    def handle(args) -> int:
        command = getattr(args, "tailgate_command", None)
        if command == "setup":
            return setup.run(data_dir, sources, args.schedule, args.deliver)
        if command == "tick":
            print(tracker.round(commit=not args.dry_run) or "(nothing to report)")
            return 0
        if command == "status":
            print("Sources: " + (", ".join(s.name for s in sources) or "none configured"))
            print(tracker.list())
            return 0
        if command in ("mute", "follow"):
            print(tracker.mute(args.job) if command == "mute" else tracker.follow(args.job))
            return 0
        print("usage: hermes tailgate setup|tick|status|mute|follow")
        return 2

    ctx.register_cli_command("tailgate", help="Follow handed-off jobs (tailgate plugin)",
                             setup_fn=build, handler_fn=handle)
