# Contributing to tailgate

Thanks for helping. tailgate is small on purpose, so most changes touch one or two files.

## How the code is laid out

```
tailgate/
├── plugin.yaml              manifest Hermes reads
├── __init__.py              register(ctx): wires the pieces together, nothing else
├── tailgate_core/           the engine, plain Python, never imports Hermes
│   ├── models.py            Job and JobState
│   ├── sources.py           Source: runs a job source command and parses its JSON lines
│   ├── store.py             Store: the locked jobs.json shared by the gateway and the cron tick
│   ├── tracker.py           Tracker: numbers, names, rounds, mute and follow
│   └── render.py            every sentence a user sees
├── hermes_adapter/          everything that uses Hermes's plugin API
│   ├── commands.py          /tailgate and /tg
│   ├── tool.py              the tailgate_job_id tool and its prompt sentence
│   ├── cli.py               hermes tailgate setup|tick|status|mute|follow
│   └── schedule.py          the tick script and the cron job (hermes tailgate setup)
└── tests/                   one test file per module
```

Two rules keep it that way:

- **`tailgate_core` never imports Hermes.** It runs in two places: inside the Hermes gateway, and
  in the cron tick script, which is a separate process. It must work in both, and it is tested
  without Hermes installed.
- **`Tracker` is the only way in.** Commands, the tool, the CLI and the tick script all call
  `Tracker` methods. If you need new behaviour, add a method there and keep the adapters thin.

Where to make a change:

| You want to... | Edit |
|---|---|
| Change what a message says | `tailgate_core/render.py` |
| Accept a new field from sources | `tailgate_core/models.py` and `tailgate_core/sources.py` |
| Change numbering, naming, muting or what a round sends | `tailgate_core/tracker.py` |
| Add a slash command or CLI subcommand | `hermes_adapter/commands.py` or `hermes_adapter/cli.py`, backed by a `Tracker` method |

## Setting up

Python 3.11 or newer. Nothing to install at runtime, tailgate uses only the standard library.

```bash
python3 -m venv .venv
.venv/bin/pip install pytest ruff
.venv/bin/python -m pytest       # all tests, no Hermes needed
.venv/bin/ruff check .           # lint
```

Before a pull request, if you have Hermes:

```bash
hermes plugins validate .        # what the Hermes plugin catalog runs, incl. its security scan
hermes plugins doctor . --ci
```

## Writing a job source

You don't need to change tailgate to support a new kind of job. Write a command that prints one
JSON object per job and exits 0 (the format is in the README under *Job sources*), then add it to
`plugins.entries.tailgate.settings.sources`. A short script in your repository or a gist is a good
way to share one. Open an issue if you think it belongs in the README as an example.

## Pull requests

- One change per pull request, with a test that fails without it.
- Keep user-facing text in `render.py`, and keep it short: people read it on a phone.
- No new runtime dependencies.
- Blocking work never runs on the gateway's event loop. Slash-command handlers are async and push
  their work to a thread (see `hermes_adapter/commands.py`).
- Text from job sources is untrusted. It is shown to the user and never passed to the model.

## Releasing (maintainers)

On an up-to-date, clean `main`:

```bash
python3 scripts/release.py
```

It shows the current version, asks which one to release (patch, minor, major or a typed
version), bumps `plugin.yaml`, commits, tags and, after asking, pushes. The tag starts
`.github/workflows/release.yml`, which checks the tag matches `plugin.yaml`, runs the tests and
publishes the GitHub Release with generated notes.

## Reporting a bug

Include your Hermes version (`hermes --version`), the output of `hermes tailgate status`, and, if
a round misbehaves, `hermes tailgate tick --dry-run`.
