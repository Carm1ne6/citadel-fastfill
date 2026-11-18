# Venue notes: the conventions the parsers rely on, and how each was checked

The rebuild reads four public BTC/USD WebSocket feeds (endpoints and subscriptions in `record.py`; `live.py` uses the
same). Each convention below is quoted from the venue's documentation (fetched 2026-11-18) and then checked on our own
recordings with `check_venues.py` (books never crossed, Kraken checksums, taker sides) and the parser tests in
`tests/test_feeds.py`, which run on real messages cut from `data/probe` (`tests/fixtures/`, made by
`tests/make_fixtures.py`). Numbers below are from `python -X utf8 check_venues.py data/run_final` (20 minutes,
2026-11-18 03:14-03:34 UTC) unless stated.

| venue | endpoint | channels | book | trade side in the message |
|---|---|---|---|---|
| Coinbase Exchange | `wss://ws-feed.exchange.coinbase.com` | `level2_batch`, `matches`, `heartbeat` (BTC-USD) | full snapshot, then `l2update` changes | maker side (taker = opposite) |
| Kraken (WS v2) | `wss://ws.kraken.com/v2` | `book` depth 25, `trade` (BTC/USD) | snapshot + updates, CRC32 checksum | taker side |
| Bitstamp (WS v2) | `wss://ws.bitstamp.net` | `order_book_btcusd`, `live_trades_btcusd` | every message is the whole top 100 | taker side (`type` 0 buy, 1 sell) |
| Gemini (market data v2) | `wss://api.gemini.com/v2/marketdata` | `l2` (BTCUSD) | first `l2_updates` = full book, then changes | taker side |

## Coinbase Exchange

Docs: https://docs.cdp.coinbase.com/exchange/websocket-feed/channels

- Book. "The properties `bids` and `asks` are arrays of `[price, size]` tuples and represent the entire order book."
  "The `changes` property of `l2update`s is an array with `[side, price, size]` tuples." "The `size` property is the
  updated size at the price level, not a delta." "A size of `"0"` indicates the price level can be removed."
  Code: `Coinbase.on_msg` loads the snapshot, then sets each change (size 0 deletes).
- Batching. "The `level2_batch` channel sends batches of `level2` messages every 50 milliseconds (0.05 seconds)."
  "The `level2_50` channel was renamed `level2_batch` but you can use either when subscribing." Seen live on
  2026-11-18: the subscription is confirmed as `level2_50`; the snapshot carried the whole book (13,780 bids,
  24,407 asks). Measured on run_final, the 19,273 `l2update` batches arrive 50.8 / 59.9 / 62.0 ms apart (p10 / p50 /
  p90), not every 50 ms. So a trade out is read on the first book message of its venue received at or after the
  50 ms delay (RULES.md section 4); on run_final that read comes 58 / 74 / 104 ms after sending (p10 / p50 / p90).
- Trade side. "The `side` field indicates the maker order side." "If the side is `sell` this indicates the maker was
  a sell order and the `match` is considered an up-tick. A `buy` side match is a down-tick." Code: the marketable
  order (the taker) is the opposite side. Checked: taker buys print at or above the mid of the book before them
  99.6% of the time, taker sells at or below it 98.8% (14,292 prints). The book comes in batches, so the
  book before a print can already include it.
- Gotchas. The first message of `matches` is a `last_match` (a past trade): not an order, ignored. "Messages can be
  dropped from this channel." (matches); a dropped print is a missing order, not a wrong one.
- Price grid: $0.01 (every last-cent digit occurs in the books).

## Kraken (WebSocket v2)

Docs: https://docs.kraken.com/api/docs/websocket-v2/book, https://docs.kraken.com/api/docs/guides/spot-ws-book-v2,
https://docs.kraken.com/api/docs/websocket-v2/trade

