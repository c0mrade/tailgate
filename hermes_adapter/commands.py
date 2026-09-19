"""The /tailgate and /tg slash commands."""

from __future__ import annotations

import asyncio

from ..tailgate_core import Tracker, render


def register(ctx, tracker: Tracker) -> None:
    async def tailgate(raw_args: str) -> str:
        # Async with the work in a thread: a blocking handler runs on the gateway's event loop
        # and can get the gateway killed (hermes-agent#105279).
        sub, _, rest = (raw_args or "").strip().partition(" ")
        sub, rest = sub.lower(), rest.strip()
        if sub in ("", "jobs", "list"):
            return await asyncio.to_thread(tracker.list)
        if sub == "mute":
            return await asyncio.to_thread(tracker.mute, rest)
        if sub == "follow":
            return await asyncio.to_thread(tracker.follow, rest)
        return render.USAGE

    for name in ("tailgate", "tg"):  # a name taken elsewhere is skipped by Hermes; /tg is a shortcut
        ctx.register_command(name, tailgate, description="Jobs: list, mute <number>, follow <number>",
                             args_hint="[mute|follow <number>]")
