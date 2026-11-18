# Run the sensitivity and summarize

Runs the SIP latency, round-lot and toxicity-threshold grids (plus trade-out delay and SmartProvide feed) on a
recording and separates what the data says from what our choices say.

```text
Run the sensitivity analysis of this rebuild and summarize it. Read CLAUDE.md and RULES.md sections 8-10 first.

1. Use the newest recording in data/ (a folder with coinbase.jsonl.gz and the other venues). If there is none, record
   one: citadel-fastfill record --minutes 15 --out data/run1.
2. Run the built-in grids:
   citadel-fastfill sensitivity --data data/<run>
   citadel-fastfill sensitivity --data data/<run> --json
3. Run finer grids around the headline:
   citadel-fastfill sensitivity --data data/<run> --latency-grid 1,2,5,10,20,50,100 --round-lot-grid 0.005,0.01,0.0155,0.03,0.05,0.1,0.2 --threshold-grid 0.5,0.6,0.65,0.7,0.75,0.8,0.9 --json
4. For each grid, give a small table and two sentences:
   - SIP latency (SEC 22; fn 9: 4.04 / 5.42 ms in early 2010): how the FastFill share, the odd-lot share and the
     executable gap move, under the sip and the latency preset. Rows under 1 ms are below the ~1 ms clock of a
     Windows recorder.
   - Round lot (SEC 5: 100 shares; 0.0155 BTC = 100 shares at the order's $10.00 example): FastFill share, odd-lot
     share, triggers on sub-share levels.
   - Toxicity threshold (OURS): orders without a principal interest and the trade-out share of FastFill BTC on each
     half (the second half is out of sample), against SEC 31's "less than 6.9%".
   - Trade-out delay (OURS) against fn 13's "approximately 55%", and the SmartProvide feed (SEC 34).
5. Conclude: which results hold across every grid, which depend on one of our choices (name the parameter), and which
   default you would question and why. Do not change any default; if you suggest one, show the config file that
   would test it (copy fastfill.example.toml) and the command.

Never present a share as comparable with the SEC's (RULES.md section 10). Never call the code Citadel's.
```
