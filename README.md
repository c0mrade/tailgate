# tailgate

Follow the jobs your [Hermes Agent](https://github.com/NousResearch/hermes-agent) hands off to
other tools (coding agents, CI, long scripts) from whatever chat you use, **without spending model
turns on it**.

```
⏳ intraday-1 · running 1h 05m · 83 events · last: bundle exec rspec
⏳ nightly-build-3 · running 12m · step 4/9
✅ rubocop-fix-2 · done after 1h 11m · ask Hermes about rubocop-fix-2
```

- A progress line for every job you follow, every few minutes, and one line when it finishes.
- `/tailgate mute <id>` silences one job; you still hear when it finishes. `/tailgate follow <id>`
  brings it back. New jobs are followed by default.
- Every job has a unique id. The agent asks tailgate for one before it hands a job off, so ids
  never collide and never get reused.
- None of this calls the model. Progress runs as a Hermes no-agent cron job, and the slash commands
  answer directly. On a local model that matters: an agent that wakes up every five minutes to
  check on a job slows down the very job it is watching.

## Why

A local agent that delegates coding work to another agent (OpenHands, Codex, Claude Code) will
happily leave you wondering for hours whether anything is happening. Hermes can notify you about
processes it started itself, but only with one global setting, and not about jobs running
somewhere else. tailgate watches those jobs, lets you pick which ones you care about, and keeps
the model out of it.

## Install

```bash
hermes plugins install https://github.com/c0mrade/tailgate    # or copy this directory to ~/.hermes/plugins/tailgate
hermes plugins enable --no-allow-tool-override tailgate
```

Tell tailgate where your jobs are (see *Job sources*), restart the gateway, then schedule the
progress round:

```bash
hermes tailgate setup --every 5m --deliver telegram     # any Hermes delivery target; default: origin
```

`setup` writes `~/.hermes/scripts/tailgate-tick.py` and creates (or updates) a no-agent cron job
named `tailgate`. Run it again after changing the interval or target.

Hermes wraps every cron delivery in a `Cronjob Response: tailgate` header and a footer suggesting
"stop reminder tailgate", which would cost a model turn and delete the whole schedule. tailgate
adds its own `/tg mute <id>` hint instead, so you probably want the wrapper off:

```bash
hermes config set cron.wrap_response false     # applies to all cron jobs; Hermes has no per-job switch
```

## Use

| In chat | |
|---|---|
| `/tailgate` or `/tg` | List jobs, running first, with 🔔 following or 🔕 muted |
| `/tailgate mute <id>` | No more progress lines for that job; the finish line still comes |
| `/tailgate follow <id>` | Progress lines again |

| In a terminal | |
|---|---|
| `hermes tailgate status` | Sources and jobs |
| `hermes tailgate tick [--dry-run]` | Run one progress round now and print it |
| `hermes tailgate mute <id>` / `follow <id>` | Same as in chat |
| `hermes tailgate setup [--every 5m] [--deliver target]` | Install or update the cron job |

The agent gets one tool, `tailgate_job_id(topic)`, which returns a fresh id such as `intraday-2`.
Tell it in your skill or `SOUL.md` to call it before handing a job off and to use the id as the
job's name.

## Job sources

A source is a command tailgate runs (no shell) that prints one JSON object per job, one per line,
and exits 0:

```json
{"id": "intraday-1", "state": "running", "elapsed_s": 3900, "progress": "83 events", "last": "bundle exec rspec"}
```

| Field | Required | |
|---|---|---|
| `id` | yes | `[a-z0-9][a-z0-9._-]{0,62}` |
| `state` | yes | `queued`, `running`, `done`, `incomplete` or `failed` |
| `elapsed_s` | no | Seconds since the job started (or how long it ran) |
| `progress` | no | Short free text, e.g. `83 events` or `step 4/9` |
| `last` | no | Short free text, e.g. the last command |

Configure sources in `~/.hermes/config.yaml`:

```yaml
plugins:
  entries:
    tailgate:
      settings:
        sources:
          - name: openhands
            command: [ssh, sandbox, openhands-remote, watch, --json]
```

or with `hermes config set plugins.entries.tailgate.settings.sources '[{"name": "openhands", "command": ["ssh", "sandbox", "openhands-remote", "watch", "--json"]}]'`.
Restart the gateway after changing sources.

Jobs that were already finished the first time tailgate sees them are not announced (they ended
before tailgate knew about them), unless their id came from `tailgate_job_id`. State for jobs no
source has reported for two weeks is dropped.

## Security

`progress` and `last` come from wherever the job runs, which may be a machine running untrusted
code. tailgate only ever shows them to you: they never enter the model's context (keep Hermes's
`cron.mirror_delivery` off, the default), control characters are stripped and length is capped.
Sources are argv lists run without a shell.

## Known issues

- [hermes-agent#114209](https://github.com/NousResearch/hermes-agent/issues/114209): no-agent cron
  scripts can lose their environment. The generated tick script restores `HOME` so SSH sources keep
  working; if a source needs other variables, set them in its command (e.g. `env VAR=… cmd`).
- Mute and follow apply to the whole Hermes instance, not per user: plugin commands do not receive
  the sender yet ([hermes-agent#91526](https://github.com/NousResearch/hermes-agent/issues/91526)).

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install pytest
.venv/bin/python -m pytest
hermes plugins validate .          # what the Hermes plugin catalog runs
hermes plugins doctor . --ci
```

## License

MIT
