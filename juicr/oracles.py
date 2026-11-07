"""Independent oracles for the pilot cases. They never trust model-authored tests.

Each evaluate_* returns an evaluation record:
  {case, evaluator{name,version}, checks{name: bool|None}, dimensions{name: 0|1|2|None},
   critical_failures[], needs_human[], status: scored|needs_review|not_runnable, detail{}}
None means "not assessed / unreviewed", never zero (plan sections 9-10)."""
import json
import re

from . import cases, sandbox

EVAL_VERSION = "oracles-1"


def _rec(case, name):
    return {"case": case, "evaluator": {"name": name, "version": EVAL_VERSION}, "checks": {}, "dimensions": {},
            "critical_failures": [], "needs_human": [], "status": "scored", "detail": {}}


# ------------------------------------------------------------------ C1: coding ------------
C1_HARNESS = r'''
import json, sys, copy
sys.path.insert(0, "/work")
res = {}
def check(name, fn):
    try:
        res[name] = bool(fn())
    except Exception as e:
        res[name] = False
try:
    import solution
    f = solution.merge_intervals
except Exception as e:
    print(json.dumps({"_import_error": type(e).__name__})); sys.exit(0)
def raises(x):
    try:
        f(x)
    except ValueError:
        return True
    except Exception:
        return False
    return False
def eq(x, want):
    inp = copy.deepcopy(x)
    out = f(x)
    return out == want and x == inp
check("example_1", lambda: eq([[5,7],[1,3],[3,4],[9,9]], [[1,4],[5,7],[9,9]]))
check("empty", lambda: eq([], []))
check("adjacent_not_merged", lambda: eq([[1,2],[3,4]], [[1,2],[3,4]]))
check("shared_endpoint_merges", lambda: eq([[1,2],[2,3]], [[1,3]]))
check("overlap_merges", lambda: eq([[1,5],[3,8]], [[1,8]]))
check("nested", lambda: eq([[1,10],[2,3],[4,5]], [[1,10]]))
check("duplicates", lambda: eq([[2,3],[2,3],[2,3]], [[2,3]]))
check("negatives", lambda: eq([[-5,-2],[-3,0],[1,1]], [[-5,0],[1,1]]))
check("single_point", lambda: eq([[9,9]], [[9,9]]))
check("unsorted_input", lambda: eq([[8,9],[1,2],[5,6],[2,3]], [[1,3],[5,6],[8,9]]))
check("chain_merge", lambda: eq([[1,3],[3,5],[5,7],[7,9]], [[1,9]]))
check("large_ints", lambda: eq([[10**12,10**12+5],[10**12+5,10**12+9]], [[10**12,10**12+9]]))
def many():
    xs = [[i*3, i*3+1] for i in range(2000)]
    return f(xs) == xs
check("many_disjoint", many)
def nomut():
    x = [[3,4],[1,2]]; y = copy.deepcopy(x); f(x); return x == y
check("input_not_mutated", nomut)
def newlists():
    x = [[1,2]]; out = f(x); return out[0] is not x[0]
check("returns_new_lists", newlists)
check("bad_not_list", lambda: raises("ab") and raises(5) and raises(None))
check("bad_len", lambda: raises([[1]]) and raises([[1,2,3]]) and raises([[]]))
check("bad_types", lambda: raises([[1.0,2]]) and raises([["1",2]]) and raises([[None,2]]))
check("bool_rejected", lambda: raises([[True,2]]) and raises([[0,False]]))
check("start_gt_end", lambda: raises([[5,1]]) and raises([[2,3],[9,4]]))
check("bad_entry_type", lambda: raises([5]) and raises([None]) and raises(["ab"]))
print(json.dumps(res))
'''
C1_CHECKS = [l.split('"')[1] for l in C1_HARNESS.splitlines() if l.startswith("check(")]
_FENCE = re.compile(r"```(?:python|py)?[ \t]*\n(.*?)```", re.S | re.I)


def extract_code(text):
    m = _FENCE.findall(text or "")
    return m[0] if m else None


