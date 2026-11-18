# Add Binance and OKX

Adds two more public order-book feeds as venues five and six: recorder, parsers, checks, tests and notes, without
changing the published defaults.

```text
Add Binance and OKX as venues of this rebuild. Read CLAUDE.md first, especially "Add an exchange" and its parser
contract, and follow it exactly. Public market-data channels only: no API keys, no private channels, nothing that
places orders.

For each venue:
1. Read its current public WebSocket documentation and quote, in a new NOTES.md section like the existing four: the
   endpoint, the book channel and how it updates, the trade channel and whether its side field is the taker's or the
   maker's, and the price grid of the pair. Starting points to verify, not to trust:
   - Binance spot, wss://stream.binance.com:9443/ws with a SUBSCRIBE message for "btcusdt@depth20@100ms" (every
     message is the top 20 levels, so each one replaces the book, like Bitstamp) and "btcusdt@trade" (field "m" is
     true when the buyer is the market maker, so the taker sold). Binance.com is not available from some countries;
     Binance.US (wss://stream.binance.us:9443) has its own symbols.
   - OKX, wss://ws.okx.com:8443/ws/v5/public with {"op": "subscribe", "args": [...]} for the "books5" channel (top 5
     levels per message) or "books" (snapshot plus updates with a CRC32 checksum), and "trades" (side = the taker's
     side), instId "BTC-USDT".
2. Add the endpoint and subscriptions to record.py VENUES and record a probe:
   citadel-fastfill record --venues <name> --minutes 1 --probe --out data/probe_<name>
   Read the printed messages and compare them with the documentation.
3. Write the Venue subclass in citadel/venues.py per the contract (taker side, taker_key, valid only after a full
   book, past trades ignored, checksum checked if you use a checksummed channel), register it in VENUE_CLASSES, add a
   two-letter code to citadel/report.py SHORT and the name to check_venues.py VENUES.
4. Check it until it is clean: citadel-fastfill check-venues data/probe_<name>. The book must never cross, and the
   taker side must agree with the mid of the book before the trade for nearly every print.
5. Cut fixtures from the probe into tests/fixtures/<name>.jsonl.gz (the pattern of tests/make_fixtures.py) and add a
   test class to tests/test_feeds.py like the existing four.

Then:
6. Run python -X utf8 -m pytest tests -q; everything must pass.
7. Record 10 minutes with all six venues (citadel-fastfill record --minutes 10 --out data/run6) and replay it twice:
   citadel-fastfill replay --data data/run6
   citadel-fastfill replay --data data/run6 --out out/run6_six --set venues=coinbase,kraken,bitstamp,gemini,binance,okx --set fastfill_feeds=coinbase,kraken,bitstamp,gemini,binance,okx
   Compare the two reports: FastFill share, odd-lot share, executable gap, trade outs, SmartProvide. Note the basis of
   the USDT books against Coinbase's USD book (RULES.md section 2: the basis absorbs a slow premium).
8. Do not change params.VENUES or any other default. Tell me that adding the venues to record.VENUES also adds them
   to the default --venues of record and live, and that toxicity.PARAMETERS has NBBO-source flags for the four
   original venues only.
```
