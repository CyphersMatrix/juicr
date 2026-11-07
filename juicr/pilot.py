"""Pilot pack builder (plan section 7): 5 cases x 2 temperatures x 3 seeds = 30 top-level trials,
matched-pair order randomized with a recorded RNG seed."""
import random

from . import cases

ORDER_RNG_SEED = 20261107


def build_pilot(model, order_seed=ORDER_RNG_SEED, ctx_size=8192, include_agent=True):
    rng = random.Random(order_seed)
    pairs = []
    for cid, c in cases.CASES.items():
        if c["kind"] == "agent" and not include_agent:
            continue
        for seed in cases.SEEDS:
            conds = list(cases.CONDITIONS)
            rng.shuffle(conds)                       # within-pair order, recorded below
            pairs.append({"pair_id": f"{cid}-s{seed}", "case": cid, "seed": seed, "order": conds})
    rng.shuffle(pairs)                               # pair order across the run
    trials, idx = [], 0
    for p in pairs:
        for temp in p["order"]:
            idx += 1
            tid = f"{p['pair_id']}-t{str(temp).replace('.', '')}"
            c = cases.CASES[p["case"]]
            body = {"model": model, "messages": [{"role": "user", "content": c["prompt"]}], "temperature": temp,
                    "seed": p["seed"], **cases.SAMPLING_FROZEN}
            if c["kind"] == "single":
                body["max_tokens"] = cases.LIMITS_SINGLE["max_tokens"]
                spec = {"kind": "single", "request": body, "deadline_s": cases.LIMITS_SINGLE["deadline_s"], "max_attempts": 1}
                cost = {"requests": 1, "tokens": cases.LIMITS_SINGLE["max_tokens"], "seconds": cases.LIMITS_SINGLE["deadline_s"]}
            else:
                spec = {"kind": "agent", "request": body, "limits": dict(cases.LIMITS_AGENT), "max_attempts": 1,
                        "admission": c["admission"]}
                L = cases.LIMITS_AGENT
                cost = {"requests": L["max_requests"], "tool_calls": L["max_tool_calls"], "tokens": L["max_tokens"],
                        "seconds": L["deadline_s"]}
            trials.append({"trial_id": tid, "pair_id": p["pair_id"], "case": p["case"], "temperature": temp,
                           "seed": p["seed"], "order_index": idx, "spec": spec, "max_cost": cost})
    return {"pack_version": cases.PACK_VERSION, "pack_status": cases.PACK_STATUS, "order_rng_seed": order_seed,
            "model": model, "load": {"ctx_size": ctx_size}, "pairs": pairs, "trials": trials,
            "sampling_frozen": cases.SAMPLING_FROZEN, "limits": {"single": cases.LIMITS_SINGLE, "agent": cases.LIMITS_AGENT}}


def accounting(pack):
    """Plan arithmetic: 30 top-level trials; 24 single-response requests + up to 24 agent requests = 48."""
    t = pack["trials"]
    single = [x for x in t if x["spec"]["kind"] == "single"]
    agent = [x for x in t if x["spec"]["kind"] == "agent"]
    return {"top_level": len(t), "single_requests": sum(x["max_cost"]["requests"] for x in single),
            "agent_requests_max": sum(x["max_cost"]["requests"] for x in agent),
            "measured_requests_max": sum(x["max_cost"]["requests"] for x in t),
            "agent_tool_calls_max": sum(x["max_cost"].get("tool_calls", 0) for x in agent)}