- Book. Depth "One of: 10, 25, 100, 500, 1000" (we subscribe with 25; the snapshot seen live had 25 levels a
  side). The guide: "An update with `"qty": 0` means that price level should be removed." "After each update,
  truncate your book to the subscribed depth" and "you will not receive `"qty": 0` for levels that fall out of
  scope." Code: `Kraken.on_msg` applies the update, then truncates to 25 levels.
- Checksum. "The checksum is always calculated over the top 10 price levels regardless of subscription depth." For
  the top 10 asks (low to high) and then the top 10 bids (high to low): remove the `.` from price and qty, remove
  leading zeros, concatenate, then "pass the result to a CRC32 function" and compare as an unsigned 32-bit integer
  with the message's `checksum`. Code: `kraken_checksum` (BTC/USD: price with 1 decimal, qty with 8). Checked:
  245,345 of 245,345 book messages of run_final match; a book that fails is left out of both views until it matches
  again (`Kraken.live`).
- Trade side. `side`: "The side of the taker order." The trade channel's `snapshot` "reflects the most recent 50
  trades" (history): ignored, only `update` messages are orders. Checked: 100% of 1,800 prints agree with the mid.
- Price grid: $0.10 (every price in the books is on the 0.1 grid; the checksum format with 1 decimal matches).

## Bitstamp (WebSocket v2)

Docs: https://www.bitstamp.net/websocket/v2/ (official; on 2026-11-18 it is served behind a bot check and could not be
fetched by script or from the Internet Archive). The same field is documented in Bitstamp's REST specification,
https://www.bitstamp.net/api/: trade `type` "0 (buy) or 1 (sell)."

- Book. Each `order_book_btcusd` message is the whole top of the book: seen live on 2026-11-18, 100 bids and
  100 asks per message, with `timestamp` and `microtimestamp`. The recorder keeps the top 20 to save disk
  (`record.py`, `BITSTAMP_KEEP`). Code: `Bitstamp.on_msg` replaces the book with each message.
- Trade side. `type` 0 = buy, 1 = sell, the side of the taker. Checked two ways: (1) the taker is the order that
  arrived last, and Bitstamp's order ids grow with time, so `type` 0 should come exactly when
  `buy_order_id > sell_order_id`: 235 of 235 prints of run_final agree (`check_venues.py`, test
  `test_trade_type_is_the_side_of_the_newer_order`); (2) against the mid of the book before the print: buys 94.3%,
  sells 100%. Each of the 9 misses comes after a book message stamped 3 to 30 ms after the trade
  (`microtimestamp`): the book channel was ahead of the trade channel, and that book no longer shows the level the
  taker hit.
- Price grid: $0.01.

## Gemini (market data v2)

Docs: https://developer.gemini.com/websocket/archived/v2 (Gemini now lists this API as archived; the endpoint served
every recording of 2026-11-18).

- Subscription: `{"type": "subscribe", "subscriptions": [{"name": "l2", "symbols": ["BTCUSD"]}]}`.
- Book. "The initial response will show the existing state of the order books and last 50 trades." `l2_updates`
  carries `changes`, an "array of arrays" `[side, price, quantity]`. The page does not say what quantity 0 means;
  we read quantity as the new size at that price, 0 removing the level (the book never crossed in 73,458 book
  states of run_final). Seen live on 2026-11-18: the first `l2_updates` held 4,185 levels and a `trades` array of
  100 past trades (the page says 50); those past trades are history and are ignored.
- Trade side. Trade `side`: "Taker side: `buy` or `sell`". Checked: 100% of 214 prints agree with the mid.
- Price grid: $0.01.

## Our recordings

- Files: one gzip JSONL per venue, one line per received message `{"t": wall clock ns, "m": monotonic ns, "raw":
  message}`, plus `{"event": "connected"}` / `{"event": "error"}` lines. A killed recorder leaves no gzip trailer and
  may end in half a line: `iter_records` stops cleanly there (test `test_truncated_gzip_stops_cleanly`).
