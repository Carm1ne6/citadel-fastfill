---
description: Explain one event or order of a run step by step, with the SEC paragraph and patent passage behind each step
argument-hint: "[order id, or a rule such as TRADE_OUT] [--run out/run1]"
allowed-tools: Bash(citadel-fastfill:*), Read, Grep
---
Explain an event of a replay, step by step, against the documents.

Arguments: `$ARGUMENTS`

1. The run is `--run <folder>` if given, else the newest folder under `out/` with an `events.jsonl` (if there is none,
   offer `/replay`).
2. If the arguments hold an order id, run `citadel-fastfill explain --run <run> --id <id>`. If they hold a rule
   (FASTFILL, TRADE_OUT, SMARTPROVIDE, SP_FILLED, SP_REEVAL), run
   `citadel-fastfill explain --run <run> --rule <RULE>`. With no arguments, list a few candidates first with
   `citadel-fastfill explain --run <run> --list --limit 10` and explain the first FastFill with a trade out if there
   is one, else the first FastFill.
3. Retell the output in plain words, one short paragraph per step: what the engine saw (prices, sizes, times), what
   it did, and the exact quote from the SEC order or the patent behind it (keep quotes word for word, with their
   paragraph, footnote or column:line). Name every step that rests on an OURS choice.
4. Never describe the code as Citadel's; it is a rebuild from the SEC order and the patent.
