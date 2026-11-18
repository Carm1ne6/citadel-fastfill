# Instructions for AI coding agents

Claude Code reads this file as CLAUDE.md; AGENTS.md is the same text for other agents. Keep the two identical
(`tests/test_cli.py` checks it).

## What this repository is

FastFill and SmartProvide: the two algorithmic strategies (the SEC's own words, paras 8-9) that the SEC described in
its order of January 13, 2017 against Citadel Securities LLC (Release No. 33-10280), rebuilt rule by rule, plus the
toxicity analyzer of Citadel Investment Group's patent US 7,587,347 for the principal-interest step the order does not
describe (our choice).

- It is a rebuild from public documents, not Citadel's code. The code of FastFill and SmartProvide is not public.
- The conduct ran from late 2007 to January 2010. The order (2017) ordered Citadel Securities to pay $22,665,268
  ($5,200,000 disgorgement + $1,465,268 prejudgment interest + $16,000,000 civil penalty, Section V, item C), neither
  admitting nor denying the findings.
- It runs on public BTC/USD order books from Coinbase, Kraken, Bitstamp and Gemini. It is a simulation: it reads
  public market data and never sends an order anywhere.
- Every behaviour cites a paragraph of the SEC order or a passage of the patent in `RULES.md`. Everything the
  documents do not fix is a named parameter in `citadel/params.py` marked **OURS**.

## Install and run (3 commands)

```bash
pip install -e ".[test]"                              # Python 3.10+: websockets, numpy and the `citadel-fastfill` CLI
citadel-fastfill record --minutes 5 --out data/run1   # 5 minutes of public books and trades from the four venues
citadel-fastfill replay --data data/run1              # the report, the SEC's numbers next to ours; writes out/run1/
```

No recording yet? `citadel-fastfill live --seconds 180` runs the same engine on the live feeds (no order is handled in
the first 60 s, the warm-up). Run every command from the repository folder (paths are relative to it). No API keys:
every feed is public market data.

## Folder map

| path | what it is |
|---|---|
| `citadel/` | the engine: `venues.py` (parsers, books), `sip.py` (the slow view), `depth.py` (the direct view), `ces.py` (FastFill, SmartProvide, trade outs: SEC paras 26-39), `toxicity.py` (the patent's toxicity and profit analyzers), `sim.py` (one session for replay and live), `pipeline.py` (run, grids, toxicity test), `report.py`, `params.py` (every number and its source) |
| `citadel/cli.py`, `config.py`, `explain.py` | the `citadel-fastfill` command, the `--config` file loader, the event explainer |
| `run.py`, `live.py`, `record.py`, `export_film.py`, `check_venues.py`, `check_film.py` | the scripts behind `replay`, `live`, `record`, `export-film`, `check-venues`, `check-film` (each also runs as `python -X utf8 <script>.py`) |
| `fastfill.example.toml` | every parameter with its default, meaning and source: copy it to make a config |
| `tools/markout_my_fills.py` | markouts of the reader's own fills against recorded books (`citadel-fastfill markout`) |
| `tests/` | the SEC's examples, the patent's steps, no look-ahead, parsers on real messages (`tests/fixtures/`), the CLI |
| `RULES.md` | every rule and parameter with its SEC paragraph or patent passage, quoted; our assumptions; results |
| `NOTES.md` | the venues' conventions, quoted from their documentation and checked on the recordings |
| `SPEC.md` | the original specification of the rebuild |
| `docs/` | the SEC order (PDF and text), the patents (USPTO scans), `SOURCES.md`, the README figures (`docs/img/`, sources in `docs/img/src/`) |
| `prompts/` | copy-ready prompts for Claude Code |
| `.claude/skills/citadel-fastfill/`, `.claude/commands/` | the Claude Code skill and slash commands |
| `data/`, `out/` | local recordings and run outputs (not in git) |

## Rules for editing this repository

Each rule names the place in `RULES.md` it comes from.

1. **Never present the code as Citadel's.** It is a rebuild from the SEC order and the patent; the code of FastFill
   and SmartProvide is not public (RULES.md, opening paragraph). The assignee named on the patent is Citadel
   Investment Group, L.L.C. (now Citadel Enterprise Americas LLC / Citadel Enterprise IP Holdings LP, per Google
   Patents), not the respondent Citadel Securities LLC (RULES.md section 4, row "Principal interest").
2. **Every behaviour has a source.** A change to what the engine does cites the SEC paragraph or footnote, or the
   patent's column:line, with a short quote, in the code comment and in its RULES.md row (RULES.md, opening paragraph
   and the tables of sections 1-6).
3. **Mark our assumptions OURS.** Whatever the documents do not fix is a named parameter in `citadel/params.py`, with
   an entry in `params.SOURCE` that says OURS, a row in RULES.md section 9 and a line in section 8 (RULES.md sections 8
   and 9; `tests/test_engine_rules.py` checks every parameter has both).
4. **The principal-interest link is ours.** The SEC order does not say how CES decided principal interest; using the
   patent's toxicity analyzer is our choice, and every place that uses it says so (RULES.md section 4, row "Principal
   interest"; section 5, row "Responding").
5. **No look-ahead.** A decision at time t uses only messages received by t, and the SIP only what it has published
   (RULES.md section 1, row "The SIP is late"; section 5, row "Only earlier orders count"; `tests/test_no_lookahead.py`).
6. **One engine for replay and live.** Change `citadel/sim.py` and `citadel/ces.py`, never logic inside `live.py`: live
   prints exactly the replay's lines (RULES.md section 3, row "Replay order"; section 11).
7. **The SEC's numbers are quotes.** `params.SEC_NUMBERS` and RULES.md section 7 change only if the order says
   otherwise. Never present our shares as comparable with the SEC's (RULES.md section 10).
8. **Read only, public data.** Never add code that sends orders, signs requests or needs API keys; `record.py` and
   `live.py` send only subscribe messages to public market-data channels (RULES.md, opening paragraph: "no orders
   are sent anywhere").
9. **`docs/` is the record.** Do not edit the SEC order, its text or the patent scans; quote them (RULES.md, Sources).
10. **Defaults are the published results.** Experiment with `--config` or `--set`; change a default only with its
    source and the new numbers in RULES.md sections 8, 9 and 10.
11. **Run the tests** before you finish: `python -X utf8 -m pytest tests -q`. A new rule gets a test that names its
    paragraph (RULES.md section 11); report and event lines stay at most 96 characters, ASCII (section 11, the
    regression tests).

## Common tasks

### Replay a recording

```bash
citadel-fastfill replay --data data/run1                       # report + out/run1/events.jsonl, out/run1/summary.json
citadel-fastfill replay --data data/run1 --preset latency      # the latency-only view (SEC 22)
citadel-fastfill replay --data data/run1 --config my.toml --set tox_threshold=0.8
```

`events.jsonl` has one line per FastFill, trade out, SmartProvide display, fill and re-evaluation; `summary.json` has
the statistics, every sensitivity grid, the toxicity test, the profit analyzer and the report lines.

### Go live

```bash
citadel-fastfill live --seconds 180                            # every event after the 60 s warm-up, a summary every 10 s
citadel-fastfill live --rules FASTFILL,TRADE_OUT --venues coinbase,kraken
citadel-fastfill live --quiet --every 30                       # summaries only
```

### Change parameters

```bash
cp fastfill.example.toml my.toml                               # every parameter, default, meaning, source
citadel-fastfill params --config my.toml --changed             # what a run would use, and the sources
citadel-fastfill replay --data data/run1 --config my.toml
```

Precedence: defaults < `--config` file < `--preset` / `--latency-ms` / `--round-lot` / `--venues` < `--set KEY=VALUE`.

### Run the latency / round-lot / threshold sensitivity

```bash
citadel-fastfill sensitivity --data data/run1                  # reads out/run1/summary.json, replays if stale
citadel-fastfill sensitivity --data data/run1 --only latency,threshold --json
citadel-fastfill sensitivity --data data/run1 --latency-grid 1,5,20,100 --round-lot-grid 0.0155,0.05 --threshold-grid 0.6,0.7,0.8
```

Grids: SIP latency (SEC 22, fn 9) under both presets, round lot (SEC 5), toxicity threshold (OURS, bounded by SEC 31),
trade-out delay (OURS, fn 13) and SmartProvide feed (SEC 34). `*` marks the headline row.

### Explain an event

```bash
citadel-fastfill explain --run out/run1 --list --rule TRADE_OUT    # pick an order id
citadel-fastfill explain --run out/run1 --id 380                   # every event of order 380, every step quoted
citadel-fastfill explain --run out/run1 --rule SMARTPROVIDE --nth 2 --short
```

Each step prints the event's numbers, the RULES.md row it rests on, that row's quotes from the SEC order or the
patent, and the code. `--json` gives the same for a program.

### Compare with the SEC's numbers

```bash
citadel-fastfill sec-check --run out/run1
```

The SEC order's numbers (paras 18, 28, 29, 31, 33, 37, 39, fn 13, V.C) next to the run's, with the reason each share
is not comparable (RULES.md section 10).

### Add an exchange

Files to change:

1. `record.py`, `VENUES`: the public WebSocket URL and the subscribe messages (market data only, no keys).
2. `citadel/venues.py`: a `Venue` subclass, registered in `VENUE_CLASSES`.
3. `citadel/report.py`, `SHORT`: a two-letter code for the event lines.
4. `check_venues.py`, `VENUES`: add the name so the venue checks run on it.
5. `NOTES.md`: a section quoting the venue's documentation (book messages, trade side, price grid) and the checks.
6. `tests/`: real messages cut into `tests/fixtures/<venue>.jsonl.gz` (as `tests/make_fixtures.py` does) and a class
   in `tests/test_feeds.py` (book never crossed, trade side is the taker's, checksum if the venue sends one).

The parser contract (`citadel/venues.py`):

- `name`: the venue's name; the recorder writes `data/<run>/<name>.jsonl.gz`.
- `is_trade(msg) -> bool`: True for a trade message. A trade message must never change the book (`sim.Session`
  reads it before applying it, to group the fills of one taker order).
- `on_msg(msg) -> (book_changed, trades)`: `msg` is one received JSON object. `trades` is a tuple of
  `(side, price, size, taker_key)`: `side` is the TAKER's side, `"BUY"` or `"SELL"` (the marketable order); `price` in
  USD and `size` in BTC as floats; `taker_key` is the same for every fill of one taker order (the taker's order id if
  the venue publishes one, else `(side, exchange timestamp)`).
- Books: `self.bids` / `self.asks` are `BookSide`s: `load(levels)` for a full snapshot, `set(price, size)` per change
  (size 0 removes the level), `truncate(n)` for a depth-limited feed. Set `self.valid = True` after the first full
  snapshot and ignore updates before it. If the venue sends a checksum, verify it and leave the book out while it
  fails (`Kraken.live`).
- Return `(False, ())` for heartbeats, subscription replies and past trades (a trade history is not an order).
- Reconnects are handled by the base class: a `connected` or `error` line clears the book until the next snapshot.

Then record, check and replay with the venue added explicitly:

```bash
citadel-fastfill record --venues okx --minutes 1 --probe --out data/probe_okx
citadel-fastfill check-venues data/probe_okx
citadel-fastfill replay --data data/run_okx --set venues=coinbase,kraken,bitstamp,gemini,okx --set fastfill_feeds=coinbase,kraken,bitstamp,gemini,okx
```

The replay default (`params.VENUES`) stays the four venues, so the published numbers do not move. Adding a venue to
`record.VENUES` also adds it to the default `--venues` of `record` and `live`. A USDT-quoted book differs from USD by
a slow premium, which the basis absorbs (RULES.md section 2). `toxicity.PARAMETERS` has one NBBO-source flag per
venue of the four; a new venue sets none of them unless you add one, which changes the model (RULES.md section 5).
`prompts/04_add_binance_okx.md` is a ready prompt for Binance and OKX.

### Markouts of your own fills

```bash
citadel-fastfill markout --fills my_fills.csv --data data/run1 --per-fill out/markouts.csv
```

The CSV needs `time` (ISO 8601 UTC or epoch), `side` (your side), `price`, `size` and optionally `venue`; record the
venue's books while you trade. Markouts at 1 / 5 / 30 s, split into the edge at the fill and the later move (our split
of the patent's running tab of profit or loss, RULES.md section 6).

### Check a recording, export the film data, run the tests

```bash
citadel-fastfill check-venues data/run1                        # books never crossed, Kraken checksums, taker sides
citadel-fastfill export-film --data data/run1 --out out/film_data.json && citadel-fastfill check-film out/film_data.json
python -X utf8 -m pytest tests -q                              # tests that need data/probe or data/run_dev skip without them
```

The recordings those tests read are local (record them: `citadel-fastfill record --minutes 0.5 --out data/probe`;
`--minutes 2.5 --out data/run_dev`). `export-film` writes the data file of a replay animation (candles, event lines,
markout fan, report); the animation itself is not part of this repository.
