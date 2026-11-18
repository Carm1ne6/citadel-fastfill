# Explain the SEC order paragraph by paragraph against the code

Walks through the SEC's order (Release No. 33-10280) and shows, for each paragraph, the code that implements it, the
choices that are ours, and a real event from a replay where the rule fired.

```text
Explain the SEC order in docs/sec_order_2017_33-10280.txt paragraph by paragraph against this code. Read CLAUDE.md
and RULES.md first.

For each numbered paragraph and footnote of the order that describes market structure, CES's order handling,
FastFill, SmartProvide or the numbers (at least paragraphs 1-15, 17-40 and footnotes 9-13):
1. Quote the key sentence exactly, with its paragraph or footnote number.
2. Say where the code implements it: file and function (from RULES.md's "Code, parameter" column, then open the code
   to confirm), or "context only, not modelled" when nothing implements it.
3. Name what the paragraph does not fix and the code had to choose, marked OURS, with the parameter that holds it
   (citadel/params.py) and its default.
4. Where a test reproduces the paragraph's example, name the test (tests/test_sec_examples.py, tests/test_engine_rules.py).

Then make it concrete on data. If out/ has no run, replay a recording first (citadel-fastfill replay --data
data/<run>; record one with citadel-fastfill record --minutes 5 --out data/run1 if data/ is empty). Use
citadel-fastfill explain --run out/<run> --list --rule <RULE> to find, and citadel-fastfill explain --run out/<run>
--id <order id> to show, one event for each of: a FastFill at arrival (paras 10-11, 29-30), a FastFill that took over a
SmartProvide display (para 30), a trade out (para 31, fn 13), a SmartProvide display (paras 12-13, 34-38) and a
re-evaluation (para 39). For each, retell the steps in plain words next to the paragraph they come from.

Finish with the toxicity analyzer: why the order's paragraph 26 needs a decision rule it does not describe, what the
patent US 7,587,347 says (RULES.md section 5, quoted), and why using it here is our choice (the assignee named on the
patent is Citadel Investment Group, not the respondent, and neither document links the two).

Keep every quote word for word. Never call the code Citadel's: it is a rebuild from the SEC order and the patent.
```
