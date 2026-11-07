"""Interference watcher for a pilot window (plan decision 7: record observed interference, label exclusivity
unverified). Polls Lemonade health+stats every INTERVAL s and appends JSONL. Read-only, GET only.
Usage: python pilot_watch.py KEYFILE_ENV OUTFILE DURATION_S [INTERVAL_S]   (KEYFILE_ENV: a file with LEMONADE_API_KEY=...)"""
import json, sys, time, urllib.request

env, out, dur = sys.argv[1], sys.argv[2], float(sys.argv[3])
interval = float(sys.argv[4]) if len(sys.argv) > 4 else 15
key = [l.split("=", 1)[1].strip().strip("\"'") for l in open(env) if l.startswith("LEMONADE_API_KEY=")][0]
def get(p):
    r = urllib.request.Request("http://10.138.6.1:13305" + p, headers={"Authorization": "Bearer " + key})
    return json.load(urllib.request.urlopen(r, timeout=10))
t_end = time.time() + dur
with open(out, "a") as f:
    while time.time() < t_end:
        rec = {"ts": time.time()}
        try:
            h, s = get("/api/v1/health"), get("/api/v1/stats")
            rec["models"] = [{"name": m["model_name"], "busy": bool(m["is_busy"]), "streaming": bool(m["is_streaming"]),
                              "ctx": m["recipe_options"].get("ctx_size"), "device": m["device"]} for m in h["all_models_loaded"]]
            rec["requests_total"] = s["request_count_total"]; rec["active"] = s["live"]["active_requests"]
            rec["tps"] = s.get("tokens_per_second"); rec["ttft"] = s.get("time_to_first_token")
        except Exception as e:
            rec["error"] = type(e).__name__
        f.write(json.dumps(rec) + "\n"); f.flush()
        time.sleep(interval)
