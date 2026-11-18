# A markout study of your own fills

Measures adverse selection on your own trades: after each of your fills, did the price move for you or against you
1, 5 and 30 seconds later? It applies the idea of the patent's profit analyzer (US 7,587,347, col. 13:56-14:11) to
your side of the trade: log the spread at the fill, mark the trade later, keep a running tab.

What you need: a CSV export of your fills on Coinbase, Kraken, Bitstamp or Gemini (BTC/USD), and a recording of the
same venue's public order book covering the same time. Record while you trade, for example
`citadel-fastfill record --venues coinbase --minutes 120 --out data/my_session`.

```text
Run a markout study of my own fills with tools/markout_my_fills.py. Read CLAUDE.md and the docstring of
tools/markout_my_fills.py first. My fills are in <path to my CSV>; the order books I recorded while trading are in
data/<my recording>.

1. Look at the header and the first rows of my CSV. If its columns are not time, side, price, size (and venue),
   write a converted copy to data/my_fills.csv: time in UTC (ISO 8601 or epoch), side = MY side (buy or sell), price
   in USD, size in BTC, venue = coinbase, kraken, bitstamp or gemini. Keep only BTC/USD fills and tell me what you
   dropped. Do not print my full fills in your replies; summarize them (count, time range, venues, sides).
2. Check that the recording covers the fills: citadel-fastfill check-venues data/<my recording>, and compare its time
   range with the fills' time range.
3. Run: citadel-fastfill markout --fills data/my_fills.csv --data data/<my recording> --per-fill out/my_markouts.csv
   and the same with --json.
4. Explain the result:
   - per horizon (1 / 5 / 30 s): mean and median markout in bps, the share of losing fills, and the dollar result
     split into the edge at the fill and the later move,
   - by venue and side,
   - what it says about adverse selection: if most of my fills lose at 5 s, the orders that traded with me tended to
     know where the price was going. The patent calls "toxic" the orders that would give the broker-dealer who
     accepts them "no profit, reduced profit and/or a loss" (col. 4:1-3).
   - which fills cost the most (from out/my_markouts.csv, top 5 by dollar loss at 5 s, with time and size).
5. If my exchange timestamps and the recording's clock differ, the 1 s markouts are unreliable: say so, and try
   --shift-ms with the offset if I know it.

My fills are private: keep them in data/ and out/ (both are ignored by git), do not upload them anywhere, and do not
commit them. This is a measurement of my past trades, not investment advice.
```
