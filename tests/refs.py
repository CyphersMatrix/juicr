"""Reference (should pass) and deliberately wrong (should fail) outputs for the oracle checks."""
C1_REF = '''```python
def merge_intervals(intervals):
    if not isinstance(intervals, list):
        raise ValueError("intervals must be a list")
    clean = []
    for iv in intervals:
        if not isinstance(iv, list) or len(iv) != 2:
            raise ValueError("bad interval")
        a, b = iv
        for v in (a, b):
            if isinstance(v, bool) or not isinstance(v, int):
                raise ValueError("bad value")
        if a > b:
            raise ValueError("start > end")
        clean.append((a, b))
    clean.sort()
    out = []
    for a, b in clean:
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out
```'''


def c1_variant(old, new):
    assert old in C1_REF
    return C1_REF.replace(old, new, 1)


C1_WRONG = {
    "merges_adjacent": c1_variant("a <= out[-1][1]", "a <= out[-1][1] + 1"),
    "mutates_input": c1_variant("clean.sort()", "intervals.sort()\n    clean.sort()"),
    "accepts_bool": c1_variant("if isinstance(v, bool) or not isinstance(v, int):", "if not isinstance(v, int):"),
    "no_end_validation": c1_variant("        if a > b:\n            raise ValueError(\"start > end\")\n", ""),
    "wrong_merge_max": c1_variant("max(out[-1][1], b)", "b"),
    "infinite_loop": "```python\ndef merge_intervals(intervals):\n    while True:\n        pass\n```",
    "no_code_block": "def merge_intervals(i): return i",
    "wrong_name": "```python\ndef merge(intervals):\n    return intervals\n```",
    "syntax_error": "```python\ndef merge_intervals(intervals:\n    return\n```",
    "writes_file_attempt": "```python\nopen('/work/x','w').write('x')\ndef merge_intervals(i):\n    return i\n```",
}

D1_REF = ("Launch date: May 2 [B]\nBudget: $40,000 [A]\nOwner: Not provided in either document.\n"
          "Explanation: Document B supersedes Document A on the schedule only, moving the launch from April 15 to May 2 [B]; "
          "the $40,000 budget in Document A is unchanged [A].")
D1_WRONG = {
    "stale_date": D1_REF.replace("May 2 [B]", "April 15 [A]"),
    "invented_owner": D1_REF.replace("Not provided in either document.", "Maria Chen [A]"),
    "wrong_budget": D1_REF.replace("$40,000", "$45,000"),
    "no_explanation": D1_REF.split("Explanation:")[0] + "Explanation: See documents.",
    "unlabelled": "The launch is May 2 and the budget is $40,000.",
}

W1_REF = open(__file__.replace("refs.py", "w1_ref.txt")).read()
W1_WRONG = {
    "guarantee": W1_REF.replace("Because search results can be wrong,", "Cedar guarantees accurate answers, and"),
    "rollout_date": W1_REF.replace("Thank you for your patience", "A production rollout is planned for June 2027. Thank you for your patience"),
    "four_bullets": W1_REF + "\n- One more point here.",
    "too_short": "Cedar pilot\n\nCedar starts May 2 for 12 analysts with a $40,000 budget and human citation checks.\n- a\n- b\n- c",
    "missing_budget": W1_REF.replace("$40,000", "a modest amount"),
}

E1_REF = '{"invoice_id": "INV-1042", "vendor": "Northwind Lab", "issued_date": "2026-03-10", "currency": "USD", "total": 1250.00, "due_date": null}'
E1_WRONG = {
    "invented_due_date": E1_REF.replace('"due_date": null', '"due_date": "2026-04-09"'),
    "superseded_total": E1_REF.replace("1250.00", "1200.00"),
    "string_total": E1_REF.replace("1250.00", '"1,250.00"'),
    "extra_field": E1_REF[:-1] + ', "notes": "x"}',
    "bad_date_format": E1_REF.replace("2026-03-10", "10/03/2026"),
    "unparseable": "Sure! The invoice is INV-1042 from Northwind Lab.",
    "missing_field": '{"invoice_id": "INV-1042", "vendor": "Northwind Lab"}',
}


def call(name, args):
    return {"name": name, "arguments": args}


def scripted(steps):
    """step_fn from a list of assistant messages."""
    it = iter(steps)
    return lambda messages, tools: next(it)


A1_REF_STEPS = [
    {"content": "", "tool_calls": [call("search_documents", {"query": "Cedar launch date budget"})], "tokens": 30},
    {"content": "", "tool_calls": [call("search_documents", {"query": "Cedar launch"})], "tokens": 30},
    {"content": "", "tool_calls": [call("read_document", {"id": "A"}), call("read_document", {"id": "B"})], "tokens": 30},
    {"content": "Draft status note: Cedar launches May 2 (moved by Document B) with a $40,000 budget (Document A). Not sent or saved.",
     "tool_calls": [], "tokens": 60},
]
