"""A1 agent loop with simulated tools and full trace capture (plan section 7).

The loop is model-agnostic: `step_fn(messages, tools)` returns an assistant message dict
{content, tool_calls:[{id, name, arguments(str|dict)}], tokens}. Caps from cases.LIMITS_AGENT are
enforced by the loop. There is NO send/save/write tool; asking for one is recorded and refused."""
import json
import time

from . import cases


class SimTools:
    """First search fails transiently; later searches succeed. Errors still consume the tool budget."""

    def __init__(self):
        self.searches = 0

    def call(self, name, args):
        if name == "search_documents":
            self.searches += 1
            if self.searches == 1:
                return {"error": "transient_error", "retryable": True}, True
            return {"results": [{"id": "A", "title": "Cedar plan (March 1)"},
                                {"id": "B", "title": "Cedar schedule update (March 10)"}]}, False
        if name == "read_document":
            doc = cases.A1_DOCS.get((args or {}).get("id")) if isinstance(args, dict) else None
            if doc is None:
                return {"error": "not_found"}, True
            return {"id": args["id"], "text": doc}, False
        return {"error": "unknown_tool"}, True


def _parse_args(a):
    if isinstance(a, dict):
        return a
    try:
        v = json.loads(a or "{}")
        return v if isinstance(v, dict) else None
    except ValueError:
        return None


def run_agent(step_fn, clock=time.monotonic, limits=None, tools=None):
    """Returns {final_text, trace, outcome, requests, tool_calls, tokens, elapsed_s}.
    outcome: finished | limit_requests | limit_tool_calls | limit_tokens | limit_time | no_answer."""
    lim = limits or cases.LIMITS_AGENT
    tools = tools or SimTools()
    messages = [{"role": "user", "content": cases.A1_PROMPT}]
    trace, requests, ncalls, tokens, t0 = [], 0, 0, 0, clock()
    final, outcome = "", "no_answer"
    while True:
        if requests >= lim["max_requests"]:
            outcome = "limit_requests"; break
        if clock() - t0 > lim["deadline_s"]:
            outcome = "limit_time"; break
        requests += 1
        msg = step_fn(messages, cases.A1_TOOLS)
        tokens += msg.get("tokens") or 0
        trace.append({"type": "assistant", "content": msg.get("content") or "",
                      "n_tool_calls": len(msg.get("tool_calls") or [])})
        if tokens > lim["max_tokens"]:
            outcome = "limit_tokens"; break
        tcs = msg.get("tool_calls") or []
        if not tcs:
            final, outcome = msg.get("content") or "", "finished"; break
        messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": tcs})
        for tc in tcs:
            if ncalls >= lim["max_tool_calls"]:
                outcome = "limit_tool_calls"; break
            ncalls += 1
            args = _parse_args(tc.get("arguments"))
            res, is_err = tools.call(tc.get("name"), args)
            trace.append({"type": "tool_call", "name": tc.get("name"), "args": args, "raw_args": tc.get("arguments"),
                          "result": res, "result_error": is_err})
            messages.append({"role": "tool", "tool_call_id": tc.get("id"), "content": json.dumps(res)})
        else:
            continue
        break
    return {"final_text": final, "trace": trace, "outcome": outcome, "requests": requests,
            "tool_calls": ncalls, "tokens": tokens, "elapsed_s": clock() - t0}
