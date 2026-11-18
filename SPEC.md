# FastFill / SmartProvide rebuild: spec from the SEC order

Source: SEC, In the Matter of Citadel Securities LLC, Securities Act Release No. 10280, Jan 13, 2017
(https://www.sec.gov/litigation/admin/2017/33-10280.pdf; text in docs/sec_order_2017_33-10280.txt).
The code of FastFill and SmartProvide is not public; this is a rebuild of the rules the order describes, run on public
crypto feeds.

This is the specification written before the build. Where it differs from the code (the headline preset is `sip`, not
`latency`; an order is one public taker order of at least 0.0005 BTC with its fills grouped, not one print; the replay
clock is the wall clock `t`, not `m`; at window end the rest of a SmartProvide order is re-evaluated as in para 39; the
SIP latency grid runs from 0.5 to 1,000 ms; there is no price-improvement grid), RULES.md is the record and gives the
reason.

## What the order says (paragraph numbers from the order)
- CES = Citadel Execution Services, Citadel Securities' wholesale market maker for retail orders (para 3, 17).
  About 35% of the average daily volume of US retail equity shares at the time of the order, January 2017 (para 17).
- Two views of the market (para 4-7, 19-25):
  - SIP feeds: consolidated best round-lot quotes from every exchange -> the NBBO. Slower (consolidation + distance;
    footnote 9: average SIP consolidation latency at the start of 2010 was 4.04 ms NYSE, 5.42 ms Nasdaq). No odd lots.
  - Depth-of-book direct feeds: every displayed order, odd lots included, received sooner.
  - Reasons they differ: latency (22), odd lots (23), transmission issues (24), referencing fewer than all exchanges (25).
- CES internalized at the SIP NBB/NBO or better, often with a small price improvement (para 8, 27; fn 11: NBO $10.00 ->
  fill at $9.999).
- FastFill (para 10-11, 28-32): triggered when the best price from the depth-of-book feeds was BETTER than the SIP
  price for the order. It immediately internalized the marketable order at the SIP NBB/NBO (or better). Example: buy
  order, SIP offer $10.01, direct feeds $10.00 -> filled at $10.01. Price improvement "often was not sufficient to equal
  the price difference that had triggered the strategy" (30). About 2.7 million retail orders, June 2008 - Jan 2010,
  about 0.4% of CES order flow (28).
- SmartProvide (para 12-13, 33-34): triggered when the SIP NBB/NBO was BETTER than the best price on at least one
  depth-of-book feed. It did not fill at the SIP price; it routed a non-marketable order displayed for up to 1 to 5
  seconds depending on size (example: buy, SIP offer $10.01, direct $10.02 -> buy displayed at $10.00). About 18% of
  shares got the better price; the rest was re-handled (internalized or routed), some at a worse price than without
  SmartProvide. About 690,000 marketable orders, about 490,000 internalized afterwards (33).
- Both: 2.6% of retail orders through CES's algorithmic engine, 0.6% of overall flow (18). Discontinued (para 3: "CES
  has since discontinued these two algorithms"; fn 3 defines a marketable limit order).
  Ordered to pay: disgorgement $5,200,000 + prejudgment interest $1,465,268 + civil money penalty $16,000,000 =
  $22,665,268; censured (Section V, items B-C; the violation is in Section IV, paras 41-42).

## Rebuild on public crypto data (BTC/USD)
- Venues (same instrument, USD-quoted, public WebSockets): Coinbase, Kraken, Bitstamp, Gemini.
- Fast view ("direct feeds"): consolidated best bid/offer across all four books as received, ANY size.
- Slow view ("SIP NBBO"): consolidated best bid/offer using only levels of at least ROUND_LOT (default 0.05 BTC, OURS:
  no document fixes a share price; 100 shares at the order's $10.00 example are about 0.0155 BTC), published with
  CONSOLIDATION_DELAY (default 5 ms, between fn 9's 4.04 and 5.42 ms averages of early 2010). Both are parameters;
  results are reported for the defaults and a sensitivity grid.
- Incoming orders: every public trade print on the four venues is treated as a marketable order of that side and size
  arriving at the receive time of the print.
- FastFill rule: buy if fast_ask < slow_ask (sell if fast_bid > slow_bid) -> fill at the slow price minus PRICE_IMPROVEMENT
  (default 0); record gap = |slow - fast| per BTC and in bps, and gap x size in USD.
- SmartProvide rule: buy if slow_ask < fast_ask (the slow view still shows a price that is gone) -> post a buy at
  slow_ask - one tick, displayed 1 s (size <= 0.1 BTC) up to 5 s (size >= 1 BTC); filled if a sell print at or below that
  price arrives inside the window; otherwise fill at the fast ask at window end and compare with the slow ask at arrival.
- Output: event log (JSONL), per-rule counts, share of orders, gap distribution (bps, USD), SmartProvide fill rate vs the
  SEC's 18%, and the same stats per venue and per hour.
- Labels: everything is a simulation of a wholesaler on public data. No orders are sent anywhere.

## Maximum fidelity: as close to the documents as the data allows
Every behaviour below is taken from the order; anything the order does not fix is a named parameter, documented as ours.
- Para 26: CES decides per order whether it has a "principal interest" (internalize) or works it riskless principal in
  the market, using depth-of-book information; the order does not say how. OURS: we decide principal interest with the
  toxicity analyzer of Citadel Investment Group's patent US 7,587,347 B2 (Griffin, Andresen); neither document links
  the two. See "Toxicity analyzer" below.
- Para 27, fn 11: internalize at the SIP NBB/NBO or better, with a small price improvement (fn 11: NBO $10.00 -> $9.999,
  i.e. 1/10 of a tick); sometimes for more size than the NBBO shows. PRICE_IMPROVEMENT is a parameter (default 1/10 tick
  = $0.001 is not quotable in BTC; default 0, sensitivity reported).
- Para 29: FastFill trigger = best bid/offer on one or more depth-of-book feeds better than the SIP. Record whether the
  trigger came from an odd lot (SEC: about 20% of FastFill internalizations were odd-lot triggered).
- Para 30: the trigger is checked when handling starts AND while another strategy (SmartProvide) is handling the order;
  if it arises, FastFill immediately internalizes the whole remaining quantity at the SIP price.
- Para 31, fn 13: "trade out attempt": when FastFill internalizes without principal interest (or above a position
  limit), it sends a proprietary order for itself at the better depth-of-book price, size <= 90% of the displayed best
  quantity. SEC: under 6.9% of FastFill shares; filled in full or part about 55% of the time, no fill about 45%.
  Simulate success as "the level was still there after TRADE_OUT_LATENCY_MS" and report our rate next to 55%.
- Para 32: FastFill fills the ENTIRE remaining quantity at the SIP price regardless of displayed sizes. Report the SEC's
  split: larger orders got a better overall price than the displayed liquidity, smaller orders fared worse.
- Para 34-35: SmartProvide references only ONE depth-of-book feed for many securities (default: one venue,
  SMARTPROVIDE_FEEDS=coinbase) and can trigger even when another exchange still shows the SIP price.
- Para 36-38: SmartProvide displays a non-marketable order below the SIP NBO (buy) / above the SIP NBB (sell), for up to
  1 to 5 seconds depending on size, or until a new strategy is chosen on market-data updates.
- Para 37: SEC: about 18% of SmartProvide shares were filled in the market. Report ours next to it.
- Para 39: unfilled shares are re-evaluated: display again, route to take liquidity, or internalize (SEC: about 490,000
  of 690,000 SmartProvide orders were internalized afterwards). Default: internalize at the SIP price at window end;
  report how many got a worse price than immediate internalization at the arrival SIP price.
- Footnote 9: SIP consolidation latency 4.04 ms (NYSE) / 5.42 ms (Nasdaq) in early 2010, 0.49 / 0.92 ms in early 2016.
  SIP_LATENCY_MS default 5; sensitivity 0.5 / 5 / 50 / 250.

## Toxicity analyzer (Citadel Investment Group's patent US 7,587,347 B2, divisional US 7,987,128 B2; using it is our choice)
Read the full text (e.g. https://patents.justia.com/patent/7587347 and /7987128) and implement its method on public data:
per order, record order parameters, market-condition parameters and execution parameters; track the outcome of each
fill (a running profit/loss tab from the spread at the fill and the later price); combine the parameters empirically
into a "toxicity quotient" by correlating them with losing versus profitable fills; respond (internalize, route,
delay). On our data: dealer P&L markouts at 1 s / 5 s / 30 s after each print, features available in the feeds,
fit on the first half of the recording, test out of sample on the second half; principal interest = quotient below a
threshold (OURS: the SEC order does not say how CES decided, and neither document links the two). Quote the patent's
own words for every feature and step in the code and in RULES.md.

## Refinements (decided before the build)
- Two presets, both always reported:
  - `latency` (headline): slow view = the same consolidated top of book, delayed by CONSOLIDATION_DELAY (5 ms default;
    sensitivity 5 / 50 / 250 / 1000 ms). This isolates the SEC's main reason (para 22).
  - `sip`: slow view = round-lot-only consolidated top (levels >= ROUND_LOT, default 0.05 BTC) + the same delay
    (adds the odd-lot reason, para 23). Crypto books carry many dust levels, so this preset fires far more often; say so.
- Gap metrics for FastFill, both reported:
  - `gap_top`: the SEC's literal comparison, |slow price - fast top price| per BTC and in bps.
  - `gap_exec_usd`: the value the order could really have had: walk the fast consolidated book from the top while the
    level price is better than the slow price, up to the order size, sum (slow - level) x size used. This avoids
    counting a 0.0001 BTC dust level as if it could fill a whole order.
- Ordering: merge all venue files by the monotonic receive clock `m`; display times from `t`.
- Trade sides (aggressor): Coinbase `match.side` is the MAKER side (taker = opposite); Kraken v2 `trade.side`,
  Bitstamp `type` (0 buy, 1 sell) and Gemini `trade.side` are taker sides. Verify each against the venue docs.
- Books: Coinbase `snapshot` + `l2update` changes [side, price, size] (size 0 = delete); Kraken v2 `book` snapshot +
  updates (qty 0 = delete; checksum available for verification); Bitstamp `order_book_btcusd` = full top-20 snapshot each
  message (replace); Gemini first `l2_updates` = full book, then changes [side, price, qty] (qty 0 = delete).
- Reconnects (`event: connected`) reset that venue's book until a new snapshot arrives; a venue with no valid book is
  left out of both views (never treat a stale book as live).
- SmartProvide: display window 1 s for size <= 0.1 BTC, 5 s for size >= 1 BTC, linear between; posted price = slow price
  minus one tick ($0.01) on the customer's side; fills only from prints that arrive after posting, inside the window,
  at or through the posted price; partial fills allowed; the rest fills at the fast top at window end. Compare the final
  average price with the slow price at arrival (what the customer would have got without SmartProvide).
- Tests must reproduce the order's two examples with synthetic books: FastFill (buy; slow NBO 10.01, fast 10.00 -> fill
  at 10.01, gap 0.01) and SmartProvide (buy; slow NBO 10.01, fast 10.02 -> post a bid at 10.00).
