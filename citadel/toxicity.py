"""Toxicity analyzer and profit analyzer, as Citadel Investment Group's patent describes them. In this rebuild
(OURS: neither the SEC order nor the patent links them) they decide whether CES has a "principal interest" in an
order (SEC order 33-10280, para 26, which says only that CES "used proprietary algorithms to decide").

Source: US 7,587,347 B2, "Computer implemented and/or assisted methods and systems for detecting, tracking and
responding to toxic, or likely toxic, orders in an equities order flow using toxicity and/or profit analyzers".
Inventors Kenneth C. Griffin and Matthew Andresen; the assignee named on the patent is Citadel Investment Group, L.L.C.
(not the respondent of the SEC order, Citadel Securities LLC); filed Nov 30, 2004, before CES began "in late 2005" (SEC
17); granted Sep 8, 2009 (scan: docs/patents/US7587347.pdf). Its division US 7,987,128 B2 carries the same
specification. Citations are column:line of US 7,587,347 B2, read on the scan, and claim numbers. The patent publishes
a method, not code, and fixes no weights, horizons or thresholds: those are ours and live in params.py (TOX_*,
PROFIT_*).

The patent's method, step by step, and where each step is in this file:

1. What "toxic" means (col. 4:1-8): "orders that, if accepted by a consolidating broker-dealer, would result in no
   profit, reduced profit and/or a loss to the broker-dealer". Claim 1: the toxicity quotient defines "when the order,
   if executed, comprises one of no profit, reduced profit or a loss when a wholesaler or a market maker provides
   market liquidity".                                                                   -> is_toxic(), dealer_pnl()
2. Measure each order (col. 9:36-51): "The variables which can be used to characterize toxicity can be derived from
   order parameters, market condition parameters, and/or order execution parameters."  -> PARAMETERS, measure()
3. Track the outcome of each executed trade (col. 13:67-14:5): a profit analyzer "may be used to monitor the outcome
   of some or all trades that are received by a consolidating broker-dealer and that are subsequently executed. By
   logging the bid/offer spread at the time of order filling for each equity trade executed, a running tab of profit
   (or loss) can be kept."                                                                       -> ProfitAnalyzer
4. Combine the parameters empirically (col. 9:57-67): "some or all of the above and other order, market condition,
   and/or order execution parameters may be combined empirically to generate a "toxicity quotient" ... by measuring
   order, market condition, and/or execution parameters of various orders over time, and correlating these
   parameters with the outcome of trades (e.g., toxic trades yielding a loss compared to normal profitable trades)."
                                                               -> ToxicityAnalyzer (running), evaluate() (offline)
5. Respond (col. 11:9-17; FIG. 5, col. 11:59-67): an order that is not toxic "is executed at step 508 (e.g., by
   sending the order to an exchange or internalizing the order ...)"; a likely toxic order "is either rejected or, for
   example, it is executed at a later time, at another market center, with another market participant, and/or for an
   increased fee (commission)" (step 510); claims 10-11 add "executing a modified version of the order", with "a
   modified order size". OURS: of these responses only "another market center" is used.  -> respond(), RESPONSES

How this rebuild uses the response (OURS; the SEC order does not say how CES decided): "principal interest" (para 26)
= the analyzer says EXECUTE, so CES may internalize; otherwise CES "typically attempted to execute a retail order in
the market on a riskless principal basis" (fn 12), i.e. at another market center, except FastFill's "narrow set of
circumstances" (para 31): internalize at the SIP price and send a "trade out attempt" for itself at the better price.
That logic is in ces.py. The patent itself says its profit measure "could not be used to stop the execution of
unprofitable orders" (col. 13:60-64); the decision here uses the toxicity quotient, which is computed before the
order is executed.
"""
import math
from collections import deque

import numpy as np

BUY, SELL = "BUY", "SELL"
EPS_USD = 1e-9

ORDER = "order parameter"
MARKET = "market condition parameter"
EXECUTION = "execution parameter"
OTHER = "other distinct characteristic"