- Clocks on the Windows machine that made the recordings: `t` is stored in 100 ns units but advances in steps of about 1 ms: the 367,699
  messages of run_final share 123,936 distinct `t` values, no two closer than 0.30 ms, and 0.2% of the steps are
  under 0.5 ms. `m` moves in steps of 15 to 16 ms, too coarse for a 5 ms SIP delay. The replay orders messages by `t`,
  checked against `m` for wall-clock steps (`citadel/sim.py`); messages with the same `t` keep the venue order
  coinbase, kraken, bitstamp, gemini. The 0.5 ms SIP sensitivity row is below the clock's resolution.
- Taker orders. A marketable order is one public taker order, its fills on one venue grouped (`citadel/sim.py`). The
  taker of a fill: Coinbase `taker_order_id`; Bitstamp the newer (larger) of `buy_order_id` and `sell_order_id`
  (the taker arrived last; the same rule that checks `type`, above); Kraken and Gemini publish no order ids, so the
  fills of one sweep are those with the same side and exchange timestamp. On run_final the 16,541 fills (Coinbase
  14,292, Kraken 1,800, Bitstamp 235, Gemini 214) are 7,723 taker orders; 2,750 of them have more than one fill.
  The fills of one taker arrive back to back: Kraken's in one message, Coinbase's within 1.8 ms (p99), Bitstamp's and
  Gemini's within 20 ms. A group ends when the next message received, from any venue, is not one of its fills; 41
  multi-fill takers (28 Coinbase, 10 Bitstamp, 3 Gemini) were interrupted by another message and count as two or
  more orders (47 extra orders). After the 0.0005 BTC floor and the 60 s warm-up, 2,267 orders remain (they carry
  9,231 fills).
- Reconnects: a `connected` or `error` line clears that venue's book until its next snapshot; a venue without a valid
  book is left out of both views. run_final has one connection per venue and no reconnect.
- Basis between venues over run_final (EMA, end of run): Kraken -$1.74, Bitstamp -$1.65, Gemini +$15.22 against
  Coinbase (about -0.3, -0.3 and +2.4 bps at $64,300). RULES.md section 2 explains how the replay uses it.

## Results on run_final

### Behaviours the regression tests pin (`tests/test_regressions.py`)

- **Crossed-quote clip.** Another venue's bid must stay below Coinbase's ask and its ask above Coinbase's bid (the
  reference venue's OPPOSITE quote), so another venue can tie or improve Coinbase's own side and set the best price
  (test `CrossedQuoteClip`, RULES.md section 2).
- **Taker orders.** One marketable order per public taker order, not per fill (section "Our recordings"); the dust
  floor applies to the grouped size (test `TakerOrders`).
- **Trade-out reading.** A trade out is read on the first book message of its venue received from 50 ms after
  sending, not on the book seen at exactly 50 ms, which is most often still the book at sending; prices are compared
  in the venue's own prices fixed at sending. The same holds for SmartProvide fills (tests
  `TradeOutReadOnTheVenuesNextBook`, `RawVenuePrices`).
- **Warm-up and the end of the data.** No order in the first 60 s (one basis time constant); a display window or
  trade out still open at the end of the data is decided at the last message, so no event lies after it. The film's
  fan says how many FastFills it leaves out (`n_fastfill`, `n_excluded_end`); `orders_t` gives every order's receive
  time (tests `WarmUp`, `DataEnd`, `ReportOnProbe`).
- **Clock.** `t` advances in steps of about 1 ms, not 100 ns (section "Our recordings").
- **Toxicity baselines.** Each parameter alone as a score, out of sample, next to the quotient; the report says where
  the model fails (30 s) (test `ToxicityBaselines`).
