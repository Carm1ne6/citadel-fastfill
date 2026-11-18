# Set up and replay in one go

Installs the project, checks it, records 10 minutes of public BTC/USD order books from Coinbase, Kraken, Bitstamp and
Gemini, replays them through FastFill and SmartProvide, and puts the result next to the SEC's numbers.

```text
Set up this repository and run it end to end. Read CLAUDE.md first and follow its rules.

1. Create a virtual environment in .venv (Python 3.10 or newer), activate it and run: pip install -e ".[test]".
   Check that `citadel-fastfill --help` works.
2. Run the tests: python -X utf8 -m pytest tests -q. Tell me how many passed and how many skipped (tests that need
   local recordings skip without them). Stop and show me any failure.
3. Record 10 minutes of public order books and trades:
   citadel-fastfill record --minutes 10 --out data/run1
   (run it in the background and wait; it prints message counts every minute). If a venue reports connection
   errors, tell me which.
4. Replay it: citadel-fastfill replay --data data/run1
5. Run citadel-fastfill sec-check --run out/run1 and citadel-fastfill check-venues data/run1.
6. Give me a short summary:
   - a table: metric, the SEC order's number with its paragraph, this run (FastFill share and odd-lot share, trade
     outs share and fill rate, SmartProvide share and fill rate, SmartProvide internalized after),
   - why the shares are not comparable (RULES.md section 10, in two sentences),
   - the toxicity test: out-of-sample AUC at 1 / 5 / 30 s and whether it beats its best single parameter,
   - the profit analyzer's running tab, split into the spread at the fill and the later price,
   - the venue checks (books never crossed, Kraken checksums, taker sides).
7. Pick one FastFill with a trade out and explain it: citadel-fastfill explain --run out/run1 --id <order id>.
   Retell it in plain words with the exact quotes from the SEC order behind each step.

This is a rebuild from public documents and a simulation on public data: never call it Citadel's code, and never
send an order anywhere.
```