# The parameters, in the order measure() returns them: (name, kind, the patent's words, how we measure it on public
# BTC/USD feeds). The patent's words are col. 9:38-51 unless another place is named.
PARAMETERS = (
    ("side_buy", ORDER, "the action type (e.g., buy or sell)", "+1 buy, -1 sell"),
    ("log_size", ORDER, "the order size (e.g., the number of shares)", "log10 of the order size in BTC"),
    ("limit_through_bps", ORDER, "the limit price (e.g., for a limit order)",
     "OURS: a public taker order shows no limit price, but its last (worst) fill price bounds it; how far it "
     "traded through the SIP price, bps, signed by side"),
    ("theo_vs_sip_bps", MARKET, "the theoretical value of the equity",
     "direct-feed microprice minus SIP mid, bps, signed by side. The patent's theoretical fair value is \"the value "
     "to which the price is likely to move in the near future\" (col. 4:12-13); the microprice is ours"),
    ("order_freq", MARKET, "the frequency of order submission",
     "log(1 + taker orders per second on the order's venue over the last 10 s)"),
    ("src_coinbase", MARKET, "the NBBO market source", "1 if Coinbase sets the SIP price on the order's side"),
    ("src_kraken", MARKET, "the NBBO market source", "1 if Kraken sets the SIP price on the order's side"),
    ("src_bitstamp", MARKET, "the NBBO market source", "1 if Bitstamp sets the SIP price on the order's side"),
    ("src_gemini", MARKET, "the NBBO market source", "1 if Gemini sets the SIP price on the order's side"),
    ("log_nbbo_size", MARKET, "the NBBO size", "log10 of the BTC at the SIP price on the order's side"),
    ("log_size_vs_nbbo", MARKET, "order size relative to NBBO size (claim 6)", "log10(order size / NBBO size)"),
    ("log_liquidity", MARKET, "the liquidity of the equity in the market",
     "log10 of the direct-feed BTC within 10 bps of the best price on the order's side"),
    ("nbbo_spread_bps", MARKET, "the implied spread (NBBO); claim 6: \"current NBBO bid-offer spread\"",
     "SIP NBO minus NBB, bps"),
    ("fill_spread_bps", EXECUTION, "the fill spread (e.g., the bid/offer spread at the time of filling the order)",
     "direct-feed best offer minus best bid at the fill, bps"),
    ("fill_edge_bps", EXECUTION, "the fill price",
     "fill price (the SIP price, para 30) minus the direct-feed mid, on the dealer's side, bps"),
    ("momentum_1s_bps", OTHER, "\"any other distinct characteristic may be used alone and/or in combination with "
     "any or all of the above mentioned parameters\" (col. 9:53-55)",
     "OURS: direct-feed mid change over the last second, bps, signed by side"),
)
FEATURES = PARAMETERS
FEATURE_NAMES = [p[0] for p in PARAMETERS]

# Parameters the patent lists that public BTC/USD feeds cannot supply.
NOT_AVAILABLE = (
    ("the identity of the equity (e.g., symbol)", ORDER, "one instrument, BTC/USD"),
    ("the order type (e.g., market, limit, stop, all-or-none)", ORDER, "every order is marketable (SEC para 1)"),
    ("equity-market performance correlation (e.g., beta)", MARKET, "one instrument and no market index"),
)
NOT_USED = [(a, c) for a, _, c in NOT_AVAILABLE]

# Responses (col. 11:9-17 and FIG. 5 steps 508 / 510, col. 11:59-67).
EXECUTE = "EXECUTE"                       # step 508: executed, "by sending the order to an exchange or internalizing"
ANOTHER_MARKET_CENTER = "ANOTHER_MARKET_CENTER"   # step 510: executed "at another market center"
RESPONSES = {
    EXECUTE: "not toxic: \"the order is executed at step 508 (e.g., by sending the order to an exchange or "
             "internalizing the order ...)\" (col. 11:60-62) -> OURS: CES has a principal interest (SEC para 26)",
    ANOTHER_MARKET_CENTER: "likely toxic: \"not executed (e.g., are not sent to an exchange and are not "
                           "internalized)\", or executed \"at another market center\" (col. 11:12-16) -> OURS: no "
                           "principal interest: riskless principal in the market (SEC fn 12), or FastFill with a "
                           "trade out attempt (SEC para 31). The patent's other responses (reject, later, another "
                           "participant, a higher fee, a reduced size: col. 11:59-67, claims 10-11) are not used",
}