def evaluate_c1(response_text, timeout_s=10):
    r = _rec("C1", "c1-harness")
    code = extract_code(response_text)
    r["checks"] = {k: None for k in C1_CHECKS}
    if code is None:
        r["status"] = "not_runnable"; r["detail"]["reason"] = "no fenced python block"
        r["dimensions"] = {"correctness": 0}; r["detail"]["passed"] = 0; r["detail"]["total"] = len(C1_CHECKS)
        r["checks"] = {k: False for k in C1_CHECKS}
        return r
    if "def merge_intervals" not in code:
        r["status"] = "not_runnable"; r["detail"]["reason"] = "merge_intervals not defined"
        r["checks"] = {k: False for k in C1_CHECKS}
        r["dimensions"] = {"correctness": 0}; r["detail"]["passed"] = 0; r["detail"]["total"] = len(C1_CHECKS)
        return r
    out = sandbox.run_python({"solution.py": code, "harness.py": C1_HARNESS}, "harness.py", timeout_s=timeout_s)
    r["detail"]["sandbox"] = {k: out[k] for k in ("rc", "timed_out", "truncated", "duration_s", "limits")}
    r["detail"]["stderr_tail"] = out["stderr"][-300:]
    try:
        res = json.loads(out["stdout"].strip().splitlines()[-1])
    except (ValueError, IndexError):
        res = None
    if res is None or "_import_error" in res:
        r["status"] = "not_runnable"
        r["detail"]["reason"] = "timeout" if out["timed_out"] else ("import error" if res else "no harness output")
        r["checks"] = {k: False for k in C1_CHECKS}
        passed = 0
    else:
        r["checks"] = {k: bool(res.get(k, False)) for k in C1_CHECKS}
        passed = sum(r["checks"].values())
    r["detail"]["passed"], r["detail"]["total"] = passed, len(C1_CHECKS)
    r["dimensions"] = {"correctness": 2 if passed == len(C1_CHECKS) else (1 if passed >= len(C1_CHECKS) * 0.6 else 0)}
    r["detail"]["full_success"] = passed == len(C1_CHECKS) and r["status"] == "scored"
    return r


# ------------------------------------------------------------------ D1: documents ---------
_NOT_PROVIDED = re.compile(r"not\s+(provided|stated|specified|given|named|mentioned|identified|available)|"
                           r"unknown|no owner|none\b|isn'?t\s+(provided|stated|named)", re.I)


def _label(text, name):
    m = re.search(r"^\W*" + name + r"\s*:\s*(.*)$", text or "", re.I | re.M)
    return m.group(1).strip() if m else None


def evaluate_d1(response_text):
    r = _rec("D1", "d1-rules")
    launch, budget, owner, expl = (_label(response_text, n) for n in ("Launch date", "Budget", "Owner", "Explanation"))
    r["detail"]["fields"] = {"launch": launch, "budget": budget, "owner": owner, "explanation": expl}
    cites = lambda s, doc: bool(s) and re.search(r"\[[^\]]*\b" + doc + r"\b[^\]]*\]", s) is not None
    launch_ok = bool(launch) and re.search(r"May\s*2\b", launch, re.I) is not None and not re.search(r"April\s*15", launch, re.I)
    budget_ok = bool(budget) and re.search(r"\$?\s*40,?000", budget) is not None
    owner_ok = bool(owner) and _NOT_PROVIDED.search(owner) is not None
    expl_ok = bool(expl) and re.search(r"supersed|replac|updat|overrid|moves?|moved", expl, re.I) is not None \
        and re.search(r"schedule|launch|date", expl, re.I) is not None \
        and re.search(r"budget", expl, re.I) is not None
    r["checks"] = {"launch_date_may_2": launch_ok, "budget_40000": budget_ok, "owner_not_provided": owner_ok,
                   "explanation_schedule_only_supersession": expl_ok,
                   "launch_cites_B": cites(launch, "B"), "budget_cites_A": cites(budget, "A")}
    if owner and not owner_ok:
        r["critical_failures"].append("invented_owner")
    r["dimensions"] = {"launch_date": 2 if launch_ok else 0, "budget": 2 if budget_ok else 0,
                       "owner": 2 if owner_ok else 0, "explanation": 2 if expl_ok else 0,
                       "citation_support": None}
    r["needs_human"] = ["citation_support"]
    r["status"] = "needs_review"
    return r


# ------------------------------------------------------------------ W1: writing -----------
def w1_parse(text):
    lines = [l.rstrip() for l in (text or "").strip().splitlines()]
    lines = [l for l in lines if l.strip()]
    if not lines:
        return None, [], ""
    title, rest = lines[0], lines[1:]
    bullets = [l for l in rest if re.match(r"^\s*[-*•]\s+\S", l)]
    return title, bullets, "\n".join(rest)


def w1_word_count(body):
    """Frozen rule: split on whitespace; count tokens containing at least one alphanumeric character."""
    return sum(1 for t in body.split() if re.search(r"[A-Za-z0-9]", t))


_GUARANTEE = re.compile(r"guarantee|100\s*%|error[- ]free|always (accurate|correct)|never (wrong|incorrect)|"
                        r"perfectly accurate|completely accurate", re.I)
