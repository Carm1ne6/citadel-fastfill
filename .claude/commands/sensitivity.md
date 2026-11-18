---
description: Run the SIP latency, round-lot, toxicity-threshold, trade-out and feed sensitivity of a recording and summarize it
argument-hint: "[data folder, e.g. data/run1] [grids such as --latency-grid 1,5,20 --threshold-grid 0.6,0.7,0.8]"
allowed-tools: Bash(citadel-fastfill:*), Read, Glob
---
Run the sensitivity grids and say which conclusions depend on our choices.

Arguments: `$ARGUMENTS`

1. The recording is the first argument if it is a folder; otherwise the newest folder under `data/` with
   `*.jsonl.gz` files.
2. Run `citadel-fastfill sensitivity --data <folder> <the other arguments>` (it reuses `out/<run>/summary.json` when
   it was made with the same parameters and replays otherwise), then the same with `--json` to read exact numbers.
3. Summarize each grid against its source, one line or two each:
   - SIP latency (SEC 22, fn 9: 4.04 / 5.42 ms in early 2010), under both presets; rows under 1 ms are below the
     ~1 ms clock of a Windows recorder.
   - Round lot (SEC 5: 100 shares; 0.0155 BTC is 100 shares at the order's $10.00 example): how the FastFill share
     and the odd-lot share move.
   - Toxicity threshold (OURS): orders without a principal interest and the trade-out share on each half, against
     SEC 31's "less than 6.9%".
   - Trade-out delay (OURS): the fill rate against fn 13's "approximately 55%".
   - SmartProvide feed (SEC 34): Coinbase against Kraken (a $0.10 grid).
4. End with what is robust (does not move across the grid) and what is a consequence of a choice, and repeat that
   shares are not comparable with the SEC's (RULES.md section 10).
