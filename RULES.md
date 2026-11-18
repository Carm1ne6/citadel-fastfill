# Rules: where every behaviour comes from

This is a rebuild from public documents, not Citadel's code: the code of FastFill and SmartProvide is not public. It
rebuilds the two order-handling algorithms the SEC described in its 2017 order against Citadel Securities (FastFill and
SmartProvide) and the toxicity and profit analyzers of Citadel Investment Group's patent US 7,587,347, and runs them
on public BTC/USD market data. Every behaviour below is traced to a paragraph or footnote of the SEC order or to a
passage of the patent, with a short quote, or is marked **OURS** where neither fixes it. Whatever the documents do not
fix is a named parameter in `citadel/params.py`, marked **OURS**, listed in section 8 and printed with every run. It
is a simulation: no orders are sent anywhere.

## Sources

- **SEC**: *In the Matter of Citadel Securities LLC*, Securities Act Release No. 10280 / Exchange Act Release
  No. 79790, Administrative Proceeding File No. 3-17772, January 13, 2017. Text: `docs/sec_order_2017_33-10280.txt`
  (PDF: https://www.sec.gov/litigation/admin/2017/33-10280.pdf). "SEC 30" is paragraph 30, "fn 13" is footnote 13,
  "V.C" is Section V, item C.
- **PAT**: US 7,587,347 B2, "Computer implemented and/or assisted methods and systems for detecting, tracking and
  responding to toxic, or likely toxic, orders in an equities order flow using toxicity and/or profit analyzers".
  Inventors Kenneth C. Griffin and Matthew Andresen; assignee Citadel Investment Group, L.L.C.; filed Nov 30, 2004;
  granted Sep 8, 2009. "col. 9:36-38" is column 9, lines 36-38 of the scan in `docs/patents/US7587347.pdf` (line
  numbers read from the scan's gutter by eye); the wording was checked against the Google Patents text
  (https://patents.google.com/patent/US7587347B2/en). Claims are cited by number. Its divisional US 7,987,128 B2
  (application 12/546,854) has the same specification; its six claims are method claims: a "toxicity analyzer
  computer system" receives the order, determines a toxicity quotient and transmits the order for execution according
  to its eligibility (claims 2-6 add the parameters, outcome monitoring and corrective action). The certificate of
  correction on the last page of the scan (Mar 30, 2010) changes the patent's term adjustment from 745 to 1,170 days.
- Venue conventions (book messages, trade sides, price grids) are in `NOTES.md`, with the venues' documentation and the
  checks on our data.

## 1. The two views of the market (`citadel/sip.py`, `citadel/depth.py`)

| Behaviour | Source | Code, parameter |
|---|---|---|
| The slow view is a SIP: each venue's best **round-lot** quote, consolidated into one NBB and one NBO. Odd lots are left out. | SEC 5: "The SIPs disseminate the best-priced, round lot quotations (i.e. quotations in increments of at least 100 shares) from each exchange and calculate and identify the National Best Bid ("NBB") and National Best Offer ("NBO")". "The SIPs do not disseminate odd lot quotations". SEC 19: "a SIP consolidates all of the best-priced, round lot quotations from each exchange ..., calculates the NBBO for the security, and disseminates both the exchanges' best quotations and the NBBO." | `sip.py`; `preset` = `sip` |
| A round lot is 0.05 BTC. | SEC 5: a round lot is "at least 100 shares". **OURS**: 0.05 BTC, about $3,200 at the recording's price. No document fixes a share price; at the order's own example price ($10.00, SEC 11, 13, fn 11) 100 shares are $1,000, about 0.0155 BTC, a row of the sensitivity 0.01 / 0.0155 / 0.05 / 0.10 BTC. | `round_lot_btc` |
| The SIP is late: at time t it shows only quotes it received by t minus the latency. No look-ahead (test `test_sip_publishes_only_after_its_latency`). | SEC 22: "Because of the time required for this consolidation process and the additional physical distance that the information must travel between the exchange and the SIP processor, a market participant may be able to receive information from the direct feeds and process it before the market participant has received the same information from the SIP." Fn 9: "4.04 milliseconds and 5.42 milliseconds" (NYSE and Nasdaq SIP, beginning of 2010); "0.49 milliseconds and 0.92 milliseconds" (beginning of 2016). | `sip_latency_ms` = 5; sensitivity 0.5 / 5 / 50 / 250 / 1000 ms |
| The fast view is the depth-of-book feeds: every displayed level of every venue, odd lots included, as received, consolidated across venues. | SEC 6: depth of book feeds "include information about all displayed quotations, executions, and cancellations". SEC 7: "Market participants also can consolidate multiple depth of book feeds"; the views differ partly because depth data "is received sooner by market participants and includes odd lots". SEC 20: they "include information related to both round lot and odd lot orders". | `depth.py` |
| A second preset, `latency`: the slow view is the same top of book, odd lots included, only late. Both presets are reported, side by side in the report. | SEC 22 (latency) and SEC 23 (odd lots: "if the best quote displayed on an exchange is based on an odd lot, this information would not be included in the SIP NBBO") are separate reasons the views differ; this preset isolates SEC 22. SPEC named `latency` the headline; the headline is `sip` because the SEC's SIP carries round lots only (SEC 5: "The SIPs do not disseminate odd lot quotations") and SEC 29 counts odd-lot triggers, which `latency` cannot produce. | `preset` = `latency` |
| The four venues stand for the exchanges. | SEC 4: CES received "several data feeds sent directly by individual stock exchanges"; SEC 20: "CES subscribed to a subset of exchanges' depth of book feeds". **OURS**: Coinbase, Kraken, Bitstamp and Gemini, BTC/USD. | `venues` |
| A venue whose connection dropped, or whose Kraken book fails its checksum, is left out of both views until a fresh book arrives. | SEC 24: "Transmission or system issues also can cause discrepancies between the best prices displayed on the SIP and depth of book feeds." SEC 23: views differ by "how market data errors are resolved". **OURS**: the handling. | `venues.py` (`Venue.live`) |

## 2. Four crypto venues as one national market (OURS, needed by the data)

US exchanges trade one fungible stock at one price under Reg NMS. Coinbase, Kraken, Bitstamp and Gemini do not. Over
run_final Gemini's prices sit about $15 (2.4 bps) above Coinbase's, Kraken's and Bitstamp's about $1.7 (0.3 bps)
below, and the four books, put side by side, are crossed by a few dollars most of the time (fees stop the
arbitrage): without the rule below the consolidated direct view is crossed or locked in 1,193 and the SIP in 1,058 of
the 1,199 one-second samples of run_final (the first second, before every book has its snapshot, is left out, as in
section 10). Two adjustments, both reported and both switchable:

| Behaviour | Source | Code, parameter |
|---|---|---|
| **Basis**: each venue's prices are shifted onto the reference venue's scale by its slow price difference to it: a time-weighted EMA of venue mid minus reference mid, past data only, rounded to whole cents. No order is handled in the first `warmup_s` (one time constant): before that the basis is a single, noisy price difference. | **OURS** | `basis_adjust` = on, `basis_ref` = coinbase, `basis_tau_s` = 60, `warmup_s` = 60 |
| **Crossed quotes**: a level of another venue that locks or crosses the reference venue's best OPPOSITE quote is not counted, in both views: its bids must stay below the reference ask, its asks above the reference bid. It may tie or improve the reference's own side. Two other venues can still cross each other inside a reference spread wider than one cent; that is left as it is and counted (section 10). | SEC 23: views differ by "how locked and crossed markets are resolved". **OURS**: resolving them the way Reg NMS Rule 610(d) treats displayed quotes. Test `CrossedQuoteClip`. | `clip_crossed` = on |
| Book levels smaller than one share-equivalent are quotes like any other. | SEC 5: an odd lot is a quotation "for less than 100 shares" (OURS: equity quotes are whole shares, so never under one share); SEC 23: views differ by "how odd lots are treated". **OURS**: the floor is off. At one share-equivalent (0.0005 BTC) it removes Coinbase's sub-share levels, Coinbase's spread widens, and the other three venues cross each other inside it: the SIP of run_final is crossed or locked in 260 of 1,199 seconds instead of 1. Every FastFill reports instead whether its trigger rested only on sub-share levels (`sub_share_pct`). | `min_level_btc` = 0 |
| Price grids: Coinbase, Bitstamp and Gemini quote BTC/USD in $0.01, Kraken in $0.10 (checked in `NOTES.md`). | venue rules | `venue_ticks`, `tick_usd` |

## 3. Orders (`citadel/sim.py`)

| Behaviour | Source | Code, parameter |
|---|---|---|
| Every public taker order on the four venues is one marketable order of its aggressor (taker) side: its fills on one venue are grouped, its size is their sum, and it arrives when its last fill is received, seeing the books as they were then. A group ends when the next message received, from any venue, is not one of its fills. | SEC 1: "The primary type of equity order that retail broker-dealers send to wholesale market makers are marketable orders from retail investors, which can be market orders or marketable limit orders". SEC 2: wholesalers "largely handle marketable orders on a fully automated basis". **OURS**: public taker orders stand for those orders; the taker of a fill is Coinbase's `taker_order_id`, Bitstamp's newer order id, and for Kraken and Gemini (no order ids) the side and exchange timestamp (`NOTES.md`). Test `TakerOrders`. | `sim.Session.feed`, `ces.on_order` |
| Orders below one share-equivalent are dust, not orders (the floor applies to the grouped size). | **OURS**: `round_lot_btc` / 100 = 0.0005 BTC (about $32). | `min_order_btc` |
| The taker side of each venue's print. | Venue documentation, checked on our data: `NOTES.md`. | `venues.py`, `check_venues.py` |
| Replay order: the four recordings merged by receive time. The recorder's monotonic clock moves in 15-16 ms steps on the recording machine (Windows), too coarse for a 5 ms SIP delay, so the replay clock is the wall clock `t`, checked against the monotonic clock for steps. `t` is stored in 100 ns units but advances in steps of about 1 ms (run_final: 367,699 messages share 123,936 distinct times, none closer than 0.3 ms), so the 0.5 ms SIP row is below the clock's resolution. Messages with the same `t` keep the order coinbase, kraken, bitstamp, gemini. Live (`live.py`) uses the same clock rule on the same engine. | **OURS** | `sim._stream`, `live.Clock` |

## 4. CES order handling (`citadel/ces.py`), paragraph by paragraph

| Step | Source | Code, parameter |
|---|---|---|
| **Principal interest.** For every order CES decides whether to take it as principal or work it in the market. We decide it with the patent's toxicity analyzer (section 5): principal interest when the order's toxicity quotient is below `tox_threshold`. | SEC 26: "CES used proprietary algorithms to decide whether to internalize a marketable order in whole or in part (referred to as having a "principal interest") or whether to seek to fill the order in the market on a riskless principal basis." SEC 2: "using proprietary algorithms to determine whether to execute the order, in whole or in part, as a principal ... or whether to attempt to fill all or part of the order on a riskless principal basis". **OURS**: the SEC order does not say how CES decided; we use for it the toxicity analyzer of Citadel Investment Group's patent (filed in 2004, granted in 2009). Neither document links the two: the SEC order never mentions the patent, the patent was filed in 2004 by Citadel Investment Group (not the respondent, Citadel Securities LLC) before CES began "in late 2005" (SEC 17), and the patent itself says its profit measure "could not be used to stop the execution of unprofitable orders" (col. 13:60-64; our decision uses the toxicity quotient, known before execution). | `ces._new_order`, `toxicity.ToxicityAnalyzer.respond` |
| Without a principal interest, the order is worked in the market (riskless principal). | Fn 12: "CES typically attempted to execute a retail order in the market on a riskless principal basis if it determined that it did not have a principal interest in the order." Fn 10: a riskless principal trade is one where the dealer "buys (or sells) the security for its own account from (or to) another person and then allocates the shares to the customer order in a contemporaneous offsetting transaction". | `ces._sp_end`, `sp_reeval_no_pi` |
| **Internalization price**: the SIP NBB or NBO at the time of execution, or better by a price improvement. | SEC 8: "When it internalized shares, CES executed those shares at the SIP NBBO at the time of execution or better." SEC 27: "CES often gave those orders price improvement by adjusting this price by a small amount." Fn 11: "if CES internalized a customer order to buy when the NBO was $10.00, CES might execute the order at $9.999 per share." **OURS**: 0, because BTC books quote in whole cents and fn 11's tenth of a cent is not a BTC price; test `test_fn_11_price_improvement` runs it at $0.001. | `price_improvement_usd` = 0 |
| **FastFill trigger**: the best price on one or more of the depth-of-book feeds FastFill references is better than the SIP price for the order (a lower offer for a buy, a higher bid for a sell). | SEC 10: "known as FastFill, was triggered when the best price from one or more of the depth of book feeds that FastFill referenced was better than the best price disseminated by the SIP feed." SEC 29: "FastFill was triggered when the best bid or offer from one or more of the depth of book feeds was better than the SIP best bid or best offer, as applicable." **OURS**: FastFill references all four feeds (the order does not say which). | `ces._ff_trigger`, `fastfill_feeds` |
| Checked when handling starts and again on every market-data update while SmartProvide holds the order. | SEC 30: "If the FastFill triggering event existed at the time CES first began handling the order or arose while another strategy was handling the order". | `ces._new_order`, `ces._check_takeovers` |
| **FastFill fill**: immediately, the whole remaining quantity, at the SIP price, whatever the displayed sizes, never at the better direct price. | SEC 30: "FastFill immediately internalized the order (or remaining unexecuted quantity) using the SIP NBB or NBO, as applicable. FastFill did not internalize the order at the better bid or offer from one or more of the depth of book feeds, nor did FastFill seek to obtain that price through routing." SEC 32: "it filled the entire unexecuted portion of the order at the SIP NBB or NBO, as applicable, and did so (other than in the circumstances described in Paragraph 31), regardless of the number of shares associated with the SIP NBB or NBO or the best bid or offer from one or more of the depth of book feeds." SEC 11: SIP best offer $10.01, depth-of-book offer $10.00: "FastFill immediately internalized the order using the SIP offer of $10.01 per share." | `ces._fastfill`; test `test_para_11_buy_filled_at_sip_offer` |
| **Odd-lot trigger**: flagged when no round-lot level on the direct feeds beats the SIP price, so only odd lots caused the trigger. | SEC 29: "For approximately 20% of all orders internalized by FastFill, the triggering event was caused by an odd lot from one or more of the depth of book feeds." | `ces._fastfill` (`odd_lot`) |
| **Gap**: what the order did not get. `gap_top` = SIP price minus the best direct price (per BTC and in bps, the SEC's own comparison); `gap_usd` = the executable value: walk the direct book from the top while a level beats the SIP price, up to the order size. The report counts the FastFill orders whose price improvement was smaller than `gap_top` (`pi_short_pct`). | SEC 30: price improvement "often was not sufficient to equal the price difference that had triggered the strategy." SEC 15: the statements "suggested that CES would either internalize a marketable order at, or seek to obtain for that order through routing, the best price for that order that CES observed on the various market data feeds it referenced, which FastFill and SmartProvide did not do." **OURS**: the executable walk (a 0.0001 BTC level cannot fill a whole order). | `depth.walk_gap`, `ces._fastfill` (`ff_pi_short`) |
| **Sub-share triggers**: each FastFill records whether its trigger rested only on levels smaller than one share-equivalent (`min_order_btc`), which no equity book can show. | SEC 5: odd lots are quotations "for less than 100 shares". **OURS**: the disclosure (the floor itself is off, section 2). | `ces._fastfill` (`sub_share`) |
| **Small orders worse, large orders better**: the SIP fill is compared with taking the whole order from the displayed direct liquidity; "small" is under one round lot. | SEC 32: "FastFill improved the overall execution price for a substantial number of (predominantly larger) orders. However, a substantial number of smaller orders fared worse because of FastFill in that there was sufficient liquidity displayed in the market to fill all or most of such orders at a price better than the SIP NBB or NBO". SEC 27: CES "at times internalized orders for a quantity that was greater than that associated with the NBB or NBO". **OURS**: the size split at one round lot. | `ces._fastfill`, `depth.walk_vwap`; test `test_para_32_entire_quantity_at_sip_regardless_of_size` |
| **Trade out attempt**: without a principal interest, or above the position limit, FastFill still internalizes the order and, at the same moment, sends an order for CES's own account at the better direct price for the quantity it took on. | SEC 31: "In a narrow set of circumstances, FastFill internalized the order even though CES did not have a principal interest in the order or internalization of the order would cause CES to exceed its position limit in the relevant security. In these situations, contemporaneous with determining to internalize the order at the SIP NBB or NBO, as applicable, FastFill sent a proprietary order to the market in an effort to execute for itself at a price better than the SIP NBB or NBO". **OURS**: no position limit (its value is not public). | `ces._fastfill`, `position_limit_btc` = 0 (off) |
| Only if that order is at most 90% of the quantity at the best bid or offer on a depth feed. Otherwise FastFill does not take the order, which is left to CES's riskless principal handling outside these two algorithms. | Fn 13: "an additional eligibility criterion applied that required the share quantity of the proprietary order to be no greater than 90% of the quantity associated with the best bid or offer, as relevant, from one or more of the depth of book feeds." SEC 30: "and all other eligibility conditions for internalizing by FastFill were met". **OURS**, two readings of fn 13: the proprietary order is for the customer's whole quantity, and the 90% base is the largest single venue's size at the best price (fn 13's "from one or more of the depth of book feeds" could also mean the sum over feeds); the order goes to that venue. | `trade_out_cap` = 0.90; test `test_fn_13_ninety_percent_rule` |
| Trade out result: the order reaches its venue `trade_out_latency_ms` after it is sent and is read on the first book message of that venue received from then on: filled, in full or in part, if the level is still there. The price is compared in the venue's own prices fixed when the order was sent. | Fn 13: "FastFill successfully executed these proprietary trade out attempt orders, in full or in part, approximately 55% of the time and did not receive any fill approximately 45% of the time." **OURS**: the 50 ms delay and the reading. Coinbase's book batches arrive every 51 / 60 / 62 ms (p10 / p50 / p90, run_final, `NOTES.md`): reading the book at exactly 50 ms would most often read the book at sending, and the 5 ms row would be an artifact. Sensitivity 5 / 50 / 250 ms (tests `TradeOutReadOnTheVenuesNextBook`, `RawVenuePrices`). | `ces.on_book`, `ces._trade_out_result`, `trade_out_latency_ms` = 50 |
| **SmartProvide trigger**: the SIP price is better than the best price on at least one of the feeds SmartProvide references, compared feed by feed; it often references one feed and triggers even when another venue shows the SIP price. | SEC 12: "triggered when the SIP NBB or NBO, as applicable, was better than the best price from at least one of the depth of book feeds." SEC 34: "SmartProvide referenced only one depth of book feed for many securities and fewer than all of the depth of book feeds for other securities." "SmartProvide sometimes could be triggered when the difference existed between the SIP and only one of the depth of book feeds SmartProvide referenced, and not the others." SEC 35: "even though the best offer on one or more of the depth of book feeds from one or more other exchanges was $10.01." | `ces._sp_trigger`, `smartprovide_feeds`; tests `test_para_35_triggers_although_another_feed_shows_sip_price`, `test_para_34_triggers_on_one_referenced_feed_and_not_the_others` |
| Which feed: Coinbase (SPEC). | **OURS**: SEC 34 says only that "at times" SmartProvide was triggered when the SIP price came from an exchange whose feed it did not reference. Kraken quotes on a $0.10 grid, so its best price is behind the SIP on nearly every order: with Kraken the trigger is a standing condition, not an event (under `latency` it fires on almost every order, and nearly all FastFills become takeovers of those displays). With Coinbase it fires when the SIP still shows a Coinbase price that is gone (SEC 22) or another venue's round lot sits inside Coinbase's spread. Both feeds are reported under both presets (section 10). | `smartprovide_feeds` = coinbase |
| SmartProvide can only reach an order FastFill did not take at arrival; its share is reported of all orders and of the orders without a FastFill trigger at arrival. | SEC 30 (FastFill first), SEC 12. **OURS**: the second share. | `share_no_trigger_pct` |
| **Display**: a non-marketable order one cent inside the SIP price (a bid below the NBO for a buy, an offer above the NBB for a sell), then rounded away from the SIP onto the display venue's price grid (Coinbase $0.01: exactly one cent inside; Kraken $0.10: 1 to 10 cents inside), on the venue SmartProvide reads. Nothing is internalized meanwhile. | SEC 36: "SmartProvide routed a non-marketable order to be displayed in the market at a price that was less than the SIP NBO for a buy order or greater than the SIP NBB for a sell order. SmartProvide did not immediately internalize the marketable order at the SIP NBB or NBO, as applicable, or seek immediately to obtain shares at that price through routing." SEC 13: SIP NBO $10.01, feed $10.02: "SmartProvide would send a buy order to be displayed in the market at a price less than $10.01, such as $10.00." SEC 37: "by at least one penny per share". **OURS**: one cent, the rounding, the display venue. | `ces._smartprovide`, `tick_usd`, `venue_ticks`, `sp_post_venue` = coinbase; tests `test_para_13_37_display_bid_below_sip_offer`, `DisplayGrid` |
| **Window**: displayed 1 s (order of 0.1 BTC or less) to 5 s (1 BTC or more), linear in between, or until FastFill takes over. | SEC 38: "The order would be displayed for up to one to five seconds (depending on the size of the order) or until CES chose a new order handling strategy (which might be prompted by updates in market data)." SEC 13: "displayed for up to one to five seconds, depending on the size of the order." **OURS**: the size scale and its direction (that larger orders are displayed longer). | `sp_display_min_s` = 1, `sp_display_max_s` = 5, `sp_small_btc` = 0.1, `sp_large_btc` = 1 |
| **Fills**: later prints on the display venue at or through the displayed price fill it, partial fills allowed; the customer gets that price. The print is compared in the venue's own prices with the displayed price fixed at display. | SEC 37: "If that order received an execution at $10.00, CES gave that better price to the retail order. Contemporaneous reports generated by CES indicated that approximately 18% of the shares handled by SmartProvide were filled in the market". **OURS**: only prints on the display venue that arrive after the display can fill it. | `ces._sp_fill_from_print`; tests `test_para_37_fill_at_better_price`, `RawVenuePrices` |
| **Delay**: how long each SmartProvide order waited from arrival to its last fill, against 0 for immediate internalization. | SEC 38: "By doing so, SmartProvide likely delayed execution of some marketable orders (as compared to internalizing those orders immediately or routing the order at a marketable price)." **OURS**: the measure. | `ces._finish_sp` (`sp_delay_ms`); test `SecObservations` |
| **Takeover**: a FastFill trigger that arises while SmartProvide displays the order ends the display; FastFill internalizes the rest at the SIP price. | SEC 30: "arose while another strategy was handling the order". SEC 38: "until CES chose a new order handling strategy (which might be prompted by updates in market data)". | `ces._check_takeovers`; test `test_para_30_fastfill_takes_over_smartprovide` |
| **Re-evaluation** at window end: FastFill if its trigger is there; else, with a principal interest, internalize the rest at the SIP price then; without one, route it to take the displayed liquidity. Each is compared with internalizing at the SIP price on arrival. | SEC 39: CES "would evaluate how to handle the order (or its remaining quantity), which might include displaying an order in the market using another strategy, routing an order to the market in an attempt to take liquidity, or internalizing the order." "Some of the orders CES internalized after SmartProvide displayed an order in the market on their behalf received a price that was worse than they would have received had CES immediately internalized at the SIP NBB or NBO". SEC 13: CES "could either internalize or seek to obtain an execution in the market." SEC 33: "approximately 490,000 orders were internalized following handling by SmartProvide." **OURS**: which branch when (fn 12 for the no-principal-interest branch); no second display strategy. | `ces._sp_end`, `sp_reeval_no_pi` = route; tests `test_para_39_reevaluation_worse_than_immediate`, `test_para_39_with_principal_interest_internalizes`, `test_para_39_fn_12_without_principal_interest_routes` |
| Customers do not cancel. | SEC 39: "the customer might cancel the order." **OURS**: not modelled. | - |
| An order whose FastFill trigger exists but which fails the 90% rule is not handed to SmartProvide. | **OURS**: the order does not say what happened to it; fn 12 suggests riskless principal handling outside these two algorithms. | `ces._new_order` |
| The end of the data: a display window or a trade out still open is decided at the last message's time with the last books, so every event lies inside the recording. | **OURS** | `ces.CES.finish` (`sp_cut_by_data_end`); test `DataEnd` |

What every gap is measured against is what CES told its clients. SEC 14: CES described a market order as an
"[o]rder to buy (sell) at the best offer (bid) currently available in the marketplace". SEC 40: the same disclosure
("best offer (bid) price currently available in the marketplace"). SEC 42 (and 16): "Citadel Securities willfully
violated Section 17(a)(2) of the Securities Act."

## 5. Toxicity analyzer (`citadel/toxicity.py`, PAT)

| Step | The patent's words | Code, parameter |
|---|---|---|
| What "toxic" means | col. 4:1-8: "orders that, if accepted by a consolidating broker-dealer, would result in no profit, reduced profit and/or a loss to the broker-dealer"; claim 1: the toxicity quotient defines "when the order, if executed, comprises one of no profit, reduced profit or a loss when a wholesaler or a market maker provides market liquidity" | `is_toxic`: the dealer made no profit or a loss (P&L <= 0). "Reduced profit" is the profit analyzer's test (section 6) |
| Where the dealer's profit comes from | col. 3:9-14: the profit, "when internalizing the trade ..., is made at the level of the trade execution, and is based on the spread between bid and offer prices for the equity" | `dealer_pnl`: fill price against the direct-feed mid |
| The outcome to learn from | col. 10:49-53: "Upon execution of normal orders, broker-dealer 130 would likely record a trading profit (on average), while upon execution of toxic orders, a reduction in trading profit or a loss would likely result" | label: the dealer's P&L `tox_horizon_s` after internalizing the order at the SIP price is <= 0 (**OURS**: the horizon; the offline test also uses 1 and 30 s) |
| What is measured | col. 9:36-38: "The variables which can be used to characterize toxicity can be derived from order parameters, market condition parameters, and/or order execution parameters." | `measure`, `PARAMETERS` (16 parameters, all known when the order arrives) |
| Order parameters | col. 9:38-43: "the identity of the equity (e.g., symbol), the action type (e.g., buy or sell), the order type (e.g., market, limit, stop, all-or-none), the limit price (e.g., for a limit order), and the order size (e.g., the number of shares)"; claim 29 | `side_buy`, `log_size`; `limit_through_bps` (**OURS**: a public taker order has no visible limit price, but its last (worst) fill price bounds it: how far it traded through the SIP price). Symbol and order type are constant here (one instrument, every order marketable): `NOT_AVAILABLE` |
| Market condition parameters | col. 9:43-49: "the theoretical value of the equity, the frequency of order submission, the NBBO market source, the NBBO size, the liquidity of the equity in the market, the implied spread (NBBO), and equity-market performance correlation (e.g., beta)"; claim 6: "order size relative to NBBO size", "current NBBO bid-offer spread"; claim 30 | `theo_vs_sip_bps` (**OURS**: direct-feed microprice minus SIP mid; col. 4:12-13 calls the theoretical fair value "the value to which the price is likely to move in the near future"), `order_freq` (taker orders a second on the order's venue, last 10 s), `src_coinbase` / `src_kraken` / `src_bitstamp` / `src_gemini` (which venue sets the SIP price), `log_nbbo_size`, `log_size_vs_nbbo`, `log_liquidity` (direct size within 10 bps of the best price), `nbbo_spread_bps`. Beta: one instrument, not used |
| Execution parameters | col. 9:49-51: "the fill spread (e.g., the bid/offer spread at the time of filling the order) and the fill price"; claim 31 | `fill_spread_bps` (direct spread at the fill), `fill_edge_bps` (fill price against the direct mid) |
| Other characteristics | col. 9:53-55: "any other distinct characteristic may be used alone and/or in combination with any or all of the above mentioned parameters" | `momentum_1s_bps` (**OURS**: direct mid change over the last second) |
| Combining them | col. 9:57-67: the parameters "may be combined empirically to generate a "toxicity quotient" ... by measuring order, market condition, and/or execution parameters of various orders over time, and correlating these parameters with the outcome of trades (e.g., toxic trades yielding a loss compared to normal profitable trades)"; claim 5 | `fit_logistic`: logistic regression of losing (1) against profitable (0) fills on the standardized parameters, L2 penalty `tox_ridge` (**OURS**: the model form); `correlations` reports each parameter's correlation with a losing fill |
| Only earlier orders count | col. 9:67-10:3: an order's characteristics "may optionally be compared to previously received (and potentially executed) orders in order to determine such a toxicity quotient" | walk-forward: the model is refit every `tox_refit_s` on the outcomes already known; it stays silent (screens nothing) until `tox_min_train` outcomes exist (tests `test_toxicity_learns_an_outcome_only_once_it_is_known`, `test_dev_cut_at_100_s_with_the_quotient_running`) |
| Updated as outcomes arrive | col. 10:13-16: "a previously generated toxicity quotient may be modified. For example, toxicity quotients may be dynamically modified based on events occurring after their initial generation"; claim 12 | the same refit |
| Using it | col. 10:16-19: "an order may be examined to determine the likelihood that it will be toxic if executed." col. 10:58-60 (FIG. 3): the analyzer "may be used to "screen-out" orders that fit certain criteria characteristic of toxic orders" | `quotient`: the fitted probability that the fill loses |
| Responding | col. 11:59-67 (FIG. 5): "If it is determined at step 506 that the order submitted by investor 110 is not toxic, the order is executed at step 508 (e.g., by sending the order to an exchange or internalizing the order ...). Otherwise, if the order is determined to be toxic at step 506, at step 510, the order submitted by investor 110 is either rejected or, for example, it is executed at a later time, at another market center, with another market participant, and/or for an increased fee (commission)." col. 11:9-13 (FIG. 4): likely toxic orders "are not executed (e.g., are not sent to an exchange and are not internalized)". Claims 10-11: "either rejecting the order or executing a modified version of the order", "a modified order size" | `respond`: EXECUTE (quotient below `tox_threshold`) = principal interest (SEC 26, **OURS**: the link); otherwise ANOTHER_MARKET_CENTER = no principal interest: riskless principal in the market (fn 12) or FastFill with a trade out (SEC 31). **OURS**: of the patent's responses only "another market center" is used; reject, later, another participant, a higher fee and a reduced size are not |
| Threshold | not given. Two passages bound it. col. 9:1-5: "with a standard investing population, a certain level of toxic trades and/or trades that are suspect to be toxic may be tolerated and/or is to be expected due to the random arrival of orders in conjunction with the random movement of equity bid and offer levels." SEC 31: FastFill without a principal interest was "a narrow set of circumstances"; trade outs "less than 6.9% of the shares internalized by FastFill" | `tox_threshold` = 0.7 (**OURS**). At the 5 s horizon about half of all fills lose (the random arrival of col. 9:1-5), so 0.5 ("more likely than not") would screen about half of an ordinary flow. 0.7 screens only the orders the analyzer expects to lose at least 7 times in 10. The report prints the grid 0.5 / 0.6 / 0.7 / 0.8 / 0.9 with the orders without a principal interest, the FastFill share and the trade-out share of FastFill BTC on each half (the second half out of sample) next to SEC 31's 6.9% |
| Test | not in the patent | **OURS**: fit on the first half of the time orders are handled, AUC on the second half, horizons 1 / 5 / 30 s (`toxicity.evaluate`), next to each single parameter used alone as a score (its direction from the first half): the label marks a fill at the SIP price, so a wide SIP spread alone makes a profit likely, and a quotient that does not beat its best single parameter adds little (`single_feature_auc`) |

## 6. Profit analyzer (`citadel/toxicity.py`, PAT)

| Step | The patent's words | Code, parameter |
|---|---|---|
| Watch every executed trade | col. 13:66-14:3: "a profit analyzer according to the invention may be used to monitor the outcome of some or all trades that are received by a consolidating broker-dealer and that are subsequently executed"; claims 18, 33, 62 | `ProfitAnalyzer.on_fill`: every fill CES makes for its own account (FastFill, internalization after SmartProvide, trade outs) |
| Log the spread at the fill | col. 14:3-5: "By logging the bid/offer spread at the time of order filling for each equity trade executed, a running tab of profit (or loss) can be kept."; claim 19: "logging the bid/offer spread at the time the order is filled" | bid, offer, fill price, size, side and order flow of each fill |
| Profit is known only later | col. 13:60-62: "profit (or loss) on a trade can only be determined after a trade has been executed" | each fill is marked at the direct mid `profit_mark_s` later (**OURS**: 5 s); test `test_profit_analyzer_marks_a_fill_only_later` |
| The running tab | col. 14:18-22 (FIG. 15): the dealer can "track its level of profitability (e.g., in real-time, and/or over a certain period of time)" | `tab_usd`, split into the spread at the fill and the later price move |
| Quality of an order flow | col. 13:56-60: "broker-dealer profit information coupled with market condition information (either instantaneous or averaged over some time period) can be used to characterize the quality of an individual order or flow of orders" | the tab per order flow; **OURS**: an order flow is the venue the order came from |
| Expected level, corrective action | col. 14:5-11: "by monitoring average trading volume and average per trade-profit over various periods of time (days, weeks, or months), an expected level of profit can be established for various OFPs. Deviations from these averages could then be used to signal the need to take corrective action against a particular OFP or investor (e.g., to adjust payment for order flow to the OFP)"; claims 34-35: "establishing an expected level of profit for the order based on the profitability of previous orders", "taking corrective action where the profitability of the order is below the expected level of profit" | per order flow and period `profit_period_s` (**OURS**: our recordings last minutes, not months); a period whose average per-trade profit is more than `profit_signal_z` standard errors below the flow's expected level is a corrective-action signal (**OURS**); the expected level is used once the flow has `profit_min_trades` trades (**OURS**) |
| Payment for order flow follows the profit | col. 14:22-26 (FIG. 15): "By making the amount of payment provided to OFP 120 in exchange for order flow variable and a function of the profit ultimately realized by consolidating broker-dealer 130, at least some of the risk for loss (e.g., based on toxic trades) may be shifted to OFP 120." | not simulated (no payments); the signals are reported |

## 7. The SEC's numbers we print next to ours (`params.SEC_NUMBERS`)

| Number | Source |
|---|---|
| FastFill: about 2.7 million retail orders, about 0.4% of CES's overall order flow | SEC 28: "FastFill executed approximately 2.7 million retail orders between June 2008 and January 2010, which amounted to approximately 0.4% of CES's overall order flow during that period." |
| Both algorithms: 2.6% of the algorithmic engine's retail orders, 0.6% of all CES flow | SEC 18: "approximately 2.6% of the retail orders handled by CES's algorithmic trading engine and 0.6% of CES's overall order flow between June 2008 and January 2010." |
| FastFill triggers caused by an odd lot: about 20% | SEC 29 (quoted in section 4) |
| Trade outs: under 6.9% of FastFill shares | SEC 31: "occurred with respect to less than 6.9% of the shares internalized by FastFill." |
| Trade outs filled in full or part: about 55%; no fill: about 45% | fn 13 (quoted in section 4) |
| SmartProvide: about 690,000 orders, about 490,000 internalized afterwards | SEC 33: "SmartProvide handled approximately 690,000 marketable orders. Of those orders, approximately 490,000 orders were internalized following handling by SmartProvide." |
| SmartProvide shares filled in the market: about 18% | SEC 13: "This occurred for approximately 18% of the shares handled by SmartProvide."; SEC 37 |
| Ordered to pay: $5,200,000 + $1,465,268 + $16,000,000 = $22,665,268 | V.C: "pay disgorgement of $5,200,000, prejudgment interest of $1,465,268, and a civil money penalty of $16,000,000". V.A: cease and desist from violating Section 17(a)(2) of the Securities Act; V.B: "Respondent is censured". Section II: "without admitting or denying the findings". |
| Period and status | SEC 3: statements "from late 2007 through January 2010"; "CES has since discontinued these two algorithms." SEC 33: "CES began using SmartProvide in late 2007." |
| CES's size | SEC 17: "approximately 35% of the average daily volume of retail equity shares traded in the U.S. markets" (at the time of the order, 2017); SEC 18: "approximately 1.2 million equity orders on average per day" during the relevant period |

## 8. Our assumptions (everything the documents do not fix)

1. **The market.** BTC/USD on Coinbase, Kraken, Bitstamp and Gemini stands for one US stock on its exchanges, and
   every public taker order (its fills on one venue grouped) of at least `min_order_btc` (0.0005 BTC) stands for one
   retail marketable order (SEC 1). Taker orders are not retail orders: crypto takers include fast traders, the
   population the patent itself expects to be more toxic ("With the professional investing population (e.g.,
   professional traders), however, the level of toxic trades is likely to be increased", col. 9).
2. **The SIP.** A round lot is `round_lot_btc` = 0.05 BTC (no share price is fixed by any document; 0.0155 BTC, 100
   shares at the order's $10.00 example, is a sensitivity row); the SIP is `sip_latency_ms` = 5 ms late (fn 9's 2010
   order of magnitude); `preset` = `sip` is the headline (SPEC named `latency`; see section 1) and `latency` is printed
   next to it.
3. **One national market.** `basis_adjust`, `basis_ref` = coinbase, `basis_tau_s` = 60 s, no order in the first
   `warmup_s` = 60 s; `clip_crossed` (another venue's levels may not lock or cross the reference's opposite quote);
   `min_level_btc` = 0 (sub-share levels count; section 2).
4. **Prices.** `price_improvement_usd` = 0; `tick_usd` = $0.01 and `venue_ticks` (the venues' grids).
5. **FastFill.** It references all four feeds (`fastfill_feeds`); a trade out is read on its venue's first book
   message from `trade_out_latency_ms` = 50 ms after sending; it is for the customer's whole quantity and the 90%
   base is the largest single venue at the best price; no position limit (`position_limit_btc` = 0); "small" orders
   are those under one round lot; an order that fails the 90% rule is not handed to SmartProvide.
6. **SmartProvide.** It reads Coinbase only (`smartprovide_feeds`, as SPEC) and displays there (`sp_post_venue`); it
   displays one cent inside the SIP price, rounded away from the SIP onto the venue's grid; the window grows linearly
   from 1 s at `sp_small_btc` = 0.1 BTC to 5 s at `sp_large_btc` = 1 BTC (`sp_display_min_s`, `sp_display_max_s` are
   the SEC's; the scale and its direction are ours); only later prints on the display venue fill it; at window end
   the rest is internalized with a principal interest and routed without one (`sp_reeval_no_pi` = route); customers
   never cancel; no second display strategy; a window still open when the data ends is decided at the last message.
7. **Principal interest** comes from the patent's toxicity analyzer (the SEC order does not say how CES decided;
   neither document links the two).
8. **Toxicity analyzer.** Outcome horizon `tox_horizon_s` = 5 s; threshold `tox_threshold` = 0.7 (bounded by col.
   9:1-5 and SEC 31, section 5); of the patent's responses only "another market center"; refit every `tox_refit_s` =
   30 s; silent until `tox_min_train` = 300 outcomes; logistic regression with `tox_ridge` = 1; the proxies for the
   limit price (`limit_through_bps`) and the theoretical value (`theo_vs_sip_bps`); the extra parameter
   `momentum_1s_bps`; dealer P&L marked at the direct-feed mid.
9. **Profit analyzer.** Marks `profit_mark_s` = 5 s after the fill; an order flow is a venue; periods of
   `profit_period_s` = 60 s; a signal at `profit_signal_z` = 2 standard errors below the expected level, once a flow
   has `profit_min_trades` = 20 trades.
10. **Replay.** Messages merged by the wall clock (about 1 ms resolution) checked against the monotonic clock, ties
    in the order coinbase, kraken, bitstamp, gemini; the fills of one taker order grouped while they arrive back to
    back; a venue without a valid book (after a reconnect, or a failed Kraken checksum) is left out of both views;
    `eps` = 1e-9 is the numerical tolerance of every price comparison.

## 9. Every parameter (`citadel/params.py`)

| Parameter | Default | Source |
|---|---|---|
| `venues` | coinbase, kraken, bitstamp, gemini | SEC 4, 20 (several direct feeds, a subset); the venues are **OURS** |
| `preset` | sip | SEC 5, 19, 23 (round lots) + SEC 22 (latency); `latency` isolates SEC 22. SPEC named `latency` the headline; `sip` is, because the SEC's SIP has round lots only and SEC 29 counts odd-lot triggers (**OURS**); both are printed side by side |
| `sip_latency_ms` | 5 | fn 9 (4.04 / 5.42 ms in 2010, 0.49 / 0.92 ms in 2016) |
| `round_lot_btc` | 0.05 | SEC 5 (100 shares); the BTC size is **OURS**; sensitivity 0.01 / 0.0155 ($10.00 x 100 shares, SEC 11) / 0.05 / 0.10 |
| `basis_adjust` | on | **OURS** (section 2) |
| `basis_ref` | coinbase | **OURS** |
| `basis_tau_s` | 60 | **OURS** |
| `warmup_s` | 60 | **OURS** (one basis time constant before the first order) |
| `clip_crossed` | on | SEC 23 ("how locked and crossed markets are resolved"); Reg NMS Rule 610(d); **OURS** |
| `min_level_btc` | 0 (off) | SEC 5 (odd lots are under 100 shares; at least one share, since equity quotes are whole shares, is **OURS**), SEC 23 ("how odd lots are treated"); **OURS**: off, section 2 |
| `min_order_btc` | 0.0005 | **OURS** (one share-equivalent = round lot / 100, applied to the grouped taker order) |
| `price_improvement_usd` | 0 | SEC 27, fn 11; the value is **OURS** |
| `tick_usd` | 0.01 | SEC 13 ("such as $10.00" under a $10.01 NBO), 37 ("at least one penny"); the one cent is **OURS** (section 4); Coinbase's grid (`NOTES.md`) |
| `venue_ticks` | 0.01; Kraken 0.10 | the venues' price grids (`NOTES.md`) |
| `fastfill_feeds` | all four | SEC 10 ("the depth of book feeds that FastFill referenced"), SEC 20; the choice is **OURS** |
| `trade_out_cap` | 0.90 | fn 13; measuring the 90% base on the single venue with the most size at the best price is **OURS** (section 4) |
| `trade_out_latency_ms` | 50 | **OURS**: read on the venue's first book message from then on (Coinbase batches arrive every 51 / 60 / 62 ms); sensitivity 5 / 50 / 250 ms |
| `position_limit_btc` | 0 (off) | SEC 31 names a position limit; its value is **OURS** |
| `smartprovide_feeds` | coinbase | SEC 34 ("only one depth of book feed for many securities"; "at times" triggered by an exchange it did not reference); the feed is **OURS**, as SPEC; Kraken is a sensitivity row |
| `sp_post_venue` | coinbase | **OURS** |
| `sp_display_min_s` | 1 | SEC 13, 38 ("up to one to five seconds") |
| `sp_display_max_s` | 5 | SEC 13, 38 |
| `sp_small_btc` | 0.10 | **OURS** (the size scale of SEC 13, 38 and its direction) |
| `sp_large_btc` | 1.00 | **OURS** (the size scale of SEC 13, 38 and its direction) |
| `sp_reeval_no_pi` | route | SEC 39 (the three options) + fn 12 (no principal interest: riskless principal); the choice is **OURS** |
| `tox_horizon_s` | 5 | **OURS** (PAT col. 9:65-67 correlates with "the outcome of trades", no horizon) |
| `tox_threshold` | 0.7 | **OURS**, bounded by PAT col. 9:1-5 (a level of toxic trades "is to be expected" from random arrival) and SEC 31 ("a narrow set of circumstances", trade outs "less than 6.9%"); grid 0.5-0.9 in the report |
| `tox_refit_s` | 30 | **OURS** (PAT col. 10:13-16: quotients "dynamically modified") |
| `tox_min_train` | 300 | **OURS** |
| `tox_ridge` | 1.0 | **OURS** |
| `profit_mark_s` | 5 | **OURS** (PAT col. 13:60-62: profit known only "after a trade has been executed") |
| `profit_period_s` | 60 | **OURS** (PAT col. 14:5-7: "various periods of time (days, weeks, or months)") |
| `profit_signal_z` | 2.0 | **OURS** (PAT col. 14:8-11: "Deviations from these averages ... signal the need to take corrective action") |
| `profit_min_trades` | 20 | **OURS** |
| `eps` | 1e-9 | numerical tolerance |

## 10. What the crypto data changes (read before comparing with the SEC's numbers)

Numbers from `python -X utf8 run.py --data data/run_final` (20 minutes of 2026-11-18; 16,541 fills in 7,723 public
taker orders; 2,267 orders of at least 0.0005 BTC after the 60 s warm-up). The SEC's shares are of CES's order flow
(SEC 18, 28), of the retail orders of its algorithmic engine (SEC 18) or of the orders FastFill and SmartProvide
handled (SEC 29, 31, 37, fn 13), all after eligibility conditions that are not public (SEC 10, 30); ours are of every
public taker order with none of them, so no share below is comparable with the SEC's.

- **Odd lots everywhere.** With a round-lot SIP the direct feeds beat the SIP at arrival on 1,536 of 2,267 orders.
  FastFill takes 56.2% of orders (1,273; 295 more fail fn 13's 90% rule), and 96.0% of its triggers are odd lots
  (SEC: about 20%); 25.8% rest only on levels under one share-equivalent. The `latency` preset (SEC 22 alone, the
  same feeds) gives FastFill 1.1% (25 orders: 2 at arrival, 23 taking over a SmartProvide display), 48.0% of them odd
  lots.
- **Prints are not retail orders.** Crypto takers include fast traders (the patent expects more toxic trades from
  "the professional investing population", col. 9), and an order's own impact shows in the later book, so the
  dealer's markouts here need not resemble those on retail flow.
- **SmartProvide is an event on Coinbase and a standing condition on Kraken.** Reading Coinbase it takes 1.8% of
  orders (40), 5.5% of the orders without a FastFill trigger at arrival (only those can reach it); 4.0% under
  `latency`. Reading Kraken ($0.10 grid) it takes 98.1% of those orders under `sip` and 97.9% under `latency`, and
  FastFill takes over many of its displays (472 of about 717 under `sip`; 465 of about 2,217 under `latency`, which
  are 465 of its 467 FastFills).
- **SmartProvide rarely fills.** 0.3% of SmartProvide BTC filled in the market (SEC: about 18%): it fires when the
  feed it reads has moved away from the SIP, so a bid one cent under the old SIP offer sits behind the market.
  FastFill took over 32 of the 40 displays; 87.5% of the orders internalized afterwards got a worse price than the
  arrival SIP (SEC 39: "some"); a SmartProvide order waited 181 ms (median, p90 1,000 ms) for its last fill.
- **Principal interest.** At `tox_threshold` 0.7 the analyzer finds no principal interest in 18.4% of orders (first
  half 19.6%, second 16.9%); trade outs are 1.1% of FastFill BTC (first half 1.4%, second half, out of sample, 0.7%;
  SEC 31: "less than 6.9%"). At 0.5 they would be 50.2% and 5.3%; the grid is in the report.
- **Trade outs.** Read on the venue's first book message from 50 ms after sending (58 / 74 / 104 ms, p10 / p50 / p90):
  71.8% filled in full or part (SEC: about 55%); 74.7% when sent 5 ms ahead, 57.8% at 250 ms. 67 of the 71 went to
  Coinbase.
- **Crossed quotes left.** With the clip corrected, two other venues can still cross each other inside a Coinbase
  spread wider than a cent: the SIP is crossed or locked in 1 of the 1,199 one-second samples (03:19:26) and the
  direct view in 2 (03:23:12, 03:29:58). They are left as they are.
- **The toxicity quotient** separates losing from profitable fills out of sample at 1 s (AUC 0.769) and 5 s (0.693),
  a little better than its best single parameter (nbbo_spread_bps 0.674 at 1 s, momentum_1s_bps 0.632 at 5 s: the
  label marks a fill at the SIP price, so a wide SIP spread alone makes a profit likely). At 30 s it fails out of
  sample (0.459).
- **Profit analyzer.** A running tab of $1.04 on 1,324 marked dealer fills: the spread at the fill gave $62.06 and
  the later price took $61.03.

## 11. Which test reproduces what

| Test file | What it checks |
|---|---|
| `tests/test_sec_examples.py` | the SEC's own examples with synthetic books: paras 11, 13, 29, 30, 32, 35, 37, 39, fn 11, fn 13 |
| `tests/test_engine_rules.py` | paras 26, 34, 39 and fn 12; every parameter has a source and a row in this file |
| `tests/test_patent_toxicity.py` | the patent's steps: toxic = no profit or a loss, the parameter lists, the empirical quotient, the response, the running tab, the expected level and corrective action |
| `tests/test_no_lookahead.py` | the SIP publishes only after its latency; outcomes are learned only once known; a recording cut at T gives exactly the same events and quotients before T; `live.py` prints exactly the replay's lines |
| `tests/test_feeds.py` | the four parsers on real messages (`tests/fixtures`), every real Kraken checksum, taker sides, reconnects, truncated files |
| `tests/test_film_contract.py` | the film data: 1 s candles rebuild the 5 s candles; the probe export meets the contract |
| `tests/test_regressions.py` | one class per behaviour: the crossed-quote clip, trade outs read on the venue's next book, raw venue prices, taker orders, the warm-up, the end of the data, the toxicity baselines, the attribution of the patent link, the SmartProvide feed and threshold, the shared market, the display grid, SEC 30 and 38, the halves, the report and the film's report panel |
| `tests/test_cli.py` | the command line: a `--config` file gives the same parameters as the same `--set` items, `fastfill.example.toml` lists every parameter with its default and source, `explain` finds every RULES.md row it quotes, `replay`, `sensitivity`, `sec-check` and `params` run on `tests/fixtures`, the AI docs name only existing commands |
| `tests/test_markout.py` | `tools/markout_my_fills.py`: markouts of one's own fills against a recording, split into the edge at the fill and the later move (our markout version of the patent's profit-analyzer tab, section 6) |
