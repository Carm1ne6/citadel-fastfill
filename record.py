"""Record public BTC/USD order books and trades from four exchanges, with local receive timestamps.

    python record.py --minutes 60 --out data/run1

One gzip JSONL file per venue: {"t": wall-clock ns, "m": monotonic ns, "raw": "<message>"}.
Only public market-data channels are used; nothing is sent besides the subscribe messages.
"""
import argparse
import asyncio
import gzip
import json
import os
import sys
import time

import websockets

VENUES = {
    "coinbase": {
        "url": "wss://ws-feed.exchange.coinbase.com",
        "subs": [{"type": "subscribe", "product_ids": ["BTC-USD"],
                  "channels": ["level2_batch", "matches", "heartbeat"]}],
    },
    "kraken": {
        "url": "wss://ws.kraken.com/v2",
        "subs": [{"method": "subscribe", "params": {"channel": "book", "symbol": ["BTC/USD"], "depth": 25}},
                 {"method": "subscribe", "params": {"channel": "trade", "symbol": ["BTC/USD"]}}],
    },
    "bitstamp": {
        "url": "wss://ws.bitstamp.net",
        "subs": [{"event": "bts:subscribe", "data": {"channel": "order_book_btcusd"}},
                 {"event": "bts:subscribe", "data": {"channel": "live_trades_btcusd"}}],
    },
    "gemini": {
        "url": "wss://api.gemini.com/v2/marketdata",
        "subs": [{"type": "subscribe", "subscriptions": [{"name": "l2", "symbols": ["BTCUSD"]}]}],
    },
}

BITSTAMP_KEEP = 20  # Bitstamp sends the top 100 levels on every change; keep the top 20 to save disk.


def trim_bitstamp(raw):
    try:
        msg = json.loads(raw)
    except ValueError:
        return raw
    data = msg.get("data")
    if msg.get("channel", "").startswith("order_book") and isinstance(data, dict) and "bids" in data:
        data["bids"] = data["bids"][:BITSTAMP_KEEP]
        data["asks"] = data["asks"][:BITSTAMP_KEEP]
        return json.dumps(msg, separators=(",", ":"))
    return raw


async def run_venue(name, cfg, out_dir, stop_at, stats, probe):
    path = os.path.join(out_dir, f"{name}.jsonl.gz")
    backoff = 1.0
    with gzip.open(path, "at", encoding="utf-8", compresslevel=6) as fh:
        while time.monotonic() < stop_at:
            try:
                async with websockets.connect(cfg["url"], max_size=None, ping_interval=20,
                                              open_timeout=15) as ws:
                    for sub in cfg["subs"]:
                        await ws.send(json.dumps(sub))
                    fh.write(json.dumps({"t": time.time_ns(), "m": time.monotonic_ns(),
                                         "event": "connected"}) + "\n")
                    backoff = 1.0
                    while time.monotonic() < stop_at:
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, stop_at - time.monotonic()))
                        except asyncio.TimeoutError:
                            break
                        t, m = time.time_ns(), time.monotonic_ns()
                        if isinstance(raw, bytes):
                            raw = raw.decode("utf-8", "replace")
                        if name == "bitstamp":
                            raw = trim_bitstamp(raw)
                        fh.write(json.dumps({"t": t, "m": m, "raw": raw}, separators=(",", ":")) + "\n")
                        stats[name] = stats.get(name, 0) + 1
                        if probe and stats[name] <= 2:
                            print(f"[{name}] {raw[:300]}", flush=True)
            except Exception as exc:  # reconnect on any network error
                fh.write(json.dumps({"t": time.time_ns(), "m": time.monotonic_ns(),
                                     "event": "error", "error": repr(exc)[:300]}) + "\n")
                print(f"[{name}] error: {exc!r}", file=sys.stderr, flush=True)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)


async def report(stats, stop_at, every):
    start = time.monotonic()
    while time.monotonic() < stop_at:
        await asyncio.sleep(min(every, max(0.1, stop_at - time.monotonic())))
        el = time.monotonic() - start
        print(f"{el/60:6.1f} min  " + "  ".join(f"{k}={v}" for k, v in sorted(stats.items())), flush=True)


async def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--minutes", type=float, default=60, help="how long to record (default 60)")
    ap.add_argument("--out", default="data/run", help="recording folder (default data/run)")
    ap.add_argument("--venues", default=",".join(VENUES), help=f"comma-separated, from {','.join(VENUES)}")
    ap.add_argument("--probe", action="store_true", help="print each venue's first messages and counts every 10 s")
    a = ap.parse_args()
    bad = [n.strip() for n in a.venues.split(",") if n.strip() and n.strip() not in VENUES]
    if bad:
        raise SystemExit(f"--venues: unknown venue {bad[0]!r}; add it to record.VENUES first")
    os.makedirs(a.out, exist_ok=True)
    stop_at = time.monotonic() + a.minutes * 60
    stats = {}
    names = [v.strip() for v in a.venues.split(",") if v.strip()]
    with open(os.path.join(a.out, "meta.json"), "w") as fh:
        json.dump({"start_wall_ns": time.time_ns(), "minutes": a.minutes, "venues": names}, fh)
    tasks = [run_venue(n, VENUES[n], a.out, stop_at, stats, a.probe) for n in names]
    tasks.append(report(stats, stop_at, 10 if a.probe else 60))
    await asyncio.gather(*tasks)
    print("done", json.dumps(stats), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