def dealer_pnl(side, fill_px, mid):
    """Dealer profit per unit after taking the other side of a customer order of `side` at fill_px, marked at mid.

    "The profit for consolidating broker-dealer 130, when internalizing the trade ..., is made at the level of the
    trade execution, and is based on the spread between bid and offer prices for the equity" (col. 3:9-13)."""
    return (fill_px - mid) if side == BUY else (mid - fill_px)


def is_toxic(pnl_usd):
    """Toxic = the trade gave the dealer "no profit ... and/or a loss" (col. 4:1-3): P&L <= 0. "Reduced profit" (a
    positive profit below the expected level) is the profit analyzer's test, per claims 34-35."""
    return pnl_usd <= EPS_USD


def measure(side, size, print_px, sip_px, fill_px, nbb, nbo, nbbo_size, nbbo_venues, bid, bid_size, ask, ask_size,
            liquidity_btc, prints_per_s, mid_1s_ago):
    """The order, market condition and execution parameters of one order (col. 9:36-51) at the moment CES decides on
    it, in FEATURE_NAMES order. Every input is known at that moment (no look-ahead):

    side, size, print_px     the order: aggressor side, BTC, the print's price
    sip_px                   the SIP price for the order (NBO for a buy, NBB for a sell) as published now
    fill_px                  the fill CES would give: the SIP price adjusted by price improvement (SEC 27, 30)
    nbb, nbo, nbbo_size      the SIP NBBO as published now and the size at the price on the order's side
    nbbo_venues              the venues setting that SIP price ("the NBBO market source")
    bid, bid_size, ask, ...  the direct-feed consolidated best bid / offer and sizes (the depth-of-book view)
    liquidity_btc            direct-feed size within 10 bps of the best price on the order's side
    prints_per_s             taker orders per second on the order's venue, last 10 s (including this one)
    mid_1s_ago               the direct-feed mid one second ago, or None
    """
    sgn = 1.0 if side == BUY else -1.0
    if bid is not None and ask is not None:
        mid = 0.5 * (bid + ask)
        micro = (bid * ask_size + ask * bid_size) / (bid_size + ask_size) if bid_size + ask_size > 0 else mid
    else:
        mid = micro = sip_px
    sip_mid = 0.5 * (nbb + nbo) if (nbb is not None and nbo is not None) else sip_px
    k = 1e4 / mid
    nb_size = max(nbbo_size, 1e-8)
    liq = max(liquidity_btc, 1e-8)
    return [
        sgn,
        math.log10(max(size, 1e-8)),
        sgn * (print_px - sip_px) * k,
        sgn * (micro - sip_mid) * k,
        math.log1p(prints_per_s),
        1.0 if "coinbase" in nbbo_venues else 0.0,
        1.0 if "kraken" in nbbo_venues else 0.0,
        1.0 if "bitstamp" in nbbo_venues else 0.0,
        1.0 if "gemini" in nbbo_venues else 0.0,
        math.log10(nb_size),
        math.log10(max(size, 1e-8) / nb_size),
        math.log10(liq),
        ((nbo - nbb) * k) if (nbb is not None and nbo is not None) else 0.0,
        ((ask - bid) * k) if (bid is not None and ask is not None) else 0.0,
        sgn * (fill_px - mid) * k,
        (sgn * (mid - mid_1s_ago) * k) if mid_1s_ago is not None else 0.0,
    ]


# ------------------------------------------------------------------------------------------- the empirical quotient
def sigmoid(z):
    return 1.0 / (1.0 + np.exp(-np.clip(z, -35, 35)))


