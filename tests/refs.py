"""Reference (should pass) and deliberately wrong (should fail) outputs for the oracle checks."""
from juicr.simdata import C1_REF, D1_REF, W1_REF, E1_REF, A1_REF_STEPS, call  # noqa: F401


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

D1_WRONG = {
    "stale_date": D1_REF.replace("May 2 [B]", "April 15 [A]"),
    "invented_owner": D1_REF.replace("Not provided in either document.", "Maria Chen [A]"),
    "wrong_budget": D1_REF.replace("$40,000", "$45,000"),
    "no_explanation": D1_REF.split("Explanation:")[0] + "Explanation: See documents.",
    "unlabelled": "The launch is May 2 and the budget is $40,000.",
}

W1_WRONG = {
    "guarantee": W1_REF.replace("Because search results can be wrong,", "Cedar guarantees accurate answers, and"),
    "rollout_date": W1_REF.replace("Thank you for your patience", "A production rollout is planned for June 2027. Thank you for your patience"),
    "four_bullets": W1_REF + "\n- One more point here.",
    "too_short": "Cedar pilot\n\nCedar starts May 2 for 12 analysts with a $40,000 budget and human citation checks.\n- a\n- b\n- c",
    "missing_budget": W1_REF.replace("$40,000", "a modest amount"),
}

E1_WRONG = {
    "invented_due_date": E1_REF.replace('"due_date": null', '"due_date": "2026-04-09"'),
    "superseded_total": E1_REF.replace("1250.00", "1200.00"),
    "string_total": E1_REF.replace("1250.00", '"1,250.00"'),
    "extra_field": E1_REF[:-1] + ', "notes": "x"}',
    "bad_date_format": E1_REF.replace("2026-03-10", "10/03/2026"),
    "unparseable": "Sure! The invoice is INV-1042 from Northwind Lab.",
    "missing_field": '{"invoice_id": "INV-1042", "vendor": "Northwind Lab"}',
}


def scripted(steps):
    """step_fn from a list of assistant messages."""
    it = iter(steps)
    return lambda messages, tools: next(it)


