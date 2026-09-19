"""The tailgate_job_id tool and the prompt sentence that asks the agent to use it."""

from __future__ import annotations

import json

from ..tailgate_core import Tracker

PROMPT_SECTION = (
    "When you hand a long-running job to another tool (a coding agent, CI, a long script), first "
    "call the `tailgate_job_id` tool (find it with tool_search if it is not in your tool list) and "
    "use the id it returns as the job's name. tailgate reports the job's progress to the user; "
    "do not poll it yourself."
)

SCHEMA = {
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
                "description": "One to three words naming the job, e.g. 'intraday storage' or "
                               "'rubocop fix'. Not a sentence: it becomes the id the user types.",
            }
        },
        "required": ["topic"],
    },
}


def register(ctx, tracker: Tracker) -> None:
    def handler(params, **kwargs) -> str:
        topic = str((params or {}).get("topic") or "job")
        return json.dumps({"job_id": tracker.new_name(topic)})

    ctx.register_system_prompt_section("tailgate.job-ids", PROMPT_SECTION, max_chars=600)
    ctx.register_tool(name="tailgate_job_id", toolset="tailgate", schema=SCHEMA, handler=handler)