def fit_logistic(X, y, ridge=1.0, iters=30):
    """Logistic regression of toxic (1) vs profitable (0) outcomes on standardized parameters (Newton steps, L2
    penalty). The patent asks for an empirical combination correlated with trade outcomes (col. 9:57-67); this form of
    it is ours. Returns (weights with intercept first, means, standard deviations)."""
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float)
    mu = X.mean(axis=0)
    sd = X.std(axis=0)
    sd[sd < 1e-12] = 1.0
    Z = (X - mu) / sd
    n, k = Z.shape
    A = np.empty((n, k + 1))
    A[:, 0] = 1.0
    A[:, 1:] = Z
    w = np.zeros(k + 1)
    R = np.eye(k + 1) * ridge
    R[0, 0] = 0.0
    for _ in range(iters):
        p = sigmoid(A @ w)
        g = A.T @ (p - y) + R @ w
        W = p * (1 - p)
        H = A.T @ (A * W[:, None]) + R
        step = np.linalg.solve(H, g)
        w -= step
        if np.max(np.abs(step)) < 1e-8:
            break
    return w, mu, sd


def predict(model, X):
    w, mu, sd = model
    Z = (np.asarray(X, dtype=float) - mu) / sd
    return sigmoid(w[0] + Z @ w[1:])


def auc(score, y):
    """Area under the ROC curve (Mann-Whitney, ties averaged)."""
    score = np.asarray(score, dtype=float)
    y = np.asarray(y).astype(bool)
    n1 = int(y.sum())
    n0 = len(y) - n1
    if n1 == 0 or n0 == 0:
        return float("nan")
    uniq, inv, cnt = np.unique(score, return_inverse=True, return_counts=True)
    start = np.concatenate(([0], np.cumsum(cnt)[:-1]))
    avg_rank = start + (cnt + 1) / 2.0
    ranks = avg_rank[inv]
    return float((ranks[y].sum() - n1 * (n1 + 1) / 2.0) / (n1 * n0))


def correlations(X, y):
    """Each parameter "correlat[ed] ... with the outcome of trades (e.g., toxic trades yielding a loss compared to
    normal profitable trades)" (col. 9:65-67): Pearson correlation with the toxic label, and the parameter's mean
    over toxic and over profitable trades. Returns [[name, r, mean_toxic, mean_profitable], ...]."""
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float)
    out = []
    if len(y) < 3 or y.min() == y.max():
        return out
    for j, name in enumerate(FEATURE_NAMES):
        x = X[:, j]
        sx = x.std()
        r = float(np.mean((x - x.mean()) * (y - y.mean())) / (sx * y.std())) if sx > 1e-12 else 0.0
        out.append([name, round(r, 4), round(float(x[y == 1].mean()), 4), round(float(x[y == 0].mean()), 4)])
    return out


