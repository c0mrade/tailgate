"""tailgate, a Hermes plugin: progress updates in chat for jobs the agent hands off, with no model calls.

register() only wires things together: the engine is tailgate_core/, the Hermes side hermes_adapter/.
"""

from __future__ import annotations

import os
from pathlib import Path

from .hermes_adapter import cli, commands, tool
from .tailgate_core import Source, Store, Tracker, save_settings


def _data_dir(ctx) -> Path:
    try:
        return Path(ctx.state.data_dir)
    except Exception:  # older Hermes without ctx.state
        return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes") / "plugin-data" / "tailgate"


def register(ctx):
    data_dir = _data_dir(ctx)
    sources = Source.from_config(ctx.get_config("sources", default=[]))
    save_settings(data_dir, sources)  # the cron tick reads this snapshot; refreshed on every load
    tracker = Tracker(Store(data_dir), sources)
    commands.register(ctx, tracker)
    tool.register(ctx, tracker)
    cli.register(ctx, tracker, data_dir, sources)
