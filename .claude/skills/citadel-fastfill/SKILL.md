---
name: citadel-fastfill
description: Run, explain, tune and extend the FastFill / SmartProvide rebuild (the two algorithmic strategies described in SEC order 33-10280 against Citadel Securities, plus, as our own choice for the principal-interest step, the toxicity analyzer of Citadel Investment Group's patent US 7,587,347) on public BTC/USD order books. Use it to set up the repo, record order books, replay a recording, go live, change parameters with a config file, run the latency / round-lot / threshold sensitivity, explain an event with its SEC paragraph and patent passage, compare a run with the SEC's numbers, add an exchange (Binance, OKX, ...), or measure markouts and adverse selection on the user's own fills. Triggers - "replay", "go live", "FastFill", "SmartProvide", "trade out", "SIP latency", "round lot", "toxicity threshold", "sensitivity", "explain this event", "which paragraph", "why did FastFill fire", "compare with the SEC", "add Binance", "add OKX", "markout", "adverse selection", "my fills", "citadel-fastfill".
---

# citadel-fastfill

The repository rebuilds FastFill and SmartProvide, the two algorithmic strategies (the SEC's own words, paras 8-9)
that the SEC described in its order of January 13, 2017 against Citadel Securities LLC (Release No. 33-10280), rule by
rule, plus the toxicity analyzer of Citadel Investment Group's patent US 7,587,347 for the principal-interest step the
order does not describe (our choice). It runs on public BTC/USD books from Coinbase, Kraken, Bitstamp and Gemini.

Before editing anything, read `CLAUDE.md` (the rules) and the `RULES.md` rows of the behaviour you touch. Always:

- Call it a rebuild from the SEC order and the patent, never Citadel's code.
- Mark every assumption the documents do not fix as OURS (a parameter in `citadel/params.py`, a row in RULES.md).
- Read public market data only; never add code that sends orders or needs keys.
- Say that the run's shares are not comparable with the SEC's (RULES.md section 10) whenever you show them together.
- Run `python -X utf8 -m pytest tests -q` after any code change.

## Set up

1. `pip install -e ".[test]"` from the repository folder (Python 3.10+). Check: `citadel-fastfill --help`.
2. If `data/` has no recording (a folder with `coinbase.jsonl.gz` and the other venues), record one:
   `citadel-fastfill record --minutes 5 --out data/run1` (public data; it prints message counts every minute).
3. `python -X utf8 -m pytest tests -q` should pass; tests that need `data/probe` or `data/run_dev` skip without them.

## Replay a recording

1. Pick the recording: the folder the user named, else the newest folder under `data/` with `*.jsonl.gz` files.
2. `citadel-fastfill replay --data data/<run>` (add `--config FILE`, `--preset latency`, `--set KEY=VALUE` if asked).
   It prints the report and writes `out/<run>/events.jsonl` and `out/<run>/summary.json`.
3. Summarize from the report: orders, FastFill share and odd-lot share, trade outs (share and fill rate), SmartProvide
   share and fill rate, the toxicity test (AUC out of sample, and whether it beats its best single parameter), the
   profit analyzer's tab. Put each next to the SEC's number with its paragraph, and say they are not comparable.

## Go live

1. `citadel-fastfill live --seconds 180` (no order in the first 60 s, the warm-up) (or `--rules FASTFILL,TRADE_OUT`,
   `--quiet --every 30`, `--venues ...`).
2. It connects to the four public feeds, prints each event line as it happens and a summary every 10 s. The
   toxicity analyzer screens nothing until `tox_min_train` outcomes are known (300 by default, a few minutes).
3. Ctrl+C or `--seconds` ends it with a final summary. Nothing is written to disk.

## Change parameters

1. `cp fastfill.example.toml my.toml`; every parameter there has its default, meaning and source.
2. Edit only what the user wants; check with `citadel-fastfill params --config my.toml --changed`.
3. Run with `--config my.toml`. The command line wins over the file (`--preset`, `--latency-ms`, `--round-lot`,
   `--venues`, then `--set`).
4. A default changes only with its source and new numbers in RULES.md sections 8-10; prefer a config file.

## Sensitivity (latency, round lot, threshold, trade-out delay, feed)

1. `citadel-fastfill sensitivity --data data/<run>`; it reuses `out/<run>/summary.json` when it was made with the same
   parameters and replays otherwise. Custom grids: `--latency-grid 1,5,20`, `--round-lot-grid 0.0155,0.05`,
   `--threshold-grid 0.6,0.7,0.8`, `--trade-out-grid 5,50,250`. `--json` for numbers.
2. Read each grid against its source: latency (SEC 22, fn 9: 4.04 / 5.42 ms in 2010; rows under 1 ms are below the
   ~1 ms clock of a Windows recorder), round lot (SEC 5; 0.0155 BTC = 100 shares at the order's $10.00 example),
   threshold (OURS; trade outs against SEC 31's 6.9%, first half and out-of-sample second half), trade-out delay (OURS;
   fill rate against fn 13's 55%), SmartProvide feed (SEC 34; Kraken's $0.10 grid makes its trigger a standing
   condition).
3. Summarize what moves with each knob and what does not; name the headline row (`*`).

## Explain an event

1. Find the event: `citadel-fastfill explain --run out/<run> --list --rule FASTFILL` (or TRADE_OUT, SMARTPROVIDE,
   SP_FILLED, SP_REEVAL) gives order ids and event lines.
2. `citadel-fastfill explain --run out/<run> --id <order>` prints every event of the order and, for each step, the
   event's numbers, the RULES.md row, its quotes from the SEC order or the patent, and the code. `--short` cites the
   paragraphs without quoting them; `--json` for a program.
3. When you retell it, keep the quotes exact and say which step is OURS (the principal-interest link, the 50 ms
   trade-out delay, the display venue, the size scale of the window, ...).

## Compare with the SEC's numbers

1. `citadel-fastfill sec-check --run out/<run>` prints each SEC number with its paragraph (18, 28, 29, 31, 33, 37,
   39, fn 13, V.C) next to the run's and a note; it also checks that the summary quotes `params.SEC_NUMBERS` unchanged.
2. Report the differences with RULES.md section 10's reasons: odd lots everywhere in crypto books, public taker orders
   are not retail orders, SmartProvide is an event on Coinbase and a standing condition on Kraken.

## Add an exchange

1. Read the venue's current WebSocket documentation for its public book and trade channels; quote the lines on the
   book messages, the trade side (taker or maker) and the price grid. Prefer a full-snapshot channel (every message is
   the top N levels) or one with a checksum.
2. Add the endpoint and subscriptions to `record.py` `VENUES`, then record a probe:
   `citadel-fastfill record --venues <name> --minutes 1 --probe --out data/probe_<name>` and read the printed messages.
3. Write the `Venue` subclass in `citadel/venues.py` following the parser contract in CLAUDE.md ("Add an exchange"),
   register it in `VENUE_CLASSES`, add a code to `report.SHORT` and the name to `check_venues.py` `VENUES`.
4. `citadel-fastfill check-venues data/probe_<name>`: the book must never cross and the trade side must agree with the
   mid before the trade (or with order ids). Fix the parser until it does.
5. Cut fixtures from the probe into `tests/fixtures/<name>.jsonl.gz` (the pattern of `tests/make_fixtures.py`) and add a
   test class in `tests/test_feeds.py`. Add a NOTES.md section with the quotes and the check numbers.
6. Record a longer run with all five venues and replay with them added explicitly:
   `citadel-fastfill replay --data data/<run> --set venues=coinbase,kraken,bitstamp,gemini,<name> --set fastfill_feeds=coinbase,kraken,bitstamp,gemini,<name>`.
   Keep `params.VENUES` (the replay default) as it is unless the user decides to change the headline.

## Markouts of the user's own fills

1. Ask for a CSV of their fills with `time` (UTC, ISO 8601 or epoch), `side` (their side), `price`, `size` and
   optionally `venue`, and a recording of the same venues covering the same time (record while they trade).
2. `citadel-fastfill markout --fills fills.csv --data data/<run> --per-fill out/markouts.csv` (`--json` for numbers,
   `--shift-ms` if their timestamps are on another clock).
3. Read it as our markout version of the patent's profit-analyzer tab: the edge at the fill plus the later move; a fill
   that gives no profit or a loss at 5 s (our horizon) is what the patent calls toxic from the liquidity provider's
   side ("no profit, reduced profit and/or a loss", col. 4:1-3). Many losing maker fills mean adverse selection.
   This is a measurement of their own trades, not advice.