class ToxicityAnalyzer:
    """The running toxicity analyzer used for decisions (FIG. 3-5, toxicity analyzer 330).

    - quotient(x): "an order may be examined to determine the likelihood that it will be toxic if executed"
      (col. 10:17-19). Ours: the fitted probability that internalizing the order at the SIP price gives the dealer no
      profit or a loss TOX_HORIZON_S later. The two outcomes it separates are the patent's: "Upon execution of normal
      orders, broker-dealer 130 would likely record a trading profit (on average), while upon execution of toxic
      orders, a reduction in trading profit or a loss would likely result" (col. 10:49-53).
    - observe() / on_time(): the outcome of every order is learned once it is known. The model is refitted every
      TOX_REFIT_S on outcomes already known at that time, so it only compares a new order with "previously received
      (and potentially executed) orders" (col. 9:67-10:3), and "toxicity quotients may be dynamically modified based
      on events occurring after their initial generation" (col. 10:15-16). Silent (no screening) until TOX_MIN_TRAIN
      outcomes with both kinds of trades exist.
    - respond(q): step 506 of FIG. 5, the eligibility test, with threshold TOX_THRESHOLD (ours; bounded by col. 9:1-5,
      a level of toxic trades "is to be expected" from random arrival, and SEC 31, see params.py).
    """

    def __init__(self, p):
        self.H = int(p.tox_horizon_s * 1e9)
        self.refit_ns = int(p.tox_refit_s * 1e9)
        self.min_train = p.tox_min_train
        self.ridge = p.tox_ridge
        self.threshold = p.tox_threshold
        self.pending = deque()
        self.X = []
        self.y = []
        self.model = None
        self.last_fit = None
        self.n_fits = 0
        self.n_toxic = 0
        # outcome of every order had it been internalized at the SIP price (the training labels), USD
        self.label_pnl_usd = 0.0
        self.n_labels = 0

    @property
    def tab_usd(self):          # kept for older callers; the dealer's own running tab is ProfitAnalyzer.tab_usd
        return self.label_pnl_usd

    def base_rate(self):
        return (sum(self.y) / len(self.y)) if self.y else 0.5

    def quotient(self, x):
        """Toxicity quotient of an order with parameters x, or None while the analyzer is silent."""
        if self.model is None:
            return None
        return float(predict(self.model, [x])[0])

    def respond(self, q):
        """FIG. 5 step 506: EXECUTE (step 508) when the order is not likely toxic, else ANOTHER_MARKET_CENTER (step
        510). A silent analyzer screens nothing."""
        if q is None or q < self.threshold:
            return EXECUTE
        return ANOTHER_MARKET_CENTER

    def observe(self, m, x, side, fill_px, qty):
        """Record the parameters of an order received at m; its outcome is known at m + TOX_HORIZON_S."""
        self.pending.append((m + self.H, x, side, fill_px, qty))

    def on_time(self, m, mid):
        """Learn every outcome known by time m (mid = the direct-feed mid now), then refit if due."""
        if mid is None:
            return
        pend = self.pending
        while pend and pend[0][0] <= m:
            _, x, side, px, qty = pend.popleft()
            pnl = dealer_pnl(side, px, mid)
            lab = 1 if is_toxic(pnl) else 0
            self.X.append(x)
            self.y.append(lab)
            self.n_toxic += lab
            self.label_pnl_usd += pnl * qty
            self.n_labels += 1
        if len(self.y) >= self.min_train and (self.last_fit is None or m - self.last_fit >= self.refit_ns):
            if 0 < self.n_toxic < len(self.y):
                try:
                    self.model = fit_logistic(self.X, self.y, self.ridge)
                    self.n_fits += 1
                except np.linalg.LinAlgError:
                    pass
            self.last_fit = m