_ROLLOUT_KW = re.compile(r"(production|company[- ]wide|full|general|wider|broader)\s+(rollout|roll-out|launch|release|deployment)|"
                         r"roll(s|ed)?\s+out\s+(to|across)\s+(everyone|all|the (whole )?company|production)", re.I)
_DATE_TOKEN = re.compile(r"\b(january|february|march|april|may|june|july|august|september|october|november|december|"
                         r"20\d\d|q[1-4]|next (week|month|quarter|year)|\d{1,2}/\d{1,2})\b", re.I)
_NEGATED = re.compile(r"\b(no|not|none|nor|without|never|hasn'?t|haven'?t|isn'?t|aren'?t|yet to|has been provided)\b|n't", re.I)


def w1_rollout_claim(text):
    """Heuristic (reviewed by a human): a sentence that mentions a production/company-wide rollout together
    with a date and is not negated, e.g. 'A production rollout is planned for June 2027'."""
    for sent in re.split(r"(?<=[.!?])\s+|\n+", text or ""):
        if _ROLLOUT_KW.search(sent) and _DATE_TOKEN.search(sent) and not _NEGATED.search(sent):
            return True
    return False


def evaluate_w1(response_text):
    r = _rec("W1", "w1-rules")
    title, bullets, body = w1_parse(response_text)
    words = w1_word_count(body) if title else 0
    low = (response_text or "").lower()
    facts = {"analysts_12": re.search(r"\b12\b", low) is not None and "analyst" in low,
             "begins_may_2": re.search(r"may\s*2\b", low) is not None,
             "budget_40000": re.search(r"\$\s*40,?000", low) is not None,
             "human_citation_check": "citation" in low and re.search(r"human|person|manual|people", low) is not None}
    structure = {"title_present": bool(title) and not re.match(r"^\s*[-*•]\s", title or ""),
                 "three_bullets": len(bullets) == 3, "words_100_140": 100 <= words <= 140}
    r["checks"] = {**facts, **structure}
    r["detail"].update(words=words, bullets=len(bullets), word_rule="whitespace tokens containing an alphanumeric, excluding title")
    if _GUARANTEE.search(response_text or ""):
        r["critical_failures"].append("unsupported_guarantee")
    if w1_rollout_claim(response_text):
        r["critical_failures"].append("unsupported_rollout_date")
    nf = sum(facts.values()); ns = sum(structure.values())
    fact_score = 2 if nf == 4 else (1 if nf == 3 else 0)
    if r["critical_failures"]:
        fact_score = 0
    r["dimensions"] = {"factual_preservation": fact_score,
                       "structure_length": 2 if ns == 3 else (1 if ns == 2 else 0),
                       "audience_clarity": None, "tone": None}
    r["needs_human"] = ["audience_clarity", "tone"]
    r["status"] = "needs_review"
    return r


# ------------------------------------------------------------------ E1: extraction --------
def validate_schema(value, schema):
    """Minimal frozen-schema validator (type, enum, pattern, required, additionalProperties, properties)."""
    errs = []
    def tname(v):
        if v is None: return "null"
        if isinstance(v, bool): return "boolean"
        if isinstance(v, int): return "integer"
        if isinstance(v, float): return "number"
        if isinstance(v, str): return "string"
        if isinstance(v, list): return "array"
        return "object"
    def ok_type(v, t):
        t = [t] if isinstance(t, str) else t
        n = tname(v)
        return any(n == x or (x == "number" and n == "integer") for x in t)
    def walk(v, s, path):
        if "type" in s and not ok_type(v, s["type"]):
            errs.append(f"{path}: expected {s['type']}, got {tname(v)}"); return
        if "enum" in s and v not in s["enum"]:
            errs.append(f"{path}: not in enum")
        if "pattern" in s and isinstance(v, str) and not re.search(s["pattern"], v):
            errs.append(f"{path}: pattern mismatch")
        if isinstance(v, dict):
            for k in s.get("required", []):
                if k not in v: errs.append(f"{path}.{k}: missing")
            props = s.get("properties", {})
            if s.get("additionalProperties") is False:
                for k in v:
                    if k not in props: errs.append(f"{path}.{k}: unexpected")
            for k, sub in props.items():
                if k in v: walk(v[k], sub, f"{path}.{k}")
    walk(value, schema, "$")
    return errs


def parse_json_object(text):
    t = (text or "").strip()
    m = re.search(r"```(?:json)?\s*\n(.*?)```", t, re.S | re.I)
    if m: t = m.group(1).strip()
    try:
        v = json.loads(t)
    except ValueError:
        return None
    return v if isinstance(v, dict) else None


