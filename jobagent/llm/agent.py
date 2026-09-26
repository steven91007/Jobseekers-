"""The research agent: an OpenAI Responses API tool loop with a hard turn cap."""

import json
import logging
from datetime import datetime, timezone

from .. import observability as obs
from ..config import Settings
from .client import call
from .prompts import AGENT_INSTRUCTIONS, PROMPT_VERSION
from .tools import (
    BOARD_CHECK_BUDGET, CANDIDATE_BUDGET, DISPATCH, LINKEDIN_CALL_BUDGET, TOOL_DEFS,
    ToolContext, region_names, watchlist_names,
)

log = logging.getLogger(__name__)

TOOL_OUTPUT_CHARS = 12000


def _task(ctx: ToolContext, counts: dict, profile: str) -> str:
    return f"""Today is {datetime.now(timezone.utc):%Y-%m-%d}. Search window: jobs posted within {ctx.since}.
Regions for this run: {region_names(ctx.regions)} (plus REMOTE_EU for EU-remote roles). Stay within them.

This run's pipeline results: {json.dumps(counts)}

Watchlist companies already covered (do not propose these): {watchlist_names()}

Budgets for this run: search_linkedin {LINKEDIN_CALL_BUDGET} calls, check_company_board \
{BOARD_CHECK_BUDGET} calls, add_company_candidate {CANDIDATE_BUDGET} records.

Candidate profile:
{profile[:4000]}
"""


def _truncate(value) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= TOOL_OUTPUT_CHARS else text[:TOOL_OUTPUT_CHARS] + '..."(truncated)"'


def run_agent(client, ctx: ToolContext, counts: dict, profile: str) -> dict:
    """Run the loop. Returns {"briefing", "turns", "tool_calls", "transcript", "error"}."""
    settings: Settings = ctx.settings
    tools = list(TOOL_DEFS)
    if settings.agent_web_search:
        tools.append({"type": "web_search"})

    transcript: list[dict] = []
    result = {"briefing": "", "turns": 0, "tool_calls": 0, "transcript": transcript, "error": ""}
    task = _task(ctx, counts, profile)
    input_items: list = [{"role": "user", "content": task}]
    previous_id = None

    with obs.span(obs.NAMES.AGENT, as_type="agent",
                  input=task,
                  metadata={"prompt_version": PROMPT_VERSION, "model": settings.openai_model,
                            "max_turns": settings.agent_max_turns, "web_search": settings.agent_web_search,
                            "counts": counts}) as agent_span:
        for turn in range(1, settings.agent_max_turns + 1):
            last_turn = turn == settings.agent_max_turns
            kwargs = dict(
                model=settings.openai_model,
                instructions=AGENT_INSTRUCTIONS,
                tools=tools,
                input=input_items,
                max_output_tokens=16000,
                prompt_cache_key=f"jobagent-agent-{PROMPT_VERSION}",
            )
            if settings.agent_web_search:
                # Return the pages web_search read, so the trace shows the agent's sources.
                kwargs["include"] = ["web_search_call.action.sources"]
            if previous_id:
                kwargs["previous_response_id"] = previous_id
            if last_turn:
                kwargs["tool_choice"] = "none"  # force the final briefing
                input_items.append({"role": "user", "content":
                                    "Turn budget reached. Write the final briefing now."})
            try:
                resp = call(client.responses.create, effort=settings.agent_reasoning_effort,
                            trace_name=obs.NAMES.AGENT_GENERATION,
                            trace_metadata={"turn": turn, "final_turn": last_turn,
                                            "prompt_version": PROMPT_VERSION}, **kwargs)
            except Exception as e:
                result["error"] = f"{type(e).__name__}: {e}"
                log.error("agent turn %d failed: %s", turn, result["error"])
                break
            previous_id = resp.id
            result["turns"] = turn

            entry = {"turn": turn, "response_id": resp.id, "tool_calls": [], "web_searches": []}
            usage = getattr(resp, "usage", None)
            if usage is not None:
                entry["usage"] = {"input": usage.input_tokens, "output": usage.output_tokens}

            for item in resp.output:
                if item.type == "web_search_call":
                    # Runs server-side inside the generation; recorded as a sibling tool
                    # observation so tool calls can be filtered and evaluated uniformly.
                    action = getattr(item, "action", None)
                    query = getattr(action, "query", None) if action is not None else None
                    sources = [getattr(src, "url", None) for src in
                               (getattr(action, "sources", None) or [])][:20]
                    entry["web_searches"].append(query)
                    with obs.span(obs.NAMES.WEB_SEARCH, as_type="tool",
                                  input={"query": query, "action": getattr(action, "type", None)},
                                  metadata={"turn": turn, "server_side": True}) as ws:
                        ws.update(output={"status": getattr(item, "status", None), "sources": sources})

            calls = [item for item in resp.output if item.type == "function_call"]
            if not calls:
                result["briefing"] = resp.output_text or ""
                entry["final"] = result["briefing"]
                transcript.append(entry)
                break

            input_items = []
            for c in calls:
                result["tool_calls"] += 1
                try:
                    args = json.loads(c.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                with obs.span(c.name, as_type=obs.TOOL_TYPES.get(c.name, "tool"), input=args,
                              metadata={"turn": turn, "call_id": c.call_id}) as ts:
                    fn = DISPATCH.get(c.name)
                    try:
                        output = fn(ctx, **args) if fn else {"error": f"unknown tool {c.name}"}
                    except Exception as e:  # a tool error goes back to the model, not up the stack
                        log.warning("tool %s failed: %s", c.name, e)
                        output = {"error": f"{type(e).__name__}: {e}"}
                        ts.update(level="ERROR", status_message=str(e)[:500])
                    text = _truncate(output)
                    ts.update(output=output if len(text) < 4000 else text[:4000])
                entry["tool_calls"].append({"name": c.name, "args": args, "output": text[:2000]})
                input_items.append({"type": "function_call_output", "call_id": c.call_id,
                                    "output": text})
            transcript.append(entry)

        agent_span.update(
            output=result["briefing"] or None,
            metadata={"turns": result["turns"], "tool_calls": result["tool_calls"],
                      "new_jobs_found": len(ctx.found_keys), "candidates": ctx.candidates,
                      "error": result["error"] or None},
            level="ERROR" if result["error"] else None,
            status_message=result["error"] or None,
        )
    return result
