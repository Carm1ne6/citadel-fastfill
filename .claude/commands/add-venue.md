---
description: Add a public exchange feed (book + trades) to the recorder, the parsers, the checks and the tests
argument-hint: "<venue name, e.g. okx or binance>"
---
Add the venue `$ARGUMENTS` to the rebuild without changing its published defaults.

Follow CLAUDE.md, section "Add an exchange", and the skill's procedure (`.claude/skills/citadel-fastfill/SKILL.md`).

1. Read the venue's current public WebSocket documentation. Quote the lines that fix: the book channel (snapshot and
   updates, or a full top-N snapshot in every message, and any checksum), the trade channel and whether its side is
   the taker's or the maker's, the BTC/USD (or BTC/USDT) price grid. Use public market-data channels only: no keys,
   no private channels, nothing that places orders.
2. Add the URL and subscriptions to `record.py` `VENUES`, then record a probe and read the first messages:
   `citadel-fastfill record --venues $ARGUMENTS --minutes 1 --probe --out data/probe_$ARGUMENTS`
3. Write the `Venue` subclass in `citadel/venues.py` (the parser contract is in CLAUDE.md), register it in
   `VENUE_CLASSES`, add a two-letter code to `citadel/report.py` `SHORT` and the name to `check_venues.py` `VENUES`.
4. Check it: `citadel-fastfill check-venues data/probe_$ARGUMENTS`. The book must never cross; the taker side must
   agree with the mid of the book before each trade for nearly every print. Fix the parser until both hold.
5. Cut fixtures from the probe into `tests/fixtures/$ARGUMENTS.jsonl.gz` (the pattern of `tests/make_fixtures.py`), add
   a test class to `tests/test_feeds.py`, and a NOTES.md section with the documentation quotes and the check numbers.
6. Run `python -X utf8 -m pytest tests -q`.
7. Show how to use it without changing the defaults:
   `citadel-fastfill replay --data data/<run> --set venues=coinbase,kraken,bitstamp,gemini,$ARGUMENTS --set fastfill_feeds=coinbase,kraken,bitstamp,gemini,$ARGUMENTS`
   and say that adding it to `record.VENUES` also adds it to the default `--venues` of `record` and `live`.