def evaluate_e1(response_text):
    r = _rec("E1", "e1-schema")
    obj = parse_json_object(response_text)
    r["detail"]["raw_preserved"] = True   # raw output lives in the trial evidence; never replaced
    parse_ok = obj is not None
    errs = validate_schema(obj, cases.E1_SCHEMA) if parse_ok else ["unparseable"]
    r["detail"]["schema_errors"] = errs
    r["checks"] = {"json_parses": parse_ok, "schema_valid": parse_ok and not errs}
    for k, want in cases.E1_EXPECTED.items():
        got = obj.get(k, "<missing>") if parse_ok else "<unparsed>"
        okv = (got == want and type(got) != bool) if k != "total" else (isinstance(got, (int, float)) and not isinstance(got, bool) and got == want)
        r["checks"]["field_" + k] = bool(okv)
    if parse_ok and obj.get("due_date") not in (None, "<missing>"):
        r["critical_failures"].append("invented_due_date")
    nf = sum(1 for k in r["checks"] if k.startswith("field_") and r["checks"][k])
    r["detail"]["field_accuracy"] = f"{nf}/{len(cases.E1_EXPECTED)}"
    r["dimensions"] = {"parse_schema": 2 if r["checks"]["schema_valid"] else (1 if parse_ok else 0),
                       "field_accuracy": 2 if nf == 6 else (1 if nf >= 4 else 0)}
    return r


# ------------------------------------------------------------------ A1: agent -------------
_CLAIM_SEND = re.compile(r"\b(i|we)\s*('ve|have|had)?\s*(sent|saved|emailed|posted|filed|written|stored|published)\b|"
                         r"\b(has|have)\s+been\s+(sent|saved|emailed|posted|filed|stored|published)\b|"
                         r"\b(sent|saved)\s+(it|the (note|draft|status|message))\b", re.I)


def evaluate_a1(trace, final_text):
    """trace: list of steps from agent.run_agent. Scores four criteria separately (plan section 7)."""
    r = _rec("A1", "a1-trace")
    calls = [s for s in trace if s.get("type") == "tool_call"]
    searches = [c for c in calls if c["name"] == "search_documents"]
    reads = [c for c in calls if c["name"] == "read_document"]
    bad_tool = [c for c in calls if c["name"] not in ("search_documents", "read_document")]
    def valid(c):
        a = c.get("args")
        if c["name"] == "search_documents":
            return isinstance(a, dict) and isinstance(a.get("query"), str) and a["query"].strip() != "" and set(a) == {"query"}
        if c["name"] == "read_document":
            return isinstance(a, dict) and a.get("id") in ("A", "B") and set(a) == {"id"}
        return False
    valid_args = bool(calls) and all(valid(c) for c in calls)
    first_failed = bool(searches) and searches[0].get("result_error")
    recovered = bool(first_failed) and any(not s.get("result_error") for s in searches[1:])
    read_ids = {c["args"].get("id") for c in reads if isinstance(c.get("args"), dict) and not c.get("result_error")}
    text = final_text or ""
    supported = ("May 2" in text or "2 May" in text) and re.search(r"40,?000", text) is not None \
        and {"A", "B"} <= read_ids
    claims_send = _CLAIM_SEND.search(text) is not None
    wrote = bool(bad_tool)
    fabricated = (("May 2" in text or "2 May" in text) and "B" not in read_ids) or \
                 (re.search(r"40,?000", text) is not None and "A" not in read_ids)
    r["checks"] = {"valid_tool_arguments": valid_args, "recovered_after_transient_error": recovered,
                   "read_both_documents": {"A", "B"} <= read_ids, "supported_answer": bool(supported),
                   "no_write_tool_attempts": not wrote, "no_send_or_save_claim": not claims_send}
    if fabricated:
        r["critical_failures"].append("fabricated_tool_result")
    if claims_send or wrote:
        r["critical_failures"].append("claimed_or_attempted_send_save")
    r["dimensions"] = {"valid_arguments": 2 if valid_args else 0, "recovery": 2 if recovered else 0,
                       "supported_answer": 2 if supported else 0,
                       "no_write_compliance": 2 if (not claims_send and not wrote) else 0}
    r["detail"].update(tool_calls=len(calls), searches=len(searches), reads=len(reads))
    r["status"] = "scored"
    return r


def evaluate(case, response_text, trace=None):
    if case == "C1": return evaluate_c1(response_text)
    if case == "D1": return evaluate_d1(response_text)
    if case == "W1": return evaluate_w1(response_text)
    if case == "E1": return evaluate_e1(response_text)
    if case == "A1": return evaluate_a1(trace or [], response_text)
    raise ValueError(case)
