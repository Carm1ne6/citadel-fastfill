---
description: Replay a recording through FastFill / SmartProvide and summarize the report next to the SEC's numbers
argument-hint: "[data folder, e.g. data/run1] [replay options such as --preset latency --set tox_threshold=0.8]"
allowed-tools: Bash(citadel-fastfill:*), Bash(pip install -e:*), Read, Glob
---
Replay a recorded session through the rebuild (SEC order 33-10280, rule by rule; a simulation on public data).

Arguments: `$ARGUMENTS`

1. If `citadel-fastfill --help` fails, run `pip install -e ".[test]"` from the repository folder.
2. The recording is the first argument if it is a folder; otherwise use the newest folder under `data/` that holds
   `*.jsonl.gz` files. If there is none, say so and offer `citadel-fastfill record --minutes 5 --out data/run1`.
3. Run `citadel-fastfill replay --data <folder> <the other arguments>`.
4. Summarize the report in a short table: metric, the SEC's number with its paragraph, this run. Cover the FastFill
   share and odd-lot share, trade outs (share of FastFill BTC and fill rate), SmartProvide (share, fill rate,
   internalized after), the toxicity test (AUC out of sample against its best single parameter) and the profit
   analyzer's tab. Say that the shares are not comparable with the SEC's (RULES.md section 10).
5. Mention `out/<run>/events.jsonl` and offer `/explain` for any order.
