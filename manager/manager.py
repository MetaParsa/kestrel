"""One manager cycle: Claude reads the bundle through read-only tools and returns a PlaybookOut.

- Model `claude-opus-5-5`, adaptive thinking (always on for this model), effort `high`.
- Structured output (`output_config.format`) guarantees the final answer is PlaybookOut JSON.
- Tools are `strict`; forced tool_choice isn't allowed on Opus 5.5, so the prompt steers.
- System prompt + tools are cached (stable prefix); the bundle summary comes after.
- Server-side refusal fallback on (`fallbacks="default"`, beta server-side-fallback-2026-07-01).
- Every request is checked against the spend cap before it is sent, and recorded after.
- The full transcript (inputs, tool calls, tool results, outputs) is returned for the logs.
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path

from ai_box.spend import SpendLedger, Usage, worst_case_call
from manager.schema import PlaybookOut, canonical_json, output_json_schema
from manager.tools import TOOL_NAMES, TOOLS, run_tool

MODEL = "claude-opus-5-5"
EFFORT = "high"
MAX_TOKENS = 16000
MAX_TOOL_CALLS = 6
FALLBACK_BETA = "server-side-fallback-2026-07-01"
PROMPT = (Path(__file__).parent / "prompts" / "manager.md").read_text(encoding="utf-8")


class ManagerError(RuntimeError):
    pass


@dataclass
class ManagerResult:
    out: PlaybookOut
    model: str
    cost_usd: float
    requests: int
    tool_calls: int
    transcript: list[dict] = field(default_factory=list)


def inline_schema(schema: dict) -> dict:
    """Resolve $refs and drop titles so the schema is self-contained and minimal."""
    defs = schema.get("$defs", {})

    def walk(node):
        if isinstance(node, dict):
            if "$ref" in node:
                return walk(copy.deepcopy(defs[node["$ref"].split("/")[-1]]))
            return {k: walk(v) for k, v in node.items() if k not in ("title", "$defs")}
        if isinstance(node, list):
            return [walk(x) for x in node]
        return node

    return walk(schema)


PLAYBOOK_SCHEMA = inline_schema(output_json_schema())


def summary_message(bundle: dict) -> str:
    """The volatile part of the request: a compact view of the bundle (tools give the detail)."""
    view = {
        "bundle_hash": bundle["hash"], "reason": bundle["reason"], "calendar": bundle["calendar"],
        "portfolio": bundle["portfolio"],
        "markets": {m: {"snapshot": s["snapshot"], "stats": s["stats"]} for m, s in bundle["markets"].items()},
        "forecast_scorecard": bundle["forecast_scorecard"],
        "last_playbook": bundle["previous_playbooks"][0] if bundle["previous_playbooks"] else None,
    }
    return ("Context bundle summary (all numbers from the trading server). Use the tools for bars, "
            "decisions and earlier playbooks, then return one playbook.\n" + canonical_json(view))


def _usage(resp) -> Usage:
    u = resp.usage
    return Usage(input_tokens=u.input_tokens or 0, output_tokens=u.output_tokens or 0,
                 cache_creation_input_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
                 cache_read_input_tokens=getattr(u, "cache_read_input_tokens", 0) or 0)


def _block_dict(b) -> dict:
    if hasattr(b, "model_dump"):
        return b.model_dump(exclude_none=True)
    return {k: v for k, v in vars(b).items() if not k.startswith("_")}


def run_manager(bundle: dict, client, ledger: SpendLedger, *, model: str = MODEL,
                max_tool_calls: int = MAX_TOOL_CALLS, max_requests: int = 8) -> ManagerResult:
    system = [{"type": "text", "text": PROMPT, "cache_control": {"type": "ephemeral"}}]
    messages: list[dict] = [{"role": "user", "content": summary_message(bundle)}]
    transcript: list[dict] = [{"role": "user", "content": messages[0]["content"]}]
    cost, tool_calls = 0.0, 0

    for n in range(1, max_requests + 1):
        # re-estimated every request: tool results and earlier turns grow the input
        est_input = (len(PROMPT) + len(json.dumps(TOOLS)) + len(json.dumps(transcript, default=str))) // 3 + 4000
        worst = worst_case_call(model, est_input, MAX_TOKENS)
        ledger.check(worst)
        try:
            resp = client.beta.messages.create(
                model=model, max_tokens=MAX_TOKENS, system=system, tools=TOOLS, messages=messages,
                output_config={"effort": EFFORT, "format": {"type": "json_schema", "schema": PLAYBOOK_SCHEMA}},
                betas=[FALLBACK_BETA], fallbacks="default",
            )
        except Exception as e:      # SDK errors: the request may have been billed if it timed out mid-generation
            if "Timeout" in type(e).__name__:
                cost += worst
                ledger.record(model=model, usage=Usage(0, 0), request_id="timeout", purpose="manager",
                              charge_usd=worst)
            raise ManagerError(f"API error: {type(e).__name__}") from e
        served_by = getattr(resp, "model", model) or model
        cost += ledger.record(model=served_by, usage=_usage(resp), request_id=getattr(resp, "_request_id", "") or "",
                              purpose="manager")
        transcript.append({"role": "assistant", "stop_reason": resp.stop_reason, "model": served_by,
                           "content": [_block_dict(b) for b in resp.content]})

        if resp.stop_reason == "refusal":
            details = getattr(resp, "stop_details", None)
            raise ManagerError(f"refused ({getattr(details, 'category', None)})")
        if resp.stop_reason == "max_tokens":
            raise ManagerError("hit max_tokens before finishing the playbook")
        if resp.stop_reason == "end_turn":
            text = next((b.text for b in resp.content if b.type == "text"), None)
            if text is None:
                raise ManagerError("no text block in the final answer")
            try:
                out = PlaybookOut.model_validate_json(text)
            except ValueError as e:
                raise ManagerError(f"final answer is not a valid playbook: {e}") from e
            return ManagerResult(out=out, model=served_by, cost_usd=cost, requests=n,
                                 tool_calls=tool_calls, transcript=transcript)

        messages.append({"role": "assistant", "content": resp.content})   # unchanged: keeps thinking valid
        if resp.stop_reason == "pause_turn":
            continue
        if resp.stop_reason != "tool_use":
            raise ManagerError(f"unexpected stop_reason {resp.stop_reason}")
        results = []
        for b in (b for b in resp.content if b.type == "tool_use"):
            tool_calls += 1
            if tool_calls > max_tool_calls:
                body, err = "Tool budget used up. Return the playbook now from what you have.", True
            elif b.name not in TOOL_NAMES:
                body, err = f"Unknown tool {b.name}", True
            else:
                try:
                    body, err = run_tool(b.name, dict(b.input), bundle), False
                except (KeyError, ValueError, TypeError) as e:
                    body, err = f"Tool error: {type(e).__name__}: {e}", True
            results.append({"type": "tool_result", "tool_use_id": b.id, "content": body, "is_error": err})
        messages.append({"role": "user", "content": results})
        transcript.append({"role": "user", "content": results})
    raise ManagerError(f"no playbook after {max_requests} requests")
