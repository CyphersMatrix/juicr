"""Pilot case pack (plan section 7). Synthetic only. Version-pinned; all text frozen here.
Status: owner approved fixtures, sampling values and limits as-is on 2026-11-07 (PILOT_REVIEW.md). Approval to RUN and to PUBLISH are separate and still pending."""
PACK_VERSION = "pilot-1"
PACK_STATUS = "approved-fixtures-limits-20261107; run-approval-pending; A1-admitted-compat-verified"

# Sampling: top_p/top_k are the model's own load-time defaults observed on 2026-11-07 (not invented);
# temperature is the experimental condition; seeds are the paired seeds from the plan.
SAMPLING_FROZEN = {"top_p": 0.95, "top_k": 64}
CONDITIONS = (0.0, 0.7)
SEEDS = (17, 43, 101)
LIMITS_SINGLE = {"max_tokens": 1024, "deadline_s": 180}
LIMITS_AGENT = {"max_requests": 4, "max_tool_calls": 4, "max_tokens": 4096, "deadline_s": 600}

DOC_A = ("Document A (dated March 1). Project Cedar launches on April 15 with a budget of $40,000.")
DOC_B = ("Document B (dated March 10). This memo supersedes Document A on the schedule only: "
         "the Cedar launch moves to May 2. Nothing else in Document A changes.")

C1_PROMPT = (
    "Implement a Python function `merge_intervals(intervals)`.\n"
    "- `intervals` is a list of intervals; each interval is a list of two integers [start, end] with start <= end.\n"
    "- Malformed input must raise ValueError: an interval that is not a list of exactly two integers, "
    "a non-integer value (booleans are NOT integers), or start > end.\n"
    "- Return a new list of merged intervals sorted by start, without mutating the input.\n"
    "- Intervals that overlap OR share an endpoint merge; intervals that are merely adjacent integers do not.\n"
    "Examples: [[5,7],[1,3],[3,4],[9,9]] -> [[1,4],[5,7],[9,9]]; [] -> []; [[1,2],[3,4]] -> [[1,2],[3,4]].\n"
    "Reply with exactly one fenced ```python code block containing the complete function and nothing else.")

D1_PROMPT = (
    "Use only the two documents below.\n\n" + DOC_A + "\n\n" + DOC_B + "\n\n"
    "Answer in exactly these four labelled lines, citing the source document ID in square brackets "
    "for each fact, e.g. [A] or [B]. If a fact is not provided, say it is not provided.\n"
    "Launch date: ...\nBudget: ...\nOwner: ...\nExplanation: ...")

W1_SOURCE = ("Cedar is an internal document-search pilot for 12 analysts. It begins May 2 and has a $40,000 budget. "
             "It requires human citation checking. No production rollout date and no guarantee of accuracy has been provided.")
W1_PROMPT = (
    "Source facts: " + W1_SOURCE + "\n\n"
    "Write a calm, clear announcement for nontechnical staff. Format: a one-line title, then a short paragraph, "
    "then exactly three bullet points (each starting with '- '). The title does not count toward length: "
    "everything after the title must total 100 to 140 words. Use only the source facts; do not add dates, "
    "promises or guarantees.")

E1_INPUT = ("Invoice INV-1042; vendor Northwind Lab; issued 2026-03-10; total USD 1,250.00. "
            "Footer: old draft total USD 1,200.00; superseded. Payment due date: not supplied.")
E1_PROMPT = (
    "Extract the invoice fields from the text below. Return only a JSON object with exactly these fields and no "
    "others: invoice_id, vendor, issued_date, currency, total, due_date. Dates use ISO YYYY-MM-DD; total is a JSON "
    "number; use null for any field the text does not supply. Use the current (not superseded) amount.\n\nText: " + E1_INPUT)
E1_SCHEMA = {"type": "object", "additionalProperties": False,
             "required": ["invoice_id", "vendor", "issued_date", "currency", "total", "due_date"],
             "properties": {"invoice_id": {"type": "string"}, "vendor": {"type": "string"},
                            "issued_date": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"},
                            "currency": {"type": "string", "pattern": r"^[A-Z]{3}$"},
                            "total": {"type": "number"},
                            "due_date": {"type": ["string", "null"], "pattern": r"^\d{4}-\d{2}-\d{2}$"}}}
E1_EXPECTED = {"invoice_id": "INV-1042", "vendor": "Northwind Lab", "issued_date": "2026-03-10",
               "currency": "USD", "total": 1250, "due_date": None}

A1_PROMPT = ("Find Cedar's current launch date and budget, then draft a short status note. "
             "Do not send or save it. Use the tools to look things up; do not guess.")
A1_TOOLS = [
    {"type": "function", "function": {"name": "search_documents", "description": "Search project documents.",
     "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "read_document", "description": "Read one document by id.",
     "parameters": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}}}]
A1_DOCS = {"A": DOC_A, "B": DOC_B}

CASES = {
    "C1": {"workload": "coding", "title": "merge intervals", "prompt": C1_PROMPT, "kind": "single"},
    "D1": {"workload": "document analysis", "title": "conflicting dates", "prompt": D1_PROMPT, "kind": "single"},
    "W1": {"workload": "writing", "title": "audience and factual preservation", "prompt": W1_PROMPT, "kind": "single"},
    "E1": {"workload": "extraction", "title": "superseded invoice amount", "prompt": E1_PROMPT, "kind": "single"},
    "A1": {"workload": "agent", "title": "recover, read, draft", "prompt": A1_PROMPT, "kind": "agent",
           "admission": "admitted: tool-call compatibility verified 2026-11-07 (evidence/a1-compat-20261107.json)"},
}


# --- pilot-2 (owner-approved 2026-11-07): rerun of C1, W1 and A1 only, with the two limits that confounded run 1 raised.
# Run 1 showed hidden reasoning consumes the response budget (C1 hit 1,024 tokens 5/6) and that four sequential tool
# calls plus a final answer need five requests. Fixtures, prompts, sampling and seeds are unchanged.
LIMITS_SINGLE_2 = {"max_tokens": 4096, "deadline_s": 300}
LIMITS_AGENT_2 = {"max_requests": 6, "max_tool_calls": 4, "max_tokens": 4096, "deadline_s": 600, "turn_max_tokens": 1536}
PACKS = {
    "pilot-1": {"single": LIMITS_SINGLE, "agent": LIMITS_AGENT, "cases": ("C1", "D1", "W1", "E1", "A1"),
                "status": PACK_STATUS, "order_seed": 20261107, "token_budget": 60000},
    "pilot-2": {"single": LIMITS_SINGLE_2, "agent": LIMITS_AGENT_2, "cases": ("C1", "W1", "A1"),
                "status": "approved-by-owner-20261107 (rerun of C1,W1,A1 with raised caps); run-approval-pending",
                "order_seed": 20261108, "token_budget": 100000},
}