# ------------------------------------------------------------------------------------------------ profit analyzer
class ProfitAnalyzer:
    """The patent's profit analyzer (col. 13:45-14:11, FIG. 15-17, claims 18-19 and 33-35), kept by the dealer.

    - on_fill(): every trade the dealer executes is logged with "the bid/offer spread at the time of order filling"
      (col. 14:3-4; claim 19: "logging the bid/offer spread at the time the order is filled"): bid, offer, fill price,
      size, side and the order flow it came from.
    - on_time(): "profit (or loss) on a trade can only be determined after a trade has been executed" (col. 13:60-62):
      each fill is marked at the direct-feed mid PROFIT_MARK_S later and added to "a running tab of profit (or loss)"
      (col. 14:4-5). The tab is split into what the spread at the fill gave (fill price vs mid at the fill) and what
      the later price took or gave (mid at the fill vs mid at the mark): "broker-dealer profit information coupled
      with market condition information (either instantaneous or averaged over some time period) can be used to
      characterize the quality of an individual order or flow of orders" (col. 13:56-60).
    - expected level: "by monitoring average trading volume and average per trade-profit over various periods of time
      (days, weeks, or months), an expected level of profit can be established for various OFPs. Deviations from these
      averages could then be used to signal the need to take corrective action against a particular OFP or investor
      (e.g., to adjust payment for order flow to the OFP)" (col. 14:5-11). Here an order flow ("OFP") is the venue
      the order came from (ours), a period is PROFIT_PERIOD_S (ours: our recordings last minutes, not months), and a
      signal is a period whose average per-trade profit is below the expected level by more than PROFIT_SIGNAL_Z
      standard errors (ours). Claims 34-35: "establishing an expected level of profit for the order based on the
      profitability of previous orders" and "taking corrective action where the profitability of the order is below
      the expected level of profit".
    The dealer's own trades (trade out attempts, SEC para 31) count in the tab but belong to no order flow.
    """

    def __init__(self, mark_s=5.0, period_s=60.0, z=2.0, min_trades=20):
        self.H = int(mark_s * 1e9)
        self.period = int(period_s * 1e9)
        self.z = z
        self.min_trades = min_trades
        self.pending = deque()
        self.n_logged = 0
        self.n = 0
        self.qty = 0.0
        self.tab_usd = 0.0
        self.spread_usd = 0.0
        self.move_usd = 0.0
        self.n_losing = 0
        self.flows = {}            # source -> {"n", "qty", "tab", "s1", "s2", "signals"}: all closed periods
        self.cur = {}              # source -> [n, s1, s2, qty] of the open period
        self.p0 = None
        self.signals = []          # (period start ns, source, period avg, expected avg, n in period)

    def on_fill(self, m, source, side, px, qty, bid, ask, flow=True):
        """Log one executed trade: the customer's side (for a trade out: the opposite of the dealer's), the fill
        price and size, and the bid/offer at the fill. flow=False for the dealer's own trades."""
        if qty <= 0:
            return
        mid = 0.5 * (bid + ask) if (bid is not None and ask is not None) else px
        self.pending.append((m + self.H, m, source if flow else None, side, px, qty, mid))
        self.n_logged += 1

    def on_time(self, m, mid):
        if mid is None:
            return
        pend = self.pending
        while pend and pend[0][0] <= m:
            _, m0, src, side, px, qty, mid0 = pend.popleft()
            spread = dealer_pnl(side, px, mid0) * qty
            total = dealer_pnl(side, px, mid) * qty
            self.n += 1
            self.qty += qty
            self.tab_usd += total
            self.spread_usd += spread
            self.move_usd += total - spread
            self.n_losing += 1 if is_toxic(total) else 0
            if src is not None:
                self._period(m0)
                c = self.cur.setdefault(src, [0, 0.0, 0.0, 0.0])
                c[0] += 1
                c[1] += total
                c[2] += total * total
                c[3] += qty

    def _period(self, m0):
        if self.p0 is None:
            self.p0 = m0 - m0 % self.period
        while m0 >= self.p0 + self.period:
            self._close()
            self.p0 += self.period

    def _close(self):
        for src, (n, s1, s2, qty) in self.cur.items():
            f = self.flows.setdefault(src, {"n": 0, "qty": 0.0, "tab": 0.0, "s1": 0.0, "s2": 0.0, "signals": 0,
                                            "periods": 0})
            if f["n"] >= self.min_trades and n > 0:
                exp = f["s1"] / f["n"]
                var = max(f["s2"] / f["n"] - exp * exp, 0.0)
                se = math.sqrt(var / n)
                avg = s1 / n
                if avg < exp - self.z * se - EPS_USD:
                    f["signals"] += 1
                    self.signals.append((self.p0, src, avg, exp, n))
            f["n"] += n
            f["qty"] += qty
            f["tab"] += s1
            f["s1"] += s1
            f["s2"] += s2
            f["periods"] += 1
        self.cur = {}

    def summary(self):
        """Running tab and the per-order-flow table (closes the open period)."""
        if self.cur:
            self._close()
        flows = {}
        for src, f in sorted(self.flows.items()):
            flows[src] = {"trades": f["n"], "btc": round(f["qty"], 6), "tab_usd": round(f["tab"], 2),
                          "expected_usd_per_trade": round(f["s1"] / f["n"], 4) if f["n"] else None,
                          "periods": f["periods"], "corrective_action_signals": f["signals"]}
        return {"trades_marked": self.n, "trades_logged": self.n_logged, "btc": round(self.qty, 6),
                "tab_usd": round(self.tab_usd, 2), "from_spread_at_fill_usd": round(self.spread_usd, 2),
                "from_later_price_usd": round(self.move_usd, 2),
                "losing_or_flat_pct": round(100.0 * self.n_losing / self.n, 2) if self.n else 0.0,
                "mark_s": self.H / 1e9, "period_s": self.period / 1e9, "signal_z": self.z,
                "by_order_flow": flows,
                "signals": [[int(p // 1_000_000), s, round(a, 4), round(e, 4), n] for p, s, a, e, n in self.signals]}


# --------------------------------------------------------------------------------------- the out-of-sample test
def evaluate(rows, mid_at, t_split, horizons=(1.0, 5.0, 30.0), main_h=5.0, ridge=1.0, threshold=0.5):
    """Out-of-sample test of the quotient (ours; the patent has no test): fit on the orders before t_split (the first
    half of the recording), test on the rest. Labels: toxic = no profit or a loss for the dealer had it internalized
    the order at the SIP price, marked h seconds later. rows: [(m, x, side, fill_px)]; mid_at(m) -> mid or None."""
    out = {"horizons": {}, "main_h": main_h}
    for h in horizons:
        hn = int(h * 1e9)
        X1, y1, X2, y2 = [], [], [], []
        for m, x, side, px in rows:
            mid = mid_at(m + hn)
            if mid is None:
                continue
            lab = 1 if is_toxic(dealer_pnl(side, px, mid)) else 0
            if m < t_split:
                X1.append(x)
                y1.append(lab)
            else:
                X2.append(x)
                y2.append(lab)
        res = {"n_train": len(y1), "n_test": len(y2),
               "loss_rate_train": (sum(y1) / len(y1)) if y1 else None,
               "loss_rate_test": (sum(y2) / len(y2)) if y2 else None}
        if len(y1) >= 50 and len(y2) >= 50 and 0 < sum(y1) < len(y1):
            model = fit_logistic(X1, y1, ridge)
            s2 = predict(model, X2)
            s1 = predict(model, X1)
            res["auc_in"] = auc(s1, y1)
            res["auc_oos"] = auc(s2, y2)
            res["toxic_share_pct"] = float(100.0 * np.mean(s2 >= threshold))
            res["weights"] = [[n, round(float(w), 4)] for n, w in zip(FEATURE_NAMES, model[0][1:])]
            res["intercept"] = float(model[0][0])
            res["correlation"] = correlations(X1, y1)
            # how the response would have split the test half, and what each group really did
            tox = s2 >= threshold
            y2a = np.asarray(y2)
            res["test_toxic_rate_flagged"] = float(y2a[tox].mean()) if tox.any() else None
            res["test_toxic_rate_passed"] = float(y2a[~tox].mean()) if (~tox).any() else None
            res["single_feature_auc"] = single_feature_auc(X1, y1, X2, y2)
            res["beats_chance_oos"] = bool(res["auc_oos"] > 0.5)
        out["horizons"][str(h)] = res
    return out


def single_feature_auc(X1, y1, X2, y2):
    """Baselines for the quotient: each parameter alone as a score, its direction taken from the first half, AUC on
    the second half. Returns [[name, AUC out of sample, AUC in sample], ...], best first. A quotient that does not
    beat its best single parameter adds little to it."""
    X1 = np.asarray(X1, dtype=float)
    X2 = np.asarray(X2, dtype=float)
    out = []
    for j, name in enumerate(FEATURE_NAMES):
        a1 = auc(X1[:, j], y1)
        if a1 != a1:
            continue
        sgn = 1.0 if a1 >= 0.5 else -1.0
        a2 = auc(sgn * X2[:, j], y2)
        out.append([name, round(float(a2), 4), round(float(max(a1, 1.0 - a1)), 4)])
    out.sort(key=lambda r: -r[1])
    return out
