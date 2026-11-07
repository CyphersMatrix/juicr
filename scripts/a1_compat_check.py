"""One-shot A1 tool-call compatibility check against the REAL Lemonade (owner-approved, short).
Refuses to start unless Lemonade looks idle; loads the model per config (non-persistent), runs A1 once,
restores API-visible state, writes evidence. Never reruns itself. Usage: python a1_compat_check.py KEYFILE OUTDIR"""
import json, os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from juicr import agent, cases, oracles
from juicr.evidence import EvidenceStore
from juicr.lemonade import LemonadeClient, LemonadeError
from juicr.runner import Runner

URL = "http://10.138.6.1:13305"
MODEL = "Gemma-4-E4B-it-GGUF"
CFG = {"model": MODEL, "load_options": {"ctx_size": 8192}, "expect": {"ctx_size": 8192}}
keyfile, out = sys.argv[1], sys.argv[2]
cl = LemonadeClient(URL, key_file=keyfile, timeout=60)
ev = EvidenceStore(out)
r = Runner(cl, ev)
report = {"model": MODEL, "steps": []}

# 1. idle check (two health polls + stats can't be read here, so require not busy twice, 8 s apart)
def busy():
    return any(m.get("is_busy") or m.get("is_streaming") for m in cl.health().get("all_models_loaded", []))
if busy(): print("ABORT: Lemonade busy"); sys.exit(2)
time.sleep(8)
if busy(): print("ABORT: Lemonade busy"); sys.exit(2)

prior = r.snapshot_state(); report["prior_models"] = [m["model_name"] for m in prior["models"]]
requests = 0
try:
    cfg_ev = r.establish(CFG); report["config"] = {k: cfg_ev[k] for k in ("settings", "device", "recipe")}
    def step(messages, tools):
        global requests
        requests += 1
        body = {"model": MODEL, "messages": messages, "tools": tools, "temperature": 0.0, "seed": 17,
                "top_p": 0.95, "top_k": 64, "max_tokens": 512}
        j = r.ensure_config(CFG) and cl.chat(body)
        m = j["choices"][0]["message"]
        raw = {"content": m.get("content"), "tool_calls": m.get("tool_calls"), "finish": j["choices"][0].get("finish_reason"),
               "usage": j.get("usage")}
        report["steps"].append(raw)
        tcs = [{"id": t.get("id"), "name": t["function"]["name"], "arguments": t["function"].get("arguments")} for t in (m.get("tool_calls") or [])]
        return {"content": m.get("content") or "", "tool_calls": tcs, "tokens": (j.get("usage") or {}).get("completion_tokens") or 0}
    res = agent.run_agent(step)
    report["agent"] = {k: res[k] for k in ("outcome", "requests", "tool_calls", "tokens", "elapsed_s")}
    report["final_text"] = res["final_text"]
    report["trace"] = res["trace"]
    report["evaluation"] = oracles.evaluate_a1(res["trace"], res["final_text"])
except (LemonadeError, Exception) as e:
    report["error"] = f"{type(e).__name__}: {str(e)[:200]}"
finally:
    report["restoration"] = r.restore(prior)
    report["requests_sent"] = requests
    final = {m["model_name"] for m in r.snapshot_state()["models"]}
    report["final_models"] = sorted(final)
h = ev.put_artifact(json.dumps(report, indent=1, default=str).encode(), "application/json")
ev.finalize("a1check", "compat-20261107", {"report_artifact": h, "summary": {k: report.get(k) for k in ("agent", "error", "restoration", "requests_sent", "final_models")}})
print(json.dumps({k: report.get(k) for k in ("config", "agent", "error", "restoration", "requests_sent", "prior_models", "final_models")}, indent=1, default=str))
print("EVAL:", json.dumps({k: report["evaluation"][k] for k in ("checks", "critical_failures", "dimensions")}, default=str) if "evaluation" in report else "none")
print("FINAL TEXT:", (report.get("final_text") or "")[:400].replace("\n", " | "))
print("STEP0 tool_calls:", json.dumps(report["steps"][0]["tool_calls"] if report["steps"] else None)[:300], "finish:", report["steps"][0]["finish"] if report["steps"] else None)
print("ARTIFACT", h)