- **SmartProvide reads Coinbase (SPEC).** Kraken is a sensitivity row; both feeds under both presets are printed.
- **Threshold 0.7.** With the threshold grid 0.5-0.9 and the trade-out share on each half.
- **Both presets side by side**, and the shares labelled not comparable (test `ReportOnProbe`).
- **The patent link is marked ours** wherever the patent decides principal interest (test `Attribution`).
- **Smaller ones.** The round-lot comment cites the order's own $10.00 example, with 0.0155 BTC as a sensitivity row;
  a sub-share level floor was tried and left off (RULES.md section 2); every sensitivity engine shares the headline's
  market settings (test `SharedMarket`); the display rounding, the window direction, the two fn 13 readings and the
  single patent response are marked ours; SEC 30 and SEC 38 get a number each (tests `DisplayGrid`,
  `SecObservations`).

### Final numbers (run_final, `python -X utf8 run.py --data data/run_final`)

| | SEC order | this run (`sip`, headline) | `latency` preset |
|---|---|---|---|
| orders | | 2,267 taker orders | same |
| FastFill share of orders | 0.4% of all CES flow (not comparable) | 56.2% (1,273) | 1.1% (25) |
| FastFill triggers caused by an odd lot | about 20% | 96.0% (25.8% on sub-share levels only) | 48.0% |
| executable gap / top-of-book gap | not disclosed | $12.66 / $61.67 | $0.03 / $0.05 |
| trade outs, share of FastFill BTC | less than 6.9% | 1.1% (halves 1.4% / 0.7%) | 10.1% |
| trade outs filled in full or part | about 55% | 71.8% | 90.0% |
| SmartProvide orders | 690,000 | 40 (1.8%; 5.5% of no-trigger orders) | 91 (4.0%) |
| SmartProvide BTC filled in the market | about 18% | 0.3% | 0.2% |
| internalized after SmartProvide | 490,000 of 690,000 (about 71%) | 80.0% | 51.6% |
| of those, worse than immediate | "some" | 87.5% | |
| toxicity AUC out of sample (1 / 5 / 30 s) | | 0.769 / 0.693 / 0.459 | |

### The parameter choices, and why

- `preset` = `sip`: the SEC's SIP is round lots only (SEC 5, 19, 23) and SEC 29 counts odd-lot triggers. SPEC named
  `latency`; it is printed next to the headline, so the reader sees both.
- `smartprovide_feeds` = `sp_post_venue` = coinbase: SPEC's choice. Kraken's $0.10 grid keeps its best price behind
  the SIP on nearly every order, which turns the trigger into a standing condition.
- `tox_threshold` = 0.7: col. 9:1-5 expects a level of toxic trades from random arrival (half of all fills lose at
  5 s here), and SEC 31 calls the no-principal-interest case narrow; trade outs stay under SEC 31's 6.9% on both
  halves at every threshold of the grid, so the bound does not pick the value; 0.7 flags only orders the analyzer
  expects to lose at least 7 times in 10.
- `round_lot_btc` = 0.05: ours; no document fixes a share price. The $10.00 example gives 0.0155 BTC (FastFill
  43.7%, odd lot 95.9%).
- `min_level_btc` = 0: a one-share floor widens Coinbase's spread and lets the other venues cross inside it.
- `warmup_s` = 60: one basis time constant.

### Open limits

- No share is comparable with the SEC's; the odd-lot share (96%) shows how much crypto books differ from US books.
- SmartProvide fills almost never (0.3% against 18%); 40 orders are too few for its percentages to be stable.
- 41 multi-fill takers interrupted by another message count as two or more orders (47 extra).
- 1 SIP second and 2 direct seconds of 1,199 stay crossed or locked (two non-reference venues inside a wide Coinbase
  spread).
- The trade-out success (71.8%) depends on the 50 ms delay we chose; fn 13's 55% is below all three rows (74.7% at
  5 ms, 71.8% at 50 ms, 57.8% at 250 ms).
- The toxicity model adds little over its best single parameter and fails at 30 s.
