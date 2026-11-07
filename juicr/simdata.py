"""Canned reference answers used by (a) the oracle tests as the "should pass" set and (b) the service's
simulated backend, so the UI can be exercised end-to-end without Lemonade. Single source of truth."""

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

D1_REF = ("Launch date: May 2 [B]\nBudget: $40,000 [A]\nOwner: Not provided in either document.\n"
          "Explanation: Document B supersedes Document A on the schedule only, moving the launch from April 15 to May 2 [B]; "
          "the $40,000 budget in Document A is unchanged [A].")

W1_REF = '''Introducing Cedar: a small pilot for document search

We are starting a small internal pilot called Cedar to help our analysts find information in documents more easily. The pilot is intentionally limited, and we will learn from how the team uses it before making any decisions about what comes next.

- Cedar begins on May 2 and is open to a group of 12 analysts who will try it in their regular work.
- The pilot has a budget of $40,000, which covers the time and tools needed to run it carefully.
- Because search results can be wrong, a person will check every citation by hand before anyone relies on an answer.

Thank you for your patience while the team tries Cedar. We will share what we learn.'''

E1_REF = '{"invoice_id": "INV-1042", "vendor": "Northwind Lab", "issued_date": "2026-03-10", "currency": "USD", "total": 1250.00, "due_date": null}'


def call(name, args):
    return {"name": name, "arguments": args}


A1_REF_STEPS = [
    {"content": "", "tool_calls": [call("search_documents", {"query": "Cedar launch date budget"})], "tokens": 30},
    {"content": "", "tool_calls": [call("search_documents", {"query": "Cedar launch"})], "tokens": 30},
    {"content": "", "tool_calls": [call("read_document", {"id": "A"}), call("read_document", {"id": "B"})], "tokens": 30},
    {"content": "Draft status note: Cedar launches May 2 (moved by Document B) with a $40,000 budget (Document A). Not sent or saved.",
     "tool_calls": [], "tokens": 60},
]

SINGLE_REFS = {"C1": C1_REF, "D1": D1_REF, "W1": W1_REF, "E1": E1_REF}
